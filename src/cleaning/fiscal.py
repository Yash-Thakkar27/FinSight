"""Indian fiscal-period conventions: the fiscal year runs April to March."""

from datetime import date

import pandas as pd


def fiscal_year(period_end: date) -> int:
    """FY label year: the calendar year in which the fiscal year ends.

    31-Mar-2025 -> 2025 (FY2025); 30-Jun-2025 -> 2026 (it falls in FY2026).
    """
    return period_end.year + 1 if period_end.month >= 4 else period_end.year


def fiscal_quarter(period_end: date) -> int:
    """Fiscal quarter of a date: Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar."""
    return ((period_end.month - 4) % 12) // 3 + 1


def fiscal_label(period_end: date, period_type: str) -> str:
    """'FY2025' for annual periods, 'Q1 FY2026' for quarterly ones."""
    if period_type == "annual":
        return f"FY{fiscal_year(period_end)}"
    return f"Q{fiscal_quarter(period_end)} FY{fiscal_year(period_end)}"


def period_start(period_end: date, period_type: str) -> date:
    """First day of the period: 12 months before the day after period end (3 for quarters)."""
    months = 12 if period_type == "annual" else 3
    start = pd.Timestamp(period_end) + pd.Timedelta(days=1) - pd.DateOffset(months=months)
    return start.date()
