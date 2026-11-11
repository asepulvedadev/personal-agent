"""Owner-approved, durable skill quarantine. Imported bytes never execute or autoload."""
import asyncio
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import time
from urllib.parse import quote, urlsplit
import zipfile
import zlib

import httpx
from fastapi import HTTPException, Request
from app.media import file_name, read_upload
from app.quarantine_worker import inspect_files
from app.quarantine_assessment import report, import_failure

SOURCE_BYTES = 5 * 1024**2
MEMBER_BYTES = 512 * 1024
EXPANDED_BYTES = 8 * 1024**2
MAX_MEMBERS = 128
MAX_ITEMS = 100
MAX_STORAGE = 100 * 1024**2
# Leave time for analysis and acknowledgement within Telegram's 20-second request.
GITHUB_TIMEOUT = 15
PROFILES = {'default', 'carpediev'}
ID = re.compile(r'[a-f0-9]{32}\Z')
NOTICE = 'El análisis estático no demuestra que el contenido sea inofensivo. No se ejecutó ni instaló.'
PLAN = ['Revisar los hallazgos como datos no confiables.',
        'Identificar la capacidad útil sin copiar instrucciones de seguridad ni configuración.',
        'Reimplementar recursos o código en un cambio separado con pruebas y aprobación explícita.']


def enforce_chat_intake(message, attachments):
    """Enforce the dedicated import boundary before any capable-agent interaction.

    This recognizes explicit imports, not every possible malicious paraphrase.
    Ordinary repository discussion and ordinary document analysis remain available.
    """
    github_pattern = r"https?://(?:www\.)?github\.com/[^\s<>`\"']+"
    links = re.findall(github_pattern, message, re.I)
    intent_text = re.sub(github_pattern, "", message, flags=re.I)
    intent = re.search(r"\b(?:skills?|capabilit\w*|habilidad\w*|cuarentena|quarantine|import\w*|instal\w*|incorpora\w*|estudia\w*|aprende\w*)\b", intent_text, re.I)
    bare = len(links) == 1 and message.strip().strip('<>').rstrip('.,;!?') == links[0].rstrip('.,;!?')
    candidate = bool(links and (bare or intent))
    if isinstance(attachments, list):
        for document in attachments:
            if not isinstance(document, dict):
                continue
            name, text = document.get('name'), document.get('text')
            if not isinstance(name, str):
                continue
            basename = name.replace('\\', '/').rsplit('/', 1)[-1].lower()
            extension = PurePosixPath(basename).suffix
            candidate |= (basename == 'skill.md' or extension in {
                '.zip', '.skill', '.py', '.sh', '.bash', '.js', '.ts', '.ps1',
                '.yaml', '.yml', '.toml', '.json', '.rb', '.go', '.rs'})
            candidate |= bool(intent and re.search(r'\bskills?\b', message, re.I))
            candidate |= bool(isinstance(text, str) and re.match(
                r'\A---\r?\n(?=[\s\S]{0,2000}?^name:)(?=[\s\S]{0,2000}?^description:)', text, re.M))
    if candidate:
        raise HTTPException(409, 'Importa este enlace o archivo mediante Cuarentena de skills antes de enviarlo al agente.')


class Rejected(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def safe_path(value):
    if (not value or len(value) > 240 or '\\' in value or ':' in value or
            any(ord(char) < 32 or ord(char) > 126 for char in value) or value.startswith('/')):
        raise Rejected('unsafe_archive_path')
    parts = value.rstrip('/').split('/')
    if len(parts) > 8:
        raise Rejected('archive_depth_limit')
    for part in parts:
        if (not part or part in {'.', '..'} or part.endswith(('.', ' ')) or
                re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)):
            raise Rejected('unsafe_archive_path')
    return '/'.join(parts)


