"""Comparable-company analysis: peer statistics, target positioning, generated interpretation.

All functions are pure. They take the metric rows already stored in
core.metrics, so the app can call them directly when the user changes the
peer set and get the same numbers the pipeline stored for the default set.

Rules
-----
* Default peers are the other companies in the target's `peer_group`.
* The target is **excluded** from every peer statistic.
* A metric that is N/A for a peer is excluded too (it is never counted as 0),
  and `n` reports how many peers actually contributed.
* Percentiles use linear interpolation (the same as Excel's PERCENTILE.INC).
* With fewer than four peers only min, median and max are reported, and the
  target's position is a rank ("2nd of 3") instead of a percentile.
* Interpretation text comes from fixed templates filled with stored numbers.
  A sentence whose inputs are missing is skipped. The wording is descriptive
  only: "trades at a lower multiple than the peer median", never a judgement.
"""

import pandas as pd

from src.analytics import ratios, valuation  # noqa: F401  (importing registers the metrics)
from src.analytics.registry import REGISTRY
from src.formatting import format_metric

# (metric name, period type) per table of the Comparable Companies page.
COMPS_METRICS = {
    "operating": [
        ("revenue_growth", "annual"), ("eps_growth", "annual"), ("gross_margin", "annual"),
        ("ebitda_margin", "annual"), ("net_margin", "annual"), ("roe", "annual"),
        ("roa", "annual"), ("fcf_margin", "annual"),
    ],
    "capital_structure": [
        ("debt_to_equity", "annual"), ("net_debt_to_ebitda", "annual"),
        ("current_ratio", "annual"),
    ],
    "valuation": [
        ("pe_ratio", "ttm"), ("pb_ratio", "ttm"), ("ev_ebitda", "ttm"), ("ev_revenue", "ttm"),
    ],
}

LABELS = {
    "revenue_growth": "revenue growth", "eps_growth": "EPS growth", "gross_margin": "gross margin",
    "ebitda_margin": "EBITDA margin", "net_margin": "net margin", "roe": "ROE", "roa": "ROA",
    "fcf_margin": "FCF margin", "debt_to_equity": "debt/equity",
    "net_debt_to_ebitda": "net debt/EBITDA", "current_ratio": "current ratio",
    "pe_ratio": "P/E", "pb_ratio": "P/B", "ev_ebitda": "EV/EBITDA", "ev_revenue": "EV/Revenue",
}

VALUATION_METRICS = {name for name, _ in COMPS_METRICS["valuation"]}
GROWTH_METRICS = {"revenue_growth", "eps_growth"}

STATISTICS = ["n", "min", "p25", "median", "mean", "p75", "max"]

# With fewer peers than this, quartiles and the mean are not reported and
# position is given as a rank rather than a percentile.
MIN_PEERS_FOR_DISTRIBUTION = 4


# ------------------------------------------------------------------ peers ----

def default_peers(companies: pd.DataFrame, target: str) -> list[str]:
    """The other companies in the target's peer group, in universe order."""
    companies = companies[companies["entity_type"] == "company"]
    group = companies.loc[companies["ticker"] == target, "peer_group"].iloc[0]
    members = companies[(companies["peer_group"] == group) & (companies["ticker"] != target)]
    return members["ticker"].tolist()


def peer_warnings(companies: pd.DataFrame, target: str, peers: list[str]) -> list[str]:
    """Warnings about a peer set: mixed sector types, or too few peers to summarize."""
    lookup = companies.set_index("ticker")
    warnings = []
    target_type = lookup.at[target, "sector_type"]
    different = sorted({lookup.at[p, "sector_type"] for p in peers} - {target_type})
    if different:
        warnings.append(
            f"The peer set mixes sector types ({target_type} with {', '.join(different)}). "
            "Metrics that do not apply to a sector type are N/A for those peers and are "
            "left out of the statistics."
        )
    if target in peers:
        warnings.append("The target was in the peer list and has been removed from it.")
    if len([p for p in peers if p != target]) < 2:
        warnings.append("Fewer than two peers: peer statistics describe very little.")
    return warnings


