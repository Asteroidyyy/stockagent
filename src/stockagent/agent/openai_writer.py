from __future__ import annotations

import json

from stockagent.agent.summary_guard import summary_matches_report
from stockagent.schemas import DailyReport

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - depends on local environment
    OpenAI = None


class OpenAIReportWriter:
    """OpenAI-backed report writer with deterministic fallback."""

    def __init__(self, *, api_key: str, model: str, fallback_writer, base_url: str = "") -> None:
        self.api_key = api_key
        self.model = model
        self.fallback_writer = fallback_writer
        self.client = (
            OpenAI(api_key=api_key, base_url=base_url or None)
            if OpenAI is not None and api_key
            else None
        )

    def render_summary(self, report: DailyReport) -> str:
        if self.client is None:
            return self.fallback_writer.render_summary(report)

        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=(
                    "你是A股研究助理。你只能基于提供的结构化日报生成摘要。"
                    "严禁编造任何未在输入中出现的股票代码、股票名称、指标或事件。"
                    "必须覆盖市场环境、持仓调整、观察名单和风险控制。"
                ),
                input=self._build_prompt(report),
                max_output_tokens=700,
            )
        except Exception as exc:  # pragma: no cover - depends on remote API
            fallback = self.fallback_writer.render_summary(report)
            return f"{fallback}\n\nLLM 生成失败，已切回模板摘要。原因: {exc}"

        output_text = getattr(response, "output_text", "").strip()
        if output_text and summary_matches_report(output_text, report):
            return output_text
        return self.fallback_writer.render_summary(report)

    def _build_prompt(self, report: DailyReport) -> str:
        payload = report.model_dump()
        payload["instruction"] = (
            "根据这份结构化日报输出简洁中文摘要。"
            "只能引用 payload 中已有的 symbol、action、score、reasons、risk_alerts。"
            "不要添加股票名称，不要扩写不存在的结论。"
        )
        return json.dumps(payload, ensure_ascii=False)
