# FinSight: final report

Figures are from the data retrieved on 2026-10-06 and the runs recorded in this repository.
"Verified" means the thing was executed and its result checked, and says how.

## 1. Setup

**macOS** (verified from a fresh copy of the repository, section 3):

```bash
git clone https://github.com/Yash-Thakkar27/FinSight.git
cd FinSight
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # edit .env and set POSTGRES_PASSWORD
open -a Docker                  # if Docker Desktop is not running
docker compose up -d            # PostgreSQL 16 on localhost:5433
```

**Windows** (PowerShell; written, not verified):

```powershell
git clone https://github.com/Yash-Thakkar27/FinSight.git
cd FinSight
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env          # edit .env and set POSTGRES_PASSWORD
docker compose up -d
```

Requirements: Python 3.11+ (verified on 3.13), Docker Desktop, git, internet access for the first
data fetch. PostgreSQL through Homebrew instead of Docker is described in the README and is not
verified.

## 2. Commands

| Purpose | Command |
|---|---|
| Initialize the database | `python scripts/init_db.py` (`--reset` drops everything first) |
| Refresh data (fetch → clean → validate → load → metrics → exports) | `python scripts/update_data.py` |
| Rebuild from raw snapshots, offline | `python scripts/update_data.py --skip-fetch` |
| Fetch selected tickers only | `python scripts/update_data.py --tickers TCS.NS INFY.NS` |
| Run the Data Science Lab and regenerate model cards | `python scripts/run_ml.py` |
| Run the app | `streamlit run app/Home.py` → http://localhost:8501 |
| Run tests | `pytest` (with coverage: `pytest --cov=src --cov=app`) |
| Lint | `ruff check .` |
| Generate exports | part of `update_data.py`; files appear in `data/exports/excel/` and `data/exports/powerbi/` |
| Regenerate the data dictionary | `python scripts/build_data_dictionary.py` |

## 3. Features: verified or partial

