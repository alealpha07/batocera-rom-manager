import asyncio

from aiohttp import web

from ...app_keys import BOOT_CHECK, COLLECTIONS_RESTART, DOWNLOADS, ES
from ...core.batocera import disk_usage, restart_service, system_info
from ...core.collections import CollectionError
from ...core.emulationstation import ES_ERRORS, es_process_id
from ..layout import render

APP_RESTART_DELAY_SECONDS = 0.5

routes = web.RouteTableDef()


def refuse(message):
    return web.json_response({"error": message}, status=400)


@routes.get("/support")
async def support_page(request):
    return await render(request, "support/page.html", **system_info(), es_state=await request.app[ES].state(),
                        disk=await asyncio.to_thread(disk_usage), downloads_active=request.app[DOWNLOADS].active(),
                        restarted=request.query.get("restarted"))


@routes.get("/support/status")
async def status(request):
    return web.json_response({"es": await request.app[ES].state()})


@routes.post("/support/restart/es")
async def restart_es(request):
    es = request.app[ES]
    if request.app[BOOT_CHECK].running:
        return refuse("A boot check is running: stop it first")
    running = await es.running_game()
    if running and not await es.quit_game():
        return refuse(f"{running.name} didn't quit, so EmulationStation wasn't restarted")
    old_process_id = es_process_id()
    try:
        await es.restart()
    except ES_ERRORS:
        return refuse("EmulationStation isn't answering")
    try:
        await asyncio.to_thread(request.app[COLLECTIONS_RESTART].reapply_after_exit, old_process_id)
    except (CollectionError, OSError) as error:
        return refuse(f"EmulationStation restarted, but the collection settings weren't saved: {error}")
    return web.json_response({"ok": True})


@routes.post("/support/restart/app")
async def restart_app(request):
    if request.app[BOOT_CHECK].running:
        return refuse("A boot check is running: stop it first")
    asyncio.get_running_loop().call_later(APP_RESTART_DELAY_SECONDS, restart_service)
    return web.json_response({"ok": True})
