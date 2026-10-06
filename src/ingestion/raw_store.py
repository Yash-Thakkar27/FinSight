"""Immutable raw snapshot store.

Every source response is written once, unchanged, to

    data/raw/{source}/{dataset}/{ticker}/{retrieved_at}.parquet   (tables)
    data/raw/{source}/{dataset}/{ticker}/{retrieved_at}.json      (dict responses)

with metadata (source, dataset, ticker, period_type, retrieved_at in UTC)
stored inside the file. Files are never overwritten: writing to an existing
path raises, and files are made read-only after writing.

The only change made to a response is what the file format requires:
statement column labels (period-end timestamps) become ISO date strings,
because Parquet column names must be strings. Values are untouched.
"""

import json
import os
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from config.settings import RAW_DIR

METADATA_KEY = b"finsight"
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True)
class SnapshotMeta:
    source: str
    dataset: str
    ticker: str
    period_type: str      # daily | annual | quarterly | point_in_time
    retrieved_at: str     # UTC, ISO 8601


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def make_meta(source: str, dataset: str, ticker: str, period_type: str,
              retrieved_at: datetime) -> SnapshotMeta:
    if retrieved_at.tzinfo is None:
        raise ValueError("retrieved_at must be timezone-aware (UTC)")
    return SnapshotMeta(source, dataset, ticker, period_type,
                        retrieved_at.astimezone(timezone.utc).isoformat())


def snapshot_path(meta: SnapshotMeta, extension: str, raw_dir: Path = RAW_DIR) -> Path:
    stamp = datetime.fromisoformat(meta.retrieved_at).strftime(TIMESTAMP_FORMAT)
    return raw_dir / meta.source / meta.dataset / meta.ticker / f"{stamp}.{extension}"


def _prepare(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"raw snapshot already exists and is immutable: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def _make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def save_frame(df: pd.DataFrame, meta: SnapshotMeta, raw_dir: Path = RAW_DIR) -> Path:
    """Write a DataFrame snapshot as Parquet with the metadata in the file footer."""
    path = snapshot_path(meta, "parquet", raw_dir)
    _prepare(path)
    table = pa.Table.from_pandas(df, preserve_index=True)
    footer = dict(table.schema.metadata or {})
    footer[METADATA_KEY] = json.dumps(asdict(meta)).encode("utf-8")
    pq.write_table(table.replace_schema_metadata(footer), path)
    _make_read_only(path)
    return path


def save_json(payload: dict, meta: SnapshotMeta, raw_dir: Path = RAW_DIR) -> Path:
    """Write a dict response as JSON: {"metadata": {...}, "payload": <response>}."""
    path = snapshot_path(meta, "json", raw_dir)
    _prepare(path)
    document = {"metadata": asdict(meta), "payload": payload}
    path.write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")
    _make_read_only(path)
    return path


def read_meta(path: Path | str) -> SnapshotMeta:
    path = Path(path)
    if path.suffix == ".json":
        return SnapshotMeta(**json.loads(path.read_text(encoding="utf-8"))["metadata"])
    footer = pq.read_schema(path).metadata or {}
    return SnapshotMeta(**json.loads(footer[METADATA_KEY]))


def read_frame(path: Path | str) -> pd.DataFrame:
    return pd.read_parquet(path)


def read_json(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["payload"]


def list_snapshots(source: str, dataset: str, ticker: str,
                   raw_dir: Path = RAW_DIR) -> list[Path]:
    """All snapshots for a ticker and dataset, oldest first.

    File names are UTC timestamps in a sortable format, so name order is time order.
    """
    folder = raw_dir / source / dataset / ticker
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix in (".parquet", ".json"))


def latest_snapshot(source: str, dataset: str, ticker: str,
                    raw_dir: Path = RAW_DIR) -> Path | None:
    """Most recent snapshot for a ticker and dataset, or None if there is none."""
    files = list_snapshots(source, dataset, ticker, raw_dir)
    return files[-1] if files else None
