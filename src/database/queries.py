"""Read queries. Every query is parameterized SQL; nothing is built by string formatting.

NUMERIC columns are cast to float8 in SQL so pandas gets floats, not Decimals.
"""

import pandas as pd
from sqlalchemy import Connection, text


def read(conn: Connection, sql: str, **params) -> pd.DataFrame:
    return pd.read_sql(text(sql), conn, params=params)


COMPANIES = """
    SELECT c.company_id, c.ticker, c.company_name, c.entity_type, c.sector_type, c.peer_group,
           s.sector_name, i.industry_name
    FROM core.companies c
    LEFT JOIN core.sectors s USING (sector_id)
    LEFT JOIN core.industries i USING (industry_id)
    ORDER BY c.company_id
"""

STATEMENTS = """
    SELECT c.ticker, c.sector_type, f.statement, f.line_item, f.period_end_date, f.period_type,
           f.fiscal_year, f.fiscal_quarter,
           f.value::float8          AS value,
           f.original_value::float8 AS original_value,
           f.original_currency, f.unit, f.missing_reason
    FROM core.financial_statements f
    JOIN core.companies c USING (company_id)
    ORDER BY c.ticker, f.period_type, f.period_end_date, f.statement, f.line_item
"""

PRICES = """
    SELECT c.ticker, p.date, p.close::float8 AS close, p.adj_close::float8 AS adj_close,
           p.volume, p.is_stale_quote
    FROM core.market_prices p
    JOIN core.companies c USING (company_id)
    ORDER BY c.ticker, p.date
"""

PRICES_OHLC = """
    SELECT c.ticker, p.date, p.adj_close::float8 AS adj_close, p.high::float8 AS high,
           p.low::float8 AS low, p.close::float8 AS close, p.volume, p.is_stale_quote
    FROM core.market_prices p
    JOIN core.companies c USING (company_id)
    ORDER BY c.ticker, p.date
"""

LATEST_SHARES = """
    SELECT DISTINCT ON (c.ticker)
           c.ticker, s.shares_outstanding::float8 AS shares_outstanding, s.as_of_date
    FROM core.shares_outstanding s
    JOIN core.companies c USING (company_id)
    ORDER BY c.ticker, s.as_of_date DESC
"""

STOCK_SPLITS = """
    SELECT c.ticker, s.date, s.split_ratio
    FROM core.stock_splits s
    JOIN core.companies c USING (company_id)
    ORDER BY c.ticker, s.date
"""

LATEST_SOURCE_METRICS = """
    SELECT DISTINCT ON (c.ticker, m.metric_name)
           c.ticker, m.metric_name, m.value, m.as_of_date
    FROM core.source_reported_metrics m
    JOIN core.companies c USING (company_id)
    ORDER BY c.ticker, m.metric_name, m.as_of_date DESC
"""

METRICS = """
    SELECT c.ticker, c.sector_type, m.metric_name, m.period_type, m.period_end_date,
           m.value, m.na_reason, m.method, m.unit, m.formula_id, m.as_of_date, m.input_fields,
           m.reporting_currency, m.is_translated
    FROM core.metrics m
    JOIN core.companies c USING (company_id)
    WHERE (CAST(:metric_name AS text) IS NULL OR m.metric_name = :metric_name)
      AND (CAST(:period_type AS text) IS NULL OR m.period_type = :period_type)
    ORDER BY c.ticker, m.metric_name, m.period_end_date
"""


def load_companies(conn: Connection) -> pd.DataFrame:
    return read(conn, COMPANIES)


def load_statements(conn: Connection) -> pd.DataFrame:
    return read(conn, STATEMENTS)


def load_prices(conn: Connection) -> pd.DataFrame:
    return read(conn, PRICES)


def load_prices_ohlc(conn: Connection) -> pd.DataFrame:
    return read(conn, PRICES_OHLC)


def load_latest_shares(conn: Connection) -> pd.DataFrame:
    return read(conn, LATEST_SHARES)


def load_stock_splits(conn: Connection) -> pd.DataFrame:
    return read(conn, STOCK_SPLITS)


def load_latest_source_metrics(conn: Connection) -> pd.DataFrame:
    return read(conn, LATEST_SOURCE_METRICS)


def load_metrics(conn: Connection, metric_name: str | None = None,
                 period_type: str | None = None) -> pd.DataFrame:
    return read(conn, METRICS, metric_name=metric_name, period_type=period_type)


# ---------------------------------------------------------------------------
# App queries. The Streamlit app reads only through these functions.
# ---------------------------------------------------------------------------

