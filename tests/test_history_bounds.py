import asyncio
from test_portal import portal,login

def test_all_over_limit_never_deletes(portal,monkeypatch):
 m,c=portal;h=login(c);writes=[]
 async def native(method,path,**kw):
  if method=='DELETE':writes.append(path)
  return {'data':[{'id':'owner_'+'a'*32}],'has_more':True}
 monkeypatch.setattr(m,'hermes',native)
 assert c.request('DELETE','/api/sessions',json={'confirmation':'ELIMINAR TODO'},headers=h).status_code==409
 assert writes==[] and not m.busy.locked()

def test_new_chat_turn_rejected_while_delete_snapshot_is_pending(portal,monkeypatch):
 m,c=portal;h=login(c)
 c.post('/api/vault',json={'openai_key':'test-only'},headers=h)
 async def probe():
  import httpx
  entered=asyncio.Event();release=asyncio.Event()
  async def native(method,path,**kw):
   if method=='GET':entered.set();await release.wait();return {'data':[],'has_more':False}
   raise AssertionError('Unexpected native mutation')
  monkeypatch.setattr(m,'hermes',native)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app),base_url='https://agent.example.test',cookies={'__Host-personal':c.cookies.get('__Host-personal')}) as client:
   deletion=asyncio.create_task(client.request('DELETE','/api/sessions',json={'confirmation':'ELIMINAR TODO'},headers=h))
   await entered.wait()
   response=await client.post('/api/chat',json={'session_id':'owner_'+'a'*32,'message':'test'},headers=h)
   assert response.status_code==409
   release.set();assert (await deletion).status_code==200
 asyncio.run(probe())
