"""Build-only, hash-pinned Telegram seam patch; rejects upstream drift."""
from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path

PINNED_ADAPTER_SHA256 = "92c1220ca56b3c71001e89f9329b0427657cd69c189bf0d579c58ce010ba4427"
PINNED_HERMES_COMMIT = "3b421578588243f920886e6fe79a6faa87ea026e"
RELATIVE_ADAPTER = "plugins/platforms/telegram/adapter.py"
PATCH_MARKER = "# personal-agent: image-owned quarantine ingress v1"
ANCHOR = '''    def _register_handlers(self, app) -> None:
        """Register every PTB handler on ``app`` (initial connect and the transient-init rebuild)."""
        table = getattr(app, "handlers", None)
'''
LATE_ANCHOR = '''            current[:] = prior[:first_core] + added + prior[first_core:]

    async def _build_ptb_requests(self) -> tuple:
'''
_INJECTION = '''        # personal-agent: image-owned quarantine ingress v1
        from importlib.util import module_from_spec, spec_from_file_location
        _q_spec = spec_from_file_location("_personal_agent_quarantine", "/opt/runtime/quarantine_ingress.py")
        if _q_spec is None or _q_spec.loader is None:
            raise RuntimeError("Image-owned quarantine ingress is unavailable")
        _q_module = module_from_spec(_q_spec)
        import sys
        sys.modules[_q_spec.name] = _q_module
        _q_spec.loader.exec_module(_q_module)
        _q_module.install_telegram_guard(app, self)
'''
_LATE_INJECTION = '''        # personal-agent: verify late registration before returning to dispatch
        try:
            from importlib.util import module_from_spec, spec_from_file_location
            _q_spec = spec_from_file_location("_personal_agent_quarantine", "/opt/runtime/quarantine_ingress.py")
            if _q_spec is None or _q_spec.loader is None:
                raise RuntimeError("Image-owned quarantine ingress is unavailable")
            _q_module = module_from_spec(_q_spec)
            import sys
            sys.modules[_q_spec.name] = _q_module
            _q_spec.loader.exec_module(_q_module)
            _q_module.assert_telegram_guard_order(native, self)
        except BaseException:
            handlers.clear()
            handlers.update(before)
            raise

'''


def patch_source(source: bytes, *, expected_sha256=PINNED_ADAPTER_SHA256) -> bytes:
    text = source.decode("utf-8")
    # Idempotence is accepted only if removing our exact injection restores the pinned file.
    if PATCH_MARKER in text:
        if text.count(_INJECTION) != 1 or text.count(_LATE_INJECTION) != 1 or text.count(PATCH_MARKER) != 1:
            raise ValueError("Quarantine patch marker differs from the trusted patch")
        original = text.replace(_INJECTION, "", 1).replace(_LATE_INJECTION, "", 1)
        if hashlib.sha256(original.encode()).hexdigest() != expected_sha256:
            raise ValueError("Patched adapter does not recover the pinned upstream hash")
        return source
    if hashlib.sha256(source).hexdigest() != expected_sha256 or text.count(ANCHOR) != 1 or text.count(LATE_ANCHOR) != 1:
        raise ValueError("Pinned Telegram adapter hash/anchor mismatch; refuse upstream drift")
    replacement = ANCHOR.replace('        table = getattr(app, "handlers", None)\n', _INJECTION + '        table = getattr(app, "handlers", None)\n')
    patched = text.replace(ANCHOR, replacement, 1)
    patched = patched.replace(LATE_ANCHOR, LATE_ANCHOR.replace('    async def _build_ptb_requests', _LATE_INJECTION + '    async def _build_ptb_requests'), 1)
    ast.parse(patched)
    return patched.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Pinned Hermes source root inside the build image")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    target = root / RELATIVE_ADAPTER
    if target.is_symlink() or not target.resolve(strict=True).is_relative_to(root):
        raise ValueError("Adapter must be a regular file inside the image source root")
    before = target.read_bytes()
    after = patch_source(before)
    if after != before:
        target.write_bytes(after)
    print("Pinned Telegram quarantine seam verified")


if __name__ == "__main__":
    main()
