# MASTER PROMPT — Build FinSight
### Financial Markets & Comparable Companies Analytics Platform (Indian Equities)

---

## 0. Your Role and How You Must Work

You are a senior data scientist with strong data-engineering and finance-domain skills. You are building a portfolio project for a candidate applying to a **Data Science internship**, most likely in financial services. Finance is the domain; **data science is the skill being demonstrated**.

The project must show the full data-science workflow:
- Reliable data pipelines
- Exploratory analysis
- Statistical reasoning
- Feature engineering
- Leakage-free modelling against honest baselines
- Rigorous evaluation
- Clear communication of uncertainty

Interviewers will probe every number and every modelling choice, so prioritize, in this order:

1. **Correctness and data integrity**
2. **Methodological rigour** (baselines, proper validation, no leakage, uncertainty quantified)
3. **Analytical depth and interpretation**
4. **Clean, testable, reproducible code**
5. **Professional presentation**

Flashy UI is the lowest priority.

A modest model evaluated honestly beats an impressive-looking model evaluated badly. **Never** frame any model as predicting stock prices for trading. If a model fails to beat its baseline, report that plainly. It is a legitimate and valuable finding.

### Operating rules (non-negotiable)

- **Work phase by phase** (Section 17). Complete one phase, run it, show me the evidence it works (commands run, output, row counts, test results), then **stop and wait for my approval** before starting the next phase.
- **Inspect before you design.** Before writing the schema, pull real data for 2–3 tickers and print exactly which fields, periods and units the source returns. Design the schema around what actually exists.
- **Never fabricate data.** That means no fake records, no hard-coded results, no invented accuracy figures, and no placeholder numbers made to look real. If a value can't be retrieved or validly calculated, store NULL and display "N/A" with the reason.
- **Never claim something works without running it.** "Done" means executed successfully, not merely written.
- **Ask before making significant assumptions.** For small ones, choose the conservative option and record it in `docs/assumptions.md`.
- **Prefer simple, explicit code** over clever abstractions. Every module should be explainable in an interview.
- **No investment advice.** Present objective comparisons only (Section 11).

---

## 1. Objective

Build an end-to-end pipeline that turns public financial and market data for Indian listed companies into validated, auditable analytics:

```text
Public sources → Ingestion → Raw snapshot (immutable) → Cleaning → Validation
→ PostgreSQL (staging → core) → Ratio & valuation engine → Comps / Market / Risk analytics
→ Streamlit app  +  Excel exports  +  Power BI-ready star schema
```

---

## 2. Scope Tiers

**Tier 1 — Must have** (the project fails without these):
- Configurable universe
- Ingestion with raw snapshots
- Cleaning and validation, with a logged data-quality report
- PostgreSQL schema
- Ratio engine with sector applicability rules
- Comparable companies with peer statistics
- Returns and risk metrics
- Streamlit app (all 7 pages)
- Refresh script
- Unit tests
- README and methodology docs

**Tier 1 also includes the Data Science Lab** (Section 10.8): EDA notebook, volatility forecasting with walk-forward evaluation, peer clustering, and statistical-testing modules.

**Tier 2 — Should have:**
- Anomaly detection (statistical vs ML comparison)
- Correlation analysis with significance testing
- Regime detection
- Notebooks
- Excel exports
- Power BI star-schema exports and model documentation

**Tier 3 — Optional:**
- Financial health score
- Experiment tracking (MLflow)
- Docker Compose
- Benchmark-relative metrics (beta, tracking error)

Complete each tier fully before starting the next.

---

## 3. Tech Stack

- **Core:** Python 3.11+, pandas, NumPy, SQLAlchemy 2.x, psycopg, Pydantic (config and record schemas), PostgreSQL 15+
- **Data:** yfinance as the primary free source (NSE tickers use the `.NS` suffix). Use requests/httpx only if another documented public source is needed.
- **Data science:** scikit-learn, SciPy, statsmodels, `arch` (GARCH), optionally LightGBM. Fix random seeds everywhere.
- **App:** Streamlit, Plotly
- **Exports:** openpyxl (Excel), CSV (Power BI)
- **Quality:** pytest, ruff, Python `logging`
- **Config:** `.env` and `.env.example`, `pyproject.toml` or `requirements.txt` with pinned versions
- **Dev environment:** the developer uses **macOS (MacBook Air)**. Every setup instruction must work on macOS first, with Windows notes second. Run PostgreSQL through Docker or Homebrew, and document both.

