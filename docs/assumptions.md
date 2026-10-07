# Assumptions

Small assumptions made while building FinSight, each with the conservative option chosen.
Significant assumptions are raised with the project owner first and marked **OPEN** until settled.

## Decisions made by the project owner (2026-10-06)

| # | Topic | Decision |
|---|---|---|
| D1 | Risk-free rate | 91-day Government of India T-bill, **5.2599%** annualized (implicit yield at cut-off), RBI weekly auction, as of 2026-09-02. Source: RBI press release "Treasury Bills: Full Auction Result", https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63500. The value was supplied by the project owner and stored in `config/universe.yaml`; the pipeline does not fetch it. |
| D2 | Risk-free rate is a **single constant** | One rate is applied to the whole price history. This is a simplification: see `limitations.md`. Where a daily rate is needed it is `(1 + r)^(1/252) − 1` = 0.020344% per trading day (`RiskFreeRate.daily`). |
| D3 | Tata Motors excluded, Bajaj Auto added | Neither successor ticker is used (see `limitations.md`). `BAJAJ-AUTO.NS` replaces it; verified to resolve with 1,240 daily rows from 2021-10-06 to 2026-10-06. |

| D4 | Infosys stays in the universe (2026-10-07) | Ratios, margins and growth are computed in USD, the reporting currency. INR translation is used only for levels and valuation: flows at the period-average rate, balances at the period-end rate, TTM quarter by quarter. Reported USD values, the rate, its type and its source are stored and never overwritten. Translated figures are flagged. Full rule in `methodology.md` 2.4. |

| D5 | Cash in EV and net debt (2026-10-07) | Approved: cash and short-term investments, falling back to cash and equivalents. |
| D6 | EPS (2026-10-07) | EPS = net income attributable to shareholders / shares on the current basis, restated for splits and bonus issues using yfinance's split history; weighted-average shares where available, otherwise period-end shares, recorded on the row. Yahoo's reported EPS is stored for reconciliation only. See E4 for how the restatement is applied. |
| D7 | P/E and P/B (2026-10-07) | Approved: market cap / net income attributable to shareholders, and market cap / shareholders' equity. N/A when the denominator is zero or negative; never a negative multiple; N/A values are excluded from peer statistics. |
| D8 | Downside deviation (2026-10-07) | Approved: Sortino form, RMS of returns below a 0% daily target over all days, × √252. |
| D9 | Risk windows (2026-10-07) | Approved: 1 and 3 years for stored risk metrics. The Data Science Lab uses the full price history. |
| D10 | ROE, ROA, net debt / EBITDA (2026-10-07) | Approved: annual only, closing-balance fallback for the earliest year. Each row stores `method` (`average_balance` or `closing_balance`) and the UI footnotes the fallback. |

| D11 | Small peer groups (2026-10-07) | With fewer than 4 peers that have a value: show only min, median and max, and give position as a rank ("2nd of 3") instead of a percentile. |
| D12 | Anomaly scoring (2026-10-07) | Robust modified z-score, 0.6745 × (x − median) / MAD, flagged at \|M\| > 3.5 (Iglewicz & Hoaglin), in place of the classical z-score. IQR fences stay at 1.5× for fundamentals and 3× for market data. All thresholds fixed before looking at results. |
| D13 | Fundamental anomalies (2026-10-07) | Raw changes are not pooled across sectors. Each change has its peer-group median change for the same metric and period subtracted; the residuals are pooled and tested. No score is ever computed on one company's own annual values. |
| D14 | Universe expanded to 25 (2026-10-07) | Added Tech Mahindra, State Bank of India, Lupin, Divi's Laboratories, Hero MotoCorp, Eicher Motors, Nestle India and Britannia, so every peer group has 4 peers after excluding the target. All eight verified: resolve, five years of prices, statements in INR. |

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
| C1 | **Infosys statements are translated from USD to INR** by FinSight: flows at the period-average USD/INR rate, balances at the period-end rate. | The spec requires INR storage. Yahoo serves Infosys in USD (FY2026 revenue 2.016e10, EPS 0.80). Confirmed and refined by the project owner (decision D4). |
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

## Phase 4 (ratios, valuation, returns, risk)

Formulas are in `methodology.md` sections 5–8. The choices below involved judgement.

