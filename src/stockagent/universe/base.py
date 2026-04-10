from abc import ABC, abstractmethod


class UniverseLoader(ABC):
    @abstractmethod
    def load_symbols(self, limit: int | None = None) -> list[str]:
        raise NotImplementedError
