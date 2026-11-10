"""Owns only the Hermes child in this container; never accesses Docker or the host."""
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

from workspace_config import install_identity, runtime_config, seed_skills, sync_approved_skills, _write

WORKSPACE=Path('/workspace')
HOME=WORKSPACE/'.hermes'
TOKEN=Path('/run/runtime-auth/token').read_text().strip()
API_TOKEN=Path('/run/runtime-auth/hermes-token').read_text().strip()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
stopping=False
child=None

def config():
    req=urllib.request.Request('http://web:8080/internal/runtime-config',headers={'Authorization':'Bearer '+TOKEN})
    with opener.open(req, timeout=8) as r:
        return json.load(r)

def stop_child():
    global child
    if child is None or child.poll() is not None:
        return
    import psutil
    try:
        processes=psutil.Process(child.pid).children(recursive=True)
        processes.append(psutil.Process(child.pid))
        for p in processes:
            try: p.terminate()
            except psutil.Error: pass
        _,alive=psutil.wait_procs(processes,timeout=10)
        for p in alive:
            try: p.kill()
            except psutil.Error: pass
    except psutil.Error:
        pass
    child.wait(timeout=15)

def terminate(*_):
    global stopping
    stopping=True

signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
WORKSPACE.mkdir(exist_ok=True)
HOME.mkdir(exist_ok=True,mode=0o700)
(WORKSPACE/'home').mkdir(exist_ok=True,mode=0o700)
(WORKSPACE/'projects').mkdir(exist_ok=True)
PROJECT=WORKSPACE/'projects'/'personal-agent'
PROJECT.mkdir(exist_ok=True)
for profile in ('default', 'carpediev'):
    profile_home=HOME if profile=='default' else HOME/'profiles'/profile
    install_identity(profile_home, profile)
    seed_skills(profile_home, Path('/opt/runtime/skills'))
revision=None
backoff=2
while not stopping:
    try:
        c=config()
        imports_changed=0
        if 'approved_skills' in c:
            try:
                imports_changed=sync_approved_skills(HOME, c['approved_skills'])
            except Exception:
                # Never keep a capable child running after failed approval reconciliation.
                stop_child()
                raise
        if child is None or child.poll() is not None or c['revision']!=revision or imports_changed:
            stop_child()
            p=HOME/'config.yaml'
            owner_config=None
            if p.exists():
                from ruamel.yaml import YAML
                owner_config=YAML(typ='safe', pure=True).load(p.read_text())
            conf=runtime_config(c['model'], owner_config=owner_config)
            _write(p, json.dumps(conf,indent=2), replace=p.exists())
            env={**os.environ,**c.get('variables',{})}
            env.update({'HERMES_HOME':str(HOME),'HOME':'/workspace/home','OPENAI_API_KEY':TOKEN,
                'OPENAI_BASE_URL':'http://web:8080/provider/v1','API_SERVER_ENABLED':'true',
                'API_SERVER_HOST':'0.0.0.0','API_SERVER_PORT':'8642','API_SERVER_KEY':API_TOKEN,
                'HERMES_REDACT_SECRETS':'true','TERMINAL_CWD':str(PROJECT),
                'NO_PROXY':'web,hermes,localhost,127.0.0.1','no_proxy':'web,hermes,localhost,127.0.0.1',
                'UV_CACHE_DIR':'/workspace/home/.cache/uv','XDG_CACHE_HOME':'/workspace/home/.cache',
                'PYTHONDONTWRITEBYTECODE':'1'})
            env.pop('HERMES_YOLO_MODE', None)
            print('Starting isolated Hermes runtime; configuration revision',c['revision'],flush=True)
            child=subprocess.Popen(['/opt/hermes/.venv/bin/hermes','gateway','run'],env=env,cwd=PROJECT,start_new_session=True)
            revision=c['revision']
            time.sleep(backoff)
            backoff=min(backoff*2,60) if child.poll() is not None else 2
        for _ in range(5):
            if stopping: break
            time.sleep(1)
    except Exception as e:
        print('Runtime waiting for control service:',type(e).__name__,flush=True)
        time.sleep(5)
stop_child()
