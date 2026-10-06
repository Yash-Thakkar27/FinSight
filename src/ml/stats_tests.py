"""Statistical tests for the Data Science Lab.

Each function returns plain rows (dicts) with a test statistic, a p-value where
one applies, an effect size, and a confidence interval where one applies, so
the app can show results with their uncertainty instead of bare numbers.

    return_distribution_tests   normality (Jarque-Bera), skewness, excess kurtosis
    sharpe_confidence_intervals block-bootstrap interval for each Sharpe ratio
    peer_median_intervals       bootstrap interval for each peer-group median
    correlation_significance    p-values for every pair, Benjamini-Hochberg corrected
    sector_difference_tests     Kruskal-Wallis across sectors, pairwise post-hoc, effect sizes
    stress_correlation_test     average correlation in stressed vs calm markets
"""

import itertools

import numpy as np
import pandas as pd
from scipy import stats

from src.ml.evaluation import benjamini_hochberg, block_bootstrap_indices, bootstrap_ci

SEED = 42
TRADING_DAYS = 252
ALPHA = 0.05
SHARPE_BLOCK = 21            # block length for resampling daily returns
STRESS_WINDOW = 21           # days of market volatility that define the regime
STRESS_QUANTILE = 0.75       # top quartile of rolling market volatility = stressed


def return_distribution_tests(returns: pd.DataFrame) -> list[dict]:
    """Jarque-Bera normality test with skewness and excess kurtosis, per column of returns.

    Under normality skewness is 0 and excess kurtosis is 0. Daily equity
    returns typically show fat tails (positive excess kurtosis), which is why
    volatility-based risk measures understate the frequency of large moves.
    """
    rows = []
    for ticker in returns.columns:
        sample = returns[ticker].dropna().to_numpy()
        statistic, p_value = stats.jarque_bera(sample)
        standardized = (sample - sample.mean()) / sample.std(ddof=1)
        rows.append({
            "test": "jarque_bera", "subject": ticker, "statistic": float(statistic),
            "p_value": float(p_value), "n": int(len(sample)),
            "skewness": float(stats.skew(sample)),
            "excess_kurtosis": float(stats.kurtosis(sample)),          # Fisher: normal = 0
            # share of days beyond 3 standard deviations; 0.27% under normality
            "share_beyond_3_sd": float(np.mean(np.abs(standardized) > 3)),
        })
    adjusted = benjamini_hochberg([r["p_value"] for r in rows])
    for row, p in zip(rows, adjusted):
        row["p_adjusted"] = float(p)
    return rows


def sharpe(sample: np.ndarray, risk_free_rate: float) -> float:
    spread = sample.std(ddof=1)
    if spread < 1e-12:
        return np.nan
    return (sample.mean() * TRADING_DAYS - risk_free_rate) / (spread * np.sqrt(TRADING_DAYS))


def sharpe_confidence_intervals(returns: pd.DataFrame, risk_free_rate: float,
                                n_resamples: int = 2000, seed: int = SEED) -> list[dict]:
    """95% interval for each Sharpe ratio by moving-block bootstrap of daily returns.

    Blocks of 21 days keep the volatility clustering in the data. The interval
    is wide: a few years of daily returns say little about a mean return.
    """
    rows = []
    for ticker in returns.columns:
        sample = returns[ticker].dropna().to_numpy()
        result = bootstrap_ci(sample, lambda s: sharpe(s, risk_free_rate), n_resamples,
                              block=SHARPE_BLOCK, seed=seed)
        rows.append({"test": "sharpe_bootstrap_ci", "subject": ticker,
                     "statistic": result["estimate"], "ci_low": result["ci_low"],
                     "ci_high": result["ci_high"], "n": result["n"],
                     "excludes_zero": bool(result["ci_low"] > 0 or result["ci_high"] < 0)})
    return rows


def peer_median_intervals(values: pd.DataFrame, n_resamples: int = 2000,
                          seed: int = SEED) -> list[dict]:
    """Bootstrap interval for the median of each peer group.

    values: columns peer_group, metric_name, value (one row per company).
    With five companies per group the interval can only take values that occur
    in the group, so it mostly spans the group's range: it shows how little a
    peer median of five pins down.
    """
    rows = []
    for (group, metric), frame in values.dropna(subset=["value"]).groupby(
            ["peer_group", "metric_name"]):
        sample = frame["value"].to_numpy()
        if len(sample) < 3:
            continue
        result = bootstrap_ci(sample, np.median, n_resamples, seed=seed)
        rows.append({"test": "peer_median_bootstrap_ci", "subject": f"{group} | {metric}",
                     "statistic": result["estimate"], "ci_low": result["ci_low"],
                     "ci_high": result["ci_high"], "n": result["n"]})
    return rows


def correlation_significance(returns: pd.DataFrame) -> list[dict]:
    """Pearson correlation and p-value for every pair, with Benjamini-Hochberg correction.

    Testing many pairs at 5% would produce false positives by chance alone, so
    the p-values are adjusted to control the false discovery rate.
    """
    rows = []
    for a, b in itertools.combinations(returns.columns, 2):
        pair = returns[[a, b]].dropna()
        correlation, p_value = stats.pearsonr(pair[a], pair[b])
        rows.append({"test": "pearson_correlation", "subject": f"{a} | {b}",
                     "statistic": float(correlation), "p_value": float(p_value),
                     "n": int(len(pair)), "effect_size": float(correlation ** 2)})
    adjusted = benjamini_hochberg([r["p_value"] for r in rows])
    for row, p in zip(rows, adjusted):
        row["p_adjusted"] = float(p)
        row["significant"] = bool(p < ALPHA)
    return rows


