import asyncio
import urllib.parse
from collections import Counter

from aiohttp import web

from ...app_keys import COLLECTIONS_RESTART
from ...core.collections import (AUTO_COLLECTIONS, GROUPING_MODES, KINDS, MANUAL, PLAYER_COUNTS, CollectionError,
                                 CollectionSettings, Filters, all_collections, collection_kinds, create_collection,
                                 delete_collection, es_genres, find_collection, rename_collection)
from ..layout import loaded_library, redirect, render

MAX_UNKNOWN_GENRES = 50
MAX_SEARCH_RESULTS = 50
MAX_PREVIEW_GAMES = 200

routes = web.RouteTableDef()


def collection_url(name):
    return "/collections/" + urllib.parse.quote(name, safe="")


def restart_banner(request, done_url):
    return {"restart_pending": request.app[COLLECTIONS_RESTART].waiting(),
            "restarted": request.query.get("restarted"), "restart_done": done_url + "?restarted=1"}


def genre_summary(games):
    counts, unknown, without_genre = Counter(), Counter(), 0
    for game in games:
        ids = es_genres().ids_of(game)
        counts.update(ids)
        genre = (game.metadata.get("genre") or "").strip()
        if not genre:
            without_genre += 1
        elif not ids:
            unknown[genre] += 1
    return counts, unknown, without_genre


def collection_size(collection, games):
    try:
        return collection.size(games)
    except (CollectionError, OSError):
        return None


def overview(games):
    settings = CollectionSettings.read()
    counts, unknown, without_genre = genre_summary([game for game in games if not game.hidden])
    own = [{"name": collection.name, "kind": collection.kind, "url": collection_url(collection.name),
            "enabled": collection.name in settings.custom, "size": collection_size(collection, games)}
           for collection in all_collections()]
    return {"own": own,
            "auto": [{"key": key, "name": name, "enabled": key in settings.auto} for key, name in AUTO_COLLECTIONS],
            "genres": [{"key": genre.collection_key, "name": genre.english_name, "size": counts[genre.id],
                        "enabled": genre.collection_key in settings.auto} for genre in es_genres().collections],
            "grouping": settings.grouping, "unknown": unknown.most_common(MAX_UNKNOWN_GENRES),
            "more_unknown": max(len(unknown) - MAX_UNKNOWN_GENRES, 0), "unknown_total": sum(unknown.values()),
            "without_genre": without_genre}


@routes.get("/collections")
async def collections_page(request):
    library = await loaded_library(request)
    try:
        data, error = await asyncio.to_thread(overview, library.games), None
    except (CollectionError, OSError) as failure:
        data, error = None, str(failure)
    return await render(request, "collections/list.html", data=data, error=error, grouping_modes=GROUPING_MODES,
                        kinds=KINDS, **restart_banner(request, "/collections"))


def settings_from_form(form):
    settings = CollectionSettings.read()
    grouping = form.get("group", "")
    if grouping not in dict(GROUPING_MODES):
        raise CollectionError("Unknown grouping")
    wanted_auto = set(form.getall("auto", []))
    managed_auto = [key for key, _ in AUTO_COLLECTIONS] + [genre.collection_key for genre in es_genres().collections]
    unmanaged_auto = [key for key in settings.auto if key not in managed_auto]
    own, wanted_own = set(collection_kinds()), set(form.getall("custom", []))
    custom = [name for name in settings.custom if name not in own or name in wanted_own]
    custom += sorted((name for name in wanted_own & own if name not in custom), key=str.lower)
    settings.auto = [key for key in managed_auto if key in wanted_auto] + unmanaged_auto
    settings.custom, settings.grouping = custom, grouping
    return settings.write()


@routes.post("/collections/settings")
async def save_settings(request):
    form = await request.post()
    try:
        request.app[COLLECTIONS_RESTART].remember(await asyncio.to_thread(settings_from_form, form))
    except (CollectionError, OSError) as error:
        raise redirect("/collections", "danger", f"Not saved: {error}")
    raise redirect("/collections", "success", "Saved")


@routes.post("/collections/new")
async def create(request):
    form = await request.post()
    try:
        if form.get("kind") not in KINDS:
            raise CollectionError("Choose manual or dynamic")
        collection, settings = await asyncio.to_thread(create_collection, form.get("name", ""), form["kind"])
    except (CollectionError, OSError) as error:
        raise redirect("/collections", "danger", f"Collection not created: {error}")
    request.app[COLLECTIONS_RESTART].remember(settings)
    raise redirect(collection_url(collection.name), "success", f"Created {collection.name}")


async def collection_from(request):
    try:
        return await asyncio.to_thread(find_collection, request.match_info["name"])
    except CollectionError:
        raise web.HTTPNotFound()


def preview_games(games):
    return [{"name": game.name, "fullname": game.system_fullname, "url": game.url}
            for game in games[:MAX_PREVIEW_GAMES]]


