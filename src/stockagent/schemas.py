from typing import Any, Literal

from pydantic import BaseModel, Field


ActionType = Literal["buy_more", "reduce", "hold", "watch"]


class PositionInput(BaseModel):
    symbol: str
    name: str | None = None
    weight: float = Field(ge=0, le=1)
    cost_basis: float | None = None
    max_weight: float | None = Field(default=None, ge=0, le=1)
    stop_loss_pct: float | None = Field(default=None, ge=0, le=1)
    take_profit_pct: float | None = Field(default=None, ge=0, le=5)


class MarketSummary(BaseModel):
    regime: str
    summary: str
    breadth: float | None = None
    average_score: float | None = None


class StockSignal(BaseModel):
    symbol: str
    name: str | None = None
    action: ActionType
    score: float
    reasons: list[str]
    target_weight: float | None = Field(default=None, ge=0, le=1)
    risk_flags: list[str] = Field(default_factory=list)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    score_explanations: dict[str, str] = Field(default_factory=dict)


class PortfolioSummary(BaseModel):
    position_count: int = Field(ge=0)
    current_exposure: float = Field(ge=0, le=1)
    target_exposure: float = Field(ge=0, le=1)
    max_single_position: float = Field(ge=0, le=1)
    rebalance_bias: str


class DailyReport(BaseModel):
    trade_date: str
    market_summary: MarketSummary
    portfolio_summary: PortfolioSummary
    portfolio_actions: list[StockSignal]
    watchlist: list[StockSignal]
    risk_alerts: list[str]
    cash_exposure_target: float = Field(ge=0, le=1)
    llm_summary: str | None = None


class ReportEvaluation(BaseModel):
    report_id: str
    score: float = Field(ge=0, le=100)
    strengths: list[str]
    weaknesses: list[str]
    metrics: dict[str, float | int]


class EvaluationBatchSummary(BaseModel):
    report_count: int
    generated_at: str
    average_score: float = Field(ge=0, le=100)
    average_metrics: dict[str, float]
    top_strengths: list[str]
    top_weaknesses: list[str]
    reports: list[ReportEvaluation]


class DailyReportRequest(BaseModel):
    positions: list[PositionInput] = Field(default_factory=list)
    candidate_symbols: list[str] = Field(default_factory=list)
    include_default_universe: bool = True


class BacktestRequest(BaseModel):
    positions: list[PositionInput] = Field(default_factory=list)
    candidate_symbols: list[str] = Field(default_factory=list)
    include_default_universe: bool = True
    start_date: str
    end_date: str
    start_mode: Literal["from_cash", "rebalance"] = "from_cash"


class StoredDailyReport(BaseModel):
    id: str
    trade_date: str
    market_regime: str
    created_at: str
    metadata: dict = Field(default_factory=dict)
    report: DailyReport


class ReplayBundle(BaseModel):
    stored_report: StoredDailyReport
    analysis_context: dict
    evaluation: ReportEvaluation | None = None


class BaselineRunResult(BaseModel):
    case_name: str
    report_id: str
    regime: str
    evaluation_score: float
    output_path: str | None = None


class SimulationSignalResult(BaseModel):
    symbol: str
    name: str | None = None
    action: str
    entry_date: str
    exit_date: str | None = None
    horizon_days: int
    entry_price: float | None = None
    exit_price: float | None = None
    return_pct: float | None = None
    max_drawdown_pct: float | None = None
    max_runup_pct: float | None = None
    verdict: str
    notes: list[str] = Field(default_factory=list)


class SimulationReport(BaseModel):
    report_id: str
    trade_date: str
    horizon_days: int
    generated_at: str
    summary_score: float = Field(ge=0, le=100)
    signal_results: list[SimulationSignalResult]
    aggregate_metrics: dict[str, float | int]


class SimulationBatchSummary(BaseModel):
    horizon_days: int
    report_count: int
    generated_at: str
    average_score: float = Field(ge=0, le=100)
    average_return_pct: float
    average_max_drawdown_pct: float
    average_max_runup_pct: float
    win_rate: float = Field(ge=0, le=1)
    verdict_counts: dict[str, int]
    reports: list[SimulationReport]


class BacktestDayResult(BaseModel):
    trade_date: str
    next_trade_date: str | None = None
    start_nav: float
    end_nav: float
    daily_return_pct: float
    target_exposure: float
    cash_weight: float
    market_regime: str
    top_actions: list[str] = Field(default_factory=list)


class BacktestSummary(BaseModel):
    start_date: str
    end_date: str
    trading_days: list[str]
    generated_at: str
    initial_nav: float
    final_nav: float
    total_return_pct: float
    max_drawdown_pct: float
    average_exposure: float
    day_results: list[BacktestDayResult]


class OrderIntent(BaseModel):
    symbol: str
    name: str | None = None
    side: Literal["buy", "sell"]
    action: ActionType
    current_weight: float = Field(ge=0, le=1)
    target_weight: float = Field(ge=0, le=1)
    weight_delta: float
    notional_amount: float = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class OrderPlan(BaseModel):
    plan_id: str
    report_id: str
    trade_date: str
    total_capital: float = Field(gt=0)
    generated_at: str
    summary: dict[str, Any] = Field(default_factory=dict)
    orders: list[OrderIntent] = Field(default_factory=list)


class ExecutedOrder(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    notional_amount: float = Field(ge=0)
    status: str
    message: str = ""


class OrderExecutionResult(BaseModel):
    execution_id: str
    plan_id: str
    report_id: str
    broker: str
    mode: str
    executed_at: str
    summary: dict[str, Any] = Field(default_factory=dict)
    orders: list[ExecutedOrder] = Field(default_factory=list)


class TaskStatus(BaseModel):
    task_id: str
    task_type: str
    status: Literal["pending", "running", "completed", "failed"]
    created_at: str
    updated_at: str
    detail: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
