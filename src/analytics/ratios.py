"""Fundamental ratios: growth, profitability, leverage, liquidity, cash flow, bank metrics.

Every metric is a pure function: it takes a wide statement table and returns a
Series on the same index. The table has one row per (ticker, period_end_date)
and one column per canonical line item, for a single period type.

The table holds values **in the company's reporting currency** (the figures as
reported). Ratios and margins are currency-invariant, and growth is computed in
the reporting currency so that exchange-rate movements do not distort it.

A metric returns NaN whenever an input is missing or a denominator is not
positive where it must be. Nothing is ever assumed to be 0.
"""

import numpy as np
import pandas as pd

from src.analytics.registry import LENDERS, NON_FINANCIAL, register

# ------------------------------------------------------------- helpers ----

def column(df: pd.DataFrame, name: str) -> pd.Series:
    """A line-item column, or all-NaN if the table has no such column."""
    if name in df.columns:
        return df[name].astype("float64")
    return pd.Series(np.nan, index=df.index, dtype="float64")


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """numerator / denominator, NaN where the denominator is zero or either side is missing."""
    return numerator / denominator.where(denominator != 0)


def ratio_to_positive(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """numerator / denominator, NaN unless the denominator is strictly positive."""
    return numerator / denominator.where(denominator > 0)


def prior_year(series: pd.Series) -> pd.Series:
    """For each (ticker, period_end_date), the value exactly one year earlier.

    Looked up by date, not by row position, so a gap in the history gives NaN
    instead of silently comparing periods two years apart. Works for annual
    periods and for quarterly ones (same quarter of the previous year).
    """
    tickers = series.index.get_level_values(0)
    ends = pd.to_datetime(series.index.get_level_values(1))
    previous_ends = (ends - pd.DateOffset(years=1)) + pd.offsets.MonthEnd(0)
    lookup = pd.MultiIndex.from_arrays([tickers, previous_ends.date])
    return pd.Series(series.reindex(lookup).to_numpy(), index=series.index, dtype="float64")


def yoy_growth(series: pd.Series) -> pd.Series:
    """(current - prior) / prior, NaN if the prior-year value is missing or not positive.

    Growth from a zero or negative base is not meaningful (a move from -10 to
    +5 is not "-150% growth"), so it is left undefined.
    """
    previous = prior_year(series)
    return (series - previous) / previous.where(previous > 0)


def average_with_prior(series: pd.Series) -> pd.Series:
    """Mean of the current and prior-year balance; the current balance alone if there is no prior.

    Used for the denominator of return ratios. The fallback is recorded by the
    caller through the metric's input fields.
    """
    previous = prior_year(series)
    return ((series + previous) / 2).where(previous.notna(), series)


def liquid_cash(df: pd.DataFrame) -> pd.Series:
    """Cash and short-term investments; cash and equivalents where that is not reported."""
    return column(df, "cash_and_short_term_investments").fillna(column(df, "cash_and_equivalents"))


def eps_on_current_basis(df: pd.DataFrame) -> pd.Series:
    """Net income attributable to shareholders / adjusted shares.

    `adjusted_shares` is the period's share count restated to the current
    (post-split, post-bonus) basis by analytics/shares.py: weighted-average
    shares where available, otherwise period-end shares. The source's own
    reported EPS is never used here. EPS may be negative (a loss); it is null
    only when net income or the share count is missing.
    """
    return ratio_to_positive(column(df, "net_income"), column(df, "adjusted_shares"))


# -------------------------------------------------------------- growth ----

@register("revenue_growth", formula_id="F_REVENUE_GROWTH", unit="pct",
          expression="(revenue_t - revenue_t-1) / revenue_t-1",
          description="Year-on-year revenue growth in the reporting currency. "
                      "Null if the prior value is missing or not positive.",
          inputs=("revenue",), prior_inputs=("revenue",))
def revenue_growth(df: pd.DataFrame) -> pd.Series:
    return yoy_growth(column(df, "revenue"))


@register("ebitda_growth", formula_id="F_EBITDA_GROWTH", unit="pct",
          expression="(ebitda_t - ebitda_t-1) / ebitda_t-1", applicable=NON_FINANCIAL,
          description="Year-on-year EBITDA growth in the reporting currency.",
          inputs=("ebitda",), prior_inputs=("ebitda",))
def ebitda_growth(df: pd.DataFrame) -> pd.Series:
    return yoy_growth(column(df, "ebitda"))


@register("net_income_growth", formula_id="F_NET_INCOME_GROWTH", unit="pct",
          expression="(net_income_t - net_income_t-1) / net_income_t-1",
          description="Year-on-year net income growth in the reporting currency.",
          inputs=("net_income",), prior_inputs=("net_income",))
def net_income_growth(df: pd.DataFrame) -> pd.Series:
    return yoy_growth(column(df, "net_income"))


@register("eps", formula_id="F_EPS", unit="per_share",
          expression="net_income / adjusted_shares",
          description="Net income attributable to shareholders over weighted-average shares "
                      "(period-end shares if unavailable), restated to the current share basis "
                      "for splits and bonus issues. In the reporting currency.",
          inputs=("net_income", "adjusted_shares"))
def eps(df: pd.DataFrame) -> pd.Series:
    return eps_on_current_basis(df)


@register("eps_growth", formula_id="F_EPS_GROWTH", unit="pct",
          expression="(eps_t - eps_t-1) / eps_t-1, eps = net_income / adjusted_shares",
          description="Year-on-year growth in EPS on the current share basis, so a split or "
                      "bonus issue does not appear as a fall in EPS. In the reporting currency.",
          inputs=("net_income", "adjusted_shares"),
          prior_inputs=("net_income", "adjusted_shares"))
def eps_growth(df: pd.DataFrame) -> pd.Series:
    return yoy_growth(eps_on_current_basis(df))


@register("fcf_growth", formula_id="F_FCF_GROWTH", unit="pct",
          expression="(fcf_t - fcf_t-1) / fcf_t-1", applicable=NON_FINANCIAL,
          description="Year-on-year free cash flow growth in the reporting currency.",
          inputs=("free_cash_flow",), prior_inputs=("free_cash_flow",))
def fcf_growth(df: pd.DataFrame) -> pd.Series:
    return yoy_growth(column(df, "free_cash_flow"))


# ------------------------------------------------------- profitability ----

@register("gross_margin", formula_id="F_GROSS_MARGIN", unit="pct",
          expression="gross_profit / revenue", applicable=NON_FINANCIAL,
          inputs=("gross_profit", "revenue"))
def gross_margin(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "gross_profit"), column(df, "revenue"))


