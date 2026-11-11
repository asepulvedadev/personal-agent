"""Public shells contain no data; APIs retain existing owner boundaries."""
from test_portal import portal


def test_projects_shells_and_private_api(portal):
    _, client = portal
    for path in ['/projects', '/project']:
        response = client.get(path)
        assert response.status_code == 200
        assert '/static/projects.js' in response.text
        assert 'Ranchos' not in response.text
    assert client.get('/api/projects').status_code == 401
    assert client.get('/internal/projects').status_code == 401


def test_projects_token_and_db_config_are_not_editable_runtime_variables(portal):
    mod, _ = portal
    for name in ['PROJECTS_DATABASE_FILE','PROJECTS_TOKEN','PROJECTS_TOKEN_FILE']:
        assert not mod.safe_name(name)


def test_unauthenticated_project_commands_reject_before_reading_large_bodies(portal):
    import asyncio
    import httpx
    mod, _ = portal
    async def probe():
        for path in ['/api/projects/command','/internal/projects/command']:
            consumed=[]
            async def stream():
                consumed.append(True)
                yield b' ' * 1_000_000
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app),base_url='https://agent.example.test') as client:
                response=await client.post(path,content=stream(),headers={'Content-Type':'application/json'})
            assert response.status_code==401
            assert consumed==[]
    asyncio.run(probe())


def test_authenticated_owner_and_service_keep_large_document_support(portal,monkeypatch,tmp_path):
    import base64
    from app.projects import Projects,uid
    from app.projects_api import configure_projects
    from test_projects import MemoryStore
    from test_portal import login
    mod,client=portal
    configure_projects(mod.app,lambda:workspace)
    workspace=Projects(MemoryStore(),tmp_path/'documents')
    headers=login(client)
    monkeypatch.setenv('PROJECTS_TOKEN','test-service-projects-token-only')
    p=client.post('/api/projects/command',headers=headers,json={'operation_id':uid(),'action':'create_project','data':{'name':'Upload test'}}).json()['project']
    content=b'bounded actual document\n'*6000
    for path,auth in [('/api/projects/command',headers),('/internal/projects/command',{'Authorization':'Bearer test-service-projects-token-only','X-Project-Actor':'carpediev'})]:
        response=client.post(path,headers=auth,json={'action':'add_document','operation_id':uid(),'project_id':p['id'],'revision':p['revision'],'data':{'name':'Test.txt','media_type':'text/plain','content_base64':base64.b64encode(content).decode()}})
        assert response.status_code==200
        p=response.json()['project']
    assert len(p['documents'])==2
    assert all(doc['size']==len(content) for doc in p['documents'])