def rank_biserial(a: np.ndarray, b: np.ndarray, u_statistic: float) -> float:
    """Effect size for Mann-Whitney U: 2U/(n1*n2) - 1, from -1 to 1."""
    return float(2 * u_statistic / (len(a) * len(b)) - 1)


def sector_difference_tests(values: pd.DataFrame, variable: str) -> list[dict]:
    """Do sectors differ on a variable? Kruskal-Wallis, then pairwise Mann-Whitney U.

    values: columns sector, value (one row per company).
    Kruskal-Wallis makes no normality assumption, which matters with five
    companies per sector. Its effect size is epsilon-squared, H / (n - 1):
    the share of rank variance explained by sector. Pairwise p-values are
    Benjamini-Hochberg adjusted and come with a rank-biserial effect size. With
    five against five the smallest possible two-sided Mann-Whitney p-value is
    0.0079, so after correction few pairs can reach significance; the effect
    sizes carry the information.
    """
    clean = values.dropna(subset=["value"])
    groups = {sector: frame["value"].to_numpy() for sector, frame in clean.groupby("sector")
              if len(frame) >= 2}
    if len(groups) < 2:
        return []
    statistic, p_value = stats.kruskal(*groups.values())
    n = sum(len(g) for g in groups.values())
    rows = [{"test": "kruskal_wallis", "subject": variable, "statistic": float(statistic),
             "p_value": float(p_value), "n": int(n), "effect_size": float(statistic / (n - 1)),
             "effect_size_name": "epsilon_squared", "groups": len(groups)}]
    pairs = []
    for a, b in itertools.combinations(sorted(groups), 2):
        u_statistic, p_pair = stats.mannwhitneyu(groups[a], groups[b], alternative="two-sided")
        pairs.append({"test": "mann_whitney_posthoc", "subject": f"{variable}: {a} vs {b}",
                      "statistic": float(u_statistic), "p_value": float(p_pair),
                      "n": int(len(groups[a]) + len(groups[b])),
                      "effect_size": rank_biserial(groups[a], groups[b], u_statistic),
                      "effect_size_name": "rank_biserial"})
    adjusted = benjamini_hochberg([r["p_value"] for r in pairs])
    for row, p in zip(pairs, adjusted):
        row["p_adjusted"] = float(p)
        row["significant"] = bool(p < ALPHA)
    return rows + pairs


def mean_pairwise_correlation(sample: np.ndarray) -> float:
    """Average off-diagonal correlation of the columns of `sample`."""
    matrix = np.corrcoef(sample, rowvar=False)
    upper = matrix[np.triu_indices_from(matrix, k=1)]
    return float(np.nanmean(upper))


def stress_correlation_test(returns: pd.DataFrame, market: pd.Series, n_resamples: int = 1000,
                            seed: int = SEED) -> list[dict]:
    """Is the average pairwise correlation higher when the market is stressed?

    A day is 'stressed' when the market's trailing 21-day volatility, known at
    the previous close, is in its top quartile over the sample. The difference
    in average correlation (stressed - calm) gets a block-bootstrap interval.
    The quartile threshold uses the whole sample, so this describes the past;
    it is not a rule that could have been applied in real time.
    """
    rolling = market.rolling(STRESS_WINDOW).std(ddof=1).shift(1)
    threshold = rolling.quantile(STRESS_QUANTILE)
    known = rolling.notna()
    stressed = (rolling > threshold) & known
    calm = (rolling <= threshold) & known
    data = returns.to_numpy()
    flags = stressed.to_numpy()
    usable = known.to_numpy()

    def difference(index: np.ndarray) -> float:
        rows, is_stressed = data[index], flags[index]
        valid = usable[index]
        if is_stressed[valid].sum() < 30 or (~is_stressed & valid).sum() < 30:
            return np.nan
        return (mean_pairwise_correlation(rows[is_stressed & valid])
                - mean_pairwise_correlation(rows[~is_stressed & valid]))

    rng = np.random.default_rng(seed)
    estimates = [difference(block_bootstrap_indices(len(data), STRESS_WINDOW, rng))
                 for _ in range(n_resamples)]
    low, high = np.nanquantile(estimates, [0.025, 0.975])
    in_stress = mean_pairwise_correlation(data[flags])
    in_calm = mean_pairwise_correlation(data[calm.to_numpy()])
    return [{
        "test": "stress_vs_calm_correlation", "subject": "all company pairs",
        "statistic": float(in_stress - in_calm), "ci_low": float(low), "ci_high": float(high),
        "n": int(known.sum()), "mean_correlation_stressed": in_stress,
        "mean_correlation_calm": in_calm, "stressed_days": int(stressed.sum()),
        "calm_days": int(calm.sum()), "excludes_zero": bool(low > 0 or high < 0),
        "market_volatility_threshold_annualized": float(threshold * np.sqrt(TRADING_DAYS)),
    }]


def rolling_correlation_summary(returns: pd.DataFrame, window: int = 63) -> pd.DataFrame:
    """Average pairwise correlation over rolling windows: how stable is co-movement?"""
    values = returns.to_numpy()
    rows = []
    for end in range(window, len(values) + 1, 21):
        rows.append({"date": returns.index[end - 1],
                     "mean_pairwise_correlation": mean_pairwise_correlation(
                         values[end - window:end])})
    return pd.DataFrame(rows)
