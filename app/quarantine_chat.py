"""Trusted chat ingress and durable display-only transcripts, separate from model history."""
import asyncio
import json
from pathlib import Path
import re
import secrets
import shutil
import time

from fastapi import HTTPException

from app.quarantine import Quarantine, ID, enforce_chat_intake, github_package, summary, write_new
from app.quarantine_assessment import import_failure

SESSION = re.compile(r'owner_[a-f0-9]{32}\Z')
GITHUB = re.compile(r"https?://(?:www\.)?github\.com/[^\s<>`\"']+", re.I)
DETAIL = re.compile(r'(?:/?(?:ver|revisar)\s+)?/?skill\s+([a-f0-9]{32})[.!]?\Z', re.I)
LIST = re.compile(r'/?(?:cuarentena|quarantine)(?:\s+(?:de\s+)?skills?)?[.!]?\Z', re.I)
EXPLICIT = re.compile(r'(?<!\S)\$cuarentena(?![A-Za-z0-9_./-])', re.I)
COMMAND = re.compile(r'\$cuarentena(?:\s+([^\r\n]*))?\Z', re.I)
COMMAND_DETAIL = re.compile(r'(?:informe\s+)?([a-f0-9]{32})(?:\s+([1-9][0-9]{0,2}|1000))?\Z', re.I)
HELP = ('Revisión compartida · Hermes Web y Carpediev\n'
        'Usa $cuarentena https://github.com/propietario/repositorio o escribe $cuarentena y adjunta el archivo original de la skill o herramienta.\n'
        'Envía un solo enlace o los archivos, sin mezclarlos. Solo se admiten enlaces HTTPS de GitHub y formatos de código o ZIP compatibles.\n'
        '$cuarentena lista muestra las revisiones; $cuarentena ID muestra el informe; $cuarentena informe ID 2 abre otra página.\n'
        'La revisión analiza el contenido como datos: no lo instala ni ejecuta.')
REJECT = ('Revisión compartida · Hermes Web y Carpediev\n'
          'Solicitud de cuarentena ambigua o no compatible. No se ha descargado, instalado ni ejecutado contenido.\n'
          'Usa $cuarentena con un único enlace HTTPS de GitHub o adjunta el archivo original sin enlaces. '
          'También puedes usar $cuarentena lista o $cuarentena informe ID [página de 1 a 1000].')


def session(value):
    if not isinstance(value,str) or not SESSION.fullmatch(value):
        raise HTTPException(400, 'Conversación inválida.')
    return value


