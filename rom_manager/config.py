import json
import os

HOST = "0.0.0.0"
PORT = 18765
APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = "/userdata/system/configs/rom-manager/config.json"
ES_API = "http://127.0.0.1:1234"
ROMS_DIR = "/userdata/roms"
BIOS_DIR = "/userdata/bios"
BIOS_CHECKER = "/usr/bin/batocera-systems"


def load_config():
    try:
        with open(CONFIG_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    temp_file = CONFIG_FILE + ".tmp"
    with open(os.open(temp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(config, fh, indent=2)
    os.replace(temp_file, CONFIG_FILE)
