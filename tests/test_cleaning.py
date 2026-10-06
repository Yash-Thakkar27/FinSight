"""Offline tests for the cleaning pipeline. Expected values are hand-calculated in comments."""

from datetime import date

import pandas as pd
import pytest

from config.settings import Company
from src.cleaning import pipeline
from src.cleaning.fiscal import fiscal_label, fiscal_quarter, fiscal_year, period_start

FIELD_MAP = pipeline.load_field_map()
RETRIEVED = "2026-04-01T10:00:00+00:00"
LATER = "2026-04-02T10:00:00+00:00"


def company(sector_type="non_financial", currency="INR", ticker="AAA.NS"):
    return Company(ticker=ticker, name="A", sector="S", industry="I", peer_group="g",
                   sector_type=sector_type, statement_currency=currency)


def raw_statement(rows: dict, periods=("2026-03-31", "2025-03-31")) -> pd.DataFrame:
    """A raw statement table as stored: line items as rows, ISO period ends as columns."""
    frame = pd.DataFrame(rows, index=list(periods)).T.astype("float64")
    frame.index.name = "line_item"
    return frame


def value_of(statements, line_item, period_end="2026-03-31", column="value"):
    row = statements[(statements["line_item"] == line_item)
                     & (statements["period_end_date"] == date.fromisoformat(period_end))]
    assert len(row) == 1, f"{line_item} {period_end}: {len(row)} rows"
    return row.iloc[0][column]


FX_SOURCE = "yfinance INR=X daily close"


def clean(co, snapshots, status=None, fx=None):
    return pipeline.clean_company_statements(
        co, snapshots, FIELD_MAP, status or {}, fx or {}, pd.Timestamp(RETRIEVED),
        {"USD": FX_SOURCE},
    )


# --- fiscal periods ----------------------------------------------------------

def test_fiscal_year_runs_april_to_march():
    assert fiscal_year(date(2025, 3, 31)) == 2025    # last day of FY2025
    assert fiscal_year(date(2025, 4, 1)) == 2026     # first day of FY2026
    assert fiscal_year(date(2025, 12, 31)) == 2026
    assert fiscal_year(date(2026, 1, 15)) == 2026


def test_fiscal_quarter_mapping():
    # Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar
    assert [fiscal_quarter(date(2025, m, 28)) for m in range(1, 13)] == \
        [4, 4, 4, 1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert fiscal_label(date(2025, 6, 30), "quarterly") == "Q1 FY2026"
    assert fiscal_label(date(2026, 3, 31), "quarterly") == "Q4 FY2026"
    assert fiscal_label(date(2025, 3, 31), "annual") == "FY2025"


def test_period_start():
    assert period_start(date(2026, 3, 31), "annual") == date(2025, 4, 1)
    assert period_start(date(2025, 6, 30), "quarterly") == date(2025, 4, 1)
    assert period_start(date(2025, 12, 31), "quarterly") == date(2025, 10, 1)


# --- prices ------------------------------------------------------------------

def raw_prices(dates, closes, volumes):
    index = pd.DatetimeIndex(dates, tz="Asia/Kolkata", name="Date")
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                         "Adj Close": closes, "Volume": volumes,
                         "Dividends": 0.0, "Stock Splits": 0.0}, index=index)


def test_price_dates_are_local_trading_dates_without_timezone():
    raw = raw_prices(["2026-10-06"], [100.0], [10])
    mapped = pipeline.map_prices(raw, "AAA.NS", RETRIEVED, "f.parquet", FIELD_MAP["market_prices"])
    # midnight IST on 6 Oct is 18:30 UTC on 5 Oct; the trading date must stay 6 Oct
    assert mapped.loc[0, "date"] == date(2026, 10, 6)
    assert list(mapped.columns[:8]) == ["ticker", "date", "open", "high", "low", "close",
                                        "adj_close", "volume"]


