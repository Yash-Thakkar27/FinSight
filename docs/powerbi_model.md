# Power BI model

`python scripts/update_data.py` writes a star schema to `data/exports/powerbi/` as seven CSV
files. The same tables exist as views in the PostgreSQL `mart` schema (`sql/schema.sql`), so Power
BI can also connect to the database directly.

**Power BI Desktop runs on Windows only. No `.pbix` file is produced by this project.** This
document is the specification for building the report by hand from the CSV files.

## 1. Tables

| Table | Grain (one row per) | Rows (2026-10-06 data) |
|---|---|---|
| `dim_company` | company or index | 26 |
| `dim_sector` | sector, plus "Benchmark index" | 6 |
| `dim_date` | calendar day, from the first to the last date in any fact | 1,827 |
| `fact_market_prices` | company × trading date | 32,243 |
| `fact_financials` | company × period end × period type × statement × line item | 7,347 |
| `fact_metrics` | company × date × period type × metric | 5,384 |
| `fact_valuation` | company × price date × period end × basis | 127 |

Surrogate keys: `company_key` and `sector_key` are database-generated integers; `date_key` is the
date as an integer `YYYYMMDD`.

### Columns

- **dim_company:** `company_key`, `ticker`, `company_name`, `entity_type` (company / index),
  `sector_key`, `industry_name`, `peer_group`, `sector_type`, `exchange`, `country`,
  `reporting_currency`
- **dim_sector:** `sector_key`, `sector_name`
- **dim_date:** `date_key`, `date`, `year`, `quarter`, `month`, `month_name`, `year_month`,
  `day_of_week`, `fiscal_year`, `fiscal_year_label` (FY2026), `fiscal_quarter` (1–4, Q1 =
  Apr–Jun), `fiscal_quarter_label` (Q1 FY2026), `is_trading_day`
- **fact_market_prices:** `company_key`, `date_key`, `open`, `high`, `low`, `close`, `adj_close`,
  `volume`, `daily_return`, `is_stale_quote`
- **fact_financials:** `company_key`, `date_key` (period end), `period_type`, `statement`,
  `line_item`, `value_inr`, `value_reported`, `reporting_currency`, `is_translated`, `unit`,
  `fiscal_year`, `fiscal_quarter`, `missing_reason`, `is_calculated`
- **fact_metrics:** `company_key`, `date_key`, `period_type`, `metric_name`, `value`, `unit`,
  `na_reason`, `method`, `reporting_currency`, `is_translated`
- **fact_valuation:** `company_key`, `date_key` (price date), `period_end_date_key`,
  `valuation_basis` (current / fiscal_year_end), `market_cap_inr`, `enterprise_value_inr`,
  `pe_ratio`, `pb_ratio`, `ev_ebitda`, `ev_revenue`, `earnings_basis`, `is_translated`

Blank cells are missing values (NULL in the database). They are never zero: a blank P/E means the
multiple is not available or not meaningful, and `na_reason` in `fact_metrics` says why.

## 2. Loading

1. **Get Data → Text/CSV**, load all seven files.
2. In Power Query set `date` in `dim_date` to type Date; leave every `*_key` column as Whole
   Number. Set `is_stale_quote`, `is_translated`, `is_calculated` and `is_trading_day` to
   True/False.
3. **Mark `dim_date` as the date table** (Table tools → Mark as date table → `date`).
4. Sort `fiscal_quarter_label` by `date_key` and `month_name` by `month`.

## 3. Relationships

All are single-direction, from the dimension (one) to the fact (many).

| From (many) | To (one) | Cardinality | Active |
|---|---|---|---|
| `fact_market_prices[company_key]` | `dim_company[company_key]` | many-to-one | yes |
| `fact_financials[company_key]` | `dim_company[company_key]` | many-to-one | yes |
| `fact_metrics[company_key]` | `dim_company[company_key]` | many-to-one | yes |
| `fact_valuation[company_key]` | `dim_company[company_key]` | many-to-one | yes |
| `dim_company[sector_key]` | `dim_sector[sector_key]` | many-to-one | yes |
| `fact_market_prices[date_key]` | `dim_date[date_key]` | many-to-one | yes |
| `fact_financials[date_key]` | `dim_date[date_key]` | many-to-one | yes |
| `fact_metrics[date_key]` | `dim_date[date_key]` | many-to-one | yes |
| `fact_valuation[date_key]` | `dim_date[date_key]` | many-to-one | yes |
| `fact_valuation[period_end_date_key]` | `dim_date[date_key]` | many-to-one | **no** (use `USERELATIONSHIP`) |

Every foreign key in the exported files exists in its dimension, and every table is unique at
its grain; the export refuses to write files that fail either check
(`src/exports/powerbi.py`, `validate_star_schema`).

## 4. DAX measures

Create a table named `Measures` (Enter Data, one empty column) and add these.

### Financials