| # | Assumption | Reason |
|---|---|---|
| E1 | **Cash in EV and net debt is cash and short-term investments**, falling back to cash and equivalents where the broader figure is not reported. | Liquid investments are available to repay debt; for cash-rich companies the two differ several-fold (TCS: about ₹6,405 Cr vs ₹41,373 Cr). With this choice calculated EV is within 5% of Yahoo's for 18 of the 20 non-financials (the exceptions are Infosys at −5.0% and HCLTech at −7.7%, where Yahoo mixes currencies). With the narrow figure TCS alone would be about 5% off. Closes the question left open in A8. |
| E2 | A missing minority interest counts as zero in EV, and the row records `minority_interest_assumed_zero`. | A company with no minority interest does not report the line. Missing debt or cash, by contrast, makes EV NULL. |
| E3 | P/E is market cap / net income attributable to shareholders and P/B is market cap / shareholders' equity; both N/A on a zero or negative denominator (D7). | Does not depend on the source's per-share history, and a negative multiple has no meaning. |
| E4 | **Share counts are restated for splits only where they are still on the old basis** (D6). A figure is tested against a current-basis reference with a 1/√factor midpoint, and the decision is stored on the row. | The owner asked for every pre-split period to be multiplied by the cumulative factor. The source has already restated most of them: of 10 annual periods that precede a split, only HDFC Bank's FY2023 and FY2024 weighted averages are on the old basis. A blanket multiplication would double-count the other eight (halving Wipro's FY2024 EPS, for instance). The tested rule gives the result the owner specified: HDFC Bank FY2023→FY2024 EPS growth is −1.2% instead of the source's −50.2%. **Flagged to the owner at the Phase 4 review.** |
| E4a | Split history is read from the `Stock Splits` column of the 5-year price history, not from a separate full split history. | It is yfinance's split data and is already in the raw snapshots. Every statement period ends on or after 31-Mar-2022, inside the price window, so no relevant event is missed. A longer statement history would need the full split series. |
| E4b | If a period precedes a split and there is no reference to test its share count against, EPS is N/A rather than guessed. | Either guess could be wrong by the split factor. No row is in this state in the current data. |
| E4c | The period-end share count is tested against the company's **current** share count, which a merger or large buyback can move. HDFC Bank's FY2023 period-end count is 0.724 of today's because of its 2023 merger, close to the 0.707 midpoint; it is classified correctly and would be marked marginal if it were the figure used. | Weighted-average shares are used for EPS wherever they exist, and they are tested against the same period's period-end count, where the gap is unambiguous (0.50 or 1.00). |
| E5 | Growth is NULL when the prior-year value is zero or negative, and is looked up by date exactly one year earlier. | Growth from a negative base is not meaningful; a row-offset lookup would silently span a gap in the history. |
| E6 | ROE and ROA use average balances, with the closing balance as a fallback for the earliest year, recorded in the row's `method` field (D10). Computed for annual periods only. Net debt / EBITDA uses closing balances by definition, so its `method` is empty. | Spec. Quarterly return ratios would need annualizing, which adds an assumption for little use given the thin quarterly data. |
| E7 | Net debt / EBITDA is annual only. | A quarter's EBITDA against a full debt balance would overstate leverage about fourfold. |
| E8 | Quick ratio is NULL when inventory is not reported (Infosys). | Inventory is never assumed to be zero, even for a services company. |
| E9 | Sector applicability beyond the spec's matrix: revenue and net income growth apply to all sector types; EBITDA growth, FCF growth, FCF margin, OCF margin, capex / revenue and debt / assets apply to non-financials only. | The matrix lists only some metrics. A bank's operating cash flow is dominated by deposit and loan movements, and its borrowings are not comparable to corporate debt. |
| E10 | TTM uses the four quarters ending at the company's latest quarter with revenue, only if all four are present. Otherwise the latest annual figure, labelled `latest_annual`. | Spec. In the current data 17 of 25 companies fall back to the annual figure for P/E because the source has gaps in its quarterly history. |
| E11 | Current multiples use the latest price that is not a placeholder row, and the source's latest share count. | A placeholder row repeats the previous close and is not a traded price. |
| E12 | A price or FX rate counts for a target date if it is on or before it and at most 7 calendar days older. | Covers weekends and holiday clusters without reaching for a stale quote. |
| E13 | Risk metrics use trailing 1-year and 3-year windows, need at least 30 daily returns, and are NULL when the history is shorter than the window. | A 5-year window would sit exactly at the edge of the 5-year price history and fail whenever the start date falls on a holiday. |
| E14 | Annualized return in the Sharpe ratio is the arithmetic mean daily return × 252. | The standard Sharpe convention, and consistent with annualizing volatility by √252. |
| E15 | Downside deviation is the lower-partial-moment form against a 0% daily target, not the standard deviation of the negative returns alone. | The spec allows either target and asks that the choice be documented. The LPM form is the Sortino denominator and accounts for how often losses occur. |
| E16 | Return and risk metrics exclude placeholder rows (C9). | Otherwise each market holiday would count as a zero-return trading day. |
| E17 | `core.metrics` is deleted and rebuilt in full on every run, as is `core.valuation_reconciliation`. | Metrics are a pure function of the core data; rebuilding makes them reproducible and leaves nothing stale. Verified by identical fingerprints across runs. |
| E18 | Reconciliation differences above 10% are flagged and logged, never corrected. | Spec. The flagged cases are definitional (section "Valuation" of `limitations.md`). |
| E19 | More tables and columns: `core.valuation_reconciliation`, `core.stock_splits` (and its staging table); `original_value`, `fx_rate_type`, `fx_source` on statements; `reporting_currency`, `is_translated`, `method` on metrics. | Required by decisions D4, D6 and D10 and by the reconciliation rule. |
| E20 | `method` is also filled for EPS rows (the share count used) and for current multiples (`ttm` or `latest_annual`). | The same need as D10: the UI can footnote any figure whose calculation had more than one possible basis. |