def test_duplicate_prices_keep_latest_retrieval():
    column_map = FIELD_MAP["market_prices"]
    old = pipeline.map_prices(raw_prices(["2026-10-05", "2026-10-06"], [100.0, 101.0], [5, 5]),
                              "AAA.NS", RETRIEVED, "old.parquet", column_map)
    new = pipeline.map_prices(raw_prices(["2026-10-06"], [102.0], [7]),
                              "AAA.NS", LATER, "new.parquet", column_map)
    cleaned, removed = pipeline.clean_prices(pd.concat([new, old], ignore_index=True))
    assert removed == 1
    assert len(cleaned) == 2
    assert cleaned.loc[cleaned["date"] == date(2026, 10, 6), "close"].item() == 102.0


def test_splits_are_read_from_the_price_history():
    raw = raw_prices(["2025-08-25", "2025-08-26", "2025-08-27"], [1900.0, 950.0, 955.0],
                     [10, 10, 10])
    raw["Stock Splits"] = [0.0, 2.0, 0.0]         # 1:1 bonus effective 26 August
    splits = pipeline.clean_splits(raw, "AAA.NS", RETRIEVED, "f.parquet")
    assert splits[["ticker", "date", "split_ratio"]].values.tolist() == \
        [["AAA.NS", date(2025, 8, 26), 2.0]]
    no_splits = pipeline.clean_splits(raw.assign(**{"Stock Splits": 0.0}), "AAA.NS", RETRIEVED, "f")
    assert no_splits.empty and list(no_splits.columns) == pipeline.SPLIT_COLUMNS


def test_stale_quote_flag():
    # day 3: zero volume and flat at the previous close -> placeholder
    # day 4: zero volume but the price moved -> not a placeholder
    # day 5: flat at the previous close but volume traded -> not a placeholder
    raw = raw_prices(["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"],
                     [100.0, 101.0, 101.0, 103.0, 103.0], [10, 10, 0, 0, 10])
    mapped = pipeline.map_prices(raw, "AAA.NS", RETRIEVED, "f", FIELD_MAP["market_prices"])
    cleaned, _ = pipeline.clean_prices(mapped)
    assert cleaned["is_stale_quote"].tolist() == [False, False, True, False, False]
    assert len(cleaned) == 5    # flagged, not deleted


def mapped_prices(dates, closes, retrieved_at, raw_file, adj=None):
    raw = raw_prices(dates, closes, [10] * len(dates))
    if adj is not None:
        raw["Adj Close"] = adj
    return pipeline.map_prices(raw, "AAA.NS", retrieved_at, raw_file, FIELD_MAP["market_prices"])


def test_incomplete_row_is_repaired_from_an_earlier_snapshot():
    days = ["2026-10-02", "2026-10-05", "2026-10-06"]
    earlier = mapped_prices(days, [100.0, 101.0, 102.0], RETRIEVED, "earlier.parquet")
    latest = mapped_prices(days, [100.0, 101.0, None], LATER, "latest.parquet")
    latest.loc[2, ["open", "high", "low"]] = [101.5, 102.5, 101.0]   # only the close is missing

    kept, repaired, rejected = pipeline.repair_incomplete_rows(latest, [earlier])
    assert len(kept) == 3 and len(repaired) == 1 and len(rejected) == 0
    row = kept[kept["date"] == date(2026, 10, 6)].iloc[0]
    assert row["close"] == 102.0
    assert row["raw_file"] == "earlier.parquet"      # lineage points at the snapshot used
    # untouched rows still come from the latest snapshot
    assert kept.loc[kept["date"] == date(2026, 10, 5), "raw_file"].item() == "latest.parquet"


def test_incomplete_row_is_rejected_when_the_adjustment_basis_changed():
    days = ["2026-10-02", "2026-10-05", "2026-10-06"]
    # the earlier snapshot's adjusted closes are 2% higher: a dividend re-based the history since
    earlier = mapped_prices(days, [100.0, 101.0, 102.0], RETRIEVED, "earlier.parquet",
                            adj=[102.0, 103.02, 104.04])
    latest = mapped_prices(days, [100.0, 101.0, None], LATER, "latest.parquet")
    kept, repaired, rejected = pipeline.repair_incomplete_rows(latest, [earlier])
    assert len(repaired) == 0 and len(rejected) == 1
    assert kept["date"].tolist() == [date(2026, 10, 2), date(2026, 10, 5)]   # row not loaded
    assert kept["close"].notna().all()


