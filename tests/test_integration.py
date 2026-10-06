"""End-to-end test: fixture raw snapshots -> clean -> validate -> PostgreSQL, run twice.

Uses a separate `finsight_test` database in the project's PostgreSQL container
(created on first use) and never touches the main database or the network.
Skipped automatically when PostgreSQL is not reachable.
"""

import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text

from config.settings import Universe, get_database_settings
from src.cleaning.pipeline import load_field_map
from src.database import models
from src.database.setup import initialize
from src.ingestion import raw_store
from src.ingestion.financial_data import to_storable
from src.pipeline import run_pipeline

pytestmark = pytest.mark.integration

TEST_DATABASE = "finsight_test"
RETRIEVED = pd.Timestamp("2026-04-01 06:00", tz="UTC").to_pydatetime()
DAYS = ["2026-03-23", "2026-03-24", "2026-03-25", "2026-03-26", "2026-03-27",
        "2026-03-30", "2026-03-31"]
FX_RATE = 85.0


def company(ticker, sector_type="non_financial", **extra):
    return {"ticker": ticker, "name": ticker, "sector": "Banks" if sector_type == "bank" else "IT",
            "industry": "x" + sector_type, "peer_group": sector_type, "sector_type": sector_type,
            **extra}


UNIVERSE = Universe.model_validate({
    "benchmark": {"ticker": "^IDX", "name": "Index"},
    "risk_free_rate": {"value": None},
    "fx": {"USD": {"ticker": "INR=X"}},
    "companies": [company("INRCO.NS"), company("USDCO.NS", statement_currency="USD"),
                  company("BANK.NS", "bank")],
})


@pytest.fixture(scope="module")
def engine():
    settings = get_database_settings()
    try:
        admin = create_engine(settings.url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"),
                                  {"name": TEST_DATABASE}).scalar()
            if not exists:
                conn.execute(text(f"CREATE DATABASE {TEST_DATABASE}"))
        admin.dispose()
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable: {type(exc).__name__}")
    test_engine = create_engine(settings.url.rsplit("/", 1)[0] + f"/{TEST_DATABASE}")
    with test_engine.begin() as conn:
        initialize(conn, UNIVERSE, reset=True)
    yield test_engine
    test_engine.dispose()


def save(frame, dataset, ticker, period_type, raw_dir):
    meta = raw_store.make_meta("yfinance", dataset, ticker, period_type, RETRIEVED)
    raw_store.save_frame(frame, meta, raw_dir)


def price_frame(closes, volumes):
    index = pd.DatetimeIndex(DAYS, tz="Asia/Kolkata", name="Date")
    closes = pd.Series(closes, index=index, dtype="float64")
    return pd.DataFrame({"Open": closes, "High": closes + 1, "Low": closes - 1, "Close": closes,
                         "Adj Close": closes, "Volume": volumes,
                         "Dividends": 0.0, "Stock Splits": 0.0})


def statement(rows):
    frame = pd.DataFrame(rows, index=[pd.Timestamp("2026-03-31"), pd.Timestamp("2025-03-31")]).T
    return to_storable(frame.astype("float64"))


@pytest.fixture(scope="module")
def raw_dir(tmp_path_factory):
    """Raw snapshots for three companies, an index and an FX series."""
    root = tmp_path_factory.mktemp("raw")
    rising = [100.0, 101.0, 102.0, 102.0, 104.0, 105.0, 106.0]
    for ticker in UNIVERSE.tickers + ["^IDX"]:
        frame = price_frame(rising, [10, 10, 10, 10, 10, 10, 10])
        if ticker == "INRCO.NS":
            # 26 March: zero volume, flat at the previous close -> placeholder row
            frame.loc[frame.index[3], ["Open", "High", "Low", "Close", "Volume"]] = \
                [102.0, 102.0, 102.0, 102.0, 0]
        save(frame, "market_prices", ticker, "daily", root)

    fx_days = pd.bdate_range("2024-04-01", "2026-03-31", tz="Europe/London", name="Date")
    save(pd.DataFrame({"Close": FX_RATE}, index=fx_days), "fx_rates", "INR=X", "daily", root)

    for ticker, eps in (("INRCO.NS", 10.0), ("USDCO.NS", 0.5), ("BANK.NS", 20.0)):
        income = {"Total Revenue": [1000.0, 900.0], "Net Income": [150.0, 120.0],
                  "Diluted EPS": [eps, eps]}
        if ticker != "BANK.NS":
            income["Gross Profit"] = [400.0, 350.0]
        balance = {"Total Assets": [2000.0, 1800.0],
                   "Total Liabilities Net Minority Interest": [1200.0, 1100.0],
                   "Total Equity Gross Minority Interest": [800.0, 700.0],
                   "Ordinary Shares Number": [15.0, 15.0]}
        cashflow = {"Operating Cash Flow": [300.0, 250.0], "Capital Expenditure": [-80.0, -70.0]}
        save(statement(income), "income_annual", ticker, "annual", root)
        save(statement(balance), "balance_annual", ticker, "annual", root)
        save(statement(cashflow), "cashflow_annual", ticker, "annual", root)
        meta = raw_store.make_meta("yfinance", "company_metadata", ticker, "point_in_time",
                                   RETRIEVED)
        # trailing EPS is in INR at source: 0.5 USD * 85 = 42.5 for the USD reporter
        raw_store.save_json({"longName": ticker, "exchange": "NSI", "country": "India",
                             "currency": "INR", "sharesOutstanding": 15,
                             "trailingEps": 42.0 if ticker == "USDCO.NS" else eps}, meta, root)
    return root


