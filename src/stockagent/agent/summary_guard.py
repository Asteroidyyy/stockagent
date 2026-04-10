from __future__ import annotations

from stockagent.schemas import DailyReport


def summary_matches_report(summary: str, report: DailyReport) -> bool:
    if not summary.strip():
        return False

    allowed_symbols = {item.symbol for item in report.portfolio_actions} | {
        item.symbol for item in report.watchlist
    }
    tokens = summary.replace("（", " ").replace("）", " ").replace("(", " ").replace(")", " ").split()
    mentioned_symbols = {token.strip(",.，。") for token in tokens if "." in token and len(token) >= 8}

    if mentioned_symbols and not mentioned_symbols.issubset(allowed_symbols):
        return False

    if report.market_summary.regime and report.market_summary.regime not in summary:
        return False

    return True
