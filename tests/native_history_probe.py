"""Run inside pinned Hermes against a disposable /tmp database, never live chats."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,'/opt/hermes')


async def check(home):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter
    from hermes_state import SessionDB
    db=SessionDB(home/'state.db')
    root='owner_'+'a'*32;tip='20261110_143500_a1b2c3';other='owner_'+'b'*32
    db.create_session(root,'api_server');db.append_message(root,'user','synthetic parent')
    db.create_session(other,'api_server');db.append_message(other,'user','unrelated fixture')
    db.publish_compression_child(parent_session_id=root,child_session_id=tip,source='api_server',
        messages=[{'role':'assistant','content':'synthetic summary'}],require_compression_lease=False)
    sessions=home/'sessions';sessions.mkdir(exist_ok=True)
    for sid in (root,tip,other):(sessions/(sid+'.json')).write_text('synthetic transcript')
    adapter=APIServerAdapter(PlatformConfig(enabled=True));adapter._session_db=db
    # Only the disposable handler fixture bypasses auth; portal auth is separately tested.
    adapter._check_auth=lambda request:None
    app=web.Application();app.router.add_delete('/api/sessions/{session_id}',adapter._handle_delete_session)
    async with TestClient(TestServer(app)) as client:
        result=await client.delete('/api/sessions/'+root)
        assert result.status==200,await result.text()
        assert (await result.json())['deleted'] is True
        assert db.get_session(root) is None
        assert db.get_session(tip) is None,'Compression continuation survived deletion'
        assert db.get_session(other) is not None
        assert not (sessions/(root+'.json')).exists()
        assert not (sessions/(tip+'.json')).exists()
        assert (sessions/(other+'.json')).exists()
        assert (await client.delete('/api/sessions/'+root)).status==404
    db.close()
    print('PASS: native API removes compressed root, tip and transcripts; unrelated chat preserved.')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='personal-agent-native-history-') as temp:
        os.environ['HERMES_HOME']=temp
        asyncio.run(check(Path(temp)))
