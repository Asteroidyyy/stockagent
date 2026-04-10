from __future__ import annotations

from collections import Counter
from datetime import datetime

from stockagent.schemas import (
    BaselineRunResult,
    DailyReport,
    EvaluationBatchSummary,
    ReplayBundle,
    ReportEvaluation,
    StoredDailyReport,
)
from stockagent.storage.models import DailyReportRecord
from stockagent.storage.repository import DailyReportRepository


class ReportHistoryService:
    def __init__(self, repository: DailyReportRepository) -> None:
        self.repository = repository

    def save_report(
        self,
        report: DailyReport,
        *,
        context: dict | None = None,
        metadata: dict | None = None,
    ) -> StoredDailyReport:
        record = self.repository.save(report, context=context, metadata=metadata)
        return self._to_schema(record)

    def list_reports(self, *, limit: int = 20) -> list[StoredDailyReport]:
        return [self._to_schema(record) for record in self.repository.list_reports(limit=limit)]

    def get_latest(self) -> StoredDailyReport | None:
        record = self.repository.get_latest()
        return self._to_schema(record) if record else None

    def get_by_id(self, report_id: str) -> StoredDailyReport | None:
        record = self.repository.get_by_id(report_id)
        return self._to_schema(record) if record else None

    def replay(self, report_id: str) -> ReplayBundle | None:
        record = self.repository.get_by_id(report_id)
        if record is None:
            return None
        stored = self._to_schema(record)
        evaluation = self.evaluate(stored)
        return ReplayBundle(
            stored_report=stored,
            analysis_context=record.context_json or {},
            evaluation=evaluation,
        )

    def evaluate(self, stored: StoredDailyReport) -> ReportEvaluation:
        report = stored.report
        strengths: list[str] = []
        weaknesses: list[str] = []

        reduce_count = sum(1 for item in report.portfolio_actions if item.action == "reduce")
        watch_count = len(report.watchlist)
        risk_flag_count = sum(len(item.risk_flags) for item in report.portfolio_actions)
        target_exposure_gap = abs(
            report.portfolio_summary.target_exposure - report.cash_exposure_target
        )
        complete_signal_fields = sum(
            1
            for item in report.portfolio_actions + report.watchlist
            if item.symbol and item.action and item.reasons and item.score_breakdown and item.score_explanations
        )
        total_signals = len(report.portfolio_actions) + len(report.watchlist)
        structure_completeness = (
            complete_signal_fields / total_signals if total_signals else 0.0
        )
        weak_market_risk_consistency = (
            1.0
            if report.market_summary.regime not in {"弱势", "震荡偏弱"} or reduce_count > 0
            else 0.0
        )
        exposure_consistency = max(0.0, 1 - min(target_exposure_gap / 0.3, 1.0))
        risk_coverage = min(risk_flag_count / max(len(report.portfolio_actions), 1), 1.0)

        score = 70.0
        if report.market_summary.breadth is not None:
            strengths.append("包含市场宽度信息")
            score += 5
        else:
            weaknesses.append("缺少市场宽度")
            score -= 10

        if report.risk_alerts:
            strengths.append("包含显式风险提示")
            score += 5
        else:
            weaknesses.append("风险提示为空，可能低估隐性风险")
            score -= 5

        if reduce_count > 0 and report.market_summary.regime in {"弱势", "震荡偏弱"}:
            strengths.append("弱市环境下执行了主动降仓")
            score += 5

        if target_exposure_gap > 0.15:
            weaknesses.append("目标仓位与现金暴露目标偏离较大")
            score -= 10

        if watch_count == 0:
            weaknesses.append("没有观察名单，后续机会跟踪不足")
            score -= 5

        if risk_flag_count == 0:
            weaknesses.append("持仓建议缺少风险标记")
            score -= 5
        else:
            strengths.append("持仓建议附带风险标记")

        if structure_completeness >= 0.9:
            strengths.append("结构化字段完整度高")
            score += 5
        else:
            weaknesses.append("部分信号缺少评分拆解或解释")
            score -= 5

        if exposure_consistency >= 0.8:
            strengths.append("目标仓位与风险预算基本一致")
            score += 5
        else:
            weaknesses.append("目标仓位与风险预算一致性不足")
            score -= 5

        if weak_market_risk_consistency < 1.0:
            weaknesses.append("弱市环境下风险动作不足")
            score -= 5

        return ReportEvaluation(
            report_id=stored.id,
            score=max(0.0, min(100.0, score)),
            strengths=strengths,
            weaknesses=weaknesses,
            metrics={
                "reduce_count": reduce_count,
                "watch_count": watch_count,
                "risk_flag_count": risk_flag_count,
                "target_exposure": round(report.portfolio_summary.target_exposure, 4),
                "cash_exposure_target": round(report.cash_exposure_target, 4),
                "structure_completeness": round(structure_completeness, 4),
                "risk_consistency": round(weak_market_risk_consistency, 4),
                "exposure_consistency": round(exposure_consistency, 4),
                "risk_coverage": round(risk_coverage, 4),
            },
        )

    def evaluate_many(self, reports: list[StoredDailyReport]) -> EvaluationBatchSummary:
        evaluations = [self.evaluate(report) for report in reports]
        metric_names = {
            key
            for evaluation in evaluations
            for key, value in evaluation.metrics.items()
            if isinstance(value, (int, float))
        }
        average_metrics = {
            name: round(
                sum(float(evaluation.metrics.get(name, 0.0)) for evaluation in evaluations)
                / len(evaluations),
                4,
            )
            for name in sorted(metric_names)
        } if evaluations else {}

        strength_counter = Counter(
            strength
            for evaluation in evaluations
            for strength in evaluation.strengths
        )
        weakness_counter = Counter(
            weakness
            for evaluation in evaluations
            for weakness in evaluation.weaknesses
        )

        average_score = (
            round(sum(evaluation.score for evaluation in evaluations) / len(evaluations), 4)
            if evaluations
            else 0.0
        )
        return EvaluationBatchSummary(
            report_count=len(evaluations),
            generated_at=datetime.utcnow().isoformat(),
            average_score=average_score,
            average_metrics=average_metrics,
            top_strengths=[item for item, _ in strength_counter.most_common(5)],
            top_weaknesses=[item for item, _ in weakness_counter.most_common(5)],
            reports=evaluations,
        )

    def summarize_baseline_result(
        self,
        *,
        case_name: str,
        stored: StoredDailyReport,
    ) -> BaselineRunResult:
        evaluation = self.evaluate(stored)
        output_path = None
        if isinstance(stored.metadata, dict):
            output_path = stored.metadata.get("output_path")
        return BaselineRunResult(
            case_name=case_name,
            report_id=stored.id,
            regime=stored.report.market_summary.regime,
            evaluation_score=evaluation.score,
            output_path=output_path,
        )

    def _to_schema(self, record: DailyReportRecord) -> StoredDailyReport:
        return StoredDailyReport(
            id=record.id,
            trade_date=record.trade_date,
            market_regime=record.market_regime,
            created_at=record.created_at.isoformat(),
            metadata=record.metadata_json or {},
            report=DailyReport.model_validate(record.report_json),
        )
