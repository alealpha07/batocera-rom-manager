import functools
import glob
import os
import xml.etree.ElementTree as ET

ES_SYSTEMS_FILE = "/usr/share/emulationstation/es_systems.cfg"
USER_ES_SYSTEMS_FILES = "/userdata/system/configs/emulationstation/es_systems_*.cfg"


@functools.cache
def all_systems():
    systems = {}
    for path in [ES_SYSTEMS_FILE] + sorted(glob.glob(USER_ES_SYSTEMS_FILES)):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        for system in root.iter("system"):
            name, folder = system.findtext("name"), system.findtext("path")
            if name and folder:
                systems[name] = {"name": name, "fullname": system.findtext("fullname") or name,
                                 "path": os.path.expanduser(folder.strip()),
                                 "extensions": (system.findtext("extension") or "").lower().split()}
    return systems
