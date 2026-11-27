from aiohttp import web

from ...app_keys import BOOT_CHECK, ES
from ...core.boot_check import CheckError, checkable, is_checkable, parse_seconds
from ...core.emulationstation import ES_ERRORS
from ..layout import loaded_library

routes = web.RouteTableDef()


def games_in_scope(library, data):
    scope = data.get("scope")
    if scope == "library":
        return checkable(library.select())
    if scope == "system":
        system = str(data.get("system") or "")
        return checkable(library.select(system)) if system else []
    if scope == "game":
        group, _, game_id = str(data.get("game") or "").partition("/")
        game = library.game(group, game_id)
        if game and not is_checkable(game):
            raise CheckError(f"{game.name} is hidden or not a game")
        return [game] if game else []
    raise CheckError("Pick what to check")


@routes.post("/check")
async def start(request):
    es, boot_check = request.app[ES], request.app[BOOT_CHECK]
    library = await loaded_library(request)
    try:
        if boot_check.running:
            raise CheckError("A boot check is already running")
        data = await request.json()
        seconds = parse_seconds(data.get("seconds"))
        games = sorted(games_in_scope(library, data), key=lambda game: game.group_fullname.lower())
        if games and data.get("skip_passed"):
            games = [game for game in games if not boot_check.passed(game)]
            if not games:
                raise CheckError("Every game here already passed")
        running = await es.running_game()
        if running and running.path != data.get("running"):
            return web.json_response({"confirm": f"Quit {running.name} and run the check?",
                                      "running": running.path}, status=409)
        boot_check.start(games, seconds, running.name if running else None)
    except CheckError as error:
        return web.json_response({"error": str(error)}, status=400)
    except (ValueError, AttributeError):
        return web.json_response({"error": "Invalid request"}, status=400)
    except ES_ERRORS:
        return web.json_response({"error": "EmulationStation isn't answering"}, status=503)
    return web.json_response(boot_check.status())


@routes.get("/check/status")
async def status(request):
    return web.json_response(request.app[BOOT_CHECK].status())


@routes.post("/check/stop")
async def stop(request):
    request.app[BOOT_CHECK].stop()
    return web.json_response(request.app[BOOT_CHECK].status())


@routes.get("/check/games")
async def games(request):
    library = await loaded_library(request)
    games = checkable(library.select())
    systems = {game.group: game.group_fullname for game in games}
    return web.json_response({
        "systems": [{"name": name, "fullname": fullname}
                    for name, fullname in sorted(systems.items(), key=lambda system: system[1].lower())],
        "games": [{"key": f"{game.group}/{game.id}", "label": f"{game.name} ({game.system_fullname})"}
                  for game in games]})
