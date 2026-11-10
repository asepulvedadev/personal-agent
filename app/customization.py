"""Owner-only, CSRF-protected proxy for bounded customization resources."""
import json
import re

import httpx
from fastapi import HTTPException, Request

PROFILE = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
ASSET = re.compile(r'(?:owner|shared|draft):[a-z0-9][a-z0-9_-]{0,63}\Z|installed:[a-f0-9]{64}\Z')
KINDS = {'skill', 'tool', 'plugin', 'script'}
MAX_TEXT = 32768
MESSAGE = 'No se pudo acceder a las personalizaciones de Hermes.'


def register_customization(app, owner, csrf, body, native_url, native_token):
    def parameters(request, *, item=False):
        values = {}
        allowed = {'profile', 'kind', 'scope', 'id'} if item else {'profile', 'kind', 'scope'}
        if set(request.query_params) - allowed:
            raise HTTPException(400, 'Recurso no permitido.')
        for key, default in [('profile', 'default'), ('kind', 'skill'), ('scope', 'profile'), *([('id', '')] if item else [])]:
            candidates = request.query_params.getlist(key) or [default]
            if len(candidates) != 1:
                raise HTTPException(400, 'Recurso no permitido.')
            values[key] = candidates[0]
        if (values['scope'] not in {'profile', 'shared'}
                or (values['scope'] == 'shared' and (values['profile'] != 'default' or values['kind'] != 'skill'))
                or not PROFILE.fullmatch(values['profile']) or values['kind'] not in KINDS
                or (item and not ASSET.fullmatch(values['id']))):
            raise HTTPException(400, 'Recurso no permitido.')
        return values

    def validate_asset(value, kind, *, full=False, expected_id=None):
        keys = {'id', 'name', 'kind', 'status', 'editable'} | ({'content', 'revision'} if full else set())
        if (not isinstance(value, dict) or set(value) != keys
                or not isinstance(value['id'], str) or not ASSET.fullmatch(value['id'])
                or (expected_id is not None and value['id'] != expected_id)
                or not isinstance(value['name'], str) or not PROFILE.fullmatch(value['name'])
                or value['kind'] != kind or type(value['editable']) is not bool
                or value['status'] not in ({'active', 'installed', 'shadowed'} if kind == 'skill' else {'draft'})
                or (kind != 'skill' and not value['id'].startswith('draft:'))
                or (kind == 'skill' and value['id'].startswith('draft:'))
                or (value['status'] == 'shadowed' and not value['id'].startswith('shared:'))):
            raise ValueError()
        if full and (not isinstance(value['content'], str) or len(value['content'].encode('utf-8')) > MAX_TEXT
                     or not isinstance(value['revision'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['revision'])):
            raise ValueError()
        return value

    async def exchange(method, query, payload=None, *, item=False):
        try:
            path = '/api/settings/customization' + ('/item' if item else '')
            async with app.state.internal.stream(method, native_url() + path, params=query,
                    headers={'Authorization': 'Bearer ' + native_token()},
                    **({'json': payload} if payload is not None else {})) as response:
                if response.status_code >= 400:
                    status = response.status_code if response.status_code in {400, 403, 404, 409, 413, 503} else 502
                    messages = {409: 'El recurso cambió o ya existe. Recárgalo antes de guardar.',
                                413: 'El recurso supera el tamaño permitido.', 403: 'Este recurso es de solo lectura.',
                                400: 'Recurso o contenido no permitido.', 404: 'Recurso no disponible.'}
                    raise HTTPException(status, messages.get(status, MESSAGE))
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 200000:
                        raise ValueError()
            result = json.loads(raw)
            if method == 'GET' and not item:
                if (not isinstance(result, dict) or set(result) != {'items', 'note'}
                        or not isinstance(result['items'], list) or len(result['items']) > 512
                        or not isinstance(result['note'], str) or len(result['note']) > 1000):
                    raise ValueError()
                for value in result['items']:
                    validate_asset(value, query['kind'])
                if len({value['id'] for value in result['items']}) != len(result['items']):
                    raise ValueError()
            else:
                validate_asset(result, query['kind'], full=True, expected_id=(query.get('id') if item else
                    ('shared:' if query['scope'] == 'shared' else 'owner:' if query['kind'] == 'skill' else 'draft:') + payload['id']))
            return result
        except (httpx.HTTPError, ValueError, UnicodeError, TypeError, KeyError):
            raise HTTPException(502, MESSAGE) from None

    async def payload_for(request, *, create=False):
        payload = await body(request)
        keys = {'id', 'content'} if create else {'content', 'revision'}
        if not isinstance(payload, dict) or set(payload) != keys or not isinstance(payload['content'], str):
            raise HTTPException(400, 'Contenido inválido.')
        try:
            if len(payload['content'].encode('utf-8')) > MAX_TEXT:
                raise HTTPException(413, 'El recurso supera el tamaño permitido.')
        except UnicodeError:
            raise HTTPException(400, 'Contenido inválido.') from None
        if create:
            if not isinstance(payload['id'], str) or not PROFILE.fullmatch(payload['id']):
                raise HTTPException(400, 'Nombre inválido.')
        elif not isinstance(payload['revision'], str) or not re.fullmatch(r'[a-f0-9]{64}|missing', payload['revision']):
            raise HTTPException(400, 'Revisión inválida.')
        return payload

    @app.get('/api/customization')
    async def listing(request: Request):
        owner(request)
        return await exchange('GET', parameters(request))

    @app.get('/api/customization/item')
    async def read(request: Request):
        owner(request)
        return await exchange('GET', parameters(request, item=True), item=True)

    @app.post('/api/customization')
    async def create(request: Request):
        csrf(request, owner(request))
        query = parameters(request)
        return await exchange('POST', query, await payload_for(request, create=True))

    @app.put('/api/customization/item')
    async def write(request: Request):
        csrf(request, owner(request))
        query = parameters(request, item=True)
        return await exchange('PUT', query, await payload_for(request), item=True)
