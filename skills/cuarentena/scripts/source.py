"""Pinned public GitHub acquisition shared by the portal and read-only CLI."""
import asyncio
import base64
import hashlib
import io
import json
from pathlib import PurePosixPath
import re
import stat
import time
import zipfile
import zlib
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SOURCE_BYTES = 5 * 1024**2
MEMBER_BYTES = 512 * 1024
# Transport budget is separate from the retained, inspectable source package.
REMOTE_ARCHIVE_BYTES = 64 * 1024**2
MAX_SOURCE_FILES = 512
OPAQUE_ASSETS = {'.jpg','.jpeg','.png','.webp','.gif','.ico','.avif','.mp4','.mov','.mp3','.wav','.woff','.woff2','.ttf','.otf','.pdf','.zip','.gz','.tar','.tgz','.bz2','.xz','.7z','.rar','.whl','.jar','.exe','.dll','.so','.dylib','.bin','.o','.class'}
EXTENSIONS = {'.md','.txt','.py','.js','.ts','.mjs','.cjs','.sh','.bash','.json','.toml','.yaml','.yml','.go','.rs'}


class SourceError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def parse_source(url):
    if not isinstance(url,str) or not 1 <= len(url) <= 2048:
        raise SourceError('invalid_github_source')
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise SourceError('invalid_github_source') from error
    if parsed.scheme!='https' or parsed.netloc!='github.com' or parsed.query or parsed.fragment or '%' in parsed.path or '\\' in url:
        raise SourceError('canonical_public_github_url_required')
    parts = parsed.path.strip('/').split('/')
    if len(parts)<2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',p) and p not in {'.','..'} for p in parts):
        raise SourceError('invalid_github_path')
    owner, repo = parts[:2]
    repo = repo.removesuffix('.git')
    if not repo or repo in {'.','..'}:
        raise SourceError('invalid_repository')
    ref, path = None, ''
    if len(parts)>2:
        if len(parts)<4 or parts[2] not in {'tree','blob'}:
            raise SourceError('explicit_tree_or_blob_reference_required')
        ref, path = parts[3], '/'.join(parts[4:])
        if parts[2]=='blob' and (not path or (PurePosixPath(path).suffix.lower() not in EXTENSIONS and PurePosixPath(path).name not in {'Dockerfile','Makefile','LICENSE'})):
            raise SourceError('supported_source_file_required')
    return owner, repo, ref, path


def accept_type(url):
    parsed = urlsplit(url)
    return 'application/vnd.github.sha' if parsed.netloc=='api.github.com' and '/commits/' in parsed.path else 'application/vnd.github+json'


