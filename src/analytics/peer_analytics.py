"""Compute and store the Phase 5 outputs: peer comparisons, anomalies, correlations.

Reads core.metrics, statements and prices; writes core.peer_comparisons,
core.anomalies and core.correlations. Each table is rebuilt in full on every
run, like core.metrics: all three are pure functions of the core data.
"""

import logging

import pandas as pd
from sqlalchemy import Connection, insert, text

from config.settings import Universe
from src.analytics import anomaly_detection, comps, risk
from src.analytics.compute import clean_number
from src.database import models, queries

log = logging.getLogger(__name__)

# (label, years); None = the whole aligned history
CORRELATION_WINDOWS = (("1y", 1), ("3y", 3), ("full", None))


def aligned_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily adjusted-close returns, one column per ticker, on dates every ticker traded.

    Placeholder rows are dropped before returns are taken. The inner join means
    every pair in a correlation matrix is measured on exactly the same dates.
    """
    traded = prices[~prices["is_stale_quote"]]
    wide = traded.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    wide.index = pd.to_datetime(wide.index)
    return wide.pct_change().dropna(how="any")


def correlation_rows(prices: pd.DataFrame, windows=CORRELATION_WINDOWS) -> list[dict]:
    """Pairwise correlations for each window, both orderings and the diagonal."""
    returns = aligned_returns(prices)
    if returns.empty:
        return []
    rows = []
    for label, years in windows:
        window = returns
        if years is not None:
            start = returns.index[-1] - pd.DateOffset(years=years)
            if returns.index[0] > start:
                continue                  # history does not cover the window
            window = returns.loc[returns.index > start]
        matrix = risk.correlation_matrix(window)
        for a in matrix.index:
            for b in matrix.columns:
                rows.append({"ticker_a": a, "ticker_b": b, "window_label": label,
                             "correlation": clean_number(matrix.at[a, b]),
                             "n_observations": int(len(window)),
                             "start_date": window.index[0].date(),
                             "end_date": window.index[-1].date()})
    return rows


def peer_comparison_rows(metrics: pd.DataFrame, companies: pd.DataFrame) -> list[dict]:
    """Default-peer-group comparison for every company in the universe."""
    rows = []
    for ticker in companies.loc[companies["entity_type"] == "company", "ticker"]:
        result = comps.compare(metrics, companies, ticker)
        for warning in result["warnings"]:
            log.warning("Comps %s: %s", ticker, warning)
        for row in result["rows"]:
            rows.append({k: (clean_number(v) if isinstance(v, float) else v)
                         for k, v in row.items() if k != "peer_values"})
    return rows


def recompute_peer_analytics(conn: Connection, universe: Universe) -> dict:
    """Rebuild peer comparisons, anomalies and correlations. Returns counts for the log."""
    companies = queries.load_companies(conn)
    metrics = queries.load_metrics(conn)
    statements = queries.load_statements(conn)
    prices = queries.load_prices(conn)
    company_id = dict(zip(companies["ticker"], companies["company_id"]))
    settings = universe.anomalies

    comparisons = peer_comparison_rows(metrics, companies)
    peer_group = dict(zip(companies["ticker"], companies["peer_group"]))
    changes = anomaly_detection.sector_adjust(
        anomaly_detection.fundamental_changes(statements), peer_group,
        settings.min_peer_group_size)
    anomalies = pd.concat([
        anomaly_detection.detect_market_anomalies(
            prices, settings.rolling_window, settings.rolling_min_periods,
            settings.market_iqr_multiplier, settings.modified_zscore_threshold),
        anomaly_detection.detect_fundamental_anomalies(
            changes, settings.iqr_multiplier, settings.modified_zscore_threshold),
    ], ignore_index=True)
    correlations = correlation_rows(prices)

    for table in ("peer_comparisons", "anomalies", "correlations"):
        conn.execute(text(f"DELETE FROM core.{table}"))
    if comparisons:
        conn.execute(insert(models.PeerComparison.__table__), [
            {"company_id": int(company_id[r["ticker"]]),
             **{k: v for k, v in r.items() if k != "ticker"}} for r in comparisons
        ])
    if len(anomalies):
        records = anomalies.astype(object).where(anomalies.notna(), None).to_dict("records")
        conn.execute(insert(models.Anomaly.__table__), [
            {"company_id": int(company_id[r["ticker"]]),
             **{k: v for k, v in r.items() if k != "ticker"}} for r in records
        ])
    if correlations:
        conn.execute(insert(models.Correlation.__table__), [
            {"company_id_a": int(company_id[r["ticker_a"]]),
             "company_id_b": int(company_id[r["ticker_b"]]),
             **{k: v for k, v in r.items() if k not in ("ticker_a", "ticker_b")}}
            for r in correlations
        ])

    return {
        "comparisons": len(comparisons),
        "interpretations": sum(1 for r in comparisons if r["interpretation"]),
        "anomalies": len(anomalies),
        "anomalies_by_dataset": anomalies.groupby(["dataset", "method"]).size().to_dict(),
        "correlations": len(correlations),
        "correlation_windows": sorted({r["window_label"] for r in correlations}),
    }
