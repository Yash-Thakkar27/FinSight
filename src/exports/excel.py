"""Excel exports (openpyxl).

    financial_summary.xlsx      Company Overview, Financial Ratios, Growth Analysis, Valuation
    comparable_companies.xlsx   Peer Set, Operating Metrics, Valuation Multiples, Peer Statistics
    market_analysis.xlsx        Returns, Risk, Correlation

Design rules:
  * Figures come from PostgreSQL. A missing figure is the text "N/A", never 0.
  * Statistics are live Excel formulas (MEDIAN, PERCENTILE.INC, AVERAGEIFS, COUNTIFS) with
    INDEX/MATCH lookups, so the workbook recalculates when the user changes an input: in
    particular the target company on the Peer Set sheet. XLOOKUP is not used, for
    compatibility with older Excel and LibreOffice.
  * The same rules as the app: the target is excluded from its peer statistics, N/A peers are
    left out, and with fewer than four peers only min / median / max are shown.
  * Every sheet starts with a header giving the source, the as-of date and the units.
  * No PivotTables: openpyxl cannot create them. docs/excel_guide.md explains how to add one.

The builders take DataFrames, so they can be tested without a database.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import Connection

from src.analytics import comps
from src.analytics.registry import REGISTRY
from src.database import queries

NA = "N/A"
SUPPRESSED = "–"                 # shown where a statistic is not reported (fewer than 4 peers)
CRORE = 1e7
DISCLAIMER = "FinSight is an analytics tool, not investment advice."
FIRST_DATA_ROW = 12              # the same on every company table, so rows line up across sheets
HEADER_ROW = FIRST_DATA_ROW - 1

ACCENT = "1F4E79"
FILL_HEADER = PatternFill("solid", fgColor=ACCENT)
FILL_INPUT = PatternFill("solid", fgColor="FFF4CC")          # cells the user may change
FILL_ABOVE = PatternFill("solid", fgColor="D6E4F0")          # above the median
FILL_BELOW = PatternFill("solid", fgColor="E7E6E6")          # below the median
FILL_FORMULA = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="BFBFBF")
FORMATS = {"pct": "0.0%", "multiple": '0.0"x"', "ratio": "0.00", "crore": "#,##0",
           "per_share": "#,##0.00", "integer": "0", "correlation": "0.00"}

OPERATING = [name for name, _ in comps.COMPS_METRICS["operating"]
             + comps.COMPS_METRICS["capital_structure"]]
VALUATION = [name for name, _ in comps.COMPS_METRICS["valuation"]]
PERIOD_TYPE = {name: period for group in comps.COMPS_METRICS.values() for name, period in group}
RATIO_SHEET_METRICS = ["gross_margin", "ebitda_margin", "ebit_margin", "net_margin", "roe", "roa",
                       "debt_to_equity", "net_debt_to_ebitda", "current_ratio", "quick_ratio",
                       "fcf_margin"]
RETURN_METRICS = ["return_1m", "return_3m", "return_ytd", "return_1y", "cagr_3y"]
RISK_METRICS = ["volatility", "downside_deviation", "sharpe", "max_drawdown"]
LABELS = {
    **comps.LABELS, "ebit_margin": "EBIT margin", "quick_ratio": "quick ratio",
    "market_cap": "Market cap", "enterprise_value": "Enterprise value",
    "return_1m": "1M return", "return_3m": "3M return", "return_ytd": "YTD return",
    "return_1y": "1Y return", "cagr_3y": "3Y CAGR", "volatility": "Volatility",
    "downside_deviation": "Downside deviation", "sharpe": "Sharpe", "max_drawdown": "Max drawdown",
    "net_income_growth": "net income growth",
}


@dataclass
class ExportData:
    companies: pd.DataFrame       # ticker, company_name, sector_name, industry_name, ...
    metrics: pd.DataFrame         # rows of core.metrics with ticker
    statements: pd.DataFrame      # rows of core.financial_statements with ticker
    correlations: pd.DataFrame    # ticker_a, ticker_b, correlation, n_observations, dates
    sources: str
    as_of: date
    correlation_window: str = "3y"


def gather(conn: Connection) -> ExportData:
    """Read everything the workbooks need from PostgreSQL."""
    return ExportData(
        companies=queries.load_companies(conn), metrics=queries.load_metrics(conn),
        statements=queries.load_statements(conn),
        correlations=queries.load_correlation_matrix(conn, "3y"),
        sources=", ".join(queries.load_data_sources(conn)["name"]),
        as_of=queries.load_latest_price_date(conn).iloc[0]["latest"],
    )


# ----------------------------------------------------------------- helpers ----

def cell_value(value):
    """A number for Excel, or the text N/A. Never a blank that could be read as zero."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return NA
    if isinstance(value, (pd.Timestamp,)):
        return value.date()
    if hasattr(value, "item") and not isinstance(value, (str, date)):
        return value.item()
    return value


def write_header(ws, title: str, data: ExportData, units: str, note: str = "") -> None:
    """Rows 1-5 of every sheet: title, source, as-of date, units, disclaimer."""
    ws["A1"] = title
    ws["A1"].font = Font(size=14, bold=True, color=ACCENT)
    ws["A2"] = f"Source: {data.sources} (Yahoo Finance), via the FinSight pipeline"
    ws["A3"] = f"As of: {data.as_of}"
    ws["A4"] = f"Units: {units}"
    ws["A5"] = f"{DISCLAIMER} {note}".strip()
    for row in range(2, 6):
        ws.cell(row, 1).font = Font(size=9, color="595959")


