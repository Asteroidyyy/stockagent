from __future__ import annotations

from abc import ABC, abstractmethod


class EventProvider(ABC):
    @abstractmethod
    def fetch_events(
        self,
        symbols: list[str],
        *,
        as_of_date: str | None = None,
    ) -> dict[str, list[str]]:
        raise NotImplementedError
