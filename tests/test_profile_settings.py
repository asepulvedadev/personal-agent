"""Owner profile selection uses live Hermes homes, never arbitrary paths."""
import json
import os

import httpx
import pytest

from runtime.config_files import ConfigError, ProfileFiles
from test_portal import portal, login


def seeded(tmp_path):
    home = tmp_path / 'home'
    for root, name in [(home, 'Web'), (home / 'profiles/carpediev', 'Carpediev')]:
        (root / 'memories').mkdir(parents=True)
        (root / 'SOUL.md').write_text(name)
        (root / 'config.yaml').write_text(json.dumps({'agent': {'max_turns': 20},
            'mcp_servers': {'hidden': {'env': {'TOKEN': 'PRIVATE-SYNTHETIC'}}}}))
    return home


def test_profiles_discovered_and_edits_are_isolated(tmp_path):
    home = seeded(tmp_path)
    store = ProfileFiles(home)
    assert store.listing() == {'profiles': [{'id': 'default', 'name': 'Hermes Web'}, {'id': 'carpediev', 'name': 'Carpediev'}]}
    value = store.read('carpediev', 'soul')
    store.write('carpediev', 'soul', 'New Carpediev', value['revision'])
    assert (home / 'SOUL.md').read_text() == 'Web'
    assert (home / 'profiles/carpediev/SOUL.md').read_text() == 'New Carpediev'
    assert not (home / '.config-editor-backups').exists()
    value = store.read('carpediev', 'config')
    assert 'PRIVATE-SYNTHETIC' not in value['content']
    store.write('carpediev', 'config', 'agent: {max_turns: 31}', value['revision'])
    assert json.loads((home / 'config.yaml').read_text())['agent']['max_turns'] == 20
    assert json.loads((home / 'profiles/carpediev/config.yaml').read_text())['mcp_servers']['hidden']['env']['TOKEN'] == 'PRIVATE-SYNTHETIC'
    future = home / 'profiles/future_agent'
    future.mkdir(); (future / 'SOUL.md').write_text('Future')
    assert store.listing()['profiles'][-1]['id'] == 'future_agent'
    assert store.read('future_agent', 'soul')['content'] == 'Future'
    with pytest.raises(ConfigError): store.read('future_agent', 'config')


def test_profile_links_tombstones_and_shells_are_never_available(tmp_path):
    home = seeded(tmp_path); store = ProfileFiles(home)
    (home / 'profiles/empty').mkdir()
    (home / 'profiles/.partial').mkdir()
    (home / 'profiles/linked').symlink_to(home)
    (home / 'profiles/.deleted').mkdir()
    (home / 'profiles/.deleted/carpediev').touch()
    assert store.listing()['profiles'] == [{'id': 'default', 'name': 'Hermes Web'}]
    for profile in ('empty', 'linked', '.partial', '../', 'carpediev', 'DEFAULT', 'missing', 'x/y'):
        with pytest.raises(ConfigError): store.read(profile, 'soul')
    assert not (home / 'profiles/missing').exists()
    (home / 'profiles/.deleted/carpediev').unlink()
    assert store.read('carpediev', 'soul')['content'] == 'Carpediev'
    root = home / 'profiles/carpediev'
    root.rename(home / 'profiles/old')
    root.symlink_to(home)
    with pytest.raises(ConfigError): store.write('carpediev', 'soul', 'wrong', 'missing')
    assert (home / 'SOUL.md').read_text() == 'Web'


def test_web_profile_proxy_is_authenticated_bounded_and_exact(portal):
    m, client = portal
    assert client.get('/api/settings/profiles').status_code == 401
    headers = login(client); calls = []
    def native(req):
        calls.append(req)
        if req.url.path.endswith('/profiles'):
            return httpx.Response(200, json={'profiles': [{'id': 'default', 'name': 'Hermes Web'}, {'id': 'carpediev', 'name': 'Carpediev'}]})
        return httpx.Response(200, json={'id': 'soul', 'name': 'SOUL.md', 'content': 'Carpediev', 'revision': 'a' * 64, 'note': 'Conversaciones nuevas.'})
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(native))
    assert len(client.get('/api/settings/profiles').json()['profiles']) == 2
    assert client.get('/api/settings/files/soul?profile=carpediev').json()['content'] == 'Carpediev'
    assert calls[-1].url.params['profile'] == 'carpediev'
    assert client.put('/api/settings/files/soul?profile=carpediev', json={'content': 'x', 'revision': 'a'*64}).status_code == 403
    assert client.put('/api/settings/files/soul?profile=carpediev', json={'content': 'x', 'revision': 'a'*64}, headers=headers).status_code == 200
    assert calls[-1].url.params['profile'] == 'carpediev'
    count = len(calls)
    for query in ('profile=../', 'profile=', 'profile=a&profile=b', 'profile=x%2Fy'):
        assert client.get('/api/settings/files/soul?' + query).status_code in (400, 404)
    assert len(calls) == count


def test_real_native_profile_routes_recheck_live_identity_and_never_mirror(tmp_path, monkeypatch):
    import asyncio
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from runtime.config_files import register_config_files
    home = seeded(tmp_path); monkeypatch.setenv('HERMES_HOME', str(home))
    class Adapter:
        _api_key = 'synthetic-native-token'
        def _check_auth(self, request):
            return None if request.headers.get('Authorization') == 'Bearer synthetic-native-token' else web.Response(status=401)
    async def exercise():
        app = web.Application(); register_config_files(app, Adapter())
        async with TestClient(TestServer(app)) as client:
            assert (await client.get('/api/settings/profiles')).status == 401
            headers = {'Authorization': 'Bearer synthetic-native-token'}
            names = await (await client.get('/api/settings/profiles', headers=headers)).json()
            assert names['profiles'][1]['id'] == 'carpediev'
            url = '/api/settings/files/soul?profile=carpediev'
            value = await (await client.get(url, headers=headers)).json()
            assert value['content'] == 'Carpediev'
            payload = {'content': 'Updated Carpediev', 'revision': value['revision']}
            assert (await client.put(url, headers=headers, json=payload)).status == 200
            assert (await client.put(url, headers=headers, json=payload)).status == 409
            assert (home / 'SOUL.md').read_text() == 'Web'
            assert (await client.get('/api/settings/files/soul?profile=default&profile=carpediev', headers=headers)).status == 404
            assert (await client.get('/p/carpediev/api/settings/profiles', headers=headers)).status == 404
            (home / 'profiles/.deleted').mkdir(); (home / 'profiles/.deleted/carpediev').touch()
            assert (await client.get(url, headers=headers)).status == 404
            assert (await client.get('/api/settings/files?profile=carpediev', headers=headers)).status == 404
    asyncio.run(exercise())


@pytest.mark.parametrize('payload', [None, {'profiles': []}, {'profiles': [{'id': '../', 'name': 'Bad'}]},
    {'profiles': [{'id': 'default', 'name': 'secret accidental content'}]},
    {'profiles': [{'id': 'default', 'name': 'Hermes Web'}, {'id': 'default', 'name': 'Hermes Web'}]}])
def test_proxy_rejects_bad_profile_metadata_without_echoing_it(portal, payload):
    m, client = portal; login(client)
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    response = client.get('/api/settings/profiles')
    assert response.status_code == 502
    assert 'accidental' not in response.text and '../' not in response.text