def select_archive(data, path):
    """Keep bounded skill/tool source from a large immutable repository archive.

    Never extract to disk or decompress unrelated assets. Selection is explicit
    evidence, not a complete-repository assessment or an approval artifact.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 20000:
                raise SourceError('repository_inventory_too_large')
            files, seen, roots = [], set(), set()
            for item in entries:
                name = item.filename.rstrip('/')
                parts = name.split('/')
                if (item.orig_filename != item.filename or len(name)>240 or len(parts)>9 or
                        any(ord(c)<32 or ord(c)>126 for c in name) or '\\' in name or
                        any(not p or p in {'.','..'} or p.endswith(('.', ' ')) or ':' in p for p in parts)):
                    raise SourceError('unsafe_repository_path')
                key = name.casefold()
                if key in seen:
                    raise SourceError('duplicate_repository_path')
                seen.add(key)
                kind = stat.S_IFMT(item.external_attr >> 16)
                if item.flag_bits & 1 or kind not in {0,stat.S_IFREG,stat.S_IFDIR}:
                    raise SourceError('special_or_encrypted_repository_member')
                roots.add(parts[0])
                if item.is_dir():
                    if kind==stat.S_IFREG:
                        raise SourceError('invalid_repository_member')
                    continue
                if kind == stat.S_IFDIR or len(parts)<2:
                    raise SourceError('invalid_repository_member')
                files.append((item, '/'.join(parts[1:])))
            file_names = {item.filename.casefold() for item,_ in files}
            if any('/'.join(name.split('/')[:n]) in file_names for name in seen for n in range(1,len(name.split('/')))):
                raise SourceError('repository_file_directory_collision')
            if len(roots)!=1:
                raise SourceError('invalid_github_archive')
            in_scope = [(item,logical) for item,logical in files
                        if not path or logical==path or logical.startswith(path.rstrip('/')+'/')]
            skill_roots = sorted({str(PurePosixPath(logical).parent) for _,logical in in_scope
                                  if PurePosixPath(logical).name=='SKILL.md'})
            root_set = set(skill_roots)
            selected_roots = skill_roots or [path or '.']
            context = {'README.md','LICENSE','LICENSE.md','package.json','pyproject.toml','requirements.txt','Cargo.toml'}
            selected = [(item,logical) for item,logical in in_scope if not skill_roots or
                        any(str(parent) in root_set for parent in PurePosixPath(logical).parents) or
                        str(PurePosixPath(logical).parent)==(path or '.') and PurePosixPath(logical).name in context]
            # Scripts/manifests lead when resource budgets require partial coverage.
            selected.sort(key=lambda pair:(0 if PurePosixPath(pair[1]).suffix in {'.py','.js','.mjs','.cjs','.sh','.json','.toml'} or PurePosixPath(pair[1]).name=='SKILL.md' else 1,pair[1]))
            output, package_bytes, omitted, reasons = {}, 22, 0, {}
            for item,logical in selected:
                packaged_name = 'r/'+logical
                # ZIP_STORED local header + central header + both filename copies.
                encoded_size = len(packaged_name.encode('utf-8'))
                member_bytes = item.file_size+76+2*encoded_size
                reason = ('opaque_assets' if PurePosixPath(logical).suffix.lower() in OPAQUE_ASSETS else
                          'large_source_members' if item.file_size>MEMBER_BYTES else
                          'source_batch_budget' if len(output)>=MAX_SOURCE_FILES or package_bytes+member_bytes>SOURCE_BYTES else None)
                if reason:
                    omitted += 1
                    reasons[reason] = reasons.get(reason,0)+1
                    continue
                # Compression ratio remains a guard; selected source is not executed.
                if item.file_size>max(item.compress_size,1)*100:
                    raise SourceError('archive_expansion_limit')
                with archive.open(item) as member:
                    raw = member.read(MEMBER_BYTES+1)
                if len(raw)!=item.file_size or len(raw)>MEMBER_BYTES:
                    raise SourceError('archive_size_mismatch')
                output[packaged_name] = raw
                package_bytes += member_bytes
            if not output:
                raise SourceError('no_inspectable_source')
            result = io.BytesIO()
            # Stored entries cannot themselves trigger a compression-ratio refusal.
            with zipfile.ZipFile(result,'w',zipfile.ZIP_STORED) as package:
                for logical,raw in output.items():
                    info = zipfile.ZipInfo(logical)
                    info.external_attr = (stat.S_IFREG|0o644)<<16
                    package.writestr(info,raw)
            if len(result.getvalue())>SOURCE_BYTES:
                raise SourceError('source_size_limit',413)
            selection = {'roots':selected_roots[:32], 'roots_total':len(selected_roots), 'received_files':len(output),
                         'scoped_files':len(selected), 'omitted_files':omitted,
                         'omission_reasons':reasons,
                         'excluded_repository_files':len(files)-len(selected),
                         'method':'selected_source_archive'}
            return result.getvalue(), selection
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError,EOFError,OSError,zlib.error) as error:
        raise SourceError('invalid_repository_archive') from error


def needs_selection(data):
    """Detect archive-wide budgets before unrelated content blocks skill intake."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            return (len(entries)>MAX_SOURCE_FILES or sum(i.file_size for i in entries)>8*1024**2 or
                    any(i.file_size>MEMBER_BYTES for i in entries))
    except zipfile.BadZipFile:
        return False  # The regular archive validator returns the invalid-input evidence.


