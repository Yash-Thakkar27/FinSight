"""The refresh pipeline, step by step. scripts/update_data.py is a thin wrapper around this.

    1 fetch            src/ingestion     (skipped with skip_fetch)
    2 save raw         src/ingestion     (immutable snapshots under data/raw)
    3 clean            src/cleaning
    4 validate         src/validation
    5 upsert           src/database/load (staging -> core)
    6 recompute metrics   src/analytics
    7 regenerate exports  src/exports (Excel workbooks, Power BI star schema)
    8 write the pipeline_runs row

With skip_fetch the pipeline rebuilds everything from the raw snapshots already
on disk, which is how the project runs reproducibly offline.
"""

import logging
import time
from datetime import date
from pathlib import Path

from sqlalchemy import Engine

from config.settings import EXPORTS_DIR, PROCESSED_DIR, RAW_DIR, Universe
from src.analytics.compute import recompute_metrics
from src.analytics.peer_analytics import recompute_peer_analytics
from src.cleaning.pipeline import run_cleaning
from src.database import load
from src.database.setup import table_counts
from src.exports.run import generate_exports
from src.ingestion.run import run_ingestion
from src.validation import checks

log = logging.getLogger(__name__)


def run_pipeline(universe: Universe, engine: Engine, *, tickers: list[str] | None = None,
                 skip_fetch: bool = False, raw_dir: Path = RAW_DIR,
                 processed_dir: Path | None = PROCESSED_DIR,
                 exports_dir: Path | None = EXPORTS_DIR,
                 as_of: date | None = None) -> dict:
    """Run the pipeline. Returns the run id, status, quality summary and report text."""
    as_of = as_of or date.today()
    run_id = load.start_run(engine)
    log.info("Pipeline run %d started", run_id)
    failed_datasets = 0
    try:
        # Steps 1-2
        start = time.perf_counter()
        if skip_fetch:
            log.info("Steps 1-2 (fetch, save raw): skipped (--skip-fetch)")
        else:
            results, _ = run_ingestion(universe, tickers, raw_dir)
            failed_datasets = sum(1 for r in results if r.status == "failed")
            log.info("Steps 1-2 (fetch, save raw): %d datasets in %.1fs",
                     len(results), time.perf_counter() - start)

        # Step 3
        start = time.perf_counter()
        cleaned = run_cleaning(universe, raw_dir, processed_dir)
        log.info("Step 3 (clean): %.1fs", time.perf_counter() - start)

        # Step 4
        start = time.perf_counter()
        check_results = checks.run_all_checks(cleaned, universe.validation, as_of)
        summary = checks.summarize(check_results, cleaned)
        report = checks.format_report(check_results, summary, cleaned)
        log.info("Step 4 (validate): %d check results in %.1fs",
                 len(check_results), time.perf_counter() - start)
        log.info("Validation: %.2f%% of records passed", summary["pass_rate_pct"])

        # Step 5: one transaction, so the database never holds half a run
        start = time.perf_counter()
        with engine.begin() as conn:
            staged = load.load_staging(conn, cleaned)
            unmatched = load.unmatched_staging_tickers(conn)
            if unmatched:
                raise RuntimeError(
                    f"tickers not in core.companies (run scripts/init_db.py): {unmatched}"
                )
            written = load.upsert_core(conn)
            load.write_quality(conn, run_id, check_results, summary)
            counts = table_counts(conn)
        for name, rows in staged.items():
            log.info("Staged %s rows into %s", f"{rows:,}", name)
        for name, rows in written.items():
            log.info("Upserted %s rows into %s", f"{rows:,}", name)
        log.info("Step 5 (upsert to PostgreSQL): %.1fs", time.perf_counter() - start)

        # Step 6: metrics are rebuilt in full from the core tables
        start = time.perf_counter()
        with engine.begin() as conn:
            metric_counts = recompute_metrics(conn, universe)
            peer_counts = recompute_peer_analytics(conn, universe)
            counts = table_counts(conn)
        log.info("Computed %s metric rows (%s with a value, %s N/A) from %d registered formulas",
                 f"{metric_counts['metrics']:,}", f"{metric_counts['with_value']:,}",
                 f"{metric_counts['not_available']:,}", metric_counts["formulas"])
        log.info("Valuation reconciliation: %d figures compared with the source, %d flagged",
                 metric_counts["reconciled"], metric_counts["flagged"])
        log.info("Peer comparisons: %d rows, %d with a generated interpretation",
                 peer_counts["comparisons"], peer_counts["interpretations"])
        log.info("Potential data anomalies flagged: %d (%s)", peer_counts["anomalies"],
                 ", ".join(f"{dataset} {method}: {n}" for (dataset, method), n
                           in sorted(peer_counts["anomalies_by_dataset"].items())))
        log.info("Correlations: %d pairs stored for windows %s", peer_counts["correlations"],
                 ", ".join(peer_counts["correlation_windows"]))
        log.info("Step 6 (recompute metrics): %.1fs", time.perf_counter() - start)
        # Step 7: Excel workbooks and the Power BI star schema, from what is now stored
        start = time.perf_counter()
        exports = None
        if exports_dir is not None:
            with engine.connect() as conn:
                exports = generate_exports(conn, exports_dir)
            log.info("Step 7 (regenerate exports): %d workbooks, %d CSV files in %.1fs",
                     len(exports["workbooks"]), len(exports["powerbi_rows"]),
                     time.perf_counter() - start)
        else:
            log.info("Step 7 (regenerate exports): skipped (no export directory)")

        # Step 8
        status = "partial" if failed_datasets else "success"
        notes = (f"skip_fetch={skip_fetch}; failed datasets={failed_datasets}; "
                 f"invalid records={summary['invalid_records']}; "
                 f"warnings={summary['warning_count']}")
        load.finish_run(engine, run_id, status, summary["total_records"], notes)
        log.info("Step 8 (pipeline_runs): run %d finished with status '%s'", run_id, status)
        return {"run_id": run_id, "status": status, "summary": summary, "report": report,
                "table_counts": counts, "failed_datasets": failed_datasets,
                "metric_counts": metric_counts, "peer_counts": peer_counts,
                "exports": exports}
    except Exception as exc:
        log.exception("Pipeline run %d failed", run_id)
        load.finish_run(engine, run_id, "failed", None, f"{type(exc).__name__}: {exc}"[:500])
        raise
