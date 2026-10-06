"""Shared ingestion pieces: retry with backoff, and the per-dataset result record."""

import logging
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from config.settings import IngestionSettings, get_ingestion_settings

log = logging.getLogger(__name__)

SOURCE = "yfinance"

T = TypeVar("T")


class EmptyResponseError(Exception):
    """The source answered but returned no rows for a dataset that must have some."""


@dataclass
class IngestResult:
    """Outcome of fetching one dataset for one ticker.

    status:
      ok      - response saved to a raw snapshot
      empty   - source returned nothing (later classified unavailable_from_source)
      failed  - all attempts raised (later classified failed_retrieval)
    """

    ticker: str
    dataset: str
    period_type: str
    status: str
    rows: int = 0
    columns: int = 0
    path: str | None = None
    retrieved_at: str | None = None
    error: str | None = None


def is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return "ratelimit" in text or "too many requests" in text or "429" in text


def fetch_with_retry(fn: Callable[[], T], what: str,
                     settings: IngestionSettings | None = None,
                     sleep: Callable[[float], None] | None = None) -> T:
    """Call fn, retrying on any exception with exponential backoff.

    Waits backoff_seconds * 2^(attempt - 1) between attempts, or at least
    rate_limit_wait_seconds when the source signals rate limiting.
    Re-raises the last exception once max_attempts is exhausted.
    """
    settings = settings or get_ingestion_settings()
    sleep = sleep or time.sleep
    for attempt in range(1, settings.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == settings.max_attempts:
                log.error("%s: failed after %d attempts: %s: %s",
                          what, attempt, type(exc).__name__, exc)
                raise
            wait = settings.backoff_seconds * 2 ** (attempt - 1)
            if is_rate_limit(exc):
                wait = max(wait, settings.rate_limit_wait_seconds)
            log.warning("%s: attempt %d/%d failed (%s: %s); retrying in %.0fs",
                        what, attempt, settings.max_attempts, type(exc).__name__, exc, wait)
            sleep(wait)
    raise AssertionError("unreachable")
