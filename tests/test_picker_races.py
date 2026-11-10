import asyncio
import httpx
from test_models import configure
from test_portal import portal

def test_busy_started_during_inventory_wait_rejects_without_writing(portal):
    m,c,h=configure(portal)
    before=(m.CONTROL/'vault.enc').read_bytes()
    async def probe():
        started=asyncio.Event();release=asyncio.Event()
        class External:
            async def aclose(self):pass
            async def get(self,*a,**kw):
                started.set();await release.wait()
                return httpx.Response(200,json={'data':[{'id':'gpt-6-luna'}]})
        m.app.state.external=External()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app),base_url='https://agent.example.test',cookies={'__Host-personal':c.cookies.get('__Host-personal')}) as client:
            pending=asyncio.create_task(client.post('/api/model',json={'model':'gpt-6-luna','reasoning_effort':'low'},headers=h))
            await started.wait();await m.busy.acquire();release.set()
            try:
                response=await pending
                assert response.status_code==409
                assert (m.CONTROL/'vault.enc').read_bytes()==before
            finally:m.busy.release()
    asyncio.run(probe())

def test_key_rotation_during_inventory_wait_does_not_cache_old_inventory(portal):
    m,c,h=configure(portal)
    async def probe():
        started=asyncio.Event();release=asyncio.Event()
        class External:
            async def aclose(self):pass
            async def get(self,*a,**kw):
                started.set();await release.wait()
                return httpx.Response(200,json={'data':[{'id':'gpt-6-luna'}]})
        m.app.state.external=External()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app),base_url='https://agent.example.test',cookies={'__Host-personal':c.cookies.get('__Host-personal')}) as client:
            pending=asyncio.create_task(client.get('/api/models'))
            await started.wait();value=m.vault();value['openai_key']='synthetic-rotated-key';m.save_vault(value);release.set()
            assert (await pending).status_code==409
            assert not m.model_inventory
    asyncio.run(probe())