## Phase 5 (comps, anomalies, correlation)

Rules are in `methodology.md` sections 9–11.

| # | Assumption | Reason |
|---|---|---|
| F1 | Percentile rank counts a peer with an equal value as half (mid-rank): 100 × (below + 0.5 × equal) / n. Approved by the owner. | Symmetric: a target level with all its peers sits at 50, not 0 or 100. |
| F2 | For percentage metrics the comparison with the median is a gap in percentage points; a relative premium is reported only for multiples and ratios, and only when the median is positive. Approved by the owner. | "20% above a 10% margin" is easily misread as 30%. A relative premium over a zero or negative median is meaningless. |
| F3 | The comps tables use 15 metrics: 8 operating, 3 capital structure, 4 valuation (`comps.COMPS_METRICS`). | Covers the three tables the spec asks for without near-duplicates. |
| F4 | Each company is compared on its own latest annual period; periods are not forced to match. | The target's period is stored with each row. Nestle India's latest annual period (FY2026) has no income-statement data at source, so it is N/A in operating comparisons rather than represented by an older year. |
| F5 | The rank shown when n < 4 (D11) counts the target: position among the target and the peers with a value, highest first, ties sharing the better place. So with two peers the label reads "… of 3". | The owner's example was "2nd of 3". Counting the target is the natural reading of a rank and matches that example for two peers. |
| F6 | When n < 4, P25, mean and P75 are stored as NULL in `core.peer_comparisons`, and `percentile_rank` is NULL; `position_label` holds what the UI shows. | The suppression is enforced in the data, not left to each screen or export. |
| F7 | Default-peer comparisons are stored for every company; custom peer sets are computed in the app by the same functions from stored metrics. | The app reads only from PostgreSQL either way, and the two paths cannot disagree. |
| F8 | The peer-group median used for the sector adjustment (D13) includes the company itself and needs at least 3 members with a value for that period. | "The company's peer-group median" read literally. With five members, excluding the company would leave a median of four and make every company's reference different. Below three values a median is not a sector signal, and those rows are not tested. |
| F9 | When MAD is zero the modified z-score uses 1.253314 × the mean absolute deviation; if that is also zero the score is undefined. | The fallback given by Iglewicz and Hoaglin. Avoids dividing by zero when more than half a sample is identical. |
| F10 | Volume is tested in logs. | Volume is right-skewed; in levels only high-volume days could ever be flagged. |
| F11 | Correlations use dates common to all 26 tickers, including the Nifty 50. | One sample for every pair. The cost is a slightly shorter sample and that a ticker's return can span two days where its own row was a placeholder. |
| F12 | Anomalies, peer comparisons and correlations are rebuilt in full on each run. | Same reasoning as E17; verified by identical fingerprints across runs. |
| F13 | Number formats live in `src/formatting.py`. | The spec asks for one formatting module; the interpretation text needs it before the app exists. |
| F14 | The bank peer group is named `banks` (was `private_banks`) and State Bank of India's industry is "Public Sector Banks". | SBI is a public-sector bank; the owner placed it with the other four banks. |
| F15 | Fiscal labels assume an April–March year for every company. Nestle India's December 2021 and 2022 year-ends are therefore labelled FY2022 and FY2023. | One convention for the whole universe. `period_end_date` is stored and is what calculations use; year-on-year comparisons look up the date exactly one year earlier, so a December year is never compared with a March year. |

