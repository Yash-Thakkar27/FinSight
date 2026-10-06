"""Financial statements: income statement, balance sheet and cash flow, annual and quarterly.

The source returns each statement as a table with one row per line item and one
column per period end. That table is saved as-is; mapping source labels to
canonical fields (config/field_map.yaml) happens later, in cleaning.
"""

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

from config.settings import RAW_DIR
from src.ingestion import raw_store
from src.ingestion.common import SOURCE, IngestResult, fetch_with_retry

log = logging.getLogger(__name__)

# (statement, period_type) -> yfinance Ticker attribute
STATEMENT_ATTRIBUTES = {
    ("income", "annual"): "income_stmt",
    ("income", "quarterly"): "quarterly_income_stmt",
    ("balance", "annual"): "balance_sheet",
    ("balance", "quarterly"): "quarterly_balance_sheet",
    ("cashflow", "annual"): "cashflow",
    ("cashflow", "quarterly"): "quarterly_cashflow",
}


def dataset_name(statement: str, period_type: str) -> str:
    return f"{statement}_{period_type}"


def to_storable(df: pd.DataFrame) -> pd.DataFrame:
    """Make a statement table Parquet-compatible without touching any value.

    Period-end column labels (timestamps) become ISO date strings and the row
    index is named `line_item`.
    """
    out = df.copy()
    out.columns = [pd.Timestamp(c).date().isoformat() for c in out.columns]
    out.index = out.index.astype(str)
    out.index.name = "line_item"
    return out


def fetch_statement(ticker_obj: yf.Ticker, statement: str, period_type: str) -> pd.DataFrame:
    df = getattr(ticker_obj, STATEMENT_ATTRIBUTES[(statement, period_type)])
    return df if df is not None else pd.DataFrame()


def ingest_statement(ticker: str, ticker_obj: yf.Ticker, statement: str, period_type: str,
                     raw_dir: Path = RAW_DIR) -> IngestResult:
    dataset = dataset_name(statement, period_type)
    result = IngestResult(ticker=ticker, dataset=dataset, period_type=period_type, status="failed")
    try:
        df = fetch_with_retry(
            lambda: fetch_statement(ticker_obj, statement, period_type), f"{ticker} {dataset}"
        )
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    if df.empty:
        # Not a failure: the source simply has no such statement for this company.
        result.status = "empty"
        return result

    retrieved_at = raw_store.utc_now()
    meta = raw_store.make_meta(SOURCE, dataset, ticker, period_type, retrieved_at)
    path = raw_store.save_frame(to_storable(df), meta, raw_dir)
    result.status = "ok"
    result.rows, result.columns = df.shape
    result.path = str(path)
    result.retrieved_at = meta.retrieved_at
    return result
