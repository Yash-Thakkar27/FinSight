"""Valuation: market cap, enterprise value and multiples.

Conventions
-----------
* Everything here is in INR. For a foreign-currency reporter the statement
  inputs are FinSight's translations (flows at the period-average rate,
  balances at the period-end rate), and the result is flagged as translated.
* Current multiples: price on the latest price date, with trailing-twelve-month
  (TTM) flows. TTM is the sum of the last four quarters when all four are
  present and contiguous; otherwise the latest annual figure, labelled as such.
  Each quarter of a foreign-currency reporter is translated at its own average
  rate before summing.
* Historical multiples: price at the fiscal period end and that period's figures.
* Market cap = price x shares outstanding.
* EV = market cap + total debt + minority interest - cash. Non-financials only.
* P/E = market cap / net income and P/B = market cap / shareholders' equity,
  so they do not depend on the source's per-share history.
"""

from datetime import date

import pandas as pd

from src.analytics.registry import ALL, NON_FINANCIAL, register

RECONCILIATION_TOLERANCE_PCT = 10.0


def ttm_value(quarterly: pd.Series, annual: pd.Series, anchor: date | None) -> dict:
    """Trailing-twelve-month value of a flow item for one company.

    quarterly, annual: values indexed by period_end_date (missing values as NaN).
    anchor: the quarter end the TTM window should end on (the company's latest
            reported quarter), or None if it has no quarterly data.

    Returns {'value', 'basis', 'period_end', 'periods'}:
      basis 'ttm'            sum of the four quarters ending at `anchor`
      basis 'latest_annual'  latest annual value, used when the four quarters
                             are not all present
      basis None             neither is available (value None)
    """
    if anchor is not None:
        ends = [(pd.Timestamp(anchor) - pd.DateOffset(months=3 * k) + pd.offsets.MonthEnd(0)).date()
                for k in range(4)]
        values = [quarterly.get(end) for end in ends]
        if all(v is not None and pd.notna(v) for v in values):
            return {"value": float(sum(values)), "basis": "ttm", "period_end": anchor,
                    "periods": [str(end) for end in sorted(ends)]}
    available = annual.dropna()
    if len(available):
        latest = max(available.index)
        return {"value": float(available[latest]), "basis": "latest_annual",
                "period_end": latest, "periods": [str(latest)]}
    return {"value": None, "basis": None, "period_end": None, "periods": []}


def latest_value(*series: pd.Series) -> dict:
    """Most recent non-missing balance-sheet value across the given series."""
    combined = pd.concat([s.dropna() for s in series]) if series else pd.Series(dtype="float64")
    if combined.empty:
        return {"value": None, "period_end": None}
    latest = max(combined.index)
    value = combined[latest]
    if isinstance(value, pd.Series):       # same period end in annual and quarterly
        value = value.iloc[0]
    return {"value": float(value), "period_end": latest}


@register("market_cap", formula_id="F_MARKET_CAP", unit="INR", kind="valuation",
          expression="price * shares_outstanding", applicable=ALL,
          description="Share count is the source's latest, with its as-of date recorded.")
def market_cap(price: float | None, shares: float | None) -> float | None:
    if price is None or shares is None or pd.isna(price) or pd.isna(shares) or shares <= 0:
        return None
    return float(price) * float(shares)


@register("enterprise_value", formula_id="F_ENTERPRISE_VALUE", unit="INR", kind="valuation",
          expression="market_cap + total_debt + minority_interest - cash",
          applicable=NON_FINANCIAL,
          description="Cash is cash and short-term investments, or cash and equivalents where "
                      "that is not reported. Minority interest counts as zero when the source "
                      "does not report one (recorded in the inputs).")
def enterprise_value(market_cap_value: float | None, total_debt: float | None,
                     minority_interest: float | None, cash: float | None) -> float | None:
    """EV = market cap + total debt + minority interest - cash.

    None if market cap, debt or cash is missing. A missing minority interest is
    taken as none reported.
    """
    required = (market_cap_value, total_debt, cash)
    if any(v is None or pd.isna(v) for v in required):
        return None
    minority = 0.0 if minority_interest is None or pd.isna(minority_interest) else minority_interest
    return float(market_cap_value + total_debt + minority - cash)


def multiple(numerator: float | None, denominator: float | None) -> float | None:
    """numerator / denominator; None when the denominator is missing or not positive.

    A multiple on a zero or negative base (P/E of a loss-making company) is not
    meaningful and is left undefined.
    """
    if (numerator is None or denominator is None or pd.isna(numerator) or pd.isna(denominator)
            or denominator <= 0):
        return None
    return float(numerator) / float(denominator)


@register("pe_ratio", formula_id="F_PE", unit="multiple", kind="valuation", applicable=ALL,
          expression="market_cap / net_income",
          description="Current: TTM net income. Historical: that fiscal year's net income. "
                      "Null when net income is not positive.")
def pe_ratio(market_cap_value, net_income):
    return multiple(market_cap_value, net_income)


@register("pb_ratio", formula_id="F_PB", unit="multiple", kind="valuation", applicable=ALL,
          expression="market_cap / total_equity",
          description="Equity attributable to shareholders, latest balance sheet.")
def pb_ratio(market_cap_value, total_equity):
    return multiple(market_cap_value, total_equity)


@register("ev_ebitda", formula_id="F_EV_EBITDA", unit="multiple", kind="valuation",
          applicable=NON_FINANCIAL, expression="enterprise_value / ebitda",
          description="Current: TTM EBITDA. Null when EBITDA is not positive.")
def ev_ebitda(enterprise_value_value, ebitda):
    return multiple(enterprise_value_value, ebitda)


@register("ev_revenue", formula_id="F_EV_REVENUE", unit="multiple", kind="valuation",
          applicable=NON_FINANCIAL, expression="enterprise_value / revenue",
          description="Current: TTM revenue.")
def ev_revenue(enterprise_value_value, revenue):
    return multiple(enterprise_value_value, revenue)


def reconcile(calculated: float | None, source: float | None,
              tolerance_pct: float = RECONCILIATION_TOLERANCE_PCT) -> dict:
    """Compare a calculated figure with the one the source reports.

    difference_pct = (calculated - source) / |source| * 100. Flagged when the
    absolute difference exceeds the tolerance, or when only one side exists.
    """
    have_calculated = calculated is not None and not pd.isna(calculated)
    have_source = source is not None and not pd.isna(source) and source != 0
    if not have_calculated or not have_source:
        if not have_calculated and not have_source:
            note = "neither value available"
        elif have_calculated:
            note = "source does not report this figure"
        else:
            note = "not calculated (see the metric's N/A reason)"
        return {"difference_pct": None, "is_flagged": False, "note": note}
    difference = (calculated - source) / abs(source) * 100
    flagged = abs(difference) > tolerance_pct
    return {"difference_pct": float(difference), "is_flagged": bool(flagged),
            "note": f"differs from the source by more than {tolerance_pct:.0f}%" if flagged
                    else None}
