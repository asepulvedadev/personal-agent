"""Single-owner portal. The agent never receives the real OpenAI credential."""
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from app.media import register_media, attachment_message
from app.images import register_images
from app.quarantine import register_quarantine, approved_skills
from app.quarantine_chat import events as quarantine_events
from app.models import CATALOG, selection, enforce_selection
from app.skills import catalog as skill_catalog, requested as requested_skills
from app.settings import register_settings
from app.integrations import register_integrations, managed_variables
from app.customization import register_customization
from app.specialist import register_specialist
from app.projects_api import register_projects
from itsdangerous import BadSignature, URLSafeTimedSerializer

CONTROL = Path(os.getenv("CONTROL_DIR", "/control"))
ORIGIN = os.getenv("PUBLIC_ORIGIN", "https://alvaro.2.24.115.58.sslip.io")
HERMES = os.getenv("HERMES_URL", "http://hermes:8642")
STATIC = Path(__file__).parent / "static"
COOKIE = "__Host-personal"
SESSION_TTL = 8 * 3600
DENIED = {"PATH", "HOME", "USER", "SHELL", "ENV", "BASH_ENV", "NODE_OPTIONS",
          "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "VIRTUAL_ENV",
          "UV_PROJECT_ENVIRONMENT", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
          "http_proxy", "https_proxy", "all_proxy", "no_proxy", "OPENAI_API_KEY", "OPENAI_BASE_URL",
          "OPENAI_API_BASE", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
          "CURL_CA_BUNDLE", "CONTROL_DIR", "PUBLIC_ORIGIN", "RUNTIME_TOKEN", "HERMES_TOKEN", "PROJECTS_DATABASE_FILE", "PROJECTS_TOKEN", "PROJECTS_TOKEN_FILE"}
SESSION_ID = re.compile(r"owner_[a-f0-9]{32}\Z")
MODEL_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{1,100}\Z")
NAME = re.compile(r"[A-Z][A-Z0-9_]{1,79}\Z")
attempts: dict[str, list[float]] = {}
provider_attempts: list[float] = []
busy = asyncio.Lock()
login_workers = asyncio.Semaphore(2)
active_selection = None
model_inventory = {}


def token(name):
    path = os.getenv(name + "_FILE")
    return Path(path).read_text().strip() if path else os.getenv(name, "")


def atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def initialize(password=None):
    CONTROL.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not (CONTROL / "master.key").exists():
        atomic(CONTROL / "master.key", Fernet.generate_key())
    if not (CONTROL / "auth.json").exists():
        if password is None:
            password = Path(os.environ["BOOTSTRAP_PASSWORD_FILE"]).read_text().strip()
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        atomic(CONTROL / "auth.json", json.dumps({"salt": salt.hex(), "hash": digest.hex(),
               "signing": secrets.token_hex(32)}).encode())
    if not (CONTROL / "vault.enc").exists():
        save_vault({"openai_key": "", "model": "gpt-5.4-mini", "variables": {}, "revision": 0})


def cipher():
    return Fernet((CONTROL / "master.key").read_bytes())


def vault():
    return json.loads(cipher().decrypt((CONTROL / "vault.enc").read_bytes()))


def save_vault(value):
    atomic(CONTROL / "vault.enc", cipher().encrypt(json.dumps(value).encode()))


def auth_data():
    return json.loads((CONTROL / "auth.json").read_text())


def signer():
    return URLSafeTimedSerializer(auth_data()["signing"], salt="owner-session-v1")


def owner(request):
    try:
        data = signer().loads(request.cookies.get(COOKIE, ""), max_age=SESSION_TTL)
        if data.get("owner") is not True:
            raise BadSignature("invalid owner")
        return data
    except (BadSignature, ValueError, KeyError):
        raise HTTPException(401, "Inicia sesión para continuar.")


def csrf(request, data=None):
    if request.headers.get("origin") != ORIGIN:
        raise HTTPException(403, "Origen de la solicitud no permitido.")
    if data is not None and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), data["csrf"]):
        raise HTTPException(403, "Token de seguridad inválido. Recarga la página.")


