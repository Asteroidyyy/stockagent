from abc import ABC, abstractmethod
from typing import Any


class MarketDataProvider(ABC):
    @abstractmethod
    def fetch_market_snapshot(
        self,
        symbols: list[str],
        *,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError


class MarketDataError(RuntimeError):
    """Raised when a market data provider cannot produce a usable snapshot."""
