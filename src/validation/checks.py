"""Data validation checks.

Each check is a small function that takes a cleaned table and returns a list
of CheckResult. A check emits one `fail` result per offending record and one
`pass` result per ticker that had no offending record, so the Data Quality
page can show what was checked as well as what failed.

Severity: `error` means the record is wrong; `warning` means it needs review;
`info` is a neutral observation. Nothing is removed from the data here.
"""

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from config.settings import ValidationThresholds

PRICE_DATASET = "market_prices"
STATEMENT_DATASET = "financial_statements"
METADATA_DATASET = "company_metadata"

PERIOD_KEY = ["ticker", "period_type", "period_end_date"]


@dataclass(frozen=True)
class CheckResult:
    check_name: str
    category: str      # market_data | financials | completeness | consistency | freshness
    severity: str      # error | warning | info
    status: str        # pass | fail
    message: str
    ticker: str | None
    dataset: str
    record_key: str | None


def price_key(row) -> str:
    return f"{row['ticker']}|{row['date']}"


def period_record_key(row, line_item: str) -> str:
    return f"{row['ticker']}|{line_item}|{row['period_end_date']}|{row['period_type']}"


def _results(df: pd.DataFrame, failed: pd.Series, *, check_name: str, category: str,
             severity: str, dataset: str, key, message) -> list[CheckResult]:
    """Turn a boolean `failed` mask into results: fails per record, one pass per clean ticker."""
    results = []
    failed = failed.fillna(False).astype(bool)
    for ticker, group in df.groupby("ticker", sort=True):
        bad = group[failed.loc[group.index]]
        if bad.empty:
            results.append(CheckResult(check_name, category, severity, "pass",
                                       f"{len(group)} records checked", ticker, dataset, None))
        for _, row in bad.iterrows():
            results.append(CheckResult(check_name, category, severity, "fail",
                                       message(row), ticker, dataset, key(row)))
    return results


# --------------------------------------------------------------------------
# Market data
# --------------------------------------------------------------------------

def check_high_is_highest(prices: pd.DataFrame) -> list[CheckResult]:
    """High >= max(Open, Close, Low)."""
    failed = prices["high"] < prices[["open", "close", "low"]].max(axis=1)
    return _results(prices, failed, check_name="high_is_highest", category="market_data",
                    severity="error", dataset=PRICE_DATASET, key=price_key,
                    message=lambda r: f"high {r['high']:.2f} is below open/close/low "
                                      f"({r['open']:.2f}/{r['close']:.2f}/{r['low']:.2f})")


def check_low_is_lowest(prices: pd.DataFrame) -> list[CheckResult]:
    """Low <= min(Open, Close)."""
    failed = prices["low"] > prices[["open", "close"]].min(axis=1)
    return _results(prices, failed, check_name="low_is_lowest", category="market_data",
                    severity="error", dataset=PRICE_DATASET, key=price_key,
                    message=lambda r: f"low {r['low']:.2f} is above open/close "
                                      f"({r['open']:.2f}/{r['close']:.2f})")


def check_volume_non_negative(prices: pd.DataFrame) -> list[CheckResult]:
    failed = prices["volume"] < 0
    return _results(prices, failed, check_name="volume_non_negative", category="market_data",
                    severity="error", dataset=PRICE_DATASET, key=price_key,
                    message=lambda r: f"volume {r['volume']} is negative")


def check_prices_positive(prices: pd.DataFrame) -> list[CheckResult]:
    """Open, high, low, close and adjusted close are all present and > 0."""
    columns = ["open", "high", "low", "close", "adj_close"]
    failed = (prices[columns].isna() | (prices[columns] <= 0)).any(axis=1)
    return _results(prices, failed, check_name="prices_positive", category="market_data",
                    severity="error", dataset=PRICE_DATASET, key=price_key,
                    message=lambda r: "a price is missing or not positive: "
                                      + ", ".join(f"{c}={r[c]}" for c in columns))


def check_no_duplicate_prices(prices: pd.DataFrame) -> list[CheckResult]:
    """No two rows share (ticker, date). Run on rows before de-duplication."""
    failed = prices.duplicated(subset=["ticker", "date"], keep=False)
    return _results(prices, failed, check_name="no_duplicate_prices", category="market_data",
                    severity="warning", dataset=PRICE_DATASET, key=price_key,
                    message=lambda r: "duplicate ticker+date row (latest retrieval kept)")


