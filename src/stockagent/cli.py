import argparse
import json

from stockagent.agent.analyzer_factory import build_report_analyzer
from stockagent.agent.factory import build_report_writer
from stockagent.config import get_settings
from stockagent.data.factory import build_market_data_provider
from stockagent.events.factory import build_event_provider
from stockagent.schemas import StoredDailyReport
from stockagent.services.backtest_service import BacktestService
from stockagent.services.calibration_service import ModelCalibrationService
from stockagent.services.order_execution_service import OrderExecutionService
from stockagent.services.report_history_service import ReportHistoryService
from stockagent.services.report_service import ReportService
from stockagent.storage.database import init_database, session_scope
from stockagent.storage.repository import DailyReportRepository
from stockagent.utils.baseline_runner import run_baseline_cases
from stockagent.utils.input_loader import (
    load_analysis_candidate_symbols,
    load_positions,
    save_candidate_symbols,
)
from stockagent.utils.metadata import build_run_metadata
from stockagent.universe.factory import build_universe_loader


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stockagent")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("report")
    subparsers.add_parser("baseline")
    subparsers.add_parser("latest")

    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("target", choices=["csi500"])
    fetch.add_argument("--limit", type=int, default=None)

    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("target", choices=["today"])

    show = subparsers.add_parser("show")
    show.add_argument("target", choices=["candidates"])

    backtest = subparsers.add_parser("backtest")
    backtest.add_argument("--start-date", required=True)
    backtest.add_argument("--end-date", required=True)
    backtest.add_argument("--start-mode", choices=["from_cash", "rebalance"], default="from_cash")

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--horizon-days", type=int, default=3)
    calibrate.add_argument("--limit", type=int, default=30)
    calibrate.add_argument("--min-samples", type=int, default=5)

    order_plan = subparsers.add_parser("plan-orders")
    order_plan.add_argument("--report-id", required=True)
    order_plan.add_argument("--total-capital", type=float, default=None)

    execute = subparsers.add_parser("execute-mock")
    execute.add_argument("--report-id", required=True)
    execute.add_argument("--total-capital", type=float, default=None)

    return parser.parse_args()


def build_report_service() -> ReportService:
    return ReportService(
        provider=build_market_data_provider(),
        writer=build_report_writer(),
        event_provider=build_event_provider(),
        analyzer=build_report_analyzer(),
    )


def format_report_summary(stored: StoredDailyReport) -> str:
    report = stored.report
    lines = [
        f"报告ID: {stored.id}",
        f"交易日: {stored.trade_date}",
        f"市场状态: {report.market_summary.regime}",
        f"市场摘要: {report.market_summary.summary}",
        (
            "组合概览: "
            f"当前仓位 {report.portfolio_summary.current_exposure:.0%}, "
            f"执行目标仓位 {report.portfolio_summary.target_exposure:.0%}, "
            f"偏向 {report.portfolio_summary.rebalance_bias}"
        ),
        (
            "风险预算: "
            f"目标股票仓位 {report.cash_exposure_target:.0%}, "
            f"目标空仓比例 {1 - report.cash_exposure_target:.0%}"
        ),
    ]
    if report.watchlist:
        lines.append("观察名单:")
        for item in report.watchlist[:10]:
            target = f", 目标仓位 {item.target_weight:.0%}" if item.target_weight is not None else ""
            lines.append(f"- {item.symbol} {item.action}，分数 {item.score:.1f}{target}")
    else:
        lines.append("观察名单: 暂无")

    if report.portfolio_actions:
        lines.append("持仓建议:")
        for item in report.portfolio_actions:
            target = f", 目标仓位 {item.target_weight:.0%}" if item.target_weight is not None else ""
            lines.append(f"- {item.symbol} {item.action}，分数 {item.score:.1f}{target}")
    else:
        lines.append("持仓建议: 当前无持仓，无存量仓位需要调整")

    lines.append(f"风险提示: {'；'.join(report.risk_alerts) if report.risk_alerts else '暂无'}")
    if report.llm_summary:
        lines.append("")
        lines.append("摘要:")
        lines.append(report.llm_summary)
    return "\n".join(lines)


