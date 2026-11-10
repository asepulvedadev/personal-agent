"""Root-owned, project-only backup; database reads run as the sandbox user."""
import datetime
import io
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path('/srv/personal-agent')
os.umask(0o077)
resource.setrlimit(resource.RLIMIT_FSIZE,(2*1024**3,2*1024**3))
AGENT_SNAPSHOT=r'''
import os,sys,sqlite3,tarfile,tempfile,hashlib
from pathlib import Path
from urllib.parse import quote
root=Path('/workspace')
with tempfile.TemporaryDirectory(prefix='backup-',dir='/tmp') as tmp:
 with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz',dereference=False) as archive:
  for parent,dirs,files in os.walk(root,followlinks=False):
   for name in dirs+files:
    p=Path(parent)/name
    rel=str(p.relative_to(root))
    if p.is_symlink() or p.is_dir():
     archive.add(p,arcname=rel,recursive=False)
    elif p.is_file() and p.suffix=='.db' and p.stat().st_size:
     resolved=p.resolve()
     if not resolved.is_relative_to(root): raise RuntimeError('Database path outside workspace')
     snapshot=Path(tmp)/hashlib.sha256(rel.encode()).hexdigest()
     with sqlite3.connect('file:'+quote(str(resolved))+'?mode=ro',uri=True,timeout=30) as src:
      with sqlite3.connect(snapshot) as dst:
       src.backup(dst)
       if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('Database integrity check failed')
     archive.add(snapshot,arcname=rel,recursive=False)
    elif p.is_file(): archive.add(p,arcname=rel,recursive=False)
'''
with tempfile.TemporaryDirectory(prefix='.backup-',dir=ROOT/'backups') as tmp:
    workspace=Path(tmp)/'workspace.tar.gz'
    with workspace.open('wb') as out:
        subprocess.run(['docker','exec','-i','personal-agent-hermes-1','python','-'],input=AGENT_SNAPSHOT.encode(),stdout=out,check=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target=ROOT/'backups'/('personal-agent-'+stamp+'.tar')
    partial=target.with_suffix('.partial')
    with tarfile.open(partial,'w',dereference=False) as archive:
        archive.add(workspace,arcname='workspace.tar.gz')
        archive.add(ROOT/'control',arcname='control')
        archive.add(ROOT/'runtime-auth',arcname='runtime-auth')
        archive.add(ROOT/'portal-bootstrap',arcname='portal-bootstrap')
    os.replace(partial,target)
    for old in sorted((ROOT/'backups').glob('personal-agent-*.tar'))[:-3]: old.unlink()
    print(json.dumps({'backup':target.name,'bytes':target.stat().st_size,'database_integrity':'verified inside sandbox','retention':3}))
