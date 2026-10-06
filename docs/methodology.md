# Methodology

How FinSight turns raw source data into stored, validated figures. Sections 1–4 cover cleaning,
validation and loading; sections 5–8 the metrics; sections 9–11 comparable companies, anomalies
and correlation; section 12 the Data Science Lab.

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

### 2.4 Foreign-currency statements (Infosys)

Yahoo serves Infosys statements in USD. The statement currency is an explicit per-company setting in
`config/universe.yaml` (`statement_currency`), because Yahoo's own `financialCurrency` flag is
unreliable: it says USD for HCLTech, whose statements are in INR.

**What is stored.** Every statement row keeps the figure exactly as reported and, separately, an
INR value. The reported figure is never overwritten.

| Column | Content |
|---|---|
| `original_value`, `original_currency` | The figure as reported, in the reporting currency (USD for Infosys) |
| `value` | INR. Equal to `original_value` for INR reporters; a translation otherwise |
| `fx_rate` | INR per unit of the reporting currency used for the translation |
| `fx_rate_type` | `average` or `period_end` |
| `fx_source` | `yfinance INR=X daily close` |

**Translation rule.** `value = original_value × fx_rate`.

| Item | Rate | `fx_rate_type` |
|---|---|---|
| Income statement and cash flow (flows), including EPS | Mean of daily USD/INR closes over that fiscal period | `average` |
| Balance sheet | Last USD/INR close on or before the period-end date (at most 7 days old) | `period_end` |
| TTM figures | Each quarter is translated at its own average rate, then the four are summed | `average` |
| Share counts | Not translated | (none) |

The rate series is Yahoo `INR=X` via yfinance, ten years of daily closes, stored in `core.fx_rates`
with its retrieval timestamp. A flow is only translated when quotes exist within 7 days of both
ends of its period; otherwise the INR value is NULL and an error is logged.

**Which currency each calculation uses.**

| Calculation | Currency | Why |
|---|---|---|
| Ratios and margins: gross, EBITDA, EBIT and net margin, ROE, ROA, debt/equity, current and quick ratio, FCF margin and the rest of section 5 | Reporting currency (USD) | Currency-invariant. Computing them from the reported figures avoids mixing an average-rate numerator with a period-end-rate denominator (ROE, for example). |
| Growth: revenue, EBITDA, net income, EPS, FCF | Reporting currency (USD) | Exchange-rate movement would otherwise appear as growth. Labelled **"reporting-currency growth (USD)"**. |
| Levels shown in ₹ crore, market cap, EV, P/E, EV/EBITDA, EV/Revenue, P/B | INR (translated) | They are compared with INR prices and INR peers. |

Metric rows carry `reporting_currency` and `is_translated`. Wherever a translated figure is shown
it is marked "translated from USD": it is FinSight's translation, **not** the INR figure Infosys
publishes. Example (FY2026): revenue grew 4.57% in USD; the same revenue translated to INR grew
9.27%, the difference being the weaker rupee (average rate 84.54 → 88.34).

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

## 5. Fundamental ratios (`src/analytics/ratios.py`)

Every metric is a pure function registered in `src/analytics/registry.py` with a formula id, unit
and the sector types it applies to; the registry populates `core.formulas`. Inputs are the reported
figures in the company's reporting currency (section 2.4). A metric is NULL, with a reason, when an
input is missing, when a required base is not positive, or when it is not meaningful for the
company's sector type. Nothing is assumed to be zero.

| Metric | Formula | Applies to |
|---|---|---|
| Revenue / net income growth | (x_t − x_t−1) / x_t−1 | All |
| EBITDA / FCF growth | same | Non-financial |
| EPS | net income / adjusted shares (section 5.1) | All |
| EPS growth | (EPS_t − EPS_t−1) / EPS_t−1 | All |
| NII growth | same, on net interest income | Banks, NBFCs |
| Gross / EBITDA / EBIT margin | item / revenue | Non-financial |
| Net margin | net income / revenue | All |
| ROE | net income / average shareholders' equity | All (annual) |
| ROA | net income / average total assets | All (annual) |
| Debt / equity | total debt / shareholders' equity | Non-financial |
| Debt / assets | total debt / total assets | Non-financial |
| Net debt / EBITDA | (total debt − cash) / EBITDA | Non-financial (annual) |
| Current ratio | current assets / current liabilities | Non-financial |
| Quick ratio | (current assets − inventory) / current liabilities | Non-financial |
| FCF margin, OCF margin, capex / revenue | item / revenue | Non-financial |
| Cost-to-income | operating expenses / revenue | Banks, NBFCs (source field absent: N/A) |
| Loan / deposit | loans / deposits | Banks, NBFCs (deposits absent: N/A) |

