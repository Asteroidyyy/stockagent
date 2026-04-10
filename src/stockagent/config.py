from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = Field(default="dev", alias="APP_ENV")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5-mini", alias="OPENAI_MODEL")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    tushare_token: str = Field(default="", alias="TUSHARE_TOKEN")
    database_url: str = Field(
        default="sqlite:///./stockagent.db",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    default_market: str = Field(default="cn", alias="DEFAULT_MARKET")
    default_report_time: str = Field(default="16:00", alias="DEFAULT_REPORT_TIME")
    portfolio_file: str = Field(default="inputs/portfolio.json", alias="PORTFOLIO_FILE")
    candidate_file: str = Field(default="inputs/candidates.json", alias="CANDIDATE_FILE")
    baseline_dir: str = Field(default="inputs/baselines", alias="BASELINE_DIR")
    run_baseline_mode: bool = Field(default=False, alias="RUN_BASELINE_MODE")
    cache_dir: str = Field(default="data_cache", alias="CACHE_DIR")
    report_output_dir: str = Field(default="outputs/reports", alias="REPORT_OUTPUT_DIR")
    execution_output_dir: str = Field(default="outputs/executions", alias="EXECUTION_OUTPUT_DIR")
    data_provider: str = Field(default="mock", alias="DATA_PROVIDER")
    event_provider: str = Field(default="mock", alias="EVENT_PROVIDER")
    event_lookback_days: int = Field(default=3, alias="EVENT_LOOKBACK_DAYS")
    analysis_backend: str = Field(default="rules", alias="ANALYSIS_BACKEND")
    report_writer_backend: str = Field(default="template", alias="REPORT_WRITER_BACKEND")
    execution_backend: str = Field(default="mock", alias="EXECUTION_BACKEND")
    default_order_capital: float = Field(default=1_000_000, alias="DEFAULT_ORDER_CAPITAL")
    universe_name: str = Field(default="csi500", alias="UNIVERSE_NAME")
    universe_limit: int = Field(default=20, alias="UNIVERSE_LIMIT")
    watchlist_limit: int = Field(default=10, alias="WATCHLIST_LIMIT")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def resolve_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate
