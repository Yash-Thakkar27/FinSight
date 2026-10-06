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
    -- Source placeholder row (zero volume, flat at the previous close), typically
    -- an exchange holiday. Kept, never deleted; analytics exclude flagged rows.
    is_stale_quote  BOOLEAN NOT NULL DEFAULT FALSE,
    source_id     INTEGER NOT NULL REFERENCES core.data_sources (source_id),
    retrieved_at  TIMESTAMPTZ NOT NULL,
    CONSTRAINT market_prices_company_date_key UNIQUE (company_id, date)
);

-- Splits and bonus issues, from the source's price history. split_ratio is new shares
-- per old share (2.0 for a 2-for-1 split or a 1:1 bonus). Used to put share counts
-- on the current basis for EPS.
CREATE TABLE IF NOT EXISTS core.stock_splits (
    company_id    INTEGER NOT NULL REFERENCES core.companies (company_id),
    date          DATE NOT NULL,
    split_ratio   DOUBLE PRECISION NOT NULL CHECK (split_ratio > 0),
    source_id     INTEGER NOT NULL REFERENCES core.data_sources (source_id),
    retrieved_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (company_id, date)
);

-- FX rates used to convert foreign-currency statements. rate = INR per 1 unit.
CREATE TABLE IF NOT EXISTS core.fx_rates (
    currency      TEXT NOT NULL,
    date          DATE NOT NULL,
    rate          NUMERIC(20, 6) NOT NULL CHECK (rate > 0),
    source_id     INTEGER NOT NULL REFERENCES core.data_sources (source_id),
    retrieved_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (currency, date)
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
    -- Reporting currency and the value exactly as the source reported it. The
    -- reported value is never overwritten. When the reporting currency is not
    -- INR, value = original_value * fx_rate, where the rate is the period average
    -- for flows (income statement, cash flow) and the period-end rate for balances.
    original_currency  TEXT NOT NULL DEFAULT 'INR',
    original_value   NUMERIC(28, 6),
    fx_rate          NUMERIC(20, 6),
    fx_rate_type     TEXT CHECK (fx_rate_type IN ('average', 'period_end')),
    fx_source        TEXT,
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
    ),
    -- a translated value always records how it was translated
    CONSTRAINT financial_statements_fx_fields_together CHECK (
        (fx_rate IS NULL AND fx_rate_type IS NULL AND fx_source IS NULL)
        OR (fx_rate IS NOT NULL AND fx_rate_type IS NOT NULL AND fx_source IS NOT NULL)
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
    -- Which variant of the calculation produced the value, where there is more than one:
    --   roe, roa:           average_balance | closing_balance (fallback, footnoted in the UI)
    --   eps, eps_growth:    weighted_average_diluted | weighted_average_basic | period_end
    --   current multiples:  ttm | latest_annual
    method           TEXT,
    unit             TEXT NOT NULL,             -- ratio | pct | multiple | INR | ...
    formula_id       TEXT REFERENCES core.formulas (formula_id),
    as_of_date       DATE NOT NULL,             -- price date for multiples, else period end
    input_fields     JSONB,
    -- Currency the inputs were taken in. Ratios, margins and growth are computed
    -- in the company's reporting currency. is_translated marks INR figures built
    -- from statements FinSight translated from a foreign currency.
    reporting_currency  TEXT NOT NULL DEFAULT 'INR',
    is_translated    BOOLEAN NOT NULL DEFAULT FALSE,
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

-- Calculated multiples compared with the ones the source reports. A difference
-- above the tolerance is flagged for review, not corrected.
CREATE TABLE IF NOT EXISTS core.valuation_reconciliation (
    company_id        INTEGER NOT NULL REFERENCES core.companies (company_id),
    metric_name       TEXT NOT NULL,
    as_of_date        DATE NOT NULL,
    calculated_value  DOUBLE PRECISION,
    source_value      DOUBLE PRECISION,
    difference_pct    DOUBLE PRECISION,          -- (calculated - source) / |source| * 100
    is_flagged        BOOLEAN NOT NULL,
    note              TEXT,
    PRIMARY KEY (company_id, metric_name)
);

-- Comparable-company analysis for each company against its default peer group
-- (same peer_group, target excluded from every statistic). One row per target
-- and metric. The app recomputes the same figures when the user overrides peers.
CREATE TABLE IF NOT EXISTS core.peer_comparisons (
    company_id        INTEGER NOT NULL REFERENCES core.companies (company_id),
    metric_name       TEXT NOT NULL,
    category          TEXT NOT NULL,             -- operating | capital_structure | valuation
    period_type       TEXT NOT NULL,
    period_end_date   DATE,                      -- the target's period
    target_value      DOUBLE PRECISION,
    target_na_reason  TEXT,
    n_peers           INTEGER NOT NULL,          -- peers with a value (N/A excluded)
    peer_min          DOUBLE PRECISION,
    peer_p25          DOUBLE PRECISION,
    peer_median       DOUBLE PRECISION,
    peer_mean         DOUBLE PRECISION,
    peer_p75          DOUBLE PRECISION,
    peer_max          DOUBLE PRECISION,
    -- With fewer than 4 peers only min / median / max are stored (p25, mean, p75 are NULL)
    -- and position is a rank ("2nd of 3") instead of a percentile.
    percentile_rank   DOUBLE PRECISION,          -- 0-100, target's position within peers; n >= 4
    rank_position     INTEGER,                   -- 1 = highest, among the target and its peers
    rank_of           INTEGER,                   -- peers with a value + the target
    position_label    TEXT,                      -- what the UI shows: "62nd percentile" or "2nd of 3"
    premium_pct       DOUBLE PRECISION,          -- (target / median - 1) * 100, multiples and ratios
    difference        DOUBLE PRECISION,          -- target - median, in the metric's own unit
    interpretation    TEXT,                      -- rule-generated sentence, NULL if inputs missing
    peer_tickers      TEXT[] NOT NULL,           -- the peer set, whether or not each has a value
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, metric_name)
);

