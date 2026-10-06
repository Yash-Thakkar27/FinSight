"""Compute every registered metric from the core tables and store the results.

Reads statements, prices and share counts from PostgreSQL, calls the pure
functions in ratios.py, valuation.py, returns.py and risk.py, and writes
core.metrics, core.formulas and core.valuation_reconciliation.

core.metrics is rebuilt in full on every run: it is a pure function of the
core data, so there is nothing to merge.

Currency rule (docs/methodology.md):
  * ratios, margins and growth use statement values in the company's
    **reporting currency** (original_value);
  * valuation uses **INR** values (translated for a foreign-currency reporter),
    and those rows are flagged is_translated.
"""

import logging
import math
from datetime import date

import pandas as pd
from sqlalchemy import Connection, insert, text

from config.settings import Universe
from src.analytics import ratios, returns, risk, shares, valuation  # noqa: F401  (register)
from src.analytics.registry import REGISTRY, is_applicable, not_applicable_reason, specs
from src.database import models, queries

log = logging.getLogger(__name__)

INDEX = ["ticker", "period_end_date"]
CASH_ITEMS = {"cash_and_short_term_investments", "cash_and_equivalents"}
RISK_WINDOWS = (1, 3)
TTM_FLOW_ITEMS = ("revenue", "ebitda", "net_income")
BALANCE_ITEMS = ("total_equity", "total_debt", "minority_interest",
                 "cash_and_short_term_investments", "cash_and_equivalents")


def clean_number(value):
    """A plain float, or None for missing / non-finite values (JSON and SQL cannot hold NaN)."""
    if value is None:
        return None
    if isinstance(value, (date, str, bool, list, dict)):
        return value
    number = float(value)
    return number if math.isfinite(number) else None


def wide_table(statements: pd.DataFrame, period_type: str, value_column: str) -> pd.DataFrame:
    """One row per (ticker, period_end_date) that the company actually reported, one column per
    line item.

    Built with unstack rather than pivot_table: a pivot over two index columns
    fills in the full cross product, which would invent an empty row for every
    period end that any other company reported.
    """
    rows = statements[statements["period_type"] == period_type]
    rows = rows.drop_duplicates(subset=INDEX + ["line_item"], keep="last")
    wide = (rows.set_index(INDEX + ["line_item"])[value_column].astype("float64")
            .unstack("line_item"))
    wide.columns.name = None
    return wide.sort_index()


def metric_row(ticker, period_end, period_type, name, value, na_reason, as_of, inputs,
               reporting_currency="INR", is_translated=False, method=None) -> dict:
    spec = REGISTRY[name]
    value = clean_number(value)
    return {
        "ticker": ticker, "period_end_date": period_end, "period_type": period_type,
        "metric_name": name, "value": value, "na_reason": None if value is not None else na_reason,
        "method": method,
        "unit": spec.unit, "formula_id": spec.formula_id, "as_of_date": as_of,
        "input_fields": {k: (str(v) if isinstance(v, date) else clean_number(v))
                         for k, v in inputs.items()},
        "reporting_currency": reporting_currency, "is_translated": bool(is_translated),
    }


# ----------------------------------------------------------- fundamentals ----

def explain_missing(spec, inputs: dict, prior: dict) -> str:
    """Why a fundamental metric has no value."""
    missing = [item for item in spec.inputs if inputs.get(item) is None]
    if CASH_ITEMS <= set(spec.inputs) and CASH_ITEMS - set(missing):
        missing = [item for item in missing if item not in CASH_ITEMS]   # one cash figure is enough
    if missing:
        return "input unavailable: " + ", ".join(missing)
    if spec.name == "eps":
        return "share count is not positive"
    if spec.name.endswith("_growth"):
        missing_prior = [item for item in spec.prior_inputs if prior.get(item) is None]
        if missing_prior:
            return "prior-year value unavailable: " + ", ".join(missing_prior)
        return "prior-year base is not positive"
    return "denominator is not positive"


def share_adjustments(wide: pd.DataFrame, splits: pd.DataFrame | None,
                      latest_shares: pd.DataFrame | None) -> dict:
    """{(ticker, period_end): (AdjustedShares, share basis)} for every row of a wide table."""
    split_series = {}
    if splits is not None and len(splits):
        for ticker, group in splits.groupby("ticker"):
            split_series[ticker] = group.set_index("date")["split_ratio"]
    current = {}
    if latest_shares is not None and len(latest_shares):
        current = dict(zip(latest_shares["ticker"], latest_shares["shares_outstanding"]))

    adjustments = {}
    for key in wide.index:
        ticker, period_end = key
        factor = shares.cumulative_split_factor(split_series.get(ticker), period_end)
        period = {item: clean_number(ratios.column(wide, item).loc[key])
                  for item, _ in shares.SHARE_BASES}
        adjustments[key] = shares.adjusted_shares_for_eps(period, factor, current.get(ticker))
    return adjustments