class ChatQuarantine:
    def __init__(self, control):
        self.store = Quarantine(control)
        self.root = Path(control) / 'quarantine-chat'
        self.lock = asyncio.Lock()

    def history(self, sid):
        folder = self.root / session(sid)
        rows = []
        if folder.exists():
            for path in sorted(folder.glob('*.json')):
                record = json.loads(path.read_text())
                for role in ('user','assistant'):
                    rows.append({'id':record['id']+'-'+role, 'role':role,
                                 'content':record[role], 'timestamp':record['timestamp'],
                                 'quarantine_report':True})
        return sorted(rows, key=lambda row:(row['timestamp'], row['role']=='assistant'))

    def forget(self, sid):
        folder = self.root / session(sid)
        if folder.is_dir() and not folder.is_symlink():
            shutil.rmtree(folder)

    def reserve_history(self, sid):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = self.root / session(sid)
        if not folder.exists() and len(list(self.root.iterdir())) >= 100:
            raise HTTPException(409, 'El historial de revisiones está lleno; revisa tus conversaciones.')
        folder.mkdir(mode=0o700, exist_ok=True)
        if len(list(folder.iterdir())) >= 100:
            raise HTTPException(409, 'Esta conversación alcanzó el límite de informes. Abre otra conversación.')
        return folder

    def items(self):
        if not self.store.root.exists():
            return []
        folders=sorted((p for p in self.store.root.iterdir() if ID.fullmatch(p.name)),
                       key=lambda p:(p/'record.json').stat().st_mtime_ns, reverse=True)[:10]
        return [self.store.read(p.name,previews=False) for p in folders]

    def listing(self):
        items = self.items()
        lines = ['Revisión compartida · Hermes Web y Carpediev',
                 'La misma cuarentena está disponible desde ambos chats; no activa skills automáticamente.']
        lines.extend(f"{item['id']} · {item['state']} · escribe ver skill {item['id']}" for item in items[:10])
        if not items:
            lines.append('Todavía no hay skills en cuarentena. Envía un enlace de GitHub o un archivo de skill.')
        return {'kind':'quarantine-list', 'report':{'version':1, 'scope':'shared', 'text':'\n'.join(lines)}}

    async def handle(self, sid, message, attachments):
        query = DETAIL.fullmatch(message.strip())
        listing = LIST.fullmatch(message.strip())
        explicit = EXPLICIT.search(message)
        page = 1
        local_reply = None
        if explicit:
            command = COMMAND.fullmatch(message.strip())
            argument = command[1].strip() if command and command[1] else ''
            if not command:
                local_reply = REJECT
            elif not argument:
                if not attachments:
                    local_reply = HELP
            elif argument.casefold() == 'lista' and not attachments:
                listing = True
            elif (detail := COMMAND_DETAIL.fullmatch(argument)) and not attachments:
                query = detail
                page = int(detail[2] or 1)
            elif (not attachments and GITHUB.fullmatch(argument)
                  and argument.startswith('https://github.com/')):
                pass
            else:
                local_reply = REJECT
        references = isinstance(attachments,list) and any(isinstance(a,dict) and 'quarantine_id' in a for a in attachments)
        candidate = False
        try:
            enforce_chat_intake(message, attachments)
        except HTTPException as error:
            if error.status_code != 409:
                raise
            candidate = True
        if not (explicit or query or listing or references or candidate):
            return None
        if not isinstance(attachments,list) or len(attachments)>3:
            raise HTTPException(400, 'Adjunta un máximo de tres archivos.')
        async with self.lock:
            folder = self.reserve_history(sid)
            reports = [local_reply] if local_reply else []
            prepared = []
            # Validate all attachment envelopes before retaining new source bytes.
            for attachment in attachments if not local_reply else []:
                if not isinstance(attachment,dict):
                    raise HTTPException(400, 'Adjunto inválido.')
                name = attachment.get('name')
                if not isinstance(name,str) or not 1<=len(name)<=120 or any(c in name for c in '/\\\x00'):
                    raise HTTPException(400, 'Nombre de archivo inválido.')
                receipt = attachment.get('quarantine_id')
                if receipt is not None:
                    prepared.append(('receipt', self.store.read(receipt, previews=False)))
                    continue
                text = attachment.get('text')
                if Path(name).suffix.lower() not in {'.md','.txt','.csv','.json','.yaml','.yml','.toml','.ini','.cfg',
                        '.py','.sh','.bash','.js','.ts','.mjs','.cjs','.ps1','.rb','.go','.rs','.html','.css','.xml','.skill'} or not isinstance(text,str) or len(text)>55000:
                    raise HTTPException(400, 'Adjunta los bytes originales mediante el clip del chat; no texto que simule un ZIP.')
                prepared.append(('source',(name,text.encode())))
            links = list(dict.fromkeys(url.rstrip('.,;!?') for url in GITHUB.findall(message)))
            if not local_reply and (len(links)>1 or (links and prepared)):
                raise HTTPException(400, 'Envía un único enlace o los archivos de skill, sin mezclar ambos.')
            if local_reply:
                pass
            elif query and not prepared and not links:
                item = self.store.read(query[1].lower(), previews=False)
                reviewed = summary(item, page=page) if page > 1 else summary(item)
                reports.append(reviewed['report']['text'])
            elif listing and not prepared and not links:
                reports.append(self.listing()['report']['text'])
            else:
                if links:
                    try:
                        data, provenance = await github_package(links[0])
                    except HTTPException as error:
                        reports.append(import_failure(error.status_code)['text'])
                    else:
                        item = self.store.create(data, provenance.get('filename', 'github.zip') if provenance['kind']=='github' else provenance['filename'],
                                                 {**provenance,'channel':'web-chat','session_id':sid})
                        reports.append(summary(item)['report']['text'])
                for kind, value in prepared:
                    if kind=='receipt':
                        item=value
                    else:
                        name,data=value
                        item=self.store.create(data,name,{'kind':'upload','filename':name,'channel':'web-chat','session_id':sid})
                    reports.append(summary(item)['report']['text'])
            if not reports:
                raise HTTPException(400, 'Envía el enlace o archivo original de la skill.')
            answer='\n\n'.join(reports)
            record={'id':secrets.token_hex(16), 'timestamp':time.time(),
                    'user':message[:20000], 'assistant':answer}
            write_new(folder/(record['id']+'.json'),json.dumps(record,ensure_ascii=False).encode())
            return answer


def events(text):
    for payload in ({'type':'assistant.delta','delta':text},{'type':'run.completed'}):
        yield 'data: '+json.dumps(payload,ensure_ascii=False)+'\n\n'
