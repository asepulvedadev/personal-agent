"""The VPS owns one transport and routes only the owner DM into Carpediev."""
import json
from pathlib import Path

import pytest

from runtime.workspace_config import runtime_config


def test_missing_token_does_not_start_a_second_transport(tmp_path):
    from runtime.telegram_config import bot_credential
    assert bot_credential(tmp_path/'missing') is None


def test_only_the_authorized_bot_token_is_accepted(tmp_path):
    from runtime.telegram_config import bot_credential
    path=tmp_path/'token'
    path.write_text('9272502356:'+'x'*35)
    assert bot_credential(path).startswith('9272502356:')
    path.write_text('123:'+'x'*35)
    with pytest.raises(ValueError): bot_credential(path)
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(ValueError): bot_credential(link)


def test_default_transport_routes_owner_to_separate_profile(tmp_path):
    from runtime.telegram_config import prepare
    home=tmp_path/'home';home.mkdir()
    base=runtime_config('test-model')
    conf=prepare(home,base,'synthetic-internal-relay')
    assert conf['multiplex_profiles'] is True
    route=conf['profile_routes'][0]
    assert route=={'name':'carpediev-owner-dm','platform':'telegram','bot_profile':'default',
                   'chat_id':'7139153140','user_id':'7139153140','profile':'carpediev'}
    assert conf['platform_toolsets']['api_server']==base['platform_toolsets']['api_server']
    profile=home/'profiles/carpediev'
    actual=json.loads((profile/'config.yaml').read_text())
    assert actual['model']['base_url']=='http://web:8080/provider/v1'
    assert 'terminal' in actual['platform_toolsets']['telegram']
    env=profile/'.env'
    assert 'OPENAI_API_KEY=synthetic-internal-relay' in env.read_text()
    assert 'TELEGRAM_BOT_TOKEN' not in env.read_text()
    assert env.stat().st_mode & 0o777==0o600
    # Owner improvements survive another portal revision.
    actual['agent']['max_turns']=37
    (profile/'config.yaml').write_text(json.dumps(actual))
    (profile/'SOUL.md').write_text('Owner identity')
    prepare(home,runtime_config('another-model'),'synthetic-internal-relay')
    assert json.loads((profile/'config.yaml').read_text())['agent']['max_turns']==37
    assert (profile/'SOUL.md').read_text()=='Owner identity'


def test_secondary_profile_cannot_own_the_same_transport(tmp_path):
    from runtime.telegram_config import prepare
    home=tmp_path/'home';profile=home/'profiles/carpediev';profile.mkdir(parents=True)
    (profile/'.env').write_text('TELEGRAM_BOT_TOKEN=synthetic-conflict\n')
    with pytest.raises(ValueError): prepare(home,runtime_config('test'),'synthetic-relay')


def test_stale_default_transport_cannot_shadow_the_mounted_credential(tmp_path):
    from runtime.telegram_config import validate_transport
    (tmp_path/'.env').write_text('TELEGRAM_BOT_TOKEN=synthetic-conflict\n')
    with pytest.raises(ValueError): validate_transport(tmp_path,None)
    with pytest.raises(ValueError): validate_transport(tmp_path,'another-token')
