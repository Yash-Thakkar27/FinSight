# Architecture

How FinSight is put together and why. Formulas and rules are in `methodology.md`; table and
column definitions are in `data_dictionary.md`.

## 1. Data flow

```mermaid
flowchart LR
    Y[(Yahoo Finance<br/>via yfinance)] -->|1 fetch| ING[src/ingestion]
    ING -->|2 save unchanged| RAW[(data/raw<br/>immutable snapshots<br/>+ run manifest)]
    RAW -->|3 clean| CLN[src/cleaning]
    CLN -->|4 validate| VAL[src/validation]
    CLN --> STG[(staging schema)]
    VAL --> DQ[(data_quality_logs<br/>data_quality_summary)]
    STG -->|5 upsert| CORE[(core schema)]
    CORE -->|6 compute| ANA[src/analytics]
    ANA --> MET[(metrics, peer_comparisons,<br/>anomalies, correlations)]
    MET -->|7 export| EXP[src/exports]
    EXP --> XLS[Excel workbooks]
    EXP --> PBI[Power BI CSVs<br/>mart views]
    CORE --> ML[src/ml<br/>scripts/run_ml.py]
    MET --> ML
    ML --> MLR[(ml_* tables<br/>model cards)]
    CORE --> APP[Streamlit app]
    MET --> APP
    MLR --> APP
    DQ --> APP
```

`python scripts/update_data.py` runs steps 1–8 (step 8 writes the `pipeline_runs` row).
`--skip-fetch` starts at step 3, rebuilding everything from the raw snapshots on disk with no
network access. `python scripts/run_ml.py` runs the Data Science Lab separately, so refreshing data
never silently retrains a model. The app only reads.

## 2. Layers and their rules

| Layer | Where | Rule |
|---|---|---|
| Configuration | `config/universe.yaml`, `config/field_map.yaml`, `config/settings.py`, `.env` | The universe, benchmark, risk-free rate, field mapping and thresholds are defined here and nowhere else. |
| Raw | `data/raw/{source}/{dataset}/{ticker}/{timestamp}` | Written once, never modified (read-only files). Metadata is stored inside each file. |
| Cleaning | `src/cleaning/` | Idempotent. Missing values are NULL with a reason, never 0. Reported values are kept beside INR values. |
| Validation | `src/validation/checks.py` | Small functions returning structured results; nothing is deleted. |
| Staging | `staging` schema | Truncated and reloaded each run, keyed by ticker, no foreign keys. |
| Core | `core` schema | Normalized, constrained, upserted on natural keys. Derived tables are rebuilt in full. |
| Analytics | `src/analytics/` | Pure functions registered with a formula id, unit and applicable sector types. |
| Data Science Lab | `src/ml/` | Walk-forward validation, baselines first, fixed seed, results stored. |
| Mart | `mart` schema (views) | Star schema for Power BI. |
| Presentation | `app/`, `src/exports/` | Read-only over PostgreSQL. One formatting module (`src/formatting.py`). |

## 3. Modules

```mermaid
flowchart TB
    subgraph scripts
        U[update_data.py] --> P[src/pipeline.py]
        I[init_db.py] --> SETUP[src/database/setup.py]
        R[run_ml.py] --> MLRUN[src/ml/run.py]
    end
    P --> ING[ingestion: market_data, financial_data,<br/>company_metadata, raw_store, run]
    P --> CLN[cleaning: pipeline, fiscal]
    P --> VAL[validation: checks]
    P --> LOAD[database: load, queries, models]
    P --> CMP[analytics: compute, peer_analytics]
    P --> EXP[exports: excel, powerbi, run]
    CMP --> REG[analytics: registry]
    REG --- RAT[ratios] & VALN[valuation] & RET[returns] & RSK[risk]
    CMP --> SH[shares] & COMPS[comps] & AD[anomaly_detection]
    MLRUN --> VOL[volatility] & CLU[clustering] & ST[stats_tests] & RG[regimes] & AC[anomaly_comparison]
    VOL --> FE[features] & SP[splits] & EV[evaluation]
    MLRUN --> MC[model_cards]
    APP[app/Home.py + pages] --> Q[database/queries.py]
    APP --> COMPS
    EXP --> Q
```