@register("ebitda_margin", formula_id="F_EBITDA_MARGIN", unit="pct",
          expression="ebitda / revenue", applicable=NON_FINANCIAL,
          inputs=("ebitda", "revenue"))
def ebitda_margin(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "ebitda"), column(df, "revenue"))


@register("ebit_margin", formula_id="F_EBIT_MARGIN", unit="pct",
          expression="ebit / revenue", applicable=NON_FINANCIAL,
          inputs=("ebit", "revenue"))
def ebit_margin(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "ebit"), column(df, "revenue"))


@register("net_margin", formula_id="F_NET_MARGIN", unit="pct",
          expression="net_income / revenue", inputs=("net_income", "revenue"))
def net_margin(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "net_income"), column(df, "revenue"))


@register("roe", formula_id="F_ROE", unit="pct",
          expression="net_income / average(total_equity_t, total_equity_t-1)",
          description="Return on average shareholders' equity. Uses ending equity when the "
                      "prior year-end is unavailable. Annual periods only.",
          inputs=("net_income", "total_equity"), prior_inputs=("total_equity",),
          annual_only=True)
def roe(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "net_income"),
                             average_with_prior(column(df, "total_equity")))


@register("roa", formula_id="F_ROA", unit="pct",
          expression="net_income / average(total_assets_t, total_assets_t-1)",
          description="Return on average total assets. Uses ending assets when the prior "
                      "year-end is unavailable. Annual periods only.",
          inputs=("net_income", "total_assets"), prior_inputs=("total_assets",),
          annual_only=True)
def roa(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "net_income"),
                             average_with_prior(column(df, "total_assets")))


# ------------------------------------------------------------ leverage ----

