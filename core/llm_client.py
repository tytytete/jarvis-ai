import json
import os
import re
import time
import urllib.error
import urllib.request

from utils.logger import get_logger

log = get_logger("llm")

RETRYABLE_HTTP = {429, 500, 502, 503, 504}

_CYRILLIC = re.compile(r"[Ѐ-ӿа-яёА-ЯЁ]")
_EN_WORDS = re.compile(r"[A-Za-z']+")


def _looks_like_junk(text):
    """Детектирует мусорный ответ (случайные английские цитаты от битого провайдера)."""
    if not text or not text.strip():
        return False
    if _CYRILLIC.search(text):
        return False
    # Нет кириллицы: если ≥4 английских слова — подозрительно (ответ на русском запрошен)
    return len(_EN_WORDS.findall(text)) >= 4


class LLMClient:
    """Slow-Path: DeepSeek и другие модели через anymodel.org.

    - Автоматический fallback между моделями при мусорных ответах.
    - Устойчив к «502 Bad Gateway», таймаутам (ретраи + фолбэк).
    - Поддерживает Tool Calling с циклом rounds.
    """

    def __init__(self, base_url=None, api_key=None, model=None, timeout=None, max_retries=None):
        self.base_url = (
            base_url or os.getenv("ANYMODEL_BASE_URL", "https://anymodel.org/v1")
        ).rstrip("/")
        self.api_key = api_key or os.getenv("ANYMODEL_API_KEY", "")
        self.timeout = int(timeout or os.getenv("LLM_TIMEOUT", "45"))
        self.max_retries = int(max_retries or os.getenv("LLM_MAX_RETRIES", "4"))

        # Модели: primary + fallbacks через запятую в ANYMODEL_MODEL / ANYMODEL_FALLBACK
        self.models = self._parse_models(
            model or os.getenv("ANYMODEL_MODEL", "cx/gpt-6-luna"),
            os.getenv("ANYMODEL_FALLBACK", "am/free,ds/deepseek-v4-pro"),
        )
        self._last_attempts = 0
        self._last_status = None
        self._last_error = None

    @staticmethod
    def _parse_models(primary, fallbacks_str):
        models = [m.strip() for m in primary.split(",") if m.strip()]
        if fallbacks_str:
            for m in fallbacks_str.split(","):
                m = m.strip()
                if m and m not in models:
                    models.append(m)
        return models or ["cx/gpt-6-luna"]

    def _post(self, payload):
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
        last_error = "неизвестная ошибка"
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self._last_attempts = attempt + 1
                    self._last_status = resp.status
                    self._last_error = None
                    return json.loads(resp.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}: {e.reason}"
                self._last_status = e.code
                if e.code not in RETRYABLE_HTTP:
                    self._last_attempts = attempt + 1
                    self._last_error = self._friendly_error(e.code)
                    return None
            except urllib.error.URLError as e:
                last_error = f"сеть/шлюз: {e.reason}"
            except (TimeoutError, OSError) as e:
                last_error = f"таймаут/соединение: {e}"
            except (json.JSONDecodeError, ValueError) as e:
                last_error = f"некорректный ответ: {e}"
            if attempt < self.max_retries:
                delay = min(2 ** attempt, 8)
                log.warning("попытка %s/%s (%s), повтор через %ss",
                            attempt + 1, self.max_retries + 1, last_error, delay)
                time.sleep(delay)

        self._last_attempts = self.max_retries + 1
        self._last_error = last_error
        self._last_status = None
        return None

    def ask(self, question, system_prompt=None, history=None, tools=None, tool_executor=None,
            max_tool_rounds=5, max_junk=4):
        """Возвращает dict: {ok, answer, error, attempts, status}."""
        if not self.api_key:
            return self._result(False, "", "API-ключ не задан (ANYMODEL_API_KEY в .env).", 0, None)

        if history:
            messages = list(history) + [{"role": "user", "content": question}]
        else:
            messages = [{"role": "user", "content": question}]

        total_attempts = 0
        model_idx = 0
        junk_count = 0

        for _round in range(max_tool_rounds + 1):
            current_model = self.models[model_idx]
            payload = {"model": current_model, "input": messages}
            if system_prompt:
                payload["instructions"] = system_prompt
            if tools:
                payload["tools"] = tools

            data = self._post(payload)
            total_attempts += self._last_attempts or 1

            if data is None:
                return self._result(
                    False, self._fallback_message(), self._last_error,
                    total_attempts, self._last_status,
                )

            function_calls = [
                it for it in data.get("output", [])
                if isinstance(it, dict) and it.get("type") == "function_call"
            ]

            if not function_calls:
                answer = self._parse(data)
                if answer and not _looks_like_junk(answer):
                    return self._result(True, answer, None, total_attempts, 200)

                if answer:
                    junk_count += 1
                    log.warning("мусорный ответ от %s (поп. %s/%s): %r",
                                current_model, junk_count, max_junk, answer[:60])
                    if junk_count <= max_junk:
                        continue
                    # Сменить модель
                    if model_idx < len(self.models) - 1:
                        model_idx += 1
                        junk_count = 0
                        log.info("переключаюсь на модель %s", self.models[model_idx])
                        continue
                    # Все модели исчерпаны
                    return self._result(
                        False, "Сервис отвечает некорректно. Попробуй позже или спроси в чате.",
                        "junk", total_attempts, 200,
                    )

                # Пустой ответ
                return self._result(
                    False, self._fallback_message(), "пустой ответ модели",
                    total_attempts, 200,
                )

            # Выполнить tools
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
                    "type": "function_call_output", "call_id": call_id, "output": output,
                })

        return self._result(
            False, self._fallback_message(), "превышено число rounds",
            total_attempts, 200,
        )

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

    def _friendly_error(self, code):
        if code in (401, 403):
            return "Неверный API-ключ (ANYMODEL_API_KEY)."
        if code == 402:
            return "Исчерпан баланс anymodel."
        if code == 429:
            return "Слишком много запросов (429)."
        if code in (500, 502, 503, 504):
            return "Сервер anymodel.org временно недоступен."
        return f"Ошибка HTTP {code}."

    def _fallback_message(self):
        return (
            "Джарвис: сервис временно не отвечает. Повтори позже."
        )

    @staticmethod
    def _result(ok, answer, error, attempts, status):
        return {"ok": ok, "answer": answer, "error": error, "attempts": attempts, "status": status}
