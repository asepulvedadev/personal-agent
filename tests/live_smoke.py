"""Explicitly invoked HTTPS smoke test; reads the local access file without printing it."""
import json
import re
import sys
from pathlib import Path
import httpx

base='https://alvaro.2.24.115.58.sslip.io'
access=Path(sys.argv[1]).read_text()
password=re.search(r'Password: `([^`]+)`',access).group(1)
with httpx.Client(base_url=base,timeout=30,trust_env=False) as c:
    assert c.get('/').status_code==200
    assert c.get('/api/vault').status_code==401
    assert c.get('/internal/runtime-config').status_code==404
    assert c.post('/provider/v1/chat/completions',json={}).status_code==404
    r=c.post('/api/login',json={'password':password},headers={'Origin':base})
    assert r.status_code==200
    assert all(x in r.headers['set-cookie'].lower() for x in ['secure','httponly','samesite=strict'])
    headers={'Origin':base,'X-CSRF-Token':r.json()['csrf']}
    assert c.get('/api/me').status_code==200
    status=c.get('/api/status').json()
    assert status['hermes']=='online',status
    before=c.get('/api/vault').json()
    denied=c.post('/api/vault',json={'variables':{'PATH':'/tmp'}},headers=headers)
    assert denied.status_code==400
    assert c.get('/api/vault').json()==before
    denied=c.post('/api/vault',json={'variables':{'WORKSPACE_TEST_FLAG':'no'}},headers={**headers,'Origin':'https://evil.test'})
    assert denied.status_code==403
    assert c.get('/api/vault').json()==before
    session=c.post('/api/sessions',json={},headers=headers)
    assert session.status_code==200,session.text
    sid=session.json()['session']['id']
    assert c.get(f'/api/sessions/{sid}/messages').status_code==200
    if not before['configured']:
        assert c.post('/api/chat',json={'session_id':sid,'message':'hello'},headers=headers).status_code==409
    update=c.post('/api/vault',json={'variables':{'WORKSPACE_TEST_FLAG':'isolation-smoke-only'}},headers=headers)
    assert update.status_code==200
    assert 'isolation-smoke-only' not in update.text
    print(json.dumps({'https':'verified','authentication':'passed','csrf_origin':'passed','reserved_variables':'blocked','native_sessions':'passed','provider_configured':before['configured'],'variable_revision':update.json()['revision'],'persisted_session_id':sid}))