LATEST_RUN = """
    SELECT r.run_id, r.started_at, r.finished_at, r.status, r.records_processed, r.notes,
           q.total_records, q.valid_records, q.invalid_records, q.warning_count, q.missing_pct,
           q.duplicate_records, q.pass_rate_pct
    FROM core.pipeline_runs r
    LEFT JOIN core.data_quality_summary q USING (run_id)
    WHERE r.status <> 'running'
    ORDER BY r.run_id DESC
    LIMIT 1
"""

DATA_VERSION = """
    SELECT COALESCE((SELECT max(run_id) FROM core.pipeline_runs), 0) AS pipeline_run,
           COALESCE((SELECT max(ml_run_id) FROM core.ml_runs), 0)    AS ml_run
"""

DATA_SOURCES = "SELECT name, url, notes FROM core.data_sources ORDER BY source_id"

LATEST_PRICE_DATE = "SELECT max(date) AS latest FROM core.market_prices WHERE NOT is_stale_quote"

PRICE_HISTORY = """
    SELECT c.ticker, p.date, p.close::float8 AS close, p.adj_close::float8 AS adj_close,
           p.volume, p.is_stale_quote
    FROM core.market_prices p
    JOIN core.companies c USING (company_id)
    WHERE c.ticker = ANY(:tickers) AND p.date BETWEEN :start AND :end
    ORDER BY c.ticker, p.date
"""

COMPANY_STATEMENTS = """
    SELECT f.statement, f.line_item, f.period_end_date, f.period_type, f.fiscal_year,
           f.fiscal_quarter, f.value::float8 AS value, f.original_value::float8 AS original_value,
           f.original_currency, f.fx_rate::float8 AS fx_rate, f.fx_rate_type, f.unit,
           f.missing_reason, f.is_calculated
    FROM core.financial_statements f
    JOIN core.companies c USING (company_id)
    WHERE c.ticker = :ticker AND f.period_type = :period_type
    ORDER BY f.period_end_date, f.statement, f.line_item
"""

COMPANY_METRICS = """
    SELECT m.metric_name, m.period_type, m.period_end_date, m.value, m.na_reason, m.method,
           m.unit, m.formula_id, m.as_of_date, m.input_fields, m.reporting_currency,
           m.is_translated
    FROM core.metrics m
    JOIN core.companies c USING (company_id)
    WHERE c.ticker = :ticker
    ORDER BY m.metric_name, m.period_end_date
"""

COMPANY_PROFILE = """
    SELECT c.ticker, c.company_name, c.exchange, c.country, c.currency, c.sector_type,
           c.peer_group, s.sector_name, i.industry_name,
           sh.shares_outstanding::float8 AS shares_outstanding, sh.as_of_date AS shares_as_of
    FROM core.companies c
    LEFT JOIN core.sectors s USING (sector_id)
    LEFT JOIN core.industries i USING (industry_id)
    LEFT JOIN LATERAL (SELECT shares_outstanding, as_of_date FROM core.shares_outstanding
                       WHERE company_id = c.company_id ORDER BY as_of_date DESC LIMIT 1) sh ON TRUE
    WHERE c.ticker = :ticker
"""

PEER_COMPARISONS = """
    SELECT c.ticker, p.*
    FROM core.peer_comparisons p
    JOIN core.companies c USING (company_id)
    WHERE c.ticker = :ticker
"""

CORRELATION_MATRIX = """
    SELECT a.ticker AS ticker_a, b.ticker AS ticker_b, x.correlation, x.n_observations,
           x.start_date, x.end_date
    FROM core.correlations x
    JOIN core.companies a ON a.company_id = x.company_id_a
    JOIN core.companies b ON b.company_id = x.company_id_b
    WHERE x.window_label = :window
"""

FORMULAS = """
    SELECT formula_id, metric_name, expression, description, unit, applicable_sector_types
    FROM core.formulas ORDER BY metric_name
"""

QUALITY_LOGS = """
    SELECT check_name, category, severity, status, message, ticker, dataset, record_key
    FROM core.data_quality_logs
    WHERE run_id = :run_id
      AND (CAST(:status AS text) IS NULL OR status = :status)
    ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
             check_name, ticker, record_key
"""

COMPLETENESS = """
    SELECT c.ticker, f.period_type,
           count(f.value)                                     AS present,
           count(*)                                           AS expected,
           100.0 * count(f.value) / count(*)                  AS completeness_pct,
           count(DISTINCT f.period_end_date)                  AS periods
    FROM core.financial_statements f
    JOIN core.companies c USING (company_id)
    WHERE f.missing_reason IS DISTINCT FROM 'not_applicable'
    GROUP BY c.ticker, f.period_type
    ORDER BY c.ticker, f.period_type
"""

