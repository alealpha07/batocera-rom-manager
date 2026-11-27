import jinja2
from aiohttp import web

from .core.boot_check import BootCheck
from .core.collections import PendingRestart
from .core.emulationstation import EmulationStation
from .core.library import Library
from .core.transfers import DownloadQueue

CONFIG = web.AppKey("config", dict)
TEMPLATES = web.AppKey("templates", jinja2.Environment)
ES = web.AppKey("es", EmulationStation)
LIBRARY = web.AppKey("library", Library)
DOWNLOADS = web.AppKey("downloads", DownloadQueue)
BOOT_CHECK = web.AppKey("boot_check", BootCheck)
COLLECTIONS_RESTART = web.AppKey("collections_restart", PendingRestart)
