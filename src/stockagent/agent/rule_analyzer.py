from __future__ import annotations

from typing import Any

from stockagent.agent.base import ReportAnalyzer
from stockagent.schemas import DailyReport


class RuleBasedReportAnalyzer(ReportAnalyzer):
    """Return the deterministic fallback report unchanged."""

    def analyze(
        self,
        *,
        context: dict[str, Any],
        fallback_report: DailyReport,
    ) -> DailyReport:
        return fallback_report
