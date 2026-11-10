"""API connection records never imply a successful provider connection."""
import pytest
from test_portal import portal, login


def record(**changes):
    return dict(id='demo-app', name='Demo API', url='https://api.example.com/v1',
                description='Owner API connection', enabled=True, credential='private-demo-value', **changes)


def test_integrations_owner_csrf_and_secret_redaction(portal):
    m, c = portal
    assert c.get('/api/integrations').status_code == 401
    headers = login(c)
    assert c.post('/api/integrations', json=record()).status_code == 403
    saved = c.post('/api/integrations', json=record(), headers=headers)
    assert saved.status_code == 200
    item = saved.json()
    assert item['configured'] and item['status'] == 'not_verified'
    assert item['credential_variable'] == 'APP_DEMO_APP_API_KEY'
    assert 'private-demo-value' not in saved.text
    assert 'private-demo-value' not in c.get('/api/integrations').text
    assert b'private-demo-value' not in (m.CONTROL / 'vault.enc').read_bytes()
    runtime = c.get('/internal/runtime-config', headers={'Authorization': 'Bearer runtime-test-token-with-enough-entropy'}).json()
    assert runtime['variables']['APP_DEMO_APP_API_KEY'] == 'private-demo-value'
    assert runtime['variables']['APP_DEMO_APP_BASE_URL'] == 'https://api.example.com/v1'
    assert c.get('/api/vault').json()['configured'] is False


def test_integrations_cas_disable_and_no_native_requests(portal):
    m, c = portal
    h = login(c)
    item = c.post('/api/integrations', json=record(), headers=h).json()
    before = (m.CONTROL/'vault.enc').read_bytes()
    payload = {key: item[key] for key in ['name','url','description','enabled','revision']}
    payload['revision'] = '0'*64
    assert c.put('/api/integrations/demo-app', json=payload, headers=h).status_code == 409
    assert (m.CONTROL/'vault.enc').read_bytes() == before
    payload.update(revision=item['revision'], enabled=False)
    result = c.put('/api/integrations/demo-app', json=payload, headers=h)
    assert result.status_code == 200 and result.json()['configured']
    assert 'APP_DEMO_APP_API_KEY' not in m.vault()['variables']
    assert 'APP_DEMO_APP_BASE_URL' not in m.vault()['variables']
    payload.update(revision=result.json()['revision'], enabled=True)
    assert c.put('/api/integrations/demo-app', json=payload, headers=h).status_code == 200
    assert m.vault()['variables']['APP_DEMO_APP_API_KEY'] == 'private-demo-value'


@pytest.mark.parametrize('url', ['http://api.example.com','https://user:pass@api.example.com',
    'https://127.0.0.1', 'https://localhost/x', 'https://api.example.com?token=secret',
    'https://api.example.com/#secret', 'https://api.example.com:9999'])
def test_integrations_reject_unsafe_metadata_without_writes(portal, url):
    m,c = portal
    h=login(c)
    before=(m.CONTROL/'vault.enc').read_bytes()
    payload=record(); payload['url']=url
    assert c.post('/api/integrations',json=payload,headers=h).status_code == 400
    assert (m.CONTROL/'vault.enc').read_bytes() == before


def test_integration_does_not_overwrite_generic_variables(portal):
    m,c=portal; h=login(c)
    assert c.post('/api/vault',json={'variables':{'APP_DEMO_APP_API_KEY':'existing'}},headers=h).status_code == 200
    before=(m.CONTROL/'vault.enc').read_bytes()
    assert c.post('/api/integrations',json=record(),headers=h).status_code == 409
    assert (m.CONTROL/'vault.enc').read_bytes() == before


def test_integration_rotation_and_removal_never_echo_secret(portal):
    m,c=portal; h=login(c)
    item=c.post('/api/integrations',json=record(),headers=h).json()
    payload={k:item[k] for k in ['name','url','description','enabled','revision']}
    payload['credential']='rotated-private-demo'
    updated=c.put('/api/integrations/demo-app',json=payload,headers=h)
    assert updated.status_code == 200 and 'rotated-private-demo' not in updated.text
    payload.update(revision=updated.json()['revision'],credential='',remove_credential=True)
    removed=c.put('/api/integrations/demo-app',json=payload,headers=h)
    assert removed.status_code == 200 and not removed.json()['configured']
    assert 'APP_DEMO_APP_API_KEY' not in m.vault()['variables']


def test_integrations_busy_and_shape_preserve_vault(portal):
    import asyncio
    m,c=portal; h=login(c)
    before=(m.CONTROL/'vault.enc').read_bytes()
    asyncio.run(m.busy.acquire())
    try:
        assert c.post('/api/integrations',json=record(),headers=h).status_code == 409
    finally: m.busy.release()
    assert c.post('/api/integrations',json={**record(), 'token':'not-a-field'},headers=h).status_code == 400
    assert (m.CONTROL/'vault.enc').read_bytes() == before


@pytest.mark.parametrize('extra',[{}, {'credential':''}, {'remove_credential':True}])
def test_retained_credentials_cannot_leak_through_metadata(portal,extra):
    m,c=portal; h=login(c)
    item=c.post('/api/integrations',json=record(),headers=h).json()
    payload={k:item[k] for k in ['name','url','description','enabled','revision']}
    payload.update(description='Accidental key: private-demo-value',**extra)
    before=(m.CONTROL/'vault.enc').read_bytes()
    result=c.put('/api/integrations/demo-app',json=payload,headers=h)
    assert result.status_code==400 and 'private-demo-value' not in result.text
    assert (m.CONTROL/'vault.enc').read_bytes()==before


@pytest.mark.parametrize('payload',[{'variables':{'APP_DEMO_APP_API_KEY':'replacement'}},
    {'variables':{'APP_DEMO_APP_BASE_URL':'https://different.example.com'}},
    {'remove_variables':['APP_DEMO_APP_API_KEY']}])
def test_generic_vault_cannot_change_integration_owned_variables(portal,payload):
    m,c=portal; h=login(c)
    assert c.post('/api/integrations',json=record(),headers=h).status_code==200
    before=(m.CONTROL/'vault.enc').read_bytes()
    assert c.post('/api/vault',json=payload,headers=h).status_code==409
    assert (m.CONTROL/'vault.enc').read_bytes()==before
