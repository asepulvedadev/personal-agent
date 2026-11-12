import pytest
from app.projects import ProjectError, new_project, edit_task


def test_done_requires_resolution_and_evidence():
    task = {'status':'todo','resolution':'','evidence':''}
    with pytest.raises(ProjectError):
        edit_task(task, {'status':'done'})


def test_create_has_unknown_estimates():
    project = new_project({'name':'Client workspace'})
    assert project['revision'] == 1
    assert project['tasks'] == []
    assert project['shares'] == []

import base64
import copy
from contextlib import contextmanager
from app.projects import Projects, client_project, uid


class MemoryStore:
    """Isolated deterministic test double, never selected by production configuration."""
    def __init__(self):
        self.state = {'project':{},'contact':{},'operations':{},'shares':{},'audit':[]}

    @contextmanager
    def transaction(self, *args):
        before = copy.deepcopy(self.state)
        try: yield self
        except Exception:
            self.state = before
            raise

    def get(self, kind, item_id, lock=False):
        if item_id not in self.state[kind]: raise ProjectError(404,'missing')
        return copy.deepcopy(self.state[kind][item_id])

    def all(self, kind): return copy.deepcopy(list(self.state[kind].values()))
    def save(self, kind, value): self.state[kind][value['id']] = copy.deepcopy(value)
    def operation(self, value): return self.state['operations'].get(value)
    def record_operation(self, op, actor, fingerprint, result):
        self.state['operations'][op] = (actor,fingerprint,copy.deepcopy(result))
    def share(self, hashed, lock=False):
        row = self.state['shares'].get(hashed)
        if not row or row[2]: raise ProjectError(401,'revoked')
        return row
    def add_share(self, hashed, share_id, project_id): self.state['shares'][hashed] = (share_id,project_id,False)
    def revoke_share(self, share_id, project_id):
        for hashed,row in self.state['shares'].items():
            if row[:2] == (share_id,project_id): self.state['shares'][hashed] = (share_id,project_id,True)
    def audit(self, project_id, event): self.state['audit'].append((project_id,event))


@pytest.fixture
def workspace(tmp_path):
    return Projects(MemoryStore(),tmp_path,clock=lambda:'2026-11-11T12:00:00Z')


def command(workspace, action, data=None, project=None, actor='owner', operation_id=None):
    value = {'action':action,'operation_id':operation_id or uid(),'data':data or {}}
    if project: value.update(project_id=project['id'],revision=project['revision'])
    return workspace.command(value,actor)


def test_contact_project_groups_and_all_task_fields(workspace):
    contact = command(workspace,'create_contact',{'name':'Ana','status':'client'})['contact']
    contact = command(workspace,'update_contact',{'contact_id':contact['id'],'revision':1,'company':'Studio'})['contact']
    assert contact['revision'] == 2
    p = command(workspace,'create_project',{'name':'Project','client_id':contact['id'],'groups':[{'name':'A'}],
        'tasks':[{'title':'Proposal','code':'A1','group_id':'A','priority':'high'}]})['project']
    assert p['tasks'][0]['group_id'] == p['groups'][0]['id']
    p = command(workspace,'update_group',{'group_id':p['groups'][0]['id'],'name':'Renamed'},p)['project']
    p = command(workspace,'add_group',{'name':'B'},p)['project']
    p = command(workspace,'add_task',{'title':'Next','estimated_minutes':30,'why':'why','benefit':'benefit',
        'proposal':'proposal','approval':'approved','visible':True},p)['project']
    p = command(workspace,'update_project',{'internal_notes':'private','status':'active'},p)['project']
    task = p['tasks'][1]
    p = command(workspace,'update_task',{'task_id':task['id'],'status':'blocked','blocker':'access',
        'blocker_owner':'owner','next_step':'request access'},p)['project']
    p = command(workspace,'update_task',{'task_id':task['id'],'status':'done','resolution':'implemented','evidence':'test'},p)['project']
    assert p['tasks'][1]['status'] == 'done'
    assert workspace.index()['projects'][0]['estimated_minutes'] is None
    assert workspace.index()['projects'][0]['done_count'] == 1


