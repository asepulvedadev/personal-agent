"""New private DB policy must not broaden existing deployment authority."""
import importlib.util
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def admin():
    spec = importlib.util.spec_from_file_location('projects_admin', ROOT/'deploy/projects_admin.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compose_private_database_only_web():
    policy = yaml.safe_load((ROOT/'compose.yaml').read_text())
    assert policy['networks']['projects_db'] == {'external': True, 'name': 'personal-agent_projects-db'}
    assert 'projects_db' in policy['services']['web']['networks']
    assert 'projects_db' not in policy['services']['hermes']['networks']
    assert policy['services']['web']['environment']['PROJECTS_DATABASE_FILE'] == '/run/projects/database.json'
    assert all('5432:' not in str(service.get('ports', [])) for service in policy['services'].values())
    db = yaml.safe_load((ROOT/'deploy/projects-db.compose.yaml').read_text())
    postgres = db['services']['database']
    assert 'ports' not in postgres and postgres['user'] == '999:999'
    assert '@sha256:' in postgres['image']
    assert db['networks']['database']['internal'] is True
    assert postgres['read_only'] is True and postgres['cap_drop'] == ['ALL']


def test_admin_patch_is_exact_and_does_not_touch_other_services():
    mod = admin()
    baseline = (ROOT/'compose.yaml').read_text()
    # Source is already the final policy; an attempted second patch fails closed.
    import pytest
    with pytest.raises(ValueError):
        mod.patch_compose(baseline)
    assert mod.ROOT == Path('/srv/personal-agent')
    assert mod.DB_CONTAINER == 'personal-agent-projects-db'


def test_backup_includes_dump_and_private_connection_recovery():
    text = (ROOT/'deploy/backup.py').read_text()
    assert 'pg_dump' in text and 'projects.pg_dump' in text
    assert "ROOT/'projects-db'/'web'" in text
    assert "'projects-database-config'" in text
    assert '--format=custom' in text


def test_guard_blocks_database_host_and_other_destinations():
    text = (ROOT/'deploy/network-guard.sh').read_text()
    assert '-s 172.30.43.0/29 -d 172.30.43.0/29 -j RETURN' in text
    assert '-s 172.30.43.0/29 -j DROP' in text
    assert '172.30.41.0/28 172.30.43.0/29' in text
