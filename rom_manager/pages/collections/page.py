import asyncio
import urllib.parse
from collections import Counter

from aiohttp import web

from ...utils.collections import (AUTO_COLLECTIONS, GROUP_MODES, MANUAL, MODES, CollectionError, collection_files,
                                  create_collection, game_genre_ids, genre_collections, list_collections,
                                  matching_games, read_dynamic, read_manual, read_settings, split_list,
                                  update_settings)
from ..layout import flash, render

MAX_UNKNOWN_GENRES = 50


def collection_url(name):
    return "/collections/" + urllib.parse.quote(name, safe="")


def genre_summary(games):
    counts, unknown, no_genre = Counter(), Counter(), 0
    for game in games:
        ids = game_genre_ids(game)
        counts.update(ids)
        genre = (game["es"].get("genre") or "").strip()
        if not genre:
            no_genre += 1
        elif not ids:
            unknown[genre] += 1
    return counts, unknown, no_genre


def collection_size(collection, games):
    try:
        if collection["kind"] == MANUAL:
            paths = {g["path"] for g in games}
            return sum(1 for path in read_manual(collection["name"]) if path in paths)
        return len(matching_games(games, read_dynamic(collection["name"])[0]))
    except (CollectionError, OSError):
        return None


def overview(games):
    settings = read_settings()
    visible = [g for g in games if not g["hidden"]]
    counts, unknown, no_genre = genre_summary(visible)
    enabled = set(split_list(settings["CollectionSystemsAuto"]))
    own = [dict(c, url=collection_url(c["name"]), size=collection_size(c, games)) for c in list_collections(settings)]
    return {"own": own,
            "auto": [{"key": key, "name": name, "enabled": key in enabled} for key, name in AUTO_COLLECTIONS],
            "genres": [dict(g, size=counts[g["id"]], enabled=g["key"] in enabled) for g in genre_collections()],
            "group": settings["UseCustomCollectionsSystemEx"], "unknown": unknown.most_common(MAX_UNKNOWN_GENRES),
            "more_unknown": max(len(unknown) - MAX_UNKNOWN_GENRES, 0), "unknown_total": sum(unknown.values()),
            "no_genre": no_genre}


async def collections(request):
    library = request.app["library"]
    await library.ensure_loaded(request.app["es"])
    try:
        data, error = await asyncio.to_thread(overview, library.games), None
    except (CollectionError, OSError) as failure:
        data, error = None, str(failure)
    return await render(request, "collections", data=data, error=error, group_modes=GROUP_MODES,
                        modes=MODES, restart_pending=request.app["collections_restart"].waiting(),
                        restarted=request.query.get("restarted"), restart_done="/collections?restarted=1")


def settings_from_form(form):
    settings = read_settings()
    wanted = set(form.getall("auto", []))
    keys = [key for key, _ in AUTO_COLLECTIONS] + [genre["key"] for genre in genre_collections()]
    kept = [item for item in split_list(settings["CollectionSystemsAuto"]) if item not in keys]
    own, wanted_own = set(collection_files()), set(form.getall("custom", []))
    enabled_own = [name for name in split_list(settings["CollectionSystemsCustom"])
                   if name not in own or name in wanted_own]
    enabled_own += sorted((name for name in wanted_own & own if name not in enabled_own), key=str.lower)
    group = form.get("group", "")
    if group not in dict(GROUP_MODES):
        raise CollectionError("Unknown grouping")
    return update_settings(CollectionSystemsAuto=",".join([key for key in keys if key in wanted] + kept),
                           CollectionSystemsCustom=",".join(enabled_own), UseCustomCollectionsSystemEx=group)


async def save_settings(request):
    form = await request.post()
    response = web.HTTPFound("/collections")
    try:
        request.app["collections_restart"].remember(await asyncio.to_thread(settings_from_form, form))
        flash(response, "success", "Saved")
    except (CollectionError, OSError) as error:
        flash(response, "danger", "Not saved: %s" % error)
    raise response


async def create(request):
    form = await request.post()
    try:
        if form.get("kind") not in MODES:
            raise CollectionError("Choose manual or dynamic")
        name, settings = await asyncio.to_thread(create_collection, form.get("name", ""), form["kind"])
    except (CollectionError, OSError) as error:
        response = web.HTTPFound("/collections")
        flash(response, "danger", "Collection not created: %s" % error)
        raise response
    request.app["collections_restart"].remember(settings)
    response = web.HTTPFound(collection_url(name))
    flash(response, "success", "Created %s" % name)
    raise response
