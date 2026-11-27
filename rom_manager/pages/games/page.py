import csv
import datetime
import io
import re

from aiohttp import web

from ...app_keys import BOOT_CHECK, LIBRARY
from ...core.emulationstation import known_systems, system_fullname
from ...core.game_files import human_size
from ...core.library import es_date, human_time, to_int
from ..layout import loaded_library, local_url, redirect, render, sidebar

EXPORT_TYPES = {"csv": "text/csv", "txt": "text/plain"}
CSV_COLUMNS = ("Name", "System", "File", "Size", "Genre", "Year", "Players", "Rating", "Play count", "Play time",
               "Last played", "Favorite", "Hidden", "Boot check")
TXT_COLUMNS = ("Name", "System", "File", "Size")
UTF8_BOM = "﻿"

routes = web.RouteTableDef()


class GameListView:
    def __init__(self, query):
        self.system = query.get("system", "")
        self.search = query.get("q", "").strip()
        self.sort = query.get("sort", "name")
        self.show_hidden = query.get("hidden") == "1"
        self.only_failed = query.get("failed") == "1"

    def games(self, library, boot_check):
        games = library.select(self.system, self.search, self.sort, self.show_hidden)
        return [game for game in games if boot_check.failed(game)] if self.only_failed else games

    def title(self):
        parts = [system_fullname(self.system) if self.system else "All games"]
        if self.search:
            parts.append(f"search '{self.search}'")
        if self.show_hidden:
            parts.append("hidden included")
        if self.only_failed:
            parts.append("failed boot check only")
        return ", ".join(parts + [datetime.date.today().isoformat()])


def csv_row(game, boot):
    info = game.info_form
    return [game.name, game.system_fullname, game.relative_path, human_size(game.size), info["genre"],
            info["releasedate"][:4], info["players"], info["rating"], to_int(game.metadata.get("playcount")) or "",
            human_time(to_int(game.metadata.get("gametime"))), es_date(game.metadata.get("lastplayed")),
            "yes" if info["favorite"] else "", "yes" if game.hidden else "",
            ("passed" if boot["passed"] else "failed") if boot else ""]


def csv_text(games, boot_check):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows(csv_row(game, boot_check.result(game.path)) for game in games)
    return UTF8_BOM + buffer.getvalue()


def txt_text(games, title):
    rows = [TXT_COLUMNS] + [(game.name, game.system_fullname, game.relative_path, human_size(game.size))
                            for game in games]
    widths = [max(len(row[column]) for row in rows) for column in range(len(TXT_COLUMNS))]
    table = ["  ".join(value.ljust(width) for value, width in zip(row[:-1], widths)) +
             "  " + row[-1].rjust(widths[-1]) for row in rows]
    total = f"{len(games)} games, {human_size(sum(game.size for game in games))}"
    return "\n".join([title, ""] + table + ["", total, ""])


@routes.get("/")
async def index(request):
    raise redirect("/games")


@routes.get("/games")
async def games_page(request):
    library, view = await loaded_library(request), GameListView(request.query)
    games = view.games(library, request.app[BOOT_CHECK])
    params = {key: value for key, value in request.query.items() if value}
    return await render(request, "games/page.html", sidebar=sidebar(request, view.system, view.show_hidden),
                        games=games, total_size=sum(game.size for game in games), view=view,
                        boot_results=request.app[BOOT_CHECK].results, params=params, error=library.error)


@routes.get("/games/export")
async def export(request):
    kind = request.query.get("format", "")
    if kind not in EXPORT_TYPES:
        raise web.HTTPBadRequest(text="Unknown export format")
    library, view = await loaded_library(request), GameListView(request.query)
    boot_check = request.app[BOOT_CHECK]
    games = view.games(library, boot_check)
    text = csv_text(games, boot_check) if kind == "csv" else txt_text(games, view.title())
    filename = (re.sub(r"[^\w.-]", "_", view.system) or "all-games") + "." + kind
    return web.Response(text=text, content_type=EXPORT_TYPES[kind], charset="utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@routes.post("/refresh")
async def refresh(request):
    form = await request.post()
    known_systems.cache_clear()
    await request.app[LIBRARY].refresh()
    raise redirect(local_url(form.get("next", "")))