def service(request):
    expected = token("RUNTIME_TOKEN")
    supplied = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(supplied, "Bearer " + expected):
        raise HTTPException(401, "Se requiere autenticación del servicio.")


async def body(request):
    try:
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise HTTPException(400, "Se esperaba un objeto JSON.")


def safe_name(name):
    return (isinstance(name, str) and NAME.fullmatch(name) and name not in DENIED
            and not name.startswith(("LD_", "DYLD_", "HERMES_", "API_SERVER_", "UV_")))


def session_id(value):
    if not isinstance(value, str) or not SESSION_ID.fullmatch(value):
        raise HTTPException(400, "Identificador de conversación inválido.")
    return value


@asynccontextmanager
async def lifespan(app):
    initialize()
    app.state.internal = httpx.AsyncClient(timeout=httpx.Timeout(15, read=600), trust_env=False)
    app.state.external = httpx.AsyncClient(timeout=httpx.Timeout(20, read=300),
        proxy=os.getenv("HTTPS_PROXY"), trust_env=False)
    yield
    await app.state.internal.aclose()
    await app.state.external.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def boundaries(request, call_next):
    upload = request.url.path in {'/api/documents', '/api/audio/file', '/api/quarantine/upload', '/internal/quarantine/upload'}
    limit = (10*1024**2 if request.url.path == '/api/audio/file' else 5*1024**2) if upload else (300_000 if request.url.path == '/api/chat' else (2_000_000 if request.url.path.startswith('/provider/') else 65_536))
    if request.url.path in {"/api/projects/command", "/internal/projects/command"}:
        limit = 14 * 1024**2
    try:
        size = int(request.headers.get("content-length", "0"))
    except ValueError:
        return JSONResponse({"detail": "Tamaño de solicitud inválido."}, status_code=400)
    if size > limit:
        return JSONResponse({"detail": "La solicitud supera el tamaño permitido."}, status_code=413)
    # Read and bound chunked bodies too; no request bodies are logged.
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not upload:
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                return JSONResponse({"detail": "La solicitud supera el tamaño permitido."}, status_code=413)
        request._body = bytes(data)
    response = await call_next(request)
    response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY", "X-Robots-Tag": "noindex, nofollow",
        "Content-Security-Policy": f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self' {ORIGIN.replace('https://', 'wss://').replace('http://', 'ws://')}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        "Permissions-Policy": "camera=(), microphone=(self), geolocation=()"})
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/login")
async def login(request: Request):
    csrf(request)
    client = request.client.host
    now = time.time()
    for key in list(attempts):
        attempts[key] = [t for t in attempts[key] if now-t < 300]
        if not attempts[key]:
            del attempts[key]
    times = attempts.setdefault(client, [])
    if len(times) >= 5 or len(attempts) > 2048:
        raise HTTPException(429, "Demasiados intentos. Espera cinco minutos.")
    # Reserve before any await so concurrent requests count pending hashes too.
    times.append(now)
    payload = await body(request)
    password = payload.get("password", "")
    if not isinstance(password, str) or len(password) > 512:
        raise HTTPException(400, "Credenciales inválidas.")
    auth = auth_data()
    async with login_workers:
        digest = await asyncio.to_thread(hashlib.scrypt, password.encode(), salt=bytes.fromhex(auth["salt"]), n=16384, r=8, p=1)
    if not hmac.compare_digest(digest.hex(), auth["hash"]):
        raise HTTPException(401, "Contraseña incorrecta.")
    attempts.pop(client, None)
    data = {"owner": True, "csrf": secrets.token_urlsafe(24)}
    response = JSONResponse({"csrf": data["csrf"]})
    response.set_cookie(COOKIE, signer().dumps(data), max_age=SESSION_TTL,
        httponly=True, secure=True, samesite="strict", path="/")
    return response


@app.get("/api/me")
async def me(request: Request):
    data = owner(request)
    return {"owner": "Álvaro", "csrf": data["csrf"]}


@app.post("/api/logout")
async def logout(request: Request):
    csrf(request, owner(request))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return response


def public_vault():
    v = vault()
    return {"configured": bool(v["openai_key"]), **selection(v),
            "variables": sorted(v["variables"]), "revision": v["revision"]}


