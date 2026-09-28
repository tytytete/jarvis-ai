from .system_base import BaseSystem


class WinSystem(BaseSystem):
    """Windows 10 x64 — реализуем следующим этапом (см. PROJECT_SPEC.md)."""

    platform = "windows"

    def _unsupported(self):
        raise NotImplementedError(
            "Windows-реализация ещё не собрана. Сейчас активен только macOS-слой."
        )

    def open_application(self, name):  # pragma: no cover
        self._unsupported()

    def search_application(self, name):  # pragma: no cover
        self._unsupported()

    def open_file(self, query):  # pragma: no cover
        self._unsupported()

    def search_files(self, query, limit=5):  # pragma: no cover
        self._unsupported()

    def volume_up(self, step=10):  # pragma: no cover
        self._unsupported()

    def volume_down(self, step=10):  # pragma: no cover
        self._unsupported()

    def mute(self):  # pragma: no cover
        self._unsupported()
