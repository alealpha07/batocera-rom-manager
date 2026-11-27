import copy
import functools
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from ..config import USERDATA_DIR
from .emulationstation import es_process_id

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
GROUPING_MODES = (("", "Only collections without their own theme"), ("true", "Always"), ("false", "Never"))
AUTO_SETTING, CUSTOM_SETTING, GROUPING_SETTING = ("CollectionSystemsAuto", "CollectionSystemsCustom",
                                                  "UseCustomCollectionsSystemEx")
SETTING_DEFAULTS = {AUTO_SETTING: "all,favorites", CUSTOM_SETTING: "", GROUPING_SETTING: ""}
LEGACY_GROUPING_SETTING = "UseCustomCollectionsSystem"
SKIPPED_GENRE_IDS = {32, 33, 34, 35}
NAME_PATTERN = re.compile(r"[A-Za-z0-9\-\[\]() ]+")
MAX_NAME_LENGTH = 60
PLAYER_COUNTS = tuple(range(1, 10))
MANUAL, DYNAMIC = "manual", "dynamic"
KINDS = {MANUAL: "Manual", DYNAMIC: "Dynamic"}
FILTER_TAGS = ("system", "genre", "players")
ES_EXIT_TIMEOUT_SECONDS = 30
ES_EXIT_POLL_SECONDS = 0.05


class CollectionError(Exception):
    pass


def leading_int(text):
    match = re.match(r"\s*([+-]?\d+)", text or "")
    return int(match.group(1)) if match else 0


def split_list(value):
    return [item for item in (value or "").split(",") if item]


