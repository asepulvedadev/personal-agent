"""Native image generation stays on the private relay across existing profiles."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import types

import pytest

from runtime.workspace_config import runtime_config

IMAGE_CONFIG = {
    'provider': 'openai', 'model': 'gpt-image-2-medium',
    'openai': {'model': 'gpt-image-2-medium', 'base_url': 'http://web:8080/provider/v1',
               'key_env': 'OPENAI_API_KEY'},
}


def test_web_image_generation_is_explicit_and_keeps_existing_chat_routes():
    config = runtime_config('owner-chat-model')
    assert config.get('image_gen') == IMAGE_CONFIG
    assert 'image_gen' in config['platform_toolsets']['api_server']
    assert config['model']['default'] == 'owner-chat-model'
    assert config['model']['api_mode'] == 'codex_responses'
    assert config['approvals']['mode'] == 'smart'


def test_existing_web_toolsets_are_not_replaced_or_mutated():
    owner = {'platform_toolsets': {'api_server': ['file', 'owner-tool'], 'discord': ['owner-tool']},
             'mcp_servers': {'owner': {'command': 'example'}},
             'image_gen': {'provider': 'fal', 'openai': {'base_url': 'https://wrong', 'api_key': 'wrong'}}}
    before = deepcopy(owner)
    config = runtime_config('owner-chat-model', owner_config=owner)
    assert config['platform_toolsets']['api_server'] == ['file', 'owner-tool', 'image_gen']
    assert config['platform_toolsets']['discord'] == ['owner-tool']
    assert config['image_gen'] == IMAGE_CONFIG
    assert config['mcp_servers'] == owner['mcp_servers']
    assert owner == before


def test_only_the_selected_bundled_backend_is_reenabled_in_existing_profiles(tmp_path):
    from runtime.telegram_config import prepare
    plugins = {'disabled': ['owner-disabled', 'image_gen/openai', 'openai'],
               'enabled': ['owner-enabled']}
    owner = {'plugins': plugins}
    config = runtime_config('chat', owner_config=owner)
    assert config['plugins'] == {'disabled': ['owner-disabled'], 'enabled': ['owner-enabled']}
    assert owner['plugins'] == plugins
    profile = tmp_path / 'profiles/carpediev'
    profile.mkdir(parents=True)
    (profile / 'config.yaml').write_text(json.dumps(owner))
    prepare(tmp_path, runtime_config('chat'), 'synthetic-relay')
    actual = json.loads((profile / 'config.yaml').read_text())
    assert actual['plugins'] == config['plugins']


@pytest.mark.parametrize('existing', [None, {
    'model': {'default': 'carpe-chat', 'base_url': 'http://owner-chat'},
    'agent': {'max_turns': 31}, 'approvals': {'mode': 'off'},
    'platform_toolsets': {'telegram': ['file', 'owner-tool'], 'discord': ['owner-tool']},
    'image_gen': {'provider': 'openai-codex', 'model': 'wrong',
                  'openai': {'provider': 'wrong', 'base_url': 'https://wrong', 'key_env': 'WRONG_KEY'}},
}])
def test_carpediev_images_override_stale_image_defaults_not_owner_chat(tmp_path, existing):
    from runtime.telegram_config import prepare
    profile = tmp_path / 'profiles/carpediev'
    profile.mkdir(parents=True)
    if existing:
        (profile / 'config.yaml').write_text(json.dumps(existing))
    base = runtime_config('web-chat')
    returned = prepare(tmp_path, base, 'synthetic-private-relay')
    actual = json.loads((profile / 'config.yaml').read_text())
    assert actual['image_gen'] == IMAGE_CONFIG
    assert 'image_gen' in actual['platform_toolsets']['telegram']
    assert 'image_gen' in actual['platform_toolsets']['api_server']
    assert (profile / '.env').read_text().splitlines() == [
        '', 'OPENAI_API_KEY=synthetic-private-relay', 'OPENAI_BASE_URL=http://web:8080/provider/v1']
    assert returned['model'] == base['model']
    if existing:
        for key in ('model', 'agent', 'approvals'):
            assert actual[key] == existing[key]
        assert actual['platform_toolsets']['telegram'] == ['file', 'owner-tool', 'image_gen']
        assert actual['platform_toolsets']['discord'] == ['owner-tool']
    prepare(tmp_path, base, 'synthetic-private-relay')
    assert json.loads((profile / 'config.yaml').read_text()) == actual


def test_image_patch_is_wired_into_the_pinned_build():
    source = Path('deploy/Dockerfile.hermes').read_text()
    assert 'COPY runtime/patch_images.py /opt/runtime/patch_images.py' in source
    assert 'RUN python /opt/runtime/patch_images.py /opt/hermes' in source
    assert source.index('RUN python /opt/runtime/patch_images.py') < source.index('USER 10001:10001')


# Minimal executable method fixtures from pinned Hermes; no imports or side effects.
NATIVE_PROVIDER = """from __future__ import annotations
import logging
def _build_client(openai: Any, base_url: str, api_key: str) -> Any:
    \"\"\"``openai.OpenAI`` on Hermes' env-only-proxy httpx client, so a local/custom endpoint never
    routes through a macOS system proxy whose ExceptionsList httpx cannot see (#64888). The project
    header is blanked: an ``OPENAI_PROJECT_ID`` set for chat makes ``/images/generations`` 403 on
    projects with a model allow-list, and the key already carries the project (#60748).\"\"\"
    from agent.process_bootstrap import build_keepalive_http_client

    kwargs: dict[str, Any] = {"api_key": api_key, "default_headers": {"OpenAI-Project": ""}}
    if base_url:
        kwargs["base_url"] = base_url
    http_client = build_keepalive_http_client(base_url)
    if http_client is not None:
        kwargs["http_client"] = http_client
    return openai.OpenAI(**kwargs)

class OpenAIImageGenProvider:
    def capabilities(self) -> dict[str, Any]:
        # images.edit() accepts up to 16 source images.
        return {"modalities": ["text", "image"], "max_reference_images": 16}

    def generate(
        self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, *,
        image_url: Optional[str] = None, reference_image_urls: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        if not prompt:
            return prompt_required_error("openai", aspect)
        base_url, api_key = _resolve_endpoint()
        if not api_key:
            return error_factory("openai", aspect)(
                "OPENAI_API_KEY not set (or the variable named by image_gen.openai.key_env is empty). "
                "Run `hermes tools` → Image Generation → OpenAI to configure, or `hermes setup` "
                "to add the key.",
                "auth_required")

        openai, err = import_openai("openai", aspect)
        if err:
            return err
        tier_id, meta = _resolve_model()
        size = size_for(aspect)
        sources = collect_source_images(image_url, reference_image_urls, limit=16)
        is_edit = bool(sources)
        fail = error_factory("openai", aspect, model=tier_id, prompt=prompt)
        client = _build_client(openai, base_url, api_key)

        # gpt-image-2 returns b64_json unconditionally and REJECTS
        # ``response_format`` as an unknown parameter. Don't send it.
        # A custom (non-catalog) model id carries no quality tier: gateways reject unknown enum values.
        request: dict[str, Any] = dict(model=meta["api_model"], prompt=prompt, size=size, n=1)
        if meta["quality"] is not None:
            request["quality"] = meta["quality"]
        if is_edit:
            try:
                files = [_named_bytes_io(ref) for ref in sources]
            except Exception as exc:
                return fail(f"Could not load source image for editing: {exc}", "io_error")
            request["image"] = files if len(files) > 1 else files[0]
        verb, call = ("edit", client.images.edit) if is_edit else ("generation", client.images.generate)
        try:
            response = call(**request)
        except Exception as exc:
            logger.debug("OpenAI image %s failed", verb, exc_info=True)
            return fail(f"OpenAI image {'editing' if is_edit else 'generation'} failed: {exc}", "api_error")

        # gpt-image bills per text/image token; the tier id is a Hermes label, the API model prices.
        # Recorded before extraction/save: the tokens are billed whether or not an image came back.
        record_token_usage(getattr(response, "usage", None), model=meta["api_model"], provider="openai")
        data = getattr(response, "data", None) or []
        if not data:
            return fail("OpenAI returned no image data", "empty_response")
        first = data[0]
        image_ref, err = materialize_image(
            getattr(first, "b64_json", None), getattr(first, "url", None),
            prefix=f"openai_{tier_id}", label="OpenAI", provider="openai",
            model=tier_id, prompt=prompt, aspect=aspect, log=logger)
        if err:
            return err
        extra: dict[str, Any] = {"size": size, "quality": meta["quality"]}
        if getattr(first, "revised_prompt", None):
            extra["revised_prompt"] = first.revised_prompt
        return success_response(
            image=image_ref, model=tier_id, prompt=prompt, aspect_ratio=aspect, provider="openai",
            modality="image" if is_edit else "text", extra=extra)

"""
NATIVE_TOOL = """from __future__ import annotations
def _handle_image_generate(args, **kw):
    prompt = args.get("prompt", "")
    if not prompt:
        return tool_error("prompt is required for image generation")
    aspect_ratio = args.get("aspect_ratio", DEFAULT_ASPECT_RATIO)
    upscale = args.get("upscale")
    task_id = kw.get("task_id")
    # Confinement chokepoint BEFORE any dispatch: every route receives sandbox-confined bytes.
    image_url, reference_image_urls, confine_error = _confine_source_images(
        args.get("image_url"), args.get("reference_image_urls"), task_id)
    if confine_error is not None:
        return confine_error
    # Order matters: explicit plugin provider, then the model-driven managed gateways (Krea /
    # Portal — only under the "nous"/unset selection, so BYO/direct FAL stays untouched), then FAL.
    sources = dict(image_url=image_url, reference_image_urls=reference_image_urls,
                   upscale=upscale if isinstance(upscale, bool) else None)
    controls = {name: args[name] for name in _CREATIVE_CONTROL_PARAMS if name in args}
    raw = _dispatch_to_plugin_provider(prompt, aspect_ratio, controls=controls or None, **sources)
    if raw is None:
        raw = _maybe_route_managed_model(prompt, aspect_ratio, controls=controls or None, **sources)
        if raw is not None:
            raw = _fall_back_to_fal(raw, prompt, aspect_ratio, sources)
    if raw is None:
        raw = image_generate_tool(prompt, aspect_ratio, **sources)
    return _postprocess_image_generate_result(raw, task_id=task_id)

def _build_dynamic_image_schema() -> dict[str, Any]:
    \"\"\"Render description AND params from the active model's capabilities; args it cannot
    honor are NOT advertised (the handler still accepts them for replay compat).\"\"\"
    base_desc = (
        "{lead} {edit_clause}Use this rather than calling image APIs or scripts directly. "
        "Returns the result in the `image` field — a URL or an absolute "
        "file path; reference it in your response using the current "
        "platform's file-delivery convention."
    )
    info = _active_image_capabilities()
    max_refs = int(info.get("max_reference_images") or 0)
    can_edit = "image" in set(info.get("modalities") or ["text"])
    static_props = IMAGE_GENERATE_SCHEMA["parameters"]["properties"]
    properties: dict[str, Any] = {
        "prompt": static_props["prompt"], "aspect_ratio": static_props["aspect_ratio"]}
    style_refs = info.get("source_image_role") == "style"
    if can_edit:
        if style_refs:
            lead = "Generate images, optionally styled on reference images, via configured model."
            edit_clause = "Pass style references in image_url. "
            properties["prompt"] = _STYLE_PROMPT_PARAM
            properties["image_url"] = _STYLE_IMAGE_URL_PARAM
            refs_desc = f"Up to {max_refs} more style references. URLs or absolute local paths."
        else:
            lead = ("Generate or edit images (with reference images) using the configured model."
                    if max_refs > 1 else "Generate or edit images using the configured image model.")
            edit_clause = "Pass image_url to edit / transform an existing image. "
            properties["image_url"] = _IMAGE_URL_PARAM
            refs_desc = (
                f"Up to {max_refs} additional reference images (style, "
                "character, or composition) guiding an edit. URLs or "
                "absolute local paths."
            )
        if max_refs > 1:
            properties["reference_image_urls"] = {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": max_refs,
                "description": refs_desc,
            }
    else:
        lead = "Generate images from text with the configured model (it cannot edit images)."
        edit_clause = ""
    if info.get("supports_upscale"):
        properties["upscale"] = _UPSCALE_PARAM
    for name in info.get("creative_controls") or []:
        if name in _CREATIVE_CONTROL_PARAMS:
            properties[name] = _CREATIVE_CONTROL_PARAMS[name]
    return {"description": base_desc.format(lead=lead, edit_clause=edit_clause),
            "parameters": {"type": "object", "properties": properties, "required": ["prompt"]}}

"""


def write_native_fixtures(root, monkeypatch):
    from runtime import patch_images
    fixture = {patch_images.PROVIDER: NATIVE_PROVIDER, patch_images.TOOL: NATIVE_TOOL}
    for relative, source in fixture.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    monkeypatch.setattr(patch_images, 'PINNED', {
        relative: hashlib.sha256(source.encode()).hexdigest() for relative, source in fixture.items()})
    return patch_images


@pytest.fixture
def native(tmp_path, monkeypatch):
    patch_images = write_native_fixtures(tmp_path, monkeypatch)
    patch_images.patch(tmp_path)
    calls = []
    saved = []
    def fail_factory(*args, **kwargs):
        return lambda error, error_type: {'success': False, 'error': error, 'error_type': error_type}
    def materialize(b64, url, **kwargs):
        saved.append((b64, url))
        return '/home/agent/.hermes/cache/generated/images/generated.png', None
    def request(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(data=[types.SimpleNamespace(
            b64_json='PNG_BASE64', portal_url='/api/images/' + 'a' * 32)], usage=None)
    sdk = types.SimpleNamespace(images=types.SimpleNamespace(generate=request))
    scope = {
        'DEFAULT_ASPECT_RATIO': 'square',
        'resolve_aspect_ratio': lambda value: value,
        '_resolve_endpoint': lambda: ('http://web:8080/provider/v1', 'synthetic-private-relay'),
        'error_factory': fail_factory, 'prompt_required_error': lambda *a: fail_factory()('prompt required', 'invalid_argument'),
        'import_openai': lambda *a: (None, None),
        '_resolve_model': lambda: ('gpt-image-2-medium', {'api_model': 'gpt-image-2', 'quality': 'medium'}),
        'size_for': lambda aspect: {'square': '1024x1024', 'landscape': '1536x1024', 'portrait': '1024x1536'}[aspect],
        'collect_source_images': lambda *a, **k: [], 'record_token_usage': lambda *a, **k: None,
        'materialize_image': materialize, 'success_response': lambda extra, **kwargs: {'success': True, **kwargs, **extra},
        'logger': types.SimpleNamespace(debug=lambda *a, **kw: None),
    }
    exec(compile((tmp_path / patch_images.PROVIDER).read_text(), '<native-provider>', 'exec'), scope)
    build_client = scope['_build_client']
    scope['_build_client'] = lambda *a: sdk
    return types.SimpleNamespace(root=tmp_path, scope=scope, patch=patch_images,
                                 calls=calls, saved=saved, sdk=sdk, build_client=build_client)


def test_native_build_patch_rejects_repeat_drift_and_links_without_partial_writes(tmp_path, monkeypatch):
    patch_images = write_native_fixtures(tmp_path, monkeypatch)
    provider = tmp_path / patch_images.PROVIDER
    tool = tmp_path / patch_images.TOOL
    before = provider.read_bytes()
    tool.write_text(tool.read_text() + '\n# drift\n')
    with pytest.raises(RuntimeError, match='not pristine'):
        patch_images.patch(tmp_path)
    assert provider.read_bytes() == before
    tool.write_text(NATIVE_TOOL)
    patch_images.patch(tmp_path)
    after = provider.read_bytes()
    with pytest.raises(RuntimeError, match='not pristine'):
        patch_images.patch(tmp_path)
    assert provider.read_bytes() == after
    # No target can redirect the trusted build patch to another file.
    tool.unlink()
    tool.symlink_to(provider)
    with pytest.raises(RuntimeError):
        patch_images.patch(tmp_path)


def test_native_provider_preserves_local_delivery_and_exposes_only_validated_portal_path(native):
    provider = native.scope['OpenAIImageGenProvider']()
    assert provider.capabilities() == {'modalities': ['text'], 'max_reference_images': 0}
    result = provider.generate('A garden', aspect_ratio='landscape')
    assert native.calls == [{'model': 'gpt-image-2', 'prompt': 'A garden', 'size': '1536x1024', 'n': 1, 'quality': 'medium'}]
    assert native.saved == [('PNG_BASE64', None)]
    assert result['image'].startswith('/') and result['image'].endswith('.png')
    assert result['web_image_url'] == '/api/images/' + 'a' * 32
    assert result['web_markdown'] == '![Imagen generada](' + result['web_image_url'] + ')'
    assert 'verbatim' in result['delivery_note']
    assert 'MEDIA:' not in json.dumps(result)


@pytest.mark.parametrize('portal_url', [None, 'https://remote/image.png', '//remote/path',
                                      '/api/images/' + 'A' * 32, '/api/images/' + 'a' * 32 + '?x=1',
                                      '/api/images/../secret', '/api/images/' + 'a' * 32 + '\n'])
def test_native_provider_rejects_untrusted_portal_paths_before_materialization(native, portal_url):
    native.sdk.images.generate = lambda **kwargs: types.SimpleNamespace(data=[types.SimpleNamespace(
        portal_url=portal_url, b64_json='PNG_BASE64', url='https://remote/never-fetch')])
    result = native.scope['OpenAIImageGenProvider']().generate('A garden')
    assert result['success'] is False
    assert result['error_type'] == 'invalid_response'
    assert native.saved == []


def test_native_provider_never_fetches_remote_output_or_sources(native):
    provider = native.scope['OpenAIImageGenProvider']()
    for sources in ({'image_url': '/etc/private'}, {'reference_image_urls': ['https://remote/private']}):
        result = provider.generate('An edit', **sources)
        assert result['error_type'] == 'modality_unsupported'
    assert native.calls == []
    native.sdk.images.generate = lambda **kwargs: types.SimpleNamespace(data=[types.SimpleNamespace(
        portal_url='/api/images/' + 'a' * 32, url='https://remote/never-fetch')])
    assert provider.generate('A garden')['error_type'] == 'invalid_response'
    assert native.saved == []


def test_native_provider_client_does_not_retry_billed_generations(native, monkeypatch):
    import sys
    bootstrap = types.ModuleType('agent.process_bootstrap')
    bootstrap.build_keepalive_http_client = lambda base: None
    monkeypatch.setitem(sys.modules, bootstrap.__name__, bootstrap)
    seen = {}
    def client(**kwargs):
        seen.update(kwargs)
        return 'stub-client'
    assert native.build_client(types.SimpleNamespace(OpenAI=client), 'http://web:8080/provider/v1', 'synthetic-relay') == 'stub-client'
    assert seen['max_retries'] == 0
    assert seen['timeout'] == 300.0
    assert seen['base_url'] == 'http://web:8080/provider/v1'
    assert seen['api_key'] == 'synthetic-relay'


def test_native_tool_hides_edit_fields_and_rejects_before_source_confinement(native):
    scope = {
        'DEFAULT_ASPECT_RATIO': 'square', '_CREATIVE_CONTROL_PARAMS': {},
        'IMAGE_GENERATE_SCHEMA': {'parameters': {'properties': {'prompt': {}, 'aspect_ratio': {}}}},
        '_IMAGE_URL_PARAM': {},
        '_active_image_capabilities': native.scope['OpenAIImageGenProvider']().capabilities,
        '_provider_error': lambda error, kind: {'success': False, 'error_type': kind},
        '_confine_source_images': lambda *args: pytest.fail('source IO must never run'),
    }
    exec(compile((native.root / native.patch.TOOL).read_text(), '<native-tool>', 'exec'), scope)
    schema = scope['_build_dynamic_image_schema']()
    assert set(schema['parameters']['properties']) == {'prompt', 'aspect_ratio'}
    assert 'web_markdown' in schema['description']
    for sources in ({'image_url': '/etc/private'}, {'reference_image_urls': ['https://remote/private']}):
        assert scope['_handle_image_generate']({'prompt': 'An edit', **sources})['error_type'] == 'modality_unsupported'


def test_native_tool_refuses_alternate_providers_and_never_falls_back(native):
    scope = {
        'DEFAULT_ASPECT_RATIO': 'square', '_CREATIVE_CONTROL_PARAMS': {},
        '_provider_error': lambda error, kind: {'success': False, 'error_type': kind},
        '_read_configured_image_provider': lambda: 'openai',
        '_confine_source_images': lambda *args: (None, None, None),
        '_dispatch_to_plugin_provider': lambda *args, **kwargs: None,
        '_maybe_route_managed_model': lambda *args, **kwargs: pytest.fail('must not select managed providers'),
        'image_generate_tool': lambda *args, **kwargs: pytest.fail('must not select FAL'),
    }
    exec(compile((native.root / native.patch.TOOL).read_text(), '<native-tool>', 'exec'), scope)
    assert scope['_handle_image_generate']({'prompt': 'A garden'})['error_type'] == 'provider_unavailable'
    scope['_dispatch_to_plugin_provider'] = lambda *args, **kwargs: pytest.fail('must not dispatch another provider')
    for provider in ('fal', 'openai-codex', None):
        scope['_read_configured_image_provider'] = lambda: provider
        assert scope['_handle_image_generate']({'prompt': 'A garden'})['error_type'] == 'provider_unavailable'


def test_native_provider_uncertain_failure_is_not_retried_and_warns_about_charge(native):
    attempts = []
    def timed_out(**kwargs):
        attempts.append(kwargs)
        raise TimeoutError('synthetic timeout')
    native.sdk.images.generate = timed_out
    result = native.scope['OpenAIImageGenProvider']().generate('A garden')
    assert len(attempts) == 1
    assert result['success'] is False
    assert result['error_type'] == 'api_error'
    assert 'Do not retry automatically' in result['error']
    assert 'outcome is unknown' in result['error']
    assert 'charged' in result['error']
    assert native.saved == []
