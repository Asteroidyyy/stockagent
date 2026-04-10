from __future__ import annotations

from stockagent.schemas import PositionInput, StockSignal


def score_position(
    position: PositionInput,
    stock_snapshot: dict,
    *,
    market_regime: str = "震荡",
) -> StockSignal:
    trend_score = float(stock_snapshot.get("trend_score", 50))
    price_change = float(stock_snapshot.get("price_change", 0))
    momentum_5d = float(stock_snapshot.get("momentum_5d", 0))
    drawdown_20d = float(stock_snapshot.get("drawdown_20d", 0))
    volatility_10d = float(stock_snapshot.get("volatility_10d", 0))
    turnover_rate = float(stock_snapshot.get("turnover_rate", 0))
    amount_ratio_5d = float(stock_snapshot.get("amount_ratio_5d", 1))
    amount_ratio_20d = float(stock_snapshot.get("amount_ratio_20d", 1))
    close_price = float(stock_snapshot.get("close", 0))

    action = "hold"
    target_weight = position.weight
    reasons = [f"趋势评分 {trend_score:.1f}", f"市场环境 {market_regime}"]
    risk_flags: list[str] = []
    score_breakdown = {
        "trend": trend_score,
        "market": 0.0,
        "momentum": 0.0,
        "drawdown": 0.0,
        "volatility": 0.0,
        "activity": 0.0,
        "event": 0.0,
    }
    score_explanations = {
        "trend": "基础趋势分，来自均线结构与收盘强弱。",
        "market": "市场环境修正，强市加分、弱市减分。",
        "momentum": "5日动量修正。",
        "drawdown": "相对20日高点回撤修正。",
        "volatility": "10日波动率修正。",
        "activity": "换手率与成交额活跃度修正。",
        "event": "风险事件、止盈止损和仓位约束修正。",
    }

    adjusted_score = trend_score
    if market_regime in {"强势", "震荡偏强"}:
        adjusted_score += 3
        score_breakdown["market"] += 3
    elif market_regime in {"弱势", "震荡偏弱"}:
        adjusted_score -= 8
        score_breakdown["market"] -= 8

    if momentum_5d >= 0.08:
        adjusted_score += 5
        score_breakdown["momentum"] += 5
        reasons.append(f"5日动量较强 {momentum_5d:.1%}")
        score_explanations["momentum"] = f"5日动量 {momentum_5d:.1%}，给予正向加分。"
    elif momentum_5d <= -0.04:
        adjusted_score -= 5
        score_breakdown["momentum"] -= 5
        reasons.append(f"5日动量偏弱 {momentum_5d:.1%}")
        score_explanations["momentum"] = f"5日动量 {momentum_5d:.1%}，给予负向扣分。"
    else:
        score_explanations["momentum"] = f"5日动量 {momentum_5d:.1%}，影响中性。"

    if drawdown_20d <= -0.12:
        adjusted_score -= 10
        score_breakdown["drawdown"] -= 10
        reasons.append(f"距离20日高点回撤较大 {drawdown_20d:.1%}")
        score_explanations["drawdown"] = f"距20日高点回撤 {drawdown_20d:.1%}，触发回撤扣分。"
    else:
        score_explanations["drawdown"] = f"距20日高点回撤 {drawdown_20d:.1%}，处于可接受范围。"

    if volatility_10d >= 0.04:
        adjusted_score -= 5
        score_breakdown["volatility"] -= 5
        reasons.append(f"10日波动偏高 {volatility_10d:.1%}")
        risk_flags.append("high_volatility")
        score_explanations["volatility"] = f"10日波动率 {volatility_10d:.1%} 偏高，降低评分。"
    else:
        score_explanations["volatility"] = f"10日波动率 {volatility_10d:.1%}，风险可控。"

    activity_delta = 0.0
    activity_reasons: list[str] = []
    if turnover_rate and turnover_rate < 0.005:
        activity_delta -= 3
        activity_reasons.append(f"换手率偏低 {turnover_rate:.1%}")
    elif 0.01 <= turnover_rate <= 0.08:
        activity_delta += 2
        activity_reasons.append(f"换手率健康 {turnover_rate:.1%}")
    elif turnover_rate >= 0.15:
        activity_delta -= 2
        risk_flags.append("high_turnover")
        activity_reasons.append(f"换手率过高 {turnover_rate:.1%}")

    if amount_ratio_5d >= 1.5 and amount_ratio_20d >= 1.2:
        if price_change > 0:
            activity_delta += 4
            activity_reasons.append(
                f"成交额放大，5日比 {amount_ratio_5d:.1f} 倍，20日比 {amount_ratio_20d:.1f} 倍"
            )
        elif price_change < -0.02:
            activity_delta -= 4
            risk_flags.append("volume_selloff")
            activity_reasons.append(
                f"放量下跌，5日比 {amount_ratio_5d:.1f} 倍，20日比 {amount_ratio_20d:.1f} 倍"
            )
    elif amount_ratio_5d <= 0.6 and price_change > 0.02:
        activity_delta -= 2
        activity_reasons.append(f"缩量上涨，成交额仅为5日均值 {amount_ratio_5d:.1f} 倍")

    if turnover_rate >= 0.15 and price_change >= 0.07:
        activity_delta -= 3
        risk_flags.append("overheat")
        activity_reasons.append("高换手大涨，短线过热")

    activity_delta = max(-8.0, min(6.0, activity_delta))
    adjusted_score += activity_delta
    score_breakdown["activity"] += activity_delta
    if activity_reasons:
        reasons.extend(activity_reasons)
        score_explanations["activity"] = "；".join(activity_reasons) + f"，修正 {activity_delta:+.1f}。"
    else:
        score_explanations["activity"] = "换手率与成交额未出现显著放大、萎缩或过热信号。"

    adjusted_score = max(0.0, min(100.0, adjusted_score))

    if position.weight == 0:
        can_open = (
            adjusted_score >= 84
            and market_regime in {"强势", "震荡偏强"}
            and momentum_5d >= 0.03
            and drawdown_20d >= -0.06
            and volatility_10d < 0.03
            and score_breakdown["activity"] >= 0
            and not risk_flags
        )
        if can_open:
            action = "buy_more"
            target_weight = 0.08 if market_regime == "强势" else 0.06
            reasons.append("候选标的趋势与动量共振，允许建立首仓")
            score_explanations["event"] = "未出现明显风险标签，允许从观察升级为首仓。"
        else:
            action = "watch"
            target_weight = None
            reasons.append("暂列观察，不建议立即建仓")
    else:
        can_add = (
            adjusted_score >= 82
            and momentum_5d > 0
            and drawdown_20d > -0.08
            and volatility_10d < 0.035
            and score_breakdown["activity"] >= -2
            and market_regime not in {"弱势", "震荡偏弱"}
        )
        if can_add:
            action = "buy_more"
            target_weight = min(position.weight + 0.02, position.max_weight or 0.25, 0.25)
            reasons.append("综合趋势与环境偏强，可考虑顺势加仓")
        elif adjusted_score <= 48 or market_regime == "弱势":
            action = "reduce"
            target_weight = max(position.weight - 0.05, 0.0)
            reasons.append("综合趋势偏弱，优先降低仓位暴露")
        else:
            reasons.append("暂无明显加减仓信号")

    if price_change < -0.03:
        action = "reduce"
        target_weight = max(position.weight - 0.05, 0.0) if position.weight > 0 else None
        reasons.append("单日跌幅偏大，需要控制回撤")
        risk_flags.append("large_daily_drop")

    if position.cost_basis and close_price > 0:
        pnl_ratio = close_price / position.cost_basis - 1
        stop_loss_pct = position.stop_loss_pct or 0.08
        take_profit_pct = position.take_profit_pct or 0.2
        if pnl_ratio <= -stop_loss_pct:
            action = "reduce"
            target_weight = max(position.weight - 0.1, 0.0)
            reasons.append(f"相对成本回撤 {pnl_ratio:.1%}，触发止损约束")
            risk_flags.append("stop_loss")
            score_breakdown["event"] -= 10
            adjusted_score = min(adjusted_score, 35.0)
            score_explanations["event"] = f"相对成本回撤 {pnl_ratio:.1%}，触发止损，显著扣分。"
        elif pnl_ratio >= take_profit_pct and market_regime in {"弱势", "震荡偏弱"}:
            action = "reduce"
            target_weight = max(position.weight - 0.05, 0.0)
            reasons.append(f"相对成本盈利 {pnl_ratio:.1%}，弱市优先锁定部分利润")
            risk_flags.append("take_profit")
            score_breakdown["event"] -= 3
            score_explanations["event"] = f"相对成本盈利 {pnl_ratio:.1%}，弱市锁定利润，轻微扣分。"
        else:
            reasons.append(f"相对成本收益 {pnl_ratio:.1%}")
            score_explanations["event"] = f"相对成本收益 {pnl_ratio:.1%}，未触发止盈止损。"

    max_weight = position.max_weight or 0.3
    if position.weight > max_weight:
        action = "reduce"
        target_weight = min(target_weight if target_weight is not None else max_weight, max_weight)
        reasons.append(f"当前仓位超过单票上限 {max_weight:.0%}")
        risk_flags.append("overweight")

    for tag in stock_snapshot.get("event_tags", []):
        reasons.append(tag)
        if tag.startswith("无重大风险"):
            continue
        if any(keyword in tag for keyword in ["风险", "问询", "减持", "停牌", "ST"]):
            action = "reduce" if position.weight > 0 else "watch"
            if position.weight > 0:
                target_weight = max(position.weight - 0.05, 0.0)
            risk_flags.append("event_risk")
            score_breakdown["event"] -= 8
            score_explanations["event"] = f"出现风险事件标签“{tag}”，因此额外扣分。"

    return StockSignal(
        symbol=position.symbol,
        name=position.name or stock_snapshot.get("name"),
        action=action,
        score=adjusted_score,
        reasons=reasons,
        target_weight=target_weight,
        risk_flags=list(dict.fromkeys(risk_flags)),
        score_breakdown=score_breakdown,
        score_explanations=score_explanations,
    )


