"""Statistical anomaly detection: IQR fences and the robust modified z-score.

An anomaly here is a data point that is unusual relative to a reference
sample. It is a prompt to look at the data point (a data error, a corporate
action, a one-off item), nothing more. The message always begins
"Potential data anomaly detected" and never characterises the company.

Two rules, both resistant to the outliers they are looking for:

* IQR fences (Tukey): outside Q1 - k x IQR or Q3 + k x IQR.
* Modified z-score (Iglewicz & Hoaglin, 1993): M = 0.6745 x (x - median) / MAD,
  flagged when |M| > 3.5. MAD is the median absolute deviation from the
  median. Unlike the classical z-score, neither the centre nor the scale is
  pulled towards the outlier.

Two settings:

* Market data (daily returns, trading volume): each observation is compared
  with the `window` trading days **before** it. The window is shifted by one
  day, so an observation never influences its own threshold and nothing from
  the future is used.
* Fundamentals (year-on-year changes): first, each change is measured
  relative to the company's peer group, by subtracting the peer-group median
  change for the same variable and period. These sector-adjusted residuals are
  then pooled across all companies and years, and the two rules are applied to
  the pool. A move shared by a whole sector therefore cancels out, and what is
  flagged is a company that moved differently from its peers.

  A score is never computed from one company's own history: with four annual
  values a classical z-score cannot exceed (n - 1) / sqrt(n) = 1.5 in absolute
  value, so no threshold of 3 could ever be reached.

Thresholds are conventional values set in config/settings.py before any
results were examined. Nothing is removed from the data.
"""

import numpy as np
import pandas as pd

from src.analytics import ratios

MESSAGE_PREFIX = "Potential data anomaly detected"
COLUMNS = ["ticker", "date", "dataset", "variable", "method", "value", "peer_group_median",
           "adjusted_value", "lower_bound", "upper_bound", "score", "message"]

VARIABLE_LABELS = {
    "daily_return": "daily return", "log_volume": "trading volume (log)",
    "revenue_growth": "revenue growth", "net_margin_change": "change in net margin",
    "ebitda_margin_change": "change in EBITDA margin", "debt_growth": "change in total debt",
}
NON_FINANCIAL_ONLY = {"ebitda_margin_change", "debt_growth"}

# Modified z-score constants (Iglewicz & Hoaglin). 0.6745 is the 75th percentile
# of the standard normal, which makes MAD / 0.6745 estimate the standard deviation.
# When MAD is zero (more than half the sample is identical) the mean absolute
# deviation is used instead, with 1.253314 playing the same role.
MAD_SCALE = 0.6745
MEAN_AD_SCALE = 1.253314

# A value must clear a fence by more than this to be flagged, and a sample's
# spread must exceed it to count as spread. Without it, when a sample is
# constant, floating-point noise of ~1e-17 would be scored as an anomaly.
TOLERANCE = 1e-9


def outside(values: pd.Series, lower, upper) -> pd.Series:
    """True where a value is beyond [lower, upper] by more than the tolerance."""
    return (values < lower - TOLERANCE) | (values > upper + TOLERANCE)


# ------------------------------------------------------- static sample ----

def iqr_bounds(values: pd.Series, multiplier: float = 1.5) -> tuple[float, float]:
    """Tukey fences: (Q1 - k x IQR, Q3 + k x IQR). Quartiles by linear interpolation."""
    clean = pd.Series(values, dtype="float64").dropna()
    q1, q3 = clean.quantile(0.25), clean.quantile(0.75)
    spread = q3 - q1
    return float(q1 - multiplier * spread), float(q3 + multiplier * spread)


def iqr_flags(values: pd.Series, multiplier: float = 1.5) -> pd.Series:
    """True where a value lies outside the Tukey fences of the sample. NaN is never flagged."""
    lower, upper = iqr_bounds(values, multiplier)
    return outside(values, lower, upper)


def robust_scale(sample: np.ndarray) -> float:
    """Divide (x - median) by this to get a modified z-score.

    MAD / 0.6745, or 1.253314 x the mean absolute deviation when MAD is zero.
    NaN when the sample has no spread at all.
    """
    sample = sample[~np.isnan(sample)]
    if sample.size == 0:
        return np.nan
    deviations = np.abs(sample - np.median(sample))
    mad = np.median(deviations)
    if mad > TOLERANCE:
        return mad / MAD_SCALE
    mean_ad = deviations.mean()
    # below the tolerance the "spread" is floating-point residue, not variation
    return MEAN_AD_SCALE * mean_ad if mean_ad > TOLERANCE else np.nan


def modified_zscores(values: pd.Series) -> pd.Series:
    """M = 0.6745 x (x - median) / MAD for every value in a sample."""
    clean = pd.Series(values, dtype="float64")
    return (clean - clean.median()) / robust_scale(clean.to_numpy())