## Phase 6 (Data Science Lab)

Method is in `methodology.md` section 12; results and limitations for each task are in
`docs/model_cards/`. The choices below involved judgement. Every setting was fixed before the
experiment was run and none was changed after seeing results.

| # | Assumption | Reason |
|---|---|---|
| G1 | Realized variance is the mean of squared daily returns (zero mean), and the target is that quantity over t+1 .. t+h. | The standard daily-data convention. Subtracting a sample mean over 5 days would be mostly noise. |
| G2 | Walk-forward settings: 504 days before the first test block, 63-day blocks, expanding window, refit once per block. | About two years to fit on and one quarter per fold gives 12 folds from five years of data. |
| G3 | The last h training rows before each cutoff are purged. | Their targets extend into the test block. Without the purge the model would be fitted on outcomes it is then tested on. |
| G4 | Ridge and gbm are pooled across companies, with no company identifier as a feature. | 25 short histories; pooling gives about 12,000 training rows in the first fold instead of about 440 per company. |
| G5 | Hyperparameters are fixed and not tuned: ridge alpha 1; trees depth 3, learning rate 0.05, 200 iterations, 50 samples per leaf; EWMA lambda 0.94 (RiskMetrics). | With no tuning there is no validation set to leak from and no selection to flatter the test results. The cost is that a tuned model might do better. |
| G6 | Models predict log variance and forecasts are exp(prediction + half the training residual variance). | Log variance is close to symmetric; the correction makes the forecast the mean rather than the median of the implied log-normal. |
| G7 | GARCH(1,1) with zero mean and normal errors, parameters re-estimated at each fold cutoff and held fixed within the block while the variance recursion is updated daily. | The textbook specification. Daily refitting would be more than 18,000 fits per horizon for little change. |
| G8 | "Best baseline" is chosen per horizon by pooled QLIKE, and the Diebold-Mariano test is on QLIKE. | QLIKE is robust to noise in the realized-variance proxy (Patton, 2011), which matters most at the 5-day horizon. |
| G9 | The pooled Diebold-Mariano test averages the loss difference across companies per date; Newey-West lags are h−1. | One time series respects the cross-sectional dependence between companies on a given day; h-step forecasts overlap by h−1. Per-company tests are reported alongside. |
| G10 | All forecasters are scored on the rows every one of them covers. | A fair comparison needs one sample. |
| G11 | Clustering uses only fundamentals that apply to every sector type (net margin, ROE, revenue growth); EBITDA margin and debt/equity are left out. | Otherwise banks would have to be dropped or imputed on features that mean nothing for them. |
| G12 | k is chosen by maximum silhouette; the elbow curve is reported but not used to decide. | A single stated rule. For K-means the silhouette is nearly flat between k = 3 and k = 5, which is said in the model card. |
| G13 | Cluster stability resamples trading days in 21-day blocks and holds fundamentals fixed. | Blocks preserve volatility clustering. There is only one cross-section of fundamentals to use. |
| G14 | Sharpe intervals use a moving-block bootstrap with 21-day blocks; other intervals resample independently. | Daily returns are close to serially uncorrelated but their volatility is not. |
| G15 | Multiple comparisons use Benjamini-Hochberg (false discovery rate), not Bonferroni. | Spec. With hundreds of correlation pairs Bonferroni would be needlessly strict. |
| G16 | Post-hoc sector comparisons use pairwise Mann-Whitney U with BH correction. | Dunn's test is not in SciPy; pairwise Mann-Whitney is a standard substitute. With 5 against 5 its smallest two-sided p-value is 0.0079, so effect sizes are reported for every pair. |
| G17 | A "stressed" day has the Nifty 50's trailing 21-day volatility, known at the previous close, in the top quartile of the sample. | A simple, explicit definition. The quartile uses the whole sample, so it is descriptive. |
| G18 | Isolation Forest contamination is fixed at 2% and is not matched to the number of points the rules flag. | Matching it would manufacture agreement. Its features are the rolling modified z-scores, so they are comparable across companies and leakage-free. |
| G19 | Regimes: two states are assumed; a day is turbulent when its smoothed probability exceeds 0.5. | The smallest model that separates calm from turbulent. Smoothed probabilities are the right quantity for describing history and the wrong one for real-time use, which the card states. |
| G20 | Each task keeps only its latest run in the database; a rerun replaces it. | The stored results always correspond to the current data. Each run records its seed and a hash of the price data it used. |
| G21 | The lab is a separate command (`scripts/run_ml.py`), not a step of `update_data.py`. | Refreshing data should not silently retrain models; the two are run and reviewed separately. |
| G22 | Model cards are generated by the run from its own results. | A hand-written card would go stale the first time the data changed. |

