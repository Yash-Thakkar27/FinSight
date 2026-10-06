"""Run ingestion for the universe and write a manifest of what was saved.

For each company: metadata, daily prices, and six statement tables.
For the benchmark: daily prices only.

A failure in one dataset or ticker is logged and recorded; the run continues.
The manifest (data/raw/_manifests/{run timestamp}.json) lists every dataset's
outcome, including empty responses and failures, so later steps can tell
"the source has no such data" apart from "retrieval failed".
"""

import json
import logging
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import yfinance as yf

from config.settings import RAW_DIR, Universe, get_ingestion_settings
from src.ingestion import raw_store
from src.ingestion.common import SOURCE, IngestResult
from src.ingestion.company_metadata import ingest_metadata
from src.ingestion.financial_data import STATEMENT_ATTRIBUTES, ingest_statement
from src.ingestion.market_data import ingest_prices

log = logging.getLogger(__name__)

MANIFEST_DIR_NAME = "_manifests"


def ingest_ticker(ticker: str, years: int, with_fundamentals: bool,
                  raw_dir: Path = RAW_DIR, pause: float = 0.0) -> list[IngestResult]:
    """Fetch and save every dataset for one ticker. Never raises."""
    results: list[IngestResult] = []
    try:
        ticker_obj = yf.Ticker(ticker)
        results.append(ingest_prices(ticker, ticker_obj, years, raw_dir))
        if with_fundamentals:
            time.sleep(pause)
            results.append(ingest_metadata(ticker, ticker_obj, raw_dir))
            for statement, period_type in STATEMENT_ATTRIBUTES:
                time.sleep(pause)
                results.append(
                    ingest_statement(ticker, ticker_obj, statement, period_type, raw_dir)
                )
    except Exception as exc:  # anything unexpected: record it and move on
        log.exception("%s: unexpected ingestion error", ticker)
        results.append(IngestResult(ticker=ticker, dataset="*", period_type="*",
                                    status="failed", error=f"{type(exc).__name__}: {exc}"))

    for r in results:
        if r.status == "failed":
            log.error("%s %s: FAILED (%s)", r.ticker, r.dataset, r.error)
        elif r.status == "empty":
            log.warning("%s %s: source returned no data", r.ticker, r.dataset)
    return results


def write_manifest(results: list[IngestResult], started_at, finished_at,
                   requested: list[str], raw_dir: Path = RAW_DIR) -> Path:
    folder = raw_dir / MANIFEST_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{started_at.strftime(raw_store.TIMESTAMP_FORMAT)}.json"
    document = {
        "source": SOURCE,
        "yfinance_version": yf.__version__,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "tickers_requested": requested,
        "status_counts": dict(Counter(r.status for r in results)),
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def run_ingestion(universe: Universe, tickers: list[str] | None = None,
                  raw_dir: Path = RAW_DIR) -> tuple[list[IngestResult], Path]:
    """Ingest the whole universe (or the given tickers) plus the benchmark."""
    settings = get_ingestion_settings()
    company_tickers = universe.tickers
    if tickers:
        unknown = sorted(set(tickers) - set(company_tickers) - {universe.benchmark.ticker})
        if unknown:
            raise ValueError(f"tickers not in config/universe.yaml: {unknown}")
        company_tickers = [t for t in company_tickers if t in tickers]
    include_benchmark = not tickers or universe.benchmark.ticker in tickers

    started_at = raw_store.utc_now()
    results: list[IngestResult] = []
    for ticker in company_tickers:
        results += ingest_ticker(ticker, universe.price_history_years, True,
                                 raw_dir, settings.pause_seconds)
        time.sleep(settings.pause_seconds)
    if include_benchmark:
        results += ingest_ticker(universe.benchmark.ticker, universe.price_history_years,
                                 False, raw_dir, settings.pause_seconds)

    requested = company_tickers + ([universe.benchmark.ticker] if include_benchmark else [])
    manifest = write_manifest(results, started_at, raw_store.utc_now(), requested, raw_dir)

    counts = Counter(r.status for r in results)
    price_rows = sum(r.rows for r in results if r.dataset == "market_prices")
    n_price_tickers = sum(1 for r in results if r.dataset == "market_prices" and r.status == "ok")
    log.info("Retrieved %s price rows for %d tickers", f"{price_rows:,}", n_price_tickers)
    log.info("Ingestion: %d datasets saved, %d empty at source, %d failed",
             counts["ok"], counts["empty"], counts["failed"])
    log.info("Manifest: %s", manifest)
    return results, manifest
