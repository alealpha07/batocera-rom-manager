import json
import urllib.parse

from aiohttp import web

from ..app_keys import BOOT_CHECK, ES, LIBRARY, TEMPLATES
from ..core import auth
from ..core.emulationstation import known_systems
from ..core.transfers import CHUNK_SIZE

FLASH_COOKIE = "rom_manager_flash"
FLASH_KINDS = ("success", "warning", "danger")
SHOW_EMPTY_COOKIE = "rom_manager_show_empty"
SHOW_EMPTY_SECONDS = 365 * 24 * 3600

routes = web.RouteTableDef()


async def render(request, template_name, **context):
    template = request.app[TEMPLATES].get_template(template_name)
    logged_in = auth.logged_in(request)
    boot_check = request.app[BOOT_CHECK]
    html = template.render(
        logged_in=logged_in, flash=read_flash(request), here=request.path_qs,
        running=await request.app[ES].running_game() if logged_in else None,
        checking=boot_check.status() if logged_in and boot_check.running else None, **context)
    response = web.Response(text=html, content_type="text/html")
    if FLASH_COOKIE in request.cookies:
        response.del_cookie(FLASH_COOKIE)
    return response


def redirect(url, kind=None, message=None):
    response = web.HTTPFound(url)
    if message:
        response.set_cookie(FLASH_COOKIE, urllib.parse.quote(json.dumps([kind, message])),
                            httponly=True, samesite="Strict")
    return response


def read_flash(request):
    try:
        kind, message = json.loads(urllib.parse.unquote(request.cookies.get(FLASH_COOKIE, "")))
    except (ValueError, TypeError):
        return None
    return {"kind": kind, "text": message} if kind in FLASH_KINDS and isinstance(message, str) else None


def local_url(url, default="/games"):
    return url if url.startswith("/") and not url.startswith(("//", "/\\")) else default


async def loaded_library(request):
    library = request.app[LIBRARY]
    await library.ensure_loaded()
    return library


async def upload_chunks(part):
    while chunk := await part.read_chunk(CHUNK_SIZE):
        yield chunk


def sidebar(request, current_system, show_hidden):
    library = request.app[LIBRARY]
    counts = library.counts(show_hidden)
    show_empty = request.cookies.get(SHOW_EMPTY_COOKIE) == "1"
    systems = sorted(((system, counts[name]) for name, system in known_systems().items()
                      if show_empty or counts[name] or name == current_system),
                     key=lambda entry: entry[0].fullname.lower())
    return {"systems": systems, "total": len(library.visible(show_hidden)), "show_empty": show_empty,
            "current": current_system, "show_hidden": show_hidden}


@routes.post("/sidebar/empty")
async def toggle_empty_systems(request):
    form = await request.post()
    response = redirect(local_url(form.get("next", "")))
    if form.get("show_empty") == "1":
        response.set_cookie(SHOW_EMPTY_COOKIE, "1", max_age=SHOW_EMPTY_SECONDS, httponly=True, samesite="Strict")
    else:
        response.del_cookie(SHOW_EMPTY_COOKIE)
    raise response


@routes.post("/running/quit")
async def quit_running_game(request):
    form = await request.post()
    back = local_url(form.get("next", ""))
    es = request.app[ES]
    running = await es.running_game()
    if request.app[BOOT_CHECK].running:
        raise redirect(back, "warning", "A boot check is running: stop it first")
    if not running:
        raise redirect(back, "warning", "No game is running")
    if running.path != form.get("running"):
        raise redirect(back, "warning", f"Not quit: {running.name} is running now")
    if await es.quit_game():
        raise redirect(back, "success", f"Quit {running.name}")
    raise redirect(back, "danger", f"{running.name} is still running")
