"""Offline native-tool boundary checks; no running Web/Hermes required."""
import ast
import importlib.util
import json
from pathlib import Path
import socket
import sys
import types

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'runtime/projects_tool.py'
PROJECT = 'a' * 32
OPERATION = 'b' * 32


@pytest.fixture
def tool(monkeypatch, tmp_path):
    registrations = []
    registry = types.ModuleType('tools.registry')
    registry.registry = types.SimpleNamespace(register=lambda **kw: registrations.append(kw))
    monkeypatch.setitem(sys.modules, 'tools', types.ModuleType('tools'))
    monkeypatch.setitem(sys.modules, 'tools.registry', registry)
    spec = importlib.util.spec_from_file_location('project_tool_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = tmp_path / 'token'
    token.write_text('test-projects-secret-' + 'x' * 40)
    token.chmod(0o600)
    monkeypatch.setattr(module, 'TOKEN_PATH', token)
    module.registrations = registrations
    return module


class Response:
    status = 200
    body = b'{"projects":[],"contacts":[]}'

    def read(self, limit):
        assert limit > 0
        return self.body[:limit]


@pytest.fixture
def http(tool, monkeypatch):
    calls = []

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port) == ('web', 8080)
            assert 0 < timeout <= 15
            self.closed = False
            calls.append(self)

        def request(self, method, path, body=None, headers=None):
            self.request_args = (method, path, body, headers)

        def getresponse(self):
            return Response()

        def close(self):
            self.closed = True

    monkeypatch.setattr(tool.http.client, 'HTTPConnection', Connection)
    return calls


def invoke(tool, args):
    return json.loads(tool.handle_project_manager(args, task_id='native-context'))


def command(**overrides):
    return dict(mode='command', action='add_task', project_id=PROJECT,
                revision=2, operation_id=OPERATION, data={'title': 'Requested work'}, **overrides)


def test_native_ast_registration_and_handler_contract(tool):
    tree = ast.parse(SOURCE.read_text())
    assert any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'register'
               and isinstance(n.value.func.value, ast.Name) and n.value.func.value.id == 'registry'
               for n in tree.body)
    registered, = tool.registrations
    assert registered['name'] == 'project_manager'
    assert registered['toolset'] == 'personal_projects'
    assert registered['handler'] is tool.handle_project_manager
    assert registered['schema']['parameters']['properties']['mode']['enum'] == ['list', 'get', 'command', 'download_document']


