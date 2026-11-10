"""Shared owner skills use one bounded native search root, not profile copies."""
import sys
import types
from pathlib import Path

import pytest


def test_fixed_shared_root_is_live_and_never_follows_links(tmp_path):
    from runtime.shared_skills import shared_skill_roots
    root = tmp_path / 'shared-skills'
    assert shared_skill_roots(root) == []
    folder = root / 'focus'; folder.mkdir(parents=True)
    root.chmod(0o700)
    (folder / 'SKILL.md').write_text('safe')
    assert shared_skill_roots(root) == [root]
    target = tmp_path / 'private'; target.mkdir()
    (folder / 'resource').symlink_to(target)
    with pytest.raises(RuntimeError): shared_skill_roots(root)


def test_native_patch_keeps_precedence_and_applies_to_future_profiles(tmp_path, monkeypatch):
    from runtime.patch_shared_skills import patch
    from runtime.shared_skills import shared_skill_roots
    source = '''from pathlib import Path
TIER_EXTERNAL = 3

def get_skill_search_roots(local):
    roots = [(1, local)]
    roots += [(TIER_EXTERNAL, d) for d in get_external_skills_dirs()]
    seen = set()
    return [(t, d) for t, d in roots if not (d in seen or seen.add(d))]
'''
    target = tmp_path / 'agent/skill_utils.py'
    target.parent.mkdir(); target.write_text(source)
    patch(tmp_path)
    with pytest.raises(RuntimeError): patch(tmp_path)
    root = tmp_path / 'shared-skills'; root.mkdir(mode=0o700)
    stub = types.ModuleType('agent.personal_shared_skills')
    stub.shared_skill_roots = lambda: shared_skill_roots(root)
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    namespace = {'get_external_skills_dirs': lambda: [Path('/other')]}
    exec(compile(target.read_text(), str(target), 'exec'), namespace)
    for profile in ('default', 'carpediev', 'app-config', 'future'):
        local = Path('/profiles') / profile / 'skills'
        assert namespace['get_skill_search_roots'](local) == [(1, local), (3, Path('/other')), (3, root)]
    assert (tmp_path / 'agent/personal_shared_skills.py').read_text() == Path('runtime/shared_skills.py').read_text()
