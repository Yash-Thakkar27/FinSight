"""Offline tests for validation checks. Each check is tested on a small hand-built table."""

from datetime import date
from types import SimpleNamespace

import pandas as pd

from src.validation import checks


def prices(rows):
    """rows: (ticker, date, open, high, low, close, volume[, adj_close])"""
    frame = pd.DataFrame(
        [(t, date.fromisoformat(d), o, h, lo, c, v, (rest[0] if rest else c))
         for t, d, o, h, lo, c, v, *rest in rows],
        columns=["ticker", "date", "open", "high", "low", "close", "volume", "adj_close"],
    )
    frame["is_stale_quote"] = False
    return frame


def statements(rows, ticker="AAA.NS", period_end="2026-03-31", period_type="annual"):
    """rows: {line_item: value or (value, missing_reason)}"""
    records = []
    for line_item, value in rows.items():
        value, reason = value if isinstance(value, tuple) else (value, None)
        if value is None and reason is None:
            reason = "unavailable_from_source"
        records.append({"ticker": ticker, "statement": "x", "line_item": line_item,
                        "period_end_date": date.fromisoformat(period_end),
                        "period_type": period_type, "value": value, "original_value": value,
                        "missing_reason": reason, "original_currency": "INR"})
    frame = pd.DataFrame(records)
    frame["value"] = frame["value"].astype("float64")
    frame["original_value"] = frame["original_value"].astype("float64")
    return frame


def failures(results):
    return [r for r in results if r.status == "fail"]


GOOD = ("AAA.NS", "2026-10-05", 100.0, 105.0, 99.0, 103.0, 1000)


# --- market data -------------------------------------------------------------

def test_invalid_ohlc_is_caught():
    table = prices([
        GOOD,
        ("AAA.NS", "2026-10-06", 100.0, 101.0, 99.0, 104.0, 1000),   # close above high
        ("AAA.NS", "2026-10-07", 100.0, 105.0, 101.0, 103.0, 1000),  # low above open
        ("BBB.NS", "2026-10-05", 50.0, 51.0, 49.0, 50.5, 10),
    ])
    high = checks.check_high_is_highest(table)
    assert [r.record_key for r in failures(high)] == ["AAA.NS|2026-10-06"]
    assert failures(high)[0].severity == "error"
    low = checks.check_low_is_lowest(table)
    assert [r.record_key for r in failures(low)] == ["AAA.NS|2026-10-07"]
    # the clean ticker gets a pass result saying how many records were checked
    passed = [r for r in high if r.status == "pass"]
    assert [(r.ticker, r.message) for r in passed] == [("BBB.NS", "1 records checked")]


def test_volume_and_price_sign_checks():
    table = prices([
        GOOD,
        ("AAA.NS", "2026-10-06", 100.0, 105.0, 99.0, 103.0, -5),
        ("AAA.NS", "2026-10-07", 0.0, 105.0, 99.0, 103.0, 10),
        ("AAA.NS", "2026-10-08", 100.0, 105.0, 99.0, None, 10),
    ])
    assert [r.record_key for r in failures(checks.check_volume_non_negative(table))] == \
        ["AAA.NS|2026-10-06"]
    assert [r.record_key for r in failures(checks.check_prices_positive(table))] == \
        ["AAA.NS|2026-10-07", "AAA.NS|2026-10-08"]


def test_duplicate_detection():
    table = prices([GOOD, GOOD, ("AAA.NS", "2026-10-06", 100.0, 105.0, 99.0, 103.0, 1000),
                    ("BBB.NS", "2026-10-05", 50.0, 51.0, 49.0, 50.5, 10)])
    duplicated = failures(checks.check_no_duplicate_prices(table))
    assert [r.record_key for r in duplicated] == ["AAA.NS|2026-10-05"] * 2
    assert not failures(checks.check_no_duplicate_prices(table.drop_duplicates()))


def test_price_gap_counts_missing_weekdays():
    table = prices([
        ("AAA.NS", "2026-10-01", 1.0, 1.0, 1.0, 1.0, 1),   # Thu
        ("AAA.NS", "2026-10-05", 1.0, 1.0, 1.0, 1.0, 1),   # Mon: 1 weekday missing (Fri 2nd)
        ("AAA.NS", "2026-10-13", 1.0, 1.0, 1.0, 1.0, 1),   # Tue: 5 missing (6,7,8,9,12)
        ("AAA.NS", "2026-10-22", 1.0, 1.0, 1.0, 1.0, 1),   # Thu: 6 missing (14,15,16,19,20,21)
    ])
    gaps = failures(checks.check_price_gaps(table, max_gap_weekdays=5))
    assert [r.record_key for r in gaps] == ["AAA.NS|2026-10-22"]
    assert "6 weekdays missing since 2026-10-13" in gaps[0].message


