import asyncio
import os
import re
from collections import Counter

import aiohttp

from .es import all_systems
from .roms import display_path, file_size, game_files

ES_RESCAN_SETTLE_SECONDS = 1
MEDIA_KINDS = ("thumbnail", "image", "titleshot", "marquee", "wheel", "video")
MAX_LISTED_FILES = 200
SORT_KEYS = {
    "name": lambda g: g["name"].lower(),
    "system": lambda g: g["fullname"].lower(),
    "file": lambda g: g["file"].lower(),
    "size": lambda g: g["size"],
}


class Library:
    def __init__(self):
        self.games = []
        self.loaded = False
        self.error = None
        self.lock = asyncio.Lock()

    async def ensure_loaded(self, es):
        async with self.lock:
            if not self.loaded:
                await self.load(es)

    async def refresh(self, es):
        async with self.lock:
            await self.load(es)

    async def rescan(self, es):
        try:
            await es.reload_games()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        await asyncio.sleep(ES_RESCAN_SETTLE_SECONDS)
        await self.refresh(es)

    async def load(self, es):
        try:
            listed = await fetch_games(es)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            self.error = "EmulationStation isn't answering, so the game list may be out of date."
            return
        self.games = await asyncio.to_thread(add_file_info, listed)
        self.error = None
        self.loaded = True

    def select(self, system="", query="", sort="name", hidden=False):
        query = query.lower()
        games = [g for g in self.games
                 if (hidden or not g["hidden"])
                 and (not system or system in (g["system"], g["group"]))
                 and (query in g["name"].lower() or query in g["file"].lower())]
        key = SORT_KEYS.get(sort.lstrip("-"), SORT_KEYS["name"])
        return sorted(games, key=key, reverse=sort.startswith("-"))

    def game(self, system, game_id):
        return next((g for g in self.games if g["id"] == game_id and system in (g["group"], g["system"])), None)

    def counts(self, hidden=False):
        counts = Counter()
        for g in self.games:
            if hidden or not g["hidden"]:
                counts.update({g["system"], g["group"]})
        return counts

    def total(self, hidden=False):
        return sum(1 for g in self.games if hidden or not g["hidden"])


async def fetch_games(es):
    systems = [s for s in await es.systems()
               if s.get("collection") != "true" and int(s.get("totalGames") or 0)]
    # grouped systems (prboom inside ports...) are listed by their visible parent too: the parent wins
    systems.sort(key=lambda s: s.get("visible") != "true")
    games, seen = [], set()
    for system in systems:
        for game in await es.games(system["name"]):
            path = game.get("path", "")
            if path and path not in seen:
                seen.add(path)
                games.append(make_game(system, game))
    return games


def make_game(es_system, game):
    path = game["path"]
    system = game.get("systemName") or es_system["name"]
    return {"id": game.get("id", ""), "system": system, "group": es_system["name"],
            "group_fullname": es_system.get("fullname") or es_system["name"],
            "fullname": all_systems().get(system, {}).get("fullname") or es_system.get("fullname") or system,
            "name": (game.get("name") or "").strip() or os.path.splitext(os.path.basename(path))[0],
            "path": path, "hidden": game.get("hidden") == "true", "es": game}


def add_file_info(games):
    for game in games:
        game["file"] = display_path(game["path"], game["system"])
        game["size"] = sum(file_size(f) for f in game_files(game["path"]))
    return games


def to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def es_date(value):
    value = value or ""
    return "%s-%s-%s" % (value[:4], value[4:6], value[6:8]) if re.match(r"\d{8}T", value) else ""


def human_time(seconds):
    if not seconds:
        return ""
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return "%dh %02dm" % (hours, minutes)
    return "%dm" % minutes if minutes else "<1m"


def game_details(game):
    meta = game["es"]
    rating = to_float(meta.get("rating"))
    playcount = to_int(meta.get("playcount"))
    details = [
        ("Genre", meta.get("genre")),
        ("Developer", meta.get("developer")),
        ("Publisher", meta.get("publisher")),
        ("Released", es_date(meta.get("releasedate"))),
        ("Players", meta.get("players")),
        ("Rating", "%.1f / 5" % (rating * 5) if rating else ""),
        ("Region", (meta.get("region") or "").upper()),
        ("Language", meta.get("lang")),
        ("Series", meta.get("family")),
        ("Times played", str(playcount) if playcount else ""),
        ("Play time", human_time(to_int(meta.get("gametime")))),
        ("Last played", es_date(meta.get("lastplayed"))),
    ]
    return [(label, value) for label, value in details if value]


def game_media(game):
    return [kind for kind in MEDIA_KINDS if (game["es"].get(kind) or "").startswith("/systems/")]


def file_list(game):
    files = game_files(game["path"])
    listed = [(display_path(f, game["system"]), file_size(f)) for f in files[:MAX_LISTED_FILES]]
    return listed, len(files) - len(listed)
