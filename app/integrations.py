"""Owner-managed API metadata and write-only credentials in the encrypted vault.

Saving does not contact endpoints, widen network egress, or install a connector.
Enabled records publish namespaced variables through the existing runtime relay.
"""
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

SLUG = re.compile(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z')


def managed_variables(value):
    """One authority for integration variables, including disabled records."""
    names = set()
    for key in value.get('integrations', {}):
        prefix = 'APP_' + key.replace('-', '_').upper()
        names.update((prefix + '_API_KEY', prefix + '_BASE_URL'))
    return names


def register_integrations(app, owner, csrf, body, vault, save_vault, is_busy):
    def fail(message='Revisa los datos de la integración.', code=400):
        raise HTTPException(code, message)

    def validate_id(value):
        if not isinstance(value, str) or len(value) > 40 or not SLUG.fullmatch(value):
            fail('Usa un identificador en minúsculas, números y guiones.')
        return value

    def public(item):
        fields = {k: item[k] for k in ('id', 'name', 'url', 'description', 'enabled')}
        fields.update(configured=bool(item.get('credential')), status='not_verified',
                      credential_variable='APP_'+item['id'].replace('-', '_').upper()+'_API_KEY',
                      url_variable='APP_'+item['id'].replace('-', '_').upper()+'_BASE_URL')
        fields['revision'] = hashlib.sha256(json.dumps({**fields, 'version': item['version']},
            sort_keys=True).encode()).hexdigest()
        return fields

    def validate(payload, creating):
        required = {'id', 'name', 'url', 'description', 'enabled'} if creating else {'name', 'url', 'description', 'enabled', 'revision'}
        if set(payload) - (required | {'credential', 'remove_credential'}) or not required <= set(payload):
            fail()
        for key, maximum in [('name', 80), ('description', 1000), ('url', 2048)]:
            value = payload[key]
            if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
                fail()
        if not payload['name'].strip() or type(payload['enabled']) is not bool:
            fail()
        try:
            url = urlsplit(payload['url'])
            host = url.hostname or ''
            if (url.scheme != 'https' or not host or '.' not in host or host.endswith('.')
                    or url.username or url.password or url.query or url.fragment or url.port not in (None, 443)
                    or not re.fullmatch(r'[a-zA-Z0-9.-]+', host) or '..' in host
                    or host.lower().endswith(('.local', '.internal', '.localhost', '.test'))):
                fail('Usa una URL HTTPS pública sin credenciales, consulta ni fragmento.')
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                fail('Usa el dominio público de la API, no una dirección IP.')
        except ValueError:
            fail()
        credential = payload.get('credential', '')
        if (not isinstance(credential, str) or len(credential) > 8192
                or any(ord(c) < 32 for c in credential)
                or type(payload.get('remove_credential', False)) is not bool):
            fail('La credencial no es válida.')
        if credential and payload.get('remove_credential'):
            fail('No se puede reemplazar y quitar la clave a la vez.')
        if credential and any(credential in payload[key] for key in ('name', 'url', 'description')):
            fail('La credencial solo debe ir en el campo protegido.')
        if not creating and (not isinstance(payload['revision'], str) or not re.fullmatch(r'[a-f0-9]{64}', payload['revision'])):
            fail()

    def save(v, item, previous=None):
        items = v.setdefault('integrations', {})
        # Validate effective and retained keys, not only a newly typed password.
        secrets = [record.get('credential') for record in items.values()]
        secrets.extend((item.get('credential'), v.get('openai_key')))
        if any(secret and any(secret in item[key] for key in ('id', 'name', 'url', 'description'))
               for secret in secrets):
            fail('Las credenciales solo deben ir en el campo protegido.')
        if len(items) >= 30 and previous is None:
            fail('Se alcanzó el límite de 30 integraciones.')
        fields = public(item)
        names = (fields['credential_variable'], fields['url_variable'])
        if previous is None and any(name in v['variables'] for name in names):
            fail('Ya existen variables con este identificador. Elige otro.', 409)
        if item['enabled']:
            v['variables'][names[1]] = item['url']
            if item.get('credential'):
                v['variables'][names[0]] = item['credential']
            else:
                v['variables'].pop(names[0], None)
        else:
            for name in names:
                v['variables'].pop(name, None)
        if len(v['variables']) > 64:
            fail('Se alcanzó el máximo de variables.')
        items[item['id']] = item
        v['revision'] += 1
        v['runtime_revision'] = v['revision']
        save_vault(v)
        return fields

    @app.get('/api/integrations')
    async def listing(request: Request):
        owner(request)
        return {'items': [public(value) for _, value in sorted(vault().get('integrations', {}).items())],
                'note': 'Configurada no significa conectada: no se prueba la API ni se amplía la salida de red. Las claves nunca se muestran.'}

    @app.post('/api/integrations')
    async def create(request: Request):
        csrf(request, owner(request))
        payload = await body(request)
        validate(payload, True)
        key = validate_id(payload['id'])
        if is_busy():
            fail('Espera a que termine la respuesta antes de cambiar integraciones.', 409)
        v = vault()
        if key in v.get('integrations', {}):
            fail('Ya existe esta integración.', 409)
        item = {k: payload[k] for k in ('id', 'name', 'url', 'description', 'enabled')}
        item.update(credential=payload.get('credential', ''), version=1)
        return save(v, item)

    @app.put('/api/integrations/{key}')
    async def update(key: str, request: Request):
        csrf(request, owner(request))
        validate_id(key)
        payload = await body(request)
        validate(payload, False)
        if is_busy():
            fail('Espera a que termine la respuesta antes de cambiar integraciones.', 409)
        v = vault()
        previous = v.get('integrations', {}).get(key)
        if previous is None:
            fail('Integración no disponible.', 404)
        if public(previous)['revision'] != payload['revision']:
            fail('La integración cambió. Recárgala antes de guardar.', 409)
        item = {**previous, **{k: payload[k] for k in ('name', 'url', 'description', 'enabled')}, 'version': previous['version']+1}
        if payload.get('credential'):
            item['credential'] = payload['credential']
        if payload.get('remove_credential'):
            item['credential'] = ''
        return save(v, item, previous)