## 4. Core entities

```mermaid
erDiagram
    sectors ||--o{ industries : has
    sectors ||--o{ companies : classifies
    industries ||--o{ companies : classifies
    data_sources ||--o{ market_prices : provides
    data_sources ||--o{ financial_statements : provides
    companies ||--o{ market_prices : "daily"
    companies ||--o{ financial_statements : "per line item, per period"
    companies ||--o{ shares_outstanding : has
    companies ||--o{ stock_splits : has
    companies ||--o{ metrics : "computed"
    companies ||--o{ peer_comparisons : "as target"
    companies ||--o{ anomalies : flagged
    companies ||--o{ ml_forecasts : forecast
    formulas ||--o{ metrics : defines
    formulas ||--o{ financial_statements : "derived fields"
    pipeline_runs ||--o{ data_quality_logs : produces
    pipeline_runs ||--|| data_quality_summary : summarizes
    ml_runs ||--o{ ml_metrics : produces
    ml_runs ||--o{ ml_forecasts : produces

    companies {
        int company_id PK
        text ticker UK
        text entity_type "company or index"
        int sector_id FK
        text peer_group
        text sector_type "non_financial, bank, nbfc, insurance"
    }
    market_prices {
        int company_id FK
        date date
        numeric adj_close
        bigint volume
        bool is_stale_quote
    }
    financial_statements {
        int company_id FK
        text statement
        text line_item
        date period_end_date
        text period_type
        numeric value "INR"
        numeric original_value "as reported"
        text missing_reason
        numeric fx_rate
    }
    metrics {
        int company_id FK
        text metric_name
        date period_end_date
        text period_type
        float value
        text na_reason
        text method
        text formula_id FK
        bool is_translated
    }
    formulas {
        text formula_id PK
        text expression
        text_array applicable_sector_types
    }
```

Natural keys: `market_prices (company_id, date)`;
`financial_statements (company_id, statement, line_item, period_end_date, period_type)`;
`metrics (company_id, metric_name, period_end_date, period_type)`.

## 5. Star schema (Power BI)

```mermaid
erDiagram
    dim_sector ||--o{ dim_company : ""
    dim_company ||--o{ fact_market_prices : ""
    dim_company ||--o{ fact_financials : ""
    dim_company ||--o{ fact_metrics : ""
    dim_company ||--o{ fact_valuation : ""
    dim_date ||--o{ fact_market_prices : ""
    dim_date ||--o{ fact_financials : ""
    dim_date ||--o{ fact_metrics : ""
    dim_date ||--o{ fact_valuation : ""
```

`agg_return_correlation` is a helper table outside the star schema (company × company). See
`powerbi_model.md`.

## 6. Design decisions

| Decision | Reason |
|---|---|
| Raw snapshots are immutable and everything downstream is rebuilt from them | Every stored number can be traced to a file the source returned, and the project runs offline and reproducibly. |
| Statements and metrics are in long format | Line items and applicable metrics differ by sector type; a wide table would be mostly empty for banks. |
| "Value XOR reason" is a database constraint | A missing financial value can never be mistaken for zero, and the reason is always available to display. |
| Sector applicability is data (`formulas.applicable_sector_types`), enforced when metrics are computed | A bank can never be shown an EBITDA margin, whichever screen or export reads the table. |
| Reported values are stored beside INR values | Ratios and growth use the reporting currency; levels and valuation use INR; nothing is overwritten. |
| Derived tables (`metrics`, `peer_comparisons`, `anomalies`, `correlations`, `ml_*`) are rebuilt in full | They are pure functions of the core data, so a rebuild is reproducible and nothing goes stale. |
| The pipeline's upsert and the app share one set of functions for comps | The stored default-peer results and a custom peer set chosen in the app cannot disagree. |
| The Data Science Lab is a separate command and the app never trains | Results shown are the stored results of a recorded run (seed, data snapshot id). |
| Model cards are generated by the run | A card cannot quote a number the run did not produce. |
| Tests run offline on hand-built fixtures; database tests use a separate `finsight_test` database | No test depends on the network or touches project data. |
