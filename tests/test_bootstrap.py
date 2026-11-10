"""Bootstrap and service policy probes use isolated files and fake command runners."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

SHA = 'a' * 40
ACCOUNT = 'personal-agent-deploy'


@pytest.fixture(autouse=True)
def private_umask():
    previous = os.umask(0o077)
    yield
    os.umask(previous)


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'deploy' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if name == 'service_gate':
        module.OWNER = os.getuid()
    return module


def test_scheduled_backup_keeps_same_bounded_window_as_release_backup():
    definition = load('bootstrap').UNITS['personal-agent-backup.service']
    assert 'TimeoutStartSec=900\n' in definition
    assert 'MemoryMax=384M\n' in definition
    assert 'CPUQuota=25%\n' in definition


def project(tmp_path):
    root = tmp_path / 'srv/personal-agent'
    (root / 'releases/initial').mkdir(parents=True)
    root.chmod(0o700)
    (root / 'current').symlink_to('releases/initial')
    (root / 'deploy-policy').mkdir(mode=0o700)
    (root / 'deployment-state').mkdir(mode=0o700)
    (root / 'deploy-policy/compose.yaml').write_text('trusted')
    (root / 'deploy-policy/network-guard.sh').write_text('trusted')
    return root


def test_service_initial_uses_only_fixed_policy(tmp_path):
    gate = load('service_gate')
    root = project(tmp_path)
    calls = []
    gate.operate('start', root, lambda argv, **kw: calls.append((argv, kw)))
    assert len(calls) == 3
    assert calls[0][0][-4:] == ['create', '--no-build', '--pull', 'never']
    assert calls[1][0] == ['/bin/sh', str(root / 'deploy-policy/network-guard.sh')]
    assert calls[2][0][-5:] == ['up', '-d', '--no-build', '--pull', 'never']
    assert all('COMPOSE_FILE' not in kw['env'] for _, kw in calls)
    assert all(str(root / 'current') not in ' '.join(argv) for argv, _ in calls)


def test_service_rejects_unaccepted_and_hostile_override(tmp_path):
    gate = load('service_gate')
    root = project(tmp_path)
    (root / 'releases' / SHA).mkdir()
    (root / 'current').unlink()
    (root / 'current').symlink_to(f'releases/{SHA}')
    with pytest.raises(ValueError):
        gate.operate('stop', root, lambda *a, **kw: pytest.fail('executed'))
    (root / 'deployment-state' / f'{SHA}.accepted.json').write_text(json.dumps({'release': SHA}))
    override = root / 'deployment-state' / f'{SHA}.images.json'
    override.write_text(json.dumps({'services': {'web': {'volumes': ['/etc:/host']}}}))
    with pytest.raises(ValueError):
        gate.operate('start', root, lambda *a, **kw: pytest.fail('executed'))
    override.write_text(json.dumps({'services': {s: {'image': f'personal-agent-{s}:{SHA}'} for s in ['web','hermes','egress']}}))
    calls = []
    gate.operate('stop', root, lambda argv, **kw: calls.append(argv))
    assert len(calls) == 1 and calls[0][-1] == 'stop'
    assert str(override) in calls[0]


@pytest.mark.parametrize('target', ['../../escape', 'releases/alias'])
def test_service_rejects_pointer_outside_accepted_release(tmp_path, target):
    gate = load('service_gate')
    root = project(tmp_path)
    (tmp_path / 'escape').mkdir()
    (root / 'releases/alias').symlink_to('initial')
    (root / 'current').unlink()
    (root / 'current').symlink_to(target)
    with pytest.raises(ValueError):
        gate.operate('start', root, lambda *a, **kw: pytest.fail('executed'))


def setup(tmp_path):
    bootstrap = load('bootstrap')
    root = project(tmp_path)
    source = tmp_path / 'reviewed'
    for name in bootstrap.SOURCE_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('pass\n' if path.suffix == '.py' else 'reviewed local policy\n')
    account = SimpleNamespace(pw_uid=999, pw_gid=999, pw_name=ACCOUNT,
                              pw_dir='/var/lib/personal-agent-deploy', pw_shell='/bin/sh')
    main = tmp_path / 'etc/ssh/sshd_config'
    main.parent.mkdir(parents=True)
    main.write_bytes(b'Include /etc/ssh/sshd_config.d/*.conf\nUsePAM yes\nPermitRootLogin yes\n')
    main.chmod(0o640)
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        output = ACCOUNT if argv[:2] == ['/usr/bin/id', '-nG'] else ''
        if argv[:2] == ['/usr/sbin/sshd', '-T']:
            output = bootstrap.effective_ssh_expectations() if 'user=personal-agent-deploy' in argv[-1] else 'root-existing-configuration\n'
        if argv[:2] == ['/usr/bin/systemctl', 'show']:
            unit = argv[-1]
            output = f'FragmentPath=/etc/systemd/system/{unit}\nDropInPaths=\n'
        return subprocess.CompletedProcess(argv, 0, output, '')
    provision = bootstrap.Bootstrap(source, tmp_path, runner, lambda name: account, owner=os.getuid())
    return bootstrap, provision, root, source, account, calls


def test_bootstrap_preserves_private_root_and_freezes_only_own_hooks(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    provision.install()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for unit in bootstrap.UNITS:
        content = (tmp_path / 'etc/systemd/system' / unit).read_text()
        assert '/current/' not in content
    assert (tmp_path / 'usr/local/libexec/personal-agent-ssh-gate.py').stat().st_mode & 0o777 == 0o755
    assert (tmp_path / 'etc/ssh/personal-agent-deploy.keys').stat().st_size == 0
    assert ['/usr/bin/systemctl', 'daemon-reload'] in calls
    assert not any('restart' in c and 'personal-agent.service' in c for c in calls)
    assert not any(x in c for c in calls for x in ['down', 'prune', 'apt', 'apt-get'])
    assert all(c[-1] in ['personal-agent-edge-link.service', 'personal-agent-firewall.service'] for c in calls if 'restart' in c)
    assert not any(c[0] == '/usr/bin/docker' for c in calls)


@pytest.mark.parametrize('fault', ['root-mode', 'current-sha', 'source-symlink', 'groups', 'account-home', 'dropin'])
def test_bootstrap_fails_closed_before_mutation(tmp_path, fault):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    if fault == 'root-mode': root.chmod(0o755)
    if fault == 'current-sha':
        (root / 'releases' / SHA).mkdir()
        (root / 'current').unlink()
        (root / 'current').symlink_to(f'releases/{SHA}')
    if fault == 'source-symlink':
        (source / 'deploy/backup.py').unlink()
        (source / 'deploy/backup.py').symlink_to(source / 'deploy/ssh_gate.py')
    if fault == 'groups':
        old = provision.run
        provision.run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, 'docker', '') if argv[:2] == ['/usr/bin/id','-nG'] else old(argv, **kw)
    if fault == 'account-home': account.pw_dir = '/root'
    if fault == 'dropin':
        path = tmp_path / 'etc/systemd/system/personal-agent.service.d/override.conf'
        path.parent.mkdir(parents=True)
        path.write_text('ExecStart=/bad')
    with pytest.raises((ValueError, PermissionError)):
        provision.install()
    assert not (tmp_path / 'etc/ssh/personal-agent-deploy.conf').exists()
    assert not any('reload' in c or 'restart' in c for c in calls)


def test_check_is_read_only_and_requires_installed_policy(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        provision.check()
    provision.install()
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file() and not p.is_symlink()}
    calls.clear()
    provision.check()
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file() and not p.is_symlink()}
    assert not any('reload' in c or 'restart' in c or 'daemon-reload' in c for c in calls)


def test_key_enable_is_separate_and_rejects_private_key_input(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    provision.install()
    public = tmp_path / 'deployment.pub'
    blob = b'\0\0\0\x0bssh-ed25519\0\0\0\x20' + b'a'*32
    public.write_text('ssh-ed25519 ' + base64.b64encode(blob).decode() + ' owner\n')
    provision.enable_key(public)
    key = (tmp_path / 'etc/ssh/personal-agent-deploy.keys').read_text()
    assert key.startswith('restrict,command="/usr/bin/python3 -I /usr/local/libexec/personal-agent-ssh-gate.py" ssh-ed25519 ')
    private = tmp_path / 'secret'
    private.write_text('-----BEGIN OPENSSH PRIVATE KEY-----')
    with pytest.raises(ValueError):
        provision.enable_key(private)


def test_ssh_validation_failure_never_reloads(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    old = provision.run
    def reject(argv, **kwargs):
        if argv[:2] == ['/usr/sbin/sshd', '-t']:
            raise subprocess.CalledProcessError(1, argv)
        return old(argv, **kwargs)
    provision.run = reject
    with pytest.raises(subprocess.CalledProcessError):
        provision.install()
    assert not any('reload' in c or 'restart' in c for c in calls)
    assert not (tmp_path / 'etc/ssh/personal-agent-deploy.conf').exists()


def test_bootstrap_new_account_has_only_project_identity(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    created = [False]
    old = provision.run
    def lookup(name):
        if not created[0]:
            raise KeyError(name)
        return account
    def runner(argv, **kwargs):
        if argv[0] == '/usr/sbin/useradd':
            created[0] = True
        return old(argv, **kwargs)
    provision.lookup, provision.run = lookup, runner
    provision.install()
    added = next(c for c in calls if c[0] == '/usr/sbin/useradd')
    assert '--user-group' in added and '--no-create-home' in added
    assert added[added.index('--shell') + 1] == '/bin/sh'
    assert ['/usr/sbin/usermod', '--lock', ACCOUNT] in calls
    assert not any('-aG' in c or 'docker' in c for c in calls)


def test_bootstrap_unknown_own_unit_refused(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    old = provision.run
    provision.run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, 'personal-agent-extra.service enabled\n', '') if argv[:2] == ['/usr/bin/systemctl', 'list-unit-files'] else old(argv, **kw)
    with pytest.raises(ValueError):
        provision.install()
    assert not (tmp_path / 'etc/ssh/personal-agent-deploy.keys').exists()


def test_service_release_lock_prevents_overlap(tmp_path):
    import fcntl
    gate = load('service_gate')
    root = project(tmp_path)
    with (root / 'deployment-state/deployment.lock').open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            gate.operate('stop', root, lambda *a, **kw: pytest.fail('executed'))


def test_service_rejects_symlinked_lock(tmp_path):
    gate = load('service_gate')
    root = project(tmp_path)
    outside = tmp_path / 'unrelated'
    outside.write_text('unchanged')
    (root / 'deployment-state/deployment.lock').symlink_to(outside)
    with pytest.raises(OSError):
        gate.operate('stop', root, lambda *a, **kw: pytest.fail('executed'))
    assert outside.read_text() == 'unchanged'


def test_trailing_ssh_include_preserves_existing_bytes_owner_and_mode(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    main = tmp_path / 'etc/ssh/sshd_config'
    main.write_bytes(b'UsePAM yes\nPermitRootLogin yes')
    before = main.read_bytes()
    metadata = main.stat()
    provision.install()
    assert main.read_bytes() == before + b'\n' + bootstrap.SSH_MARKER
    assert stat.S_IMODE(main.stat().st_mode) == stat.S_IMODE(metadata.st_mode)
    assert (main.stat().st_uid, main.stat().st_gid) == (metadata.st_uid, metadata.st_gid)
    backup = root / 'deployment-state/sshd_config.before-personal-agent'
    assert backup.read_bytes() == before
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert not (tmp_path / 'etc/ssh/sshd_config.d/00-personal-agent-deploy.conf').exists()
    # Re-run with empty keys is idempotent; the first root-private backup is retained.
    provision.install()
    assert main.read_bytes().count(bootstrap.SSH_MARKER) == 1
    assert backup.read_bytes() == before


def test_failed_ssh_validation_restores_global_config(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    main = tmp_path / 'etc/ssh/sshd_config'
    before = main.read_bytes()
    old = provision.run
    def reject(argv, **kwargs):
        if argv[:2] == ['/usr/sbin/sshd', '-t']:
            raise subprocess.CalledProcessError(1, argv)
        return old(argv, **kwargs)
    provision.run = reject
    with pytest.raises(subprocess.CalledProcessError):
        provision.install()
    assert main.read_bytes() == before
    assert stat.S_IMODE(main.stat().st_mode) == 0o640
    assert not any('reload' in c or 'restart' in c for c in calls)


def test_conflicting_marker_refused_before_mutation(tmp_path):
    bootstrap, provision, root, source, account, calls = setup(tmp_path)
    main = tmp_path / 'etc/ssh/sshd_config'
    main.write_bytes(b'# personal-agent restricted deployment\nBAD\nUsePAM yes\n')
    with pytest.raises(ValueError):
        provision.install()
    assert not (tmp_path / 'etc/ssh/personal-agent-deploy.keys').exists()


def test_service_environment_can_resolve_trusted_firewall_utilities():
    service = load('service_gate')
    assert '/usr/sbin' in service.ENV['PATH'].split(':')
    assert '/sbin' in service.ENV['PATH'].split(':')
