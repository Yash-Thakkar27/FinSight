# Limitations

Known limitations of FinSight. Items 1–6 were observed directly in the Phase 1 source
inspection on 2026-10-06 (`docs/data_source_inspection.md`); the figures quoted are from that run.

## Data source (Yahoo Finance via yfinance)

1. **Unofficial, free, delayed source.** yfinance scrapes Yahoo Finance. It has no SLA, can change
   field names without notice, and rate-limits (a plain HTTP request without the library's session
   returned HTTP 429 during setup).

2. **Short statement history.** 4–5 annual periods are returned, and the oldest annual column is
   often entirely empty: TCS balance sheet and cash flow return 5 annual columns with values in 4.
   In practice about **4 usable annual periods**, so at most 3 YoY growth observations per company.

3. **Quarterly data is patchy and has gaps.**
   - TCS quarterly income statement: 5 periods with **2025-09-30 missing** (2026-06-30, 2026-03-31,
     2025-12-31, 2025-06-30, 2025-03-31).
   - HDFC Bank quarterly income statement: only **2 periods** (2025-06-30, 2025-03-31).
   - Sun Pharma quarterly balance sheet: only 2 periods.
   - Consequence: TTM figures can only be built when the last 4 quarters are present **and
     contiguous**. Otherwise the latest annual figure is used and labelled as such.

3a. **Quarterly completeness is low.** In the 2026-10-06 data, 22.8% of applicable statement fields
    have no value, almost all of them quarterly: annual completeness is 77–100% per company,
    quarterly 41–86%. 59 company-periods fall below the 80% completeness threshold.

4. **Quarterly cash flow is mostly unavailable.** The source returns it for only 4 of the 25
   companies (TCS, Infosys, Wipro and Dr. Reddy's). Quarterly and TTM
   cash-flow metrics will be N/A for most companies; annual cash flow is used instead.

5. **Bank fields are incomplete.** For HDFC Bank the source returns `Net Interest Income` and
   `Net Loan`, but **no deposits field** and no usable operating-expense field. Loan/deposit ratio
   and cost-to-income will therefore be N/A unless other banks in the universe return the fields.
   Gross profit, EBITDA, EBIT, current assets and current liabilities are absent for banks, as expected.

6. **Statement periods are not aligned across statements.** For one company the income statement,
   balance sheet and cash flow can cover different sets of period ends (HDFC Bank: income
   FY2022–FY2026, balance sheet FY2023–FY2026).

6a. **Nestle India has a broken annual history.** It moved its year-end from December to March.
    The source has December 2021, December 2022 and March 2025, has no figures at all for the
    15-month transition year, and has no income statement yet for March 2026. Its year-on-year
    growth is N/A for every recent year, and because its latest annual period has no income data
    it is N/A in the consumer group's operating comparisons, which leaves that group with three
    usable peers for those metrics. Its fiscal labels follow the April–March convention, so the
    December year-ends appear as FY2022 and FY2023.

7. **No announcement dates.** The source gives period-end dates only. Any model feature that uses
   fundamentals applies a documented conservative publication lag instead (Phase 6).

8. **Metadata is point-in-time only.** `sharesOutstanding`, market cap and Yahoo's own multiples
   are current values with no history. Historical share counts come from period-end balance sheets.

8a. **An empty statement cannot be told apart from a swallowed source error.** yfinance returns an
    empty table both when Yahoo has no data and when an internal request fails quietly. FinSight
    records these as `empty` (unavailable from source). A later refresh that does return data will
    simply add a newer snapshot.

8b. **Infosys statements are served in USD.** Its ratios, margins and growth rates are computed in
    USD, its reporting currency, and growth is labelled "reporting-currency growth (USD)". Those
    growth rates are therefore not directly comparable with the INR growth of its peers: in FY2026
    Infosys revenue grew 4.57% in USD, and the same revenue translated to INR grew 9.27%.
    Levels in ₹ crore and all valuation multiples use FinSight's INR translation (flows at the
    period-average USD/INR rate, balances at the period-end rate, TTM quarter by quarter). These
    translated figures are close to, but **not the same as, the INR figures Infosys itself
    publishes**, and are marked "translated from USD". The FX series is Yahoo `INR=X` daily closes,
    which is an indicative market rate, not an official reference rate.

