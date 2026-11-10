"""The owner-authored style is self-contained and uses existing profile delivery."""
import hashlib
import json
from pathlib import Path

import pytest

from app.quarantine import Quarantine
from runtime.workspace_config import seed_skills


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'focused-replies' / 'SKILL.md'


def test_focused_replies_is_a_standalone_inspectable_skill(tmp_path):
    raw = SKILL.read_bytes()
    assert sorted(p.name for p in SKILL.parent.iterdir()) == ['SKILL.md']
    assert not SKILL.is_symlink()
    assert not SKILL.stat().st_mode & 0o111
    item = Quarantine(tmp_path / 'control').create(
        raw, 'SKILL.md', {'kind': 'upload', 'filename': 'SKILL.md'})
    assert item['state'] == 'quarantined'
    assert item['eligible'] is True
    assert item['findings'] == []
    assert item['metadata']['SKILL.md']['name'] == 'focused-replies'
    assert item['sha256'] == hashlib.sha256(raw).hexdigest()
    assert not list((tmp_path / 'control').rglob('approved.json'))


@pytest.mark.parametrize('profile', ['default', 'carpediev'])
def test_focused_replies_seeds_each_profile_without_replacing_owner_state(tmp_path, profile):
    home = tmp_path / '.hermes'
    if profile != 'default':
        home = home / 'profiles' / profile
    home.mkdir(parents=True)
    unchanged = {
        'SOUL.md': 'Owner identity stays separate.\n',
        'config.yaml': json.dumps({'approvals': {'mode': 'smart'}}),
        '.approved-deliveries.json': '{}',
    }
    for name, text in unchanged.items():
        (home / name).write_text(text)
    seed_skills(home, ROOT / 'skills')
    target = home / 'skills' / 'project' / 'focused-replies' / 'SKILL.md'
    assert target.read_bytes() == SKILL.read_bytes()
    assert {name: (home / name).read_text() for name in unchanged} == unchanged
    owner_edit = target.read_text() + '\nOwner preference retained.\n'
    target.write_text(owner_edit)
    seed_skills(home, ROOT / 'skills')
    assert target.read_text() == owner_edit
    assert {name: (home / name).read_text() for name in unchanged} == unchanged