def check_price_gaps(prices: pd.DataFrame, max_gap_weekdays: int = 5) -> list[CheckResult]:
    """Flag gaps of more than `max_gap_weekdays` missing weekdays between consecutive rows."""
    ordered = prices.sort_values(["ticker", "date"])
    previous = ordered.groupby("ticker")["date"].shift(1)
    has_previous = previous.notna()
    missing = pd.Series(0, index=ordered.index)
    # weekdays strictly between the two dates
    missing[has_previous] = np.busday_count(
        previous[has_previous].to_numpy(dtype="datetime64[D]"),
        ordered.loc[has_previous, "date"].to_numpy(dtype="datetime64[D]"),
    ) - 1
    ordered = ordered.assign(previous_date=previous, missing_weekdays=missing)
    return _results(ordered, ordered["missing_weekdays"] > max_gap_weekdays,
                    check_name="price_gap", category="market_data", severity="warning",
                    dataset=PRICE_DATASET, key=price_key,
                    message=lambda r: f"{r['missing_weekdays']} weekdays missing since "
                                      f"{r['previous_date']}")


def check_extreme_returns(prices: pd.DataFrame, max_abs_return: float = 0.20) -> list[CheckResult]:
    """Flag |daily adjusted-close return| above the threshold for review."""
    ordered = prices.sort_values(["ticker", "date"])
    returns = ordered.groupby("ticker")["adj_close"].pct_change()
    ordered = ordered.assign(daily_return=returns)
    return _results(ordered, returns.abs() > max_abs_return, check_name="extreme_daily_return",
                    category="market_data", severity="warning", dataset=PRICE_DATASET,
                    key=price_key,
                    message=lambda r: f"Potential data anomaly detected: daily return "
                                      f"{r['daily_return']:.1%} exceeds {max_abs_return:.0%}")


def check_stale_quotes(prices: pd.DataFrame) -> list[CheckResult]:
    """Report placeholder rows (zero volume, flat at the previous close)."""
    return _results(prices, prices["is_stale_quote"], check_name="stale_quote",
                    category="market_data", severity="info", dataset=PRICE_DATASET,
                    key=price_key,
                    message=lambda r: "placeholder row: zero volume and price unchanged from the "
                                      "previous close (likely a market holiday); flagged, kept")


# --------------------------------------------------------------------------
# Financial statements
# --------------------------------------------------------------------------

def pivot_statements(statements: pd.DataFrame) -> pd.DataFrame:
    """Wide view: one row per (ticker, period_type, period_end_date), one column per line item."""
    wide = statements.pivot_table(index=PERIOD_KEY, columns="line_item", values="value",
                                  aggfunc="first", dropna=False)
    wide.columns.name = None
    return wide.reset_index()


def _line_item_check(statements: pd.DataFrame, line_item: str, bad, *, check_name: str,
                     severity: str, message) -> list[CheckResult]:
    rows = statements[(statements["line_item"] == line_item) & statements["value"].notna()]
    return _results(rows, bad(rows["value"]), check_name=check_name, category="financials",
                    severity=severity, dataset=STATEMENT_DATASET,
                    key=lambda r: period_record_key(r, line_item), message=message)


def check_assets_non_negative(statements: pd.DataFrame) -> list[CheckResult]:
    return _line_item_check(statements, "total_assets", lambda v: v < 0,
                            check_name="assets_non_negative", severity="error",
                            message=lambda r: f"total assets {r['value']:,.0f} is negative")


def check_revenue_non_negative(statements: pd.DataFrame) -> list[CheckResult]:
    return _line_item_check(statements, "revenue", lambda v: v < 0,
                            check_name="revenue_non_negative", severity="error",
                            message=lambda r: f"revenue {r['value']:,.0f} is negative")


def check_shares_positive(statements: pd.DataFrame) -> list[CheckResult]:
    return _line_item_check(statements, "shares_outstanding", lambda v: v <= 0,
                            check_name="shares_positive", severity="error",
                            message=lambda r: f"shares outstanding {r['value']:,.0f} "
                                              "is not positive")


def check_capex_sign(statements: pd.DataFrame) -> list[CheckResult]:
    """Capex is stored as a positive outflow; a negative value means the source sign flipped."""
    return _line_item_check(statements, "capex", lambda v: v < 0,
                            check_name="capex_is_positive_outflow", severity="warning",
                            message=lambda r: f"capex {r['value']:,.0f} is negative after sign "
                                              "normalization (source reported a net inflow)")


