import json
import urllib.parse

from aiohttp import web

from ..utils import auth
from ..utils.emulator import game_title, quit_game
from ..utils.roms import CHUNK_SIZE

FLASH_COOKIE = "rom_manager_flash"
FLASH_KINDS = ("success", "warning", "danger")


async def running_game(request):
    game = await request.app["es"].running_game()
    if not game:
        return None
    url = "/game/%s/%s" % (urllib.parse.quote(game.get("systemName", "")), urllib.parse.quote(game.get("id", "")))
    return {"name": game_title(game), "path": game["path"], "url": url}


async def render(request, page, **context):
    template = request.app["templates"].get_template(page + "/page.html")
    logged_in = auth.logged_in(request)
    running = await running_game(request) if logged_in else None
    boot_check = request.app["boot_check"]
    html = template.render(logged_in=logged_in, flash=read_flash(request), running=running, here=request.path_qs,
                           checking=boot_check.status() if logged_in and boot_check.running else None, **context)
    response = web.Response(text=html, content_type="text/html")
    if FLASH_COOKIE in request.cookies:
        response.del_cookie(FLASH_COOKIE)
    return response


def flash(response, kind, text):
    response.set_cookie(FLASH_COOKIE, urllib.parse.quote(json.dumps([kind, text])), httponly=True, samesite="Strict")


def read_flash(request):
    try:
        kind, text = json.loads(urllib.parse.unquote(request.cookies.get(FLASH_COOKIE, "")))
    except (ValueError, TypeError):
        return None
    return {"kind": kind, "text": text} if kind in FLASH_KINDS and isinstance(text, str) else None


async def part_chunks(part):
    while chunk := await part.read_chunk(CHUNK_SIZE):
        yield chunk


def safe_next(url):
    return url if url.startswith("/") and not url.startswith(("//", "/\\")) else "/games"


async def quit_running(request):
    form = await request.post()
    response = web.HTTPFound(safe_next(form.get("next", "")))
    es = request.app["es"]
    running = await es.running_game()
    if request.app["boot_check"].running:
        flash(response, "warning", "A boot check is running: stop it first")
    elif not running:
        flash(response, "warning", "No game is running")
    elif running["path"] != form.get("running"):
        flash(response, "warning", "Not quit: %s is running now" % game_title(running))
    elif await quit_game(es):
        flash(response, "success", "Quit %s" % game_title(running))
    else:
        flash(response, "danger", "%s is still running" % game_title(running))
    raise response
