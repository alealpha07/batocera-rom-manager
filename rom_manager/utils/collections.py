import copy
import functools
import os
import re
import time
import xml.etree.ElementTree as ET

from ..config import USERDATA_DIR
from .emulator import es_pid

ES_CONFIG_DIR = "/userdata/system/configs/emulationstation"
ES_SETTINGS_FILE = ES_CONFIG_DIR + "/es_settings.cfg"
COLLECTIONS_DIR = ES_CONFIG_DIR + "/collections"
ES_HOME_DIR = "/userdata/system"
GENRES_FILE = "/usr/share/emulationstation/resources/genres.xml"
AUTO_COLLECTIONS = (
    ("all", "All games"), ("recent", "Last played"), ("favorites", "Favorites"), ("2players", "2 players"),
    ("4players", "4 players"), ("neverplayed", "Never played"), ("retroachievements", "RetroAchievements"),
    ("arcade", "Arcade"), ("vertical", "Vertical arcade"), ("lightgun", "Lightgun games"),
    ("wheel", "Wheel games"), ("trackball", "Trackball games"), ("spinner", "Spinner games"),
)
GROUP_MODES = (("", "Only collections without their own theme"), ("true", "Always"), ("false", "Never"))
SETTING_DEFAULTS = {"CollectionSystemsAuto": "all,favorites", "CollectionSystemsCustom": "",
                    "UseCustomCollectionsSystemEx": ""}
LEGACY_GROUP_SETTING = "UseCustomCollectionsSystem"
SKIPPED_GENRE_IDS = {32, 33, 34, 35}
NAME_PATTERN = re.compile(r"[A-Za-z0-9\-\[\]() ]+")
MAX_NAME_LENGTH = 60
PLAYER_COUNTS = tuple(range(1, 10))
MANUAL, DYNAMIC = "manual", "dynamic"
MODES = {MANUAL: "Manual", DYNAMIC: "Dynamic"}
DYNAMIC_TAGS = ("system", "genre", "players")
ES_EXIT_TIMEOUT_SECONDS = 30
ES_EXIT_STEP_SECONDS = 0.05


class CollectionError(Exception):
    pass


def leading_int(text):
    match = re.match(r"\s*([+-]?\d+)", text or "")
    return int(match.group(1)) if match else 0


def split_list(value):
    return [item for item in (value or "").split(",") if item]


@functools.cache
def genres():
    try:
        root = ET.parse(GENRES_FILE).getroot()
    except (OSError, ET.ParseError):
        return {}
    found = {}
    for node in root.iter("genre"):
        genre_id = leading_int(node.findtext("id"))
        if genre_id:
            found[genre_id] = {"id": genre_id, "parent": leading_int(node.findtext("parent")),
                               "names": {child.tag: (child.text or "").strip() for child in node
                                         if child.tag.startswith("nom_") and (child.text or "").strip()},
                               "altnames": [(child.text or "").strip() for child in node.findall("altname")]}
    return found


def english_name(genre):
    return genre["names"].get("nom_en", str(genre["id"]))


def genre_label(genre_id):
    genre = genres().get(genre_id)
    if not genre:
        return str(genre_id)
    parent = genres().get(genre["parent"])
    return "%s / %s" % (english_name(parent), english_name(genre)) if parent else english_name(genre)


@functools.cache
def genre_ids_by_name():
    names = {}
    for genre in genres().values():
        parent = genres().get(genre["parent"])
        for language, name in genre["names"].items():
            if not parent:
                names[name.upper()] = genre["id"]
            elif parent["names"].get(language):
                names["%s / %s" % (parent["names"][language].upper(), name.upper())] = genre["id"]
        for name in genre["altnames"]:
            names[name.upper()] = genre["id"]
    return names


def genre_from_name(name):
    name = name.strip().upper()
    found = genre_ids_by_name().get(name)
    if found:
        return found
    return next((genre_ids_by_name()[part.strip()] for part in name.split("/")
                 if part.strip() in genre_ids_by_name()), None)


def short_genre_name(name):
    name = name.lower()
    for removed in (" ", "'", ",", "-"):
        name = name.replace(removed, "")
    return name.replace("game", "")


@functools.cache
def genre_collections():
    found = [{"key": "_" + short_genre_name(english_name(genre)), "id": genre["id"], "name": english_name(genre)}
             for genre in genres().values() if not genre["parent"] and genre["id"] not in SKIPPED_GENRE_IDS]
    return sorted(found, key=lambda genre: genre["name"].lower())


def game_genre_ids(game):
    found = set()
    for value in split_list(game["es"].get("genres")):
        genre = genres().get(leading_int(value))
        if genre:
            found.add(genre["id"])
            if genre["parent"] in genres():
                found.add(genre["parent"])
    return found


