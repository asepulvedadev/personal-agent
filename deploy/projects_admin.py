#!/usr/bin/python3
"""Explicit administrator provisioning, never called by a source release hook.

No existing service is restarted here. App activation remains the restricted
GitHub Actions gate. On failure retain the private checkpoint, do not retry blindly.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time

ROOT = Path('/srv/personal-agent')
DB_CONTAINER = 'personal-agent-projects-db'
BASE_COMPOSE_HASH = '61f8cf7a69fbf9914a69ddab84b5399cda00a608b0b6d126d7a463c7b65695b4'
BASE_GUARD_HASH = '47eb86b3f4d9cf10414f14c0697d57819c53434270901268dcb90cdd1387d910'
BASE_BACKUP_HASH = 'e46ac1c2284b689306895bf7e10c63ad9be6e2180d10b232d6185892595be001'
ENV = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin', 'HOME':'/root', 'LANG':'C.UTF-8'}


def patch_compose(text):
    if 'PROJECTS_DATABASE_FILE' in text or 'projects_db:' in text:
        raise ValueError('Projects policy already present; inspect instead of reapplying')
    before = [
        ('      CONTROL_DIR: /control\n', '      CONTROL_DIR: /control\n      PROJECTS_DATABASE_FILE: /run/projects/database.json\n      PROJECTS_TOKEN_FILE: /run/projects/service-token\n'),
        ('      - /srv/personal-agent/control:/control\n', '      - /srv/personal-agent/control:/control\n      - /srv/personal-agent/projects-db/web:/run/projects:ro\n'),
        ('      front: {ipv4_address: 172.30.42.2, aliases: [personal-agent-web]}\n', '      front: {ipv4_address: 172.30.42.2, aliases: [personal-agent-web]}\n      projects_db: {ipv4_address: 172.30.43.2}\n'),
    ]
    for anchor, replacement in before:
        if text.count(anchor) != 1:
            raise ValueError('Unexpected installed Compose policy')
        text = text.replace(anchor, replacement)
    return text + '  projects_db:\n    external: true\n    name: personal-agent_projects-db\n'


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=kwargs.pop('timeout', 180), env=ENV, **kwargs)


def trusted(path):
    path = path.absolute()
    for part in [path, *path.parents]:
        if part.is_symlink() or part.stat().st_uid != 0 or part.stat().st_mode & 0o022:
            raise ValueError('Untrusted administrator path')
    return path


def write_private(path, value, uid=0):
    with path.open('xb') as handle:
        handle.write(value)
    path.chmod(0o600)
    os.chown(path, uid, uid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('Administrator required')
    source = trusted(args.source)
    policy = trusted(ROOT/'deploy-policy')
    with (ROOT/'deployment-state'/'deployment.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, expected in [('compose.yaml',BASE_COMPOSE_HASH),('network-guard.sh',BASE_GUARD_HASH),('backup.py',BASE_BACKUP_HASH)]:
            if hashlib.sha256(trusted(policy/name).read_bytes()).hexdigest() != expected:
                raise ValueError('Installed policy changed; explicit reconciliation required')
        db = ROOT/'projects-db'
        if db.exists() or db.is_symlink() or (ROOT/'runtime-auth/projects-token').exists():
            raise ValueError('Projects provisioning already started; inspect checkpoint')
        # Ensure this exact name is not owned by another deployment.
        names = run(['docker','ps','-a','--format','{{.Names}}']).stdout.decode().splitlines()
        if DB_CONTAINER in names:
            raise ValueError('Database name already exists')
        network = json.loads(run(['docker','network','inspect',*run(['docker','network','ls','-q']).stdout.decode().split()]).stdout)
        import ipaddress
        planned = ipaddress.ip_network('172.30.43.0/29')
        for item in network:
            for entry in item.get('IPAM',{}).get('Config',[]) or []:
                if entry.get('Subnet') and planned.overlaps(ipaddress.ip_network(entry['Subnet'])):
                    raise ValueError('Database subnet overlaps an existing network')
        checkpoint = ROOT/'deployment-state'/('before-projects-'+str(int(time.time())))
        checkpoint.mkdir(mode=0o700)
        for name in ['compose.yaml','network-guard.sh','backup.py']:
            shutil.copy2(policy/name, checkpoint/name)
        # Keep all recovery material root-private; app gets only its own login.
        db.mkdir(mode=0o700)
        for name in ['data','secrets','web']:
            (db/name).mkdir(mode=0o700)
        os.chown(db/'data',999,999)
        os.chown(db/'web',10002,10002)
        master, app_password = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        write_private(db/'secrets/postgres-password',master.encode(),999)
        config = {'host':'projects-db','port':5432,'dbname':'personal_projects','user':'projects_app',
                  'password':app_password,'connect_timeout':5,'options':'-c statement_timeout=15000 -c lock_timeout=5000'}
        write_private(db/'web/database.json',json.dumps(config).encode(),10002)
        # Parent private directory protects the read-only runtime credential mount.
        service_token = secrets.token_urlsafe(48).encode()
        write_private(ROOT/'runtime-auth/projects-token',service_token,10001)
        (ROOT/'runtime-auth/projects-token').chmod(0o400)
        write_private(db/'web/service-token',service_token,10002)
        (db/'web/service-token').chmod(0o400)
        for name in ['projects-db.compose.yaml']:
            shutil.copyfile(trusted(source/name),policy/name)
            (policy/name).chmod(0o600)
        run(['docker','compose','--project-name','personal-agent-projects','-f',str(policy/'projects-db.compose.yaml'),'up','-d'],timeout=300)
        for _ in range(60):
            state = json.loads(run(['docker','inspect',DB_CONTAINER]).stdout)[0]
            if state['State'].get('Health',{}).get('Status') == 'healthy':
                break
            time.sleep(2)
        else:
            raise RuntimeError('Database did not become healthy; checkpoint retained')
        # SQL is stdin only; secrets never appear in argv, env, logs or output.
        sql = f"CREATE ROLE projects_app LOGIN PASSWORD '{app_password}';\nALTER DATABASE personal_projects OWNER TO projects_app;\nREVOKE ALL ON DATABASE personal_projects FROM PUBLIC;\n"
        run(['docker','exec','-i','-u','999',DB_CONTAINER,'psql','-v','ON_ERROR_STOP=1','-U','postgres','-d','personal_projects'],input=sql.encode())
        patched = patch_compose((policy/'compose.yaml').read_text())
        write_private(checkpoint/'compose.projects.yaml',patched.encode())
        run(['docker','compose','--project-directory',str((ROOT/'current').resolve()),'-f',str(checkpoint/'compose.projects.yaml'),'config','--quiet'])
        # These exact reviewed policy bytes replace only project-owned files.
        for name in ['network-guard.sh','backup.py']:
            candidate = trusted(source/name)
            temp = policy/(name+'.projects-new')
            write_private(temp,candidate.read_bytes())
            os.replace(temp,policy/name)
        temp = policy/'compose.yaml.projects-new'
        write_private(temp,patched.encode())
        os.replace(temp,policy/'compose.yaml')
        run(['/bin/sh',str(policy/'network-guard.sh')],timeout=30)
        print(json.dumps({'projects_database':'healthy','ports_published':False,
                          'existing_services_restarted':False,'checkpoint':checkpoint.name,
                          'next':'restricted application rollout'}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Secret-bearing subprocess errors must not be rendered.
        print(json.dumps({'provisioning':'stopped','error_type':type(exc).__name__}))
        raise SystemExit(1)
