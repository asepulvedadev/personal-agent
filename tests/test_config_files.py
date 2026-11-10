"""Offline contracts for the fixed-file, non-secret profile editor."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from runtime.config_files import ConfigFiles, ConfigError, editable_values
from runtime.workspace_config import runtime_config


@pytest.fixture
def files(tmp_path):
    home = tmp_path / 'home'
    (home / 'memories').mkdir(parents=True)
    (home / 'config.yaml').write_text(json.dumps({'agent': {'max_turns': 20}, 'model': {'default': 'private'},
        'approvals': {'mode': 'off'}, 'mcp_servers': {'example': {'env': {'TOKEN': 'DO-NOT-EXPOSE'}}}}))
    (home / 'SOUL.md').write_text('Be helpful.\n')
    (home / 'memories/USER.md').write_text('Owner preferences.\n')
    return ConfigFiles(home), home


def test_projection_and_cas_preserve_hidden_config(files):
    editor, home = files
    value = editor.read('config')
    assert 'DO-NOT-EXPOSE' not in value['content'] and 'mcp_servers' not in value['content']
    updated = editor.write('config', 'agent:\n  max_turns: 30\n', value['revision'])
    assert updated['revision'] != value['revision']
    current = json.loads((home / 'config.yaml').read_text())
    assert current['agent']['max_turns'] == 30
    assert current['approvals']['mode'] == 'off'
    assert current['mcp_servers']['example']['env']['TOKEN'] == 'DO-NOT-EXPOSE'
    before = (home / 'config.yaml').read_bytes()
    with pytest.raises(ConfigError) as exc:
        editor.write('config', 'agent:\n  max_turns: 40\n', value['revision'])
    assert exc.value.status == 409 and (home / 'config.yaml').read_bytes() == before


@pytest.mark.parametrize('content', ['model: {default: altered}', 'mcp_servers: {}',
    'agent: {max_turns: true}', 'agent: {max_turns: 201}', 'memory: {memory_enabled: 1}',
    'agent: {unexpected: 1}', 'agent: [1]', '[]', 'agent: {max_turns: 20, max_turns: 40}',
    'agent: &x {max_turns: 30}\nterminal: *x', 'terminal: {timeout: 0}', ''])
def test_invalid_projection_never_writes_or_backs_up(files, content):
    editor, home = files
    before = (home / 'config.yaml').read_bytes()
    with pytest.raises(ConfigError):
        editor.write('config', content, editor.read('config')['revision'])
    assert (home / 'config.yaml').read_bytes() == before
    assert not (home / '.config-editor-backups').exists()


def test_markdown_roundtrip_secret_block_and_fixed_ids(files):
    editor, home = files
    value = editor.read('user')
    assert value['content'] == 'Owner preferences.\n'
    editor.write('user', '# Preferences\nEspañol.\n', value['revision'])
    assert (home / 'memories/USER.md').read_text() == '# Preferences\nEspañol.\n'
    for secret in ['-----BEGIN OPENSSH PRIVATE KEY-----\nanything', 'API_KEY=super-secret-value', 'sk-' + 'x' * 30]:
        with pytest.raises(ConfigError):
            editor.write('soul', secret, editor.read('soul')['revision'])
        (home / 'memories/USER.md').write_text(secret)
        with pytest.raises(ConfigError):
            editor.read('user')
    for file_id in ['../config.yaml', 'auth', '.env', 'memory', 'carpediev', 'config.yaml']:
        with pytest.raises(ConfigError):
            editor.read(file_id)


def test_links_oversize_utf8_and_bounded_private_backups(files):
    editor, home = files
    for i in range(8):
        editor.write('soul', f'New identity {i}', editor.read('soul')['revision'])
    backups = list((home / '.config-editor-backups').iterdir())
    assert len(backups) == 5
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in backups)
    assert (home / '.config-editor-backups').stat().st_mode & 0o777 == 0o700
    (home / 'SOUL.md').unlink()
    (home / 'SOUL.md').symlink_to(home / 'config.yaml')
    with pytest.raises(ConfigError): editor.read('soul')
    (home / 'SOUL.md').unlink()
    (home / 'SOUL.md').write_bytes(b'\xff')
    with pytest.raises(ConfigError): editor.read('soul')
    (home / 'SOUL.md').write_text('x' * 32769)
    with pytest.raises(ConfigError): editor.read('soul')
    (home / 'memories/USER.md').unlink()
    (home / 'memories').rmdir()
    (home / 'memories').symlink_to(home)
    with pytest.raises(ConfigError): editor.read('user')


def test_runtime_preserves_only_typed_editor_fields_and_existing_root_relay():
    config = runtime_config('chosen', owner_config={'agent': {'max_turns': 38}, 'terminal': {'timeout': 99, 'backend': 'ssh'},
        'skills': {'creation_nudge_interval': 0}, 'memory': {'memory_enabled': False},
        'approvals': {'mode': 'off'}, 'model': {'base_url': 'evil'}})
    assert config['agent']['max_turns'] == 38
    assert config['terminal']['timeout'] == 99 and config['terminal']['backend'] == 'local'
    assert config['skills']['creation_nudge_interval'] == 0
    assert config['memory']['memory_enabled'] is False
    assert config['model']['base_url'] == 'http://web:8080/provider/v1'
    assert config['approvals']['mode'] == 'off'


def test_native_routes_are_authenticated_bounded_and_not_profile_mirrored(files, monkeypatch):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from runtime.config_files import register_config_files
    editor, home = files
    monkeypatch.setenv('HERMES_HOME', str(home))
    class Adapter:
        _api_key = 'synthetic-native-token'
        calls = 0
        def _check_auth(self, request):
            self.calls += 1
            return None if request.headers.get('Authorization') == 'Bearer synthetic-native-token' else web.json_response({'detail': 'No autorizado.'}, status=401)
    adapter = Adapter()
    async def exercise():
        app = web.Application()
        register_config_files(app, adapter)
        # Home is captured before any request-time profile/environment changes.
        monkeypatch.setenv('HERMES_HOME', str(home / 'profiles' / 'carpediev'))
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/api/settings/files')).status == 401
            headers = {'Authorization': 'Bearer synthetic-native-token'}
            assert (await client.get('/api/settings/files', headers=headers)).status == 200
            assert (await client.get('/p/carpediev/api/settings/files', headers=headers)).status == 404
            response = await client.get('/api/settings/files/config', headers=headers)
            snapshot = await response.json()
            assert 'DO-NOT-EXPOSE' not in str(snapshot)
            updated = await client.put('/api/settings/files/config', headers=headers, json={'content': 'agent: {max_turns: 32}', 'revision': snapshot['revision']})
            assert updated.status == 200 and (await updated.json())['saved'] is True
            conflict = await client.put('/api/settings/files/config', headers=headers, json={'content': 'agent: {max_turns: 33}', 'revision': snapshot['revision']})
            assert conflict.status == 409
            assert '32' in (await (await client.get('/api/settings/files/config', headers=headers)).json())['content']
            too_large = await client.put('/api/settings/files/soul', headers=headers, data=b'x' * 65537)
            assert too_large.status == 413
            invalid = await client.put('/api/settings/files/soul', headers=headers, data=b'\xff')
            assert invalid.status == 400 and 'ff' not in await invalid.text()
            adapter._api_key = ''
            assert (await client.get('/api/settings/files', headers=headers)).status == 401
        assert adapter.calls >= 7
    asyncio.run(exercise())


def test_editor_creates_missing_markdown_but_rejects_missing_parent(files):
    editor, home = files
    (home / 'memories/USER.md').unlink()
    value = editor.read('user')
    assert value['content'] == '' and value['revision'] == 'missing'
    editor.write('user', 'New owner context.', 'missing')
    assert (home / 'memories/USER.md').read_text() == 'New owner context.'
    (home / 'memories/USER.md').unlink()
    (home / 'memories').rmdir()
    with pytest.raises(ConfigError): editor.read('user')
    (home / 'config.yaml').unlink()
    with pytest.raises(ConfigError) as exc: editor.read('config')
    assert exc.value.status == 404


def test_linked_home_lock_backups_and_hardlinks_are_rejected(files, tmp_path):
    editor, home = files
    linked_home = tmp_path / 'linked-home'
    linked_home.symlink_to(home, target_is_directory=True)
    with pytest.raises(ConfigError): ConfigFiles(linked_home).read('soul')
    (home / '.config-editor.lock').symlink_to(home / 'SOUL.md')
    with pytest.raises(ConfigError): editor.read('soul')
    (home / '.config-editor.lock').unlink()
    os.link(home / 'SOUL.md', home / 'duplicate.md')
    with pytest.raises(ConfigError): editor.read('soul')
    (home / 'duplicate.md').unlink()
    (home / '.config-editor-backups').symlink_to(home, target_is_directory=True)
    before = (home / 'SOUL.md').read_bytes()
    with pytest.raises(ConfigError): editor.write('soul', 'New identity', editor.read('soul')['revision'])
    assert (home / 'SOUL.md').read_bytes() == before


def test_final_cas_detects_noncooperating_writer_before_replace(files, monkeypatch):
    editor, home = files
    value = editor.read('soul')
    original = editor._backup
    def racing_backup(*args):
        original(*args)
        (home / 'SOUL.md').write_text('Written by another process.')
    monkeypatch.setattr(editor, '_backup', racing_backup)
    with pytest.raises(ConfigError) as exc: editor.write('soul', 'My proposed text.', value['revision'])
    assert exc.value.status == 409
    assert (home / 'SOUL.md').read_text() == 'Written by another process.'


def test_pinned_patch_exact_once_ast_and_post_mirror_placement(tmp_path):
    import ast
    from runtime.patch_config_files import patch, ANCHOR, INJECTION
    target = tmp_path / 'gateway/platforms/api_server.py'
    target.parent.mkdir(parents=True)
    fixture = ('class Adapter:\n    async def connect(self):\n        try:\n'
               '            for method, path, handler in self._http_route_table():\n'
               '                self._app.router.add_route(method, f"/p/{profile}{path}", handler)\n' + ANCHOR +
               '            self._runner = web.AppRunner(self._app)\n        except Exception:\n            pass\n')
    target.write_text(fixture)
    patch(tmp_path)
    result = target.read_text()
    ast.parse(result)
    assert result.count(INJECTION) == 1
    assert result.index('register_config_files') > result.index('for method, path')
    assert result.index('register_config_files') < result.index('web.AppRunner')
    with pytest.raises(RuntimeError): patch(tmp_path)
    target.write_text(fixture.replace(ANCHOR, ANCHOR + ANCHOR))
    with pytest.raises(RuntimeError): patch(tmp_path)
    target.write_text(fixture.replace(ANCHOR, ''))
    with pytest.raises(RuntimeError): patch(tmp_path)
