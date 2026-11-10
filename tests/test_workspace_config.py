"""Portable assistant defaults and hash-bound, non-destructive skill delivery."""
import hashlib
import json
from pathlib import Path

import pytest

from runtime.workspace_config import (
    assistant_config, install_identity, seed_skills, sync_approved_skills,
)


def approved(profile='default', name='imported-example'):
    content = f'---\nname: {name}\ndescription: A reviewed example.\n---\n\n# Example\nUse the requested read-only procedure.\n'
    return {'id': 'a' * 32, 'profile': profile, 'name': name, 'content': content,
            'sha256': hashlib.sha256(content.encode()).hexdigest(), 'source_sha256': 'b' * 64}


def test_private_profiles_and_quiet_smart_defaults():
    config = assistant_config()
    assert config['approvals']['mode'] == 'smart'
    assert config['approvals']['destructive_slash_confirm'] is False
    assert config['display']['suppress_warning_notifications'] is True
    assert config['display']['tool_progress'] == 'off'
    assert config['telegram']['gateway_restart_notification'] is False
    assert 'command_allowlist' not in config


def test_identities_preserve_existing_owner_improvements(tmp_path):
    home = tmp_path / '.hermes'
    install_identity(home, 'default')
    assert 'Hermes Web' in (home / 'SOUL.md').read_text()
    original = (home / 'SOUL.md').read_text() + '\nMy later personal preference.\n'
    (home / 'SOUL.md').write_text(original)
    install_identity(home, 'default')
    assert (home / 'SOUL.md').read_text() == original
    carpe = home / 'profiles' / 'carpediev'
    install_identity(carpe, 'carpediev')
    assert 'Carpediev' in (carpe / 'SOUL.md').read_text()
    assert (carpe / 'SOUL.md').stat().st_ino != (home / 'SOUL.md').stat().st_ino


def test_seeded_skills_preserve_local_edits(tmp_path):
    source = tmp_path / 'source' / 'github-cli'
    source.mkdir(parents=True)
    (source / 'SKILL.md').write_text('---\nname: github-cli\ndescription: GitHub work.\n---\n# GitHub\n')
    home = tmp_path / 'home'
    seed_skills(home, source.parent)
    target = home / 'skills' / 'project' / 'github-cli' / 'SKILL.md'
    assert target.read_bytes() == (source / 'SKILL.md').read_bytes()
    target.write_text(target.read_text() + '\nOwner improvement.\n')
    seed_skills(home, source.parent)
    assert 'Owner improvement.' in target.read_text()


def test_only_exact_owner_approved_content_is_imported(tmp_path):
    item = approved()
    assert sync_approved_skills(tmp_path, [item]) == 1
    target = tmp_path / 'skills' / 'approved-imports' / item['name'] / 'SKILL.md'
    assert target.read_text() == item['content']
    assert sync_approved_skills(tmp_path, [item]) == 0
    assert not (tmp_path / 'quarantine').exists()


@pytest.mark.parametrize('change', [
    {'name': '../SOUL'}, {'name': '/etc/passwd'}, {'profile': 'root'},
    {'sha256': '0' * 64}, {'content': 'changed after review'},
])
def test_rejected_import_never_changes_existing_skill(tmp_path, change):
    item = approved()
    sync_approved_skills(tmp_path, [item])
    target = tmp_path / 'skills' / 'approved-imports' / item['name'] / 'SKILL.md'
    before = target.read_bytes()
    with pytest.raises(ValueError):
        sync_approved_skills(tmp_path, [{**item, **change}])
    assert target.read_bytes() == before


def test_cross_profile_approval_is_kept_separate(tmp_path):
    item = approved(profile='carpediev')
    assert sync_approved_skills(tmp_path, [item]) == 1
    assert not (tmp_path / 'skills' / 'approved-imports' / item['name']).exists()
    assert (tmp_path / 'profiles' / 'carpediev' / 'skills' / 'approved-imports' / item['name'] / 'SKILL.md').exists()


