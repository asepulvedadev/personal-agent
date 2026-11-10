"""Saved history and confirmed hard deletion; never touches live conversations."""
import asyncio
from test_portal import portal, login

ROOT='owner_'+'a'*32
OTHER='owner_'+'b'*32


def test_history_pages_preserve_owner_root_and_bounds(portal,monkeypatch):
    m,c=portal; login(c);calls=[]
    async def native(method,path,**kw):
        calls.append((method,path,kw))
        return {'data':[{'id':'20261110_123456_abcdef','_lineage_root_id':ROOT,'title':'Chat guardado'},
                        {'id':'foreign','title':'No es del portal'}], 'has_more':True}
    monkeypatch.setattr(m,'hermes',native)
    r=c.get('/api/sessions?offset=40&limit=20')
    assert r.status_code==200 and r.json()['data'][0]['id']==ROOT
    assert len(r.json()['data'])==1 and r.json()['has_more'] is True
    assert calls[0][2]['params']=={'limit':20,'offset':40,'source':'api_server'}
    assert c.get('/api/sessions?offset=-1').status_code==400
    assert c.get('/api/sessions?limit=201').status_code==400
    assert c.get('/api/sessions?offset=secret').status_code==400


def test_history_message_pagination_keeps_native_order(portal,monkeypatch):
    m,c=portal;login(c)
    async def native(method,path,**kw):
        assert path==f'/api/sessions/{ROOT}/messages'
        assert kw['params']=={'limit':200,'offset':200,'order':'latest'}
        return {'data':[{'role':'user','content':'anterior'}],'pagination':{'returned':1}}
    monkeypatch.setattr(m,'hermes',native)
    assert c.get(f'/api/sessions/{ROOT}/messages?offset=200').status_code==200


def test_delete_requires_owner_csrf_confirmation_and_idle(portal,monkeypatch):
    m,c=portal;calls=[]
    async def native(*args,**kw):calls.append(args);return {'deleted':True}
    monkeypatch.setattr(m,'hermes',native)
    assert c.request('DELETE',f'/api/sessions/{ROOT}',json={'confirmation':ROOT}).status_code==401
    h=login(c)
    assert c.request('DELETE',f'/api/sessions/{ROOT}',json={'confirmation':ROOT}).status_code==403
    assert c.request('DELETE',f'/api/sessions/{ROOT}',json={'confirmation':'wrong'},headers=h).status_code==400
    asyncio.run(m.busy.acquire())
    try: assert c.request('DELETE',f'/api/sessions/{ROOT}',json={'confirmation':ROOT},headers=h).status_code==409
    finally:m.busy.release()
    assert calls==[]
    r=c.request('DELETE',f'/api/sessions/{ROOT}',json={'confirmation':ROOT},headers=h)
    assert r.status_code==200 and r.json()['deleted'] is True
    assert calls==[('DELETE',f'/api/sessions/{ROOT}')]
    assert not m.busy.locked()


def test_delete_all_snapshots_every_page_and_never_deletes_non_owner(portal,monkeypatch):
    m,c=portal;h=login(c);deleted=[];reads=[]
    async def native(method,path,**kw):
        if method=='GET':
            offset=kw['params']['offset'];reads.append(offset)
            return {'data':[{'id':ROOT if offset==0 else OTHER},{'id':'foreign'}], 'has_more':offset==0}
        assert len(reads)==2
        deleted.append(path);return {'deleted':True}
    monkeypatch.setattr(m,'hermes',native)
    assert c.request('DELETE','/api/sessions',json={'confirmation':'wrong'},headers=h).status_code==400
    r=c.request('DELETE','/api/sessions',json={'confirmation':'ELIMINAR TODO'},headers=h)
    assert r.status_code==200 and r.json()['deleted_count']==2
    assert reads==[0,200]
    assert deleted==[f'/api/sessions/{ROOT}',f'/api/sessions/{OTHER}']
    assert not m.busy.locked()


def test_delete_errors_release_lock_and_preserve_native_status(portal,monkeypatch):
    from fastapi import HTTPException
    m,c=portal;h=login(c)
    async def native(*a,**kw):raise HTTPException(409,'Sesión activa')
    monkeypatch.setattr(m,'hermes',native)
    assert c.request('DELETE',f'/api/sessions/{ROOT}',json={'confirmation':ROOT},headers=h).status_code==409
    assert not m.busy.locked()


def test_all_static_buttons_are_icons_with_accessible_names():
    from html.parser import HTMLParser
    from pathlib import Path
    class Buttons(HTMLParser):
        def __init__(self):super().__init__();self.buttons=[];self.current=None;self.hidden=0
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='button':
                assert self.current is None
                self.current={'attrs':attrs,'visible':'','svg':False}
            if self.current:
                if tag=='svg':self.current['svg']=True;self.hidden+=1
                if tag=='span' and 'sr-only' in attrs.get('class',''):self.hidden+=1
        def handle_endtag(self,tag):
            if self.current:
                if tag in ('svg','span') and self.hidden:self.hidden-=1
                if tag=='button':self.buttons.append(self.current);self.current=None;self.hidden=0
        def handle_data(self,data):
            if self.current and not self.hidden:self.current['visible']+=data
    parser=Buttons();parser.feed(Path('app/static/index.html').read_text())
    assert len(parser.buttons)>20
    for button in parser.buttons:
        assert button['attrs'].get('aria-label'),button
        assert button['svg'] and not button['visible'].strip(),button
