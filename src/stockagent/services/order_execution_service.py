from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from stockagent.config import get_settings, resolve_path
from stockagent.schemas import (
    ExecutedOrder,
    OrderExecutionResult,
    OrderIntent,
    OrderPlan,
    PositionInput,
    ReplayBundle,
)


class OrderExecutionService:
    def __init__(self) -> None:
        settings = get_settings()
        self.backend = settings.execution_backend
        self.default_capital = settings.default_order_capital
        self.output_dir = resolve_path(settings.execution_output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_plan(self, replay: ReplayBundle, *, total_capital: float | None = None) -> OrderPlan:
        capital = float(total_capital or self.default_capital)
        report = replay.stored_report.report
        context = replay.analysis_context or {}
        positions = [
            PositionInput.model_validate(item)
            for item in context.get("positions", [])
            if isinstance(item, dict)
        ]
        current_weights = {item.symbol: item.weight for item in positions}
        name_map = {
            item.symbol: item.name
            for item in positions
            if item.name
        }
        desired_weights = {
            item.symbol: (item.target_weight if item.target_weight is not None else current_weights.get(item.symbol, 0.0))
            for item in report.portfolio_actions
        }
        reason_map = {item.symbol: list(item.reasons) for item in report.portfolio_actions}
        action_map = {item.symbol: item.action for item in report.portfolio_actions}

        for item in report.watchlist:
            if item.action == "buy_more" and item.target_weight is not None:
                desired_weights[item.symbol] = item.target_weight
                reason_map[item.symbol] = list(item.reasons)
                action_map[item.symbol] = item.action
                if item.name:
                    name_map[item.symbol] = item.name

        orders: list[OrderIntent] = []
        for symbol in sorted(set(current_weights) | set(desired_weights)):
            current_weight = round(float(current_weights.get(symbol, 0.0)), 4)
            target_weight = round(float(desired_weights.get(symbol, 0.0)), 4)
            weight_delta = round(target_weight - current_weight, 4)
            if abs(weight_delta) < 0.005:
                continue
            action = action_map.get(symbol, "hold")
            orders.append(
                OrderIntent(
                    symbol=symbol,
                    name=name_map.get(symbol),
                    side="buy" if weight_delta > 0 else "sell",
                    action=action,
                    current_weight=current_weight,
                    target_weight=target_weight,
                    weight_delta=weight_delta,
                    notional_amount=round(abs(weight_delta) * capital, 2),
                    reasons=reason_map.get(symbol, []),
                )
            )

        buy_count = sum(1 for item in orders if item.side == "buy")
        sell_count = len(orders) - buy_count
        plan = OrderPlan(
            plan_id=str(uuid4()),
            report_id=replay.stored_report.id,
            trade_date=replay.stored_report.trade_date,
            total_capital=capital,
            generated_at=datetime.utcnow().isoformat(),
            summary={
                "order_count": len(orders),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "net_target_exposure": report.portfolio_summary.target_exposure,
                "cash_exposure_target": report.cash_exposure_target,
            },
            orders=orders,
        )
        self._write_json(self.output_dir / f"order_plan_{plan.plan_id}.json", plan.model_dump_json(indent=2))
        return plan

    def execute_mock(self, plan: OrderPlan) -> OrderExecutionResult:
        executed_orders = [
            ExecutedOrder(
                symbol=item.symbol,
                side=item.side,
                notional_amount=item.notional_amount,
                status="filled",
                message="mock paper execution completed",
            )
            for item in plan.orders
        ]
        result = OrderExecutionResult(
            execution_id=str(uuid4()),
            plan_id=plan.plan_id,
            report_id=plan.report_id,
            broker=self.backend,
            mode="paper",
            executed_at=datetime.utcnow().isoformat(),
            summary={
                "filled_count": len(executed_orders),
                "buy_notional": round(
                    sum(item.notional_amount for item in plan.orders if item.side == "buy"),
                    2,
                ),
                "sell_notional": round(
                    sum(item.notional_amount for item in plan.orders if item.side == "sell"),
                    2,
                ),
            },
            orders=executed_orders,
        )
        self._write_json(
            self.output_dir / f"execution_{result.execution_id}.json",
            result.model_dump_json(indent=2),
        )
        return result

    def _write_json(self, path: Path, payload: str) -> None:
        path.write_text(payload, encoding="utf-8")
