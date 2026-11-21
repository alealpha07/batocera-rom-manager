import functools
import glob
import os
import urllib.parse
import xml.etree.ElementTree as ET

import aiohttp
from aiohttp import web

from ..config import ES_API

ES_SYSTEMS_FILE = "/usr/share/emulationstation/es_systems.cfg"
USER_ES_SYSTEMS_FILES = "/userdata/system/configs/emulationstation/es_systems_*.cfg"
API_TIMEOUT = aiohttp.ClientTimeout(total=5)
RELOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)
MEDIA_TIMEOUT = aiohttp.ClientTimeout(sock_connect=5, sock_read=30)
FORWARDED_MEDIA_HEADERS = ("Content-Type", "Content-Length", "Content-Range")


@functools.cache
def all_systems():
    systems = {}
    for path in [ES_SYSTEMS_FILE] + sorted(glob.glob(USER_ES_SYSTEMS_FILES)):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        for system in root.iter("system"):
            name, folder = system.findtext("name"), system.findtext("path")
            if name and folder:
                systems[name] = {"name": name, "fullname": system.findtext("fullname") or name,
                                 "path": os.path.expanduser(folder.strip()),
                                 "extensions": (system.findtext("extension") or "").lower().split()}
    return systems


class EmulationStation:
    def __init__(self):
        self.session = None

    async def open(self):
        self.session = aiohttp.ClientSession()

    async def close(self):
        await self.session.close()

    async def get_json(self, path):
        async with self.session.get(ES_API + path, timeout=API_TIMEOUT) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def systems(self):
        return await self.get_json("/systems")

    async def games(self, system):
        return await self.get_json("/systems/%s/games" % urllib.parse.quote(system))

    async def reload_games(self):
        async with self.session.get(ES_API + "/reloadgames", timeout=RELOAD_TIMEOUT) as response:
            response.raise_for_status()

    async def proxy_media(self, request, path, content_type=None):
        headers = {"Range": request.headers["Range"]} if "Range" in request.headers else {}
        async with self.session.get(ES_API + path, headers=headers, timeout=MEDIA_TIMEOUT) as upstream:
            if upstream.status not in (200, 206):
                raise web.HTTPNotFound()
            response = web.StreamResponse(status=upstream.status, headers={
                name: upstream.headers[name] for name in FORWARDED_MEDIA_HEADERS if name in upstream.headers})
            if content_type:
                response.headers["Content-Type"] = content_type
            response.headers["Accept-Ranges"] = "bytes"
            response.headers["Cache-Control"] = "private, max-age=86400"
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(1 << 16):
                await response.write(chunk)
            await response.write_eof()
            return response