def check_balance_sheet_identity(statements: pd.DataFrame,
                                 tolerance: float = 0.02) -> list[CheckResult]:
    """Assets ~ Liabilities + Equity (including minority interest), within tolerance."""
    wide = pivot_statements(statements)
    needed = ["total_assets", "total_liabilities", "total_equity_incl_minority"]
    if not set(needed) <= set(wide.columns):
        return []
    wide = wide.dropna(subset=needed)
    wide = wide[wide["total_assets"] != 0]
    difference = (wide["total_assets"]
                  - (wide["total_liabilities"] + wide["total_equity_incl_minority"]))
    wide = wide.assign(relative_gap=(difference / wide["total_assets"]).abs())
    return _results(wide, wide["relative_gap"] > tolerance, check_name="balance_sheet_identity",
                    category="financials", severity="warning", dataset=STATEMENT_DATASET,
                    key=lambda r: period_record_key(r, "total_assets"),
                    message=lambda r: f"assets differ from liabilities + equity by "
                                      f"{r['relative_gap']:.2%} (tolerance {tolerance:.0%})")


def check_margins_in_range(statements: pd.DataFrame,
                           bounds: tuple[float, float] = (-1.0, 1.0)) -> list[CheckResult]:
    """Gross, EBITDA, EBIT and net margins lie within bounds (default -100% to 100%)."""
    wide = pivot_statements(statements)
    results = []
    for numerator in ("gross_profit", "ebitda", "ebit", "net_income"):
        if numerator not in wide.columns or "revenue" not in wide.columns:
            continue
        rows = wide.dropna(subset=[numerator, "revenue"])
        rows = rows[rows["revenue"] > 0]
        rows = rows.assign(margin=rows[numerator] / rows["revenue"])
        outside = (rows["margin"] < bounds[0]) | (rows["margin"] > bounds[1])
        results += _results(
            rows, outside, check_name=f"{numerator}_margin_in_range", category="financials",
            severity="warning", dataset=STATEMENT_DATASET,
            key=lambda r, item=numerator: period_record_key(r, item),
            message=lambda r, item=numerator: f"{item} / revenue = {r['margin']:.1%} is outside "
                                              f"[{bounds[0]:.0%}, {bounds[1]:.0%}]",
        )
    return results


def check_statement_currency_scale(statements: pd.DataFrame, source_metrics: pd.DataFrame,
                                   bounds: tuple[float, float] = (0.2, 5.0)) -> list[CheckResult]:
    """Guard against a wrong statement currency.

    Compares the latest annual diluted EPS (after any FX conversion) with the
    trailing EPS the source reports in INR. A wrong currency is off by roughly
    the exchange rate (about 90x), far outside these bounds; ordinary growth
    between the last fiscal year and the trailing twelve months is well inside.
    """
    eps = statements[(statements["line_item"] == "eps_diluted")
                     & (statements["period_type"] == "annual")
                     & statements["value"].notna()]
    eps = eps.sort_values("period_end_date").groupby("ticker").tail(1)
    trailing = source_metrics[source_metrics["metric_name"] == "trailing_eps"]
    merged = eps.merge(trailing[["ticker", "value"]], on="ticker", suffixes=("", "_source"))
    merged = merged[merged["value_source"] > 0]
    merged = merged.assign(ratio=merged["value"] / merged["value_source"])
    outside = (merged["ratio"] < bounds[0]) | (merged["ratio"] > bounds[1])
    return _results(
        merged, outside, check_name="statement_currency_scale", category="consistency",
        severity="error", dataset=STATEMENT_DATASET,
        key=lambda r: period_record_key(r, "eps_diluted"),
        message=lambda r: f"annual EPS {r['value']:.2f} vs source trailing EPS "
                          f"{r['value_source']:.2f} (ratio {r['ratio']:.3g}): statement currency "
                          f"or scale looks wrong (configured: {r['original_currency']})",
    )


# --------------------------------------------------------------------------
# Completeness, consistency, freshness
# --------------------------------------------------------------------------

def check_completeness(statements: pd.DataFrame, warn_below: float = 0.80) -> list[CheckResult]:
    """Share of applicable fields present, per company and period.

    Fields that are not applicable to the company's sector type are excluded
    from the denominator. Always reports the percentage; fails (warning) when
    it is below `warn_below`.
    """
    applicable = statements[statements["missing_reason"] != "not_applicable"]
    results = []
    grouped = applicable.groupby(PERIOD_KEY, sort=True)["value"]
    for (ticker, period_type, period_end), values in grouped:
        present, expected = int(values.notna().sum()), len(values)
        share = present / expected
        results.append(CheckResult(
            "field_completeness", "completeness", "warning",
            "pass" if share >= warn_below else "fail",
            f"{present}/{expected} applicable fields present ({share:.1%})",
            ticker, STATEMENT_DATASET, f"{ticker}|{period_end}|{period_type}",
        ))
    return results