---

## 4. Company Universe (config-driven)

Create `config/universe.yaml`. Nothing about the universe may be hard-coded elsewhere. Each entry has:

```yaml
- ticker: TCS.NS
  name: Tata Consultancy Services
  sector: Information Technology
  industry: IT Services
  peer_group: it_services        # comps are drawn from the same peer_group by default
  sector_type: non_financial     # non_financial | bank | nbfc | insurance
```

Starting universe of about 17 companies across 5 sectors:

| Sector | Companies |
|---|---|
| IT | TCS, Infosys, HCLTech, Wipro |
| Banks | HDFC Bank, ICICI Bank, Axis Bank, Kotak Mahindra Bank |
| Pharma | Sun Pharma, Cipla, Dr. Reddy's |
| Auto | Tata Motors (see note), Mahindra & Mahindra, Maruti Suzuki |
| Consumer | Hindustan Unilever, ITC, Asian Paints |

**Verify every ticker before use.** Fetch it, confirm it returns current data, and confirm the company name. Corporate actions change tickers. For example, Tata Motors demerged into separate passenger-vehicle and commercial-vehicle entities, so confirm the correct current ticker and note the history limitation in `docs/limitations.md`.

Also configure:
- **Benchmark:** Nifty 50 (`^NSEI`)
- **Risk-free rate:** a documented Indian rate (for example, the 91-day T-bill yield or the 10-year G-sec yield). Store it as a config value with its source and as-of date. Don't fetch it silently.

---

## 5. Indian Market Conventions

- **Fiscal year:** April–March. Label periods as `FY2025` (year ending 31-Mar-2025) and store the actual `period_end_date`. Map quarters to fiscal quarters (Q1 = Apr–Jun).
- **Currency:** INR. Store all monetary values in **INR, absolute units** in the database.
- **Display units:** configurable, with **₹ crore** as the default (1 crore = 10^7). Example display formats: `₹1,24,530 Cr`, `18.42%`, `24.7x`, `₹1,245.30`. Use one formatting module for everything.
- **Trading days:** annualize with 252 trading days (documented assumption).

---

## 6. Ingestion (`src/ingestion/`)

- `company_metadata.py`: name, ticker, exchange, sector, industry, country, currency, shares outstanding (with as-of date)
- `market_data.py`: daily OHLC, adjusted close, volume; at least 5 years where available. Include the benchmark.
- `financial_data.py`: annual and quarterly statements, covering:
  - **Income statement:** revenue, gross profit, EBITDA, EBIT, net income, EPS, interest expense
  - **Balance sheet:** total assets, total liabilities, total equity, total debt, cash and equivalents, current assets, current liabilities, minority interest
  - **Cash flow:** operating cash flow, capex, free cash flow
  - **Bank-specific fields where available:** net interest income, total deposits, total loans

**Requirements:**
- Save every response **unchanged** to `data/raw/{source}/{dataset}/{ticker}/{retrieved_at}.parquet` (or JSON), with metadata: source, retrieved_at (UTC), ticker, dataset, period_type.
- Raw files are never modified.
- Retries with backoff, rate-limit awareness, and logged failures. One failed ticker must not stop the run.
- Build a **field-mapping table** (source field name → canonical field name) in `config/field_map.yaml`, because source labels vary.
- Expect short histories (yfinance often gives about 4 annual and 4–5 quarterly periods for Indian stocks). Analytics must handle this without crashing, and the UI must say how many periods are available.

---

## 7. Cleaning (`src/cleaning/pipeline.py`)

The pipeline must be reusable and idempotent (running it twice on the same raw data gives the same output). It handles:

- **Missing values:** classify each as `unavailable_from_source`, `not_applicable` (for example, gross profit for a bank) or `failed_retrieval`. **Never fill financial values with 0.**
- **Duplicates:** deduplicate on natural keys (company + date for prices; company + period_end + period_type for financials), keeping the latest retrieval.
- **Dates:** ISO dates, fiscal-period labels, timezone-naive dates for market data.
- **Units:** convert to absolute INR and record the original unit.
- **Sign conventions:** capex stored as a positive outflow and debt as positive. Document every convention in `docs/methodology.md`.
- **Derived fields:** when a field is missing but mathematically derivable (for example, FCF = OCF − Capex, or EBITDA = EBIT + D&A), derive it and set `is_calculated = true` with a `formula_id`.
- **Outliers:** flag them, never delete them.