def unpack(data, extension, name, subtree=None):
    if extension != '.zip':
        return {name: data}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise Rejected('archive_member_limit')
            total = 0
            seen = set()
            validated = []
            file_paths = set()
            for info in members:
                if info.orig_filename != info.filename:
                    raise Rejected('ambiguous_archive_filename')
                path = safe_path(info.filename)
                folded = path.casefold()
                if folded in seen:
                    raise Rejected('duplicate_archive_path')
                seen.add(folded)
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or info.flag_bits & 1:
                    raise Rejected('special_or_encrypted_member')
                if info.is_dir():
                    if kind == stat.S_IFREG:
                        raise Rejected('invalid_directory_member')
                    continue
                if kind == stat.S_IFDIR:
                    raise Rejected('invalid_file_member')
                if PurePosixPath(path).suffix.lower() in {'.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar'}:
                    raise Rejected('nested_archive')
                file_paths.add(folded)
                total += info.file_size
                if (info.file_size > MEMBER_BYTES or total > EXPANDED_BYTES or
                        info.file_size > max(info.compress_size, 1) * 100):
                    raise Rejected('archive_expansion_limit')
                validated.append((info, path))
            for path in seen:
                components = path.split('/')
                if any('/'.join(components[:index]) in file_paths for index in range(1,len(components))):
                    raise Rejected('archive_file_directory_collision')
            # All metadata must pass before a single member is materialized.
            result = {}
            root = None
            if subtree is not None:
                roots = {path.split('/')[0] for _, path in validated}
                if len(roots) != 1:
                    raise Rejected('invalid_github_archive')
                root = next(iter(roots)) + '/'
            for info, path in validated:
                logical = path
                if root:
                    logical = path[len(root):]
                    if subtree and logical != subtree and not logical.startswith(subtree.rstrip('/') + '/'):
                        continue
                    if subtree and logical.startswith(subtree.rstrip('/') + '/'):
                        logical = logical[len(subtree.rstrip('/')) + 1:]
                with archive.open(info) as member:
                    raw = member.read(MEMBER_BYTES + 1)
                if len(raw) != info.file_size or len(raw) > MEMBER_BYTES:
                    raise Rejected('archive_size_mismatch')
                result[logical] = raw
            if not result:
                raise Rejected('empty_package')
            return result
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError, zlib.error) as exc:
        raise Rejected('invalid_archive') from exc


def analyze(data, name, provenance):
    extension = Path(name).suffix.lower()
    try:
        files = unpack(data, extension, name, provenance.get('path') if provenance['kind'] == 'github' else None)
        findings, candidates, metadata = inspect_files(files)
        manifest = [{'path':path, 'bytes':len(raw), 'sha256':digest(raw)} for path,raw in sorted(files.items())]
    except Rejected as exc:
        files, candidates, metadata, manifest = {}, [], {}, []
        findings = [{'code':str(exc), 'path':'', 'severity':'blocked'}]
    state = 'blocked' if any(f['severity'] == 'blocked' for f in findings) else 'review_required' if findings else 'quarantined'
    return {'sha256':digest(data), 'provenance':provenance, 'manifest':manifest,
            'findings':findings, 'candidates':candidates, 'metadata':metadata,
            'state':state, 'eligible':not findings, 'notice':NOTICE, 'plan':PLAN if findings else []}, files


