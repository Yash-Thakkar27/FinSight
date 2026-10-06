"""Refresh pipeline.

    python scripts/update_data.py                       # whole universe
    python scripts/update_data.py --tickers TCS.NS INFY.NS
    python scripts/update_data.py --skip-fetch          # rebuild from the raw snapshots on disk

Steps: 1 fetch, 2 save raw, 3 clean, 4 validate, 5 upsert to PostgreSQL,
6 recompute metrics, 7 regenerate exports, 8 write the pipeline_runs row.
See src/pipeline.py. Steps 6 and 7 are added in Phases 4 and 8.

--tickers limits what is fetched. Cleaning, validation and loading always cover
the whole universe, using the latest snapshot on disk for every ticker.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_universe  # noqa: E402
from src.database.connection import get_engine  # noqa: E402
from src.database.setup import format_counts  # noqa: E402
from src.logging_config import setup_logging  # noqa: E402
from src.pipeline import run_pipeline  # noqa: E402

log = logging.getLogger("update_data")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh FinSight data.")
    parser.add_argument("--tickers", nargs="+", help="limit the fetch to these tickers")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="do not call the source; rebuild from the raw snapshots on disk")
    args = parser.parse_args()

    setup_logging()
    outcome = run_pipeline(get_universe(), get_engine(), tickers=args.tickers,
                           skip_fetch=args.skip_fetch)
    print(outcome["report"])
    print("\n" + format_counts(outcome["table_counts"]))
    return 0 if outcome["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
