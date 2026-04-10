from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from stockagent.config import get_settings
from stockagent.data.base import MarketDataError, MarketDataProvider
from stockagent.utils.cache import JsonCache

try:
    import tushare as ts
except ImportError:  # pragma: no cover - optional runtime dependency
    ts = None


class TushareMarketDataProvider(MarketDataProvider):
    INDEX_CODES = {
        "上证指数": "000001.SH",
        "深证成指": "399001.SZ",
        "创业板指": "399006.SZ",
    }

    def __init__(self) -> None:
        settings = get_settings()
        self.token = settings.tushare_token
        self.cache = JsonCache("market")
        self.client = None
        if ts is not None and self.token:
            self.client = ts.pro_api(self.token)

    def fetch_market_snapshot(
        self,
        symbols: list[str],
        *,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        if self.client is None:
            raise MarketDataError("Tushare is not configured. Install tushare and set TUSHARE_TOKEN.")
        if not symbols:
            raise MarketDataError("no symbols provided")

        stock_payload: dict[str, Any] = {}
        errors: list[str] = []

        for symbol in symbols:
            try:
                stock_payload[symbol] = self._fetch_stock_snapshot(symbol, as_of_date=as_of_date)
            except Exception as exc:
                cached = self.cache.load(self._cache_key(symbol, as_of_date))
                if cached is not None:
                    stock_payload[symbol] = cached
                    errors.append(f"{symbol}: live fetch failed, fallback to cache: {exc}")
                else:
                    errors.append(f"{symbol}: {exc}")

        if not stock_payload:
            raise MarketDataError(
                "Tushare did not return data for any requested symbol. "
                f"Errors: {'; '.join(errors) if errors else 'unknown'}"
            )

        average_change = sum(float(item.get("price_change", 0.0)) for item in stock_payload.values()) / len(
            stock_payload
        )
        average_trend_score = sum(float(item.get("trend_score", 50.0)) for item in stock_payload.values()) / len(
            stock_payload
        )
        breadth = sum(1 for item in stock_payload.values() if float(item.get("price_change", 0.0)) > 0) / len(
            stock_payload
        )
        index_snapshot = self._fetch_index_snapshot(as_of_date=as_of_date)
        market_regime = self._classify_market_regime(
            stock_pool_change=average_change,
            breadth=breadth,
            index_snapshot=index_snapshot,
        )
        sector_summary = self._build_market_summary_lines(
            market_regime=market_regime,
            average_change=average_change,
            breadth=breadth,
            average_trend_score=average_trend_score,
            index_snapshot=index_snapshot,
        )
        if errors:
            sector_summary.append(f"部分标的获取失败: {'; '.join(errors)}")

        snapshot = {
            "market_regime": market_regime,
            "sector_summary": sector_summary,
            "stocks": stock_payload,
            "errors": errors,
            "breadth": breadth,
            "average_trend_score": average_trend_score,
            "index_snapshot": index_snapshot,
            "board_snapshot": {"leaders": [], "laggards": []},
        }
        self.cache.save(self._snapshot_cache_key(symbols, as_of_date), snapshot)
        return snapshot

    def _fetch_stock_snapshot(self, symbol: str, *, as_of_date: str | None = None) -> dict[str, Any]:
        prepared = self._fetch_prepared_history(symbol=symbol, as_of_date=as_of_date)
        latest = prepared.iloc[-1]
        previous = prepared.iloc[-2]
        ma5 = prepared["close"].tail(5).mean()
        ma20 = prepared["close"].tail(20).mean() if len(prepared) >= 20 else prepared["close"].mean()
        pct_change_series = prepared["pct_change"] / 100
        lookback_close = float(prepared["close"].iloc[-5]) if len(prepared) >= 5 else float(prepared["close"].iloc[0])
        recent_high = float(prepared["high"].tail(20).max())
        volatility_10d = float(pct_change_series.tail(10).std()) if len(prepared) >= 10 else float(
            pct_change_series.std()
        )
        amount_5d = float(prepared["amount"].tail(5).mean()) if len(prepared) >= 5 else float(prepared["amount"].mean())
        amount_20d = (
            float(prepared["amount"].tail(20).mean())
            if len(prepared) >= 20
            else float(prepared["amount"].mean())
        )
        latest_amount = float(latest["amount"])
        amount_ratio_5d = latest_amount / amount_5d if amount_5d else 1.0
        amount_ratio_20d = latest_amount / amount_20d if amount_20d else 1.0
        turnover_rate = self._fetch_turnover_rate(symbol, latest["date"])

        event_tags = []
        if float(latest["close"]) > ma20:
            event_tags.append("收盘站上20日线")
        if float(latest["pct_change"]) <= -5:
            event_tags.append("单日跌幅较大")
        if float(latest["amount"]) > 0:
            event_tags.append(f"最新成交额={float(latest['amount']) / 1e5:.2f}亿")

        snapshot = {
            "name": self._lookup_stock_name(symbol),
            "close": float(latest["close"]),
            "price_change": float(latest["pct_change"]) / 100,
            "ma5": float(ma5),
            "ma20": float(ma20),
            "trend_score": self._score_trend(
                latest_close=float(latest["close"]),
                prev_close=float(previous["close"]),
                ma5=float(ma5),
                ma20=float(ma20),
            ),
            "momentum_5d": float(latest["close"]) / lookback_close - 1 if lookback_close else 0.0,
            "drawdown_20d": float(latest["close"]) / recent_high - 1 if recent_high else 0.0,
            "volatility_10d": volatility_10d if pd.notna(volatility_10d) else 0.0,
            "turnover_rate": turnover_rate,
            "amount_ratio_5d": amount_ratio_5d if pd.notna(amount_ratio_5d) else 1.0,
            "amount_ratio_20d": amount_ratio_20d if pd.notna(amount_ratio_20d) else 1.0,
            "event_tags": event_tags or ["无重大风险"],
        }
        self.cache.save(self._cache_key(symbol, as_of_date), snapshot)
        return snapshot

    def _fetch_turnover_rate(self, symbol: str, trade_date: pd.Timestamp) -> float:
        cache_key = f"turnover_{symbol}_{trade_date.strftime('%Y-%m-%d')}"
        cached = self.cache.load(cache_key)
        if cached is not None:
            return float(cached)
        try:
            frame = self.client.daily_basic(
                ts_code=symbol,
                trade_date=trade_date.strftime("%Y%m%d"),
                fields="ts_code,trade_date,turnover_rate",
            )
            if frame is None or frame.empty:
                return 0.0
            turnover_rate = float(frame.iloc[0]["turnover_rate"]) / 100
            self.cache.save(cache_key, turnover_rate)
            return turnover_rate
        except Exception:
            return 0.0

    def _fetch_prepared_history(self, *, symbol: str, as_of_date: str | None = None) -> pd.DataFrame:
        end_date = (as_of_date or date.today().isoformat()).replace("-", "")
        frame = self.client.daily(ts_code=symbol, start_date="20240101", end_date=end_date)
        if frame is None or frame.empty:
            raise MarketDataError(f"Tushare returned empty history for {symbol}")

        renamed = frame.rename(
            columns={
                "trade_date": "date",
                "open": "open",
                "close": "close",
                "high": "high",
                "low": "low",
                "vol": "volume",
                "amount": "amount",
                "pct_chg": "pct_change",
            }
        )
        required = ["date", "open", "close", "high", "low", "volume", "amount", "pct_change"]
        missing = [column for column in required if column not in renamed.columns]
        if missing:
            raise MarketDataError(f"Tushare history missing columns: {', '.join(missing)}")

        prepared = renamed[required].copy()
        prepared["date"] = pd.to_datetime(prepared["date"], format="%Y%m%d", errors="coerce")
        for column in required[1:]:
            prepared[column] = pd.to_numeric(prepared[column], errors="coerce")
        prepared = prepared.dropna().sort_values("date").reset_index(drop=True)
        if len(prepared) < 2:
            raise MarketDataError(f"not enough bars returned for {symbol}")
        return prepared

    def _fetch_index_snapshot(self, *, as_of_date: str | None = None) -> dict[str, dict[str, Any]]:
        cache_key = f"indices_tushare_{as_of_date or date.today().isoformat()}"
        payload: dict[str, dict[str, Any]] = {}
        trade_date = (as_of_date or date.today().isoformat()).replace("-", "")
        try:
            for name, ts_code in self.INDEX_CODES.items():
                frame = self.client.index_daily(ts_code=ts_code, start_date=trade_date, end_date=trade_date)
                if frame is None or frame.empty:
                    continue
                row = frame.iloc[0]
                payload[name] = {
                    "price_change": float(row["pct_chg"]) / 100,
                    "close": float(row["close"]),
                }
            if payload:
                self.cache.save(cache_key, payload)
                return payload
        except Exception:
            cached = self.cache.load(cache_key)
            if cached is not None:
                return cached
        return {}

    def _lookup_stock_name(self, symbol: str) -> str | None:
        cache_key = f"stock_name_map_tushare_{date.today().isoformat()}"
        mapping = self.cache.load(cache_key) or {}
        if symbol in mapping:
            return mapping[symbol]

        try:
            frame = self.client.stock_basic(exchange="", list_status="L", fields="ts_code,name")
            if frame is None or frame.empty:
                return None
            mapping = {str(row["ts_code"]): str(row["name"]).strip() for _, row in frame.iterrows()}
            self.cache.save(cache_key, mapping)
            return mapping.get(symbol)
        except Exception:
            return None

    def _score_trend(self, *, latest_close: float, prev_close: float, ma5: float, ma20: float) -> float:
        score = 50.0
        if latest_close > ma5:
            score += 10
        if latest_close > ma20:
            score += 15
        if ma5 > ma20:
            score += 10
        if latest_close > prev_close:
            score += 5
        if latest_close < ma20 * 0.97:
            score -= 20
        return max(0.0, min(100.0, score))

    def _classify_market_regime(
        self,
        *,
        stock_pool_change: float,
        breadth: float,
        index_snapshot: dict[str, dict[str, Any]],
    ) -> str:
        index_changes = [float(item.get("price_change", 0.0)) for item in index_snapshot.values()]
        index_average = sum(index_changes) / len(index_changes) if index_changes else stock_pool_change
        combined_signal = stock_pool_change * 0.55 + index_average * 0.35 + (breadth - 0.5) * 0.1
        if combined_signal >= 0.02:
            return "强势"
        if combined_signal >= 0.005:
            return "震荡偏强"
        if combined_signal <= -0.02:
            return "弱势"
        if combined_signal <= -0.005:
            return "震荡偏弱"
        return "震荡"

    def _build_market_summary_lines(
        self,
        *,
        market_regime: str,
        average_change: float,
        breadth: float,
        average_trend_score: float,
        index_snapshot: dict[str, dict[str, Any]],
    ) -> list[str]:
        lines = [
            f"股票池平均涨跌幅 {average_change:.2%}，当前判定为 {market_regime}。",
            f"上涨家数占比 {breadth:.0%}，平均趋势评分 {average_trend_score:.1f}。",
        ]
        if index_snapshot:
            lines.append(
                "核心指数表现: "
                + "，".join(f"{name}{item['price_change']:+.2%}" for name, item in index_snapshot.items())
                + "。"
            )
        lines.append("Tushare 模式暂未接行业板块快照。")
        return lines

    def _cache_key(self, symbol: str, as_of_date: str | None = None) -> str:
        return f"tushare_{symbol}_{as_of_date or date.today().isoformat()}"

    def _snapshot_cache_key(self, symbols: list[str], as_of_date: str | None = None) -> str:
        return f"tushare_snapshot_{as_of_date or date.today().isoformat()}_{len(symbols)}"