Rules:

- **Growth** compares with the period exactly one year earlier, looked up by date (same quarter of
  the previous year for quarterly data). It is NULL when the prior value is missing, zero or
  negative: growth from a negative base is not meaningful.
- **EPS** = net income attributable to shareholders / adjusted shares (section 5.1). The source's
  reported EPS is stored for reconciliation and never used in a calculation.
- **ROE and ROA** use the average of the opening and closing balance. When the prior year-end is
  unavailable (the earliest year), the closing balance alone is used. Every row records which in
  its `method` field, `average_balance` or `closing_balance`, and the app footnotes each figure
  that used the fallback. ROE, ROA and net debt / EBITDA are computed for annual periods only.
- **Net income** everywhere is the amount attributable to shareholders of the parent, after
  minority interest (source label `Net Income`, not `Net Income Including Noncontrolling Interests`).
- **Cash** in net debt and EV is cash and short-term investments, falling back to cash and
  equivalents where the broader figure is not reported. The item used is recorded.
- **Banks**: where a metric does not apply the stored value is NULL with the reason
  "N/A (not meaningful for banks)", even if the inputs exist. Deposits are not debt.

### 5.1 EPS and the share count (`src/analytics/shares.py`)

A split or bonus issue changes the share count without changing the company, so EPS is only
comparable across years when every year's share count is on the same basis. FinSight puts every
period on the **current** basis.

1. **Split history.** Splits and bonus issues come from the `Stock Splits` column of yfinance's
   price history and are stored in `core.stock_splits`. The cumulative factor for a period is the
   product of the ratios of all events **after** its period end (a 1:1 bonus has ratio 2).
2. **Which share count.** Weighted-average diluted shares, then weighted-average basic shares,
   then period-end shares. The one used is recorded on the row (`method`: `weighted_average_diluted`,
   `weighted_average_basic` or `period_end`).
3. **Restating.** A figure that is still on the old share basis is multiplied by the cumulative
   factor. A figure the source has already restated is left alone.

