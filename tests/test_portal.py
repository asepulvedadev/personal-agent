"""Boundary tests for the owner portal; provider calls are stubbed, never billed."""
import importlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTROL_DIR', str(tmp_path/'control'))
    monkeypatch.setenv('PUBLIC_ORIGIN', 'https://agent.example.test')
    monkeypatch.setenv('RUNTIME_TOKEN', 'runtime-test-token-with-enough-entropy')
    monkeypatch.setenv('HERMES_TOKEN', 'hermes-test-token-with-enough-entropy')
    from app import main
    mod=importlib.reload(main)
    mod.initialize('test-owner-password-long')
    with TestClient(mod.app, base_url='https://agent.example.test') as client:
        yield mod, client


def login(client):
    r=client.post('/api/login',json={'password':'test-owner-password-long'},headers={'Origin':'https://agent.example.test'})
    assert r.status_code==200
    return {'Origin':'https://agent.example.test','X-CSRF-Token':r.json()['csrf']}


def test_auth_required(portal):
    _,c=portal
    assert c.get('/api/vault').status_code==401
    assert c.get('/api/status').status_code==401
    assert c.get('/api/sessions').status_code==401


def test_login_cookie_security(portal):
    _,c=portal
    h=login(c)
    assert h['X-CSRF-Token']
    cookie=c.cookies.get('__Host-personal')
    assert cookie
    r=c.get('/api/me')
    assert r.status_code==200
    assert 'Secure' in c.post('/api/login',json={'password':'test-owner-password-long'},headers={'Origin':'https://agent.example.test'}).headers['set-cookie']


def test_wrong_password_and_bruteforce(portal):
    _,c=portal
    for _ in range(5):
        assert c.post('/api/login',json={'password':'wrong'},headers={'Origin':'https://agent.example.test'}).status_code==401
    assert c.post('/api/login',json={'password':'wrong'},headers={'Origin':'https://agent.example.test'}).status_code==429


def test_concurrent_logins_reserve_attempts_before_hashing(portal):
    import asyncio
    import httpx

    mod, _ = portal

    async def burst():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app),
                                     base_url='https://agent.example.test') as client:
            async def wrong_password():
                return await client.post('/api/login', json={'password': 'wrong'},
                    headers={'Origin': 'https://agent.example.test'})

            results = await asyncio.gather(*(wrong_password() for _ in range(12)))
            codes = [result.status_code for result in results]
            assert codes.count(401) == 5
            assert codes.count(429) == 7
            assert (await wrong_password()).status_code == 429
            assert len(mod.attempts['127.0.0.1']) == 5

    asyncio.run(burst())


def test_origin_and_csrf_before_any_write(portal):
    m,c=portal
    h=login(c)
    before=(m.CONTROL/'vault.enc').read_bytes()
    assert c.post('/api/vault',json={'openai_key':'a-secret'},headers={'Origin':'https://evil.test','X-CSRF-Token':h['X-CSRF-Token']}).status_code==403
    assert c.post('/api/vault',json={},headers={'Origin':'https://agent.example.test'}).status_code==403
    assert (m.CONTROL/'vault.enc').read_bytes()==before


def test_vault_encrypted_and_secrets_not_returned(portal):
    m,c=portal
    h=login(c)
    secret='sk-test-not-a-real-openai-key-abcdef'
    r=c.post('/api/vault',json={'openai_key':secret,'model':'gpt-5.4-mini','variables':{'CLIENT_API_KEY':'private-value'}},headers=h)
    assert r.status_code==200
    assert secret not in r.text and 'private-value' not in r.text
    assert secret.encode() not in (m.CONTROL/'vault.enc').read_bytes()
    assert 'private-value' not in c.get('/api/vault').text
    assert oct((m.CONTROL/'vault.enc').stat().st_mode & 0o777)=='0o600'


@pytest.mark.parametrize('name',['PATH','PYTHONPATH','LD_PRELOAD','HOME','HERMES_HOME','OPENAI_BASE_URL','HTTP_PROXY','API_SERVER_HOST','RUNTIME_TOKEN','NODE_OPTIONS'])
def test_environment_cannot_override_security(portal,name):
    m,c=portal
    h=login(c)
    before=(m.CONTROL/'vault.enc').read_bytes()
    r=c.post('/api/vault',json={'variables':{name:'override'}},headers=h)
    assert r.status_code==400
    assert (m.CONTROL/'vault.enc').read_bytes()==before


def test_runtime_never_receives_upstream_openai_key(portal):
    _,c=portal
    h=login(c)
    c.post('/api/vault',json={'openai_key':'sk-secret-never-return','variables':{'CLIENT_API_KEY':'allowed-secret'}},headers=h)
    assert c.get('/internal/runtime-config').status_code==401
    r=c.get('/internal/runtime-config',headers={'Authorization':'Bearer runtime-test-token-with-enough-entropy'})
    assert r.status_code==200
    assert 'sk-secret-never-return' not in r.text
    assert r.json()['variables']['CLIENT_API_KEY']=='allowed-secret'


