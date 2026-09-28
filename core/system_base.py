from abc import ABC, abstractmethod


class BaseSystem(ABC):
    """Абстракция OS-интерфейса: сейчас macOS, Windows — следующим этапом."""

    platform = "unknown"

    @abstractmethod
    def open_application(self, name: str) -> bool:
        ...

    @abstractmethod
    def search_application(self, name: str):
        ...

    @abstractmethod
    def open_file(self, query: str) -> bool:
        ...

    @abstractmethod
    def search_files(self, query: str, limit: int = 5):
        ...

    @abstractmethod
    def volume_up(self, step: int = 10) -> bool:
        ...

    @abstractmethod
    def volume_down(self, step: int = 10) -> bool:
        ...

    @abstractmethod
    def mute(self) -> bool:
        ...
