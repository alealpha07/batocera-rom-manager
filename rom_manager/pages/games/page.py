from aiohttp import web

from ...utils.es import all_systems
from ..layout import render, safe_next, sidebar


async def index(request):
    raise web.HTTPFound("/games")


async def games(request):
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    system = request.query.get("system", "")
    query = request.query.get("q", "").strip()
    sort = request.query.get("sort", "name")
    hidden = request.query.get("hidden") == "1"
    selected = library.select(system, query, sort, hidden)
    params = {key: value for key, value in request.query.items() if value}
    return await render(request, "games", sidebar=sidebar(request, hidden, system), games=selected,
                        total_size=sum(g["size"] for g in selected), current=system, query=query, sort=sort,
                        hidden=hidden, params=params, path=request.path_qs, error=library.error)


async def refresh(request):
    form = await request.post()
    all_systems.cache_clear()
    await request.app["library"].refresh(request.app["es"])
    raise web.HTTPFound(safe_next(form.get("next", "")))
