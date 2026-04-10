from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from stockagent.schemas import DailyReport
from stockagent.storage.models import DailyReportRecord


class DailyReportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(
        self,
        report: DailyReport,
        *,
        context: dict | None = None,
        metadata: dict | None = None,
    ) -> DailyReportRecord:
        record = DailyReportRecord(
            trade_date=report.trade_date,
            market_regime=report.market_summary.regime,
            report_json=report.model_dump(),
            metadata_json=metadata,
            context_json=context,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def list_reports(self, *, limit: int = 20) -> list[DailyReportRecord]:
        statement = (
            select(DailyReportRecord)
            .order_by(desc(DailyReportRecord.trade_date), desc(DailyReportRecord.created_at))
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def get_latest(self) -> DailyReportRecord | None:
        statement = select(DailyReportRecord).order_by(
            desc(DailyReportRecord.trade_date),
            desc(DailyReportRecord.created_at),
        )
        return self.session.scalars(statement.limit(1)).first()

    def get_by_id(self, report_id: str) -> DailyReportRecord | None:
        return self.session.get(DailyReportRecord, report_id)
