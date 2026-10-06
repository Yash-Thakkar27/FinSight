"""Regenerate every export from PostgreSQL (step 7 of the refresh pipeline)."""

import logging
from pathlib import Path

from sqlalchemy import Connection

from config.settings import EXPORTS_DIR
from src.exports import excel, powerbi

log = logging.getLogger(__name__)


def generate_exports(conn: Connection, out_dir: Path = EXPORTS_DIR) -> dict:
    """Write the Excel workbooks and the Power BI CSV files. Returns what was written."""
    workbooks = excel.export_excel(excel.gather(conn), out_dir / "excel")
    csv_rows = powerbi.export_powerbi(conn, out_dir / "powerbi")
    for path in workbooks:
        log.info("Excel workbook written: %s", path)
    log.info("Power BI star schema written to %s: %s", out_dir / "powerbi",
             ", ".join(f"{view} {rows:,}" for view, rows in csv_rows.items()))
    return {"workbooks": [p.name for p in workbooks], "powerbi_rows": csv_rows}
