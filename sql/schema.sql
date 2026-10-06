-- FinSight database schema. Single source of truth: SQLAlchemy models must match this file.
-- Layers: staging (cleaned rows as loaded, keyed by ticker) -> core (normalized,
-- constrained) -> mart (Power BI star-schema views, added in Phase 8).
-- Idempotent: safe to run repeatedly. Conventions:
--   * monetary values are absolute INR (never crore/lakh) in the database
--   * a missing financial value is NULL with a missing_reason, never 0
--   * capex is stored as a positive outflow, debt as positive

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;

-- ---------------------------------------------------------------------------
-- core: lookups
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.sectors (
    sector_id    SERIAL PRIMARY KEY,
    sector_name  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS core.industries (
    industry_id    SERIAL PRIMARY KEY,
    industry_name  TEXT NOT NULL UNIQUE,
    sector_id      INTEGER NOT NULL REFERENCES core.sectors (sector_id)
);

CREATE TABLE IF NOT EXISTS core.data_sources (
    source_id  SERIAL PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    url        TEXT,
    notes      TEXT
);

-- ---------------------------------------------------------------------------
-- core: companies
-- The benchmark index is stored here too (entity_type = 'index') so that
-- market_prices has one uniform shape. Index rows carry no sector attributes.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.companies (
    company_id    SERIAL PRIMARY KEY,
    ticker        TEXT NOT NULL UNIQUE,
    company_name  TEXT NOT NULL,
    entity_type   TEXT NOT NULL DEFAULT 'company'
                  CHECK (entity_type IN ('company', 'index')),
    exchange      TEXT,
    sector_id     INTEGER REFERENCES core.sectors (sector_id),
    industry_id   INTEGER REFERENCES core.industries (industry_id),
    peer_group    TEXT,
    sector_type   TEXT CHECK (sector_type IN ('non_financial', 'bank', 'nbfc', 'insurance')),
    country       TEXT,
    currency      TEXT NOT NULL DEFAULT 'INR',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT companies_company_attrs_required CHECK (
        entity_type = 'index'
        OR (sector_id IS NOT NULL AND industry_id IS NOT NULL
            AND peer_group IS NOT NULL AND sector_type IS NOT NULL)
    )
);

-- Point-in-time shares outstanding from source metadata (Ticker.info), kept
-- with its as-of date. Period-end share counts from the balance sheet live in
-- financial_statements (line_item = 'shares_outstanding').
CREATE TABLE IF NOT EXISTS core.shares_outstanding (
    company_id          INTEGER NOT NULL REFERENCES core.companies (company_id),
    as_of_date          DATE NOT NULL,
    shares_outstanding  NUMERIC(24, 2) NOT NULL CHECK (shares_outstanding > 0),
    source_id           INTEGER NOT NULL REFERENCES core.data_sources (source_id),
    retrieved_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (company_id, as_of_date)
);

-- ---------------------------------------------------------------------------
-- core: pipeline runs and data quality
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.pipeline_runs (
    run_id             BIGSERIAL PRIMARY KEY,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'success', 'partial', 'failed')),
    records_processed  BIGINT,
    notes              TEXT
);

