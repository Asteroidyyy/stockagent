from typing import Any

from stockagent.data.base import MarketDataProvider


class MockMarketDataProvider(MarketDataProvider):
    """Temporary provider used while real data connectors are added."""

    def fetch_market_snapshot(
        self,
        symbols: list[str],
        *,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        return {
            "market_regime": "震荡偏强",
            "sector_summary": [
                "人工智能和算力方向维持强势。",
                "高股息板块表现平稳。",
            ],
            "stocks": {
                symbol: {
                    "trend_score": 60 + index * 5,
                    "event_tags": ["无重大风险"],
                    "price_change": 0.01 * (index + 1),
                    "momentum_5d": 0.02 * (index + 1),
                    "drawdown_20d": -0.02 * index,
                    "volatility_10d": 0.015 + index * 0.003,
                }
                for index, symbol in enumerate(symbols)
            },
            "breadth": 0.6,
            "average_trend_score": 67.5,
        }
