#!/usr/bin/env python3
"""Джарвис — гибридный ИИ-ассистент. Точка входа.

Режимы:
  python main.py                          интерактивный REPL
  python main.py ask "вопрос"             один запрос (можно запускать в фоне)
  python main.py "открой терминал"        одна локальная команда
  python main.py --daemon queue.txt       фоновый режим (читает очередь из файла)
"""
import argparse
import datetime
import os
import time

from utils.env import load_env
from utils.logger import get_logger

load_env()

from core.config import load_settings  # noqa: E402
from core.llm_client import LLMClient  # noqa: E402
from core.router import Router  # noqa: E402
from core.system_mac import MacSystem  # noqa: E402
from core.voice.tts import speak  # noqa: E402
from utils.simple_math import evaluate_math  # noqa: E402

log = get_logger("main")

SYSTEM_PROMPT = (
    "Ты — Джарвис, персональный ИИ-ассистент на компьютере пользователя. "
    "Отвечай по-русски и ОЧЕНЬ кратко: одно короткое предложение или несколько слов. "
    "Без вступлений, без списков и без лишних пояснений, если пользователь явно не просит подробности."
)

HELP_TEXT = (
    "Я умею (локально, без нейросети):\n"
    "- «открой Safari» / «запусти хром» / «включи музыку» — открыть приложение\n"
    "- «закрой Discord» / «выключи Telegram» — закрыть приложение\n"
    "- «открой файл отчёт» / «открой report.pdf» — найти и открыть файл\n"
    "- «открой сайт github.com» / «перейди на youtube.com» — открыть сайт\n"
    "- «загугли погода в кишинёве» — поиск в интернете\n"
    "- «громче» / «тише» / «выключи звук» / «включи звук»\n"
    "- «ярче» / «темнее» / «скриншот» / «заблокируй экран» / «спи»\n"
    "- «который час» / «какое сегодня число» / «сколько будет 12*7»\n"
    "Любой другой вопрос уходит нейросети (Slow-Path)."
)


def dispatch(text, llm, router, system, settings, history=None, voice=True):
    """Fast-Path → локально, иначе Slow-Path → LLM. Возвращает текст ответа."""
    result = router.route(text)

    if not result["matched"]:
        log.info("slow-path: %s", text)
        res = llm.ask(text, system_prompt=SYSTEM_PROMPT, history=history)
        answer = res["answer"] or res["error"] or "Нет ответа."
        if res["ok"]:
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": answer})
            del history[:-2]  # храним контекст 2 предыдущих сообщений (вопрос+ответ)
            if voice and settings.get("tts_enabled"):
                speak(answer)
        return answer

    intent = result["intent"]
    if intent == Router.INTENT_EXIT:
        return None
    if intent == Router.INTENT_HELP:
        return HELP_TEXT

    # Ответы без обращения к системе
    if intent == Router.INTENT_TIME:
        return "Сейчас " + datetime.datetime.now().strftime("%H:%M") + "."
    if intent == Router.INTENT_DATE:
        return "Сегодня " + datetime.datetime.now().strftime("%d.%m.%Y") + "."
    if intent == Router.INTENT_MATH:
        value = evaluate_math(result.get("expression", ""))
        if value is None:
            return "Не смог посчитать «%s» — проверь выражение." % result.get("expression", "")
        return "Результат: %s." % value

    # Действия через system
    ok = False
    detail = ""
    if intent == Router.INTENT_OPEN_APP:
        detail = result.get("target", "")
        ok = system.open_application(detail)
    elif intent == Router.INTENT_CLOSE_APP:
        detail = result.get("target", "")
        ok = system.close_application(detail)
    elif intent == Router.INTENT_OPEN_FILE:
        detail = result.get("target", "")
        ok = system.open_file(detail)
    elif intent == Router.INTENT_OPEN_URL:
        detail = result.get("target", "")
        ok = system.open_url(detail)
    elif intent == Router.INTENT_WEB_SEARCH:
        from urllib.parse import quote
        detail = "https://www.google.com/search?q=" + quote(result.get("query", ""))
        ok = system.open_url(detail)
    elif intent == Router.INTENT_VOLUME_UP:
        ok = system.volume_up()
    elif intent == Router.INTENT_VOLUME_DOWN:
        ok = system.volume_down()
    elif intent == Router.INTENT_MUTE:
        ok = system.mute()
    elif intent == Router.INTENT_UNMUTE:
        ok = system.unmute()
    elif intent == Router.INTENT_BRIGHTNESS_UP:
        ok = system.brightness_up()
    elif intent == Router.INTENT_BRIGHTNESS_DOWN:
        ok = system.brightness_down()
    elif intent == Router.INTENT_SCREENSHOT:
        path = system.screenshot()
        if path:
            return "Скриншот сохранён: %s" % path
        return "Не удалось сделать скриншот."
    elif intent == Router.INTENT_LOCK:
        ok = system.lock_screen()
    elif intent == Router.INTENT_SLEEP:
        ok = system.sleep()
    elif intent == Router.INTENT_TYPE_TEXT:
        ok = system.type_text(result.get("text", ""))
        detail = result.get("text", "")

    if ok:
        return "Готово: %s." % detail if detail else "Готово."
    return "Не удалось выполнить «%s». Проверь название." % (detail or intent)


def run_repl(llm, router, system, settings, voice):
    history = []
    print("Джарвис запущен. Вводи команды (для выхода: «стоп» или Ctrl+D).")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        answer = dispatch(text, llm, router, system, settings, history=history, voice=voice)
        if answer is None:
            print("Пока!")
            break
        print(answer)


def run_once(text, llm, router, system, settings, voice):
    answer = dispatch(text, llm, router, system, settings, history=[], voice=voice)
    if answer is not None:
        print(answer)


def run_daemon(queue_path, llm, router, system, settings, voice):
    history = []
    answers_path = queue_path + ".answers"
    log.info("daemon: очередь=%s ответы=%s", queue_path, answers_path)
    seen = 0
    while True:
        try:
            if not os.path.exists(queue_path):
                time.sleep(1.0)
                continue
            with open(queue_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) <= seen:
                time.sleep(1.0)
                continue
            new_lines = [line.rstrip("\n") for line in lines[seen:]]
            seen = len(lines)
            for line in new_lines:
                line = line.strip()
                if not line:
                    continue
                answer = dispatch(line, llm, router, system, settings, history=history, voice=voice)
                with open(answers_path, "a", encoding="utf-8") as f:
                    f.write("Q: %s\nA: %s\n\n" % (line, answer))
        except Exception:
            log.exception("daemon error")
            time.sleep(2.0)


def build_parser():
    parser = argparse.ArgumentParser(description="Джарвис — гибридный ИИ-ассистент")
    parser.add_argument("text", nargs="*", help="команда или вопрос (выполнит и завершит)")
    parser.add_argument("--ask", metavar="ВОПРОС", help="задать вопрос нейросети и выйти")
    parser.add_argument("--daemon", metavar="QUEUE_FILE", help="фоновый режим: очередь из файла")
    parser.add_argument("--no-voice", action="store_true", help="не озвучивать ответы")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    settings = load_settings()
    llm = LLMClient()
    router = Router()
    system = MacSystem()
    voice = not args.no_voice

    if args.daemon:
        run_daemon(args.daemon, llm, router, system, settings, voice)
        return

    text = " ".join(args.text)
    if args.ask:
        text = args.ask

    if text:
        run_once(text, llm, router, system, settings, voice)
    else:
        run_repl(llm, router, system, settings, voice)


if __name__ == "__main__":
    main()
