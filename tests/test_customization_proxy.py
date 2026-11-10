"""The owner customization proxy does not expose native errors or credentials."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.customization import register_customization


ITEM = {'id': 'draft:sample', 'name': 'sample', 'kind': 'script', 'status': 'draft',
        'editable': True, 'content': 'safe text', 'revision': 'a' * 64}


@pytest.fixture
def proxy():
    app = FastAPI()
    requests = []
    def native(request):
        requests.append(request)
        return httpx.Response(200, json=ITEM)
    app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(native))
    def owner(request):
        if request.headers.get('cookie') != 'owner=yes':
            raise HTTPException(401)
        return {'csrf': 'test-csrf'}
    def csrf(request, data):
        if request.headers.get('x-csrf-token') != data['csrf']:
            raise HTTPException(403)
    async def body(request):
        return await request.json()
    register_customization(app, owner, csrf, body, lambda: 'http://native', lambda: 'private-token')
    with TestClient(app) as client:
        yield app, client, requests
    asyncio.run(app.state.internal.aclose())


def test_owner_csrf_and_fixed_query_proxy(proxy):
    _, client, requests = proxy
    route = '/api/customization/item?profile=carpediev&kind=script&id=draft:sample'
    assert client.get(route).status_code == 401
    headers = {'Cookie': 'owner=yes'}
    assert client.get(route, headers=headers).json() == ITEM
    assert requests[0].url.path == '/api/settings/customization/item'
    assert requests[0].url.params['profile'] == 'carpediev'
    assert requests[0].headers['authorization'] == 'Bearer private-token'
    data = {'content': 'next', 'revision': 'a' * 64}
    assert client.put(route, headers=headers, json=data).status_code == 403
    headers['X-CSRF-Token'] = 'test-csrf'
    assert client.put(route, headers=headers, json=data).status_code == 200
    assert client.get(route + '&profile=default', headers=headers).status_code == 400
    assert client.get(route.replace('carpediev', '../outside'), headers=headers).status_code == 400
    assert len(requests) == 2


@pytest.mark.parametrize('response', [httpx.Response(200, json={'content': 'private'}),
    httpx.Response(200, json={**ITEM, 'path': '/private'}),
    httpx.Response(200, json={**ITEM, 'kind': 'plugin'}),
    httpx.Response(200, content=b'x' * 200001),
    httpx.Response(409, json={'detail': 'private-password'})])
def test_upstream_shape_bounds_and_errors_are_not_leaked(proxy, response):
    app, client, _ = proxy
    app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response))
    result = client.get('/api/customization/item?kind=script&id=draft:sample', headers={'Cookie': 'owner=yes'})
    assert result.status_code in {409, 502}
    assert 'private' not in result.text and '/private' not in result.text


def test_creation_and_listing_contract(proxy):
    app, client, requests = proxy
    headers = {'Cookie': 'owner=yes', 'X-CSRF-Token': 'test-csrf'}
    assert client.post('/api/customization?kind=script', headers=headers,
                       json={'id': 'sample', 'content': 'text'}).json() == ITEM
    assert client.post('/api/customization?kind=script', headers=headers,
                       json={'id': '../bad', 'content': 'text'}).status_code == 400
    app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,
        json={'items': [{k: v for k, v in ITEM.items() if k not in {'content', 'revision'}}], 'note': 'Drafts only.'})))
    assert client.get('/api/customization?kind=script', headers=headers).json()['items'][0]['name'] == 'sample'
