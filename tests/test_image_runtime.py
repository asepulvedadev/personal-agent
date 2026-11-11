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
    assert config['platform_toolsets']['api_server'] == ['file', 'owner-tool', 'image_gen', 'personal_projects']
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
        assert actual['platform_toolsets']['telegram'] == ['file', 'owner-tool', 'image_gen', 'personal_projects']
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


# Pinned gateway/parser excerpts exercise native collectors, not a delivery reimplementation.
NATIVE_GATEWAY = """from __future__ import annotations
import re
import json
from typing import Any, Optional
from gateway.media_repair import tool_name_by_call_id as _tool_name_by_call_id

_AUTO_APPEND_MEDIA_TOOL_NAMES = {"text_to_speech", "text_to_speech_tool", "image_generate"}


_JSON_MEDIA_TOOL_PATH_FIELDS = ("host_image", "image", "agent_visible_image")


_TOOL_MEDIA_RE = re.compile(
    r'MEDIA:((?:[A-Za-z]:[/\\\\]|/|~\\/)\\S+\\.(?:png|jpe?g|gif|webp|'
    r'mp4|mov|avi|mkv|webm|ogg|opus|mp3|wav|m4a|'
    r'flac|epub|pdf|zip|rar|7z|docx?|xlsx?|pptx?|'
    r'txt|csv|apk|ipa))',
    re.IGNORECASE)


def _collect_auto_append_media_tags(
    messages: list[dict[str, Any]], history_offset: int = 0,
    history_media_paths: Optional[set] = None) -> tuple[list[str], bool]:
    \"\"\"Collect real media tags from current-turn producer-tool results only.

    Producer allowlist: docs/logs/search results contain example MEDIA: strings that must never become
    attachments. If mid-run compression shrank the list below the history length the slice is
    untrustworthy, so scan every message (dedup via history_media_paths).

    1. Producer-tool allowlist: only tools that intentionally emit deliverable artifacts (TTS) are eligible.
    (Fixes the original report behind #16721.) 2. Current-turn isolation: only messages produced this turn
    are scanned, so a tool result from an earlier turn (still present in the full message list) cannot leak
    onto a later text-only reply (#34608).
    When that happens the slice boundary is no longer trustworthy, so fall back to scanning every message
    and rely on ``history_media_paths`` for dedup, preserving the compression-safe behaviour of #160. The
    producer-tool allowlist still applies on the fallback path.
    \"\"\"
    history_media_paths = history_media_paths or set()
    new_messages = (messages[history_offset:]
                    if history_offset and len(messages) >= history_offset else messages)

    tool_name_by_call_id = _tool_name_by_call_id(new_messages)

    media_tags: list[str] = []
    has_voice_directive = False
    for msg in new_messages:
        if msg.get("role") not in ("tool", "function"):
            continue
        call_id = str(msg.get("tool_call_id") or msg.get("call_id") or "")
        if tool_name_by_call_id.get(call_id) not in _AUTO_APPEND_MEDIA_TOOL_NAMES:
            continue
        content = str(msg.get("content") or "")
        tool_name = tool_name_by_call_id.get(call_id)
        # image_generate emits a JSON path field, not a MEDIA: tag; extract it: deterministic delivery.
        if tool_name == "image_generate" and "MEDIA:" not in content:
            try:
                payload = json.loads(content)
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("success"):
                for field in _JSON_MEDIA_TOOL_PATH_FIELDS:
                    path = payload.get(field)
                    if (isinstance(path, str)
                            and _TOOL_MEDIA_RE.fullmatch(f"MEDIA:{path}")
                            and path not in history_media_paths):
                        media_tags.append(f"MEDIA:{path}")
                        break
            continue
        if "MEDIA:" not in content:
            continue
        for match in _TOOL_MEDIA_RE.finditer(content):
            path = match.group(1).strip().rstrip('",}')
            if path and path not in history_media_paths:
                media_tags.append(f"MEDIA:{path}")
        if "[[audio_as_voice]]" in content:
            has_voice_directive = True

    return media_tags, has_voice_directive


def _collect_history_media_paths(agent_history: list[dict[str, Any]]) -> set:
    \"\"\"Dedup set of media paths already delivered (JSON-payload and assistant-message shapes alike).

    Missing the JSON-payload shape caused #46627; missing the assistant-message shape caused repeated
    delivery when the model echoed a previous MEDIA tag.
    \"\"\"
    paths: set = set()
    tool_name_by_call_id = _tool_name_by_call_id(agent_history)

    def _add_text_media_paths(content: str) -> None:
        for match in _TOOL_MEDIA_RE.finditer(content):
            path = match.group(1).strip().rstrip('",}')
            if path:
                paths.add(path)
        # The regex misses quoted/spaced paths extract_media accepts; use the same extractor to dedup.
        media_files, _ = BasePlatformAdapter.extract_media(content)
        paths.update(path for path, _is_voice in media_files)

    for msg in agent_history:
        role = msg.get("role")
        if role not in ("assistant", "tool", "function"):
            continue
        content = str(msg.get("content", "") or "")
        if "MEDIA:" in content:
            _add_text_media_paths(content)
            continue
        if role == "assistant":
            continue
        cid = str(msg.get("tool_call_id") or msg.get("call_id") or "")
        if tool_name_by_call_id.get(cid) == "image_generate":
            try:
                payload = json.loads(content)
            except Exception:
                payload = None
            if isinstance(payload, dict) and payload.get("success"):
                for field in _JSON_MEDIA_TOOL_PATH_FIELDS:
                    jp = payload.get(field)
                    if isinstance(jp, str) and jp:
                        paths.add(jp)
                        break
    return paths

"""