8c. **Yahoo's `financialCurrency` flag is unreliable.** It reports USD for HCLTech, whose statements
    are in INR. Statement currency is therefore set by hand per company and guarded by a scale
    check; a company added to the universe needs the same verification. Yahoo's own summary
    figures for such companies (enterprise value, revenue) may be in a different currency from its
    statements and are only used for reconciliation.

8d. **The source can return incomplete recent rows.** Fetches made around midnight IST on
    2026-10-07 returned the latest trading day (2026-10-06) with no close. For the 18 tickers
    fetched earlier that evening the row was repaired from the earlier snapshot. The 8 companies
    added on 2026-10-07 have no earlier snapshot, so their row was rejected: their latest price
    is 2026-10-05, their current multiples are as of that date, and correlations end on
    2026-10-05 for every pair (dates must be common to all tickers). The next refresh that
    returns a complete row removes the difference (`methodology.md` 2.5).

8e. **Placeholder rows on exchange holidays.** Yahoo emits flat, zero-volume rows for stocks on
    days the exchange was closed (170 rows across the universe; none for the Nifty 50). They are
    flagged, not removed. No exchange holiday calendar is used, so the flag relies on the pattern
    (zero volume, price unchanged), which would also catch a genuine no-trade day.

## Corporate actions

9. **Tata Motors is excluded.** `TATAMOTORS.NS` no longer resolves after the 2025 demerger
   ("Quote not found"). Neither successor is used:
   - `TMPV.NS` (Tata Motors Passenger Vehicles): its prices before the demerger are those of the
     **combined** company. That structural break would corrupt volatility targets, correlations
     and clustering.
   - `TMCV.NS` (now named "Tata Motors Limited", commercial vehicles): price history starts
     2025-11-12, too short for walk-forward evaluation.

   Bajaj Auto (`BAJAJ-AUTO.NS`) takes its place in the auto peer group. It makes two- and
   three-wheelers, while M&M and Maruti Suzuki are mainly four-wheeler makers, so the auto peer
   group is less homogeneous than the others.

10. **Adjusted close** depends on Yahoo's split, bonus and dividend adjustments, which are taken as given.

## Valuation

10b. **TTM figures are often unavailable.** Because of gaps in the source's quarterly history
     (items 3 and 4), current P/E falls back to the latest annual net income for 17 of 25
     companies. A fallback multiple can include one-off items from the last fiscal year that a
     true TTM figure would have rolled past.

10c. **Calculated multiples differ from Yahoo's for definitional reasons.** Of 150 figures
     compared on 2026-10-06 data, 21 differ by more than 10% and are flagged:
     - EV/EBITDA is 12–32% lower than Yahoo's for 8 companies whose enterprise values agree with
       Yahoo's closely, so the gap is the EBITDA figure: Yahoo's summary EBITDA is narrower than
       the statement EBITDA used here, and some comparisons are annual-versus-TTM.
     - Infosys and HCLTech show about −99% on EV/EBITDA and EV/Revenue because Yahoo divides an
       INR enterprise value by USD statement figures.
     - HDFC Bank P/B (1.34 vs 1.81) reflects a different book-value basis at source.
     - Hindustan Unilever, Cipla, M&M and Nestle India differ on P/E or P/B because of the annual
       fallback (10b); for Nestle India the latest annual figures are a year old (item 6a).

     Enterprise value itself is within 5% of Yahoo's for 18 of the 20 non-financials. These
     differences are reported, not corrected.

