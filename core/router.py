import difflib
import re

from . import config


class Router:
    """Fast-Path: локальный NLU/regex-роутер, 0 токенов LLM.

    Приоритет интентов важен: сначала точные/безопасные (0-арные), потом
    специфичные с целью (файл/URL/поиск/закрыть), и в самом конце — общий
    «открой <приложение>» с допуском опечаток в ведущем глаголе.
    """

    INTENT_EXIT = "exit"
    INTENT_HELP = "help"
    INTENT_TIME = "time"
    INTENT_DATE = "date"
    INTENT_VOLUME_UP = "volume_up"
    INTENT_VOLUME_DOWN = "volume_down"
    INTENT_MUTE = "mute"
    INTENT_UNMUTE = "unmute"
    INTENT_BRIGHTNESS_UP = "brightness_up"
    INTENT_BRIGHTNESS_DOWN = "brightness_down"
    INTENT_SCREENSHOT = "screenshot"
    INTENT_LOCK = "lock"
    INTENT_SLEEP = "sleep"
    INTENT_MATH = "math"
    INTENT_OPEN_APP = "open_app"
    INTENT_OPEN_FILE = "open_file"
    INTENT_OPEN_URL = "open_url"
    INTENT_CLOSE_APP = "close_app"
    INTENT_WEB_SEARCH = "web_search"
    INTENT_TYPE_TEXT = "type_text"

    OPEN_VERBS = [
        "открой", "открыть", "откройте", "открывай",
        "запусти", "запустить", "запускай",
        "включи", "включить", "включай",
        "open", "launch", "run", "start",
    ]
    CLOSE_VERBS = [
        "закрой", "закрыть", "закрывай",
        "выключи", "выключить",
        "отключи", "отключить",
        "quit", "close", "exit",
    ]
    TYPE_VERBS = ["напечатай", "введи", "набери", "type"]

    # Точные исправления опечаток в ведущем глаголе (фаззи не ловит длинные опечатки)
    TYPO_MAP = {
        "открткрой": "открой",
        "откройт": "открой",
        "откро": "открой",
        "отктой": "открой",
        "откртой": "открой",
        "запустиь": "запусти",
        "заупсти": "запусти",
        "закройте": "закрой",
    }

    FILE_MARKERS = ["файл", "файлы", "документ", "документы", "папку", "папка", "file", "document", "folder"]
    URL_MARKERS = ["сайт", "сайты", "ссылку", "ссылке", "страницу", "страница", "вкладку", "в браузере"]

    def __init__(self):
        self.aliases = config.load_apps().get("aliases", {})

    ALL_VERBS = set(OPEN_VERBS) | set(CLOSE_VERBS) | set(TYPE_VERBS)

    # ---- служебные --------------------------------------------------------

    def _preprocess(self, text):
        t = (text or "").strip().lower()
        t = re.sub(r"[.,!?;:]+$", "", t)
        t = re.sub(r"\s+", " ", t)
        return t

    def _canonical_verb(self, word, verbs):
        if word in verbs:
            return word
        # Известный глагол другого интента (напр. «включи»/«выключи») не подменяем
        # по фаззи — это дало бы ошибочную кросс-классификацию антонимов.
        if word in self.ALL_VERBS:
            return None
        if word in self.TYPO_MAP:
            mapped = self.TYPO_MAP[word]
            if mapped in verbs:
                return mapped
        if len(word) < 4:
            return None
        matches = difflib.get_close_matches(word, verbs, n=1, cutoff=0.82)
        return matches[0] if matches else None

    def _clean_target(self, target):
        target = target.strip()
        target = re.sub(r"^(мне|пожалуйста|давай|ну|плиз)\s+", "", target)
        target = re.sub(r"\s+(пожалуйста|плиз)$", "", target)
        return target.strip()

    def _resolve_alias(self, name):
        key = name.strip().lower()
        return self.aliases.get(key, name)

    def _ok(self, intent, **payload):
        result = {"matched": True, "intent": intent}
        result.update(payload)
        return result

    def _no(self):
        return {"matched": False, "intent": None}

    # ---- маршрутизация ----------------------------------------------------

    def route(self, text):
        low = self._preprocess(text)
        if not low:
            return self._no()

        # 1. Выход / помощь (точное совпадение)
        if re.fullmatch(r"(выход|выйди|закройся|стоп|хватит|exit|quit|q)", low):
            return self._ok(self.INTENT_EXIT)
        if re.fullmatch(r"(помощь|help|что ты умеешь|команды|справка)", low):
            return self._ok(self.INTENT_HELP)

        # 2. Время / дата
        if re.search(r"(который час|сколько времени|сколько сейчас времени|текущее время|what time is it|сколько время)", low):
            return self._ok(self.INTENT_TIME)
        if re.search(r"(какое сегодня число|какой сегодня день|какая сегодня дата|сегодняшняя дата|какой день недели|what is the date|какое число)", low):
            return self._ok(self.INTENT_DATE)

        # 3. Звук
        if re.search(r"(громче|погромче|увеличь громкость|увеличь звук|прибавь звук|сделай громче|сделай погромче|громкость вверх|volume up|louder)", low):
            return self._ok(self.INTENT_VOLUME_UP)
        if re.search(r"(тише|потише|убавь звук|уменьши громкость|уменьши звук|сделай тише|громкость вниз|volume down|quieter)", low):
            return self._ok(self.INTENT_VOLUME_DOWN)
        if re.search(r"(выключи звук|отключи звук|без звука|заглуши|mute|мут)", low):
            return self._ok(self.INTENT_MUTE)
        if re.search(r"(включи звук|верни звук|unmute)", low):
            return self._ok(self.INTENT_UNMUTE)

        # 4. Яркость
        if re.search(r"(ярче|увеличь яркость|прибавь яркость|сделай ярче|brightness up)", low):
            return self._ok(self.INTENT_BRIGHTNESS_UP)
        if re.search(r"(темнее|уменьши яркость|убавь яркость|сделай темнее|приглуши экран|brightness down)", low):
            return self._ok(self.INTENT_BRIGHTNESS_DOWN)

        # 5. Скриншот / блокировка / сон
        if re.search(r"(скриншот|сделай скрин|сделай скриншот|скрин|screenshot|заскринь)", low):
            return self._ok(self.INTENT_SCREENSHOT)
        if re.fullmatch(r"(заблокируй экран|заблокируй компьютер|заблокируй|lock|lock screen)", low):
            return self._ok(self.INTENT_LOCK)
        if re.fullmatch(r"(спи|спать|усни|сон|засыпай|sleep|go to sleep)", low):
            return self._ok(self.INTENT_SLEEP)

        # 6. Арифметика (локальный расчёт, без ИИ)
        math = self._match_math(low)
        if math:
            return math

        # 7. Открыть файл (специфичнее, чем приложение)
        result = self._match_open_file(low)
        if result:
            return result

        # 8. Открыть URL
        result = self._match_open_url(low)
        if result:
            return result

        # 9. Веб-поиск
        result = self._match_web_search(low)
        if result:
            return result

        # 10. Закрыть приложение
        result = self._match_close_app(low)
        if result:
            return result

        # 11. Ввод текста
        result = self._match_type_text(low)
        if result:
            return result

        # 12. Открыть приложение (самый общий — в конце)
        result = self._match_open_app(low)
        if result:
            return result

        return self._no()

    # ---- матчеры с целью --------------------------------------------------

    def _match_math(self, low):
        m = re.search(r"(?:сколько будет|посчитай|вычисли|чему равно|calculate|what is)\s+(.+)", low)
        expr = m.group(1) if m else None
        if expr is None:
            # чистое арифметическое выражение вида «12+7» / «3 * (4-1)»
            if re.fullmatch(r"[\d\s+\-*/^().,х×]+", low) and re.search(r"\d", low):
                expr = low
        if expr:
            expr = expr.strip()
            if re.fullmatch(r"[\d\s+\-*/^().,х×]+", expr):
                return self._ok(self.INTENT_MATH, expression=expr)
        return None

    def _match_open_file(self, low):
        markers = "|".join(self.FILE_MARKERS)
        m = re.search(rf"(?:открой|открыть|найди|open|find)\s+(?:{markers})\s+(.+)", low)
        if m:
            return self._ok(self.INTENT_OPEN_FILE, target=m.group(1).strip())
        m = re.search(r"(?:открой|открыть|запусти|найди|open|find|launch)\s+(\S*\.\S+)", low)
        if m:
            return self._ok(self.INTENT_OPEN_FILE, target=m.group(1).strip())
        return None

    def _match_open_url(self, low):
        markers = "|".join(self.URL_MARKERS)
        m = re.search(rf"(?:открой|открыть|перейди|зайди|open|go to)\s+(?:{markers})\s+(.+)", low)
        if m:
            return self._ok(self.INTENT_OPEN_URL, target=m.group(1).strip())
        m = re.search(r"(?:открой|открыть|open|go to)\s+([a-z0-9-]+\.(?:com|ru|org|net|io|dev|ai|me|app|site|by|ua|kz|md|com\.ua|com\.md)\S*)", low)
        if m:
            return self._ok(self.INTENT_OPEN_URL, target=m.group(1).strip())
        m = re.search(r"перейди\s+(?:на|в)\s+(\S+\.\S+)", low)
        if m:
            return self._ok(self.INTENT_OPEN_URL, target=m.group(1).strip())
        return None

    def _match_web_search(self, low):
        m = re.search(r"(?:загугли|погугли|гугл|поищи в интернете|поищи в гугле|найди в интернете|поищи|search for|google)\s+(.+)", low)
        if m:
            query = m.group(1).strip()
            return self._ok(self.INTENT_WEB_SEARCH, query=query)
        return None

    def _match_close_app(self, low):
        m = re.match(r"^(\S+)\s+(.+)$", low)
        if not m:
            return None
        verb = self._canonical_verb(m.group(1), self.CLOSE_VERBS)
        if verb is None:
            return None
        target = self._clean_target(m.group(2))
        if not target:
            return None
        return self._ok(self.INTENT_CLOSE_APP, target=self._resolve_alias(target))

    def _match_type_text(self, low):
        m = re.match(r"^(\S+)\s+(.+)$", low)
        if not m:
            return None
        verb = self._canonical_verb(m.group(1), self.TYPE_VERBS)
        if verb is None:
            return None
        target = self._clean_target(m.group(2))
        if not target:
            return None
        return self._ok(self.INTENT_TYPE_TEXT, text=target)

    def _match_open_app(self, low):
        m = re.match(r"^(\S+)\s+(.+)$", low)
        if not m:
            return None
        verb = self._canonical_verb(m.group(1), self.OPEN_VERBS)
        if verb is None:
            return None
        target = self._clean_target(m.group(2))
        if not target:
            return None
        return self._ok(self.INTENT_OPEN_APP, target=self._resolve_alias(target))
