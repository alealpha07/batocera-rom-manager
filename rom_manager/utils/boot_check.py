import asyncio
import datetime
import json
import os
import re
import time

import aiohttp

from ..config import HEALTH_FILE
from .emulator import game_title, quit_game, wait_es_ready

LAUNCH_LOGS = ("/userdata/system/logs/es_launch_stdout.log", "/userdata/system/logs/es_launch_stderr.log")
DEFAULT_SECONDS, MIN_SECONDS, MAX_SECONDS = 20, 5, 120
START_WAIT_SECONDS = 15
STAT_FIELDS = ("playcount", "lastplayed", "gametime")
MAX_ERROR_LINES = 6
MAX_LINE_LENGTH = 220
APP_SYSTEMS = {"steam", "flatpak", "moonlight", "kodi", "windows_installers"}
APP_EXTENSIONS = (".xci_config",)
TOOL_SCRIPT = re.compile(r"\b(wget|curl|pacman|apt-get|install|updater?|git clone)\b", re.I)
LOG_PREFIX = re.compile(r"^\[[^\]]*\] \[(\w+)\s*\] \(([^)]*)\): (?:\[(?:stderr|stdout)\] )?")
ERROR_WORDS = re.compile(r"\b(error|fatal|fail(ed|ure)?|could ?n[o']t|cannot|can't|unable|missing|not found|"
                         r"no such|invalid|unsupported|corrupt(ed)?|segmentation|abort(ed)?|exception|panic|"
                         r"crash(ed)?|not an?)\b", re.I)
NOISE = re.compile(r"\[(libretro )?(INFO|DEBUG)\]|GET_VARIABLE|Not implemented|batocera-launch exception|"
                   r"Failed to connect to Wayland|Qt depends on a UTF-8 locale|QPainter::|qt\.qpa\.|QDBusError|"
                   r"org\.freedesktop\.", re.I)
WARNING = re.compile(r"\bWARN(ING)?\b")
EXIT_STATUS = re.compile(r"Exiting batocera-launch with status (\d+)")
ES_ERRORS = (aiohttp.ClientError, asyncio.TimeoutError)


class CheckError(Exception):
    pass


def load_results():
    try:
        with open(HEALTH_FILE) as fh:
            results = json.load(fh)
    except (OSError, ValueError):
        return {}
    return results if isinstance(results, dict) else {}


def save_results(results):
    os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
    temp_file = HEALTH_FILE + ".tmp"
    with open(temp_file, "w") as fh:
        json.dump(results, fh, indent=1)
    os.replace(temp_file, HEALTH_FILE)


