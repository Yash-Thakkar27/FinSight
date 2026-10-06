"""Power BI export: the mart star schema as CSV files.

The star schema is defined as views in the `mart` schema (sql/schema.sql). This
module writes each view to data/exports/powerbi/{view}.csv and checks that the
files form a valid star schema before they are handed to Power BI. One helper table,
agg_return_correlation, is exported alongside the star schema but is not part of it.

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

# Helper tables exported alongside the star schema but not part of it. The correlation
# table is company x company: both keys refer to dim_company, with no active relationship.
HELPERS = {"agg_return_correlation": ["company_a_key", "company_b_key", "window_label"]}
HELPER_KEYS = {("agg_return_correlation", "company_a_key"): ("dim_company", "company_key"),
               ("agg_return_correlation", "company_b_key"): ("dim_company", "company_key")}
EXPORTED = {**ORDER, **HELPERS}


def read_view(conn: Connection, view: str) -> pd.DataFrame:
    if view not in EXPORTED:
        raise ValueError(f"unknown mart view: {view}")
    return pd.read_sql(text(f"SELECT * FROM mart.{view} ORDER BY {', '.join(EXPORTED[view])}"),
                       conn)


def validate_helpers(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Problems in the helper tables: a non-unique grain, or a key with no row in its dimension."""
    problems = []
    for name, grain in HELPERS.items():
        if name not in tables:
            problems.append(f"{name}: missing")
            continue
        duplicates = int(tables[name].duplicated(subset=grain).sum())
        if duplicates:
            problems.append(f"{name}: {duplicates} duplicate row(s) at grain {grain}")
    for (table, column), (dimension, key) in HELPER_KEYS.items():
        if table in tables and dimension in tables:
            orphans = ~tables[table][column].isin(tables[dimension][key])
            if orphans.any():
                problems.append(f"{table}.{column}: {int(orphans.sum())} value(s) not in "
                                f"{dimension}.{key}")
    return problems


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
    tables = {view: read_view(conn, view) for view in EXPORTED}
    problems = validate_star_schema(tables) + validate_helpers(tables)
    if problems:
        raise ValueError("Power BI export is not a valid star schema: " + "; ".join(problems))
    rows = {}
    for view, table in tables.items():
        table.to_csv(out_dir / f"{view}.csv", index=False, date_format="%Y-%m-%d")
        rows[view] = len(table)
    return rows
