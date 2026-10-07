"""Generate docs/data_dictionary.md from the live database, the field map and the metric registry.

    python scripts/build_data_dictionary.py

Column lists come from PostgreSQL's catalog, canonical line items from config/field_map.yaml
and metric definitions from the registry, so the dictionary describes what actually exists.
Only the one-line purpose of each table is written by hand (below).
"""

import sys
from pathlib import Path

import yaml
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import CONFIG_DIR, DOCS_DIR  # noqa: E402
from src.analytics.registry import REGISTRY  # noqa: E402
from src.database.connection import get_engine  # noqa: E402

PURPOSE = {
    "core.companies": "One row per company, plus the benchmark index (entity_type = 'index').",
    "core.sectors": "Sector lookup. Labels come from config/universe.yaml, not from the source.",
    "core.industries": "Industry lookup, each belonging to a sector.",
    "core.data_sources": "Where data comes from.",
    "core.shares_outstanding": "Latest share count from source metadata, with its as-of date.",
    "core.stock_splits": "Splits and bonus issues (ratio = new shares per old share).",
    "core.market_prices": "Daily prices. Placeholder rows on market holidays are flagged.",
    "core.fx_rates": "Daily FX rates (INR per unit) used to translate foreign-currency statements.",
    "core.financial_statements": "Statements in long format: one row per line item per period. "
                                 "value is INR; original_value is the figure as reported.",
    "core.formulas": "Every metric's formula, unit and applicable sector types.",
    "core.metrics": "Computed metrics in long format: ratios, growth, valuation, returns, risk. "
                    "A row has a value or an na_reason.",
    "core.source_reported_metrics": "Multiples the source reports itself; used only to reconcile.",
    "core.valuation_reconciliation": "Calculated vs source-reported valuation figures.",
    "core.peer_comparisons": "Default-peer-group comparison for each company and metric.",
    "core.anomalies": "Statistical outliers flagged for review "
                      "('Potential data anomaly detected').",
    "core.correlations": "Pairwise correlations of daily returns for three windows.",
    "core.pipeline_runs": "One row per run of scripts/update_data.py.",
    "core.data_quality_logs": "One row per validation check result per run.",
    "core.data_quality_summary": "Run-level quality summary, computed from the logs.",
    "core.ml_runs": "One row per Data Science Lab task: seed, data snapshot id, parameters, "
                    "small result tables.",
    "core.ml_metrics": "Forecast-evaluation metrics: pooled, per fold and per company.",
    "core.ml_forecasts": "Out-of-sample volatility forecasts and realized volatility (annualized).",
    "core.ml_cluster_assignments": "Cluster of each company under both clustering methods.",
    "core.ml_stat_tests": "Statistical test results with p-values, effect sizes and intervals.",
    "core.ml_regimes": "Daily market-regime probabilities (descriptive).",
    "core.ml_anomaly_flags": "Company-days flagged by any of three anomaly methods.",
    "staging.company_metadata": "Cleaned source metadata, as loaded.",
    "staging.market_prices": "Cleaned prices, as loaded, keyed by ticker.",
    "staging.financial_statements": "Cleaned statements, as loaded, with source label and file.",
    "staging.fx_rates": "Cleaned FX rates, as loaded.",
    "staging.stock_splits": "Splits read from the price history, as loaded.",
    "staging.source_reported_metrics": "Source-reported multiples, as loaded.",
    "mart.dim_company": "Power BI dimension: companies and the index.",
    "mart.dim_sector": "Power BI dimension: sectors, plus 'Benchmark index'.",
    "mart.dim_date": "Power BI dimension: every calendar day, with fiscal year and quarter.",
    "mart.fact_market_prices": "Power BI fact: daily prices with daily return.",
    "mart.fact_financials": "Power BI fact: statement line items.",
    "mart.fact_metrics": "Power BI fact: ratios, growth, return and risk metrics.",
    "mart.fact_valuation": "Power BI fact: valuation, one row per company and valuation date.",
    "mart.agg_return_correlation": "Power BI helper table (outside the star schema): "
                                   "pairwise return correlations.",
}

COLUMNS = """
    SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.is_nullable,
           t.table_type
    FROM information_schema.columns c
    JOIN information_schema.tables t USING (table_schema, table_name)
    WHERE c.table_schema IN ('core', 'staging', 'mart')
    ORDER BY CASE c.table_schema WHEN 'core' THEN 0 WHEN 'staging' THEN 1 ELSE 2 END,
             c.table_name, c.ordinal_position
"""
KEYS = """
    SELECT tc.table_schema, tc.table_name, tc.constraint_type, kcu.column_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu USING (constraint_schema, constraint_name)
    WHERE tc.table_schema IN ('core', 'staging')
      AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
"""


