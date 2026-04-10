from __future__ import annotations

from datetime import datetime

import pandas as pd

from stockagent.data.akshare_provider import AkshareMarketDataProvider
from stockagent.schemas import BacktestDayResult, BacktestSummary, PositionInput
from stockagent.services.report_service import ReportService


ACTION_LABELS = {
    "buy_more": "加仓",
    "reduce": "减仓",
    "hold": "持有",
    "watch": "观察",
}


class BacktestService:
    def __init__(self, report_service: ReportService) -> None:
        self.report_service = report_service
        self.market_provider = report_service.provider
        if not isinstance(self.market_provider, AkshareMarketDataProvider):
            raise RuntimeError("Backtest currently requires AkshareMarketDataProvider.")

    def run_window(
        self,
        *,
        positions: list[PositionInput],
        candidate_symbols: list[str],
        start_date: str,
        end_date: str,
        start_mode: str = "from_cash",
    ) -> BacktestSummary:
        symbols = list(dict.fromkeys(
            [position.symbol for position in positions] + candidate_symbols
        ))
        if not symbols:
            raise RuntimeError("Backtest requires at least one portfolio or candidate symbol.")

        history_map = self._load_history_map(symbols=symbols, end_date=end_date)
        trading_days = self._extract_trading_days(history_map, start_date=start_date, end_date=end_date)
        if not trading_days:
            raise RuntimeError("No trading days found in the selected window.")

        state_by_symbol = self._initialize_state(positions=positions, start_mode=start_mode)
        nav = 1.0
        nav_path = [nav]
        day_results: list[BacktestDayResult] = []

        for index, trade_date in enumerate(trading_days):
            next_trade_date = trading_days[index + 1] if index + 1 < len(trading_days) else None
            current_positions = self._build_current_positions(state_by_symbol)
            report = self.report_service.build_daily_report(
                current_positions,
                candidate_symbols=candidate_symbols,
                as_of_date=trade_date,
            )

            target_weights = self._extract_target_weights(
                report,
                state_by_symbol=state_by_symbol,
            )
            target_exposure = round(sum(target_weights.values()), 4)
            start_nav = nav

            if next_trade_date is None:
                day_results.append(
                    BacktestDayResult(
                        trade_date=trade_date,
                        next_trade_date=None,
                        start_nav=round(start_nav, 6),
                        end_nav=round(start_nav, 6),
                        daily_return_pct=0.0,
                        target_exposure=target_exposure,
                        cash_weight=round(max(0.0, 1 - target_exposure), 4),
                        market_regime=report.market_summary.regime,
                        top_actions=self._format_actions(report),
                    )
                )
                continue

            nav, state_by_symbol = self._rebalance_and_mark_to_market(
                state_by_symbol=state_by_symbol,
                target_weights=target_weights,
                history_map=history_map,
                trade_date=next_trade_date,
                fallback_template=current_positions,
                report=report,
                nav=nav,
            )
            nav_path.append(nav)

            day_results.append(
                BacktestDayResult(
                    trade_date=trade_date,
                    next_trade_date=next_trade_date,
                    start_nav=round(start_nav, 6),
                    end_nav=round(nav, 6),
                    daily_return_pct=round(nav / start_nav - 1, 4) if start_nav else 0.0,
                    target_exposure=target_exposure,
                    cash_weight=round(max(0.0, 1 - target_exposure), 4),
                    market_regime=report.market_summary.regime,
                    top_actions=self._format_actions(report),
                )
            )

        max_drawdown = self._calc_max_drawdown(nav_path)
        average_exposure = (
            round(sum(item.target_exposure for item in day_results) / len(day_results), 4)
            if day_results
            else 0.0
        )
        return BacktestSummary(
            start_date=start_date,
            end_date=end_date,
            trading_days=trading_days,
            generated_at=datetime.utcnow().isoformat(),
            initial_nav=1.0,
            final_nav=round(nav, 6),
            total_return_pct=round(nav - 1, 4),
            max_drawdown_pct=round(max_drawdown, 4),
            average_exposure=average_exposure,
            day_results=day_results,
        )

    def _initialize_state(
        self,
        *,
        positions: list[PositionInput],
        start_mode: str,
    ) -> dict[str, dict]:
        state: dict[str, dict] = {}
        for position in positions:
            state[position.symbol] = {
                "name": position.name,
                "weight": position.weight if start_mode == "rebalance" else 0.0,
                "cost_basis": position.cost_basis,
                "max_weight": position.max_weight,
                "stop_loss_pct": position.stop_loss_pct,
                "take_profit_pct": position.take_profit_pct,
            }
        return state

    def _load_history_map(self, *, symbols: list[str], end_date: str) -> dict[str, pd.DataFrame]:
        history_map: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            market_code, stock_code = self.market_provider._normalize_symbol(symbol)
            history = self.market_provider._fetch_prepared_history(
                symbol=symbol,
                stock_code=stock_code,
                market_code=market_code,
                as_of_date=end_date,
            )
            history_map[symbol] = history
        return history_map

    def _extract_trading_days(
        self,
        history_map: dict[str, pd.DataFrame],
        *,
        start_date: str,
        end_date: str,
    ) -> list[str]:
        benchmark = next(iter(history_map.values()))
        filtered = benchmark[
            (benchmark["date"] >= pd.to_datetime(start_date))
            & (benchmark["date"] <= pd.to_datetime(end_date))
        ]
        return [str(item.date()) for item in filtered["date"].tolist()]

    def _build_current_positions(self, state_by_symbol: dict[str, dict]) -> list[PositionInput]:
        positions: list[PositionInput] = []
        for symbol, payload in state_by_symbol.items():
            if payload.get("weight", 0.0) <= 0:
                continue
            positions.append(
                PositionInput(
                    symbol=symbol,
                    name=payload.get("name"),
                    weight=min(max(float(payload.get("weight", 0.0)), 0.0), 1.0),
                    cost_basis=payload.get("cost_basis"),
                    max_weight=payload.get("max_weight"),
                    stop_loss_pct=payload.get("stop_loss_pct"),
                    take_profit_pct=payload.get("take_profit_pct"),
                )
            )
        return positions

    def _rebalance_and_mark_to_market(
        self,
        *,
        state_by_symbol: dict[str, dict],
        target_weights: dict[str, float],
        history_map: dict[str, pd.DataFrame],
        trade_date: str,
        fallback_template: list[PositionInput],
        report,
        nav: float,
    ) -> tuple[float, dict[str, dict]]:
        template_map = {position.symbol: position for position in fallback_template}
        for item in report.watchlist:
            if item.symbol not in template_map and item.action == "buy_more":
                template_map[item.symbol] = PositionInput(
                    symbol=item.symbol,
                    name=item.name,
                    weight=0.0,
                )
        updated_state: dict[str, dict] = {}
        cash_weight = max(0.0, 1 - sum(target_weights.values()))
        cash_value = nav * cash_weight
        asset_values: dict[str, float] = {}

        for symbol, weight in target_weights.items():
            row = self._row_for_trade_date(history_map.get(symbol), trade_date)
            if row is None:
                cash_value += nav * weight
                continue
            open_price = float(row["open"])
            close_price = float(row["close"])
            if open_price <= 0:
                cash_value += nav * weight
                continue
            asset_values[symbol] = nav * weight * (close_price / open_price)

        new_nav = cash_value + sum(asset_values.values())
        if new_nav <= 0:
            return nav, state_by_symbol

        for symbol, value in asset_values.items():
            template = template_map.get(symbol)
            previous = state_by_symbol.get(symbol, {})
            updated_state[symbol] = {
                "name": previous.get("name") or (template.name if template else None),
                "weight": value / new_nav,
                "cost_basis": previous.get("cost_basis") or (template.cost_basis if template else None),
                "max_weight": previous.get("max_weight") or (template.max_weight if template else None),
                "stop_loss_pct": previous.get("stop_loss_pct") or (template.stop_loss_pct if template else None),
                "take_profit_pct": previous.get("take_profit_pct") or (template.take_profit_pct if template else None),
            }

        for symbol, payload in state_by_symbol.items():
            updated_state.setdefault(
                symbol,
                {
                    "name": payload.get("name"),
                    "weight": 0.0,
                    "cost_basis": payload.get("cost_basis"),
                    "max_weight": payload.get("max_weight"),
                    "stop_loss_pct": payload.get("stop_loss_pct"),
                    "take_profit_pct": payload.get("take_profit_pct"),
                },
            )
        return new_nav, updated_state

    def _row_for_trade_date(self, history: pd.DataFrame | None, trade_date: str) -> pd.Series | None:
        if history is None or history.empty:
            return None
        matched = history[history["date"] == pd.to_datetime(trade_date)]
        if matched.empty:
            return None
        return matched.iloc[-1]

    def _format_actions(self, report) -> list[str]:
        actions: list[str] = []
        actionable_signals = list(report.portfolio_actions) + [
            item for item in report.watchlist if item.action == "buy_more"
        ]
        for item in actionable_signals[:5]:
            action_label = ACTION_LABELS.get(item.action, item.action)
            if item.target_weight is not None:
                actions.append(f"{item.symbol} {action_label}至{item.target_weight:.0%}")
            else:
                actions.append(f"{item.symbol} {action_label}")
        return actions

    def _extract_target_weights(self, report, *, state_by_symbol: dict[str, dict]) -> dict[str, float]:
        target_weights: dict[str, float] = {}
        for item in list(report.portfolio_actions) + list(report.watchlist):
            if item.action != "watch" and item.target_weight is not None and item.target_weight > 0:
                current_weight = float(state_by_symbol.get(item.symbol, {}).get("weight", 0.0))
                target_weights[item.symbol] = float(
                    self._clip_weight_step(
                        current_weight=current_weight,
                        target_weight=float(item.target_weight),
                    )
                )
        return target_weights

    def _clip_weight_step(
        self,
        *,
        current_weight: float,
        target_weight: float,
    ) -> float:
        max_up_step = 0.08
        max_down_step = 0.08
        if target_weight > current_weight:
            return min(target_weight, current_weight + max_up_step)
        return max(target_weight, current_weight - max_down_step)

    def _calc_max_drawdown(self, nav_path: list[float]) -> float:
        peak = 0.0
        max_drawdown = 0.0
        for nav in nav_path:
            peak = max(peak, nav)
            if peak > 0:
                max_drawdown = min(max_drawdown, nav / peak - 1)
        return max_drawdown
