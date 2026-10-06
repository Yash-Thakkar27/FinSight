"""SQLAlchemy models. These mirror sql/schema.sql, which is the source of truth.

The schema is created by running schema.sql, never by `Base.metadata.create_all`.
tests/test_integration.py compares these models with the live database so the
two cannot drift apart unnoticed.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Timestamp = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- core ----

class Sector(Base):
    __tablename__ = "sectors"
    __table_args__ = {"schema": "core"}
    sector_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sector_name: Mapped[str] = mapped_column(Text, unique=True)


class Industry(Base):
    __tablename__ = "industries"
    __table_args__ = {"schema": "core"}
    industry_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    industry_name: Mapped[str] = mapped_column(Text, unique=True)
    sector_id: Mapped[int] = mapped_column(ForeignKey("core.sectors.sector_id"))


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = {"schema": "core"}
    source_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)
    url: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class CompanyRow(Base):
    __tablename__ = "companies"
    __table_args__ = {"schema": "core"}
    company_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(Text, unique=True)
    company_name: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(Text)
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("core.sectors.sector_id"))
    industry_id: Mapped[int | None] = mapped_column(ForeignKey("core.industries.industry_id"))
    peer_group: Mapped[str | None] = mapped_column(Text)
    sector_type: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    updated_at: Mapped[datetime] = mapped_column(Timestamp)


class SharesOutstanding(Base):
    __tablename__ = "shares_outstanding"
    __table_args__ = {"schema": "core"}
    company_id: Mapped[int] = mapped_column(ForeignKey("core.companies.company_id"),
                                            primary_key=True)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    shares_outstanding: Mapped[Decimal] = mapped_column(Numeric(24, 2))
    source_id: Mapped[int] = mapped_column(ForeignKey("core.data_sources.source_id"))
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = {"schema": "core"}
    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(Timestamp)
    finished_at: Mapped[datetime | None] = mapped_column(Timestamp)
    status: Mapped[str] = mapped_column(Text)
    records_processed: Mapped[int | None] = mapped_column(BigInteger)
    notes: Mapped[str | None] = mapped_column(Text)


class DataQualityLog(Base):
    __tablename__ = "data_quality_logs"
    __table_args__ = {"schema": "core"}
    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("core.pipeline_runs.run_id"))
    check_name: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    message: Mapped[str | None] = mapped_column(Text)
    ticker: Mapped[str | None] = mapped_column(Text)
    dataset: Mapped[str | None] = mapped_column(Text)
    record_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp)


class DataQualitySummary(Base):
    __tablename__ = "data_quality_summary"
    __table_args__ = {"schema": "core"}
    run_id: Mapped[int] = mapped_column(ForeignKey("core.pipeline_runs.run_id"),
                                        primary_key=True)
    total_records: Mapped[int] = mapped_column(BigInteger)
    valid_records: Mapped[int] = mapped_column(BigInteger)
    invalid_records: Mapped[int] = mapped_column(BigInteger)
    warning_count: Mapped[int] = mapped_column(BigInteger)
    missing_pct: Mapped[float | None] = mapped_column(Double)
    duplicate_records: Mapped[int] = mapped_column(BigInteger)
    pass_rate_pct: Mapped[float | None] = mapped_column(Double)
    created_at: Mapped[datetime] = mapped_column(Timestamp)


class MarketPrice(Base):
    __tablename__ = "market_prices"
    __table_args__ = {"schema": "core"}
    # UNIQUE (company_id, date) in SQL; declared as the mapper's key here.
    company_id: Mapped[int] = mapped_column(ForeignKey("core.companies.company_id"),
                                            primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    adj_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    is_stale_quote: Mapped[bool] = mapped_column(Boolean)
    source_id: Mapped[int] = mapped_column(ForeignKey("core.data_sources.source_id"))
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = {"schema": "core"}
    currency: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    source_id: Mapped[int] = mapped_column(ForeignKey("core.data_sources.source_id"))
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)


class Formula(Base):
    __tablename__ = "formulas"
    __table_args__ = {"schema": "core"}
    formula_id: Mapped[str] = mapped_column(Text, primary_key=True)
    metric_name: Mapped[str] = mapped_column(Text)
    expression: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    applicable_sector_types: Mapped[list[str]] = mapped_column(ARRAY(Text))


class FinancialStatement(Base):
    __tablename__ = "financial_statements"
    __table_args__ = {"schema": "core"}
    # UNIQUE (company_id, statement, line_item, period_end_date, period_type) in SQL.
    company_id: Mapped[int] = mapped_column(ForeignKey("core.companies.company_id"),
                                            primary_key=True)
    statement: Mapped[str] = mapped_column(Text, primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(Integer)
    fiscal_quarter: Mapped[int | None] = mapped_column(SmallInteger)
    period_end_date: Mapped[date] = mapped_column(Date, primary_key=True)
    period_type: Mapped[str] = mapped_column(Text, primary_key=True)
    line_item: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
    missing_reason: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text)
    original_currency: Mapped[str] = mapped_column(Text)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    is_calculated: Mapped[bool] = mapped_column(Boolean)
    formula_id: Mapped[str | None] = mapped_column(ForeignKey("core.formulas.formula_id"))
    source_id: Mapped[int] = mapped_column(ForeignKey("core.data_sources.source_id"))
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)


class Metric(Base):
    __tablename__ = "metrics"
    __table_args__ = {"schema": "core"}
    # UNIQUE (company_id, metric_name, period_end_date, period_type) in SQL.
    company_id: Mapped[int] = mapped_column(ForeignKey("core.companies.company_id"),
                                            primary_key=True)
    period_end_date: Mapped[date] = mapped_column(Date, primary_key=True)
    period_type: Mapped[str] = mapped_column(Text, primary_key=True)
    metric_name: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float | None] = mapped_column(Double)
    na_reason: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text)
    formula_id: Mapped[str | None] = mapped_column(ForeignKey("core.formulas.formula_id"))
    as_of_date: Mapped[date] = mapped_column(Date)
    input_fields: Mapped[dict | None] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(Timestamp)


class SourceReportedMetric(Base):
    __tablename__ = "source_reported_metrics"
    __table_args__ = {"schema": "core"}
    company_id: Mapped[int] = mapped_column(ForeignKey("core.companies.company_id"),
                                            primary_key=True)
    metric_name: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float | None] = mapped_column(Double)
    as_of_date: Mapped[date] = mapped_column(Date, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("core.data_sources.source_id"))
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)


# ------------------------------------------------------------- staging ----

class StagingCompanyMetadata(Base):
    __tablename__ = "company_metadata"
    __table_args__ = {"schema": "staging"}
    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    source_name: Mapped[str | None] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(Text)
    source_sector: Mapped[str | None] = mapped_column(Text)
    source_industry: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    financial_currency: Mapped[str | None] = mapped_column(Text)
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(24, 2))
    shares_as_of_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)
    raw_file: Mapped[str | None] = mapped_column(Text)


class StagingMarketPrice(Base):
    __tablename__ = "market_prices"
    __table_args__ = {"schema": "staging"}
    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    high: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    low: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    adj_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    is_stale_quote: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)
    raw_file: Mapped[str | None] = mapped_column(Text)


class StagingFxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = {"schema": "staging"}
    currency: Mapped[str] = mapped_column(Text, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    source: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)
    raw_file: Mapped[str | None] = mapped_column(Text)


class StagingSourceReportedMetric(Base):
    __tablename__ = "source_reported_metrics"
    __table_args__ = {"schema": "staging"}
    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    metric_name: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float | None] = mapped_column(Double)
    as_of_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)
    raw_file: Mapped[str | None] = mapped_column(Text)


class StagingFinancialStatement(Base):
    __tablename__ = "financial_statements"
    __table_args__ = {"schema": "staging"}
    ticker: Mapped[str] = mapped_column(Text, primary_key=True)
    statement: Mapped[str] = mapped_column(Text, primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(Integer)
    fiscal_quarter: Mapped[int | None] = mapped_column(SmallInteger)
    period_end_date: Mapped[date] = mapped_column(Date, primary_key=True)
    period_type: Mapped[str] = mapped_column(Text, primary_key=True)
    line_item: Mapped[str] = mapped_column(Text, primary_key=True)
    source_label: Mapped[str | None] = mapped_column(Text)
    value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
    missing_reason: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text)
    original_unit: Mapped[str | None] = mapped_column(Text)
    original_currency: Mapped[str] = mapped_column(Text)
    original_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 6))
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    is_calculated: Mapped[bool] = mapped_column(Boolean)
    formula_id: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(Timestamp)
    raw_file: Mapped[str | None] = mapped_column(Text)