Output goes to `data/processed/` and the database staging tables.

---

## 8. Validation (`src/validation/checks.py`)

Each check is a small, individually tested function that returns structured results (check_name, severity: `error | warning | info`, status, message, record key).

| Category | Checks |
|---|---|
| Market data | High ≥ max(Open, Close, Low); Low ≤ min(Open, Close); Volume ≥ 0; prices > 0; no duplicate company+date; flag gaps longer than 5 trading days; flag absolute daily return > 20% for review |
| Financials | Assets ≥ 0; Revenue ≥ 0; Shares > 0; Assets ≈ Liabilities + Equity (within 2% tolerance; warning, not error); margins within [−100%, 100%] (warning outside) |
| Completeness | % of expected fields present per company and period |
| Consistency | Same period never has conflicting values from the same source |
| Freshness | Latest price date ≥ last trading day − N days |

Write all results to `data_quality_logs`, plus a run-level summary (total, valid, invalid, warnings, missing %, duplicates, run timestamp). The pass rate is **computed**, never hard-coded.

---

## 9. Database (PostgreSQL)

Use `sql/schema.sql` as the single source of truth (SQLAlchemy models must match it), plus `sql/indexes.sql` and `sql/analytical_queries.sql`. Organize it in layers: `staging` schema → `core` schema → `mart` schema (Power BI views).

**Core tables (minimum):**

- `companies`: company_id, ticker (unique), company_name, exchange, sector_id, industry_id, peer_group, sector_type, country, currency, created_at, updated_at
- `sectors`, `industries`: lookups. The companies table references them; don't duplicate sector text.
- `data_sources`: source_id, name, url, notes
- `market_prices`: company_id, date, open, high, low, close, adj_close, volume, source_id, retrieved_at. **UNIQUE(company_id, date)**, index on (company_id, date).
- `financial_statements`: company_id, statement (`income | balance | cashflow`), fiscal_year, fiscal_quarter (nullable), period_end_date, period_type (`annual | quarterly`), line_item, value, currency, unit, is_calculated, formula_id, source_id, retrieved_at. **UNIQUE(company_id, statement, line_item, period_end_date, period_type)**.
  - Long format is preferred. If you choose wide per-statement tables instead, justify it in `docs/assumptions.md`.
- `metrics`: company_id, period_end_date, period_type, metric_name, value, unit, formula_id, as_of_date, input_fields (JSON). This is long format and holds both ratios and valuation multiples.
- `formulas`: formula_id, metric_name, expression (text), description, applicable_sector_types
- `data_quality_logs`: as in Section 8, plus run_id
- `pipeline_runs`: run_id, started_at, finished_at, status, records_processed, notes

Use upserts so the refresh script is idempotent.

---

## 10. Analytics Engine (`src/analytics/`)

Write every metric as a pure function (DataFrame/Series in, Series out), registered with its formula_id, unit and applicable sector types.

### 10.1 Sector applicability matrix (required)

| Metric | Non-financial | Bank / NBFC |
|---|---|---|
| Gross / EBITDA / EBIT margin, EV/EBITDA, EV/Revenue | ✓ | **N/A** |
| Current / quick ratio | ✓ | **N/A** |
| Debt/Equity, Net Debt/EBITDA | ✓ | **N/A** (deposits are not debt) |
| P/E, P/B, ROE, ROA, EPS growth, net margin | ✓ | ✓ |
| NII growth, cost-to-income, loan/deposit ratio | — | ✓ only if source fields exist; otherwise N/A |

If a metric isn't applicable, show "N/A (not meaningful for banks)", never a number.

### 10.2 Metrics

- **Growth (YoY):** revenue, EBITDA, net income, EPS, FCF. Return null if the prior value is ≤ 0 or missing (no misleading growth from a negative base).
- **Profitability:** gross, EBITDA, EBIT and net margin; ROE; ROA. Use average equity and average assets when two periods exist; document the fallback otherwise.
- **Leverage:** debt/equity, debt/assets, net debt/EBITDA
- **Liquidity:** current ratio, quick ratio
- **Cash flow:** FCF margin, OCF margin, capex/revenue

