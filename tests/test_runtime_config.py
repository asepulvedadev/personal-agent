"""Inspect the supervisor's generated config without starting its process loop."""
import ast
from pathlib import Path
from runtime.workspace_config import runtime_config


def generated_config(model, owner_config=None):
    tree = ast.parse(Path('runtime/supervisor.py').read_text())
    assignment = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'conf' for t in node.targets))
    return eval(compile(ast.Expression(assignment.value), '<runtime-config>', 'eval'),
                {'__builtins__': {}, 'runtime_config': runtime_config},
                {'c': {'model': model}, 'owner_config': owner_config})


def test_primary_and_auxiliary_routes_keep_responses_through_private_relay():
    config = generated_config('gpt-6.1-sol')
    for name in ('model', 'compression', 'title_generation'):
        route = config[name]
        assert route['provider'] == 'openai-api'
        assert route['api_mode'] == 'codex_responses'
        assert route['base_url'] == 'http://web:8080/provider/v1'
        assert route['key_env'] == 'OPENAI_API_KEY'
        assert route.get('default', route.get('model')) == 'gpt-6.1-sol'
    assert config['terminal']['cwd'] == '/workspace/projects/personal-agent'
    assert config['max_concurrent_sessions'] == 1


def test_existing_model_choice_and_workspace_limits_remain_configurable():
    config = generated_config('gpt-5.4-mini')
    assert config['model']['default'] == 'gpt-5.4-mini'
    assert config['compression']['model'] == 'gpt-5.4-mini'
    assert config['title_generation']['model'] == 'gpt-5.4-mini'
    assert config['agent']['max_turns'] == 20
    assert config['terminal']['backend'] == 'local'


def test_supervisor_does_not_enable_yolo_and_checks_approvals_without_revision_change():
    source = Path('runtime/supervisor.py').read_text()
    tree = ast.parse(source)
    assert "env.pop('HERMES_YOLO_MODE', None)" in source
    assert "'HERMES_YOLO_MODE':'1'" not in source
    assert "if 'approved_skills' in c:" in source
    assert 'or imports_changed' in source
    assert runtime_config('example')['security']['protected_instruction_files'] is True
