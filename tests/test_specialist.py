"""Owner-only configuration conversations never use the default profile key."""
import hashlib
import hmac
import json

import httpx
import pytest
from test_portal import portal, login


@pytest.fixture
def specialist(portal):
    from app.specialist import register_specialist
    m, client = portal
    if not any(getattr(route, 'path', '') == '/api/settings/assistant' for route in m.app.routes):
        register_specialist(m.app, m.owner, m.csrf, m.body, lambda: m.HERMES, lambda: m.token('HERMES_TOKEN'))
    return m, client


def native_stub(calls, reply='I can help configure the app.'):
    def native(req):
        calls.append(req)
        if req.url.path.endswith('/api/sessions'):
            return httpx.Response(201, json={'session': {'id': json.loads(req.content)['id']}})
        return httpx.Response(200, json={'object': 'hermes.session.chat.completion', 'message': {'role': 'assistant', 'content': reply}, 'usage': {'hidden': 1}, 'tool_calls': ['private']})
    return native


def test_specialist_imports_use_shared_quarantine_before_native_inference(specialist,monkeypatch):
    import app.quarantine_chat as intake
    m,c=specialist; headers=login(c); calls=[]
    async def package(url):
        return (b'---\nname: demo-skill\ndescription: Owner review sample.\n---\n\nSummarize supplied facts.\n',
                {'kind':'github-file','filename':'SKILL.md','url':url})
    monkeypatch.setattr(intake,'github_package',package)
    m.app.state.internal=httpx.AsyncClient(transport=httpx.MockTransport(native_stub(calls)))
    result=c.post('/api/settings/assistant',json={'message':'Instala esta skill: https://github.com/ayghri/i-have-adhd'},headers=headers)
    assert result.status_code==200
    assert 'Revisión compartida' in result.json()['reply']
    assert not any(req.url.path.endswith('/chat') for req in calls)
    sid=result.json()['session_id']
    continued=c.post('/api/settings/assistant',json={'message':'Explain the settings sections','session_id':sid},headers=headers)
    assert continued.status_code==200 and calls[-1].url.path.endswith('/chat')


def test_owner_csrf_and_validation_before_native(specialist):
    m, c = specialist
    assert c.post('/api/settings/assistant', json={'message': 'help'}).status_code == 401
    headers = login(c)
    assert c.post('/api/settings/assistant', json={'message': 'help'}).status_code == 403
    calls = []
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(native_stub(calls)))
    for payload in ({'message': ''}, {'message': 'x'*20001}, {'message': 'hi', 'profile': 'default'}, {'message': 'hi', 'session_id': 'owner_'+'a'*32}, {'message': 'hi', 'session_id': '../'}, {'message': 'hi', 'session_id': None}):
        assert c.post('/api/settings/assistant', json=payload, headers=headers).status_code == 400
    assert not calls


def test_new_and_resumed_session_use_only_named_profile(specialist):
    m, c = specialist
    headers = login(c); calls = []
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(native_stub(calls)))
    r = c.post('/api/settings/assistant', json={'message': 'help'}, headers=headers)
    assert r.status_code == 200
    sid = r.json()['session_id']
    assert sid.startswith('owner_config_') and len(sid) == 45
    assert r.json() == {'session_id': sid, 'reply': 'I can help configure the app.'}
    assert [req.url.path for req in calls] == ['/p/app-config/api/sessions', '/p/app-config/api/sessions/'+sid+'/chat']
    expected = hmac.new(m.token('HERMES_TOKEN').encode(), b'app-config', hashlib.sha256).hexdigest()
    assert all(req.headers['Authorization'] == 'Bearer '+expected for req in calls)
    assert json.loads(calls[1].content) == {'input': 'help'}
    assert c.post('/api/settings/assistant', json={'message': 'continue', 'session_id': sid}, headers=headers).status_code == 200
    assert len(calls) == 3


@pytest.mark.parametrize('status', [401, 404, 429, 500, 503])
def test_native_errors_are_clean_without_retries(specialist, status):
    m, c = specialist; headers = login(c); calls = []
    def native(req):
        calls.append(req)
        return httpx.Response(status, json={'error': 'internal TOKEN=private-exception'})
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(native))
    r = c.post('/api/settings/assistant', json={'message': 'help', 'session_id': 'owner_config_'+'a'*32}, headers=headers)
    assert r.status_code == (409 if status == 429 else 503 if status == 503 else 502)
    assert 'private-exception' not in r.text and 'TOKEN' not in r.text
    assert len(calls) == 1


@pytest.mark.parametrize('result', [{'message': {'content': 'x'*40001}}, {'error': 'private'}, {'message': {'role': 'tool', 'content': 'private'}}, {'message': {'role': 'assistant', 'content': 'API_SERVER_KEY=accidental-secret'}}])
def test_malformed_or_secret_responses_are_not_echoed(specialist, result):
    m, c = specialist; headers = login(c)
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=result)))
    r = c.post('/api/settings/assistant', json={'message': 'help', 'session_id': 'owner_config_'+'a'*32}, headers=headers)
    assert r.status_code == 502
    assert 'private' not in r.text and 'accidental-secret' not in r.text


def test_failed_native_turn_and_network_timeout_never_echo_diagnostics(specialist):
    m, c = specialist; headers = login(c)
    payload = {'message': 'help', 'session_id': 'owner_config_'+'b'*32}
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
        httpx.Response(200, json={'status': 'failed', 'message': {'role': 'assistant', 'content': 'private native traceback'}})))
    assert c.post('/api/settings/assistant', json=payload, headers=headers).status_code == 502
    def timeout(req):
        raise httpx.ReadTimeout('private native timeout detail', request=req)
    m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    r = c.post('/api/settings/assistant', json=payload, headers=headers)
    assert r.status_code == 502 and 'private' not in r.text


def test_concurrent_specialist_turn_does_not_start_second_native_run(specialist):
    import asyncio
    m, c = specialist; headers = login(c)
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def native(req):
            calls.append(req)
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'message': {'role': 'assistant', 'content': 'done'}})
        m.app.state.internal = httpx.AsyncClient(transport=httpx.MockTransport(native))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url='https://agent.example.test', cookies=c.cookies) as client:
            payload = {'message': 'help', 'session_id': 'owner_config_'+'c'*32}
            first = asyncio.create_task(client.post('/api/settings/assistant', json=payload, headers=headers))
            await entered.wait()
            second = await client.post('/api/settings/assistant', json=payload, headers=headers)
            assert second.status_code == 409 and len(calls) == 1
            release.set()
            assert (await first).status_code == 200
    asyncio.run(exercise())