def write_xml(path, root):
    ET.indent(root, "\t")
    temp_file = path + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as xml_file:
        xml_file.write('<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n")
    os.replace(temp_file, path)


@dataclass(frozen=True)
class Genre:
    id: int
    parent: int
    names: dict
    altnames: tuple

    @property
    def english_name(self):
        return self.names.get("nom_en", str(self.id))

    @property
    def collection_key(self):
        name = self.english_name.lower()
        for removed in (" ", "'", ",", "-"):
            name = name.replace(removed, "")
        return "_" + name.replace("game", "")


class Genres:
    def __init__(self, genres):
        self.by_id = genres

    @classmethod
    def from_file(cls):
        try:
            root = ET.parse(GENRES_FILE).getroot()
        except (OSError, ET.ParseError):
            return cls({})
        genres = {}
        for node in root.iter("genre"):
            genre_id = leading_int(node.findtext("id"))
            if genre_id:
                genres[genre_id] = Genre(
                    genre_id, leading_int(node.findtext("parent")),
                    {child.tag: (child.text or "").strip() for child in node
                     if child.tag.startswith("nom_") and (child.text or "").strip()},
                    tuple((child.text or "").strip() for child in node.findall("altname")))
        return cls(genres)

    def __contains__(self, genre_id):
        return genre_id in self.by_id

    def label(self, genre_id):
        genre = self.by_id.get(genre_id)
        if not genre:
            return str(genre_id)
        parent = self.by_id.get(genre.parent)
        return f"{parent.english_name} / {genre.english_name}" if parent else genre.english_name

    @functools.cached_property
    def ids_by_name(self):
        names = {}
        for genre in self.by_id.values():
            parent = self.by_id.get(genre.parent)
            for language, name in genre.names.items():
                if not parent:
                    names[name.upper()] = genre.id
                elif parent.names.get(language):
                    names[f"{parent.names[language].upper()} / {name.upper()}"] = genre.id
            for name in genre.altnames:
                names[name.upper()] = genre.id
        return names

    def id_from_name(self, name):
        name = name.strip().upper()
        if name in self.ids_by_name:
            return self.ids_by_name[name]
        return next((self.ids_by_name[part.strip()] for part in name.split("/")
                     if part.strip() in self.ids_by_name), None)

    @functools.cached_property
    def collections(self):
        top_level = [genre for genre in self.by_id.values() if not genre.parent and genre.id not in SKIPPED_GENRE_IDS]
        return sorted(top_level, key=lambda genre: genre.english_name.lower())

    def ids_of(self, game):
        genre_ids = set()
        for value in split_list(game.metadata.get("genres")):
            genre = self.by_id.get(leading_int(value))
            if genre:
                genre_ids.add(genre.id)
                if genre.parent in self.by_id:
                    genre_ids.add(genre.parent)
        return genre_ids


@functools.cache
def es_genres():
    return Genres.from_file()


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


@dataclass
class Filters:
    systems: set = field(default_factory=set)
    genres: set = field(default_factory=set)
    players: set = field(default_factory=set)

    @classmethod
    def from_form(cls, values):
        genre_ids = [leading_int(value) for value in values.getall("genre", [])]
        player_counts = [leading_int(value) for value in values.getall("players", [])]
        return cls({system for system in values.getall("system", []) if system},
                   {genre_id for genre_id in genre_ids if genre_id in es_genres()},
                   {count for count in player_counts if count in PLAYER_COUNTS})

    def matches(self, game):
        return ((not self.systems or game.system in self.systems)
                and (not self.genres or bool(es_genres().ids_of(game) & self.genres))
                and (not self.players or players_match(game.metadata.get("players"), self.players)))

    def matching_games(self, games):
        return sorted((game for game in games if not game.hidden and self.matches(game)),
                      key=lambda game: game.name.lower())


def settings_tree():
    try:
        return ET.parse(ES_SETTINGS_FILE)
    except FileNotFoundError:
        return ET.ElementTree(ET.Element("config"))
    except ET.ParseError:
        raise CollectionError(f"Can't read {ES_SETTINGS_FILE}")


@dataclass
class CollectionSettings:
    auto: list
    custom: list
    grouping: str

    @classmethod
    def read(cls):
        values = dict(SETTING_DEFAULTS)
        root = settings_tree().getroot()
        for node in root.findall("string"):
            if node.get("name") in values:
                values[node.get("name")] = node.get("value", "")
        for node in root.findall("bool"):
            if node.get("name") == LEGACY_GROUPING_SETTING and node.get("value") == "false":
                values[GROUPING_SETTING] = "false"
        return cls(split_list(values[AUTO_SETTING]), split_list(values[CUSTOM_SETTING]), values[GROUPING_SETTING])

    def write(self):
        values = {AUTO_SETTING: ",".join(self.auto), CUSTOM_SETTING: ",".join(self.custom),
                  GROUPING_SETTING: self.grouping}
        root = settings_tree().getroot()
        for node in root.findall("bool"):
            if node.get("name") == LEGACY_GROUPING_SETTING:
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
        return self


def update_custom_list(change):
    settings = CollectionSettings.read()
    settings.custom = change(settings.custom)
    return settings.write()


def resolve_entry(line):
    if line.startswith("./"):
        return os.path.normpath(os.path.join(USERDATA_DIR, line[2:]))
    if line.startswith("~/"):
        return os.path.normpath(os.path.join(ES_HOME_DIR, line[2:]))
    return os.path.normpath(line)


def entry_line(path):
    return "./" + os.path.relpath(path, USERDATA_DIR) if path.startswith(USERDATA_DIR + "/") else path


class ManualCollection:
    kind = MANUAL

    def __init__(self, name):
        self.name = name
        self.path = os.path.join(COLLECTIONS_DIR, f"custom-{name}.cfg")

    def game_paths(self):
        try:
            with open(self.path, encoding="utf-8", errors="replace") as collection_file:
                lines = [line.strip() for line in collection_file]
        except FileNotFoundError:
            raise CollectionError(f"The collection {self.name} doesn't exist")
        return list(dict.fromkeys(resolve_entry(line) for line in lines if line and line[0] not in "0#"))

    def save(self, paths):
        temp_file = self.path + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as collection_file:
            collection_file.writelines(entry_line(path) + "\n" for path in paths)
        os.replace(temp_file, self.path)

    def add(self, path):
        paths = self.game_paths()
        if path not in paths:
            self.save(paths + [path])

    def remove(self, path):
        paths = self.game_paths()
        if path in paths:
            self.save([other for other in paths if other != path])

    def size(self, games):
        paths = {game.path for game in games}
        return sum(1 for path in self.game_paths() if path in paths)

    def rename_file(self, new_name):
        renamed = ManualCollection(new_name)
        os.rename(self.path, renamed.path)


class DynamicCollection:
    kind = DYNAMIC

    def __init__(self, name):
        self.name = name
        self.path = os.path.join(COLLECTIONS_DIR, f"{name}.xcc")

    def root(self):
        try:
            root = ET.parse(self.path).getroot()
        except FileNotFoundError:
            raise CollectionError(f"The collection {self.name} doesn't exist")
        except ET.ParseError:
            raise CollectionError(f"Can't read the filters of {self.name}")
        if root.tag != "filter":
            raise CollectionError(f"Can't read the filters of {self.name}")
        return root

    def read(self):
        filters, other_nodes = Filters(), []
        for node in self.root():
            text = (node.text or "").strip()
            if node.tag == "system" and text:
                filters.systems.add(text)
            elif node.tag == "genre" and es_genres().id_from_name(text):
                filters.genres.add(es_genres().id_from_name(text))
            elif node.tag == "players" and leading_int(text) > 0:
                filters.players.add(leading_int(text))
            elif node.tag not in FILTER_TAGS:
                other_nodes.append(node)
        return filters, other_nodes

    def save(self, filters, other_nodes=()):
        root = ET.Element("filter", name=self.name)
        for system in sorted(filters.systems):
            ET.SubElement(root, "system").text = system
        for genre_id in sorted(filters.genres):
            ET.SubElement(root, "genre").text = es_genres().label(genre_id).upper()
        for count in sorted(filters.players):
            ET.SubElement(root, "players").text = str(count)
        for node in other_nodes:
            node = copy.deepcopy(node)
            node.tail = None
            root.append(node)
        write_xml(self.path, root)

    def size(self, games):
        return len(self.read()[0].matching_games(games))

    def rename_file(self, new_name):
        filters, other_nodes = self.read()
        DynamicCollection(new_name).save(filters, other_nodes)
        os.remove(self.path)


COLLECTION_CLASSES = {MANUAL: ManualCollection, DYNAMIC: DynamicCollection}


def collection_kinds():
    try:
        filenames = sorted(os.listdir(COLLECTIONS_DIR))
    except FileNotFoundError:
        return {}
    kinds = {}
    for filename in filenames:
        if filename.startswith("custom-") and filename.endswith(".cfg") and len(filename) > len("custom-.cfg"):
            kinds.setdefault(filename[len("custom-"):-len(".cfg")], MANUAL)
        elif filename.endswith(".xcc") and len(filename) > len(".xcc"):
            kinds[filename[:-len(".xcc")]] = DYNAMIC
    return kinds


def find_collection(name):
    kind = collection_kinds().get(name)
    if not kind:
        raise CollectionError(f"The collection {name} doesn't exist")
    return COLLECTION_CLASSES[kind](name)


def all_collections():
    return [COLLECTION_CLASSES[kind](name)
            for name, kind in sorted(collection_kinds().items(), key=lambda item: item[0].lower())]


def manual_collections():
    return [collection for collection in all_collections() if collection.kind == MANUAL]


def reserved_names():
    return ({key for key, _ in AUTO_COLLECTIONS} | {genre.collection_key for genre in es_genres().collections}
            | {"collections"})


def checked_name(name, renaming=None):
    name = " ".join((name or "").split())
    if not name:
        raise CollectionError("Give the collection a name")
    if len(name) > MAX_NAME_LENGTH:
        raise CollectionError(f"Use at most {MAX_NAME_LENGTH} characters")
    if not NAME_PATTERN.fullmatch(name):
        raise CollectionError("Use only letters, digits, spaces and - [ ] ( )")
    if name.lower() in reserved_names():
        raise CollectionError(f"EmulationStation already uses the name {name}")
    if any(other.lower() == name.lower() and other != renaming for other in collection_kinds()):
        raise CollectionError(f"A collection named {name} already exists")
    return name


def create_collection(name, kind):
    name = checked_name(name)
    os.makedirs(COLLECTIONS_DIR, exist_ok=True)
    collection = COLLECTION_CLASSES[kind](name)
    collection.save([] if kind == MANUAL else Filters())
    return collection, update_custom_list(lambda names: names + [name])


def rename_collection(collection, new_name):
    new_name = checked_name(new_name, renaming=collection.name)
    if new_name == collection.name:
        return collection, None
    collection.rename_file(new_name)
    settings = update_custom_list(lambda names: [new_name if name == collection.name else name for name in names])
    return COLLECTION_CLASSES[collection.kind](new_name), settings


def delete_collection(collection):
    for path in (ManualCollection(collection.name).path, DynamicCollection(collection.name).path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    return update_custom_list(lambda names: [name for name in names if name != collection.name])


def manual_collections_with(path):
    memberships = []
    for collection in manual_collections():
        try:
            memberships.append({"name": collection.name, "member": path in collection.game_paths()})
        except CollectionError:
            continue
    return memberships


def set_memberships(path, wanted_names):
    changed = False
    for membership in manual_collections_with(path):
        wanted = membership["name"] in wanted_names
        if membership["member"] == wanted:
            continue
        if wanted:
            ManualCollection(membership["name"]).add(path)
        else:
            ManualCollection(membership["name"]).remove(path)
        changed = True
    return changed


def save_game_collections(path, wanted_names, new_name):
    settings = None
    if new_name:
        collection, settings = create_collection(new_name, MANUAL)
        wanted_names = wanted_names | {collection.name}
    return set_memberships(path, wanted_names) or bool(new_name), settings


class PendingRestart:
    def __init__(self):
        self.es_process_id = None
        self.settings = None

    def remember(self, settings=None):
        self.settings = settings or self.settings or CollectionSettings.read()
        self.es_process_id = es_process_id()

    def waiting(self):
        if self.es_process_id is not None and self.es_process_id != es_process_id():
            self.es_process_id = self.settings = None
        return self.es_process_id is not None

    def reapply_after_exit(self, old_process_id):
        if self.settings is None or old_process_id is None:
            return
        deadline = time.monotonic() + ES_EXIT_TIMEOUT_SECONDS
        while os.path.exists(f"/proc/{old_process_id}") and time.monotonic() < deadline:
            time.sleep(ES_EXIT_POLL_SECONDS)
        if CollectionSettings.read() != self.settings:
            self.settings.write()
        self.es_process_id = self.settings = None
