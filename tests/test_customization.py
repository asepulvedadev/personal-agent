"""Owner customization contracts, exercised without Hermes or network access."""
import asyncio
import json
import os

import pytest

from runtime.config_files import ConfigError
from runtime.customization import Customization, register_customization


SKILL = '---\nname: focus\ndescription: Focused owner assistance.\n---\n\nGive direct answers.\n'


@pytest.fixture
def assets(tmp_path):
    home = tmp_path / 'home'
    (home / 'skills/builtin/builtin').mkdir(parents=True)
    (home / 'skills/builtin/builtin/SKILL.md').write_text(SKILL.replace('name: focus', 'name: builtin'))
    (home / 'profiles/carpediev').mkdir(parents=True)
    (home / 'profiles/carpediev/SOUL.md').write_text('Carpediev')
    return Customization(home), home


def test_owner_skills_active_installed_readonly_and_profile_isolated(assets):
    store, home = assets
    installed = store.listing('default', 'skill')['items'][0]
    assert installed['editable'] is False and installed['status'] == 'installed'
    with pytest.raises(ConfigError):
        store.write('default', 'skill', installed['id'], SKILL, 'missing')
    own = store.create('default', 'skill', 'focus', SKILL)
    assert own['editable'] and own['status'] == 'active'
    assert (home / 'skills/owner-created/focus/SKILL.md').read_text() == SKILL
    assert store.listing('carpediev', 'skill')['items'] == []
    assert store.read('default', 'skill', own['id']) == own
    with pytest.raises(ConfigError) as error:
        store.create('default', 'skill', 'focus', SKILL)
    assert error.value.status == 409
    updated = store.write('default', 'skill', own['id'], SKILL + 'Stay concise.\n', own['revision'])
    assert updated['revision'] != own['revision']
    with pytest.raises(ConfigError) as error:
        store.write('default', 'skill', own['id'], SKILL, own['revision'])
    assert error.value.status == 409
    assert len(list((home / '.config-editor-backups').glob('customization-*.bak'))) == 1


@pytest.mark.parametrize('kind', ['tool', 'plugin', 'script'])
def test_executable_assets_are_inert_private_drafts(assets, kind):
    store, home = assets
    content = 'raise RuntimeError("must never execute")\n'
    own = store.create('carpediev', kind, 'sample', content)
    assert own['status'] == 'draft' and own['editable'] is True
    path = home / 'profiles/carpediev/.customization-drafts' / kind / 'sample.txt'
    assert path.read_text() == content and path.stat().st_mode & 0o777 == 0o600
    assert not (home / 'profiles/carpediev/plugins').exists()
    assert store.listing('carpediev', kind)['items'][0]['id'] == own['id']


@pytest.mark.parametrize('content', ['plain text', SKILL.replace('name: focus', 'name: another'),
    SKILL.replace('description:', 'hooks:'), SKILL + '\nAPI_KEY=not-a-real-token-value', 'x' * 32769])
def test_invalid_skill_never_activates(assets, content):
    store, home = assets
    with pytest.raises(ConfigError):
        store.create('default', 'skill', 'focus', content)
    assert not (home / 'skills/owner-created/focus/SKILL.md').exists()


def test_paths_links_hardlinks_and_duplicate_queries_fail_closed(assets):
    store, home = assets
    for profile, kind, name in [('../outside', 'script', 'x'), ('default', 'other', 'x'),
                                  ('default', 'tool', '../x'), ('default', 'skill', 'A')]:
        with pytest.raises(ConfigError): store.create(profile, kind, name, SKILL)
    (home / '.customization-drafts').symlink_to(home)
    with pytest.raises(ConfigError): store.create('default', 'script', 'sample', 'safe')
    (home / '.customization-drafts').unlink()
    own = store.create('default', 'script', 'sample', 'safe')
    path = home / '.customization-drafts/script/sample.txt'
    os.link(path, home / 'duplicate')
    with pytest.raises(ConfigError): store.read('default', 'script', own['id'])
    with pytest.raises(ConfigError): store.write('default', 'script', own['id'], 'changed', own['revision'])
    assert path.read_text() == 'safe'