def filter_options(games, filters):
    systems = {}
    for game in games:
        if not game.hidden:
            systems.setdefault(game.system, game.system_fullname)
    for name in filters.systems - set(systems):
        systems[name] = name
    genres = [(genre.id, genre.english_name) for genre in es_genres().collections]
    top_level_ids = {genre_id for genre_id, _ in genres}
    genres += [(genre_id, es_genres().label(genre_id)) for genre_id in sorted(filters.genres - top_level_ids)]
    return {"systems": sorted(systems.items(), key=lambda system: system[1].lower()), "genres": genres,
            "players": PLAYER_COUNTS}


def manual_context(collection, games, query):
    paths = collection.game_paths()
    by_path = {game.path: game for game in games}
    entries = sorted(((path, by_path.get(path)) for path in paths),
                     key=lambda entry: (entry[1] is None, (entry[1].name if entry[1] else entry[0]).lower()))
    results = []
    if query:
        members = set(paths)
        results = sorted((game for game in games if game.path not in members and game.matches_search(query)),
                         key=lambda game: game.name.lower())
    return {"entries": entries, "results": results[:MAX_SEARCH_RESULTS],
            "more_results": max(len(results) - MAX_SEARCH_RESULTS, 0)}


def dynamic_context(collection, games):
    filters, other_nodes = collection.read()
    matched = filters.matching_games(games)
    return {"filters": filters, "other_filters": sorted({node.tag for node in other_nodes}),
            "options": filter_options(games, filters), "count": len(matched), "preview": preview_games(matched)}


def detail_context(collection, games, query):
    context = (manual_context(collection, games, query) if collection.kind == MANUAL
               else dynamic_context(collection, games))
    return dict(context, enabled=collection.name in CollectionSettings.read().custom)


@routes.get("/collections/{name}")
async def collection_page(request):
    collection, library = await collection_from(request), await loaded_library(request)
    query = request.query.get("q", "").strip()
    try:
        context, error = await asyncio.to_thread(detail_context, collection, library.games, query), None
    except (CollectionError, OSError) as failure:
        context, error = {"enabled": False}, str(failure)
    url = collection_url(collection.name)
    return await render(request, "collections/detail.html", collection=collection, kinds=KINDS, url=url, query=query,
                        error=error, **context, **restart_banner(request, url))


@routes.get("/collections/{name}/preview")
async def preview(request):
    await collection_from(request)
    library = await loaded_library(request)
    matched = Filters.from_form(request.query).matching_games(library.games)
    return web.json_response({"count": len(matched), "games": preview_games(matched)})


def with_search(url, query):
    return url + ("?" + urllib.parse.urlencode({"q": query}) if query else "")


@routes.post("/collections/{name}/filters")
async def save_filters(request):
    collection = await collection_from(request)
    if collection.kind == MANUAL:
        raise web.HTTPBadRequest(text="Only dynamic collections have filters")
    form = await request.post()
    url = collection_url(collection.name)
    try:
        other_nodes = (await asyncio.to_thread(collection.read))[1]
        await asyncio.to_thread(collection.save, Filters.from_form(form), other_nodes)
    except (CollectionError, OSError) as error:
        raise redirect(url, "danger", f"Filters not saved: {error}")
    request.app[COLLECTIONS_RESTART].remember()
    raise redirect(url, "success", "Filters saved")


async def change_member(request, add):
    collection, library = await collection_from(request), await loaded_library(request)
    if collection.kind != MANUAL:
        raise web.HTTPBadRequest(text="Only manual collections have a game list")
    form = await request.post()
    back = with_search(collection_url(collection.name), form.get("q", ""))
    path = form.get("path", "")
    game = library.game_at(path)
    if add and not game:
        raise redirect(back, "danger", "Game not found")
    try:
        await asyncio.to_thread(collection.add if add else collection.remove, path)
    except (CollectionError, OSError) as error:
        raise redirect(back, "danger", f"Not changed: {error}")
    request.app[COLLECTIONS_RESTART].remember()
    label = game.name if game else path
    raise redirect(back, "success", f"Added {label}" if add else f"Removed {label}")


@routes.post("/collections/{name}/add")
async def add_game(request):
    await change_member(request, True)


@routes.post("/collections/{name}/remove")
async def remove_game(request):
    await change_member(request, False)


@routes.post("/collections/{name}/rename")
async def rename(request):
    collection = await collection_from(request)
    form = await request.post()
    try:
        renamed, settings = await asyncio.to_thread(rename_collection, collection, form.get("name", ""))
    except (CollectionError, OSError) as error:
        raise redirect(collection_url(collection.name), "danger", f"Not renamed: {error}")
    if renamed.name == collection.name:
        raise redirect(collection_url(collection.name))
    request.app[COLLECTIONS_RESTART].remember(settings)
    raise redirect(collection_url(renamed.name), "success", f"Renamed to {renamed.name}")


@routes.post("/collections/{name}/delete")
async def delete(request):
    collection = await collection_from(request)
    try:
        settings = await asyncio.to_thread(delete_collection, collection)
    except (CollectionError, OSError) as error:
        raise redirect(collection_url(collection.name), "danger", f"Not deleted: {error}")
    request.app[COLLECTIONS_RESTART].remember(settings)
    raise redirect("/collections", "success", f"Deleted {collection.name}")
