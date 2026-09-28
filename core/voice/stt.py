"""Speech-to-Text.

На текущем этапе (MVP) голосовой ввод не обязателен. План — Faster-Whisper
(модель base/small) с wake-word «Джарвис» и Push-to-Talk (см. PROJECT_SPEC.md).
"""


def listen(timeout=5):
    raise NotImplementedError("Голосовой ввод (STT) подключим на следующем этапе.")
