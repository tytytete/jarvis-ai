"""STT: офлайн-распознавание речи через Vosk с wake-word «Джарвис».

Схема (как Siri): постоянно ждём wake-word лёгким распознаванием, после
срабатывания слушаем саму команду. Модель — vosk-model-small-ru-0.22 (~45 МБ).
"""
import json
import os
import queue
import time

from utils.logger import get_logger

log = get_logger("stt")

WAKE_WORDS = ("джарвис", "жарвис", "jarvis")

_model = None
_model_lock = None  # задаётся ниже


def _default_model_path():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from core.config import load_settings
        configured = load_settings().get("stt_model_path")
        if configured:
            return os.path.expanduser(os.path.join(root, configured))
    except Exception:
        pass
    return os.path.join(root, ".cache", "vosk-model-small-ru-0.22")


def get_model():
    global _model
    import threading
    global _model_lock
    if _model_lock is None:
        _model_lock = threading.Lock()
    with _model_lock:
        if _model is None:
            from vosk import Model
            path = _default_model_path()
            if not os.path.isdir(path):
                raise FileNotFoundError(
                    "Модель Vosk не найдена: %s. Скачай vosk-model-small-ru-0.22." % path
                )
            _model = Model(path)
    return _model


def wake_position(text):
    """Индекс символа сразу после wake-word, или -1."""
    low = (text or "").lower()
    best = -1
    for w in WAKE_WORDS:
        pos = low.find(w)
        if pos != -1 and (best == -1 or pos < best):
            best = pos + len(w)
    return best


def has_wake_word(text):
    return wake_position(text) != -1


def command_after_wake(text):
    pos = wake_position(text)
    if pos == -1:
        return None
    return text[pos:].strip()


class VoiceListener:
    def __init__(self, sample_rate=16000, block_size=4000):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.model = get_model()

    def _new_stream(self, callback):
        import sounddevice as sd
        return sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            dtype="int16",
            channels=1,
            callback=callback,
        )

    def _recognizer(self):
        from vosk import KaldiRecognizer
        return KaldiRecognizer(self.model, self.sample_rate)

    def listen_for_wake(self, timeout=None):
        """Ждёт wake-word. Возвращает текст команды, сказанной сразу после него (или '')."""
        rec = self._recognizer()
        q = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        started = time.time()
        try:
            with self._new_stream(callback):
                while True:
                    try:
                        data = q.get(timeout=0.2)
                    except queue.Empty:
                        data = None

                    if timeout is not None and (time.time() - started) > timeout:
                        return None
                    if data is None:
                        continue

                    accepted = rec.AcceptWaveform(data)
                    text = (
                        json.loads(rec.Result()).get("text", "")
                        if accepted
                        else json.loads(rec.PartialResult()).get("partial", "")
                    ).strip()

                    # ждём финального высказывания с wake-word, чтобы взять полный хвост
                    if accepted and has_wake_word(text):
                        return command_after_wake(text) or ""
        except Exception as exc:
            log.warning("listen_for_wake: %s", exc)
            return None

    def listen_for_command(self, initial="", timeout=8.0, silence=1.5):
        """Слушает команду после пробуждения. Возвращает текст или None."""
        rec = self._recognizer()
        q = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        command = (initial or "").strip()
        last_speech = time.time() if command else None
        started = time.time()
        try:
            with self._new_stream(callback):
                while True:
                    try:
                        data = q.get(timeout=0.2)
                    except queue.Empty:
                        data = None

                    now = time.time()
                    if now - started > timeout:
                        break
                    if last_speech is not None and (now - last_speech) > silence:
                        break
                    if data is None:
                        continue

                    accepted = rec.AcceptWaveform(data)
                    text = (
                        json.loads(rec.Result()).get("text", "")
                        if accepted
                        else json.loads(rec.PartialResult()).get("partial", "")
                    ).strip()

                    if text:
                        if accepted:
                            command = (command + " " + text).strip()
                        last_speech = now
        except Exception as exc:
            log.warning("listen_for_command: %s", exc)
        return command.strip() or None

    def listen_once(self):
        """Wake-word → команда. Возвращает текст команды или None."""
        tail = self.listen_for_wake()
        if tail is None:
            return None
        if tail:
            return tail
        return self.listen_for_command()
