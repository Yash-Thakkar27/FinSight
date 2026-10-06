"""Market regime detection on Nifty 50 returns (descriptive, Tier 2).

A two-state Markov-switching model with a different variance in each state:
one calm regime, one turbulent. A simple rolling-volatility threshold rule is
computed alongside as a transparent comparison.

This is a description of how the past can be segmented. The smoothed regime
probabilities use the whole sample, so they could not have been known at the
time. It is not a trading signal and is not presented as one.
"""

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

from src.ml.stats_tests import mean_pairwise_correlation

TRADING_DAYS = 252
THRESHOLD_WINDOW = 21
THRESHOLD_QUANTILE = 0.75


def fit_markov_regimes(market: pd.Series, seed: int = 42) -> dict:
    """Two-state Markov-switching variance model on daily market returns (in percent).

    Returns the per-day probability of the high-volatility regime (smoothed),
    each regime's annualized volatility, and the expected regime durations.
    The regimes are labelled by volatility: 0 = calm, 1 = turbulent.
    """
    np.random.seed(seed)
    sample = market.dropna() * 100
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = MarkovRegression(sample.to_numpy(), k_regimes=2, trend="c",
                                 switching_variance=True)
        fitted = model.fit(disp=False)
    params = dict(zip(model.param_names, fitted.params))
    variances = np.array([params["sigma2[0]"], params["sigma2[1]"]])
    turbulent = int(np.argmax(variances))
    probability = pd.Series(np.asarray(fitted.smoothed_marginal_probabilities)[:, turbulent],
                            index=sample.index, name="p_turbulent")
    volatility = np.sqrt(variances) / 100 * np.sqrt(TRADING_DAYS)
    durations = np.asarray(fitted.expected_durations)
    return {
        "probability": probability,
        "regime": (probability > 0.5).astype(int),
        "volatility": {"calm": float(volatility[1 - turbulent]),
                       "turbulent": float(volatility[turbulent])},
        "expected_duration_days": {"calm": float(durations[1 - turbulent]),
                                   "turbulent": float(durations[turbulent])},
        "log_likelihood": float(fitted.llf), "aic": float(fitted.aic),
        "converged": bool(fitted.mle_retvals.get("converged", True)),
    }


def threshold_regimes(market: pd.Series) -> pd.Series:
    """1 when trailing 21-day market volatility is in its top quartile, else 0."""
    rolling = market.rolling(THRESHOLD_WINDOW).std(ddof=1)
    return (rolling > rolling.quantile(THRESHOLD_QUANTILE)).astype(int).where(rolling.notna())


def regime_conditional_statistics(returns: pd.DataFrame, market: pd.Series,
                                  regime: pd.Series) -> pd.DataFrame:
    """Volatility and average pairwise correlation within each regime."""
    rows = []
    aligned = regime.reindex(returns.index)
    for value, label in ((0, "calm"), (1, "turbulent")):
        days = aligned == value
        if days.sum() < 30:
            continue
        rows.append({
            "regime": label, "days": int(days.sum()),
            "share_of_days": float(days.mean()),
            "market_volatility": float(market.reindex(returns.index)[days].std(ddof=1)
                                       * np.sqrt(TRADING_DAYS)),
            "mean_stock_volatility": float(returns[days].std(ddof=1).mean()
                                           * np.sqrt(TRADING_DAYS)),
            "mean_pairwise_correlation": mean_pairwise_correlation(returns[days].to_numpy()),
        })
    return pd.DataFrame(rows)


def run_experiment(returns: pd.DataFrame, market: pd.Series, seed: int = 42) -> dict:
    fitted = fit_markov_regimes(market, seed)
    rule = threshold_regimes(market)
    daily = pd.DataFrame({"p_turbulent": fitted["probability"], "regime": fitted["regime"],
                          "threshold_regime": rule.reindex(fitted["probability"].index)})
    both = daily.dropna()
    return {
        "daily": daily.reset_index(names="date"),
        "by_regime": regime_conditional_statistics(returns, market, fitted["regime"]),
        "by_threshold_regime": regime_conditional_statistics(returns, market, rule),
        "summary": {
            "volatility": fitted["volatility"],
            "expected_duration_days": fitted["expected_duration_days"],
            "log_likelihood": fitted["log_likelihood"], "aic": fitted["aic"],
            "converged": fitted["converged"],
            "turbulent_share_of_days": float(fitted["regime"].mean()),
            "agreement_with_threshold_rule": float((both["regime"]
                                                    == both["threshold_regime"]).mean()),
        },
        "params": {"model": "2-state Markov switching, switching variance, constant mean",
                   "threshold_rule": f"trailing {THRESHOLD_WINDOW}-day volatility above its "
                                     f"{THRESHOLD_QUANTILE:.0%} quantile",
                   "seed": seed, "n_days": int(len(fitted["probability"]))},
    }