def test_incomplete_row_is_rejected_without_an_earlier_snapshot():
    latest = mapped_prices(["2026-10-05", "2026-10-06"], [101.0, None], LATER, "latest.parquet")
    kept, repaired, rejected = pipeline.repair_incomplete_rows(latest, [])
    assert len(kept) == 1 and len(repaired) == 0
    assert rejected["date"].tolist() == [date(2026, 10, 6)]


# --- statements --------------------------------------------------------------

def test_observe_statement_maps_labels_and_never_fills_zero():
    raw = raw_statement({
        "Total Revenue": [None, 900.0],          # primary label missing for FY2026 ...
        "Operating Revenue": [1000.0, 950.0],    # ... so the fallback label is used there
        "Gross Profit": [None, None],
        "Net Income": [150.0, 120.0],
    })
    observed = pipeline.observe_statement(raw, "income", "annual", FIELD_MAP, RETRIEVED, "f")
    by_key = {(r.line_item, str(r.period_end_date)): r for r in observed.itertuples()}
    assert by_key[("revenue", "2026-03-31")].value == 1000.0
    assert by_key[("revenue", "2026-03-31")].source_label == "Operating Revenue"
    assert by_key[("revenue", "2025-03-31")].value == 900.0      # primary label wins
    assert by_key[("revenue", "2025-03-31")].source_label == "Total Revenue"
    assert not any(key[0] == "gross_profit" for key in by_key)   # missing stays missing
    assert (observed["value"] != 0).all()


def test_all_null_period_is_skipped_and_capex_sign_is_flipped():
    raw = raw_statement({
        "Operating Cash Flow": [500.0, None],
        "Capital Expenditure": [-120.0, None],   # source reports outflows as negative
    })
    observed = pipeline.observe_statement(raw, "cashflow", "annual", FIELD_MAP, RETRIEVED, "f")
    assert set(observed["period_end_date"]) == {date(2026, 3, 31)}
    assert observed.loc[observed["line_item"] == "capex", "value"].item() == 120.0


def test_missing_values_are_classified():
    income = raw_statement({"Total Revenue": [1000.0, 900.0], "Net Income": [150.0, 120.0],
                            "Gross Profit": [400.0, 350.0]})
    snapshots = {("income", "annual"): [(income, RETRIEVED, "f")]}
    status = {"income_annual": "ok", "balance_annual": "empty", "cashflow_annual": "failed"}

    bank, _, _, _ = clean(company("bank"), snapshots, status)
    # a bank's gross profit is not meaningful, even though this source table has a number
    assert pd.isna(value_of(bank, "gross_profit"))
    assert value_of(bank, "gross_profit", column="missing_reason") == "not_applicable"
    assert value_of(bank, "net_income") == 150.0
    assert pd.isna(value_of(bank, "net_income", column="missing_reason"))
    # income field the source did not provide
    assert value_of(bank, "eps_basic", column="missing_reason") == "unavailable_from_source"
    # balance sheet came back empty; cash flow could not be retrieved
    assert value_of(bank, "total_assets", column="missing_reason") == "unavailable_from_source"
    assert value_of(bank, "capex", column="missing_reason") == "failed_retrieval"

    non_financial, _, _, _ = clean(company("non_financial"), snapshots, status)
    assert value_of(non_financial, "gross_profit") == 400.0
    assert value_of(non_financial, "total_loans", column="missing_reason") == "not_applicable"

    # every row has a value XOR a reason, and no financial value was filled with 0
    for frame in (bank, non_financial):
        assert (frame["value"].isna() == frame["missing_reason"].notna()).all()
        assert not (frame["value"] == 0).any()