def test_list_and_get_use_fixed_origin_and_private_auth(tool, http, monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://attacker.invalid')
    assert invoke(tool, {'mode': 'list'})['projects'] == []
    method, path, body, headers = http[0].request_args
    assert (method, path, body) == ('GET', '/internal/projects', None)
    assert headers['Authorization'].startswith('Bearer test-projects-secret-')
    assert headers['X-Project-Actor'] == 'hermes-web'
    invoke(tool, {'mode': 'get', 'project_id': PROJECT, 'actor': 'carpediev'})
    assert http[1].request_args[1] == '/internal/projects/' + PROJECT
    assert http[1].request_args[3]['X-Project-Actor'] == 'carpediev'
    assert all(c.closed for c in http)


def test_write_preserves_exact_command_and_actor_attribution(tool, http):
    args = command()
    invoke(tool, args)
    method, path, body, _ = http[0].request_args
    assert (method, path) == ('POST', '/internal/projects/command')
    assert json.loads(body) == {k: v for k, v in args.items() if k != 'mode'}


@pytest.mark.parametrize('field,value', [('project_id', None), ('project_id', '../other'),
                                        ('revision', None), ('revision', True),
                                        ('operation_id', None), ('operation_id', 'bad'),
                                        ('actor', 'owner'), ('action', 'create_share'),
                                        ('action', 'revoke_share'), ('url', 'https://attacker.invalid')])
def test_rejects_ambiguous_or_untrusted_writes_before_network(tool, http, field, value):
    args = command()
    args[field] = value
    assert invoke(tool, args)['error']['code'] == 'invalid_input'
    assert http == []


def test_contact_and_create_project_are_explicit_server_validated_commands(tool, http):
    for action, data in [('create_contact', {'name': 'Owner requested'}),
                         ('create_project', {'name': 'Selected new project'}),
                         ('update_contact', {'contact_id': PROJECT, 'revision': 3, 'notes': 'Approved'})]:
        result = invoke(tool, {'mode': 'command', 'action': action, 'operation_id': OPERATION, 'data': data})
        assert 'error' not in result
    assert len(http) == 3


@pytest.mark.parametrize('status,code', [(401, 'authentication_required'), (403, 'forbidden'),
                                       (404, 'not_found'), (409, 'revision_or_operation_conflict'),
                                       (422, 'validation_failed'), (302, 'redirect_refused'),
                                       (500, 'service_error')])
def test_errors_are_bounded_and_do_not_leak_server_body(tool, http, monkeypatch, status, code):
    monkeypatch.setattr(Response, 'status', status)
    monkeypatch.setattr(Response, 'body', b'private-token postgres://password traceback secret')
    result = invoke(tool, command())
    assert result['error']['code'] == code
    assert result['operation_id'] == OPERATION
    assert 'postgres' not in json.dumps(result)
    assert len(http) == 1


def test_timeout_does_not_retry_or_lose_idempotency(tool, http, monkeypatch):
    def fail(self):
        raise socket.timeout('sensitive transport error')
    monkeypatch.setattr(tool.http.client.HTTPConnection, 'getresponse', fail)
    result = invoke(tool, command())
    assert result['state'] == 'unknown'
    assert result['operation_id'] == OPERATION
    assert 'sensitive' not in json.dumps(result)
    assert len(http) == 1 and http[0].closed


def test_rejects_oversized_or_invalid_success_and_redacts_secret(tool, http, monkeypatch):
    monkeypatch.setattr(Response, 'body', b'x' * (tool.MAX_RESPONSE_BYTES + 1))
    assert invoke(tool, command())['state'] == 'unknown'
    monkeypatch.setattr(Response, 'body', b'not json')
    assert invoke(tool, {'mode': 'list'})['error']['code'] == 'invalid_response'
    secret = tool.TOKEN_PATH.read_text()
    monkeypatch.setattr(Response, 'body', json.dumps({'project': {'notes': secret}}).encode())
    assert secret not in json.dumps(invoke(tool, {'mode': 'get', 'project_id': PROJECT}))


def test_token_missing_insecure_or_symlink_never_sent(tool, http, tmp_path):
    token = tool.TOKEN_PATH
    token.chmod(0o644)
    assert invoke(tool, {'mode': 'list'})['error']['code'] == 'credential_unavailable'
    token.chmod(0o600)
    link = tmp_path / 'link'
    link.symlink_to(token)
    tool.TOKEN_PATH = link
    assert invoke(tool, {'mode': 'list'})['error']['code'] == 'credential_unavailable'
    token.unlink()
    assert invoke(tool, {'mode': 'list'})['error']['code'] == 'credential_unavailable'
    assert not http


def test_active_profile_actor_uses_context_home_not_shared_process_env(tool, http, monkeypatch):
    constants = types.ModuleType('hermes_constants')
    constants.get_hermes_home = lambda: '/state/profiles/carpediev'
    monkeypatch.setitem(sys.modules, 'hermes_constants', constants)
    monkeypatch.setenv('HERMES_HOME', '/state')
    invoke(tool, {'mode': 'list'})
    assert http[0].request_args[3]['X-Project-Actor'] == 'carpediev'


@pytest.mark.parametrize('action', ['update_project', 'add_group', 'update_group', 'add_task',
                                   'update_task', 'add_comment', 'add_time', 'start_timer',
                                   'stop_timer', 'add_document', 'set_visibility'])
def test_each_existing_project_action_reaches_same_command_api(tool, http, action):
    args = command()
    args['action'] = action
    args['data'] = {'example': 'Server validates action fields'}
    invoke(tool, args)
    assert json.loads(http[0].request_args[2])['action'] == action


def test_request_size_and_nonfinite_json_rejected_before_auth_or_network(tool, http, monkeypatch):
    monkeypatch.setattr(tool, 'MAX_REQUEST_BYTES', 300)
    args = command()
    args['data'] = {'content_base64': 'x' * 301}
    assert invoke(tool, args)['error']['code'] == 'invalid_input'
    args['data'] = {'minutes': float('nan')}
    assert invoke(tool, args)['error']['code'] == 'invalid_input'
    assert not http


def test_failed_request_is_unknown_write_but_read_has_no_operation_state(tool, http, monkeypatch):
    def fail(self, *args, **kwargs):
        raise OSError('Bearer secret-sensitive-content')
    monkeypatch.setattr(tool.http.client.HTTPConnection, 'request', fail)
    result = invoke(tool, command())
    assert result['error']['code'] == 'transport_failed'
    assert result['state'] == 'unknown' and result['operation_id'] == OPERATION
    result = invoke(tool, {'mode': 'list'})
    assert 'state' not in result and 'operation_id' not in result
    assert 'secret-sensitive' not in json.dumps(result)
    assert all(c.closed for c in http)


@pytest.fixture
def download(tool, http, monkeypatch, tmp_path):
    import io
    root = tmp_path / 'workspace' / 'projects'
    root.mkdir(parents=True)
    monkeypatch.setattr(tool, 'DOWNLOAD_ROOT', root, raising=False)
    payload = b'%PDF-1.7\nprivate source content'
    headers = {'Content-Type': 'application/pdf', 'Content-Length': str(len(payload)),
               'Content-Disposition': 'attachment; filename="../../escape.pdf"'}

    class DownloadResponse:
        status = 200
        def __init__(self):
            self.stream = io.BytesIO(payload)
        def getheader(self, name, default=None):
            return headers.get(name, default)
        def read(self, limit):
            assert 0 < limit <= 65536
            return self.stream.read(limit)

    monkeypatch.setattr(tool.http.client.HTTPConnection, 'getresponse', lambda self: DownloadResponse())
    return root, payload, headers


def download_args(**kw):
    return dict(mode='download_document', project_id=PROJECT, document_id='c' * 32, **kw)


def test_download_is_scoped_binary_new_file_not_model_base64(tool, http, download):
    import hashlib
    root, payload, _ = download
    result = invoke(tool, download_args())
    path = Path(result['path'])
    assert path.is_relative_to(root / PROJECT / 'documents')
    assert path.read_bytes() == payload
    assert result['bytes'] == len(payload)
    assert result['sha256'] == hashlib.sha256(payload).hexdigest()
    assert result['media_type'] == 'application/pdf'
    assert result['name'] == path.name
    assert 'content_base64' not in result
    assert path.stat().st_mode & 0o777 == 0o600
    assert http[0].request_args[1] == f'/internal/projects/{PROJECT}/documents/' + 'c' * 32
    assert not (root.parent / 'escape.pdf').exists()


@pytest.mark.parametrize('change', [{'project_id': '../escape'}, {'document_id': '../escape'},
                                  {'destination': '/tmp/forbidden.pdf'}, {'destination': 'relative.pdf'},
                                  {'destination': '/workspace/projects/../escape.pdf'},
                                  {'destination': '/workspace/projects'}, {'action': 'add_task'}])
def test_download_validates_inputs_before_network(tool, http, download, change):
    args = download_args()
    args.update(change)
    assert invoke(tool, args)['error']['code'] == 'invalid_input'
    assert not http


def test_download_explicit_destination_never_overwrites_or_follows_links(tool, http, download, tmp_path):
    root, payload, _ = download
    target = root / 'requested.pdf'
    target.write_bytes(b'preserve existing')
    args = download_args(destination=str(target))
    assert invoke(tool, args)['error']['code'] == 'destination_unavailable'
    assert target.read_bytes() == b'preserve existing'
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'linked').symlink_to(outside, target_is_directory=True)
    assert invoke(tool, download_args(destination=str(root / 'linked' / 'escape.pdf')))['error']['code'] == 'destination_unavailable'
    assert not (outside / 'escape.pdf').exists()
    link = root / 'target.pdf'
    link.symlink_to(target)
    assert invoke(tool, download_args(destination=str(link)))['error']['code'] == 'destination_unavailable'
    assert target.read_bytes() == b'preserve existing'
    assert not list(root.glob('.project-download-*'))


