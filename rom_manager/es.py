import urllib.parse

import aiohttp
from aiohttp import web

from .config import ES_API

ES_TIMEOUT = aiohttp.ClientTimeout(total=5)
MEDIA_TIMEOUT = aiohttp.ClientTimeout(sock_connect=5, sock_read=30)
FORWARDED_HEADERS = ("Content-Type", "Content-Length", "Content-Range")


async def get_json(session, path):
    async with session.get(ES_API + path, timeout=ES_TIMEOUT) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def systems(session):
    return await get_json(session, "/systems")


async def games(session, system):
    return await get_json(session, "/systems/%s/games" % urllib.parse.quote(system))


async def proxy_media(request, session, path, content_type=None):
    headers = {"Range": request.headers["Range"]} if "Range" in request.headers else {}
    async with session.get(ES_API + path, headers=headers, timeout=MEDIA_TIMEOUT) as upstream:
        if upstream.status not in (200, 206):
            raise web.HTTPNotFound()
        response = web.StreamResponse(status=upstream.status, headers={
            name: upstream.headers[name] for name in FORWARDED_HEADERS if name in upstream.headers})
        if content_type:
            response.headers["Content-Type"] = content_type
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Cache-Control"] = "private, max-age=86400"
        await response.prepare(request)
        async for chunk in upstream.content.iter_chunked(1 << 16):
            await response.write(chunk)
        await response.write_eof()
        return response
