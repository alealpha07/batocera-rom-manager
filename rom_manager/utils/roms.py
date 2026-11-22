import contextlib
import os
import re
import shutil

from ..config import ROMS_DIR
from . import gamelist
from .es import all_systems

CHUNK_SIZE = 1 << 20
PART_SUFFIX = ".part"
MULTI_FILE = (".cue", ".gdi", ".m3u", ".ccd")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".avi": "video/avi"}


class RomError(Exception):
    pass


def clean_name(name, label="file name"):
    name = (name or "").strip()
    if not name or name in (".", "..") or any(char in name for char in "/\\\0") or name.endswith(PART_SUFFIX):
        raise RomError("Invalid %s: %r" % (label, name))
    return name


def target_dir(system, subfolder=""):
    info = all_systems().get(system)
    if not info:
        raise RomError("Unknown system: %r" % system)
    folder = info["path"]
    return os.path.join(folder, clean_name(subfolder, "subfolder")) if subfolder.strip() else folder


def destination(folder, name, overwrite):
    path = os.path.join(folder, name)
    if os.path.isdir(path):
        raise RomError("%s is a folder" % name)
    if os.path.lexists(path) and not overwrite:
        raise RomError("%s already exists" % name)
    return path


def check_space(folder, size):
    while not os.path.isdir(folder) and folder != os.path.dirname(folder):
        folder = os.path.dirname(folder)
    if size and size > shutil.disk_usage(folder or ROMS_DIR).free:
        raise RomError("Not enough free space for %s" % human_size(size))


def extension_warning(system, name):
    info = all_systems()[system]
    if info["extensions"] and not name.lower().endswith(tuple(info["extensions"])):
        return "ES doesn't list this extension for %s" % info["fullname"]
    return None


async def save_stream(chunks, path, progress=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part_path = path + PART_SUFFIX
    try:
        with open(part_path, "wb") as fh:
            async for chunk in chunks:
                fh.write(chunk)
                if progress:
                    progress(len(chunk))
        os.replace(part_path, path)
    except BaseException:
        try:
            os.remove(part_path)
        except OSError:
            pass
        raise


def display_path(path, system):
    for folder in (all_systems().get(system, {}).get("path"), ROMS_DIR):
        if folder and path.startswith(folder.rstrip("/") + "/"):
            return os.path.relpath(path, folder)
    return path


def referenced_files(path):
    folder, lower = os.path.dirname(path), path.lower()
    try:
        with open(path, errors="ignore") as fh:
            lines = [line.strip() for line in fh]
    except OSError:
        lines = []
    targets = []
    for line in lines:
        if lower.endswith(".m3u"):
            if line and not line.startswith("#"):
                targets.append(line)
        elif lower.endswith(".cue"):
            file_line = re.match(r'FILE\s+"?(.+?)"?\s+\S+$', line, re.I)
            if file_line:
                targets.append(file_line.group(1))
        elif lower.endswith(".gdi"):
            parts = re.findall(r'"[^"]+"|\S+', line)
            if len(parts) >= 5:
                targets.append(parts[4].strip('"'))
    if lower.endswith(".ccd"):
        base = os.path.splitext(os.path.basename(path))[0]
        targets += [base + ".img", base + ".sub"]
    return [os.path.normpath(os.path.join(folder, target)) for target in targets]


def game_files(path):
    if os.path.isdir(path):
        return [os.path.join(folder, name) for folder, _, names in os.walk(path) for name in names]
    files, pending = [os.path.normpath(path)], [path]
    while pending:
        current = pending.pop()
        if current.lower().endswith(MULTI_FILE):
            for ref in referenced_files(current):
                if ref not in files:
                    files.append(ref)
                    pending.append(ref)
    return files


def system_folder(game):
    return all_systems().get(game["system"], {}).get("path") or os.path.dirname(game["path"])


def is_inside(path, folder):
    return os.path.abspath(path).startswith(os.path.abspath(folder).rstrip("/") + "/")


def managed_path(game):
    path = os.path.normpath(game["path"])
    if not is_inside(path, ROMS_DIR) or path in {os.path.normpath(s["path"]) for s in all_systems().values()}:
        raise RomError("%s is outside the ROM folders" % path)
    return path


def remove_empty_parent(path, folder):
    parent = os.path.dirname(path)
    if is_inside(parent, folder) and not os.listdir(parent):
        os.rmdir(parent)


def delete_plan(game):
    path = managed_path(game)
    files = [f for f in game_files(path) if os.path.lexists(f)]
    files += [f for f in gamelist.media_files(system_folder(game), path) if f not in files]
    return [f for f in files if is_inside(f, ROMS_DIR)]


def delete_game(game, plan):
    path, folder = managed_path(game), system_folder(game)
    if os.path.isdir(path):
        shutil.rmtree(path)
    for file in plan:
        with contextlib.suppress(FileNotFoundError):
            os.remove(file)
    gamelist.remove_entry(folder, path)
    remove_empty_parent(path, folder)


def media_content_type(kind, filename):
    types = VIDEO_TYPES if kind == "video" else IMAGE_TYPES
    content_type = types.get(os.path.splitext(filename or "")[1].lower())
    if not content_type:
        raise RomError("Allowed files: %s" % ", ".join(sorted({ext.lstrip(".") for ext in types})))
    return content_type


def game_media_files(game):
    return gamelist.entry_media(system_folder(game), managed_path(game))


def delete_unused_media(game, file):
    if file and os.path.isfile(file) and is_inside(file, ROMS_DIR) \
            and file not in gamelist.all_entry_files(system_folder(game)):
        os.remove(file)


def remove_game_media(game, kind):
    file = game_media_files(game).get(kind)
    gamelist.remove_media_tag(system_folder(game), managed_path(game), kind)
    delete_unused_media(game, file)


def media_versions(game):
    try:
        files = game_media_files(game)
    except (OSError, RomError):
        return {}
    return {kind: int(os.path.getmtime(file)) for kind, file in files.items() if os.path.isfile(file)}


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def human_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return "%d %s" % (size, unit) if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1024
    return "%.1f TB" % size
