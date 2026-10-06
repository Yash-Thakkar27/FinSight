# Methodology

How FinSight turns raw source data into stored, validated figures. This document grows with each
phase; sections 1–4 cover cleaning, validation and loading (Phase 3). Metric formulas are added in
Phase 4.

## 1. Data flow

```text
yfinance -> data/raw (immutable snapshots) -> cleaning -> validation
         -> staging schema (truncate + reload) -> core schema (upsert on natural keys)
```

`python scripts/update_data.py --skip-fetch` rebuilds everything from the raw snapshots on disk,
with no network access. The same raw files always give the same database contents.

## 2. Cleaning conventions (`src/cleaning/pipeline.py`)

### 2.1 Units, currency and signs

| Convention | Rule |
|---|---|
| Storage unit | Absolute INR in the database (never crore or lakh). Display units are applied in the app. |
| Per-share values | `INR_per_share`. Share counts have unit `shares`. |
| Capex | Stored as a **positive outflow**. The source reports it negative; it is multiplied by −1. |
| Debt | Stored positive, as reported. |
| Fiscal year | April–March. `FY2025` is the year ending 31-Mar-2025. A period ending in April–December belongs to the next FY label. |
| Fiscal quarter | Q1 = Apr–Jun, Q2 = Jul–Sep, Q3 = Oct–Dec, Q4 = Jan–Mar. |
| Price dates | Timezone-naive local trading dates. The source stamps rows at midnight Asia/Kolkata. |

### 2.2 Field mapping

Source labels are mapped to canonical line items by `config/field_map.yaml`. For each canonical
field the first listed source label with a non-null value is used, and the label used is kept in
`staging.financial_statements.source_label`.

### 2.3 Missing values

A financial value is never filled with 0. A missing value is NULL with one of three reasons, and the
database enforces "value XOR reason" with a CHECK constraint:

| Reason | Meaning |
|---|---|
| `not_applicable` | The field is not meaningful for the company's sector type (gross profit, EBITDA, EBIT, current assets and liabilities for a bank; loans and deposits for a non-financial). The value is NULL **even if the source returns a number**. |
| `failed_retrieval` | The dataset could not be fetched (per the ingestion manifest) and no earlier snapshot exists. |
| `unavailable_from_source` | The source returned the statement but not this field, or returned no such statement. |

A period is "known" for a company if any of its three statements has at least one value for that
period end. Every canonical field gets a row for every known period, so a statement the source
did not return (quarterly cash flow, for most companies) still appears, marked missing.
Period columns that the source returns entirely empty are skipped: they carry no information.

### 2.4 Foreign-currency statements

Yahoo serves Infosys statements in USD. The statement currency is an explicit per-company setting in
`config/universe.yaml` (`statement_currency`), because Yahoo's own `financialCurrency` flag is
unreliable: it says USD for HCLTech, whose statements are in INR.

Conversion to INR uses the daily USD/INR close (`INR=X`):

| Item type | Rate |
|---|---|
| Income statement and cash flow (flows), including EPS | Mean of daily rates over the fiscal period |
| Balance sheet (stocks) | Last rate on or before the period end (at most 7 days old) |
| Share counts | Not converted |

The reported value, the original currency and the rate used are stored (`original_value`,
`original_currency`, `fx_rate`). If the rate series does not span the whole period, the value is
stored as NULL and reported as an error rather than converted at a partial average.
These are FinSight's translations, not the company's own reported INR figures.

### 2.5 Duplicates and multiple snapshots

| Dataset | Rule |
|---|---|
| Prices | Natural key (company, date). Only the **latest** snapshot is used, because the source re-bases the entire adjusted-close history after a dividend or split, and mixing snapshots would mix bases. |
| Statements | Natural key (company, statement, line item, period end, period type). **All** snapshots are combined and the latest retrieval wins, so history accumulates as the source drops old periods. |

**Incomplete price rows.** If the latest snapshot returns a row with a missing field (observed on
2026-10-07: the last trading day came back without a close for every ticker), the row is taken
from the most recent earlier snapshot that has it complete, provided both snapshots agree on
adjusted close for the last date before it (same adjustment basis, relative tolerance 1e-6). The
repaired row keeps the `retrieved_at` and `raw_file` of the snapshot it came from. A row that
cannot be repaired is rejected: it is logged as an error and not loaded. Within the date range the
latest snapshot covers, `core.market_prices` mirrors the cleaned rows, so a rejected row cannot
survive from an earlier run.

