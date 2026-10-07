"""Project configuration.

Two sources:
- `.env`                  -> database credentials (DatabaseSettings)
- `config/universe.yaml`  -> companies, benchmark, risk-free rate (Universe)

Nothing about the universe is hard-coded anywhere else in the project.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
EXPORTS_DIR = DATA_DIR / "exports"
SQL_DIR = PROJECT_ROOT / "sql"
DOCS_DIR = PROJECT_ROOT / "docs"
LOG_DIR = PROJECT_ROOT / "logs"

SectorType = Literal["non_financial", "bank", "nbfc", "insurance"]


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    # Set to "require" for a hosted database (Neon, Supabase, ...). Unset for local Docker.
    postgres_sslmode: str | None = None

    @property
    def url(self) -> str:
        """SQLAlchemy URL using the psycopg (v3) driver.

        The user name and password are percent-encoded, so a generated password
        containing characters such as @ or / cannot break the URL.
        """
        url = (
            f"postgresql+psycopg://{quote_plus(self.postgres_user)}:"
            f"{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
        return f"{url}?sslmode={self.postgres_sslmode}" if self.postgres_sslmode else url


class IngestionSettings(BaseSettings):
    """Retry and pacing for source requests. Override with INGEST_* in .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_prefix="INGEST_", extra="ignore"
    )

    max_attempts: int = 4
    backoff_seconds: float = 2.0       # wait = backoff_seconds * 2^(attempt - 1)
    rate_limit_wait_seconds: float = 30.0
    pause_seconds: float = 0.4         # polite gap between requests


class Company(BaseModel):
    ticker: str
    name: str
    sector: str
    industry: str
    peer_group: str
    sector_type: SectorType
    statement_currency: str = "INR"


class FxSource(BaseModel):
    ticker: str
    history_years: int = 10


class ValidationThresholds(BaseModel):
    """Thresholds used by src/validation/checks.py (Section 8 of the spec)."""

    balance_tolerance: float = 0.02          # |assets - (liabilities + equity)| / assets
    max_abs_daily_return: float = 0.20       # flag larger moves for review
    max_gap_weekdays: int = 5                # flag gaps of more than 5 missing weekdays
    freshness_max_lag_days: int = 5          # latest price vs last weekday, calendar days
    completeness_warn_below: float = 0.80    # share of applicable fields present
    margin_bounds: tuple[float, float] = (-1.0, 1.0)
    eps_scale_bounds: tuple[float, float] = (0.2, 5.0)   # statement EPS / source trailing EPS


class AnomalySettings(BaseModel):
    """Thresholds for src/analytics/anomaly_detection.py.

    Conventional values, fixed before any results were examined.
    """

    iqr_multiplier: float = 1.5             # Tukey fences for fundamentals
    market_iqr_multiplier: float = 3.0      # "far out" fences for daily market data (fat tails)
    modified_zscore_threshold: float = 3.5  # Iglewicz & Hoaglin (1993)
    rolling_window: int = 60                # trading days of history behind each market observation
    rolling_min_periods: int = 30
    min_peer_group_size: int = 3            # members needed for a peer-group median


class Benchmark(BaseModel):
    ticker: str
    name: str


class RiskFreeRate(BaseModel):
    value: float | None = Field(default=None, ge=0, le=1)
    instrument: str | None = None
    source: str | None = None
    as_of_date: date | None = None

    @model_validator(mode="after")
    def _value_needs_provenance(self):
        if self.value is not None and not (self.instrument and self.source and self.as_of_date):
            raise ValueError("risk_free_rate.value requires instrument, source and as_of_date")
        return self

    def daily(self, trading_days: int = 252) -> float | None:
        """Daily compounding equivalent: (1 + r)^(1/trading_days) - 1. None if no rate is set."""
        if self.value is None:
            return None
        return (1 + self.value) ** (1 / trading_days) - 1


class Universe(BaseModel):
    benchmark: Benchmark
    risk_free_rate: RiskFreeRate
    trading_days_per_year: int = 252
    price_history_years: int = 5
    fx: dict[str, FxSource] = Field(default_factory=dict)
    validation: ValidationThresholds = Field(default_factory=ValidationThresholds)
    anomalies: AnomalySettings = Field(default_factory=AnomalySettings)
    companies: list[Company]

    @model_validator(mode="after")
    def _tickers_unique(self):
        tickers = [c.ticker for c in self.companies]
        duplicates = {t for t in tickers if tickers.count(t) > 1}
        if duplicates:
            raise ValueError(f"duplicate tickers in universe.yaml: {sorted(duplicates)}")
        return self

    @model_validator(mode="after")
    def _foreign_currencies_have_fx(self):
        needed = {c.statement_currency for c in self.companies} - {"INR"}
        missing = needed - set(self.fx)
        if missing:
            raise ValueError(f"no fx source configured for statement currencies: {sorted(missing)}")
        return self

    def company(self, ticker: str) -> Company:
        return next(c for c in self.companies if c.ticker == ticker)

    @property
    def tickers(self) -> list[str]:
        return [c.ticker for c in self.companies]


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()


@lru_cache
def get_ingestion_settings() -> IngestionSettings:
    return IngestionSettings()


@lru_cache
def get_universe(path: Path = CONFIG_DIR / "universe.yaml") -> Universe:
    with open(path, encoding="utf-8") as f:
        return Universe.model_validate(yaml.safe_load(f))