def write_table(ws, header_row: int, headers: list[str], rows: list[list],
                formats: dict[int, str] | None = None, first_column: int = 1) -> None:
    """Write a header row and data rows. `formats` maps 0-based column offset to a number format."""
    for offset, header in enumerate(headers):
        cell = ws.cell(header_row, first_column + offset, header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = FILL_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=THIN)
    for r, row in enumerate(rows, start=header_row + 1):
        for offset, value in enumerate(row):
            cell = ws.cell(r, first_column + offset, value)
            if formats and offset in formats and not isinstance(value, str):
                cell.number_format = formats[offset]
            if isinstance(value, str) and value in (NA, SUPPRESSED):
                cell.alignment = Alignment(horizontal="right")
                cell.font = Font(color="7F7F7F")
    ws.freeze_panes = ws.cell(header_row + 1, first_column + 2)


def set_widths(ws, widths: dict[str, float]) -> None:
    for column, width in widths.items():
        ws.column_dimensions[column].width = width


def median_formatting(ws, cell_range: str, top_left: str, median_ref: str) -> None:
    """Shade numeric cells above (blue) or below (grey) a median. Shading is positional only."""
    ws.conditional_formatting.add(cell_range, FormulaRule(
        formula=[f"AND(ISNUMBER({top_left}),ISNUMBER({median_ref}),{top_left}>{median_ref})"],
        fill=FILL_ABOVE))
    ws.conditional_formatting.add(cell_range, FormulaRule(
        formula=[f"AND(ISNUMBER({top_left}),ISNUMBER({median_ref}),{top_left}<{median_ref})"],
        fill=FILL_BELOW))


def company_frame(data: ExportData) -> pd.DataFrame:
    companies = data.companies[data.companies["entity_type"] == "company"]
    return companies.reset_index(drop=True)


def latest_rows(metrics: pd.DataFrame, name: str, period_type: str) -> pd.DataFrame:
    """Each ticker's most recent row for a metric, indexed by ticker."""
    return comps.latest_rows(metrics, name, period_type).set_index("ticker")


def metric_format(name: str) -> str:
    unit = REGISTRY[name].unit if name in REGISTRY else "ratio"
    return FORMATS.get(unit, "0.00")


def label(name: str) -> str:
    text = LABELS.get(name, name.replace("_", " "))
    return text[0].upper() + text[1:]


def fiscal_year(period_end) -> int:
    return period_end.year + 1 if period_end.month >= 4 else period_end.year


# ------------------------------------------------------- financial summary ----