def test_extreme_return_flag():
    table = prices([
        ("AAA.NS", "2026-10-05", 100.0, 100.0, 100.0, 100.0, 1),
        ("AAA.NS", "2026-10-06", 120.0, 120.0, 120.0, 120.0, 1),   # +20.0%: at the limit
        ("AAA.NS", "2026-10-07", 90.0, 90.0, 90.0, 90.0, 1),       # -25.0%: flagged
        ("BBB.NS", "2026-10-07", 500.0, 500.0, 500.0, 500.0, 1),   # first row of another ticker
    ])
    flagged = failures(checks.check_extreme_returns(table, 0.20))
    assert [r.record_key for r in flagged] == ["AAA.NS|2026-10-07"]
    assert flagged[0].message.startswith("Potential data anomaly detected")
    assert "-25.0%" in flagged[0].message


# --- financials --------------------------------------------------------------

def test_sign_checks_on_statements():
    table = pd.concat([
        statements({"total_assets": -1.0, "revenue": 100.0, "shares_outstanding": 0.0,
                    "capex": -5.0}, ticker="BAD.NS"),
        statements({"total_assets": 10.0, "revenue": 0.0, "shares_outstanding": 5.0,
                    "capex": 5.0, "net_income": None}, ticker="OK.NS"),
    ], ignore_index=True)
    assert [r.ticker for r in failures(checks.check_assets_non_negative(table))] == ["BAD.NS"]
    assert failures(checks.check_revenue_non_negative(table)) == []    # zero revenue is allowed
    assert [r.ticker for r in failures(checks.check_shares_positive(table))] == ["BAD.NS"]
    assert [r.ticker for r in failures(checks.check_capex_sign(table))] == ["BAD.NS"]


def test_balance_sheet_tolerance():
    table = pd.concat([
        # 100 vs 60 + 39 = 99: gap 1% -> within the 2% tolerance
        statements({"total_assets": 100.0, "total_liabilities": 60.0,
                    "total_equity_incl_minority": 39.0}, ticker="OK.NS"),
        # 100 vs 60 + 35 = 95: gap 5% -> flagged
        statements({"total_assets": 100.0, "total_liabilities": 60.0,
                    "total_equity_incl_minority": 35.0}, ticker="OFF.NS"),
        # an input is missing: nothing to compare, so no result at all
        statements({"total_assets": 100.0, "total_liabilities": None,
                    "total_equity_incl_minority": 35.0}, ticker="NA.NS"),
    ], ignore_index=True)
    results = checks.check_balance_sheet_identity(table, tolerance=0.02)
    assert {r.ticker: r.status for r in results} == {"OK.NS": "pass", "OFF.NS": "fail"}
    flagged = failures(results)[0]
    assert flagged.severity == "warning" and "5.00%" in flagged.message


def test_margin_range():
    table = pd.concat([
        statements({"revenue": 100.0, "net_income": 20.0, "ebitda": 30.0}, ticker="OK.NS"),
        statements({"revenue": 100.0, "net_income": -150.0}, ticker="LOSS.NS"),  # -150% margin
    ], ignore_index=True)
    flagged = failures(checks.check_margins_in_range(table))
    assert [(r.check_name, r.ticker) for r in flagged] == \
        [("net_income_margin_in_range", "LOSS.NS")]
    assert "-150.0%" in flagged[0].message


def test_statement_currency_scale_catches_wrong_currency():
    table = pd.concat([
        statements({"eps_diluted": 70.0}, ticker="RIGHT.NS"),    # 70 / 73.4 = 0.95
        statements({"eps_diluted": 0.8}, ticker="USD.NS"),       # 0.8 / 73.4 = 0.011: unconverted
        statements({"eps_diluted": 5400.0}, ticker="TWICE.NS"),  # 5400 / 64 = 84: converted twice
    ], ignore_index=True)
    source = pd.DataFrame({"ticker": ["RIGHT.NS", "USD.NS", "TWICE.NS"],
                           "metric_name": "trailing_eps", "value": [73.4, 73.4, 64.0]})
    results = checks.check_statement_currency_scale(table, source)
    assert {r.ticker: r.status for r in results} == \
        {"RIGHT.NS": "pass", "USD.NS": "fail", "TWICE.NS": "fail"}
    assert all(r.severity == "error" for r in failures(results))


def test_eps_on_a_pre_split_share_basis_is_flagged():
    table = pd.concat([
        # 440 / 10 shares = 44.0 vs reported 45.0: 2% apart (dilution), fine
        statements({"eps_diluted": 45.0, "net_income": 440.0, "shares_outstanding": 10.0},
                   ticker="OK.NS"),
        # 444 / 10 = 44.4 vs reported 88.7: EPS still on the share count before a 1:1 bonus
        statements({"eps_diluted": 88.7, "net_income": 444.0, "shares_outstanding": 10.0},
                   ticker="BONUS.NS"),
    ], ignore_index=True)
    results = checks.check_eps_share_basis(table)
    assert {r.ticker: r.status for r in results} == {"OK.NS": "pass", "BONUS.NS": "fail"}
    flagged = failures(results)[0]
    assert flagged.severity == "warning"
    assert flagged.message.startswith("Potential data anomaly detected")


