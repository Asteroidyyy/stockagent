from datetime import date
from typing import Any

from stockagent.agent.base import ReportAnalyzer
from stockagent.config import get_settings
from stockagent.agent.report_writer import ReportWriter
from stockagent.data.base import MarketDataError, MarketDataProvider
from stockagent.events.base import EventProvider
from stockagent.events.normalizer import is_risk_label
from stockagent.schemas import (
    DailyReport,
    MarketSummary,
    PortfolioSummary,
    PositionInput,
    StockSignal,
)
from stockagent.strategy.scoring import (
    apply_portfolio_guardrails,
    derive_cash_exposure_target,
    score_position,
)


class ReportService:
    def __init__(
        self,
        provider: MarketDataProvider,
        writer: ReportWriter,
        event_provider: EventProvider | None = None,
        analyzer: ReportAnalyzer | None = None,
    ) -> None:
        self.provider = provider
        self.writer = writer
        self.event_provider = event_provider
        self.analyzer = analyzer
        self.last_context: dict[str, Any] = {}
        self.settings = get_settings()

    def build_daily_report(
        self,
        positions: list[PositionInput],
        candidate_symbols: list[str] | None = None,
        *,
        as_of_date: str | None = None,
    ) -> DailyReport:
        trade_date = as_of_date or date.today().isoformat()
        position_name_map = {
            position.symbol: position.name
            for position in positions
            if position.name
        }
        position_symbols = [position.symbol for position in positions]
        candidate_symbols = candidate_symbols or []
        symbols = list(dict.fromkeys(position_symbols + candidate_symbols))
        try:
            snapshot = self.provider.fetch_market_snapshot(symbols, as_of_date=trade_date)
            risk_alerts = list(snapshot.get("errors", []))
        except MarketDataError as exc:
            snapshot = {
                "market_regime": "未知",
                "sector_summary": ["真实数据获取失败，本次未生成正式建议。"],
                "stocks": {},
            }
            risk_alerts = [str(exc)]

        event_payload = self._fetch_events(symbols, as_of_date=trade_date)
        risk_alerts.extend(self._merge_events_into_snapshot(snapshot["stocks"], event_payload))
        risk_alerts = list(dict.fromkeys(risk_alerts))

        portfolio_actions: list[StockSignal] = [
            score_position(
                position,
                self._attach_stock_name(
                    snapshot["stocks"].get(position.symbol, {}),
                    symbol=position.symbol,
                    known_name=position_name_map.get(position.symbol),
                ),
                market_regime=snapshot["market_regime"],
            )
            for position in positions
        ]
        watchlist = self._build_watchlist(
            candidate_symbols,
            snapshot["stocks"],
            market_regime=snapshot["market_regime"],
        )
        current_exposure = min(sum(position.weight for position in positions), 1.0)
        watchlist = self._promote_bootstrap_entry(
            watchlist=watchlist,
            current_exposure=current_exposure,
            market_regime=snapshot["market_regime"],
        )
        actionable_watchlist = [
            item for item in watchlist if item.action == "buy_more" and item.target_weight is not None
        ]
        cash_exposure_target = derive_cash_exposure_target(
            market_regime=snapshot["market_regime"],
            portfolio_actions=portfolio_actions,
            risk_alerts=risk_alerts,
        )
        portfolio_actions, guardrail_alerts = apply_portfolio_guardrails(
            positions=positions,
            portfolio_actions=portfolio_actions,
            cash_exposure_target=cash_exposure_target,
            market_regime=snapshot["market_regime"],
        )
        risk_alerts.extend(guardrail_alerts)
        risk_alerts = list(dict.fromkeys(risk_alerts))
        target_exposure = min(
            sum(action.target_weight or 0.0 for action in portfolio_actions if action.target_weight is not None)
            + sum(action.target_weight or 0.0 for action in actionable_watchlist),
            1.0,
        )
        max_single_position = max(
            [*(action.target_weight or 0.0 for action in portfolio_actions), *(action.target_weight or 0.0 for action in actionable_watchlist)],
            default=0.0,
        )

        fallback_report = DailyReport(
            trade_date=trade_date,
            market_summary=MarketSummary(
                regime=snapshot["market_regime"],
                summary=" ".join(snapshot["sector_summary"]),
                breadth=snapshot.get("breadth"),
                average_score=snapshot.get("average_trend_score"),
            ),
            portfolio_summary=PortfolioSummary(
                position_count=len(positions),
                current_exposure=current_exposure,
                target_exposure=target_exposure,
                max_single_position=max_single_position,
                rebalance_bias=self._infer_rebalance_bias(
                    current_exposure,
                    cash_exposure_target,
                    market_regime=snapshot["market_regime"],
                ),
            ),
            portfolio_actions=portfolio_actions,
            watchlist=watchlist,
            risk_alerts=risk_alerts,
            cash_exposure_target=cash_exposure_target,
        )
        fallback_report.llm_summary = self.writer.render_summary(fallback_report)

        context = self._build_analysis_context(
            positions=positions,
            candidate_symbols=candidate_symbols,
            snapshot=snapshot,
            events=event_payload,
            risk_alerts=risk_alerts,
            trade_date=trade_date,
        )
        self.last_context = context
        report = self._run_analysis(context=context, fallback_report=fallback_report)
        if not report.llm_summary:
            report.llm_summary = self.writer.render_summary(report)
        return report

    def _build_watchlist(
        self,
        candidate_symbols: list[str],
        stocks_snapshot: dict[str, dict],
        *,
        market_regime: str,
    ) -> list[StockSignal]:
        watch_signals: list[StockSignal] = []
        for symbol in candidate_symbols:
            stock_snapshot = stocks_snapshot.get(symbol)
            if not stock_snapshot:
                continue
            signal = score_position(
                PositionInput(symbol=symbol, weight=0.0),
                self._attach_stock_name(stock_snapshot, symbol=symbol),
                market_regime=market_regime,
            )
            watch_signals.append(signal)

        filtered_signals = [item for item in watch_signals if self._is_candidate_eligible(item)]
        ranking_pool = filtered_signals or watch_signals
        ranking_pool.sort(key=self._candidate_rank_key, reverse=True)
        return ranking_pool[: self.settings.watchlist_limit]

    def _promote_bootstrap_entry(
        self,
        *,
        watchlist: list[StockSignal],
        current_exposure: float,
        market_regime: str,
    ) -> list[StockSignal]:
        if current_exposure > 0:
            return watchlist
        if market_regime in {"弱势", "震荡偏弱"}:
            score_floor = 87 if market_regime == "弱势" else 85
            starter_weight = 0.02 if market_regime == "弱势" else 0.03
            max_entries = 1 if market_regime == "弱势" else 2
            qualified = [
                item
                for item in watchlist
                if item.score >= score_floor
                and not item.risk_flags
                and item.score_breakdown.get("momentum", 0.0) > 0
                and item.score_breakdown.get("volatility", 0.0) >= 0
                and item.score_breakdown.get("activity", 0.0) >= 0
            ]
            for item in qualified[:max_entries]:
                item.action = "buy_more"
                item.target_weight = starter_weight
                item.reasons.append("空仓弱市试探规则触发，仅允许极小仓位验证强势标的")
                item.score_explanations["event"] = "空仓且标的评分很高，弱市仅允许小比例试探仓。"
            return watchlist
        if market_regime not in {"强势", "震荡偏强"}:
            return watchlist
        if any(item.action == "buy_more" for item in watchlist):
            return watchlist
        qualified = [item for item in watchlist if item.score >= 75 and not item.risk_flags]
        if not qualified:
            return watchlist
        if len(qualified) >= 2:
            starter_weight = 0.06 if market_regime == "强势" else 0.05
            selected = qualified[:2]
        else:
            starter_weight = 0.08 if market_regime == "强势" else 0.06
            selected = qualified[:1]
        for item in selected:
            item.action = "buy_more"
            item.target_weight = starter_weight
            item.reasons.append("空仓启动规则触发，允许先建立分散试探仓")
            item.score_explanations["event"] = "空仓且市场不弱，候选池高分标的允许试探建仓。"
        return watchlist

    def _is_candidate_eligible(self, signal: StockSignal) -> bool:
        if signal.risk_flags:
            return False
        if signal.score < 58:
            return False
        trend_score = signal.score_breakdown.get("trend", 0.0)
        momentum_score = signal.score_breakdown.get("momentum", 0.0)
        drawdown_score = signal.score_breakdown.get("drawdown", 0.0)
        volatility_score = signal.score_breakdown.get("volatility", 0.0)
        activity_score = signal.score_breakdown.get("activity", 0.0)
        if trend_score < 60:
            return False
        if momentum_score < 0 and drawdown_score < 0:
            return False
        if volatility_score < -5:
            return False
        if activity_score < -4:
            return False
        return True

    def _candidate_rank_key(self, signal: StockSignal) -> tuple[float, float, float, float]:
        trend_score = signal.score_breakdown.get("trend", 0.0)
        momentum_score = signal.score_breakdown.get("momentum", 0.0)
        volatility_score = signal.score_breakdown.get("volatility", 0.0)
        activity_score = signal.score_breakdown.get("activity", 0.0)
        drawdown_score = signal.score_breakdown.get("drawdown", 0.0)
        return (
            signal.score,
            trend_score + momentum_score + activity_score,
            drawdown_score - abs(volatility_score),
            -len(signal.risk_flags),
        )

    def _infer_rebalance_bias(
        self,
        current_exposure: float,
        cash_exposure_target: float,
        *,
        market_regime: str,
    ) -> str:
        target_cash = 1 - cash_exposure_target
        current_cash = 1 - current_exposure
        if market_regime == "弱势":
            return "降仓控险"
        if market_regime == "强势" and current_cash > target_cash:
            return "可适度进攻"
        if current_cash > target_cash + 0.1:
            return "可适度进攻"
        if current_cash < target_cash - 0.1:
            return "需要防守降仓"
        return "维持中性"

    def _fetch_events(self, symbols: list[str], *, as_of_date: str | None = None) -> dict[str, list[str]]:
        if self.event_provider is None or not symbols:
            return {}
        try:
            return self.event_provider.fetch_events(symbols, as_of_date=as_of_date)
        except Exception as exc:
            return {"__provider__": [f"事件数据获取失败: {exc}"]}

    def _merge_events_into_snapshot(
        self,
        stocks_snapshot: dict[str, dict],
        event_payload: dict[str, list[str]],
    ) -> list[str]:
        risk_alerts: list[str] = []

        provider_errors = event_payload.get("__provider__", [])
        risk_alerts.extend(provider_errors)

        for symbol, events in event_payload.items():
            if symbol == "__provider__":
                continue
            snapshot = stocks_snapshot.setdefault(symbol, {})
            existing_tags = list(snapshot.get("event_tags", []))
            snapshot["event_tags"] = list(dict.fromkeys(existing_tags + events))
            risk_events = [
                f"{symbol}: {event}"
                for event in events
                if self._is_risk_event(event)
            ]
            risk_alerts.extend(risk_events)

        return risk_alerts

    def _is_risk_event(self, event: str) -> bool:
        return is_risk_label(event)

    def _attach_stock_name(
        self,
        stock_snapshot: dict | None,
        *,
        symbol: str,
        known_name: str | None = None,
    ) -> dict:
        payload = dict(stock_snapshot or {})
        if known_name:
            payload["name"] = known_name
            return payload
        context_events = self.last_context.get("analysis_context_name_map") if self.last_context else None
        if isinstance(context_events, dict) and symbol in context_events:
            payload["name"] = context_events[symbol]
        return payload

    def _build_analysis_context(
        self,
        *,
        positions: list[PositionInput],
        candidate_symbols: list[str],
        snapshot: dict[str, Any],
        events: dict[str, list[str]],
        risk_alerts: list[str],
        trade_date: str,
    ) -> dict[str, Any]:
        return {
            "trade_date": trade_date,
            "positions": [position.model_dump() for position in positions],
            "candidate_symbols": candidate_symbols,
            "market_snapshot": snapshot,
            "events": events,
            "risk_alerts": risk_alerts,
            "analysis_context_name_map": self._build_name_map(positions, snapshot),
        }

    def _run_analysis(
        self,
        *,
        context: dict[str, Any],
        fallback_report: DailyReport,
    ) -> DailyReport:
        if self.analyzer is None:
            return fallback_report
        return self.analyzer.analyze(context=context, fallback_report=fallback_report)

    def _build_name_map(self, positions: list[PositionInput], snapshot: dict[str, Any]) -> dict[str, str]:
        name_map = {
            position.symbol: position.name
            for position in positions
            if position.name
        }
        for symbol, stock_snapshot in snapshot.get("stocks", {}).items():
            name = stock_snapshot.get("name")
            if isinstance(name, str) and name:
                name_map[symbol] = name
        return name_map
