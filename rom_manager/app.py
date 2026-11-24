import asyncio
import contextlib
import os

import jinja2
from aiohttp import web

from .config import APP_DIR, load_config
from .pages import layout, transfer
from .pages.add import page as add
from .pages.bios import page as bios
from .pages.game import page as game
from .pages.games import page as games
from .pages.login import page as login
from .pages.setup import page as setup
from .utils import auth
from .utils.downloads import DownloadQueue
from .utils.es import EmulationStation
from .utils.library import Library
from .utils.roms import human_size


async def es_session(app):
    await app["es"].open()
    yield
    await app["es"].close()


async def download_worker(app):
    worker = asyncio.create_task(app["downloads"].run(app))
    yield
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker


def create_app():
    app = web.Application(middlewares=[auth.login_required])
    app["config"] = load_config()
    app["templates"] = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(APP_DIR, "pages")), autoescape=True)
    app["templates"].filters["size"] = human_size
    app["es"] = EmulationStation()
    app["library"] = Library()
    app["downloads"] = DownloadQueue()
    app.cleanup_ctx.append(es_session)
    app.cleanup_ctx.append(download_worker)
    app.router.add_static("/static", os.path.join(APP_DIR, "static"))
    app.router.add_get("/", games.index)
    app.router.add_route("*", "/setup", setup.setup)
    app.router.add_route("*", "/login", login.login)
    app.router.add_post("/logout", login.logout)
    app.router.add_post("/sidebar/empty", layout.toggle_empty_systems)
    app.router.add_post("/running/quit", layout.quit_running)
    app.router.add_get("/games", games.games)
    app.router.add_post("/refresh", games.refresh)
    app.router.add_get("/game/{system}/{game_id}", game.game)
    app.router.add_get("/media/{system}/{game_id}/{kind}", game.media)
    app.router.add_post("/game/{system}/{game_id}/start", game.start)
    app.router.add_post("/game/{system}/{game_id}/info", game.edit_info)
    app.router.add_post("/game/{system}/{game_id}/media/{kind}", game.upload_media)
    app.router.add_post("/game/{system}/{game_id}/media/{kind}/remove", game.remove_media)
    app.router.add_post("/game/{system}/{game_id}/delete", game.delete)
    app.router.add_get("/add", add.add)
    app.router.add_post("/add/check", add.check)
    app.router.add_post("/add/upload", add.upload)
    app.router.add_post("/add/links", add.links)
    app.router.add_get("/add/jobs", add.jobs)
    app.router.add_get("/bios", bios.bios_page)
    app.router.add_post("/bios/check", bios.check)
    app.router.add_post("/bios/upload", bios.upload)
    app.router.add_post("/bios/links", bios.links)
    app.router.add_get("/bios/jobs", bios.jobs)
    app.router.add_post("/bios/entry/upload", bios.entry_upload)
    app.router.add_post("/bios/entry/link", bios.entry_link)
    app.router.add_post("/bios/delete", bios.delete)
    app.router.add_post("/jobs/{job_id}/cancel", transfer.cancel_job)
    return app