def players_range(players):
    if not players:
        return -1, -1
    key, low = players.replace("+", "-99999"), 1
    if "-" in key:
        cut = key.rfind("-")
        low, key = leading_int(key[:cut]), key[cut + 1:]
    return low, leading_int(key)


def players_match(players, wanted):
    low, high = players_range(players)
    if low <= 0 and high > 0:
        return high in wanted
    return high > 0 and any(low <= count <= high for count in wanted)


def matches(game, filters):
    return ((not filters["systems"] or game["system"] in filters["systems"])
            and (not filters["genres"] or bool(game_genre_ids(game) & filters["genres"]))
            and (not filters["players"] or players_match(game["es"].get("players"), filters["players"])))


def matching_games(games, filters):
    return sorted((g for g in games if not g["hidden"] and matches(g, filters)), key=lambda g: g["name"].lower())


def parse_settings():
    try:
        return ET.parse(ES_SETTINGS_FILE)
    except FileNotFoundError:
        return ET.ElementTree(ET.Element("config"))
    except ET.ParseError:
        raise CollectionError("Can't read %s" % ES_SETTINGS_FILE)


def read_settings():
    values = dict(SETTING_DEFAULTS)
    root = parse_settings().getroot()
    for node in root.findall("string"):
        if node.get("name") in values:
            values[node.get("name")] = node.get("value", "")
    for node in root.findall("bool"):
        if node.get("name") == LEGACY_GROUP_SETTING and node.get("value") == "false":
            values["UseCustomCollectionsSystemEx"] = "false"
    return values


def write_xml(path, root):
    ET.indent(root, "\t")
    temp_file = path + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n")
    os.replace(temp_file, path)


def write_settings(values):
    tree = parse_settings()
    root = tree.getroot()
    for node in root.findall("bool"):
        if node.get("name") == LEGACY_GROUP_SETTING:
            root.remove(node)
    for name, value in values.items():
        nodes = [node for node in root.findall("string") if node.get("name") == name]
        for node in nodes[1:]:
            root.remove(node)
        if value == SETTING_DEFAULTS[name]:
            if nodes:
                root.remove(nodes[0])
        elif nodes:
            nodes[0].set("value", value)
        else:
            ET.SubElement(root, "string", name=name, value=value)
    os.makedirs(ES_CONFIG_DIR, exist_ok=True)
    write_xml(ES_SETTINGS_FILE, root)


def update_settings(**values):
    settings = read_settings()
    settings.update(values)
    write_settings(settings)
    return settings


def collection_files():
    try:
        filenames = sorted(os.listdir(COLLECTIONS_DIR))
    except FileNotFoundError:
        return {}
    found = {}
    for filename in filenames:
        if filename.startswith("custom-") and filename.endswith(".cfg") and len(filename) > len("custom-.cfg"):
            found.setdefault(filename[len("custom-"):-len(".cfg")], MANUAL)
        elif filename.endswith(".xcc") and len(filename) > len(".xcc"):
            found[filename[:-len(".xcc")]] = DYNAMIC
    return found


def collection_path(name, kind):
    return os.path.join(COLLECTIONS_DIR, ("custom-%s.cfg" if kind == MANUAL else "%s.xcc") % name)


def list_collections(settings):
    enabled = set(split_list(settings["CollectionSystemsCustom"]))
    return [{"name": name, "kind": kind, "enabled": name in enabled}
            for name, kind in sorted(collection_files().items(), key=lambda item: item[0].lower())]


def reserved_names():
    return {key for key, _ in AUTO_COLLECTIONS} | {genre["key"] for genre in genre_collections()} | {"collections"}


def checked_name(name, renaming=None):
    name = " ".join((name or "").split())
    if not name:
        raise CollectionError("Give the collection a name")
    if len(name) > MAX_NAME_LENGTH:
        raise CollectionError("Use at most %d characters" % MAX_NAME_LENGTH)
    if not NAME_PATTERN.fullmatch(name):
        raise CollectionError("Use only letters, digits, spaces and - [ ] ( )")
    if name.lower() in reserved_names():
        raise CollectionError("EmulationStation already uses the name %s" % name)
    if any(other.lower() == name.lower() and other != renaming for other in collection_files()):
        raise CollectionError("A collection named %s already exists" % name)
    return name


def resolve_entry(line):
    if line.startswith("./"):
        return os.path.normpath(os.path.join(USERDATA_DIR, line[2:]))
    if line.startswith("~/"):
        return os.path.normpath(os.path.join(ES_HOME_DIR, line[2:]))
    return os.path.normpath(line)


def entry_line(path):
    return "./" + os.path.relpath(path, USERDATA_DIR) if path.startswith(USERDATA_DIR + "/") else path


def read_manual(name):
    try:
        with open(collection_path(name, MANUAL), encoding="utf-8", errors="replace") as fh:
            lines = [line.strip() for line in fh]
    except FileNotFoundError:
        raise CollectionError("The collection %s doesn't exist" % name)
    return list(dict.fromkeys(resolve_entry(line) for line in lines if line and line[0] not in "0#"))