def build_financial_summary(data: ExportData) -> Workbook:
    wb = Workbook()
    companies = company_frame(data)
    last_row = FIRST_DATA_ROW + len(companies) - 1
    metrics, statements = data.metrics, data.statements
    annual = statements[statements["period_type"] == "annual"]
    currency = statements.groupby("ticker")["original_currency"].first()

    # ---- Company Overview
    ws = wb.active
    ws.title = "Company Overview"
    write_header(ws, "Company overview", data,
                 "revenue, net income and market cap in ₹ crore (1 crore = 10^7)",
                 "Figures for a company that reports in another currency are translated to INR.")
    market_cap = latest_rows(metrics, "market_cap", "ttm")
    rows = []
    for company in companies.itertuples():
        own = annual[(annual["ticker"] == company.ticker) & annual["value"].notna()]
        revenue = own[own["line_item"] == "revenue"].sort_values("period_end_date")
        income = own[own["line_item"] == "net_income"].sort_values("period_end_date")
        period = revenue["period_end_date"].iloc[-1] if len(revenue) else None
        reporting = currency.get(company.ticker, "INR")
        cap = market_cap["value"].get(company.ticker)
        rows.append([
            company.ticker, company.company_name, company.sector_name, company.industry_name,
            company.peer_group, company.sector_type, reporting,
            f"FY{fiscal_year(period)}" if period else NA,
            cell_value(revenue["value"].iloc[-1] / CRORE) if len(revenue) else NA,
            cell_value(income["value"].iloc[-1] / CRORE) if len(income) else NA,
            cell_value(cap / CRORE) if cap is not None and pd.notna(cap) else NA,
            cell_value(market_cap["as_of_date"].get(company.ticker)),
            f"translated from {reporting}" if reporting != "INR" else "",
        ])
    headers = ["Ticker", "Company", "Sector", "Industry", "Peer group", "Sector type",
               "Reporting currency", "Latest fiscal year", "Revenue (₹ Cr)", "Net income (₹ Cr)",
               "Market cap (₹ Cr)", "Price date", "Note"]
    write_table(ws, HEADER_ROW, headers, rows, {8: FORMATS["crore"], 9: FORMATS["crore"],
                                                10: FORMATS["crore"]})
    # lookup panel: pick a ticker, INDEX/MATCH returns its row
    ws["A7"], ws["B7"] = "Look up a ticker:", companies["ticker"].iloc[0]
    ws["B7"].fill = FILL_INPUT
    tickers = f"$A${FIRST_DATA_ROW}:$A${last_row}"
    validation = DataValidation(type="list", formula1=f"={tickers}", allow_blank=False)
    ws.add_data_validation(validation)
    validation.add("B7")
    for column, (title, letter) in zip("CEGI", (("Company", "B"), ("Sector", "C"),
                                                ("Revenue (₹ Cr)", "I"),
                                                ("Market cap (₹ Cr)", "K"))):
        ws[f"{column}7"] = title
        ws[f"{column}7"].font = Font(bold=True)
        target = ws.cell(7, ws[f"{column}7"].column + 1)
        target.value = (f"=INDEX(${letter}${FIRST_DATA_ROW}:${letter}${last_row},"
                        f"MATCH($B$7,{tickers},0))")
        target.fill = FILL_FORMULA
        if "₹" in title:
            target.number_format = FORMATS["crore"]
    ws["A8"] = "Change the yellow cell; the grey cells are INDEX/MATCH formulas."
    ws["A8"].font = Font(size=9, italic=True, color="595959")
    set_widths(ws, {"A": 18, "B": 34, "C": 22, "D": 26, "E": 14, "F": 15, "G": 12, "H": 12,
                    "I": 16, "J": 16, "K": 17, "L": 12, "M": 22})

    # ---- Financial Ratios
    ws = wb.create_sheet("Financial Ratios")
    write_header(ws, "Financial ratios, latest fiscal year", data,
                 "margins and returns as percentages; leverage and liquidity as ratios; "
                 "net debt/EBITDA in turns",
                 "Ratios are computed in each company's reporting currency. N/A = not meaningful "
                 "for the sector type, or an input is unavailable.")
    latest = {name: latest_rows(metrics, name, "annual") for name in RATIO_SHEET_METRICS}
    rows = []
    for company in companies.itertuples():
        values = [cell_value(latest[name]["value"].get(company.ticker))
                  for name in RATIO_SHEET_METRICS]
        method = latest["roe"]["method"].get(company.ticker)
        rows.append([company.ticker, company.company_name, company.sector_name, *values,
                     method.replace("_", " ") if isinstance(method, str) else NA])
    headers = ["Ticker", "Company", "Sector", *[label(n) for n in RATIO_SHEET_METRICS],
               "ROE / ROA balance method"]
    write_table(ws, HEADER_ROW, headers, rows,
                {3 + i: metric_format(n) for i, n in enumerate(RATIO_SHEET_METRICS)})
    first_metric, last_metric = 4, 3 + len(RATIO_SHEET_METRICS)
    # sector averages: AVERAGEIFS over the sector column (text N/A cells are ignored)
    start = last_row + 3
    ws.cell(start - 1, 1, "Sector averages (live AVERAGEIFS formulas; N/A where no company "
                          "in the sector has a value)").font = Font(bold=True)
    sectors = sorted(companies["sector_name"].unique())
    for i, sector in enumerate(sectors):
        row = start + i
        ws.cell(row, 3, sector).font = Font(bold=True)
        for column in range(first_metric, last_metric + 1):
            letter = get_column_letter(column)
            cell = ws.cell(row, column)
            cell.value = (f'=IFERROR(AVERAGEIFS({letter}${FIRST_DATA_ROW}:{letter}${last_row},'
                          f'$C${FIRST_DATA_ROW}:$C${last_row},$C{row}),"{NA}")')
            cell.number_format = metric_format(RATIO_SHEET_METRICS[column - first_metric])
            cell.fill = FILL_FORMULA
    median_row = start + len(sectors)
    ws.cell(median_row, 3, "Universe median").font = Font(bold=True)
    for column in range(first_metric, last_metric + 1):
        letter = get_column_letter(column)
        cell = ws.cell(median_row, column)
        cell.value = (f'=IF(COUNT({letter}${FIRST_DATA_ROW}:{letter}${last_row})=0,"{NA}",'
                      f'MEDIAN({letter}${FIRST_DATA_ROW}:{letter}${last_row}))')
        cell.number_format = metric_format(RATIO_SHEET_METRICS[column - first_metric])
        cell.fill = FILL_FORMULA
        median_formatting(ws, f"{letter}{FIRST_DATA_ROW}:{letter}{last_row}",
                          f"{letter}{FIRST_DATA_ROW}", f"{letter}${median_row}")
    ws.cell(median_row + 1, 1, "Shading: blue = above the universe median for that column, "
                               "grey = below. It marks position only.").font = Font(
        size=9, italic=True, color="595959")
    set_widths(ws, {"A": 18, "B": 34, "C": 22, **{get_column_letter(c): 13
                                                  for c in range(4, last_metric + 1)},
                    get_column_letter(last_metric + 1): 20})

    # ---- Growth Analysis
    ws = wb.create_sheet("Growth Analysis")
    write_header(ws, "Growth analysis", data,
                 "revenue in crore of the reporting currency (1 crore = 10^7); growth as a "
                 "percentage",
                 "Growth is reporting-currency growth: a company reporting in USD is measured in "
                 "USD, so exchange-rate movement does not appear as growth.")
    revenue = annual[annual["line_item"] == "revenue"].copy()
    revenue["fiscal_year"] = revenue["period_end_date"].map(fiscal_year)
    years = sorted(revenue.loc[revenue["original_value"].notna(), "fiscal_year"].unique())
    table = revenue.pivot_table(index="ticker", columns="fiscal_year", values="original_value",
                                aggfunc="first")
    stored = {name: latest_rows(metrics, name, "annual")
              for name in ("revenue_growth", "net_income_growth", "eps_growth")}
    rows = []
    for company in companies.itertuples():
        levels = [cell_value(table.at[company.ticker, y] / CRORE)
                  if company.ticker in table.index and pd.notna(table.at[company.ticker, y])
                  else NA for y in years]
        rows.append([company.ticker, company.company_name, currency.get(company.ticker, "INR"),
                     *levels])
    level_headers = [f"Revenue FY{y}" for y in years]
    write_table(ws, HEADER_ROW, ["Ticker", "Company", "Reporting currency", *level_headers], rows,
                {3 + i: "#,##0" for i in range(len(years))})
    growth_start = 4 + len(years) + 1                      # one blank column after the levels
    growth_headers = [f"Growth FY{y} (formula)" for y in years[1:]]
    stored_headers = ["Stored revenue growth, latest FY", "Formula minus stored",
                      "Net income growth, latest FY", "EPS growth, latest FY"]
    for offset, header in enumerate(growth_headers + stored_headers):
        cell = ws.cell(HEADER_ROW, growth_start + offset, header)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), FILL_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    stored_column = growth_start + len(growth_headers)
    for r, company in enumerate(companies.itertuples(), start=FIRST_DATA_ROW):
        for i in range(1, len(years)):
            previous, current = get_column_letter(3 + i), get_column_letter(4 + i)
            cell = ws.cell(r, growth_start + i - 1)
            # growth only from a positive base, and only when both years exist
            cell.value = (f'=IF(AND(ISNUMBER({previous}{r}),ISNUMBER({current}{r})),'
                          f'IF({previous}{r}>0,{current}{r}/{previous}{r}-1,"{NA}"),"{NA}")')
            cell.number_format, cell.fill = FORMATS["pct"], FILL_FORMULA
        row = stored["revenue_growth"]
        value = row["value"].get(company.ticker)
        ws.cell(r, stored_column, cell_value(value)).number_format = FORMATS["pct"]
        difference = ws.cell(r, stored_column + 1)
        if company.ticker in row.index and pd.notna(value):
            year = fiscal_year(row.at[company.ticker, "period_end_date"])
            formula_column = get_column_letter(growth_start + years.index(year) - 1)
            stored_letter = get_column_letter(stored_column)
            mine, theirs = f"{formula_column}{r}", f"{stored_letter}{r}"
            difference.value = (f'=IF(AND(ISNUMBER({mine}),ISNUMBER({theirs})),'
                                f'{mine}-{theirs},"{NA}")')
            difference.number_format = "0.0000%"
        else:
            difference.value = NA
        difference.fill = FILL_FORMULA
        for offset, name in enumerate(("net_income_growth", "eps_growth"), start=2):
            ws.cell(r, stored_column + offset,
                    cell_value(stored[name]["value"].get(company.ticker))
                    ).number_format = FORMATS["pct"]
    ws.cell(last_row + 2, 1, "Grey cells are formulas computed in this workbook from the revenue "
                             "columns. \"Formula minus stored\" reconciles them with the "
                             "pipeline's figure for the latest fiscal year and should be zero. "
                             "A year the source does not provide gives N/A, never a growth rate "
                             "across the gap.").font = Font(size=9, italic=True, color="595959")
    set_widths(ws, {"A": 18, "B": 34, "C": 12, **{get_column_letter(c): 15
                                                  for c in range(4, stored_column + 4)}})
    ws.row_dimensions[HEADER_ROW].height = 44

    # ---- Valuation
    ws = wb.create_sheet("Valuation")
    write_header(ws, "Valuation, current multiples", data,
                 "market cap and enterprise value in ₹ crore; multiples in turns (x)",
                 "A multiple is N/A when its denominator is zero or negative, or when it is not "
                 "meaningful for the sector type. Valuation of a foreign-currency reporter uses "
                 "figures translated to INR.")
    names = ["market_cap", "enterprise_value", "pe_ratio", "pb_ratio", "ev_ebitda", "ev_revenue"]
    current = {name: latest_rows(metrics, name, "ttm") for name in names}
    rows = []
    for company in companies.itertuples():
        t = company.ticker
        values = []
        for name in names:
            value = current[name]["value"].get(t)
            if name in ("market_cap", "enterprise_value") and value is not None and pd.notna(value):
                value = value / CRORE
            values.append(cell_value(value))
        basis = current["pe_ratio"]["method"].get(t)
        translated = bool(current["pe_ratio"]["is_translated"].get(t, False))
        rows.append([t, company.company_name, company.sector_name,
                     cell_value(current["market_cap"]["as_of_date"].get(t)), *values,
                     {"ttm": "trailing twelve months", "latest_annual": "latest annual figure"}
                     .get(basis, NA),
                     f"translated from {currency.get(t)}" if translated else ""])
    headers = ["Ticker", "Company", "Sector", "Price date", "Market cap (₹ Cr)",
               "Enterprise value (₹ Cr)", "P/E", "P/B", "EV/EBITDA", "EV/Revenue",
               "Earnings basis for P/E", "Note"]
    write_table(ws, HEADER_ROW, headers, rows,
                {4: FORMATS["crore"], 5: FORMATS["crore"], 6: FORMATS["multiple"],
                 7: FORMATS["multiple"], 8: FORMATS["multiple"], 9: FORMATS["multiple"]})
    median_row = last_row + 2
    ws.cell(median_row, 3, "Universe median").font = Font(bold=True)
    for column in range(7, 11):
        letter = get_column_letter(column)
        cell = ws.cell(median_row, column)
        cell.value = (f'=IF(COUNT({letter}${FIRST_DATA_ROW}:{letter}${last_row})=0,"{NA}",'
                      f'MEDIAN({letter}${FIRST_DATA_ROW}:{letter}${last_row}))')
        cell.number_format, cell.fill = FORMATS["multiple"], FILL_FORMULA
        median_formatting(ws, f"{letter}{FIRST_DATA_ROW}:{letter}{last_row}",
                          f"{letter}{FIRST_DATA_ROW}", f"{letter}${median_row}")
    set_widths(ws, {"A": 18, "B": 34, "C": 22, "D": 12, "E": 17, "F": 20, "G": 10, "H": 10,
                    "I": 12, "J": 12, "K": 24, "L": 22})
    return wb


