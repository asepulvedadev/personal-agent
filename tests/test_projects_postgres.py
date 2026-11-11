"""Run only against an explicitly supplied disposable PostgreSQL test database."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading

import pytest
from app.projects import PostgresStore, Projects, ProjectError, uid
from test_projects import command

pytestmark = pytest.mark.skipif(not os.getenv('PROJECTS_TEST_DSN'),reason='explicit disposable PROJECTS_TEST_DSN required')


@pytest.fixture
def pg(tmp_path):
    import psycopg
    dsn = os.environ['PROJECTS_TEST_DSN']
    # A unique schema keeps this test isolated from any other module's tables.
    schema = 'test_projects_' + uid()
    with psycopg.connect(dsn,autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA {schema}')
    config = {'conninfo':dsn,'options':f'-c search_path={schema}'}
    workspace = Projects(PostgresStore(config),tmp_path,clock=lambda:'2026-11-11T12:00:00Z')
    yield workspace
    with psycopg.connect(dsn,autocommit=True) as connection:
        connection.execute(f'DROP SCHEMA {schema} CASCADE')


def test_pg_persistence_operation_lock_revision_race(pg):
    first = {'action':'create_project','operation_id':uid(),'data':{'name':'Project'}}
    barrier = threading.Barrier(2)
    def same():
        barrier.wait()
        return pg.command(first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(same) for _ in range(2)]
        results = [future.result() for future in futures]
    assert results[0] == results[1]
    assert len(pg.index()['projects']) == 1
    p = results[0]['project']
    fresh = Projects(PostgresStore(pg.store.config),pg.storage)
    assert fresh.get(p['id']) == p
    barrier = threading.Barrier(2)
    def update(name):
        barrier.wait()
        try: return command(pg,'update_project',{'name':name},p)
        except ProjectError as error: return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update,name) for name in ('A','B')]
        results = [future.result() for future in futures]
    assert sum(result == 409 for result in results) == 1
    assert pg.get(p['id'])['revision'] == 2
    with pytest.raises(ProjectError) as error: fresh.command(first,'carpediev')
    assert error.value.status == 409


def test_pg_client_share_hashed_docs_isolation_and_append_only(pg):
    p = command(pg,'create_project',{'name':'Project','tasks':[{'title':'Visible','visible':True},{'title':'Hidden'}]})['project']
    p = command(pg,'add_document',{'task_id':p['tasks'][1]['id'],'name':'Hidden','content_base64':'eA==','visible':True},p)['project']
    response = command(pg,'create_share',{'label':'Client'},p)
    p,share = response['project'],response['share']
    with pg.store.connect() as connection:
        stored = connection.execute('SELECT hash FROM workspace_shares').fetchone()[0]
        assert stored == pg.hash_token(share['token'])
        assert share['token'] not in str(connection.execute('SELECT result FROM workspace_operations').fetchall())
    public = pg.client_get(share['token'])
    assert len(public['tasks']) == 1 and public['documents'] == []
    assert not {'audit','shares','internal_notes','client_id'} & public.keys()
    with pytest.raises(ProjectError): pg.document(p['documents'][0]['id'],share_token=share['token'])
    data = {'task_id':p['tasks'][0]['id'],'text':'Client question','kind':'question','operation_id':uid()}
    assert pg.client_comment(share['token'],data) == pg.client_comment(share['token'],data)
    p = pg.get(p['id'])
    command(pg,'revoke_share',{'share_id':share['id']},p)
    with pytest.raises(ProjectError): pg.client_comment(share['token'],data)
    import psycopg
    with pytest.raises(psycopg.Error):
        with pg.store.connect() as connection:
            connection.execute('DELETE FROM workspace_audit')


def test_pg_rollback_timer_cross_project_actor_lock(pg):
    projects = [command(pg,'create_project',{'name':name,'tasks':[{'title':'Task'}]})['project'] for name in ('A','B')]
    barrier = threading.Barrier(2)
    def timer(p):
        barrier.wait()
        try: return command(pg,'start_timer',{'task_id':p['tasks'][0]['id']},p)
        except ProjectError as error: return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(timer,p) for p in projects]
        results = [future.result() for future in futures]
    assert sum(result == 409 for result in results) == 1
    before = pg.get(projects[0]['id'])
    with pytest.raises(ProjectError): command(pg,'update_project',{'client_id':uid()},before)
    assert pg.get(before['id']) == before
    with pg.store.connect() as connection:
        audits = connection.execute('SELECT COUNT(*) FROM workspace_audit').fetchone()[0]
    assert audits == 3
