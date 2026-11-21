import os
import re
import shutil

from ..config import ROMS_DIR
from .es import all_systems

CHUNK_SIZE = 1 << 20
PART_SUFFIX = ".part"
MULTI_FILE = (".cue", ".gdi", ".m3u", ".ccd")


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