@app.get("/api/vault")
async def get_vault(request: Request):
    owner(request)
    return public_vault()


@app.post("/api/vault")
async def update_vault(request: Request):
    csrf(request, owner(request))
    payload = await body(request)
    if busy.locked():
        raise HTTPException(409, "Espera a que termine la respuesta antes de cambiar la configuración.")
    v = vault()
    model = payload.get("model", v["model"])
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        raise HTTPException(400, "Identificador de modelo inválido.")
    key = payload.get("openai_key", "")
    if not isinstance(key, str) or len(key) > 4096 or any(c in key for c in "\r\n\x00"):
        raise HTTPException(400, "Clave del proveedor inválida.")
    variables = payload.get("variables", {})
    remove = payload.get("remove_variables", [])
    if not isinstance(variables, dict) or len(variables) > 64 or not isinstance(remove, list):
        raise HTTPException(400, "Variables inválidas.")
    if any(not safe_name(k) or not isinstance(val, str) or len(val) > 8192 or '\x00' in val for k, val in variables.items()):
        raise HTTPException(400, "El nombre de la variable está reservado o su valor es inválido.")
    if any(not isinstance(k, str) or not safe_name(k) for k in remove):
        raise HTTPException(400, "No se puede eliminar esa variable.")
    if (set(variables) | set(remove)) & managed_variables(v):
        raise HTTPException(409, 'Edita las variables de aplicaciones desde Integraciones.')
    if key:
        v["openai_key"] = key.strip()
    if payload.get("remove_openai") is True:
        v["openai_key"] = ""
    if model != v['model']:
        v['reasoning_effort'] = CATALOG.get(model,((),None,False))[1]
    v["model"] = model
    v["variables"].update(variables)
    for k in remove:
        v["variables"].pop(k, None)
    if len(v["variables"]) > 64:
        raise HTTPException(400, "Se alcanzó el máximo de variables.")
    v["revision"] += 1
    v['runtime_revision'] = v['revision']
    save_vault(v)
    return public_vault()


@app.get("/internal/runtime-config")
async def runtime_config(request: Request):
    service(request)
    v = vault()
    return {"revision": v.get("runtime_revision",v["revision"]), "model": v["model"], "variables": v["variables"],
            "configured": bool(v["openai_key"]), "approved_skills": approved_skills(CONTROL)}


async def available_models():
    v = vault()
    if not v['openai_key']:
        raise HTTPException(409, 'Primero configura tu clave de OpenAI en Secretos.')
    fingerprint=hashlib.sha256(v['openai_key'].encode()).hexdigest()
    now=time.monotonic()
    if model_inventory.get('fingerprint')==fingerprint and now-model_inventory.get('time',0)<300:
        return model_inventory['models']
    try:
        response=await app.state.external.get('https://api.openai.com/v1/models',
            headers={'Authorization':'Bearer '+v['openai_key']})
        if response.status_code!=200:raise ValueError()
        ids={item['id'] for item in response.json()['data'] if isinstance(item,dict) and isinstance(item.get('id'),str)}
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        raise HTTPException(502,'No se pudo consultar los modelos de tu cuenta de OpenAI.')
    models=[{'id':name,'efforts':list(capability[0]),'default_effort':capability[1],'legacy':capability[2]}
            for name,capability in CATALOG.items() if name in ids]
    if hashlib.sha256(vault()['openai_key'].encode()).hexdigest()!=fingerprint:
        raise HTTPException(409,'La clave cambió durante la consulta. Vuelve a abrir el selector.')
    model_inventory.update(fingerprint=fingerprint,time=now,models=models)
    return models


@app.get('/api/models')
async def models(request:Request):
    owner(request)
    available=await available_models()
    return {'models':available,'selected':selection(vault())}


