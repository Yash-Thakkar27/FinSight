-- Analytical queries over the core schema. Each runs as-is in psql:
--   psql -h localhost -p 5433 -U finsight -d finsight -f sql/analytical_queries.sql
-- Monetary values are absolute INR; divide by 1e7 for crore.

-- Q1. Coverage: price history and statement periods per company
SELECT c.ticker,
       c.sector_type,
       count(DISTINCT p.date)                                   AS price_days,
       min(p.date)                                              AS first_price,
       max(p.date)                                              AS last_price,
       count(*) FILTER (WHERE p.is_stale_quote)                 AS placeholder_days,
       (SELECT count(DISTINCT f.period_end_date) FROM core.financial_statements f
         WHERE f.company_id = c.company_id AND f.period_type = 'annual')    AS annual_periods,
       (SELECT count(DISTINCT f.period_end_date) FROM core.financial_statements f
         WHERE f.company_id = c.company_id AND f.period_type = 'quarterly') AS quarterly_periods
FROM core.companies c
JOIN core.market_prices p USING (company_id)
GROUP BY c.company_id, c.ticker, c.sector_type
ORDER BY c.company_id;

-- Q2. Latest fiscal year income summary, in INR crore
WITH latest AS (
    SELECT company_id, max(period_end_date) AS period_end_date
    FROM core.financial_statements
    WHERE period_type = 'annual' AND line_item = 'revenue' AND value IS NOT NULL
    GROUP BY company_id
)
SELECT c.ticker,
       'FY' || max(f.fiscal_year)                                              AS fiscal_year,
       round(max(f.value) FILTER (WHERE f.line_item = 'revenue') / 1e7)        AS revenue_cr,
       round(max(f.value) FILTER (WHERE f.line_item = 'ebitda') / 1e7)         AS ebitda_cr,
       round(max(f.value) FILTER (WHERE f.line_item = 'net_income') / 1e7)     AS net_income_cr,
       round(max(f.value) FILTER (WHERE f.line_item = 'eps_diluted'), 2)       AS eps_diluted,
       max(f.original_currency)                                                AS reported_in
FROM latest l
JOIN core.financial_statements f USING (company_id, period_end_date)
JOIN core.companies c USING (company_id)
WHERE f.period_type = 'annual'
GROUP BY c.company_id, c.ticker
ORDER BY revenue_cr DESC;

-- Q3. Why values are missing, by sector type and period type
SELECT c.sector_type,
       f.period_type,
       count(*)                                                          AS fields,
       count(f.value)                                                    AS with_value,
       count(*) FILTER (WHERE f.missing_reason = 'not_applicable')          AS not_applicable,
       count(*) FILTER (WHERE f.missing_reason = 'unavailable_from_source') AS unavailable,
       count(*) FILTER (WHERE f.missing_reason = 'failed_retrieval')        AS failed_retrieval
FROM core.financial_statements f
JOIN core.companies c USING (company_id)
GROUP BY c.sector_type, f.period_type
ORDER BY 1, 2;

-- Q4. Foreign-currency conversion audit: reported value, rate used and stored INR value
SELECT c.ticker, f.statement, f.line_item, f.period_end_date,
       round(f.value / f.fx_rate, 2)   AS reported_value,
       f.original_currency,
       f.fx_rate,
       round(f.value / 1e7, 1)         AS value_inr_cr
FROM core.financial_statements f
JOIN core.companies c USING (company_id)
WHERE f.fx_rate IS NOT NULL
  AND f.period_type = 'annual'
  AND f.line_item IN ('revenue', 'net_income', 'total_assets')
ORDER BY c.ticker, f.period_end_date DESC, f.statement, f.line_item;

-- Q5. Latest close and share count per company, with the implied market cap in INR crore
SELECT c.ticker,
       p.date                                            AS price_date,
       p.close,
       s.shares_outstanding,
       s.as_of_date                                      AS shares_as_of,
       round(p.close * s.shares_outstanding / 1e7)       AS market_cap_cr
FROM core.companies c
JOIN LATERAL (SELECT date, close FROM core.market_prices
              WHERE company_id = c.company_id AND NOT is_stale_quote
              ORDER BY date DESC LIMIT 1) p ON TRUE
JOIN LATERAL (SELECT shares_outstanding, as_of_date FROM core.shares_outstanding
              WHERE company_id = c.company_id
              ORDER BY as_of_date DESC LIMIT 1) s ON TRUE
ORDER BY market_cap_cr DESC;

-- Q6. Latest pipeline run and its data-quality summary
SELECT r.run_id, r.started_at, r.status,
       q.total_records, q.valid_records, q.invalid_records, q.warning_count,
       round(q.missing_pct::numeric, 1)   AS missing_pct,
       q.duplicate_records,
       round(q.pass_rate_pct::numeric, 2) AS pass_rate_pct
FROM core.pipeline_runs r
JOIN core.data_quality_summary q USING (run_id)
ORDER BY r.run_id DESC
LIMIT 1;