def test_revisions_and_idempotency_bound_to_actor_input(workspace):
    op = uid()
    original = {'action':'create_project','operation_id':op,'data':{'name':'Project'}}
    first = workspace.command(original)
    assert workspace.command(original) == first
    with pytest.raises(ProjectError) as e: workspace.command(original,'carpediev')
    assert e.value.status == 409
    with pytest.raises(ProjectError): workspace.command({**original,'data':{'name':'Other'}})
    p = first['project']
    updated = command(workspace,'add_group',{'name':'A'},p)['project']
    before = copy.deepcopy(workspace.store.state)
    with pytest.raises(ProjectError) as e: command(workspace,'add_task',{'title':'stale'},p)
    assert e.value.status == 409
    assert workspace.store.state == before
    assert workspace.get(p['id']) == updated


def test_timer_actual_elapsed_and_one_per_actor(workspace):
    p = command(workspace,'create_project',{'name':'Project','tasks':[{'title':'One'},{'title':'Two'}]})['project']
    p = command(workspace,'start_timer',{'task_id':p['tasks'][0]['id'],'note':'work'},p)['project']
    with pytest.raises(ProjectError): command(workspace,'start_timer',{'task_id':p['tasks'][1]['id']},p)
    with pytest.raises(ProjectError): command(workspace,'stop_timer',{'task_id':p['tasks'][0]['id']},p,actor='carpediev')
    workspace.clock = lambda:'2026-11-11T12:02:35Z'
    p = command(workspace,'stop_timer',{'task_id':p['tasks'][0]['id']},p)['project']
    assert p['tasks'][0]['timer'] is None
    assert p['tasks'][0]['time_entries'][0]['minutes'] == 2
    p = command(workspace,'add_time',{'task_id':p['tasks'][1]['id'],'date':'2026-11-10','minutes':15,'visible':True},p)['project']
    assert workspace.index()['projects'][0]['tracked_minutes'] == 17


def test_shares_client_comment_redaction_and_revocation(workspace):
    p = command(workspace,'create_project',{'name':'Project','tasks':[{'title':'Visible','visible':True,'internal_notes':'secret'},
        {'title':'Hidden','internal_notes':'secret'}]})['project']
    for action in ('create_share','revoke_share'):
        with pytest.raises(ProjectError) as e: command(workspace,action,{'label':'Client'},p,actor='hermes-web')
        assert e.value.status == 403
    response = command(workspace,'create_share',{'label':'Client'},p)
    p, share = response['project'],response['share']
    assert share['token'] not in repr(workspace.store.state)
    visible, hidden = p['tasks']
    p = command(workspace,'add_comment',{'task_id':visible['id'],'text':'private note','visible':False},p)['project']
    p = command(workspace,'add_comment',{'task_id':visible['id'],'text':'public answer','kind':'answer','visible':True},p)['project']
    public = workspace.client_get(share['token'])
    assert len(public['tasks']) == 1
    assert 'internal_notes' not in repr(public)
    assert 'audit' not in public and 'shares' not in public and 'client_id' not in public
    assert len(public['tasks'][0]['comments']) == 1
    assert 'actor' not in repr(public)
    data = {'task_id':visible['id'],'text':'Question','kind':'question','operation_id':uid()}
    result = workspace.client_comment(share['token'],data)
    assert workspace.client_comment(share['token'],data) == result
    with pytest.raises(ProjectError): workspace.client_comment(share['token'],{**data,'task_id':hidden['id'],'operation_id':uid()})
    p = workspace.get(p['id'])
    p = command(workspace,'revoke_share',{'share_id':share['id']},p)['project']
    with pytest.raises(ProjectError): workspace.client_get(share['token'])
    with pytest.raises(ProjectError): workspace.client_comment(share['token'],data)