def run_report() -> None:
    positions = load_positions()
    candidate_symbols = load_analysis_candidate_symbols()
    if not positions and not candidate_symbols:
        raise SystemExit(
            "No positions or candidate symbols found. Populate PORTFOLIO_FILE, CANDIDATE_FILE, or enable a default universe."
        )
    service = build_report_service()
    report = service.build_daily_report(positions, candidate_symbols=candidate_symbols)
    with session_scope() as session:
        stored = ReportHistoryService(DailyReportRepository(session)).save_report(
            report,
            context=service.last_context,
            metadata=build_run_metadata(),
        )
    print(format_report_summary(stored))


def run_fetch(target: str, limit: int | None) -> None:
    if target != "csi500":
        raise SystemExit(f"Unsupported fetch target: {target}")
    symbols = build_universe_loader().load_symbols(limit=limit)
    save_candidate_symbols(symbols)
    print(f"已更新 candidates.json，共写入 {len(symbols)} 只股票。")
    preview = "、".join(symbols[:10]) if symbols else "暂无"
    print(f"前10只: {preview}")


def run_analyze(target: str) -> None:
    if target != "today":
        raise SystemExit(f"Unsupported analyze target: {target}")
    run_report()


def run_latest() -> None:
    with session_scope() as session:
        stored = ReportHistoryService(DailyReportRepository(session)).get_latest()
    if stored is None:
        raise SystemExit("No report history found.")
    print(format_report_summary(stored))


def run_show(target: str) -> None:
    if target != "candidates":
        raise SystemExit(f"Unsupported show target: {target}")
    symbols = load_analysis_candidate_symbols()
    print(f"当前候选池共 {len(symbols)} 只。")
    if not symbols:
        print("暂无候选股票。")
        return
    for index, symbol in enumerate(symbols, start=1):
        print(f"{index:>3}. {symbol}")


def run_backtest(start_date: str, end_date: str, start_mode: str) -> None:
    positions = load_positions()
    candidate_symbols = load_analysis_candidate_symbols()
    if not positions and not candidate_symbols:
        raise SystemExit(
            "No positions or candidate symbols found. Populate PORTFOLIO_FILE, CANDIDATE_FILE, or enable a default universe."
        )
    summary = BacktestService(build_report_service()).run_window(
        positions=positions,
        candidate_symbols=candidate_symbols,
        start_date=start_date,
        end_date=end_date,
        start_mode=start_mode,
    )
    print(json.dumps(summary.model_dump(), ensure_ascii=False, indent=2))


def run_calibrate(horizon_days: int, limit: int, min_samples: int) -> None:
    with session_scope() as session:
        reports = ReportHistoryService(DailyReportRepository(session)).list_reports(limit=limit)
    if not reports:
        raise SystemExit("No report history found. Run stockagent analyze today first.")
    calibration = ModelCalibrationService().calibrate(
        reports,
        horizon_days=horizon_days,
        min_samples=min_samples,
    )
    print(json.dumps(calibration.model_dump(), ensure_ascii=False, indent=2))


def build_or_execute_orders(report_id: str, total_capital: float | None, *, execute: bool) -> None:
    with session_scope() as session:
        replay = ReportHistoryService(DailyReportRepository(session)).replay(report_id)
        if replay is None:
            raise SystemExit(f"Report not found: {report_id}")
    service = OrderExecutionService()
    plan = service.build_plan(replay, total_capital=total_capital)
    if not execute:
        print(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2))
        return
    result = service.execute_mock(plan)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    settings = get_settings()
    init_database()
    command = args.command or ("baseline" if settings.run_baseline_mode else "report")

    if command == "baseline":
        baseline_results = run_baseline_cases()
        print(
            json.dumps(
                {"baseline_results": [item.model_dump() for item in baseline_results]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if command == "report":
        run_report()
        return
    if command == "fetch":
        run_fetch(args.target, args.limit)
        return
    if command == "analyze":
        run_analyze(args.target)
        return
    if command == "latest":
        run_latest()
        return
    if command == "show":
        run_show(args.target)
        return
    if command == "backtest":
        run_backtest(args.start_date, args.end_date, args.start_mode)
        return
    if command == "calibrate":
        run_calibrate(args.horizon_days, args.limit, args.min_samples)
        return
    if command == "plan-orders":
        build_or_execute_orders(args.report_id, args.total_capital, execute=False)
        return
    if command == "execute-mock":
        build_or_execute_orders(args.report_id, args.total_capital, execute=True)
        return
    raise SystemExit(f"Unsupported command: {command}")


if __name__ == "__main__":
    main()
