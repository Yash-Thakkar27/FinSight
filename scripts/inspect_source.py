"""Phase 1 data-source inspection.

Pulls live yfinance data and writes docs/data_source_inspection.md describing
exactly what the source returns, so the schema and field map are designed
around real fields rather than assumed ones.

    python scripts/inspect_source.py

Part A verifies every ticker in config/universe.yaml.
Part B inspects fields, periods and units in depth for three tickers
(one IT company, one bank, one pharma company).

This script is a one-off diagnostic. It writes a report only: no raw snapshots
and no database rows. Ingestion proper lives in src/ingestion/ (Phase 2).
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DOCS_DIR, get_universe  # noqa: E402

DEEP_TICKERS = ["TCS.NS", "HDFCBANK.NS", "SUNPHARMA.NS"]

STATEMENTS = {
    "income": ("income_stmt", "quarterly_income_stmt"),
    "balance": ("balance_sheet", "quarterly_balance_sheet"),
    "cashflow": ("cashflow", "quarterly_cashflow"),
}

INFO_FIELDS = [
    "longName", "shortName", "quoteType", "exchange", "fullExchangeName", "sector",
    "industry", "country", "currency", "financialCurrency", "sharesOutstanding",
    "impliedSharesOutstanding", "floatShares", "marketCap", "enterpriseValue",
    "trailingPE", "forwardPE", "priceToBook", "enterpriseToEbitda",
    "enterpriseToRevenue", "trailingEps", "bookValue", "lastFiscalYearEnd",
    "mostRecentQuarter",
]

# Canonical fields the spec asks for -> candidate yfinance labels to look for.
WANTED = {
    "income": {
        "revenue": ["Total Revenue", "Operating Revenue"],
        "gross_profit": ["Gross Profit"],
        "ebitda": ["EBITDA", "Normalized EBITDA"],
        "ebit": ["EBIT", "Operating Income"],
        "net_income": ["Net Income", "Net Income Common Stockholders"],
        "eps": ["Basic EPS", "Diluted EPS"],
        "interest_expense": ["Interest Expense"],
        "depreciation_amortization": ["Reconciled Depreciation"],
        "net_interest_income (bank)": ["Net Interest Income"],
    },
    "balance": {
        "total_assets": ["Total Assets"],
        "total_liabilities": ["Total Liabilities Net Minority Interest"],
        "total_equity": ["Stockholders Equity", "Total Equity Gross Minority Interest"],
        "total_debt": ["Total Debt"],
        "cash_and_equivalents": [
            "Cash And Cash Equivalents",
            "Cash Cash Equivalents And Short Term Investments",
        ],
        "current_assets": ["Current Assets"],
        "current_liabilities": ["Current Liabilities"],
        "minority_interest": ["Minority Interest"],
        "shares_outstanding": ["Ordinary Shares Number", "Share Issued"],
        "total_deposits (bank)": ["Total Deposits"],
        "total_loans (bank)": ["Net Loan", "Gross Loan", "Loans Receivable"],
    },
    "cashflow": {
        "operating_cash_flow": ["Operating Cash Flow"],
        "capex": ["Capital Expenditure"],
        "free_cash_flow": ["Free Cash Flow"],
    },
}


def fetch(fn, attempts: int = 3, pause: float = 2.0):
    """Call fn with simple retry/backoff. Returns (result, error_message)."""
    for attempt in range(1, attempts + 1):
        try:
            return fn(), None
        except Exception as exc:  # report the failure, never hide it
            error = f"{type(exc).__name__}: {exc}"
            time.sleep(pause * attempt)
    return None, error


def fmt_num(value) -> str:
    if value is None or pd.isna(value):
        return "NULL"
    if isinstance(value, (int, float)):
        return f"{value:,.4g}" if abs(value) < 1e5 else f"{value:,.0f}"
    return str(value)


def md_table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def non_null_periods(df: pd.DataFrame | None, label: str) -> int:
    """Number of periods with a value for this line item (0 if the label is absent)."""
    if df is None or label not in df.index:
        return 0
    return int(df.loc[label].notna().sum())


def verify_universe(out: list[str]) -> None:
    universe = get_universe()
    years = universe.price_history_years
    entries = [(c.ticker, c.name) for c in universe.companies]
    entries.append((universe.benchmark.ticker, universe.benchmark.name))

    rows = []
    for ticker, config_name in entries:
        tk = yf.Ticker(ticker)
        hist, err = fetch(lambda: tk.history(period=f"{years}y", auto_adjust=False))
        if err or hist is None or hist.empty:
            rows.append([ticker, config_name, "FAILED", "", "", "", "", err or "no rows"])
            continue
        info, info_err = fetch(lambda: tk.info)
        info = info or {}
        rows.append([
            ticker,
            config_name,
            info.get("longName") or info.get("shortName") or f"N/A ({info_err})",
            info.get("currency", "N/A"),
            len(hist),
            hist.index.min().date(),
            hist.index.max().date(),
            "OK",
        ])
        time.sleep(0.5)

    out.append("## A. Universe ticker verification\n")
    out.append(
        f"Each ticker in `config/universe.yaml` was fetched for a {years}-year daily "
        "price history and its metadata. `Source name` is what Yahoo returns and must "
        "be read against `Config name`.\n"
    )
    out.append(md_table(
        ["Ticker", "Config name", "Source name", "Currency", "Price rows",
         "First date", "Last date", "Status"],
        rows,
    ))
    out.append("")


def inspect_prices(ticker: str, tk: yf.Ticker, years: int, out: list[str]) -> None:
    hist, err = fetch(lambda: tk.history(period=f"{years}y", auto_adjust=False))
    out.append("#### Daily prices\n")
    if err or hist is None or hist.empty:
        out.append(f"FAILED: {err or 'no rows'}\n")
        return
    out.append(f"- Call: `Ticker.history(period='{years}y', auto_adjust=False)`")
    out.append(f"- Rows: {len(hist):,} from {hist.index.min().date()} to {hist.index.max().date()}")
    out.append(f"- Index: `{hist.index.dtype}` (timezone `{hist.index.tz}`)")
    out.append(f"- Duplicate dates: {int(hist.index.duplicated().sum())}")
    out.append("")
    rows = [
        [col, hist[col].dtype, int(hist[col].isna().sum()), fmt_num(hist[col].iloc[-1])]
        for col in hist.columns
    ]
    out.append(md_table(["Column", "dtype", "Nulls", "Latest value"], rows))
    out.append("")


def inspect_info(tk: yf.Ticker, out: list[str]) -> None:
    info, err = fetch(lambda: tk.info)
    out.append("#### Metadata (`Ticker.info`)\n")
    if err or not info:
        out.append(f"FAILED: {err}\n")
        return
    out.append(f"`info` returned {len(info)} keys. Keys relevant to FinSight:\n")
    rows = []
    for key in INFO_FIELDS:
        value = info.get(key)
        if key in ("lastFiscalYearEnd", "mostRecentQuarter") and value:
            value = f"{value} ({datetime.fromtimestamp(value, tz=timezone.utc).date()})"
        rows.append([key, "MISSING" if key not in info else fmt_num(value)])
    out.append(md_table(["Key", "Value"], rows))
    out.append("")


def inspect_statements(tk: yf.Ticker, out: list[str]) -> dict:
    """Print periods and every line item per statement. Returns the frames."""
    frames = {}
    period_rows = []
    for statement, (annual_attr, quarterly_attr) in STATEMENTS.items():
        for period_type, attr in (("annual", annual_attr), ("quarterly", quarterly_attr)):
            df, err = fetch(lambda a=attr: getattr(tk, a))
            if err or df is None or df.empty:
                period_rows.append([statement, period_type, 0, 0, err or "empty"])
                continue
            frames[(statement, period_type)] = df
            periods = ", ".join(str(c.date()) for c in df.columns)
            period_rows.append([statement, period_type, df.shape[0], df.shape[1], periods])

    out.append("#### Statement periods returned\n")
    out.append(md_table(
        ["Statement", "Period type", "Line items", "Periods", "Period end dates"], period_rows
    ))
    out.append("")

    for statement in STATEMENTS:
        annual = frames.get((statement, "annual"))
        quarterly = frames.get((statement, "quarterly"))
        labels = []
        for df in (annual, quarterly):
            if df is not None:
                labels += [x for x in df.index if x not in labels]
        if not labels:
            continue
        n_a = annual.shape[1] if annual is not None else 0
        n_q = quarterly.shape[1] if quarterly is not None else 0
        rows = []
        for label in labels:
            a_count = non_null_periods(annual, label)
            q_count = non_null_periods(quarterly, label)
            has_annual = annual is not None and label in annual.index
            latest = annual.loc[label].iloc[0] if has_annual else None
            rows.append([label, f"{a_count}/{n_a}", f"{q_count}/{n_q}", fmt_num(latest)])
        out.append(f"<details><summary>All <b>{statement}</b> line items "
                   f"({len(labels)}): non-null periods, latest annual value</summary>\n")
        out.append(md_table(
            ["Source line item", "Annual non-null", "Quarterly non-null", "Latest annual value"],
            rows,
        ))
        out.append("\n</details>\n")
    return frames


def inspect_wanted(frames: dict, out: list[str]) -> None:
    out.append("#### Coverage of the fields FinSight needs\n")
    out.append("Non-null periods for each candidate source label "
               "(`absent` = the label is not in the response at all).\n")
    rows = []
    for statement, wanted in WANTED.items():
        annual = frames.get((statement, "annual"))
        quarterly = frames.get((statement, "quarterly"))
        for canonical, candidates in wanted.items():
            for label in candidates:
                cells = []
                for df in (annual, quarterly):
                    if df is None or label not in df.index:
                        cells.append("absent")
                    else:
                        cells.append(f"{int(df.loc[label].notna().sum())}/{df.shape[1]}")
                latest = (
                    fmt_num(annual.loc[label].iloc[0])
                    if annual is not None and label in annual.index else "absent"
                )
                rows.append([statement, canonical, label, cells[0], cells[1], latest])
    out.append(md_table(
        ["Statement", "Canonical field", "Source label", "Annual", "Quarterly",
         "Latest annual value"],
        rows,
    ))
    out.append("")


def main() -> None:
    universe = get_universe()
    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "# Data Source Inspection Report",
        "",
        f"- Generated by `scripts/inspect_source.py` at {retrieved_at}",
        f"- Source: Yahoo Finance via `yfinance` {yf.__version__}",
        "- Every value below was returned by the live source at generation time. "
        "Nothing is hand-entered.",
        "",
    ]
    verify_universe(out)

    out.append("## B. Field-level inspection (3 tickers)\n")
    for ticker in DEEP_TICKERS:
        company = next(c for c in universe.companies if c.ticker == ticker)
        out.append(f"### {ticker} ({company.name}, sector_type = `{company.sector_type}`)\n")
        tk = yf.Ticker(ticker)
        inspect_prices(ticker, tk, universe.price_history_years, out)
        inspect_info(tk, out)
        frames = inspect_statements(tk, out)
        inspect_wanted(frames, out)
        time.sleep(1)

    DOCS_DIR.mkdir(exist_ok=True)
    path = DOCS_DIR / "data_source_inspection.md"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {path} ({len(out)} blocks)")


if __name__ == "__main__":
    main()
