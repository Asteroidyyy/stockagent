from __future__ import annotations

from datetime import date
from typing import Any
from collections.abc import Callable

import pandas as pd

from stockagent.data.base import MarketDataError, MarketDataProvider
from stockagent.utils.cache import JsonCache

try:
    import akshare as ak
except ImportError:  # pragma: no cover - depends on local environment
    ak = None


class AkshareMarketDataProvider(MarketDataProvider):
    """Fetches a lightweight daily market snapshot from AkShare."""
    INDEX_NAMES = ["上证指数", "深证成指", "创业板指"]

    def __init__(self) -> None:
        self.cache = JsonCache("market")

    def fetch_market_snapshot(
        self,
        symbols: list[str],
        *,
        as_of_date: str | None = None,
    ) -> dict[str, Any]:
        if ak is None:
            raise MarketDataError("AkShare is not installed. Install project dependencies first.")
        if not symbols:
            raise MarketDataError("no symbols provided")

        stock_payload: dict[str, Any] = {}
        sector_summary: list[str] = []
        errors: list[str] = []

        for symbol in symbols:
            try:
                stock_payload[symbol] = self._fetch_stock_snapshot(symbol, as_of_date=as_of_date)
            except Exception as exc:  # pragma: no cover - depends on remote data
                cached = self.cache.load(self._cache_key(symbol, as_of_date))
                if cached is not None:
                    stock_payload[symbol] = cached
                    errors.append(f"{symbol}: live fetch failed, fallback to cache: {exc}")
                else:
                    errors.append(f"{symbol}: {exc}")

        if not stock_payload:
            cached_snapshot = self.cache.load(self._snapshot_cache_key(symbols, as_of_date))
            if cached_snapshot is not None:
                cached_snapshot["errors"] = list(cached_snapshot.get("errors", [])) + errors
                cached_snapshot["sector_summary"] = list(cached_snapshot.get("sector_summary", [])) + [
                    "本次实时抓取失败，已回退到市场快照缓存。"
                ]
                return cached_snapshot
            raise MarketDataError(
                "AkShare did not return data for any requested symbol. "
                f"Errors: {'; '.join(errors) if errors else 'unknown'}"
            )

        average_change = sum(
            float(snapshot.get("price_change", 0.0)) for snapshot in stock_payload.values()
        ) / len(stock_payload)
        average_trend_score = sum(
            float(snapshot.get("trend_score", 50.0)) for snapshot in stock_payload.values()
        ) / len(stock_payload)
        breadth = sum(
            1 for snapshot in stock_payload.values() if float(snapshot.get("price_change", 0.0)) > 0
        ) / len(stock_payload)
        index_snapshot = self._fetch_index_snapshot(as_of_date=as_of_date)
        board_snapshot = self._fetch_industry_board_snapshot(as_of_date=as_of_date)
        market_regime = self._classify_market_regime(
            stock_pool_change=average_change,
            breadth=breadth,
            index_snapshot=index_snapshot,
            sample_size=len(stock_payload),
        )
        sector_summary.extend(
            self._build_market_summary_lines(
                market_regime=market_regime,
                average_change=average_change,
                breadth=breadth,
                average_trend_score=average_trend_score,
                index_snapshot=index_snapshot,
                board_snapshot=board_snapshot,
            )
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
            "board_snapshot": board_snapshot,
        }
        self.cache.save(self._snapshot_cache_key(symbols, as_of_date), snapshot)
        return snapshot

    def _fetch_stock_snapshot(self, symbol: str, *, as_of_date: str | None = None) -> dict[str, Any]:
        market_code, stock_code = self._normalize_symbol(symbol)
        prepared = self._fetch_prepared_history(
            symbol=symbol,
            stock_code=stock_code,
            market_code=market_code,
            as_of_date=as_of_date,
        )
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

        trend_score = self._score_trend(
            latest_close=float(latest["close"]),
            prev_close=float(previous["close"]),
            ma5=float(ma5),
            ma20=float(ma20),
        )
        event_tags = [f"市场={market_code}", f"最新成交额={float(latest['amount']) / 1e8:.2f}亿"]
        if float(latest["close"]) > ma20:
            event_tags.append("收盘站上20日线")
        if float(latest["pct_change"]) <= -5:
            event_tags.append("单日跌幅较大")

        snapshot = {
            "name": self._lookup_stock_name(
                stock_code,
                market_code,
                allow_remote=not bool(as_of_date and as_of_date != date.today().isoformat()),
            ),
            "close": float(latest["close"]),
            "price_change": float(latest["pct_change"]) / 100,
            "ma5": float(ma5),
            "ma20": float(ma20),
            "trend_score": trend_score,
            "momentum_5d": float(latest["close"]) / lookback_close - 1 if lookback_close else 0.0,
            "drawdown_20d": float(latest["close"]) / recent_high - 1 if recent_high else 0.0,
            "volatility_10d": volatility_10d if pd.notna(volatility_10d) else 0.0,
            "event_tags": event_tags,
        }
        self.cache.save(self._cache_key(symbol, as_of_date), snapshot)
        return snapshot

    def _fetch_prepared_history(
        self,
        *,
        symbol: str,
        stock_code: str,
        market_code: str,
        as_of_date: str | None = None,
    ) -> pd.DataFrame:
        source_errors: list[str] = []
        end_date = (as_of_date or "2099-12-31").replace("-", "")
        source_loaders: list[tuple[str, Callable[[], pd.DataFrame]]] = [
            ("eastmoney", lambda: ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date="20240101",
                end_date=end_date,
                adjust="qfq",
                timeout=8,
            )),
            ("tencent", lambda: ak.stock_zh_a_hist_tx(
                symbol=f"{market_code.lower()}{stock_code}",
                start_date="20240101",
                end_date=end_date,
                adjust="qfq",
                timeout=8,
            )),
            ("sina", lambda: ak.stock_zh_a_daily(
                symbol=f"{market_code.lower()}{stock_code}",
                start_date="20240101",
                end_date=end_date,
                adjust="qfq",
            )),
        ]

        for source_name, loader in source_loaders:
            try:
                history = loader()
                prepared = self._prepare_history(history, source_name=source_name)
                if as_of_date:
                    prepared = prepared[prepared["date"] <= pd.to_datetime(as_of_date)].reset_index(drop=True)
                if len(prepared) < 2:
                    raise MarketDataError(f"not enough bars returned from {source_name}")
                return prepared
            except Exception as exc:
                source_errors.append(f"{source_name}: {exc}")

        raise MarketDataError(f"all history sources failed for {symbol}; {'; '.join(source_errors)}")

    def _normalize_symbol(self, symbol: str) -> tuple[str, str]:
        raw = symbol.strip().upper()
        if "." not in raw:
            raise MarketDataError(f"symbol must use exchange suffix, got {symbol}")

        stock_code, exchange = raw.split(".", maxsplit=1)
        if exchange not in {"SH", "SZ", "BJ"}:
            raise MarketDataError(f"unsupported exchange suffix for {symbol}")

        return exchange, stock_code

    def _prepare_history(self, history: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
        renamed = history.rename(columns=self._column_map_for_source(source_name))
        if "pct_change" not in renamed.columns and "close" in renamed.columns:
            close_series = pd.to_numeric(renamed["close"], errors="coerce")
            renamed["pct_change"] = close_series.pct_change() * 100
        if "volume" not in renamed.columns:
            renamed["volume"] = 0.0
        required_columns = [
            "date",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "pct_change",
        ]
        missing = [column for column in required_columns if column not in renamed.columns]
        if missing:
            raise MarketDataError(
                f"unexpected {source_name} columns: missing {', '.join(missing)}"
            )

        prepared = renamed[required_columns].copy()
        prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce")
        for numeric_column in required_columns[1:]:
            prepared[numeric_column] = pd.to_numeric(prepared[numeric_column], errors="coerce")
        prepared = prepared.dropna().reset_index(drop=True)
        if prepared.empty:
            raise MarketDataError(f"{source_name} history is empty after normalization")
        return prepared

    def _column_map_for_source(self, source_name: str) -> dict[str, str]:
        if source_name == "eastmoney":
            return {
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "涨跌幅": "pct_change",
            }
        if source_name == "tencent":
            return {
                "date": "date",
                "open": "open",
                "close": "close",
                "high": "high",
                "low": "low",
                "amount": "amount",
            }
        if source_name == "sina":
            return {
                "date": "date",
                "open": "open",
                "close": "close",
                "high": "high",
                "low": "low",
                "volume": "volume",
                "amount": "amount",
            }
        raise MarketDataError(f"unsupported history source: {source_name}")

    def _score_trend(
        self,
        *,
        latest_close: float,
        prev_close: float,
        ma5: float,
        ma20: float,
    ) -> float:
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
        sample_size: int,
    ) -> str:
        index_changes = [
            float(payload.get("price_change", 0.0))
            for payload in index_snapshot.values()
            if payload.get("price_change") is not None
        ]
        index_average = sum(index_changes) / len(index_changes) if index_changes else stock_pool_change
        combined_signal = stock_pool_change * 0.5 + index_average * 0.35 + (breadth - 0.5) * 0.15

        # When only a handful of tracked symbols are available, dampen regime switching.
        if not index_snapshot and sample_size < 20:
            confidence = max(sample_size / 20, 0.25)
            combined_signal *= confidence

        if combined_signal >= 0.02:
            return "强势"
        if combined_signal >= 0.005:
            return "震荡偏强"
        if combined_signal <= -0.02:
            return "弱势"
        if combined_signal <= -0.005:
            return "震荡偏弱"
        return "震荡"

    def _fetch_index_snapshot(self, *, as_of_date: str | None = None) -> dict[str, dict[str, Any]]:
        if as_of_date and as_of_date != date.today().isoformat():
            return {}
        cache_key = f"indices_{as_of_date or date.today().isoformat()}"
        try:
            frame = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
            if frame is None or frame.empty:
                raise MarketDataError("empty index frame")
            index_payload: dict[str, dict[str, Any]] = {}
            for index_name in self.INDEX_NAMES:
                matched = frame[frame["名称"] == index_name]
                if matched.empty:
                    continue
                row = matched.iloc[0]
                index_payload[index_name] = {
                    "price_change": float(row["涨跌幅"]) / 100,
                    "close": float(row["最新价"]),
                }
            if index_payload:
                self.cache.save(cache_key, index_payload)
                return index_payload
        except Exception:
            cached = self.cache.load(cache_key)
            if cached is not None:
                return cached
        return {}

    def _fetch_industry_board_snapshot(
        self,
        *,
        as_of_date: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        if as_of_date and as_of_date != date.today().isoformat():
            return {"leaders": [], "laggards": []}
        cache_key = f"boards_{as_of_date or date.today().isoformat()}"
        try:
            frame = ak.stock_board_industry_name_em()
            if frame is None or frame.empty:
                raise MarketDataError("empty board frame")
            top = frame.sort_values("涨跌幅", ascending=False).head(3)
            bottom = frame.sort_values("涨跌幅", ascending=True).head(3)
            payload = {
                "leaders": self._serialize_board_rows(top),
                "laggards": self._serialize_board_rows(bottom),
            }
            self.cache.save(cache_key, payload)
            return payload
        except Exception:
            cached = self.cache.load(cache_key)
            if cached is not None:
                return cached
        return {"leaders": [], "laggards": []}

    def _serialize_board_rows(self, frame: pd.DataFrame) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            rows.append(
                {
                    "name": str(row["板块名称"]),
                    "price_change": float(row["涨跌幅"]) / 100 if pd.notna(row["涨跌幅"]) else 0.0,
                    "leader": str(row.get("领涨股票", "")),
                }
            )
        return rows

    def _build_market_summary_lines(
        self,
        *,
        market_regime: str,
        average_change: float,
        breadth: float,
        average_trend_score: float,
        index_snapshot: dict[str, dict[str, Any]],
        board_snapshot: dict[str, list[dict[str, Any]]],
    ) -> list[str]:
        lines = [
            f"股票池平均涨跌幅 {average_change:.2%}，当前判定为 {market_regime}。",
            f"上涨家数占比 {breadth:.0%}，平均趋势评分 {average_trend_score:.1f}。",
        ]
        if index_snapshot:
            index_line = "，".join(
                f"{name}{payload['price_change']:+.2%}"
                for name, payload in index_snapshot.items()
            )
            lines.append(f"核心指数表现: {index_line}。")
        leaders = board_snapshot.get("leaders", [])
        laggards = board_snapshot.get("laggards", [])
        if leaders:
            leaders_line = "，".join(
                f"{item['name']}{item['price_change']:+.2%}" for item in leaders
            )
            lines.append(f"领涨行业: {leaders_line}。")
        if laggards:
            laggards_line = "，".join(
                f"{item['name']}{item['price_change']:+.2%}" for item in laggards
            )
            lines.append(f"承压行业: {laggards_line}。")
        return lines

    def _cache_key(self, symbol: str, as_of_date: str | None = None) -> str:
        return f"{symbol}_{as_of_date or date.today().isoformat()}"

    def _snapshot_cache_key(self, symbols: list[str], as_of_date: str | None = None) -> str:
        return f"snapshot_{as_of_date or date.today().isoformat()}_{len(symbols)}"

    def _lookup_stock_name(
        self,
        stock_code: str,
        market_code: str,
        *,
        allow_remote: bool = True,
    ) -> str | None:
        cache_key = f"stock_name_map_{date.today().isoformat()}"
        cache = self.cache.load(cache_key) or {}
        symbol = f"{stock_code}.{market_code}"
        if symbol in cache:
            return cache[symbol]
        if not allow_remote:
            return None

        try:
            frame = ak.stock_zh_a_spot_em()
            if frame is None or frame.empty:
                return None
            mapping = {}
            for _, row in frame.iterrows():
                code = str(row["代码"]).zfill(6)
                name = str(row["名称"]).strip()
                exchange = "SH" if code.startswith(("600", "601", "603", "605", "688")) else "SZ"
                mapping[f"{code}.{exchange}"] = name
            self.cache.save(cache_key, mapping)
            return mapping.get(symbol)
        except Exception:
            return None