def write_manual(name, paths):
    temp_file = collection_path(name, MANUAL) + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as fh:
        fh.writelines(entry_line(path) + "\n" for path in paths)
    os.replace(temp_file, collection_path(name, MANUAL))


def parse_dynamic(name):
    try:
        root = ET.parse(collection_path(name, DYNAMIC)).getroot()
    except FileNotFoundError:
        raise CollectionError("The collection %s doesn't exist" % name)
    except ET.ParseError:
        raise CollectionError("Can't read the filters of %s" % name)
    if root.tag != "filter":
        raise CollectionError("Can't read the filters of %s" % name)
    return root


def read_dynamic(name):
    filters, others = {"systems": set(), "genres": set(), "players": set()}, []
    for node in parse_dynamic(name):
        text = (node.text or "").strip()
        if node.tag == "system" and text:
            filters["systems"].add(text)
        elif node.tag == "genre" and genre_from_name(text):
            filters["genres"].add(genre_from_name(text))
        elif node.tag == "players" and leading_int(text) > 0:
            filters["players"].add(leading_int(text))
        elif node.tag not in DYNAMIC_TAGS:
            others.append(node)
    return filters, others


def write_dynamic(name, filters, others=()):
    root = ET.Element("filter", name=name)
    for system in sorted(filters["systems"]):
        ET.SubElement(root, "system").text = system
    for genre_id in sorted(filters["genres"]):
        ET.SubElement(root, "genre").text = genre_label(genre_id).upper()
    for count in sorted(filters["players"]):
        ET.SubElement(root, "players").text = str(count)
    for node in others:
        node = copy.deepcopy(node)
        node.tail = None
        root.append(node)
    write_xml(collection_path(name, DYNAMIC), root)


def collection_kind(name):
    kind = collection_files().get(name)
    if not kind:
        raise CollectionError("The collection %s doesn't exist" % name)
    return kind


def create_collection(name, kind):
    name = checked_name(name)
    os.makedirs(COLLECTIONS_DIR, exist_ok=True)
    if kind == MANUAL:
        write_manual(name, [])
    else:
        write_dynamic(name, {"systems": set(), "genres": set(), "players": set()})
    enabled = split_list(read_settings()["CollectionSystemsCustom"])
    return name, update_settings(CollectionSystemsCustom=",".join(enabled + [name]))


def rename_collection(name, new_name):
    kind = collection_kind(name)
    new_name = checked_name(new_name, renaming=name)
    if new_name == name:
        return name, None
    if kind == MANUAL:
        os.rename(collection_path(name, MANUAL), collection_path(new_name, MANUAL))
    else:
        filters, others = read_dynamic(name)
        write_dynamic(new_name, filters, others)
        os.remove(collection_path(name, DYNAMIC))
    enabled = [new_name if item == name else item for item in split_list(read_settings()["CollectionSystemsCustom"])]
    return new_name, update_settings(CollectionSystemsCustom=",".join(enabled))


def delete_collection(name):
    collection_kind(name)
    for kind in (MANUAL, DYNAMIC):
        try:
            os.remove(collection_path(name, kind))
        except FileNotFoundError:
            pass
    enabled = [item for item in split_list(read_settings()["CollectionSystemsCustom"]) if item != name]
    return update_settings(CollectionSystemsCustom=",".join(enabled))


def manual_collections_with(path):
    found = []
    for name, kind in sorted(collection_files().items(), key=lambda item: item[0].lower()):
        if kind == MANUAL:
            try:
                found.append({"name": name, "member": path in read_manual(name)})
            except CollectionError:
                continue
    return found


def set_membership(path, wanted):
    changed = False
    for collection in manual_collections_with(path):
        if collection["member"] != (collection["name"] in wanted):
            paths = read_manual(collection["name"])
            write_manual(collection["name"], paths + [path] if collection["name"] in wanted
                         else [p for p in paths if p != path])
            changed = True
    return changed


class PendingRestart:
    def __init__(self):
        self.es_pid = None
        self.settings = None

    def remember(self, settings=None):
        self.settings = settings or self.settings or read_settings()
        self.es_pid = es_pid()

    def waiting(self):
        if self.es_pid is not None and self.es_pid != es_pid():
            self.es_pid = self.settings = None
        return self.es_pid is not None

    def reapply_after_exit(self, old_pid):
        if self.settings is None or old_pid is None:
            return
        deadline = time.monotonic() + ES_EXIT_TIMEOUT_SECONDS
        while os.path.exists("/proc/%d" % old_pid) and time.monotonic() < deadline:
            time.sleep(ES_EXIT_STEP_SECONDS)
        if read_settings() != self.settings:
            write_settings(self.settings)
        self.es_pid = self.settings = None