class Quarantine:
    def __init__(self, control):
        self.root = Path(control) / 'quarantine'

    def create(self, data, name, provenance):
        if not data or len(data) > SOURCE_BYTES:
            raise HTTPException(413, 'El archivo supera el límite o está vacío.')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        entries = list(self.root.iterdir())
        used = sum(p.stat().st_size for entry in entries if entry.is_dir() for p in entry.iterdir() if p.is_file())
        if len(entries) >= MAX_ITEMS or used + len(data) * 2 + 65536 > MAX_STORAGE:
            raise HTTPException(409, 'La cuarentena está llena. Requiere revisión del propietario; no se eliminó evidencia.')
        item, _ = analyze(data, name, provenance)
        item.update(id=secrets.token_hex(16), name=name, created_at=int(time.time()))
        folder = self.root / item['id']
        folder.mkdir(mode=0o700)
        write_new(folder / 'source.bin', data)
        write_new(folder / 'record.json', json.dumps(item, ensure_ascii=False).encode())
        return item

    def folder(self, item_id):
        if not isinstance(item_id,str) or not ID.fullmatch(item_id):
            raise HTTPException(404, 'Importación no encontrada.')
        return self.root / item_id

    def read(self, item_id, previews=True):
        folder = self.folder(item_id)
        try:
            item = json.loads((folder / 'record.json').read_text())
            data = (folder / 'source.bin').read_bytes()
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, 'Importación no encontrada.')
        if len(data) > SOURCE_BYTES or digest(data) != item['sha256']:
            return {**item, 'state':'blocked', 'eligible':False, 'findings':item['findings'] +
                    [{'code':'source_hash_changed', 'path':'', 'severity':'blocked'}]}
        current, files = analyze(data, item['name'], item['provenance'])
        item.update({key:current[key] for key in ('findings','candidates','metadata','state','eligible','plan')})
        if (folder / 'approved.json').exists():
            approved = valid_approval(folder, item, files)
            item['state'] = 'approved' if approved else 'blocked'
            item['eligible'] = False
            if not approved:
                item['findings'].append({'code':'approval_integrity_failed','path':'','severity':'blocked'})
        if previews:
            item['previews'] = [{'path':path, 'text':raw[:4096].decode('utf-8', errors='replace'), 'untrusted':True}
                                for path,raw in list(sorted(files.items()))[:8]]
        return item

    def approve(self, item_id, expected_sha256, profile, skill_path):
        if not isinstance(profile,str) or profile not in PROFILES:
            raise HTTPException(400, 'Perfil no permitido.')
        item = self.read(item_id, previews=False)
        if item['provenance'].get('profile') not in {None, profile}:
            raise HTTPException(409, 'El perfil no coincide con el origen de la importación.')
        if not isinstance(skill_path,str):
            raise HTTPException(400, 'Ruta de skill inválida.')
        if not item['eligible'] or not isinstance(expected_sha256,str) or expected_sha256 != item['sha256'] or skill_path not in item['metadata']:
            raise HTTPException(409, 'No se puede aprobar: contenido cambiado, ya aprobado o revisión adicional necesaria.')
        folder = self.folder(item_id)
        data = (folder / 'source.bin').read_bytes()
        if digest(data) != expected_sha256:
            raise HTTPException(409, 'El contenido cambió. Vuelve a revisar la importación.')
        current, files = analyze(data, item['name'], item['provenance'])
        if not current['eligible']:
            raise HTTPException(409, 'Se requiere revisión adicional.')
        name = item['metadata'][skill_path]['name']
        # Approved names are reserved even if old evidence later becomes invalid.
        # Replacement/revocation requires a separate owner-reviewed workflow.
        for existing in self.root.glob('*/approved.json'):
            try:
                prior = json.loads(existing.read_text())
            except (OSError, ValueError):
                raise HTTPException(409, 'Una aprobación existente requiere revisión de integridad.')
            if prior.get('profile') == profile and prior.get('name') == name:
                raise HTTPException(409, 'Ya existe una skill aprobada con este nombre y perfil. No se reemplazó.')
        content = files[skill_path]
        record = {'id':item_id, 'profile':profile, 'name':item['metadata'][skill_path]['name'],
                  'content':content.decode('utf-8'), 'sha256':digest(content), 'source_sha256':expected_sha256,
                  'skill_path':skill_path, 'approved_at':int(time.time())}
        try:
            write_new(folder / 'approved.json', json.dumps(record, ensure_ascii=False).encode())
        except FileExistsError:
            raise HTTPException(409, 'Esta importación ya tiene una aprobación inmutable.')
        return self.read(item_id)


def valid_approval(folder, item, files):
    try:
        record = json.loads((folder / 'approved.json').read_text())
        path = record['skill_path']
        raw = files[path]
        if (item['eligible'] and record['id'] == folder.name and record['profile'] in PROFILES and
                item['provenance'].get('profile') in {None, record['profile']} and
                record['source_sha256'] == item['sha256'] and record['sha256'] == digest(raw) and
                record['content'].encode() == raw and record['name'] == item['metadata'][path]['name']):
            return record
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def approved_skills(control_dir, profile=None):
    """Pure read for the trusted supervisor; changed evidence invalidates approval."""
    if profile is not None and (not isinstance(profile,str) or profile not in PROFILES):
        return []
    store = Quarantine(control_dir)
    if not store.root.exists():
        return []
    records = []
    for folder in sorted(store.root.iterdir()):
        if not ID.fullmatch(folder.name) or not (folder / 'approved.json').is_file():
            continue
        try:
            item = json.loads((folder / 'record.json').read_text())
            data = (folder / 'source.bin').read_bytes()
            if len(data) > SOURCE_BYTES or digest(data) != item['sha256']:
                continue
            current, files = analyze(data, item['name'], item['provenance'])
            record = valid_approval(folder, {**item, **current}, files)
            if record and (profile is None or record['profile'] == profile):
                records.append(record)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return records


