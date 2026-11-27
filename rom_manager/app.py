import asyncio
import contextlib
import os

import jinja2
from aiohttp import web

from .app_keys import BOOT_CHECK, COLLECTIONS_RESTART, CONFIG, DOWNLOADS, ES, LIBRARY, TEMPLATES
from .config import APP_DIR, load_config
from .pages import layout
from .pages.add import page as add_page
from .pages.bios import page as bios_page
from .pages.check import page as check_page
from .pages.collections import page as collections_page
from .pages.game import page as game_page
from .pages.games import page as games_page
from .pages.login import page as login_page
from .pages.setup import page as setup_page
from .pages.support import page as support_page
from .pages.transfer import transfer
from .core import auth
from .core.boot_check import BootCheck
from .core.collections import PendingRestart
from .core.emulationstation import EmulationStation
from .core.game_files import human_size
from .core.library import Library
from .core.transfers import DownloadQueue

PAGES = (layout, games_page, game_page, add_page, bios_page, transfer, check_page, collections_page, support_page,
         login_page, setup_page)


async def es_session(app):
    await app[ES].open()
    yield
    await app[ES].close()


async def download_worker(app):
    worker = asyncio.create_task(app[DOWNLOADS].run(app[LIBRARY].rescan))
    yield
    worker.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await worker


async def boot_check_shutdown(app):
    yield
    await app[BOOT_CHECK].shutdown()


def create_app():
    app = web.Application(middlewares=[auth.login_required])
    app[CONFIG] = load_config()
    app[TEMPLATES] = jinja2.Environment(
        loader=jinja2.FileSystemLoader(os.path.join(APP_DIR, "pages")), autoescape=True)
    app[TEMPLATES].filters["size"] = human_size
    app[ES] = EmulationStation()
    app[LIBRARY] = Library(app[ES])
    app[DOWNLOADS] = DownloadQueue()
    app[BOOT_CHECK] = BootCheck(app[ES], app[LIBRARY])
    app[COLLECTIONS_RESTART] = PendingRestart()
    app.cleanup_ctx.append(es_session)
    app.cleanup_ctx.append(download_worker)
    app.cleanup_ctx.append(boot_check_shutdown)
    app.router.add_static("/static", os.path.join(APP_DIR, "static"))
    for page in PAGES:
        app.router.add_routes(page.routes)
    return app
