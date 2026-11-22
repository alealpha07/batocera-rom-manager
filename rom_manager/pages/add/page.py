from aiohttp import web

from ...utils.downloads import Target
from ...utils.es import all_systems
from ...utils.roms import extension_warning, target_dir
from .. import transfer
from ..layout import render


def rom_folder(fields):
    return target_dir(fields.get("system", ""), fields.get("subfolder", ""))


def rom_warning(fields, name):
    return extension_warning(fields["system"], name)


def rom_target(fields):
    system, subfolder = fields.get("system", ""), fields.get("subfolder", "").strip()
    return Target("roms", rom_folder(fields), all_systems()[system]["fullname"], subfolder, reload_es=True,
                  warning=lambda name: extension_warning(system, name))


async def rescan(request):
    await request.app["library"].rescan(request.app["es"])


ROMS = transfer.Area(rom_folder, rom_warning, rom_target, rescan)


async def add(request):
    systems = sorted(all_systems().values(), key=lambda s: s["fullname"].lower())
    return render(request, "add", systems=systems, selected=request.query.get("system", ""))


async def check(request):
    return await transfer.check(request, ROMS)


async def upload(request):
    return await transfer.upload(request, ROMS)


async def links(request):
    return await transfer.links(request, ROMS)


async def jobs(request):
    return web.json_response(request.app["downloads"].snapshot("roms"))
