"""Calculate an .xlsx workbook's formulas with an independent engine (the `formulas` library).

openpyxl writes formulas but does not evaluate them, and neither Excel nor LibreOffice is
assumed to be installed, so tests use this to check that the formulas in the exported
workbooks compute the values the pipeline computes.
"""

import logging
from pathlib import Path

import formulas
import numpy as np


class Calculated:
    """Cell values of a workbook after calculation, with optional input overrides."""

    def __init__(self, path: Path, inputs: dict | None = None):
        logging.getLogger("formulas").setLevel(logging.ERROR)
        self.book = path.name
        model = formulas.ExcelModel().loads(str(path)).finish()
        overrides = {self._key(sheet, cell): value
                     for (sheet, cell), value in (inputs or {}).items()}
        self.solution = model.calculate(inputs=overrides)

    def _key(self, sheet: str, cell: str) -> str:
        return f"'[{self.book}]{sheet.upper()}'!{cell.upper()}"

    def value(self, sheet: str, cell: str):
        """A cell's calculated value as a plain Python object (None if the cell is empty)."""
        key = self._key(sheet, cell)
        if key not in self.solution:
            return None
        value = self.solution[key].value
        if isinstance(value, np.ndarray):
            value = value.ravel()[0]
        if hasattr(value, "item") and not isinstance(value, str):
            value = value.item()
        if value is None or (isinstance(value, str) and value == ""):
            return None
        return str(value) if type(value).__name__ in ("XlError", "Error") else value
