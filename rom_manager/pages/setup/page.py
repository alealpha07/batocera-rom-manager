import asyncio
import secrets

from aiohttp import web

from ...config import save_config
from ...utils import auth
from ..layout import render


async def setup(request):
    config = request.app["config"]
    if config.get("password"):
        raise web.HTTPFound("/")
    if request.method != "POST":
        return render(request, "setup")
    form = await request.post()
    password, confirm = form.get("password", ""), form.get("confirm", "")
    if not password:
        return render(request, "setup", error="Choose a password")
    if password != confirm:
        return render(request, "setup", error="The passwords don't match")
    config["password"] = await asyncio.to_thread(auth.hash_password, password)
    config["secret"] = secrets.token_hex(32)
    save_config(config)
    auth.redirect_with_session(config)