Step 3 needs a test, because **the source restates some figures and not others**. It restates
period-end share counts, and usually the weighted averages too, but HDFC Bank's FY2023 and FY2024
weighted averages are still on the count before its 1:1 bonus of 26 August 2025, while FY2025 is
restated. Multiplying every pre-split figure by the factor would double-count the restated ones
(it would halve Wipro's FY2024 EPS, for example).

The test: against a reference count known to be on the current basis, a figure on the old basis is
about 1/factor of the reference and a restated one is about equal to it. The geometric midpoint,
1/√factor, separates them (0.707 for a 1:1 bonus). The reference for period-end shares is the
company's current share count; for a weighted average it is that same period's restated
period-end count. Each row stores the decision (`share_restatement`: `restated_by_finsight`,
`already_restated_at_source`, `no_split_after_period`), the factor, and the share count as
reported. A figure within 0.15 (log terms) of the midpoint is marked marginal and logged. If a
split followed a period and there is no reference, the share count is not guessed and EPS is N/A.

In the current data, 10 annual periods precede a split or bonus issue across four companies
(HDFC Bank, Wipro, Kotak Mahindra Bank, Dr. Reddy's). Two were restated by FinSight (HDFC Bank
FY2023 and FY2024); the rest were already restated at source.

| HDFC Bank | FY2023 | FY2024 | FY2025 | FY2026 |
|---|---|---|---|---|
| EPS as the source reports it | 88.68 | 44.16 | 44.82 | 45.75 |
| Growth implied by the source | | −50.2% | +1.5% | +2.1% |
| FinSight EPS, current basis | 44.34 | 43.80 | 43.97 | 45.75 |
| FinSight EPS growth | | −1.2% | +0.4% | +4.1% |

The reported EPS is kept with each EPS row (`reported_eps_diluted`, with the percentage difference)
and by the `eps_share_basis` validation check, which flags the FY2023 row.

## 6. Valuation (`src/analytics/valuation.py`)

All valuation figures are in INR.

| Figure | Formula |
|---|---|
| Market cap | price × shares outstanding |
| Enterprise value | market cap + total debt + minority interest − cash (non-financial only) |
| P/E | market cap / net income attributable to shareholders |
| P/B | market cap / shareholders' equity (excluding minority interest) |
| EV/EBITDA, EV/Revenue | EV / EBITDA, EV / revenue (non-financial only) |

- **Current multiples** (`period_type = 'ttm'`): the latest price that is not a placeholder row,
  the source's latest share count (with its as-of date), the latest balance sheet, and
  trailing-twelve-month flows. TTM is the sum of the four quarters ending at the company's latest
  reported quarter, used only when all four are present and contiguous. Otherwise the latest
  annual figure is used and the metric's inputs record `flow_basis = latest_annual`.
- **Historical multiples** (`period_type = 'annual'`): the close on or before the fiscal year end
  (at most 7 days earlier) × that year-end's share count, against that year's figures.
- **Cash** in EV (and in net debt) is cash and short-term investments, falling back to cash and
  equivalents where the broader figure is not reported; the row records which (`cash_item`).
  Liquid investments are available to repay debt, and for cash-rich companies the two differ
  several-fold.
- **A multiple is N/A when its denominator is zero or negative.** P/E is N/A when net income is
  not positive and P/B when equity is not positive; a negative multiple is never stored or shown.
  N/A values are NULL in the database, so they drop out of peer statistics rather than counting
  as zero.
- A missing minority interest is taken as none reported and recorded
  (`minority_interest_assumed_zero`). Missing debt or cash makes EV NULL.
- **Reconciliation**: each current figure is compared with the one Yahoo reports and stored in
  `core.valuation_reconciliation`. Differences above 10% are flagged and logged as warnings, not
  corrected.

## 7. Returns (`src/analytics/returns.py`)

All returns use adjusted close, so they include dividends and are adjusted for splits and bonus
issues. Placeholder rows (section 2.7) are removed first.

| Metric | Formula |
|---|---|
| Daily return | P_t / P_t−1 − 1 |
| Cumulative return | ∏(1 + r) − 1 |
| 1M, 3M, 1Y return | P(as of) / P(as of − horizon) − 1 |
| YTD return | P(as of) / P(last trading day of the previous calendar year) − 1 |
| 3Y CAGR | (P(as of) / P(as of − 3 years))^(1/3) − 1 |

The start price is the last price on or before the start date, accepted only if it is at most
7 calendar days older. If the history does not reach back that far the return is NULL.

## 8. Risk (`src/analytics/risk.py`)

Computed on daily simple returns over trailing 1-year and 3-year windows, annualized with 252
trading days. A metric needs at least 30 daily returns. These windows apply to the stored risk
metrics only: the Data Science Lab (volatility forecasting, clustering, statistical tests) uses
the full price history.

| Metric | Formula |
|---|---|
| Annualized volatility | sample standard deviation of daily returns × √252 |
| Annualized return | mean daily return × 252 |
| Sharpe ratio | (annualized return − Rf) / annualized volatility |
| Downside deviation | √(mean(min(r − 0, 0)²)) × √252 |
| Maximum drawdown | min over t of P_t / max(P_s, s ≤ t) − 1, with peak and trough dates |
| Correlation | Pearson correlation of daily returns on dates shared by every security |

- **Risk-free rate**: the configured constant, 5.2599% (91-day T-bill, 2026-09-02). Where a daily
  rate is needed it is (1 + r)^(1/252) − 1.
- **Downside deviation** uses a 0% daily target and the lower-partial-moment form: the mean is over
  all observations, with returns at or above the target contributing zero. This is the Sortino
  denominator. It reflects how often losses occur as well as their size, which the standard
  deviation of only the negative returns would not.
- **Sharpe** is NULL when volatility is zero.

## 9. Comparable companies (`src/analytics/comps.py`)

- **Peers.** By default, the other companies in the target's `peer_group` (four peers for every
  company in the current universe). The user can override the set in the app; a warning is shown
  when it mixes sector types or has fewer than two peers.
