import os

import aiohttp
import jinja2
from aiohttp import web

from . import auth, views
from .config import APP_DIR, load_config
from .library import Library, human_size


async def es_client(app):
    app["es"] = aiohttp.ClientSession()
    yield
    await app["es"].close()


def create_app():
    app = web.Application(middlewares=[auth.login_required])
    app["config"] = load_config()
    app["templates"] = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(APP_DIR, "templates")), autoescape=True)
    app["templates"].filters["size"] = human_size
    app["library"] = Library()
    app.cleanup_ctx.append(es_client)
    app.router.add_static("/static", os.path.join(APP_DIR, "static"))
    app.router.add_get("/", views.index)
    app.router.add_route("*", "/setup", views.setup)
    app.router.add_route("*", "/login", views.login)
    app.router.add_post("/logout", views.logout)
    app.router.add_get("/games", views.games)
    app.router.add_post("/refresh", views.refresh)
    app.router.add_get("/game/{system}/{game_id}", views.game)
    app.router.add_get("/media/{system}/{game_id}/{kind}", views.media)
    return app
