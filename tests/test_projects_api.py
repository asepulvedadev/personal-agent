import base64
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.projects import Projects, uid
from app.projects_api import configure_projects, register_projects
from test_projects import MemoryStore


@pytest.fixture
def api(tmp_path):
    app = FastAPI()
    def owner(request):
        if request.cookies.get('owner') != 'yes': raise HTTPException(401,'owner required')
        return {'csrf':'safe'}
    def csrf(request, data=None):
        if request.headers.get('origin') != 'https://portal.test': raise HTTPException(403,'origin')
        if data and request.headers.get('x-csrf-token') != data['csrf']: raise HTTPException(403,'csrf')
    register_projects(app,control=tmp_path,owner=owner,csrf=csrf,token=lambda name:'service-secret' if name=='PROJECTS_TOKEN' else '')
    workspace = Projects(MemoryStore(),tmp_path/'files')
    configure_projects(app,lambda:workspace)
    return TestClient(app),workspace,app


def auth(): return {'origin':'https://portal.test','x-csrf-token':'safe'}


def create(client):
    return client.post('/api/projects/command',json={'action':'create_project','operation_id':uid(),'data':{'name':'Project'}},headers=auth()).json()['project']


def test_owner_csrf_service_boundary(api):
    client,workspace,_ = api
    assert client.get('/api/projects').status_code == 401
    client.cookies.set('owner','yes')
    data = {'action':'create_project','operation_id':uid(),'data':{'name':'Project'}}
    assert client.post('/api/projects/command',json=data).status_code == 403
    assert client.post('/api/projects/command',json=data,headers={'origin':'https://portal.test'}).status_code == 403
    assert client.post('/api/projects/command',json=data,headers=auth()).status_code == 200
    assert client.get('/internal/projects',headers={'authorization':'Bearer service-secret'}).status_code == 403
    assert client.get('/internal/projects',headers={'authorization':'Bearer not-secret','x-project-actor':'carpediev'}).status_code == 401
    headers = {'authorization':'Bearer service-secret','x-project-actor':'carpediev'}
    assert client.get('/internal/projects',headers=headers).status_code == 200
    p = workspace.index()['projects'][0]
    share = {'action':'create_share','operation_id':uid(),'project_id':p['id'],'revision':1,'data':{'label':'Client'}}
    assert client.post('/internal/projects/command',json=share,headers=headers).status_code == 403
    data['operation_id'] = uid()
    assert client.post('/internal/projects/command',json=data,headers=headers).status_code == 200
    assert workspace.index()['projects'][-1]
    assert any(p['audit'][0]['actor']=='carpediev' for p in workspace.store.all('project'))


def test_client_download_token_header_and_revocation(api):
    client,workspace,_ = api
    client.cookies.set('owner','yes')
    p = create(client)
    def cmd(action,data):
        nonlocal p
        value = {'action':action,'operation_id':uid(),'project_id':p['id'],'revision':p['revision'],'data':data}
        result = client.post('/api/projects/command',json=value,headers=auth())
        assert result.status_code == 200,result.text
        p = result.json()['project']
        return result.json()
    cmd('add_task',{'title':'Visible','visible':True})
    cmd('add_document',{'name':'../active\r\n.html','media_type':'text/html','content_base64':base64.b64encode(b'<html>private</html>').decode(),'visible':True})
    share = cmd('create_share',{'label':'Client'})['share']
    headers = {'authorization':'Bearer '+share['token']}
    assert client.get('/api/project-view?token='+share['token']).status_code == 401
    public = client.get('/api/project-view',headers=headers)
    assert public.status_code == 200
    assert 'audit' not in public.text and 'shares' not in public.text
    document_id = p['documents'][0]['id']
    response = client.get('/api/project-view/documents/'+document_id,headers=headers)
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert '\r' not in response.headers['content-disposition'] and '\n' not in response.headers['content-disposition']
    comment = {'task_id':p['tasks'][0]['id'],'text':'Question','kind':'question','operation_id':uid()}
    assert client.post('/api/project-view/comments',json=comment,headers=headers).status_code == 403
    assert client.post('/api/project-view/comments',json=comment,headers={**headers,'origin':'https://portal.test'}).status_code == 200
    p = workspace.get(p['id'])
    cmd('revoke_share',{'share_id':share['id']})
    assert client.get('/api/project-view',headers=headers).status_code == 401
    assert client.get('/api/project-view/documents/'+document_id,headers=headers).status_code == 401
    assert client.post('/api/project-view/comments',json=comment,headers={**headers,'origin':'https://portal.test'}).status_code == 401


def test_unconfigured_and_database_errors_are_generic(api,monkeypatch):
    client,_,app = api
    client.cookies.set('owner','yes')
    del app.state.projects_factory
    monkeypatch.delenv('PROJECTS_DATABASE_FILE',raising=False)
    assert client.get('/api/projects').status_code == 503
    class Broken:
        def index(self): raise RuntimeError('postgresql://user:password@host SQL SELECT secret')
    configure_projects(app,lambda:Broken())
    response = client.get('/api/projects')
    assert response.status_code == 503
    assert 'password' not in response.text and 'SELECT' not in response.text and 'Traceback' not in response.text


def test_json_validation_and_bounded_chunked_upload(api):
    client,_,_ = api
    client.cookies.set('owner','yes')
    for value in ('[]','{"action":NaN}','bad'):
        assert client.post('/api/projects/command',content=value,headers=auth()).status_code == 400
    response = client.post('/api/projects/command',content=iter([b'x'*1024*1024]*15),headers=auth())
    assert response.status_code == 413


def test_service_document_requires_dedicated_token_and_project_scope(api):
    client,workspace,_ = api
    client.cookies.set('owner','yes')
    p = create(client)
    p = workspace.command({'action':'add_document','operation_id':uid(),'project_id':p['id'],'revision':p['revision'],
        'data':{'name':'Private','content_base64':'eA=='}})['project']
    path = f"/internal/projects/{p['id']}/documents/{p['documents'][0]['id']}"
    assert client.get(path).status_code == 401
    headers = {'authorization':'Bearer service-secret','x-project-actor':'hermes-web'}
    assert client.get(path,headers=headers).content == b'x'
    other = create(client)
    assert client.get(path.replace(p['id'],other['id']),headers=headers).status_code == 404


def test_only_owner_can_move_attachment_to_project(api):
    client, workspace, _ = api
    client.cookies.set('owner','yes');p = create(client)
    p = workspace.command({'action':'add_document','operation_id':uid(),'project_id':p['id'],'revision':p['revision'],'data':{'name':'File','content_base64':'eA=='}})['project']
    payload = {'action':'move_document_to_project','operation_id':uid(),'project_id':p['id'],'revision':p['revision'],'data':{'document_id':p['documents'][0]['id']}}
    headers = {'authorization':'Bearer service-secret','x-project-actor':'carpediev'}
    assert client.post('/internal/projects/command',json=payload,headers=headers).status_code == 403
    assert client.post('/api/projects/command',json=payload).status_code == 403
    assert workspace.get(p['id']) == p
    assert client.post('/api/projects/command',json=payload,headers=auth()).status_code == 200
    assert client.post('/internal/projects/command',json=payload,headers=headers).status_code == 403
