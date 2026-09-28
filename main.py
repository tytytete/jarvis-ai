#!/usr/bin/env python3
"""Джарвис — гибридный ИИ-ассистент. Точка входа.

Режимы:
  python main.py                          интерактивный REPL
  python main.py ask "вопрос"             один запрос (можно запускать в фоне)
  python main.py "открой терминал"        одна локальная команда
  python main.py --daemon queue.txt       фоновый режим (читает очередь из файла)
"""
import argparse
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

log = get_logger("main")

SYSTEM_PROMPT = (
    "Ты — Джарвис, персональный ИИ-ассистент на компьютере пользователя. "
    "Отвечай кратко, по-русски и по делу."
)


def dispatch(text, llm, router, system, settings, voice=True):
    """Fast-Path → локально, иначе Slow-Path → LLM. Возвращает текст ответа."""
    result = router.route(text)

    if not result["matched"]:
        log.info("slow-path: %s", text)
        res = llm.ask(text, system_prompt=SYSTEM_PROMPT)
        answer = res["answer"] or res["error"] or "Нет ответа."
        if res["ok"] and voice and settings.get("tts_enabled"):
            speak(answer)
        return answer

    intent = result["intent"]
    if intent == Router.INTENT_EXIT:
        return None

    if intent == Router.INTENT_HELP:
        return (
            "Я умею (локально, без нейросети):\n"
            "- «открой Safari» / «запусти терминал» — открыть приложение\n"
            "- «открой файл отчёт» — найти и открыть файл\n"
            "- «громче» / «тише» / «выключи звук»\n"
            "Любой другой вопрос уходит нейросети (Slow-Path)."
        )

    target = result.get("target", "")
    ok = False
    if intent == Router.INTENT_OPEN_APP:
        ok = system.open_application(target)
    elif intent == Router.INTENT_OPEN_FILE:
        ok = system.open_file(target)
    elif intent == Router.INTENT_VOLUME_UP:
        ok = system.volume_up()
    elif intent == Router.INTENT_VOLUME_DOWN:
        ok = system.volume_down()
    elif intent == Router.INTENT_MUTE:
        ok = system.mute()

    if ok:
        return f"Готово: {target or intent}."
    return f"Не удалось выполнить «{target or intent}». Проверь название."


def run_repl(llm, router, system, settings, voice):
    print("Джарвис запущен. Вводи команды (для выхода: «стоп» или Ctrl+D).")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        answer = dispatch(text, llm, router, system, settings, voice=voice)
        if answer is None:
            print("Пока!")
            break
        print(answer)


def run_once(text, llm, router, system, settings, voice):
    answer = dispatch(text, llm, router, system, settings, voice=voice)
    if answer is not None:
        print(answer)


def run_daemon(queue_path, llm, router, system, settings, voice):
    """Фоновый режим: читает новые строки из очереди, пишет ответы в <очередь>.answers."""
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
                answer = dispatch(line, llm, router, system, settings, voice=voice)
                with open(answers_path, "a", encoding="utf-8") as f:
                    f.write(f"Q: {line}\nA: {answer}\n\n")
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