@app.post('/api/model')
async def choose_model(request:Request):
    csrf(request,owner(request))
    payload=await body(request)
    model,effort=payload.get('model'),payload.get('reasoning_effort')
    if not isinstance(model,str) or model not in CATALOG:
        raise HTTPException(400,'Modelo no compatible con este asistente.')
    levels=CATALOG[model][0]
    if not ((effort is None and not levels) or (isinstance(effort,str) and effort in levels)):
        raise HTTPException(400,'Ese esfuerzo no está disponible para el modelo elegido.')
    inventory=await available_models()
    # All awaits precede the busy check and synchronous read/commit: no mid-turn switch.
    if busy.locked():raise HTTPException(409,'Espera a que termine la respuesta para cambiar de modelo.')
    if model not in {item['id'] for item in inventory}:
        raise HTTPException(400,'Ese modelo no está habilitado para tu clave de OpenAI.')
    v=vault()
    chosen={'model':model,'reasoning_effort':effort}
    if selection(v)!=chosen:
        v.setdefault('runtime_revision',v['revision'])
        v.update(chosen)
        v['revision']+=1
        save_vault(v)
    return {'selected':selection(v),'revision':v['revision']}


@app.post("/api/provider-check")
async def provider_check(request: Request):
    csrf(request, owner(request))
    v = vault()
    if not v["openai_key"]:
        raise HTTPException(409, "Primero guarda tu clave de OpenAI en Secretos.")
    try:
        r = await app.state.external.get("https://api.openai.com/v1/models/" + v["model"],
            headers={"Authorization": "Bearer " + v["openai_key"]})
    except httpx.HTTPError:
        raise HTTPException(502, "No se pudo conectar con OpenAI.")
    if r.status_code != 200:
        raise HTTPException(400, "OpenAI rechazó la clave o el modelo. Revisa los permisos y el acceso al modelo.")
    return {"ok": True, "model": v["model"], "note": "Acceso a la clave y al modelo verificado. La generación y el saldo se comprueban por separado."}


def provider_gate():
    now = time.time()
    provider_attempts[:] = [t for t in provider_attempts if now-t < 60]
    if len(provider_attempts) >= 40:
        raise HTTPException(429, "Se alcanzó el límite de solicitudes al proveedor.")
    provider_attempts.append(now)


# Exact route precedes the existing wildcard provider router.
register_images(app, owner, service, body, vault, CONTROL, provider_gate)


@app.api_route("/provider/v1/{path:path}", methods=["GET", "POST"])
async def provider(request: Request, path: str):
    service(request)
    if path not in {"chat/completions", "responses", "models"} and not re.fullmatch(r"models/[A-Za-z0-9._-]+", path):
        raise HTTPException(404, "Operación del proveedor no admitida.")
    v = vault()
    if not v["openai_key"]:
        raise HTTPException(503, "La clave de OpenAI no está configurada.")
    provider_gate()
    payload = None
    if request.method == "POST":
        if path not in {"chat/completions", "responses"}:
            raise HTTPException(405, "Método no permitido.")
        payload = await body(request)
        enforce_selection(payload,path,active_selection or selection(v))
        field = "max_output_tokens" if path == "responses" else "max_completion_tokens"
        requested = payload.pop("max_tokens", payload.get(field, 8192))
        if not isinstance(requested, int) or requested < 1:
            raise HTTPException(400, "Límite de respuesta inválido.")
        payload[field] = min(requested, 8192)
        payload["store"] = False
    elif path not in {"models", "models/" + v["model"]}:
        raise HTTPException(403, "Modelo no permitido.")
    req = app.state.external.build_request(request.method, "https://api.openai.com/v1/" + path,
        json=payload, headers={"Authorization": "Bearer " + v["openai_key"]})
    try:
        response = await app.state.external.send(req, stream=True)
    except httpx.HTTPError:
        raise HTTPException(502, "OpenAI no está disponible.")
    if response.status_code >= 400:
        status = response.status_code
        await response.aclose()
        return JSONResponse({"error": {"message": f"OpenAI rechazó la solicitud ({status}). Revisa Secretos, acceso al modelo y saldo.", "type": "provider_error"}}, status_code=status)
    async def chunks():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
    return StreamingResponse(chunks(), media_type=response.headers.get("content-type", "application/json"))


