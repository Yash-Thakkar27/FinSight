"""Tables: turn result frames into formatted, display-ready frames."""

import pandas as pd

from components.formatting import NA, format_metric, label
from src.analytics import comps
from src.analytics.registry import REGISTRY


def format_comps_table(table: pd.DataFrame) -> pd.DataFrame:
    """Format a comps table (companies, then peer-statistic rows) for display.

    Statistic rows suppressed for small peer groups (P25, mean, P75 when n < 4)
    arrive as NaN and are shown as a dash, so they cannot be mistaken for N/A data.
    """
    shown = pd.DataFrame(index=[i.replace(".NS", "") for i in table.index])
    for column in table.columns:
        unit = REGISTRY[column].unit
        cells = []
        for index, value in table[column].items():
            if index == "Peer n":
                cells.append(f"{int(value)}")
            elif pd.isna(value):
                cells.append("–" if str(index).startswith("Peer") else NA)
            else:
                cells.append(format_metric(value, unit))
        shown[label(column)] = cells
    return shown


def comparison_export(result: dict) -> pd.DataFrame:
    """Flat table of a comps result for the CSV download: one row per metric."""
    rows = []
    for row in result["rows"]:
        rows.append({
            "target": result["target"], "metric": row["metric_name"], "category": row["category"],
            "period_type": row["period_type"], "period_end_date": row["period_end_date"],
            "target_value": row["target_value"], "target_na_reason": row["target_na_reason"],
            "n_peers": row["n_peers"], "peer_min": row["peer_min"], "peer_p25": row["peer_p25"],
            "peer_median": row["peer_median"], "peer_mean": row["peer_mean"],
            "peer_p75": row["peer_p75"], "peer_max": row["peer_max"],
            "position": row["position_label"], "premium_to_median_pct": row["premium_pct"],
            "difference_from_median": row["difference"],
            "peers": " ".join(row["peer_tickers"]), "interpretation": row["interpretation"],
        })
    return pd.DataFrame(rows)


def position_rows(result: dict) -> pd.DataFrame:
    """Rows for the positioning chart: target and median scaled to the peers' min-max range."""
    rows = []
    for row in result["rows"]:
        low, high, target = row["peer_min"], row["peer_max"], row["target_value"]
        if target is None or low is None or high is None or high == low:
            continue
        unit = REGISTRY[row["metric_name"]].unit
        span = high - low
        rows.append({
            "label": comps.LABELS.get(row["metric_name"], row["metric_name"]),
            "target_scaled": min(max((target - low) / span, -0.2), 1.2),
            "median_scaled": (row["peer_median"] - low) / span,
            "target_text": f"target {format_metric(target, unit)} ({row['position_label']})",
            "median_text": f"peer median {format_metric(row['peer_median'], unit)} "
                           f"(n={row['n_peers']})",
        })
    return pd.DataFrame(rows)
