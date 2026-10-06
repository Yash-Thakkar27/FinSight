"""Offline tests for ingestion. The source is replaced by fakes; no network calls."""

import json

import pandas as pd
import pytest

from config.settings import IngestionSettings, RiskFreeRate
from src.ingestion import financial_data, raw_store
from src.ingestion import run as ingest_run
from src.ingestion.common import EmptyResponseError, IngestResult, fetch_with_retry, is_rate_limit
from src.ingestion.company_metadata import ingest_metadata
from src.ingestion.financial_data import ingest_statement, to_storable
from src.ingestion.market_data import ingest_prices

FAST = IngestionSettings(max_attempts=3, backoff_seconds=2, rate_limit_wait_seconds=30,
                         pause_seconds=0)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Retries and pauses must not slow the tests down."""
    monkeypatch.setattr("src.ingestion.common.time.sleep", lambda s: None)
    monkeypatch.setattr("src.ingestion.run.time.sleep", lambda s: None)
    monkeypatch.setattr("src.ingestion.common.get_ingestion_settings", lambda: FAST)
    monkeypatch.setattr("src.ingestion.run.get_ingestion_settings", lambda: FAST)


def price_frame():
    index = pd.DatetimeIndex(["2026-10-05", "2026-10-06"], tz="Asia/Kolkata", name="Date")
    return pd.DataFrame(
        {"Open": [100.0, 102.0], "High": [103.0, 104.0], "Low": [99.0, 101.0],
         "Close": [102.0, 103.5], "Adj Close": [101.5, 103.0], "Volume": [1000, 1200]},
        index=index,
    )


def statement_frame():
    return pd.DataFrame(
        {pd.Timestamp("2026-03-31"): [500.0, None], pd.Timestamp("2025-03-31"): [450.0, 90.0]},
        index=["Total Revenue", "EBITDA"],
    )


class FakeTicker:
    """Stands in for yfinance.Ticker."""

    def __init__(self, ticker, prices=None, info=None, statement=None, fail=False):
        self.ticker = ticker
        self._prices = price_frame() if prices is None else prices
        self._info = {"longName": "Fake Co", "sharesOutstanding": 10} if info is None else info
        self._statement = statement_frame() if statement is None else statement
        self._fail = fail

    def history(self, **kwargs):
        if self._fail:
            raise ConnectionError("network down")
        return self._prices

    @property
    def info(self):
        if self._fail:
            raise ConnectionError("network down")
        return self._info

    def __getattr__(self, name):
        if name in financial_data.STATEMENT_ATTRIBUTES.values():
            if self._fail:
                raise ConnectionError("network down")
            return self._statement
        raise AttributeError(name)


# --- raw store -------------------------------------------------------------

def test_snapshot_path_and_metadata_round_trip(tmp_path):
    when = pd.Timestamp("2026-10-06 18:30:05", tz="UTC").to_pydatetime()
    meta = raw_store.make_meta("yfinance", "market_prices", "TCS.NS", "daily", when)
    path = raw_store.save_frame(price_frame(), meta, tmp_path)

    expected = tmp_path / "yfinance" / "market_prices" / "TCS.NS" / "20261006T183005Z.parquet"
    assert path == expected
    assert raw_store.read_meta(path) == meta
    assert meta.retrieved_at == "2026-10-06T18:30:05+00:00"
    # the response comes back exactly as it went in, including the timezone-aware index
    pd.testing.assert_frame_equal(raw_store.read_frame(path), price_frame())


def test_raw_snapshot_is_never_overwritten(tmp_path):
    when = raw_store.utc_now()
    meta = raw_store.make_meta("yfinance", "market_prices", "TCS.NS", "daily", when)
    path = raw_store.save_frame(price_frame(), meta, tmp_path)
    with pytest.raises(FileExistsError):
        raw_store.save_frame(price_frame() * 2, meta, tmp_path)
    pd.testing.assert_frame_equal(raw_store.read_frame(path), price_frame())
    assert not (path.stat().st_mode & 0o222), "raw file should be read-only"


def test_naive_retrieved_at_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        raw_store.make_meta("yfinance", "market_prices", "TCS.NS", "daily",
                            pd.Timestamp("2026-10-06").to_pydatetime())


def test_latest_snapshot_picks_newest(tmp_path):
    assert raw_store.latest_snapshot("yfinance", "market_prices", "TCS.NS", tmp_path) is None
    for stamp in ("2026-10-06 09:00", "2026-10-07 09:00", "2026-10-05 09:00"):
        when = pd.Timestamp(stamp, tz="UTC").to_pydatetime()
        meta = raw_store.make_meta("yfinance", "market_prices", "TCS.NS", "daily", when)
        raw_store.save_frame(price_frame(), meta, tmp_path)
    latest = raw_store.latest_snapshot("yfinance", "market_prices", "TCS.NS", tmp_path)
    assert latest.name == "20261007T090000Z.parquet"


def test_json_snapshot_round_trip(tmp_path):
    result = ingest_metadata("TCS.NS", FakeTicker("TCS.NS"), tmp_path)
    assert result.status == "ok"
    path = raw_store.latest_snapshot("yfinance", "company_metadata", "TCS.NS", tmp_path)
    assert raw_store.read_json(path) == {"longName": "Fake Co", "sharesOutstanding": 10}
    assert raw_store.read_meta(path).period_type == "point_in_time"


# --- statements ------------------------------------------------------------

def test_statement_storage_keeps_values_and_nulls(tmp_path):
    stored = to_storable(statement_frame())
    assert list(stored.columns) == ["2026-03-31", "2025-03-31"]
    assert stored.index.name == "line_item"

    result = ingest_statement("TCS.NS", FakeTicker("TCS.NS"), "income", "annual", tmp_path)
    assert (result.status, result.rows, result.columns) == ("ok", 2, 2)
    back = raw_store.read_frame(result.path)
    assert back.loc["Total Revenue", "2025-03-31"] == 450.0
    # a missing source value stays missing: it is not turned into 0
    assert pd.isna(back.loc["EBITDA", "2026-03-31"])
    assert raw_store.read_meta(result.path).dataset == "income_annual"


def test_empty_statement_is_recorded_as_empty_not_failed(tmp_path):
    fake = FakeTicker("HDFCBANK.NS", statement=pd.DataFrame())
    result = ingest_statement("HDFCBANK.NS", fake, "cashflow", "quarterly", tmp_path)
    assert result.status == "empty"
    assert result.path is None
    assert not (tmp_path / "yfinance" / "cashflow_quarterly").exists()


# --- retry -----------------------------------------------------------------

def test_retry_succeeds_after_failures_with_exponential_backoff():
    calls, waits = [], []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("boom")
        return "ok"

    assert fetch_with_retry(flaky, "x", FAST, sleep=waits.append) == "ok"
    # backoff_seconds=2: waits are 2 * 2^0 and 2 * 2^1
    assert waits == [2, 4]


def test_retry_gives_up_and_reraises():
    waits = []

    def always_fails():
        raise ConnectionError("boom")

    with pytest.raises(ConnectionError):
        fetch_with_retry(always_fails, "x", FAST, sleep=waits.append)
    assert len(waits) == FAST.max_attempts - 1


def test_rate_limit_waits_longer():
    waits = []

    def limited():
        raise RuntimeError("Too Many Requests. Rate limited.")

    with pytest.raises(RuntimeError):
        fetch_with_retry(limited, "x", FAST, sleep=waits.append)
    assert waits == [30, 30]
    assert is_rate_limit(Exception("HTTP 429"))
    assert not is_rate_limit(ConnectionError("network down"))


def test_empty_prices_are_a_failure(tmp_path):
    fake = FakeTicker("BAD.NS", prices=pd.DataFrame())
    result = ingest_prices("BAD.NS", fake, 5, tmp_path)
    assert result.status == "failed"
    assert EmptyResponseError.__name__ in result.error


# --- whole run -------------------------------------------------------------

class TinyUniverse:
    tickers = ["GOOD.NS", "BAD.NS", "ALSOGOOD.NS"]
    price_history_years = 5

    class benchmark:
        ticker = "^IDX"


def test_one_failed_ticker_does_not_stop_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ingest_run.yf, "Ticker", lambda t: FakeTicker(t, fail=(t == "BAD.NS"))
    )
    results, manifest_path = ingest_run.run_ingestion(TinyUniverse, raw_dir=tmp_path)

    by_ticker = {}
    for r in results:
        by_ticker.setdefault(r.ticker, []).append(r.status)
    # companies: prices + metadata + 6 statements = 8 datasets; benchmark: prices only
    assert by_ticker["GOOD.NS"] == ["ok"] * 8
    assert by_ticker["ALSOGOOD.NS"] == ["ok"] * 8
    assert by_ticker["BAD.NS"] == ["failed"] * 8
    assert by_ticker["^IDX"] == ["ok"]

    manifest = json.loads(manifest_path.read_text())
    assert manifest["status_counts"] == {"ok": 17, "failed": 8}
    assert len(manifest["results"]) == 25
    assert all(isinstance(IngestResult(**r), IngestResult) for r in manifest["results"])


def test_unknown_ticker_argument_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="not in config/universe.yaml"):
        ingest_run.run_ingestion(TinyUniverse, tickers=["NOPE.NS"], raw_dir=tmp_path)


# --- risk-free rate --------------------------------------------------------

def test_daily_risk_free_rate():
    rate = RiskFreeRate(value=0.052599, instrument="91-day T-bill", source="RBI",
                        as_of_date="2026-09-02")
    # (1 + 0.052599)^(1/252) - 1: ln(1.052599) = 0.0512624; / 252 = 0.00020342;
    # exp(0.00020342) - 1 = 0.00020344
    assert rate.daily(252) == pytest.approx(0.00020344, abs=1e-8)
    assert RiskFreeRate(value=None).daily() is None
