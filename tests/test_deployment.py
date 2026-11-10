"""Host deployment boundaries; no Docker, SSH, or network access."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'deploy' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SHA = 'a' * 40
OLD = 'b' * 40


def archive(name='app/main.py', kind=tarfile.REGTYPE, payload=b'pass\n', size=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as bundle:
        entry = tarfile.TarInfo(f'personal-agent-{SHA}/{name}')
        entry.type = kind
        entry.size = len(payload) if size is None else size
        entry.linkname = '/etc/shadow'
        bundle.addfile(entry, io.BytesIO(payload) if entry.isfile() else None)
    return output.getvalue()


@pytest.mark.parametrize('command', ['', 'status x', 'deploy', f'deploy {SHA}; id', f'deploy {SHA.upper()}', f'rollback {SHA} extra', 'deploy ../x', ' status', 'status\n'])
def test_gate_rejects_noncanonical_commands(command):
    with pytest.raises(ValueError):
        load('ssh_gate').parse_command(command)


def test_gate_uses_only_fixed_sudo_argv(monkeypatch):
    gate = load('ssh_gate')
    calls = []
    monkeypatch.setenv('SSH_ORIGINAL_COMMAND', f'deploy {SHA}')
    monkeypatch.setattr(gate.subprocess, 'run', lambda argv, **kw: calls.append((argv, kw)) or subprocess.CompletedProcess(argv, 0))
    assert gate.main() == 0
    assert calls[0][0] == ['/usr/bin/sudo', '-n', '/usr/bin/python3', '-I', '/srv/personal-agent/deploy-policy/release_gate.py', 'deploy', SHA]
    assert calls[0][1]['stdin'] is subprocess.DEVNULL
    assert 'shell' not in calls[0][1]


@pytest.mark.parametrize('name,kind', [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE), ('app/link', tarfile.SYMTYPE), ('app/hard', tarfile.LNKTYPE), ('app/device', tarfile.CHRTYPE), ('.env', tarfile.REGTYPE), ('workspace/private', tarfile.REGTYPE), ('.git/config', tarfile.REGTYPE), ('app/../../escape', tarfile.REGTYPE)])
def test_archive_rejects_unsafe_entries(tmp_path, name, kind):
    gate = load('release_gate')
    with pytest.raises(ValueError):
        gate.extract_source(archive(name, kind), tmp_path / 'source', SHA)
    assert not (tmp_path / 'escape').exists()


def test_archive_bounds_and_identity(tmp_path, monkeypatch):
    gate = load('release_gate')
    monkeypatch.setattr(gate, 'MAX_FILE', 3)
    with pytest.raises(ValueError):
        gate.extract_source(archive(payload=b'1234'), tmp_path / 'source', SHA)
    with pytest.raises(ValueError):
        gate.extract_source(archive(), tmp_path / 'other', OLD)
    monkeypatch.setattr(gate, 'MAX_DOWNLOAD', 3)
    with pytest.raises(ValueError):
        gate.extract_source(archive(), tmp_path / 'large', SHA)


def test_archive_extracts_regular_source_without_archive_modes(tmp_path):
    gate = load('release_gate')
    gate.extract_source(archive(), tmp_path / 'source', SHA)
    assert (tmp_path / 'source/app/main.py').read_text() == 'pass\n'
    assert (tmp_path / 'source/app/main.py').stat().st_mode & 0o777 == 0o644


def prepared(tmp_path):
    gate = load('release_gate')
    (tmp_path / 'releases/initial').mkdir(parents=True)
    (tmp_path / 'current').symlink_to('releases/initial')
    (tmp_path / 'deploy-policy').mkdir()
    for name in ['compose.yaml', 'network-guard.sh', 'backup.py']:
        (tmp_path / 'deploy-policy' / name).write_text('trusted')
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='[]')
    release = gate.ReleaseGate(tmp_path, run=run, fetch=lambda url, limit: json.dumps({'sha': SHA}).encode() if 'api.github.com' in url else archive())
    return gate, release, calls


def test_wrong_main_sha_rejected_before_build(tmp_path):
    gate, release, calls = prepared(tmp_path)
    with pytest.raises(ValueError):
        release.deploy(OLD)
    assert calls == []
    assert (tmp_path / 'current').readlink() == Path('releases/initial')


def test_failed_rollout_keeps_prior_pointer_and_rolls_back_once(tmp_path, monkeypatch):
    gate, release, calls = prepared(tmp_path)
    health = iter([False, True])
    monkeypatch.setattr(release, 'healthy', lambda source, label: next(health))
    with pytest.raises(RuntimeError):
        release.deploy(SHA)
    assert (tmp_path / 'current').readlink() == Path('releases/initial')
    assert not release.record(SHA).exists()
    ups = [c for c in calls if 'up' in c]
    assert len(ups) == 2
    assert '--no-build' in ups[1] and '--pull' in ups[1]
    assert all(str(tmp_path / 'deploy-policy/compose.yaml') in c for c in calls if c[0] == '/usr/bin/docker')
    assert not any(str(tmp_path / f'releases/{SHA}/deploy/') in ' '.join(c) for c in calls)
    assert not any(x in c for c in calls for x in ['down', 'prune'])


def test_success_builds_then_promotes_and_reuses_images(tmp_path, monkeypatch):
    gate, release, calls = prepared(tmp_path)
    monkeypatch.setattr(release, 'healthy', lambda source, label: True)
    release.deploy(SHA)
    assert (tmp_path / 'current').readlink() == Path(f'releases/{SHA}')
    assert release.record(SHA).exists()
    assert next(i for i,c in enumerate(calls) if 'build' in c) < next(i for i,c in enumerate(calls) if 'up' in c)
    override = json.loads(release.override(SHA).read_text())
    assert set(override) == {'services'}
    assert override['services']['web'] == {'image': f'personal-agent-web:{SHA}'}


def test_build_failure_never_changes_running_release(tmp_path):
    gate, release, calls = prepared(tmp_path)
    original = release.run
    def fail_build(argv, **kwargs):
        if 'build' in argv:
            raise subprocess.CalledProcessError(1, argv)
        return original(argv, **kwargs)
    release.run = fail_build
    with pytest.raises(subprocess.CalledProcessError):
        release.deploy(SHA)
    assert (tmp_path / 'current').readlink() == Path('releases/initial')
    assert not any('up' in c for c in calls)


def test_rollback_requires_previously_accepted_release(tmp_path):
    gate, release, calls = prepared(tmp_path)
    with pytest.raises(ValueError):
        release.rollback(SHA)
    assert calls == []


def test_download_rejects_redirects():
    gate = load('release_gate')
    with pytest.raises(ValueError):
        gate.download('https://evil.example/archive', 1024)
    with pytest.raises(Exception):
        gate.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example/')


def test_expansion_and_member_limits(tmp_path, monkeypatch):
    gate = load('release_gate')
    monkeypatch.setattr(gate, 'MAX_EXPANDED', 100)
    with pytest.raises(ValueError):
        gate.extract_source(archive(), tmp_path / 'expanded', SHA)
    monkeypatch.setattr(gate, 'MAX_EXPANDED', 128 * 1024**2)
    monkeypatch.setattr(gate, 'MAX_MEMBERS', 0)
    with pytest.raises(ValueError):
        gate.extract_source(archive(), tmp_path / 'members', SHA)


def test_failed_recovery_preserves_pointer_and_failure_evidence(tmp_path, monkeypatch):
    gate, release, calls = prepared(tmp_path)
    monkeypatch.setattr(release, 'healthy', lambda source, label: False)
    with pytest.raises(RuntimeError):
        release.deploy(SHA)
    assert (tmp_path / 'current').readlink() == Path('releases/initial')
    assert len([c for c in calls if 'up' in c]) == 2
    evidence = [json.loads(line) for line in (release.state / 'events.jsonl').read_text().splitlines()]
    assert evidence[-1]['outcome'] == 'failed-owner-action-required'


def test_redeploy_current_sha_never_rebuilds_accepted_images(tmp_path, monkeypatch):
    gate, release, calls = prepared(tmp_path)
    monkeypatch.setattr(release, 'healthy', lambda source, label: True)
    release.deploy(SHA)
    calls.clear()
    release.deploy(SHA)
    assert calls == []


def test_returning_to_accepted_main_sha_reuses_retained_images(tmp_path, monkeypatch):
    gate, release, calls = prepared(tmp_path)
    monkeypatch.setattr(release, 'healthy', lambda source, label: True)
    release.deploy(SHA)
    release.promote('initial')
    calls.clear()
    release.deploy(SHA)
    assert not any('build' in c for c in calls)


def test_main_changes_during_build_blocks_activation(tmp_path):
    gate, release, calls = prepared(tmp_path)
    original = release.fetch
    reads = iter([SHA, OLD])
    release.fetch = lambda url, limit: json.dumps({'sha': next(reads)}).encode() if url == gate.MAIN_URL else original(url, limit)
    with pytest.raises(ValueError):
        release.deploy(SHA)
    assert not any('up' in c for c in calls)
    assert (tmp_path / 'current').readlink() == Path('releases/initial')


def test_workflow_production_boundary():
    import yaml
    workflow = yaml.safe_load((Path(__file__).parents[1] / '.github/workflows/workspace.yml').read_text())
    assert workflow['permissions'] == {'contents': 'read'}
    test_job = workflow['jobs']['test']
    assert 'environment' not in test_job
    assert 'secrets.' not in json.dumps(test_job)
    deploy = workflow['jobs']['deploy']
    assert deploy['needs'] == 'test'
    assert deploy['environment'] == 'production'
    assert deploy['concurrency']['cancel-in-progress'] is False
    assert "github.ref == 'refs/heads/main'" in deploy['if']
    assert "github.event_name == 'push'" in deploy['if']
    for step in test_job['steps']:
        if 'uses' in step:
            assert len(step['uses'].split('@')[1]) == 40
    commands = ' '.join(step.get('run', '') for step in test_job['steps'])
    assert 'live_smoke' not in commands and 'isolation_probe' not in commands and 'native_history_probe' not in commands


def test_health_wait_is_bounded(tmp_path, monkeypatch):
    gate, release, calls = prepared(tmp_path)
    elapsed = [0]
    monkeypatch.setattr(gate.time, 'monotonic', lambda: elapsed[0])
    monkeypatch.setattr(gate.time, 'sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    assert release.healthy(tmp_path / 'releases/initial', 'initial') is False
    assert len(calls) == 60
    assert elapsed[0] <= 300


def test_failed_promotion_recovers_previous_release(tmp_path, monkeypatch):
    gate, release, calls = prepared(tmp_path)
    monkeypatch.setattr(release, 'healthy', lambda source, label: True)
    promote = release.promote
    attempts = []
    def fail_new(label):
        attempts.append(label)
        if label == SHA:
            raise OSError('simulated promotion error')
        promote(label)
    monkeypatch.setattr(release, 'promote', fail_new)
    with pytest.raises(OSError):
        release.deploy(SHA)
    assert attempts == [SHA, 'initial']
    assert (tmp_path / 'current').readlink() == Path('releases/initial')
