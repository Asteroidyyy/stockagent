from stockagent.agent.openai_analyst import OpenAIReportAnalyst
from stockagent.agent.rule_analyzer import RuleBasedReportAnalyzer
from stockagent.config import get_settings


def build_report_analyzer():
    settings = get_settings()
    fallback_analyzer = RuleBasedReportAnalyzer()

    if settings.analysis_backend == "openai":
        return OpenAIReportAnalyst(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url,
            fallback_analyzer=fallback_analyzer,
        )

    return fallback_analyzer