-- Statistical outliers. "Potential data anomaly detected" is a prompt to review
-- the data point, never a statement about the company.
CREATE TABLE IF NOT EXISTS core.anomalies (
    company_id    INTEGER NOT NULL REFERENCES core.companies (company_id),
    date          DATE NOT NULL,                 -- price date, or fiscal period end
    dataset       TEXT NOT NULL CHECK (dataset IN ('market', 'fundamentals')),
    variable      TEXT NOT NULL,                 -- daily_return | log_volume | revenue_growth | ...
    method        TEXT NOT NULL CHECK (method IN ('iqr', 'modified_zscore')),
    value         DOUBLE PRECISION NOT NULL,     -- the observation itself
    -- Fundamentals only: the peer-group median change for that period, and the
    -- observation minus it. The thresholds are applied to adjusted_value.
    peer_group_median  DOUBLE PRECISION,
    adjusted_value     DOUBLE PRECISION,
    lower_bound   DOUBLE PRECISION,              -- IQR fences, on the scale that was tested
    upper_bound   DOUBLE PRECISION,
    score         DOUBLE PRECISION,              -- modified z-score, or distance beyond the fence in IQRs
    message       TEXT NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, date, variable, method)
);

-- Pairwise correlation of daily returns over an aligned date range.
-- Stored for both orderings and the diagonal, so a heatmap is a plain pivot.
CREATE TABLE IF NOT EXISTS core.correlations (
    company_id_a    INTEGER NOT NULL REFERENCES core.companies (company_id),
    company_id_b    INTEGER NOT NULL REFERENCES core.companies (company_id),
    window_label    TEXT NOT NULL,               -- 1y | 3y | full
    correlation     DOUBLE PRECISION,
    n_observations  INTEGER NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id_a, company_id_b, window_label)
);

-- ---------------------------------------------------------------------------
-- core: Data Science Lab results. The app shows these stored results; it never
-- trains a model. Each task keeps its latest run only (rerunning replaces it).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.ml_runs (
    ml_run_id         BIGSERIAL PRIMARY KEY,
    task              TEXT NOT NULL UNIQUE,      -- volatility | clustering | stats_tests | regimes | anomaly_comparison
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    seed              INTEGER NOT NULL,
    data_snapshot_id  TEXT NOT NULL,             -- hash of the price data the run used
    params            JSONB NOT NULL,
    results           JSONB NOT NULL             -- small result tables and summaries
);

-- Forecast-evaluation metrics: pooled, per walk-forward fold and per ticker.
CREATE TABLE IF NOT EXISTS core.ml_metrics (
    ml_run_id  BIGINT NOT NULL REFERENCES core.ml_runs (ml_run_id) ON DELETE CASCADE,
    scope      TEXT NOT NULL CHECK (scope IN ('pooled', 'fold', 'ticker')),
    horizon    INTEGER NOT NULL,
    model      TEXT NOT NULL,
    metric     TEXT NOT NULL,                    -- rmse | mae | qlike
    fold       INTEGER,
    ticker     TEXT,
    value      DOUBLE PRECISION NOT NULL,
    n          INTEGER NOT NULL
);