def completeness_by_company(statements: pd.DataFrame) -> pd.DataFrame:
    """Percentage of applicable fields present, per company and period type."""
    applicable = statements[statements["missing_reason"] != "not_applicable"]
    table = applicable.groupby(["ticker", "period_type"])["value"].agg(
        present=lambda v: int(v.notna().sum()), expected="size"
    ).reset_index()
    table["completeness_pct"] = 100 * table["present"] / table["expected"]
    return table


def check_no_conflicting_values(observations: pd.DataFrame,
                                relative_tolerance: float = 1e-9) -> list[CheckResult]:
    """The same period never has conflicting values from the same source.

    `observations` are statement values before de-duplication, one row per
    retrieval. Different values for one (line item, period) within a single
    retrieval are an error. A value that changed between retrievals is reported
    as info: the source revised it, and the latest retrieval is used.
    """
    key = ["ticker", "statement", "line_item", "period_end_date", "period_type"]
    results = []
    checked = observations.groupby("ticker", sort=True).size()
    failing_tickers = set()
    for group_key, group in observations.groupby(key, sort=True):
        if len(group) < 2:
            continue
        spread = group["value"].max() - group["value"].min()
        scale = max(abs(group["value"]).max(), 1.0)
        if spread / scale <= relative_tolerance:
            continue
        same_retrieval = group.groupby("retrieved_at")["value"].nunique().max() > 1
        record_key = "|".join(str(part) for part in group_key)
        failing_tickers.add(group_key[0])
        values = ", ".join(f"{v:,.4g}" for v in group.sort_values("retrieved_at")["value"])
        results.append(CheckResult(
            "no_conflicting_values", "consistency",
            "error" if same_retrieval else "info", "fail",
            (f"conflicting values within one retrieval: {values}" if same_retrieval
             else f"value revised by the source between retrievals: {values} (latest kept)"),
            group_key[0], STATEMENT_DATASET, record_key,
        ))
    for ticker, n in checked.items():
        if ticker not in failing_tickers:
            results.append(CheckResult("no_conflicting_values", "consistency", "error", "pass",
                                       f"{n} observations checked", ticker,
                                       STATEMENT_DATASET, None))
    return results


def last_weekday(as_of: date) -> date:
    """Most recent Monday-Friday on or before as_of."""
    day = as_of
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def check_freshness(prices: pd.DataFrame, as_of: date, max_lag_days: int = 5) -> list[CheckResult]:
    """Latest price date >= last weekday on or before `as_of`, minus max_lag_days."""
    reference = last_weekday(as_of)
    cutoff = reference - timedelta(days=max_lag_days)
    results = []
    for ticker, latest in prices.groupby("ticker", sort=True)["date"].max().items():
        fresh = latest >= cutoff
        results.append(CheckResult(
            "price_freshness", "freshness", "warning", "pass" if fresh else "fail",
            f"latest price {latest}; last weekday {reference}; allowed lag {max_lag_days} days",
            ticker, PRICE_DATASET, f"{ticker}|{latest}",
        ))
    return results


def check_cleaning_issues(issues: list[dict]) -> list[CheckResult]:
    """Problems recorded by the cleaning pipeline.

    Missing snapshot, missing FX rate, and incomplete price rows (repaired from
    an earlier snapshot: warning; rejected: error).
    """
    return [
        CheckResult(issue["kind"], "completeness", issue["severity"], "fail", issue["message"],
                    issue["ticker"], issue["dataset"], issue["record_key"])
        for issue in issues
    ]


# --------------------------------------------------------------------------
# Run everything and summarize
# --------------------------------------------------------------------------

def run_all_checks(cleaned, thresholds: ValidationThresholds, as_of: date) -> list[CheckResult]:
    """Run every check on a CleanedData bundle."""
    prices, statements = cleaned.prices, cleaned.statements
    results: list[CheckResult] = []
    results += check_high_is_highest(prices)
    results += check_low_is_lowest(prices)
    results += check_volume_non_negative(prices)
    results += check_prices_positive(prices)
    results += check_no_duplicate_prices(cleaned.prices_before_dedup)
    results += check_price_gaps(prices, thresholds.max_gap_weekdays)
    results += check_extreme_returns(prices, thresholds.max_abs_daily_return)
    results += check_stale_quotes(prices)
    results += check_assets_non_negative(statements)
    results += check_revenue_non_negative(statements)
    results += check_shares_positive(statements)
    results += check_capex_sign(statements)
    results += check_balance_sheet_identity(statements, thresholds.balance_tolerance)
    results += check_margins_in_range(statements, thresholds.margin_bounds)
    results += check_statement_currency_scale(statements, cleaned.source_metrics,
                                              thresholds.eps_scale_bounds)
    results += check_completeness(statements, thresholds.completeness_warn_below)
    results += check_no_conflicting_values(cleaned.statement_observations)
    results += check_freshness(prices, as_of, thresholds.freshness_max_lag_days)
    results += check_cleaning_issues(cleaned.issues)
    return results


