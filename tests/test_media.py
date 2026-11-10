import io
import json
import zipfile
import pytest
from starlette.websockets import WebSocketDisconnect
from test_portal import portal, login


def upload(c, name, data, headers):
    return c.post('/api/documents', params={'name': name}, content=data,
                  headers={**headers, 'Content-Type': 'application/octet-stream'})


def test_documents_auth_csrf_and_plaintext(portal):
    _, c = portal
    assert upload(c, 'notas.txt', b'privado', {}).status_code == 401
    h = login(c)
    assert upload(c, 'notas.txt', b'privado', {'Origin':'https://evil.test'}).status_code == 403
    r = upload(c, 'notas.txt', 'Proyecto de Álvaro'.encode(), h)
    assert r.status_code == 200
    assert r.json()['text'] == 'Proyecto de Álvaro'
    assert upload(c, 'script.exe', b'executable', h).status_code == 400
    assert upload(c, 'grande.txt', b'x'*(5*1024*1024+1), h).status_code == 413


def test_docx_and_pdf_failures_are_safe(portal):
    _, c = portal
    h = login(c)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="urn:word"><w:p><w:r><w:t>DOCUMENTO_OK</w:t></w:r></w:p></w:document>')
    assert upload(c, 'informe.docx', data.getvalue(), h).json()['text'] == 'DOCUMENTO_OK'
    assert upload(c, 'roto.pdf', b'not a pdf', h).status_code == 400
    assert upload(c, 'roto.docx', b'not a zip', h).status_code == 400


def test_chat_attachment_text_is_bounded_and_untrusted(portal):
    import httpx
    m,c=portal;h=login(c)
    c.post('/api/vault',json={'openai_key':'test'},headers=h)
    class Internal:
        def build_request(self,*args,**kwargs):
            text=kwargs['json']['input']
            assert 'DOCUMENTO_OK' in text and 'no confiable' in text
            assert 'notas.txt' in text and 'Resume' in text
            return httpx.Request('POST','http://hermes/chat')
        async def send(self,req,**kwargs):
            return httpx.Response(200,content=b'event: run.completed\ndata: {}\n\n',request=req)
    original=m.app.state.internal;m.app.state.internal=Internal()
    try:
        payload={'session_id':'owner_'+'a'*32,'message':'Resume','attachments':[{'name':'notas.txt','text':'DOCUMENTO_OK'}]}
        assert c.post('/api/chat',json=payload,headers=h).status_code==200
        payload['attachments'][0]['text']='x'*60001
        assert c.post('/api/chat',json=payload,headers=h).status_code==400
    finally:m.app.state.internal=original


def test_audio_requires_owner_origin_csrf_before_upstream(portal):
    _,c=portal
    assert c.post('/api/audio/file?name=voz.wav',content=b'audio').status_code==401
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect('/api/audio/live',headers={'Origin':'https://agent.example.test'}):pass
    h=login(c)
    wsheaders={'Origin':'https://agent.example.test','Cookie':'__Host-personal='+c.cookies.get('__Host-personal')}
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect('/api/audio/live',headers={'Origin':'https://evil.test'}):pass
    with c.websocket_connect('/api/audio/live',headers=wsheaders) as ws:
        ws.send_json({'csrf':'incorrect'})
        assert ws.receive_json()['type']=='error'


def test_spanish_icon_controls_and_microphone_policy(portal):
    _,c=portal
    r=c.get('/')
    assert '<html lang="es">' in r.text
    assert 'aria-label="Adjuntar documentos o audios"' in r.text
    assert 'aria-label="Dictar por voz"' in r.text
    assert 'aria-label="Enviar mensaje"' in r.text
    assert 'microphone=(self)' in r.headers['Permissions-Policy']
    assert 'wss://agent.example.test' in r.headers['Content-Security-Policy']


def test_live_transcription_only_forwards_pcm_and_safe_text(portal,monkeypatch):
    import asyncio
    from app import media
    _,c=portal;h=login(c)
    c.post('/api/vault',json={'openai_key':'sk-only-server'},headers=h)
    sent=[]
    class Upstream:
        def __init__(self):self.committed=asyncio.Event()
        async def send(self,payload):
            item=json.loads(payload);sent.append(item)
            if item['type']=='input_audio_buffer.commit':self.committed.set()
        def __aiter__(self):return self.events()
        async def events(self):
            yield json.dumps({'type':'session.updated','secret':'never-return'})
            await self.committed.wait()
            yield json.dumps({'type':'conversation.item.input_audio_transcription.delta','item_id':'one','delta':'Hola'})
            yield json.dumps({'type':'conversation.item.input_audio_transcription.completed','item_id':'one','transcript':'Hola mundo'})
            await asyncio.Event().wait()
    class Context:
        async def __aenter__(self):self.upstream=Upstream();return self.upstream
        async def __aexit__(self,*args):pass
    def connection(url,**kwargs):
        assert url=='wss://api.openai.com/v1/realtime?intent=transcription'
        assert kwargs['additional_headers']=={'Authorization':'Bearer sk-only-server'}
        return Context()
    monkeypatch.setattr(media,'connect',connection)
    headers={'Origin':'https://agent.example.test','Cookie':'__Host-personal='+c.cookies.get('__Host-personal')}
    with c.websocket_connect('/api/audio/live',headers=headers) as ws:
        ws.send_json({'csrf':h['X-CSRF-Token']})
        assert ws.receive_json()=={'type':'ready'}
        ws.send_bytes(b'\0'*4800)
        ws.send_json({'type':'stop'})
        assert ws.receive_json()=={'type':'delta','id':'one','text':'Hola'}
        assert ws.receive_json()=={'type':'completed','id':'one','text':'Hola mundo'}
    assert sent[0]==media.LIVE_SESSION
    assert [x['type'] for x in sent]==['session.update','input_audio_buffer.append','input_audio_buffer.commit']


def test_recorded_audio_streams_text_without_upstream_metadata(portal):
    import httpx
    m,c=portal;h=login(c)
    c.post('/api/vault',json={'openai_key':'sk-only-server'},headers=h)
    class External:
        def build_request(self,method,url,**kwargs):
            assert url=='https://api.openai.com/v1/audio/transcriptions'
            assert kwargs['data']['model']=='gpt-transcribe'
            assert kwargs['data']['stream']=='true'
            assert kwargs['data']['languages[]']=='es'
            assert 'language' not in kwargs['data']
            return httpx.Request(method,url)
        async def send(self,req,**kwargs):
            return httpx.Response(200,request=req,content=b'data: {"type":"transcript.text.delta","delta":"Hola","secret":"sk-only-server"}\n\ndata: {"type":"transcript.text.done","text":"Hola mundo"}\n\n')
    original=m.app.state.external;m.app.state.external=External()
    try:
        r=c.post('/api/audio/file?name=voz.wav',content=b'test-audio',headers=h)
        assert r.status_code==200
        assert 'Hola mundo' in r.text and 'sk-only-server' not in r.text
    finally:m.app.state.external=original


def test_composer_has_no_informational_footer(portal):
    _,c=portal
    html=c.get('/').text
    assert 'PDF, DOCX o texto: 5 MB' not in html
    assert 'El audio se transcribe con OpenAI; revisa el texto antes de enviarlo.' not in html
    assert 'El agente puede trabajar en su espacio, no en el VPS ni en otros proyectos.' not in html