def test_download_oversize_or_incomplete_never_publishes_partial_file(tool, http, download, monkeypatch):
    root, payload, headers = download
    monkeypatch.setattr(tool, 'MAX_DOCUMENT_BYTES', 10, raising=False)
    args = download_args(destination=str(root / 'requested.pdf'))
    assert invoke(tool, args)['error']['code'] == 'document_too_large'
    assert not (root / 'requested.pdf').exists()
    headers.pop('Content-Length')
    assert invoke(tool, args)['error']['code'] == 'document_too_large'
    assert not (root / 'requested.pdf').exists()
    monkeypatch.setattr(tool, 'MAX_DOCUMENT_BYTES', 100)
    headers['Content-Length'] = str(len(payload) + 1)
    assert invoke(tool, args)['error']['code'] == 'invalid_response'
    assert not (root / 'requested.pdf').exists()
    assert not list(root.glob('.project-download-*'))


def test_download_network_failure_does_not_create_or_return_file(tool, http, download, monkeypatch):
    root, _, _ = download
    class BrokenResponse:
        status = 200
        def getheader(self, *args):
            return None
        def read(self, limit):
            raise OSError('secret-bearing details')
    monkeypatch.setattr(tool.http.client.HTTPConnection, 'getresponse', lambda self: BrokenResponse())
    result = invoke(tool, download_args(destination=str(root / 'requested.pdf')))
    assert 'path' not in result and 'secret-bearing' not in json.dumps(result)
    assert not (root / 'requested.pdf').exists()
    assert not list(root.glob('.project-download-*'))


@pytest.mark.parametrize('status', [302, 401, 403, 404, 500])
def test_download_error_or_redirect_creates_no_file_and_no_second_request(tool, http, download, monkeypatch, status):
    root, _, _ = download
    class FailedResponse:
        pass
    failed = FailedResponse()
    failed.status = status
    monkeypatch.setattr(tool.http.client.HTTPConnection, 'getresponse', lambda self: failed)
    result = invoke(tool, download_args(destination=str(root / 'requested.pdf')))
    assert 'error' in result and 'path' not in result
    assert not (root / 'requested.pdf').exists()
    assert len(http) == 1 and http[0].closed


def test_download_refuses_symlink_workspace_ancestor(tool, http, download, monkeypatch, tmp_path):
    root, _, _ = download
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(tool, 'DOWNLOAD_ROOT', alias)
    result = invoke(tool, download_args(destination=str(alias / 'requested.pdf')))
    assert result['error']['code'] == 'destination_unavailable'
    assert not (root / 'requested.pdf').exists()
