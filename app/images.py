"""Bounded, authenticated image-generation relay and private PNG registry."""
import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import stat
import struct
import time
import zlib

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

MODEL = 'gpt-image-2'
SIZES = {'1024x1024', '1536x1024', '1024x1536'}
QUALITIES = {'low', 'medium', 'high', 'auto'}
RESPONSE_BYTES = 36 * 1024**2
PNG_BYTES = 25 * 1024**2
IMAGE_ID = re.compile(r'[a-f0-9]{32}\Z')
TIMEOUT = 300


class ImageError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def validate_request(value):
    if set(value) != {'model', 'prompt', 'n', 'size', 'quality'}:
        raise ImageError(400, 'invalid_request', 'Envía solo modelo, descripción, cantidad, tamaño y calidad admitidos.')
    if (value['model'] != MODEL or type(value['n']) is not int or value['n'] != 1
            or not isinstance(value['size'], str) or value['size'] not in SIZES
            or not isinstance(value['quality'], str) or value['quality'] not in QUALITIES
            or not isinstance(value['prompt'], str) or not value['prompt'].strip()
            or len(value['prompt']) > 32000 or '\x00' in value['prompt']):
        raise ImageError(400, 'invalid_request', 'Usa gpt-image-2, una imagen, una descripción de hasta 32.000 caracteres y tamaño/calidad admitidos.')
    return value


async def bounded_json(response, limit):
    raw = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=65536):
        if len(raw) + len(chunk) > limit:
            raise ImageError(502, 'invalid_image_response', 'OpenAI devolvió una respuesta demasiado grande.')
        raw.extend(chunk)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ImageError(502, 'invalid_image_response', 'OpenAI no devolvió una imagen válida.')


def validate_png(data, size):
    """Check bounded PNG framing, CRC, header and a single complete pixel stream."""
    invalid = ImageError(502, 'invalid_image_response', 'OpenAI no devolvió una imagen PNG válida del tamaño solicitado.')
    if not 57 <= len(data) <= PNG_BYTES or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise invalid
    position = 8
    dimensions = None
    compressed = bytearray()
    ended = False
    has_palette = False
    idat_ended = False
    while position + 12 <= len(data):
        length = struct.unpack_from('>I', data, position)[0]
        kind = data[position+4:position+8]
        if length > PNG_BYTES or position + 12 + length > len(data):
            raise invalid
        value = data[position+8:position+8+length]
        crc = struct.unpack_from('>I', data, position+8+length)[0]
        if zlib.crc32(kind + value) != crc:
            raise invalid
        if dimensions is None:
            if kind != b'IHDR' or length != 13:
                raise invalid
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', value)
            if (f'{width}x{height}' != size or compression or filtering or interlace
                    or color not in {0, 2, 3, 4, 6}
                    or depth not in ({1, 2, 4, 8, 16} if color == 0 else {1, 2, 4, 8} if color == 3 else {8, 16})):
                raise invalid
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            row_bytes = (width * channels * depth + 7) // 8
            expected = (row_bytes + 1) * height
            dimensions = (width, height)
        elif kind == b'IHDR':
            raise invalid
        elif kind == b'PLTE':
            if compressed or not length or length > 768 or length % 3 or has_palette:
                raise invalid
            has_palette = True
        elif kind == b'IDAT':
            if idat_ended or (color == 3 and not has_palette):
                raise invalid
            compressed.extend(value)
        elif kind == b'IEND':
            if length or not compressed or position + 12 != len(data):
                raise invalid
            ended = True
            break
        elif not kind[0] & 32:
            # Unknown critical chunks are not safe to interpret as PNG.
            raise invalid
        elif compressed:
            idat_ended = True
        position += length + 12
    if not ended:
        raise invalid
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, expected + 1)
        if (len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                or any(pixels[index] > 4 for index in range(0, expected, row_bytes + 1))):
            raise invalid
    except zlib.error:
        raise invalid
    return data


