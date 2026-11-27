import asyncio
import secrets

from aiohttp import web

from ...app_keys import CONFIG
from ...config import save_config
from ...core import auth
from ..layout import redirect, render

routes = web.RouteTableDef()


@routes.route("*", "/setup")
async def setup(request):
    config = request.app[CONFIG]
    if config.get("password"):
        raise redirect("/")
    if request.method != "POST":
        return await render(request, "setup/page.html")
    form = await request.post()
    password, confirm = form.get("password", ""), form.get("confirm", "")
    if not password:
        return await render(request, "setup/page.html", error="Choose a password")
    if password != confirm:
        return await render(request, "setup/page.html", error="The passwords don't match")
    config["password"] = await asyncio.to_thread(auth.hash_password, password)
    config["secret"] = secrets.token_hex(32)
    save_config(config)
    auth.redirect_with_session(config)