def test_documents_private_scope_and_rollback(workspace):
    p = command(workspace,'create_project',{'name':'Project','tasks':[{'title':'Visible','visible':True},{'title':'Hidden'}]})['project']
    for task_id,visible in [(None,True),(p['tasks'][1]['id'],True),(None,False)]:
        p = command(workspace,'add_document',{'name':'../test.html','task_id':task_id,'media_type':'text/html',
            'content_base64':base64.b64encode(b'<script>x</script>').decode(),'visible':visible},p)['project']
    response = command(workspace,'create_share',{'label':'Client'},p)
    p,share = response['project'],response['share']
    assert len(workspace.client_get(share['token'])['documents']) == 1
    doc,content = workspace.document(p['documents'][0]['id'],share_token=share['token'])
    assert content == b'<script>x</script>'
    with pytest.raises(ProjectError): workspace.document(p['documents'][1]['id'],share_token=share['token'])
    other = command(workspace,'create_project',{'name':'Other'})['project']
    with pytest.raises(ProjectError): workspace.document(p['documents'][0]['id'],project_id=other['id'])
    files_before = set(workspace.storage.rglob('*'))
    original = workspace.store.record_operation
    workspace.store.record_operation = lambda *args: (_ for _ in ()).throw(RuntimeError('database failed'))
    with pytest.raises(RuntimeError):
        command(workspace,'add_document',{'name':'fail','content_base64':'eA=='},p)
    workspace.store.record_operation = original
    assert set(workspace.storage.rglob('*')) == files_before
    assert len(workspace.get(p['id'])['documents']) == 3


@pytest.mark.parametrize('action,data',[
    ('add_task',{'title':'x','status':'done'}),('add_task',{'title':'x','status':'blocked'}),
    ('add_task',{'title':'x','visible':'true'}),('add_task',{'title':'x','estimated_minutes':True}),
    ('update_project',{'status':[]}),('add_document',{'name':'file','content_base64':'***'}),
    ('add_task',{'title':'x','unknown':'field'}),('add_group',{'name':''})])
def test_validation_no_mutation(workspace,action,data):
    p = command(workspace,'create_project',{'name':'Project'})['project']
    before = copy.deepcopy(workspace.store.state)
    with pytest.raises(ProjectError): command(workspace,action,data,p)
    assert workspace.store.state == before


def test_visibility_edit_is_project_scoped_and_client_forbidden(workspace):
    p = command(workspace,'create_project',{'name':'Project','tasks':[{'title':'Task','visible':True}]})['project']
    task_id = p['tasks'][0]['id']
    p = command(workspace,'add_comment',{'task_id':task_id,'text':'Private'},p)['project']
    p = command(workspace,'add_time',{'task_id':task_id,'date':'2026-11-11','minutes':10},p)['project']
    p = command(workspace,'add_document',{'name':'Document','content_base64':'eA=='},p)['project']
    items = [('comment',p['tasks'][0]['comments'][0]['id']),('time',p['tasks'][0]['time_entries'][0]['id']),('document',p['documents'][0]['id'])]
    other = command(workspace,'create_project',{'name':'Other'})['project']
    for kind,item_id in items:
        p = command(workspace,'set_visibility',{'kind':kind,'id':item_id,'visible':True},p,actor='carpediev')['project']
        with pytest.raises(ProjectError): command(workspace,'set_visibility',{'kind':kind,'id':item_id,'visible':False},other)
    public = client_project(p)
    assert len(public['tasks'][0]['comments']) == 1
    assert len(public['tasks'][0]['time_entries']) == 1
    assert len(public['documents']) == 1
    response = command(workspace,'create_share',{'label':'Client'},p)
    with pytest.raises(ProjectError) as error:
        workspace.command({'action':'set_visibility','operation_id':uid(),'data':{'kind':'document','id':items[2][1],'visible':False}},client_token=response['share']['token'])
    assert error.value.status == 403