def test_provider_requires_distinct_service_auth(portal):
    _,c=portal
    assert c.post('/provider/v1/chat/completions',json={}).status_code==401
    assert c.post('/provider/v1/chat/completions',json={},headers={'Authorization':'Bearer runtime-test-token-with-enough-entropy'}).status_code==503
    assert c.post('/provider/v1/unknown',json={},headers={'Authorization':'Bearer runtime-test-token-with-enough-entropy'}).status_code==404


def test_sessions_validate_ids(portal):
    _,c=portal
    h=login(c)
    assert c.post('/api/chat',json={'session_id':'../../etc/passwd','message':'hello'},headers=h).status_code==400


def test_no_raw_validation_echo(portal):
    _,c=portal
    h=login(c)
    secret='should-not-be-echoed'
    r=c.post('/api/vault',json={'model':{'secret':secret}},headers=h)
    assert r.status_code==400
    assert secret not in r.text


def test_oversized_requests_and_security_headers(portal):
    _,c=portal
    h=login(c)
    assert c.post('/api/vault',content='x'*70000,headers=h).status_code==413
    r=c.get('/')
    assert r.headers['X-Content-Type-Options']=='nosniff'
    assert "default-src 'self'" in r.headers['Content-Security-Policy']
    assert r.headers['Cache-Control']=='no-store'


@pytest.mark.parametrize('endpoint,budget_field', [('chat/completions','max_completion_tokens'), ('responses','max_output_tokens')])
def test_provider_sanitizes_errors_and_keeps_upstream_key_out_of_runtime(portal, endpoint, budget_field):
    import httpx
    m,c=portal
    h=login(c)
    c.post('/api/vault',json={'openai_key':'sk-never-echo-me'},headers=h)
    class External:
        def build_request(self,*a,**kw):
            assert kw['headers']['Authorization']=='Bearer sk-never-echo-me'
            assert kw['json'][budget_field]==8192
            assert kw['json']['store'] is False
            return httpx.Request('POST','https://api.openai.com/v1/'+endpoint)
        async def send(self,req,**kw):
            return httpx.Response(401,json={'error':{'message':'Incorrect key: sk-never-echo-me'}},request=req)
    original=m.app.state.external
    m.app.state.external=External()
    try:
        r=c.post('/provider/v1/'+endpoint,json={'model':'gpt-5.4-mini','max_tokens':100000},headers={'Authorization':'Bearer runtime-test-token-with-enough-entropy'})
        assert r.status_code==401 and 'sk-never-echo-me' not in r.text
    finally:
        m.app.state.external=original


def test_real_native_sse_shape_passes_through_and_releases_lock(portal):
    import httpx
    m,c=portal
    h=login(c)
    c.post('/api/vault',json={'openai_key':'test-key'},headers=h)
    class Internal:
        def build_request(self,*args,**kwargs):
            assert kwargs['headers']['Authorization']=='Bearer hermes-test-token-with-enough-entropy'
            return httpx.Request('POST','http://hermes:8642/api/sessions/owner_'+'a'*32+'/chat/stream')
        async def send(self,req,**kwargs):
            return httpx.Response(200,content=b'event: assistant.delta\ndata: {"run_id":"run_test","delta":"hello"}\n\nevent: run.completed\ndata: {"run_id":"run_test"}\n\n',request=req)
    original=m.app.state.internal
    m.app.state.internal=Internal()
    try:
        r=c.post('/api/chat',json={'session_id':'owner_'+'a'*32,'message':'hello'},headers=h)
        assert r.status_code==200
        assert 'assistant.delta' in r.text and 'run.completed' in r.text
        assert not m.busy.locked()
    finally:
        m.app.state.internal=original


def test_settings_shell_has_accessible_presence_and_owner_editor(portal):
    from html.parser import HTMLParser

    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.nodes = {}
            self.scripts = []
            self.in_connection = False
            self.connection_text = ''

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if attrs.get('id'):
                self.nodes[attrs['id']] = (tag, attrs)
            if tag == 'script':
                self.scripts.append(attrs.get('src'))
            if attrs.get('id') == 'connection':
                self.in_connection = True

        def handle_endtag(self, tag):
            if tag == 'div':
                self.in_connection = False

        def handle_data(self, value):
            if self.in_connection:
                self.connection_text += value

    _, client = portal
    parser = Elements()
    parser.feed(client.get('/').text)
    assert parser.connection_text.strip() == ''
    assert parser.nodes['connection'][1]['aria-label']
    assert parser.nodes['config-open'][1]['aria-haspopup'] == 'dialog'
    assert 'hidden' in parser.nodes['config-open'][1]
    assert parser.nodes['config-dialog'][0] == 'dialog'
    assert parser.nodes['config-dialog'][1]['aria-labelledby'] == 'config-title'
    assert parser.nodes['config-content'][0] == 'textarea'
    assert parser.nodes['config-content'][1]['spellcheck'] == 'false'
    assert 'disabled' in parser.nodes['config-content'][1]
    assert parser.scripts.index('/static/config-editor.js') < parser.scripts.index('/static/app.js')