NATIVE_CALL_NAMES = """def tool_name_by_call_id(messages: list[dict[str, Any]]) -> dict[str, str]:
    \"\"\"Map assistant tool-call ids to tool names for the given messages.\"\"\"
    mapping: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for call in msg.get("tool_calls") or []:
            call_id = call.get("id") or call.get("call_id")
            name = str((call.get("function") or {}).get("name") or call.get("name") or "")
            if call_id and name:
                mapping[str(call_id)] = name
    return mapping

"""

NATIVE_NORMALIZE = """def normalize_tool_call_entries(args: dict[str, Any]) -> tuple[list[dict[str, Any]], Optional[str]]:
    \"\"\"Normalize ``tool_call`` arguments into a ``calls[]`` list of entries.

    Accepts the advertised batch shape ``{"calls": [{"name", "arguments"}, ...]}``
    and, tolerantly, the legacy single shape ``{"name": ..., "arguments": ...}``
    (a single call is a batch of one). Each entry's ``arguments`` is coerced to
    a dict (JSON strings parsed, ``None`` → ``{}``). Returns ``(entries, None)``
    or ``([], error_message)``.
    \"\"\"
    raw_calls = args.get("calls")
    if raw_calls is None:
        # Legacy single shape.
        if not str(args.get("name") or "").strip():
            return [], "tool_call requires 'calls' (an array of {name, arguments})"
        raw_calls = [{"name": args.get("name"), "arguments": args.get("arguments")}]
    if isinstance(raw_calls, str):
        # Tolerate the model emitting the batch envelope as a JSON string —
        # mirror the per-entry `arguments` handling below (#114484).
        try:
            raw_calls = json.loads(raw_calls)
        except json.JSONDecodeError as e:
            return [], f"tool_call 'calls' is not valid JSON: {e}"
    if isinstance(raw_calls, dict):
        raw_calls = [raw_calls]
    if not isinstance(raw_calls, list) or not raw_calls:
        return [], "tool_call 'calls' must be a non-empty array of {name, arguments}"

    entries: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_calls):
        if not isinstance(raw, dict):
            return [], f"tool_call calls[{position}] must be an object with 'name' and 'arguments'"
        name = str(raw.get("name") or "").strip()
        if not name:
            return [], f"tool_call calls[{position}] requires a 'name'"
        if name in BRIDGE_TOOL_NAMES:
            return [], f"tool_call cannot invoke '{name}' (it is itself a bridge tool)"
        raw_args = raw.get("arguments")
        if raw_args is None or (isinstance(raw_args, str) and not raw_args.strip()):
            # "" / whitespace is how some OpenAI-compatible gateways spell "no arguments" for a
            # parameterless tool (#83937); the loop already treats an empty outer arguments string
            # as {} (turn_tool_validation), and a missing required param still surfaces below via
            # validate_deferred_call_args instead of an opaque JSON parse error.
            raw_args = {}
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError as e:
                return [], f"tool_call calls[{position}].arguments is not valid JSON: {e}"
        if not isinstance(raw_args, dict):
            return [], f"tool_call calls[{position}].arguments must be an object"
        entries.append({"name": name, "arguments": raw_args})
    return entries, None

"""

