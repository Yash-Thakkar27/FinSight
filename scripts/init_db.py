"""Initialize the FinSight database.

    python scripts/init_db.py            # create anything missing, seed reference data
    python scripts/init_db.py --reset    # DROP the staging/core/mart schemas first

The work is done by src/database/setup.py. Every step is idempotent, so running
this twice leaves the same state.
"""

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import get_database_settings, get_universe  # noqa: E402
from src.database.connection import get_engine  # noqa: E402
from src.database.setup import SCHEMAS, format_counts, initialize, table_counts  # noqa: E402

log = logging.getLogger("init_db")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the FinSight database.")
    parser.add_argument("--reset", action="store_true",
                        help="drop the staging, core and mart schemas first (deletes all data)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    settings = get_database_settings()
    log.info("Database: %s@%s:%s/%s", settings.postgres_user, settings.postgres_host,
             settings.postgres_port, settings.postgres_db)

    with get_engine().begin() as conn:  # one transaction: all or nothing
        version = conn.execute(text("SHOW server_version")).scalar_one()
        log.info("PostgreSQL server version %s", version)
        initialize(conn, get_universe(), reset=args.reset)
        counts = table_counts(conn)
        n_indexes = conn.execute(
            text("SELECT count(*) FROM pg_indexes WHERE schemaname = ANY(:schemas)"),
            {"schemas": list(SCHEMAS)},
        ).scalar_one()

    print("\n" + format_counts(counts))
    print(f"{len(counts)} tables, {n_indexes} indexes")


if __name__ == "__main__":
    main()
