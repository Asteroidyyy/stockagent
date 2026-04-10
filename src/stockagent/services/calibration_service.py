from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Any

from stockagent.schemas import (
    CalibrationBucket,
    CalibrationReport,
    SimulationReport,
    StockSignal,
    StoredDailyReport,
)
from stockagent.services.simulation_service import SimulationService


class ModelCalibrationService:
    def __init__(self, simulation_service: SimulationService | None = None) -> None:
        self.simulation_service = simulation_service or SimulationService()

    def calibrate(
        self,
        reports: list[StoredDailyReport],
        *,
        horizon_days: int = 3,
        min_samples: int = 5,
    ) -> CalibrationReport:
        simulations = [
            self.simulation_service.simulate(report, horizon_days=horizon_days)
            for report in reports
        ]
        samples = self._build_samples(reports, simulations)
        valid_samples = [sample for sample in samples if sample["return_pct"] is not None]

        overall = self._bucket("all", valid_samples)
        score_buckets = self._bucket_group(valid_samples, self._score_bucket)
        action_buckets = self._bucket_group(valid_samples, lambda sample: sample["action"])
        market_buckets = self._bucket_group(valid_samples, lambda sample: sample["market_regime"])
        factor_buckets = self._build_factor_buckets(valid_samples)

        return CalibrationReport(
            generated_at=datetime.utcnow().isoformat(),
            horizon_days=horizon_days,
            report_count=len(reports),
            signal_count=len(samples),
            valid_sample_count=len(valid_samples),
            overall=overall,
            score_buckets=score_buckets,
            action_buckets=action_buckets,
            market_buckets=market_buckets,
            factor_buckets=factor_buckets,
            recommendations=self._recommend(
                score_buckets=score_buckets,
                action_buckets=action_buckets,
                market_buckets=market_buckets,
                factor_buckets=factor_buckets,
                min_samples=min_samples,
            ),
        )

    def _build_samples(
        self,
        reports: list[StoredDailyReport],
        simulations: list[SimulationReport],
    ) -> list[dict[str, Any]]:
        report_by_id = {report.id: report for report in reports}
        samples: list[dict[str, Any]] = []
        for simulation in simulations:
            stored = report_by_id.get(simulation.report_id)
            if stored is None:
                continue
            signal_queue_by_symbol: dict[str, deque[StockSignal]] = defaultdict(deque)
            for signal in stored.report.portfolio_actions + stored.report.watchlist:
                signal_queue_by_symbol[signal.symbol].append(signal)

            for result in simulation.signal_results:
                signal_queue = signal_queue_by_symbol.get(result.symbol)
                if not signal_queue:
                    continue
                signal = signal_queue.popleft()
                samples.append(
                    {
                        "symbol": signal.symbol,
                        "action": signal.action,
                        "market_regime": stored.report.market_summary.regime,
                        "score": signal.score,
                        "score_breakdown": signal.score_breakdown,
                        "risk_flags": signal.risk_flags,
                        "return_pct": result.return_pct,
                        "max_drawdown_pct": result.max_drawdown_pct,
                    }
                )
        return samples

    def _bucket_group(
        self,
        samples: list[dict[str, Any]],
        bucket_fn,
    ) -> list[CalibrationBucket]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for sample in samples:
            grouped[bucket_fn(sample)].append(sample)
        return [self._bucket(name, grouped[name]) for name in sorted(grouped)]

    def _build_factor_buckets(self, samples: list[dict[str, Any]]) -> list[CalibrationBucket]:
        factor_specs = [
            ("activity", "活跃度"),
            ("momentum", "动量"),
            ("drawdown", "回撤"),
            ("volatility", "波动"),
            ("market", "市场"),
        ]
        buckets: list[CalibrationBucket] = []
        for key, label in factor_specs:
            positive = [
                sample
                for sample in samples
                if sample["score_breakdown"].get(key, 0.0) > 0
            ]
            negative = [
                sample
                for sample in samples
                if sample["score_breakdown"].get(key, 0.0) < 0
            ]
            neutral = [
                sample
                for sample in samples
                if sample["score_breakdown"].get(key, 0.0) == 0
            ]
            buckets.append(self._bucket(f"{label}>0", positive))
            buckets.append(self._bucket(f"{label}=0", neutral))
            buckets.append(self._bucket(f"{label}<0", negative))
        return buckets

    def _bucket(self, name: str, samples: list[dict[str, Any]]) -> CalibrationBucket:
        returns = [float(sample["return_pct"]) for sample in samples if sample["return_pct"] is not None]
        drawdowns = [
            float(sample["max_drawdown_pct"])
            for sample in samples
            if sample["max_drawdown_pct"] is not None
        ]
        return CalibrationBucket(
            name=name,
            sample_count=len(returns),
            avg_return_pct=round(sum(returns) / len(returns), 4) if returns else 0.0,
            win_rate=round(sum(1 for value in returns if value > 0) / len(returns), 4)
            if returns
            else 0.0,
            avg_max_drawdown_pct=round(sum(drawdowns) / len(drawdowns), 4)
            if drawdowns
            else 0.0,
        )

    def _score_bucket(self, sample: dict[str, Any]) -> str:
        score = float(sample["score"])
        if score >= 90:
            return "score>=90"
        if score >= 85:
            return "85<=score<90"
        if score >= 75:
            return "75<=score<85"
        if score >= 60:
            return "60<=score<75"
        return "score<60"

    def _recommend(
        self,
        *,
        score_buckets: list[CalibrationBucket],
        action_buckets: list[CalibrationBucket],
        market_buckets: list[CalibrationBucket],
        factor_buckets: list[CalibrationBucket],
        min_samples: int,
    ) -> list[str]:
        recommendations: list[str] = []
        by_name = {bucket.name: bucket for bucket in score_buckets + action_buckets + market_buckets + factor_buckets}

        buy_more = by_name.get("buy_more")
        if buy_more and buy_more.sample_count >= min_samples:
            if buy_more.avg_return_pct <= 0 or buy_more.win_rate < 0.5:
                recommendations.append("buy_more 信号后验表现偏弱，建议提高买入分数阈值或降低单次目标仓位。")
            elif buy_more.avg_return_pct >= 0.01 and buy_more.win_rate >= 0.58:
                recommendations.append("buy_more 信号后验表现较好，可保持当前买入阈值。")

        high_score = by_name.get("score>=90")
        mid_score = by_name.get("75<=score<85")
        if high_score and mid_score and high_score.sample_count >= min_samples and mid_score.sample_count >= min_samples:
            if high_score.avg_return_pct <= mid_score.avg_return_pct:
                recommendations.append("高分组未跑赢中分组，建议下调趋势/动量权重或检查过热扣分是否不足。")

        activity_positive = by_name.get("活跃度>0")
        activity_negative = by_name.get("活跃度<0")
        if (
            activity_positive
            and activity_negative
            and activity_positive.sample_count >= min_samples
            and activity_negative.sample_count >= min_samples
        ):
            spread = activity_positive.avg_return_pct - activity_negative.avg_return_pct
            if spread >= 0.005:
                recommendations.append("活跃度正分显著优于负分，建议保留或小幅强化成交活跃度因子。")
            elif spread <= 0:
                recommendations.append("活跃度因子暂未贡献正向区分度，建议降低其权重并继续观察样本。")

        weak_market = by_name.get("弱势")
        if weak_market and weak_market.sample_count >= min_samples:
            if weak_market.avg_return_pct < 0 and weak_market.win_rate < 0.45:
                recommendations.append("弱势市场信号整体后验偏弱，建议收紧弱势试探仓条件。")

        if not recommendations:
            recommendations.append("有效样本不足或分桶差异不明显，建议继续积累报告后再调整规则。")
        return recommendations
