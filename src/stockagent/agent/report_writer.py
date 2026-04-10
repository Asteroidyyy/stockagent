from stockagent.schemas import DailyReport


class ReportWriter:
    """Fallback report writer before wiring a live LLM."""

    def render_summary(self, report: DailyReport) -> str:
        actionable_watchlist = [
            item
            for item in report.watchlist
            if item.action == "buy_more" and item.target_weight is not None
        ]
        watchlist = ", ".join(
            f"{item.symbol}({item.score:.1f})" for item in report.watchlist[:3]
        ) or "暂无"
        risk_summary = (
            f"共 {len(report.risk_alerts)} 条，详见正文"
            if report.risk_alerts
            else "暂无"
        )

        lines = [
            (
                "结论: "
                f"{report.market_summary.regime}市场，"
                f"风险预算股票仓位 {report.cash_exposure_target:.0%}，"
                f"实际执行目标仓位 {report.portfolio_summary.target_exposure:.0%}。"
            )
        ]
        if actionable_watchlist:
            entries = ", ".join(
                f"{item.symbol}({item.target_weight:.0%})"
                for item in actionable_watchlist[:3]
            )
            lines.append(f"试探建仓: {entries}。")
        elif report.portfolio_actions:
            actions = ", ".join(
                f"{item.symbol}:{item.action}"
                for item in report.portfolio_actions[:3]
            )
            lines.append(f"持仓动作: {actions}。")
        else:
            lines.append("持仓动作: 当前无持仓，无存量仓位需要调整。")
        lines.append(f"观察重点: {watchlist}。")
        lines.append(f"风险提示: {risk_summary}。")
        return "\n".join(lines)
