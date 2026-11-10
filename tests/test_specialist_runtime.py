"""Real profile seeding preserves owner state and never clones a transport."""
import hashlib
import hmac
import json

import pytest
from runtime import workspace_config as wc


def test_specialist_has_isolated_identity_config_and_scoped_credentials(tmp_path):
    base = wc.runtime_config('test-model')
    result = wc.prepare_specialist(tmp_path, base, 'relay-only', 'owner-api')
    root = tmp_path / 'profiles/app-config'
    assert result == base
    assert 'Configurador' in (root / 'SOUL.md').read_text()
    conf = json.loads((root / 'config.yaml').read_text())
    assert conf['security']['protected_instruction_files'] is True
    assert conf['approvals']['mode'] == 'smart'
    assert conf['model']['base_url'] == 'http://web:8080/provider/v1'
    expected = hmac.new(b'owner-api', b'app-config', hashlib.sha256).hexdigest()
    assert 'API_SERVER_KEY=' + expected in (root / '.env').read_text()
    assert 'OPENAI_API_KEY=relay-only' in (root / '.env').read_text()
    assert 'TELEGRAM' not in (root / '.env').read_text()
    assert not (tmp_path / 'SOUL.md').exists()
    assert not (tmp_path / 'profiles/carpediev').exists()


def test_repeat_seed_preserves_owner_edits_but_pins_relay(tmp_path):
    base = wc.runtime_config('first')
    wc.prepare_specialist(tmp_path, base, 'relay', 'owner')
    root = tmp_path / 'profiles/app-config'
    soul = (root / 'SOUL.md').read_text() + '\nOwner improvement.\n'
    (root / 'SOUL.md').write_text(soul)
    conf = json.loads((root / 'config.yaml').read_text())
    conf['agent']['max_turns'] = 33
    conf['model']['base_url'] = 'https://unwanted.example'
    conf['security']['protected_instruction_files'] = False
    conf['approvals']['mode'] = 'off'
    conf['mcp_servers'] = {'owner': {'url': 'https://owner.example'}}
    (root / 'config.yaml').write_text(json.dumps(conf))
    (root / '.env').write_text((root / '.env').read_text() + 'OWN_VALUE=preserved\n')
    wc.prepare_specialist(tmp_path, wc.runtime_config('second'), 'relay2', 'owner')
    assert (root / 'SOUL.md').read_text() == soul
    conf = json.loads((root / 'config.yaml').read_text())
    assert conf['agent']['max_turns'] == 33
    assert conf['model']['default'] == 'second'
    assert conf['model']['base_url'] == 'http://web:8080/provider/v1'
    assert conf['security']['protected_instruction_files'] is True
    assert conf['approvals']['mode'] == 'smart'
    assert conf['mcp_servers']['owner']['url'] == 'https://owner.example'
    assert 'OWN_VALUE=preserved' in (root / '.env').read_text()
    assert 'OPENAI_API_KEY=relay2' in (root / '.env').read_text()


def test_specialist_rejects_symlink_and_unexpected_transport(tmp_path):
    root = tmp_path / 'profiles/app-config'
    root.mkdir(parents=True)
    (root / '.env').write_text('TELEGRAM_BOT_TOKEN=unexpected\n')
    with pytest.raises(ValueError):
        wc.prepare_specialist(tmp_path, wc.runtime_config('m'), 'relay', 'owner')
    (root / '.env').unlink()
    outside = tmp_path / 'outside'
    outside.write_text('Keep')
    (root / 'config.yaml').symlink_to(outside)
    with pytest.raises(ValueError):
        wc.prepare_specialist(tmp_path, wc.runtime_config('m'), 'relay', 'owner')
    assert outside.read_text() == 'Keep'
