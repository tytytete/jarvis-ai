import json
import os
import time
import urllib.error
import urllib.request

from utils.logger import get_logger

log = get_logger("llm")

RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class LLMClient:
    """Slow-Path: DeepSeek через anymodel.org.

    Устойчив к «502 Bad Gateway», таймаутам и обрывам соединения:
    повторяет запрос с экспоненциальной задержкой и не роняет программу.
    """

    def __init__(self, base_url=None, api_key=None, model=None, timeout=None, max_retries=None):
        self.base_url = (
            base_url or os.getenv("ANYMODEL_BASE_URL", "https://anymodel.org/v1")
        ).rstrip("/")
        self.api_key = api_key or os.getenv("ANYMODEL_API_KEY", "")
        self.model = model or os.getenv("ANYMODEL_MODEL", "ds/deepseek-v4-flash")
        self.timeout = int(timeout or os.getenv("LLM_TIMEOUT", "45"))
        self.max_retries = int(max_retries or os.getenv("LLM_MAX_RETRIES", "4"))

    def ask(self, question, system_prompt=None):
        """Возвращает dict: {ok, answer, error, attempts, status}."""
        if not self.api_key:
            return self._result(
                False, "", "API-ключ не задан. Добавь его в .env (ANYMODEL_API_KEY).", 0, None
            )

        payload = {"model": self.model, "input": question}
        if system_prompt:
            payload["instructions"] = system_prompt

        url = self.base_url + "/responses"
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error = "неизвестная ошибка"
        for attempt in range(self.max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8", "replace")
                    answer = self._parse(json.loads(body))
                    if answer:
                        return self._result(True, answer, None, attempt + 1, resp.status)
                    last_error = "модель вернула пустой ответ"
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}: {e.reason}"
                if e.code not in RETRYABLE_HTTP:
                    return self._result(
                        False, "", self._friendly_error(e.code), attempt + 1, e.code
                    )
            except urllib.error.URLError as e:
                last_error = f"сеть/шлюз: {e.reason}"
            except (TimeoutError, OSError) as e:
                last_error = f"таймаут/соединение: {e}"
            except (json.JSONDecodeError, ValueError) as e:
                last_error = f"некорректный ответ: {e}"

            if attempt < self.max_retries:
                delay = min(2 ** attempt, 8)
                log.warning(
                    "попытка %s/%s не удалась (%s), повтор через %ss",
                    attempt + 1, self.max_retries + 1, last_error, delay,
                )
                time.sleep(delay)

        return self._result(
            False, self._fallback_message(), last_error, self.max_retries + 1, None
        )

    def _parse(self, data):
        # Responses API: output[].content[].text
        items = data.get("output", []) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and item.get("type") == "message":
                for chunk in item.get("content", []):
                    if isinstance(chunk, dict) and chunk.get("type") in ("output_text", "text"):
                        text = chunk.get("text", "")
                        if text.strip():
                            return text.strip()
        # Chat Completions API
        if isinstance(data, dict) and "choices" in data:
            try:
                return data["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError, TypeError, AttributeError):
                pass
        # Простое поле text
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            return data["text"].strip()
        return ""

    def _friendly_error(self, code):
        if code in (401, 403):
            return "Неверный API-ключ или нет доступа. Проверь ANYMODEL_API_KEY в .env."
        if code == 402:
            return "Исчерпан баланс/лимит аккаунта anymodel."
        if code == 429:
            return "Слишком много запросов (429). Подожди и повтори."
        if code in (500, 502, 503, 504):
            return "Сервер нейросети временно недоступен (гейтвей)."
        return f"Ошибка API: HTTP {code}."

    def _fallback_message(self):
        return (
            "Джарвис: не удалось достучаться до нейросети — сервер временно недоступен. "
            "Я не упал и продолжаю выполнять локальные команды. Повтори вопрос через несколько секунд."
        )

    @staticmethod
    def _result(ok, answer, error, attempts, status):
        return {"ok": ok, "answer": answer, "error": error, "attempts": attempts, "status": status}
