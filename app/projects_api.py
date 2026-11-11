"""Owner/service/client HTTP boundaries for the optional project workspace."""
import hmac
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.projects import MAX_COMMAND, PostgresStore, ProjectError, Projects


def configure_projects(app, factory):
    """Inject an application-local workspace factory, primarily for isolated tests."""
    app.state.projects_factory = factory


def register_projects(app, *, control, owner, csrf, token):
    cached = None

    def workspace():
        nonlocal cached
        factory = getattr(app.state, 'projects_factory', None)
        if factory is not None:
            return factory()
        if cached is None:
            filename = os.getenv('PROJECTS_DATABASE_FILE')
            if not filename:
                raise ProjectError(503, 'El módulo de proyectos no está configurado.')
            # This file is private to Web; its contents never appear in service/tool responses.
            try:
                config = json.loads(Path(filename).read_text())
                if not isinstance(config, dict) or not config:
                    raise ValueError()
            except (OSError, ValueError, TypeError):
                raise ProjectError(503, 'El módulo de proyectos no está disponible.') from None
            cached = Projects(PostgresStore(config), Path(control) / 'projects-documents')
        return cached

    async def perform(method, *args, **kwargs):
        try:
            return await run_in_threadpool(lambda: getattr(workspace(),method)(*args,**kwargs))
        except ProjectError as error:
            raise HTTPException(error.status,error.message) from None
        except Exception:
            # Never return SQL, DSN, filesystem paths, stack traces or raw database exceptions.
            raise HTTPException(503,'El módulo de proyectos no está disponible.') from None

    async def body(request, limit=MAX_COMMAND):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>limit: raise HTTPException(413,'La solicitud supera el tamaño permitido.')
        try:
            value = json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value,dict): raise ValueError()
            return value
        except (ValueError,TypeError,UnicodeError,RecursionError):
            raise HTTPException(400,'Se esperaba un objeto JSON.') from None

    def owner_read(request):
        return owner(request)

    def owner_write(request):
        data = owner(request)
        csrf(request,data)
        return 'owner'

    def service(request):
        try: expected = token('PROJECTS_TOKEN')
        except Exception: expected = ''
        supplied = request.headers.get('authorization','')
        if not expected or not hmac.compare_digest(supplied.encode(),('Bearer '+expected).encode()):
            raise HTTPException(401,'Se requiere autenticación del servicio.')
        actor = request.headers.get('x-project-actor','')
        if actor not in {'hermes-web','carpediev'}:
            raise HTTPException(403,'Identifica el servicio autorizado.')
        return actor

    # Reuse these exact guards before the outer middleware buffers large uploads.
    app.state.projects_authorize = lambda request: owner_write(request) if request.url.path == '/api/projects/command' else service(request)

    def client(request):
        value = request.headers.get('authorization','')
        if not value.startswith('Bearer ') or len(value)>207:
            raise HTTPException(401,'El acceso no está disponible.')
        return value[7:]

    def download(document, content):
        name = re.sub(r'[^A-Za-z0-9._ -]','_',document['name']).strip(' .')[:180] or 'document'
        return Response(content,media_type='application/octet-stream',headers={
            'X-Document-Media-Type':document.get('media_type','application/octet-stream') if re.fullmatch(r'[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+',document.get('media_type','')) else 'application/octet-stream',
            'Content-Disposition':f'attachment; filename="{name}"; filename*=UTF-8\'\'{quote(name)}',
            'X-Content-Type-Options':'nosniff','Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.get('/api/projects')
    async def owner_index(request: Request):
        owner_read(request)
        return await perform('index')

    @app.get('/api/projects/{project_id}')
    async def owner_project(project_id: str,request: Request):
        owner_read(request)
        return {'project':await perform('get',project_id)}

    @app.post('/api/projects/command')
    async def owner_command(request: Request):
        actor = owner_write(request)
        return await perform('command',await body(request),actor)

    @app.get('/internal/projects')
    async def service_index(request: Request):
        service(request)
        return await perform('index')

    @app.get('/internal/projects/{project_id}')
    async def service_project(project_id: str,request: Request):
        service(request)
        return {'project':await perform('get',project_id)}

    @app.post('/internal/projects/command')
    async def service_command(request: Request):
        actor = service(request)
        return await perform('command',await body(request),actor)

    @app.get('/api/project-view')
    async def client_view(request: Request):
        return {'project':await perform('client_get',client(request))}

    @app.post('/api/project-view/comments')
    async def client_comment(request: Request):
        share_token = client(request)
        csrf(request)
        return await perform('client_comment',share_token,await body(request,65536))

    @app.get('/api/projects/{project_id}/documents/{document_id}')
    async def owner_document(project_id: str,document_id: str,request: Request):
        owner_read(request)
        document,content = await perform('document',document_id,project_id=project_id)
        return download(document,content)

    @app.get('/api/project-view/documents/{document_id}')
    async def client_document(document_id: str,request: Request):
        document,content = await perform('document',document_id,share_token=client(request))
        return download(document,content)

    @app.get('/internal/projects/{project_id}/documents/{document_id}')
    async def service_document(project_id: str,document_id: str,request: Request):
        service(request)
        document,content = await perform('document',document_id,project_id=project_id)
        return download(document,content)