### 10.3 Valuation (`valuation.py`)

Define the conventions explicitly:
- **Current multiples:** as of the latest price date, using **TTM** figures (sum of the last 4 quarters if available, otherwise the latest annual, labelled accordingly).
- **Historical multiples:** price at the fiscal period end ÷ that period's fundamentals.
- **Market cap** = price × shares outstanding (record the shares as-of date).
- **EV** = market cap + total debt + minority interest − cash. Non-financials only.
- If the source provides multiples directly, store them separately and **reconcile** them against your calculated values. Flag differences above 10% as warnings.

### 10.4 Returns and risk (`returns.py`, `risk.py`)

All calculations use adjusted close, so they are split- and bonus-adjusted.

- **Returns:** daily (simple), cumulative, 1M, 3M, YTD, 1Y, 3Y (CAGR). Return null if the history is too short.
- **Annualized volatility** = std(daily returns) × √252
- **Downside deviation:** standard deviation of returns below 0 (or the daily risk-free rate; document which), × √252
- **Sharpe** = (annualized return − Rf) ÷ annualized volatility, with Rf from config
- **Max drawdown:** from the running peak of cumulative wealth, also reporting the peak and trough dates
- **Correlation:** a matrix of daily returns over an aligned date range (inner join on trading dates)
- **Tier 3:** beta and tracking error vs Nifty 50

### 10.5 Comparable companies (`valuation.py` / `comps.py`)

- Default peers come from the same `peer_group`. The user may override, but show a warning when peers mix sector types.
- **Peer statistics** for each metric: min, 25th percentile, median, mean, 75th percentile, max, and n (count of non-null peers). The **target is excluded** from the peer statistics.
- **Target positioning:** percentile rank within peers, and the premium or discount vs the peer median, as a %.
- **Interpretation text** is generated from rules only, using templates such as: *"{Company} trades at {x}x EV/EBITDA, {y}% above the peer median of {z}x (n={n})."* Skip any sentence whose inputs are null.

### 10.6 Anomaly detection (`anomaly_detection.py`, Tier 2)

- IQR and z-score methods on YoY changes in revenue, margins and debt, and on daily returns and volume.
- Use a rolling window for market data.
- Wording must always be *"Potential data anomaly detected"*. Never fraud, misconduct or similar.

### 10.7 Financial health score (Tier 3)

- Rule-based and transparent: Profitability 20%, Growth 20%, Leverage 20%, Liquidity 15%, Cash Flow 25%.
- Normalize metrics by **percentile within peer_group**, not across the whole universe.
- Banks use their own documented component set, or are excluded with a stated reason.
- The UI shows each component's raw value, its normalized score, its weight and its contribution.

### 10.8 Data Science Lab (`src/ml/`) — the core of the project

**General rules for every model:**
- **Time-aware validation only.** Use walk-forward / expanding-window splits, never random K-fold on time series.
- Every feature at time *t* uses only information available at *t*. Lag features explicitly. Fundamentals become available on their **announcement date**, not the period-end date; if announcement dates are unavailable, apply a documented conservative lag (for example, 60 days after quarter end).
- Every model is compared against **naive baselines**, with a table of metrics on held-out periods.
- Report uncertainty: confidence intervals, or variation across walk-forward folds.
- Save results (metrics, parameters, seed, data snapshot id) to `data/processed/ml_results/` and the database, so the app displays stored results rather than retraining live.
- Write `docs/model_cards/{model}.md` for each model, covering: purpose, data, features, validation scheme, results vs baseline, limitations, and what it must not be used for.

**A. Volatility forecasting** (`volatility.py`). This is the main supervised task, because volatility is genuinely forecastable and directly useful for risk.
- **Target:** realized volatility over the next 5 and 21 trading days.
- **Baselines:** historical volatility (trailing 21-day), EWMA (RiskMetrics, λ = 0.94).
- **Models:** GARCH(1,1) via `arch`, plus a feature-based model (ridge regression or gradient boosting) using lagged realized vol at several horizons, absolute returns, volume changes, range-based vol (Parkinson), and market (Nifty) vol.
- **Evaluation:** walk-forward; RMSE, MAE and QLIKE loss per ticker and pooled; a Diebold–Mariano test vs the best baseline.
- **Display:** forecast vs realized chart, and a model-comparison table.