- **The target is excluded from every peer statistic.** Changing the target's own value changes
  its positioning and nothing else (this is a unit test).
- **N/A is excluded, never counted as zero.** A peer for which a metric is N/A does not enter the
  statistics, and `n` is the number of peers that did.
- **Periods:** each company's latest annual period for operating and capital-structure metrics;
  current (TTM-basis) multiples for valuation.

**What is reported depends on n**, the number of peers with a value:

| | n ≥ 4 | n < 4 |
|---|---|---|
| Statistics | n, min, P25, median, mean, P75, max | n, min, median, max only |
| Target's position | Percentile, e.g. "75th percentile" | Rank, e.g. "2nd of 3" |

With two or three peers a quartile is an interpolation between two numbers and the mean is driven
by any one of them, so they are suppressed (stored as NULL) rather than shown as if they described
a distribution. Percentiles use linear interpolation, the same as Excel's `PERCENTILE.INC`.

| Positioning figure | Definition |
|---|---|
| Percentile rank (n ≥ 4) | 100 × (peers below + 0.5 × peers equal) / n. 0 = below every peer, 100 = above every peer. A tie counts as half (mid-rank). |
| Rank (n < 4) | Position among the target and the peers with a value, highest first: 1 + the number of peers strictly above, "of" n + 1. |
| Premium to median | (target / median − 1) × 100, for multiples and ratios. NULL when the median is zero or negative. |
| Difference | target − median in the metric's own unit. For percentage metrics this is the gap in percentage points, which is reported instead of a relative premium. |

**Interpretation text** is produced from three fixed templates, one per kind of metric, filled with
stored numbers. No model writes it.

| Metric kind | Template |
|---|---|
| Valuation multiple | "{Company} trades at {x}x {metric}, {y}% above/below the peer median of {z}x (n={n})." |
| Percentage | "{Company}'s {metric} of {x}% is {y} percentage points above/below the peer median of {z}% (n={n})." |
| Ratio | "{Company}'s {metric} of {x} is above/below the peer median of {z} (n={n})." |

A sentence is skipped when the target value or the peer median is missing. "In line with" replaces
the comparison when the two are equal at display precision. Caveats are written into the sentence
they apply to: figures translated from another currency, growth measured in a reporting currency
other than the peers', and a multiple based on the latest annual figure because TTM is unavailable.
The wording is descriptive only; a unit test fails if a sentence contains a judgement word
("undervalued", "overvalued", "buy", "sell", "cheap", "expensive", "attractive" and similar).

Default-peer results for every company are stored in `core.peer_comparisons`.

## 10. Anomaly detection (`src/analytics/anomaly_detection.py`)

A flagged point is unusual relative to a reference sample. It is a prompt to check the data point
(a data error, a corporate action, a one-off item). Every message begins **"Potential data anomaly
detected"**, and nothing is removed from the data.

### 10.1 The two rules

| Rule | Definition | Flag when |
|---|---|---|
| IQR fences (Tukey) | Q1 − k × IQR and Q3 + k × IQR | The value is outside the fences: k = 1.5 for fundamentals, k = 3 for market data |
| Modified z-score (Iglewicz & Hoaglin, 1993) | M = 0.6745 × (x − median) / MAD, where MAD is the median absolute deviation from the median | \|M\| > 3.5 |

Both are robust: the centre and scale they measure against are not dragged towards the outlier
being tested. The classical z-score is not used, because an outlier inflates the standard
deviation it is judged against. (For the sample 2, 4, 4, 4, 5, 5, 7, 9 the value 9 has a classical
z of 1.87 and a modified z of 6.07.) When MAD is zero, because more than half the sample is
identical, the scale is 1.253314 × the mean absolute deviation, as Iglewicz and Hoaglin suggest;
if that is zero too the score is undefined and nothing is flagged.