async def load_github(url, fetch, timeout=15):
    owner, repo, ref, path = parse_source(url)
    prefix = 'https://api.github.com/repos/'+owner+'/'+repo
    try:
        async with asyncio.timeout(timeout):
            metadata = json.loads(await fetch(prefix,65536))
            if not isinstance(metadata,dict) or metadata.get('private') is not False:
                raise SourceError('public_repository_required')
            ref = ref or metadata['default_branch']
            if not isinstance(ref,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',ref):
                raise SourceError('unsupported_reference')
            identity = (await fetch(prefix+'/commits/'+quote(ref,safe=''),80)).decode().strip()
            commit = identity if re.fullmatch(r'[a-f0-9]{40}',identity) else json.loads(identity)['sha']
            if not isinstance(commit,str) or not re.fullmatch(r'[a-f0-9]{40}',commit):
                raise SourceError('invalid_commit')
            provenance = {'kind':'github','url':url,'repository':owner+'/'+repo,'commit':commit,'path':path}
            if urlsplit(url).path.strip('/').split('/')[2:3]==['blob']:
                descriptor = json.loads(await fetch(prefix+'/contents/'+quote(path,safe='/')+'?ref='+commit,1024*1024))
                if not isinstance(descriptor,dict) or descriptor.get('type')!='file' or descriptor.get('path')!=path or descriptor.get('encoding')!='base64':
                    raise SourceError('regular_source_file_required')
                size = descriptor.get('size')
                if not isinstance(size,int) or isinstance(size,bool) or not 0 < size <= MEMBER_BYTES:
                    raise SourceError('source_size_limit',413)
                data = base64.b64decode(''.join(descriptor['content'].split()),validate=True)
                blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
                if len(data)!=size or descriptor.get('sha')!=blob:
                    raise SourceError('github_file_integrity_failed')
                provenance['filename'] = PurePosixPath(path).name
            else:
                archive_url = 'https://codeload.github.com/'+owner+'/'+repo+'/zip/'+commit
                try:
                    data = await fetch(archive_url,SOURCE_BYTES)
                except ValueError as error:
                    if str(error)!='source_size_limit':
                        raise
                    upstream = await fetch(archive_url,REMOTE_ARCHIVE_BYTES)
                    data, provenance['selection'] = select_archive(upstream,path)
                    provenance['upstream_sha256'] = hashlib.sha256(upstream).hexdigest()
                else:
                    if needs_selection(data):
                        provenance['upstream_sha256'] = hashlib.sha256(data).hexdigest()
                        data, provenance['selection'] = select_archive(data,path)
    except SourceError:
        raise
    except TimeoutError as error:
        raise SourceError('github_timeout',504) from error
    except (OSError,ValueError,KeyError,TypeError,UnicodeError,AttributeError) as error:
        code = 'source_size_limit' if str(error)=='source_size_limit' else 'github_source_unavailable'
        raise SourceError(code,413 if code=='source_size_limit' else 400) from error
    return data, provenance


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise SourceError('redirect_forbidden')


def _fetch(url,limit):
    parsed = urlsplit(url)
    if parsed.scheme!='https' or parsed.netloc not in {'api.github.com','codeload.github.com'}:
        raise SourceError('invalid_fetch_host')
    # Explicit public-source review never reuses proxy credentials or GitHub authentication.
    opener = build_opener(ProxyHandler({}),NoRedirect())
    request = Request(url,headers={'Accept':accept_type(url),'User-Agent':'cuarentena-source-audit'})
    deadline = time.monotonic()+12
    with opener.open(request,timeout=5) as response:
        if response.status!=200:
            raise SourceError('github_source_unavailable')
        if int(response.headers.get('content-length','0'))>limit:
            raise SourceError('source_size_limit',413)
        data=bytearray()
        while True:
            if time.monotonic()>deadline:
                raise SourceError('github_timeout',504)
            chunk=response.read(min(65536,limit+1-len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data)>limit:
                raise SourceError('source_size_limit',413)


async def cli_fetch(url,limit):
    return await asyncio.to_thread(_fetch,url,limit)
