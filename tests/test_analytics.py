"""Offline tests for the analytics engine. Expected values are hand-calculated in comments."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from config.settings import Company
from src.analytics import compute, ratios, returns, risk, shares, valuation
from src.analytics.registry import (
    ALL,
    LENDERS,
    NON_FINANCIAL,
    REGISTRY,
    is_applicable,
    not_applicable_reason,
)
from src.cleaning import pipeline

FY24, FY25, FY26 = date(2024, 3, 31), date(2025, 3, 31), date(2026, 3, 31)


def wide(rows: dict) -> pd.DataFrame:
    """rows: {(ticker, period_end): {line_item: value}} -> wide statement table."""
    frame = pd.DataFrame.from_dict(rows, orient="index").astype("float64")
    frame.index = pd.MultiIndex.from_tuples(frame.index, names=["ticker", "period_end_date"])
    return frame.sort_index()


def approx(value, **kwargs):
    return pytest.approx(value, **kwargs)


# --- growth ------------------------------------------------------------------

def test_revenue_growth():
    table = wide({("A", FY24): {"revenue": 100.0}, ("A", FY25): {"revenue": 120.0},
                  ("A", FY26): {"revenue": 90.0}})
    growth = ratios.revenue_growth(table)
    assert pd.isna(growth[("A", FY24)])                 # no prior year
    assert growth[("A", FY25)] == approx(0.20)          # (120 - 100) / 100
    assert growth[("A", FY26)] == approx(-0.25)         # (90 - 120) / 120


def test_growth_from_a_negative_or_zero_base_is_null():
    table = wide({("A", FY24): {"net_income": -10.0}, ("A", FY25): {"net_income": 5.0},
                  ("B", FY24): {"net_income": 0.0}, ("B", FY25): {"net_income": 5.0},
                  ("C", FY24): {"net_income": 10.0}, ("C", FY25): {"net_income": -5.0}})
    growth = ratios.net_income_growth(table)
    assert pd.isna(growth[("A", FY25)])                 # -10 -> 5 is not "-150% growth"
    assert pd.isna(growth[("B", FY25)])                 # zero base
    assert growth[("C", FY25)] == approx(-1.5)          # positive base: (-5 - 10) / 10


def test_growth_is_not_computed_across_a_gap_or_another_company():
    table = wide({("A", FY24): {"revenue": 100.0}, ("A", FY26): {"revenue": 150.0},   # no FY25
                  ("B", FY25): {"revenue": 500.0}})
    growth = ratios.revenue_growth(table)
    assert growth.isna().all()


def test_quarterly_growth_compares_the_same_quarter_a_year_earlier():
    table = wide({("A", date(2025, 3, 31)): {"revenue": 40.0},
                  ("A", date(2025, 6, 30)): {"revenue": 50.0},
                  ("A", date(2026, 6, 30)): {"revenue": 55.0}})
    growth = ratios.revenue_growth(table)
    assert growth[("A", date(2026, 6, 30))] == approx(0.10)     # 55 vs 50, not vs 40


def test_eps_growth_uses_adjusted_shares():
    # EPS 100/10 = 10 -> 132/11 = 12: growth 20%, although net income grew 32%
    table = wide({("A", FY25): {"net_income": 100.0, "adjusted_shares": 10.0},
                  ("A", FY26): {"net_income": 132.0, "adjusted_shares": 11.0}})
    assert ratios.eps(table)[("A", FY26)] == approx(12.0)
    assert ratios.eps_growth(table)[("A", FY26)] == approx(0.20)
    assert ratios.net_income_growth(table)[("A", FY26)] == approx(0.32)
    # a loss gives a negative EPS, and growth from it is undefined the following year
    loss = wide({("A", FY25): {"net_income": -50.0, "adjusted_shares": 10.0},
                 ("A", FY26): {"net_income": 20.0, "adjusted_shares": 10.0}})
    assert ratios.eps(loss)[("A", FY25)] == approx(-5.0)
    assert pd.isna(ratios.eps_growth(loss)[("A", FY26)])


# --- share counts on the current basis ------------------------------------------

BONUS_1_FOR_1 = pd.Series({date(2025, 8, 26): 2.0})     # HDFC Bank's 1:1 bonus issue


def test_cumulative_split_factor():
    splits = pd.Series({date(2024, 10, 28): 5.0, date(2026, 1, 14): 2.0})
    assert shares.cumulative_split_factor(splits, date(2024, 3, 31)) == 10.0   # both follow
    assert shares.cumulative_split_factor(splits, date(2025, 3, 31)) == 2.0    # only the second
    assert shares.cumulative_split_factor(splits, date(2026, 3, 31)) == 1.0    # none follow
    assert shares.cumulative_split_factor(splits, date(2026, 1, 14)) == 1.0    # on the day
    assert shares.cumulative_split_factor(pd.Series(dtype="float64"), FY24) == 1.0
    assert shares.cumulative_split_factor(None, FY24) == 1.0


def test_a_figure_on_the_old_basis_is_restated_and_a_restated_one_is_left_alone():
    # factor 2: the midpoint is 1/sqrt(2) = 0.707 of the reference
    old_basis = shares.restate_to_current_basis(7.107e9, 2.0, 15.19e9)     # ratio 0.468
    assert old_basis.shares == approx(14.214e9) and old_basis.restatement == shares.RESTATED
    assert old_basis.as_reported == 7.107e9 and old_basis.split_factor == 2.0
    restated = shares.restate_to_current_basis(15.32e9, 2.0, 15.30e9)      # ratio 1.001
    assert restated.shares == 15.32e9
    assert restated.restatement == shares.ALREADY_RESTATED
    # no split after the period: nothing to decide
    untouched = shares.restate_to_current_basis(10.0, 1.0, None)
    assert untouched.shares == 10.0 and untouched.restatement == shares.NO_SPLIT
    # a split followed but there is nothing to check against: not guessed
    unknown = shares.restate_to_current_basis(10.0, 2.0, None)
    assert unknown.shares is None and unknown.restatement == shares.UNVERIFIED
    missing = shares.restate_to_current_basis(None, 2.0, 10.0)
    assert missing.shares is None and missing.restatement == shares.NOT_REPORTED


def test_a_decision_near_the_midpoint_is_marked_marginal():
    # HDFC Bank FY2023 period-end shares vs today's count: 11.16bn / 15.42bn = 0.724, just
    # above the 0.707 midpoint, because the 2023 merger also raised the share count
    close = shares.restate_to_current_basis(11.16e9, 2.0, 15.418e9)
    assert close.restatement == shares.ALREADY_RESTATED and close.marginal is True
    clear = shares.restate_to_current_basis(15.19e9, 2.0, 15.418e9)        # ratio 0.985
    assert clear.marginal is False


def test_eps_denominator_prefers_weighted_average_shares_and_records_the_basis():
    period = {"weighted_average_shares_diluted": 100.0, "weighted_average_shares_basic": 98.0,
              "shares_outstanding": 104.0}
    adjusted, basis = shares.adjusted_shares_for_eps(period, 1.0, 104.0)
    assert (adjusted.shares, basis) == (100.0, "weighted_average_diluted")
    adjusted, basis = shares.adjusted_shares_for_eps(
        {"weighted_average_shares_basic": 98.0, "shares_outstanding": 104.0}, 1.0, 104.0)
    assert (adjusted.shares, basis) == (98.0, "weighted_average_basic")
    adjusted, basis = shares.adjusted_shares_for_eps({"shares_outstanding": 104.0}, 1.0, 104.0)
    assert (adjusted.shares, basis) == (104.0, "period_end")
    adjusted, basis = shares.adjusted_shares_for_eps({}, 2.0, 104.0)
    assert adjusted.shares is None and basis is None
    assert adjusted.restatement == shares.NOT_REPORTED
    # a share count exists and a split followed, but nothing to check its basis against
    adjusted, basis = shares.adjusted_shares_for_eps({"shares_outstanding": 50.0}, 2.0, None)
    assert adjusted.shares is None and adjusted.restatement == shares.UNVERIFIED


# HDFC Bank as the source reports it (annual, INR). The 1:1 bonus took effect on 26 Aug 2025.
# Period-end shares are restated by the source for every year. Weighted-average shares are
# restated for FY2025 and FY2026 only: FY2023 and FY2024 are still on the pre-bonus count.
HDFC = {
    date(2023, 3, 31): {"net_income": 4.954469e11, "eps_diluted": 88.68,
                        "weighted_average_shares_diluted": 5.587e9, "shares_outstanding": 1.116e10},
    date(2024, 3, 31): {"net_income": 6.226566e11, "eps_diluted": 44.16,
                        "weighted_average_shares_diluted": 7.107e9, "shares_outstanding": 1.519e10},
    date(2025, 3, 31): {"net_income": 6.735083e11, "eps_diluted": 44.82,
                        "weighted_average_shares_diluted": 1.532e10,
                        "shares_outstanding": 1.530e10},
    date(2026, 3, 31): {"net_income": 7.047934e11, "eps_diluted": 45.75,
                        "weighted_average_shares_diluted": 1.541e10,
                        "shares_outstanding": 1.539e10},
}


def hdfc_rows():
    statements = pd.DataFrame([
        row for period_end, values in HDFC.items()
        for row in statement_rows("HDFCBANK.NS", "bank", period_end, values)
    ])
    splits = pd.DataFrame({"ticker": ["HDFCBANK.NS"], "date": [date(2025, 8, 26)],
                           "split_ratio": [2.0]})
    current = pd.DataFrame({"ticker": ["HDFCBANK.NS"], "shares_outstanding": [1.5418e10]})
    return compute.fundamental_metric_rows(statements, splits, current)


def test_hdfc_bank_bonus_does_not_show_as_a_fall_in_eps():
    rows = hdfc_rows()
    eps = {end: metric(rows, "HDFCBANK.NS", "eps", end) for end in HDFC}
    growth = {end: metric(rows, "HDFCBANK.NS", "eps_growth", end) for end in HDFC}

    # The source's own EPS series halves between FY2023 and FY2024: 44.16 / 88.68 - 1 = -50.2%
    assert HDFC[FY24]["eps_diluted"] / HDFC[date(2023, 3, 31)]["eps_diluted"] - 1 == \
        approx(-0.502, abs=1e-3)

    # FY2023: 4.954469e11 / (5.587e9 * 2) = 44.34    (weighted average restated by FinSight)
    # FY2024: 6.226566e11 / (7.107e9 * 2) = 43.81    (weighted average restated by FinSight)
    # FY2025: 6.735083e11 / 1.532e10      = 43.96    (already restated at source: not doubled)
    # FY2026: 7.047934e11 / 1.541e10      = 45.74    (no split after the period)
    assert eps[date(2023, 3, 31)]["value"] == approx(44.34, abs=0.01)
    assert eps[FY24]["value"] == approx(43.81, abs=0.01)
    assert eps[FY25]["value"] == approx(43.96, abs=0.01)
    assert eps[FY26]["value"] == approx(45.74, abs=0.01)

    # FY2023 -> FY2024 on the current basis: 43.81 / 44.34 - 1 = -1.2%, not -50%
    assert growth[FY24]["value"] == approx(-0.012, abs=1e-3)
    assert growth[FY24]["value"] > -0.05
    assert growth[FY25]["value"] == approx(43.96 / 43.81 - 1, abs=1e-3)       # +0.3%
    assert growth[FY26]["value"] == approx(45.74 / 43.96 - 1, abs=1e-3)       # +4.0%

    # each row records which share count was used and what was done to it
    fields = {end: row["input_fields"] for end, row in eps.items()}
    assert all(row["method"] == "weighted_average_diluted" for row in eps.values())
    assert fields[date(2023, 3, 31)]["share_restatement"] == shares.RESTATED
    assert fields[FY24]["share_restatement"] == shares.RESTATED
    assert fields[FY24]["split_factor"] == 2.0
    assert fields[FY24]["shares_as_reported"] == 7.107e9
    assert fields[FY24]["adjusted_shares"] == approx(1.4214e10)
    assert fields[FY25]["share_restatement"] == shares.ALREADY_RESTATED
    assert fields[FY26]["share_restatement"] == shares.NO_SPLIT

    # the source's reported EPS is stored alongside, for reconciliation only
    assert fields[date(2023, 3, 31)]["reported_eps_diluted"] == 88.68
    assert fields[date(2023, 3, 31)]["difference_from_reported_pct"] == approx(-50.0, abs=0.1)
    assert abs(fields[FY26]["difference_from_reported_pct"]) < 0.1


def test_figures_the_source_already_restated_are_not_restated_again():
    # Wipro's 1:1 bonus of Dec 2024: the source restated every share figure itself.
    # Applying the factor blindly would halve FY2024 EPS (10.41 -> 5.20).
    wipro = {FY24: {"net_income": 1.105e11, "weighted_average_shares_diluted": 1.061e10,
                    "shares_outstanding": 1.044e10},
             FY25: {"net_income": 1.314e11, "weighted_average_shares_diluted": 1.049e10,
                    "shares_outstanding": 1.046e10}}
    statements = pd.DataFrame([row for end, values in wipro.items()
                               for row in statement_rows("WIPRO.NS", "non_financial", end, values)])
    splits = pd.DataFrame({"ticker": ["WIPRO.NS"], "date": [date(2024, 12, 3)],
                           "split_ratio": [2.0]})
    current = pd.DataFrame({"ticker": ["WIPRO.NS"], "shares_outstanding": [9.893e9]})
    rows = compute.fundamental_metric_rows(statements, splits, current)
    eps = metric(rows, "WIPRO.NS", "eps", FY24)
    assert eps["value"] == approx(1.105e11 / 1.061e10)                   # 10.41, not 5.20
    assert eps["input_fields"]["share_restatement"] == shares.ALREADY_RESTATED
    assert metric(rows, "WIPRO.NS", "eps_growth", FY25)["value"] == approx(0.203, abs=1e-3)


def test_without_weighted_average_shares_period_end_shares_are_used_and_recorded():
    statements = pd.DataFrame(statement_rows("A", "non_financial", FY26, {
        "net_income": 200.0, "shares_outstanding": 40.0}))
    rows = compute.fundamental_metric_rows(statements)
    eps = metric(rows, "A", "eps", FY26)
    assert eps["value"] == approx(5.0) and eps["method"] == "period_end"
    assert eps["input_fields"]["share_basis"] == "period_end"


# --- profitability, leverage, liquidity, cash flow ------------------------------

FUNDAMENTALS = wide({
    ("A", FY25): {"revenue": 100.0, "gross_profit": 45.0, "ebitda": 25.0, "ebit": 20.0,
                  "net_income": 24.0, "total_equity": 160.0, "total_assets": 400.0,
                  "total_debt": 40.0, "cash_and_short_term_investments": 60.0,
                  "cash_and_equivalents": 10.0, "current_assets": 150.0, "inventory": 30.0,
                  "current_liabilities": 80.0, "operating_cash_flow": 28.0, "capex": 8.0,
                  "free_cash_flow": 20.0},
    ("A", FY26): {"revenue": 120.0, "gross_profit": 54.0, "ebitda": 30.0, "ebit": 24.0,
                  "net_income": 30.0, "total_equity": 200.0, "total_assets": 500.0,
                  "total_debt": 50.0, "cash_and_short_term_investments": 80.0,
                  "cash_and_equivalents": 15.0, "current_assets": 180.0, "inventory": None,
                  "current_liabilities": 90.0, "operating_cash_flow": 36.0, "capex": 12.0,
                  "free_cash_flow": 24.0},
})


def test_margins():
    assert ratios.ebitda_margin(FUNDAMENTALS)[("A", FY26)] == approx(0.25)      # 30 / 120
    assert ratios.gross_margin(FUNDAMENTALS)[("A", FY26)] == approx(0.45)       # 54 / 120
    assert ratios.ebit_margin(FUNDAMENTALS)[("A", FY26)] == approx(0.20)        # 24 / 120
    assert ratios.net_margin(FUNDAMENTALS)[("A", FY26)] == approx(0.25)         # 30 / 120
    assert ratios.fcf_margin(FUNDAMENTALS)[("A", FY26)] == approx(0.20)         # 24 / 120
    assert ratios.ocf_margin(FUNDAMENTALS)[("A", FY26)] == approx(0.30)         # 36 / 120
    assert ratios.capex_to_revenue(FUNDAMENTALS)[("A", FY26)] == approx(0.10)   # 12 / 120


def test_margin_is_null_without_positive_revenue():
    table = wide({("A", FY26): {"revenue": 0.0, "ebitda": 5.0},
                  ("B", FY26): {"revenue": None, "ebitda": 5.0}})
    assert ratios.ebitda_margin(table).isna().all()


def test_return_ratios_use_average_balances_with_a_documented_fallback():
    # FY26: 30 / average(200, 160) = 30 / 180 = 16.67%
    assert ratios.roe(FUNDAMENTALS)[("A", FY26)] == approx(30 / 180)
    # FY25 has no prior year-end: falls back to ending equity, 24 / 160 = 15%
    assert ratios.roe(FUNDAMENTALS)[("A", FY25)] == approx(0.15)
    # FY26: 30 / average(500, 400) = 30 / 450
    assert ratios.roa(FUNDAMENTALS)[("A", FY26)] == approx(30 / 450)


def test_debt_to_equity():
    assert ratios.debt_to_equity(FUNDAMENTALS)[("A", FY26)] == approx(0.25)     # 50 / 200
    assert ratios.debt_to_assets(FUNDAMENTALS)[("A", FY26)] == approx(0.10)     # 50 / 500
    negative_equity = wide({("A", FY26): {"total_debt": 50.0, "total_equity": -20.0}})
    assert pd.isna(ratios.debt_to_equity(negative_equity)[("A", FY26)])


def test_net_debt_to_ebitda_uses_cash_and_short_term_investments():
    # (50 - 80) / 30 = -1.0: net cash
    assert ratios.net_debt_to_ebitda(FUNDAMENTALS)[("A", FY26)] == approx(-1.0)
    # falls back to cash and equivalents when the broader figure is not reported: (50 - 15) / 30
    only_cash = FUNDAMENTALS.drop(columns="cash_and_short_term_investments")
    assert ratios.net_debt_to_ebitda(only_cash)[("A", FY26)] == approx(35 / 30)


def test_liquidity_ratios_never_assume_zero_inventory():
    assert ratios.current_ratio(FUNDAMENTALS)[("A", FY26)] == approx(2.0)       # 180 / 90
    assert ratios.quick_ratio(FUNDAMENTALS)[("A", FY25)] == approx(1.5)         # (150 - 30) / 80
    assert pd.isna(ratios.quick_ratio(FUNDAMENTALS)[("A", FY26)])               # no inventory


# --- sector applicability ------------------------------------------------------

def test_applicability_matrix_matches_the_spec():
    non_financial_only = ["gross_margin", "ebitda_margin", "ebit_margin", "ev_ebitda",
                          "ev_revenue", "current_ratio", "quick_ratio", "debt_to_equity",
                          "net_debt_to_ebitda", "enterprise_value"]
    everyone = ["pe_ratio", "pb_ratio", "roe", "roa", "eps_growth", "net_margin"]
    lenders_only = ["nii_growth", "cost_to_income", "loan_to_deposit"]
    for name in non_financial_only:
        assert REGISTRY[name].applicable == NON_FINANCIAL, name
        assert not is_applicable(name, "bank") and not is_applicable(name, "nbfc")
    for name in everyone:
        assert REGISTRY[name].applicable == ALL, name
    for name in lenders_only:
        assert REGISTRY[name].applicable == LENDERS, name
        assert not is_applicable(name, "non_financial")
    assert not_applicable_reason("bank") == "N/A (not meaningful for banks)"
    formula_ids = [spec.formula_id for spec in REGISTRY.values()]
    assert len(formula_ids) == len(set(formula_ids))


def statement_rows(ticker, sector_type, period_end, values: dict, currency="INR",
                   inr_per_unit=1.0, period_type="annual"):
    return [{"ticker": ticker, "sector_type": sector_type, "statement": "x", "line_item": item,
             "period_end_date": period_end, "period_type": period_type,
             "original_value": value,
             "value": None if value is None else (value if item == "shares_outstanding"
                                                  else value * inr_per_unit),
             "original_currency": currency}
            for item, value in values.items()]


def metric(rows, ticker, name, period_end=FY26, period_type="annual"):
    found = [r for r in rows if (r["ticker"], r["metric_name"], r["period_end_date"],
                                 r["period_type"]) == (ticker, name, period_end, period_type)]
    assert len(found) == 1, f"{ticker} {name}: {len(found)} rows"
    return found[0]


def test_no_metric_rows_are_invented_for_periods_a_company_did_not_report():
    # A reports FY25 and FY26; B has a December year-end and reports only Dec-2025.
    december = date(2025, 12, 31)
    statements = pd.DataFrame(
        statement_rows("A", "non_financial", FY25, {"revenue": 100.0, "net_income": 10.0})
        + statement_rows("A", "non_financial", FY26, {"revenue": 110.0, "net_income": 11.0})
        + statement_rows("B", "non_financial", december, {"revenue": 50.0, "net_income": 5.0}))
    table = compute.wide_table(statements, "annual", "original_value")
    assert list(table.index) == [("A", FY25), ("A", FY26), ("B", december)]
    rows = compute.fundamental_metric_rows(statements)
    periods = {(r["ticker"], r["period_end_date"]) for r in rows}
    assert periods == {("A", FY25), ("A", FY26), ("B", december)}
    assert metric(rows, "B", "net_margin", december)["value"] == approx(0.10)


def test_return_ratio_rows_record_the_balance_method():
    statements = pd.DataFrame(
        statement_rows("A", "non_financial", FY25, {"net_income": 24.0, "total_equity": 160.0,
                                                    "total_assets": 400.0})
        + statement_rows("A", "non_financial", FY26, {"net_income": 30.0, "total_equity": 200.0,
                                                      "total_assets": 500.0}))
    rows = compute.fundamental_metric_rows(statements)
    # FY26 has a prior year-end: average balance. 30 / average(200, 160) = 16.67%
    assert metric(rows, "A", "roe", FY26)["method"] == "average_balance"
    assert metric(rows, "A", "roe", FY26)["value"] == approx(30 / 180)
    assert metric(rows, "A", "roa", FY26)["method"] == "average_balance"
    # FY25 is the earliest year: closing-balance fallback. 24 / 160 = 15%
    assert metric(rows, "A", "roe", FY25)["method"] == "closing_balance"
    assert metric(rows, "A", "roe", FY25)["value"] == approx(0.15)
    assert metric(rows, "A", "roa", FY25)["input_fields"]["method"] == "closing_balance"
    # metrics without such a choice carry no method
    assert metric(rows, "A", "net_margin", FY26)["method"] is None


def test_bank_metrics_are_not_applicable_even_when_inputs_exist():
    bank = {"revenue": 100.0, "ebitda": 40.0, "net_income": 20.0, "total_debt": 300.0,
            "total_equity": 100.0, "total_assets": 1000.0, "total_loans": 600.0,
            "total_deposits": None, "net_interest_income": 50.0}
    statements = pd.DataFrame(
        statement_rows("BANK", "bank", FY26, bank)
        + statement_rows("CORP", "non_financial", FY26, {"revenue": 100.0, "ebitda": 40.0,
                                                         "net_income": 20.0})
    )
    rows = compute.fundamental_metric_rows(statements)

    for name in ("ebitda_margin", "debt_to_equity", "current_ratio", "net_debt_to_ebitda"):
        row = metric(rows, "BANK", name)
        assert row["value"] is None, name                  # never a number for a bank
        assert row["na_reason"] == "N/A (not meaningful for banks)", name
    assert metric(rows, "BANK", "net_margin")["value"] == approx(0.20)
    assert metric(rows, "BANK", "roe")["value"] == approx(0.20)       # 20 / 100, ending equity
    assert metric(rows, "BANK", "roe")["method"] == "closing_balance"
    # bank-only metrics: N/A with the missing source field named
    assert metric(rows, "BANK", "loan_to_deposit")["value"] is None
    assert metric(rows, "BANK", "loan_to_deposit")["na_reason"] == \
        "input unavailable: total_deposits"
    assert "operating_expenses" in metric(rows, "BANK", "cost_to_income")["na_reason"]
    # and the other way round
    assert metric(rows, "CORP", "ebitda_margin")["value"] == approx(0.40)
    assert metric(rows, "CORP", "nii_growth")["na_reason"] == \
        "N/A (not meaningful for non-financial companies)"
    assert metric(rows, "CORP", "revenue_growth")["na_reason"] == \
        "prior-year value unavailable: revenue"


# --- reporting currency --------------------------------------------------------

def test_growth_and_ratios_are_computed_in_the_reporting_currency():
    # USD reporter: revenue 100 -> 110 USD (+10%). Translated at 80 then 90 INR per USD the
    # INR figures are 8,000 -> 9,900 (+23.75%): the difference is purely the exchange rate.
    statements = pd.DataFrame(
        statement_rows("USDCO", "non_financial", FY25, {"revenue": 100.0, "net_income": 20.0},
                       "USD", 80.0)
        + statement_rows("USDCO", "non_financial", FY26, {"revenue": 110.0, "net_income": 22.0},
                         "USD", 90.0)
    )
    rows = compute.fundamental_metric_rows(statements)
    growth = metric(rows, "USDCO", "revenue_growth")
    assert growth["value"] == approx(0.10)
    assert growth["reporting_currency"] == "USD" and growth["is_translated"] is False
    assert growth["input_fields"]["revenue"] == 110.0              # reported USD, not INR
    assert growth["input_fields"]["currency"] == "USD"
    assert metric(rows, "USDCO", "net_margin")["value"] == approx(0.20)


def test_ttm_of_a_usd_reporter_translates_each_quarter_at_its_own_average_rate():
    quarters = ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31"]
    reported = [10.0, 20.0, 30.0, 40.0]            # USD revenue per quarter
    rate_by_quarter = [80.0, 82.0, 84.0, 86.0]     # constant INR per USD within each quarter
    quotes = {}
    for quarter_end, rate in zip(quarters, rate_by_quarter):
        end = pd.Timestamp(quarter_end)
        start = end + pd.Timedelta(days=1) - pd.DateOffset(months=3)
        for day in (start, start + pd.Timedelta(days=45), end):
            quotes[day] = rate
    rates = pd.Series(quotes).sort_index()

    raw = pd.DataFrame({q: [v] for q, v in zip(quarters, reported)}, index=["Total Revenue"])
    raw.index.name = "line_item"
    usd_company = Company(ticker="USDCO.NS", name="U", sector="S", industry="I", peer_group="g",
                          sector_type="non_financial", statement_currency="USD")
    statements, _, _, issues = pipeline.clean_company_statements(
        usd_company, {("income", "quarterly"): [(raw, "2026-04-01T10:00:00+00:00", "f")]},
        pipeline.load_field_map(), {}, {"USD": rates}, pd.Timestamp("2026-04-01", tz="UTC"),
        {"USD": "test"},
    )
    assert issues == []
    revenue = statements[statements["line_item"] == "revenue"].set_index("period_end_date")
    ttm = valuation.ttm_value(revenue["value"], pd.Series(dtype="float64"), date(2026, 3, 31))
    # 10*80 + 20*82 + 30*84 + 40*86 = 800 + 1,640 + 2,520 + 3,440 = 8,400
    assert ttm["basis"] == "ttm" and ttm["value"] == approx(8400.0)
    # NOT the USD total at one blended rate: 100 * mean(80, 82, 84, 86) = 8,300
    assert ttm["value"] != approx(100.0 * 83.0)
    assert revenue["original_value"].sum() == 100.0            # USD figures intact


# --- TTM and valuation ---------------------------------------------------------

def quarters(values, last=date(2026, 6, 30)):
    ends = [(pd.Timestamp(last) - pd.DateOffset(months=3 * k) + pd.offsets.MonthEnd(0)).date()
            for k in range(len(values))][::-1]
    return pd.Series(values, index=ends, dtype="float64")


ANNUAL = pd.Series({FY25: 80.0, FY26: 95.0})


def test_ttm_sums_the_last_four_quarters():
    ttm = valuation.ttm_value(quarters([5.0, 10.0, 20.0, 30.0, 40.0]), ANNUAL, date(2026, 6, 30))
    assert ttm["value"] == 100.0 and ttm["basis"] == "ttm"       # 10 + 20 + 30 + 40
    assert ttm["periods"] == ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]


def test_ttm_falls_back_to_the_latest_annual_figure():
    series = quarters([10.0, 20.0, 30.0, 40.0])
    missing_value = series.copy()
    missing_value.iloc[1] = np.nan
    gap = series.drop(series.index[1])                           # a quarter absent altogether
    for case in (missing_value, gap, series.iloc[1:]):           # last case: only 3 quarters
        ttm = valuation.ttm_value(case, ANNUAL, date(2026, 6, 30))
        assert (ttm["value"], ttm["basis"], ttm["period_end"]) == (95.0, "latest_annual", FY26)
    no_quarters = valuation.ttm_value(pd.Series(dtype="float64"), ANNUAL, None)
    assert no_quarters["basis"] == "latest_annual"
    nothing = valuation.ttm_value(pd.Series(dtype="float64"), pd.Series(dtype="float64"), None)
    assert nothing["value"] is None and nothing["basis"] is None


def test_enterprise_value():
    assert valuation.market_cap(250.0, 4.0) == 1000.0
    # 1,000 + 200 + 50 - 150 = 1,100
    assert valuation.enterprise_value(1000.0, 200.0, 50.0, 150.0) == 1100.0
    assert valuation.enterprise_value(1000.0, 200.0, None, 150.0) == 1050.0   # no minority reported
    assert valuation.enterprise_value(1000.0, None, 50.0, 150.0) is None      # debt unknown
    assert valuation.enterprise_value(1000.0, 200.0, 50.0, None) is None      # cash unknown
    assert valuation.enterprise_value(None, 200.0, 50.0, 150.0) is None
    assert valuation.market_cap(250.0, None) is None


def test_multiples_are_null_on_a_non_positive_base():
    assert valuation.pe_ratio(1000.0, 50.0) == 20.0
    assert valuation.pe_ratio(1000.0, -50.0) is None             # loss-making: no P/E
    assert valuation.pe_ratio(1000.0, 0.0) is None
    assert valuation.pb_ratio(1000.0, 400.0) == 2.5
    assert valuation.pb_ratio(1000.0, -400.0) is None            # negative equity: no P/B
    assert valuation.pb_ratio(1000.0, 0.0) is None
    assert valuation.ev_ebitda(1100.0, 110.0) == 10.0
    assert valuation.ev_revenue(1100.0, None) is None


def test_reconciliation_flags_differences_above_ten_percent():
    assert valuation.reconcile(10.9, 10.0)["is_flagged"] is False             # +9%
    outcome = valuation.reconcile(12.0, 10.0)
    assert outcome["is_flagged"] is True and outcome["difference_pct"] == approx(20.0)
    assert valuation.reconcile(8.0, 10.0)["difference_pct"] == approx(-20.0)
    assert valuation.reconcile(None, 10.0)["is_flagged"] is False
    assert valuation.reconcile(None, 10.0)["difference_pct"] is None


def test_valuation_rows_current_and_historical():
    # one non-financial company, INR, two fiscal years, price 100 on every day
    days = pd.bdate_range("2025-03-24", "2026-04-10")
    prices = pd.DataFrame({"ticker": "CORP", "date": days.date, "close": 100.0,
                           "adj_close": 100.0, "is_stale_quote": False})
    fy = {"revenue": 500.0, "ebitda": 100.0, "net_income": 50.0, "total_equity": 400.0,
          "total_debt": 200.0, "minority_interest": None, "cash_and_equivalents": 100.0,
          "shares_outstanding": 10.0}
    statements = pd.DataFrame(statement_rows("CORP", "non_financial", FY25, fy)
                              + statement_rows("CORP", "non_financial", FY26, fy))
    shares = pd.DataFrame({"ticker": ["CORP"], "shares_outstanding": [12.0],
                           "as_of_date": [date(2026, 4, 10)]})
    rows, current = compute.valuation_metric_rows(statements, prices, shares)

    as_of = date(2026, 4, 10)
    # current: market cap = 100 * 12 = 1,200; EV = 1,200 + 200 + 0 - 100 = 1,300
    assert metric(rows, "CORP", "market_cap", as_of, "ttm")["value"] == 1200.0
    assert metric(rows, "CORP", "enterprise_value", as_of, "ttm")["value"] == 1300.0
    pe = metric(rows, "CORP", "pe_ratio", as_of, "ttm")
    assert pe["value"] == approx(24.0)                               # 1,200 / 50
    assert pe["input_fields"]["flow_basis"] == "latest_annual"       # no quarterly data
    assert pe["method"] == "latest_annual"
    assert metric(rows, "CORP", "ev_ebitda", as_of, "ttm")["value"] == approx(13.0)
    assert metric(rows, "CORP", "pb_ratio", as_of, "ttm")["value"] == approx(3.0)
    ev = metric(rows, "CORP", "enterprise_value", as_of, "ttm")["input_fields"]
    assert ev["minority_interest_assumed_zero"] is True
    assert ev["cash_item"] == "cash_and_equivalents"
    assert current["CORP"]["pe_ratio"] == approx(24.0)
    # historical FY2026: price at 31 Mar 2026 x period-end shares = 100 * 10 = 1,000
    assert metric(rows, "CORP", "market_cap", FY26)["value"] == 1000.0
    assert metric(rows, "CORP", "pe_ratio", FY26)["value"] == approx(20.0)        # 1,000 / 50
    assert metric(rows, "CORP", "ev_revenue", FY26)["value"] == approx(1100 / 500)
    assert metric(rows, "CORP", "pe_ratio", FY26)["as_of_date"] == FY26


def test_a_loss_or_negative_equity_gives_na_never_a_negative_multiple():
    days = pd.bdate_range("2026-03-24", "2026-04-10")
    prices = pd.DataFrame({"ticker": "LOSS", "date": days.date, "close": 100.0,
                           "adj_close": 100.0, "is_stale_quote": False})
    statements = pd.DataFrame(statement_rows("LOSS", "non_financial", FY26, {
        "revenue": 500.0, "ebitda": -20.0, "net_income": -50.0, "total_equity": -30.0,
        "total_debt": 200.0, "cash_and_equivalents": 100.0, "shares_outstanding": 10.0}))
    share_counts = pd.DataFrame({"ticker": ["LOSS"], "shares_outstanding": [10.0],
                                 "as_of_date": [date(2026, 4, 10)]})
    rows, _ = compute.valuation_metric_rows(statements, prices, share_counts)
    as_of = date(2026, 4, 10)
    for period_end, period_type in ((as_of, "ttm"), (FY26, "annual")):
        pe = metric(rows, "LOSS", "pe_ratio", period_end, period_type)
        assert pe["value"] is None and pe["na_reason"] == "net income is not positive"
        pb = metric(rows, "LOSS", "pb_ratio", period_end, period_type)
        assert pb["value"] is None and pb["na_reason"] == "equity is not positive"
        assert metric(rows, "LOSS", "ev_ebitda", period_end, period_type)["na_reason"] == \
            "EBITDA is not positive"
    assert not any(r["value"] is not None and r["value"] < 0 for r in rows
                   if r["metric_name"] in ("pe_ratio", "pb_ratio", "ev_ebitda", "ev_revenue"))


def test_net_income_is_the_figure_attributable_to_shareholders():
    field_map = pipeline.load_field_map()
    sources = field_map["income"]["net_income"]["sources"]
    assert sources[0] == "Net Income"
    assert "Net Income Including Noncontrolling Interests" not in sources
    # HDFC Bank FY2026 at source: 7.048e11 attributable vs 7.271e11 including minority interest
    raw = pd.DataFrame({"2026-03-31": [7.047934e11, 7.270964e11]},
                       index=["Net Income", "Net Income Including Noncontrolling Interests"])
    observed = pipeline.observe_statement(raw, "income", "annual", field_map,
                                          "2026-04-01T10:00:00+00:00", "f")
    assert observed.loc[observed["line_item"] == "net_income", "value"].item() == 7.047934e11


def test_bank_gets_no_enterprise_value():
    days = pd.bdate_range("2026-03-24", "2026-04-10")
    prices = pd.DataFrame({"ticker": "BANK", "date": days.date, "close": 100.0,
                           "adj_close": 100.0, "is_stale_quote": False})
    statements = pd.DataFrame(statement_rows("BANK", "bank", FY26, {
        "revenue": 500.0, "net_income": 50.0, "total_equity": 400.0, "total_debt": 2000.0,
        "cash_and_equivalents": 100.0, "shares_outstanding": 10.0}))
    shares = pd.DataFrame({"ticker": ["BANK"], "shares_outstanding": [10.0],
                           "as_of_date": [date(2026, 4, 10)]})
    rows, _ = compute.valuation_metric_rows(statements, prices, shares)
    as_of = date(2026, 4, 10)
    for name in ("enterprise_value", "ev_ebitda", "ev_revenue"):
        row = metric(rows, "BANK", name, as_of, "ttm")
        assert row["value"] is None and row["na_reason"] == "N/A (not meaningful for banks)"
    assert metric(rows, "BANK", "pe_ratio", as_of, "ttm")["value"] == approx(20.0)
    assert metric(rows, "BANK", "pb_ratio", as_of, "ttm")["value"] == approx(2.5)


# --- returns -------------------------------------------------------------------

def price_series(values, start="2026-01-01", freq="B"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq),
                     dtype="float64")


def test_daily_and_cumulative_returns():
    daily = returns.daily_returns(price_series([100.0, 110.0, 99.0]))
    assert daily.tolist() == approx([0.10, -0.10])          # 110/100 - 1, 99/110 - 1
    assert returns.cumulative_returns(daily).iloc[-1] == approx(-0.01)   # 99/100 - 1


def test_trailing_returns_and_cagr():
    index = pd.to_datetime(["2023-04-10", "2025-04-10", "2025-12-31", "2026-01-09",
                            "2026-03-10", "2026-04-10"])
    prices = pd.Series([100.0, 110.0, 121.0, 125.0, 127.05, 133.1], index=index)
    as_of = date(2026, 4, 10)
    assert returns.trailing_return(prices, as_of, months=1) == approx(133.1 / 127.05 - 1)
    assert returns.trailing_return(prices, as_of, years=1) == approx(0.21)       # 133.1 / 110
    assert returns.ytd_return(prices, as_of) == approx(0.10)                     # 133.1 / 121
    assert returns.cagr(prices, as_of, 3) == approx(0.10)       # (133.1 / 100) ^ (1/3) - 1
    # three months back is 10 Jan 2026: the 9 Jan price is one day old and is used
    assert returns.trailing_return(prices, as_of, months=3) == approx(133.1 / 125 - 1)


def test_returns_are_null_when_history_is_too_short():
    prices = price_series([100.0] * 30, start="2026-03-01")
    as_of = prices.index[-1].date()
    assert returns.trailing_return(prices, as_of, years=1) is None
    assert returns.cagr(prices, as_of, 3) is None
    assert returns.ytd_return(prices, as_of) is None
    assert returns.window_returns(prices, as_of, 1) is None
    # a price more than a week before the target date does not count as that date's price
    sparse = pd.Series([100.0, 120.0], index=pd.to_datetime(["2025-03-20", "2026-04-10"]))
    assert returns.trailing_return(sparse, date(2026, 4, 10), years=1) is None


# --- risk ----------------------------------------------------------------------

ALTERNATING = pd.Series([0.01, -0.01] * 15)       # 30 daily returns, mean 0


def test_annualized_volatility():
    # sample variance = 30 * 0.0001 / 29; std = 0.01 * sqrt(30/29) = 0.01017095
    # annualized = 0.01017095 * sqrt(252) = 0.01017095 * 15.87451 = 0.161459
    assert risk.annualized_volatility(ALTERNATING) == approx(0.161459, abs=1e-6)
    assert risk.annualized_volatility(ALTERNATING.iloc[:29]) is None     # too few observations


def test_sharpe_ratio():
    daily = pd.Series([0.02, 0.00] * 15)     # mean 0.01, same dispersion as ALTERNATING
    # annualized return = 0.01 * 252 = 2.52; volatility = 0.161459
    # Sharpe = (2.52 - 0.052599) / 0.161459 = 15.2819
    assert risk.annualized_return(daily) == approx(2.52)
    assert risk.sharpe_ratio(daily, 0.052599) == approx(15.2819, abs=1e-3)
    assert risk.sharpe_ratio(daily, None) is None                   # no rate configured
    assert risk.sharpe_ratio(pd.Series([0.01] * 30), 0.05) is None  # zero volatility


def test_downside_deviation():
    # 15 of 30 returns are -1%: mean squared shortfall = 15 * 0.0001 / 30 = 0.00005
    # sqrt = 0.00707107; annualized = 0.00707107 * 15.87451 = 0.112250
    assert risk.downside_deviation(ALTERNATING) == approx(0.112250, abs=1e-6)
    assert risk.downside_deviation(pd.Series([0.01] * 30)) == 0.0   # never below target


def test_max_drawdown_with_known_peak_and_trough():
    prices = price_series([100.0, 120.0, 90.0, 95.0, 130.0, 117.0], start="2026-01-05")
    result = risk.max_drawdown(prices)
    # worst fall: 120 -> 90 = -25%. The later 130 -> 117 is only -10%.
    assert result["max_drawdown"] == approx(-0.25)
    assert result["peak_date"] == date(2026, 1, 6)
    assert result["trough_date"] == date(2026, 1, 7)
    rising = risk.max_drawdown(price_series([100.0, 101.0, 102.0]))
    assert rising["max_drawdown"] == 0.0
    assert risk.max_drawdown(price_series([100.0])) is None


def test_correlation_uses_only_dates_shared_by_all_securities():
    frame = pd.DataFrame({"A": [0.01, 0.02, -0.01, 0.03, None],
                          "B": [0.02, 0.04, -0.02, 0.06, 0.50],     # B = 2A where both exist
                          "C": [-0.01, -0.02, 0.01, -0.03, 0.10]})  # C = -A
    matrix = risk.correlation_matrix(frame)
    assert matrix.loc["A", "B"] == approx(1.0)
    assert matrix.loc["A", "C"] == approx(-1.0)
    # the last row is dropped for every pair, so B and C are compared on the same 4 dates
    assert matrix.loc["B", "C"] == approx(-1.0)


def test_market_metrics_exclude_placeholder_rows():
    days = pd.bdate_range("2025-01-01", periods=300)
    # steady +0.1% per day, plus one placeholder row that repeats the previous close
    closes = 100.0 * 1.001 ** np.arange(len(days))
    prices = pd.DataFrame({"ticker": "A", "date": days.date, "close": closes,
                           "adj_close": closes, "is_stale_quote": False})
    stale = prices.iloc[[150]].assign(is_stale_quote=True)
    stale["adj_close"] = prices["adj_close"].iloc[149]
    with_stale = pd.concat([prices.drop(index=150), stale]).sort_values("date")

    rows = compute.market_metric_rows(with_stale, risk_free_rate=0.05, trading_days=252)
    as_of = days[-1].date()
    volatility = metric(rows, "A", "volatility_1y", as_of, "point_in_time")
    window_start = pd.Timestamp(as_of) - pd.DateOffset(years=1)
    expected_observations = int((days > window_start).sum()) - 1     # the placeholder day is gone
    assert volatility["input_fields"]["observations"] in (expected_observations,
                                                          expected_observations + 1)
    # without the placeholder there is no zero-return day; the only non-0.1% return is the
    # two-day step over the removed row: (1.001^2 - 1)
    series = with_stale[~with_stale["is_stale_quote"]].set_index("date")["adj_close"]
    daily = series.pct_change().dropna()
    assert (daily > 0).all() and daily.max() == approx(1.001 ** 2 - 1)
    assert metric(rows, "A", "max_drawdown_1y", as_of, "point_in_time")["value"] == 0.0
    assert metric(rows, "A", "cagr_3y", as_of, "point_in_time")["value"] is None
    assert metric(rows, "A", "cagr_3y", as_of, "point_in_time")["na_reason"] == \
        "insufficient price history"
    sharpe = metric(rows, "A", "sharpe_1y", as_of, "point_in_time")
    assert sharpe["input_fields"]["risk_free_rate"] == 0.05 and sharpe["value"] > 0
    assert math.isfinite(volatility["value"])