## Phase 7 (Streamlit app)

| # | Assumption | Reason |
|---|---|---|
| H1 | Seven pages: `app/Home.py` is the Overview, and `app/pages/` holds 01 Company Analysis, 02 Comparable Companies, 03 Market Analytics, 04 Risk Analytics, 05 Data Science Lab, 06 Data Quality. | The spec's file list numbers the pages 01–07 alongside a `Home.py`, which would make eight files for seven pages. Using `Home.py` as the Overview keeps seven; the last two page numbers are therefore one lower than in the spec. |
| H2 | The app reads only through `src/database/queries.py` (parameterized SQL) and imports no data-source or HTTP library. A test fails if it does, and each page is run with outbound connections blocked. | "Read from PostgreSQL only. Never call external APIs from the app." |
| H3 | Cached with `st.cache_data` for 10 minutes, and every loader is also keyed on the latest pipeline run and Data Science Lab run (re-checked every 30 seconds). | A refresh changes the key, so new data appears without waiting for the 10-minute expiry and without a manual cache clear. |
| H4 | The server is bound to `localhost` and Streamlit usage statistics are off (`.streamlit/config.toml`). | Streamlit otherwise looks up the machine's external IP when started headless, which is an outbound call, and would serve the app to the local network. |
| H5 | A missing figure shows `N/A` with the stored reason beneath it; a chart with nothing to plot is replaced by the reason. Suppressed peer statistics (n < 4) show a dash, not N/A. | N/A and "suppressed" mean different things and should not look the same. A missing value is never drawn as zero. |
| H6 | Figures carry their caveats where they appear: "translated from USD", "reporting-currency growth (USD)", "closing balance used", "latest annual figure (TTM unavailable)". | Decisions D4, D6 and D10. |
| H7 | The Comparable Companies page recomputes with `src/analytics/comps.py` when the peer set is changed, from stored metrics. | The same functions the pipeline uses for the default peer set, so the two cannot disagree. |
| H8 | Market Analytics computes returns, rolling volatility and drawdown for the chosen dates in pandas from stored prices; Risk Analytics shows the stored 1- and 3-year metrics. | A free date range cannot be precomputed. The page says the two differ and why. |
| H9 | The Data Science Lab page renders the model cards from `docs/model_cards/` on disk and shows stored results only. A test fails if app code calls a training function. | The spec asks for a link to each card and for results to be read from stored outputs. |
| H10 | The "Highest in the universe" tables on the Overview are plain rankings of stored metrics, labelled as descriptive. | The spec asks for top performers; the wording avoids any suggestion of a recommendation. |
| H11 | One neutral theme: white background, one accent colour (#1f4e79), greys for context, no emojis, gradients or decorative charts. Number formats come from `src/formatting.py`. | Spec, Section 12. |
| H12 | The app tests run against the project database and are skipped when it is unreachable or empty. | They verify the real pages on real data; a fixture database would only verify the fixture. |

## Phase 8 (exports)

How to use the files is in `excel_guide.md` and `powerbi_model.md`.

| # | Assumption | Reason |
|---|---|---|
| I1 | **Workbook formulas were verified with an independent calculation engine** (the Python `formulas` library), not by opening the files in Excel or LibreOffice. | Neither is installed on the development machine, and installing a large application was not done without asking. The engine evaluates every formula in the saved file, so "the formulas calculate, and to the right values" is tested; how the files look in Excel is not. **Flagged to the owner at the Phase 8 review.** |
| I2 | `comparable_companies.xlsx` covers the whole universe with a target cell the user changes, instead of one static sheet per target. | "Live formulas" then means something: every statistic recalculates for whichever company is chosen. |
| I3 | Peer statistics are computed from a block of "peer value" formulas beside each metric table (the value if the company is a peer and has a number, otherwise blank). | Keeps every statistic an ordinary formula (`MEDIAN`, `PERCENTILE.INC`, `MIN`, `MAX`) with no array formulas, which behave differently between Excel versions and LibreOffice. The mean uses `AVERAGEIFS` on the peer flag directly. |
| I4 | The workbook applies the same rules as the app: target excluded, N/A peers excluded, P25 / mean / P75 hidden and a rank shown when fewer than four peers have a value. | One set of rules everywhere (decisions D7 and D11). |
| I5 | Missing figures are the text "N/A"; suppressed statistics are "–". | Excel's statistical functions ignore text, so an N/A can never enter a median. A blank cell could be read as zero by a formula. |
| I6 | Capital-structure metrics have their own "Capital Structure" sheet (owner's decision, 2026-10-07; the spec's sheet list is a minimum). | Five sheets in `comparable_companies.xlsx`. |
| I7 | The Growth Analysis sheet recomputes year-on-year revenue growth with formulas and reconciles it with the stored figure. | Shows the growth rule in the workbook itself, and gives a built-in check that the export agrees with the database. |
| I8 | Percentile labels round half up (62.5 → 63) in both the app and Excel. | Python's default rounding goes to the even number and Excel's `ROUND` goes up; the two would otherwise disagree on exact halves. |
| I9 | The star schema is defined as views in the `mart` schema; the CSV files are exports of those views. | One definition serves both a direct database connection and file import. |
| I10 | `fact_valuation` is one wide row per company and valuation date; `fact_metrics` holds all other metrics in long form. | The spec lists both tables. Valuation multiples are usually shown side by side, and they are keyed on a price date as well as a period end. |
| I11 | `dim_date` has every calendar day between the first and last date in any fact, with `is_trading_day`. | Statement period ends often fall on non-trading days, and Power BI's time intelligence requires a contiguous date table. |
| I12 | `dim_sector` has an extra row, "Benchmark index", with key 0, which the Nifty 50 row in `dim_company` points to. | Every foreign key must resolve; a NULL sector key would break the relationship. |
| I13 | `fact_market_prices` includes placeholder rows, flagged; `daily_return` is NULL on them and steps over them. | The fact table mirrors the database. The documented DAX measures exclude them. |
| I14 | The export refuses to write the CSV files if a key is null, a grain is not unique or a foreign key has no match. | A broken star schema fails silently in Power BI (blank rows, wrong totals). |
| I15 | The pairwise correlation matrix is exported as a helper table, `agg_return_correlation`, outside the star schema (owner's decision, 2026-10-07). A `window_label` column is added to the owner's column list. | It is company × company, not a fact at a date. Three windows are stored, and a label is easier to filter on than a date range. |
| I16 | No `.pbix` is produced and the DAX measures are untested. | Power BI Desktop is Windows-only. `powerbi_model.md` says so. |

## Phase 9 (notebooks, documentation, final verification)

| # | Assumption | Reason |
|---|---|---|
| J1 | The fresh-clone check used a copy of the repository's tracked and untracked-but-not-ignored files in an empty directory, with a new virtual environment and its own PostgreSQL container. | It is what a clone would contain once the pending changes are committed. Nothing ignored (data, `.env`, virtual environment) was carried over. |
| J2 | In that check `.env` set `POSTGRES_PORT=5434` and `POSTGRES_CONTAINER=finsight-db-freshclone`, and the directory had a different name. | The original project was running on the same machine; the defaults would have collided with it. On a clean machine the README's defaults apply unchanged. |
| J3 | `docker-compose.yml` reads the container name from `POSTGRES_CONTAINER`, defaulting to `finsight-db`. | Lets two copies of the project run side by side; the default behaviour is unchanged. |
| J4 | `docs/data_dictionary.md` is generated from the live database catalog, the field map and the metric registry (`scripts/build_data_dictionary.py`). | A hand-written dictionary of 382 columns would drift. Only each table's one-line purpose is written by hand, and the script fails if a table has none. |
| J5 | Notebooks 05–07 read stored Data Science Lab results and do not retrain. | Same rule as the app; the notebooks then agree with the model cards by construction. |
| J6 | Notebook interpretation text was checked against each notebook's printed output after execution, and corrected where it did not match (notebook 01's statement on volatility persistence). | An interpretation written before the result is a hypothesis, not a finding. |
| J7 | Nothing was committed or pushed by the assistant. | Commits are the owner's to make. |