def run(engine, raw_dir):
    return run_pipeline(UNIVERSE, engine, skip_fetch=True, raw_dir=raw_dir, processed_dir=None,
                        as_of=pd.Timestamp("2026-03-31").date())


def scalar(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def statement_row(engine, ticker, line_item, period_end="2026-03-31"):
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT f.value, f.missing_reason, f.fx_rate, f.original_currency, f.is_calculated,
                   f.formula_id, f.fiscal_year, f.fiscal_quarter
            FROM core.financial_statements f JOIN core.companies c USING (company_id)
            WHERE c.ticker = :ticker AND f.line_item = :item AND f.period_end_date = :end
        """), {"ticker": ticker, "item": line_item, "end": period_end}).mappings().one()


def test_pipeline_end_to_end_and_idempotent(engine, raw_dir):
    first = run(engine, raw_dir)
    assert first["status"] == "success"

    n_fields = sum(len(load_field_map()[s]) for s in ("income", "balance", "cashflow"))
    counts = first["table_counts"]
    assert counts["core.market_prices"] == 4 * 7             # 3 companies + index, 7 days
    assert counts["core.financial_statements"] == 3 * 2 * n_fields   # 3 companies, 2 years
    assert counts["core.fx_rates"] == len(pd.bdate_range("2024-04-01", "2026-03-31"))
    assert counts["core.shares_outstanding"] == 3
    assert counts["core.pipeline_runs"] == 1

    # INR company: stored as reported; capex is a positive outflow
    revenue = statement_row(engine, "INRCO.NS", "revenue")
    assert float(revenue["value"]) == 1000.0 and revenue["fx_rate"] is None
    assert (revenue["fiscal_year"], revenue["fiscal_quarter"]) == (2026, None)
    assert float(statement_row(engine, "INRCO.NS", "capex")["value"]) == 80.0
    # FCF is absent at source, so it is derived: 300 - 80 = 220
    fcf = statement_row(engine, "INRCO.NS", "free_cash_flow")
    assert float(fcf["value"]) == 220.0 and fcf["is_calculated"]
    assert fcf["formula_id"] == "DERIVED_FCF"

    # USD company: revenue 1000 * 85 = 85,000; EPS 0.5 * 85 = 42.5; shares untouched
    revenue = statement_row(engine, "USDCO.NS", "revenue")
    assert float(revenue["value"]) == 85000.0 and float(revenue["fx_rate"]) == 85.0
    assert revenue["original_currency"] == "USD"
    assert float(statement_row(engine, "USDCO.NS", "eps_diluted")["value"]) == 42.5
    assert float(statement_row(engine, "USDCO.NS", "shares_outstanding")["value"]) == 15.0

    # bank: gross profit is NULL and marked not applicable, never 0
    gross = statement_row(engine, "BANK.NS", "gross_profit")
    assert gross["value"] is None and gross["missing_reason"] == "not_applicable"
    assert scalar(engine, "SELECT count(*) FROM core.financial_statements WHERE value = 0") == 0

    # the placeholder price row is kept and flagged
    assert scalar(engine, "SELECT count(*) FROM core.market_prices WHERE is_stale_quote") == 1

    # quality results were written for this run and the summary is computed from them
    run_id = first["run_id"]
    summary = first["summary"]
    assert summary["invalid_records"] == 0 and summary["pass_rate_pct"] == 100.0
    assert scalar(engine, "SELECT total_records FROM core.data_quality_summary WHERE run_id = :r",
                  r=run_id) == summary["total_records"]
    assert scalar(engine, "SELECT count(*) FROM core.data_quality_logs WHERE run_id = :r",
                  r=run_id) > 0
    assert scalar(engine, "SELECT status FROM core.pipeline_runs WHERE run_id = :r",
                  r=run_id) == "success"

    # second run on the same raw data: identical row counts and identical values
    def fingerprint():
        return scalar(engine, """
            SELECT md5(string_agg(md5(f::text), '' ORDER BY company_id, statement, line_item,
                                  period_end_date, period_type))
            FROM core.financial_statements f""")

    before = fingerprint()
    second = run(engine, raw_dir)
    grows_per_run = {"core.pipeline_runs", "core.data_quality_logs", "core.data_quality_summary"}
    for table, rows in first["table_counts"].items():
        if table not in grows_per_run:
            assert second["table_counts"][table] == rows, table
    assert second["table_counts"]["core.pipeline_runs"] == 2
    assert fingerprint() == before


def test_incomplete_latest_price_row_never_overwrites_good_data(engine, raw_dir):
    """A newer snapshot with a missing close must not null out the stored close."""
    later = pd.Timestamp("2026-04-02 06:00", tz="UTC").to_pydatetime()
    rising = [100.0, 101.0, 102.0, 102.0, 104.0, 105.0, 106.0]

    # BANK.NS: same history, last close missing -> repaired from the earlier snapshot
    broken = price_frame(rising, [10] * 7)
    broken.loc[broken.index[-1], ["Close", "Adj Close"]] = None
    meta = raw_store.make_meta("yfinance", "market_prices", "BANK.NS", "daily", later)
    raw_store.save_frame(broken, meta, raw_dir)

    # USDCO.NS: last close missing AND adjusted closes re-based by 1% -> cannot be repaired
    rebased = price_frame(rising, [10] * 7)
    rebased["Adj Close"] = rebased["Close"] * 0.99
    rebased.loc[rebased.index[-1], ["Close", "Adj Close"]] = None
    meta = raw_store.make_meta("yfinance", "market_prices", "USDCO.NS", "daily", later)
    raw_store.save_frame(rebased, meta, raw_dir)

    outcome = run(engine, raw_dir)

    def last_close(ticker):
        return scalar(engine, """
            SELECT p.close FROM core.market_prices p JOIN core.companies c USING (company_id)
            WHERE c.ticker = :t AND p.date = '2026-03-31'""", t=ticker)

    assert float(last_close("BANK.NS")) == 106.0          # kept, from the earlier snapshot
    assert last_close("USDCO.NS") is None                 # row removed, not stored as NULL
    assert scalar(engine, """
        SELECT count(*) FROM core.market_prices p JOIN core.companies c USING (company_id)
        WHERE c.ticker = 'USDCO.NS'""") == 6
    assert scalar(engine, "SELECT count(*) FROM core.market_prices WHERE close IS NULL") == 0
    # the re-based adjusted closes of the newer snapshot are what is stored for USDCO.NS
    assert float(scalar(engine, """
        SELECT p.adj_close FROM core.market_prices p JOIN core.companies c USING (company_id)
        WHERE c.ticker = 'USDCO.NS' AND p.date = '2026-03-30'""")) == pytest.approx(105 * 0.99)

    # one rejected row is an error-severity record; the repair is a warning
    assert outcome["summary"]["invalid_records"] == 1
    logged = dict(pd.read_sql(text("""
        SELECT check_name, count(*) AS n FROM core.data_quality_logs
        WHERE run_id = :r AND check_name IN ('price_row_repaired', 'incomplete_price_row')
        GROUP BY 1"""), engine, params={"r": outcome["run_id"]}).values.tolist())
    assert logged == {"price_row_repaired": 1, "incomplete_price_row": 1}


def test_models_match_schema(engine):
    """Every table created by sql/schema.sql has a model with the same columns and nullability."""
    inspector = inspect(engine)
    model_tables = {(t.schema, t.name): t for t in models.Base.metadata.tables.values()}
    database_tables = {(schema, name) for schema in ("core", "staging")
                       for name in inspector.get_table_names(schema=schema)}
    assert set(model_tables) == database_tables

    for (schema, name), table in model_tables.items():
        database_columns = {c["name"]: c for c in inspector.get_columns(name, schema=schema)}
        assert set(database_columns) == {c.name for c in table.columns}, f"{schema}.{name}"
        for column in table.columns:
            assert database_columns[column.name]["nullable"] == column.nullable, \
                f"{schema}.{name}.{column.name} nullability"
