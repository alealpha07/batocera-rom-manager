import os

from aiohttp import web

from ...app_keys import DOWNLOADS, LIBRARY
from ...core.emulationstation import known_systems
from ...core.transfers import Destination, TransferError, clean_name
from ..layout import render
from ..transfer import transfer

routes = web.RouteTableDef()


def rom_destination(fields):
    system_name = fields.get("system", "")
    system = known_systems().get(system_name)
    if not system:
        raise TransferError(f"Unknown system: {system_name!r}")
    subfolder = fields.get("subfolder", "").strip()
    folder = os.path.join(system.folder, clean_name(subfolder, "subfolder")) if subfolder else system.folder
    return Destination("roms", folder, system.fullname, subfolder, reload_es=True, warning=system.extension_warning)


@routes.get("/add")
async def add_page(request):
    systems = sorted(known_systems().values(), key=lambda system: system.fullname.lower())
    return await render(request, "add/page.html", systems=systems, selected=request.query.get("system", ""))


@routes.post("/add/check")
async def check(request):
    return await transfer.check(request, rom_destination)


@routes.post("/add/upload")
async def upload(request):
    return await transfer.upload(request, rom_destination, request.app[LIBRARY].rescan)


@routes.post("/add/links")
async def links(request):
    return await transfer.links(request, rom_destination)


@routes.get("/add/jobs")
async def jobs(request):
    return web.json_response(request.app[DOWNLOADS].listing("roms"))
