from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from stockagent.agent.analyzer_factory import build_report_analyzer
from stockagent.agent.factory import build_report_writer
from stockagent.config import get_settings
from stockagent.data.factory import build_market_data_provider
from stockagent.events.factory import build_event_provider
from stockagent.schemas import BacktestRequest, DailyReportRequest
from stockagent.services.backtest_service import BacktestService
from stockagent.services.order_execution_service import OrderExecutionService
from stockagent.services.report_service import ReportService
from stockagent.services.report_history_service import ReportHistoryService
from stockagent.services.report_pdf_service import ReportPdfService
from stockagent.services.simulation_service import SimulationService
from stockagent.storage.database import init_database, session_scope
from stockagent.storage.repository import DailyReportRepository
from stockagent.utils.cache import TaskStateStore
from stockagent.utils.input_loader import load_analysis_candidate_symbols

app = FastAPI(title="StockAgent")
task_store = TaskStateStore()


@app.on_event("startup")
def startup() -> None:
    init_database()


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reports/daily")
def generate_daily_report(request: DailyReportRequest) -> dict:
    task_id = str(uuid4())
    task_store.set_status(task_id=task_id, task_type="daily_report", status="running")
    candidate_symbols = list(request.candidate_symbols)
    if request.include_default_universe:
        settings = get_settings()
        if not candidate_symbols:
            candidate_symbols.extend(load_analysis_candidate_symbols())
        elif settings.universe_name:
            candidate_symbols.extend(load_analysis_candidate_symbols(include_default_universe=True))

    deduped_symbols = list(dict.fromkeys(candidate_symbols))
    service = ReportService(
        provider=build_market_data_provider(),
        writer=build_report_writer(),
        event_provider=build_event_provider(),
        analyzer=build_report_analyzer(),
    )
    try:
        report = service.build_daily_report(request.positions, candidate_symbols=deduped_symbols)
        with session_scope() as session:
            stored = ReportHistoryService(DailyReportRepository(session)).save_report(
                report,
                context=service.last_context,
            )
        task_store.set_status(
            task_id=task_id,
            task_type="daily_report",
            status="completed",
            payload={"report_id": stored.id, "trade_date": stored.trade_date},
        )
        response = stored.model_dump()
        response["task_id"] = task_id
        return response
    except Exception as exc:
        task_store.set_status(
            task_id=task_id,
            task_type="daily_report",
            status="failed",
            detail=str(exc),
        )
        raise


@app.get("/reports/daily/latest")
def get_latest_report() -> dict:
    with session_scope() as session:
        stored = ReportHistoryService(DailyReportRepository(session)).get_latest()
        if stored is None:
            raise HTTPException(status_code=404, detail="No report history found.")
        return stored.model_dump()


@app.get("/reports/daily/history")
def list_report_history(limit: int = 20) -> list[dict]:
    limit = min(max(limit, 1), 100)
    with session_scope() as session:
        reports = ReportHistoryService(DailyReportRepository(session)).list_reports(limit=limit)
        return [report.model_dump() for report in reports]


