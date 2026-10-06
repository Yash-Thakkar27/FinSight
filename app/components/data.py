"""Cached data access for the app.

Every function reads from PostgreSQL through src/database/queries.py. The app
never calls an external API and never trains a model.

Caching: results are cached with a time-to-live. Each loader also takes the
current data version (the latest pipeline run and Data Science Lab run), so a
refresh changes the cache key and the app picks up new data immediately
instead of waiting for the TTL.
"""

import pandas as pd
import streamlit as st

from src.database import queries
from src.database.connection import get_engine

TTL_SECONDS = 600


def _query(function, *args, **kwargs) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return function(conn, *args, **kwargs)


@st.cache_data(ttl=30, show_spinner=False)
def data_version() -> tuple[int, int]:
    """(latest pipeline run, latest lab run). Checked often; everything else is keyed on it."""
    row = _query(queries.load_data_version).iloc[0]
    return int(row["pipeline_run"]), int(row["ml_run"])


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def companies(version) -> pd.DataFrame:
    return _query(queries.load_companies)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def latest_run(version) -> pd.Series | None:
    frame = _query(queries.load_latest_run)
    return None if frame.empty else frame.iloc[0]


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def data_sources(version) -> pd.DataFrame:
    return _query(queries.load_data_sources)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def latest_price_date(version):
    return _query(queries.load_latest_price_date).iloc[0]["latest"]


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def all_metrics(version) -> pd.DataFrame:
    return _query(queries.load_metrics)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def company_metrics(version, ticker: str) -> pd.DataFrame:
    return _query(queries.load_company_metrics, ticker)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def company_profile(version, ticker: str) -> pd.Series:
    return _query(queries.load_company_profile, ticker).iloc[0]


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def company_statements(version, ticker: str, period_type: str) -> pd.DataFrame:
    return _query(queries.load_company_statements, ticker, period_type)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def price_history(version, tickers: tuple, start, end) -> pd.DataFrame:
    return _query(queries.load_price_history, list(tickers), start, end)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def peer_comparisons(version, ticker: str) -> pd.DataFrame:
    return _query(queries.load_peer_comparisons, ticker)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def correlation_matrix(version, window: str) -> pd.DataFrame:
    return _query(queries.load_correlation_matrix, window)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def formulas(version) -> pd.DataFrame:
    return _query(queries.load_formulas)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def quality_logs(version, run_id: int, status: str | None = None) -> pd.DataFrame:
    return _query(queries.load_quality_logs, run_id, status)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def completeness(version) -> pd.DataFrame:
    return _query(queries.load_completeness)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def anomalies(version, dataset: str) -> pd.DataFrame:
    return _query(queries.load_anomalies, dataset)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def ml_run(version, task: str) -> pd.Series | None:
    frame = _query(queries.load_ml_run, task)
    return None if frame.empty else frame.iloc[0]


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def ml_metrics(version, scope: str) -> pd.DataFrame:
    return _query(queries.load_ml_metrics, scope)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def ml_forecasts(version, ticker: str, horizon: int) -> pd.DataFrame:
    return _query(queries.load_ml_forecasts, ticker, horizon)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def ml_clusters(version) -> pd.DataFrame:
    return _query(queries.load_ml_clusters)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def ml_stat_tests(version, test: str) -> pd.DataFrame:
    return _query(queries.load_ml_stat_tests, test)


@st.cache_data(ttl=TTL_SECONDS, show_spinner=False)
def ml_regimes(version) -> pd.DataFrame:
    return _query(queries.load_ml_regimes)