**B. Peer clustering** (`clustering.py`). This is the unsupervised task, and it answers a real comps question: do data-driven peer groups match official sector labels?
- **Features:** return-based (correlations, volatility, beta) and fundamental (margins, growth, leverage where applicable), standardized. Document how missing values and banks are handled.
- **Methods:** K-means and hierarchical clustering (on 1 − correlation distance). Choose k with silhouette score and the elbow method, and justify the choice.
- **Validation:** adjusted Rand index vs sector labels, and stability across bootstrap resamples and time windows.
- **Display:** PCA/UMAP 2-D projection, dendrogram, cluster vs sector cross-tab, and an interpretation of mismatches.

**C. Statistical testing** (`stats_tests.py`).
- Return distributions: normality tests (Jarque–Bera), skew, kurtosis, QQ plots, and fat tails discussed.
- Bootstrapped confidence intervals for peer medians and for Sharpe ratios.
- Correlation significance, with a multiple-comparison correction (Benjamini–Hochberg).
- Sector differences in margins or returns: Kruskal–Wallis plus post-hoc tests. Report effect sizes, not just p-values.
- Rolling correlation stability: how correlations change in stressed vs calm periods.

**D. Anomaly detection comparison** (extends 10.6).
- Isolation Forest vs IQR and z-score on the same data.
- Report the overlap between methods and inspect a sample of flagged cases manually in a notebook.
- Wording stays *"Potential data anomaly detected"*.

**E. Regime detection** (Tier 2, `regimes.py`).
- A 2–3 state Gaussian HMM, or a rolling-volatility threshold model, on Nifty returns.
- Show regime-conditional volatility and correlation.
- Describe it as **descriptive** segmentation, not a trading signal.

**Explicitly out of scope:** price or return-direction prediction presented as a trading strategy, sentiment scraping, and any "AI picks".

---

## 11. Analytical Language Rules

- ✗ "undervalued", "overvalued", "good investment", "buy", "sell", "strong company"
- ✓ "trades at a lower EV/EBITDA multiple than the peer median"
- Every displayed number must trace back to retrieved data, stored data or a documented formula.
- Display a footer on every page: *"FinSight is an analytics tool, not investment advice. Data: {sources}, as of {date}."*

---

## 12. Streamlit App (`app/`)

**Branding:** title **FinSight**, subtitle *Financial Markets & Comparable Companies Analytics Platform*. The design should be restrained, in an investment-banking style: neutral palette, one accent colour, consistent number formatting, and no emojis, gradients or decorative charts.

**Pages:**

1. **Overview:**
   - Universe size, sectors, latest data date
   - Median 1Y return, median P/E
   - Top performers (1Y return, revenue growth, ROE)
   - Data-quality summary (records, pass rate, missing %)
2. **Company Analysis:**
   - Sidebar: company, annual or quarterly, period range
   - Profile and metric cards (revenue, growth, EBITDA margin, net margin, ROE, D/E, P/E, EV/EBITDA, with N/A where not applicable)
   - Trend charts: revenue, EBITDA, net income, margins, debt, FCF
   - Show the number of periods available
3. **Comparable Companies:**
   - Target and peer selectors
   - Operating, capital-structure and valuation tables with the peer statistics rows
   - Percentile positioning chart
   - Generated interpretation
   - CSV download
4. **Market Analytics:**
   - Companies, date range, benchmark
   - Cumulative return vs benchmark, return distribution, rolling volatility, drawdown, volume
5. **Risk Analytics:**
   - Volatility, max drawdown, Sharpe, downside deviation table
   - Correlation heatmap
   - An expandable plain-English explanation and formula for each metric
6. **Data Science Lab:**
   - Volatility forecast vs realized for the selected ticker
   - Model-vs-baseline metrics table with fold variation
   - Peer-cluster map and the cluster vs sector cross-tab
   - Statistical test results with confidence intervals
   - A link to each model card
   - All results are read from stored outputs
