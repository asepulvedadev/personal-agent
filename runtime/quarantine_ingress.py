"""Trusted, image-owned Telegram intake. Candidate bytes are data, never code.

The build patch calls install_telegram_guard before core handler registration.
Only the adapter glue imports PTB; the core is dependency-injectable for offline tests.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_UPLOAD_BYTES = 5 * 1024**2
MAX_RESPONSE_BYTES = 65536
GUARD_GROUP = -10000
GUARD_VERSION = "personal-agent-quarantine-v1"
_GITHUB = re.compile(r"https?://(?:www\.)?github\.com/[^\s<>`\"']+", re.I)
_URL = re.compile(r"https?://[^\s<>`\"']+", re.I)
_EXPLICIT = re.compile(r'(?<!\S)\$cuarentena(?![A-Za-z0-9_./-])', re.I)
_COMMAND = re.compile(r'\$cuarentena(?:\s+([^\r\n]*))?\Z', re.I)
_COMMAND_DETAIL = re.compile(r'(?:informe\s+)?([a-f0-9]{32})(?:\s+([1-9][0-9]{0,2}|1000))?\Z', re.I)
HELP = ('Revisión compartida · Hermes Web y Carpediev\n'
        'Usa $cuarentena https://github.com/propietario/repositorio o escribe $cuarentena y adjunta el archivo original de la skill o herramienta.\n'
        'Envía un solo enlace o los archivos, sin mezclarlos. Solo se admiten enlaces HTTPS de GitHub y formatos de código o ZIP compatibles.\n'
        '$cuarentena lista muestra las revisiones; $cuarentena ID muestra el informe; $cuarentena informe ID 2 abre otra página.\n'
        'La revisión analiza el contenido como datos: no lo instala ni ejecuta.')
REJECT = ('Revisión compartida · Hermes Web y Carpediev\n'
          'Solicitud de cuarentena ambigua o no compatible. No se ha descargado, instalado ni ejecutado contenido.\n'
          'Usa $cuarentena con un único enlace HTTPS de GitHub o adjunta el archivo original sin enlaces. '
          'También puedes usar $cuarentena lista o $cuarentena informe ID [página de 1 a 1000].')
_INTAKE = re.compile(r"\b(?:skills?|capabilit\w*|habilidad\w*|cuarentena|quarantine|import\w*|instal\w*|incorpora\w*|estudia\w*|aprende\w*)\b", re.I)
_TEXT_FILE_EXTENSIONS = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".py", ".sh", ".bash", ".js", ".ts", ".mjs", ".cjs", ".ps1", ".rb", ".go", ".rs", ".html", ".css", ".xml"}
_UPLOAD_EXTENSIONS = _TEXT_FILE_EXTENSIONS | {".zip", ".skill"}
_ORDINARY_DOCUMENTS = {".pdf", ".docx", ".txt", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".m4a", ".mov"}


class IntakeError(ValueError):
    """An intake operation cannot safely continue; never forward its raw details."""


def _numeric_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class GuardConfig:
    owner_ids: frozenset[int]
    bot_id: int
    target_profile: str = "carpediev"
    review_url: str = ""

    def validate(self):
        if not self.owner_ids or not all(_numeric_id(v) for v in self.owner_ids) or not _numeric_id(self.bot_id):
            raise IntakeError("Explicit numeric owner and receiving bot identities are required")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.target_profile):
            raise IntakeError("Invalid target profile")
        if self.review_url:
            value = urlsplit(self.review_url)
            if value.scheme != "https" or not value.hostname or value.username or value.password or value.query or value.fragment:
                raise IntakeError("Review URL must be an operator-configured HTTPS URL")
        return self

    @classmethod
    def from_env(cls):
        raw = os.environ.get("QUARANTINE_OWNER_IDS", os.environ.get("TELEGRAM_ALLOWED_USERS", ""))
        owners = raw.split(",")
        bot = os.environ.get("QUARANTINE_BOT_ID", "")
        if not bot:
            bot = os.environ.get("TELEGRAM_BOT_TOKEN", "").partition(":")[0]
        if not owners or any(not re.fullmatch(r"[1-9][0-9]*", v.strip()) for v in owners) or not re.fullmatch(r"[1-9][0-9]*", bot):
            raise IntakeError("Missing explicit quarantine identities")
        return cls(frozenset(int(v.strip()) for v in owners), int(bot),
                   os.environ.get("QUARANTINE_TARGET_PROFILE", "carpediev"),
                   os.environ.get("QUARANTINE_REVIEW_URL", "")).validate()


@dataclass(frozen=True)
class Candidate:
    kind: str
    url: str = ""
    name: str = ""
    page: int = 1


def _message_text(message):
    return str(getattr(message, "text", None) or getattr(message, "caption", None) or "")


def candidate_for_message(message):
    """Classify narrowly, without downloads, caches, filesystem access or execution."""
    if message is None:
        return None
    text = _message_text(message)
    document = getattr(message, "document", None)
    urls = _URL.findall(text)
    for entity in [*(getattr(message, "entities", None) or []), *(getattr(message, "caption_entities", None) or [])]:
        value = getattr(entity, "url", None)
        if isinstance(value, str) and _URL.fullmatch(value):
            urls.append(value)
    urls = list(dict.fromkeys(v.rstrip(".,;!?") for v in urls))
    explicit = _EXPLICIT.search(text)
    if explicit:
        command = _COMMAND.fullmatch(text.strip())
        argument = command[1].strip() if command and command[1] else ''
        if not command:
            return Candidate('reject')
        if document is not None:
            if argument or urls:
                return Candidate('reject')
            name = str(getattr(document, 'file_name', '') or '')
            return Candidate('upload' if Path(name).suffix.lower() in _UPLOAD_EXTENSIONS else 'reject', name=name)
        if not argument and not urls:
            return Candidate('help')
        if argument.casefold() == 'lista' and not urls:
            return Candidate('review', url='list')
        detail = _COMMAND_DETAIL.fullmatch(argument)
        if detail and not urls:
            return Candidate('review', url=detail[1].lower(), page=int(detail[2] or 1))
        if len(urls) == 1 and argument == urls[0]:
            try:
                return Candidate('github', url=_safe_github_url(argument))
            except IntakeError:
                pass
        return Candidate('reject')
    if document is not None:
        name = str(getattr(document, "file_name", "") or "")
        extension = Path(name).suffix.lower()
        if extension in _ORDINARY_DOCUMENTS and not _INTAKE.search(text):
            return None
        if urls:
            return Candidate('reject')
        return Candidate("upload" if extension in _UPLOAD_EXTENSIONS else "reject", name=name)
    query = re.fullmatch(r'(?:/?(?:ver|revisar)\s+)?/?skill\s+([a-f0-9]{32})[.!]?', text.strip(), re.I)
    if query:
        return Candidate('review', url=query[1])
    if re.fullmatch(r'/?(?:cuarentena|quarantine)(?:\s+(?:de\s+)?skills?)?[.!]?', text.strip(), re.I):
        return Candidate('review', url='list')
    links = _GITHUB.findall(text)
    for entity in [*(getattr(message, "entities", None) or []), *(getattr(message, "caption_entities", None) or [])]:
        value = getattr(entity, "url", None)
        if isinstance(value, str) and _GITHUB.fullmatch(value):
            links.append(value)
    links = list(dict.fromkeys(v.rstrip(".,;!?") for v in links))
    if not links:
        return None
    # A bare link is an intake request. URLs embedded in ordinary code discussion are not.
    bare = len(links) == 1 and text.strip().strip("<>").rstrip(".,;!?") == links[0]
    if not bare and not _INTAKE.search(_GITHUB.sub("", text)):
        return None
    if len(links) != 1:
        return Candidate("reject")
    return Candidate("github", url=links[0])


def _safe_name(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 120 or name in {".", ".."} or any(c in name for c in "/\\\x00") or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise IntakeError("Invalid display filename")
    if Path(name).suffix.lower() not in _UPLOAD_EXTENSIONS:
        raise IntakeError("Unsupported bundle format")
    return name


def _safe_github_url(value):
    parsed = urlsplit(value)
    if len(value) > 2048 or parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.netloc != "github.com" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise IntakeError("Only canonical HTTPS GitHub source URLs are accepted")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or any(not p or p in {".", ".."} for p in parts) or any(c in value for c in "\r\n\\"):
        raise IntakeError("Invalid GitHub repository URL")
    return value


@dataclass(frozen=True)
class BrokerConfig:
    origin: str
    token: str

    def validate(self):
        try:
            value = urlsplit(self.origin)
            value.port
        except ValueError as exc:
            raise IntakeError("Invalid configured broker origin") from exc
        if value.scheme not in {"http", "https"} or not value.hostname or value.username or value.password or value.query or value.fragment or value.path not in {"", "/"}:
            raise IntakeError("Broker must be a fixed operator-configured HTTP origin")
        if not isinstance(self.token, str) or not 24 <= len(self.token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in self.token):
            raise IntakeError("Invalid narrow broker credential")
        return self

    @classmethod
    def from_env(cls):
        token = os.environ.get("QUARANTINE_INGEST_TOKEN", "")
        if not token:
            path = os.environ.get("QUARANTINE_INGEST_TOKEN_FILE", "")
            if not path or not Path(path).is_absolute():
                raise IntakeError("Narrow broker credential is required")
            with open(path, "r", encoding="utf-8") as handle:
                token = handle.read(4097).strip()
        return cls(os.environ.get("QUARANTINE_BROKER_URL", "http://web:8080"), token).validate()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise IntakeError("Broker redirects are forbidden")


class BrokerClient:
    def __init__(self, config, opener=None):
        self.config = config.validate()
        self.opener = opener or build_opener(ProxyHandler({}), _NoRedirect())

    def _post(self, path, data, content_type, method="POST"):
        if not path.startswith("/internal/quarantine/"):
            raise IntakeError("Invalid broker operation")
        request = Request(self.config.origin.rstrip("/") + path, data=data, method=method,
                          headers={"Authorization": "Bearer " + self.config.token,
                                   "Content-Type": content_type, "Accept": "application/json"})
        with self.opener.open(request, timeout=20) as response:
            if getattr(response, "status", 200) != 200 and getattr(response, "status", 200) != 201:
                raise IntakeError("Broker rejected intake")
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise IntakeError("Broker metadata exceeds limit")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise IntakeError("Invalid broker metadata")
        return result

    async def review(self, item_id, page=1):
        if item_id != 'list' and not re.fullmatch(r'[a-f0-9]{32}', item_id):
            raise IntakeError('Invalid review reference')
        if type(page) is not int or not 1 <= page <= 1000 or (item_id == 'list' and page != 1):
            raise IntakeError('Invalid review page')
        path = '/internal/quarantine/' + item_id + ('?page=' + str(page) if page > 1 else '')
        return await asyncio.wait_for(asyncio.to_thread(self._post, path, None, 'application/json', 'GET'), timeout=25)

    async def ingest_github(self, url, profile):
        data = json.dumps({"url": _safe_github_url(url), "profile": profile}).encode()
        return await asyncio.wait_for(asyncio.to_thread(self._post, "/internal/quarantine/github", data, "application/json"), timeout=25)

    async def ingest_upload(self, name, data, profile):
        _safe_name(name)
        if not 0 < len(data) <= MAX_UPLOAD_BYTES:
            raise IntakeError("Upload exceeds limit")
        path = "/internal/quarantine/upload?" + urlencode({"name": name, "profile": profile})
        return await asyncio.wait_for(asyncio.to_thread(self._post, path, data, "application/octet-stream"), timeout=25)


class _BoundedBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_UPLOAD_BYTES:
            raise IntakeError("Actual Telegram download exceeds limit")
        return super().write(data)


def _shared_report(result):
    report = result.get('report')
    if not isinstance(report, dict) or set(report) != {'version','scope','text'} or report.get('version') != 1 or report.get('scope') != 'shared':
        raise IntakeError('Invalid shared assessment')
    text = report.get('text')
    if not isinstance(text,str) or not 1 <= len(text) <= 3600 or not text.startswith('Revisión compartida · Hermes Web y Carpediev\n') or any(ord(c)<32 and c!='\n' for c in text):
        raise IntakeError('Invalid shared assessment text')
    return text


def receipt_text(result, config):
    if result.get('kind') in {'quarantine-list', 'quarantine-error'} and set(result) == {'kind','report'}:
        return _shared_report(result)
    item_id = result.get("id")
    if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,96}", item_id):
        raise IntakeError("Invalid quarantine receipt")
    state = result.get("state", "quarantined")
    state_labels = {"approved": "aprobado; entrega no comprobada por esta revisión", "quarantined": "en cuarentena", "rejected": "rechazado", "blocked": "bloqueado",
                    "review_pending": "pendiente de revisión", "ready_for_review": "listo para revisión",
                    "pending": "pendiente", "scanned": "analizado", "needs_review": "requiere revisión",
                    "review_required": "requiere revisión"}
    if state not in state_labels:
        raise IntakeError("Invalid quarantine state")
    findings = result.get("findings", [])
    count = len(findings) if isinstance(findings, (list, dict)) else 0
    if 'report' in result:
        return _shared_report(result)
    # Legacy summaries retain the narrow opaque-receipt fallback.
    reply = f"Recibido en cuarentena. Referencia: {item_id}. Estado: {state_labels[state]}. Hallazgos registrados: {count}. No se ha instalado ni ejecutado."
    if config.review_url:
        reply += " Revisa el informe en " + config.review_url
    return reply


class QuarantineIngress:
    def __init__(self, config, broker, stop_exception, application):
        self.config = config.validate()
        self.broker = broker
        self.stop_exception = stop_exception
        self.application = application

    def _authorized(self, message, context):
        sender, chat = getattr(message, "from_user", None), getattr(message, "chat", None)
        try:
            return (context.application is self.application and context.bot is self.application.bot
                    and context.bot.id == self.config.bot_id
                    and sender is not None and sender.id in self.config.owner_ids
                    and not getattr(sender, "is_bot", True)
                    and chat is not None and chat.type == "private" and chat.id == sender.id)
        except (AttributeError, RuntimeError):
            return False

    async def handle(self, update, context):
        message = getattr(update, "effective_message", None)
        candidate = candidate_for_message(message)
        if candidate is None:
            return
        # Every candidate outcome, including failures, stops core/observer processing.
        try:
            if not self._authorized(message, context):
                return
            try:
                if candidate.kind in {'help', 'reject'}:
                    result = {'kind':'quarantine-list', 'report':{
                        'version':1, 'scope':'shared', 'text':HELP if candidate.kind == 'help' else REJECT}}
                elif candidate.kind == "review":
                    result = (await self.broker.review(candidate.url, page=candidate.page)
                              if candidate.page > 1 else await self.broker.review(candidate.url))
                elif candidate.kind == "github":
                    result = await self.broker.ingest_github(_safe_github_url(candidate.url), self.config.target_profile)
                elif candidate.kind == "upload":
                    name = _safe_name(candidate.name)
                    doc = message.document
                    size = getattr(doc, "file_size", None)
                    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_UPLOAD_BYTES:
                        raise IntakeError("Unverifiable or oversized attachment")
                    file = await asyncio.wait_for(doc.get_file(), timeout=10)
                    with _BoundedBuffer() as output:
                        await asyncio.wait_for(file.download_to_memory(output), timeout=20)
                        data = output.getvalue()
                    if not 0 < len(data) <= MAX_UPLOAD_BYTES:
                        raise IntakeError("Empty or oversized attachment")
                    result = await self.broker.ingest_upload(name, data, self.config.target_profile)
                else:
                    raise IntakeError("Unsupported or ambiguous import candidate")
                reply = receipt_text(result, self.config)
            except Exception:
                reply = "No pude confirmar el registro seguro de ese material. No se ha enviado al asistente ni instalado. Escribe «cuarentena» para comprobar el registro antes de volver a enviarlo."
            try:
                await asyncio.wait_for(message.reply_text(reply, disable_web_page_preview=True, parse_mode=None), timeout=10)
            except Exception:
                pass  # Reply transport failure never reopens the ordinary agent path.
        finally:
            raise self.stop_exception


def install_telegram_guard(app, adapter, *, config=None, broker=None, sdk=None):
    """Install before core registration. Any setup failure propagates to connect()."""
    config = (config or GuardConfig.from_env()).validate()
    if sdk is None:
        from telegram.ext import ApplicationHandlerStop, MessageHandler, filters
        sdk = SimpleNamespace(Stop=ApplicationHandlerStop, MessageHandler=MessageHandler, MessageFilter=filters.MessageFilter)
    broker = broker or BrokerClient(BrokerConfig.from_env())
    table = getattr(app, "handlers", None)
    if not isinstance(table, dict):
        raise IntakeError("Unexpected Telegram handler registry")
    existing = [(group, handler) for group, handlers in table.items() for handler in handlers
                if getattr(handler, "_quarantine_guard_version", None)]
    if existing:
        if len(existing) != 1 or existing[0][0] != GUARD_GROUP or existing[0][1]._quarantine_guard_version != GUARD_VERSION or existing[0][1]._quarantine_guard_adapter is not adapter:
            raise IntakeError("Conflicting quarantine handler")
        if min(table) != GUARD_GROUP:
            raise IntakeError("A handler precedes quarantine intake")
        return
    if any(group <= GUARD_GROUP for group in table):
        raise IntakeError("Unexpected pre-ingress Telegram handler")

    class CandidateFilter(sdk.MessageFilter):
        def filter(self, message):
            return candidate_for_message(message) is not None

    class GuardHandler(sdk.MessageHandler):
        __slots__ = ("_quarantine_guard_version", "_quarantine_guard_adapter")

    ingress = QuarantineIngress(config, broker, sdk.Stop, app)
    handler = GuardHandler(CandidateFilter(), ingress.handle, block=True)
    handler._quarantine_guard_version = GUARD_VERSION
    handler._quarantine_guard_adapter = adapter
    app.add_handler(handler, group=GUARD_GROUP)
    if min(app.handlers) != GUARD_GROUP or app.handlers[GUARD_GROUP] != [handler]:
        raise IntakeError("Quarantine handler registration order is unsafe")


def assert_telegram_guard_order(app, adapter):
    """Late native plugin registration cannot move a callback ahead of intake."""
    table = getattr(app, "handlers", {})
    guards = [(group, handler) for group, handlers in table.items() for handler in handlers
              if getattr(handler, "_quarantine_guard_version", None)]
    if len(guards) != 1 or guards[0][0] != GUARD_GROUP or min(table) != GUARD_GROUP:
        raise IntakeError("Late Telegram registration displaced quarantine intake")
    handler = guards[0][1]
    if handler._quarantine_guard_version != GUARD_VERSION or handler._quarantine_guard_adapter is not adapter or table[GUARD_GROUP] != [handler]:
        raise IntakeError("Late Telegram registration changed the trusted handler")
