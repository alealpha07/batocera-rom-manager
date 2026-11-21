import asyncio

from aiohttp import web

from ...utils.library import file_list, game_details, game_media
from ..layout import render, sidebar


async def find_game(request):
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    game = library.game(request.match_info["system"], request.match_info["game_id"])
    if not game:
        raise web.HTTPNotFound()
    return game


async def game(request):
    found = await find_game(request)
    files, more_files = await asyncio.to_thread(file_list, found)
    return render(request, "game", sidebar=sidebar(request, False, found["group"]), current=found["group"],
                  hidden=False, game=found, details=game_details(found), media=game_media(found),
                  files=files, more_files=more_files, error=request.app["library"].error)


async def media(request):
    found = await find_game(request)
    kind = request.match_info["kind"]
    if kind not in game_media(found):
        raise web.HTTPNotFound()
    # ES serves scraped videos as text/plain
    content_type = "video/mp4" if kind == "video" else None
    return await request.app["es"].proxy_media(request, found["es"][kind], content_type)
