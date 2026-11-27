import asyncio
import datetime
import os
import re
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field

from .emulationstation import ES_ERRORS, relative_rom_path, system_fullname
from .game_files import MEDIA_KINDS, GameFiles, GameFilesError, file_size, files_of_game, media_content_type

INFO_TEXT_FIELDS = ("name", "desc", "genre", "developer", "publisher", "players", "region")
INFO_FLAGS = ("favorite", "hidden")
SORT_KEYS = {
    "name": lambda game: game.name.lower(),
    "system": lambda game: game.system_fullname.lower(),
    "file": lambda game: game.relative_path.lower(),
    "size": lambda game: game.size,
}


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
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if re.match(r"\d{8}T", value) else ""


def human_time(seconds):
    if not seconds:
        return ""
    hours, minutes = divmod(seconds // 60, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m" if minutes else "<1m"


def info_form(metadata):
    rating = to_float(metadata.get("rating"))
    form = {key: metadata.get(key) or "" for key in INFO_TEXT_FIELDS}
    form.update({key: metadata.get(key) == "true" for key in INFO_FLAGS})
    form["releasedate"] = es_date(metadata.get("releasedate"))
    form["rating"] = f"{round(rating * 5, 1):g}" if rating else ""
    return form


def es_value(key, value):
    if key == "name" and not value:
        raise ValueError("The name can't be empty")
    if key == "releasedate" and value:
        try:
            return datetime.date.fromisoformat(value).strftime("%Y%m%dT000000")
        except ValueError:
            raise ValueError(f"Invalid release date: {value}")
    if key == "rating":
        try:
            stars = float(value or 0)
        except ValueError:
            stars = -1
        if not 0 <= stars <= 5:
            raise ValueError("The rating goes from 0 to 5")
        return f"{round(stars / 5, 4):g}"
    return value


def info_changes(metadata, form):
    current, changes = info_form(metadata), {}
    for key in INFO_FLAGS:
        if (form.get(key) == "1") != current[key]:
            changes[key] = "true" if form.get(key) == "1" else "false"
    for key in INFO_TEXT_FIELDS + ("releasedate", "rating"):
        value = form.get(key, "").replace("\r\n", "\n").strip()
        if value != current[key].strip():
            changes[key] = es_value(key, value)
    return changes


@dataclass
class Game:
    id: str
    system: str
    system_fullname: str
    group: str
    group_fullname: str
    name: str
    path: str
    hidden: bool
    metadata: dict = field(repr=False)
    relative_path: str = ""
    size: int = 0

    @property
    def url(self):
        return f"/game/{urllib.parse.quote(self.group)}/{urllib.parse.quote(self.id)}"

    @property
    def favorite(self):
        return self.metadata.get("favorite") == "true"

    @property
    def description(self):
        return self.metadata.get("desc") or ""

    @property
    def media(self):
        return [kind for kind in MEDIA_KINDS if (self.metadata.get(kind) or "").startswith("/systems/")]

    @property
    def info_form(self):
        return info_form(self.metadata)

    @property
    def details(self):
        rating = to_float(self.metadata.get("rating"))
        playcount = to_int(self.metadata.get("playcount"))
        details = [
            ("Genre", self.metadata.get("genre")),
            ("Developer", self.metadata.get("developer")),
            ("Publisher", self.metadata.get("publisher")),
            ("Released", es_date(self.metadata.get("releasedate"))),
            ("Players", self.metadata.get("players")),
            ("Rating", f"{rating * 5:.1f} / 5" if rating else ""),
            ("Region", (self.metadata.get("region") or "").upper()),
            ("Language", self.metadata.get("lang")),
            ("Series", self.metadata.get("family")),
            ("Times played", str(playcount) if playcount else ""),
            ("Play time", human_time(to_int(self.metadata.get("gametime")))),
            ("Last played", es_date(self.metadata.get("lastplayed"))),
        ]
        return [(label, value) for label, value in details if value]

    def matches_search(self, query):
        query = query.lower()
        return query in self.name.lower() or query in self.relative_path.lower()

    def add_file_info(self):
        self.relative_path = relative_rom_path(self.path, self.system)
        self.size = sum(file_size(file) for file in files_of_game(self.path))


def game_from_es(es_system, metadata):
    path = metadata["path"]
    system = metadata.get("systemName") or es_system["name"]
    group_fullname = es_system.get("fullname") or es_system["name"]
    return Game(id=metadata.get("id", ""), system=system, system_fullname=system_fullname(system, group_fullname),
                group=es_system["name"], group_fullname=group_fullname,
                name=(metadata.get("name") or "").strip() or os.path.splitext(os.path.basename(path))[0],
                path=path, hidden=metadata.get("hidden") == "true", metadata=metadata)


async def games_from_es(es):
    systems = [system for system in await es.systems()
               if system.get("collection") != "true" and int(system.get("totalGames") or 0)]
    # grouped systems (prboom inside ports...) are listed by their visible parent too: the parent wins
    systems.sort(key=lambda system: system.get("visible") != "true")
    games, seen = [], set()
    for system in systems:
        for metadata in await es.games(system["name"]):
            path = metadata.get("path", "")
            if path and path not in seen:
                seen.add(path)
                games.append(game_from_es(system, metadata))
    return games


def with_file_info(games):
    for game in games:
        game.add_file_info()
    return games


class Library:
    def __init__(self, es):
        self.es = es
        self.games = []
        self.loaded = False
        self.error = None
        self.lock = asyncio.Lock()

    async def ensure_loaded(self):
        async with self.lock:
            if not self.loaded:
                await self.load()

    async def refresh(self):
        async with self.lock:
            await self.load()

    async def rescan(self):
        await self.es.reload_games()
        await self.refresh()

    async def load(self):
        try:
            listed = await games_from_es(self.es)
        except (*ES_ERRORS, ValueError):
            self.error = "EmulationStation isn't answering, so the game list may be out of date."
            return
        self.games = await asyncio.to_thread(with_file_info, listed)
        self.error = None
        self.loaded = True

    def visible(self, show_hidden=False):
        return [game for game in self.games if show_hidden or not game.hidden]

    def select(self, system="", query="", sort="name", show_hidden=False):
        games = [game for game in self.visible(show_hidden)
                 if (not system or system in (game.system, game.group)) and game.matches_search(query)]
        sort_key = SORT_KEYS.get(sort.lstrip("-"), SORT_KEYS["name"])
        return sorted(games, key=sort_key, reverse=sort.startswith("-"))

    def game(self, system, game_id):
        return next((game for game in self.games if game.id == game_id and system in (game.group, game.system)), None)

    def game_at(self, path):
        return next((game for game in self.games if game.path == path), None)

    def counts(self, show_hidden=False):
        counts = Counter()
        for game in self.visible(show_hidden):
            counts.update({game.system, game.group})
        return counts

    async def save_info(self, game, form):
        changes = info_changes(await self.es.game(game.system, game.id), form)
        if changes:
            await self.es.update_game(game.system, game.id, changes)
            await self.rescan()
        return bool(changes)

    async def upload_media(self, game, kind, filename, chunks):
        files = GameFiles(game)
        old_file = (await asyncio.to_thread(files.media_files)).get(kind)
        await self.es.upload_media(game.system, game.id, kind, media_content_type(kind, filename), chunks)
        await self.rescan()
        try:
            if old_file != (await asyncio.to_thread(files.media_files)).get(kind):
                await asyncio.to_thread(files.delete_unused_media, old_file)
        except (GameFilesError, OSError):
            pass

    async def remove_media(self, game, kind):
        # ES writes its unsaved changes back over the whole gamelist entry, so they are saved before the edit
        await self.es.reload_games()
        try:
            await asyncio.to_thread(GameFiles(game).remove_media, kind)
        finally:
            await self.rescan()

    async def delete_game(self, game):
        es_updated = False
        try:
            files = GameFiles(game)
            plan = await asyncio.to_thread(files.delete_plan)
            try:
                await self.es.remove_game(game.system, game.path)
                es_updated = True
            except ES_ERRORS:
                pass
            await asyncio.to_thread(files.delete, plan)
        except (GameFilesError, OSError):
            es_updated = False
            raise
        finally:
            await (self.refresh() if es_updated else self.rescan())
