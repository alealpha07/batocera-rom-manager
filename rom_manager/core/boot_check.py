import asyncio
import datetime
import json
import os
import re
import time

from ..config import HEALTH_FILE
from .emulationstation import ES_ERRORS

LAUNCH_LOGS = ("/userdata/system/logs/es_launch_stdout.log", "/userdata/system/logs/es_launch_stderr.log")
DEFAULT_SECONDS, MIN_SECONDS, MAX_SECONDS = 20, 5, 120
START_WAIT_SECONDS = 15
STAT_FIELDS = ("playcount", "lastplayed", "gametime")
MAX_ERROR_LINES = 6
MAX_LINE_LENGTH = 220
UNCHECKED_SYSTEMS = {"steam", "flatpak", "moonlight", "kodi", "windows_installers", "ports"}
APP_EXTENSIONS = (".xci_config",)
LOG_PREFIX = re.compile(r"^\[[^\]]*\] \[(\w+)\s*\] \(([^)]*)\): (?:\[(?:stderr|stdout)\] )?")
ERROR_WORDS = re.compile(r"\b(error|fatal|fail(ed|ure)?|could ?n[o']t|cannot|can't|unable|missing|not found|"
                         r"no such|invalid|unsupported|corrupt(ed)?|segmentation|abort(ed)?|exception|panic|"
                         r"crash(ed)?|not an?)\b", re.I)
NOISE = re.compile(r"\[(libretro )?(INFO|DEBUG)\]|GET_VARIABLE|Not implemented|batocera-launch exception|"
                   r"Failed to connect to Wayland|Qt depends on a UTF-8 locale|QPainter::|^qt\.[\w.]+:|QDBusError|"
                   r"org\.freedesktop\.|FindFileInDir: not found|which is already loaded|display_rom_load_results|"
                   r"^[\d-]+ [\d:.]+ (INFO|DEBUG)\s", re.I)
WARNING = re.compile(r"\bWARN(ING)?\b")
EXIT_STATUS = re.compile(r"Exiting batocera-launch with status (\d+)")


class CheckError(Exception):
    pass


def load_results():
    try:
        with open(HEALTH_FILE) as results_file:
            results = json.load(results_file)
    except (OSError, ValueError):
        return {}
    return results if isinstance(results, dict) else {}


def save_results(results):
    os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
    temp_file = HEALTH_FILE + ".tmp"
    with open(temp_file, "w") as results_file:
        json.dump(results, results_file, indent=1)
    os.replace(temp_file, HEALTH_FILE)