async def hermes(method, path, **kwargs):
    try:
        response = await app.state.internal.request(method, HERMES + path,
            headers={"Authorization": "Bearer " + token("HERMES_TOKEN")}, **kwargs)
    except httpx.HTTPError:
        raise HTTPException(503, "Hermes está iniciando o no está disponible. Revisa Estado.")
    if response.status_code >= 400:
        raise HTTPException(response.status_code if response.status_code in {400, 404, 409} else 502,
                            "Hermes no pudo completar la operación. Revisa Estado.")
    return response.json()


@app.get("/api/skills")
async def skills(request: Request):
    owner(request)
    return {"skills": await skill_catalog(hermes)}


@app.get("/api/status")
async def status(request: Request):
    owner(request)
    started = time.monotonic()
    try:
        await hermes("GET", "/health")
        runtime = "online"
    except HTTPException:
        runtime = "iniciando o no disponible"
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection("egress", 3128), timeout=2)
        writer.close()
        await writer.wait_closed()
        egress = "online"
    except (OSError, TimeoutError):
        egress = "no disponible"
    return {"portal": "online", "hermes": runtime, **selection(vault()),
        "egress": egress,
        "provider_configured": bool(vault()["openai_key"]), "busy": busy.locked(),
        "probe_ms": round((time.monotonic()-started)*1000), "isolation": "Contenedor sin root; espacio privado; salida de red filtrada",
        "hermes_version": "v0.23.1", "runtime_limit": "1 CPU / 2 GiB / 256 PIDs",
        "workspace": "/workspace", "revision": vault()["revision"]}


def history_page(request, default_limit=40):
    try:
        limit = int(request.query_params.get("limit", default_limit))
        offset = int(request.query_params.get("offset", 0))
    except ValueError:
        raise HTTPException(400, "Paginación inválida.")
    if not 1 <= limit <= 200 or not 0 <= offset <= 1_000_000:
        raise HTTPException(400, "Paginación fuera de los límites permitidos.")
    return {"limit": limit, "offset": offset}


def owner_history(payload):
    rows = []
    for item in payload.get("data", []):
        root = item.get("_lineage_root_id") or item.get("id")
        if isinstance(root, str) and SESSION_ID.fullmatch(root):
            rows.append({**item, "id": root})
    return {**payload, "data": rows}


@app.get("/api/sessions")
async def sessions(request: Request):
    owner(request)
    return owner_history(await hermes("GET", "/api/sessions",
        params={**history_page(request), "source": "api_server"}))


@app.post("/api/sessions")
async def new_session(request: Request):
    csrf(request, owner(request))
    return await hermes("POST", "/api/sessions", json={"id": "owner_" + secrets.token_hex(16), "source": "api_server"})


@app.get("/api/sessions/{sid}/messages")
async def messages(request: Request, sid: str):
    owner(request)
    return await hermes("GET", "/api/sessions/" + session_id(sid) + "/messages",
        params={**history_page(request, 200), "order": "latest"})


@app.delete("/api/sessions/{sid}")
async def delete_session(request: Request, sid: str):
    csrf(request, owner(request))
    sid = session_id(sid)
    payload = await body(request)
    if payload.get("confirmation") != sid:
        raise HTTPException(400, "Confirma la conversación que deseas eliminar.")
    if busy.locked():
        raise HTTPException(409, "Espera a que termine la respuesta antes de eliminar chats.")
    async with busy:
        result = await hermes("DELETE", "/api/sessions/" + sid)
        app.state.quarantine_chat.forget(sid)
        return result


@app.delete("/api/sessions")
async def delete_history(request: Request):
    csrf(request, owner(request))
    payload = await body(request)
    if payload.get("confirmation") != "ELIMINAR TODO":
        raise HTTPException(400, "Confirma la eliminación de todo el historial.")
    if busy.locked():
        raise HTTPException(409, "Espera a que termine la respuesta antes de eliminar chats.")
    async with busy:
        # Snapshot all roots before deleting: offsets must not move during writes.
        ids = set()
        for offset in range(0, 10_000, 200):
            page = await hermes("GET", "/api/sessions",
                params={"limit": 200, "offset": offset, "source": "api_server"})
            ids.update(row["id"] for row in owner_history(page)["data"])
            if not page.get("has_more"):
                break
        else:
            raise HTTPException(409, "El historial es demasiado extenso. Elimina conversaciones individualmente.")
        deleted = 0
        for sid in sorted(ids):
            try:
                await hermes("DELETE", "/api/sessions/" + sid)
                app.state.quarantine_chat.forget(sid)
                deleted += 1
            except HTTPException as exc:
                if exc.status_code == 404:
                    continue
                raise HTTPException(exc.status_code,
                    f"Se eliminaron {deleted} conversaciones. La operación se interrumpió; recarga el historial antes de reintentar.")
        return {"deleted": True, "deleted_count": deleted}