def fundamental_metric_rows(statements: pd.DataFrame, splits: pd.DataFrame | None = None,
                            latest_shares: pd.DataFrame | None = None) -> list[dict]:
    """Ratios, margins and growth for every company and period, in the reporting currency."""
    sector_type = statements.groupby("ticker")["sector_type"].first()
    currency = statements.groupby("ticker")["original_currency"].first()
    rows = []
    for period_type in ("annual", "quarterly"):
        wide = wide_table(statements, period_type, "original_value")
        if wide.empty:
            continue
        adjustments = share_adjustments(wide, splits, latest_shares)
        wide["adjusted_shares"] = pd.Series(
            {key: adjusted.shares for key, (adjusted, _) in adjustments.items()}, dtype="float64"
        )
        for key, (adjusted, _) in adjustments.items():
            if adjusted.marginal:
                log.warning("%s %s: share-basis decision is marginal (%s, factor %g); review",
                            key[0], key[1], adjusted.restatement, adjusted.split_factor)
        reported_eps = ratios.column(wide, "eps_diluted")

        for spec in specs("fundamental"):
            if spec.annual_only and period_type != "annual":
                continue
            result = spec.func(wide)
            priors = {item: ratios.prior_year(ratios.column(wide, item))
                      for item in spec.prior_inputs}
            for key in wide.index:
                ticker, period_end = key
                inputs = {item: clean_number(ratios.column(wide, item).loc[key])
                          for item in spec.inputs}
                prior = {item: clean_number(series.loc[key]) for item, series in priors.items()}
                fields = dict(inputs)
                fields.update({f"{item}_prior_year": v for item, v in prior.items()})
                fields["currency"] = currency[ticker]
                method = None
                if spec.name in ("roe", "roa"):
                    # average of opening and closing balance; closing balance alone when the
                    # prior year-end is unavailable (the earliest year)
                    method = ("average_balance" if prior[spec.prior_inputs[0]] is not None
                              else "closing_balance")
                    fields["method"] = method
                if spec.name in ("eps", "eps_growth"):
                    adjusted, basis = adjustments[key]
                    method = basis
                    fields.update({
                        "share_basis": basis,
                        "share_restatement": adjusted.restatement,
                        "split_factor": adjusted.split_factor,
                        "shares_as_reported": adjusted.as_reported,
                        "share_basis_marginal": adjusted.marginal,
                    })
                if not is_applicable(spec.name, sector_type[ticker]):
                    value, reason = None, not_applicable_reason(sector_type[ticker])
                else:
                    value = clean_number(result.loc[key])
                    reason = None if value is not None else explain_missing(spec, inputs, prior)
                if spec.name == "eps":
                    # the source's reported EPS, kept for reconciliation only
                    source_eps = clean_number(reported_eps.loc[key])
                    fields["reported_eps_diluted"] = source_eps
                    fields["difference_from_reported_pct"] = (
                        (value / source_eps - 1) * 100
                        if value is not None and source_eps not in (None, 0) else None
                    )
                if value is None:
                    method = method if spec.name in ("eps", "eps_growth") else None
                rows.append(metric_row(ticker, period_end, period_type, spec.name, value, reason,
                                       period_end, fields, currency[ticker], False, method))
    return rows


# --------------------------------------------------------------- valuation ----

def company_series(wide: pd.DataFrame, ticker: str, item: str) -> pd.Series:
    """One line item for one company, indexed by period_end_date (empty if absent)."""
    if item not in wide.columns or ticker not in wide.index.get_level_values(0):
        return pd.Series(dtype="float64")
    return wide.loc[ticker, item].astype("float64")


def valuation_inputs_reason(needed: dict) -> str | None:
    missing = [name for name, value in needed.items() if value is None]
    return "input unavailable: " + ", ".join(missing) if missing else None


