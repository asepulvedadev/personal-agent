"""Build-only native OpenAI image seam for the pinned Hermes source.

The web service owns paid credentials and the authenticated image store. Hermes
receives only a relay token, caches the generated PNG for native Telegram delivery,
and passes the private portal path back to Hermes Web. No edit/source IO is allowed.
"""
import ast
import hashlib
from pathlib import Path
import sys

PROVIDER = 'plugins/image_gen/openai/__init__.py'
TOOL = 'tools/image_generation_tool.py'
PINNED = {
    PROVIDER: '9894554d437f9531267a74258a75b5a790db8cbec969e38db81576e46c718cd0',
    TOOL: '61728818282eec3f3d108ad7287c724c70e0b3746cee1ad840c7c5fc838f0c86',
}

PROVIDER_REPLACEMENTS = (
    ('import logging\n', 'import logging\nimport re\n'),
    ('    kwargs: dict[str, Any] = {"api_key": api_key, "default_headers": {"OpenAI-Project": ""}}\n',
     '    kwargs: dict[str, Any] = {"api_key": api_key, "default_headers": {"OpenAI-Project": ""},\n'
     '                              "max_retries": 0, "timeout": 300.0}\n'),
    ('        # images.edit() accepts up to 16 source images.\n'
     '        return {"modalities": ["text", "image"], "max_reference_images": 16}\n',
     '        # personal-agent: the private relay supports text generation only.\n'
     '        return {"modalities": ["text"], "max_reference_images": 0}\n'),
    ('        base_url, api_key = _resolve_endpoint()\n',
     '        if image_url or reference_image_urls:\n'
     '            return error_factory("openai", aspect)(\n'
     '                "Image editing and source images are not supported by the private relay.",\n'
     '                "modality_unsupported")\n'
     '        base_url, api_key = _resolve_endpoint()\n'),
    ('        first = data[0]\n'
     '        image_ref, err = materialize_image(\n'
     '            getattr(first, "b64_json", None), getattr(first, "url", None),\n',
     '        first = data[0]\n'
     '        portal_url = getattr(first, "portal_url", None)\n'
     '        if not isinstance(portal_url, str) or not re.fullmatch(r"/api/images/[0-9a-f]{32}", portal_url):\n'
     '            return fail("Private image response has no valid portal path", "invalid_response")\n'
     '        if not isinstance(getattr(first, "b64_json", None), str) or not first.b64_json:\n'
     '            return fail("Private image response has no base64 image", "invalid_response")\n'
     '        image_ref, err = materialize_image(\n'
     '            first.b64_json, None,\n'),
    ('            return fail(f"OpenAI image {\'editing\' if is_edit else \'generation\'} failed: {exc}", "api_error")\n',
     '            return fail(f"OpenAI image generation failed: {exc}. Do not retry automatically. "\n'
     '                        "If the connection failed or timed out, the outcome is unknown and the request "\n'
     '                        "may already have been charged.", "api_error")\n'),
    ('        extra: dict[str, Any] = {"size": size, "quality": meta["quality"]}\n',
     '        extra: dict[str, Any] = {\n'
     '            "size": size, "quality": meta["quality"], "web_image_url": portal_url,\n'
     '            "web_markdown": f"![Imagen generada]({portal_url})",\n'
     '            "delivery_note": (\n'
     '                "In Hermes Web include web_markdown verbatim in your final reply. "\n'
     '                "Do not use the local cache path as a web link. On Telegram the gateway "\n'
     '                "automatically attaches the local image; do not add a MEDIA marker or the web link."),\n'
     '        }\n'),
)

TOOL_REPLACEMENTS = (
    ('def _handle_image_generate(args, **kw):\n'
     '    prompt = args.get("prompt", "")\n',
     'def _handle_image_generate(args, **kw):\n'
     '    # Reject sources before confinement resolves/reads any URL or local file.\n'
     '    if args.get("image_url") or args.get("reference_image_urls"):\n'
     '        return _provider_error(\n'
     '            "Image editing and source images are not supported by the private relay.",\n'
     '            "modality_unsupported")\n'
     '    if _read_configured_image_provider() != "openai":\n'
     '        return _provider_error("The private OpenAI image backend is not configured.", "provider_unavailable")\n'
     '    prompt = args.get("prompt", "")\n'),
    ('    if raw is None:\n'
     '        raw = _maybe_route_managed_model(prompt, aspect_ratio, controls=controls or None, **sources)\n'
     '        if raw is not None:\n'
     '            raw = _fall_back_to_fal(raw, prompt, aspect_ratio, sources)\n'
     '    if raw is None:\n'
     '        raw = image_generate_tool(prompt, aspect_ratio, **sources)\n',
     '    if raw is None:\n'
     '        return _provider_error("The private OpenAI image backend is unavailable; no alternate provider was used.",\n'
     '                               "provider_unavailable")\n'),
    ('        "platform\'s file-delivery convention."\n',
     '        "platform\'s file-delivery convention. In Hermes Web include the returned "\n'
     '        "web_markdown verbatim in your final reply, not the local cache path. "\n'
     '        "On Telegram the gateway attaches the image automatically; do not add MEDIA markers. "\n'
     '        "Do not retry failed or uncertain image generation automatically; report the error."\n'),
)


def patch(root):
    """Validate both pristine targets before writing either; repeated application refuses."""
    root = Path(root)
    updates = []
    for relative, replacements in ((PROVIDER, PROVIDER_REPLACEMENTS), (TOOL, TOOL_REPLACEMENTS)):
        target = root / relative
        if any(path.is_symlink() for path in [root, target, *target.parents]):
            raise RuntimeError('Image patch targets cannot be symlinks')
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != PINNED[relative]:
            raise RuntimeError('Pinned Hermes image source is not pristine')
        source = raw.decode('utf-8')
        for old, new in replacements:
            if source.count(old) != 1:
                raise RuntimeError('Pinned Hermes image seam no longer matches exactly once')
            source = source.replace(old, new, 1)
        ast.parse(source, filename=str(target))
        updates.append((target, source))
    for target, source in updates:
        target.write_text(source, encoding='utf-8')


if __name__ == '__main__':
    patch(sys.argv[1] if len(sys.argv) == 2 else '/opt/hermes')
