import shutil
import subprocess


def speak(text, block=False):
    """Озвучка через встроенный macOS `say`. Работает асинхронно (не блокирует поток)."""
    if not text or shutil.which("say") is None:
        return False
    try:
        proc = subprocess.Popen(
            ["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if block:
            proc.wait()
        return True
    except OSError:
        return False
