import asyncio
import contextlib
import functools
import glob
import os
import re
import signal
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
import xml.sax.saxutils
from dataclasses import dataclass

import aiohttp
from aiohttp import web

from ..config import ES_API, HOTKEYGEN, ROMS_DIR

ES_SYSTEMS_FILE = "/usr/share/emulationstation/es_systems.cfg"
USER_ES_SYSTEMS_FILES = "/userdata/system/configs/emulationstation/es_systems_*.cfg"
ES_ERRORS = (aiohttp.ClientError, TimeoutError)
API_TIMEOUT = aiohttp.ClientTimeout(total=5)
RELOAD_TIMEOUT = aiohttp.ClientTimeout(total=30)
UPLOAD_TIMEOUT = aiohttp.ClientTimeout(sock_connect=5, sock_read=60)
MEDIA_TIMEOUT = aiohttp.ClientTimeout(sock_connect=5, sock_read=30)
STATUS_TIMEOUT = aiohttp.ClientTimeout(total=2)
FORWARDED_MEDIA_HEADERS = ("Content-Type", "Content-Length", "Content-Range")
MEDIA_CHUNK_SIZE = 1 << 16
RELOAD_SETTLE_SECONDS = 1
READY_SETTLE_SECONDS = 1
POLL_SECONDS = 0.25
EXIT_WAIT_SECONDS = 5
HOTKEYGEN_TIMEOUT_SECONDS = 5
HOTKEY_CONTEXT = re.compile(r"Context \[(.*)\]")
ES_HOTKEY_CONTEXT = "emulationstation"
ES_PROCESS = "emulationstation"
LAUNCHER_PROCESSES = {"batocera-launch", "original-launcher"}


@dataclass(frozen=True)
class System:
    name: str
    fullname: str
    folder: str
    extensions: tuple

    def extension_warning(self, filename):
        if self.extensions and not filename.lower().endswith(self.extensions):
            return f"ES doesn't list this extension for {self.fullname}"
        return None


@functools.cache
def known_systems():
    systems = {}
    for path in [ES_SYSTEMS_FILE] + sorted(glob.glob(USER_ES_SYSTEMS_FILES)):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        for system in root.iter("system"):
            name, folder = system.findtext("name"), system.findtext("path")
            if name and folder:
                systems[name] = System(name, system.findtext("fullname") or name, os.path.expanduser(folder.strip()),
                                       tuple((system.findtext("extension") or "").lower().split()))
    return systems


def system_fullname(name, fallback=None):
    system = known_systems().get(name)
    return system.fullname if system else fallback or name


def relative_rom_path(path, system_name):
    system = known_systems().get(system_name)
    for folder in ([system.folder] if system else []) + [ROMS_DIR]:
        if path.startswith(folder.rstrip("/") + "/"):
            return os.path.relpath(path, folder)
    return path


@dataclass(frozen=True)
class RunningGame:
    system: str
    id: str
    name: str
    path: str

    @property
    def url(self):
        return f"/game/{urllib.parse.quote(self.system)}/{urllib.parse.quote(self.id)}"


def running_processes():
    processes = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as stat:
                parent = int(stat.read().rsplit(b")", 1)[1].split()[1])
            with open(f"/proc/{name}/cmdline", "rb") as cmdline:
                args = cmdline.read().split(b"\0")[:2]
        except (OSError, ValueError, IndexError):
            continue
        processes[int(name)] = (parent, [os.path.basename(arg.decode(errors="replace")) for arg in args])
    return processes


def es_process_id():
    return next((pid for pid, (_, args) in running_processes().items() if args and args[0] == ES_PROCESS), None)


def kill_launchers():
    processes = running_processes()
    children = {}
    for pid, (parent, _) in processes.items():
        children.setdefault(parent, []).append(pid)
    for launcher in [pid for pid, (_, args) in processes.items() if LAUNCHER_PROCESSES.intersection(args)]:
        tree = [launcher]
        for pid in tree:
            tree.extend(children.get(pid, []))
        for pid in reversed(tree):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