def test_fiscal_labels_on_statement_rows():
    quarterly = raw_statement({"Total Revenue": [250.0, 240.0]},
                              periods=("2025-06-30", "2025-03-31"))
    statements, _, _, _ = clean(company(), {("income", "quarterly"): [(quarterly, RETRIEVED, "f")]})
    q1 = statements[statements["period_end_date"] == date(2025, 6, 30)].iloc[0]
    q4 = statements[statements["period_end_date"] == date(2025, 3, 31)].iloc[0]
    assert (q1["fiscal_year"], q1["fiscal_quarter"]) == (2026, 1)
    assert (q4["fiscal_year"], q4["fiscal_quarter"]) == (2025, 4)


def test_statement_duplicates_keep_latest_retrieval():
    first = raw_statement({"Total Revenue": [1000.0, 900.0]})
    revised = raw_statement({"Total Revenue": [1010.0, 900.0]})
    snapshots = {("income", "annual"): [(first, RETRIEVED, "old"), (revised, LATER, "new")]}
    statements, observations, removed, _ = clean(company(), snapshots)
    assert len(observations) == 4 and removed == 2
    assert value_of(statements, "revenue") == 1010.0
    assert value_of(statements, "revenue", column="raw_file") == "new"


def test_derived_fields():
    income = raw_statement({"EBIT": [300.0, 280.0],
                            "Reconciled Depreciation": [50.0, None]})
    cashflow = raw_statement({"Operating Cash Flow": [500.0, 450.0],
                              "Capital Expenditure": [-120.0, -100.0],
                              "Free Cash Flow": [None, 350.0]})
    snapshots = {("income", "annual"): [(income, RETRIEVED, "f")],
                 ("cashflow", "annual"): [(cashflow, RETRIEVED, "f")]}
    statements, _, _, _ = clean(company(), snapshots)

    # FCF = OCF - capex = 500 - 120 = 380 (capex is a positive outflow after cleaning)
    assert value_of(statements, "free_cash_flow") == 380.0
    assert value_of(statements, "free_cash_flow", column="original_value") == 380.0
    assert bool(value_of(statements, "free_cash_flow", column="is_calculated"))
    assert value_of(statements, "free_cash_flow", column="formula_id") == "DERIVED_FCF"
    assert pd.isna(value_of(statements, "free_cash_flow", column="missing_reason"))
    # a value the source did provide is left alone
    assert value_of(statements, "free_cash_flow", "2025-03-31") == 350.0
    assert not value_of(statements, "free_cash_flow", "2025-03-31", column="is_calculated")
    # EBITDA = EBIT + D&A = 300 + 50 = 350
    assert value_of(statements, "ebitda") == 350.0
    assert value_of(statements, "ebitda", column="formula_id") == "DERIVED_EBITDA"
    # FY2025 has no D&A, so EBITDA cannot be derived and stays missing
    assert pd.isna(value_of(statements, "ebitda", "2025-03-31"))

    bank, _, _, _ = clean(company("bank"), snapshots)
    assert pd.isna(value_of(bank, "ebitda"))       # never derived where not applicable
    assert value_of(bank, "ebitda", column="missing_reason") == "not_applicable"


# --- foreign-currency conversion ------------------------------------------------

def usd_rates():
    # quotes on 1 Apr, 15 May and 30 Jun 2025: average 82, closing 84
    return pd.Series([80.0, 82.0, 84.0],
                     index=pd.DatetimeIndex(["2025-04-01", "2025-05-15", "2025-06-30"]))


def test_fx_rates():
    rates = usd_rates()
    assert pipeline.average_rate(rates, date(2025, 4, 1), date(2025, 6, 30)) == 82.0
    assert pipeline.closing_rate(rates, date(2025, 6, 30)) == 84.0
    assert pipeline.closing_rate(rates, date(2025, 7, 3)) == 84.0     # 3 days old: accepted
    assert pipeline.closing_rate(rates, date(2025, 7, 31)) is None    # a month old: rejected
    # quotes cover only the tail of the period, or only its start: no average
    assert pipeline.average_rate(rates, date(2025, 1, 1), date(2025, 6, 30)) is None
    assert pipeline.average_rate(rates, date(2025, 4, 1), date(2026, 3, 31)) is None