def modified_zscore_flags(values: pd.Series, threshold: float = 3.5) -> pd.Series:
    return modified_zscores(values).abs() > threshold


# ------------------------------------------------------ rolling sample ----

def rolling_modified_zscores(series: pd.Series, window: int = 60,
                             min_periods: int = 30) -> pd.Series:
    """Modified z-score of each observation against the `window` observations before it."""
    history = series.shift(1).rolling(window, min_periods=min_periods)
    scale = history.apply(robust_scale, raw=True)
    return (series - history.median()) / scale


def rolling_iqr_bounds(series: pd.Series, window: int = 60, min_periods: int = 30,
                       multiplier: float = 3.0) -> pd.DataFrame:
    """Tukey fences from the `window` observations before each one. Columns: lower, upper."""
    history = series.shift(1).rolling(window, min_periods=min_periods)
    q1, q3 = history.quantile(0.25), history.quantile(0.75)
    spread = q3 - q1
    return pd.DataFrame({"lower": q1 - multiplier * spread, "upper": q3 + multiplier * spread})


# ----------------------------------------------------------- detectors ----

def _number(value):
    return None if value is None or pd.isna(value) else float(value)


def _row(ticker, date, dataset, variable, method, value, lower, upper, score, detail,
         peer_group_median=None, adjusted_value=None) -> dict:
    label = VARIABLE_LABELS.get(variable, variable)
    return {"ticker": ticker, "date": date, "dataset": dataset, "variable": variable,
            "method": method, "value": float(value),
            "peer_group_median": _number(peer_group_median),
            "adjusted_value": _number(adjusted_value),
            "lower_bound": _number(lower), "upper_bound": _number(upper), "score": _number(score),
            "message": f"{MESSAGE_PREFIX}: {label} {detail}"}


def detect_market_anomalies(prices: pd.DataFrame, window: int = 60, min_periods: int = 30,
                            iqr_multiplier: float = 3.0,
                            modified_zscore_threshold: float = 3.5) -> pd.DataFrame:
    """Rolling-window anomalies in daily returns and volume, per ticker.

    prices: ticker, date, adj_close, volume, is_stale_quote. Placeholder rows
    are excluded first. Volume is examined in logs, on days with volume > 0.
    """
    rows = []
    traded = prices[~prices["is_stale_quote"]].sort_values(["ticker", "date"])
    for ticker, group in traded.groupby("ticker", sort=True):
        group = group.set_index("date")
        volume = group["volume"].astype("float64")
        series = {
            "daily_return": group["adj_close"].astype("float64").pct_change(),
            "log_volume": np.log(volume.where(volume > 0)).dropna(),
        }
        for variable, values in series.items():
            values = values.dropna()
            if values.empty:
                continue
            scores = rolling_modified_zscores(values, window, min_periods)
            bounds = rolling_iqr_bounds(values, window, min_periods, iqr_multiplier)
            as_pct = variable == "daily_return"

            def shown(v, as_pct=as_pct):
                return f"{v:+.1%}" if as_pct else f"{np.exp(v):,.0f} shares"

            for date in values.index[scores.abs() > modified_zscore_threshold]:
                rows.append(_row(ticker, date, "market", variable, "modified_zscore",
                                 values[date], None, None, scores[date],
                                 f"of {shown(values[date])} has a modified z-score of "
                                 f"{scores[date]:+.1f} against its prior {window} days "
                                 f"(threshold {modified_zscore_threshold:g})"))
            for date in values.index[outside(values, bounds["lower"], bounds["upper"])]:
                lower, upper = bounds.at[date, "lower"], bounds.at[date, "upper"]
                spread = (upper - lower) / (1 + 2 * iqr_multiplier)
                beyond = max(lower - values[date], values[date] - upper)
                fence = (f"[{lower:+.1%}, {upper:+.1%}]" if as_pct
                         else f"[{np.exp(lower):,.0f}, {np.exp(upper):,.0f}]")
                rows.append(_row(ticker, date, "market", variable, "iqr", values[date], lower,
                                 upper, beyond / spread if spread > 0 else None,
                                 f"of {shown(values[date])} is outside the {iqr_multiplier:g}x IQR "
                                 f"range {fence} of its prior {window} days"))
    return pd.DataFrame(rows, columns=COLUMNS)


