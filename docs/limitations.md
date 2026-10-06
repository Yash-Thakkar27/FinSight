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

4. **Quarterly cash flow is mostly unavailable.** In the 2026-10-06 ingestion run it was empty for
   **13 of 17 companies** (returned only for TCS, Infosys, Wipro and Dr. Reddy's). Quarterly and TTM
   cash-flow metrics will be N/A for most companies; annual cash flow is used instead.

5. **Bank fields are incomplete.** For HDFC Bank the source returns `Net Interest Income` and
   `Net Loan`, but **no deposits field** and no usable operating-expense field. Loan/deposit ratio
   and cost-to-income will therefore be N/A unless other banks in the universe return the fields.
   Gross profit, EBITDA, EBIT, current assets and current liabilities are absent for banks, as expected.

6. **Statement periods are not aligned across statements.** For one company the income statement,
   balance sheet and cash flow can cover different sets of period ends (HDFC Bank: income
   FY2022–FY2026, balance sheet FY2023–FY2026).

7. **No announcement dates.** The source gives period-end dates only. Any model feature that uses
   fundamentals applies a documented conservative publication lag instead (Phase 6).

8. **Metadata is point-in-time only.** `sharesOutstanding`, market cap and Yahoo's own multiples
   are current values with no history. Historical share counts come from period-end balance sheets.

8a. **An empty statement cannot be told apart from a swallowed source error.** yfinance returns an
    empty table both when Yahoo has no data and when an internal request fails quietly. FinSight
    records these as `empty` (unavailable from source). A later refresh that does return data will
    simply add a newer snapshot.

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

## Methodology simplifications

10a. **Constant risk-free rate.** One 91-day T-bill yield (5.2599%, as of 2026-09-02) is applied to
     the whole 5-year window. Indian short rates moved materially over that period, so a
     time-varying T-bill series would be more precise for Sharpe ratios and excess returns,
     especially for the earlier years.

## Scope

11. **Single currency and market.** INR, NSE-listed companies only.
12. **Small universe.** 17 companies in 5 sectors. Peer groups have 3–4 members, so peer statistics
    (with the target excluded) rest on 2–3 observations and are reported with `n`.
13. **Not investment advice.** FinSight presents objective comparisons only.
