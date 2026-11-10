"""Owner-only proxy to the trusted Hermes fixed-file editor, never an agent tool."""
import json
import re

import httpx
from fastapi import HTTPException, Request

FILES = {'config': ('config.yaml', 'Ajustes editables sin secretos'),
         'soul': ('SOUL.md', 'Identidad e instrucciones de Hermes'),
         'user': ('USER.md', 'Preferencias y contexto personal')}
MAX_TEXT = 32768


def register_settings(app, owner, csrf, body, native_url, native_token):
    def file_id(value):
        if value not in FILES:
            raise HTTPException(404, 'Archivo no disponible.')
        return value

    async def exchange(method, key, payload=None):
        try:
            async with app.state.internal.stream(method, native_url() + '/api/settings/files/' + key,
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
            result = json.loads(raw)
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

    @app.get('/api/settings/files')
    async def listing(request: Request):
        owner(request)
        return {'files': [{'id': key, 'name': name, 'description': description}
                          for key, (name, description) in FILES.items()]}

    @app.get('/api/settings/files/{key}')
    async def read(key: str, request: Request):
        owner(request)
        return await exchange('GET', file_id(key))

    @app.put('/api/settings/files/{key}')
    async def write(key: str, request: Request):
        csrf(request, owner(request))
        file_id(key)
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
        return await exchange('PUT', key, payload)