7. **Data Quality:**
   - Run summary, last refresh time
   - Filterable failures table (company, dataset, check, severity, status, message)
   - Completeness by company

**Performance:**
- Read from PostgreSQL only. Never call external APIs from the app.
- Use parameterized SQL in `src/database/queries.py`.
- Use `st.cache_data` with a TTL, and cache invalidation on refresh.
- Use vectorized pandas.

---

## 13. Exports (Tier 2)

### Excel (`src/exports/excel.py`, openpyxl)

- `financial_summary.xlsx`: Company Overview, Financial Ratios, Growth Analysis, Valuation
- `comparable_companies.xlsx`: Peer Set, Operating Metrics, Valuation Multiples, Peer Statistics
- `market_analysis.xlsx`: Returns, Risk, Correlation

**Requirements:**
- Peer statistics use **live Excel formulas** (`MEDIAN`, `PERCENTILE.INC`, `AVERAGEIFS`), with `INDEX/MATCH` lookups and conditional formatting for above or below the median.
- Prefer `INDEX/MATCH` over `XLOOKUP` for compatibility.
- openpyxl cannot create PivotTables. Don't fake them. Document how to add one manually in `docs/excel_guide.md`.
- Every sheet has a header noting source, as-of date and units.

### Power BI (`src/exports/powerbi.py`)

- Create CSVs and `mart` schema views: `dim_company`, `dim_date` (with fiscal year and fiscal quarter columns), `dim_sector`, `fact_market_prices`, `fact_financials`, `fact_metrics`, `fact_valuation`.
- Use a star schema with surrogate keys.
- `docs/powerbi_model.md` must cover:
  - Relationships and cardinality
  - Recommended **DAX measures**, written out (for example, peer median EV/EBITDA, YoY growth, cumulative return)
  - A 5-page report layout spec (Executive Overview, Financial Performance, Comparable Companies, Market Performance, Risk & Correlation)
- Note that Power BI Desktop is Windows-only. The .pbix is built by the user, not by you. Do not claim a .pbix exists.

---

## 14. Refresh and Logging

`python scripts/update_data.py [--tickers ...] [--skip-fetch]` runs these steps, each logged with timing and counts:

1. Fetch
2. Save raw
3. Clean
4. Validate
5. Upsert to PostgreSQL
6. Recompute metrics
7. Regenerate exports
8. Write the `pipeline_runs` row

`--skip-fetch` rebuilds everything from the latest raw snapshot. This is how the project runs reproducibly offline.

Use Python `logging` (INFO / WARNING / ERROR) to the console and to `logs/finsight.log`, in this style:

```text
INFO - Retrieved 21,340 price rows for 17 tickers
WARNING - EBITDA unavailable for HDFCBANK.NS (not applicable: bank)
INFO - Validation: 98.7% of records passed
```

---

## 15. Testing (`tests/`)

- Tests must run **offline**, against small fixture DataFrames with **hand-calculated expected values** written in comments.
- No network calls in tests.
- **Minimum tests:**
  - Revenue growth, including a negative-base case
  - EBITDA margin
  - Debt/equity
  - N/A for banks
  - Daily return
  - Max drawdown, with a known peak and trough
  - Annualized volatility
  - Sharpe
  - Peer statistics, excluding the target
  - Percentile rank
  - EV calculation
  - TTM aggregation
  - Duplicate detection
  - Invalid OHLC
  - Balance-sheet tolerance check
  - IQR anomaly flag
  - Fiscal-quarter mapping
- **ML tests:**
  - Walk-forward splits never let a test date precede a train date
  - A feature-leakage test: features at *t* are unchanged when data after *t* is altered
  - EWMA baseline against a hand-computed value
  - Clustering is reproducible with a fixed seed
  - Bootstrap CI contains the point estimate
- One integration test runs the pipeline end to end on the fixture data against a test database (or a SQLite fallback, if documented).
- Target: meaningful coverage of `analytics/` and `validation/`, and report the actual coverage %.

---

## 16. Project Structure