# ------------------------------------------------------------- statistics ----

def peer_statistics(peer_values: pd.Series) -> dict:
    """n, min, median and max of the peers' values, plus p25, mean and p75 when n >= 4.

    `peer_values` must not contain the target. Missing values are dropped, so n
    is the number of peers with a value. With fewer than four peers a quartile
    is only an interpolation between two or three numbers and the mean is
    driven by any one of them, so p25, mean and p75 are None. With no values
    every statistic is None.
    """
    values = pd.Series(peer_values, dtype="float64").dropna()
    stats = {"n": int(len(values)), "min": None, "p25": None, "median": None, "mean": None,
             "p75": None, "max": None}
    if values.empty:
        return stats
    stats.update(min=float(values.min()), median=float(values.median()), max=float(values.max()))
    if len(values) >= MIN_PEERS_FOR_DISTRIBUTION:
        stats.update(
            p25=float(values.quantile(0.25)),     # linear interpolation = PERCENTILE.INC
            mean=float(values.mean()),
            p75=float(values.quantile(0.75)),
        )
    return stats


def percentile_rank(target_value: float | None, peer_values: pd.Series) -> float | None:
    """Where the target sits among its peers, 0-100.

    The share of peers with a lower value, counting peers with an equal value
    as half: 100 x (below + 0.5 x equal) / n. 0 means below every peer, 100
    above every peer, 50 level with the middle. None if the target or all
    peers are missing.
    """
    values = pd.Series(peer_values, dtype="float64").dropna()
    if target_value is None or pd.isna(target_value) or values.empty:
        return None
    below = int((values < target_value).sum())
    equal = int((values == target_value).sum())
    return 100.0 * (below + 0.5 * equal) / len(values)


def rank_among_peers(target_value: float | None, peer_values: pd.Series) -> tuple | None:
    """(position, group size) of the target among itself and its peers; 1 = highest value.

    The group is the target plus the peers that have a value. Ties share the
    better position (1 + number of peers strictly above). None if the target
    or all peers are missing.
    """
    values = pd.Series(peer_values, dtype="float64").dropna()
    if target_value is None or pd.isna(target_value) or values.empty:
        return None
    return 1 + int((values > target_value).sum()), len(values) + 1


def ordinal(number: int) -> str:
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 11 -> '11th', 22 -> '22nd'."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def position_label(target_value: float | None, peer_values: pd.Series) -> str | None:
    """How the target's position is shown: a percentile with four or more peers, else a rank.

    '75th percentile' or '2nd of 3' (highest first, counting the target).
    """
    rank = rank_among_peers(target_value, peer_values)
    if rank is None:
        return None
    n_peers = rank[1] - 1
    if n_peers >= MIN_PEERS_FOR_DISTRIBUTION:
        # round half up (62.5 -> 63), the same as Excel's ROUND in the exported workbook
        return f"{ordinal(int(percentile_rank(target_value, peer_values) + 0.5))} percentile"
    return f"{ordinal(rank[0])} of {rank[1]}"


def premium_to_median(target_value: float | None, median: float | None) -> float | None:
    """(target / median - 1) x 100. None unless both exist and the median is positive.

    A relative premium over a zero or negative median has no meaning.
    """
    if target_value is None or median is None or pd.isna(target_value) or pd.isna(median):
        return None
    if median <= 0:
        return None
    return (target_value / median - 1) * 100.0


def difference_from_median(target_value: float | None, median: float | None) -> float | None:
    """target - median, in the metric's own unit (percentage points for percentages)."""
    if target_value is None or median is None or pd.isna(target_value) or pd.isna(median):
        return None
    return float(target_value - median)


# ---------------------------------------------------------- interpretation ----

