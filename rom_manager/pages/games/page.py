import re

from aiohttp import web

from ...utils.es import all_systems
from ...utils.export import csv_text, export_title, txt_text
from ..layout import render, safe_next
from ..sidebar.sidebar import sidebar

EXPORT_TYPES = {"csv": "text/csv", "txt": "text/plain"}


async def index(request):
    raise web.HTTPFound("/games")


async def shown_games(request):
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    shown = {"system": request.query.get("system", ""), "query": request.query.get("q", "").strip(),
             "sort": request.query.get("sort", "name"), "hidden": request.query.get("hidden") == "1",
             "failed": request.query.get("failed") == "1", "results": request.app["boot_check"].results}
    selected = library.select(shown["system"], shown["query"], shown["sort"], shown["hidden"])
    if shown["failed"]:
        selected = [g for g in selected if g["path"] in shown["results"] and not shown["results"][g["path"]]["passed"]]
    return selected, shown


async def games(request):
    selected, shown = await shown_games(request)
    params = {key: value for key, value in request.query.items() if value}
    return await render(request, "games", sidebar=sidebar(request, shown["hidden"], shown["system"]),
                        games=selected, total_size=sum(g["size"] for g in selected), current=shown["system"],
                        query=shown["query"], sort=shown["sort"], hidden=shown["hidden"], failed=shown["failed"],
                        results=shown["results"], params=params, path=request.path_qs,
                        error=request.app["library"].error)


async def export(request):
    kind = request.query.get("format", "")
    if kind not in EXPORT_TYPES:
        raise web.HTTPBadRequest(text="Unknown export format")
    selected, shown = await shown_games(request)
    system = shown["system"]
    if kind == "csv":
        text = csv_text(selected, shown["results"])
    else:
        selection = all_systems().get(system, {}).get("fullname") or system or "All games"
        text = txt_text(selected, export_title(selection, shown["query"], shown["hidden"], shown["failed"]))
    filename = "%s.%s" % (re.sub(r"[^\w.-]", "_", system) or "all-games", kind)
    return web.Response(text=text, content_type=EXPORT_TYPES[kind], charset="utf-8",
                        headers={"Content-Disposition": 'attachment; filename="%s"' % filename})


async def refresh(request):
    form = await request.post()
    all_systems.cache_clear()
    await request.app["library"].refresh(request.app["es"])
    raise web.HTTPFound(safe_next(form.get("next", "")))