# --- completeness, consistency, freshness -------------------------------------

def test_completeness_excludes_not_applicable_fields():
    table = statements({
        "revenue": 100.0, "net_income": 10.0, "total_assets": 500.0,
        "eps_basic": None,                              # applicable but missing
        "gross_profit": (None, "not_applicable"),       # not counted
        "ebitda": (None, "not_applicable"),
    })
    result = checks.check_completeness(table, warn_below=0.80)[0]
    # 3 of 4 applicable fields = 75% -> below 80%
    assert result.status == "fail" and "3/4 applicable fields present (75.0%)" in result.message
    assert checks.check_completeness(table, warn_below=0.75)[0].status == "pass"
    by_company = checks.completeness_by_company(table).iloc[0]
    assert (by_company["present"], by_company["expected"]) == (3, 4)
    assert by_company["completeness_pct"] == 75.0


def observation(value, retrieved_at, line_item="revenue"):
    return {"ticker": "AAA.NS", "statement": "income", "line_item": line_item,
            "period_end_date": date(2026, 3, 31), "period_type": "annual", "value": value,
            "retrieved_at": pd.Timestamp(retrieved_at)}


def test_conflicting_values():
    same = pd.DataFrame([observation(100.0, "2026-04-01"), observation(100.0, "2026-04-02")])
    assert failures(checks.check_no_conflicting_values(same)) == []

    revised = pd.DataFrame([observation(100.0, "2026-04-01"), observation(101.0, "2026-04-02")])
    result = failures(checks.check_no_conflicting_values(revised))[0]
    assert result.severity == "info" and "revised" in result.message

    conflict = pd.DataFrame([observation(100.0, "2026-04-01"), observation(101.0, "2026-04-01")])
    result = failures(checks.check_no_conflicting_values(conflict))[0]
    assert result.severity == "error" and "within one retrieval" in result.message


def test_freshness():
    assert checks.last_weekday(date(2026, 10, 4)) == date(2026, 10, 2)   # Sunday -> Friday
    assert checks.last_weekday(date(2026, 10, 7)) == date(2026, 10, 7)   # Wednesday
    table = prices([("FRESH.NS", "2026-10-06", 1.0, 1.0, 1.0, 1.0, 1),
                    ("EDGE.NS", "2026-10-02", 1.0, 1.0, 1.0, 1.0, 1),    # exactly 5 days
                    ("STALE.NS", "2026-10-01", 1.0, 1.0, 1.0, 1.0, 1)])  # 6 days
    results = checks.check_freshness(table, as_of=date(2026, 10, 7), max_lag_days=5)
    assert {r.ticker: r.status for r in results} == \
        {"FRESH.NS": "pass", "EDGE.NS": "pass", "STALE.NS": "fail"}


# --- summary -----------------------------------------------------------------

def test_summary_is_computed_from_results():
    price_table = prices([
        GOOD,
        ("AAA.NS", "2026-10-06", 100.0, 101.0, 99.0, 104.0, 1000),   # bad high AND ...
        ("AAA.NS", "2026-10-07", 100.0, 105.0, 99.0, 103.0, -1),     # negative volume
    ])
    price_table.loc[1, "low"] = 102.0                                # ... bad low on the same row
    statement_table = statements({"revenue": 100.0, "net_income": None,
                                  "gross_profit": (None, "not_applicable")})
    cleaned = SimpleNamespace(prices=price_table, statements=statement_table,
                              metadata=pd.DataFrame({"ticker": ["AAA.NS"]}),
                              fx=pd.DataFrame(), rejected_prices=pd.DataFrame(),
                              duplicates_removed=4)
    results = (checks.check_high_is_highest(price_table) + checks.check_low_is_lowest(price_table)
               + checks.check_volume_non_negative(price_table)
               + checks.check_completeness(statement_table))
    summary = checks.summarize(results, cleaned)
    # 3 price rows + 3 statement rows + 1 metadata row = 7 records
    assert summary["total_records"] == 7
    # two distinct records have errors (the 6 Oct row fails two checks but counts once)
    assert summary["invalid_records"] == 2 and summary["valid_records"] == 5
    assert summary["pass_rate_pct"] == 100 * 5 / 7
    assert summary["warning_count"] == 1          # completeness: 1 of 2 applicable = 50%
    assert summary["missing_pct"] == 50.0         # not_applicable is outside the denominator
    assert summary["duplicate_records"] == 4