@register("debt_to_equity", formula_id="F_DEBT_TO_EQUITY", unit="ratio",
          expression="total_debt / total_equity", applicable=NON_FINANCIAL,
          description="Null when equity is not positive.",
          inputs=("total_debt", "total_equity"))
def debt_to_equity(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "total_debt"), column(df, "total_equity"))


@register("debt_to_assets", formula_id="F_DEBT_TO_ASSETS", unit="ratio",
          expression="total_debt / total_assets", applicable=NON_FINANCIAL,
          inputs=("total_debt", "total_assets"))
def debt_to_assets(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "total_debt"), column(df, "total_assets"))


@register("net_debt_to_ebitda", formula_id="F_NET_DEBT_TO_EBITDA", unit="multiple",
          expression="(total_debt - cash) / ebitda", applicable=NON_FINANCIAL,
          description="Cash is cash and short-term investments, or cash and equivalents where "
                      "that is not reported. Negative means net cash. Null when EBITDA is not "
                      "positive. Annual periods only.",
          inputs=("total_debt", "cash_and_short_term_investments", "cash_and_equivalents",
                  "ebitda"),
          annual_only=True)
def net_debt_to_ebitda(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "total_debt") - liquid_cash(df), column(df, "ebitda"))


# ----------------------------------------------------------- liquidity ----

@register("current_ratio", formula_id="F_CURRENT_RATIO", unit="ratio",
          expression="current_assets / current_liabilities", applicable=NON_FINANCIAL,
          inputs=("current_assets", "current_liabilities"))
def current_ratio(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "current_assets"), column(df, "current_liabilities"))


@register("quick_ratio", formula_id="F_QUICK_RATIO", unit="ratio",
          expression="(current_assets - inventory) / current_liabilities",
          applicable=NON_FINANCIAL,
          description="Null when inventory is not reported: it is never assumed to be zero.",
          inputs=("current_assets", "inventory", "current_liabilities"))
def quick_ratio(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "current_assets") - column(df, "inventory"),
                             column(df, "current_liabilities"))


# ----------------------------------------------------------- cash flow ----

@register("fcf_margin", formula_id="F_FCF_MARGIN", unit="pct",
          expression="free_cash_flow / revenue", applicable=NON_FINANCIAL,
          inputs=("free_cash_flow", "revenue"))
def fcf_margin(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "free_cash_flow"), column(df, "revenue"))


@register("ocf_margin", formula_id="F_OCF_MARGIN", unit="pct",
          expression="operating_cash_flow / revenue", applicable=NON_FINANCIAL,
          inputs=("operating_cash_flow", "revenue"))
def ocf_margin(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "operating_cash_flow"), column(df, "revenue"))


@register("capex_to_revenue", formula_id="F_CAPEX_TO_REVENUE", unit="pct",
          expression="capex / revenue", applicable=NON_FINANCIAL,
          description="Capex is a positive outflow.", inputs=("capex", "revenue"))
def capex_to_revenue(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "capex"), column(df, "revenue"))


# -------------------------------------------------- banks and NBFCs only ----

@register("nii_growth", formula_id="F_NII_GROWTH", unit="pct",
          expression="(nii_t - nii_t-1) / nii_t-1", applicable=LENDERS,
          description="Year-on-year growth in net interest income.",
          inputs=("net_interest_income",), prior_inputs=("net_interest_income",))
def nii_growth(df: pd.DataFrame) -> pd.Series:
    return yoy_growth(column(df, "net_interest_income"))


@register("cost_to_income", formula_id="F_COST_TO_INCOME", unit="pct",
          expression="operating_expenses / revenue", applicable=LENDERS,
          description="N/A unless the source provides operating expenses for the bank.",
          inputs=("operating_expenses", "revenue"))
def cost_to_income(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "operating_expenses"), column(df, "revenue"))


@register("loan_to_deposit", formula_id="F_LOAN_TO_DEPOSIT", unit="pct",
          expression="total_loans / total_deposits", applicable=LENDERS,
          description="N/A unless the source provides both loans and deposits.",
          inputs=("total_loans", "total_deposits"))
def loan_to_deposit(df: pd.DataFrame) -> pd.Series:
    return ratio_to_positive(column(df, "total_loans"), column(df, "total_deposits"))