NATIVE_APPEND_MEDIA = """def _append_auto_media_tags(self, final_response: str, result, agent_history, history_media_paths) -> str:
    \"\"\"Append MEDIA:<path> tags from tool results (e.g. TTS) that the model's final text omits, so
    extract_media() delivers each file once. Scoped to THIS turn (slice at len(agent_history)) so
    a stale MEDIA: path from an earlier turn never rides a later reply; the history-path dedup is
    the secondary guard — and the sole one when mid-run compression shrank the list.\"\"\"
    from gateway.run import _collect_auto_append_media_tags
    if "MEDIA:" in final_response:
        return final_response
    # Scan tool results for MEDIA:<path> tags that need to be delivered as native audio/file
    # attachments. The TTS tool embeds MEDIA: tags in its JSON response, but the model's final text
    # reply usually doesn't include them. We collect unique tags from tool results and append any that
    # aren't already present in the final response, so the adapter's extract_media() can find and
    # deliver the files exactly once. Scope the scan to THIS turn's tool results only. ``agent_history``
    # was passed into run_conversation as ``conversation_history``, so the agent's returned ``messages``
    # list is ``agent_history`` followed by the messages produced this turn. Slicing at
    # ``len(agent_history)`` isolates the current turn precisely, so a stale MEDIA: path emitted by a
    # tool several turns earlier (still present in the full message list) can never leak onto a later
    # text-only reply. (Fixes #34608) Path-based deduplication against _history_media_paths (collected
    # before run_conversation) is retained as a secondary guard. It is also the sole guard on the
    # fallback branch taken when mid-run context compression shrinks the message list below the original
    # history length, preserving the compression-safe behaviour of #160.
    media_tags, has_voice_directive = _collect_auto_append_media_tags(
        result.get("messages", []), history_offset=len(agent_history), history_media_paths=history_media_paths,
    )
    if not media_tags:
        return final_response
    unique_tags = (["[[audio_as_voice]]"] if has_voice_directive else []) + list(dict.fromkeys(media_tags))
    return final_response + "\\n" + "\\n".join(unique_tags)
"""


def write_native_fixtures(root, monkeypatch):
    from runtime import patch_images
    fixture = {patch_images.PROVIDER: NATIVE_PROVIDER, patch_images.TOOL: NATIVE_TOOL,
               'gateway/run.py': NATIVE_GATEWAY}
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


@pytest.fixture
def gateway_native(native, monkeypatch):
    """Compile the patched, original gateway collectors with native bridge normalization."""
    import re
    import sys
    from typing import Any, Optional
    namespace = {'json': json, 're': re, 'Any': Any, 'Optional': Optional,
                 'BRIDGE_TOOL_NAMES': {'tool_search', 'tool_describe', 'tool_call'}}
    exec(compile(NATIVE_CALL_NAMES + NATIVE_NORMALIZE, '<pinned-bridge-parser>', 'exec'), namespace)
    namespace['_tool_name_by_call_id'] = namespace['tool_name_by_call_id']
    namespace['BasePlatformAdapter'] = types.SimpleNamespace(
        extract_media=lambda content: ([(match.group(1), False) for match in namespace['_TOOL_MEDIA_RE'].finditer(content)], content))
    bridge = types.ModuleType('tools.tool_search_validation')
    bridge.normalize_tool_call_entries = namespace['normalize_tool_call_entries']
    monkeypatch.setitem(sys.modules, bridge.__name__, bridge)
    source = (native.root / 'gateway/run.py').read_text()
    tree = ast.parse(source)
    # Execute native functions/globals without booting the live gateway or importing its dependencies.
    function_names = {'_personal_image_tool_name_by_call_id', '_collect_auto_append_media_tags', '_collect_history_media_paths'}
    constant_names = {'_AUTO_APPEND_MEDIA_TOOL_NAMES', '_JSON_MEDIA_TOOL_PATH_FIELDS', '_TOOL_MEDIA_RE'}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in function_names:
            selected.append(node)
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in constant_names for target in node.targets):
            selected.append(node)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<pinned-gateway-media>', 'exec'), namespace)
    gateway = types.ModuleType('gateway.run')
    gateway._collect_auto_append_media_tags = namespace['_collect_auto_append_media_tags']
    monkeypatch.setitem(sys.modules, gateway.__name__, gateway)
    exec(compile(NATIVE_APPEND_MEDIA, '<pinned-turn-runner-media>', 'exec'), namespace)
    return namespace