def main() -> None:
    with get_engine().connect() as conn:
        columns = conn.execute(text(COLUMNS)).all()
        keys = conn.execute(text(KEYS)).all()
    flags = {}
    for schema, table, kind, column in keys:
        flags.setdefault((schema, table, column), set()).add(
            {"PRIMARY KEY": "PK", "UNIQUE": "UQ", "FOREIGN KEY": "FK"}[kind])

    out = ["# Data dictionary", "",
           "Generated by `python scripts/build_data_dictionary.py` from the live database, "
           "`config/field_map.yaml` and the metric registry. Do not edit by hand.", "",
           "Conventions: monetary values are **absolute INR** in the database (divide by 10^7 "
           "for crore); a missing value is NULL with a reason, never 0; dates are ISO; "
           "timestamps are UTC. Schemas: `staging` (cleaned rows as loaded) → `core` "
           "(normalized, constrained) → `mart` (Power BI star schema, views).", "",
           "Key: PK = primary key, UQ = part of a unique constraint, FK = foreign key.", ""]

    current = None
    for schema, table, column, data_type, nullable, table_type in columns:
        name = f"{schema}.{table}"
        if name != current:
            current = name
            kind = "view" if table_type == "VIEW" else "table"
            out += ["", f"### `{name}` ({kind})", "", PURPOSE.get(name, ""), "",
                    "| Column | Type | Nullable | Key |", "|---|---|---|---|"]
        key = ", ".join(sorted(flags.get((schema, table, column), [])))
        out.append(f"| `{column}` | {data_type} | {'yes' if nullable == 'YES' else 'no'} | {key} |")
    undocumented = sorted({f"{s}.{t}" for s, t, *_ in columns} - set(PURPOSE))
    if undocumented:
        raise SystemExit(f"add a purpose line for: {undocumented}")

    field_map = yaml.safe_load((CONFIG_DIR / "field_map.yaml").read_text(encoding="utf-8"))
    out += ["", "## Canonical statement line items", "",
            "`financial_statements.line_item` values, and the source labels each is read from "
            "(the first label with a value is used). \"Applies to\" lists the sector types the "
            "item is meaningful for; elsewhere it is stored as NULL with reason `not_applicable`.",
            "", "| Statement | Line item | Unit | Source label(s) | Applies to |",
            "|---|---|---|---|---|"]
    for statement in ("income", "balance", "cashflow"):
        for item, spec in field_map[statement].items():
            note = " (sign flipped: stored as a positive outflow)" if spec.get("sign") == -1 else ""
            out.append(f"| {statement} | `{item}` | {spec['unit']} | "
                       f"{', '.join(spec['sources'])}{note} | "
                       f"{', '.join(spec.get('applies_to', ['all']))} |")

    out += ["", "## Metrics", "",
            "`metrics.metric_name` values. Units: `pct` is a fraction (0.184 = 18.4%), "
            "`multiple` is turns (x), `ratio` is a plain number, `INR` is absolute rupees, "
            "`per_share` is in the reporting currency. Fundamental metrics are computed in the "
            "company's reporting currency; valuation is in INR.", "",
            "| Metric | Kind | Unit | Formula | Applies to | Formula id |",
            "|---|---|---|---|---|---|"]
    order = {"fundamental": 0, "valuation": 1, "market": 2}
    for spec in sorted(REGISTRY.values(), key=lambda s: (order[s.kind], s.name)):
        applies = "all" if len(spec.applicable) == 4 else ", ".join(spec.applicable)
        out.append(f"| `{spec.name}` | {spec.kind} | {spec.unit} | {spec.expression} | {applies} "
                   f"| {spec.formula_id} |")

    out += ["", "## Coded values", "",
            "| Column | Values |", "|---|---|",
            "| `companies.sector_type` | non_financial, bank, nbfc, insurance |",
            "| `companies.entity_type` | company, index |",
            "| `financial_statements.statement` | income, balance, cashflow |",
            "| `financial_statements.period_type` | annual, quarterly |",
            "| `financial_statements.missing_reason` | unavailable_from_source, not_applicable, "
            "failed_retrieval |",
            "| `financial_statements.fx_rate_type` | average (flows), period_end (balances) |",
            "| `metrics.period_type` | annual, quarterly, ttm (current valuation), "
            "point_in_time (returns and risk) |",
            "| `metrics.method` | average_balance, closing_balance (ROE, ROA); "
            "weighted_average_diluted, weighted_average_basic, period_end (EPS); ttm, "
            "latest_annual (current multiples) |",
            "| `data_quality_logs.severity` | error, warning, info |",
            "| `data_quality_logs.status` | pass, fail |",
            "| `pipeline_runs.status` | running, success, partial, failed |",
            "| `anomalies.method` | iqr, modified_zscore |",
            "| `correlations.window_label` | 1y, 3y, full |",
            "| `fact_valuation.valuation_basis` | current, fiscal_year_end |", ""]
    path = DOCS_DIR / "data_dictionary.md"
    path.write_text("\n".join(out), encoding="utf-8")
    tables = len({(s, t) for s, t, *_ in columns})
    print(f"Wrote {path}: {tables} tables and views, {len(columns)} columns, "
          f"{len(REGISTRY)} metrics")


if __name__ == "__main__":
    main()