10d. **The source restates share data inconsistently after splits and bonus issues.** HDFC Bank's
     reported EPS (FY2023) and weighted-average shares (FY2023, FY2024) are on the share count
     before its 1:1 bonus of August 2025, while its later years and all of Wipro, Kotak Mahindra
     Bank and Dr. Reddy's are restated. FinSight detects the basis of each figure with a
     threshold test (`methodology.md` 5.1) rather than knowing it from the source. The test is
     unambiguous in the current data, but it is a heuristic: a company whose share count changed
     by close to the square root of its split factor for other reasons could be misclassified,
     and such cases are marked marginal for review. Split history covers the 5-year price window
     only.

10e. **Bank-specific ratios are unavailable.** Cost-to-income and loan/deposit are N/A for all four
     banks because the source provides neither operating expenses nor deposits. NII growth is
     available.

## Comparable companies and anomalies

10f. **Peer statistics rest on four companies at most.** Each company has 4 peers. A median of
     four numbers and quartiles interpolated between them describe this small universe; they are
     not estimates for a sector. When a metric is N/A for a peer, n falls to 3 and only min,
     median and max are shown, with a rank instead of a percentile. This happens for 44 of the
     375 stored comparisons, mainly in the consumer group (item 6a).

10g. **Peers are not on identical bases.** Within one peer set, some current multiples use TTM
     flows and others the latest annual figure (10b), Infosys is translated from USD, and its
     growth is in USD while its peers' is in INR. The generated sentences say so where it applies.

10h. **Anomaly flags are statistical, not diagnostic.** On daily returns, 618 of about 31,270
     observations (2.0%) have a modified z-score beyond 3.5 against their prior 60 days, and 223
     (0.7%) fall outside the 3× IQR fences. Under a normal distribution far fewer would: the gap
     is fat tails, not data errors. For fundamentals, measuring each company against its own peer
     group removes sector-wide moves but leaves a tighter distribution, so it flags more points
     than pooling raw changes did (27 company-year points against 21), most of them one-off items.
     Peer-group medians rest on four or five companies. A flag is a reason to look, nothing more.

10i. **Correlation is not adjusted for non-synchronous effects or tested for significance here.**
     Significance testing with a multiple-comparison correction is part of Phase 6.

## Data Science Lab

Each model card lists its own limitations. The ones that apply across the lab:

10j. **One sample, one market.** About five years of prices for 25 large Indian companies, with a
     three-year test period. Model rankings, cluster memberships and regime statistics describe
     that sample and may not carry to another period or a broader universe.

10k. **Volatility is only modestly persistent here.** Absolute daily returns have an average
     lag-1 autocorrelation of 0.10, and month-to-month realized volatility 0.22. The models'
     gains over the baselines are real and statistically significant when pooled, but they are
     significant for only about half the companies individually.

10l. **No hyperparameter tuning.** All model settings are fixed conventional values. That keeps
     the test results honest but probably leaves accuracy on the table.

10m. **Small cross-sections.** Sector tests compare 5 companies with 5; clustering has 25 points.
     A non-significant sector difference is not evidence of no difference, and one company moving
     cluster changes the adjusted Rand index noticeably.

10n. **The universe was chosen by sector.** Five well-known companies per sector is a tidy design.
     The close match between return-based clusters and sectors is partly a property of that
     selection.

10o. **Descriptive models use the whole sample.** The Isolation Forest, the regime model and the
     stress threshold are fitted on all the data. They describe history; they are not real-time
     rules, and the regime labels are not a trading signal.

10p. **No accuracy for unsupervised tasks.** Anomaly flags and regimes have no ground truth, so
     only agreement between methods can be reported.

## Methodology simplifications

10a. **Constant risk-free rate.** One 91-day T-bill yield (5.2599%, as of 2026-09-02) is applied to
     the whole 5-year window. Indian short rates moved materially over that period, so a
     time-varying T-bill series would be more precise for Sharpe ratios and excess returns,
     especially for the earlier years.

## Scope

11. **Single currency and market.** INR, NSE-listed companies only.
12. **Small universe.** 25 companies in 5 sectors, five per peer group. Peer statistics (with the
    target excluded) rest on at most 4 observations and are reported with `n`.
13. **Not investment advice.** FinSight presents objective comparisons only.
