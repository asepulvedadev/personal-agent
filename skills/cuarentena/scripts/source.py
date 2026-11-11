"""Pinned public GitHub acquisition shared by the portal and read-only CLI."""
import asyncio
import base64
import hashlib
import json
from pathlib import PurePosixPath
import re
import time
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

SOURCE_BYTES = 5 * 1024**2
MEMBER_BYTES = 512 * 1024
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
                data = await fetch('https://codeload.github.com/'+owner+'/'+repo+'/zip/'+commit,SOURCE_BYTES)
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
