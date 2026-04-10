from __future__ import annotations

import json
from typing import Any

from stockagent.agent.base import ReportAnalyzer
from stockagent.agent.report_writer import ReportWriter
from stockagent.agent.summary_guard import summary_matches_report
from stockagent.schemas import DailyReport, PortfolioSummary

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - depends on local environment
    OpenAI = None


class OpenAIReportAnalyst(ReportAnalyzer):
    """Use OpenAI to generate the full structured report from market context."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        fallback_analyzer: ReportAnalyzer,
        base_url: str = "",
    ) -> None:
        self.model = model
        self.fallback_analyzer = fallback_analyzer
        self.safe_summary_writer = ReportWriter()
        self.client = (
            OpenAI(api_key=api_key, base_url=base_url or None)
            if OpenAI is not None and api_key
            else None
        )

    def analyze(
        self,
        *,
        context: dict[str, Any],
        fallback_report: DailyReport,
    ) -> DailyReport:
        if self.client is None:
            return self.fallback_analyzer.analyze(
                context=context,
                fallback_report=fallback_report,
            )

        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=self._build_instructions(),
                input=self._build_prompt(context, fallback_report),
                max_output_tokens=2200,
                temperature=0.2,
            )
        except Exception:
            return self.fallback_analyzer.analyze(
                context=context,
                fallback_report=fallback_report,
            )

        output_text = getattr(response, "output_text", "").strip()
        if not output_text:
            return self.fallback_analyzer.analyze(
                context=context,
                fallback_report=fallback_report,
            )

        try:
            payload = self._extract_json(output_text)
            report = DailyReport.model_validate(payload)
        except Exception:
            return self.fallback_analyzer.analyze(
                context=context,
                fallback_report=fallback_report,
            )

        return self._apply_guardrails(report, fallback_report)

    def _build_instructions(self) -> str:
        return (
            "你是A股股票研究 agent。你要根据给定的市场、持仓、候选股、事件和规则参考，"
            "输出一个严格 JSON 格式的结构化日报。"
            "禁止输出 markdown、解释文字或代码块，只能输出 JSON。"
            "你的判断应以输入数据为基础，自主完成市场判断、持仓动作、观察名单和仓位目标。"
            "规则参考仅作为护栏和参考，不是必须逐项照抄。"
            "llm_summary 字段必须为空字符串，不要在结构化阶段生成自由文本摘要。"
        )

    def _build_prompt(self, context: dict[str, Any], fallback_report: DailyReport) -> str:
        schema_hint = {
            "trade_date": "YYYY-MM-DD",
            "market_summary": {
                "regime": "强势/震荡偏强/震荡/震荡偏弱/弱势/未知",
                "summary": "字符串",
                "breadth": "0-1之间数字或null",
                "average_score": "0-100之间数字或null",
            },
            "portfolio_summary": {
                "position_count": "整数",
                "current_exposure": "0-1之间数字",
                "target_exposure": "0-1之间数字",
                "max_single_position": "0-1之间数字",
                "rebalance_bias": "字符串",
            },
            "portfolio_actions": [
                {
                    "symbol": "股票代码",
                    "action": "buy_more/reduce/hold/watch",
                    "score": "0-100之间数字",
                    "reasons": ["字符串"],
                    "target_weight": "0-1之间数字或null",
                }
            ],
            "watchlist": [
                {
                    "symbol": "股票代码",
                    "action": "watch",
                    "score": "0-100之间数字",
                    "reasons": ["字符串"],
                    "target_weight": None,
                }
            ],
            "risk_alerts": ["字符串"],
            "cash_exposure_target": "0-1之间数字",
            "llm_summary": "",
        }

        payload = {
            "task": "基于输入数据生成完整的收盘后日报 JSON。",
            "output_schema_hint": schema_hint,
            "market_context": context,
            "rule_reference_report": fallback_report.model_dump(),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _extract_json(self, text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            parts = stripped.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                if part.startswith("{") and part.endswith("}"):
                    return json.loads(part)
        return json.loads(stripped)

    def _apply_guardrails(
        self,
        report: DailyReport,
        fallback_report: DailyReport,
    ) -> DailyReport:
        valid_actions = {"buy_more", "reduce", "hold", "watch"}
        action_by_symbol = {item.symbol: item for item in fallback_report.portfolio_actions}

        for item in report.portfolio_actions:
            if item.action not in valid_actions:
                item.action = "hold"
            item.score = max(0.0, min(100.0, item.score))
            if item.target_weight is not None:
                item.target_weight = max(0.0, min(0.3, item.target_weight))
            fallback_item = action_by_symbol.get(item.symbol)
            if fallback_item and any("风险" in reason for reason in fallback_item.reasons):
                if item.target_weight is not None and fallback_item.target_weight is not None:
                    item.target_weight = min(item.target_weight, fallback_item.target_weight)

        for item in report.watchlist:
            item.action = "watch"
            item.target_weight = None
            item.score = max(0.0, min(100.0, item.score))

        fallback_watch_by_symbol = {item.symbol: item for item in fallback_report.watchlist}
        if not any(item.action == "buy_more" for item in report.watchlist):
            for item in report.watchlist:
                fallback_item = fallback_watch_by_symbol.get(item.symbol)
                if (
                    fallback_item
                    and fallback_item.action == "buy_more"
                    and fallback_item.target_weight is not None
                ):
                    item.action = "buy_more"
                    item.target_weight = fallback_item.target_weight
                    item.reasons = list(dict.fromkeys(item.reasons + fallback_item.reasons))
                    break

        report.cash_exposure_target = max(0.2, min(0.9, report.cash_exposure_target))
        report.portfolio_summary = self._normalize_portfolio_summary(report)
        report.llm_summary = self._build_safe_summary(report, fallback_report)
        return report

    def _normalize_portfolio_summary(self, report: DailyReport) -> PortfolioSummary:
        actionable_watchlist = [
            item for item in report.watchlist if item.action == "buy_more" and item.target_weight is not None
        ]
        target_exposure = min(
            sum(item.target_weight or 0.0 for item in report.portfolio_actions if item.target_weight is not None)
            + sum(item.target_weight or 0.0 for item in actionable_watchlist),
            1.0,
        )
        max_single = max(
            [*(item.target_weight or 0.0 for item in report.portfolio_actions), *(item.target_weight or 0.0 for item in actionable_watchlist)],
            default=0.0,
        )
        return PortfolioSummary(
            position_count=len(report.portfolio_actions),
            current_exposure=report.portfolio_summary.current_exposure,
            target_exposure=target_exposure,
            max_single_position=max_single,
            rebalance_bias=report.portfolio_summary.rebalance_bias,
        )

    def _build_safe_summary(self, report: DailyReport, fallback_report: DailyReport) -> str:
        summary = self.safe_summary_writer.render_summary(report)
        if summary_matches_report(summary, report):
            return summary
        return fallback_report.llm_summary or self.safe_summary_writer.render_summary(fallback_report)