@app.post("/api/chat")
async def chat(request: Request):
    global active_selection
    csrf(request, owner(request))
    payload = await body(request)
    sid = session_id(payload.get("session_id"))
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 20000:
        raise HTTPException(400, "El mensaje debe tener entre 1 y 20.000 caracteres.")
    quarantine_reply = await app.state.quarantine_chat.handle(sid, message, payload.get('attachments', []))
    if quarantine_reply is not None:
        return StreamingResponse(quarantine_events(quarantine_reply), media_type='text/event-stream',
                                 headers={'X-Accel-Buffering':'no'})
    message = await requested_skills(message, hermes)
    message = attachment_message(message, payload.get('attachments', []))
    if not vault()["openai_key"]:
        raise HTTPException(409, "Guarda tu clave de OpenAI en Secretos para activar el agente.")
    if busy.locked():
        raise HTTPException(409, "Ya hay una respuesta en curso.")
    await busy.acquire()
    active_selection=selection(vault())
    try:
        req = app.state.internal.build_request("POST", HERMES + "/api/sessions/" + sid + "/chat/stream",
            json={"input": message, "model":active_selection['model'], "require_model_lock":True,
                  "model_options":{"reasoning":{"effort":active_selection['reasoning_effort'] or 'none'}}},
            headers={"Authorization": "Bearer " + token("HERMES_TOKEN")})
        response = await app.state.internal.send(req, stream=True)
        if response.status_code >= 400:
            await response.aclose()
            raise HTTPException(502, "Hermes no aceptó el mensaje.")
    except BaseException as exc:
        active_selection=None
        busy.release()
        if isinstance(exc, httpx.TimeoutException):
            raise HTTPException(504, {"code": "timeout"}) from None
        if isinstance(exc, httpx.RequestError):
            raise HTTPException(502, {"code": "unavailable"}) from None
        raise
    async def stream():
        global active_selection
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        except httpx.RequestError as exc:
            # Never expose transport exception strings, URLs, or upstream credentials.
            code = "timeout" if isinstance(exc, httpx.TimeoutException) else "disconnected"
            yield b"\n\nevent: error\ndata: " + json.dumps({"code": code}).encode() + b"\n\n"
        finally:
            try:
                await response.aclose()
            finally:
                active_selection=None
                busy.release()
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.post("/api/stop")
async def stop(request: Request):
    csrf(request, owner(request))
    payload = await body(request)
    run = payload.get("run_id", "")
    if not isinstance(run, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run):
        raise HTTPException(400, "Identificador de ejecución inválido.")
    return await hermes("POST", "/v1/runs/" + run + "/stop", json={})


register_media(app, owner, csrf, vault, ORIGIN)
register_quarantine(app, owner, csrf, CONTROL)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
@app.get("/secrets")
@app.get("/status")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get('/settings')
async def settings_page():
    return FileResponse(STATIC / 'settings.html')

register_settings(app, owner, csrf, body, lambda: HERMES, lambda: token("HERMES_TOKEN"))
register_integrations(app, owner, csrf, body, vault, save_vault, lambda: busy.locked())
register_customization(app, owner, csrf, body, lambda: HERMES, lambda: token('HERMES_TOKEN'))
register_specialist(app, owner, csrf, body, lambda: HERMES, lambda: token('HERMES_TOKEN'))


@app.get('/projects')
@app.get('/project')
async def projects_page():
    # Empty public shell; all records require owner or project-scoped auth.
    return FileResponse(STATIC / 'projects.html')

register_projects(app, control=CONTROL, owner=owner, csrf=csrf, token=token)