def currency_note(metric_name: str, target_row: dict, peer_rows: list[dict]) -> str:
    """A caveat when currencies differ between the target and its peers."""
    if metric_name in VALUATION_METRICS:
        if target_row.get("is_translated"):
            return f" INR figures for {target_row['ticker']} are translated from " \
                   f"{target_row['reporting_currency']}."
        translated = [r["ticker"] for r in peer_rows if r.get("is_translated")]
        if translated:
            return f" Peer figures for {', '.join(translated)} are translated to INR."
        return ""
    if metric_name in GROWTH_METRICS:
        currency = target_row.get("reporting_currency", "INR")
        others = sorted({r.get("reporting_currency", "INR") for r in peer_rows} - {currency})
        if currency != "INR":
            return (f" This is reporting-currency growth ({currency}); peers reporting in "
                    "other currencies are not directly comparable.")
        if others:
            foreign = [r["ticker"] for r in peer_rows if r.get("reporting_currency") in others]
            return (f" Growth for {', '.join(foreign)} is reporting-currency growth "
                    f"({', '.join(others)}).")
    return ""


def basis_note(metric_name: str, target_row: dict) -> str:
    """A caveat when the target's current multiple could not use trailing-twelve-month flows."""
    if metric_name in VALUATION_METRICS and target_row.get("method") == "latest_annual":
        return (f" The multiple for {target_row['ticker']} uses the latest annual figure; "
                "a trailing-twelve-month figure is not available.")
    return ""


def possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def interpret(company_name: str, metric_name: str, target_value: float | None, stats: dict,
              note: str = "") -> str | None:
    """One descriptive sentence comparing the target with the peer median, or None.

    None when the target value or the peer median is missing: a sentence is
    never written around a missing number.
    """
    median = stats.get("median")
    if target_value is None or pd.isna(target_value) or median is None or stats.get("n", 0) == 0:
        return None
    unit = REGISTRY[metric_name].unit
    label = LABELS.get(metric_name, metric_name)
    value_text, median_text = format_metric(target_value, unit), format_metric(median, unit)
    n = stats["n"]

    if unit == "multiple" and metric_name in VALUATION_METRICS:
        premium = premium_to_median(target_value, median)
        if premium is None:
            return None
        if abs(premium) < 0.05:
            position = "in line with"
        else:
            position = f"{abs(premium):.1f}% {'above' if premium > 0 else 'below'}"
        return (f"{company_name} trades at {value_text} {label}, {position} the peer median "
                f"of {median_text} (n={n}).{note}")

    if unit == "pct":
        gap = (target_value - median) * 100          # percentage points
        if abs(gap) < 0.05:
            position = "in line with"
        else:
            position = f"{abs(gap):.1f} percentage points {'above' if gap > 0 else 'below'}"
        return (f"{possessive(company_name)} {label} of {value_text} is {position} the peer "
                f"median of {median_text} (n={n}).{note}")

    gap = target_value - median
    if abs(gap) < 0.005:
        position = "in line with"
    else:
        position = "above" if gap > 0 else "below"
    return (f"{possessive(company_name)} {label} of {value_text} is {position} the peer median "
            f"of {median_text} (n={n}).{note}")


# ------------------------------------------------------------ comparison ----

def latest_rows(metrics: pd.DataFrame, metric_name: str, period_type: str) -> pd.DataFrame:
    """Each company's most recent row for a metric and period type."""
    rows = metrics[(metrics["metric_name"] == metric_name)
                   & (metrics["period_type"] == period_type)]
    if rows.empty:
        return rows
    return rows.sort_values("period_end_date").groupby("ticker", sort=False).tail(1)