def usd_quarter_snapshots():
    periods = ("2025-06-30",)
    return {
        ("income", "quarterly"): [(raw_statement(
            {"Total Revenue": [100.0], "Net Income": [20.0], "Gross Profit": [40.0],
             "Diluted EPS": [2.0], "EBIT": [25.0], "Reconciled Depreciation": [5.0]},
            periods), RETRIEVED, "f")],
        ("balance", "quarterly"): [(raw_statement(
            {"Total Assets": [50.0], "Ordinary Shares Number": [1000.0]}, periods),
            RETRIEVED, "f")],
        ("cashflow", "quarterly"): [(raw_statement(
            {"Operating Cash Flow": [30.0], "Capital Expenditure": [-10.0]}, periods),
            RETRIEVED, "f")],
    }


def test_flow_item_is_translated_at_the_period_average_rate():
    statements, _, _, issues = clean(company(currency="USD"), usd_quarter_snapshots(),
                                     fx={"USD": usd_rates()})
    assert issues == []
    # quotes 80, 82, 84 over the quarter: average 82. Revenue 100 USD -> 8,200 INR
    assert value_of(statements, "revenue", "2025-06-30") == pytest.approx(8200.0)
    assert value_of(statements, "revenue", "2025-06-30", "fx_rate") == pytest.approx(82.0)
    assert value_of(statements, "revenue", "2025-06-30", "fx_rate_type") == "average"
    assert value_of(statements, "revenue", "2025-06-30", "fx_source") == FX_SOURCE
    # per-share flows and cash-flow items use the same average: EPS 2 -> 164; capex 10 -> 820
    assert value_of(statements, "eps_diluted", "2025-06-30") == pytest.approx(164.0)
    assert value_of(statements, "capex", "2025-06-30") == pytest.approx(820.0)
    assert value_of(statements, "capex", "2025-06-30", "fx_rate_type") == "average"


def test_balance_item_is_translated_at_the_period_end_rate():
    statements, _, _, _ = clean(company(currency="USD"), usd_quarter_snapshots(),
                                fx={"USD": usd_rates()})
    # closing quote on 30 June is 84. Total assets 50 USD -> 4,200 INR
    assert value_of(statements, "total_assets", "2025-06-30") == pytest.approx(4200.0)
    assert value_of(statements, "total_assets", "2025-06-30", "fx_rate") == pytest.approx(84.0)
    assert value_of(statements, "total_assets", "2025-06-30", "fx_rate_type") == "period_end"
    # a share count is not money: untouched, no rate recorded
    assert value_of(statements, "shares_outstanding", "2025-06-30") == 1000.0
    assert pd.isna(value_of(statements, "shares_outstanding", "2025-06-30", "fx_rate"))
    assert pd.isna(value_of(statements, "shares_outstanding", "2025-06-30", "fx_rate_type"))


def test_reported_usd_values_are_never_overwritten():
    statements, _, _, _ = clean(company(currency="USD"), usd_quarter_snapshots(),
                                fx={"USD": usd_rates()})
    for line_item, reported in (("revenue", 100.0), ("total_assets", 50.0), ("capex", 10.0),
                                ("eps_diluted", 2.0)):
        assert value_of(statements, line_item, "2025-06-30", "original_value") == reported
        assert value_of(statements, line_item, "2025-06-30", "original_currency") == "USD"
    assert value_of(statements, "revenue", "2025-06-30", "original_unit") == "USD"
    assert value_of(statements, "eps_diluted", "2025-06-30", "original_unit") == "USD_per_share"
    assert value_of(statements, "revenue", "2025-06-30", "currency") == "INR"