### 2.6 Derived fields

When a field is missing but derivable, it is calculated, flagged `is_calculated = true` and given
a `formula_id` registered in `core.formulas`. A field is only derived when every input is present,
and never where it is `not_applicable`.

| Field | Formula | formula_id |
|---|---|---|
| Free cash flow | operating cash flow − capex | `DERIVED_FCF` |
| EBITDA | EBIT + depreciation and amortization | `DERIVED_EBITDA` |

In the current data the source provides both wherever their inputs exist, so no row is derived.

### 2.7 Unusual values

Nothing is deleted for being unusual. Two kinds of row are flagged:

- **Placeholder price rows** (`is_stale_quote`): zero volume with open, high, low and close all
  equal to the previous close. The source emits these on exchange holidays. They are kept in the
  database and **excluded from return and risk calculations**, since each would add an artificial
  zero return.
- **Large moves**: an absolute daily return above 20% is logged for review with the wording
  "Potential data anomaly detected".

## 3. Validation (`src/validation/checks.py`)

Each check is a small function returning structured results (check name, category, severity,
status, message, ticker, dataset, record key). A check logs one `fail` row per offending record and
one `pass` row per ticker with none. Severity: `error` = the record is wrong; `warning` = review;
`info` = neutral observation. Thresholds are in `config/settings.py` (`ValidationThresholds`).

| Category | Check | Severity | Rule |
|---|---|---|---|
| Market data | `high_is_highest` | error | High ≥ max(Open, Close, Low) |
| | `low_is_lowest` | error | Low ≤ min(Open, Close) |
| | `volume_non_negative` | error | Volume ≥ 0 |
| | `prices_positive` | error | Open, high, low, close, adjusted close present and > 0 |
| | `no_duplicate_prices` | warning | No repeated company + date (checked before de-duplication) |
| | `price_gap` | warning | More than 5 weekdays missing between consecutive rows |
| | `extreme_daily_return` | warning | Absolute daily adjusted-close return > 20% |
| | `stale_quote` | info | Placeholder row (section 2.7) |
| Financials | `assets_non_negative`, `revenue_non_negative` | error | ≥ 0 |
| | `shares_positive` | error | > 0 |
| | `capex_is_positive_outflow` | warning | Capex ≥ 0 after sign normalization |
| | `balance_sheet_identity` | warning | Assets vs liabilities + equity (incl. minority interest) within 2% |
| | `*_margin_in_range` | warning | Gross, EBITDA, EBIT, net margin within [−100%, 100%] |
| Completeness | `field_completeness` | warning | Share of **applicable** fields present per company and period; fails below 80% |
| | `incomplete_price_row` / `price_row_repaired` | error / warning | Section 2.5 |
| | `fx_rate_missing`, `snapshot_missing` | error | A value could not be converted; a ticker has no raw data |
| Consistency | `no_conflicting_values` | error / info | Different values for one period within a retrieval (error); revised between retrievals (info) |
| | `statement_currency_scale` | error | Latest annual EPS ÷ source trailing EPS within [0.2, 5]. A wrong currency is off by about the exchange rate. |
| Freshness | `price_freshness` | warning | Latest price ≥ last weekday − 5 days |

### Run summary

Written to `core.data_quality_summary`; every figure is computed from the run.

| Figure | Definition |
|---|---|
| Total records | Cleaned price, statement, metadata and FX rows, plus rejected price rows |
| Invalid records | Distinct records with at least one **error**-severity failure |
| Valid records | Total − invalid |
| Pass rate | Valid ÷ total |
| Warnings | Number of warning-severity failures |
| Missing % | Applicable statement fields with no value ÷ applicable fields (`not_applicable` is outside the denominator) |
| Duplicates | Rows removed by de-duplication |

## 4. Loading (`src/database/load.py`)

Everything in step 5 runs in one transaction. Staging tables are truncated and reloaded, then core
tables are upserted on their natural keys (`INSERT ... ON CONFLICT DO UPDATE`), which makes the
refresh idempotent. `core.pipeline_runs`, `core.data_quality_logs` and `core.data_quality_summary`
gain rows on every run by design: they are the run history.
