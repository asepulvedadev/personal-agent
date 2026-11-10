#!/usr/bin/python3
"""Administrator-only installation of reviewed personal-agent host policy."""
import argparse
import ast
import base64
import binascii
import json
import re
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile

ACCOUNT = 'personal-agent-deploy'
PROJECT = Path('/srv/personal-agent')
POLICY = PROJECT / 'deploy-policy'
PUBLIC_GATE = Path('/usr/local/libexec/personal-agent-ssh-gate.py')
KEYS = Path('/etc/ssh/personal-agent-deploy.keys')
SSH_CONFIG = Path('/etc/ssh/personal-agent-deploy.conf')
SSH_MAIN = Path('/etc/ssh/sshd_config')
SSH_MARKER = b'# personal-agent restricted deployment\nMatch all\nInclude /etc/ssh/personal-agent-deploy.conf\n'
SUDOERS = Path('/etc/sudoers.d/personal-agent-deploy')
HOME = Path('/var/lib/personal-agent-deploy')
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}
SOURCE_FILES = ('compose.yaml', 'deploy/release_gate.py', 'deploy/ssh_gate.py',
                'deploy/service_gate.py', 'deploy/backup.py', 'deploy/network-guard.sh',
                'deploy/edge-link.py')
UNITS = {
    'personal-agent.service': '''[Unit]
Description=Isolated personal Hermes workspace
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor=/srv/personal-agent/workspace
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/srv/personal-agent/deploy-policy
ExecStart=/usr/bin/python3 -I /srv/personal-agent/deploy-policy/service_gate.py start
ExecStop=/usr/bin/python3 -I /srv/personal-agent/deploy-policy/service_gate.py stop
TimeoutStartSec=600
TimeoutStopSec=190
[Install]
WantedBy=multi-user.target
''',
    'personal-agent-backup.service': '''[Unit]
Description=Private personal workspace backup
After=personal-agent.service
Requires=personal-agent.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /srv/personal-agent/deploy-policy/backup.py
Nice=10
IOSchedulingClass=idle
MemoryMax=384M
CPUQuota=25%
TimeoutStartSec=900
''',
    'personal-agent-backup.timer': '''[Unit]
Description=Daily backup of the isolated personal workspace
[Timer]
OnCalendar=*-*-* 04:15:00 America/Bogota
RandomizedDelaySec=5m
Persistent=true
[Install]
WantedBy=timers.target
''',
    'personal-agent-edge-link.service': '''[Unit]
Description=Maintain only the personal workspace frontend attachment
After=personal-agent.service
[Service]
ExecStart=/usr/bin/python3 -I /srv/personal-agent/deploy-policy/edge-link.py
Restart=on-failure
RestartSec=10
MemoryMax=96M
CPUQuota=5%
[Install]
WantedBy=multi-user.target
''',
    'personal-agent-firewall.service': '''[Unit]
Description=Personal workspace network guards before Docker startup
Before=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh /srv/personal-agent/deploy-policy/network-guard.sh
[Install]
WantedBy=multi-user.target
''',
}
SSH_TEXT = f'''Match User {ACCOUNT}
    AuthorizedKeysFile {KEYS}
    ForceCommand /usr/bin/python3 -I {PUBLIC_GATE}
    AuthenticationMethods publickey
    AuthorizedKeysCommand none
    PubkeyAcceptedAlgorithms ssh-ed25519
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    DisableForwarding yes
    AllowTcpForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitUserRC no
Match all
'''
SUDO_TEXT = f'{ACCOUNT} ALL=(root) NOPASSWD: /usr/bin/python3 -I {POLICY}/release_gate.py *\n'


def effective_ssh_expectations():
    return '\n'.join([
        f'authorizedkeysfile {KEYS}', f'forcecommand /usr/bin/python3 -I {PUBLIC_GATE}',
        'authenticationmethods publickey', 'authorizedkeyscommand none',
        'pubkeyacceptedalgorithms ssh-ed25519', 'passwordauthentication no',
        'kbdinteractiveauthentication no', 'permittty no', 'disableforwarding yes',
        'allowtcpforwarding no', 'allowagentforwarding no', 'x11forwarding no',
        'permittunnel no', 'permituserrc no',
    ])


