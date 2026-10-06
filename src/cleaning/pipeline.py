"""Cleaning pipeline: raw snapshots -> tidy, typed tables ready for validation and loading.

Reads only from data/raw (never the network) and is idempotent: the same raw
files always produce the same output. Conventions (also in docs/methodology.md):

  * Prices: timezone-naive dates; the latest snapshot only, because adjusted
    close is re-based by the source whenever a dividend or split occurs and
    mixing snapshots would mix bases. One exception: a row the latest snapshot
    returned incomplete (a missing close, say) is taken from an earlier snapshot
    if that snapshot is on the same adjustment basis; otherwise it is rejected.
  * Statements: long format, canonical line items from config/field_map.yaml.
    All snapshots are combined and the latest retrieval wins per line item and
    period, so history accumulates as the source drops old periods.
  * Missing values are NULL with a reason, never 0:
        not_applicable           field is not meaningful for the sector type
        failed_retrieval         the dataset could not be fetched
        unavailable_from_source  the source simply does not have it
  * Money is absolute INR in `value`. `original_value` holds the figure exactly
    as reported, in the reporting currency, and is never overwritten. For a
    foreign-currency reporter `value` is a translation: income-statement and
    cash-flow items at the period-average rate, balance-sheet items at the
    period-end rate, with the rate, its type and its source stored alongside.
    Ratios, margins and growth are later computed from original_value.
  * Signs: capex is a positive outflow; debt is positive.
  * Nothing is deleted for being unusual. Placeholder price rows are flagged.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from config.settings import CONFIG_DIR, PROCESSED_DIR, RAW_DIR, Company, Universe
from src.cleaning.fiscal import fiscal_quarter, fiscal_year, period_start
from src.ingestion import raw_store
from src.ingestion.common import SOURCE
from src.ingestion.financial_data import dataset_name
from src.ingestion.market_data import DATASET as PRICE_DATASET
from src.ingestion.market_data import FX_DATASET

log = logging.getLogger(__name__)

STATEMENTS = ("income", "balance", "cashflow")
PERIOD_TYPES = ("annual", "quarterly")

PRICE_KEY = ["ticker", "date"]
STATEMENT_KEY = ["ticker", "statement", "line_item", "period_end_date", "period_type"]

PRICE_COLUMNS = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume",
                 "is_stale_quote", "source", "retrieved_at", "raw_file"]
STATEMENT_COLUMNS = [
    "ticker", "statement", "fiscal_year", "fiscal_quarter", "period_end_date", "period_type",
    "line_item", "source_label", "value", "missing_reason", "currency", "unit",
    "original_unit", "original_currency", "original_value", "fx_rate", "fx_rate_type",
    "fx_source", "is_calculated", "formula_id", "source", "retrieved_at", "raw_file",
]

# Fields derived when the source omits them. Registered in core.formulas.
DERIVED_FORMULAS = {
    "free_cash_flow": {
        "formula_id": "DERIVED_FCF",
        "statement": "cashflow",
        "expression": "operating_cash_flow - capex",
        "description": "Free cash flow derived when the source omits it. "
                       "Capex is stored as a positive outflow.",
        "inputs": [("cashflow", "operating_cash_flow", 1), ("cashflow", "capex", -1)],
    },
    "ebitda": {
        "formula_id": "DERIVED_EBITDA",
        "statement": "income",
        "expression": "ebit + depreciation_amortization",
        "description": "EBITDA derived when the source omits it.",
        "inputs": [("income", "ebit", 1), ("income", "depreciation_amortization", 1)],
    },
}

# FX quotes may be at most this many days away from a period boundary.
FX_MAX_STALENESS_DAYS = 7


@dataclass
class CleanedData:
    prices: pd.DataFrame
    statements: pd.DataFrame
    metadata: pd.DataFrame
    source_metrics: pd.DataFrame
    fx: pd.DataFrame
    splits: pd.DataFrame
    # Inputs kept for validation: rows before de-duplication.
    prices_before_dedup: pd.DataFrame
    statement_observations: pd.DataFrame
    # Incomplete price rows that could not be repaired. Not loaded; reported as errors.
    rejected_prices: pd.DataFrame = field(default_factory=pd.DataFrame)
    duplicates_removed: int = 0
    # Problems found while cleaning: {ticker, kind, severity, dataset, record_key, message}
    issues: list[dict] = field(default_factory=list)


def load_field_map(path: Path = CONFIG_DIR / "field_map.yaml") -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Prices and FX
# --------------------------------------------------------------------------

def flag_stale_quotes(prices: pd.DataFrame) -> pd.Series:
    """True for placeholder rows: zero volume and all four prices equal to the previous close.

    The source emits these on exchange holidays. They are not real trades, so
    they would add artificial zero returns if treated as observations.
    """
    prices = prices.sort_values(PRICE_KEY)
    previous_close = prices.groupby("ticker")["close"].shift(1)
    flat = (
        (prices["open"] == prices["close"])
        & (prices["high"] == prices["close"])
        & (prices["low"] == prices["close"])
    )
    return (prices["volume"] == 0) & flat & (prices["close"] == previous_close)


def map_prices(raw: pd.DataFrame, ticker: str, retrieved_at: str, raw_file: str,
               column_map: dict) -> pd.DataFrame:
    """Rename source columns and make dates timezone-naive. No rows are dropped here."""
    df = raw.rename(columns=column_map)[list(column_map.values())].copy()
    # The source stamps each row at local midnight (Asia/Kolkata): dropping the
    # timezone keeps the local trading date.
    df.insert(0, "date", pd.DatetimeIndex(raw.index).tz_localize(None).date)
    df.insert(0, "ticker", ticker)
    df["volume"] = df["volume"].astype("Int64")
    df["source"] = SOURCE
    df["retrieved_at"] = pd.Timestamp(retrieved_at)
    df["raw_file"] = raw_file
    return df.reset_index(drop=True)


SPLIT_COLUMNS = ["ticker", "date", "split_ratio", "source", "retrieved_at", "raw_file"]


def clean_splits(raw: pd.DataFrame, ticker: str, retrieved_at: str, raw_file: str) -> pd.DataFrame:
    """Splits and bonus issues from a raw price table: one row per date with a non-zero ratio.

    The source reports them in the "Stock Splits" column of the price history
    (2.0 for a 2-for-1 split or a 1:1 bonus), so coverage is the price window.
    """
    if "Stock Splits" not in raw.columns:
        return pd.DataFrame(columns=SPLIT_COLUMNS)
    ratios = raw["Stock Splits"]
    ratios = ratios[ratios.notna() & (ratios != 0)]
    return pd.DataFrame({
        "ticker": ticker,
        "date": pd.DatetimeIndex(ratios.index).tz_localize(None).date,
        "split_ratio": ratios.to_numpy(dtype="float64"),
        "source": SOURCE,
        "retrieved_at": pd.Timestamp(retrieved_at),
        "raw_file": raw_file,
    }, columns=SPLIT_COLUMNS)


def deduplicate(df: pd.DataFrame, key: list[str]) -> tuple[pd.DataFrame, int]:
    """Keep the latest retrieval per natural key. Returns (frame, rows removed)."""
    ordered = df.sort_values(key + ["retrieved_at"], kind="stable")
    deduped = ordered.drop_duplicates(subset=key, keep="last").reset_index(drop=True)
    return deduped, len(df) - len(deduped)


REQUIRED_PRICE_FIELDS = ["open", "high", "low", "close", "adj_close", "volume"]
ADJUSTMENT_BASIS_TOLERANCE = 1e-6


def same_adjustment_basis(latest: pd.DataFrame, earlier: pd.DataFrame, before: date) -> bool:
    """True if two snapshots agree on adjusted close for the last shared date before `before`.

    The source re-bases the whole adjusted-close history after a dividend or
    split. If the two snapshots agree just before the row in question, no
    re-basing happened between them and a row can safely be taken from either.
    """
    def adjusted(frame):
        has_all_fields = frame[REQUIRED_PRICE_FIELDS].notna().all(axis=1)
        complete = frame[has_all_fields & (frame["date"] < before)]
        return complete.drop_duplicates("date", keep="last").set_index("date")["adj_close"]

    a, b = adjusted(latest), adjusted(earlier)
    shared = a.index.intersection(b.index)
    if shared.empty:
        return False
    last = shared.max()
    return abs(a[last] - b[last]) <= ADJUSTMENT_BASIS_TOLERANCE * abs(a[last])


def repair_incomplete_rows(latest: pd.DataFrame, earlier_snapshots: list[pd.DataFrame]):
    """Replace incomplete rows of the latest snapshot with complete rows from earlier ones.

    latest: mapped price rows of one ticker from its latest snapshot.
    earlier_snapshots: mapped rows from that ticker's older snapshots, newest first.
    Returns (rows to keep, rows repaired, rows rejected). A repaired row keeps
    the retrieved_at and raw_file of the snapshot it came from. Rows that cannot
    be repaired are rejected: an observation without a close is not usable.
    """
    incomplete = latest[REQUIRED_PRICE_FIELDS].isna().any(axis=1)
    if not incomplete.any():
        return latest, latest.iloc[0:0], latest.iloc[0:0]

    replacements, rejected_index = [], []
    for index, row in latest[incomplete].iterrows():
        for earlier in earlier_snapshots:
            candidate = earlier[(earlier["date"] == row["date"])
                                & earlier[REQUIRED_PRICE_FIELDS].notna().all(axis=1)]
            if len(candidate) and same_adjustment_basis(latest, earlier, row["date"]):
                replacements.append(candidate.iloc[[-1]])
                break
        else:
            rejected_index.append(index)

    repaired = (pd.concat(replacements, ignore_index=True) if replacements
                else latest.iloc[0:0])
    kept = pd.concat([latest[~incomplete], repaired], ignore_index=True)
    return kept.sort_values("date").reset_index(drop=True), repaired, latest.loc[rejected_index]


def clean_prices(mapped: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """De-duplicate mapped price rows and flag placeholder rows."""
    prices, removed = deduplicate(mapped, PRICE_KEY)
    prices["is_stale_quote"] = flag_stale_quotes(prices).reindex(prices.index).fillna(False)
    return prices[PRICE_COLUMNS], removed


def clean_fx(raw: pd.DataFrame, currency: str, retrieved_at: str, raw_file: str) -> pd.DataFrame:
    """Daily closing FX rate (INR per unit of currency), one row per date."""
    df = pd.DataFrame({
        "currency": currency,
        "date": pd.DatetimeIndex(raw.index).tz_localize(None).date,
        "rate": raw["Close"].to_numpy(),
        "source": SOURCE,
        "retrieved_at": pd.Timestamp(retrieved_at),
        "raw_file": raw_file,
    })
    df = df.dropna(subset=["rate"])
    return df.drop_duplicates(subset=["currency", "date"], keep="last").reset_index(drop=True)


def average_rate(rates: pd.Series, start: date, end: date) -> float | None:
    """Mean of daily rates from start to end inclusive.

    None unless the quotes span the period: the first must fall within a week
    of the start and the last within a week of the end. An average over part of
    a period would silently misstate the converted figure.
    """
    window = rates.loc[pd.Timestamp(start):pd.Timestamp(end)]
    if window.empty:
        return None
    late_start = (window.index[0] - pd.Timestamp(start)).days > FX_MAX_STALENESS_DAYS
    early_end = (pd.Timestamp(end) - window.index[-1]).days > FX_MAX_STALENESS_DAYS
    if late_start or early_end:
        return None
    return float(window.mean())


def closing_rate(rates: pd.Series, end: date) -> float | None:
    """Last rate on or before `end`, if it is at most a week old."""
    window = rates.loc[:pd.Timestamp(end)]
    if window.empty or (pd.Timestamp(end) - window.index[-1]).days > FX_MAX_STALENESS_DAYS:
        return None
    return float(window.iloc[-1])


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------

def observe_statement(raw: pd.DataFrame, statement: str, period_type: str, field_map: dict,
                      retrieved_at: str, raw_file: str) -> pd.DataFrame:
    """Pull the canonical line items out of one raw statement table.

    One row per (line item, period) that has a value. For each canonical field
    the first listed source label with a non-null value is used. Periods where
    the whole source column is empty carry no information and are skipped.
    """
    raw = raw[~raw.index.duplicated(keep="first")]
    periods = [c for c in raw.columns if raw[c].notna().any()]
    rows = []
    for line_item, spec in field_map[statement].items():
        labels = [label for label in spec["sources"] if label in raw.index]
        for period in periods:
            for label in labels:
                value = raw.at[label, period]
                if pd.notna(value):
                    rows.append({
                        "statement": statement,
                        "period_type": period_type,
                        "line_item": line_item,
                        "period_end_date": date.fromisoformat(period),
                        "value": float(value) * spec.get("sign", 1),
                        "source_label": label,
                        "retrieved_at": pd.Timestamp(retrieved_at),
                        "raw_file": raw_file,
                    })
                    break
    columns = ["statement", "period_type", "line_item", "period_end_date", "value",
               "source_label", "retrieved_at", "raw_file"]
    return pd.DataFrame(rows, columns=columns)


def missing_reason(spec: dict, sector_type: str, dataset_status: str | None) -> str:
    """Why a canonical field has no value."""
    if "applies_to" in spec and sector_type not in spec["applies_to"]:
        return "not_applicable"
    if dataset_status == "failed":
        return "failed_retrieval"
    return "unavailable_from_source"


def build_statement_grid(company: Company, observations: pd.DataFrame, field_map: dict,
                         dataset_status: dict, fallback_retrieved_at: pd.Timestamp) -> pd.DataFrame:
    """One row per canonical field per known period, with value or missing reason.

    `observations` are de-duplicated rows from observe_statement for this company.
    Known periods are every period end that has at least one value in any of the
    three statements (per period type), so a statement the source did not return
    still gets rows, each marked with why it is missing.
    `dataset_status` maps a dataset name to 'ok' | 'empty' | 'failed' | None.
    """
    rows = []
    for period_type in PERIOD_TYPES:
        of_type = observations[observations["period_type"] == period_type]
        known_periods = sorted(of_type["period_end_date"].unique())
        for statement in STATEMENTS:
            observed = of_type[of_type["statement"] == statement]
            lookup = {(r.line_item, r.period_end_date): r for r in observed.itertuples()}
            status = dataset_status.get(dataset_name(statement, period_type))
            latest = observed["retrieved_at"].max() if len(observed) else fallback_retrieved_at
            for line_item, spec in field_map[statement].items():
                applicable = ("applies_to" not in spec
                              or company.sector_type in spec["applies_to"])
                for period_end in known_periods:
                    hit = lookup.get((line_item, period_end)) if applicable else None
                    rows.append({
                        "ticker": company.ticker,
                        "statement": statement,
                        "fiscal_year": fiscal_year(period_end),
                        "fiscal_quarter": (fiscal_quarter(period_end)
                                           if period_type == "quarterly" else None),
                        "period_end_date": period_end,
                        "period_type": period_type,
                        "line_item": line_item,
                        "source_label": hit.source_label if hit else None,
                        "value": hit.value if hit else None,
                        "missing_reason": (None if hit else
                                           missing_reason(spec, company.sector_type, status)),
                        "currency": "INR",
                        "unit": spec["unit"],
                        "original_unit": spec["unit"].replace("INR", company.statement_currency),
                        "original_currency": company.statement_currency,
                        "original_value": hit.value if hit else None,
                        "fx_rate": None,
                        "fx_rate_type": None,
                        "fx_source": None,
                        "is_calculated": False,
                        "formula_id": None,
                        "source": SOURCE,
                        "retrieved_at": hit.retrieved_at if hit else latest,
                        "raw_file": hit.raw_file if hit else None,
                    })
    grid = pd.DataFrame(rows, columns=STATEMENT_COLUMNS)
    grid["fiscal_quarter"] = grid["fiscal_quarter"].astype("Int64")
    grid["value"] = grid["value"].astype("float64")
    grid["original_value"] = grid["original_value"].astype("float64")
    grid["fx_rate"] = grid["fx_rate"].astype("float64")
    grid["fx_rate_type"] = grid["fx_rate_type"].astype("object")
    grid["fx_source"] = grid["fx_source"].astype("object")
    return grid


def translate_to_inr(statements: pd.DataFrame, rates: pd.Series, currency: str,
                     fx_source: str) -> list[dict]:
    """Translate a foreign-currency reporter's monetary rows to INR, in place.

    value = original_value * rate. Balance-sheet items use the period-end rate
    ('period_end'); income-statement and cash-flow items, including per-share
    amounts, use the period-average rate ('average'). Share counts are not money
    and are left alone. original_value is never changed. A value whose rate
    cannot be found gets a NULL `value` (unavailable_from_source) and is
    reported as an issue; its original_value stays.
    """
    issues = []
    monetary = (statements["original_value"].notna()
                & statements["unit"].isin(["INR", "INR_per_share"]))
    for index, row in statements[monetary].iterrows():
        if row["statement"] == "balance":
            rate, rate_type = closing_rate(rates, row["period_end_date"]), "period_end"
        else:
            start = period_start(row["period_end_date"], row["period_type"])
            rate, rate_type = average_rate(rates, start, row["period_end_date"]), "average"
        if rate is None:
            statements.loc[index, "value"] = None
            statements.loc[index, "missing_reason"] = "unavailable_from_source"
            issues.append({
                "ticker": row["ticker"], "kind": "fx_rate_missing", "severity": "error",
                "dataset": "financial_statements",
                "record_key": "|".join(str(row[k]) for k in STATEMENT_KEY),
                "message": f"no {currency}/INR rate for period ending {row['period_end_date']}; "
                           "the INR value is NULL (the reported value is kept)",
            })
            continue
        statements.loc[index, "value"] = row["original_value"] * rate
        statements.loc[index, "fx_rate"] = rate
        statements.loc[index, "fx_rate_type"] = rate_type
        statements.loc[index, "fx_source"] = fx_source
    return issues


def add_derived_fields(statements: pd.DataFrame) -> int:
    """Fill a missing field that is mathematically derivable, in place. Returns rows derived.

    Runs before any currency translation, so the arithmetic is in the reporting
    currency and the result is stored as the row's original_value. Only fills
    fields that are applicable (never overrides not_applicable) and only when
    every input is present. Derived rows are marked is_calculated.
    """
    derived = 0
    values = statements.set_index(STATEMENT_KEY)["value"]
    for line_item, formula in DERIVED_FORMULAS.items():
        targets = statements[
            (statements["line_item"] == line_item)
            & (statements["statement"] == formula["statement"])
            & statements["value"].isna()
            & (statements["missing_reason"] != "not_applicable")
        ]
        for index, row in targets.iterrows():
            inputs = [
                values.get((row["ticker"], stmt, item, row["period_end_date"], row["period_type"]))
                for stmt, item, _ in formula["inputs"]
            ]
            if any(v is None or pd.isna(v) for v in inputs):
                continue
            total = sum(v * sign for v, (_, _, sign) in zip(inputs, formula["inputs"]))
            statements.loc[index, "value"] = total
            statements.loc[index, "original_value"] = total
            statements.loc[index, "missing_reason"] = None
            statements.loc[index, "is_calculated"] = True
            statements.loc[index, "formula_id"] = formula["formula_id"]
            derived += 1
    return derived


def clean_company_statements(company: Company, snapshots: dict, field_map: dict,
                             dataset_status: dict, fx_rates: dict,
                             fallback_retrieved_at: pd.Timestamp,
                             fx_sources: dict | None = None):
    """All statement rows for one company.

    snapshots: {(statement, period_type): [(raw_frame, retrieved_at, raw_file), ...]}
    Returns (statements, observations before de-duplication, duplicates removed, issues).
    """
    frames = [
        observe_statement(raw, statement, period_type, field_map, retrieved_at, raw_file)
        for (statement, period_type), items in snapshots.items()
        for raw, retrieved_at, raw_file in items
    ]
    columns = ["statement", "period_type", "line_item", "period_end_date", "value",
               "source_label", "retrieved_at", "raw_file"]
    observations = (pd.concat(frames, ignore_index=True) if frames
                    else pd.DataFrame(columns=columns))
    observations.insert(0, "ticker", company.ticker)

    latest, removed = deduplicate(observations, STATEMENT_KEY)
    statements = build_statement_grid(company, latest, field_map, dataset_status,
                                      fallback_retrieved_at)
    # Derive in the reporting currency first, then translate.
    add_derived_fields(statements)
    issues = []
    if company.statement_currency != "INR":
        currency = company.statement_currency
        issues = translate_to_inr(statements, fx_rates[currency], currency,
                                  (fx_sources or {}).get(currency, "unspecified"))
    statements = statements.sort_values(STATEMENT_KEY, kind="stable").reset_index(drop=True)
    return statements, observations, removed, issues


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------

def clean_metadata(info: dict, ticker: str, retrieved_at: str, raw_file: str,
                   field_map: dict) -> tuple[dict, list[dict]]:
    """One metadata row and the source-reported metric rows for a ticker."""
    retrieved = pd.Timestamp(retrieved_at)
    row = {"ticker": ticker}
    row.update({column: info.get(key) for key, column in field_map["company_metadata"].items()})
    # info has no history: the retrieval date is the as-of date for the share count.
    row["shares_as_of_date"] = retrieved.date() if row.get("shares_outstanding") else None
    row.update({"source": SOURCE, "retrieved_at": retrieved, "raw_file": raw_file})

    metrics = []
    for key, metric_name in field_map["source_reported_metrics"].items():
        value = info.get(key)
        if isinstance(value, (int, float)) and not pd.isna(value):
            metrics.append({
                "ticker": ticker, "metric_name": metric_name, "value": float(value),
                "as_of_date": retrieved.date(), "source": SOURCE,
                "retrieved_at": retrieved, "raw_file": raw_file,
            })
    return row, metrics


# --------------------------------------------------------------------------
# Whole run
# --------------------------------------------------------------------------

def latest_dataset_status(raw_dir: Path) -> dict:
    """{(ticker, dataset): status} from ingestion manifests; later runs override earlier ones."""
    status = {}
    folder = raw_dir / "_manifests"
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            for result in json.loads(path.read_text(encoding="utf-8"))["results"]:
                status[(result["ticker"], result["dataset"])] = result["status"]
    return status


def _relative(path: Path, raw_dir: Path) -> str:
    return str(path.relative_to(raw_dir))


def run_cleaning(universe: Universe, raw_dir: Path = RAW_DIR,
                 processed_dir: Path | None = PROCESSED_DIR) -> CleanedData:
    """Clean every latest raw snapshot for the universe. Writes Parquet to processed_dir."""
    field_map = load_field_map()
    manifest_status = latest_dataset_status(raw_dir)
    issues: list[dict] = []

    # FX first: statements need it.
    fx_frames, fx_rates = [], {}
    fx_sources = {currency: f"{SOURCE} {fx.ticker} daily close"
                  for currency, fx in universe.fx.items()}
    for currency, fx_source in universe.fx.items():
        path = raw_store.latest_snapshot(SOURCE, FX_DATASET, fx_source.ticker, raw_dir)
        if path is None:
            issues.append({"ticker": fx_source.ticker, "kind": "snapshot_missing",
                           "severity": "error", "dataset": FX_DATASET,
                           "record_key": fx_source.ticker,
                           "message": f"no raw FX snapshot for {currency}"})
            fx_rates[currency] = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
            continue
        meta = raw_store.read_meta(path)
        frame = clean_fx(raw_store.read_frame(path), currency, meta.retrieved_at,
                         _relative(path, raw_dir))
        fx_frames.append(frame)
        fx_rates[currency] = pd.Series(frame["rate"].to_numpy(),
                                       index=pd.DatetimeIndex(frame["date"])).sort_index()
    fx = (pd.concat(fx_frames, ignore_index=True) if fx_frames
          else pd.DataFrame(columns=["currency", "date", "rate", "source", "retrieved_at",
                                     "raw_file"]))

    # Prices: companies and the benchmark.
    column_map = field_map["market_prices"]
    mapped_frames, kept_frames, rejected_frames, split_frames = [], [], [], []
    for ticker in universe.tickers + [universe.benchmark.ticker]:
        paths = raw_store.list_snapshots(SOURCE, PRICE_DATASET, ticker, raw_dir)
        if not paths:
            issues.append({"ticker": ticker, "kind": "snapshot_missing", "severity": "error",
                           "dataset": PRICE_DATASET, "record_key": ticker,
                           "message": "no raw price snapshot"})
            continue

        def mapped_snapshot(path, ticker=ticker):
            return map_prices(raw_store.read_frame(path), ticker,
                              raw_store.read_meta(path).retrieved_at,
                              _relative(path, raw_dir), column_map)

        latest = mapped_snapshot(paths[-1])
        mapped_frames.append(latest)
        if ticker != universe.benchmark.ticker:
            split_frames.append(clean_splits(raw_store.read_frame(paths[-1]), ticker,
                                             raw_store.read_meta(paths[-1]).retrieved_at,
                                             _relative(paths[-1], raw_dir)))
        if latest[REQUIRED_PRICE_FIELDS].isna().any(axis=1).any():
            earlier = [mapped_snapshot(p) for p in reversed(paths[:-1])]
            latest, repaired, rejected = repair_incomplete_rows(latest, earlier)
            for row in repaired.itertuples():
                issues.append({
                    "ticker": ticker, "kind": "price_row_repaired", "severity": "warning",
                    "dataset": PRICE_DATASET, "record_key": f"{ticker}|{row.date}",
                    "message": "latest snapshot returned this row incomplete; the complete row "
                               f"from the earlier snapshot {row.raw_file} is used",
                })
            for row in rejected.itertuples():
                missing = [c for c in REQUIRED_PRICE_FIELDS if pd.isna(getattr(row, c))]
                issues.append({
                    "ticker": ticker, "kind": "incomplete_price_row", "severity": "error",
                    "dataset": PRICE_DATASET, "record_key": f"{ticker}|{row.date}",
                    "message": f"source row is missing {', '.join(missing)} and no earlier "
                               "snapshot on the same adjustment basis has it; row not loaded",
                })
            rejected_frames.append(rejected)
        kept_frames.append(latest)
    prices_before_dedup = pd.concat(mapped_frames, ignore_index=True)
    prices, price_duplicates = clean_prices(pd.concat(kept_frames, ignore_index=True))
    rejected_prices = (pd.concat(rejected_frames, ignore_index=True) if rejected_frames
                       else prices.iloc[0:0])

    # Metadata and statements, per company.
    metadata_rows, metric_rows, statement_frames, observation_frames = [], [], [], []
    statement_duplicates = 0
    for company in universe.companies:
        ticker = company.ticker
        fallback = prices.loc[prices["ticker"] == ticker, "retrieved_at"].max()

        path = raw_store.latest_snapshot(SOURCE, "company_metadata", ticker, raw_dir)
        if path is None:
            issues.append({"ticker": ticker, "kind": "snapshot_missing", "severity": "error",
                           "dataset": "company_metadata", "record_key": ticker,
                           "message": "no raw metadata snapshot"})
        else:
            meta = raw_store.read_meta(path)
            row, metrics = clean_metadata(raw_store.read_json(path), ticker, meta.retrieved_at,
                                          _relative(path, raw_dir), field_map)
            metadata_rows.append(row)
            metric_rows += metrics

        snapshots, dataset_status = {}, {}
        for statement in STATEMENTS:
            for period_type in PERIOD_TYPES:
                dataset = dataset_name(statement, period_type)
                paths = raw_store.list_snapshots(SOURCE, dataset, ticker, raw_dir)
                # A snapshot on disk means the data is available, whatever a later run said.
                dataset_status[dataset] = "ok" if paths else manifest_status.get((ticker, dataset))
                snapshots[(statement, period_type)] = [
                    (raw_store.read_frame(p), raw_store.read_meta(p).retrieved_at,
                     _relative(p, raw_dir))
                    for p in paths
                ]
        statements, observations, removed, company_issues = clean_company_statements(
            company, snapshots, field_map, dataset_status, fx_rates, fallback, fx_sources
        )
        statement_frames.append(statements)
        observation_frames.append(observations)
        statement_duplicates += removed
        issues += company_issues

    cleaned = CleanedData(
        prices=prices,
        statements=pd.concat(statement_frames, ignore_index=True),
        metadata=pd.DataFrame(metadata_rows),
        source_metrics=pd.DataFrame(metric_rows),
        fx=fx,
        splits=(pd.concat(split_frames, ignore_index=True) if split_frames
                else pd.DataFrame(columns=SPLIT_COLUMNS)),
        prices_before_dedup=prices_before_dedup,
        statement_observations=pd.concat(observation_frames, ignore_index=True),
        rejected_prices=rejected_prices,
        duplicates_removed=price_duplicates + statement_duplicates,
        issues=issues,
    )

    if processed_dir is not None:
        processed_dir.mkdir(parents=True, exist_ok=True)
        cleaned.prices.to_parquet(processed_dir / "market_prices.parquet", index=False)
        cleaned.statements.to_parquet(processed_dir / "financial_statements.parquet", index=False)
        cleaned.metadata.to_parquet(processed_dir / "company_metadata.parquet", index=False)
        cleaned.source_metrics.to_parquet(
            processed_dir / "source_reported_metrics.parquet", index=False
        )
        cleaned.fx.to_parquet(processed_dir / "fx_rates.parquet", index=False)
        cleaned.splits.to_parquet(processed_dir / "stock_splits.parquet", index=False)

    statements = cleaned.statements
    log.info("Cleaned %s price rows for %d tickers (%d placeholder rows flagged)",
             f"{len(prices):,}", prices["ticker"].nunique(), int(prices["is_stale_quote"].sum()))
    log.info("Cleaned %s statement rows for %d companies: %s with values, %s missing "
             "(%s not applicable, %s unavailable, %s failed retrieval), %d derived",
             f"{len(statements):,}", statements["ticker"].nunique(),
             f"{int(statements['value'].notna().sum()):,}",
             f"{int(statements['value'].isna().sum()):,}",
             f"{int((statements['missing_reason'] == 'not_applicable').sum()):,}",
             f"{int((statements['missing_reason'] == 'unavailable_from_source').sum()):,}",
             f"{int((statements['missing_reason'] == 'failed_retrieval').sum()):,}",
             int(statements["is_calculated"].sum()))
    log.info("Splits and bonus issues in the price window: %d across %d companies",
             len(cleaned.splits), cleaned.splits["ticker"].nunique())
    log.info("Duplicates removed: %d (keeping the latest retrieval)", cleaned.duplicates_removed)
    repaired_rows = sum(1 for i in issues if i["kind"] == "price_row_repaired")
    if repaired_rows or len(rejected_prices):
        log.warning("Incomplete price rows in the latest snapshots: %d repaired from an earlier "
                    "snapshot, %d rejected", repaired_rows, len(rejected_prices))
    for company in universe.companies:
        if company.sector_type != "non_financial":
            log.warning("EBITDA unavailable for %s (not applicable: %s)",
                        company.ticker, company.sector_type)
    return cleaned
