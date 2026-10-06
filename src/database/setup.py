"""Create the database objects and seed reference data. Used by scripts/init_db.py and tests.

Steps: (optional drop) -> sql/schema.sql -> sql/indexes.sql -> seed data_sources,
sectors, industries and companies from the universe. Every step is idempotent.
"""

import logging

from sqlalchemy import Connection, text

from config.settings import SQL_DIR, Universe

log = logging.getLogger(__name__)

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
    log.info("Seeded reference data for %d companies and the benchmark", len(universe.companies))


def initialize(conn: Connection, universe: Universe, reset: bool = False) -> None:
    if reset:
        drop_schemas(conn)
    run_sql_file(conn, "schema.sql")
    run_sql_file(conn, "indexes.sql")
    seed_reference_data(conn, universe)


def table_counts(conn: Connection) -> dict[str, int]:
    """Row count of every base table in the FinSight schemas."""
    tables = conn.execute(
        text("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema = ANY(:schemas) AND table_type = 'BASE TABLE'
            ORDER BY table_schema, table_name
        """),
        {"schemas": list(SCHEMAS)},
    ).all()
    return {
        f"{schema}.{table}": conn.execute(
            text(f'SELECT count(*) FROM "{schema}"."{table}"')
        ).scalar_one()
        for schema, table in tables
    }


def format_counts(counts: dict[str, int]) -> str:
    lines = [f"{'table':<40}{'rows':>10}", "-" * 50]
    lines += [f"{name:<40}{rows:>10,}" for name, rows in counts.items()]
    lines.append("-" * 50)
    return "\n".join(lines)