def test_symlinked_skill_directory_is_never_followed(tmp_path):
    home = tmp_path / 'home'
    outside = tmp_path / 'outside'
    home.mkdir()
    outside.mkdir()
    (home / 'skills').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        sync_approved_skills(home, [approved()])
    assert not list(outside.iterdir())


def test_existing_same_name_is_not_overwritten(tmp_path):
    item = approved()
    target = tmp_path / 'skills' / 'approved-imports' / item['name']
    target.mkdir(parents=True)
    (target / 'SKILL.md').write_text('Existing owner content')
    with pytest.raises(ValueError):
        sync_approved_skills(tmp_path, [item])
    assert (target / 'SKILL.md').read_text() == 'Existing owner content'


def test_invalidated_source_revokes_only_its_managed_import(tmp_path):
    item = approved()
    sync_approved_skills(tmp_path, [item])
    target = tmp_path / 'skills' / 'approved-imports' / item['name'] / 'SKILL.md'
    unrelated = tmp_path / 'skills' / 'manual' / 'SKILL.md'
    unrelated.parent.mkdir()
    unrelated.write_text('Independent owner skill')
    assert sync_approved_skills(tmp_path, []) == 1
    assert not target.exists()
    assert unrelated.read_text() == 'Independent owner skill'
    assert list((tmp_path / 'disabled-imports').rglob('SKILL.md'))


def test_tampered_managed_skill_is_disabled_without_deleting_owner_changes(tmp_path):
    item = approved()
    sync_approved_skills(tmp_path, [item])
    target = tmp_path / 'skills' / 'approved-imports' / item['name'] / 'SKILL.md'
    target.write_text('Owner changed this after approval')
    assert sync_approved_skills(tmp_path, [item]) == 1
    assert not target.exists()
    disabled = list((tmp_path / 'disabled-imports').rglob('SKILL.md'))
    assert len(disabled) == 1
    assert disabled[0].read_text() == 'Owner changed this after approval'


def test_invalid_snapshot_cannot_revoke_previous_imports(tmp_path):
    item = approved()
    sync_approved_skills(tmp_path, [item])
    before = (tmp_path / '.approved-deliveries.json').read_bytes()
    with pytest.raises(ValueError):
        sync_approved_skills(tmp_path, [{'name': 'invalid'}])
    assert (tmp_path / '.approved-deliveries.json').read_bytes() == before
    assert (tmp_path / 'skills' / 'approved-imports' / item['name'] / 'SKILL.md').exists()


def test_identical_unmanaged_skill_is_not_silently_adopted(tmp_path):
    item = approved()
    target = tmp_path / 'skills' / 'approved-imports' / item['name'] / 'SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_text(item['content'])
    with pytest.raises(ValueError):
        sync_approved_skills(tmp_path, [item])
    assert not (tmp_path / '.approved-deliveries.json').exists()


def test_duplicate_target_rejects_entire_snapshot_without_writes(tmp_path):
    with pytest.raises(ValueError):
        sync_approved_skills(tmp_path, [approved(), approved()])
    assert not list(tmp_path.iterdir())


def test_project_runtime_configuration_uses_smart_protected_defaults():
    from runtime.workspace_config import runtime_config
    config = runtime_config('gpt-6.1-sol')
    assert config['terminal']['cwd'] == '/workspace/projects/personal-agent'
    assert config['security']['protected_instruction_files'] is True
    assert config['approvals']['mode'] == 'smart'
    assert config['display']['tool_progress'] == 'off'
    for name in ('model', 'compression', 'title_generation'):
        route = config[name]
        assert route['api_mode'] == 'codex_responses'
        assert route['base_url'] == 'http://web:8080/provider/v1'
        assert route.get('model', route.get('default')) == 'gpt-6.1-sol'
    assert config['agent']['max_turns'] == 20
    assert config['max_concurrent_sessions'] == 1
