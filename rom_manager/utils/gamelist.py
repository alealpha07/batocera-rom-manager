import os
import re
import xml.etree.ElementTree as ET

GAME_ENTRY = re.compile(r"[ \t]*<game\b.*?</game>[ \t]*\r?\n?", re.S)
NEW_GAMELIST = '<?xml version="1.0"?>\n<gameList>\n%s</gameList>\n'
ES_DEFAULT_VALUES = {"", "0", "0.000000", "false", "not-a-date-time"}
ES_NOT_METADATA = {"id", "path", "systemName"}


def gamelist_file(folder):
    return os.path.join(folder, "gamelist.xml")


def resolve(folder, value):
    value = (value or "").strip()
    return os.path.normpath(os.path.join(folder, os.path.expanduser(value))) if value else ""


def is_file_ref(value):
    return (value or "").strip().startswith(("./", "/", "~/"))


def read_entries(folder):
    try:
        with open(gamelist_file(folder), encoding="utf-8", newline="") as fh:
            text = fh.read()
    except FileNotFoundError:
        return "", []
    except UnicodeDecodeError:
        raise OSError("Can't read %s" % gamelist_file(folder))
    entries = []
    for match in GAME_ENTRY.finditer(text):
        try:
            entries.append((match, ET.fromstring(match.group().strip())))
        except ET.ParseError:
            continue
    return text, entries


def is_entry_for(folder, element, path):
    return resolve(folder, element.findtext("path")) == os.path.normpath(path)


def entry_files(folder, element):
    return {resolve(folder, child.text) for child in element if child.tag != "path" and is_file_ref(child.text)}


def media_files(folder, path):
    own, others = set(), set()
    for _, element in read_entries(folder)[1]:
        (own if is_entry_for(folder, element, path) else others).update(entry_files(folder, element))
    return sorted(f for f in own - others if os.path.isfile(f))


def all_entry_files(folder):
    return {file for _, element in read_entries(folder)[1] for file in entry_files(folder, element)}


def entry_media(folder, path):
    for _, element in read_entries(folder)[1]:
        if is_entry_for(folder, element, path):
            return {child.tag: resolve(folder, child.text) for child in element
                    if child.tag != "path" and is_file_ref(child.text)}
    return {}


def remove_media_tag(folder, path, kind):
    text, entries = read_entries(folder)
    tag = re.compile(r"[ \t]*<%s>.*?</%s>[ \t]*\r?\n?" % (kind, kind), re.S)
    blocks = [match for match, element in entries if is_entry_for(folder, element, path)]
    for match in reversed(blocks):
        text = text[:match.start()] + tag.sub("", match.group()) + text[match.end():]
    if blocks:
        write_text(folder, text)


def write_text(folder, text):
    temp_file = gamelist_file(folder) + ".tmp"
    with open(temp_file, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(temp_file, gamelist_file(folder))


def remove_entry(folder, path):
    text, entries = read_entries(folder)
    spans = [match.span() for match, element in entries if is_entry_for(folder, element, path)]
    if spans:
        write_text(folder, without_spans(text, spans))


def without_spans(text, spans):
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    return text
