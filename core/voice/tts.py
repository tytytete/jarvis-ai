"""Text-to-Speech.

Основной движок — XTTS v2 (coqui-tts) с клонированием голоса из `voice.mp3`.
Если XTTS/зависимости недоступны или модель не загрузилась — фолбэк на macOS `say`.
Работает асинхронно: `speak()` возвращается сразу, синтез идёт в фоновом потоке.
"""
import os
import shutil
import subprocess
import tempfile
import threading

from utils.logger import get_logger

log = get_logger("tts")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_VOICE = os.path.join(ROOT_DIR, "voice.mp3")

# Кэш моделей XTTS и matplotlib — в локальную папку проекта (без прав на системные каталоги).
_CACHE_DIR = os.path.join(ROOT_DIR, ".cache")
os.environ.setdefault("TTS_HOME", os.path.join(_CACHE_DIR, "tts"))
os.environ.setdefault("MPLCONFIGDIR", os.path.join(_CACHE_DIR, "mpl"))
# XTTS-v2 требует согласия с лицензией Coqui (CPML); при первом скачивании
# не блокируемся на интерактивном вопросе.
os.environ.setdefault("COQUI_TOS_AGREED", "1")

_engine = None
_engine_failed = False
_engine_lock = threading.Lock()


def _load_settings():
    from core.config import load_settings
    return load_settings()


class _XttsEngine:
    """Лениво загружаемый XTTS-v2 движок."""

    def __init__(self):
        self._tts = None
        self._device = None

    def _ensure(self):
        if self._tts is not None:
            return True
        try:
            import torch  # noqa: F401
            from TTS.api import TTS
        except Exception as exc:
            log.warning("XTTS: нет torch/coqui-tts — %s", exc)
            return False
        try:
            # Вокодер XTTS падает на MPS («Output channels > 65536 not supported»),
            # поэтому стабильно работаем на CPU.
            device = "cpu"
            self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
            self._device = device
            log.info("XTTS: модель загружена (device=%s)", device)
            return True
        except Exception as exc:
            log.warning("XTTS: не удалось загрузить модель — %s", exc)
            self._tts = None
            return False

    def synthesize(self, text, out_path, reference_voice, language):
        if not self._ensure():
            return False
        try:
            kwargs = {"text": text, "language": language, "file_path": out_path}
            if reference_voice and os.path.exists(reference_voice):
                kwargs["speaker_wav"] = reference_voice
            self._tts.tts_to_file(**kwargs)
            return os.path.exists(out_path) and os.path.getsize(out_path) > 0
        except Exception as exc:
            log.warning("XTTS: ошибка синтеза — %s", exc)
            return False


def _get_engine():
    global _engine, _engine_failed
    with _engine_lock:
        if _engine is None and not _engine_failed:
            candidate = _XttsEngine()
            if candidate._ensure():
                _engine = candidate
            else:
                _engine_failed = True
    return _engine


def _say(text):
    if shutil.which("say") is None:
        return False
    try:
        subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def _play(path):
    if shutil.which("afplay") is None:
        return False
    try:
        subprocess.run(
            ["afplay", path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _speak_worker(text):
    settings = _load_settings()
    engine_name = (settings.get("tts_engine") or "auto").lower()
    reference = settings.get("tts_reference_voice") or DEFAULT_VOICE
    language = settings.get("tts_language") or "ru"

    if engine_name == "say":
        _say(text)
        return

    engine = _get_engine()
    if engine is not None:
        fd, out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            if engine.synthesize(text, out_path, reference, language):
                _play(out_path)
                return
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass

    if engine_name == "xtts":
        log.warning("XTTS недоступен (tts_engine=xtts) — озвучка пропущена")
        return
    _say(text)


def speak(text, block=False):
    """Озвучка текста голосом Джарвиса (XTTS) или системным голосом (фолбэк)."""
    if not text:
        return False
    thread = threading.Thread(target=_speak_worker, args=(text,), daemon=True)
    thread.start()
    if block:
        thread.join()
    return True
