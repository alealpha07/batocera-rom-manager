import asyncio

from aiohttp import web

from ...app_keys import CONFIG
from ...core import auth
from ..layout import redirect, render

routes = web.RouteTableDef()


@routes.route("*", "/login")
async def login(request):
    config = request.app[CONFIG]
    if not config.get("password"):
        raise redirect("/setup")
    if request.method != "POST":
        return await render(request, "login/page.html")
    form = await request.post()
    if await asyncio.to_thread(auth.check_password, form.get("password", ""), config["password"]):
        auth.redirect_with_session(config)
    await asyncio.sleep(1)
    return await render(request, "login/page.html", error="Wrong password")


@routes.post("/logout")
async def logout(request):
    response = redirect("/login")
    auth.end_session(response)
    raise response