def parse_seconds(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise CheckError("Seconds per game must be a number")
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise CheckError("Seconds per game go from %d to %d" % (MIN_SECONDS, MAX_SECONDS))
    return seconds


def is_game(game):
    if game["hidden"] or game["system"] in APP_SYSTEMS or game["path"].lower().endswith(APP_EXTENSIONS):
        return False
    if game["path"].endswith(".sh"):
        try:
            with open(game["path"], errors="ignore") as fh:
                return not TOOL_SCRIPT.search(fh.read())
        except OSError:
            pass
    return True


def checkable(games):
    return [game for game in games if is_game(game)]


def error_lines(text):
    lines = []
    for raw in text.splitlines():
        prefix = LOG_PREFIX.match(raw)
        level, origin = (prefix.group(1), prefix.group(2)) if prefix else ("", "")
        message = (raw[prefix.end():] if prefix else raw).strip()
        if not message or NOISE.search(message) or message[:MAX_LINE_LENGTH] in lines:
            continue
        # emulator output is logged at ERROR level line by line, so only its wording tells real errors apart
        from_emulator = "_log_emulator_output" in origin
        if (prefix and not from_emulator and level in ("ERROR", "CRITICAL")) or \
                ((from_emulator or not prefix) and (ERROR_WORDS.search(message) or re.search(r"\bWARN", message))):
            lines.append(message[:MAX_LINE_LENGTH])
    return lines[-MAX_ERROR_LINES:]


def launch_log(since):
    text = ""
    for path in LAUNCH_LOGS:
        try:
            if os.path.getmtime(path) >= since:
                with open(path, errors="replace") as fh:
                    text += fh.read()
        except OSError:
            pass
    return text


def outcome(passed, reason, lines=()):
    return {"passed": passed, "reason": reason, "lines": list(lines), "date": datetime.date.today().isoformat()}


def runs(game):
    return lambda running: running is not None and running["path"] == game["path"]


def stopped(game):
    return lambda running: running is None or running["path"] != game["path"]


async def leave_game(es, game):
    running = await es.running_game()
    if running and running["path"] == game["path"] and not await quit_game(es):
        raise CheckError("Stopped: %s didn't quit" % game["name"])
    if not running:
        await wait_es_ready()


async def boot_game(es, game, seconds):
    if not os.path.exists(game["path"]):
        return outcome(False, "the game file is missing")
    stats = await es.game(game["system"], game["id"])
    launched = time.time()
    try:
        await es.launch(game["path"])
        if not await es.wait_until(runs(game), START_WAIT_SECONDS):
            return outcome(False, "EmulationStation didn't start it", error_lines(launch_log(launched)))
        started = time.monotonic()
        if await es.wait_until(stopped(game), seconds):
            text = launch_log(launched)
            status = EXIT_STATUS.search(text)
            return outcome(False, "it closed by itself after %d s%s" % (
                time.monotonic() - started, " with status %s" % status.group(1) if status else ""), error_lines(text))
        errors = [line for line in error_lines(launch_log(launched)) if not WARNING.search(line)]
        return outcome(not errors, "the emulator logged errors" if errors else "it ran for %d s" % seconds, errors)
    finally:
        await leave_game(es, game)
        await restore_stats(es, game, stats)


async def restore_stats(es, game, before):
    after = await es.game(game["system"], game["id"])
    # ES answers 404 to an update that changes nothing
    changed = {key: before.get(key, "") for key in STAT_FIELDS if after.get(key, "") != before.get(key, "")}
    if changed:
        await es.update_game(game["system"], game["id"], changed)


class BootCheck:
    def __init__(self):
        self.results = load_results()
        self.task = None
        self.progress = None

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def result(self, path):
        return self.results.get(path)

    def forget(self, path):
        if self.results.pop(path, None) is not None:
            save_results(self.results)

    def status(self):
        return dict(self.progress or {}, running=self.running)

    def start(self, app, games, seconds, quit_first):
        if self.running:
            raise CheckError("A boot check is already running")
        if not games:
            raise CheckError("No games to check")
        self.progress = {"total": len(games), "done": 0, "passed": 0, "failed": 0,
                         "current": "quitting %s" % quit_first if quit_first else games[0]["name"],
                         "seconds": seconds, "message": "", "last": None}
        self.task = asyncio.create_task(self.run(app, games, seconds, quit_first))

    def stop(self):
        if self.running and not self.progress["message"]:
            self.progress["message"] = "Stopping..."
            self.task.cancel()

    async def shutdown(self):
        if self.running:
            self.task.cancel()
            await asyncio.wait([self.task])

    async def run(self, app, games, seconds, quit_first):
        es, progress = app["es"], self.progress
        try:
            if quit_first:
                progress["current"] = "quitting %s" % quit_first
                if not await quit_game(es):
                    raise CheckError("Not started: %s didn't quit" % quit_first)
            for game in games:
                progress["current"] = game["name"]
                running = await es.running_game()
                if running:
                    raise CheckError("Stopped: %s was started on the TV" % game_title(running))
                result = await boot_game(es, game, seconds)
                self.results[game["path"]] = result
                await asyncio.to_thread(save_results, self.results)
                progress["passed" if result["passed"] else "failed"] += 1
                progress["last"] = dict(result, name=game["name"])
                progress["done"] += 1
            progress["message"] = "Done"
        except asyncio.CancelledError:
            progress["message"] = "Stopped"
        except CheckError as error:
            progress["message"] = str(error)
        except ES_ERRORS:
            progress["message"] = "Stopped: EmulationStation isn't answering"
        finally:
            progress["current"] = ""
            await app["library"].rescan(es)
