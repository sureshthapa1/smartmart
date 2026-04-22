from __future__ import annotations

from datetime import date, timedelta

from .ai_advisor_service import AIAdvisorService
from .report_service import ReportService


class DashboardService:
    @staticmethod
    def resolve_range(filter_key: str) -> tuple[date, date]:
        today = date.today()
        if filter_key == "week":
            return today - timedelta(days=today.weekday()), today
        if filter_key == "month":
            return today.replace(day=1), today
        if filter_key == "quarter":
            # Current calendar quarter start
            quarter_start_month = ((today.month - 1) // 3) * 3 + 1
            return today.replace(month=quarter_start_month, day=1), today
        if filter_key == "year":
            return today.replace(month=1, day=1), today
        return today, today  # default: today

    @staticmethod
    def payload(filter_key: str, start: date | None = None, end: date | None = None) -> dict:
        if start is None or end is None:
            start, end = DashboardService.resolve_range(filter_key)

        report = ReportService.dashboard_payload(start, end)
        insights = AIAdvisorService.analyze()
        report["insights"] = insights
        report["range"] = {
            "filter": filter_key,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        return report