ANOMALIES = """
    SELECT c.ticker, a.date, a.dataset, a.variable, a.method, a.value, a.peer_group_median,
           a.adjusted_value, a.score, a.message
    FROM core.anomalies a
    JOIN core.companies c USING (company_id)
    WHERE a.dataset = :dataset
    ORDER BY a.date DESC, c.ticker
"""

ML_RUN = """
    SELECT task, created_at, seed, data_snapshot_id, params, results
    FROM core.ml_runs WHERE task = :task
"""

ML_METRICS = """
    SELECT scope, horizon, model, metric, fold, ticker, value, n
    FROM core.ml_metrics
    WHERE scope = :scope
    ORDER BY horizon, metric, model, fold, ticker
"""

ML_FORECASTS = """
    SELECT f.date, f.horizon, f.fold, f.realized, f.hist_21, f.ewma, f.garch, f.ridge, f.gbm
    FROM core.ml_forecasts f
    JOIN core.companies c USING (company_id)
    WHERE c.ticker = :ticker AND f.horizon = :horizon
    ORDER BY f.date
"""

ML_CLUSTERS = """
    SELECT c.ticker, c.company_name, a.sector, a.kmeans_cluster, a.hierarchical_cluster,
           a.pc1, a.pc2
    FROM core.ml_cluster_assignments a
    JOIN core.companies c USING (company_id)
    ORDER BY a.sector, c.ticker
"""

ML_STAT_TESTS = """
    SELECT test, subject, statistic, p_value, p_adjusted, effect_size, ci_low, ci_high, n,
           details
    FROM core.ml_stat_tests
    WHERE test = :test
    ORDER BY subject
"""

ML_REGIMES = "SELECT date, p_turbulent, regime, threshold_regime FROM core.ml_regimes ORDER BY date"


def load_latest_run(conn: Connection) -> pd.DataFrame:
    return read(conn, LATEST_RUN)


def load_data_version(conn: Connection) -> pd.DataFrame:
    return read(conn, DATA_VERSION)


def load_data_sources(conn: Connection) -> pd.DataFrame:
    return read(conn, DATA_SOURCES)


def load_latest_price_date(conn: Connection) -> pd.DataFrame:
    return read(conn, LATEST_PRICE_DATE)


def load_price_history(conn: Connection, tickers: list[str], start, end) -> pd.DataFrame:
    return read(conn, PRICE_HISTORY, tickers=list(tickers), start=start, end=end)


def load_company_statements(conn: Connection, ticker: str, period_type: str) -> pd.DataFrame:
    return read(conn, COMPANY_STATEMENTS, ticker=ticker, period_type=period_type)


def load_company_metrics(conn: Connection, ticker: str) -> pd.DataFrame:
    return read(conn, COMPANY_METRICS, ticker=ticker)


def load_company_profile(conn: Connection, ticker: str) -> pd.DataFrame:
    return read(conn, COMPANY_PROFILE, ticker=ticker)


def load_peer_comparisons(conn: Connection, ticker: str) -> pd.DataFrame:
    return read(conn, PEER_COMPARISONS, ticker=ticker)


def load_correlation_matrix(conn: Connection, window: str) -> pd.DataFrame:
    return read(conn, CORRELATION_MATRIX, window=window)


def load_formulas(conn: Connection) -> pd.DataFrame:
    return read(conn, FORMULAS)


def load_quality_logs(conn: Connection, run_id: int, status: str | None = None) -> pd.DataFrame:
    return read(conn, QUALITY_LOGS, run_id=run_id, status=status)


def load_completeness(conn: Connection) -> pd.DataFrame:
    return read(conn, COMPLETENESS)


def load_anomalies(conn: Connection, dataset: str) -> pd.DataFrame:
    return read(conn, ANOMALIES, dataset=dataset)


def load_ml_run(conn: Connection, task: str) -> pd.DataFrame:
    return read(conn, ML_RUN, task=task)


def load_ml_metrics(conn: Connection, scope: str) -> pd.DataFrame:
    return read(conn, ML_METRICS, scope=scope)


def load_ml_forecasts(conn: Connection, ticker: str, horizon: int) -> pd.DataFrame:
    return read(conn, ML_FORECASTS, ticker=ticker, horizon=horizon)


def load_ml_clusters(conn: Connection) -> pd.DataFrame:
    return read(conn, ML_CLUSTERS)


def load_ml_stat_tests(conn: Connection, test: str) -> pd.DataFrame:
    return read(conn, ML_STAT_TESTS, test=test)


def load_ml_regimes(conn: Connection) -> pd.DataFrame:
    return read(conn, ML_REGIMES)
