from __future__ import annotations

from pathlib import Path

from stockagent.config import get_settings, resolve_path
from stockagent.schemas import StoredDailyReport

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - optional runtime dependency
    A4 = None
    pdfmetrics = None
    UnicodeCIDFont = None
    canvas = None


class ReportPdfService:
    FONT_NAME = "STSong-Light"

    def __init__(self) -> None:
        settings = get_settings()
        self.output_dir = resolve_path(settings.report_output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._font_ready = False

    def export(self, stored: StoredDailyReport) -> Path:
        if canvas is None or pdfmetrics is None or UnicodeCIDFont is None:
            raise RuntimeError("PDF export requires reportlab. Install dependencies first.")

        self._ensure_font()
        output_path = self.output_dir / f"daily_report_{stored.id}.pdf"
        pdf = canvas.Canvas(str(output_path), pagesize=A4)
        width, height = A4
        margin_x = 48
        y = height - 52

        y = self._draw_title(pdf, stored, margin_x, y)
        y = self._draw_section(
            pdf,
            "市场概览",
            self._clean_lines(
                [
                    f"市场状态: {stored.report.market_summary.regime}",
                    stored.report.market_summary.summary,
                    (
                        f"市场宽度: {stored.report.market_summary.breadth:.0%}"
                        if stored.report.market_summary.breadth is not None
                        else ""
                    ),
                    (
                        f"平均趋势分: {stored.report.market_summary.average_score:.1f}"
                        if stored.report.market_summary.average_score is not None
                        else ""
                    ),
                ]
            ),
            margin_x,
            y,
        )
        y = self._draw_section(
            pdf,
            "组合概览",
            [
                f"当前仓位: {stored.report.portfolio_summary.current_exposure:.0%}",
                f"执行目标仓位: {stored.report.portfolio_summary.target_exposure:.0%}",
                f"风险预算股票仓位: {stored.report.cash_exposure_target:.0%}",
                f"风险预算空仓比例: {1 - stored.report.cash_exposure_target:.0%}",
                f"单票上限: {stored.report.portfolio_summary.max_single_position:.0%}",
                f"调仓偏向: {stored.report.portfolio_summary.rebalance_bias}",
            ],
            margin_x,
            y,
        )
        y = self._draw_section(
            pdf,
            "评分说明",
            [
                "趋势评分: 由 MA5/MA20 相对位置、收盘强弱、是否跌破均线等技术指标构成。",
                "最终分数: 在趋势评分基础上叠加市场环境、5日动量、20日回撤、10日波动率和事件风险。",
                "分数越高表示结构越强，但弱市下仍可能因为风险控制而给出 reduce 动作。",
            ],
            margin_x,
            y,
        )
        y = self._draw_section(
            pdf,
            "持仓建议",
            self._build_signal_lines(stored.report.portfolio_actions),
            margin_x,
            y,
        )
        y = self._draw_section(
            pdf,
            "观察名单",
            self._build_signal_lines(stored.report.watchlist[:10], include_target=False),
            margin_x,
            y,
        )
        y = self._draw_section(
            pdf,
            "风险提示",
            stored.report.risk_alerts or ["暂无"],
            margin_x,
            y,
        )
        self._draw_section(
            pdf,
            "摘要",
            [stored.report.llm_summary or "暂无"],
            margin_x,
            y,
        )
        pdf.save()
        return output_path

    def _ensure_font(self) -> None:
        if self._font_ready:
            return
        pdfmetrics.registerFont(UnicodeCIDFont(self.FONT_NAME))
        self._font_ready = True

    def _draw_title(self, pdf: canvas.Canvas, stored: StoredDailyReport, x: int, y: float) -> float:
        pdf.setFont(self.FONT_NAME, 18)
        pdf.drawString(x, y, f"{stored.trade_date} 日报")
        y -= 26
        pdf.setFont(self.FONT_NAME, 10)
        pdf.drawString(x, y, f"报告ID: {stored.id}")
        y -= 16
        pdf.drawString(x, y, f"交易日: {stored.trade_date}    生成时间: {stored.created_at}")
        return y - 22

    def _draw_section(
        self,
        pdf: canvas.Canvas,
        title: str,
        lines: list[str],
        x: int,
        y: float,
    ) -> float:
        width_limit = 66
        if y < 90:
            pdf.showPage()
            pdf.setFont(self.FONT_NAME, 10)
            y = A4[1] - 52

        pdf.setFont(self.FONT_NAME, 13)
        pdf.drawString(x, y, title)
        y -= 18
        pdf.setFont(self.FONT_NAME, 10)
        for line in lines:
            wrapped = self._wrap_text(line, width_limit=width_limit)
            for part in wrapped:
                if y < 60:
                    pdf.showPage()
                    pdf.setFont(self.FONT_NAME, 10)
                    y = A4[1] - 52
                pdf.drawString(x, y, part)
                y -= 14
        return y - 10

    def _wrap_text(self, text: str, *, width_limit: int) -> list[str]:
        normalized = " ".join(str(text).split())
        if not normalized:
            return [""]
        chunks: list[str] = []
        current = ""
        for char in normalized:
            current += char
            if len(current) >= width_limit:
                chunks.append(current)
                current = ""
        if current:
            chunks.append(current)
        return chunks

    def _build_signal_lines(self, signals, *, include_target: bool = True) -> list[str]:
        lines: list[str] = []
        if not signals:
            return ["暂无"]

        for item in signals:
            display_name = f"{item.symbol} {item.name}" if item.name else item.symbol
            target = (
                f" | 目标仓位 {item.target_weight:.0%}" if include_target and item.target_weight is not None else ""
            )
            breakdown_lines = self._format_breakdown_lines(item.score_breakdown, item.score_explanations)
            lines.append(
                f"{display_name} | 动作 {item.action} | 最终分数 {item.score:.1f}{target}"
            )
            lines.extend(breakdown_lines)
            lines.append(f"依据: {'；'.join(item.reasons[:4])}")
            if item.risk_flags:
                lines.append(f"风险标签: {', '.join(item.risk_flags)}")
        return lines

    def _format_breakdown_lines(
        self,
        breakdown: dict[str, float],
        explanations: dict[str, str],
    ) -> list[str]:
        if not breakdown:
            return []
        labels = {
            "trend": "趋势",
            "market": "市场",
            "momentum": "动量",
            "drawdown": "回撤",
            "volatility": "波动",
            "event": "事件",
        }
        lines: list[str] = ["评分明细:"]
        for key, value in breakdown.items():
            label = labels.get(key, key)
            explanation = explanations.get(key, "")
            lines.append(f"{label}: {value:+.1f} | {explanation}".strip())
        return lines

    def _clean_lines(self, lines: list[str]) -> list[str]:
        cleaned = [line for line in lines if str(line).strip()]
        return cleaned or ["暂无"]
