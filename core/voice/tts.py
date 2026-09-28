"""Text-to-Speech.

Движки (выбираются через settings.json → tts_engine):
  - edge  — Microsoft Edge TTS (быстрый нейросетевой голос, ~0.5 с, нужен интернет);
  - say   — системный macOS `say` (мгновенно, офлайн);
  - xtts  — XTTS v2 с клонированием из voice.mp3 (медленно на CPU, ~5-10 с).

По умолчанию `auto` = edge → фолбэк на say. Озвучка идёт в фоновом потоке.
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
DEFAULT_EDGE_VOICE = "ru-RU-DmitryNeural"

_CACHE_DIR = os.path.join(ROOT_DIR, ".cache")
os.environ.setdefault("TTS_HOME", os.path.join(_CACHE_DIR, "tts"))
os.environ.setdefault("MPLCONFIGDIR", os.path.join(_CACHE_DIR, "mpl"))
os.environ.setdefault("COQUI_TOS_AGREED", "1")

_engine = None
_engine_failed = False
_engine_lock = threading.Lock()


def _load_settings():
    from core.config import load_settings
    return load_settings()


# --- macOS say ---------------------------------------------------------------

def _say(text):
    if shutil.which("say") is None:
        return False
    try:
        subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# --- Microsoft Edge TTS ------------------------------------------------------

def _edge_synthesize(text, voice, out_path):
    import asyncio
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, voice).save(out_path)

    asyncio.run(go())


# --- XTTS v2 (медленный, для клонированного голоса) -------------------------

class _XttsEngine:
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


# --- воспроизведение --------------------------------------------------------

def _play(path):
    if shutil.which("afplay") is None:
        return False
    try:
        subprocess.run(
            ["afplay", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _synth_to_temp(text, settings):
    """Синтезирует текст во временный файл. Возвращает путь или None."""
    fd, out_path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        voice = settings.get("tts_edge_voice") or DEFAULT_EDGE_VOICE
        _edge_synthesize(text, voice, out_path)
        return out_path if os.path.getsize(out_path) > 0 else None
    except Exception as exc:
        log.warning("edge-tts недоступен: %s", exc)
        try:
            os.remove(out_path)
        except OSError:
            pass
        return None


def _speak_worker(text):
    settings = _load_settings()
    engine = (settings.get("tts_engine") or "say").lower()

    # 1) edge — нейросетевой голос (opt-in), иначе фолбэк на say
    if engine == "edge":
        out_path = _synth_to_temp(text, settings)
        if out_path:
            try:
                _play(out_path)
                return
            finally:
                try:
                    os.remove(out_path)
                except OSError:
                    pass
        _say(text)
        return

    # 2) xtts — клонированный голос (opt-in, медленный), иначе say
    if engine == "xtts":
        ref = settings.get("tts_reference_voice") or DEFAULT_VOICE
        language = settings.get("tts_language") or "ru"
        eng = _get_engine()
        if eng is not None:
            fd, out_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                if eng.synthesize(text, out_path, ref, language):
                    _play(out_path)
                    return
            finally:
                try:
                    os.remove(out_path)
                except OSError:
                    pass
        log.warning("XTTS недоступен — фолбэк на say")
        _say(text)
        return

    # 3) say / auto — системный голос (мгновенно, офлайн)
    _say(text)


def speak(text, block=False):
    """Озвучивает текст выбранным движком (не блокирует главный поток)."""
    if not text:
        return False
    thread = threading.Thread(target=_speak_worker, args=(text,), daemon=True)
    thread.start()
    if block:
        thread.join()
    return True


def preload():
    """Предзагрузка нужна только для медленного XTTS; для edge/say — не требуется."""
    settings = _load_settings()
    if (settings.get("tts_engine") or "say").lower() == "xtts":
        threading.Thread(target=_get_engine, daemon=True).start()
        return True
    return False
