"""Owner-only proxy to the trusted Hermes fixed-file editor, never an agent tool."""
import json
import re

import httpx
from fastapi import HTTPException, Request

FILES = {'config': ('config.yaml', 'Ajustes editables sin secretos'),
         'soul': ('SOUL.md', 'Identidad e instrucciones de Hermes'),
         'user': ('USER.md', 'Preferencias y contexto personal')}
MAX_TEXT = 32768
PROFILE = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')


def register_settings(app, owner, csrf, body, native_url, native_token):
    def file_id(value):
        if value not in FILES:
            raise HTTPException(404, 'Archivo no disponible.')
        return value

    def profile_id(request):
        values = request.query_params.getlist('profile') or ['default']
        if len(values) != 1 or not PROFILE.fullmatch(values[0]):
            raise HTTPException(404, 'Agente no disponible.')
        return values[0]

    async def fetch(method, path, payload=None, profile='default'):
        try:
            async with app.state.internal.stream(method, native_url() + path,
                    params=({'profile': profile} if profile != 'default' else None),
                    headers={'Authorization': 'Bearer ' + native_token()},
                    **({'json': payload} if payload is not None else {})) as response:
                if response.status_code >= 400:
                    status = response.status_code if response.status_code in {400, 404, 409, 413, 503} else 502
                    message = {409: 'El archivo cambió. Recárgalo antes de guardar.',
                               413: 'El archivo supera el tamaño permitido.',
                               400: 'Contenido o archivo no permitido. Revisa los ajustes sin incluir secretos.',
                               404: 'Archivo no disponible.'}.get(status, 'No se pudo acceder a la configuración de Hermes.')
                    raise HTTPException(status, message)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 160000:
                        raise ValueError()
            return json.loads(raw)
        except (httpx.HTTPError, ValueError, UnicodeError, TypeError):
            raise HTTPException(502, 'No se pudo acceder a la configuración de Hermes.') from None

    async def exchange(method, key, payload=None, profile='default'):
        try:
            result = await fetch(method, '/api/settings/files/' + key, payload, profile)
            if (not isinstance(result, dict) or set(result) - {'id', 'name', 'content', 'revision', 'note', 'saved'}
                    or result.get('id') != key or result.get('name') != FILES[key][0]
                    or not isinstance(result.get('content'), str)
                    or len(result['content'].encode('utf-8')) > MAX_TEXT
                    or not isinstance(result.get('note'), str) or len(result['note']) > 1000
                    or not isinstance(result.get('revision'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}|missing', result['revision'])):
                raise ValueError()
            return result
        except (httpx.HTTPError, ValueError, UnicodeError, TypeError):
            raise HTTPException(502, 'No se pudo acceder a la configuración de Hermes.') from None

    @app.get('/api/settings/profiles')
    async def profiles(request: Request):
        owner(request)
        result = await fetch('GET', '/api/settings/profiles')
        values = result.get('profiles') if isinstance(result, dict) else None
        if (not isinstance(values, list) or not 1 <= len(values) <= 65
                or any(not isinstance(p, dict) or set(p) != {'id', 'name'}
                       or not isinstance(p['id'], str) or not PROFILE.fullmatch(p['id'])
                       or not isinstance(p['name'], str) or p['name'] != ({'default': 'Hermes Web', 'carpediev': 'Carpediev'}.get(p['id'], p['id'])) for p in values)
                or len({p['id'] for p in values}) != len(values) or values[0]['id'] != 'default'):
            raise HTTPException(502, 'No se pudo cargar la lista de agentes.')
        return {'profiles': values}

    @app.get('/api/settings/files')
    async def listing(request: Request):
        owner(request)
        profile = profile_id(request)
        files = [{'id': key, 'name': name, 'description': description} for key, (name, description) in FILES.items()]
        if profile != 'default':
            result = await fetch('GET', '/api/settings/files', profile=profile)
            values = result.get('files') if isinstance(result, dict) else None
            if (not isinstance(values, list) or not 1 <= len(values) <= len(files)
                    or any(f not in files for f in values)
                    or len({f['id'] for f in values}) != len(values)):
                raise HTTPException(502, 'No se pudo cargar la lista de archivos.')
            files = values
        return {'files': files}

    @app.get('/api/settings/files/{key}')
    async def read(key: str, request: Request):
        owner(request)
        return await exchange('GET', file_id(key), profile=profile_id(request))

    @app.put('/api/settings/files/{key}')
    async def write(key: str, request: Request):
        csrf(request, owner(request))
        file_id(key)
        profile = profile_id(request)
        payload = await body(request)
        if (set(payload) != {'content', 'revision'} or not isinstance(payload['content'], str)
                or not isinstance(payload['revision'], str)
                or not re.fullmatch(r'[a-f0-9]{64}|missing', payload['revision'])):
            raise HTTPException(400, 'Contenido de configuración inválido.')
        try:
            size = len(payload['content'].encode('utf-8'))
        except UnicodeError:
            raise HTTPException(400, 'Contenido de configuración inválido.') from None
        if size > MAX_TEXT:
            raise HTTPException(413, 'El archivo supera el tamaño permitido.')
        return await exchange('PUT', key, payload, profile)
