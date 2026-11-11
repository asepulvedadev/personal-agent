"""Image relay boundaries; fake provider only, never a paid request."""
import asyncio
import base64
import json
import re
import struct
import zlib

import httpx
import pytest
from test_portal import portal, login

ENDPOINT = '/provider/v1/images/generations'
AUTH = {'Authorization': 'Bearer runtime-test-token-with-enough-entropy'}
PAYLOAD = {'model': 'gpt-image-2', 'prompt': 'A blue square', 'n': 1,
           'size': '1024x1024', 'quality': 'low'}


def png(width=1024, height=1024):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\x00\x33\x66' * width) * height)) + chunk(b'IEND', b''))


def provider(portal, *, status=200, result=None, failure=None):
    m, c = portal
    c.post('/api/vault', json={'openai_key': 'sk-image-private'}, headers=login(c))
    calls = []
    class External:
        async def aclose(self): pass
        def build_request(self, method, url, **kwargs):
            assert method == 'POST' and url == 'https://api.openai.com/v1/images/generations'
            assert kwargs['headers']['Authorization'] == 'Bearer sk-image-private'
            assert kwargs['json'] == PAYLOAD
            assert 'response_format' not in kwargs['json']
            assert kwargs['timeout'].read <= 300
            calls.append(kwargs)
            return httpx.Request(method, url)
        async def send(self, request, **kwargs):
            assert kwargs.get('stream') is True
            if failure: raise failure
            return httpx.Response(status, json=result if result is not None else {
                'created': 1234, 'data': [{'b64_json': base64.b64encode(png()).decode()}],
                'usage': {'private': 'do-not-leak'}, 'prompt': 'do-not-persist'}, request=request)
    m.app.state.external = External()
    return m, c, calls


def test_service_auth_before_parsing_or_provider(portal):
    _, c = portal
    assert c.post(ENDPOINT, content=b'not-json').status_code == 401
    assert c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).status_code == 503
    login(c)
    assert c.post(ENDPOINT, json=PAYLOAD).status_code == 401
    assert c.post('/provider/v1/images/edits', json={}, headers=AUTH).status_code == 404


@pytest.mark.parametrize('change', [
    {'model': 'other'}, {'n': 2}, {'n': True}, {'size': 'auto'}, {'size': []},
    {'quality': 'max'}, {'prompt': ''}, {'prompt': 'x' * 32001}, {'prompt': []},
    {'response_format': 'url'}, {'image': 'https://evil.test/pic'}, {'stream': True},
    {'output_format': 'jpeg'}, {'user': 'unexpected'},
])
def test_rejects_unsupported_input_without_billing(portal, change):
    _, c, calls = provider(portal)
    response = c.post(ENDPOINT, json={**PAYLOAD, **change}, headers=AUTH)
    assert response.status_code == 400
    assert not calls


def test_json_only_and_required_controls(portal):
    _, c, calls = provider(portal)
    assert c.post(ENDPOINT, content='{}', headers={**AUTH, 'Content-Type': 'multipart/form-data'}).status_code == 415
    for field in PAYLOAD:
        body = {k: v for k, v in PAYLOAD.items() if k != field}
        assert c.post(ENDPOINT, json=body, headers=AUTH).status_code == 400
    assert not calls


def test_validated_png_is_private_registered_and_response_is_sanitized(portal):
    m, c, calls = provider(portal)
    response = c.post(ENDPOINT, json=PAYLOAD, headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data['created'] == 1234
    assert set(data) <= {'created', 'data', 'model', 'size', 'quality'}
    entry = data['data'][0]
    assert set(entry) == {'b64_json', 'portal_url'}
    assert re.fullmatch(r'/api/images/[a-f0-9]{32}', entry['portal_url'])
    assert base64.b64decode(entry['b64_json']) == png()
    assert len(calls) == 1
    root = m.CONTROL / 'generated-images'
    assert root.stat().st_mode & 0o777 == 0o700
    assert len(list(root.iterdir())) == 2
    for path in root.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        assert b'do-not-persist' not in path.read_bytes()
    c.cookies.clear()
    assert c.get(entry['portal_url']).status_code == 401
    assert c.get(entry['portal_url'], headers=AUTH).status_code == 401
    login(c)
    image = c.get(entry['portal_url'])
    assert image.status_code == 200 and image.content == png()
    assert image.headers['content-type'] == 'image/png'
    assert image.headers['cache-control'] == 'no-store'
    assert c.head(entry['portal_url']).status_code in {200, 405}
    assert c.get('/api/images/' + '0' * 32).status_code == 404
    assert c.get('/api/images/not-an-id').status_code == 404
    assert c.get('/api/images/%2e%2e%2fauth.json').status_code == 404


@pytest.mark.parametrize('data', [
    [{'url': 'https://evil.test/private'}], [{'b64_json': '???'}],
    [{'b64_json': base64.b64encode(b'not-png').decode()}], [],
    [{'b64_json': base64.b64encode(png(1, 1)).decode()}],
    [{'b64_json': base64.b64encode(png()).decode()}] * 2,
])
def test_untrusted_image_response_never_falls_back_to_url(portal, data):
    m, c, calls = provider(portal, result={'created': 1, 'data': data})
    r = c.post(ENDPOINT, json=PAYLOAD, headers=AUTH)
    assert r.status_code == 502
    assert len(calls) == 1
    assert not (m.CONTROL / 'generated-images').exists()
    assert 'evil.test' not in r.text


@pytest.mark.parametrize('status, code', [(401, 'invalid_api_key'), (403, 'image_access_denied'),
    (429, 'rate_limit_exceeded'), (500, 'provider_error')])
def test_upstream_failure_sanitized_and_status_retained(portal, status, code):
    _, c, calls = provider(portal, status=status, result={'error': {
        'message': 'sk-image-private A blue square', 'code': 'whatever'}})
    r = c.post(ENDPOINT, json=PAYLOAD, headers=AUTH)
    assert r.status_code == status
    assert r.json()['error']['code'] == code
    assert r.json()['error']['status'] == status
    assert 'sk-image-private' not in r.text and 'A blue square' not in r.text
    assert len(calls) == 1


def test_insufficient_credit_distinguished(portal):
    _, c, _ = provider(portal, status=429, result={'error': {'code': 'insufficient_quota'}})
    assert c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).json()['error']['code'] == 'insufficient_quota'


