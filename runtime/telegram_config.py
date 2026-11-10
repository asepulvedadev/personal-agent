"""One default-owned Telegram transport, routed to the isolated Carpediev profile."""
import copy
import json
from pathlib import Path
import re

try:
    from workspace_config import runtime_config, _directory, _write
except ImportError:
    from runtime.workspace_config import runtime_config, _directory, _write

OWNER='7139153140'
BOT='9272502356'


def bot_credential(path):
    path=Path(path)
    if path.is_symlink():
        raise ValueError('Telegram credential cannot be a symlink')
    if not path.exists():
        return None
    if path.stat().st_size>512:
        raise ValueError('Invalid Telegram credential size')
    value=path.read_text().strip()
    if not re.fullmatch(BOT+r':[A-Za-z0-9_-]{30,100}',value):
        raise ValueError('Unexpected Telegram bot identity')
    return value


def _environment(path):
    if path.is_symlink():
        raise ValueError('Profile credential cannot be a symlink')
    return path.read_text() if path.exists() else ''


def validate_transport(home, token):
    current=_environment(Path(home)/'.env')
    match=re.search(r'(?m)^\s*(?:export\s+)?TELEGRAM_BOT_TOKEN\s*=\s*(.*?)\s*$',current)
    if match and match[1].strip(' \"\'') and match[1].strip(' \"\'')!=token:
        raise ValueError('Conflicting default Telegram credential')


def prepare(home, base, relay_token):
    home=Path(home)
    profile=_directory(home/'profiles'/'carpediev')
    credential=profile/'.env'
    text=_environment(credential)
    if re.search(r'(?m)^\s*(?:export\s+)?TELEGRAM_BOT_TOKEN\s*=',text):
        raise ValueError('Carpediev cannot own a second Telegram transport')
    for key,value in [('OPENAI_API_KEY',relay_token),('OPENAI_BASE_URL','http://web:8080/provider/v1')]:
        pattern=re.compile(r'(?m)^\s*(?:export\s+)?'+key+r'\s*=.*$')
        text=pattern.sub('',text).rstrip()+'\n'+key+'='+value+'\n'
    if not credential.exists() or credential.read_text()!=text:
        _write(credential,text,replace=credential.exists())
    path=profile/'config.yaml'
    if path.is_symlink():
        raise ValueError('Profile configuration cannot be a symlink')
    defaults=runtime_config(base['model']['default'])
    defaults['platform_toolsets']['telegram']=defaults['platform_toolsets']['api_server'][:]
    if path.exists():
        try:
            existing=json.loads(path.read_text())
        except json.JSONDecodeError:
            from ruamel.yaml import YAML
            existing=YAML(typ='safe',pure=True).load(path.read_text())
        if not isinstance(existing,dict):
            raise ValueError('Invalid Carpediev configuration')
        defaults.update(existing)
        defaults.setdefault('platform_toolsets',{}).setdefault('telegram',base['platform_toolsets']['api_server'][:])
    _write(path,json.dumps(defaults,indent=2),replace=path.exists())
    conf=copy.deepcopy(base)
    conf['multiplex_profiles']=True
    conf['profile_routes']=[{'name':'carpediev-owner-dm','platform':'telegram','bot_profile':'default',
                             'chat_id':OWNER,'user_id':OWNER,'profile':'carpediev'}]
    return conf
