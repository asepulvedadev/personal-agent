"""Owner-only final-answer proxy to the isolated native configuration specialist."""
import asyncio
import hashlib
import hmac
import json
import re
import secrets

import httpx
from fastapi import HTTPException, Request

SESSION = re.compile(r'owner_config_[a-f0-9]{32}\Z')
SECRET = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,})\b|(?:API_SERVER_KEY|API_KEY|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD|PRIVATE_KEY)\s*[:=]\s*\S+', re.I)


def register_specialist(app, owner, csrf, body, native_url, native_token):
    lock = asyncio.Lock()

    async def fetch(path, payload):
        key = hmac.new(native_token().encode(), b'app-config', hashlib.sha256).hexdigest()
        try:
            async with app.state.internal.stream('POST', native_url() + '/p/app-config' + path,
                    headers={'Authorization': 'Bearer ' + key}, json=payload) as response:
                if response.status_code >= 400:
                    status = 409 if response.status_code == 429 else 503 if response.status_code == 503 else 502
                    message = ('Hermes está ocupado. Espera a que termine la tarea en curso.' if status == 409
                               else 'El Configurador no está disponible en este momento.')
                    raise HTTPException(status, message)
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 180000:
                        raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (httpx.HTTPError, ValueError, UnicodeError, TypeError):
            raise HTTPException(502, 'No se pudo obtener una respuesta del Configurador.') from None

    @app.post('/api/settings/assistant')
    async def assistant(request: Request):
        csrf(request, owner(request))
        payload = await body(request)
        message = payload.get('message')
        if (set(payload) - {'message', 'session_id'} or not isinstance(message, str)
                or not message.strip() or len(message) > 20000
                or ('session_id' in payload and (not isinstance(payload['session_id'], str)
                    or not SESSION.fullmatch(payload['session_id'])))):
            raise HTTPException(400, 'Mensaje o conversación de configuración inválidos.')
        if lock.locked():
            raise HTTPException(409, 'El Configurador ya está respondiendo. Espera a que termine.')
        async with lock:
            sid = payload.get('session_id') or 'owner_config_' + secrets.token_hex(16)
            # Separate display-only reports; raw candidates never reach the model.
            report_sid = 'owner_' + hashlib.sha256(('configuration:' + sid).encode()).hexdigest()[:32]
            report = await app.state.quarantine_chat.handle(report_sid, message, [])
            if 'session_id' not in payload:
                await fetch('/api/sessions', {'id': sid, 'source': 'api_server'})
            if report is not None:
                return {'session_id': sid, 'reply': report}
            result = await fetch('/api/sessions/' + sid + '/chat', {'input': message})
            answer = result.get('message')
            if (result.get('status', 'completed') != 'completed'
                    or not isinstance(answer, dict) or answer.get('role') != 'assistant'
                    or not isinstance(answer.get('content'), str)
                    or not answer['content'].strip() or len(answer['content']) > 40000
                    or SECRET.search(answer['content']) or native_token() in answer['content']
                    or hmac.new(native_token().encode(), b'app-config', hashlib.sha256).hexdigest() in answer['content']):
                raise HTTPException(502, 'No se pudo obtener una respuesta segura del Configurador.')
            return {'session_id': sid, 'reply': answer['content']}
