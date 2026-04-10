from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from stockagent.events.base import EventProvider
from stockagent.events.normalizer import normalize_notice
from stockagent.utils.cache import JsonCache

try:
    import akshare as ak
except ImportError:  # pragma: no cover - depends on local environment
    ak = None


class AkshareEventProvider(EventProvider):
    """Fetch recent A-share notices and normalize them into event tags."""

    NOTICE_TYPES = ["风险提示", "重大事项", "持股变动", "融资公告", "财务报告", "信息变更"]

    def __init__(self, *, lookback_days: int = 3) -> None:
        self.lookback_days = max(1, lookback_days)
        self.cache = JsonCache("events")

    def fetch_events(
        self,
        symbols: list[str],
        *,
        as_of_date: str | None = None,
    ) -> dict[str, list[str]]:
        if ak is None:
            raise RuntimeError("AkShare is not installed. Install project dependencies first.")

        codes = {self._normalize_symbol(symbol): symbol for symbol in symbols}
        if not codes:
            return {}

        merged: dict[str, list[str]] = {symbol: [] for symbol in symbols}
        frames = self._fetch_notice_frames(as_of_date=as_of_date)
        if not frames:
            cached = self.cache.load(self._cache_key(as_of_date))
            if cached is not None:
                return {symbol: cached.get(symbol, ["无重大风险"]) for symbol in symbols}
            return {symbol: ["无重大风险"] for symbol in symbols}

        notices = pd.concat(frames, ignore_index=True)
        if notices.empty:
            return {symbol: ["无重大风险"] for symbol in symbols}

        notices["代码"] = notices["代码"].astype(str).str.zfill(6)
        filtered = notices[notices["代码"].isin(codes.keys())].copy()
        if filtered.empty:
            return {symbol: ["无重大风险"] for symbol in symbols}

        filtered["事件标签"] = filtered.apply(
            lambda row: normalize_notice(
                title=str(row.get("公告标题", "")),
                notice_type=str(row.get("公告类型", "")),
            ),
            axis=1,
        )

        for code, group in filtered.groupby("代码"):
            symbol = codes[code]
            labels = list(dict.fromkeys(group["事件标签"].tolist()))
            merged[symbol] = labels[:5]

        for symbol, labels in merged.items():
            if not labels:
                merged[symbol] = ["无重大风险"]

        self.cache.save(self._cache_key(as_of_date), merged)
        return merged

    def _fetch_notice_frames(self, *, as_of_date: str | None = None) -> list[pd.DataFrame]:
        frames: list[pd.DataFrame] = []
        anchor_date = date.fromisoformat(as_of_date) if as_of_date else date.today()
        for offset in range(self.lookback_days):
            trade_date = (anchor_date - timedelta(days=offset)).strftime("%Y%m%d")
            for notice_type in self.NOTICE_TYPES:
                try:
                    frame = ak.stock_notice_report(symbol=notice_type, date=trade_date)
                except Exception:
                    continue
                if frame is None or frame.empty:
                    continue
                frames.append(frame)
        return frames

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.split(".", maxsplit=1)[0].zfill(6)

    def _cache_key(self, as_of_date: str | None = None) -> str:
        return f"events_{as_of_date or date.today().isoformat()}"
