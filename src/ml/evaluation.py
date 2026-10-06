"""Forecast evaluation: loss functions, the Diebold-Mariano test, bootstrap intervals."""

import numpy as np
import pandas as pd
from scipy import stats

from src.ml.features import TRADING_DAYS, VARIANCE_FLOOR


def qlike(realized_variance, forecast_variance):
    """QLIKE loss per observation: RV/f - ln(RV/f) - 1.

    Zero when the forecast equals the realized variance, positive otherwise.
    Unlike squared error it is robust to noise in the volatility proxy (Patton,
    2011) and penalises under-prediction of variance more than over-prediction.
    """
    ratio = np.maximum(realized_variance, VARIANCE_FLOOR) / np.maximum(forecast_variance,
                                                                        VARIANCE_FLOOR)
    return ratio - np.log(ratio) - 1


def volatility_errors(realized_variance, forecast_variance):
    """Forecast error in annualized volatility terms (forecast - realized)."""
    return (np.sqrt(np.asarray(forecast_variance) * TRADING_DAYS)
            - np.sqrt(np.asarray(realized_variance) * TRADING_DAYS))


def forecast_metrics(realized_variance, forecast_variance) -> dict:
    """RMSE and MAE on annualized volatility, and mean QLIKE on variance."""
    realized = np.asarray(realized_variance, dtype="float64")
    forecast = np.asarray(forecast_variance, dtype="float64")
    errors = volatility_errors(realized, forecast)
    return {"rmse": float(np.sqrt(np.mean(errors ** 2))), "mae": float(np.mean(np.abs(errors))),
            "qlike": float(np.mean(qlike(realized, forecast))), "n": int(len(errors))}


def newey_west_variance(series: np.ndarray, lags: int) -> float:
    """Long-run variance of a series with Bartlett weights up to `lags`."""
    centred = series - series.mean()
    n = len(centred)
    variance = np.dot(centred, centred) / n
    for lag in range(1, min(lags, n - 1) + 1):
        weight = 1 - lag / (lags + 1)
        variance += 2 * weight * np.dot(centred[lag:], centred[:-lag]) / n
    return float(variance)


def diebold_mariano(loss_model, loss_benchmark, horizon: int = 1) -> dict:
    """Diebold-Mariano test of equal predictive accuracy.

    d_t = loss_model_t - loss_benchmark_t. H0: E[d] = 0. A negative statistic
    means the model has the lower loss. h-step forecasts made every day
    overlap, so d_t is autocorrelated up to lag h-1; the variance uses a
    Newey-West estimator with h-1 lags, and the Harvey-Leybourne-Newbold
    small-sample correction is applied with Student-t(n-1) critical values.
    """
    differential = np.asarray(loss_model, dtype="float64") - np.asarray(loss_benchmark,
                                                                        dtype="float64")
    differential = differential[~np.isnan(differential)]
    n = len(differential)
    if n < 10:
        return {"statistic": None, "p_value": None, "n": n, "mean_difference": None}
    long_run_variance = newey_west_variance(differential, horizon - 1)
    if long_run_variance <= 0:
        return {"statistic": None, "p_value": None, "n": n,
                "mean_difference": float(differential.mean())}
    statistic = differential.mean() / np.sqrt(long_run_variance / n)
    correction = np.sqrt((n + 1 - 2 * horizon + horizon * (horizon - 1) / n) / n)
    statistic *= correction
    p_value = 2 * stats.t.sf(abs(statistic), df=n - 1)
    return {"statistic": float(statistic), "p_value": float(p_value), "n": n,
            "mean_difference": float(differential.mean())}


def block_bootstrap_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Indices for one moving-block bootstrap resample of length n."""
    starts = rng.integers(0, n - block + 1, size=int(np.ceil(n / block)))
    return np.concatenate([np.arange(s, s + block) for s in starts])[:n]


def bootstrap_ci(values, statistic, n_resamples: int = 2000, confidence: float = 0.95,
                 block: int = 1, seed: int = 42) -> dict:
    """Percentile bootstrap confidence interval for statistic(values).

    block = 1 resamples observations independently. block > 1 resamples
    contiguous blocks, which preserves short-range dependence in time series.
    values may be 1-D or 2-D (rows are observations).
    """
    data = np.asarray(values, dtype="float64")
    n = len(data)
    rng = np.random.default_rng(seed)
    estimates = np.empty(n_resamples)
    for i in range(n_resamples):
        index = (rng.integers(0, n, size=n) if block <= 1
                 else block_bootstrap_indices(n, block, rng))
        estimates[i] = statistic(data[index])
    alpha = (1 - confidence) / 2
    low, high = np.nanquantile(estimates, [alpha, 1 - alpha])
    return {"estimate": float(statistic(data)), "ci_low": float(low), "ci_high": float(high),
            "confidence": confidence, "n": n, "n_resamples": n_resamples, "block": block}


def benjamini_hochberg(p_values) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (controls the false discovery rate)."""
    p = np.asarray(p_values, dtype="float64")
    n = len(p)
    order = np.argsort(p)
    ranked = p[order] * n / np.arange(1, n + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(adjusted, 1.0)
    return out


def summarize_folds(frame: pd.DataFrame, value: str = "value") -> dict:
    """Mean, standard deviation, min and max of a metric across folds."""
    return {"mean": float(frame[value].mean()), "std": float(frame[value].std(ddof=1)),
            "min": float(frame[value].min()), "max": float(frame[value].max()),
            "folds": int(len(frame))}