def multiples(sector: str, market_cap_value, flows: dict, balance: dict) -> dict:
    """Enterprise value and the four multiples for one company at one point in time.

    flows: revenue, ebitda, net_income (INR). balance: equity, debt, minority interest and
    the two cash figures (INR). Returns {metric_name: (value, na_reason, inputs)}.
    """
    cash_item = "cash_and_short_term_investments"
    cash = balance.get(cash_item)
    if cash is None:
        cash, cash_item = balance.get("cash_and_equivalents"), "cash_and_equivalents"
    debt, minority = balance.get("total_debt"), balance.get("minority_interest")
    ev = valuation.enterprise_value(market_cap_value, debt, minority, cash)
    out = {}

    def add(name, value, required: dict, extra: dict | None = None, positive_base=None):
        inputs = required | (extra or {})
        if not is_applicable(name, sector):
            out[name] = (None, not_applicable_reason(sector), inputs)
            return
        reason = None
        if value is None:
            reason = valuation_inputs_reason(required) or f"{positive_base} is not positive"
        out[name] = (value, reason, inputs)

    add("enterprise_value", ev, {"market_cap": market_cap_value, "total_debt": debt, "cash": cash},
        {"minority_interest": minority, "minority_interest_assumed_zero": minority is None,
         "cash_item": cash_item})
    add("pe_ratio", valuation.pe_ratio(market_cap_value, flows.get("net_income")),
        {"market_cap": market_cap_value, "net_income": flows.get("net_income")},
        positive_base="net income")
    add("pb_ratio", valuation.pb_ratio(market_cap_value, balance.get("total_equity")),
        {"market_cap": market_cap_value, "total_equity": balance.get("total_equity")},
        positive_base="equity")
    add("ev_ebitda", valuation.ev_ebitda(ev, flows.get("ebitda")),
        {"enterprise_value": ev, "ebitda": flows.get("ebitda")}, positive_base="EBITDA")
    add("ev_revenue", valuation.ev_revenue(ev, flows.get("revenue")),
        {"enterprise_value": ev, "revenue": flows.get("revenue")}, positive_base="revenue")
    return out


def valuation_metric_rows(statements: pd.DataFrame, prices: pd.DataFrame,
                          share_counts: pd.DataFrame) -> tuple[list[dict], dict]:
    """Current (TTM) and historical (fiscal-year-end) valuation rows, in INR.

    Returns (rows, {ticker: {metric_name: current value}}) for reconciliation.
    """
    annual = wide_table(statements, "annual", "value")
    quarterly = wide_table(statements, "quarterly", "value")
    sector_type = statements.groupby("ticker")["sector_type"].first()
    currency = statements.groupby("ticker")["original_currency"].first()
    latest_shares = share_counts.set_index("ticker")
    traded = prices[~prices["is_stale_quote"]]
    rows, current = [], {}

    for ticker in sector_type.index:
        sector = sector_type[ticker]
        translated = currency[ticker] != "INR"
        close = traded[traded["ticker"] == ticker].set_index("date")["close"].sort_index()
        close.index = pd.to_datetime(close.index)
        if close.empty:
            continue
        as_of = close.index[-1].date()

        # ---- current multiples: latest price, TTM flows, latest balance sheet
        share_count = (latest_shares.at[ticker, "shares_outstanding"]
                       if ticker in latest_shares.index else None)
        shares_as_of = (latest_shares.at[ticker, "as_of_date"]
                        if ticker in latest_shares.index else None)
        cap = valuation.market_cap(float(close.iloc[-1]), share_count)

        quarterly_revenue = company_series(quarterly, ticker, "revenue").dropna()
        anchor = max(quarterly_revenue.index) if len(quarterly_revenue) else None
        flows, flow_notes = {}, {}
        for item in TTM_FLOW_ITEMS:
            ttm = valuation.ttm_value(company_series(quarterly, ticker, item),
                                      company_series(annual, ticker, item), anchor)
            flows[item] = ttm["value"]
            flow_notes[item] = {"basis": ttm["basis"], "periods": ttm["periods"]}

        balance, balance_dates = {}, {}
        for item in BALANCE_ITEMS:
            latest = valuation.latest_value(company_series(quarterly, ticker, item),
                                            company_series(annual, ticker, item))
            balance[item], balance_dates[item] = latest["value"], latest["period_end"]

        cap_inputs = {"price": float(close.iloc[-1]), "price_date": as_of,
                      "shares_outstanding": share_count, "shares_as_of_date": shares_as_of}
        rows.append(metric_row(ticker, as_of, "ttm", "market_cap", cap,
                               valuation_inputs_reason({"shares_outstanding": share_count}),
                               as_of, cap_inputs))
        current[ticker] = {"market_cap": cap, "_as_of": as_of}
        for name, (value, reason, inputs) in multiples(sector, cap, flows, balance).items():
            flow_item = {"pe_ratio": "net_income", "ev_ebitda": "ebitda",
                         "ev_revenue": "revenue"}.get(name)
            method = None
            if flow_item:
                method = flow_notes[flow_item]["basis"] if value is not None else None
                inputs = inputs | {"flow_basis": flow_notes[flow_item]["basis"],
                                   "flow_periods": flow_notes[flow_item]["periods"]}
            if name in ("pb_ratio", "enterprise_value"):
                inputs = inputs | {"balance_sheet_date": balance_dates["total_equity"]}
            inputs = inputs | {"price_date": as_of}
            rows.append(metric_row(ticker, as_of, "ttm", name, value, reason, as_of, inputs,
                                   currency[ticker], translated, method))
            current[ticker][name] = value

        # ---- historical multiples: price at each fiscal year end, that year's figures
        if ticker not in annual.index.get_level_values(0):
            continue
        for period_end, year in annual.loc[ticker].iterrows():
            price = returns.price_on_or_before(close, period_end)
            if price is None:
                continue       # no price near that fiscal year end: nothing to value
            price_date = close.loc[:pd.Timestamp(period_end)].index[-1].date()
            year_values = {k: clean_number(v) for k, v in year.items()}
            cap_then = valuation.market_cap(price, year_values.get("shares_outstanding"))
            rows.append(metric_row(
                ticker, period_end, "annual", "market_cap", cap_then,
                valuation_inputs_reason({"shares_outstanding":
                                         year_values.get("shares_outstanding")}),
                price_date,
                {"price": price, "price_date": price_date,
                 "shares_outstanding": year_values.get("shares_outstanding")}))
            for name, (value, reason, inputs) in multiples(sector, cap_then, year_values,
                                                           year_values).items():
                rows.append(metric_row(ticker, period_end, "annual", name, value, reason,
                                       price_date, inputs | {"price_date": price_date},
                                       currency[ticker], translated))
    return rows, current