def derive_cash_exposure_target(
    *,
    market_regime: str,
    portfolio_actions: list[StockSignal],
    risk_alerts: list[str],
) -> float:
    regime_targets = {
        "强势": 0.7,
        "震荡偏强": 0.6,
        "震荡": 0.5,
        "震荡偏弱": 0.35,
        "弱势": 0.2,
        "未知": 0.3,
    }
    target = regime_targets.get(market_regime, 0.6)

    reduce_count = sum(1 for action in portfolio_actions if action.action == "reduce")
    buy_count = sum(1 for action in portfolio_actions if action.action == "buy_more")
    if reduce_count > buy_count:
        target -= 0.05
    elif buy_count > reduce_count:
        target += 0.03

    if risk_alerts:
        target -= min(0.15, 0.03 * len(risk_alerts))

    return max(0.2, min(0.9, target))


def apply_portfolio_guardrails(
    *,
    positions: list[PositionInput],
    portfolio_actions: list[StockSignal],
    cash_exposure_target: float,
    market_regime: str,
) -> tuple[list[StockSignal], list[str]]:
    guardrail_alerts: list[str] = []
    action_by_symbol = {item.symbol: item for item in portfolio_actions}

    for position in positions:
        action = action_by_symbol.get(position.symbol)
        if action is None:
            continue
        max_weight = position.max_weight or 0.3
        if action.target_weight is not None and action.target_weight > max_weight:
            action.target_weight = max_weight
            action.action = "reduce" if position.weight > max_weight else action.action
            action.reasons.append(f"已按单票上限 {max_weight:.0%} 截断目标仓位")
            action.risk_flags.append("max_weight_cap")

    desired_total_exposure = max(0.0, min(cash_exposure_target, 1.0))
    total_target = sum(
        item.target_weight or 0.0
        for item in portfolio_actions
        if item.target_weight is not None
    )
    if total_target > desired_total_exposure and total_target > 0:
        scale = desired_total_exposure / total_target
        for item in portfolio_actions:
            if item.target_weight is not None:
                item.target_weight = round(item.target_weight * scale, 4)
        guardrail_alerts.append("组合目标仓位超过全局仓位目标，已按比例压缩目标仓位")

    total_target = sum(
        item.target_weight or 0.0
        for item in portfolio_actions
        if item.target_weight is not None
    )
    if total_target < desired_total_exposure:
        capacity_by_symbol: dict[str, float] = {}
        for position in positions:
            action = action_by_symbol.get(position.symbol)
            if action is None or action.target_weight is None or action.action == "reduce":
                continue
            cap = max((position.max_weight or 0.3) - action.target_weight, 0.0)
            if cap > 0:
                capacity_by_symbol[position.symbol] = cap

        remaining_gap = desired_total_exposure - total_target
        total_capacity = sum(capacity_by_symbol.values())
        if total_capacity > 0 and remaining_gap > 0:
            for symbol, capacity in capacity_by_symbol.items():
                action = action_by_symbol[symbol]
                addition = remaining_gap * (capacity / total_capacity)
                bounded_addition = min(capacity, addition)
                action.target_weight = round((action.target_weight or 0.0) + bounded_addition, 4)
            guardrail_alerts.append("组合目标仓位低于全局仓位目标，已在可加仓标的内补足目标仓位")

    if market_regime == "弱势":
        weak_reduce_count = sum(1 for item in portfolio_actions if item.action == "reduce")
        if weak_reduce_count == 0 and portfolio_actions:
            weakest = min(portfolio_actions, key=lambda item: item.score)
            weakest.action = "reduce"
            if weakest.target_weight is not None:
                weakest.target_weight = max((weakest.target_weight or 0.0) - 0.05, 0.0)
            weakest.reasons.append("弱市环境下至少保留一个主动降仓动作")
            weakest.risk_flags.append("weak_market_guardrail")
            guardrail_alerts.append("弱市环境自动强化了最弱持仓的降仓动作")

    for item in portfolio_actions:
        item.risk_flags = list(dict.fromkeys(item.risk_flags))
        if item.target_weight is not None:
            item.target_weight = round(max(item.target_weight, 0.0), 4)

    return portfolio_actions, guardrail_alerts
