"""Company metadata: name, exchange, sector, industry, country, currency, shares outstanding.

The source returns one dict of current values (yfinance `Ticker.info`). It has no
history, so the snapshot's retrieved_at date is the as-of date for shares outstanding.
The whole dict is saved; the fields FinSight uses are listed in config/field_map.yaml.
"""

import logging
from pathlib import Path

import yfinance as yf

from config.settings import RAW_DIR
from src.ingestion import raw_store
from src.ingestion.common import SOURCE, EmptyResponseError, IngestResult, fetch_with_retry

log = logging.getLogger(__name__)

DATASET = "company_metadata"
PERIOD_TYPE = "point_in_time"


def fetch_info(ticker_obj: yf.Ticker) -> dict:
    info = ticker_obj.info
    # An unknown symbol comes back as a near-empty dict rather than an error.
    if not info or not (info.get("longName") or info.get("shortName")):
        raise EmptyResponseError(f"no metadata returned for {ticker_obj.ticker}")
    return info


def ingest_metadata(ticker: str, ticker_obj: yf.Ticker, raw_dir: Path = RAW_DIR) -> IngestResult:
    result = IngestResult(ticker=ticker, dataset=DATASET, period_type=PERIOD_TYPE, status="failed")
    try:
        info = fetch_with_retry(lambda: fetch_info(ticker_obj), f"{ticker} {DATASET}")
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    retrieved_at = raw_store.utc_now()
    meta = raw_store.make_meta(SOURCE, DATASET, ticker, PERIOD_TYPE, retrieved_at)
    path = raw_store.save_json(info, meta, raw_dir)
    result.status = "ok"
    result.rows, result.columns = 1, len(info)
    result.path = str(path)
    result.retrieved_at = meta.retrieved_at
    return result
