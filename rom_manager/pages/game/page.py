import asyncio
import urllib.parse

import aiohttp
from aiohttp import web

from ...app_keys import BOOT_CHECK, COLLECTIONS_RESTART, ES, LIBRARY
from ...core.boot_check import is_checkable
from ...core.collections import CollectionError, manual_collections_with, save_game_collections
from ...core.emulationstation import ES_ERRORS
from ...core.game_files import IMAGE_TYPES, MEDIA_KINDS, VIDEO_TYPES, GameFiles, GameFilesError
from ..layout import loaded_library, redirect, render, sidebar, upload_chunks

START_WAIT_SECONDS = 5

routes = web.RouteTableDef()


async def find_game(request):
    library = await loaded_library(request)
    game = library.game(request.match_info["system"], request.match_info["game_id"])
    if not game:
        raise web.HTTPNotFound()
    return game


def media_kind(request):
    kind = request.match_info["kind"]
    if kind not in MEDIA_KINDS:
        raise web.HTTPNotFound()
    return kind


def game_collections(game):
    try:
        return manual_collections_with(game.path)
    except OSError:
        return []


def page_files(game):
    files = GameFiles(game)
    return {"files": files.listing(), "deletable": files.deletable_listing(), "versions": files.media_versions(),
            "collections": game_collections(game)}


@routes.get("/game/{system}/{game_id}")
async def game_page(request):
    game = await find_game(request)
    accept = {"image": ",".join(IMAGE_TYPES), "video": ",".join(VIDEO_TYPES)}
    return await render(request, "game/page.html", sidebar=sidebar(request, game.group, False), game=game,
                        media_kinds=MEDIA_KINDS, accept=accept,
                        boot=request.app[BOOT_CHECK].result(game.path), can_check=is_checkable(game),
                        error=request.app[LIBRARY].error, **await asyncio.to_thread(page_files, game))


@routes.get("/media/{system}/{game_id}/{kind}")
async def media(request):
    game = await find_game(request)
    kind = request.match_info["kind"]
    if kind not in game.media:
        raise web.HTTPNotFound()
    # ES serves scraped videos as text/plain
    content_type = "video/mp4" if kind == "video" else None
    return await request.app[ES].proxy_media(request, game.metadata[kind], content_type)


@routes.post("/game/{system}/{game_id}/collections")
async def edit_collections(request):
    game = await find_game(request)
    form = await request.post()
    try:
        changed, settings = await asyncio.to_thread(save_game_collections, game.path,
                                                    set(form.getall("collection", [])), form.get("new", "").strip())
    except (CollectionError, OSError) as error:
        raise redirect(game.url, "danger", f"Collections not saved: {error}")
    if not changed:
        raise redirect(game.url, "success", "Nothing changed")
    request.app[COLLECTIONS_RESTART].remember(settings)
    raise redirect(game.url, "success",
                   "Collections saved: restart EmulationStation from the Collections page to see them")


@routes.post("/game/{system}/{game_id}/media/{kind}")
async def upload_media(request):
    game, kind = await find_game(request), media_kind(request)
    label = kind.capitalize()
    try:
        part = await (await request.multipart()).next()
        if part is None or not part.filename:
            raise GameFilesError("Choose a file")
        await request.app[LIBRARY].upload_media(game, kind, part.filename, upload_chunks(part))
    except (GameFilesError, OSError) as error:
        raise redirect(game.url, "danger", f"{label} not uploaded: {error}")
    except aiohttp.ClientResponseError:
        raise redirect(game.url, "danger", f"{label} not uploaded: EmulationStation refused the file")
    except ES_ERRORS:
        raise redirect(game.url, "danger", f"{label} not uploaded: EmulationStation isn't answering")
    raise redirect(game.url, "success", f"{label} uploaded")


@routes.post("/game/{system}/{game_id}/media/{kind}/remove")
async def remove_media(request):
    game, kind = await find_game(request), media_kind(request)
    try:
        await request.app[LIBRARY].remove_media(game, kind)
    except (GameFilesError, OSError) as error:
        raise redirect(game.url, "danger", f"{kind.capitalize()} not removed: {error}")
    raise redirect(game.url, "success", f"{kind.capitalize()} removed")


@routes.post("/game/{system}/{game_id}/info")
async def edit_info(request):
    game = await find_game(request)
    try:
        changed = await request.app[LIBRARY].save_info(game, await request.post())
    except ValueError as error:
        raise redirect(game.url, "danger", f"Info not saved: {error}")
    except ES_ERRORS:
        raise redirect(game.url, "danger", "Info not saved: EmulationStation isn't answering")
    raise redirect(game.url, "success", "Info saved" if changed else "Nothing changed")


@routes.post("/game/{system}/{game_id}/delete")
async def delete(request):
    game = await find_game(request)
    system_url = "/games?system=" + urllib.parse.quote(game.group)
    try:
        await request.app[LIBRARY].delete_game(game)
    except (GameFilesError, OSError) as error:
        raise redirect(system_url, "danger", f"Couldn't delete {game.name}: {error}")
    await asyncio.to_thread(request.app[BOOT_CHECK].forget, game.path)
    raise redirect(system_url, "success", f"Deleted {game.name}")


@routes.post("/game/{system}/{game_id}/start")
async def start(request):
    game = await find_game(request)
    es = request.app[ES]
    confirmed_running = (await request.post()).get("running", "")
    if request.app[BOOT_CHECK].running:
        raise redirect(game.url, "warning", "A boot check is running: stop it first")
    try:
        running = await es.running_game()
        if running and running.path == game.path:
            raise redirect(game.url, "warning", f"{game.name} is already running")
        if running and running.path != confirmed_running:
            raise redirect(game.url, "warning", f"{running.name} is running now, press Start again to switch")
        if running and not await es.quit_game():
            raise redirect(game.url, "danger", f"Not started: {running.name} didn't quit")
        await es.launch(game.path)
        started = await es.wait_until_running(game.path, START_WAIT_SECONDS)
    except ES_ERRORS:
        raise redirect(game.url, "danger", "Not started: EmulationStation isn't answering")
    if not started:
        raise redirect(game.url, "warning", f"Sent {game.name} to EmulationStation, but it didn't report it running")
    raise redirect(game.url, "success", f"Started {game.name} on the TV")
