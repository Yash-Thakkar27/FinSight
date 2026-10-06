"""Display formatting for the app.

The formats themselves are defined once, in src/formatting.py, and re-exported
here so pages import them from one place. This module adds only what is
specific to showing a metric row: its N/A reason and its unit.
"""

import pandas as pd

from src.formatting import (  # noqa: F401  (re-exported)
    NA,
    format_crore,
    format_metric,
    format_multiple,
    format_pct,
    format_price,
    format_ratio,
    indian_grouping,
    is_missing,
)

METRIC_LABELS = {
    "revenue_growth": "Revenue growth", "ebitda_growth": "EBITDA growth",
    "net_income_growth": "Net income growth", "eps_growth": "EPS growth", "eps": "EPS",
    "fcf_growth": "FCF growth", "gross_margin": "Gross margin", "ebitda_margin": "EBITDA margin",
    "ebit_margin": "EBIT margin", "net_margin": "Net margin", "roe": "ROE", "roa": "ROA",
    "debt_to_equity": "Debt / equity", "debt_to_assets": "Debt / assets",
    "net_debt_to_ebitda": "Net debt / EBITDA", "current_ratio": "Current ratio",
    "quick_ratio": "Quick ratio", "fcf_margin": "FCF margin", "ocf_margin": "OCF margin",
    "capex_to_revenue": "Capex / revenue", "nii_growth": "NII growth",
    "cost_to_income": "Cost-to-income", "loan_to_deposit": "Loan / deposit",
    "market_cap": "Market cap", "enterprise_value": "Enterprise value", "pe_ratio": "P/E",
    "pb_ratio": "P/B", "ev_ebitda": "EV / EBITDA", "ev_revenue": "EV / Revenue",
    "return_1m": "1M return", "return_3m": "3M return", "return_ytd": "YTD return",
    "return_1y": "1Y return", "cagr_3y": "3Y CAGR", "volatility_1y": "Volatility (1Y)",
    "volatility_3y": "Volatility (3Y)", "downside_deviation_1y": "Downside deviation (1Y)",
    "downside_deviation_3y": "Downside deviation (3Y)", "sharpe_1y": "Sharpe (1Y)",
    "sharpe_3y": "Sharpe (3Y)", "max_drawdown_1y": "Max drawdown (1Y)",
    "max_drawdown_3y": "Max drawdown (3Y)",
}


def label(metric_name: str) -> str:
    return METRIC_LABELS.get(metric_name, metric_name.replace("_", " "))


def format_value(value, unit: str, currency: str = "INR") -> str:
    """Format a metric by unit. Per-share values carry their currency."""
    if is_missing(value) or pd.isna(value):
        return NA
    if unit == "per_share":
        return format_price(value) if currency == "INR" else f"{currency} {value:,.2f}"
    return format_metric(value, unit)


def format_or_reason(value, unit: str, reason: str | None, currency: str = "INR") -> str:
    """The formatted value, or 'N/A' with the stored reason."""
    if is_missing(value) or pd.isna(value):
        if reason and reason.startswith("N/A"):
            return reason
        return f"{NA} ({reason})" if reason else NA
    return format_value(value, unit, currency)


def fiscal_label(period_end, period_type: str) -> str:
    """'FY2026' or 'Q1 FY2027', following the April-March convention."""
    year = period_end.year + 1 if period_end.month >= 4 else period_end.year
    if period_type == "annual":
        return f"FY{year}"
    quarter = ((period_end.month - 4) % 12) // 3 + 1
    return f"Q{quarter} FY{year}"
