from __future__ import annotations

from stockagent.events.base import EventProvider


class MockEventProvider(EventProvider):
    """Deterministic local event provider for development and demos."""

    DEFAULT_EVENTS = {
        "600519.SH": ["无重大风险"],
        "300750.SZ": ["机构调研活跃", "无重大风险"],
        "000001.SZ": ["业绩预增预告"],
        "601012.SH": ["行业景气度承压", "减持预披露风险"],
    }

    def fetch_events(
        self,
        symbols: list[str],
        *,
        as_of_date: str | None = None,
    ) -> dict[str, list[str]]:
        return {
            symbol: list(self.DEFAULT_EVENTS.get(symbol, ["无重大风险"]))
            for symbol in symbols
        }
