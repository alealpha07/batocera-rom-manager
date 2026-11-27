import contextlib
import os
import re
import shutil
import xml.etree.ElementTree as ET

from ..config import ROMS_DIR
from .emulationstation import known_systems, relative_rom_path

MULTI_FILE_EXTENSIONS = (".cue", ".gdi", ".m3u", ".ccd")
MEDIA_KINDS = ("thumbnail", "image", "titleshot", "marquee", "wheel", "video")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".avi": "video/avi"}
MAX_LISTED_FILES = 200
GAME_ENTRY = re.compile(r"[ \t]*<game\b.*?</game>[ \t]*\r?\n?", re.S)


class GameFilesError(Exception):
    pass


def file_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def human_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def is_inside(path, folder):
    return os.path.abspath(path).startswith(os.path.abspath(folder).rstrip("/") + "/")


def media_content_type(kind, filename):
    types = VIDEO_TYPES if kind == "video" else IMAGE_TYPES
    content_type = types.get(os.path.splitext(filename or "")[1].lower())
    if not content_type:
        allowed = ", ".join(sorted({extension.lstrip(".") for extension in types}))
        raise GameFilesError(f"Allowed files: {allowed}")
    return content_type


def referenced_files(path):
    folder, lower = os.path.dirname(path), path.lower()
    try:
        with open(path, errors="ignore") as listing:
            lines = [line.strip() for line in listing]
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


def files_of_game(path):
    if os.path.isdir(path):
        return [os.path.join(folder, name) for folder, _, names in os.walk(path) for name in names]
    files, pending = [os.path.normpath(path)], [path]
    while pending:
        current = pending.pop()
        if current.lower().endswith(MULTI_FILE_EXTENSIONS):
            for referenced in referenced_files(current):
                if referenced not in files:
                    files.append(referenced)
                    pending.append(referenced)
    return files


class Gamelist:
    def __init__(self, folder):
        self.folder = folder
        self.path = os.path.join(folder, "gamelist.xml")

    def resolve(self, value):
        value = (value or "").strip()
        return os.path.normpath(os.path.join(self.folder, os.path.expanduser(value))) if value else ""

    @staticmethod
    def is_file_reference(value):
        return (value or "").strip().startswith(("./", "/", "~/"))

    def read(self):
        try:
            with open(self.path, encoding="utf-8", newline="") as gamelist:
                text = gamelist.read()
        except FileNotFoundError:
            return "", []
        except UnicodeDecodeError:
            raise OSError(f"Can't read {self.path}")
        entries = []
        for match in GAME_ENTRY.finditer(text):
            try:
                entries.append((match, ET.fromstring(match.group().strip())))
            except ET.ParseError:
                continue
        return text, entries

    def write(self, text):
        temp_file = self.path + ".tmp"
        with open(temp_file, "w", encoding="utf-8", newline="") as gamelist:
            gamelist.write(text)
        os.replace(temp_file, self.path)

    def is_entry_for(self, element, path):
        return self.resolve(element.findtext("path")) == os.path.normpath(path)

    def referenced_files(self, element):
        return {self.resolve(child.text) for child in element
                if child.tag != "path" and self.is_file_reference(child.text)}

    def all_referenced_files(self):
        return {file for _, element in self.read()[1] for file in self.referenced_files(element)}

    def files_only_used_by(self, path):
        own, others = set(), set()
        for _, element in self.read()[1]:
            (own if self.is_entry_for(element, path) else others).update(self.referenced_files(element))
        return sorted(file for file in own - others if os.path.isfile(file))

    def media_of(self, path):
        for _, element in self.read()[1]:
            if self.is_entry_for(element, path):
                return {child.tag: self.resolve(child.text) for child in element
                        if child.tag != "path" and self.is_file_reference(child.text)}
        return {}

    def remove_media_tag(self, path, kind):
        text, entries = self.read()
        tag = re.compile(rf"[ \t]*<{kind}>.*?</{kind}>[ \t]*\r?\n?", re.S)
        blocks = [match for match, element in entries if self.is_entry_for(element, path)]
        for match in reversed(blocks):
            text = text[:match.start()] + tag.sub("", match.group()) + text[match.end():]
        if blocks:
            self.write(text)

    def remove_entry(self, path):
        text, entries = self.read()
        spans = [match.span() for match, element in entries if self.is_entry_for(element, path)]
        for start, end in reversed(spans):
            text = text[:start] + text[end:]
        if spans:
            self.write(text)


class GameFiles:
    def __init__(self, game):
        self.game = game
        system = known_systems().get(game.system)
        self.system_folder = system.folder if system else os.path.dirname(game.path)
        self.gamelist = Gamelist(self.system_folder)

    def managed_path(self):
        path = os.path.normpath(self.game.path)
        system_folders = {os.path.normpath(system.folder) for system in known_systems().values()}
        if not is_inside(path, ROMS_DIR) or path in system_folders:
            raise GameFilesError(f"{path} is outside the ROM folders")
        return path

    def listing(self, files=None):
        files = files_of_game(self.game.path) if files is None else files
        listed = [(relative_rom_path(file, self.game.system), file_size(file)) for file in files[:MAX_LISTED_FILES]]
        return listed, len(files) - len(listed)

    def delete_plan(self):
        path = self.managed_path()
        files = [file for file in files_of_game(path) if os.path.lexists(file)]
        files += [file for file in self.gamelist.files_only_used_by(path) if file not in files]
        return [file for file in files if is_inside(file, ROMS_DIR)]

    def deletable_listing(self):
        try:
            return self.listing(self.delete_plan())
        except (GameFilesError, OSError):
            return None, 0

    def delete(self, plan):
        path = self.managed_path()
        if os.path.isdir(path):
            shutil.rmtree(path)
        for file in plan:
            with contextlib.suppress(FileNotFoundError):
                os.remove(file)
        self.gamelist.remove_entry(path)
        parent = os.path.dirname(path)
        if is_inside(parent, self.system_folder) and not os.listdir(parent):
            os.rmdir(parent)

    def media_files(self):
        return self.gamelist.media_of(self.managed_path())

    def media_versions(self):
        try:
            files = self.media_files()
        except (GameFilesError, OSError):
            return {}
        return {kind: int(os.path.getmtime(file)) for kind, file in files.items() if os.path.isfile(file)}

    def delete_unused_media(self, file):
        if file and os.path.isfile(file) and is_inside(file, ROMS_DIR) \
                and file not in self.gamelist.all_referenced_files():
            os.remove(file)

    def remove_media(self, kind):
        file = self.media_files().get(kind)
        self.gamelist.remove_media_tag(self.managed_path(), kind)
        self.delete_unused_media(file)