def test_derived_field_is_computed_in_reporting_currency_then_translated():
    statements, _, _, _ = clean(company(currency="USD"), usd_quarter_snapshots(),
                                fx={"USD": usd_rates()})
    # FCF = 30 - 10 = 20 USD, then translated at the average rate 82 -> 1,640 INR
    assert value_of(statements, "free_cash_flow", "2025-06-30", "original_value") == 20.0
    assert value_of(statements, "free_cash_flow", "2025-06-30") == pytest.approx(1640.0)
    assert bool(value_of(statements, "free_cash_flow", "2025-06-30", "is_calculated"))
    # EBITDA = 25 + 5 = 30 USD -> 2,460 INR
    assert value_of(statements, "ebitda", "2025-06-30", "original_value") == 30.0
    assert value_of(statements, "ebitda", "2025-06-30") == pytest.approx(2460.0)


def test_margins_are_identical_before_and_after_translation():
    statements, _, _, _ = clean(company(currency="USD"), usd_quarter_snapshots(),
                                fx={"USD": usd_rates()})

    def margin(numerator, column):
        return (value_of(statements, numerator, "2025-06-30", column)
                / value_of(statements, "revenue", "2025-06-30", column))

    # both sides of a margin are flows translated at the same average rate, so it cancels
    assert margin("net_income", "original_value") == pytest.approx(0.20)      # 20 / 100
    assert margin("net_income", "value") == pytest.approx(margin("net_income", "original_value"))
    assert margin("gross_profit", "value") == pytest.approx(0.40)             # 40 / 100
    assert margin("free_cash_flow", "value") == pytest.approx(0.20)           # 20 / 100
    # a flow over a balance is NOT invariant (82 vs 84), which is why such ratios
    # are computed from the reported values: 100 / 50 = 2.0, but 8,200 / 4,200 = 1.952
    turnover_reported = (value_of(statements, "revenue", "2025-06-30", "original_value")
                         / value_of(statements, "total_assets", "2025-06-30", "original_value"))
    turnover_translated = (value_of(statements, "revenue", "2025-06-30")
                           / value_of(statements, "total_assets", "2025-06-30"))
    assert turnover_reported == 2.0
    assert turnover_translated == pytest.approx(8200 / 4200)
    assert turnover_translated != pytest.approx(turnover_reported)


def test_missing_fx_rate_gives_null_not_an_unconverted_number():
    snapshots = {("income", "annual"): [(raw_statement({"Total Revenue": [100.0, 90.0]}),
                                         RETRIEVED, "f")]}
    statements, _, _, issues = clean(company(currency="USD"), snapshots, fx={"USD": usd_rates()})
    assert pd.isna(value_of(statements, "revenue"))
    assert value_of(statements, "revenue", column="missing_reason") == "unavailable_from_source"
    assert value_of(statements, "revenue", column="original_value") == 100.0   # reported value kept
    assert len(issues) == 2 and issues[0]["kind"] == "fx_rate_missing"


def test_inr_company_is_not_converted():
    snapshots = {("income", "annual"): [(raw_statement({"Total Revenue": [100.0, 90.0]}),
                                         RETRIEVED, "f")]}
    statements, _, _, _ = clean(company(), snapshots)
    assert value_of(statements, "revenue") == 100.0
    assert pd.isna(value_of(statements, "revenue", column="fx_rate"))
    assert value_of(statements, "revenue", column="original_unit") == "INR"


# --- metadata ----------------------------------------------------------------

def test_clean_metadata():
    info = {"longName": "A Ltd", "exchange": "NSI", "sharesOutstanding": 1000,
            "trailingPE": 20.5, "marketCap": None, "currency": "INR"}
    row, metrics = pipeline.clean_metadata(info, "AAA.NS", RETRIEVED, "f.json", FIELD_MAP)
    assert row["source_name"] == "A Ltd" and row["shares_outstanding"] == 1000
    assert row["shares_as_of_date"] == date(2026, 4, 1)    # the retrieval date
    assert row["source_sector"] is None                    # absent stays absent
    assert [(m["metric_name"], m["value"]) for m in metrics] == [("pe_ratio", 20.5)]