**The thresholds are conventional values, fixed in `config/settings.py` (`AnomalySettings`) before
any results were examined.** They were not tuned to the number of flags they produce.

### 10.2 Market data

Daily adjusted-close returns and the log of trading volume, per ticker. Each observation is
compared with the 60 trading days **before** it (at least 30). The window is shifted by one day,
so an observation never enters its own threshold and nothing after it is used; a unit test changes
a later observation and checks that every earlier score is unchanged. The IQR multiplier is 3
(Tukey's "far out" fence) because daily returns have fat tails and the 1.5× fence flags routine
days. Placeholder rows are excluded, and volume is examined only on days with volume above zero.

### 10.3 Fundamentals: sector-adjusted residuals

Year-on-year revenue growth, change in net margin, change in EBITDA margin and change in total
debt, in the reporting currency. EBITDA margin and debt are examined for non-financials only.

1. **Adjust for the sector.** For each variable and fiscal period, the median change across the
   company's peer group is subtracted: `residual = change − peer-group median change`. The median
   is taken over all members of the group with a value for that period, the company included, and
   needs at least three of them.
2. **Pool.** The residuals for a variable are pooled across all companies and years
   (56–72 company-years).
3. **Apply the rules** of 10.1 to the pool.

A move shared by a whole sector cancels in step 1, so what is flagged is a company that moved
differently from its peers. Raw changes are **not** pooled across sectors: that would flag every
company in a sector whose economics shifted together (a unit test shows five banks flagged
before the adjustment and none after).

**A score is never computed from one company's own history.** With four annual values a classical
z-score cannot exceed (n − 1) / √n = 1.5 in absolute value, whatever the data, so no threshold near
3 could ever be reached (also a unit test).

Flagged points are stored in `core.anomalies` with the raw change, the peer-group median, the
residual, the bounds, a score (the modified z-score, or the distance beyond the fence in IQR
units) and the message. A variable with fewer than four observations is not tested. A value must
clear a fence by more than 1e-9 and a sample's spread must exceed 1e-9, so floating-point residue
is never scored when a sample is constant.

## 11. Correlation (`src/analytics/peer_analytics.py`)

Pearson correlation of daily adjusted-close returns, for trailing 1-year and 3-year windows and
the full history. Placeholder rows are dropped first, and only dates on which **every** ticker has
a return are used, so all pairs in a matrix share one sample. `core.correlations` stores both
orderings and the diagonal, with the number of observations and the date range. A window longer
than the available history is not reported. Significance testing with a multiple-comparison
correction belongs to the statistical-testing module (Phase 6).

## 12. Data Science Lab (`src/ml/`)

Run with `python scripts/run_ml.py`. It reads from PostgreSQL, uses a fixed seed (42), and stores
results in the `core.ml_*` tables and `data/processed/ml_results/`. The app displays these stored
results and never trains a model. Each task has a model card in `docs/model_cards/`, written by
the run itself from its own results, so the figures in a card cannot drift from what is stored.
The stored risk metrics of section 8 use 1- and 3-year windows; the lab uses the full price
history.

### 12.1 Rules that apply to every model

- **Time-aware validation only.** Walk-forward, expanding window (`src/ml/splits.py`). Random
  K-fold is never used on time series.
- **No look-ahead in features.** A feature at date t uses only data up to t; every window is
  trailing. Targets are the only forward-looking quantities and are built by a separate function.
- **Purged training rows.** The target at date s covers s+1 .. s+h, so the last h rows before a
  fold's cutoff are dropped from training; otherwise their targets would overlap the test block.
- **Baselines first.** Every model is compared with naive baselines on the same held-out rows.
- **Uncertainty reported.** Fold-to-fold variation, Diebold-Mariano tests, bootstrap intervals.
- **Leakage is tested, not assumed.** Unit tests alter all data after a date and check that
  features, and every forecaster's output up to that date, are unchanged.
- **If a model does not beat its baseline, the results say so.**

### 12.2 Volatility forecasting (`volatility.py`)

| | |
|---|---|
| Target | Realized variance over the next 5 and 21 trading days: the mean of squared daily returns over t+1 .. t+h |
| Baselines | `hist_21`: trailing 21-day realized variance. `ewma`: RiskMetrics, σ²ₜ = 0.94 σ²ₜ₋₁ + 0.06 r²ₜ |
| Models | `garch`: GARCH(1,1), zero mean, refit each fold, forecast variance averaged over the horizon. `ridge`: ridge regression (alpha 1) on standardized features. `gbm`: gradient-boosted trees. Ridge and gbm are pooled across companies |
| Features | Log realized variance over 1, 5, 21 and 63 days; absolute return; log volume relative to its 21-day mean; log Parkinson range variance over 5 and 21 days; log Nifty 50 realized variance over 5 and 21 days |
| Validation | 504 trading days before the first test block, then 63-day blocks; 12 folds; models refit at the start of each |
| Metrics | RMSE and MAE on annualized volatility; QLIKE on variance (RV/f − ln(RV/f) − 1); pooled, per fold and per company |
| Significance | Diebold-Mariano on QLIKE against the best baseline, Newey-West with h−1 lags, Harvey-Leybourne-Newbold correction |

Ridge and gbm predict log variance; the forecast is exp(prediction + ½ training residual
variance), the mean of a log-normal, so it is not biased low by the log. Hyperparameters are
fixed conventional values and were not tuned on any data, so the reported test results are not
optimistically selected. All forecasters are scored on one common set of rows.

### 12.3 Peer clustering (`clustering.py`)

Two views of similarity: K-means on standardized company features (volatility, beta, correlation
with the market, net margin, ROE, revenue growth) and hierarchical clustering with average linkage
on the distance 1 − correlation of daily returns. k is chosen by silhouette score over k = 2..8,
with K-means inertia reported for the elbow. Agreement with sector labels is measured by the
adjusted Rand index; stability by re-clustering 200 block-bootstrap resamples of the trading days
and the two halves of the sample. Only fundamentals that apply to every sector type are used, so
banks are clustered on the same features as everyone else. A missing fundamental is replaced by the
cross-sectional median and recorded.

### 12.4 Statistical tests (`stats_tests.py`)

| Question | Test | Reported with |
|---|---|---|
| Are returns normal? | Jarque-Bera, skewness, excess kurtosis | Benjamini-Hochberg adjusted p-values; share of days beyond 3 standard deviations |
| How precise is a Sharpe ratio? | Moving-block bootstrap (21-day blocks, 2,000 resamples) | 95% interval |
| How precise is a peer median? | Bootstrap of the peer group | 95% interval |
| Which correlations differ from zero? | Pearson t-test for all 300 pairs | Benjamini-Hochberg adjusted p-values |
| Do sectors differ? | Kruskal-Wallis, pairwise Mann-Whitney U | Epsilon-squared and rank-biserial effect sizes; adjusted pairwise p-values |
| Do correlations rise in stressed markets? | Mean pairwise correlation, top-quartile market volatility vs the rest | Block-bootstrap 95% interval for the difference |

### 12.5 Anomaly-method comparison (`anomaly_comparison.py`)

An Isolation Forest (200 trees, contamination fixed at 2% in advance) is fitted on each
company-day's rolling modified z-scores for return and volume, and compared with the IQR and
modified z-score rules of section 10 on the same observations. There are no labels, so the
comparison is overlap between methods (Jaccard), not accuracy. A sample of the cases flagged by all
three is inspected by hand in `notebooks/07_anomalies_and_regimes.ipynb`.

### 12.6 Regime detection (`regimes.py`)

A two-state Markov-switching model with a different variance per state, fitted to Nifty 50
returns, with a rolling-volatility threshold rule alongside for comparison. It reports each
regime's volatility and average pairwise correlation. **This is descriptive segmentation, not a
trading signal:** the smoothed probabilities use the whole sample and could not have been known at
the time.

### 12.7 Out of scope

Price or return-direction prediction, anything presented as a trading strategy, sentiment
scraping, and any form of stock selection.
