"""Build-only exact seam: all native profiles discover the same owner skills."""
import ast
from pathlib import Path

ANCHOR = '    roots += [(TIER_EXTERNAL, d) for d in get_external_skills_dirs()]\n'
INJECTION = '''    # personal-agent: fixed shared owner skills v1
    from agent.personal_shared_skills import shared_skill_roots
    roots += [(TIER_EXTERNAL, d) for d in shared_skill_roots()]
'''


def patch(root):
    target = Path(root) / 'agent/skill_utils.py'
    helper = Path(root) / 'agent/personal_shared_skills.py'
    if target.is_symlink() or helper.exists() or helper.is_symlink():
        raise RuntimeError('Shared skill patch target is not pristine')
    source = target.read_text()
    if source.count(ANCHOR) != 1 or 'fixed shared owner skills' in source:
        raise RuntimeError('Pinned shared skill seam no longer matches exactly once')
    result = source.replace(ANCHOR, ANCHOR + INJECTION, 1)
    ast.parse(result)
    helper.write_text(Path(__file__).with_name('shared_skills.py').read_text())
    target.write_text(result)


if __name__ == '__main__':
    patch('/opt/hermes')