# ---------------------------------------------------- comparable companies ----

def build_comparable_companies(data: ExportData, target: str | None = None) -> Workbook:
    wb = Workbook()
    companies = company_frame(data)
    target = target or companies["ticker"].iloc[0]
    last_row = FIRST_DATA_ROW + len(companies) - 1

    def column_range(sheet: str, letter: str) -> str:
        return f"'{sheet}'!${letter}${FIRST_DATA_ROW}:${letter}${last_row}"

    # ---- Peer Set
    ws = wb.active
    ws.title = "Peer Set"
    write_header(ws, "Comparable companies: peer set", data, "none (labels)",
                 "Peers are the other companies in the target's peer group. The target is never "
                 "its own peer.")
    ws["A7"], ws["B7"] = "Target company (ticker):", target
    ws["B7"].fill = FILL_INPUT
    ws["A8"] = "Target's peer group:"
    ws["B8"] = (f"=INDEX($D${FIRST_DATA_ROW}:$D${last_row},"
                f"MATCH($B$7,$A${FIRST_DATA_ROW}:$A${last_row},0))")
    ws["A9"] = "Number of peers:"
    ws["B9"] = f"=COUNTIF($F${FIRST_DATA_ROW}:$F${last_row},TRUE)"
    for ref in ("B8", "B9"):
        ws[ref].fill = FILL_FORMULA
    ws["D7"] = "Change the yellow cell to any ticker in column A; every sheet recalculates."
    ws["D7"].font = Font(size=9, italic=True, color="595959")
    validation = DataValidation(type="list",
                                formula1=f"=$A${FIRST_DATA_ROW}:$A${last_row}", allow_blank=False)
    ws.add_data_validation(validation)
    validation.add("B7")
    rows = [[c.ticker, c.company_name, c.sector_name, c.peer_group, c.sector_type]
            for c in companies.itertuples()]
    write_table(ws, HEADER_ROW, ["Ticker", "Company", "Sector", "Peer group", "Sector type",
                                 "Is a peer of the target (formula)"], rows)
    for r in range(FIRST_DATA_ROW, last_row + 1):
        cell = ws.cell(r, 6, f"=AND($D{r}=$B$8,$A{r}<>$B$7)")
        cell.fill = FILL_FORMULA
    set_widths(ws, {"A": 24, "B": 34, "C": 22, "D": 16, "E": 16, "F": 22})

    # ---- the two metric sheets, each with a block of "peer values" formulas beside the data
    locations = {}       # metric -> (sheet, value column letter, peer-value column letter)

    def metric_sheet(title: str, names: list[str], heading: str, units: str, note: str) -> None:
        ws = wb.create_sheet(title)
        write_header(ws, heading, data, units, note)
        latest = {name: latest_rows(data.metrics, name, PERIOD_TYPE[name]) for name in names}
        rows = [[c.ticker, c.company_name, c.peer_group,
                 *[cell_value(latest[name]["value"].get(c.ticker)) for name in names]]
                for c in companies.itertuples()]
        write_table(ws, HEADER_ROW, ["Ticker", "Company", "Peer group",
                                     *[label(n) for n in names]], rows,
                    {3 + i: metric_format(n) for i, n in enumerate(names)})
        helper_start = 4 + len(names) + 1
        ws.cell(HEADER_ROW - 1, helper_start,
                "Peer values for the target on the Peer Set sheet (formulas: blank unless the "
                "company is a peer and has a value)").font = Font(bold=True, size=9)
        for i, name in enumerate(names):
            value_letter = get_column_letter(4 + i)
            helper_letter = get_column_letter(helper_start + i)
            header = ws.cell(HEADER_ROW, helper_start + i, label(name))
            header.font, header.fill = Font(bold=True, color="FFFFFF"), FILL_HEADER
            header.alignment = Alignment(horizontal="center", wrap_text=True)
            for r in range(FIRST_DATA_ROW, last_row + 1):
                cell = ws.cell(r, helper_start + i)
                cell.value = (f"=IF(AND('Peer Set'!$F{r},ISNUMBER({value_letter}{r})),"
                              f'{value_letter}{r},"")')
                cell.number_format, cell.fill = metric_format(name), FILL_FORMULA
            locations[name] = (title, value_letter, helper_letter)
        set_widths(ws, {"A": 18, "B": 34, "C": 14,
                        **{get_column_letter(c): 13 for c in range(4, helper_start + len(names))}})

    metric_sheet("Operating Metrics", OPERATING,
                 "Operating and capital-structure metrics, latest fiscal year",
                 "growth, margins and returns as percentages; debt/equity and current ratio as "
                 "ratios; net debt/EBITDA in turns",
                 "Computed in each company's reporting currency; growth for a company reporting "
                 "in another currency is reporting-currency growth.")
    metric_sheet("Valuation Multiples", VALUATION, "Valuation multiples, current",
                 "multiples in turns (x)",
                 "N/A when the denominator is zero or negative or the multiple is not meaningful "
                 "for the sector type. Foreign-currency reporters are translated to INR.")

    # ---- Peer Statistics: all live formulas
    ws = wb.create_sheet("Peer Statistics")
    write_header(ws, "Peer statistics for the selected target", data,
                 "as on the metric sheets: percentages, ratios, or multiples in turns (x)",
                 "Statistics exclude the target and peers whose value is N/A. With fewer than 4 "
                 f"peers that have a value, P25, mean and P75 show {SUPPRESSED} and position is a "
                 "rank.")
    ws["A7"], ws["B7"] = "Target company:", "='Peer Set'!$B$7"
    ws["A8"], ws["B8"] = "Peer group:", "='Peer Set'!$B$8"
    ws["B7"].fill = ws["B8"].fill = FILL_FORMULA
    ws["D7"] = "To change the target, edit the yellow cell on the Peer Set sheet."
    ws["D7"].font = Font(size=9, italic=True, color="595959")
    headers = ["Metric", "Category", "Target", "Peers with a value (n)", "Min", "P25", "Median",
               "Mean", "P75", "Max", "Target vs median", "How 'vs median' is measured",
               "Position"]
    for offset, header in enumerate(headers, start=1):
        cell = ws.cell(HEADER_ROW, offset, header)
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), FILL_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    category = {name: cat.replace("_", " ") for cat, items in comps.COMPS_METRICS.items()
                for name, _ in items}
    is_peer = column_range("Peer Set", "F")
    tickers_on = "'%s'!$A$" + f"{FIRST_DATA_ROW}:$A${last_row}"
    for r, name in enumerate(OPERATING + VALUATION, start=FIRST_DATA_ROW):
        sheet, value_letter, helper_letter = locations[name]
        values, peers = column_range(sheet, value_letter), column_range(sheet, helper_letter)
        number_format = metric_format(name)
        is_pct = REGISTRY[name].unit == "pct"
        ws.cell(r, 1, label(name))
        ws.cell(r, 2, category[name])
        formulas = {
            3: f'=IFERROR(INDEX({values},MATCH($B$7,{tickers_on % sheet},0)),"{NA}")',
            4: f"=COUNT({peers})",
            5: f'=IF($D{r}=0,"{NA}",MIN({peers}))',
            6: (f'=IF($D{r}>=4,_xlfn.PERCENTILE.INC({peers},0.25),'
                f'IF($D{r}=0,"{NA}","{SUPPRESSED}"))'),
            7: f'=IF($D{r}=0,"{NA}",MEDIAN({peers}))',
            8: (f'=IF($D{r}>=4,AVERAGEIFS({values},{is_peer},TRUE),'
                f'IF($D{r}=0,"{NA}","{SUPPRESSED}"))'),
            9: (f'=IF($D{r}>=4,_xlfn.PERCENTILE.INC({peers},0.75),'
                f'IF($D{r}=0,"{NA}","{SUPPRESSED}"))'),
            10: f'=IF($D{r}=0,"{NA}",MAX({peers}))',
            # percentages: gap in percentage points. Multiples and ratios: relative premium,
            # only when the median is positive.
            11: (f'=IF(AND(ISNUMBER($C{r}),ISNUMBER($G{r})),$C{r}-$G{r},"{NA}")' if is_pct else
                 f'=IF(AND(ISNUMBER($C{r}),ISNUMBER($G{r})),'
                 f'IF($G{r}>0,$C{r}/$G{r}-1,"{NA}"),"{NA}")'),
            # percentile (ties count as half) with 4+ peers; otherwise a rank, highest first,
            # counting the target
            13: (f'=IF(OR(NOT(ISNUMBER($C{r})),$D{r}=0),"{NA}",IF($D{r}>=4,"percentile "&'
                 f'ROUND(100*(COUNTIF({peers},"<"&$C{r})+0.5*COUNTIF({peers},$C{r}))/$D{r},0),'
                 f'"rank "&(1+COUNTIF({peers},">"&$C{r}))&" of "&($D{r}+1)))'),
        }
        for column, formula in formulas.items():
            cell = ws.cell(r, column, formula)
            cell.fill = FILL_FORMULA
            if column in (3, 5, 6, 7, 8, 9, 10):
                cell.number_format = number_format
            elif column == 11:
                cell.number_format = "+0.0%;-0.0%;0.0%"
            elif column == 4:
                cell.number_format = FORMATS["integer"]
        ws.cell(r, 12, "percentage points (target − median)" if is_pct
                else "relative premium (target ÷ median − 1)")
    last_stat = FIRST_DATA_ROW + len(OPERATING + VALUATION) - 1
    median_formatting(ws, f"C{FIRST_DATA_ROW}:C{last_stat}", f"$C{FIRST_DATA_ROW}",
                      f"$G{FIRST_DATA_ROW}")
    ws.cell(last_stat + 2, 1, "Shading on the Target column: blue = above the peer median, grey "
                              "= below. It marks position only. Percentiles use linear "
                              "interpolation (PERCENTILE.INC).").font = Font(
        size=9, italic=True, color="595959")
    set_widths(ws, {"A": 20, "B": 16, "C": 12, "D": 12, "E": 10, "F": 10, "G": 10, "H": 10,
                    "I": 10, "J": 10, "K": 13, "L": 34, "M": 16})
    ws.row_dimensions[HEADER_ROW].height = 32
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, 3)
    return wb


