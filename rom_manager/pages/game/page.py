import asyncio
import urllib.parse

import aiohttp
from aiohttp import web

from ...utils.boot_check import is_game
from ...utils.emulator import game_title, quit_game
from ...utils.library import MEDIA_KINDS, file_list, game_details, game_media, info_changes, info_form
from ...utils.roms import (IMAGE_TYPES, VIDEO_TYPES, RomError, delete_game, delete_plan, delete_unused_media,
                           game_media_files, media_content_type, media_versions, remove_game_media)
from ..layout import flash, part_chunks, render
from ..sidebar.sidebar import sidebar

ES_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)
START_WAIT_SECONDS = 5


async def find_game(request):
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    game = library.game(request.match_info["system"], request.match_info["game_id"])
    if not game:
        raise web.HTTPNotFound()
    return game


def game_url(game):
    return "/game/%s/%s" % (urllib.parse.quote(game["group"]), urllib.parse.quote(game["id"]))


def managed_files(game):
    try:
        return file_list(game, delete_plan(game))
    except (RomError, OSError):
        return None, 0


async def game(request):
    found = await find_game(request)
    files, more_files = await asyncio.to_thread(file_list, found)
    managed, more_managed = await asyncio.to_thread(managed_files, found)
    versions = await asyncio.to_thread(media_versions, found)
    can_check = await asyncio.to_thread(is_game, found)
    return await render(request, "game", sidebar=sidebar(request, False, found["group"]), current=found["group"],
                        hidden=False, game=found, game_url=game_url(found), details=game_details(found),
                        media=game_media(found), info=info_form(found["es"]), files=files, more_files=more_files,
                        managed=managed, more_managed=more_managed, versions=versions, media_kinds=MEDIA_KINDS,
                        accept={"image": ",".join(IMAGE_TYPES), "video": ",".join(VIDEO_TYPES)},
                        boot=request.app["boot_check"].result(found["path"]), can_check=can_check, error=request.app["library"].error)


async def media(request):
    found = await find_game(request)
    kind = request.match_info["kind"]
    if kind not in game_media(found):
        raise web.HTTPNotFound()
    # ES serves scraped videos as text/plain
    content_type = "video/mp4" if kind == "video" else None
    return await request.app["es"].proxy_media(request, found["es"][kind], content_type)


def media_kind(request):
    kind = request.match_info["kind"]
    if kind not in MEDIA_KINDS:
        raise web.HTTPNotFound()
    return kind


async def upload_media(request):
    found, kind = await find_game(request), media_kind(request)
    es, library = request.app["es"], request.app["library"]
    response = web.HTTPFound(game_url(found))
    try:
        old_file = (await asyncio.to_thread(game_media_files, found)).get(kind)
        part = await (await request.multipart()).next()
        if part is None or not part.filename:
            raise RomError("Choose a file")
        content_type = media_content_type(kind, part.filename)
        await es.upload_media(found["system"], found["id"], kind, content_type, part_chunks(part))
    except (RomError, OSError) as error:
        flash(response, "danger", "%s not uploaded: %s" % (kind.capitalize(), error))
        raise response
    except aiohttp.ClientResponseError:
        flash(response, "danger", "%s not uploaded: EmulationStation refused the file" % kind.capitalize())
        raise response
    except ES_ERRORS:
        flash(response, "danger", "%s not uploaded: EmulationStation isn't answering" % kind.capitalize())
        raise response
    await library.rescan(es)
    try:
        if old_file != (await asyncio.to_thread(game_media_files, found)).get(kind):
            await asyncio.to_thread(delete_unused_media, found, old_file)
    except (RomError, OSError):
        pass
    flash(response, "success", "%s uploaded" % kind.capitalize())
    raise response


async def remove_media(request):
    found, kind = await find_game(request), media_kind(request)
    es, library = request.app["es"], request.app["library"]
    response = web.HTTPFound(game_url(found))
    # ES writes its unsaved changes back over the whole gamelist entry, so they are saved before the edit
    await es.reload_and_wait()
    try:
        await asyncio.to_thread(remove_game_media, found, kind)
        flash(response, "success", "%s removed" % kind.capitalize())
    except (RomError, OSError) as error:
        flash(response, "danger", "%s not removed: %s" % (kind.capitalize(), error))
    await library.rescan(es)
    raise response


async def edit_info(request):
    found = await find_game(request)
    es, library = request.app["es"], request.app["library"]
    form = await request.post()
    response = web.HTTPFound(game_url(found))
    try:
        changes = info_changes(await es.game(found["system"], found["id"]), form)
        if changes:
            await es.update_game(found["system"], found["id"], changes)
    except ValueError as error:
        flash(response, "danger", "Info not saved: %s" % error)
        raise response
    except ES_ERRORS:
        flash(response, "danger", "Info not saved: EmulationStation isn't answering")
        raise response
    if changes:
        await library.rescan(es)
    flash(response, "success", "Info saved" if changes else "Nothing changed")
    raise response


async def delete(request):
    found = await find_game(request)
    es, library = request.app["es"], request.app["library"]
    response = web.HTTPFound("/games?system=" + urllib.parse.quote(found["group"]))
    try:
        plan = await asyncio.to_thread(delete_plan, found)
        try:
            await es.remove_game(found["system"], found["path"])
            es_updated = True
        except ES_ERRORS:
            es_updated = False
        await asyncio.to_thread(delete_game, found, plan)
        await asyncio.to_thread(request.app["boot_check"].forget, found["path"])
        flash(response, "success", "Deleted %s" % found["name"])
    except (RomError, OSError) as error:
        es_updated = False
        flash(response, "danger", "Couldn't delete %s: %s" % (found["name"], error))
    await (library.refresh(es) if es_updated else library.rescan(es))
    raise response


async def start_game(es, found, confirmed):
    running = await es.running_game()
    if running and running["path"] == found["path"]:
        return "warning", "%s is already running" % found["name"]
    if running and running["path"] != confirmed:
        return "warning", "%s is running now, press Start again to switch" % game_title(running)
    if running and not await quit_game(es):
        return "danger", "Not started: %s didn't quit" % game_title(running)
    await es.launch(found["path"])
    if await es.wait_until(lambda game: game is not None and game["path"] == found["path"], START_WAIT_SECONDS):
        return "success", "Started %s on the TV" % found["name"]
    return "warning", "Sent %s to EmulationStation, but it didn't report it running" % found["name"]


async def start(request):
    found = await find_game(request)
    form = await request.post()
    response = web.HTTPFound(game_url(found))
    if request.app["boot_check"].running:
        flash(response, "warning", "A boot check is running: stop it first")
        raise response
    try:
        flash(response, *await start_game(request.app["es"], found, form.get("running", "")))
    except ES_ERRORS:
        flash(response, "danger", "Not started: EmulationStation isn't answering")
    raise response
