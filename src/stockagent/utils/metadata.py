from __future__ import annotations

from stockagent.config import get_settings


def build_run_metadata(*, output_path: str | None = None, case_name: str | None = None) -> dict:
    settings = get_settings()
    metadata = {
        "analysis_backend": settings.analysis_backend,
        "report_writer_backend": settings.report_writer_backend,
        "openai_model": settings.openai_model,
        "data_provider": settings.data_provider,
        "event_provider": settings.event_provider,
        "universe_name": settings.universe_name,
        "universe_limit": settings.universe_limit,
    }
    if output_path:
        metadata["output_path"] = output_path
    if case_name:
        metadata["case_name"] = case_name
    return metadata
