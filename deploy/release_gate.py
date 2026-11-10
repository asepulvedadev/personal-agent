#!/usr/bin/python3
"""Root-managed project deployment policy; stdlib only, no checkout host hooks."""
import fcntl
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path('/srv/personal-agent')
REPO = 'asepulvedadev/personal-agent'
MAIN_URL = f'https://api.github.com/repos/{REPO}/commits/main'
MAX_DOWNLOAD = 32 * 1024**2
MAX_EXPANDED = 128 * 1024**2
MAX_FILE = 16 * 1024**2
MAX_MEMBERS = 5000
SERVICES = ('web', 'hermes', 'egress')
PRIVATE = {'.git', '.hg', '.svn', 'private', 'workspace', 'control', 'runtime-auth',
           'portal-bootstrap', 'quarantine', '__pycache__', '.pytest_cache', '.cache',
           'node_modules', '.venv', 'venv', 'backups', 'deploy-policy'}
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}


def valid_sha(value):
    if not re.fullmatch('[0-9a-f]{40}', value):
        raise ValueError('Invalid commit identity')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Source redirects are forbidden')


def download(url, limit):
    if url != MAIN_URL and not re.fullmatch(
        rf'https://codeload\.github\.com/{REPO}/tar\.gz/[0-9a-f]{{40}}', url
    ):
        raise ValueError('Source URL outside deployment policy')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': 'personal-agent-release', 'Accept': 'application/vnd.github+json'})
    with opener.open(request, timeout=30) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Source download exceeds limit')
        return body


def extract_source(body, destination, sha):
    """Validate every member before materializing any source file, never extractall."""
    valid_sha(sha)
    if len(body) > MAX_DOWNLOAD:
        raise ValueError('Source download exceeds limit')
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
        expanded = compressed.read(MAX_EXPANDED + 1)
    if len(expanded) > MAX_EXPANDED:
        raise ValueError('Expanded archive exceeds limit')
    prefix = f'personal-agent-{sha}'
    with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
        entries, names, total = [], set(), 0
        for member in archive:
            if len(entries) >= MAX_MEMBERS:
                raise ValueError('Too many source members')
            raw = member.name.rstrip('/')
            parts = raw.split('/')
            if (not parts or parts[0] != prefix or any(p in ('', '.', '..') for p in parts)
                    or '\\' in raw or any(ord(c) < 32 for c in raw)
                    or not (member.isfile() or member.isdir())):
                raise ValueError('Unsafe archive member')
            relative = PurePosixPath(*parts[1:])
            if any(p in PRIVATE or p.startswith('.env') for p in relative.parts):
                raise ValueError('Private or runtime source member')
            if relative in names or member.size < 0 or member.size > MAX_FILE:
                raise ValueError('Duplicate or oversized source member')
            total += member.size
            if total > MAX_EXPANDED:
                raise ValueError('Source files exceed limit')
            names.add(relative)
            entries.append((member, relative))
        destination.mkdir(mode=0o755)
        for member, relative in entries:
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with archive.extractfile(member) as source, target.open('xb') as out:
                    shutil.copyfileobj(source, out)
                target.chmod(0o644)