def reconciliation_rows(current: dict, source_metrics: pd.DataFrame) -> list[dict]:
    """Calculated current valuation figures against the ones the source reports."""
    source = {(r.ticker, r.metric_name): r.value for r in source_metrics.itertuples()}
    rows = []
    for ticker, values in current.items():
        for name in ("market_cap", "enterprise_value", "pe_ratio", "pb_ratio", "ev_ebitda",
                     "ev_revenue"):
            calculated, reported = values.get(name), source.get((ticker, name))
            outcome = valuation.reconcile(calculated, reported)
            rows.append({"ticker": ticker, "metric_name": name, "as_of_date": values["_as_of"],
                         "calculated_value": clean_number(calculated),
                         "source_value": clean_number(reported), **outcome})
    return rows


# ------------------------------------------------------------------ market ----

def market_metric_rows(prices: pd.DataFrame, risk_free_rate: float | None,
                       trading_days: int) -> list[dict]:
    """Return and risk metrics per ticker (companies and the benchmark), as of its latest price.

    Placeholder rows are excluded first, so a market holiday does not count as a
    zero-return day.
    """
    rows = []
    traded = prices[~prices["is_stale_quote"]]
    short = "insufficient price history"
    for ticker, group in traded.groupby("ticker", sort=True):
        series = group.set_index("date")["adj_close"].sort_index()
        series.index = pd.to_datetime(series.index)
        as_of = series.index[-1].date()

        def add(name, value, reason, inputs, ticker=ticker, as_of=as_of):
            rows.append(metric_row(ticker, as_of, "point_in_time", name, value, reason, as_of,
                                   inputs | {"price_date": as_of}))

        add("return_1m", returns.trailing_return(series, as_of, months=1), short, {})
        add("return_3m", returns.trailing_return(series, as_of, months=3), short, {})
        add("return_ytd", returns.ytd_return(series, as_of), short, {})
        add("return_1y", returns.trailing_return(series, as_of, years=1), short, {})
        add("cagr_3y", returns.cagr(series, as_of, 3), short, {})

        for years in RISK_WINDOWS:
            window_px = returns.window_prices(series, as_of, years)
            suffix = f"_{years}y"
            if window_px is None:
                for name in ("volatility", "downside_deviation", "sharpe", "max_drawdown"):
                    add(name + suffix, None, short, {})
                continue
            window = returns.daily_returns(window_px)
            observations = {"observations": len(window), "window_start": window_px.index[0].date()}
            add("volatility" + suffix, risk.annualized_volatility(window, trading_days), short,
                observations)
            add("downside_deviation" + suffix, risk.downside_deviation(window, 0.0, trading_days),
                short, observations | {"target_daily_return": 0.0})
            add("sharpe" + suffix, risk.sharpe_ratio(window, risk_free_rate, trading_days),
                "no risk-free rate configured" if risk_free_rate is None else short,
                observations | {"risk_free_rate": risk_free_rate,
                                "annualized_return": risk.annualized_return(window, trading_days)})
            drawdown = risk.max_drawdown(window_px)
            add("max_drawdown" + suffix, drawdown["max_drawdown"] if drawdown else None, short,
                observations | ({"peak_date": drawdown["peak_date"],
                                 "trough_date": drawdown["trough_date"]} if drawdown else {}))
    return rows


