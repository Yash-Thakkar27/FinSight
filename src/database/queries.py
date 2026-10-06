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