```DAX
Revenue (₹ Cr) =
CALCULATE (
    SUM ( fact_financials[value_inr] ),
    fact_financials[line_item] = "revenue",
    fact_financials[period_type] = "annual"
) / 1e7

Net Income (₹ Cr) =
CALCULATE (
    SUM ( fact_financials[value_inr] ),
    fact_financials[line_item] = "net_income",
    fact_financials[period_type] = "annual"
) / 1e7

Revenue (reporting currency) =
CALCULATE (
    SUM ( fact_financials[value_reported] ),
    fact_financials[line_item] = "revenue",
    fact_financials[period_type] = "annual"
)

-- Year-on-year growth in the reporting currency. Blank when the prior year is missing
-- or not positive, matching the pipeline's rule.
Revenue YoY % =
VAR CurrentFY = SELECTEDVALUE ( dim_date[fiscal_year] )
VAR Prior =
    CALCULATE (
        [Revenue (reporting currency)],
        REMOVEFILTERS ( dim_date ),
        dim_date[fiscal_year] = CurrentFY - 1
    )
RETURN
    IF ( NOT ISBLANK ( Prior ) && Prior > 0,
         DIVIDE ( [Revenue (reporting currency)] - Prior, Prior ) )
```

`Revenue YoY %` needs one fiscal year in context (a slicer or an axis on
`dim_date[fiscal_year_label]`). The stored equivalent is `revenue_growth` in `fact_metrics`.

### Metrics (stored ratios)

```DAX
-- Generic: the stored value of one metric for the company and period in context.
Metric Value =
VAR M = SELECTEDVALUE ( fact_metrics[metric_name] )
RETURN IF ( NOT ISBLANK ( M ), AVERAGE ( fact_metrics[value] ) )

EBITDA Margin % =
CALCULATE (
    AVERAGE ( fact_metrics[value] ),
    fact_metrics[metric_name] = "ebitda_margin",
    fact_metrics[period_type] = "annual"
)

Net Margin % =
CALCULATE (
    AVERAGE ( fact_metrics[value] ),
    fact_metrics[metric_name] = "net_margin",
    fact_metrics[period_type] = "annual"
)

ROE % =
CALCULATE (
    AVERAGE ( fact_metrics[value] ),
    fact_metrics[metric_name] = "roe",
    fact_metrics[period_type] = "annual"
)
```

Use these with a single company in context. Across several companies they return a simple
average of the companies' ratios, which is not a weighted sector ratio; label the visual
accordingly or use the medians below.

### Valuation and peer medians

```DAX
Current P/E =
CALCULATE ( AVERAGE ( fact_valuation[pe_ratio] ), fact_valuation[valuation_basis] = "current" )

Current EV/EBITDA =
CALCULATE ( AVERAGE ( fact_valuation[ev_ebitda] ), fact_valuation[valuation_basis] = "current" )

Market Cap (₹ Cr) =
CALCULATE ( SUM ( fact_valuation[market_cap_inr] ), fact_valuation[valuation_basis] = "current" ) / 1e7

-- Median EV/EBITDA of the selected company's peer group, excluding the company itself.
-- Blank multiples (N/A) are ignored by MEDIANX, never treated as zero.
Peer Median EV/EBITDA =
VAR Target = SELECTEDVALUE ( dim_company[company_key] )
VAR PeerGroup = SELECTEDVALUE ( dim_company[peer_group] )
RETURN
    CALCULATE (
        MEDIANX (
            FILTER ( VALUES ( dim_company[company_key] ), dim_company[company_key] <> Target ),
            [Current EV/EBITDA]
        ),
        REMOVEFILTERS ( dim_company ),
        dim_company[peer_group] = PeerGroup,
        dim_company[entity_type] = "company"
    )

Peers With A Value (EV/EBITDA) =
VAR Target = SELECTEDVALUE ( dim_company[company_key] )
VAR PeerGroup = SELECTEDVALUE ( dim_company[peer_group] )
RETURN
    CALCULATE (
        COUNTROWS (
            FILTER ( VALUES ( dim_company[company_key] ),
                     dim_company[company_key] <> Target && NOT ISBLANK ( [Current EV/EBITDA] ) )
        ),
        REMOVEFILTERS ( dim_company ),
        dim_company[peer_group] = PeerGroup,
        dim_company[entity_type] = "company"
    )

EV/EBITDA Premium to Peer Median % =
VAR M = [Peer Median EV/EBITDA]
RETURN IF ( NOT ISBLANK ( [Current EV/EBITDA] ) && M > 0, DIVIDE ( [Current EV/EBITDA], M ) - 1 )
```

Always show `Peers With A Value` next to a peer median. With fewer than four peers show only the
minimum, median and maximum, as the app and the Excel workbook do.

### Market performance

