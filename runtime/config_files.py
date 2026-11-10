"""Fixed default-profile editor. Never returns raw config or credential-bearing fields."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

MAX_TEXT = 32768
MAX_CONFIG = 262144
FIELDS = {
    'agent': {'max_turns': (int, 1, 200, 20)},
    'terminal': {'timeout': (int, 1, 3600, 180)},
    'memory': {'memory_enabled': (bool, None, None, True), 'user_profile_enabled': (bool, None, None, True)},
    'skills': {'creation_nudge_interval': (int, 0, 1000, 15)},
}
FILES = {
    'config': ('config.yaml', 'Ajustes editables sin secretos', ('config.yaml',)),
    'soul': ('SOUL.md', 'Identidad e instrucciones de Hermes', ('SOUL.md',)),
    'user': ('USER.md', 'Preferencias y contexto personal', ('memories', 'USER.md')),
}
SECRET = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b|(?:[\"\']?(?:[A-Z_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD|SECRET|PRIVATE_KEY))[\"\']?\s*[:=]\s*[\"\']?)[^\s\"\']{4,}', re.I)


class ConfigError(ValueError):
    def __init__(self, status=400):
        self.status = status
        super().__init__('No se pudo completar la operación de configuración.')


def yaml_load(text):
    """Safe parsers only; reject aliases, custom tags and duplicate keys."""
    try:
        try:
            from ruamel.yaml import YAML
            parser = YAML(typ='safe', pure=True)
            parser.allow_duplicate_keys = False
            if any(type(token).__name__ in {'AliasToken', 'AnchorToken', 'TagToken'} for token in parser.scan(text)):
                raise ValueError()
            value = parser.load(text)
        except ImportError:
            # Local offline tests use PyYAML; the pinned Hermes image uses ruamel.
            import yaml
            if any(type(token).__name__ in {'AliasToken', 'AnchorToken', 'TagToken'} for token in yaml.scan(text)):
                raise ValueError()
            class UniqueLoader(yaml.SafeLoader):
                pass
            def mapping(loader, node, deep=False):
                result = {}
                for key_node, value_node in node.value:
                    key = loader.construct_object(key_node, deep=deep)
                    if key in result:
                        raise ValueError()
                    result[key] = loader.construct_object(value_node, deep=deep)
                return result
            UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
            value = yaml.load(text, Loader=UniqueLoader)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except Exception:
        raise ConfigError() from None


def editable_values(config, *, strict=False, defaults=False):
    """Shared schema for editor validation and supervisor preservation."""
    if not isinstance(config, dict):
        raise ConfigError()
    if strict and (not config or set(config) - set(FIELDS)):
        raise ConfigError()
    result = {}
    for section, fields in FIELDS.items():
        source = config.get(section, {})
        if not isinstance(source, dict):
            raise ConfigError()
        if strict and (section in config and (not source or set(source) - set(fields))):
            raise ConfigError()
        values = {}
        for key, (kind, minimum, maximum, default) in fields.items():
            if key not in source and not defaults:
                continue
            value = source.get(key, default)
            if type(value) is not kind or (kind is int and not minimum <= value <= maximum):
                raise ConfigError()
            values[key] = value
        if values:
            result[section] = values
    return result


def _digest(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else 'missing'


def _text(raw):
    if len(raw) > MAX_TEXT:
        raise ConfigError(413)
    try:
        value = raw.decode('utf-8')
    except UnicodeError:
        raise ConfigError() from None
    if '\x00' in value or SECRET.search(value):
        raise ConfigError()
    return value


@contextmanager
def _directory(path):
    """Walk absolute components through directory descriptors without following links."""
    path = Path(path).absolute()
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _read(parent, name, maximum):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise ConfigError(413 if info.st_size > maximum else 400)
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            raw = handle.read(maximum + 1)
        if len(raw) > maximum:
            raise ConfigError(413)
        return raw
    finally:
        os.close(fd)


def _atomic(parent, name, raw):
    temporary = '.settings-' + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


class ConfigFiles:
    def __init__(self, home):
        self.home = Path(home).absolute()

    @staticmethod
    def listing():
        return {'files': [{'id': key, 'name': name, 'description': description}
                for key, (name, description, _) in FILES.items()]}

    @contextmanager
    def _file(self, file_id):
        if file_id not in FILES:
            raise ConfigError(404)
        try:
            with _directory(self.home) as home:
                lock = os.open('.config-editor.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=home)
                try:
                    if not stat.S_ISREG(os.fstat(lock).st_mode) or os.fstat(lock).st_nlink != 1:
                        raise ConfigError()
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    parts = FILES[file_id][2]
                    if len(parts) == 1:
                        yield home, home, parts[-1]
                    else:
                        parent = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home)
                        try:
                            yield home, parent, parts[-1]
                        finally:
                            os.close(parent)
                finally:
                    os.close(lock)
        except OSError:
            raise ConfigError() from None

    def _snapshot(self, file_id, raw):
        if file_id == 'config':
            if raw is None:
                raise ConfigError(404)
            try:
                content = json.dumps(editable_values(yaml_load(raw.decode('utf-8')), defaults=True), ensure_ascii=False, indent=2) + '\n'
            except UnicodeError:
                raise ConfigError() from None
            note = 'Vista editable sin secretos; no es el archivo completo. Los campos omitidos conservan su valor. Algunos ajustes requieren reiniciar Hermes; guardar no lo reinicia.'
        else:
            content = _text(raw or b'')
            note = ('Las preferencias se aplican en conversaciones nuevas.' if file_id == 'user'
                    else 'La identidad se vuelve a leer en conversaciones nuevas.')
        return {'id': file_id, 'name': FILES[file_id][0], 'content': content, 'revision': _digest(raw), 'note': note}

    def read(self, file_id):
        with self._file(file_id) as (_, parent, name):
            raw = _read(parent, name, MAX_CONFIG if file_id == 'config' else MAX_TEXT)
            return self._snapshot(file_id, raw)

    def _backup(self, home, file_id, raw):
        if raw is None:
            return
        try:
            os.mkdir('.config-editor-backups', 0o700, dir_fd=home)
        except FileExistsError:
            pass
        backup = os.open('.config-editor-backups', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home)
        try:
            if os.fstat(backup).st_mode & 0o777 != 0o700:
                raise ConfigError()
            name = f'{file_id}-{time.time_ns():020d}-{secrets.token_hex(8)}.bak'
            _atomic(backup, name, raw)
            names = sorted(n for n in os.listdir(backup) if re.fullmatch(re.escape(file_id) + r'-\d{20}-[a-f0-9]{16}\.bak', n))
            for previous in names[:-5]:
                os.unlink(previous, dir_fd=backup)
            os.fsync(backup)
        finally:
            os.close(backup)

    def write(self, file_id, content, revision):
        if not isinstance(content, str) or not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}|missing', revision):
            raise ConfigError()
        try:
            incoming = content.encode('utf-8')
        except UnicodeError:
            raise ConfigError() from None
        _text(incoming)
        projection = editable_values(yaml_load(content), strict=True) if file_id == 'config' else None
        with self._file(file_id) as (home, parent, name):
            maximum = MAX_CONFIG if file_id == 'config' else MAX_TEXT
            raw = _read(parent, name, maximum)
            if _digest(raw) != revision:
                raise ConfigError(409)
            # Never overwrite a credential-bearing Markdown file through this editor.
            self._snapshot(file_id, raw)
            if projection is not None:
                current = yaml_load(raw.decode('utf-8'))
                for section, values in projection.items():
                    current.setdefault(section, {}).update(values)
                incoming = (json.dumps(current, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
                if len(incoming) > MAX_CONFIG:
                    raise ConfigError(413)
            if _read(parent, name, maximum) != raw:
                raise ConfigError(409)
            self._backup(home, file_id, raw)
            # Cooperative writers share the lock; root may still ignore it.
            if _read(parent, name, maximum) != raw:
                raise ConfigError(409)
            _atomic(parent, name, incoming)
            return {**self._snapshot(file_id, incoming), 'saved': True}


def register_config_files(app, adapter):
    """Called once after upstream mirror registration, on the existing authenticated port."""
    from aiohttp import web
    # Capture the startup default home, never a request profile ContextVar.
    editor = ConfigFiles(os.environ.get('HERMES_HOME', '/workspace/.hermes'))

    async def handler(request):
        if request.path.startswith('/p/'):
            return web.json_response({'detail': 'Archivo no disponible.'}, status=404)
        if not getattr(adapter, '_api_key', ''):
            return web.json_response({'detail': 'Se requiere autenticación.'}, status=401)
        auth_error = adapter._check_auth(request)
        if auth_error is not None:
            return auth_error
        try:
            file_id = request.match_info.get('file_id')
            if request.method == 'GET':
                result = await asyncio.to_thread(editor.read, file_id) if file_id else editor.listing()
            else:
                raw = bytearray()
                async for chunk in request.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ConfigError(413)
                try:
                    payload = json.loads(raw.decode('utf-8'))
                except (ValueError, UnicodeError):
                    raise ConfigError() from None
                if not isinstance(payload, dict) or set(payload) != {'content', 'revision'}:
                    raise ConfigError()
                result = await asyncio.to_thread(editor.write, file_id, payload['content'], payload['revision'])
            return web.json_response(result, headers={'Cache-Control': 'no-store'})
        except ConfigError as exc:
            messages = {409: 'El archivo cambió. Recárgalo antes de guardar.', 413: 'El archivo supera el tamaño permitido.', 404: 'Archivo no disponible.'}
            return web.json_response({'detail': messages.get(exc.status, 'Contenido o archivo no permitido. Revisa los ajustes sin incluir secretos.')}, status=exc.status, headers={'Cache-Control': 'no-store'})
        except Exception:
            return web.json_response({'detail': 'No se pudo acceder a la configuración.'}, status=503, headers={'Cache-Control': 'no-store'})

    app.router.add_route('GET', '/api/settings/files', handler)
    app.router.add_route('GET', '/api/settings/files/{file_id}', handler)
    app.router.add_route('PUT', '/api/settings/files/{file_id}', handler)
