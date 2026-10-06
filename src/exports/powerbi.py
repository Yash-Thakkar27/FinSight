"""Power BI export: the mart star schema as CSV files.

The star schema is defined as views in the `mart` schema (sql/schema.sql). This
module writes each view to data/exports/powerbi/{view}.csv and checks that the
files form a valid star schema before they are handed to Power BI.

Power BI Desktop runs on Windows only. The report (.pbix) is built by the user
from these files, following docs/powerbi_model.md; no .pbix is produced here.
"""

import logging
from pathlib import Path

import pandas as pd
from sqlalchemy import Connection, text

log = logging.getLogger(__name__)

# view -> columns that identify a row (the grain)
DIMENSIONS = {
    "dim_sector": ["sector_key"],
    "dim_company": ["company_key"],
    "dim_date": ["date_key"],
}
FACTS = {
    "fact_market_prices": ["company_key", "date_key"],
    "fact_financials": ["company_key", "date_key", "period_type", "statement", "line_item"],
    "fact_metrics": ["company_key", "date_key", "period_type", "metric_name"],
    "fact_valuation": ["company_key", "date_key", "period_end_date_key", "valuation_basis"],
}
# (table, foreign-key column) -> (dimension, key column)
RELATIONSHIPS = {
    ("dim_company", "sector_key"): ("dim_sector", "sector_key"),
    **{(fact, "company_key"): ("dim_company", "company_key") for fact in FACTS},
    **{(fact, "date_key"): ("dim_date", "date_key") for fact in FACTS},
    ("fact_valuation", "period_end_date_key"): ("dim_date", "date_key"),
}
ORDER = {**DIMENSIONS, **FACTS}


def read_view(conn: Connection, view: str) -> pd.DataFrame:
    if view not in ORDER:
        raise ValueError(f"unknown mart view: {view}")
    return pd.read_sql(text(f"SELECT * FROM mart.{view} ORDER BY {', '.join(ORDER[view])}"),
                       conn)


def validate_star_schema(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Problems that would break the Power BI model. An empty list means the schema is sound.

    Checks: every table has its key columns; each table's grain is unique; no key is null;
    and every foreign key in a fact (or in dim_company) exists in its dimension.
    """
    problems = []
    for name, grain in ORDER.items():
        if name not in tables:
            problems.append(f"{name}: missing")
            continue
        table = tables[name]
        absent = [c for c in grain if c not in table.columns]
        if absent:
            problems.append(f"{name}: missing key column(s) {absent}")
            continue
        if table[grain].isna().any().any():
            problems.append(f"{name}: null in key column(s) {grain}")
        duplicates = int(table.duplicated(subset=grain).sum())
        if duplicates:
            problems.append(f"{name}: {duplicates} duplicate row(s) at grain {grain}")
    for (table, column), (dimension, key) in RELATIONSHIPS.items():
        if table not in tables or dimension not in tables:
            continue
        if column not in tables[table].columns or key not in tables[dimension].columns:
            continue
        orphans = ~tables[table][column].isin(tables[dimension][key])
        if orphans.any():
            problems.append(f"{table}.{column}: {int(orphans.sum())} value(s) not in "
                            f"{dimension}.{key}")
    return problems


def export_powerbi(conn: Connection, out_dir: Path) -> dict:
    """Write one CSV per mart view. Raises if the result is not a valid star schema."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {view: read_view(conn, view) for view in ORDER}
    problems = validate_star_schema(tables)
    if problems:
        raise ValueError("Power BI export is not a valid star schema: " + "; ".join(problems))
    rows = {}
    for view, table in tables.items():
        table.to_csv(out_dir / f"{view}.csv", index=False, date_format="%Y-%m-%d")
        rows[view] = len(table)
    return rows