def parse_seconds(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise CheckError("Seconds per game must be a number")
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise CheckError(f"Seconds per game go from {MIN_SECONDS} to {MAX_SECONDS}")
    return seconds


def is_checkable(game):
    return not (game.hidden or game.system in UNCHECKED_SYSTEMS or game.path.lower().endswith(APP_EXTENSIONS))


def checkable(games):
    return [game for game in games if is_checkable(game)]


def error_lines(log_text):
    lines = []
    for raw_line in log_text.splitlines():
        prefix = LOG_PREFIX.match(raw_line)
        level, origin = (prefix.group(1), prefix.group(2)) if prefix else ("", "")
        message = (raw_line[prefix.end():] if prefix else raw_line).strip()
        if not message or NOISE.search(message) or message[:MAX_LINE_LENGTH] in lines:
            continue
        # emulator output is logged at ERROR level line by line, so only its wording tells real errors apart
        from_emulator = "_log_emulator_output" in origin
        if (prefix and not from_emulator and level in ("ERROR", "CRITICAL")) or \
                ((from_emulator or not prefix) and (ERROR_WORDS.search(message) or re.search(r"\bWARN", message))):
            lines.append(message[:MAX_LINE_LENGTH])
    return lines[-MAX_ERROR_LINES:]


def launch_log(since):
    log_text = ""
    for path in LAUNCH_LOGS:
        try:
            if os.path.getmtime(path) >= since:
                with open(path, errors="replace") as log_file:
                    log_text += log_file.read() + "\n"
        except OSError:
            pass
    return log_text


def check_result(passed, reason, lines=()):
    return {"passed": passed, "reason": reason, "lines": list(lines), "date": datetime.date.today().isoformat()}


class BootCheck:
    def __init__(self, es, library):
        self.es, self.library = es, library
        self.results = load_results()
        self.task = None
        self.progress = None

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def result(self, path):
        return self.results.get(path)

    def passed(self, game):
        return (self.results.get(game.path) or {}).get("passed", False)

    def failed(self, game):
        result = self.results.get(game.path)
        return result is not None and not result["passed"]

    def forget(self, path):
        if self.results.pop(path, None) is not None:
            save_results(self.results)

    def status(self):
        return dict(self.progress or {}, running=self.running)

    def start(self, games, seconds, quit_first):
        if self.running:
            raise CheckError("A boot check is already running")
        if not games:
            raise CheckError("No games to check")
        self.progress = {"total": len(games), "done": 0, "passed": 0, "failed": 0,
                         "current": f"quitting {quit_first}" if quit_first else games[0].name,
                         "seconds": seconds, "message": "", "last": None}
        self.task = asyncio.create_task(self.run(games, seconds, quit_first))

    def stop(self):
        if self.running and not self.progress["message"]:
            self.progress["message"] = "Stopping..."
            self.task.cancel()

    async def shutdown(self):
        if self.running:
            self.task.cancel()
            await asyncio.wait([self.task])

    async def run(self, games, seconds, quit_first):
        progress = self.progress
        try:
            if quit_first:
                progress["current"] = f"quitting {quit_first}"
                if not await self.es.quit_game():
                    raise CheckError(f"Not started: {quit_first} didn't quit")
            for game in games:
                progress["current"] = game.name
                running = await self.es.running_game()
                if running:
                    raise CheckError(f"Stopped: {running.name} was started on the TV")
                result = await self.boot(game, seconds)
                self.results[game.path] = result
                await asyncio.to_thread(save_results, self.results)
                progress["passed" if result["passed"] else "failed"] += 1
                progress["last"] = dict(result, name=game.name)
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
            await self.library.rescan()

    async def boot(self, game, seconds):
        if not os.path.exists(game.path):
            return check_result(False, "the game file is missing")
        stats_before = await self.es.game(game.system, game.id)
        launched = time.time()
        try:
            await self.es.launch(game.path)
            if not await self.es.wait_until_running(game.path, START_WAIT_SECONDS):
                return check_result(False, "EmulationStation didn't start it", error_lines(launch_log(launched)))
            started = time.monotonic()
            if await self.es.wait_until_stopped(game.path, seconds):
                log_text = launch_log(launched)
                status = EXIT_STATUS.search(log_text)
                reason = f"it closed by itself after {int(time.monotonic() - started)} s"
                if status:
                    reason += f" with status {status.group(1)}"
                return check_result(False, reason, error_lines(log_text))
            errors = [line for line in error_lines(launch_log(launched)) if not WARNING.search(line)]
            return check_result(not errors, "the emulator logged errors" if errors else f"it ran for {seconds} s",
                                errors)
        finally:
            await self.leave(game)
            await self.restore_stats(game, stats_before)

    async def leave(self, game):
        running = await self.es.running_game()
        if running and running.path == game.path and not await self.es.quit_game():
            raise CheckError(f"Stopped: {game.name} didn't quit")
        if not running:
            await self.es.wait_until_ready()

    async def restore_stats(self, game, stats_before):
        stats_after = await self.es.game(game.system, game.id)
        # ES answers 404 to an update that changes nothing
        changed = {key: stats_before.get(key, "") for key in STAT_FIELDS
                   if stats_after.get(key, "") != stats_before.get(key, "")}
        if changed:
            await self.es.update_game(game.system, game.id, changed)
