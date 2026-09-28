import re

from . import config


class Router:
    """Fast-Path: локальный NLU/regex-роутер, 0 токенов LLM."""

    INTENT_OPEN_APP = "open_app"
    INTENT_OPEN_FILE = "open_file"
    INTENT_VOLUME_UP = "volume_up"
    INTENT_VOLUME_DOWN = "volume_down"
    INTENT_MUTE = "mute"
    INTENT_EXIT = "exit"
    INTENT_HELP = "help"

    def __init__(self):
        self.aliases = config.load_apps().get("aliases", {})

    def _resolve_alias(self, name):
        return self.aliases.get(name.strip().lower(), name)

    def route(self, text):
        t = (text or "").strip()
        if not t:
            return self._no()
        low = t.lower()

        if re.fullmatch(r"(выход|выйди|закройся|стоп|exit|quit|q)", low):
            return self._ok(self.INTENT_EXIT)

        if re.fullmatch(r"(помощь|help|что ты умеешь|команды)", low):
            return self._ok(self.INTENT_HELP)

        if re.search(r"(громче|увеличь громкость|громкость вверх|volume up|louder)", low):
            return self._ok(self.INTENT_VOLUME_UP)
        if re.search(r"(тише|уменьши громкость|громкость вниз|volume down|quieter)", low):
            return self._ok(self.INTENT_VOLUME_DOWN)
        if re.search(r"(выключи звук|без звука|заглуши|mute|мут)", low):
            return self._ok(self.INTENT_MUTE)

        # «открой файл X» / «найди документ X»
        m = re.search(r"(?:открой|найди|open|find)\s+(?:файл|документ|file)\s+(.+)", low)
        if m:
            return self._ok(self.INTENT_OPEN_FILE, target=m.group(1).strip())

        # «открой что-то.расширение» (файл по расширению)
        m = re.search(r"(?:открой|найди|open|find)\s+(\S*\.\S+)", low)
        if m:
            return self._ok(self.INTENT_OPEN_FILE, target=m.group(1).strip())

        # «открой/запусти <приложение>»
        m = re.search(r"(?:открой|запусти|включи|open|launch|run|start)\s+(.+)", low)
        if m:
            target = m.group(1).strip()
            return self._ok(self.INTENT_OPEN_APP, target=self._resolve_alias(target))

        return self._no()

    def _ok(self, intent, **payload):
        result = {"matched": True, "intent": intent}
        result.update(payload)
        return result

    def _no(self):
        return {"matched": False, "intent": None}
