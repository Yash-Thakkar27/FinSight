"""Initialize the FinSight database.

    python scripts/init_db.py            # create anything missing, seed reference data
    python scripts/init_db.py --reset    # DROP the staging/core/mart schemas first

Steps: (optional drop) -> sql/schema.sql -> sql/indexes.sql -> seed data_sources,
sectors, industries and companies from config/universe.yaml -> print a summary.
Every step is idempotent, so running it twice leaves the same state.
"""

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import Connection, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import SQL_DIR, Universe, get_database_settings, get_universe  # noqa: E402
from src.database.connection import get_engine  # noqa: E402

log = logging.getLogger("init_db")

SCHEMAS = ("staging", "core", "mart")

DATA_SOURCES = [
    {
        "name": "yfinance",
        "url": "https://finance.yahoo.com",
        "notes": "Yahoo Finance via the yfinance Python package. Free, unofficial, "
                 "delayed; statement history is short. See docs/limitations.md.",
    },
]


def run_sql_file(conn: Connection, filename: str) -> None:
    sql = (SQL_DIR / filename).read_text(encoding="utf-8")
    # No bound parameters, so the driver sends the whole file as one script.
    conn.exec_driver_sql(sql)
    log.info("Executed sql/%s", filename)


def drop_schemas(conn: Connection) -> None:
    for schema in SCHEMAS:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    log.warning("Dropped schemas: %s", ", ".join(SCHEMAS))


def seed_reference_data(conn: Connection, universe: Universe) -> None:
    for source in DATA_SOURCES:
        conn.execute(
            text("""
                INSERT INTO core.data_sources (name, url, notes)
                VALUES (:name, :url, :notes)
                ON CONFLICT (name) DO UPDATE SET url = EXCLUDED.url, notes = EXCLUDED.notes
            """),
            source,
        )

    for company in universe.companies:
        sector_id = conn.execute(
            text("""
                INSERT INTO core.sectors (sector_name) VALUES (:name)
                ON CONFLICT (sector_name) DO UPDATE SET sector_name = EXCLUDED.sector_name
                RETURNING sector_id
            """),
            {"name": company.sector},
        ).scalar_one()

        industry_id = conn.execute(
            text("""
                INSERT INTO core.industries (industry_name, sector_id) VALUES (:name, :sector_id)
                ON CONFLICT (industry_name) DO UPDATE SET sector_id = EXCLUDED.sector_id
                RETURNING industry_id
            """),
            {"name": company.industry, "sector_id": sector_id},
        ).scalar_one()

        conn.execute(
            text("""
                INSERT INTO core.companies
                    (ticker, company_name, entity_type, sector_id, industry_id,
                     peer_group, sector_type, country, currency)
                VALUES
                    (:ticker, :name, 'company', :sector_id, :industry_id,
                     :peer_group, :sector_type, 'India', 'INR')
                ON CONFLICT (ticker) DO UPDATE SET
                    company_name = EXCLUDED.company_name,
                    sector_id    = EXCLUDED.sector_id,
                    industry_id  = EXCLUDED.industry_id,
                    peer_group   = EXCLUDED.peer_group,
                    sector_type  = EXCLUDED.sector_type,
                    updated_at   = now()
            """),
            {
                "ticker": company.ticker,
                "name": company.name,
                "sector_id": sector_id,
                "industry_id": industry_id,
                "peer_group": company.peer_group,
                "sector_type": company.sector_type,
            },
        )

    conn.execute(
        text("""
            INSERT INTO core.companies (ticker, company_name, entity_type, country, currency)
            VALUES (:ticker, :name, 'index', 'India', 'INR')
            ON CONFLICT (ticker) DO UPDATE SET
                company_name = EXCLUDED.company_name, updated_at = now()
        """),
        {"ticker": universe.benchmark.ticker, "name": universe.benchmark.name},
    )
    log.info("Seeded reference data from config/universe.yaml")


def print_summary(conn: Connection) -> None:
    tables = conn.execute(
        text("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = ANY(:schemas) AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
        """),
        {"schemas": list(SCHEMAS)},
    ).all()
    n_indexes = conn.execute(
        text("SELECT count(*) FROM pg_indexes WHERE schemaname = ANY(:schemas)"),
        {"schemas": list(SCHEMAS)},
    ).scalar_one()

    print(f"\n{'table':<36}{'rows':>8}")
    print("-" * 44)
    for schema, table in tables:
        rows = conn.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one()
        print(f"{schema + '.' + table:<36}{rows:>8}")
    print("-" * 44)
    print(f"{len(tables)} tables, {n_indexes} indexes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the FinSight database.")
    parser.add_argument("--reset", action="store_true",
                        help="drop the staging, core and mart schemas first (deletes all data)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    settings = get_database_settings()
    log.info("Database: %s@%s:%s/%s", settings.postgres_user, settings.postgres_host,
             settings.postgres_port, settings.postgres_db)

    universe = get_universe()
    with get_engine().begin() as conn:  # one transaction: all or nothing
        version = conn.execute(text("SHOW server_version")).scalar_one()
        log.info("PostgreSQL server version %s", version)
        if args.reset:
            drop_schemas(conn)
        run_sql_file(conn, "schema.sql")
        run_sql_file(conn, "indexes.sql")
        seed_reference_data(conn, universe)
        print_summary(conn)


if __name__ == "__main__":
    main()
