import json
import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT_DIR, "config")


def _strip_comments(text):
    # Позволяет писать //-комментарии в JSON-конфигах (удобно для пользователя)
    text = re.sub(r"//[^\n]*", "", text)
    # убираем запятые перед закрывающими скобками
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.loads(_strip_comments(f.read()))
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def load_apps():
    return _read_json(os.path.join(CONFIG_DIR, "apps.json"), {"aliases": {}})


def load_settings():
    return _read_json(
        os.path.join(CONFIG_DIR, "settings.json"), {"tts_enabled": True, "language": "ru"}
    )
