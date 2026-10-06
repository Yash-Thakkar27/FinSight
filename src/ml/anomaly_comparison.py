"""Anomaly detection: an ML method against the two statistical rules, on the same data.

Isolation Forest is fitted on the daily market observations and compared with
the IQR fences and the modified z-score already used in Phase 5. The point is
to see how much the methods agree, not to crown one: there are no labels saying
which days are truly anomalous.

Each ticker-day is described by how unusual its return and its volume are
relative to its own previous 60 days (the same leakage-free rolling scores the
statistical rules use), so the forest sees comparable inputs across tickers.
Wording stays "Potential data anomaly detected".
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from src.analytics import anomaly_detection as ad

SEED = 42
# Share of observations the forest is asked to isolate. Fixed in advance at 2%,
# not matched to the number the statistical rules happen to flag.
CONTAMINATION = 0.02
FOREST_FEATURES = ["return_score", "volume_score", "abs_return"]


def observation_table(prices: pd.DataFrame, window: int, min_periods: int,
                      iqr_multiplier: float, threshold: float) -> pd.DataFrame:
    """One row per ticker-day with the rolling scores and the two statistical flags."""
    frames = []
    traded = prices[~prices["is_stale_quote"]].sort_values(["ticker", "date"])
    for ticker, group in traded.groupby("ticker", sort=True):
        group = group.set_index("date")
        returns = group["adj_close"].astype("float64").pct_change()
        volume = group["volume"].astype("float64")
        log_volume = np.log(volume.where(volume > 0))
        return_score = ad.rolling_modified_zscores(returns, window, min_periods)
        volume_score = ad.rolling_modified_zscores(log_volume, window, min_periods)
        bounds = ad.rolling_iqr_bounds(returns, window, min_periods, iqr_multiplier)
        volume_bounds = ad.rolling_iqr_bounds(log_volume, window, min_periods, iqr_multiplier)
        frames.append(pd.DataFrame({
            "ticker": ticker, "date": group.index, "daily_return": returns.to_numpy(),
            "abs_return": returns.abs().to_numpy(),
            "return_score": return_score.to_numpy(), "volume_score": volume_score.to_numpy(),
            "flag_modified_zscore": ((return_score.abs() > threshold)
                                     | (volume_score.abs() > threshold)).to_numpy(),
            "flag_iqr": (ad.outside(returns, bounds["lower"], bounds["upper"])
                         | ad.outside(log_volume, volume_bounds["lower"],
                                      volume_bounds["upper"])).to_numpy(),
        }))
    table = pd.concat(frames, ignore_index=True)
    return table.dropna(subset=FOREST_FEATURES).reset_index(drop=True)


def jaccard(a: pd.Series, b: pd.Series) -> float:
    """Overlap of two sets of flags: |A and B| / |A or B|."""
    union = int((a | b).sum())
    return float((a & b).sum() / union) if union else float("nan")


def run_experiment(prices: pd.DataFrame, window: int = 60, min_periods: int = 30,
                   iqr_multiplier: float = 3.0, threshold: float = 3.5,
                   seed: int = SEED) -> dict:
    table = observation_table(prices, window, min_periods, iqr_multiplier, threshold)
    forest = IsolationForest(n_estimators=200, contamination=CONTAMINATION, random_state=seed)
    table["flag_isolation_forest"] = forest.fit_predict(table[FOREST_FEATURES]) == -1
    table["isolation_score"] = -forest.score_samples(table[FOREST_FEATURES])   # higher = odder

    methods = ["flag_iqr", "flag_modified_zscore", "flag_isolation_forest"]
    counts = {m.replace("flag_", ""): int(table[m].sum()) for m in methods}
    overlap = []
    for i, a in enumerate(methods):
        for b in methods[i + 1:]:
            both = int((table[a] & table[b]).sum())
            overlap.append({"method_a": a.replace("flag_", ""), "method_b": b.replace("flag_", ""),
                            "both": both, "only_a": int(table[a].sum()) - both,
                            "only_b": int(table[b].sum()) - both,
                            "jaccard": jaccard(table[a], table[b])})
    votes = table[methods].sum(axis=1)
    flagged = table[votes > 0].copy()
    flagged["methods_agreeing"] = votes[votes > 0]
    flagged["message"] = [
        f"{ad.MESSAGE_PREFIX}: daily return of {r:+.1%} flagged by {n} of 3 methods"
        for r, n in zip(flagged["daily_return"], flagged["methods_agreeing"])
    ]
    return {
        "flags": flagged.sort_values(["methods_agreeing", "isolation_score"],
                                     ascending=False).reset_index(drop=True),
        "overlap": pd.DataFrame(overlap),
        "summary": {
            "observations": int(len(table)), "flagged_by": counts,
            "flagged_by_all_three": int((votes == 3).sum()),
            "flagged_by_exactly_two": int((votes == 2).sum()),
            "flagged_by_exactly_one": int((votes == 1).sum()),
            "isolation_forest_only": int((table["flag_isolation_forest"]
                                          & ~table["flag_iqr"]
                                          & ~table["flag_modified_zscore"]).sum()),
        },
        "params": {"seed": seed, "contamination": CONTAMINATION, "n_estimators": 200,
                   "features": FOREST_FEATURES, "rolling_window": window,
                   "iqr_multiplier": iqr_multiplier, "modified_zscore_threshold": threshold},
    }
