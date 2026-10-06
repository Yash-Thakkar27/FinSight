"""Metric cards: a value, or N/A with its reason, plus the flags the methodology requires."""

import pandas as pd

from components.formatting import NA, format_or_reason, format_value, label

GROWTH_METRICS = {"revenue_growth", "ebitda_growth", "net_income_growth", "eps_growth",
                  "fcf_growth", "nii_growth"}


def latest_metric(metrics: pd.DataFrame, name: str, period_type: str) -> pd.Series | None:
    """The most recent row of a metric for one company (a company_metrics frame)."""
    rows = metrics[(metrics["metric_name"] == name) & (metrics["period_type"] == period_type)]
    return None if rows.empty else rows.sort_values("period_end_date").iloc[-1]


def footnotes(row: pd.Series | None, name: str) -> list[str]:
    """Notes a figure must carry: translation, reporting-currency growth, fallback methods."""
    if row is None or pd.isna(row["value"]):
        return []
    notes = []
    currency = row.get("reporting_currency", "INR")
    if row.get("is_translated"):
        notes.append(f"translated from {currency}")
    if name in GROWTH_METRICS and currency != "INR":
        notes.append(f"reporting-currency growth ({currency})")
    if row.get("method") == "closing_balance":
        notes.append("closing balance used (no prior year-end)")
    if row.get("method") == "latest_annual":
        notes.append("latest annual figure (TTM unavailable)")
    return notes


def metric_card(column, metrics: pd.DataFrame, name: str, period_type: str,
                title: str | None = None) -> None:
    """Draw one card. A missing value shows N/A and the stored reason underneath."""
    row = latest_metric(metrics, name, period_type)
    title = title or label(name)
    if row is None:
        column.metric(title, NA)
        column.caption("not computed")
        return
    currency = row.get("reporting_currency", "INR")
    if pd.isna(row["value"]):
        column.metric(title, NA)
        reason = row["na_reason"] or ""
        column.caption(reason.replace("N/A (", "").rstrip(")") if reason else "")
        return
    column.metric(title, format_value(row["value"], row["unit"], currency))
    notes = footnotes(row, name)
    if notes:
        column.caption("; ".join(notes))


def value_text(row: pd.Series | None) -> str:
    if row is None:
        return NA
    return format_or_reason(row["value"], row["unit"], row["na_reason"],
                            row.get("reporting_currency", "INR"))
