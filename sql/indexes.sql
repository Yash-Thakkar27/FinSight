-- Secondary indexes. Idempotent. Run after schema.sql.
-- (company_id, date) on market_prices and the financial_statements / metrics
-- natural keys are already indexed by their UNIQUE constraints in schema.sql.

-- Cross-sectional reads: all companies on one date (correlation, market pages)
CREATE INDEX IF NOT EXISTS ix_market_prices_date
    ON core.market_prices (date);

-- Company page: one company's statements by period
CREATE INDEX IF NOT EXISTS ix_financial_statements_company_period
    ON core.financial_statements (company_id, period_type, period_end_date);

-- Comps: one metric across all companies
CREATE INDEX IF NOT EXISTS ix_metrics_name_period
    ON core.metrics (metric_name, period_type, period_end_date);

CREATE INDEX IF NOT EXISTS ix_companies_peer_group
    ON core.companies (peer_group);

-- Data Quality page: filter a run's results by severity / status
CREATE INDEX IF NOT EXISTS ix_data_quality_logs_run
    ON core.data_quality_logs (run_id, severity, status);
