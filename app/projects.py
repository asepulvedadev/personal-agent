"""Transactional project workspace. PostgreSQL is the only production store."""
import base64
import copy
import hashlib
import json
import os
import re
import secrets
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

ID = re.compile(r"[a-f0-9]{32}\Z")
MAX_DOCUMENT = 10 * 1024 * 1024
MAX_COMMAND = 14 * 1024 * 1024
PROJECT_STATUSES = {'draft', 'active', 'paused', 'finished', 'archived'}
TASK_DEFAULTS = dict(title='', code='', group_id=None, why='', benefit='', proposal='',
    resolution='', evidence='', priority='normal', approval='pending', status='todo',
    blocker='', blocker_owner='', next_step='', estimated_minutes=None, visible=False,
    internal_notes='')
TASK_FIELDS = set(TASK_DEFAULTS)
CONTACT_FIELDS = {'name', 'email', 'phone', 'company', 'notes', 'status'}


class ProjectError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def fail(message='Datos del proyecto inválidos.', status=400):
    raise ProjectError(status, message)


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def uid():
    return secrets.token_hex(16)


def identity(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        fail('Identificador inválido.')
    return value


def text(value, required=False, limit=20000):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        fail()
    value = value.strip()
    if required and not value:
        fail('Completa los campos obligatorios.')
    return value


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 10**9:
        fail()
    return value


def boolean(value):
    if type(value) is not bool:
        fail()
    return value


def fields(data, allowed):
    if not isinstance(data, dict) or set(data) - set(allowed):
        fail('Campos no permitidos.')


def choice(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        fail()
    return value


def revision(value, current):
    if type(value) is not int:
        fail('La revisión es obligatoria.')
    if value != current:
        fail('El proyecto cambió. Recarga antes de guardar.', 409)


def edit_task(task, data):
    fields(data, TASK_FIELDS)
    result = copy.deepcopy(task)
    for key, value in data.items():
        if key == 'visible': value = boolean(value)
        elif key == 'estimated_minutes': value = None if value is None else integer(value)
        elif key == 'group_id': value = None if value is None else identity(value)
        elif key == 'priority': value = choice(value, {'high', 'normal', 'low'})
        elif key == 'approval': value = choice(value, {'pending', 'approved'})
        elif key == 'status': value = choice(value, {'todo', 'doing', 'blocked', 'done'})
        else: value = text(value, required=key == 'title', limit=500 if key in {'title','code'} else 20000)
        result[key] = value
    if result.get('status') == 'done' and not (result.get('resolution') and result.get('evidence')):
        fail('Para completar la tarea indica resolución y evidencia.')
    if result.get('status') == 'blocked' and not (result.get('blocker') and result.get('next_step')):
        fail('Indica el bloqueo y el siguiente paso.')
    task.update(result)
    return task


def new_task(data):
    task = dict(id=uid(), **TASK_DEFAULTS, comments=[], time_entries=[], timer=None)
    edit_task(task, data)
    text(task['title'], True, 500)
    return task


def new_project(data, timestamp=None):
    fields(data, {'name','description','client_id','status','groups','tasks'})
    stamp = timestamp or now()
    project = dict(id=uid(), name=text(data.get('name'), True, 500),
        description=text(data.get('description', '')), client_id=data.get('client_id'),
        status=choice(data.get('status', 'draft'), PROJECT_STATUSES), internal_notes='',
        revision=1, created_at=stamp, updated_at=stamp, groups=[], tasks=[], documents=[], shares=[], audit=[])
    if project['client_id'] is not None: identity(project['client_id'])
    groups = data.get('groups', [])
    tasks = data.get('tasks', [])
    if not isinstance(groups, list) or not isinstance(tasks, list) or len(groups) > 200 or len(tasks) > 1000:
        fail()
    # Imports may refer to a declared group by its exact name; persistent IDs remain server generated.
    group_names = {}
    for group in groups:
        fields(group, {'name'})
        name = text(group.get('name'), True, 500)
        if name in group_names: fail('Grupo duplicado.')
        item = {'id':uid(), 'name':name}
        project['groups'].append(item)
        group_names[name] = item['id']
    for original in tasks:
        data_task = dict(original) if isinstance(original, dict) else fail()
        if isinstance(data_task.get('group_id'),str) and data_task.get('group_id') in group_names:
            data_task['group_id'] = group_names[data_task['group_id']]
        task = new_task(data_task)
        check_group(project, task['group_id'])
        project['tasks'].append(task)
    return project


def find(items, item_id):
    identity(item_id)
    for item in items:
        if item['id'] == item_id: return item
    fail('No se encontró el recurso.', 404)


def check_group(project, group_id):
    if group_id is not None: find(project['groups'], group_id)


def client_project(project):
    """Allowlist public fields, rather than recursively removing sensitive names."""
    result = {key:project[key] for key in ('id','name','description','status','revision','updated_at')}
    visible = [task for task in project['tasks'] if task['visible']]
    result['groups'] = [copy.deepcopy(group) for group in project['groups']
        if any(task['group_id'] == group['id'] for task in visible)]
    result['tasks'] = []
    public_task_fields = TASK_FIELDS - {'internal_notes'}
    for task in visible:
        clean = {'id':task['id'], **{key:copy.deepcopy(task[key]) for key in public_task_fields}}
        clean['comments'] = [{key:value for key,value in comment.items() if key != 'actor'}
                             for comment in task['comments'] if comment['visible']]
        clean['time_entries'] = [{key:value for key,value in entry.items() if key != 'actor'}
                                 for entry in task['time_entries'] if entry['visible']]
        result['tasks'].append(clean)
    visible_ids = {task['id'] for task in visible}
    result['documents'] = [copy.deepcopy(document) for document in project['documents']
        if document['visible'] and (document['task_id'] is None or document['task_id'] in visible_ids)]
    return result


def project_index(project):
    estimates = [task['estimated_minutes'] for task in project['tasks']]
    return {**{key:project[key] for key in ('id','name','description','client_id','status','revision','updated_at')},
        'task_count':len(project['tasks']), 'done_count':sum(task['status']=='done' for task in project['tasks']),
        'tracked_minutes':sum(entry['minutes'] for task in project['tasks'] for entry in task['time_entries']),
        'time_entry_count':sum(len(task['time_entries']) for task in project['tasks']),
        'estimated_minutes':sum(estimates) if estimates and all(x is not None for x in estimates) else None}


class PostgresStore:
    """Each operation owns one connection and one transaction, with row-level locks."""
    def __init__(self, config):
        self.config = config
        self.initialized = False

    def connect(self):
        import psycopg
        return psycopg.connect(**self.config)

    def initialize(self):
        if self.initialized: return
        with self.connect() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(720175111)')
            connection.execute('''CREATE TABLE IF NOT EXISTS workspace_projects (
                id text PRIMARY KEY, data jsonb NOT NULL)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS workspace_contacts (
                id text PRIMARY KEY, data jsonb NOT NULL)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS workspace_operations (
                id text PRIMARY KEY, actor text NOT NULL, fingerprint text NOT NULL, result jsonb NOT NULL)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS workspace_shares (
                hash text PRIMARY KEY, id text UNIQUE NOT NULL, project_id text NOT NULL REFERENCES workspace_projects(id),
                revoked boolean NOT NULL DEFAULT false)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS workspace_audit (
                sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                project_id text, event jsonb NOT NULL)''')
            connection.execute('''CREATE INDEX IF NOT EXISTS workspace_audit_project_idx
                ON workspace_audit(project_id, sequence)''')
            connection.execute('''CREATE OR REPLACE FUNCTION workspace_audit_immutable() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit is append only'; END $$''')
            connection.execute('''DROP TRIGGER IF EXISTS workspace_audit_immutable_trigger ON workspace_audit''')
            connection.execute('''CREATE TRIGGER workspace_audit_immutable_trigger BEFORE UPDATE OR DELETE
                ON workspace_audit FOR EACH ROW EXECUTE FUNCTION workspace_audit_immutable()''')
        self.initialized = True

    @contextmanager
    def transaction(self, operation_id=None, actor=None):
        self.initialize()
        with self.connect() as connection:
            if operation_id:
                # Serialize duplicate operation IDs across actors/projects before checking stored output.
                lock = int.from_bytes(hashlib.sha256(operation_id.encode()).digest()[:8], 'big', signed=True)
                connection.execute('SELECT pg_advisory_xact_lock(%s)', (lock,))
            if actor:
                # Timer invariant spans projects and cannot be enforced by one project row alone.
                lock = int.from_bytes(hashlib.sha256(('timer:' + actor).encode()).digest()[:8], 'big', signed=True)
                connection.execute('SELECT pg_advisory_xact_lock(%s)', (lock,))
            yield PostgresTransaction(connection)


class PostgresTransaction:
    def __init__(self, connection): self.connection = connection

    def get(self, kind, item_id, lock=False):
        table = {'project':'workspace_projects','contact':'workspace_contacts'}[kind]
        row = self.connection.execute(f'SELECT data FROM {table} WHERE id=%s' + (' FOR UPDATE' if lock else ''), (item_id,)).fetchone()
        if not row: fail('No se encontró el recurso.', 404)
        return row[0]

    def all(self, kind):
        table = {'project':'workspace_projects','contact':'workspace_contacts'}[kind]
        return [row[0] for row in self.connection.execute(f'SELECT data FROM {table} ORDER BY id')]

    def save(self, kind, value):
        from psycopg.types.json import Jsonb
        table = {'project':'workspace_projects','contact':'workspace_contacts'}[kind]
        self.connection.execute(f'INSERT INTO {table}(id,data) VALUES(%s,%s) ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data', (value['id'], Jsonb(value)))

    def operation(self, operation_id):
        row = self.connection.execute('SELECT actor,fingerprint,result FROM workspace_operations WHERE id=%s', (operation_id,)).fetchone()
        return row

    def record_operation(self, operation_id, actor, fingerprint, result):
        from psycopg.types.json import Jsonb
        self.connection.execute('INSERT INTO workspace_operations(id,actor,fingerprint,result) VALUES(%s,%s,%s,%s)',
                                (operation_id,actor,fingerprint,Jsonb(result)))

    def share(self, hashed, lock=False):
        row = self.connection.execute('SELECT id,project_id,revoked FROM workspace_shares WHERE hash=%s' + (' FOR UPDATE' if lock else ''), (hashed,)).fetchone()
        if not row or row[2]: fail('El acceso no está disponible.', 401)
        return row

    def add_share(self, hashed, share_id, project_id):
        self.connection.execute('INSERT INTO workspace_shares(hash,id,project_id) VALUES(%s,%s,%s)', (hashed,share_id,project_id))

    def revoke_share(self, share_id, project_id):
        self.connection.execute('UPDATE workspace_shares SET revoked=true WHERE id=%s AND project_id=%s', (share_id,project_id))

    def audit(self, project_id, event):
        from psycopg.types.json import Jsonb
        self.connection.execute('INSERT INTO workspace_audit(project_id,event) VALUES(%s,%s)', (project_id,Jsonb(event)))


class Projects:
    def __init__(self, store, storage, clock=now):
        self.store, self.storage, self.clock = store, Path(storage), clock

    def index(self):
        with self.store.transaction() as tx:
            return {'contacts':tx.all('contact'), 'projects':[project_index(p) for p in tx.all('project')]}

    def get(self, project_id):
        identity(project_id)
        with self.store.transaction() as tx: return tx.get('project', project_id)

    def client_get(self, share_token):
        with self.store.transaction() as tx:
            _, project_id, _ = tx.share(self.hash_token(share_token))
            return client_project(tx.get('project',project_id))

    @staticmethod
    def hash_token(value):
        if not isinstance(value, str) or not 32 <= len(value) <= 200:
            fail('El acceso no está disponible.', 401)
        return hashlib.sha256(value.encode()).hexdigest()

    def command(self, command, actor='owner', client_token=None):
        fields(command, {'action','project_id','revision','operation_id','data'})
        action = text(command.get('action'), True, 60)
        operation_id = identity(command.get('operation_id'))
        data = command.get('data', {})
        if not isinstance(data, dict): fail()
        # Owner-only authorization precedes operation replay, including cross-actor IDs.
        if action == 'move_document_to_project' and (actor != 'owner' or client_token is not None):
            fail('Solo el propietario puede mover archivos.',403)
        if action == 'update_time' and (actor != 'owner' or client_token is not None):
            fail('Solo el propietario puede corregir registros de tiempo.',403)
        if action in {'create_share','revoke_share'} and actor != 'owner': fail('Solo el propietario puede compartir.',403)
        fingerprint = hashlib.sha256(json.dumps(command,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        created_file = None
        try:
            with self.store.transaction(operation_id, actor) as tx:
                if client_token is not None:
                    share_id, project_id, _ = tx.share(self.hash_token(client_token))
                    actor = 'client:' + share_id
                    if action != 'add_comment': fail('Acción no permitida.',403)
                else:
                    project_id = command.get('project_id')
                previous = tx.operation(operation_id)
                if previous:
                    if previous[0] != actor or previous[1] != fingerprint:
                        fail('El identificador de operación ya fue utilizado.',409)
                    if client_token is not None:
                        # Operation history is evidence, not current read authorization.
                        # Use the same project-then-share lock ordering as live writes.
                        current = tx.get('project',project_id,lock=True)
                        tx.share(self.hash_token(client_token),lock=True)
                        return {'project':client_project(current)}
                    return copy.deepcopy(previous[2])
                stamp = self.clock()
                if action in {'create_contact','update_contact'}:
                    if client_token: fail('Acción no permitida.',403)
                    contact = self.contact(tx, action, data, stamp)
                    event = {'action':action,'actor':actor,'at':stamp,'revision':contact['revision']}
                    tx.audit(None,event)
                    result = {'contact':contact}
                else:
                    if action == 'create_project':
                        if client_token: fail('Acción no permitida.',403)
                        project = new_project(data,stamp)
                    else:
                        identity(project_id)
                        project = tx.get('project',project_id,lock=True)
                        if client_token is not None: tx.share(self.hash_token(client_token),lock=True)
                        if client_token is None: revision(command.get('revision'), project['revision'])
                        project['revision'] += 1
                        project['updated_at'] = stamp
                    if action != 'create_project':
                        extra, created_file = self.mutate(tx,project,action,data,actor,stamp,client_token is not None)
                    else: extra = {}
                    if project['client_id'] is not None: tx.get('contact',project['client_id'])
                    event = {'action':action,'actor':actor,'at':stamp,'revision':project['revision']}
                    project['audit'].append(event)
                    tx.save('project',project)
                    tx.audit(project['id'],event)
                    result = {'project':client_project(project) if client_token is not None else project, **extra}
                stored_result = copy.deepcopy(result)
                if action == 'create_share': stored_result['share'].pop('token',None)
                tx.record_operation(operation_id,actor,fingerprint,stored_result)
                return copy.deepcopy(result)
        except Exception:
            if created_file is not None:
                try: created_file.unlink(missing_ok=True)
                except OSError: pass
            raise

    def contact(self, tx, action, data, stamp):
        fields(data, CONTACT_FIELDS | ({'contact_id','revision'} if action == 'update_contact' else set()))
        if action == 'create_contact':
            contact = dict(id=uid(),name='',email='',phone='',company='',notes='',status='contact',revision=1,created_at=stamp,updated_at=stamp)
        else:
            contact = tx.get('contact',identity(data.get('contact_id')),lock=True)
            revision(data.get('revision'),contact['revision'])
            contact['revision'] += 1
            contact['updated_at'] = stamp
        for key in CONTACT_FIELDS & data.keys():
            contact[key] = choice(data[key],{'contact','client','archived'}) if key == 'status' else text(data[key],required=key=='name',limit=20000 if key=='notes' else 500)
        text(contact['name'],True,500)
        tx.save('contact',contact)
        return contact

    def mutate(self, tx, p, action, data, actor, stamp, client):
        extra, created = {}, None
        if action == 'update_project':
            fields(data, {'name','description','client_id','status','internal_notes'})
            for key,value in data.items():
                if key == 'client_id': value = None if value is None else identity(value)
                elif key == 'status': value = choice(value, PROJECT_STATUSES)
                else: value = text(value,required=key=='name',limit=500 if key=='name' else 20000)
                p[key] = value
        elif action in {'add_group','update_group'}:
            fields(data, {'name'} | ({'group_id'} if action=='update_group' else set()))
            name = text(data.get('name'),True,500)
            if action == 'add_group': p['groups'].append({'id':uid(),'name':name})
            else: find(p['groups'],data.get('group_id'))['name'] = name
        elif action in {'add_task','update_task'}:
            editable = dict(data)
            if action == 'add_task':
                task = new_task(editable)
                p['tasks'].append(task)
            else:
                task = find(p['tasks'],editable.pop('task_id',None))
                edit_task(task,editable)
            check_group(p,task['group_id'])
        elif action in {'add_comment','add_time','update_time','start_timer','stop_timer'}:
            task = find(p['tasks'],data.get('task_id'))
            if client and not task['visible']: fail('No se encontró el recurso.',404)
            if action == 'add_comment':
                fields(data,{'task_id','text','kind','visible'})
                entry = {'id':uid(),'actor':actor,'created_at':stamp,'text':text(data.get('text'),True),
                    'kind':choice(data.get('kind','comment'),{'comment','question','answer'}),
                    'visible':True if client else boolean(data.get('visible',False))}
                task['comments'].append(entry)
            elif action in {'add_time','update_time'}:
                fields(data,{'task_id','date','minutes','note'} | ({'visible'} if action == 'add_time' else {'entry_id'}))
                work_date = data.get('date')
                if not isinstance(work_date,str): fail()
                try:
                    if date.fromisoformat(work_date).isoformat() != work_date: fail()
                except ValueError: fail('Fecha inválida.')
                values = {'date':work_date,'minutes':integer(data.get('minutes'),1),'note':text(data.get('note',''))}
                if action == 'add_time':
                    task['time_entries'].append({'id':uid(),'actor':actor,'created_at':stamp,**values,
                        'visible':boolean(data.get('visible',False))})
                else:
                    find(task['time_entries'],data.get('entry_id')).update(values)
            elif action == 'start_timer':
                fields(data,{'task_id','note'})
                # Actor transaction advisory lock prevents concurrent starts on different projects.
                if any(t['timer'] and t['timer']['actor']==actor for project in tx.all('project') for t in project['tasks']):
                    fail('Ya hay un temporizador activo.',409)
                if task['timer']: fail('La tarea ya tiene un temporizador activo.',409)
                task['timer'] = {'actor':actor,'started_at':stamp,'note':text(data.get('note',''))}
            else:
                fields(data,{'task_id'})
                timer = task['timer']
                if not timer or timer['actor'] != actor: fail('No hay un temporizador propio activo.',409)
                elapsed = max(0,(datetime.fromisoformat(stamp.replace('Z','+00:00')) - datetime.fromisoformat(timer['started_at'].replace('Z','+00:00'))).total_seconds())
                # Whole completed minutes only: never manufacture a minute for a sub-minute timer.
                minutes = int(elapsed // 60)
                if minutes:
                    task['time_entries'].append({'id':uid(),'actor':actor,'created_at':stamp,
                        'date':stamp[:10],'minutes':minutes,'note':timer['note'],'visible':False})
                task['timer'] = None
        elif action == 'add_document':
            fields(data,{'task_id','name','media_type','content_base64','visible'})
            task_id = data.get('task_id')
            if task_id is not None: find(p['tasks'],task_id)
            name = text(data.get('name'),True,255)
            media_type = text(data.get('media_type','application/octet-stream'),True,200)
            encoded = data.get('content_base64')
            if not isinstance(encoded,str) or len(encoded) > (MAX_DOCUMENT+2)//3*4: fail('Archivo demasiado grande.',413)
            try: content = base64.b64decode(encoded,validate=True)
            except (ValueError,TypeError): fail('Archivo inválido.')
            if not content or len(content)>MAX_DOCUMENT: fail('Archivo vacío o demasiado grande.',413)
            document = {'id':uid(),'task_id':task_id,'name':name,'media_type':media_type,'size':len(content),
                'sha256':hashlib.sha256(content).hexdigest(),'visible':boolean(data.get('visible',False)),'created_at':stamp}
            self.storage.mkdir(mode=0o700,parents=True,exist_ok=True)
            os.chmod(self.storage,0o700)
            directory = self.storage / p['id']
            directory.mkdir(mode=0o700,exist_ok=True)
            os.chmod(directory,0o700)
            created = directory / document['id']
            fd = os.open(created,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:
                with os.fdopen(fd,'wb') as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                for durable_directory in (directory, self.storage):
                    directory_fd = os.open(durable_directory,os.O_RDONLY|os.O_DIRECTORY)
                    try: os.fsync(directory_fd)
                    finally: os.close(directory_fd)
            except Exception:
                created.unlink(missing_ok=True)
                raise
            p['documents'].append(document)
        elif action == 'move_document_to_project':
            fields(data,{'document_id'})
            document = find(p['documents'],data.get('document_id'))
            if document['task_id'] is not None and document['visible'] and not find(p['tasks'],document['task_id'])['visible']:
                fail('Marca el archivo como interno antes de moverlo desde una tarea no compartida.')
            # Preserve file bytes and effective privacy; remove only the task association.
            document['task_id'] = None
        elif action == 'set_visibility':
            fields(data,{'kind','id','visible'})
            kind = choice(data.get('kind'),{'document','comment','time'})
            visible = boolean(data.get('visible'))
            if kind == 'document': items = p['documents']
            else:
                key = 'comments' if kind == 'comment' else 'time_entries'
                items = [item for task in p['tasks'] for item in task[key]]
            find(items,data.get('id'))['visible'] = visible
        elif action == 'create_share':
            fields(data,{'label'})
            token = secrets.token_urlsafe(32)
            share = {'id':uid(),'label':text(data.get('label'),True,500),'created_at':stamp,'revoked_at':None}
            # Project row must exist before its share FK can be written.
            tx.save('project',p)
            tx.add_share(self.hash_token(token),share['id'],p['id'])
            p['shares'].append(share)
            extra = {'share':{'id':share['id'],'token':token}}
        elif action == 'revoke_share':
            fields(data,{'share_id'})
            share = find(p['shares'],data.get('share_id'))
            share['revoked_at'] = stamp
            tx.revoke_share(share['id'],p['id'])
        else: fail('Acción no permitida.')
        return extra, created

    def client_comment(self, share_token, data):
        fields(data,{'task_id','text','kind','operation_id'})
        return self.command({'action':'add_comment','operation_id':data.get('operation_id'),
            'data':{key:value for key,value in data.items() if key != 'operation_id'}}, actor='client',client_token=share_token)

    def document(self, document_id, project_id=None, share_token=None):
        identity(document_id)
        with self.store.transaction() as tx:
            if share_token is not None:
                _,project_id,_ = tx.share(self.hash_token(share_token))
            identity(project_id)
            project = tx.get('project',project_id)
            documents = client_project(project)['documents'] if share_token is not None else project['documents']
            document = find(documents,document_id)
            path = self.storage / project_id / document_id
            # Read under authorization and serve immutable bytes; no subsequent path race in FileResponse.
            try: content = path.read_bytes()
            except OSError: fail('El archivo no está disponible.',404)
            if len(content) != document['size'] or hashlib.sha256(content).hexdigest() != document['sha256']:
                fail('El archivo no está disponible.',404)
            return document, content
