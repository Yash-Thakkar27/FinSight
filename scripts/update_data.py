"""Refresh pipeline.

    python scripts/update_data.py                       # whole universe
    python scripts/update_data.py --tickers TCS.NS INFY.NS
    python scripts/update_data.py --skip-fetch          # rebuild from the latest raw snapshots

Steps: 1 fetch, 2 save raw, 3 clean, 4 validate, 5 upsert to PostgreSQL,
6 recompute metrics, 7 regenerate exports, 8 write the pipeline_runs row.

Implemented so far: steps 1-2 (Phase 2). Steps 3-8 are added in later phases;
until then --skip-fetch has nothing to do and says so.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_universe  # noqa: E402
from src.ingestion.run import run_ingestion  # noqa: E402
from src.logging_config import setup_logging  # noqa: E402

log = logging.getLogger("update_data")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh FinSight data.")
    parser.add_argument("--tickers", nargs="+", help="limit the run to these tickers")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="do not call the source; rebuild from the latest raw snapshots")
    args = parser.parse_args()

    setup_logging()
    universe = get_universe()

    if args.skip_fetch:
        log.info("Steps 1-2 (fetch, save raw): skipped (--skip-fetch)")
        log.warning("Steps 3-8 are not implemented yet (Phase 3 onward); nothing to rebuild")
        return 0

    start = time.perf_counter()
    results, _ = run_ingestion(universe, args.tickers)
    log.info("Steps 1-2 (fetch, save raw): %d datasets in %.1fs",
             len(results), time.perf_counter() - start)

    failed = [r for r in results if r.status == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