def decode_image(value, size):
    invalid = ImageError(502, 'invalid_image_response', 'OpenAI no devolvió una imagen válida.')
    if not isinstance(value, dict) or not isinstance(value.get('data'), list) or len(value['data']) != 1:
        raise invalid
    item = value['data'][0]
    if not isinstance(item, dict) or not isinstance(item.get('b64_json'), str):
        raise invalid
    encoded = item['b64_json']
    if len(encoded) > ((PNG_BYTES + 2) // 3) * 4:
        raise invalid
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise invalid
    return validate_png(data, size)


def storage_directory(control, create=False):
    """Open the private directory without following a generated-images link."""
    parent = os.open(control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if create:
            try:
                os.mkdir('generated-images', mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
        fd = os.open('generated-images', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
            os.close(fd)
            raise OSError('private directory required')
        return fd
    finally:
        os.close(parent)


def atomic_private(directory, name, content):
    temporary = '.' + secrets.token_hex(16) + '.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def store_image(control, data, payload, created):
    directory = storage_directory(control, create=True)
    identity = secrets.token_hex(16)
    try:
        record = {'id': identity, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                  'created': created, 'model': MODEL, 'size': payload['size'], 'quality': payload['quality']}
        atomic_private(directory, identity + '.png', data)
        try:
            # Metadata is the commit marker: orphaned PNGs are never retrievable.
            atomic_private(directory, identity + '.json', json.dumps(record).encode())
            os.fsync(directory)
        except BaseException:
            os.unlink(identity + '.png', dir_fd=directory)
            raise
        return '/api/images/' + identity
    finally:
        os.close(directory)


def private_read(directory, name, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > limit:
            raise OSError('invalid private file')
        result = stream.read(limit + 1)
    if len(result) > limit:
        raise OSError('file too large')
    return result


def read_image(control, identity):
    directory = storage_directory(control)
    try:
        record = json.loads(private_read(directory, identity + '.json', 4096))
        if (not isinstance(record, dict) or record.get('id') != identity
                or not isinstance(record.get('size'), str) or record['size'] not in SIZES
                or record.get('model') != MODEL):
            raise ValueError()
        data = private_read(directory, identity + '.png', PNG_BYTES)
        if record.get('bytes') != len(data) or record.get('sha256') != hashlib.sha256(data).hexdigest():
            raise ValueError()
        return validate_png(data, record['size'])
    finally:
        os.close(directory)


def upstream_error(status, value):
    error = value.get('error') if isinstance(value, dict) else None
    code = error.get('code') if isinstance(error, dict) else None
    credit = isinstance(code, str) and code in {'insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active'}
    if credit:
        return ImageError(status, 'insufficient_quota', 'OpenAI rechazó la generación por saldo o facturación. Revisa tu cuenta.')
    if status == 401:
        return ImageError(status, 'invalid_api_key', 'OpenAI rechazó la clave. Revisa Secretos.')
    if status in {403, 404}:
        return ImageError(status, 'image_access_denied', 'Tu cuenta no tiene acceso disponible al modelo de imágenes.')
    if status == 429:
        return ImageError(status, 'rate_limit_exceeded', 'OpenAI limitó las solicitudes. Espera antes de solicitar otra imagen.')
    return ImageError(status, 'provider_error', 'OpenAI rechazó la generación. Revisa acceso al modelo, saldo y descripción.')


def register_images(app, owner, service, body, vault, control, provider_gate):
    in_flight = asyncio.Lock()

    @app.post('/provider/v1/images/generations')
    async def generate(request: Request):
        service(request)
        try:
            if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                raise ImageError(415, 'invalid_content_type', 'La generación solo admite un objeto JSON.')
            payload = validate_request(await body(request))
            key = vault()['openai_key']
            if not key:
                raise ImageError(503, 'api_key_not_configured', 'Configura tu clave de OpenAI en Secretos.')
            if in_flight.locked():
                raise ImageError(409, 'image_generation_busy', 'Ya hay una imagen en proceso. No repitas la solicitud.')
            async with in_flight:
                try:
                    provider_gate()
                except HTTPException as exc:
                    raise ImageError(exc.status_code, 'rate_limit_exceeded', 'Se alcanzó el límite compartido de solicitudes al proveedor.') from None
                # Refuse a linked or non-private directory before issuing a paid call.
                if os.path.lexists(control / 'generated-images'):
                    fd = storage_directory(control)
                    os.close(fd)
                async with asyncio.timeout(TIMEOUT):
                    req = app.state.external.build_request('POST', 'https://api.openai.com/v1/images/generations',
                        json=payload, headers={'Authorization': 'Bearer ' + key, 'Accept-Encoding': 'identity'},
                        timeout=httpx.Timeout(20, read=TIMEOUT))
                    response = await app.state.external.send(req, stream=True)
                    try:
                        if response.headers.get('content-encoding', '').lower() not in {'', 'identity'}:
                            raise ImageError(502, 'invalid_image_response', 'OpenAI devolvió una respuesta con codificación no admitida.')
                        if response.status_code != 200:
                            status = response.status_code
                            if not 400 <= status <= 599:
                                raise ImageError(502, 'provider_error', 'OpenAI no devolvió una imagen válida.')
                            try:
                                error = await bounded_json(response, 65536)
                            except ImageError:
                                error = None
                            raise upstream_error(status, error)
                        result = await bounded_json(response, RESPONSE_BYTES)
                    finally:
                        await response.aclose()
                    data = decode_image(result, payload['size'])
                    created = result.get('created')
                    if type(created) is not int or not 0 <= created <= 2**53:
                        created = int(time.time())
                    url = store_image(control, data, payload, created)
                    return {'created': created, 'data': [{'b64_json': base64.b64encode(data).decode(), 'portal_url': url}],
                            'model': MODEL, 'size': payload['size'], 'quality': payload['quality']}
        except (TimeoutError, httpx.TimeoutException):
            error = ImageError(504, 'image_generation_timeout', 'La generación excedió el tiempo de espera. No se reintentó; revisa el consumo antes de repetirla.')
        except httpx.HTTPError:
            error = ImageError(502, 'provider_unavailable', 'No se pudo completar la conexión con OpenAI. No se reintentó la generación.')
        except OSError:
            error = ImageError(503, 'image_storage_unavailable', 'El almacenamiento privado de imágenes no está disponible. No se reintentó la generación.')
        except ImageError as exc:
            error = exc
        return JSONResponse({'error': {'message': error.message, 'type': 'image_generation_error',
                            'code': error.code, 'status': error.status}}, status_code=error.status)

    @app.get('/api/images/{identity}')
    async def image(request: Request, identity: str):
        owner(request)
        if not IMAGE_ID.fullmatch(identity):
            raise HTTPException(404, 'Imagen no encontrada.')
        try:
            data = read_image(control, identity)
        except (OSError, ValueError, TypeError, ImageError):
            raise HTTPException(404, 'Imagen no encontrada.')
        return Response(data, media_type='image/png', headers={'Content-Disposition': 'inline; filename="generated-image.png"'})