def test_native_auth_limits_and_cas(assets, monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    _, home = assets
    monkeypatch.setenv('HERMES_HOME', str(home))
    class Adapter:
        _api_key = 'test-token'
        def _check_auth(self, request):
            return None if request.headers.get('Authorization') == 'Bearer test-token' else web.json_response({}, status=401)
    async def exercise():
        app = web.Application()
        register_customization(app, Adapter())
        async with TestClient(TestServer(app)) as client:
            route = '/api/settings/customization?profile=default&kind=script'
            assert (await client.get(route)).status == 401
            headers = {'Authorization': 'Bearer test-token'}
            response = await client.post(route, headers=headers, json={'id': 'sample', 'content': 'safe'})
            assert response.status == 200
            value = await response.json()
            item = route.replace('customization?', 'customization/item?') + '&id=' + value['id']
            assert (await client.get(item, headers=headers)).status == 200
            assert (await client.put(item, headers=headers, json={'content': 'next', 'revision': 'missing'})).status == 409
            assert (await client.get(route + '&kind=plugin', headers=headers)).status == 400
            assert (await client.post(route, headers=headers, data=b'x' * 65537)).status == 413
            assert (await client.get('/p/carpediev/api/settings/customization', headers=headers)).status == 404
            shared_route = '/api/settings/customization?scope=shared&kind=skill'
            response = await client.post(shared_route, headers=headers, json={'id': 'focus', 'content': SKILL})
            assert response.status == 200 and (await response.json())['id'] == 'shared:focus'
            response = await client.get('/api/settings/customization?profile=carpediev&kind=skill', headers=headers)
            assert (await response.json())['items'][0]['id'] == 'shared:focus'
            assert (await client.get(shared_route + '&scope=profile', headers=headers)).status == 400
    asyncio.run(exercise())


def test_trusted_project_edits_but_approved_imports_remain_readonly(assets):
    store, home = assets
    for category, slug in [('project', 'focus'), ('approved-imports', 'external')]:
        folder = home / 'skills' / category / slug
        folder.mkdir(parents=True)
        folder.joinpath('SKILL.md').write_text(SKILL.replace('name: focus', 'name: ' + slug))
    values = {v['name']: v for v in store.listing('default', 'skill')['items']}
    project = store.read('default', 'skill', values['focus']['id'])
    assert project['editable'] and project['status'] == 'active'
    metadata = SKILL.replace('description: Focused owner assistance.', 'description: Focused owner assistance.\nmetadata:\n  hermes:\n    category: productivity\n    tags: [focus]')
    assert store.write('default', 'skill', project['id'], metadata, project['revision'])['content'] == metadata
    external = store.read('default', 'skill', values['external']['id'])
    with pytest.raises(ConfigError) as error:
        store.write('default', 'skill', external['id'], external['content'], external['revision'])
    assert error.value.status == 403
    with pytest.raises(ConfigError) as error:
        store.create('default', 'skill', 'external', SKILL.replace('name: focus', 'name: external'))
    assert error.value.status == 409
    assert not (home / 'skills/owner-created/external').exists()


def test_inventory_overflow_is_explicit_not_a_truncated_success(assets):
    store, home = assets
    folder = home / '.customization-drafts/script'
    folder.mkdir(parents=True)
    for index in range(513):
        folder.joinpath(f'asset-{index}.txt').write_text('safe')
    with pytest.raises(ConfigError) as error:
        store.listing('default', 'script')
    assert error.value.status == 503


def test_backup_race_refuses_overwriting_noncooperating_writer(assets, monkeypatch):
    from runtime.config_files import ConfigFiles
    store, home = assets
    item = store.create('default', 'script', 'sample', 'original')
    original = ConfigFiles._backup
    def backup(self, *args):
        original(self, *args)
        (home / '.customization-drafts/script/sample.txt').write_text('concurrent writer')
    monkeypatch.setattr(ConfigFiles, '_backup', backup)
    with pytest.raises(ConfigError) as error:
        store.write('default', 'script', item['id'], 'proposed', item['revision'])
    assert error.value.status == 409
    assert (home / '.customization-drafts/script/sample.txt').read_text() == 'concurrent writer'


def test_image_loader_seam_registers_standalone_helpers(tmp_path):
    """The image loads loose runtime files, not the repository package."""
    import subprocess
    import sys
    from pathlib import Path
    from runtime.patch_config_files import INJECTION
    root = Path(__file__).resolve().parents[1]
    script = '''from importlib.util import module_from_spec, spec_from_file_location
import sys
spec = spec_from_file_location("_personal_agent_config_files", sys.argv[1])
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
custom_spec = spec_from_file_location("_personal_agent_customization", sys.argv[2])
custom = module_from_spec(custom_spec)
custom_spec.loader.exec_module(custom)
assert custom.ConfigFiles is module.ConfigFiles
'''
    result = subprocess.run([sys.executable, '-I', '-c', script, str(root / 'runtime/config_files.py'),
                             str(root / 'runtime/customization.py')], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert INJECTION.count('_custom_module.register_customization(self._app, self)') == 1
    assert INJECTION.index('register_customization') > INJECTION.index('register_config_files')


def test_shared_skills_use_one_source_for_current_and_future_profiles(assets):
    store, home = assets
    item = store.create('default', 'skill', 'focus', SKILL, scope='shared')
    assert item['id'] == 'shared:focus' and item['editable'] and item['status'] == 'active'
    assert (home / 'shared-skills/focus/SKILL.md').read_text() == SKILL
    assert not (home / 'skills/owner-created/focus').exists()
    for profile in ('default', 'carpediev'):
        assert item['id'] in {v['id'] for v in store.listing(profile, 'skill')['items']}
        assert store.read(profile, 'skill', item['id']) == item
    future = home / 'profiles/future'
    future.mkdir(); (future / 'SOUL.md').write_text('Future agent')
    assert store.read('future', 'skill', item['id']) == item
    updated = store.write('carpediev', 'skill', item['id'], SKILL + 'Stay concise.\n', item['revision'])
    assert store.read('future', 'skill', item['id']) == updated
    assert len(list((home / '.config-editor-backups').glob('customization-*.bak'))) == 1
    with pytest.raises(ConfigError) as error:
        store.write('default', 'skill', item['id'], SKILL, item['revision'], scope='shared')
    assert error.value.status == 409
    assert [v['id'] for v in store.listing('default', 'skill', scope='shared')['items']] == [item['id']]


def test_shared_name_collisions_and_existing_profile_behavior(assets):
    store, home = assets
    alias = home / 'profiles/carpediev/skills/project/different-folder'
    alias.mkdir(parents=True)
    (alias / 'SKILL.md').write_text(SKILL)
    with pytest.raises(ConfigError) as error:
        store.create('default', 'skill', 'focus', SKILL, scope='shared')
    assert error.value.status == 409 and not (home / 'shared-skills/focus/SKILL.md').exists()
    item = store.create('default', 'skill', 'common', SKILL.replace('name: focus', 'name: common'), scope='shared')
    with pytest.raises(ConfigError) as error:
        store.create('carpediev', 'skill', 'common', item['content'])
    assert error.value.status == 409
    own = store.create('carpediev', 'skill', 'private', SKILL.replace('name: focus', 'name: private'))
    assert own['id'] == 'owner:private'
    assert own['id'] not in {v['id'] for v in store.listing('default', 'skill')['items']}
    assert (alias / 'SKILL.md').read_text() == SKILL


def test_shared_scope_never_promotes_imports_or_executable_drafts(assets):
    store, home = assets
    installed = store.listing('default', 'skill')['items'][0]
    with pytest.raises(ConfigError):
        store.read('default', 'skill', installed['id'], scope='shared')
    for kind in ('tool', 'plugin', 'script'):
        with pytest.raises(ConfigError): store.create('default', kind, 'focus', 'safe', scope='shared')
    for scope in ('../shared', 'all'):
        with pytest.raises(ConfigError): store.listing('default', 'skill', scope=scope)
    (home / 'shared-skills').symlink_to(home / 'skills')
    with pytest.raises(ConfigError): store.create('default', 'skill', 'focus', SKILL, scope='shared')
    assert not (home / 'skills/focus').exists()


def test_future_profile_collision_is_reported_without_changing_either_skill(assets):
    store, home = assets
    shared = store.create('default', 'skill', 'common', SKILL.replace('name: focus', 'name: common'), scope='shared')
    folder = home / 'profiles/future/skills/owner-created/common'
    folder.mkdir(parents=True)
    (home / 'profiles/future/SOUL.md').write_text('Future')
    private = shared['content'] + 'Private override.\n'
    (folder / 'SKILL.md').write_text(private)
    assert store.read('future', 'skill', shared['id'])['status'] == 'shadowed'
    assert store.listing('default', 'skill', scope='shared')['items'][0]['status'] == 'shadowed'
    assert (folder / 'SKILL.md').read_text() == private
    assert (home / 'shared-skills/common/SKILL.md').read_text() == shared['content']