def test_timeout_does_not_retry_and_releases_guard(portal):
    m, c, calls = provider(portal, failure=httpx.ReadTimeout('private'))
    assert c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).status_code == 504
    assert c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).status_code == 504
    assert len(calls) == 2


def test_shared_existing_provider_rate_budget(portal):
    m, c, calls = provider(portal)
    import time
    m.provider_attempts[:] = [time.time()] * 40
    response = c.post(ENDPOINT, json=PAYLOAD, headers=AUTH)
    assert response.status_code == 429
    assert response.json()['error']['status'] == 429
    assert response.json()['error']['code'] == 'rate_limit_exceeded'
    assert not calls and len(m.provider_attempts) == 40


def test_oversized_response_is_bounded(portal, monkeypatch):
    from app import images
    monkeypatch.setattr(images, 'RESPONSE_BYTES', 64)
    _, c, calls = provider(portal)
    assert c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).status_code == 502
    assert len(calls) == 1


def test_symlink_storage_and_unregistered_files_are_not_served(portal):
    m, c, _ = provider(portal)
    directory = m.CONTROL / 'generated-images'
    directory.mkdir(mode=0o700)
    (directory / ('a' * 32 + '.png')).write_bytes(png())
    assert c.get('/api/images/' + 'a' * 32).status_code == 404
    (directory / ('a' * 32 + '.png')).unlink()
    directory.rmdir()
    directory.symlink_to(m.CONTROL, target_is_directory=True)
    assert c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).status_code == 503
    assert c.get('/api/images/' + 'a' * 32).status_code == 404


def test_concurrent_images_rejected_without_second_billed_request(portal):
    m, c, _ = provider(portal)
    entered = asyncio.Event()
    release = asyncio.Event()
    class External:
        async def aclose(self): pass
        def build_request(self, *args, **kwargs): return httpx.Request('POST', 'https://api.openai.com/v1/images/generations')
        async def send(self, request, **kwargs):
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'created': 1, 'data': [{'b64_json': base64.b64encode(png()).decode()}]}, request=request)
    m.app.state.external = External()
    async def burst():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app), base_url='https://agent.example.test') as client:
            first = asyncio.create_task(client.post(ENDPOINT, json=PAYLOAD, headers=AUTH))
            await entered.wait()
            second = await client.post(ENDPOINT, json=PAYLOAD, headers=AUTH)
            assert second.status_code == 409
            release.set()
            assert (await first).status_code == 200
    asyncio.run(burst())


def test_owner_retrieval_refuses_links_and_tampering(portal):
    m, c, _ = provider(portal)
    url = c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).json()['data'][0]['portal_url']
    identity = url.rsplit('/', 1)[1]
    path = m.CONTROL / 'generated-images' / (identity + '.png')
    original = path.read_bytes()
    path.write_bytes(b'not-a-valid-image')
    assert c.get(url).status_code == 404
    path.unlink()
    path.symlink_to(m.CONTROL / 'auth.json')
    assert c.get(url).status_code == 404
    path.unlink()
    path.write_bytes(original)
    path.chmod(0o600)
    (path.parent / (identity + '.json')).unlink()
    assert c.get(url).status_code == 404


def test_corrupt_png_rejected(portal):
    malformed = bytearray(png())
    malformed[-1] ^= 1
    _, c, _ = provider(portal, result={'data': [{'b64_json': base64.b64encode(malformed).decode()}]})
    assert c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).status_code == 502


def test_malformed_provider_error_code_cannot_escape_sanitizer(portal):
    _, c, _ = provider(portal, status=400, result={'error': {'code': ['bad'], 'message': 'private'}})
    response = c.post(ENDPOINT, json=PAYLOAD, headers=AUTH)
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'provider_error'
    assert 'private' not in response.text


def test_expanding_pixel_stream_is_rejected_without_unbounded_decode(portal):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    malformed = (b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', 1024, 1024, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(b'\0' * (20 * 1024**2))) + chunk(b'IEND', b''))
    _, c, _ = provider(portal, result={'data': [{'b64_json': base64.b64encode(malformed).decode()}]})
    assert c.post(ENDPOINT, json=PAYLOAD, headers=AUTH).status_code == 502
