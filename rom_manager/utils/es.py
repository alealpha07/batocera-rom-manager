import asyncio
import functools
import glob
import os
import urllib.parse
import xml.etree.ElementTree as ET
import xml.sax.saxutils

import aiohttp
from aiohttp import web

from ..config import ES_API

ES_SYSTEMS_FILE = "/usr/share/emulationstation/es_systems.cfg"
USER_ES_SYSTEMS_FILES = "/userdata/system/configs/emulationstation/es_systems_*.cfg"
API_TIMEOUT = aiohttp.ClientTimeout(total=5)
RELOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)
UPLOAD_TIMEOUT = aiohttp.ClientTimeout(sock_connect=5, sock_read=60)
RELOAD_SETTLE_SECONDS = 1
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


def game_path(system, game_id):
    return "/systems/%s/games/%s" % (urllib.parse.quote(system), urllib.parse.quote(game_id))


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

    async def game(self, system, game_id):
        return await self.get_json(game_path(system, game_id))

    async def update_game(self, system, game_id, fields):
        async with self.session.post(ES_API + game_path(system, game_id), json=fields, timeout=API_TIMEOUT) as response:
            response.raise_for_status()

    async def remove_game(self, system, path):
        body = "<gameList><game><path>%s</path></game></gameList>" % xml.sax.saxutils.escape(path)
        url = ES_API + "/removegames/" + urllib.parse.quote(system)
        async with self.session.post(url, data=body.encode(), timeout=API_TIMEOUT) as response:
            response.raise_for_status()

    async def reload_games(self):
        async with self.session.get(ES_API + "/reloadgames", timeout=RELOAD_TIMEOUT) as response:
            response.raise_for_status()

    async def reload_and_wait(self):
        try:
            await self.reload_games()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        await asyncio.sleep(RELOAD_SETTLE_SECONDS)

    async def upload_media(self, system, game_id, kind, content_type, chunks):
        url = ES_API + game_path(system, game_id) + "/media/" + urllib.parse.quote(kind)
        async with self.session.post(url, data=chunks, headers={"Content-Type": content_type},
                                     timeout=UPLOAD_TIMEOUT) as response:
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