| Feature | Status | How it was verified |
|---|---|---|
| Fresh-clone setup following the README | **Verified** (macOS) | Repository contents copied to an empty directory; new virtual environment; its own PostgreSQL container; `init_db` → `update_data` (live fetch, 161 s) → `run_ml` → `pytest` (200 passed, 0 skipped) → app served all 7 routes. Two `.env` values (`POSTGRES_PORT`, `POSTGRES_CONTAINER`) differed from the defaults only because the original project was running on the same machine. |
| Configurable universe | **Verified** | Universe expanded from 17 to 25 companies by editing `config/universe.yaml` alone; tests check no ticker is hard-coded in `src/` or `app/`. |
| Ingestion with immutable raw snapshots | **Verified** | 181 datasets saved, 21 empty at source, 0 failed in the fresh run; files are read-only; retry, rate-limit and failed-ticker paths tested offline. |
| Cleaning, with missing-value classification | **Verified** | Idempotent (identical fingerprints across reruns); "value XOR reason" enforced by a database constraint; unit tests with hand-calculated values. |
| USD reporter handling (Infosys) | **Verified** | Translation tests (average-rate flow, period-end balance, TTM quarter by quarter, margin invariance); stored USD values never overwritten. |
| Validation and data-quality report | **Verified** | 24 checks logged per run; pass rate computed (99.98% on the project data, 100.00% on the fresh fetch); it caught a real source glitch (missing closes). |
| PostgreSQL schema (staging → core → mart) | **Verified** | 32 tables and 8 views created from `sql/schema.sql`; a test compares the SQLAlchemy models with the live schema. |
| Ratio engine with sector applicability | **Verified** | 42 registered metrics; 0 stored values for a metric outside its applicable sector types; 0 rows with neither a value nor a reason. |
| EPS on a consistent share basis | **Verified** | HDFC Bank bonus test: FY2023→FY2024 EPS growth −1.2% instead of the source's −50.2%; Wipro test that already-restated figures are not restated again. |
| Valuation with reconciliation | **Verified** | Enterprise value within 5% of the source for 18 of 20 non-financials; 21 of 150 figures flagged (definitional differences, documented). |
| Comparable companies | **Verified** | Unit test that the target's value cannot move its peer statistics; 0 stored rows with the target in its own peer set; sentences generated from stored numbers and tested for judgement words. |
| Returns and risk | **Verified** | Hand-calculated tests for returns, volatility, Sharpe, downside deviation and drawdown with a known peak and trough. |
| Anomaly detection | **Verified** | Hand-calculated IQR and modified z-score tests; look-ahead test on rolling windows; sector-adjustment test (five banks flagged before, none after). |
| Volatility forecasting | **Verified** | 12 walk-forward folds; end-to-end leakage test (alter all data after a date: every forecaster's output up to it is unchanged); results reproducible (identical fingerprints); the fresh fetch reproduced the result (QLIKE −11.7% and −26.4%). |
| Peer clustering | **Verified** | Recovers planted groups on synthetic data; reproducible with a fixed seed; stability by bootstrap. |
| Statistical tests | **Verified** | Each test checked on synthetic data with a known answer; bootstrap interval contains the point estimate; Benjamini–Hochberg by hand. |
| Regime detection, Isolation Forest comparison | **Verified** | Finds planted regimes; methods agree on a planted spike. Descriptive only; no accuracy can be claimed (no labels). |
| Model cards | **Verified** | Generated by the run from its own results; a test checks every card has the required sections and no placeholder values. |
| Streamlit app (7 pages) | **Verified, except appearance** | Each page run headlessly with outbound connections blocked: no errors, footer present, no forbidden wording; bank N/A, USD flags and custom peer sets tested. **Not looked at in a browser.** |
| Excel exports | **Partial** | Formulas calculated with an independent engine and matched to the pipeline (every Peer Statistics cell for several targets; growth reconciles to 6e-16). **Not opened in Excel or LibreOffice**; appearance unchecked. |
| Power BI | **Partial** | Star schema exported and validated (unique grains, no orphan keys, row counts equal the views). **No .pbix; DAX measures written but never run.** |
| Notebooks (7) | **Verified** | All executed against the database with no errors; printed results checked against the written interpretation (one was corrected). |
| Windows setup, Homebrew PostgreSQL | **Not verified** | Written only. |
| Tier 3 (health score, MLflow, beta and tracking error) | **Not built** | Optional in the spec. Docker Compose (also Tier 3) is used for PostgreSQL. |

Tests: 202 passing, 97% coverage of `src/` (96% with `app/`), no network calls.

## 4. Limitations and deviations from the spec

Limitations are listed with evidence in `docs/limitations.md`. Deviations from the master prompt:

| Deviation | Reason |
|---|---|
| Tata Motors replaced by Bajaj Auto | Owner's decision: the 2025 demerger leaves no ticker with a usable five-year history. |
| Universe is 25 companies, not about 17 | Owner's decision, so every peer group has four peers. |
| Database on host port 5433 | Port 5432 was in use on the development machine. |
| Pages numbered 01–06 beside `Home.py` | `Home.py` is the Overview; the spec's 01–07 plus a home page would be eight files. |
| Risk windows are 1 and 3 years, not a single window | A 5-year window sits on the edge of the price history. Approved by the owner. |
| Downside deviation in the Sortino form | The spec allowed either definition. Approved by the owner. |
| EPS from net income ÷ adjusted shares, not the source's EPS | The source's EPS history is not consistently restated. |
| Modified z-score instead of the classical z-score | Owner's decision (robust to the outliers being tested). |
| Fundamental anomalies on sector-adjusted residuals | Owner's decision. |
| Small peer groups show min / median / max and a rank | Owner's decision. |
| Extra tables and columns beyond the spec's minimum | FX rates, splits, reconciliation, peer comparisons, anomalies, correlations, `ml_*` results; documented in `assumptions.md`. |
| The Data Science Lab is a separate command | So a data refresh never silently retrains a model. |
| Gradient boosting uses scikit-learn, not LightGBM | LightGBM was optional; one fewer dependency. |
| Regimes use a Markov-switching model from statsmodels | The spec allowed an HMM or a threshold model; a threshold rule is reported alongside. |
| `comparable_companies.xlsx` has five sheets | Owner's decision: capital structure on its own sheet. |
| `agg_return_correlation` helper table for Power BI | Owner's decision. |
| Excel formulas verified by an independent engine, not in Excel/LibreOffice | Neither is installed on the development machine. |
| No experiment tracking, health score, beta or tracking error | Tier 3, optional. |

## 5. Assumptions I1–I16 that change a displayed number or affect a resume claim

| # | Effect |
|---|---|
| I1 | **Resume claim.** The workbooks' formulas were verified with an independent calculation engine, not in Excel. Do not say "tested in Excel". |
| I4 | **Displayed numbers.** With fewer than four peers that have a value, the workbook shows "–" for P25, mean and P75 and a rank instead of a percentile (44 of 375 default comparisons, all in the consumer group). |
| I5 | **Displayed values.** Missing figures show the text "N/A" and suppressed statistics "–"; neither is ever a zero, so medians and averages exclude them. |
| I8 | **Displayed number.** Percentile labels round halves up, so 62.5 shows as "63rd" in the app and "percentile 63" in Excel (it was "62nd" in the app before). |
| I12 | **Displayed number in Power BI.** `dim_sector` has six rows (five sectors plus "Benchmark index"); a sector count must filter out the index or it shows 6. |
| I13 | **Displayed numbers in Power BI.** `fact_market_prices` includes placeholder rows; a measure that does not exclude `is_stale_quote` will understate volatility and average volume. The documented measures exclude them. |
| I15 | **Availability.** The correlation matrix is outside the star schema, in the helper table `agg_return_correlation`. |
| I16 | **Resume claim.** No Power BI report exists and the DAX measures have not been run. Say "Power BI-ready star schema and documented DAX measures", never a built dashboard. |

I2, I3, I6, I7, I9, I10, I11 and I14 affect layout, structure or checks, not any displayed number
or claim.

## 6. Screenshots to capture for the README

Save under `docs/screenshots/` (the README lists the file names).

1. **Overview**: universe cards, rankings, data-quality summary.
2. **Company Analysis, HDFC Bank**: N/A cards captioned "not meaningful for banks".
3. **Comparable Companies, TCS**: valuation table with peer-statistic rows, positioning chart,
   generated sentences.
4. **Risk Analytics**: risk table and correlation heatmap.
5. **Data Science Lab, Volatility forecasting**: forecast against realized, and the
   model-versus-baseline table with fold variation.
6. **Data Science Lab, Peer clustering**: cluster map and sector cross-tab.

Optional seventh: **Data Quality** (run summary and completeness by company).

## 7. GitHub description and topics

**Description:** End-to-end analytics platform for Indian equities: validated data pipeline,
comparable-company analysis, and leakage-free volatility forecasting with walk-forward evaluation
(Python, PostgreSQL, Streamlit).

**Topics:** `data-science` `python` `postgresql` `streamlit` `time-series` `volatility-forecasting`
`walk-forward-validation` `garch` `scikit-learn` `clustering` `statistical-testing`
`data-engineering` `data-quality` `financial-analysis` `indian-stock-market` `nse` `pandas`
`sqlalchemy` `plotly` `power-bi`

## 8. Resume bullets (data science role)

Every figure is from a recorded run.

- Built an end-to-end analytics platform for 25 NSE-listed companies (Python, PostgreSQL,
  Streamlit): an idempotent pipeline from immutable raw snapshots through cleaning, validation and
  a three-layer schema, processing 42,000+ records per run with 24 automated data-quality checks
  and a 99.98% pass rate.
- Forecast 5- and 21-day realized volatility with ridge regression, GARCH(1,1) and gradient
  boosting, evaluated by 12-fold walk-forward validation with purged training windows; the best
  model reduced QLIKE loss against an EWMA baseline by 11.7% (5-day) and 26.4% (21-day), beating
  it in all 12 folds (Diebold–Mariano p < 0.001).
- Prevented look-ahead bias by design and by test: trailing-window features, purged training
  rows, untuned hyperparameters, and an end-to-end test that alters all future data and confirms
  every forecast is unchanged.
- Tested whether sector labels are valid peer groups using hierarchical clustering on return
  correlations (adjusted Rand index 0.95 against sectors; bootstrap stability 0.93) versus K-means
  on fundamentals (0.41).
- Quantified uncertainty with block-bootstrap confidence intervals, Kruskal–Wallis tests with
  effect sizes and Benjamini–Hochberg correction; showed that no stock's Sharpe ratio was
  statistically distinguishable from zero over five years.
- Engineered a metric registry of 42 formulas with sector-applicability rules (bank ratios return
  "N/A", never a number), reporting-currency handling for a USD filer, and split-adjusted EPS that
  removed a spurious 50% EPS drop caused by inconsistent source data.
- Wrote 200+ automated tests (97% coverage) with hand-calculated expected values, running without
  network access; delivered Excel workbooks with live formulas and a Power BI-ready star schema
  with documented DAX measures.

If a bullet must be shortened, keep the baseline comparison and the validation scheme: a modest
model evaluated honestly is the point of the project.

## 9. Fifteen likely interview questions

**1. Why forecast volatility and not prices or returns?**
Returns in this data have essentially no autocorrelation (average lag-1 of +0.003; notebook 01),
so there is nothing reliable to predict, and a price-prediction model would mostly demonstrate
overfitting. Volatility is persistent (absolute returns +0.10 at lag 1, monthly realized
volatility +0.22), it is directly useful for risk, and it can be evaluated honestly against
well-known baselines.

**2. How did you validate the models? Why not K-fold?**
Walk-forward with an expanding window (`src/ml/splits.py`): 504 trading days of initial training,
then 12 quarterly test blocks, models refit at the start of each. Random K-fold trains on the
future and tests on the past, and with overlapping volatility targets neighbouring rows are
nearly duplicates, so it would look far better than reality.

**3. What is "purging" and why was it needed?**
The target at date s is realized variance over s+1 to s+h. A training row from the last h days
before the cutoff has a target that reaches into the test block. Those rows are dropped
(`walk_forward_folds` trains on dates up to cutoff − h). Without it the model is fitted on
outcomes it is then tested on.

**4. How do you know there is no leakage?**
Three layers. By construction: every feature window is trailing, and scaling is fitted on
training rows only. By test: `test_features_at_t_do_not_change_when_later_data_is_altered`. End to
end: `test_no_forecast_changes_when_data_after_the_forecast_date_is_altered` runs the whole
experiment twice, the second time with all data after a date replaced, and asserts every
forecaster's output up to that date is identical, including GARCH and the pooled models.

**5. Why these baselines, and what if the model had not beaten them?**
Trailing 21-day volatility and RiskMetrics EWMA (λ = 0.94) are what a practitioner would use with
no model. Every forecaster is scored on the same rows. The comparison table and model card are
generated from the results, with a sentence that reads "does not beat" when that is the case; the
resume bullet would then have been about the evaluation framework.

**6. The improvement is 26% at 21 days. Is that too good?**
It is against EWMA, which is built as a one-day-ahead estimate and is noisy as a forecast of a
month. The gain in RMSE is 12%. It holds in all 12 folds, the leakage test passes, and a fresh
data fetch reproduced it (−26.4%). But it is significant for only 11 of 25 companies individually,
so the claim is about the pooled panel.

**7. Why did ridge beat gradient boosting?**
The relationship is close to linear in logs (it is essentially a HAR-style model), there are only
ten features, and the signal-to-noise ratio is low, so the extra flexibility mostly fits noise.
Neither was tuned, which keeps the test honest; a tuned GBM might close the gap.

**8. Why QLIKE, and how did you test significance?**
Realized variance from daily returns is a noisy proxy for true variance; QLIKE is robust to that
noise (Patton, 2011) and penalizes under-prediction more, which is what matters for risk. For
significance, a Diebold–Mariano test on the loss difference averaged across companies per date,
with Newey–West lags of h − 1 because h-day forecasts made daily overlap, and the
Harvey–Leybourne–Newbold small-sample correction (`src/ml/evaluation.py`).

**9. Returns have fat tails. Where does that show up in your design?**
Jarque–Bera rejects normality for all 26 series, with 1.23% of days beyond three standard
deviations against 0.27% under normality. So anomaly detection uses robust statistics (IQR
fences, modified z-score on median and MAD) with a 3×IQR fence for market data; the app says
volatility understates tail risk; and the Sharpe intervals are bootstrapped, not derived from a
normal assumption.

**10. You ran hundreds of correlation tests. How did you handle multiple comparisons?**
Benjamini–Hochberg on the 300 pairwise p-values. All 300 remain significant, which with 1,200
observations says only that the correlations are not zero, so the report focuses on their size.
The same correction is applied to the post-hoc sector comparisons, where with five companies
against five the smallest possible Mann–Whitney p-value is 0.0079, so effect sizes are reported
for every pair.

**11. How do you know the clusters mean anything?**
Three checks: the adjusted Rand index against sector labels (0.95 for return-based hierarchical
clustering, 0.41 for K-means on features), stability under 200 block-bootstrap resamples (0.93
versus 0.72) and across the two halves of the sample, and the silhouette score (0.27–0.31, weak
to moderate). I also state the caveat that the universe was built as five companies per sector,
which flatters the agreement.

**12. What was the hardest data problem?**
Three that would each have silently corrupted results. Infosys is served in USD while the source's
own currency flag is wrong for HCLTech, so currency is set per company and guarded by a scale
check. The source restates share counts inconsistently after bonus issues, which made HDFC Bank's
EPS appear to halve; each share figure is tested against a reference before restating. And a
fetch returned the latest day with no close for every ticker, which the validation caught and the
pipeline now repairs from an earlier snapshot or rejects.

**13. How do you handle missing data? Why not fill with zero or the mean?**
A missing financial value is stored as NULL with one of three reasons (not applicable, unavailable
from source, failed retrieval), enforced by a database constraint. A zero EBITDA and an unknown
EBITDA are different facts, and zero would flow into margins, medians and models. Peer statistics
exclude N/A peers and report n. The one place a value is imputed (a missing fundamental in
clustering, with the cross-sectional median) is recorded in the run's parameters.

**14. Why is a bank's EBITDA margin "N/A" if the source gives an EBITDA number?**
Because the metric is not meaningful for a bank: interest is its cost of goods, and deposits are
not debt. Each metric declares its applicable sector types in the registry; the engine stores
NULL with the reason "N/A (not meaningful for banks)" regardless of inputs, and a query confirms
no value exists for a metric outside its applicable types.

**15. What would you do next, and what are the main weaknesses?**
Weaknesses: one market and one three-year test period; a free data source with short, patchy
statement history; four peers per company; no hyperparameter tuning; the Excel files and the Power
BI measures have not been checked in those applications. Next: a longer history from a better
source, tuning inside the walk-forward loop with a nested validation split, a time-varying
risk-free rate, intraday data for a less noisy volatility target, and announcement dates so
fundamentals can enter the forecasting features without look-ahead.
