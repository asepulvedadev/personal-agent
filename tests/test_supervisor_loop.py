"""Exercise the actual supervisor polling loop without network or child processes."""
import ast,hashlib,json,os,sys
from pathlib import Path
from types import SimpleNamespace as NS
from runtime.workspace_config import sync_approved_skills,runtime_config,prepare_specialist
from runtime.telegram_config import prepare, validate_transport

SOURCE=Path('runtime/supervisor.py').read_text()
LOOP=next(n for n in ast.parse(SOURCE).body if isinstance(n,ast.While))

def simulate(tmp_path,snapshots,credentials=None):
    content='---\nname: imported-example\ndescription: A reviewed example.\n---\nUse supplied facts.\n'
    item={'name':'imported-example','profile':'default','content':content,'sha256':hashlib.sha256(content.encode()).hexdigest(),'source_sha256':'a'*64}
    home=tmp_path/'home';home.mkdir()
    calls=[];envs=[];polls=[]
    scope={'HOME':home,'PROJECT':tmp_path,'TOKEN':'synthetic-relay','API_TOKEN':'synthetic-api','revision':None,'backoff':2,'stopping':False,'child':None,'os':NS(environ={'HERMES_YOLO_MODE':'1'}),'json':json,'runtime_config':runtime_config,'sync_approved_skills':sync_approved_skills,'_write':lambda *a,**k:None,'print':lambda *a,**k:None,'time':NS(sleep=lambda *_:None)}
    def config():
        i=len(polls);polls.append(i)
        if i==len(snapshots)-1:scope['stopping']=True
        result={'model':'synthetic-model','revision':1,'variables':{}}
        records=snapshots[i](item)
        if records is not None:result['approved_skills']=records
        return result
    def stop():
        if scope['child'] is not None:calls.append('stop');scope['child']=None
    def spawn(*a,**kw):
        calls.append('start');envs.append(kw['env']);return NS(poll=lambda:None)
    scope.update(config=config,stop_child=stop,subprocess=NS(Popen=spawn),hashlib=hashlib,
                 bot_credential=lambda _:None if credentials is None else credentials[len(polls)-1],prepare=prepare,prepare_specialist=prepare_specialist,validate_transport=validate_transport)
    exec(compile(ast.Module(body=[LOOP],type_ignores=[]),'<supervisor-loop>','exec'),scope)
    return home,calls,envs

def test_unchanged_revision_revokes_and_restarts_child(tmp_path):
    home,calls,envs=simulate(tmp_path,[lambda item:[item],lambda item:[],lambda item:[]])
    assert calls==['start','stop','start']
    assert all('HERMES_YOLO_MODE' not in env for env in envs)
    assert not (home/'skills/approved-imports/imported-example/SKILL.md').exists()
    assert list((home/'disabled-imports').rglob('SKILL.md'))

def test_bad_approval_snapshot_stops_child_without_respawn(tmp_path):
    home,calls,envs=simulate(tmp_path,[lambda item:[item],lambda item:[{'name':'invalid'}]])
    assert calls==['start','stop']


def test_missing_optional_approval_snapshot_does_not_revoke_imports(tmp_path):
    home,calls,envs=simulate(tmp_path,[lambda item:[item],lambda item:None])
    assert calls==['start']
    assert (home/'skills/approved-imports/imported-example/SKILL.md').exists()


def test_token_file_change_restarts_one_transport_without_portal_revision_change(tmp_path):
    token='9272502356:'+'x'*35
    home,calls,envs=simulate(tmp_path,[lambda item:[],lambda item:[]],credentials=[None,token])
    assert calls==['start','stop','start']
    assert 'TELEGRAM_BOT_TOKEN' not in envs[0]
    assert envs[1]['TELEGRAM_BOT_TOKEN']==token
    assert envs[1]['TELEGRAM_ALLOWED_USERS']=='7139153140'
    assert 'TELEGRAM_BOT_TOKEN' not in (home/'profiles/carpediev/.env').read_text()