# ------------------------------------------------------------------- store ----

def register_formulas(conn: Connection) -> int:
    """Upsert every registered metric into core.formulas."""
    for spec in REGISTRY.values():
        conn.execute(
            text("""
                INSERT INTO core.formulas
                    (formula_id, metric_name, expression, description, unit,
                     applicable_sector_types)
                VALUES (:formula_id, :metric_name, :expression, :description, :unit, :sectors)
                ON CONFLICT (formula_id) DO UPDATE SET
                    metric_name = EXCLUDED.metric_name, expression = EXCLUDED.expression,
                    description = EXCLUDED.description, unit = EXCLUDED.unit,
                    applicable_sector_types = EXCLUDED.applicable_sector_types
            """),
            {"formula_id": spec.formula_id, "metric_name": spec.name,
             "expression": spec.expression, "description": spec.description or None,
             "unit": spec.unit, "sectors": list(spec.applicable)},
        )
    return len(REGISTRY)


def compute_all(statements: pd.DataFrame, prices: pd.DataFrame, latest_shares: pd.DataFrame,
                source_metrics: pd.DataFrame, splits: pd.DataFrame,
                risk_free_rate: float | None, trading_days: int) -> tuple[list[dict], list[dict]]:
    """All metric rows and reconciliation rows, from DataFrames. No database access."""
    rows = fundamental_metric_rows(statements, splits, latest_shares)
    valuation_rows, current = valuation_metric_rows(statements, prices, latest_shares)
    rows += valuation_rows
    rows += market_metric_rows(prices, risk_free_rate, trading_days)
    return rows, reconciliation_rows(current, source_metrics)


def recompute_metrics(conn: Connection, universe: Universe) -> dict:
    """Recompute and store every metric. Returns counts for the log."""
    statements = queries.load_statements(conn)
    prices = queries.load_prices(conn)
    rows, reconciliation = compute_all(
        statements, prices, queries.load_latest_shares(conn),
        queries.load_latest_source_metrics(conn), queries.load_stock_splits(conn),
        universe.risk_free_rate.value, universe.trading_days_per_year,
    )
    company_id = dict(conn.execute(text("SELECT ticker, company_id FROM core.companies")).all())

    n_formulas = register_formulas(conn)
    conn.execute(text("DELETE FROM core.metrics"))
    conn.execute(text("DELETE FROM core.valuation_reconciliation"))
    if rows:
        conn.execute(insert(models.Metric.__table__), [
            {"company_id": company_id[r["ticker"]],
             **{k: v for k, v in r.items() if k != "ticker"}} for r in rows
        ])
    if reconciliation:
        conn.execute(insert(models.ValuationReconciliation.__table__), [
            {"company_id": company_id[r["ticker"]],
             **{k: v for k, v in r.items() if k != "ticker"}} for r in reconciliation
        ])

    flagged = [r for r in reconciliation if r["is_flagged"]]
    for r in flagged:
        log.warning("Valuation reconciliation: %s %s calculated %.4g vs source %.4g (%+.1f%%)",
                    r["ticker"], r["metric_name"], r["calculated_value"], r["source_value"],
                    r["difference_pct"])
    with_value = sum(1 for r in rows if r["value"] is not None)
    return {"metrics": len(rows), "with_value": with_value, "not_available": len(rows) - with_value,
            "formulas": n_formulas, "reconciled": len(reconciliation), "flagged": len(flagged)}
