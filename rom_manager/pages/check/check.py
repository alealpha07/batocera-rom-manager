import asyncio

from aiohttp import web

from ...utils.boot_check import ES_ERRORS, CheckError, checkable, parse_seconds
from ...utils.emulator import game_title


def scope_games(library, data):
    scope = data.get("scope")
    if scope == "library":
        return checkable(library.select())
    if scope == "system":
        system = str(data.get("system") or "")
        return checkable(library.select(system)) if system else []
    if scope == "game":
        group, _, game_id = str(data.get("game") or "").partition("/")
        found = library.game(group, game_id)
        if found and not checkable([found]):
            raise CheckError("%s is hidden or not a game" % found["name"])
        return [found] if found else []
    raise CheckError("Pick what to check")


async def start(request):
    es, library, boot_check = request.app["es"], request.app["library"], request.app["boot_check"]
    await library.ensure_loaded(es)
    try:
        if boot_check.running:
            raise CheckError("A boot check is already running")
        data = await request.json()
        seconds = parse_seconds(data.get("seconds"))
        games = sorted(await asyncio.to_thread(scope_games, library, data), key=lambda g: g["group_fullname"].lower())
        if games and data.get("skip_passed"):
            games = [g for g in games if not (boot_check.result(g["path"]) or {}).get("passed")]
            if not games:
                raise CheckError("Every game here already passed")
        running = await es.running_game()
        if running and running["path"] != data.get("running"):
            return web.json_response({"confirm": "Quit %s and run the check?" % game_title(running),
                                      "running": running["path"]}, status=409)
        boot_check.start(request.app, games, seconds, game_title(running) if running else None)
    except CheckError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ValueError, AttributeError):
        return web.json_response({"error": "Invalid request"}, status=400)
    except ES_ERRORS:
        return web.json_response({"error": "EmulationStation isn't answering"}, status=503)
    return web.json_response(boot_check.status())


async def status(request):
    return web.json_response(request.app["boot_check"].status())


async def stop(request):
    request.app["boot_check"].stop()
    return web.json_response(request.app["boot_check"].status())


async def games(request):
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    games = await asyncio.to_thread(checkable, library.select())
    systems = {g["group"]: g["group_fullname"] for g in games}
    return web.json_response({
        "systems": [{"name": name, "fullname": fullname}
                    for name, fullname in sorted(systems.items(), key=lambda s: s[1].lower())],
        "games": [{"key": "%s/%s" % (g["group"], g["id"]), "label": "%s (%s)" % (g["name"], g["fullname"])}
                  for g in games]})
