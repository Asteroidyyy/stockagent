from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from stockagent.schemas import DailyReport


class ReportAnalyzer(ABC):
    @abstractmethod
    def analyze(
        self,
        *,
        context: dict[str, Any],
        fallback_report: DailyReport,
    ) -> DailyReport:
        raise NotImplementedError