-- One row per validation check result (Section 8).
CREATE TABLE IF NOT EXISTS core.data_quality_logs (
    log_id      BIGSERIAL PRIMARY KEY,
    run_id      BIGINT NOT NULL REFERENCES core.pipeline_runs (run_id) ON DELETE CASCADE,
    check_name  TEXT NOT NULL,
    category    TEXT NOT NULL,   -- market_data | financials | completeness | consistency | freshness
    severity    TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    status      TEXT NOT NULL CHECK (status IN ('pass', 'fail')),
    message     TEXT,
    ticker      TEXT,
    dataset     TEXT,            -- market_prices | financial_statements | company_metadata
    record_key  TEXT,            -- natural key of the offending record
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Run-level summary. Every figure is computed from the run, never hard-coded.
CREATE TABLE IF NOT EXISTS core.data_quality_summary (
    run_id             BIGINT PRIMARY KEY REFERENCES core.pipeline_runs (run_id) ON DELETE CASCADE,
    total_records      BIGINT NOT NULL,
    valid_records      BIGINT NOT NULL,
    invalid_records    BIGINT NOT NULL,
    warning_count      BIGINT NOT NULL,
    missing_pct        DOUBLE PRECISION,
    duplicate_records  BIGINT NOT NULL,
    pass_rate_pct      DOUBLE PRECISION,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- core: market data
-- UNIQUE (company_id, date) also provides the (company_id, date) index.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.market_prices (
    company_id    INTEGER NOT NULL REFERENCES core.companies (company_id),
    date          DATE NOT NULL,
    open          NUMERIC(20, 6),
    high          NUMERIC(20, 6),
    low           NUMERIC(20, 6),
    close         NUMERIC(20, 6),
    adj_close     NUMERIC(20, 6),
    volume        BIGINT,
    source_id     INTEGER NOT NULL REFERENCES core.data_sources (source_id),
    retrieved_at  TIMESTAMPTZ NOT NULL,
    CONSTRAINT market_prices_company_date_key UNIQUE (company_id, date)
);

-- ---------------------------------------------------------------------------
-- core: financial statements (long format: one row per line item per period)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.formulas (
    formula_id               TEXT PRIMARY KEY,
    metric_name              TEXT NOT NULL,
    expression               TEXT NOT NULL,
    description              TEXT,
    unit                     TEXT,
    applicable_sector_types  TEXT[] NOT NULL
);

CREATE TABLE IF NOT EXISTS core.financial_statements (
    company_id       INTEGER NOT NULL REFERENCES core.companies (company_id),
    statement        TEXT NOT NULL CHECK (statement IN ('income', 'balance', 'cashflow')),
    fiscal_year      INTEGER NOT NULL,          -- FY2025 = year ending 31-Mar-2025
    fiscal_quarter   SMALLINT CHECK (fiscal_quarter BETWEEN 1 AND 4),  -- Q1 = Apr-Jun
    period_end_date  DATE NOT NULL,
    period_type      TEXT NOT NULL CHECK (period_type IN ('annual', 'quarterly')),
    line_item        TEXT NOT NULL,             -- canonical name from config/field_map.yaml
    value            NUMERIC(28, 6),
    missing_reason   TEXT CHECK (missing_reason IN
                         ('unavailable_from_source', 'not_applicable', 'failed_retrieval')),
    currency         TEXT NOT NULL DEFAULT 'INR',
    unit             TEXT NOT NULL,             -- INR | INR_per_share | shares
    is_calculated    BOOLEAN NOT NULL DEFAULT FALSE,
    formula_id       TEXT REFERENCES core.formulas (formula_id),
    source_id        INTEGER NOT NULL REFERENCES core.data_sources (source_id),
    retrieved_at     TIMESTAMPTZ NOT NULL,
    CONSTRAINT financial_statements_natural_key
        UNIQUE (company_id, statement, line_item, period_end_date, period_type),
    CONSTRAINT financial_statements_quarter_matches_type CHECK (
        (period_type = 'annual' AND fiscal_quarter IS NULL)
        OR (period_type = 'quarterly' AND fiscal_quarter IS NOT NULL)
    ),
    -- a value is present XOR it carries the reason it is missing
    CONSTRAINT financial_statements_value_or_reason CHECK (
        (value IS NULL) = (missing_reason IS NOT NULL)
    ),
    CONSTRAINT financial_statements_calculated_has_formula CHECK (
        NOT is_calculated OR formula_id IS NOT NULL
    )
);

-- ---------------------------------------------------------------------------
-- core: metrics (long format: ratios, valuation multiples, return/risk metrics)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.metrics (
    company_id       INTEGER NOT NULL REFERENCES core.companies (company_id),
    period_end_date  DATE NOT NULL,
    period_type      TEXT NOT NULL
                     CHECK (period_type IN ('annual', 'quarterly', 'ttm', 'point_in_time')),
    metric_name      TEXT NOT NULL,
    value            DOUBLE PRECISION,
    na_reason        TEXT,                      -- why value is NULL, shown in the UI
    unit             TEXT NOT NULL,             -- ratio | pct | multiple | INR | ...
    formula_id       TEXT REFERENCES core.formulas (formula_id),
    as_of_date       DATE NOT NULL,             -- price date for multiples, else period end
    input_fields     JSONB,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT metrics_natural_key
        UNIQUE (company_id, metric_name, period_end_date, period_type)
);

-- Multiples and figures reported directly by the source (e.g. Yahoo trailingPE).
-- Kept apart from calculated metrics and used only for reconciliation.
CREATE TABLE IF NOT EXISTS core.source_reported_metrics (
    company_id    INTEGER NOT NULL REFERENCES core.companies (company_id),
    metric_name   TEXT NOT NULL,
    value         DOUBLE PRECISION,
    as_of_date    DATE NOT NULL,
    source_id     INTEGER NOT NULL REFERENCES core.data_sources (source_id),
    retrieved_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (company_id, metric_name, as_of_date)
);

-- ---------------------------------------------------------------------------
-- staging: cleaned rows as loaded, keyed by ticker, no foreign keys.
-- Truncated and reloaded on every run, then upserted into core.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging.company_metadata (
    ticker                    TEXT PRIMARY KEY,
    source_name               TEXT,
    exchange                  TEXT,
    source_sector             TEXT,
    source_industry           TEXT,
    country                   TEXT,
    currency                  TEXT,
    financial_currency        TEXT,
    shares_outstanding        NUMERIC(24, 2),
    shares_as_of_date         DATE,
    source                    TEXT NOT NULL,
    retrieved_at              TIMESTAMPTZ NOT NULL,
    raw_file                  TEXT
);

CREATE TABLE IF NOT EXISTS staging.market_prices (
    ticker        TEXT NOT NULL,
    date          DATE NOT NULL,
    open          NUMERIC(20, 6),
    high          NUMERIC(20, 6),
    low           NUMERIC(20, 6),
    close         NUMERIC(20, 6),
    adj_close     NUMERIC(20, 6),
    volume        BIGINT,
    source        TEXT NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL,
    raw_file      TEXT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS staging.financial_statements (
    ticker           TEXT NOT NULL,
    statement        TEXT NOT NULL,
    fiscal_year      INTEGER NOT NULL,
    fiscal_quarter   SMALLINT,
    period_end_date  DATE NOT NULL,
    period_type      TEXT NOT NULL,
    line_item        TEXT NOT NULL,
    source_label     TEXT,                      -- original source field name, NULL if derived
    value            NUMERIC(28, 6),
    missing_reason   TEXT,
    currency         TEXT NOT NULL,
    unit             TEXT NOT NULL,
    original_unit    TEXT,
    is_calculated    BOOLEAN NOT NULL DEFAULT FALSE,
    formula_id       TEXT,
    source           TEXT NOT NULL,
    retrieved_at     TIMESTAMPTZ NOT NULL,
    raw_file         TEXT,
    PRIMARY KEY (ticker, statement, line_item, period_end_date, period_type)
);
