# Excel exports guide

Three workbooks are generated into `data/exports/excel/` (not kept in git) by `python scripts/update_data.py`
(step 7 of the pipeline; `--skip-fetch` regenerates them without any network access).
They are built with openpyxl from the PostgreSQL database.

| Workbook | Sheets |
|---|---|
| `financial_summary.xlsx` | Company Overview, Financial Ratios, Growth Analysis, Valuation |
| `comparable_companies.xlsx` | Peer Set, Operating Metrics, Capital Structure, Valuation Multiples, Peer Statistics |
| `market_analysis.xlsx` | Returns, Risk, Correlation |

## Conventions on every sheet

- **Rows 1–5 are a header:** title, source, as-of date, units, and the disclaimer with any note
  that applies to the sheet.
- **Yellow cells are inputs** you may change. **Grey cells are formulas.** Everything else is a
  figure exported from the database.
- **`N/A` is text, not zero.** It means the metric is not meaningful for that company's sector
  type, or the source lacks an input. Excel's statistical functions ignore text, so an N/A never
  enters a median or an average.
- **`–`** in a peer-statistic cell means the statistic is not reported because fewer than four
  peers have a value (see below). It is different from N/A.
- **Shading** (blue above a median, grey below) marks position only.
- Lookups use `INDEX`/`MATCH`, not `XLOOKUP`, so the files work in Excel 2010 onward and in
  LibreOffice Calc.

## `comparable_companies.xlsx`: change the target, everything recalculates

1. On **Peer Set**, cell `B7` (yellow) holds the target's ticker. Pick another from the
   drop-down, or type any ticker listed in column A.
2. `B8` finds the target's peer group with `INDEX`/`MATCH`. Column F marks each company as a peer
   with `=AND(peer group = target's group, ticker <> target)`, so **the target is never its own
   peer**.
3. On **Operating Metrics**, **Capital Structure** and **Valuation Multiples**, the block to the right of the data
   repeats each value only where the company is a peer and the value is a number:
   `=IF(AND('Peer Set'!F12, ISNUMBER(D12)), D12, "")`.
4. **Peer Statistics** summarizes those blocks:

| Column | Formula | Rule |
|---|---|---|
| Target | `INDEX(values, MATCH(target, tickers, 0))` | The target's own figure |
| n | `COUNT(peer values)` | Peers that have a value; N/A peers are not counted |
| Min, Median, Max | `MIN`, `MEDIAN`, `MAX` of the peer values | Shown whenever n ≥ 1 |
| P25, P75 | `PERCENTILE.INC(peer values, 0.25 / 0.75)` | Only when n ≥ 4, otherwise `–` |
| Mean | `AVERAGEIFS(values, is-peer flags, TRUE)` | Only when n ≥ 4, otherwise `–` |
| Target vs median | Percentages: `target − median` (percentage points). Multiples and ratios: `target ÷ median − 1`, only when the median is positive | |
| Position | n ≥ 4: percentile, `100 × (peers below + ½ × peers equal) ÷ n`. n < 4: rank among the target and its peers, highest first | |

These are the same rules as the app and the database (`docs/methodology.md` section 9).

## `financial_summary.xlsx`

- **Company Overview:** type a ticker in the yellow cell `B7` and the grey cells return its
  company, sector, revenue and market cap by `INDEX`/`MATCH`.
- **Financial Ratios:** below the table, sector averages use
  `AVERAGEIFS(metric column, sector column, sector)` and the last row is the universe `MEDIAN`.
- **Growth Analysis:** revenue by fiscal year in the reporting currency, with year-on-year growth
  computed **in the workbook**: `=IF(prior > 0, current ÷ prior − 1, "N/A")`. A missing year
  gives N/A rather than growth across the gap. The column "Formula minus stored" subtracts the
  pipeline's stored figure and should read zero in every row.
- **Valuation:** current multiples with the earnings basis (trailing twelve months or latest
  annual figure) and a note where figures are translated from another currency.

## `market_analysis.xlsx`

- **Returns:** stored trailing returns, a live `MEDIAN` row, and "1Y return minus benchmark",
  which looks up the benchmark's row with `INDEX`/`MATCH`.
- **Risk:** volatility, downside deviation, Sharpe and maximum drawdown for trailing 1 and 3
  years, with the drawdown's peak and trough dates and a live `MEDIAN` row.
- **Correlation:** the trailing 3-year correlation matrix. The last column averages each row
  excluding the 1.00 on the diagonal: `=(SUM(row) − 1) ÷ (COUNT(row) − 1)`.

## Adding a PivotTable (manual)

openpyxl cannot create PivotTables, so none is included. To add one in Excel:

1. Open `comparable_companies.xlsx` and go to **Operating Metrics**.
2. Select the table: click cell `A11`, then drag to the last company row and the last metric
   column (stop before the blank column that separates the peer-value block).
3. **Insert → PivotTable → New Worksheet → OK.**
4. Drag **Peer group** to *Rows*.
5. Drag a metric, for example **EBITDA margin**, to *Values*. Click it → **Value Field
   Settings → Average** (Excel defaults to Count because the column contains `N/A` text;
   Average ignores the text). Set the number format to Percentage.
6. Optionally drag **Ticker** under **Peer group** in *Rows* to list the companies in each group.

For a pivot on prices or statements, use the Power BI CSV files in `data/exports/powerbi/`
(`fact_market_prices.csv`, `fact_financials.csv`): **Data → From Text/CSV**, load, then insert a
PivotTable on the loaded table.

In LibreOffice Calc the equivalent is **Insert → Pivot Table**.

## How the formulas were verified

Neither Excel nor LibreOffice was available on the development machine, so the workbooks were not
opened in either. Their formulas were instead calculated with an independent spreadsheet engine
(the Python `formulas` library) and compared with the pipeline's own figures:

- every Peer Statistics cell, for several targets, against `src/analytics/comps.py` and the
  stored `core.peer_comparisons`;
- the growth formulas against the stored growth rates;
- sector averages, medians and the correlation averages against hand-calculated values.

See `tests/test_exports.py`. Open the files in Excel or LibreOffice before relying on their
appearance: number formats, column widths and conditional formatting were not checked visually.
