import asyncio
import httpx
from test_portal import portal, login

SKILL='popular-web-designs'


def test_skill_catalog_is_owner_only_and_metadata_only(portal,monkeypatch):
    m,c=portal
    assert c.get('/api/skills').status_code==401
    login(c)
    async def native(method,path,**kw):
        assert (method,path)==('GET','/v1/skills')
        return {'data':[{'name':SKILL,'description':'Web sites','category':'web-development','path':'/private','content':'SECRET'},
                        {'name':'../unsafe','description':'unsafe'}, {'name':'sk-secret-token'},
                        {'name':SKILL,'description':'duplicate'}]}
    monkeypatch.setattr(m,'hermes',native)
    r=c.get('/api/skills');assert r.status_code==200
    assert r.json()=={'skills':[{'name':SKILL,'description':'Web sites','category':'web-development'}]}
    assert 'SECRET' not in r.text and '/private' not in r.text


def test_dollar_selection_requests_only_real_skills_before_documents(portal,monkeypatch):
    m,c=portal;h=login(c);c.post('/api/vault',json={'openai_key':'test-only'},headers=h)
    async def native(method,path,**kw):return {'data':[{'name':SKILL,'description':'Web'}]}
    monkeypatch.setattr(m,'hermes',native)
    class Internal:
        async def aclose(self):pass
        def build_request(self,*a,**kw):
            text=kw['json']['input']
            assert f'skill_view(name="{SKILL}")' in text
            assert '$not-installed' in text
            assert text.index('Skills solicitadas')<text.index('Documentos adjuntos:')
            assert text.count(f'skill_view(name="{SKILL}")')==1
            return httpx.Request('POST','http://hermes/chat')
        async def send(self,request,**kw):return httpx.Response(200,content=b'event: run.completed\ndata: {}\n\n',request=request)
    m.app.state.internal=Internal()
    r=c.post('/api/chat',json={'session_id':'owner_'+'a'*32,'message':f'Usa ${SKILL} y ${SKILL} $not-installed',
        'attachments':[{'name':'test.txt','text':'$not-installed'}]},headers=h)
    assert r.status_code==200


def test_currency_and_ordinary_chat_do_not_read_catalog(portal,monkeypatch):
    m,c=portal;h=login(c);c.post('/api/vault',json={'openai_key':'test-only'},headers=h)
    async def native(*a,**kw):raise AssertionError('No catalog lookup for prices')
    monkeypatch.setattr(m,'hermes',native)
    class Internal:
        async def aclose(self):pass
        def build_request(self,*a,**kw):
            assert kw['json']['input']=='Presupuesto $500 y US$100'
            return httpx.Request('POST','http://hermes/chat')
        async def send(self,request,**kw):return httpx.Response(200,content=b'event: run.completed\ndata: {}\n\n',request=request)
    m.app.state.internal=Internal()
    assert c.post('/api/chat',json={'session_id':'owner_'+'a'*32,'message':'Presupuesto $500 y US$100'},headers=h).status_code==200


def test_chat_busy_is_rechecked_after_catalog_lookup(portal,monkeypatch):
    m,c=portal;h=login(c);c.post('/api/vault',json={'openai_key':'test-only'},headers=h)
    async def native(*a,**kw):
        await m.busy.acquire()
        return {'data':[{'name':SKILL}]}
    monkeypatch.setattr(m,'hermes',native)
    try:assert c.post('/api/chat',json={'session_id':'owner_'+'a'*32,'message':'$'+SKILL},headers=h).status_code==409
    finally:
        if m.busy.locked():m.busy.release()


def test_more_than_five_skills_are_rejected_without_starting_run(portal,monkeypatch):
    m,c=portal;h=login(c)
    async def native(*a,**kw):return {'data':[{'name':'skill-'+str(i)} for i in range(6)]}
    monkeypatch.setattr(m,'hermes',native)
    r=c.post('/api/chat',json={'session_id':'owner_'+'a'*32,'message':' '.join('$skill-'+str(i) for i in range(6))},headers=h)
    assert r.status_code==400 and not m.busy.locked()


def test_catalog_rejects_bad_shape_and_pinned_patch_is_exact(tmp_path):
    import pytest
    from fastapi import HTTPException
    from app.skills import metadata
    from runtime.patch_skills import patch
    with pytest.raises(HTTPException):metadata({'data':{'not':'a list'}})
    path=tmp_path/'gateway/platforms/api_server.py';path.parent.mkdir(parents=True)
    path.write_text('''find_all_skills(
                    skip_disabled=False, include_editorial=True
                )''')
    patch(tmp_path);assert path.read_text()=='find_all_skills(skip_disabled=False)'
    with pytest.raises(RuntimeError):patch(tmp_path)