def fundamental_changes(statements: pd.DataFrame) -> pd.DataFrame:
    """Year-on-year changes per company and fiscal year, in the reporting currency.

    Columns: ticker, date (period end), variable, value. Margin changes are in
    fractions (0.02 = 2 percentage points); growth rates are fractions.
    Variables that do not apply to banks are left out for them.
    """
    annual = statements[statements["period_type"] == "annual"]
    keys = ["ticker", "period_end_date", "line_item"]
    # unstack keeps only the periods each company reported (a pivot would cross them all)
    wide = (annual.drop_duplicates(subset=keys, keep="last").set_index(keys)["original_value"]
            .astype("float64").unstack("line_item").sort_index())
    wide.columns.name = None
    sector_type = annual.groupby("ticker")["sector_type"].first()
    net_margin, ebitda_margin = ratios.net_margin(wide), ratios.ebitda_margin(wide)
    variables = {
        "revenue_growth": ratios.revenue_growth(wide),
        "net_margin_change": net_margin - ratios.prior_year(net_margin),
        "ebitda_margin_change": ebitda_margin - ratios.prior_year(ebitda_margin),
        "debt_growth": ratios.yoy_growth(ratios.column(wide, "total_debt")),
    }
    frames = []
    for variable, values in variables.items():
        frame = values.dropna().rename("value").reset_index()
        frame.columns = ["ticker", "date", "value"]
        if variable in NON_FINANCIAL_ONLY:
            frame = frame[frame["ticker"].map(sector_type) == "non_financial"]
        frames.append(frame.assign(variable=variable))
    return pd.concat(frames, ignore_index=True)[["ticker", "date", "variable", "value"]]


def sector_adjust(changes: pd.DataFrame, peer_group: dict, min_group_size: int = 3) -> pd.DataFrame:
    """Measure each change relative to its peer group.

    For each variable and period, the median change across the company's peer
    group (all members with a value, the company included) is subtracted:

        adjusted_value = value - peer_group_median

    A group with fewer than `min_group_size` values for a period has no
    meaningful median; those rows get NaN and are not tested.
    Adds columns peer_group, peer_group_median, group_size, adjusted_value.
    """
    out = changes.copy()
    out["peer_group"] = out["ticker"].map(peer_group)
    grouped = out.groupby(["variable", "date", "peer_group"])["value"]
    out["peer_group_median"] = grouped.transform("median")
    out["group_size"] = grouped.transform("count")
    enough = out["group_size"] >= min_group_size
    out["peer_group_median"] = out["peer_group_median"].where(enough)
    out["adjusted_value"] = out["value"] - out["peer_group_median"]
    return out


def detect_fundamental_anomalies(changes: pd.DataFrame, iqr_multiplier: float = 1.5,
                                 modified_zscore_threshold: float = 3.5,
                                 value_column: str = "adjusted_value") -> pd.DataFrame:
    """Flag changes that are unusual within the pooled sample for their variable.

    The rules are applied to `value_column`, pooled across all companies and
    years: by default the sector-adjusted residual from `sector_adjust`. Passing
    value_column='value' tests the raw changes instead, which is kept only to
    show what the sector adjustment changes.
    """
    adjusted = value_column == "adjusted_value"
    rows = []
    tested = changes.dropna(subset=[value_column])
    for variable, group in tested.groupby("variable", sort=True):
        values = group[value_column].astype("float64")
        if len(values) < 4:
            continue                      # quartiles of fewer than 4 points say nothing
        lower, upper = iqr_bounds(values, iqr_multiplier)
        spread = (upper - lower) / (1 + 2 * iqr_multiplier)
        scores = modified_zscores(values)
        is_margin = variable.endswith("_margin_change")
        sample = f"{len(values)} {'sector-adjusted ' if adjusted else ''}company-years"

        def shown(v, is_margin=is_margin):
            return f"{v * 100:+.1f} percentage points" if is_margin else f"{v:+.1%}"

        def describe(index):
            raw = group.at[index, "value"]
            if not adjusted:
                return f"of {shown(raw)}", None, None
            median = group.at[index, "peer_group_median"]
            return (f"of {shown(raw)} is {shown(values[index])} relative to its peer-group "
                    f"median ({shown(median)}), which", median, values[index])

        for index in group.index[outside(values, lower, upper)]:
            text, median, residual = describe(index)
            beyond = max(lower - values[index], values[index] - upper)
            rows.append(_row(group.at[index, "ticker"], group.at[index, "date"], "fundamentals",
                             variable, "iqr", group.at[index, "value"], lower, upper,
                             beyond / spread if spread > 0 else None,
                             f"{text} is outside the {iqr_multiplier:g}x IQR range "
                             f"[{shown(lower)}, {shown(upper)}] of {sample}",
                             median, residual))
        for index in group.index[scores.abs() > modified_zscore_threshold]:
            text, median, residual = describe(index)
            rows.append(_row(group.at[index, "ticker"], group.at[index, "date"], "fundamentals",
                             variable, "modified_zscore", group.at[index, "value"], None, None,
                             scores[index],
                             f"{text} has a modified z-score of {scores[index]:+.1f} among "
                             f"{sample} (threshold {modified_zscore_threshold:g})",
                             median, residual))
    return pd.DataFrame(rows, columns=COLUMNS)
