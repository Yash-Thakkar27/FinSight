"""Data Science Lab results are stored in PostgreSQL and replaced on rerun.

Uses the `finsight_test` database (never the project database) with synthetic prices.
Skipped when PostgreSQL is not reachable.
"""

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from config.settings import get_database_settings
from src.database.setup import initialize
from src.ml import run as ml_run
from src.ml import volatility
from tests.test_integration import TEST_DATABASE, UNIVERSE
from tests.test_ml import synthetic_prices

pytestmark = pytest.mark.integration

TICKERS = {"AAA": "INRCO.NS", "BBB": "USDCO.NS", "CCC": "BANK.NS", "^IDX": "^IDX"}


@pytest.fixture(scope="module")
def engine():
    settings = get_database_settings()
    try:
        admin = create_engine(settings.url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                {"n": TEST_DATABASE}).scalar():
                conn.execute(text(f"CREATE DATABASE {TEST_DATABASE}"))
        admin.dispose()
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable: {type(exc).__name__}")
    test_engine = create_engine(settings.url.rsplit("/", 1)[0] + f"/{TEST_DATABASE}")
    prices = synthetic_prices()
    prices["ticker"] = prices["ticker"].map(TICKERS)
    with test_engine.begin() as conn:
        initialize(conn, UNIVERSE, reset=True)
        company_id = dict(conn.execute(text("SELECT ticker, company_id FROM core.companies")).all())
        rows = [{"company_id": company_id[r.ticker], "date": r.date, "open": r.adj_close,
                 "high": r.high, "low": r.low, "close": r.adj_close, "adj_close": r.adj_close,
                 "volume": int(r.volume)} for r in prices.itertuples()]
        conn.execute(text("""
            INSERT INTO core.market_prices
                (company_id, date, open, high, low, close, adj_close, volume, source_id,
                 retrieved_at)
            VALUES (:company_id, :date, :open, :high, :low, :close, :adj_close, :volume,
                    (SELECT source_id FROM core.data_sources LIMIT 1), now())"""), rows)
    yield test_engine
    test_engine.dispose()


def counts(engine) -> dict:
    tables = ("ml_runs", "ml_metrics", "ml_forecasts", "ml_cluster_assignments", "ml_stat_tests",
              "ml_regimes", "ml_anomaly_flags")
    with engine.connect() as conn:
        return {t: conn.execute(text(f"SELECT count(*) FROM core.{t}")).scalar() for t in tables}


def test_lab_results_are_stored_and_replaced_on_rerun(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(volatility, "INITIAL_TRAIN", 280)
    monkeypatch.setattr(volatility, "TEST_SIZE", 50)
    monkeypatch.setattr(volatility, "GBM_PARAMS", {"max_depth": 2, "learning_rate": 0.1,
                                                   "max_iter": 30, "min_samples_leaf": 20})

    def run():
        with engine.begin() as conn:
            return ml_run.run_all(conn, UNIVERSE, results_dir=tmp_path / "results",
                                  cards_dir=tmp_path / "cards")

    results = run()
    first = counts(engine)
    assert first["ml_runs"] == 5                               # one row per task
    forecasts = results["volatility"]["forecasts"]
    assert first["ml_forecasts"] == len(forecasts) > 300
    assert first["ml_metrics"] == len(results["volatility"]["metrics"])
    assert first["ml_cluster_assignments"] == 3
    assert first["ml_regimes"] > 400 and first["ml_stat_tests"] > 5

    with engine.connect() as conn:
        # forecasts are stored as annualized volatility: sqrt(daily variance * 252)
        row = forecasts.iloc[0]
        stored = conn.execute(text("""
            SELECT f.realized, f.ridge, f.ewma FROM core.ml_forecasts f
            JOIN core.companies c USING (company_id)
            WHERE c.ticker = :t AND f.date = :d AND f.horizon = :h"""),
            {"t": row["ticker"], "d": row["date"].date(), "h": int(row["horizon"])}).one()
        assert stored.realized == pytest.approx((row["target"] * 252) ** 0.5)
        assert stored.ridge == pytest.approx((row["ridge"] * 252) ** 0.5)
        # the stored comparison table is the one the experiment returned
        run_row = conn.execute(text("""
            SELECT seed, data_snapshot_id, params, results FROM core.ml_runs
            WHERE task = 'volatility'""")).one()
        assert run_row.seed == 42 and len(run_row.data_snapshot_id) == 12
        assert len(run_row.results["comparison"]) == len(results["volatility"]["comparison"])
        assert run_row.params["features"] == results["volatility"]["params"]["features"]
        assert conn.execute(text("""SELECT count(*) FROM core.ml_anomaly_flags
                                    WHERE message NOT LIKE 'Potential data anomaly detected%'""")
                            ).scalar() == 0
        # with no fundamentals in this database, clustering falls back to return features only
        clustering = conn.execute(text(
            "SELECT params FROM core.ml_runs WHERE task = 'clustering'")).scalar()
        assert clustering["features"] == ["volatility", "beta", "market_correlation"]
        assert set(clustering["features_unavailable"]) == {"net_margin", "roe", "revenue_growth"}

    # result files and model cards are written
    assert (tmp_path / "results" / "volatility" / "forecasts.parquet").exists()
    assert (tmp_path / "results" / "clustering" / "summary.json").exists()
    cards = sorted(p.name for p in (tmp_path / "cards").glob("*.md"))
    assert cards == ["anomaly_isolation_forest.md", "peer_clustering.md", "regime_detection.md",
                     "statistical_tests.md", "volatility_forecasting.md"]
    for card in (tmp_path / "cards").glob("*.md"):
        body = card.read_text()
        for heading in ("## Purpose", "## Data", "## Limitations", "must not be used for"):
            assert heading in body, f"{card.name} lacks {heading}"
        assert " nan" not in body.lower() and "none%" not in body.lower(), card.name

    # a rerun replaces the stored results instead of adding to them, with identical metrics
    def metric_fingerprint():
        with engine.connect() as conn:
            return pd.read_sql(text("""SELECT scope, horizon, model, metric, fold, ticker, value
                                       FROM core.ml_metrics ORDER BY 1, 2, 3, 4, 5, 6"""), conn)

    before = metric_fingerprint()
    run()
    assert counts(engine) == first
    pd.testing.assert_frame_equal(before, metric_fingerprint())