def summarize(results: list[CheckResult], cleaned) -> dict:
    """Run-level summary. Every figure is computed from the data and the results.

    total_records    rows cleaned (prices + statement rows + metadata + FX),
                     plus price rows rejected as incomplete
    invalid_records  distinct records with at least one error-severity failure
    valid_records    total - invalid
    warning_count    warning-severity failures
    missing_pct      applicable statement fields with no value, as a percentage
    pass_rate_pct    valid / total
    """
    statements = cleaned.statements
    total = (len(cleaned.prices) + len(statements) + len(cleaned.metadata) + len(cleaned.fx)
             + len(cleaned.rejected_prices))
    errors = {(r.dataset, r.record_key) for r in results
              if r.status == "fail" and r.severity == "error"}
    warnings = sum(1 for r in results if r.status == "fail" and r.severity == "warning")
    applicable = statements[statements["missing_reason"] != "not_applicable"]
    missing_pct = (100 * applicable["value"].isna().mean()) if len(applicable) else None
    invalid = len(errors)
    return {
        "total_records": total,
        "valid_records": total - invalid,
        "invalid_records": invalid,
        "warning_count": warnings,
        "missing_pct": missing_pct,
        "duplicate_records": cleaned.duplicates_removed,
        "pass_rate_pct": 100 * (total - invalid) / total if total else None,
    }


def results_frame(results: list[CheckResult]) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in results], columns=list(CheckResult.__annotations__))


def format_report(results: list[CheckResult], summary: dict, cleaned) -> str:
    """Plain-text data-quality report for the console and the log."""
    frame = results_frame(results)
    lines = ["", "DATA QUALITY REPORT", "=" * 78]
    lines.append(f"Records processed : {summary['total_records']:,}")
    lines.append(f"Valid records     : {summary['valid_records']:,}")
    lines.append(f"Invalid records   : {summary['invalid_records']:,}  (error-severity failures)")
    lines.append(f"Warnings          : {summary['warning_count']:,}")
    lines.append(f"Duplicates removed: {summary['duplicate_records']:,}")
    lines.append(f"Missing fields    : {summary['missing_pct']:.1f}% "
                 "of applicable statement fields")
    lines.append(f"Pass rate         : {summary['pass_rate_pct']:.2f}%")

    lines += ["", "Results by check", "-" * 78,
              f"{'category':<13}{'check':<34}{'severity':<9}{'pass':>7}{'fail':>7}"]
    counts = (frame.groupby(["category", "check_name", "severity", "status"]).size()
              .unstack("status", fill_value=0).reset_index())
    for column in ("pass", "fail"):
        if column not in counts.columns:
            counts[column] = 0
    for _, row in counts.iterrows():
        lines.append(f"{row['category']:<13}{row['check_name']:<34}{row['severity']:<9}"
                     f"{row['pass']:>7}{row['fail']:>7}")

    failures = frame[(frame["status"] == "fail") & (frame["severity"] != "info")]
    lines += ["", f"Errors and warnings ({len(failures)}; first 25 shown)", "-" * 78]
    for row in failures.head(25).itertuples():
        lines.append(f"[{row.severity:<7}] {row.check_name}: {row.record_key} - {row.message}")
    if failures.empty:
        lines.append("none")

    completeness = completeness_by_company(cleaned.statements).pivot(
        index="ticker", columns="period_type", values="completeness_pct"
    )
    lines += ["", "Completeness by company (% of applicable fields present)", "-" * 78,
              f"{'ticker':<16}{'annual':>10}{'quarterly':>12}"]
    for ticker, row in completeness.iterrows():
        annual = f"{row.get('annual'):.1f}" if pd.notna(row.get("annual")) else "N/A"
        quarterly = f"{row.get('quarterly'):.1f}" if pd.notna(row.get("quarterly")) else "N/A"
        lines.append(f"{ticker:<16}{annual:>10}{quarterly:>12}")
    lines.append("=" * 78)
    return "\n".join(lines)
