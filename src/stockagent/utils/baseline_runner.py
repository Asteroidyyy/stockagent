from __future__ import annotations

import json

from stockagent.agent.analyzer_factory import build_report_analyzer
from stockagent.agent.factory import build_report_writer
from stockagent.config import get_settings, resolve_path
from stockagent.data.factory import build_market_data_provider
from stockagent.events.factory import build_event_provider
from stockagent.schemas import BaselineRunResult, PositionInput
from stockagent.services.report_history_service import ReportHistoryService
from stockagent.services.report_service import ReportService
from stockagent.storage.database import session_scope
from stockagent.storage.repository import DailyReportRepository
from stockagent.utils.metadata import build_run_metadata


def run_baseline_cases() -> list[BaselineRunResult]:
    settings = get_settings()
    baseline_dir = resolve_path(settings.baseline_dir)
    if not baseline_dir.exists():
        return []

    results: list[BaselineRunResult] = []
    for path in sorted(baseline_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        positions = [PositionInput.model_validate(item) for item in payload.get("positions", [])]
        candidate_symbols = [str(item).strip().upper() for item in payload.get("candidate_symbols", [])]
        if not positions:
            continue

        service = ReportService(
            provider=build_market_data_provider(),
            writer=build_report_writer(),
            event_provider=build_event_provider(),
            analyzer=build_report_analyzer(),
        )
        report = service.build_daily_report(positions, candidate_symbols=candidate_symbols)
        with session_scope() as session:
            history = ReportHistoryService(DailyReportRepository(session))
            stored = history.save_report(
                report,
                context=service.last_context,
                metadata=build_run_metadata(case_name=path.stem),
            )
            results.append(history.summarize_baseline_result(case_name=path.stem, stored=stored))

    return results
