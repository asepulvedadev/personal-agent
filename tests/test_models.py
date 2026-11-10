import json
import pytest
import httpx
from test_portal import portal,login


def configure(portal):
    m,c=portal;h=login(c)
    c.post('/api/vault',json={'openai_key':'sk-owner-private','model':'gpt-6.1-sol'},headers=h)
    class External:
        async def aclose(self):pass
        async def get(self,url,**kwargs):
            assert url=='https://api.openai.com/v1/models'
            assert kwargs['headers']['Authorization']=='Bearer sk-owner-private'
            return httpx.Response(200,json={'data':[{'id':name} for name in ['gpt-6.1-sol','gpt-6-luna','gpt-4.1','gpt-5.5-pro','gpt-transcribe','unknown-model']]})
    m.app.state.external=External()
    return m,c,h


def test_catalog_is_owner_only_and_filtered_by_account_and_capabilities(portal):
    m,c=portal
    assert c.get('/api/models').status_code==401
    m,c,h=configure(portal)
    response=c.get('/api/models')
    assert response.status_code==200
    data=response.json();models={x['id']:x for x in data['models']}
    assert set(models)=={'gpt-6.1-sol','gpt-6-luna','gpt-4.1'}
    assert models['gpt-6.1-sol']['efforts']==['low','medium','high','xhigh','max']
    assert 'none' in models['gpt-6-luna']['efforts']
    assert models['gpt-4.1']['efforts']==[]
    assert 'sk-owner-private' not in response.text


def test_selection_persists_without_runtime_restart_and_rejects_bad_effort(portal):
    m,c,h=configure(portal)
    before=c.get('/internal/runtime-config',headers={'Authorization':'Bearer runtime-test-token-with-enough-entropy'}).json()
    assert c.post('/api/model',json={'model':'gpt-6-luna','reasoning_effort':'low'},headers={'Origin':'https://evil.test'}).status_code==403
    assert c.post('/api/model',json={'model':'gpt-6.1-sol','reasoning_effort':'ultra'},headers=h).status_code==400
    assert c.post('/api/model',json={'model':'gpt-transcribe','reasoning_effort':'low'},headers=h).status_code==400
    r=c.post('/api/model',json={'model':'gpt-6-luna','reasoning_effort':'low'},headers=h)
    assert r.status_code==200
    assert m.vault()['model']=='gpt-6-luna' and m.vault()['reasoning_effort']=='low'
    after=c.get('/internal/runtime-config',headers={'Authorization':'Bearer runtime-test-token-with-enough-entropy'}).json()
    assert after['revision']==before['revision']
    assert m.vault()['openai_key']=='sk-owner-private'
    import asyncio
    asyncio.run(m.busy.acquire())
    try:assert c.post('/api/model',json={'model':'gpt-6.1-sol','reasoning_effort':'high'},headers=h).status_code==409
    finally:m.busy.release()


def test_native_model_lock_and_reasoning_survive_history_selection(portal):
    m,c,h=configure(portal)
    assert c.post('/api/model',json={'model':'gpt-6-luna','reasoning_effort':'high'},headers=h).status_code==200
    class Internal:
        async def aclose(self):pass
        def build_request(self,*args,**kwargs):
            body=kwargs['json']
            assert body['model']=='gpt-6-luna' and body['require_model_lock'] is True
            assert body['model_options']['reasoning']['effort']=='high'
            return httpx.Request('POST','http://hermes/chat')
        async def send(self,request,**kwargs):
            assert m.active_selection=={'model':'gpt-6-luna','reasoning_effort':'high'}
            return httpx.Response(200,content=b'event: run.completed\ndata: {}\n\n',request=request)
    m.app.state.internal=Internal()
    assert c.post('/api/chat',json={'session_id':'owner_'+'a'*32,'message':'hola'},headers=h).status_code==200
    assert m.active_selection is None and not m.busy.locked()


@pytest.mark.parametrize('model,effort',[('gpt-6.1-sol','max'),('gpt-6-luna','none'),('gpt-4.1',None)])
def test_relay_enforces_exact_selection_over_agent_or_auxiliary_requests(portal,model,effort):
    m,c,h=configure(portal)
    assert c.post('/api/model',json={'model':model,'reasoning_effort':effort},headers=h).status_code==200
    class External:
        async def aclose(self):pass
        def build_request(self,*args,**kwargs):
            payload=kwargs['json'];assert payload['model']==model
            if effort is None:assert 'reasoning' not in payload
            else:assert payload['reasoning']['effort']==effort
            assert payload['store'] is False
            return httpx.Request('POST','https://api.openai.com/v1/responses')
        async def send(self,req,**kwargs):return httpx.Response(200,json={'ok':True},request=req)
    m.app.state.external=External()
    r=c.post('/provider/v1/responses',json={'model':'agent-cannot-choose-another-model','reasoning':{'effort':'ultra'}},headers={'Authorization':'Bearer runtime-test-token-with-enough-entropy'})
    assert r.status_code==200


def test_compact_selector_replaces_idle_workspace_label(portal):
    _,c=portal;html=c.get('/').text
    assert 'Hermes · Espacio aislado' not in html
    assert 'aria-label="Cambiar modelo y esfuerzo"' in html
    assert 'id="model-effort"' in html