async def run_hotkeygen(*args):
    try:
        process = await asyncio.create_subprocess_exec(HOTKEYGEN, *args, stdout=subprocess.PIPE,
                                                       stderr=subprocess.DEVNULL)
    except OSError:
        return ""
    try:
        output, _ = await asyncio.wait_for(process.communicate(), HOTKEYGEN_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return ""
    return output.decode(errors="replace")


async def wait_for_hotkey_context(test, seconds):
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        context = HOTKEY_CONTEXT.match(await run_hotkeygen("--list"))
        if context and test(context.group(1)):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(POLL_SECONDS)


def game_api_path(system, game_id):
    return f"/systems/{urllib.parse.quote(system)}/games/{urllib.parse.quote(game_id)}"


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
        return await self.get_json(f"/systems/{urllib.parse.quote(system)}/games")

    async def game(self, system, game_id):
        return await self.get_json(game_api_path(system, game_id))

    async def update_game(self, system, game_id, fields):
        async with self.session.post(ES_API + game_api_path(system, game_id), json=fields,
                                     timeout=API_TIMEOUT) as response:
            response.raise_for_status()

    async def remove_game(self, system, path):
        body = f"<gameList><game><path>{xml.sax.saxutils.escape(path)}</path></game></gameList>"
        url = ES_API + "/removegames/" + urllib.parse.quote(system)
        async with self.session.post(url, data=body.encode(), timeout=API_TIMEOUT) as response:
            response.raise_for_status()

    async def reload_games(self):
        try:
            async with self.session.get(ES_API + "/reloadgames", timeout=RELOAD_TIMEOUT) as response:
                response.raise_for_status()
        except ES_ERRORS:
            pass
        await asyncio.sleep(RELOAD_SETTLE_SECONDS)

    async def upload_media(self, system, game_id, kind, content_type, chunks):
        url = ES_API + game_api_path(system, game_id) + "/media/" + urllib.parse.quote(kind)
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
            try:
                async for chunk in upstream.content.iter_chunked(MEDIA_CHUNK_SIZE):
                    await response.write(chunk)
            except ConnectionResetError:
                return response
            await response.write_eof()
            return response

    async def running_game(self):
        try:
            async with self.session.get(ES_API + "/runningGame", timeout=STATUS_TIMEOUT) as response:
                game = await response.json(content_type=None) if response.status == 200 else None
        except (*ES_ERRORS, ValueError):
            return None
        if not isinstance(game, dict) or not game.get("path"):
            return None
        return RunningGame(game.get("systemName", ""), game.get("id", ""),
                           game.get("name") or os.path.basename(game["path"]), game["path"])

    async def state(self):
        try:
            async with self.session.get(ES_API + "/isIdle", timeout=STATUS_TIMEOUT) as response:
                return "idle" if response.status == 200 else "busy"
        except ES_ERRORS:
            return "down"

    async def launch(self, path):
        async with self.session.post(ES_API + "/launch", data=path.encode(), timeout=API_TIMEOUT) as response:
            response.raise_for_status()

    async def wait_for_running_game(self, test, seconds):
        deadline = asyncio.get_running_loop().time() + seconds
        while not test(await self.running_game()):
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(POLL_SECONDS)
        return True

    async def wait_until_running(self, path, seconds):
        return await self.wait_for_running_game(lambda game: game is not None and game.path == path, seconds)

    async def wait_until_stopped(self, path, seconds):
        return await self.wait_for_running_game(lambda game: game is None or game.path != path, seconds)

    async def wait_until_idle(self, seconds):
        return await self.wait_for_running_game(lambda game: game is None, seconds)

    async def stop_game(self):
        # while a game loads the exit hotkey still belongs to ES, where it acts as "back"
        if await wait_for_hotkey_context(lambda name: name != ES_HOTKEY_CONTEXT, EXIT_WAIT_SECONDS):
            await run_hotkeygen("--send", "exit")
            if await self.wait_until_idle(EXIT_WAIT_SECONDS):
                return True
        await asyncio.to_thread(kill_launchers)
        await run_hotkeygen("--default-context")
        return await self.wait_until_idle(EXIT_WAIT_SECONDS)

    async def wait_until_ready(self):
        # ES queues or drops a launch that comes while it is still returning from the game
        await wait_for_hotkey_context(lambda name: name == ES_HOTKEY_CONTEXT, EXIT_WAIT_SECONDS)
        await asyncio.sleep(READY_SETTLE_SECONDS)

    async def quit_game(self):
        if not await self.stop_game():
            return False
        await self.wait_until_ready()
        return True

    async def restart(self):
        try:
            async with self.session.get(ES_API + "/quit", timeout=API_TIMEOUT) as response:
                response.raise_for_status()
        except (aiohttp.ServerDisconnectedError, TimeoutError):
            pass
