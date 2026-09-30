import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from utils.logger import get_logger

log = get_logger("llm")

_CYRILLIC = re.compile(r"[Ѐ-ӿа-яёА-ЯЁ]")
_EN_WORDS = re.compile(r"[A-Za-z']+")

RECOVERY_INTERVAL = 300  # 5 минут


def _looks_like_junk(text):
    if not text or not text.strip():
        return False
    if _CYRILLIC.search(text):
        return False
    return len(_EN_WORDS.findall(text)) >= 4


class LLMClient:
    """Slow-Path: модели через anymodel.org.

    - Переключение вправо при первой же ошибке или мусорном ответе.
    - Фоновый таймер: каждые 5 минут пингует самую сильную модель (индекс 0)
      и возвращается на неё, если она снова доступна.
    - Поддерживает Tool Calling.
    """

    def __init__(self, base_url=None, api_key=None, model=None, timeout=None):
        self.base_url = (
            base_url or os.getenv("ANYMODEL_BASE_URL", "https://anymodel.org/v1")
        ).rstrip("/")
        self.api_key = api_key or os.getenv("ANYMODEL_API_KEY", "")
        self.timeout = int(timeout or os.getenv("LLM_TIMEOUT", "45"))

        self.models = self._parse_models(
            model or os.getenv("ANYMODEL_MODEL", "ds/deepseek-v4-flash"),
            os.getenv("ANYMODEL_FALLBACK", "ds/deepseek-v4-pro,am/free"),
        )

        self._model_idx = 0
        self._lock = threading.Lock()
        self._recovery_scheduled = False
        self._last_error = None
        self._last_status = None

    @staticmethod
    def _parse_models(primary, fallbacks_str):
        models = [m.strip() for m in primary.split(",") if m.strip()]
        if fallbacks_str:
            for m in fallbacks_str.split(","):
                m = m.strip()
                if m and m not in models:
                    models.append(m)
        return models or ["ds/deepseek-v4-flash"]

    def _post(self, payload):
        """Один запрос к API. Никаких ретраев — переключение модели в ask()."""
        url = self.base_url + "/responses"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
        }
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self._last_status = resp.status
                self._last_error = None
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            self._last_status = e.code
            self._last_error = self._friendly_error(e.code)
        except urllib.error.URLError as e:
            self._last_status = None
            self._last_error = f"сеть: {e.reason}"
        except (TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
            self._last_status = None
            self._last_error = f"{type(e).__name__}: {e}"
        return None

    def _switch_right(self, source):
        """Переключается на следующую модель вправо. Возвращает имя новой модели или None."""
        with self._lock:
            if self._model_idx < len(self.models) - 1:
                self._model_idx += 1
                new_model = self.models[self._model_idx]
                log.info("переключаюсь с %s на %s", source, new_model)
                return new_model
        return None

    def _current_model(self):
        with self._lock:
            return self.models[self._model_idx]

    def _switch_back(self):
        with self._lock:
            if self._model_idx > 0:
                self._model_idx = 0
                log.info("возвращаюсь на %s", self.models[0])

    # --- recovery timer ---------------------------------------------------------

    def _schedule_recovery(self):
        with self._lock:
            if self._recovery_scheduled:
                return
            self._recovery_scheduled = True
        threading.Thread(target=self._recovery_loop, daemon=True).start()
        log.info("запущен recovery-таймер (%ds)", RECOVERY_INTERVAL)

    def _recovery_loop(self):
        while True:
            time.sleep(RECOVERY_INTERVAL)
            with self._lock:
                if self._model_idx == 0:  # уже на primary
                    self._recovery_scheduled = False
                    return
            if self._try_recover():
                return

    def _try_recover(self):
        """Пингует модель [0] минимальным запросом (1 output-токен)."""
        primary = self.models[0]
        payload = {
            "model": primary,
            "input": "ok",
            "max_output_tokens": 1,
        }
        data = self._post(payload)
        if data is None:
            return False
        answer = self._parse(data)
        if answer and not _looks_like_junk(answer):
            self._switch_back()
            return True
        return False

    # --- основной метод --------------------------------------------------------

    def ask(self, question, system_prompt=None, history=None, tools=None,
            tool_executor=None, max_tool_rounds=5):
        """Возвращает dict: {ok, answer, error, attempts, status}."""
        if not self.api_key:
            return self._result(False, "", "API-ключ не задан (ANYMODEL_API_KEY в .env).", 0, None)

        if history:
            messages = list(history) + [{"role": "user", "content": question}]
        else:
            messages = [{"role": "user", "content": question}]

        total_attempts = 0

        for _round in range(max_tool_rounds + 1):
            current_model = self._current_model()
            payload = {"model": current_model, "input": messages}
            if system_prompt:
                payload["instructions"] = system_prompt
            if tools:
                payload["tools"] = tools

            data = self._post(payload)
            total_attempts += 1

            if data is None:
                log.warning("модель %s ошибка: %s", current_model, self._last_error)
                if self._switch_right(current_model):
                    self._schedule_recovery()
                    continue
                return self._result(False, self._fallback_message(), self._last_error,
                                    total_attempts, self._last_status)

            function_calls = [
                it for it in data.get("output", [])
                if isinstance(it, dict) and it.get("type") == "function_call"
            ]

            if not function_calls:
                answer = self._parse(data)
                if answer and not _looks_like_junk(answer):
                    return self._result(True, answer, None, total_attempts, self._last_status)

                if answer:
                    log.warning("модель %s мусор: %r", current_model, answer[:60])
                else:
                    log.warning("модель %s пустой ответ", current_model)
                if self._switch_right(current_model):
                    self._schedule_recovery()
                    continue
                return self._result(False, self._fallback_message(),
                                    "все модели исчерпаны", total_attempts, 200)

            # tool execution
            for fc in function_calls:
                name = fc.get("name")
                call_id = fc.get("call_id")
                try:
                    args = json.loads(fc.get("arguments") or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                output = tool_executor(name, args) if tool_executor else "инструмент недоступен"
                log.info("tool call [%s]: %s(%s)", current_model, name,
                         json.dumps(args, ensure_ascii=False)[:100])
                messages.append({
                    "type": "function_call", "call_id": call_id,
                    "name": name, "arguments": fc.get("arguments"),
                })
                messages.append({
                    "type": "function_call_output", "call_id": call_id,
                    "output": output,
                })

        return self._result(False, self._fallback_message(), "превышено число rounds",
                            total_attempts, 200)

    def _parse(self, data):
        items = data.get("output", []) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("type") == "message":
                for chunk in item.get("content", []):
                    if isinstance(chunk, dict) and chunk.get("type") in ("output_text", "text"):
                        text = chunk.get("text", "")
                        if text.strip():
                            return text.strip()
        if isinstance(data, dict) and "choices" in data:
            try:
                return data["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError, TypeError, AttributeError):
                pass
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            return data["text"].strip()
        return ""

    @staticmethod
    def _friendly_error(code):
        if code in (401, 403):
            return "Неверный API-ключ."
        if code == 429:
            return "Слишком много запросов (429)."
        if code in (500, 502, 503, 504):
            return "Сервер anymodel.org временно недоступен."
        return f"Ошибка HTTP {code}."

    def _fallback_message(self):
        return "Джарвис: сервис временно не отвечает. Повтори позже."

    @staticmethod
    def _result(ok, answer, error, attempts, status):
        return {"ok": ok, "answer": answer, "error": error, "attempts": attempts, "status": status}