```DAX
-- Cumulative return over the dates in context, compounding daily returns.
-- Placeholder rows are excluded, as everywhere else in FinSight.
Cumulative Return % =
VAR Days =
    FILTER ( fact_market_prices,
             NOT fact_market_prices[is_stale_quote] && NOT ISBLANK ( fact_market_prices[daily_return] ) )
RETURN IF ( COUNTROWS ( Days ) > 0, PRODUCTX ( Days, 1 + fact_market_prices[daily_return] ) - 1 )

-- Running cumulative return for a line chart with dim_date[date] on the axis.
Cumulative Return To Date % =
VAR LastDay = MAX ( dim_date[date] )
RETURN
    CALCULATE (
        [Cumulative Return %],
        FILTER ( ALLSELECTED ( dim_date[date] ), dim_date[date] <= LastDay )
    )

Annualized Volatility % =
VAR Days =
    FILTER ( fact_market_prices,
             NOT fact_market_prices[is_stale_quote] && NOT ISBLANK ( fact_market_prices[daily_return] ) )
RETURN IF ( COUNTROWS ( Days ) >= 30, STDEVX.S ( Days, fact_market_prices[daily_return] ) * SQRT ( 252 ) )

Benchmark Cumulative Return % =
CALCULATE ( [Cumulative Return %], REMOVEFILTERS ( dim_company ), dim_company[entity_type] = "index" )

Excess Return vs Benchmark % = [Cumulative Return %] - [Benchmark Cumulative Return %]

Average Daily Volume (lakh) =
CALCULATE ( AVERAGE ( fact_market_prices[volume] ), NOT fact_market_prices[is_stale_quote] ) / 1e5
```

Use these with one company in context. `Annualized Volatility %` returns blank for fewer than 30
daily returns, the pipeline's minimum.

### Stored risk metrics

```DAX
Volatility 1Y % =
CALCULATE ( AVERAGE ( fact_metrics[value] ), fact_metrics[metric_name] = "volatility_1y" )

Sharpe 1Y =
CALCULATE ( AVERAGE ( fact_metrics[value] ), fact_metrics[metric_name] = "sharpe_1y" )

Max Drawdown 3Y % =
CALCULATE ( AVERAGE ( fact_metrics[value] ), fact_metrics[metric_name] = "max_drawdown_3y" )
```

The pairwise correlation matrix is not part of the star schema (it is company × company, not a
fact at a date). It is in `market_analysis.xlsx` and in the database table `core.correlations`;
load it as an eighth table if the Risk page needs a heatmap.

## 5. Report layout (five pages)

Every page: a title bar "FinSight", the footer text "FinSight is an analytics tool, not investment
advice. Data: yfinance, as of {latest price date}", one accent colour (#1F4E79) with greys, no
decorative visuals. Use descriptive wording only ("trades at a lower multiple than the peer
median"), never a judgement.

| Page | Slicers | Visuals |
|---|---|---|
| **1. Executive Overview** | Sector | Cards: number of companies, latest price date, median 1Y return, median current P/E. Table: company, sector, market cap, 1Y return, P/E. Bar: 1Y return by company. Card: latest pass rate (typed in, or from a loaded `data_quality_summary`). |
| **2. Financial Performance** | Company (single), fiscal year | Column chart: `Revenue (₹ Cr)` and `Net Income (₹ Cr)` by `fiscal_year_label`. Line: `EBITDA Margin %` and `Net Margin %` by fiscal year. Card: `Revenue YoY %`, labelled "reporting-currency growth" with `reporting_currency`. Matrix: line items × fiscal year from `fact_financials`. Text box where `is_translated` is true: "translated from USD". |
| **3. Comparable Companies** | Company (single) | Table: the company and its peers with `Current P/E`, `Current EV/EBITDA`, P/B, EV/Revenue. Cards: `Peer Median EV/EBITDA`, `Peers With A Value`, `EV/EBITDA Premium to Peer Median %`. Bar: EV/EBITDA by company within the peer group, with a constant line at the peer median. |
| **4. Market Performance** | Company (multi), date range | Line: `Cumulative Return To Date %` by date, one line per company, plus `Benchmark Cumulative Return %`. Column: `Average Daily Volume (lakh)` by month. Table: `Cumulative Return %`, `Annualized Volatility %`, `Excess Return vs Benchmark %`. |
| **5. Risk & Correlation** | Sector | Table: `Volatility 1Y %`, `Sharpe 1Y`, `Max Drawdown 3Y %` by company. Scatter: volatility (x) against 1Y return (y), one point per company, coloured by sector. Matrix heatmap of correlations if `core.correlations` is loaded. Text box: "With about five years of data, differences between Sharpe ratios are not statistically established (see the Data Science Lab)." |

## 6. Things to keep consistent with the rest of FinSight

- **Banks:** EBITDA margin, EV/EBITDA, debt/equity and liquidity ratios are blank for banks, with
  `na_reason` = "N/A (not meaningful for banks)". Show "N/A", never 0.
- **Infosys** reports in USD: `value_reported` is USD, `value_inr` is FinSight's translation,
  growth metrics are in USD (`reporting_currency`), and valuation rows have `is_translated` true.
- **Placeholder price rows** (`is_stale_quote`) are in the fact table so that it mirrors the
  database; the measures above exclude them.
- **The DAX measures in this document have not been run.** Power BI Desktop is Windows-only and
  was not available on the development machine. They are written against the exported schema and
  should be checked against the figures in the app when the report is built.
