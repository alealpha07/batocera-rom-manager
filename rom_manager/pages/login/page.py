import asyncio

from aiohttp import web

from ...utils import auth
from ..layout import render


async def login(request):
    config = request.app["config"]
    if not config.get("password"):
        raise web.HTTPFound("/setup")
    if request.method != "POST":
        return await render(request, "login")
    form = await request.post()
    if await asyncio.to_thread(auth.check_password, form.get("password", ""), config["password"]):
        auth.redirect_with_session(config)
    await asyncio.sleep(1)
    return await render(request, "login", error="Wrong password")


async def logout(request):
    response = web.HTTPFound("/login")
    auth.end_session(response)
    raise response