class Bootstrap:
    def __init__(self, source, host=Path('/'), run=subprocess.run, lookup=pwd.getpwnam, owner=0):
        self.source, self.host, self.run, self.lookup, self.owner = Path(source), host, run, lookup, owner

    def path(self, absolute):
        return self.host / str(absolute).lstrip('/')

    def command(self, argv):
        return self.run(argv, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, env=ENV, timeout=60, cwd=self.host)

    def safe(self, path, *, directory=False, mode=None):
        info = path.lstat()
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(info.st_mode) or info.st_uid != self.owner or info.st_mode & 0o022:
            raise ValueError('Unsafe administrator-owned path')
        if mode is not None and stat.S_IMODE(info.st_mode) != mode:
            raise ValueError('Unexpected private path permissions')

    def directory(self, path, mode=0o755):
        if not path.is_relative_to(self.host):
            raise ValueError('Installation path outside host root')
        if path != self.host:
            self.directory(path.parent)
        if path.exists() or path.is_symlink():
            self.safe(path, directory=True)
        else:
            path.mkdir(mode=mode)
            path.chmod(mode)
        self.safe(path, directory=True)

    def write(self, path, content, mode=0o644, metadata=None):
        self.directory(path.parent)
        if path.exists() or path.is_symlink():
            self.safe(path)
        descriptor, temporary = tempfile.mkstemp(prefix='.personal-agent-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                if metadata is not None:
                    os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
                os.fchmod(stream.fileno(), mode)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def account(self, required=False):
        try:
            entry = self.lookup(ACCOUNT)
        except KeyError:
            if required:
                raise ValueError('Deployment account missing')
            return None
        if (not 100 <= entry.pw_uid < 1000 or entry.pw_gid < 100 or entry.pw_name != ACCOUNT or entry.pw_dir != str(HOME)
                or entry.pw_shell != '/bin/sh'):
            raise ValueError('Unexpected deployment account')
        groups = self.command(['/usr/bin/id', '-nG', ACCOUNT]).stdout.split()
        if groups != [ACCOUNT]:
            raise ValueError('Deployment account has unexpected groups')
        home = self.path(HOME)
        if home.exists() or home.is_symlink():
            self.safe(home, directory=True, mode=0o755)
            if (home / '.ssh').exists() or (home / '.ssh').is_symlink():
                raise ValueError('Unexpected home SSH authorization')
        return entry

    def preflight(self, initial=True):
        root = self.path(PROJECT)
        self.safe(root, directory=True, mode=0o700)
        self.safe(root / 'releases', directory=True)
        current = root / 'current'
        if not current.is_symlink() or current.lstat().st_uid != self.owner:
            raise ValueError('Current release pointer is not trusted')
        target = current.readlink()
        if initial and target not in (Path('releases/initial'), PROJECT / 'releases/initial'):
            raise ValueError('Bootstrap requires the preserved initial release')
        label = target.name
        if label != 'initial' and not re.fullmatch('[0-9a-f]{40}', label):
            raise ValueError('Invalid accepted release identity')
        canonical = root / 'releases' / label
        if target not in (Path('releases') / label, PROJECT / 'releases' / label):
            raise ValueError('Indirect release pointer')
        self.safe(canonical, directory=True)
        if current.resolve(strict=True) != canonical:
            raise ValueError('Indirect release pointer')
        if label != 'initial':
            record = root / 'deployment-state' / f'{label}.accepted.json'
            override = root / 'deployment-state' / f'{label}.images.json'
            for path in (record, override):
                self.safe(path)
                if path.stat().st_size > 4096:
                    raise ValueError('Oversized release policy')
            if json.loads(record.read_text()) != {'release': label}:
                raise ValueError('Invalid accepted release marker')
            expected = {'services': {s: {'image': f'personal-agent-{s}:{label}'} for s in ('web', 'hermes', 'egress')}}
            if json.loads(override.read_text()) != expected:
                raise ValueError('Unexpected accepted image override')
        for parent in ('etc/systemd/system', 'run/systemd/system', 'usr/lib/systemd/system', 'lib/systemd/system'):
            for unit in UNITS:
                dropin = self.host / parent / (unit + '.d')
                if dropin.is_symlink() or (dropin.exists() and any(dropin.iterdir())):
                    raise ValueError('Unreviewed personal-agent systemd drop-in')
        listed = self.command(['/usr/bin/systemctl', 'list-unit-files', 'personal-agent*', '--no-legend', '--no-pager']).stdout
        for line in listed.splitlines():
            if line.split()[0] not in {*UNITS, 'personal-agent-workspace.mount'}:
                raise ValueError('Unexpected personal-agent unit requires owner review')
        self.ssh_main_content()
        self.account()

    def ssh_main_content(self):
        main = self.path(SSH_MAIN)
        self.safe(main)
        content = main.read_bytes()
        marker = b'# personal-agent restricted deployment'
        if marker in content:
            if content.count(marker) != 1 or not content.endswith(SSH_MARKER):
                raise ValueError('Unexpected or conflicting SSH marker')
            prefix = content[:-len(SSH_MARKER)]
            if b'/etc/ssh/personal-agent-deploy.conf' in prefix:
                raise ValueError('Conflicting project SSH include')
            return content, content
        if b'/etc/ssh/personal-agent-deploy.conf' in content:
            raise ValueError('Conflicting project SSH include')
        legacy = self.path(Path('/etc/ssh/sshd_config.d/00-personal-agent-deploy.conf'))
        if legacy.exists() or legacy.is_symlink():
            raise ValueError('Legacy early project Match fragment requires owner review')
        return content, content + (b'' if content.endswith(b'\n') else b'\n') + SSH_MARKER

    def inputs(self):
        result = {}
        for name in SOURCE_FILES:
            path = self.source / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
                raise ValueError('Unsafe or oversized reviewed source input')
            # Reject symlinked source ancestors, not only the final component.
            if path.resolve() != path.absolute():
                raise ValueError('Reviewed input path must not use symlinks')
            result[name] = path.read_bytes()
            if path.suffix == '.py':
                ast.parse(result[name], filename=name)
        return result

    def validate_configuration(self, root_before=None):
        self.command(['/usr/sbin/visudo', '-cf', str(SUDOERS)])
        self.command(['/usr/sbin/visudo', '-c'])
        self.command(['/usr/sbin/sshd', '-t'])
        effective = self.command(['/usr/sbin/sshd', '-T', '-C', f'user={ACCOUNT},host=localhost,addr=127.0.0.1']).stdout
        lines = set(effective.lower().splitlines())
        if not set(effective_ssh_expectations().lower().splitlines()) <= lines:
            raise ValueError('Effective SSH restrictions did not match')
        root_effective = self.command(['/usr/sbin/sshd', '-T', '-C', 'user=root,host=localhost,addr=127.0.0.1']).stdout
        if root_before is not None and sorted(root_effective.splitlines()) != sorted(root_before.splitlines()):
            raise ValueError('Existing root SSH configuration changed')

    def check(self):
        self.preflight(initial=False)
        current, appended = self.ssh_main_content()
        if current != appended:
            raise ValueError('Trailing project SSH include is missing')
        self.account(required=True)
        self.safe(self.path(HOME), directory=True, mode=0o755)
        self.safe(self.path(POLICY), directory=True, mode=0o700)
        self.safe(self.path(PROJECT / 'deployment-state'), directory=True, mode=0o700)
        for name in SOURCE_FILES:
            self.safe(self.path(POLICY / Path(name).name), mode=0o600)
        self.safe(self.path(PUBLIC_GATE), mode=0o755)
        self.safe(self.path(KEYS), mode=0o644)
        for path, text in [(SSH_CONFIG, SSH_TEXT), (SUDOERS, SUDO_TEXT), *[(Path('/etc/systemd/system') / name, text) for name, text in UNITS.items()]]:
            self.safe(self.path(path), mode=0o440 if path == SUDOERS else 0o644)
            if self.path(path).read_text() != text:
                raise ValueError('Installed host configuration differs from reviewed policy')
        self.validate_configuration()
        for name in UNITS:
            effective = self.command(['/usr/bin/systemctl', 'show', '--property=FragmentPath,DropInPaths', name]).stdout.splitlines()
            if set(effective) != {f'FragmentPath=/etc/systemd/system/{name}', 'DropInPaths='}:
                raise ValueError('Unexpected effective personal-agent unit source')

    def install(self):
        self.preflight()
        sources = self.inputs()
        main_before, main_after = self.ssh_main_content()
        main_metadata = self.path(SSH_MAIN).stat()
        root_before = self.command(['/usr/sbin/sshd', '-T', '-C', 'user=root,host=localhost,addr=127.0.0.1']).stdout
        keyfile = self.path(KEYS)
        if keyfile.exists() or keyfile.is_symlink():
            self.safe(keyfile)
            if keyfile.stat().st_size:
                raise ValueError('Disable deployment keys before changing bootstrap policy')
        for directory in (POLICY, PROJECT / 'deployment-state'):
            path = self.path(directory)
            self.directory(path, mode=0o700)
            self.safe(path, directory=True, mode=0o700)
        if self.account() is None:
            self.command(['/usr/sbin/useradd', '--system', '--user-group', '--home-dir', str(HOME), '--shell', '/bin/sh', '--no-create-home', ACCOUNT])
        self.account(required=True)
        self.command(['/usr/sbin/usermod', '--lock', ACCOUNT])
        self.directory(self.path(HOME), mode=0o755)
        self.write(keyfile, b'')
        for name, content in sources.items():
            self.write(self.path(POLICY / Path(name).name), content, mode=0o600)
        self.write(self.path(PUBLIC_GATE), sources['deploy/ssh_gate.py'], mode=0o755)
        backup = self.path(PROJECT / 'deployment-state/sshd_config.before-personal-agent')
        if not backup.exists():
            self.write(backup, main_before, mode=0o600)
        else:
            self.safe(backup, mode=0o600)
        # Restore exact prior SSH bytes/metadata and fragments before any reload on failure.
        for path in (SSH_CONFIG, SUDOERS):
            if self.path(path).exists() or self.path(path).is_symlink():
                self.safe(self.path(path))
        originals = {p: self.path(p).read_bytes() if self.path(p).exists() else None for p in (SSH_CONFIG, SUDOERS)}
        try:
            self.write(self.path(SSH_CONFIG), SSH_TEXT.encode())
            self.write(self.path(SUDOERS), SUDO_TEXT.encode(), mode=0o440)
            self.write(self.path(SSH_MAIN), main_after, mode=stat.S_IMODE(main_metadata.st_mode), metadata=main_metadata)
            self.validate_configuration(root_before=root_before)
        except Exception:
            self.write(self.path(SSH_MAIN), main_before, mode=stat.S_IMODE(main_metadata.st_mode), metadata=main_metadata)
            for path, content in originals.items():
                if content is None:
                    self.path(path).unlink(missing_ok=True)
                else:
                    self.write(self.path(path), content, mode=0o440 if path == SUDOERS else 0o644)
            raise
        for name, text in UNITS.items():
            self.write(self.path(Path('/etc/systemd/system') / name), text.encode())
        self.command(['/usr/bin/systemctl', 'daemon-reload'])
        self.check()
        self.command(['/usr/bin/systemctl', 'reload', 'ssh.service'])
        # Do not restart the application, Docker, workspace mount, or backup timer.
        for name in ('personal-agent-firewall.service', 'personal-agent-edge-link.service'):
            self.command(['/usr/bin/systemctl', 'try-restart', name])

    def enable_key(self, public):
        self.check()
        public = Path(public)
        if public.suffix != '.pub' or public.is_symlink() or not public.is_file() or public.stat().st_size > 1024:
            raise ValueError('Expected a small public .pub key file')
        text = public.read_text()
        if len(text.splitlines()) != 1:
            raise ValueError('Expected exactly one public key line')
        fields = text.strip().split()
        if len(fields) < 2 or fields[0] != 'ssh-ed25519':
            raise ValueError('Expected an Ed25519 public key, never a private key')
        try:
            blob = base64.b64decode(fields[1], validate=True)
        except binascii.Error as error:
            raise ValueError('Invalid public key encoding') from error
        if len(blob) != 51 or blob[:19] != b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20':
            raise ValueError('Invalid Ed25519 public key structure')
        line = f'restrict,command="/usr/bin/python3 -I {PUBLIC_GATE}" ssh-ed25519 {fields[1]}\n'
        self.write(self.path(KEYS), line.encode())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Absolute path to the reviewed local source directory')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--check', action='store_true', help='Validate installed structural readiness without writing')
    modes.add_argument('--enable-key', type=Path, metavar='PUBLIC.pub', help='Separately enable one public key after readiness checks')
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or not args.source.is_absolute():
        parser.error('Root and an absolute reviewed source path are required')
    os.umask(0o077)
    try:
        bootstrap = Bootstrap(args.source)
        if args.check:
            bootstrap.check()
        elif args.enable_key:
            bootstrap.enable_key(args.enable_key)
        else:
            bootstrap.install()
        print('Personal-agent policy operation completed; application rollout not performed.')
        return 0
    except Exception as error:
        print('Personal-agent policy operation failed: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