```text
finsight/
├── app/  Home.py, pages/01_Company_Analysis.py … 05_Risk_Analytics.py, 06_Data_Science_Lab.py, 07_Data_Quality.py, components/{charts,metrics,tables,formatting}.py
├── config/  universe.yaml, field_map.yaml, settings.py (Pydantic)
├── src/
│   ├── ingestion/  market_data.py, financial_data.py, company_metadata.py
│   ├── cleaning/   pipeline.py
│   ├── validation/ checks.py
│   ├── analytics/  ratios.py, valuation.py, comps.py, returns.py, risk.py, anomaly_detection.py, health_score.py
│   ├── ml/         features.py, splits.py (walk-forward), volatility.py, clustering.py, stats_tests.py, regimes.py, evaluation.py
│   ├── database/   connection.py, models.py, queries.py
│   └── exports/    excel.py, powerbi.py
├── scripts/  init_db.py, update_data.py
├── sql/  schema.sql, indexes.sql, analytical_queries.sql
├── notebooks/  01_eda, 02_financial_ratios, 03_comparable_companies, 04_market_risk_and_stats_tests, 05_volatility_forecasting, 06_peer_clustering, 07_anomalies_and_regimes
├── data/  raw/, processed/, exports/   (raw and processed are gitignored)
├── tests/
├── docs/  model_cards/, data_dictionary.md, methodology.md, powerbi_model.md, excel_guide.md, assumptions.md, limitations.md, architecture.md (Mermaid diagrams)
├── .env.example, .gitignore, pyproject.toml / requirements.txt, docker-compose.yml, README.md
```

---

## 17. Phases and Acceptance Criteria

Stop after each phase, report the evidence, and wait for approval.

| Phase | Build | Done when |
|---|---|---|
| 1 | Repo, environment, config, Postgres (Docker), `init_db.py`, data-source inspection report | DB initializes from scratch; a report shows the actual fields and periods returned for 3 tickers, including one bank |
| 2 | Ingestion and raw snapshots | All tickers fetched or failures logged; raw files on disk with metadata |
| 3 | Cleaning, validation, DB load | Tables populated; quality report printed; rerun is idempotent (same row counts) |
| 4 | Ratios, valuation, returns, risk | Metrics table populated; bank N/A rules hold; unit tests pass |
| 5 | Comps, peer statistics, anomalies, correlation | Peer stats exclude the target; interpretations are generated from data |
| 6 | Data Science Lab: EDA, volatility forecasting, clustering, statistical tests (then anomalies and regimes) | Leakage tests pass; every model has a baseline comparison table, fold-level results and a model card; results stored |
| 7 | Streamlit app | All 7 pages load with no errors and no API calls |
| 8 | Excel and Power BI exports | Files open in Excel/LibreOffice; formulas calculate; CSVs match the star schema |
| 9 | Notebooks, docs, README, diagrams, final verification | Fresh-clone setup works by following the README exactly |

Each notebook follows the same structure: question → data → method → result → interpretation → limitations. Keep them concise. They read from the database or processed data, never the live API, and they call functions from `src/` rather than duplicating logic.

---

## 18. README Requirements

Include:
- Problem, solution and features
- Architecture diagram and ER diagram (Mermaid)
- Tech stack
- Setup (macOS first, then Windows)
- Database initialization and refresh commands
- Data sources with retrieval dates
- Methodology summary (linking to `docs/methodology.md`)
- Screenshot placeholders, labelled for the user to capture
- Limitations: free-data gaps, short statement history, delayed data, bank-metric applicability, corporate actions, rate limits, single currency

---

## 19. Final Report (deliver at the end)

1. Complete setup instructions (macOS and Windows)
2. Commands to initialize the DB, refresh data, run the app, run tests and generate exports
3. Features implemented, each marked **verified** (with how it was verified) or **partial**
4. Known limitations and any deviations from this spec, with reasons
5. The 4–6 screenshots worth capturing for the README
6. A one-line GitHub repository description and topics
7. Resume bullets for a **data science** role, supported **only by verified features**, with metrics drawn from actual runs. For example: the percentage improvement in QLIKE or RMSE of the best volatility model vs EWMA, the adjusted Rand index of clusters vs sectors, rows processed, validation checks, test count. If a model did not beat its baseline, say so and write the bullet around the evaluation framework instead.
8. Fifteen likely data-science interview questions about this project, covering leakage, validation design, baseline choice, why not predict prices, fat tails, multiple testing, and cluster validity. Give each a short answer that references the actual implementation.
