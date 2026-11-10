#!/usr/bin/python3
"""Root-owned systemd wrapper; never load configuration from a source release."""
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path('/srv/personal-agent')
OWNER = 0
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}


def trusted(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != OWNER or info.st_mode & 0o022:
        raise ValueError('Unsafe policy path')


def selection(root):
    trusted(root, directory=True)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError('Project root must remain private')
    for directory in ('releases', 'deploy-policy', 'deployment-state'):
        trusted(root / directory, directory=True)
        if directory != 'releases' and stat.S_IMODE((root / directory).stat().st_mode) != 0o700:
            raise ValueError('Policy and deployment state must remain private')
    link = root / 'current'
    if not link.is_symlink() or link.lstat().st_uid != OWNER:
        raise ValueError('Current must be an administrator-owned symlink')
    pointer = link.readlink()
    label = pointer.name
    if not (label == 'initial' or re.fullmatch('[0-9a-f]{40}', label)):
        raise ValueError('Invalid current release')
    source = root / 'releases' / label
    if pointer not in (Path('releases') / label, source) or link.resolve(strict=True) != source:
        raise ValueError('Current must point directly to a release')
    trusted(source, directory=True)
    policy = root / 'deploy-policy'
    trusted(policy / 'compose.yaml')
    argv = ['/usr/bin/docker', 'compose', '--project-name', 'personal-agent',
            '--project-directory', str(source), '--env-file', '/dev/null',
            '-f', str(policy / 'compose.yaml')]
    if label != 'initial':
        record = root / 'deployment-state' / f'{label}.accepted.json'
        override = root / 'deployment-state' / f'{label}.images.json'
        for path in (record, override):
            if not path.exists():
                raise ValueError('Release has not been accepted')
            trusted(path)
            if path.stat().st_size > 4096:
                raise ValueError('Oversized release policy')
        if json.loads(record.read_text()) != {'release': label}:
            raise ValueError('Acceptance record mismatch')
        expected = {'services': {s: {'image': f'personal-agent-{s}:{label}'} for s in ('web', 'hermes', 'egress')}}
        if json.loads(override.read_text()) != expected:
            raise ValueError('Override must contain only accepted image tags')
        argv += ['-f', str(override)]
    return argv


def operate(action, root=ROOT, run=subprocess.run):
    if action not in ('start', 'stop'):
        raise ValueError('Unsupported service operation')
    argv = selection(root)
    descriptor = os.open(root / 'deployment-state/deployment.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Re-select only after holding the same lock as the release gate.
        argv = selection(root)
        options = dict(check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=ENV, cwd=root / 'deploy-policy', timeout=180)
        if action == 'stop':
            run([*argv, 'stop'], **options)
            return
        trusted(root / 'deploy-policy/network-guard.sh')
        run([*argv, 'create', '--no-build', '--pull', 'never'], **options)
        run(['/bin/sh', str(root / 'deploy-policy/network-guard.sh')], **options)
        run([*argv, 'up', '-d', '--no-build', '--pull', 'never'], **options)


def main(argv):
    if os.geteuid() != 0 or argv not in (['start'], ['stop']):
        return 2
    os.umask(0o077)
    try:
        operate(argv[0])
        return 0
    except Exception as error:
        print('Service policy failed: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
