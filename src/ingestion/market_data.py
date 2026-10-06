"""Daily market data: OHLC, adjusted close and volume, for companies and the benchmark."""

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

from config.settings import RAW_DIR
from src.ingestion import raw_store
from src.ingestion.common import SOURCE, EmptyResponseError, IngestResult, fetch_with_retry

log = logging.getLogger(__name__)

DATASET = "market_prices"
PERIOD_TYPE = "daily"


def fetch_prices(ticker_obj: yf.Ticker, years: int) -> pd.DataFrame:
    """Daily price history as returned by the source.

    auto_adjust=False keeps both the raw Close and the Adj Close columns.
    An empty frame is an error here: every ticker in the universe must have prices.
    """
    df = ticker_obj.history(period=f"{years}y", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        raise EmptyResponseError(f"no price rows returned for {ticker_obj.ticker}")
    return df


def ingest_prices(ticker: str, ticker_obj: yf.Ticker, years: int,
                  raw_dir: Path = RAW_DIR) -> IngestResult:
    result = IngestResult(ticker=ticker, dataset=DATASET, period_type=PERIOD_TYPE, status="failed")
    try:
        df = fetch_with_retry(lambda: fetch_prices(ticker_obj, years), f"{ticker} {DATASET}")
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    retrieved_at = raw_store.utc_now()
    meta = raw_store.make_meta(SOURCE, DATASET, ticker, PERIOD_TYPE, retrieved_at)
    path = raw_store.save_frame(df, meta, raw_dir)
    result.status = "ok"
    result.rows, result.columns = df.shape
    result.path = str(path)
    result.retrieved_at = meta.retrieved_at
    return result