def compare(metrics: pd.DataFrame, companies: pd.DataFrame, target: str,
            peers: list[str] | None = None) -> dict:
    """Full comparable-company analysis for one target.

    metrics: rows of core.metrics (ticker, metric_name, period_type, period_end_date, value,
             na_reason, reporting_currency, is_translated, method).
    Returns {'target', 'peers', 'warnings', 'rows': [...], 'interpretation': [...]}, where
    each row holds the target value, the peer statistics, the positioning and
    the generated sentence for one metric.
    """
    peers = default_peers(companies, target) if peers is None else list(peers)
    warnings = peer_warnings(companies, target, peers)
    peers = [p for p in peers if p != target]          # the target is never its own peer
    names = companies.set_index("ticker")["company_name"]

    rows = []
    for category, metric_list in COMPS_METRICS.items():
        for metric_name, period_type in metric_list:
            latest = latest_rows(metrics, metric_name, period_type).set_index("ticker")
            target_row = (latest.loc[target].to_dict() | {"ticker": target}
                          if target in latest.index else {"ticker": target})
            peer_rows = [latest.loc[p].to_dict() | {"ticker": p} for p in peers
                         if p in latest.index]
            peer_values = pd.Series({r["ticker"]: r.get("value") for r in peer_rows},
                                    dtype="float64")
            target_value = target_row.get("value")
            target_value = None if target_value is None or pd.isna(target_value) \
                else float(target_value)

            stats = peer_statistics(peer_values)
            rank = rank_among_peers(target_value, peer_values)
            used = [r for r in peer_rows if pd.notna(r.get("value"))]
            sentence = interpret(names[target], metric_name, target_value, stats,
                                 currency_note(metric_name, target_row, used)
                                 + basis_note(metric_name, target_row))
            unit = REGISTRY[metric_name].unit
            rows.append({
                "ticker": target, "metric_name": metric_name, "category": category,
                "period_type": period_type,
                "period_end_date": target_row.get("period_end_date"),
                "target_value": target_value,
                "target_na_reason": target_row.get("na_reason") if target_value is None else None,
                "n_peers": stats["n"],
                "peer_min": stats["min"], "peer_p25": stats["p25"],
                "peer_median": stats["median"], "peer_mean": stats["mean"],
                "peer_p75": stats["p75"], "peer_max": stats["max"],
                "percentile_rank": (percentile_rank(target_value, peer_values)
                                    if stats["n"] >= MIN_PEERS_FOR_DISTRIBUTION else None),
                "rank_position": rank[0] if rank else None,
                "rank_of": rank[1] if rank else None,
                "position_label": position_label(target_value, peer_values),
                # a relative premium is reported for multiples and ratios; for percentage
                # metrics the gap in percentage points (difference) is the meaningful figure
                "premium_pct": (premium_to_median(target_value, stats["median"])
                                if unit != "pct" else None),
                "difference": difference_from_median(target_value, stats["median"]),
                "interpretation": sentence,
                "peer_tickers": peers,
                "peer_values": peer_values.to_dict(),
            })
    return {"target": target, "peers": peers, "warnings": warnings, "rows": rows,
            "interpretation": [r["interpretation"] for r in rows if r["interpretation"]]}


def comps_table(metrics: pd.DataFrame, companies: pd.DataFrame, target: str,
                peers: list[str], category: str) -> pd.DataFrame:
    """Display table for one category: a row per company, then the peer-statistic rows.

    The target's row comes first. The statistic rows are computed from the peers
    only, so the target's own value never moves its peer median.
    """
    peers = [p for p in peers if p != target]
    columns = [name for name, _ in COMPS_METRICS[category]]
    body = pd.DataFrame(index=[target] + peers, columns=columns, dtype="float64")
    for metric_name, period_type in COMPS_METRICS[category]:
        latest = latest_rows(metrics, metric_name, period_type).set_index("ticker")["value"]
        body[metric_name] = latest.reindex(body.index).astype("float64")
    stats = pd.DataFrame({name: peer_statistics(body.loc[peers, name]) for name in columns})
    stats.index = ["Peer " + s for s in stats.index]
    return pd.concat([body, stats.loc[["Peer " + s for s in STATISTICS]]])