def image_messages(wrapper=None, *, result=None, call_id='image-call', result_id=None, outer='tool_call'):
    if wrapper is None:
        wrapper = {'calls': [{'name': 'image_generate', 'arguments': {'prompt': 'A square garden', 'aspect_ratio': 'square'}}]}
    return [
        {'role': 'assistant', 'tool_calls': [{'id': call_id, 'type': 'function',
         'function': {'name': outer, 'arguments': json.dumps(wrapper)}}]},
        {'role': 'tool', 'tool_call_id': result_id or call_id, 'content': json.dumps(result if result is not None else {
            'success': True, 'image': '/home/agent/.hermes/cache/generated/images/generated.png'})},
    ]


def test_native_gateway_delivers_actual_deferred_production_shape_once(gateway_native):
    messages = image_messages()
    path = '/home/agent/.hermes/cache/generated/images/generated.png'
    collect = gateway_native['_collect_auto_append_media_tags']
    assert collect(messages) == (['MEDIA:' + path], False)
    append = gateway_native['_append_auto_media_tags']
    rendered = append(None, 'Generated image.', {'messages': messages}, [], set())
    assert rendered == 'Generated image.\nMEDIA:' + path
    assert append(None, rendered, {'messages': messages}, [], set()) == rendered


@pytest.mark.parametrize('wrapper', [
    {'calls': {'name': 'image_generate', 'arguments': {'prompt': 'Garden'}}},
    {'calls': json.dumps([{'name': 'image_generate', 'arguments': {'prompt': 'Garden'}}])},
    {'calls': json.dumps({'name': 'image_generate', 'arguments': json.dumps({'prompt': 'Garden'})})},
    {'name': 'image_generate', 'arguments': {'prompt': 'Garden'}},
    {'name': 'image_generate', 'arguments': json.dumps({'prompt': 'Garden'})},
    {'name': ' image_generate ', 'arguments': None},
])
def test_native_gateway_supports_the_existing_single_local_bridge_shapes(gateway_native, wrapper):
    assert gateway_native['_collect_auto_append_media_tags'](image_messages(wrapper))[0] == [
        'MEDIA:/home/agent/.hermes/cache/generated/images/generated.png']


def test_native_gateway_deferred_history_dedup_and_current_turn_isolation(gateway_native):
    messages = image_messages()
    path = '/home/agent/.hermes/cache/generated/images/generated.png'
    history_paths = gateway_native['_collect_history_media_paths'](messages)
    assert history_paths == {path}
    collect = gateway_native['_collect_auto_append_media_tags']
    assert collect(messages, history_media_paths=history_paths) == ([], False)
    assert collect(messages, history_offset=len(messages)) == ([], False)
    assert collect([*messages, {'role': 'assistant', 'content': 'A later reply.'}], history_offset=len(messages)) == ([], False)
    # Compression can invalidate the offset: history-path dedup remains the secondary guard.
    assert collect(messages, history_offset=len(messages) + 10, history_media_paths=history_paths) == ([], False)
    assert gateway_native['_append_auto_media_tags'](None, 'A later reply.', {'messages': messages}, messages, history_paths) == 'A later reply.'


@pytest.mark.parametrize('wrapper', [
    {'calls': []}, {'calls': 'not-json'}, {'calls': [None]}, {'calls': None},
    {'calls': [{'name': 'image_generate', 'arguments': []}]},
    {'calls': [{'name': 'image_generate', 'arguments': 'not-json'}]},
    {'calls': [{'name': 'image_generate'}, {'name': 'image_generate'}]},
    {'calls': [{'name': 'terminal', 'arguments': {'command': 'echo fakeimage'}}]},
    {'calls': [{'name': 'image_generate'}, {'name': 'terminal'}]},
    {'calls': [{'name': 'tool_call', 'arguments': {'name': 'image_generate'}}]},
    {'calls': [{'name': 'tool_describe', 'arguments': {'name': 'image_generate'}}]},
    {'name': 'terminal', 'arguments': {'command': 'echo fakeimage'}},
])
def test_native_gateway_rejects_malformed_multiple_or_nonimage_bridges(gateway_native, wrapper):
    messages = image_messages(wrapper)
    assert gateway_native['_collect_auto_append_media_tags'](messages) == ([], False)
    assert gateway_native['_collect_history_media_paths'](messages) == set()