@app.get("/reports/daily/{report_id}")
def get_report_by_id(report_id: str) -> dict:
    with session_scope() as session:
        stored = ReportHistoryService(DailyReportRepository(session)).get_by_id(report_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Report not found.")
        return stored.model_dump()


@app.get("/reports/daily/{report_id}/replay")
def replay_report(report_id: str) -> dict:
    with session_scope() as session:
        replay = ReportHistoryService(DailyReportRepository(session)).replay(report_id)
        if replay is None:
            raise HTTPException(status_code=404, detail="Report not found.")
        return replay.model_dump()


@app.get("/reports/daily/{report_id}/evaluation")
def evaluate_report(report_id: str) -> dict:
    with session_scope() as session:
        history = ReportHistoryService(DailyReportRepository(session))
        stored = history.get_by_id(report_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Report not found.")
        evaluation = history.evaluate(stored)
        return evaluation.model_dump()


@app.get("/reports/daily/evaluation/history")
def evaluate_recent_reports(limit: int = 10) -> dict:
    limit = min(max(limit, 1), 50)
    with session_scope() as session:
        history = ReportHistoryService(DailyReportRepository(session))
        reports = history.list_reports(limit=limit)
        summary = history.evaluate_many(reports)
        return summary.model_dump()


@app.get("/reports/daily/{report_id}/pdf")
def export_report_pdf(report_id: str) -> FileResponse:
    with session_scope() as session:
        stored = ReportHistoryService(DailyReportRepository(session)).get_by_id(report_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Report not found.")
    try:
        output_path = ReportPdfService().export(stored)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(path=output_path, media_type="application/pdf", filename=output_path.name)


@app.get("/reports/daily/{report_id}/simulate")
def simulate_report(report_id: str, horizon_days: int = 1) -> dict:
    horizon_days = min(max(horizon_days, 1), 20)
    with session_scope() as session:
        stored = ReportHistoryService(DailyReportRepository(session)).get_by_id(report_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="Report not found.")
    try:
        simulation = SimulationService().simulate(stored, horizon_days=horizon_days)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return simulation.model_dump()


@app.get("/reports/daily/simulate/history")
def simulate_recent_reports(limit: int = 5, horizon_days: int = 1) -> dict:
    limit = min(max(limit, 1), 20)
    horizon_days = min(max(horizon_days, 1), 20)
    with session_scope() as session:
        stored_reports = ReportHistoryService(DailyReportRepository(session)).list_reports(limit=limit)
    try:
        summary = SimulationService().simulate_many(stored_reports, horizon_days=horizon_days)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return summary.model_dump()


@app.post("/backtests/run")
def run_backtest(request: BacktestRequest) -> dict:
    task_id = str(uuid4())
    task_store.set_status(task_id=task_id, task_type="backtest", status="running")
    candidate_symbols = list(request.candidate_symbols)
    if request.include_default_universe:
        settings = get_settings()
        if not candidate_symbols:
            candidate_symbols.extend(load_analysis_candidate_symbols())
        elif settings.universe_name:
            candidate_symbols.extend(load_analysis_candidate_symbols(include_default_universe=True))

    service = ReportService(
        provider=build_market_data_provider(),
        writer=build_report_writer(),
        event_provider=build_event_provider(),
        analyzer=build_report_analyzer(),
    )
    try:
        result = BacktestService(service).run_window(
            positions=request.positions,
            candidate_symbols=list(dict.fromkeys(candidate_symbols)),
            start_date=request.start_date,
            end_date=request.end_date,
            start_mode=request.start_mode,
        )
    except RuntimeError as exc:
        task_store.set_status(
            task_id=task_id,
            task_type="backtest",
            status="failed",
            detail=str(exc),
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    task_store.set_status(
        task_id=task_id,
        task_type="backtest",
        status="completed",
        payload={
            "start_date": request.start_date,
            "end_date": request.end_date,
            "final_nav": result.final_nav,
        },
    )
    response = result.model_dump()
    response["task_id"] = task_id
    return response


@app.get("/reports/daily/{report_id}/orders/plan")
def build_order_plan(report_id: str, total_capital: float | None = None) -> dict:
    with session_scope() as session:
        replay = ReportHistoryService(DailyReportRepository(session)).replay(report_id)
        if replay is None:
            raise HTTPException(status_code=404, detail="Report not found.")
    plan = OrderExecutionService().build_plan(replay, total_capital=total_capital)
    return plan.model_dump()


@app.post("/reports/daily/{report_id}/orders/mock-execute")
def execute_order_plan(report_id: str, total_capital: float | None = None) -> dict:
    task_id = str(uuid4())
    task_store.set_status(task_id=task_id, task_type="execution", status="running")
    with session_scope() as session:
        replay = ReportHistoryService(DailyReportRepository(session)).replay(report_id)
        if replay is None:
            task_store.set_status(
                task_id=task_id,
                task_type="execution",
                status="failed",
                detail="Report not found.",
            )
            raise HTTPException(status_code=404, detail="Report not found.")
    service = OrderExecutionService()
    plan = service.build_plan(replay, total_capital=total_capital)
    result = service.execute_mock(plan)
    task_store.set_status(
        task_id=task_id,
        task_type="execution",
        status="completed",
        payload={"execution_id": result.execution_id, "plan_id": plan.plan_id},
    )
    response = result.model_dump()
    response["task_id"] = task_id
    return response


@app.get("/tasks/{task_id}")
def get_task_status(task_id: str) -> dict:
    payload = task_store.get(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return payload
