"""Build-only exact seam patch, after native profile-route mirroring."""
import ast
from pathlib import Path

ANCHOR = '            self._app["api_server_adapter"] = self\n'
INJECTION = '''            # personal-agent: fixed default-profile configuration editor v1
            from importlib.util import module_from_spec, spec_from_file_location
            _cf_spec = spec_from_file_location("_personal_agent_config_files", "/opt/runtime/config_files.py")
            if _cf_spec is None or _cf_spec.loader is None:
                raise RuntimeError("Image-owned configuration editor is unavailable")
            _cf_module = module_from_spec(_cf_spec)
            _cf_spec.loader.exec_module(_cf_module)
            _cf_module.register_config_files(self._app, self)
'''


def patch(root):
    path = Path(root) / 'gateway/platforms/api_server.py'
    if path.is_symlink():
        raise RuntimeError('Pinned configuration patch target must not be a symlink')
    source = path.read_text()
    if source.count(ANCHOR) != 1 or 'personal-agent: fixed default-profile configuration editor' in source:
        raise RuntimeError('Pinned Hermes configuration seam no longer matches exactly once')
    result = source.replace(ANCHOR, ANCHOR + INJECTION, 1)
    ast.parse(result)
    path.write_text(result)


if __name__ == '__main__':
    patch('/opt/hermes')
