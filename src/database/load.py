"""Load cleaned data: DataFrames -> staging tables -> upsert into core.

Staging is truncated and reloaded on every run. Core tables are upserted on
their natural keys, so running the load twice leaves the same rows.
"""

import logging

import pandas as pd
from sqlalchemy import Connection, Engine, insert, text

from src.cleaning.pipeline import DERIVED_FORMULAS, CleanedData
from src.database import models
from src.validation.checks import CheckResult

log = logging.getLogger(__name__)

STAGING_TABLES = {
    "prices": models.StagingMarketPrice,
    "statements": models.StagingFinancialStatement,
    "metadata": models.StagingCompanyMetadata,
    "source_metrics": models.StagingSourceReportedMetric,
    "fx": models.StagingFxRate,
}


def to_records(df: pd.DataFrame) -> list[dict]:
    """Rows as plain Python dicts, with every missing value (NaN, NaT, pd.NA) as None."""
    return df.astype(object).where(df.notna(), None).to_dict("records")


# ------------------------------------------------------------------ runs ---

def start_run(engine: Engine) -> int:
    """Insert the pipeline_runs row in its own transaction, so a crash still leaves a record."""
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO core.pipeline_runs (status) VALUES ('running') RETURNING run_id")
        ).scalar_one()


def finish_run(engine: Engine, run_id: int, status: str, records_processed: int | None,
               notes: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""
                UPDATE core.pipeline_runs
                SET finished_at = now(), status = :status,
                    records_processed = :records, notes = :notes
                WHERE run_id = :run_id
            """),
            {"status": status, "records": records_processed, "notes": notes, "run_id": run_id},
        )


# --------------------------------------------------------------- staging ---

def load_staging(conn: Connection, cleaned: CleanedData) -> dict[str, int]:
    """Replace the contents of every staging table with this run's cleaned rows."""
    counts = {}
    for attribute, model in STAGING_TABLES.items():
        table = model.__table__
        frame = getattr(cleaned, attribute)
        conn.execute(text(f"TRUNCATE TABLE {table.schema}.{table.name}"))
        columns = [c.name for c in table.columns]
        records = to_records(frame[columns]) if len(frame) else []
        if records:
            conn.execute(insert(table), records)
        counts[f"{table.schema}.{table.name}"] = len(records)
    return counts


# ------------------------------------------------------------------ core ---

def seed_derived_formulas(conn: Connection) -> None:
    """Register the cleaning pipeline's derivation formulas (financial_statements.formula_id)."""
    for line_item, formula in DERIVED_FORMULAS.items():
        conn.execute(
            text("""
                INSERT INTO core.formulas
                    (formula_id, metric_name, expression, description, unit,
                     applicable_sector_types)
                VALUES (:formula_id, :metric_name, :expression, :description, 'INR', :sectors)
                ON CONFLICT (formula_id) DO UPDATE SET
                    metric_name = EXCLUDED.metric_name, expression = EXCLUDED.expression,
                    description = EXCLUDED.description, unit = EXCLUDED.unit,
                    applicable_sector_types = EXCLUDED.applicable_sector_types
            """),
            {
                "formula_id": formula["formula_id"], "metric_name": line_item,
                "expression": formula["expression"], "description": formula["description"],
                "sectors": ["non_financial", "bank", "nbfc", "insurance"]
                           if line_item == "free_cash_flow" else ["non_financial"],
            },
        )


UPSERTS = {
    "core.companies (metadata)": """
        UPDATE core.companies c
        SET exchange = s.exchange,
            country = COALESCE(s.country, c.country),
            updated_at = now()
        FROM staging.company_metadata s
        WHERE s.ticker = c.ticker
          AND (c.exchange IS DISTINCT FROM s.exchange
               OR c.country IS DISTINCT FROM COALESCE(s.country, c.country))
    """,
    "core.shares_outstanding": """
        INSERT INTO core.shares_outstanding
            (company_id, as_of_date, shares_outstanding, source_id, retrieved_at)
        SELECT c.company_id, s.shares_as_of_date, s.shares_outstanding, ds.source_id,
               s.retrieved_at
        FROM staging.company_metadata s
        JOIN core.companies c ON c.ticker = s.ticker
        JOIN core.data_sources ds ON ds.name = s.source
        WHERE s.shares_outstanding > 0 AND s.shares_as_of_date IS NOT NULL
        ON CONFLICT (company_id, as_of_date) DO UPDATE SET
            shares_outstanding = EXCLUDED.shares_outstanding,
            source_id = EXCLUDED.source_id,
            retrieved_at = EXCLUDED.retrieved_at
    """,
    "core.source_reported_metrics": """
        INSERT INTO core.source_reported_metrics
            (company_id, metric_name, value, as_of_date, source_id, retrieved_at)
        SELECT c.company_id, s.metric_name, s.value, s.as_of_date, ds.source_id, s.retrieved_at
        FROM staging.source_reported_metrics s
        JOIN core.companies c ON c.ticker = s.ticker
        JOIN core.data_sources ds ON ds.name = s.source
        ON CONFLICT (company_id, metric_name, as_of_date) DO UPDATE SET
            value = EXCLUDED.value,
            source_id = EXCLUDED.source_id,
            retrieved_at = EXCLUDED.retrieved_at
    """,
    "core.fx_rates": """
        INSERT INTO core.fx_rates (currency, date, rate, source_id, retrieved_at)
        SELECT s.currency, s.date, s.rate, ds.source_id, s.retrieved_at
        FROM staging.fx_rates s
        JOIN core.data_sources ds ON ds.name = s.source
        ON CONFLICT (currency, date) DO UPDATE SET
            rate = EXCLUDED.rate,
            source_id = EXCLUDED.source_id,
            retrieved_at = EXCLUDED.retrieved_at
    """,
    "core.market_prices": """
        INSERT INTO core.market_prices
            (company_id, date, open, high, low, close, adj_close, volume, is_stale_quote,
             source_id, retrieved_at)
        SELECT c.company_id, s.date, s.open, s.high, s.low, s.close, s.adj_close, s.volume,
               s.is_stale_quote, ds.source_id, s.retrieved_at
        FROM staging.market_prices s
        JOIN core.companies c ON c.ticker = s.ticker
        JOIN core.data_sources ds ON ds.name = s.source
        ON CONFLICT (company_id, date) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, adj_close = EXCLUDED.adj_close, volume = EXCLUDED.volume,
            is_stale_quote = EXCLUDED.is_stale_quote,
            source_id = EXCLUDED.source_id, retrieved_at = EXCLUDED.retrieved_at
    """,
    "core.financial_statements": """
        INSERT INTO core.financial_statements
            (company_id, statement, fiscal_year, fiscal_quarter, period_end_date, period_type,
             line_item, value, missing_reason, currency, unit, original_currency, fx_rate,
             is_calculated, formula_id, source_id, retrieved_at)
        SELECT c.company_id, s.statement, s.fiscal_year, s.fiscal_quarter, s.period_end_date,
               s.period_type, s.line_item, s.value, s.missing_reason, s.currency, s.unit,
               s.original_currency, s.fx_rate, s.is_calculated, s.formula_id, ds.source_id,
               s.retrieved_at
        FROM staging.financial_statements s
        JOIN core.companies c ON c.ticker = s.ticker
        JOIN core.data_sources ds ON ds.name = s.source
        ON CONFLICT (company_id, statement, line_item, period_end_date, period_type)
        DO UPDATE SET
            fiscal_year = EXCLUDED.fiscal_year, fiscal_quarter = EXCLUDED.fiscal_quarter,
            value = EXCLUDED.value, missing_reason = EXCLUDED.missing_reason,
            currency = EXCLUDED.currency, unit = EXCLUDED.unit,
            original_currency = EXCLUDED.original_currency, fx_rate = EXCLUDED.fx_rate,
            is_calculated = EXCLUDED.is_calculated, formula_id = EXCLUDED.formula_id,
            source_id = EXCLUDED.source_id, retrieved_at = EXCLUDED.retrieved_at
    """,
}


# Within the date range the latest snapshot covers, core.market_prices mirrors the
# cleaned rows: a row that cleaning rejected (or the source withdrew) must not
# survive from an earlier run. History older than the snapshot is left alone.
REMOVE_SUPERSEDED_PRICES = """
    DELETE FROM core.market_prices p
    USING core.companies c,
          (SELECT ticker, min(date) AS first_date FROM staging.market_prices GROUP BY ticker) w
    WHERE c.company_id = p.company_id
      AND w.ticker = c.ticker
      AND p.date >= w.first_date
      AND NOT EXISTS (SELECT 1 FROM staging.market_prices s
                      WHERE s.ticker = c.ticker AND s.date = p.date)
"""


def upsert_core(conn: Connection) -> dict[str, int]:
    """Upsert staging into core. Returns rows written per target."""
    seed_derived_formulas(conn)
    written = {target: conn.execute(text(sql)).rowcount for target, sql in UPSERTS.items()}
    written["core.market_prices (superseded rows removed)"] = conn.execute(
        text(REMOVE_SUPERSEDED_PRICES)
    ).rowcount
    return written


def unmatched_staging_tickers(conn: Connection) -> list[str]:
    """Tickers in staging with no row in core.companies (they would be silently skipped)."""
    return list(conn.execute(text("""
        SELECT DISTINCT s.ticker FROM (
            SELECT ticker FROM staging.market_prices
            UNION SELECT ticker FROM staging.financial_statements
            UNION SELECT ticker FROM staging.company_metadata
        ) s
        LEFT JOIN core.companies c ON c.ticker = s.ticker
        WHERE c.company_id IS NULL
        ORDER BY 1
    """)).scalars())


# --------------------------------------------------------------- quality ---

def write_quality(conn: Connection, run_id: int, results: list[CheckResult],
                  summary: dict) -> None:
    records = [
        {"run_id": run_id, "check_name": r.check_name, "category": r.category,
         "severity": r.severity, "status": r.status, "message": r.message,
         "ticker": r.ticker, "dataset": r.dataset, "record_key": r.record_key}
        for r in results
    ]
    if records:
        conn.execute(insert(models.DataQualityLog.__table__), records)
    conn.execute(insert(models.DataQualitySummary.__table__),
                 [{"run_id": run_id, **summary}])
