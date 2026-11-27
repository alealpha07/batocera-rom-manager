import contextlib
import functools
import importlib.machinery
import importlib.util
import io
import os

from ..config import BIOS_CHECKER, BIOS_DIR, USERDATA_DIR
from .emulationstation import system_fullname
from .game_files import file_size, is_inside
from .transfers import PART_SUFFIX, TransferError, clean_name

STATUS_LABELS = {"MISSING": "missing", "UNTESTED": "wrong md5"}


class BiosError(TransferError):
    pass


@functools.cache
def batocera_checker():
    loader = importlib.machinery.SourceFileLoader("batocera_systems", BIOS_CHECKER)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def bios_problems(systems):
    # the checker prints zip mismatches to stdout
    with contextlib.redirect_stdout(io.StringIO()):
        return batocera_checker().checkBios(systems, USERDATA_DIR, False)


def md5_text(entry):
    if "zippedFile" in entry:
        return ""
    md5 = entry.get("md5") or ""
    return " or ".join(md5) if isinstance(md5, list) else md5


def bios_report():
    systems = batocera_checker().systems
    problems = bios_problems(systems)
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
        report.append({"name": name, "fullname": system_fullname(name, info["name"]),
                       "files": files, "problems": sum(1 for file in files if file["status"] != "ok")})
    return sorted(report, key=lambda system: system["fullname"].lower())


def entry_path(system, file):
    info = batocera_checker().systems.get(system)
    if not info or not any(entry["file"] == file for entry in info["biosFiles"]):
        raise BiosError(f"Unknown BIOS file {file}")
    path = os.path.join(USERDATA_DIR, file)
    if not is_inside(path, BIOS_DIR):
        raise BiosError(f"{file} is outside the BIOS folder")
    return path


def entry_problem(system, file):
    problem = bios_problems({system: batocera_checker().systems[system]}).get(system, {}).get(file)
    if not problem:
        return None
    return "the md5 doesn't match" if problem["status"] == "UNTESTED" else "the file is still missing"


def bios_folder(relative, subfolder=""):
    folder = os.path.normpath(os.path.join(BIOS_DIR, relative or ""))
    if folder != BIOS_DIR and not is_inside(folder, BIOS_DIR):
        raise BiosError(f"Invalid path: {relative}")
    return os.path.join(folder, clean_name(subfolder, "subfolder")) if subfolder.strip() else folder


def relative_bios_path(path):
    relative = os.path.relpath(path, BIOS_DIR)
    return "" if relative == "." else relative


def bios_label(folder):
    return os.path.join("bios", relative_bios_path(folder)).rstrip("/")


def folder_listing(folder):
    if not os.path.isdir(folder):
        raise BiosError(f"No such folder: {relative_bios_path(folder)}")
    folders, files = [], []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_dir():
                folders.append(entry.name)
            elif not entry.name.endswith(PART_SUFFIX):
                files.append((entry.name, file_size(entry.path)))
    return sorted(folders, key=str.lower), sorted(files, key=lambda file: file[0].lower())


def delete_bios_file(relative):
    path = bios_folder(relative)
    if not os.path.isfile(path):
        raise BiosError(f"{relative} isn't a file")
    os.remove(path)