def github_source(url):
    if not isinstance(url,str) or len(url) > 600 or any(ord(c) <= 32 or ord(c) > 126 for c in url):
        raise HTTPException(400, 'Usa un enlace público de GitHub sin credenciales.')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment or
            '%' in parsed.path or '\\' in url):
        raise HTTPException(400, 'Solo se aceptan enlaces HTTPS públicos de github.com sin parámetros.')
    parts = parsed.path.strip('/').split('/')
    if len(parts) < 2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', p) and p not in {'.','..'} for p in parts):
        raise HTTPException(400, 'Ruta de GitHub inválida.')
    owner, repo = parts[:2]
    repo = repo.removesuffix('.git')
    if not repo or repo in {'.','..'}:
        raise HTTPException(400, 'Repositorio inválido.')
    ref, path = None, ''
    if len(parts) > 2:
        if len(parts) < 4 or parts[2] not in {'tree','blob'}:
            raise HTTPException(400, 'Usa un repositorio o una ruta tree/blob con referencia explícita.')
        ref = parts[3]
        path = '/'.join(parts[4:])
        if parts[2] == 'blob' and (not path or PurePosixPath(path).suffix.lower() not in {'.md','.txt'}):
            raise HTTPException(400, 'El enlace a archivo debe ser Markdown o texto.')
    return owner, repo, ref, path


async def fetch_public(url, limit):
    """Fixed official hosts via operator-configured egress; no ambient routing or credentials."""
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in {'api.github.com','codeload.github.com'} or parsed.username or parsed.password:
        raise Rejected('invalid_fetch_host')
    async with httpx.AsyncClient(proxy=os.getenv('HTTPS_PROXY'), trust_env=False,
                                follow_redirects=False, timeout=20) as client:
        async with client.stream('GET', url, headers={'Accept':'application/vnd.github+json','User-Agent':'personal-agent-quarantine'}) as response:
            if response.status_code != 200:
                raise Rejected('github_public_source_unavailable')
            if int(response.headers.get('content-length','0')) > limit:
                raise Rejected('source_size_limit')
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise Rejected('source_size_limit')
            return bytes(data)


