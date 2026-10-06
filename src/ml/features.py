"""Features and targets for volatility forecasting.

The rule for every feature: its value at date t uses only information
available at the close of date t. All windows are trailing (they end at t).
The targets are the only forward-looking quantities, and they are built by a
separate function so the two cannot be confused.

Variances are daily (not annualized) unless a function says otherwise.
Realized variance is the mean of squared daily returns (zero-mean), the usual
convention for daily data.
"""

import numpy as np
import pandas as pd

TRADING_DAYS = 252
VARIANCE_FLOOR = 1e-10          # keeps logs finite on a window of zero returns
EWMA_LAMBDA = 0.94              # RiskMetrics

FEATURE_COLUMNS = [
    "log_rv_1", "log_rv_5", "log_rv_21", "log_rv_63",      # own realized variance, 4 horizons
    "abs_return",                                           # latest absolute return
    "log_volume_ratio",                                     # volume vs its trailing 21-day mean
    "log_parkinson_5", "log_parkinson_21",                  # range-based variance
    "mkt_log_rv_5", "mkt_log_rv_21",                        # market (Nifty 50) realized variance
]


def realized_variance(returns: pd.Series, window: int) -> pd.Series:
    """Trailing realized variance: mean of r^2 over the `window` days ending at t."""
    return (returns ** 2).rolling(window, min_periods=window).mean()


def forward_realized_variance(returns: pd.Series, horizon: int) -> pd.Series:
    """TARGET: mean of r^2 over the `horizon` days after t (t+1 .. t+h).

    NaN for the last `horizon` dates, whose future is not yet known.
    """
    return realized_variance(returns, horizon).shift(-horizon)


def parkinson_variance(high: pd.Series, low: pd.Series, window: int) -> pd.Series:
    """Parkinson (1980) range-based variance: mean(ln(H/L)^2) / (4 ln 2), trailing window."""
    log_range_squared = np.log(high / low) ** 2
    return log_range_squared.rolling(window, min_periods=window).mean() / (4 * np.log(2))


def ewma_variance(returns: pd.Series, lam: float = EWMA_LAMBDA) -> pd.Series:
    """RiskMetrics EWMA variance: s_t = lam * s_{t-1} + (1 - lam) * r_t^2, with s_0 = r_0^2.

    s_t uses returns up to and including t, so it is the forecast made at t for t+1.
    """
    squared = (returns ** 2).to_numpy(dtype="float64")
    out = np.full(len(squared), np.nan)
    state = np.nan
    for i, value in enumerate(squared):
        if np.isnan(value):
            continue
        state = value if np.isnan(state) else lam * state + (1 - lam) * value
        out[i] = state
    return pd.Series(out, index=returns.index)


def safe_log(values: pd.Series) -> pd.Series:
    return np.log(values.clip(lower=VARIANCE_FLOOR))


def build_features(prices: pd.DataFrame, market_returns: pd.Series) -> pd.DataFrame:
    """Feature table for one ticker, indexed by date.

    prices: one ticker's traded rows (placeholders removed), indexed by date, with
            adj_close, high, low, volume.
    market_returns: daily Nifty 50 returns indexed by date.

    Columns: `return` (the daily return, kept for the baselines and targets)
    plus FEATURE_COLUMNS. Rows lacking a full 63-day history have NaN features.
    """
    prices = prices.sort_index()
    returns = prices["adj_close"].pct_change()
    volume = prices["volume"].astype("float64").where(prices["volume"] > 0)
    market = market_returns.reindex(prices.index)

    features = pd.DataFrame({"return": returns}, index=prices.index)
    for window in (1, 5, 21, 63):
        features[f"log_rv_{window}"] = safe_log(realized_variance(returns, window))
    features["abs_return"] = returns.abs()
    features["log_volume_ratio"] = np.log(volume / volume.rolling(21, min_periods=21).mean())
    for window in (5, 21):
        features[f"log_parkinson_{window}"] = safe_log(
            parkinson_variance(prices["high"], prices["low"], window))
        features[f"mkt_log_rv_{window}"] = safe_log(realized_variance(market, window))
    return features


def annualized_volatility(variance):
    """Daily variance -> annualized volatility."""
    return np.sqrt(variance * TRADING_DAYS)
