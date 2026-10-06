"""Run the Data Science Lab: volatility forecasting, clustering, statistical tests, regimes,
and the anomaly-method comparison.

    python scripts/run_ml.py                          # every task
    python scripts/run_ml.py --tasks volatility clustering

Reads prices and metrics from PostgreSQL (run scripts/update_data.py first), stores results
in the core.ml_* tables and data/processed/ml_results/, and regenerates docs/model_cards/.
No network access. Results are reproducible: the seed is fixed.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_universe  # noqa: E402
from src.database.connection import get_engine  # noqa: E402
from src.logging_config import setup_logging  # noqa: E402
from src.ml.run import SEED, TASKS, run_all  # noqa: E402

log = logging.getLogger("run_ml")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the FinSight Data Science Lab.")
    parser.add_argument("--tasks", nargs="+", choices=TASKS, default=list(TASKS))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    setup_logging()
    with get_engine().begin() as conn:
        results = run_all(conn, get_universe(), args.tasks, args.seed)

    if "volatility" in results:
        table = results["volatility"]["comparison"]
        print("\nVolatility forecasting: model vs best baseline (negative change = lower loss)")
        print(table[["horizon", "metric", "model", "kind", "value", "best_baseline",
                     "change_vs_best_baseline_pct", "fold_mean", "fold_std",
                     "folds_beating_best_baseline", "folds"]].round(4).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
