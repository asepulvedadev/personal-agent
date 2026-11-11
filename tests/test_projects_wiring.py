"""Public shells contain no data; APIs retain existing owner boundaries."""
from test_portal import portal


def test_projects_shells_and_private_api(portal):
    _, client = portal
    for path in ['/projects', '/project']:
        response = client.get(path)
        assert response.status_code == 200
        assert '/static/projects.js' in response.text
        assert 'Ranchos' not in response.text
    assert client.get('/api/projects').status_code == 401
    assert client.get('/internal/projects').status_code == 401


def test_projects_token_and_db_config_are_not_editable_runtime_variables(portal):
    mod, _ = portal
    for name in ['PROJECTS_DATABASE_FILE','PROJECTS_TOKEN','PROJECTS_TOKEN_FILE']:
        assert not mod.safe_name(name)
