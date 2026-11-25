from aiohttp import web

from ...utils.es import all_systems
from ..layout import safe_next

SHOW_EMPTY_COOKIE = "rom_manager_show_empty"


def sidebar(request, hidden, current):
    library = request.app["library"]
    counts = library.counts(hidden)
    show_empty = request.cookies.get(SHOW_EMPTY_COOKIE) == "1"
    systems = sorted((dict(s, games=counts[name]) for name, s in all_systems().items()
                      if show_empty or counts[name] or name == current),
                     key=lambda s: s["fullname"].lower())
    return {"systems": systems, "total": library.total(hidden), "show_empty": show_empty,
            "next": request.path_qs}


async def toggle_empty_systems(request):
    form = await request.post()
    response = web.HTTPFound(safe_next(form.get("next", "")))
    if form.get("show_empty") == "1":
        response.set_cookie(SHOW_EMPTY_COOKIE, "1", max_age=365 * 24 * 3600, httponly=True, samesite="Strict")
    else:
        response.del_cookie(SHOW_EMPTY_COOKIE)
    raise response
