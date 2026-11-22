import json
import urllib.parse

from aiohttp import web

from ..utils import auth
from ..utils.es import all_systems
from ..utils.roms import CHUNK_SIZE

SHOW_EMPTY_COOKIE = "rom_manager_show_empty"
FLASH_COOKIE = "rom_manager_flash"
FLASH_KINDS = ("success", "warning", "danger")


def render(request, page, **context):
    template = request.app["templates"].get_template(page + "/page.html")
    message = read_flash(request)
    html = template.render(logged_in=auth.logged_in(request), flash=message, **context)
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


def sidebar(request, hidden, current):
    library = request.app["library"]
    counts = library.counts(hidden)
    show_empty = request.cookies.get(SHOW_EMPTY_COOKIE) == "1"
    systems = sorted((dict(s, games=counts[name]) for name, s in all_systems().items()
                      if show_empty or counts[name] or name == current),
                     key=lambda s: s["fullname"].lower())
    return {"systems": systems, "total": library.total(hidden), "show_empty": show_empty,
            "next": request.path_qs}


async def part_chunks(part):
    while chunk := await part.read_chunk(CHUNK_SIZE):
        yield chunk


def safe_next(url):
    return url if url.startswith("/") and not url.startswith(("//", "/\\")) else "/games"


async def toggle_empty_systems(request):
    form = await request.post()
    response = web.HTTPFound(safe_next(form.get("next", "")))
    if form.get("show_empty") == "1":
        response.set_cookie(SHOW_EMPTY_COOKIE, "1", max_age=365 * 24 * 3600, httponly=True, samesite="Strict")
    else:
        response.del_cookie(SHOW_EMPTY_COOKIE)
    raise response
