"""Owner-authored customization editor; executable assets remain inert drafts."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat

try:
    from runtime.config_files import (ConfigError, ConfigFiles, ProfileFiles, PROFILE, MAX_TEXT,
                                     _directory, _read, _atomic, _digest, _text, yaml_load)
except ModuleNotFoundError:
    from _personal_agent_config_files import (ConfigError, ConfigFiles, ProfileFiles, PROFILE, MAX_TEXT,
                                             _directory, _read, _atomic, _digest, _text, yaml_load)

KINDS = {'skill', 'tool', 'plugin', 'script'}
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}\Z')
ASSET_ID = re.compile(r'(?:owner|draft):[a-z0-9][a-z0-9_-]{0,63}\Z|installed:[a-f0-9]{64}\Z')
NOTE = ('Owner-created skills are active for this agent. Trusted project skills are editable; '
        'other installed skills are read-only. Tools, plugins and scripts are nonexecuted drafts. '
        'External imports must use chat quarantine; this editor does not approve or install imports.')


def validate_content(kind, slug, content):
    if not isinstance(content, str):
        raise ConfigError()
    try:
        raw = content.encode('utf-8')
    except UnicodeError:
        raise ConfigError() from None
    _text(raw)
    if kind == 'skill':
        match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', content, re.S)
        if not match:
            raise ConfigError()
        metadata = yaml_load(match.group(1))
        if (set(metadata) - {'name', 'description', 'metadata'} or metadata.get('name') != slug
                or not isinstance(metadata.get('description'), str)
                or not 1 <= len(metadata['description']) <= 1000):
            raise ConfigError()
        extra = metadata.get('metadata', {})
        if 'metadata' in metadata:
            hermes = extra.get('hermes') if isinstance(extra, dict) and set(extra) == {'hermes'} else None
            if (not isinstance(hermes, dict) or set(hermes) - {'category', 'tags'}
                    or ('category' in hermes and not isinstance(hermes['category'], str))
                    or ('tags' in hermes and (not isinstance(hermes['tags'], list)
                        or len(hermes['tags']) > 30 or any(not isinstance(tag, str) or len(tag) > 100 for tag in hermes['tags'])))):
                raise ConfigError()
    return raw


@contextmanager
def _child(parent, name, *, create=False):
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


class Customization:
    """Only discovered profiles and fixed asset roots; never caller filesystem paths."""
    def __init__(self, home):
        self.profiles = ProfileFiles(home)

    @contextmanager
    def _locked(self, profile):
        editor = self.profiles._editor(profile)
        try:
            with _directory(editor.home) as home:
                lock = os.open('.customization.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=home)
                try:
                    info = os.fstat(lock)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ConfigError()
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    yield editor, home
                finally:
                    os.close(lock)
        except OSError:
            raise ConfigError() from None

    @staticmethod
    def _kind(kind):
        if kind not in KINDS:
            raise ConfigError()

    def _inventory(self, home, kind):
        items = {}
        def add(asset_id, slug, parts, status, editable):
            if len(items) >= 512:
                raise ConfigError(503)
            items[asset_id] = {'id': asset_id, 'name': slug, 'kind': kind, 'status': status,
                               'editable': editable, '_parts': parts}
        if kind == 'skill':
            visited = 0
            def walk(fd, parts):
                nonlocal visited
                visited += 1
                if visited > 2048 or len(parts) > 10:
                    raise ConfigError(503)
                for name in sorted(os.listdir(fd)):
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode) and SLUG.fullmatch(name):
                        with _child(fd, name) as child:
                            walk(child, (*parts, name))
                    elif name == 'SKILL.md' and stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        slug = parts[-1]
                        owner = len(parts) == 3 and parts[1] == 'owner-created'
                        editable = owner or (len(parts) == 3 and parts[1] == 'project')
                        asset_id = 'owner:' + slug if owner else 'installed:' + _digest('/'.join(parts).encode())
                        add(asset_id, slug, (*parts, name), 'active' if editable else 'installed', editable)
            try:
                with _child(home, 'skills') as skills:
                    walk(skills, ('skills',))
            except FileNotFoundError:
                pass
        else:
            try:
                with _child(home, '.customization-drafts') as drafts, _child(drafts, kind) as directory:
                    for name in sorted(os.listdir(directory)):
                        slug = name[:-4] if name.endswith('.txt') else ''
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        if SLUG.fullmatch(slug) and stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                            add('draft:' + slug, slug, ('.customization-drafts', kind, name), 'draft', True)
            except FileNotFoundError:
                pass
        return items

    @staticmethod
    def _public(item):
        return {k: v for k, v in item.items() if k != '_parts'}

    @contextmanager
    def _asset(self, home, parts, *, create=False):
        from contextlib import ExitStack
        with ExitStack() as stack:
            parent = home
            for part in parts[:-1]:
                parent = stack.enter_context(_child(parent, part, create=create))
            yield parent, parts[-1]

    def _snapshot(self, item, raw):
        if raw is None:
            raise ConfigError(404)
        return {**self._public(item), 'content': _text(raw), 'revision': _digest(raw)}

    def listing(self, profile, kind):
        self._kind(kind)
        with self._locked(profile) as (_, home):
            return {'items': [self._public(v) for v in self._inventory(home, kind).values()], 'note': NOTE}

    def _lookup(self, home, kind, asset_id):
        if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
            raise ConfigError(404)
        item = self._inventory(home, kind).get(asset_id)
        if item is None:
            raise ConfigError(404)
        return item

    def read(self, profile, kind, asset_id):
        self._kind(kind)
        with self._locked(profile) as (_, home):
            item = self._lookup(home, kind, asset_id)
            with self._asset(home, item['_parts']) as (parent, name):
                return self._snapshot(item, _read(parent, name, MAX_TEXT))

    def create(self, profile, kind, slug, content):
        self._kind(kind)
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise ConfigError()
        incoming = validate_content(kind, slug, content)
        parts = ('skills', 'owner-created', slug, 'SKILL.md') if kind == 'skill' else ('.customization-drafts', kind, slug + '.txt')
        item = {'id': ('owner:' if kind == 'skill' else 'draft:') + slug, 'name': slug,
                'kind': kind, 'status': 'active' if kind == 'skill' else 'draft', 'editable': True, '_parts': parts}
        with self._locked(profile) as (_, home):
            current = self._inventory(home, kind)
            if len(current) >= 512:
                raise ConfigError(503)
            # Skill discovery names are global within a profile; refuse shadowing.
            if any(v['name'] == slug for v in current.values()):
                raise ConfigError(409)
            with self._asset(home, parts, create=True) as (parent, name):
                if _read(parent, name, MAX_TEXT) is not None:
                    raise ConfigError(409)
                _atomic(parent, name, incoming)
                return self._snapshot(item, incoming)

    def write(self, profile, kind, asset_id, content, revision):
        self._kind(kind)
        if not isinstance(revision, str) or not re.fullmatch(r'[a-f0-9]{64}|missing', revision):
            raise ConfigError()
        with self._locked(profile) as (editor, home):
            item = self._lookup(home, kind, asset_id)
            if not item['editable']:
                raise ConfigError(403)
            incoming = validate_content(kind, item['name'], content)
            with self._asset(home, item['_parts']) as (parent, name):
                raw = _read(parent, name, MAX_TEXT)
                self._snapshot(item, raw)
                if _digest(raw) != revision:
                    raise ConfigError(409)
                editor._backup(home, 'customization-' + _digest(asset_id.encode())[:16], raw)
                if _read(parent, name, MAX_TEXT) != raw:
                    raise ConfigError(409)
                _atomic(parent, name, incoming)
                return self._snapshot(item, incoming)


def register_customization(app, adapter):
    """Register after profile mirroring, retaining the existing native authentication."""
    from aiohttp import web
    store = Customization(os.environ.get('HERMES_HOME', '/workspace/.hermes'))
    async def handler(request):
        if not getattr(adapter, '_api_key', ''):
            return web.json_response({'detail': 'Se requiere autenticación.'}, status=401)
        auth_error = adapter._check_auth(request)
        if auth_error is not None:
            return auth_error
        try:
            query = {}
            for key, default in [('profile', 'default'), ('kind', 'skill')]:
                values = request.query.getall(key, [default])
                if len(values) != 1:
                    raise ConfigError()
                query[key] = values[0]
            item_route = request.path.endswith('/item')
            ids = request.query.getall('id', [])
            if (set(request.query) - {'profile', 'kind', 'id'} or (item_route and len(ids) != 1)
                    or (not item_route and ids)):
                raise ConfigError()
            profile, kind = query['profile'], query['kind']
            if request.method == 'GET':
                result = await asyncio.to_thread(store.read, profile, kind, ids[0]) if item_route else await asyncio.to_thread(store.listing, profile, kind)
            else:
                raw = bytearray()
                async for chunk in request.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ConfigError(413)
                try:
                    payload = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise ConfigError() from None
                keys = {'content', 'revision'} if item_route else {'id', 'content'}
                if not isinstance(payload, dict) or set(payload) != keys:
                    raise ConfigError()
                result = (await asyncio.to_thread(store.write, profile, kind, ids[0], payload['content'], payload['revision'])
                          if item_route else await asyncio.to_thread(store.create, profile, kind, payload['id'], payload['content']))
            return web.json_response(result, headers={'Cache-Control': 'no-store'})
        except ConfigError as error:
            notes = {409: 'El recurso cambió o ya existe. Recárgalo antes de guardar.', 413: 'El recurso supera el tamaño permitido.', 403: 'Este recurso es de solo lectura.'}
            return web.json_response({'detail': notes.get(error.status, 'Recurso o contenido no permitido.')}, status=error.status, headers={'Cache-Control': 'no-store'})
        except Exception:
            return web.json_response({'detail': 'No se pudo acceder a las personalizaciones.'}, status=503, headers={'Cache-Control': 'no-store'})
    app.router.add_route('GET', '/api/settings/customization', handler)
    app.router.add_route('POST', '/api/settings/customization', handler)
    app.router.add_route('GET', '/api/settings/customization/item', handler)
    app.router.add_route('PUT', '/api/settings/customization/item', handler)
