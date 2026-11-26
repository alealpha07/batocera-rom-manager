import asyncio
import contextlib
import os
import re
import signal
import subprocess

from ..config import HOTKEYGEN

LAUNCHERS = {"batocera-launch", "original-launcher"}
EXIT_WAIT_SECONDS = 5
HOTKEYGEN_TIMEOUT_SECONDS = 5
HOTKEY_CONTEXT = re.compile(r"Context \[(.*)\]")
ES_HOTKEY_CONTEXT = "emulationstation"
CONTEXT_STEP_SECONDS = 0.25
ES_SETTLE_SECONDS = 1


def game_title(game):
    return game.get("name") or os.path.basename(game.get("path", ""))


def processes():
    found = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, "rb") as fh:
                parent = int(fh.read().rsplit(b")", 1)[1].split()[1])
            with open("/proc/%s/cmdline" % name, "rb") as fh:
                args = fh.read().split(b"\0")[:2]
        except (OSError, ValueError, IndexError):
            continue
        found[int(name)] = (parent, [os.path.basename(arg.decode(errors="replace")) for arg in args])
    return found


def es_pid():
    return next((pid for pid, (_, args) in processes().items() if args and args[0] == "emulationstation"), None)


def kill_launchers():
    found = processes()
    children = {}
    for pid, (parent, _) in found.items():
        children.setdefault(parent, []).append(pid)
    for launcher in [pid for pid, (_, args) in found.items() if LAUNCHERS.intersection(args)]:
        tree = [launcher]
        for pid in tree:
            tree.extend(children.get(pid, []))
        for pid in reversed(tree):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


async def run_hotkeygen(*args):
    try:
        process = await asyncio.create_subprocess_exec(HOTKEYGEN, *args, stdout=subprocess.PIPE,
                                                       stderr=subprocess.DEVNULL)
    except OSError:
        return ""
    try:
        output, _ = await asyncio.wait_for(process.communicate(), HOTKEYGEN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return ""
    return output.decode(errors="replace")


async def wait_hotkey_context(test, seconds):
    deadline = asyncio.get_running_loop().time() + seconds
    while True:
        context = HOTKEY_CONTEXT.match(await run_hotkeygen("--list"))
        if context and test(context.group(1)):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(CONTEXT_STEP_SECONDS)


def is_idle(game):
    return game is None


async def stop_game(es):
    # while a game loads the exit hotkey still belongs to ES, where it acts as "back"
    if await wait_hotkey_context(lambda name: name != ES_HOTKEY_CONTEXT, EXIT_WAIT_SECONDS):
        await run_hotkeygen("--send", "exit")
        if await es.wait_until(is_idle, EXIT_WAIT_SECONDS):
            return True
    await asyncio.to_thread(kill_launchers)
    await run_hotkeygen("--default-context")
    return await es.wait_until(is_idle, EXIT_WAIT_SECONDS)


async def wait_es_ready():
    # ES queues or drops a launch that comes while it is still returning from the game
    await wait_hotkey_context(lambda name: name == ES_HOTKEY_CONTEXT, EXIT_WAIT_SECONDS)
    await asyncio.sleep(ES_SETTLE_SECONDS)


async def quit_game(es):
    if not await stop_game(es):
        return False
    await wait_es_ready()
    return True
