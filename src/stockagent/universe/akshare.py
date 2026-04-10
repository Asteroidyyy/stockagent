from __future__ import annotations

import pandas as pd

from stockagent.data.base import MarketDataError
from stockagent.universe.base import UniverseLoader

try:
    import akshare as ak
except ImportError:  # pragma: no cover - depends on local environment
    ak = None


class AkshareCsi500UniverseLoader(UniverseLoader):
    """Loads CSI 500 constituents from CSIndex via AkShare."""

    INDEX_CODE = "000905"

    def load_symbols(self, limit: int | None = None) -> list[str]:
        if ak is None:
            raise MarketDataError("AkShare is not installed. Install project dependencies first.")

        try:
            frame = ak.index_stock_cons_csindex(symbol=self.INDEX_CODE)
        except Exception as exc:  # pragma: no cover - depends on remote data
            raise MarketDataError(f"failed to load CSI 500 constituents: {exc}") from exc

        if frame is None or frame.empty:
            raise MarketDataError("CSI 500 constituent list is empty")

        symbols = self._normalize(frame)
        if limit is not None:
            return symbols[:limit]
        return symbols

    def _normalize(self, frame: pd.DataFrame) -> list[str]:
        code_column = "成分券代码"
        exchange_column = "交易所"
        missing = [column for column in [code_column, exchange_column] if column not in frame.columns]
        if missing:
            raise MarketDataError(
                f"unexpected CSI 500 constituent columns: missing {', '.join(missing)}"
            )

        symbols: list[str] = []
        seen: set[str] = set()
        for _, row in frame.iterrows():
            raw_code = str(row[code_column]).strip().zfill(6)
            exchange = self._map_exchange(str(row[exchange_column]).strip())
            symbol = f"{raw_code}.{exchange}"
            if symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)

        if not symbols:
            raise MarketDataError("CSI 500 constituent normalization returned no symbols")
        return symbols

    def _map_exchange(self, exchange_name: str) -> str:
        if "深圳" in exchange_name:
            return "SZ"
        if "上海" in exchange_name:
            return "SH"
        if "北京" in exchange_name:
            return "BJ"
        raise MarketDataError(f"unsupported exchange name: {exchange_name}")
