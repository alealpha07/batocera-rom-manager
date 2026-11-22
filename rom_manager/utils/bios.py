import contextlib
import functools
import importlib.machinery
import importlib.util
import io
import os

from ..config import BIOS_CHECKER, BIOS_DIR
from .es import all_systems
from .roms import PART_SUFFIX, RomError, clean_name, file_size, is_inside

USERDATA_DIR = os.path.dirname(BIOS_DIR)
STATUS_LABELS = {"MISSING": "missing", "UNTESTED": "wrong md5"}


@functools.cache
def checker():
    loader = importlib.machinery.SourceFileLoader("batocera_systems", BIOS_CHECKER)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def check(systems):
    # the checker prints zip mismatches to stdout
    with contextlib.redirect_stdout(io.StringIO()):
        return checker().checkBios(systems, USERDATA_DIR, False)


def md5_text(entry):
    if "zippedFile" in entry:
        return ""
    md5 = entry.get("md5") or ""
    return " or ".join(md5) if isinstance(md5, list) else md5


def bios_report():
    systems = checker().systems
    problems = check(systems)
    report = []
    for name, info in systems.items():
        files = {}
        for entry in info["biosFiles"]:
            path = os.path.join(USERDATA_DIR, entry["file"])
            problem = problems.get(name, {}).get(entry["file"])
            files.setdefault(entry["file"], {
                "file": entry["file"], "md5": md5_text(entry), "uploadable": is_inside(path, BIOS_DIR),
                "status": STATUS_LABELS.get(problem["status"], "missing") if problem else "ok"})
        files = list(files.values())
        report.append({"name": name, "fullname": all_systems().get(name, {}).get("fullname") or info["name"],
                       "files": files, "problems": sum(1 for f in files if f["status"] != "ok")})
    return sorted(report, key=lambda s: s["fullname"].lower())


def entry_path(system, file):
    info = checker().systems.get(system)
    if not info or not any(entry["file"] == file for entry in info["biosFiles"]):
        raise RomError("Unknown BIOS file %s" % file)
    path = os.path.join(USERDATA_DIR, file)
    if not is_inside(path, BIOS_DIR):
        raise RomError("%s is outside the BIOS folder" % file)
    return path


def entry_problem(system, file):
    problem = check({system: checker().systems[system]}).get(system, {}).get(file)
    if not problem:
        return None
    return "the md5 doesn't match" if problem["status"] == "UNTESTED" else "the file is still missing"


def bios_folder(relative, subfolder=""):
    folder = os.path.normpath(os.path.join(BIOS_DIR, relative or ""))
    if folder != BIOS_DIR and not is_inside(folder, BIOS_DIR):
        raise RomError("Invalid path: %s" % relative)
    return os.path.join(folder, clean_name(subfolder, "subfolder")) if subfolder.strip() else folder


def relative_bios_path(path):
    relative = os.path.relpath(path, BIOS_DIR)
    return "" if relative == "." else relative


def folder_listing(folder):
    if not os.path.isdir(folder):
        raise RomError("No such folder: %s" % relative_bios_path(folder))
    folders, files = [], []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_dir():
                folders.append(entry.name)
            elif not entry.name.endswith(PART_SUFFIX):
                files.append((entry.name, file_size(entry.path)))
    return sorted(folders, key=str.lower), sorted(files, key=lambda f: f[0].lower())


def delete_bios_file(relative):
    path = bios_folder(relative)
    if not os.path.isfile(path):
        raise RomError("%s isn't a file" % relative)
    os.remove(path)
