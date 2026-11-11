#!/usr/bin/env python3
"""Prepare/replay a private Word proposal import via the shared project API.

The source directory and credentials are never copied into the repository. Run
without --apply to prepare a private manifest and inspect counts. Network target is fixed private
Web; SSH/file transfer authorization remains separate from this program.
"""
import argparse
import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import urllib.error
import urllib.request
from zipfile import ZipFile
import xml.etree.ElementTree as ET

NS = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
GROUPS = {
    'A':'Primer contacto y respuesta', 'B':'Pipeline, etapas y salidas',
    'C':'Tareas y citas', 'D':'Clientes y ventas', 'E':'Registro y calidad de datos',
    'F':'Webinar', 'G':'Tableros', 'H':'Limpieza y herramientas',
}


def paragraphs(cell):
    return '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t',NS))
                     for p in cell.findall('.//w:p',NS)).strip()


def extract_proposals(path):
    with ZipFile(path) as doc:
        info = doc.getinfo('word/document.xml')
        if info.file_size > 8*1024**2:
            raise ValueError('Word source too large')
        raw = doc.read(info)
    if b'<!DOCTYPE' in raw or b'<!ENTITY' in raw:
        raise ValueError('External XML declarations are not supported')
    root = ET.fromstring(raw)
    result = []
    for row in root.findall('.//w:tbl/w:tr',NS):
        cells = [paragraphs(cell) for cell in row.findall('w:tc',NS)]
        if len(cells) != 5 or not re.fullmatch('[A-H][1-9][0-9]?', cells[0]):
            continue
        code, proposal, why, benefit, priority = cells
        if code in {item['code'] for item in result}:
            raise ValueError('Duplicate proposal code')
        title = proposal if len(proposal) <= 160 else proposal[:157].rsplit(' ',1)[0]+'…'
        result.append({'code':code,'title':title,'proposal':proposal,'why':why,
                       'benefit':benefit,'priority':{'Alta':'high','Media':'normal','Baja':'low'}[priority],
                       'approval':'pending','status':'todo','visible':True})
    return result


class Client:
    def __init__(self, token):
        self.token = token
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(self, path, data=None):
        headers = {'Authorization':'Bearer '+self.token,'X-Project-Actor':'hermes-web'}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request('http://web:8080/internal/projects'+path,
            data=None if data is None else json.dumps(data).encode(),headers=headers)
        with self.http.open(request,timeout=45) as response:
            return json.load(response)
    def command(self, key, action, data, project=None):
        payload = {'operation_id':hashlib.sha256(key.encode()).hexdigest()[:32],
                   'action':action,'data':data}
        if project:
            current = self.request('/'+project)['project']
            payload.update(project_id=project,revision=current['revision'])
        return self.request('/command',payload)


def apply_manifest(manifest, client):
    key = manifest['source_sha256']
    contact = client.command(key+':contact','create_contact',manifest['contact'])['contact']
    groups = [{'name':name} for name in GROUPS.values()]
    tasks = [{**task,'group_id':GROUPS[task['code'][0]]} for task in manifest['tasks']]
    project = client.command(key+':project','create_project',
            {**manifest['project'],'client_id':contact['id'],'groups':groups,'tasks':tasks})['project']['id']
    for document in manifest['documents']:
        current = client.request('/'+project)['project']
        if any(item['sha256'] == document['sha256'] for item in current['documents']):
            continue
        client.command(key+':file:'+document['sha256'],'add_document',
                       {k:v for k,v in document.items() if k != 'sha256'},project)
    return client.request('/'+project)['project']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--client-name')
    parser.add_argument('--project-name')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    if args.apply:
        manifest = json.loads(args.manifest.read_text())
        token = Path('/run/runtime-auth/projects-token').read_text().strip()
        project = apply_manifest(manifest,Client(token))
        print(json.dumps({'project_id':project['id'],'tasks':len(project['tasks']),
                          'documents':len(project['documents']),'revision':project['revision']}))
        return
    if not args.source or not args.output or not args.client_name or not args.project_name:
        parser.error('--source, --output, --client-name and --project-name required to prepare private manifest')
    sources = sorted(p for p in args.source.rglob('*') if p.is_file() and not p.is_symlink())
    word = [p for p in sources if p.suffix == '.docx']
    if len(word) != 1:
        raise ValueError('Expected one authoritative Word proposal')
    tasks = extract_proposals(word[0])
    expected = {letter+str(n) for letter,count in zip('ABCDEFGH',[8,7,3,5,5,5,4,3]) for n in range(1,count+1)}
    if {item['code'] for item in tasks} != expected or len(tasks) != 40:
        raise ValueError('Source proposal set differs from verified 40-item plan')
    docs = []
    for path in sources:
        raw = path.read_bytes()
        if path.suffix not in {'.png','.docx'} or len(raw)>5*1024**2:
            raise ValueError('Unexpected source file')
        docs.append({'name':path.name,'media_type':mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                     'content_base64':base64.b64encode(raw).decode(),'visible':True,
                     'sha256':hashlib.sha256(raw).hexdigest()})
    digest = hashlib.sha256(word[0].read_bytes()).hexdigest()
    manifest = {'source_sha256':digest,'contact':{'name':args.client_name,'status':'client'},
                'project':{'name':args.project_name,'status':'active',
                           'description':'40 propuestas del documento original, todas pendientes de aprobación. Los diagnósticos son declaraciones de la fuente, no una auditoría actual de GHL.'},
                'tasks':tasks,'documents':docs}
    args.output.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as handle:
        args.output.chmod(0o600)
        json.dump(manifest,handle,ensure_ascii=False)
    print(json.dumps({'proposals':len(tasks),'groups':8,'high_priority':sum(t['priority']=='high' for t in tasks),
                      'documents':len(docs),'source_sha256':digest,'private_manifest':True}))


if __name__ == '__main__':
    main()
