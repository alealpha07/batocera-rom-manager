import asyncio
import secrets

from aiohttp import web

from . import auth, es
from .config import save_config
from .library import file_list, game_details, game_media
from .systems import all_systems


def render(request, template, **context):
    html = request.app["templates"].get_template(template).render(logged_in=auth.logged_in(request), **context)
    return web.Response(text=html, content_type="text/html")


def redirect_with_session(config):
    response = web.HTTPFound("/")
    auth.start_session(response, config["secret"])
    raise response


def sidebar(library, hidden):
    counts = library.counts(hidden)
    systems = sorted((dict(s, games=counts[name]) for name, s in all_systems().items()),
                     key=lambda s: s["fullname"].lower())
    return {"systems": systems, "total": library.total(hidden)}


def safe_next(url):
    return url if url.startswith("/") and not url.startswith(("//", "/\\")) else "/games"


async def index(request):
    raise web.HTTPFound("/games")


async def setup(request):
    config = request.app["config"]
    if config.get("password"):
        raise web.HTTPFound("/")
    if request.method != "POST":
        return render(request, "setup.html")
    form = await request.post()
    password, confirm = form.get("password", ""), form.get("confirm", "")
    if not password:
        return render(request, "setup.html", error="Choose a password")
    if password != confirm:
        return render(request, "setup.html", error="The passwords don't match")
    config["password"] = await asyncio.to_thread(auth.hash_password, password)
    config["secret"] = secrets.token_hex(32)
    save_config(config)
    redirect_with_session(config)


async def login(request):
    config = request.app["config"]
    if not config.get("password"):
        raise web.HTTPFound("/setup")
    if request.method != "POST":
        return render(request, "login.html")
    form = await request.post()
    if await asyncio.to_thread(auth.check_password, form.get("password", ""), config["password"]):
        redirect_with_session(config)
    await asyncio.sleep(1)
    return render(request, "login.html", error="Wrong password")


async def logout(request):
    response = web.HTTPFound("/login")
    auth.end_session(response)
    raise response


async def games(request):
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    system = request.query.get("system", "")
    query = request.query.get("q", "").strip()
    sort = request.query.get("sort", "name")
    hidden = request.query.get("hidden") == "1"
    selected = library.select(system, query, sort, hidden)
    params = {key: value for key, value in request.query.items() if value}
    return render(request, "games.html", sidebar=sidebar(library, hidden), games=selected,
                  total_size=sum(g["size"] for g in selected), current=system, query=query, sort=sort,
                  hidden=hidden, params=params, path=request.path_qs, error=library.error)


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
    return render(request, "game.html", sidebar=sidebar(request.app["library"], False), current=found["group"],
                  hidden=False, game=found, details=game_details(found), media=game_media(found),
                  files=files, more_files=more_files, error=request.app["library"].error)


async def media(request):
    found = await find_game(request)
    kind = request.match_info["kind"]
    if kind not in game_media(found):
        raise web.HTTPNotFound()
    # ES serves scraped videos as text/plain
    content_type = "video/mp4" if kind == "video" else None
    return await es.proxy_media(request, request.app["es"], found["es"][kind], content_type)


async def refresh(request):
    form = await request.post()
    all_systems.cache_clear()
    await request.app["library"].refresh(request.app["es"])
    raise web.HTTPFound(safe_next(form.get("next", "")))
