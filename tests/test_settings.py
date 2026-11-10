"""The browser never receives raw private configuration or filesystem paths."""
import httpx
import pytest
from test_portal import portal, login

SNAPSHOT = {'id': 'soul', 'name': 'SOUL.md', 'content': 'Helpful assistant.', 'revision': 'a' * 64, 'note': 'Conversaciones nuevas.'}


def native_client(m, capture, *, status=200, value=None, raw=None):
    def handler(request):
        capture.append(request)
        return httpx.Response(status, json=value if value is not None else SNAPSHOT) if raw is None else httpx.Response(status, content=raw)
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_settings_owner_csrf_and_default_profile_only(portal):
    m, client = portal
    for path in ['/api/settings/files', '/api/settings/files/config']:
        assert client.get(path).status_code == 401
    headers = login(client)
    requests = []
    native_client(m, requests)
    assert client.get('/api/settings/files').json()['files'][0]['id'] == 'config'
    assert client.get('/api/settings/files/soul').json() == SNAPSHOT
    assert client.put('/api/settings/files/soul', json={'content': 'Updated', 'revision': 'a' * 64}).status_code == 403
    result = client.put('/api/settings/files/soul', json={'content': 'Updated', 'revision': 'a' * 64}, headers=headers)
    assert result.status_code == 200
    assert len(requests) == 2
    assert all(r.url.path == '/api/settings/files/soul' for r in requests)
    assert all(r.headers['authorization'] == 'Bearer hermes-test-token-with-enough-entropy' for r in requests)
    assert client.get('/api/settings/files/other').status_code == 404
    assert client.put('/api/settings/files/user', json={'content': 'x', 'revision': 'a'*64, 'profile': 'carpediev'}, headers=headers).status_code == 400
    assert result.headers['cache-control'] == 'no-store'


def test_settings_never_echoes_native_errors_and_conflict_is_reported(portal):
    m, client = portal
    headers = login(client)
    native_client(m, [], status=409, value={'detail': 'raw-private-password'})
    response = client.put('/api/settings/files/soul', json={'content': 'x', 'revision': 'a'*64}, headers=headers)
    assert response.status_code == 409 and 'raw-private-password' not in response.text


@pytest.mark.parametrize('raw', [b'x' * 180000, b'{invalid json', b'{"id":"soul","name":"SOUL.md","content":"secret"}'])
def test_settings_bounds_native_payload_and_rejects_bad_shape(portal, raw):
    m, client = portal
    login(client)
    native_client(m, [], raw=raw)
    response = client.get('/api/settings/files/soul')
    assert response.status_code == 502
    assert 'secret' not in response.text
