import asyncio
import datetime
import os
import shutil
import subprocess
import time

from aiohttp import web

from ...config import APP_DIR, BATOCERA_VERSION_FILE, SERVICE_NAME, USERDATA_DIR
from ...utils.boot_check import ES_ERRORS
from ...utils.emulator import game_title, quit_game
from ..layout import render

APP_STARTED = time.time()
APP_RESTART_DELAY_SECONDS = 0.5


def read_text(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def box_uptime():
    try:
        return float(read_text("/proc/uptime").split()[0])
    except (ValueError, IndexError):
        return None


def duration(seconds):
    minutes = int(seconds // 60)
    days, hours = minutes // 1440, minutes // 60 % 24
    if days:
        return "%d d %d h" % (days, hours)
    return "%d h %d min" % (hours, minutes % 60) if hours else "%d min" % minutes


def local_time(timestamp):
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def disk_usage():
    usage = shutil.disk_usage(USERDATA_DIR)
    return {"total": usage.total, "used": usage.used, "free": usage.free,
            "percent": round(usage.used * 100 / usage.total) if usage.total else 0}


async def es_state(es):
    idle = await es.idle()
    return "down" if idle is None else "idle" if idle else "busy"


async def support(request):
    es = request.app["es"]
    uptime = box_uptime()
    return await render(request, "support",
                        batocera_version=read_text(BATOCERA_VERSION_FILE) or "unknown",
                        installed=local_time(os.path.getmtime(APP_DIR)),
                        box_uptime=duration(uptime) if uptime is not None else "unknown",
                        app_started=local_time(APP_STARTED), app_uptime=duration(time.time() - APP_STARTED),
                        es_state=await es_state(es), disk=await asyncio.to_thread(disk_usage),
                        downloads_active=request.app["downloads"].active(),
                        restarted=request.query.get("restarted"))


async def status(request):
    return web.json_response({"es": await es_state(request.app["es"])})


def refuse(text):
    return web.json_response({"error": text}, status=400)


async def restart_es(request):
    es = request.app["es"]
    if request.app["boot_check"].running:
        return refuse("A boot check is running: stop it first")
    running = await es.running_game()
    if running and not await quit_game(es):
        return refuse("%s didn't quit, so EmulationStation wasn't restarted" % game_title(running))
    try:
        await es.quit()
    except ES_ERRORS:
        return refuse("EmulationStation isn't answering")
    return web.json_response({"ok": True})


def restart_service():
    subprocess.Popen(["/usr/bin/batocera-services", "restart", SERVICE_NAME], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def restart_app(request):
    if request.app["boot_check"].running:
        return refuse("A boot check is running: stop it first")
    asyncio.get_running_loop().call_later(APP_RESTART_DELAY_SECONDS, restart_service)
    return web.json_response({"ok": True})