# --------------------------------------------------------- market analysis ----

def build_market_analysis(data: ExportData) -> Workbook:
    wb = Workbook()
    everyone = data.companies.reset_index(drop=True)          # companies and the benchmark
    # the benchmark is whichever row the universe marks as an index: never a hard-coded ticker
    indices = everyone.loc[everyone["entity_type"] == "index", "ticker"]
    benchmark = indices.iloc[0] if len(indices) else None
    last_row = FIRST_DATA_ROW + len(everyone) - 1
    point = data.metrics[data.metrics["period_type"] == "point_in_time"]
    tickers = f"$A${FIRST_DATA_ROW}:$A${last_row}"

    def stored(name: str) -> pd.DataFrame:
        return point[point["metric_name"] == name].set_index("ticker")

    # ---- Returns
    ws = wb.active
    ws.title = "Returns"
    write_header(ws, "Returns", data, "percentages; 3Y CAGR is annualized",
                 "Returns use adjusted close (dividends, splits and bonus issues included). N/A = "
                 "the price history is shorter than the horizon.")
    frames = {name: stored(name) for name in RETURN_METRICS}
    rows = [[c.ticker, c.company_name,
             cell_value(frames["return_1y"]["as_of_date"].get(c.ticker)),
             *[cell_value(frames[name]["value"].get(c.ticker)) for name in RETURN_METRICS]]
            for c in everyone.itertuples()]
    write_table(ws, HEADER_ROW, ["Ticker", "Company", "Price date",
                                 *[label(n) for n in RETURN_METRICS],
                                 "1Y return minus benchmark (formula)"], rows,
                {3 + i: FORMATS["pct"] for i in range(len(RETURN_METRICS))})
    one_year = get_column_letter(4 + RETURN_METRICS.index("return_1y"))
    excess = 4 + len(RETURN_METRICS)
    for r in range(FIRST_DATA_ROW, last_row + 1):
        cell = ws.cell(r, excess)
        if benchmark is None:
            cell.value = NA
            continue
        cell.value = (f'=IFERROR(IF(ISNUMBER({one_year}{r}),{one_year}{r}-INDEX(${one_year}$'
                      f'{FIRST_DATA_ROW}:${one_year}${last_row},MATCH("{benchmark}",{tickers},0)),'
                      f'"{NA}"),"{NA}")')
        cell.number_format, cell.fill = "+0.0%;-0.0%;0.0%", FILL_FORMULA
    median_row = last_row + 2
    ws.cell(median_row, 2, "Median (live formula; includes the benchmark row)").font = Font(
        bold=True)
    for column in range(4, 4 + len(RETURN_METRICS)):
        letter = get_column_letter(column)
        cell = ws.cell(median_row, column)
        cell.value = (f'=IF(COUNT({letter}${FIRST_DATA_ROW}:{letter}${last_row})=0,"{NA}",'
                      f'MEDIAN({letter}${FIRST_DATA_ROW}:{letter}${last_row}))')
        cell.number_format, cell.fill = FORMATS["pct"], FILL_FORMULA
        median_formatting(ws, f"{letter}{FIRST_DATA_ROW}:{letter}{last_row}",
                          f"{letter}{FIRST_DATA_ROW}", f"{letter}${median_row}")
    set_widths(ws, {"A": 18, "B": 34, "C": 12, **{get_column_letter(c): 13
                                                  for c in range(4, excess)},
                    get_column_letter(excess): 20})
    ws.row_dimensions[HEADER_ROW].height = 32

    # ---- Risk
    ws = wb.create_sheet("Risk")
    write_header(ws, "Risk metrics, trailing 1 and 3 years", data,
                 "volatility, downside deviation and drawdown as percentages (annualized, 252 "
                 "trading days); Sharpe as a ratio",
                 "Downside deviation: root mean square of daily returns below 0% over all days. "
                 "N/A = history shorter than the window.")
    columns, formats = [], {}
    for window in ("1y", "3y"):
        for name in RISK_METRICS:
            columns.append((f"{name}_{window}", f"{label(name)} ({window.upper()})"))
            formats[2 + len(columns) - 1] = (FORMATS["ratio"] if name == "sharpe"
                                             else FORMATS["pct"])
    frames = {name: stored(name) for name, _ in columns}
    rows = []
    for c in everyone.itertuples():
        values = [cell_value(frames[name]["value"].get(c.ticker)) for name, _ in columns]
        dates = []
        for window in ("1y", "3y"):
            fields = frames[f"max_drawdown_{window}"]["input_fields"].get(c.ticker) or {}
            dates += [fields.get("peak_date", NA), fields.get("trough_date", NA)]
        rows.append([c.ticker, c.company_name, *values, *dates])
    headers = ["Ticker", "Company", *[title for _, title in columns], "Drawdown peak (1Y)",
               "Drawdown trough (1Y)", "Drawdown peak (3Y)", "Drawdown trough (3Y)"]
    write_table(ws, HEADER_ROW, headers, rows, formats)
    median_row = last_row + 2
    ws.cell(median_row, 2, "Median (live formula)").font = Font(bold=True)
    for column in range(3, 3 + len(columns)):
        letter = get_column_letter(column)
        cell = ws.cell(median_row, column)
        cell.value = (f'=IF(COUNT({letter}${FIRST_DATA_ROW}:{letter}${last_row})=0,"{NA}",'
                      f'MEDIAN({letter}${FIRST_DATA_ROW}:{letter}${last_row}))')
        cell.number_format, cell.fill = formats[column - 1], FILL_FORMULA
        median_formatting(ws, f"{letter}{FIRST_DATA_ROW}:{letter}{last_row}",
                          f"{letter}{FIRST_DATA_ROW}", f"{letter}${median_row}")
    set_widths(ws, {"A": 18, "B": 34, **{get_column_letter(c): 14
                                         for c in range(3, 3 + len(columns) + 4)}})
    ws.row_dimensions[HEADER_ROW].height = 32

    # ---- Correlation
    ws = wb.create_sheet("Correlation")
    pairs = data.correlations
    sample = ("" if pairs.empty else
              f" {int(pairs['n_observations'].iloc[0]):,} trading days, "
              f"{pairs['start_date'].iloc[0]} to {pairs['end_date'].iloc[0]}, common to every "
              "ticker.")
    write_header(ws, f"Correlation of daily returns, trailing {data.correlation_window}", data,
                 "Pearson correlation coefficient, from -1 to +1", sample.strip())
    order = [t for t in everyone["ticker"] if pairs.empty or t in set(pairs["ticker_a"])]
    matrix = (pairs.pivot(index="ticker_a", columns="ticker_b", values="correlation")
              .reindex(index=order, columns=order) if not pairs.empty else pd.DataFrame())
    short = [t.replace(".NS", "") for t in order]
    rows = [[name, *[cell_value(matrix.at[t, other]) for other in order]]
            for t, name in zip(order, short)]
    n = len(order)
    write_table(ws, HEADER_ROW, ["Ticker", *short, "Mean with the others (formula)"], rows,
                {1 + i: FORMATS["correlation"] for i in range(n)})
    first, last = get_column_letter(2), get_column_letter(1 + n)
    for r in range(FIRST_DATA_ROW, FIRST_DATA_ROW + n):
        cell = ws.cell(r, 2 + n)
        # average of the row excluding the 1.00 on the diagonal
        cell.value = (f'=IF(COUNT({first}{r}:{last}{r})>1,(SUM({first}{r}:{last}{r})-1)/'
                      f'(COUNT({first}{r}:{last}{r})-1),"{NA}")')
        cell.number_format, cell.fill = FORMATS["correlation"], FILL_FORMULA
    if n:
        body = f"{first}{FIRST_DATA_ROW}:{last}{FIRST_DATA_ROW + n - 1}"
        ws.conditional_formatting.add(body, FormulaRule(
            formula=[f"AND(ISNUMBER({first}{FIRST_DATA_ROW}),{first}{FIRST_DATA_ROW}>=0.5,"
                     f"{first}{FIRST_DATA_ROW}<1)"], fill=FILL_ABOVE))
    ws.cell(FIRST_DATA_ROW + n + 1, 1, "Shading: correlation of 0.50 or more (excluding the "
                                       "diagonal).").font = Font(size=9, italic=True,
                                                                 color="595959")
    set_widths(ws, {"A": 14, **{get_column_letter(c): 8.5 for c in range(2, 2 + n)},
                    get_column_letter(2 + n): 16})
    ws.freeze_panes = ws.cell(FIRST_DATA_ROW, 2)
    ws.row_dimensions[HEADER_ROW].height = 44
    return wb


WORKBOOKS = {
    "financial_summary.xlsx": build_financial_summary,
    "comparable_companies.xlsx": build_comparable_companies,
    "market_analysis.xlsx": build_market_analysis,
}


def export_excel(data: ExportData, out_dir: Path) -> list[Path]:
    """Write the three workbooks. Returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, build in WORKBOOKS.items():
        path = out_dir / filename
        build(data).save(path)
        written.append(path)
    return written