class ReleaseGate:
    def __init__(self, root=ROOT, run=subprocess.run, fetch=download):
        self.root, self.run, self.fetch = root, run, fetch
        self.policy = root / 'deploy-policy'
        self.state = root / 'deployment-state'
        self.state.mkdir(mode=0o700, exist_ok=True)

    def record(self, label):
        return self.state / f'{label}.accepted.json'

    def override(self, label):
        return self.state / f'{label}.images.json'

    def command(self, argv, timeout=600):
        return self.run(argv, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, timeout=timeout, env=ENV,
                        cwd=self.policy)

    def compose(self, source, label, *args, timeout=600):
        argv = ['/usr/bin/docker', 'compose', '--project-name', 'personal-agent',
                '--project-directory', str(source), '--env-file', '/dev/null',
                '-f', str(self.policy / 'compose.yaml')]
        if label != 'initial':
            argv += ['-f', str(self.override(label))]
        return self.command([*argv, *args], timeout=timeout)

    def current(self):
        target = (self.root / 'current').resolve(strict=True)
        if target.parent != self.root / 'releases' or not (target.name == 'initial' or re.fullmatch('[0-9a-f]{40}', target.name)):
            raise ValueError('Current release outside policy')
        return target

    def assert_main(self, sha):
        result = json.loads(self.fetch(MAIN_URL, 65536))
        if result.get('sha') != sha:
            raise ValueError('Deployment commit is not canonical current main')

    def evidence(self, action, label, outcome):
        # Do not include subprocess output, environment, URLs, workspace, or secrets.
        with (self.state / 'events.jsonl').open('a') as log:
            log.write(json.dumps({'time': int(time.time()), 'action': action,
                                  'release': label, 'outcome': outcome}) + '\n')

    def healthy(self, source, label):
        deadline = time.monotonic() + 300
        for attempt in range(60):
            try:
                raw = self.compose(source, label, 'ps', '--all', '--format', 'json', timeout=10).stdout
                raw = raw.strip()
                rows = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines()]
                found = {row['Service']: row for row in rows}
                if all(found.get(s, {}).get('State') == 'running' for s in SERVICES) and all(
                    found.get(s, {}).get('Health') == 'healthy' for s in ('web', 'hermes')
                ):
                    return True
            except (subprocess.SubprocessError, ValueError, KeyError):
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if attempt < 59:
                time.sleep(min(5, remaining))
        return False

    def activate(self, source, label):
        self.command(['/bin/sh', str(self.policy / 'network-guard.sh')], timeout=30)
        self.compose(source, label, 'up', '-d', '--no-build', '--pull', 'never', '--remove-orphans')
        if not self.healthy(source, label):
            raise RuntimeError('Release did not become healthy')

    def promote(self, label):
        temporary = self.root / '.current-next'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(f'releases/{label}')
        os.replace(temporary, self.root / 'current')

    def transition(self, source, action):
        previous = self.current()
        label = source.name
        if previous == source:
            if not self.healthy(source, label):
                raise RuntimeError('Current release unhealthy')
            return
        try:
            self.activate(source, label)
            if action == 'deploy':
                self.record(label).write_text(json.dumps({'release': label}))
            self.promote(label)
        except Exception:
            self.evidence(action, label, 'failed')
            # Exactly one recovery attempt. Preserve the original failing exit even if recovery succeeds.
            try:
                self.activate(previous, previous.name)
                self.promote(previous.name)
                self.evidence('recovery', previous.name, 'healthy')
            except Exception:
                self.evidence('recovery', previous.name, 'failed-owner-action-required')
            raise

    def deploy(self, sha):
        valid_sha(sha)
        self.assert_main(sha)
        source = self.root / 'releases' / sha
        if self.current() == source:
            if not self.healthy(source, sha):
                raise RuntimeError('Current release unhealthy')
            return
        if self.record(sha).is_file() and self.override(sha).is_file() and source.is_dir():
            self.transition(source, 'deploy')
            self.evidence('deploy', sha, 'healthy-retained-images')
            return
        if not source.exists():
            body = self.fetch(f'https://codeload.github.com/{REPO}/tar.gz/{sha}', MAX_DOWNLOAD)
            with tempfile.TemporaryDirectory(prefix='.candidate-', dir=self.root / 'releases') as temporary:
                stage = Path(temporary) / sha
                extract_source(body, stage, sha)
                os.replace(stage, source)
        images = {s: {'image': f'personal-agent-{s}:{sha}'} for s in SERVICES}
        self.override(sha).write_text(json.dumps({'services': images}))
        self.compose(source, sha, 'build', '--pull')
        self.command(['/usr/bin/python3', '-I', str(self.policy / 'backup.py')], timeout=360)
        self.assert_main(sha)
        self.transition(source, 'deploy')
        self.evidence('deploy', sha, 'healthy')

    def rollback(self, sha):
        valid_sha(sha)
        source = self.root / 'releases' / sha
        if not self.record(sha).is_file() or not self.override(sha).is_file() or not source.is_dir():
            raise ValueError('Rollback requires a previously accepted release')
        self.transition(source, 'rollback')
        self.evidence('rollback', sha, 'healthy')

    def status(self):
        source = self.current()
        return {'release': source.name, 'healthy': self.healthy(source, source.name)}


def main(argv):
    os.umask(0o077)
    if argv != ['status'] and not (len(argv) == 2 and argv[0] in ('deploy', 'rollback') and re.fullmatch('[0-9a-f]{40}', argv[1])):
        return 2
    gate = None
    try:
        gate = ReleaseGate()
        with (gate.state / 'deployment.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if argv == ['status']:
                print(json.dumps(gate.status()))
            else:
                getattr(gate, argv[0])(argv[1])
                print(json.dumps({'release': argv[1], 'outcome': 'healthy'}))
        return 0
    except Exception as error:
        if gate is not None:
            try:
                gate.evidence(argv[0], argv[1] if len(argv) == 2 else 'current',
                              'failed-' + type(error).__name__)
            except OSError:
                pass
        print(json.dumps({'outcome': 'failed', 'error_type': type(error).__name__}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