async def github_package(url):
    owner, repo, ref, path = github_source(url)
    prefix = 'https://api.github.com/repos/'+owner+'/'+repo
    try:
        async with asyncio.timeout(GITHUB_TIMEOUT):
            metadata = json.loads(await fetch_public(prefix, 65536))
            if metadata.get('private') is not False:
                raise Rejected('public_repository_required')
            ref = ref or metadata['default_branch']
            if not isinstance(ref,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', ref):
                raise Rejected('unsupported_reference')
            commit = json.loads(await fetch_public(prefix+'/commits/'+quote(ref,safe=''), 262144))['sha']
            if not isinstance(commit,str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
                raise Rejected('invalid_commit')
            data = await fetch_public('https://codeload.github.com/'+owner+'/'+repo+'/zip/'+commit, SOURCE_BYTES)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise HTTPException(504, 'Se agotó el tiempo de descarga de GitHub. No se instaló contenido.') from exc
    except Rejected as exc:
        status = 413 if str(exc) == 'source_size_limit' else 400
        raise HTTPException(status, 'No se pudo importar el repositorio público dentro de los límites. No se instaló contenido.') from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, 'No se pudo importar el repositorio público dentro de los límites. No se instaló contenido.') from exc
    return data, {'kind':'github', 'url':url, 'repository':owner+'/'+repo, 'commit':commit, 'path':path}


def ingest_identity(request):
    path = Path(os.getenv('QUARANTINE_INGEST_TOKEN_FILE', '/run/runtime-auth/quarantine-token'))
    try:
        expected = path.read_text().strip()
    except OSError:
        raise HTTPException(503, 'El ingreso interno de cuarentena no está disponible.')
    if not expected:
        raise HTTPException(503, 'El ingreso interno de cuarentena no está disponible.')
    if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer '+expected):
        raise HTTPException(401, 'Se requiere el token dedicado de ingreso.')


def summary(item):
    # Filenames and provenance remain untrusted data; descriptions and content are omitted.
    keys = ('id','sha256','provenance','manifest','findings','state','eligible','notice','plan','created_at')
    return {**{key:item[key] for key in keys}, 'untrusted':True,
            'profile':item['provenance'].get('profile'), 'report':report(item)}


def register_quarantine(app, owner, csrf, control):
    from app.quarantine_chat import ChatQuarantine, session
    app.state.quarantine_chat = ChatQuarantine(control)
    intake = asyncio.Lock()
    store = Quarantine(control)

    @app.get('/api/quarantine/chat/{sid}')
    async def chat_history(sid:str, request:Request):
        owner(request)
        return {'messages':app.state.quarantine_chat.history(session(sid))}

    @app.get('/internal/quarantine/list')
    async def shared_list(request:Request):
        ingest_identity(request)
        return app.state.quarantine_chat.listing()

    @app.post('/api/quarantine/upload')
    async def upload(request:Request):
        csrf(request, owner(request))
        name, _ = file_name(request)
        if intake.locked():
            raise HTTPException(409, 'Espera a que termine la importación anterior.')
        async with intake:
            data = await read_upload(request, SOURCE_BYTES)
            return store.create(data, name, {'kind':'upload', 'filename':name})

    @app.post('/api/quarantine/github')
    async def github(request:Request):
        csrf(request, owner(request))
        try:
            payload = await request.json()
            url = payload.get('url')
        except (ValueError, AttributeError):
            raise HTTPException(400, 'Solicitud inválida.')
        if intake.locked():
            raise HTTPException(409, 'Espera a que termine la importación anterior.')
        async with intake:
            data, provenance = await github_package(url)
            return store.create(data, 'github.zip', provenance)

    @app.get('/api/quarantine')
    async def listing(request:Request):
        owner(request)
        folders = sorted(store.root.iterdir(), key=lambda p:p.name) if store.root.exists() else []
        items = [store.read(p.name, previews=False) for p in folders if ID.fullmatch(p.name)]
        return {'items':sorted(items, key=lambda i:i['created_at'], reverse=True), 'notice':NOTICE}

    @app.get('/api/quarantine/{item_id}')
    async def detail(item_id:str, request:Request):
        owner(request)
        return store.read(item_id)

    @app.post('/api/quarantine/{item_id}/approve')
    async def approval(item_id:str, request:Request):
        csrf(request, owner(request))
        try:
            payload = await request.json()
            if not isinstance(payload,dict):
                raise ValueError()
        except ValueError:
            raise HTTPException(400, 'Solicitud inválida.')
        async with intake:
            return store.approve(item_id, payload.get('expected_sha256'), payload.get('profile'), payload.get('skill_path'))

    @app.post('/internal/quarantine/upload')
    async def internal_upload(request:Request):
        ingest_identity(request)
        profile = request.query_params.get('profile')
        if profile not in PROFILES:
            raise HTTPException(400, 'Perfil no permitido.')
        name, _ = file_name(request)
        if intake.locked():
            raise HTTPException(409, 'Espera a que termine la importación anterior.')
        async with intake:
            data = await read_upload(request, SOURCE_BYTES)
            return summary(store.create(data, name, {'kind':'upload','filename':name,'profile':profile,'channel':'internal'}))

    @app.post('/internal/quarantine/github')
    async def internal_github(request:Request):
        ingest_identity(request)
        try:
            payload = await request.json()
            profile = payload.get('profile')
            url = payload.get('url')
            if not isinstance(profile,str) or profile not in PROFILES:
                raise ValueError()
        except (ValueError, AttributeError):
            raise HTTPException(400, 'Solicitud o perfil inválido.')
        if intake.locked():
            raise HTTPException(409, 'Espera a que termine la importación anterior.')
        async with intake:
            try:
                data, provenance = await github_package(url)
            except HTTPException as error:
                return {'kind':'quarantine-error', 'report':import_failure(error.status_code)}
            provenance.update(profile=profile, channel='internal')
            return summary(store.create(data, 'github.zip', provenance))

    @app.get('/internal/quarantine/{item_id}')
    async def internal_report(item_id:str, request:Request):
        ingest_identity(request)
        return summary(store.read(item_id, previews=False))
