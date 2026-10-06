# Assumptions

Small assumptions made while building FinSight, each with the conservative option chosen.
Significant assumptions are raised with the project owner first and marked **OPEN** until settled.

## Decisions made by the project owner (2026-10-06)

| # | Topic | Decision |
|---|---|---|
| D1 | Risk-free rate | 91-day Government of India T-bill, **5.2599%** annualized (implicit yield at cut-off), RBI weekly auction, as of 2026-09-02. Source: RBI press release "Treasury Bills: Full Auction Result", https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63500. The value was supplied by the project owner and stored in `config/universe.yaml`; the pipeline does not fetch it. |
| D2 | Risk-free rate is a **single constant** | One rate is applied to the whole price history. This is a simplification: see `limitations.md`. Where a daily rate is needed it is `(1 + r)^(1/252) − 1` = 0.020344% per trading day (`RiskFreeRate.daily`). |
| D3 | Tata Motors excluded, Bajaj Auto added | Neither successor ticker is used (see `limitations.md`). `BAJAJ-AUTO.NS` replaces it; verified to resolve with 1,240 daily rows from 2021-10-06 to 2026-10-06. |

## Open (need a decision)

None.

## Phase 1

| # | Assumption | Reason |
|---|---|---|
| A1 | Host port **5433** for the Docker PostgreSQL container (configurable via `POSTGRES_PORT`). | The development Mac already runs a PostgreSQL service on 5432. |
| A2 | Python 3.13 is the verified interpreter; the spec minimum (3.11) is kept in `pyproject.toml`. | 3.13 is what is installed on the development machine. Versions in `requirements.txt` are the ones actually run. |
| A3 | Sector, industry and peer-group labels come from `config/universe.yaml`, not from Yahoo. Yahoo's labels are kept in `staging.company_metadata` as `source_sector` / `source_industry`. | Yahoo uses US-centric labels ("Banks - Regional" for HDFC Bank, "Technology" for TCS). Peer groups must be a deliberate, documented choice. |
| A4 | The Nifty 50 benchmark is stored as a row in `core.companies` with `entity_type = 'index'` and no sector attributes. | Keeps `core.market_prices` one uniform table. A CHECK constraint requires sector attributes for every real company. |
| A5 | Statements are stored in **long format** (`core.financial_statements`, one row per line item per period). | Preferred by the spec; source line items differ by company and sector type, so a wide table would be mostly NULL for banks. |
| A6 | `financial_statements.value` is NULL **if and only if** `missing_reason` is set (CHECK constraint). | Enforces "never fill with 0" and the three-way missing classification at the database level. |
| A7 | Monetary columns are `NUMERIC`; computed metrics are `DOUBLE PRECISION`. | Statement values are exact reported figures. Ratios are derived floating-point results. |
| A8 | Two cash fields are kept: `cash_and_equivalents` and `cash_and_short_term_investments`. Which one enters EV is decided and documented in Phase 4. | The two differ a lot for cash-rich companies (TCS FY2026: about ₹6,405 Cr vs ₹41,373 Cr), so the choice must be explicit rather than hidden in the mapping. |
| A9 | `ebitda` maps to Yahoo's `EBITDA`, not `Normalized EBITDA`. | Reported figure; "normalized" applies Yahoo's own unusual-item adjustments, which cannot be audited. |
| A10 | `total_equity` means equity attributable to shareholders (`Stockholders Equity`), excluding minority interest. | Matches the denominator used for ROE, P/B and D/E. Minority interest is a separate line item for EV. |
| A11 | Two extra tables beyond the spec minimum: `core.shares_outstanding` (point-in-time shares with as-of date) and `core.source_reported_metrics` (Yahoo's own multiples, for reconciliation). | The spec requires a shares as-of date and requires source multiples to be stored separately from calculated ones. |
| A12 | `core.metrics` has an extra `na_reason` column and a `period_type` that also allows `ttm` and `point_in_time`. | The UI must show *why* a metric is N/A; current multiples are TTM and risk metrics are as-of a date. |

## Phase 2 (ingestion)

| # | Assumption | Reason |
|---|---|---|
| B1 | The auto peer group is named `auto` and the sector `Auto` for all three auto companies (Bajaj Auto, M&M, Maruti Suzuki). | The owner specified `sector Auto, peer_group auto` for Bajaj Auto. Keeping the other two on the old `auto_oem` label would have left Bajaj Auto in a peer group of one. |
| B2 | Bajaj Auto (two- and three-wheelers) is compared with two passenger-vehicle makers. | It is the owner's chosen replacement. The product mix differs from M&M and Maruti, which is noted wherever auto comps are interpreted. |
| B3 | Raw snapshot layout is `data/raw/yfinance/{dataset}/{ticker}/{YYYYMMDDTHHMMSSZ}.parquet` with datasets `market_prices`, `company_metadata` (JSON), and `{income,balance,cashflow}_{annual,quarterly}`. | Follows the spec path. The period type is part of the dataset name so annual and quarterly tables retrieved in the same second cannot collide. |
| B4 | Snapshot metadata (source, dataset, ticker, period_type, retrieved_at UTC) is stored **inside** each file: the Parquet footer, or a `metadata` object in the JSON. | The metadata cannot be separated from the data it describes. |
| B5 | The only change to a response before saving is that statement column labels (period-end timestamps) become ISO date strings. | Parquet column names must be strings. No value is altered; nulls stay null. |
| B6 | Raw files are write-once: saving to an existing path raises, and files are set read-only. | Enforces "raw files are never modified". |
| B7 | An empty **price** or **metadata** response is treated as a failure and retried. An empty **statement** is recorded as `empty` (not a failure) and no file is written. | Every ticker must have prices. A missing statement is a real property of the source (quarterly cash flow), later classified `unavailable_from_source`. |
| B8 | The whole `Ticker.info` dict is saved, not only the fields FinSight uses. | "Save every response unchanged". The used fields are listed in `config/field_map.yaml`. |
| B9 | Retry policy: 4 attempts, waits of 2s, 4s, 8s; at least 30s when the source signals rate limiting; 0.4s pause between requests. Configurable with `INGEST_*` in `.env`. | Conservative defaults for a free, rate-limited source. |
| B10 | A run manifest (`data/raw/_manifests/{run}.json`) records every dataset's outcome, including `empty` and `failed`. | Lets cleaning tell "source has no data" from "retrieval failed" without re-fetching. |

## Phase 3 (cleaning, validation, load)

Rules are described in full in `methodology.md`. The choices below are the ones that involved judgement.

| # | Assumption | Reason |
|---|---|---|
| C1 | **Infosys statements are converted from USD to INR** by FinSight: flows at the period-average USD/INR rate, balances at the period-end rate. | The spec requires INR storage. Yahoo serves Infosys in USD (FY2026 revenue 2.016e10, EPS 0.80). Average-for-flows and closing-for-stocks is the standard translation convention. **Raised with the project owner at the Phase 3 review.** |
| C2 | Statement currency is set per company in `config/universe.yaml`, not read from Yahoo's `financialCurrency`. | That flag is USD for HCLTech, whose statements are in INR (FY2026 revenue 1.301e12, EPS 61.36). Trusting it would have multiplied HCLTech's figures by about 88. The `statement_currency_scale` check guards the setting. |
| C3 | USD/INR comes from Yahoo `INR=X` via yfinance, 10 years of daily closes, stored in `core.fx_rates`. | Same documented source as everything else; 10 years so every fiscal period has a full-period average. |
| C4 | A converted value needs FX quotes within 7 days of both ends of its period (flows) or of the period end (balances); otherwise it is NULL plus an error. | A partial-period average would silently misstate the figure. |
| C5 | Fields outside a sector type's `applies_to` list are NULL (`not_applicable`) even when the source returns a number. | Yahoo computes a "Net Interest Income" for non-financials and similar items; storing them would invite meaningless bank-style ratios. |
| C6 | Prices use the latest snapshot only; statements combine all snapshots with the latest retrieval winning. | Adjusted close is re-based by the source after corporate actions, so price snapshots cannot be mixed. Statement values are not re-based, and combining snapshots keeps periods the source later drops. |
| C7 | An incomplete price row is repaired from an earlier snapshot only if both snapshots agree on adjusted close for the previous shared date; otherwise it is rejected and not loaded. | Keeps the adjusted series on one basis. Prevents a transient source glitch from overwriting a good close with NULL (this happened on 2026-10-07 before the rule existed). |
| C8 | `core.market_prices` mirrors the cleaned rows within the latest snapshot's date range (rows no longer present are removed); older history is left alone. | Makes the database a pure function of the raw files. This is the only place rows are removed from core, and only rows the cleaning step rejected or the source withdrew. |
| C9 | Placeholder price rows (zero volume, flat at the previous close) are kept and flagged `is_stale_quote`; return and risk calculations will exclude them. | 116 such rows exist; 6 of TCS's 7 fall on dates that are not Nifty trading days (exchange holidays). Each would add an artificial zero return and understate volatility. |
| C10 | Price gaps are measured in missing weekdays (Mon–Fri) between consecutive rows, without an exchange holiday calendar. | No holiday calendar is ingested. Holiday clusters do not exceed the 5-weekday threshold. |
| C11 | Freshness compares the latest price with the last weekday on or before the run date, allowing 5 calendar days. | Covers a weekend plus a holiday without false alarms. Running `--skip-fetch` on old snapshots therefore warns that the data is stale, which is intended. |
| C12 | Completeness counts only applicable fields and warns below 80% per company and period. | A bank should not be penalised for having no gross profit. 80% was fixed before looking at the results. |
| C13 | The balance-sheet identity uses equity **including** minority interest. | The source's "Total Liabilities Net Minority Interest" excludes it, so assets = liabilities + equity only holds with it added back. |
| C14 | "Invalid record" means a record with an error-severity failure. Warnings do not make a record invalid. | Warnings (low completeness, a large move) are prompts for review, not wrong data. The pass rate would otherwise mix the two. |
| C15 | Period columns that the source returns entirely empty are skipped rather than stored as all-NULL rows. | They carry no information (typically the oldest of 5 annual columns). |
| C16 | `--tickers` limits the fetch only. Cleaning, validation and loading always cover the whole universe. | Keeps staging a complete picture and the quality summary comparable between runs. |
| C17 | Integration tests use a separate `finsight_test` database in the same PostgreSQL container, created on first use. | Tests the real upsert SQL (PostgreSQL-specific) without touching project data. They skip if PostgreSQL is unreachable. |
| C18 | Three more tables beyond the spec minimum: `core.fx_rates`, `staging.fx_rates`, `staging.source_reported_metrics`; and columns `is_stale_quote`, `original_currency`, `fx_rate`. | Needed for C1, C9 and for auditing every converted figure back to its reported value. |