def test_client_replay_reprojects_current_visibility_without_new_mutation(workspace):
    p=command(workspace,'create_project',{'name':'Workspace','tasks':[{'title':'Visible work','visible':True,'blocker_owner':'Owner contact'}]})['project']
    task=p['tasks'][0]['id']
    p=command(workspace,'add_document',{'name':'Notes','media_type':'text/plain','content_base64':base64.b64encode(b'private attachment').decode(),'visible':True},p)['project']
    share=command(workspace,'create_share',{'label':'Client'},p)
    payload={'task_id':task,'text':'A question','kind':'question','operation_id':uid()}
    first=workspace.client_comment(share['share']['token'],payload)
    assert len(first['project']['tasks'])==1
    current=workspace.get(p['id'])
    current=command(workspace,'update_task',{'task_id':task,'visible':False},current)['project']
    current=command(workspace,'set_visibility',{'kind':'document','id':current['documents'][0]['id'],'visible':False},current)['project']
    before=copy.deepcopy(workspace.store.state)
    replay=workspace.client_comment(share['share']['token'],payload)
    assert replay['project']['tasks']==[] and replay['project']['documents']==[]
    assert workspace.store.state==before


@pytest.mark.parametrize('visible',[False,True])
def test_owner_archives_task_attachment_without_changing_bytes_or_tasks(workspace,visible):
    p = command(workspace,'create_project',{'name':'Project','tasks':[{'title':'Task','visible':True}]})['project']
    task_id = p['tasks'][0]['id']
    p = command(workspace,'add_document',{'name':'Draft.mp4','task_id':task_id,'media_type':'video/mp4','content_base64':'ZHJhZnQ=','visible':visible},p)['project']
    result = command(workspace,'create_share',{'label':'Client'},p);p = result['project'];token = result['share']['token']
    before = copy.deepcopy(p);document_id = p['documents'][0]['id'];operation_id = uid()
    archived = command(workspace,'move_document_to_project',{'document_id':document_id},p,operation_id=operation_id)['project']
    assert archived['tasks'] == before['tasks']
    assert archived['documents'][0] == {**before['documents'][0],'task_id':None}
    assert workspace.document(document_id,project_id=p['id'])[1] == b'draft'
    assert workspace.client_get(token)['documents'] == ([archived['documents'][0]] if visible else [])
    if visible: assert workspace.document(document_id,share_token=token)[1] == b'draft'
    else:
        with pytest.raises(ProjectError) as error: workspace.document(document_id,share_token=token)
        assert error.value.status == 404
    replay = command(workspace,'move_document_to_project',{'document_id':document_id},p,operation_id=operation_id)['project']
    assert replay == archived and workspace.get(p['id'])['revision'] == archived['revision']
    for actor in ('hermes-web','carpediev'):
        with pytest.raises(ProjectError) as error: command(workspace,'move_document_to_project',{'document_id':document_id},p,actor=actor,operation_id=operation_id)
        assert error.value.status == 403
    other = command(workspace,'create_project',{'name':'Other'})['project']
    with pytest.raises(ProjectError): command(workspace,'move_document_to_project',{'document_id':document_id},other)
    assert workspace.get(other['id']) == other
    with pytest.raises(ProjectError) as error: command(workspace,'move_document_to_project',{'document_id':document_id},p)
    assert error.value.status == 409


def test_moving_document_cannot_bypass_hidden_parent_task(workspace):
    p = command(workspace,'create_project',{'name':'Private task','tasks':[{'title':'Hidden','visible':False}]})['project']
    p = command(workspace,'add_document',{'name':'Hidden.txt','task_id':p['tasks'][0]['id'],'content_base64':'eA==','visible':True},p)['project']
    result=command(workspace,'create_share',{'label':'Client'},p);p=result['project'];token=result['share']['token']
    assert workspace.client_get(token)['documents'] == []
    before=copy.deepcopy(workspace.store.state)
    with pytest.raises(ProjectError) as error: command(workspace,'move_document_to_project',{'document_id':p['documents'][0]['id']},p)
    assert error.value.status == 400
    assert workspace.store.state == before
    assert workspace.client_get(token)['documents'] == []
