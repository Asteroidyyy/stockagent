from __future__ import annotations

from datetime import datetime
import pandas as pd

from stockagent.config import resolve_path
from stockagent.schemas import (
    SimulationBatchSummary,
    SimulationReport,
    SimulationSignalResult,
    StoredDailyReport,
)

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None


class SimulationService:
    def __init__(self) -> None:
        self.output_dir = resolve_path("outputs/simulations")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def simulate(self, stored: StoredDailyReport, *, horizon_days: int = 1) -> SimulationReport:
        if ak is None:
            raise RuntimeError("Simulation requires akshare. Install dependencies first.")

        signal_results: list[SimulationSignalResult] = []
        for signal in stored.report.portfolio_actions + stored.report.watchlist:
            signal_results.append(
                self._simulate_signal(
                    stored=stored,
                    signal=signal,
                    horizon_days=horizon_days,
                )
            )

        valid_returns = [item.return_pct for item in signal_results if item.return_pct is not None]
        valid_drawdowns = [
            item.max_drawdown_pct for item in signal_results if item.max_drawdown_pct is not None
        ]
        valid_runups = [
            item.max_runup_pct for item in signal_results if item.max_runup_pct is not None
        ]
        positive = sum(1 for item in signal_results if item.verdict == "good")
        negative = sum(1 for item in signal_results if item.verdict == "bad")
        score = 50.0
        if signal_results:
            score += (positive - negative) / len(signal_results) * 50
        if valid_returns:
            score += sum(valid_returns) / len(valid_returns) * 100

        report = SimulationReport(
            report_id=stored.id,
            trade_date=stored.trade_date,
            horizon_days=horizon_days,
            generated_at=datetime.utcnow().isoformat(),
            summary_score=max(0.0, min(100.0, score)),
            signal_results=signal_results,
            aggregate_metrics={
                "signal_count": len(signal_results),
                "positive_verdicts": positive,
                "negative_verdicts": negative,
                "win_rate": round(positive / len(signal_results), 4) if signal_results else 0.0,
                "avg_return_pct": round(sum(valid_returns) / len(valid_returns), 4)
                if valid_returns
                else 0.0,
                "avg_max_drawdown_pct": round(sum(valid_drawdowns) / len(valid_drawdowns), 4)
                if valid_drawdowns
                else 0.0,
                "avg_max_runup_pct": round(sum(valid_runups) / len(valid_runups), 4)
                if valid_runups
                else 0.0,
            },
        )
        output_path = self.output_dir / f"simulation_{stored.id}_{horizon_days}d.json"
        output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return report

    def simulate_many(
        self,
        reports: list[StoredDailyReport],
        *,
        horizon_days: int = 1,
    ) -> SimulationBatchSummary:
        simulations = [self.simulate(report, horizon_days=horizon_days) for report in reports]
        valid_scores = [item.summary_score for item in simulations]
        avg_return_values = [
            float(item.aggregate_metrics.get("avg_return_pct", 0.0)) for item in simulations
        ]
        avg_drawdown_values = [
            float(item.aggregate_metrics.get("avg_max_drawdown_pct", 0.0)) for item in simulations
        ]
        avg_runup_values = [
            float(item.aggregate_metrics.get("avg_max_runup_pct", 0.0)) for item in simulations
        ]
        win_rate_values = [
            float(item.aggregate_metrics.get("win_rate", 0.0)) for item in simulations
        ]
        verdict_counts: dict[str, int] = {}
        for simulation in simulations:
            for signal in simulation.signal_results:
                verdict_counts[signal.verdict] = verdict_counts.get(signal.verdict, 0) + 1

        summary = SimulationBatchSummary(
            horizon_days=horizon_days,
            report_count=len(simulations),
            generated_at=datetime.utcnow().isoformat(),
            average_score=round(sum(valid_scores) / len(valid_scores), 4) if valid_scores else 0.0,
            average_return_pct=round(sum(avg_return_values) / len(avg_return_values), 4)
            if avg_return_values
            else 0.0,
            average_max_drawdown_pct=round(
                sum(avg_drawdown_values) / len(avg_drawdown_values),
                4,
            )
            if avg_drawdown_values
            else 0.0,
            average_max_runup_pct=round(sum(avg_runup_values) / len(avg_runup_values), 4)
            if avg_runup_values
            else 0.0,
            win_rate=round(sum(win_rate_values) / len(win_rate_values), 4) if win_rate_values else 0.0,
            verdict_counts=verdict_counts,
            reports=simulations,
        )
        output_path = self.output_dir / f"simulation_batch_{horizon_days}d.json"
        output_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        return summary

    def _simulate_signal(self, *, stored: StoredDailyReport, signal, horizon_days: int) -> SimulationSignalResult:
        history = self._fetch_history(signal.symbol, stored.trade_date)
        if history.empty:
            return SimulationSignalResult(
                symbol=signal.symbol,
                name=signal.name,
                action=signal.action,
                entry_date=stored.trade_date,
                horizon_days=horizon_days,
                verdict="unknown",
                notes=["未获取到报告日后的历史行情，可能是报告过新或数据源暂不可用"],
            )

        entry_idx = self._find_entry_index(history, stored.trade_date)
        if entry_idx is None or entry_idx + 1 >= len(history):
            return SimulationSignalResult(
                symbol=signal.symbol,
                name=signal.name,
                action=signal.action,
                entry_date=stored.trade_date,
                horizon_days=horizon_days,
                verdict="unknown",
                notes=["报告日后暂无可用交易日，需等待更多未来数据再评估"],
            )

        buy_idx = entry_idx + 1
        exit_idx = min(buy_idx + horizon_days - 1, len(history) - 1)
        entry_row = history.iloc[buy_idx]
        exit_row = history.iloc[exit_idx]
        path = history.iloc[buy_idx : exit_idx + 1]
        entry_price = float(entry_row["open"])
        exit_price = float(exit_row["close"])
        return_pct = exit_price / entry_price - 1 if entry_price else None
        max_drawdown_pct = None
        max_runup_pct = None
        if entry_price and not path.empty:
            min_low = float(path["low"].min()) if "low" in path.columns else None
            max_high = float(path["high"].max()) if "high" in path.columns else None
            if min_low is not None:
                max_drawdown_pct = min_low / entry_price - 1
            if max_high is not None:
                max_runup_pct = max_high / entry_price - 1
        verdict, notes = self._judge(signal.action, return_pct)
        if max_drawdown_pct is not None:
            notes.append(f"区间最大回撤 {max_drawdown_pct:.2%}")
        if max_runup_pct is not None:
            notes.append(f"区间最大浮盈 {max_runup_pct:.2%}")

        return SimulationSignalResult(
            symbol=signal.symbol,
            name=signal.name,
            action=signal.action,
            entry_date=str(entry_row["date"].date()),
            exit_date=str(exit_row["date"].date()),
            horizon_days=horizon_days,
            entry_price=entry_price,
            exit_price=exit_price,
            return_pct=round(return_pct, 4) if return_pct is not None else None,
            max_drawdown_pct=round(max_drawdown_pct, 4) if max_drawdown_pct is not None else None,
            max_runup_pct=round(max_runup_pct, 4) if max_runup_pct is not None else None,
            verdict=verdict,
            notes=notes,
        )

    def _judge(self, action: str, return_pct: float | None) -> tuple[str, list[str]]:
        if return_pct is None:
            return "unknown", ["收益率不可用"]

        if action == "reduce":
            if return_pct <= 0:
                return "good", [f"后续区间收益 {return_pct:.2%}，减仓建议有效"]
            return "bad", [f"后续区间收益 {return_pct:.2%}，减仓可能偏早"]

        if action == "buy_more":
            if return_pct > 0:
                return "good", [f"后续区间收益 {return_pct:.2%}，加仓建议有效"]
            return "bad", [f"后续区间收益 {return_pct:.2%}，加仓建议失效"]

        if action == "watch":
            if return_pct > 0.03:
                return "missed", [f"观察标的后续上涨 {return_pct:.2%}，可能值得提前介入"]
            return "neutral", [f"观察标的后续收益 {return_pct:.2%}，观察结论基本合理"]

        if abs(return_pct) <= 0.02:
            return "good", [f"后续区间波动 {return_pct:.2%}，持有建议基本合理"]
        return "neutral", [f"后续区间收益 {return_pct:.2%}，需结合更长周期再评估"]

    def _fetch_history(self, symbol: str, trade_date: str) -> pd.DataFrame:
        code, exchange = symbol.split(".", maxsplit=1)
        start_date = trade_date.replace("-", "")
        source_loaders = [
            lambda: ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date,
                end_date="20991231",
                adjust="qfq",
                timeout=8,
            ),
            lambda: ak.stock_zh_a_hist_tx(
                symbol=f"{exchange.lower()}{code}",
                start_date=start_date,
                end_date="20991231",
                adjust="qfq",
                timeout=8,
            ),
            lambda: ak.stock_zh_a_daily(
                symbol=f"{exchange.lower()}{code}",
                start_date=start_date,
                end_date="20991231",
                adjust="qfq",
            ),
        ]

        for loader in source_loaders:
            try:
                frame = loader()
                prepared = self._prepare(frame)
                if not prepared.empty:
                    return prepared
            except Exception:
                continue
        return pd.DataFrame()

    def _prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()
        renamed = frame.rename(
            columns={
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "date": "date",
                "open": "open",
                "close": "close",
                "high": "high",
                "low": "low",
            }
        )
        if not {"date", "open", "close", "high", "low"}.issubset(set(renamed.columns)):
            return pd.DataFrame()
        prepared = renamed[["date", "open", "close", "high", "low"]].copy()
        prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce")
        prepared["open"] = pd.to_numeric(prepared["open"], errors="coerce")
        prepared["close"] = pd.to_numeric(prepared["close"], errors="coerce")
        prepared["high"] = pd.to_numeric(prepared["high"], errors="coerce")
        prepared["low"] = pd.to_numeric(prepared["low"], errors="coerce")
        prepared = prepared.dropna().reset_index(drop=True)
        return prepared

    def _find_entry_index(self, history: pd.DataFrame, trade_date: str) -> int | None:
        target = pd.to_datetime(trade_date)
        matched = history[history["date"] == target]
        if matched.empty:
            earlier = history[history["date"] < target]
            if earlier.empty:
                return None
            return int(earlier.index[-1])
        return int(matched.index[-1])
