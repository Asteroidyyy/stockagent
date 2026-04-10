from stockagent.agent.openai_writer import OpenAIReportWriter
from stockagent.agent.report_writer import ReportWriter
from stockagent.config import get_settings


def build_report_writer():
    settings = get_settings()
    fallback_writer = ReportWriter()

    if settings.report_writer_backend == "openai":
        return OpenAIReportWriter(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            fallback_writer=fallback_writer,
        )

    return fallback_writer
