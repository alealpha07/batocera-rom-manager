import csv
import datetime
import io

from .library import es_date, human_time, info_form, to_int
from .roms import human_size

CSV_COLUMNS = ("Name", "System", "File", "Size", "Genre", "Year", "Players", "Rating", "Play count", "Play time",
               "Last played", "Favorite", "Hidden", "Boot check")
TXT_COLUMNS = ("Name", "System", "File", "Size")


def csv_row(game, boot):
    meta, form = game["es"], info_form(game["es"])
    playcount = to_int(meta.get("playcount"))
    return [game["name"], game["fullname"], game["file"], human_size(game["size"]), form["genre"],
            form["releasedate"][:4], form["players"], form["rating"], playcount or "",
            human_time(to_int(meta.get("gametime"))), es_date(meta.get("lastplayed")),
            "yes" if form["favorite"] else "", "yes" if game["hidden"] else "",
            ("passed" if boot["passed"] else "failed") if boot else ""]


def csv_text(games, results):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows(csv_row(game, results.get(game["path"])) for game in games)
    return "﻿" + buffer.getvalue()


def txt_text(games, title):
    rows = [TXT_COLUMNS] + [(g["name"], g["fullname"], g["file"], human_size(g["size"])) for g in games]
    widths = [max(len(row[column]) for row in rows) for column in range(len(TXT_COLUMNS))]
    table = ["  ".join(value.ljust(width) for value, width in zip(row[:-1], widths)) +
             "  " + row[-1].rjust(widths[-1]) for row in rows]
    total = "%d games, %s" % (len(games), human_size(sum(g["size"] for g in games)))
    return "\n".join([title, ""] + table + ["", total, ""])


def export_title(selection, query, hidden, failed):
    parts = [selection]
    if query:
        parts.append("search '%s'" % query)
    if hidden:
        parts.append("hidden included")
    if failed:
        parts.append("failed boot check only")
    return ", ".join(parts + [datetime.date.today().isoformat()])
