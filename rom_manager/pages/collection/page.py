import asyncio
import urllib.parse

from aiohttp import web

from ...utils.collections import (MANUAL, MODES, PLAYER_COUNTS, CollectionError, collection_files, delete_collection,
                                  genre_collections, genre_label, genres, leading_int, matching_games, read_dynamic,
                                  read_manual, read_settings, rename_collection, split_list, write_dynamic,
                                  write_manual)
from ..collections.page import collection_url
from ..game.page import game_url
from ..layout import flash, render

MAX_SEARCH_RESULTS = 50
MAX_PREVIEW_GAMES = 200


async def find_collection(request):
    name = request.match_info["name"]
    kind = (await asyncio.to_thread(collection_files)).get(name)
    if not kind:
        raise web.HTTPNotFound()
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    return name, kind, library


def preview_games(games):
    return [{"name": g["name"], "fullname": g["fullname"], "url": game_url(g)} for g in games[:MAX_PREVIEW_GAMES]]


def filter_options(games, filters):
    systems = {}
    for game in games:
        if not game["hidden"]:
            systems.setdefault(game["system"], {"name": game["system"], "fullname": game["fullname"], "size": 0})
            systems[game["system"]]["size"] += 1
    for name in filters["systems"] - set(systems):
        systems[name] = {"name": name, "fullname": name, "size": 0}
    genre_options = [{"id": g["id"], "name": g["name"]} for g in genre_collections()]
    top_ids = {g["id"] for g in genre_options}
    genre_options += [{"id": i, "name": genre_label(i)} for i in sorted(filters["genres"] - top_ids)]
    return {"systems": sorted(systems.values(), key=lambda s: s["fullname"].lower()), "genres": genre_options,
            "players": PLAYER_COUNTS}


def manual_context(name, games, query):
    paths = read_manual(name)
    by_path = {g["path"]: g for g in games}
    entries = sorted(({"path": p, "game": by_path.get(p)} for p in paths),
                     key=lambda e: (e["game"] is None, (e["game"] or {}).get("name", e["path"]).lower()))
    results = []
    if query:
        members = set(paths)
        lowered = query.lower()
        results = sorted((g for g in games if g["path"] not in members
                          and (lowered in g["name"].lower() or lowered in g["file"].lower())),
                         key=lambda g: g["name"].lower())
    return {"entries": entries, "results": results[:MAX_SEARCH_RESULTS],
            "more_results": max(len(results) - MAX_SEARCH_RESULTS, 0)}


def dynamic_context(name, games):
    filters, others = read_dynamic(name)
    matched = matching_games(games, filters)
    return {"filters": filters, "others": sorted({node.tag for node in others}),
            "options": filter_options(games, filters), "count": len(matched), "preview": preview_games(matched)}


async def collection(request):
    name, kind, library = await find_collection(request)
    query = request.query.get("q", "").strip()
    try:
        if kind == MANUAL:
            context = await asyncio.to_thread(manual_context, name, library.games, query)
        else:
            context = await asyncio.to_thread(dynamic_context, name, library.games)
        enabled = name in split_list((await asyncio.to_thread(read_settings))["CollectionSystemsCustom"])
        error = None
    except (CollectionError, OSError) as failure:
        context, enabled, error = {}, False, str(failure)
    url = collection_url(name)
    return await render(request, "collection", name=name, kind=kind, modes=MODES, url=url, query=query,
                        enabled=enabled, error=error, game_url=game_url, **context,
                        restart_pending=request.app["collections_restart"].waiting(),
                        restarted=request.query.get("restarted"), restart_done=url + "?restarted=1")


def filters_from(values):
    return {"systems": {s for s in values.getall("system", []) if s},
            "genres": {leading_int(g) for g in values.getall("genre", []) if leading_int(g) in genres()},
            "players": {leading_int(p) for p in values.getall("players", []) if leading_int(p) in PLAYER_COUNTS}}


async def preview(request):
    name, kind, library = await find_collection(request)
    matched = matching_games(library.games, filters_from(request.query))
    return web.json_response({"count": len(matched), "games": preview_games(matched)})


def back(url, query=""):
    return web.HTTPFound(url + ("?" + urllib.parse.urlencode({"q": query}) if query else ""))


async def save_filters(request):
    name, kind, library = await find_collection(request)
    form = await request.post()
    response = back(collection_url(name))
    if kind == MANUAL:
        raise web.HTTPBadRequest(text="Only dynamic collections have filters")
    try:
        others = (await asyncio.to_thread(read_dynamic, name))[1]
        await asyncio.to_thread(write_dynamic, name, filters_from(form), others)
        request.app["collections_restart"].remember()
        flash(response, "success", "Filters saved")
    except (CollectionError, OSError) as error:
        flash(response, "danger", "Filters not saved: %s" % error)
    raise response


def change_members(name, path, add):
    paths = read_manual(name)
    if add and path not in paths:
        write_manual(name, paths + [path])
    elif not add and path in paths:
        write_manual(name, [p for p in paths if p != path])


async def change_member(request, add):
    name, kind, library = await find_collection(request)
    form = await request.post()
    response = back(collection_url(name), form.get("q", ""))
    path = form.get("path", "")
    game = next((g for g in library.games if g["path"] == path), None)
    if kind != MANUAL:
        raise web.HTTPBadRequest(text="Only manual collections have a game list")
    if add and not game:
        flash(response, "danger", "Game not found")
        raise response
    try:
        await asyncio.to_thread(change_members, name, path, add)
        request.app["collections_restart"].remember()
        label = game["name"] if game else path
        flash(response, "success", ("Added %s" if add else "Removed %s") % label)
    except (CollectionError, OSError) as error:
        flash(response, "danger", "Not changed: %s" % error)
    raise response


async def add_game(request):
    await change_member(request, True)


async def remove_game(request):
    await change_member(request, False)


async def rename(request):
    name, kind, library = await find_collection(request)
    form = await request.post()
    try:
        new_name, settings = await asyncio.to_thread(rename_collection, name, form.get("name", ""))
    except (CollectionError, OSError) as error:
        response = back(collection_url(name))
        flash(response, "danger", "Not renamed: %s" % error)
        raise response
    response = back(collection_url(new_name))
    if new_name != name:
        request.app["collections_restart"].remember(settings)
        flash(response, "success", "Renamed to %s" % new_name)
    raise response


async def delete(request):
    name, kind, library = await find_collection(request)
    response = web.HTTPFound("/collections")
    try:
        request.app["collections_restart"].remember(await asyncio.to_thread(delete_collection, name))
        flash(response, "success", "Deleted %s" % name)
    except (CollectionError, OSError) as error:
        response = back(collection_url(name))
        flash(response, "danger", "Not deleted: %s" % error)
    raise response
