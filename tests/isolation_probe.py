"""Run inside the Hermes container, not on the host. No credentials are printed."""
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
assert os.getuid()==10001
status=Path('/proc/self/status').read_text()
assert re.search(r'CapEff:\s+0+\s',status)
assert re.search(r'NoNewPrivs:\s+1',status)
for name in ['/etc/isolation-denied','/opt/isolation-denied']:
    try: Path(name).write_text('must not be written')
    except OSError: pass
    else: raise AssertionError('Writable outside the workspace: '+name)
for name in ['/root/INFRA.md','/srv/personal-agent/control/auth.json','/var/run/docker.sock']:
    try: Path(name).read_bytes()
    except OSError: pass
    else: raise AssertionError('Host/control resource accessible: '+name)
p=Path('/workspace/projects/isolation-probe.txt')
p.write_text('allowed');assert p.read_text()=='allowed';p.unlink()
link=Path('/workspace/projects/isolation-link')
try:
    link.symlink_to('/etc',target_is_directory=True)
    try: (link/'isolation-denied').write_text('must not be written')
    except OSError: pass
    else: raise AssertionError('Symlink escaped the read-only boundary')
finally:
    link.unlink(missing_ok=True)
for host,port in [('172.30.40.1',22),('172.16.2.1',22),('172.16.3.1',8000),('2.24.115.58',22),('1.1.1.1',443)]:
    try: s=socket.create_connection((host,port),timeout=2)
    except OSError: continue
    else: s.close();raise AssertionError('Direct TCP escape: '+host)
proxy=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://egress:3128','https':'http://egress:3128'}))
for url in ['http://169.254.169.254/latest/meta-data/','http://172.16.3.1:8000/']:
    try: proxy.open(url,timeout=5)
    except urllib.error.HTTPError as e: assert e.code==403
    else: raise AssertionError('Proxy allowed a forbidden destination')
try: proxy.open('https://2.24.115.58/',timeout=5)
except urllib.error.URLError as e: assert '403' in str(e.reason)
else: raise AssertionError('Proxy allowed the VPS public address')
try: proxy.open('https://api.openai.com/v1/models',timeout=15)
except urllib.error.HTTPError as e: assert e.code==401
else: raise AssertionError('Unexpected unauthenticated OpenAI response')
print('PASS: UID, capabilities, no-new-privileges, workspace writes, read-only root, symlink boundary, host resources, direct TCP isolation, private/metadata proxy denial, public OpenAI reachability.')