-- Out-of-sample volatility forecasts (annualized volatility) and what was realized.
CREATE TABLE IF NOT EXISTS core.ml_forecasts (
    ml_run_id  BIGINT NOT NULL REFERENCES core.ml_runs (ml_run_id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES core.companies (company_id),
    date       DATE NOT NULL,                    -- forecast origin
    horizon    INTEGER NOT NULL,                 -- trading days ahead
    fold       INTEGER NOT NULL,
    realized   DOUBLE PRECISION NOT NULL,
    hist_21    DOUBLE PRECISION NOT NULL,
    ewma       DOUBLE PRECISION NOT NULL,
    garch      DOUBLE PRECISION NOT NULL,
    ridge      DOUBLE PRECISION NOT NULL,
    gbm        DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (company_id, date, horizon)
);

CREATE TABLE IF NOT EXISTS core.ml_cluster_assignments (
    ml_run_id             BIGINT NOT NULL REFERENCES core.ml_runs (ml_run_id) ON DELETE CASCADE,
    company_id            INTEGER NOT NULL REFERENCES core.companies (company_id),
    sector                TEXT NOT NULL,
    kmeans_cluster        INTEGER NOT NULL,
    hierarchical_cluster  INTEGER NOT NULL,
    kmeans_cluster_at_sector_count        INTEGER NOT NULL,
    hierarchical_cluster_at_sector_count  INTEGER NOT NULL,
    pc1                   DOUBLE PRECISION NOT NULL,
    pc2                   DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (company_id)
);

CREATE TABLE IF NOT EXISTS core.ml_stat_tests (
    ml_run_id    BIGINT NOT NULL REFERENCES core.ml_runs (ml_run_id) ON DELETE CASCADE,
    test         TEXT NOT NULL,
    subject      TEXT NOT NULL,
    statistic    DOUBLE PRECISION,
    p_value      DOUBLE PRECISION,
    p_adjusted   DOUBLE PRECISION,               -- Benjamini-Hochberg
    effect_size  DOUBLE PRECISION,
    ci_low       DOUBLE PRECISION,
    ci_high      DOUBLE PRECISION,
    n            INTEGER,
    details      JSONB,
    PRIMARY KEY (test, subject)
);

CREATE TABLE IF NOT EXISTS core.ml_regimes (
    ml_run_id         BIGINT NOT NULL REFERENCES core.ml_runs (ml_run_id) ON DELETE CASCADE,
    date              DATE PRIMARY KEY,
    p_turbulent       DOUBLE PRECISION NOT NULL, -- smoothed probability (uses the whole sample)
    regime            INTEGER NOT NULL,          -- 0 calm, 1 turbulent
    threshold_regime  INTEGER                    -- rolling-volatility rule, for comparison
);

CREATE TABLE IF NOT EXISTS core.ml_anomaly_flags (
    ml_run_id              BIGINT NOT NULL REFERENCES core.ml_runs (ml_run_id) ON DELETE CASCADE,
    company_id             INTEGER NOT NULL REFERENCES core.companies (company_id),
    date                   DATE NOT NULL,
    daily_return           DOUBLE PRECISION NOT NULL,
    return_score           DOUBLE PRECISION,
    volume_score           DOUBLE PRECISION,
    flag_iqr               BOOLEAN NOT NULL,
    flag_modified_zscore   BOOLEAN NOT NULL,
    flag_isolation_forest  BOOLEAN NOT NULL,
    isolation_score        DOUBLE PRECISION NOT NULL,
    methods_agreeing       INTEGER NOT NULL,
    message                TEXT NOT NULL,
    PRIMARY KEY (company_id, date)
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
    is_stale_quote  BOOLEAN NOT NULL DEFAULT FALSE,
    source        TEXT NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL,
    raw_file      TEXT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS staging.stock_splits (
    ticker        TEXT NOT NULL,
    date          DATE NOT NULL,
    split_ratio   DOUBLE PRECISION NOT NULL,
    source        TEXT NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL,
    raw_file      TEXT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS staging.fx_rates (
    currency      TEXT NOT NULL,
    date          DATE NOT NULL,
    rate          NUMERIC(20, 6) NOT NULL,
    source        TEXT NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL,
    raw_file      TEXT,
    PRIMARY KEY (currency, date)
);

CREATE TABLE IF NOT EXISTS staging.source_reported_metrics (
    ticker        TEXT NOT NULL,
    metric_name   TEXT NOT NULL,
    value         DOUBLE PRECISION,
    as_of_date    DATE NOT NULL,
    source        TEXT NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL,
    raw_file      TEXT,
    PRIMARY KEY (ticker, metric_name)
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
    original_unit    TEXT,                      -- unit as reported, e.g. USD or USD_per_share
    original_currency  TEXT NOT NULL DEFAULT 'INR',
    original_value   NUMERIC(28, 6),            -- value as reported, before FX conversion
    fx_rate          NUMERIC(20, 6),
    fx_rate_type     TEXT,                      -- average | period_end
    fx_source        TEXT,
    is_calculated    BOOLEAN NOT NULL DEFAULT FALSE,
    formula_id       TEXT,
    source           TEXT NOT NULL,
    retrieved_at     TIMESTAMPTZ NOT NULL,
    raw_file         TEXT,
    PRIMARY KEY (ticker, statement, line_item, period_end_date, period_type)
);
