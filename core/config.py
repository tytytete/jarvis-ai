import json
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT_DIR, "config")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def load_apps():
    return _read_json(os.path.join(CONFIG_DIR, "apps.json"), {"aliases": {}})


def load_settings():
    return _read_json(
        os.path.join(CONFIG_DIR, "settings.json"), {"tts_enabled": True, "language": "ru"}
    )