def test_native_gateway_rejects_failed_mismatched_or_self_claimed_results(gateway_native):
    collect = gateway_native['_collect_auto_append_media_tags']
    failed = image_messages(result={'success': False, 'image': '/tmp/fake.png'})
    mismatched = image_messages(result_id='wrong-id')
    invalid_path = image_messages(result={'success': True, 'image': 'https://remote/fake.png'})
    untrusted = image_messages({'name': 'terminal', 'arguments': {}})
    untrusted[1]['name'] = 'image_generate'
    untrusted[1]['content'] = json.dumps({'name': 'image_generate', 'success': True, 'image': '/tmp/fake.png'})
    for messages in (failed, mismatched, invalid_path, untrusted, [untrusted[1]]):
        assert collect(messages) == ([], False)
    # Invalid outer argument JSON/shape never confers producer identity.
    for args in ('not-json', '[]', [], None):
        messages = image_messages()
        messages[0]['tool_calls'][0]['function']['arguments'] = args
        assert collect(messages) == ([], False)


def test_native_gateway_existing_direct_images_tts_and_unrelated_output_stay_identical(gateway_native):
    collect = gateway_native['_collect_auto_append_media_tags']
    direct = image_messages({'prompt': 'Garden'}, outer='image_generate')
    assert collect(direct) == (['MEDIA:/home/agent/.hermes/cache/generated/images/generated.png'], False)
    assert gateway_native['_collect_history_media_paths'](direct) == {'/home/agent/.hermes/cache/generated/images/generated.png'}
    tts = image_messages(outer='text_to_speech', call_id='tts-call')
    tts[1]['content'] = 'MEDIA:/tmp/generated.ogg [[audio_as_voice]]'
    assert collect(tts) == (['MEDIA:/tmp/generated.ogg'], True)
    terminal = image_messages(outer='terminal', call_id='terminal-call')
    terminal[1]['content'] = 'MEDIA:/tmp/fake.png'
    assert collect(terminal) == ([], False)
    assert collect([*direct, *tts, *terminal]) == ([
        'MEDIA:/home/agent/.hermes/cache/generated/images/generated.png', 'MEDIA:/tmp/generated.ogg'], True)


def test_native_gateway_wrapper_identity_keeps_original_last_call_wins(gateway_native):
    image = image_messages()
    terminal = image_messages({'name': 'terminal', 'arguments': {}})
    assert gateway_native['_collect_auto_append_media_tags']([*image[:1], *terminal]) == ([], False)
    direct_terminal = image_messages(outer='terminal')
    assert gateway_native['_collect_auto_append_media_tags']([*image[:1], *direct_terminal]) == ([], False)


def test_native_gateway_accepts_dict_outer_arguments_without_mutating_messages(gateway_native):
    messages = image_messages()
    call = messages[0]['tool_calls'][0]
    call['function']['arguments'] = json.loads(call['function']['arguments'])
    original = deepcopy(messages)
    assert gateway_native['_collect_auto_append_media_tags'](messages)[0] == [
        'MEDIA:/home/agent/.hermes/cache/generated/images/generated.png']
    assert messages == original


def test_image_patch_gateway_drift_is_rejected_before_any_target_write(tmp_path, monkeypatch):
    patch_images = write_native_fixtures(tmp_path, monkeypatch)
    original = {relative: (tmp_path / relative).read_bytes() for relative in patch_images.PINNED}
    (tmp_path / patch_images.GATEWAY).write_text(NATIVE_GATEWAY + '\n# unexpected drift\n')
    with pytest.raises(RuntimeError, match='not pristine'):
        patch_images.patch(tmp_path)
    for relative in (patch_images.PROVIDER, patch_images.TOOL):
        assert (tmp_path / relative).read_bytes() == original[relative]
