"""Инструменты (Tool Calling) для Slow-Path.

Каждый инструмент: (1) JSON-схема для API, (2) функция-исполнитель
`executor(name, arguments_dict) -> str`. Модель сама выбирает, что вызвать.
"""
import datetime
import html as html_lib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

from utils.logger import get_logger
from utils.simple_math import evaluate_math

log = get_logger("tools")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

HOME = os.path.expanduser("~")

# OCR лениво грузится один раз
_OCR = None


def _clean(text):
    text = html_lib.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


# --- интернет --------------------------------------------------------------

def _fetch_ddg_page(query):
    q = urllib.parse.quote(query)
    last_err = None
    for endpoint in (
        "https://html.duckduckgo.com/html/?q=",
        "https://lite.duckduckgo.com/lite/?q=",
    ):
        for attempt in range(2):
            try:
                req = urllib.request.Request(endpoint + q, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.read().decode("utf-8", "replace")
            except Exception as exc:
                last_err = exc
                time.sleep(0.6 * (attempt + 1))
    raise last_err or RuntimeError("search failed")


def _web_search(query, max_results=5):
    page = _fetch_ddg_page(query)
    results = []
    blocks = re.split(r'<div class="result results_links', page)[1:]
    for block in blocks:
        m_url = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        m_snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        if not m_url:
            continue
        href = m_url.group(1)
        real = re.search(r"uddg=([^&]+)", href)
        link = urllib.parse.unquote(real.group(1)) if real else href
        title = _clean(m_url.group(2))
        snippet = _clean(m_snip.group(1)) if m_snip else ""
        results.append({"title": title, "url": link, "snippet": snippet})
        if len(results) >= max_results:
            break
    if not results:
        return "По запросу «%s» ничего не найдено." % query
    lines = []
    for i, r in enumerate(results, 1):
        lines.append("%d. %s\n   %s\n   %s" % (i, r["title"], r["snippet"], r["url"]))
    return "\n\n".join(lines)


# --- файлы -----------------------------------------------------------------

def _resolve_path(path):
    path = os.path.expanduser(path or "")
    if not os.path.isabs(path):
        path = os.path.join(HOME, path)
    return path


def _create_file(path, content):
    path = _resolve_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return "Файл создан: %s (%d символов)." % (path, len(content))


def _write_file(path, content):
    return _create_file(path, content)


def _append_file(path, content):
    path = _resolve_path(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
    return "Дописано в файл: %s." % path


def _read_file(path, limit=20000):
    path = _resolve_path(path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(limit)
        if not data:
            return "Файл пуст: %s" % path
        return data
    except FileNotFoundError:
        return "Файл не найден: %s" % path
    except OSError as exc:
        return "Не удалось прочитать файл: %s" % exc


def _list_directory(path="~"):
    path = _resolve_path(path)
    try:
        entries = sorted(os.listdir(path))
    except (FileNotFoundError, NotADirectoryError):
        return "Каталог не найден: %s" % path
    except OSError as exc:
        return "Не удалось открыть каталог: %s" % exc
    lines = []
    for name in entries:
        full = os.path.join(path, name)
        if os.path.isdir(full):
            lines.append("[DIR ] " + name)
        else:
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            lines.append("[%5d] %s" % (size, name))
    return "Содержимое %s:\n%s" % (path, "\n".join(lines) if lines else "(пусто)")


def _current_time():
    return datetime.datetime.now().strftime("%H:%M:%S")


def _current_date():
    return datetime.datetime.now().strftime("%d.%m.%Y (%A)")


def _open_url(url):
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        url = "https://" + url
    ok = subprocess.run(["open", url], capture_output=True).returncode == 0
    return "Открыл в браузере: %s" % url if ok else "Не удалось открыть: %s" % url


def _open_application(name):
    from core.system_mac import MacSystem
    sys_ = MacSystem()
    ok = sys_.open_application(name)
    return "Запустил: %s" % name if ok else "Не удалось запустить: %s" % name


def _calculate(expression):
    value = evaluate_math(expression)
    return "Результат: %s" % value if value is not None else "Не удалось посчитать."


def _run_shell(command, timeout=30):
    try:
        proc = subprocess.run(
            ["/bin/zsh", "-lc", command],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0:
            return out if out else "(выполнено без вывода)"
        return "Ошибка (код %d): %s" % (proc.returncode, err or out)
    except subprocess.TimeoutExpired:
        return "Команда превысила лимит времени (%ds)." % timeout
    except OSError as exc:
        return "Не удалось выполнить команду: %s" % exc


# --- зрение (OCR локально, без API и токенов) -------------------------------

def _get_ocr():
    global _OCR
    if _OCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR = RapidOCR()
        except Exception as exc:
            log.warning("RapidOCR не загрузился: %s", exc)
            _OCR = False
    return _OCR or None


def _screenshot():
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    ok = subprocess.run(["screencapture", "-x", path], capture_output=True).returncode == 0
    if not ok:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return path


def _ocr_text(path):
    engine = _get_ocr()
    if engine is None:
        return None
    try:
        result, _ = engine(path)
    except Exception as exc:
        log.warning("OCR: %s", exc)
        return None
    if not result:
        return ""
    items = sorted(result, key=lambda r: (r[0][0][1], r[0][0][0]))
    return "\n".join(r[1] for r in items if r[1])


def _look_at_screen(question=""):
    """Скриншот по запросу → локальный OCR текста (без API, без токенов)."""
    path = _screenshot()
    if path is None:
        return ("Не удалось сделать скриншот. Дай терминалу право «Запись экрана»: "
                "Системные настройки → Конфиденциальность и безопасность → Запись экрана.")
    try:
        text = _ocr_text(path)
        if text:
            return "Текст на экране:\n" + text
        return "На экране не распознано текста."
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# --- схемы и диспетчер ------------------------------------------------------

TOOL_SPECS = [
    {
        "type": "function",
        "name": "web_search",
        "description": "Ищет в интернете и возвращает заголовки, выдержки и ссылки. Используй для актуальных фактов: погода, курсы, новости.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "поисковый запрос"}},
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "create_file",
        "description": "Создаёт (или перезаписывает) текстовый файл. Относительные пути — от домашней папки.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "путь к файлу"},
                "content": {"type": "string", "description": "содержимое файла"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "type": "function",
        "name": "append_file",
        "description": "Дописывает текст в конец файла.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Читает текстовый файл и возвращает его содержимое.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "type": "function",
        "name": "list_directory",
        "description": "Показывает содержимое каталога.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "путь, по умолчанию домашняя папка"}},
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "current_time",
        "description": "Возвращает текущее время.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "current_date",
        "description": "Возвращает сегодняшнюю дату.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "open_url",
        "description": "Открывает ссылку в браузере пользователя.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "type": "function",
        "name": "open_application",
        "description": "Запускает приложение по имени.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "type": "function",
        "name": "calculate",
        "description": "Считает арифметическое выражение (например, '2+2*3').",
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    {
        "type": "function",
        "name": "run_shell",
        "description": "Выполняет shell-команду в zsh и возвращает вывод. Используй осторожно и только по явной просьбе пользователя.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "type": "function",
        "name": "look_at_screen",
        "description": "Делает скриншот и возвращает распознанный текст с экрана (локальный OCR, без API).",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "опциональный вопрос о содержимом экрана"}},
            "required": [],
        },
    },
]

_HANDLERS = {
    "web_search": _web_search,
    "create_file": _create_file,
    "write_file": _write_file,
    "append_file": _append_file,
    "read_file": _read_file,
    "list_directory": _list_directory,
    "current_time": _current_time,
    "current_date": _current_date,
    "open_url": _open_url,
    "open_application": _open_application,
    "calculate": _calculate,
    "run_shell": _run_shell,
    "look_at_screen": _look_at_screen,
}


def execute_tool(name, arguments):
    """Выполняет инструмент и возвращает строку-результат для API."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return "Неизвестный инструмент: %s" % name
    args = arguments if isinstance(arguments, dict) else {}
    try:
        return str(handler(**args))
    except TypeError:
        return "Ошибка вызова инструмента %s: неверные аргументы" % name
    except Exception as exc:
        log.warning("tool %s failed: %s", name, exc)
        return "Ошибка инструмента %s: %s" % (name, exc)
