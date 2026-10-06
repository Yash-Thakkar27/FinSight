"""Volatility forecasting: the main supervised task of the Data Science Lab.

Question: can a model forecast a stock's realized volatility over the next 5
and 21 trading days better than the standard naive baselines?

    Target     realized variance over t+1 .. t+h (mean of squared daily returns)
    Baselines  hist_21  trailing 21-day realized variance
               ewma     RiskMetrics EWMA, lambda = 0.94
    Models     garch    GARCH(1,1), zero mean, refit at the start of every fold
               ridge    ridge regression on lagged volatility features (pooled across tickers)
               gbm      gradient-boosted trees on the same features (pooled)
    Validation walk-forward, expanding window, quarterly test blocks, training rows purged
               so no training target overlaps a test period
    Metrics    RMSE and MAE on annualized volatility, QLIKE on variance; per fold,
               per ticker and pooled; Diebold-Mariano test against the best baseline

This forecasts volatility for risk measurement. It does not forecast prices or
the direction of returns, and it is not a trading signal. If a model does not
beat its baseline, the results say so.
"""

import logging
import warnings

import numpy as np
import pandas as pd
from arch import arch_model
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.ml import evaluation, features
from src.ml.splits import Fold, walk_forward_folds

log = logging.getLogger(__name__)

SEED = 42
HORIZONS = (5, 21)
INITIAL_TRAIN = 504          # about two years of trading days before the first test block
TEST_SIZE = 63               # about one quarter per fold
BASELINES = ("hist_21", "ewma")
MODELS = ("garch", "ridge", "gbm")
ALL_FORECASTERS = BASELINES + MODELS
RIDGE_ALPHA = 1.0
GBM_PARAMS = {"max_depth": 3, "learning_rate": 0.05, "max_iter": 200, "min_samples_leaf": 50}
METRICS = ("rmse", "mae", "qlike")


# ------------------------------------------------------------------ panel ----

def build_panel(prices: pd.DataFrame, benchmark_ticker: str) -> pd.DataFrame:
    """Features, baseline forecasts and targets for every company, one row per ticker and date.

    prices: ticker, date, adj_close, high, low, volume, is_stale_quote (all tickers,
            including the benchmark). Placeholder rows are removed first.
    Baseline columns hold the variance forecast made at date t for the days after it.
    """
    traded = prices[~prices["is_stale_quote"]].copy()
    traded["date"] = pd.to_datetime(traded["date"])
    market = (traded[traded["ticker"] == benchmark_ticker].set_index("date")["adj_close"]
              .sort_index().pct_change())
    frames = []
    for ticker, group in traded[traded["ticker"] != benchmark_ticker].groupby("ticker"):
        table = features.build_features(group.set_index("date"), market)
        returns = table["return"]
        # baselines: the latest estimate is the forecast for every day of the horizon
        table["hist_21"] = features.realized_variance(returns, 21)
        table["ewma"] = features.ewma_variance(returns)
        for horizon in HORIZONS:
            table[f"target_{horizon}"] = features.forward_realized_variance(returns, horizon)
        table.insert(0, "ticker", ticker)
        frames.append(table.reset_index())
    return pd.concat(frames, ignore_index=True)


def make_folds(panel: pd.DataFrame, horizon: int) -> list[Fold]:
    dates = sorted(panel["date"].unique())
    return walk_forward_folds(dates, INITIAL_TRAIN, TEST_SIZE, horizon)


# ----------------------------------------------------------------- models ----

def garch_forecasts(panel: pd.DataFrame, folds: list[Fold], horizon: int) -> pd.Series:
    """GARCH(1,1) variance forecasts, averaged over the horizon, per ticker.

    For each fold the parameters are estimated on returns up to the cutoff
    only. Forecasts for the fold's test dates then use those fixed parameters,
    with the variance recursion updated by the returns observed up to each
    forecast date. Returns a Series aligned with the panel's index.
    """
    out = pd.Series(np.nan, index=panel.index, dtype="float64")
    failures = 0
    for _, group in panel.groupby("ticker"):
        series = group.set_index("date")["return"].dropna() * 100       # percent, for the optimizer
        positions = pd.Series(group.index, index=group["date"])
        for fold in folds:
            n_train = int((series.index <= fold.cutoff).sum())
            test = [d for d in fold.test_dates if d in series.index]
            if n_train < 250 or not test:
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    fitted = arch_model(series, mean="Zero", vol="GARCH", p=1, q=1).fit(
                        last_obs=n_train, disp="off")
                    variance = fitted.forecast(horizon=horizon, start=n_train,
                                               reindex=False).variance
            except Exception:
                failures += 1
                continue
            forecast = variance.mean(axis=1).reindex(test) / 1e4          # back to decimal^2
            out.loc[positions.reindex(test).to_numpy()] = forecast.to_numpy()
    if failures:
        log.warning("GARCH: %d ticker-folds failed to fit and have no forecast", failures)
    return out


def pooled_model_forecasts(panel: pd.DataFrame, folds: list[Fold], horizon: int,
                           make_model) -> pd.Series:
    """Variance forecasts from a regression fitted on all tickers together.

    The model predicts log realized variance. Fitted once per fold on rows
    whose date is in the fold's (purged) training dates; any scaling is learned
    from those rows only. The forecast is exp(prediction + s^2 / 2), where s^2
    is the training residual variance: the mean of a log-normal, so the
    forecast is not biased low by the log transform.
    """
    target = f"target_{horizon}"
    usable = panel.dropna(subset=features.FEATURE_COLUMNS)
    out = pd.Series(np.nan, index=panel.index, dtype="float64")
    for fold in folds:
        train = usable[usable["date"].isin(fold.train_dates)].dropna(subset=[target])
        test = usable[usable["date"].isin(fold.test_dates)]
        if len(train) < 500 or test.empty:
            continue
        model = make_model()
        y = features.safe_log(train[target])
        model.fit(train[features.FEATURE_COLUMNS], y)
        residual_variance = float(np.var(y - model.predict(train[features.FEATURE_COLUMNS])))
        prediction = model.predict(test[features.FEATURE_COLUMNS])
        out.loc[test.index] = np.exp(prediction + residual_variance / 2)
    return out


def make_ridge():
    return make_pipeline(StandardScaler(), Ridge(alpha=RIDGE_ALPHA))


def make_gbm(seed: int = SEED):
    return HistGradientBoostingRegressor(random_state=seed, **GBM_PARAMS)


# -------------------------------------------------------------- evaluation ----

def fold_of(dates: pd.Series, folds: list[Fold]) -> pd.Series:
    lookup = {date: fold.number for fold in folds for date in fold.test_dates}
    return dates.map(lookup)


def metric_rows(evaluated: pd.DataFrame, horizon: int, by: list[str], scope: str) -> list[dict]:
    """Metrics for every forecaster, grouped by `by` (empty = pooled)."""
    rows = []
    groups = evaluated.groupby(by) if by else [((), evaluated)]
    for key, group in groups:
        key = key if isinstance(key, tuple) else (key,)
        for name in ALL_FORECASTERS:
            scores = evaluation.forecast_metrics(group["target"], group[name])
            for metric in METRICS:
                rows.append({"scope": scope, "horizon": horizon, "model": name, "metric": metric,
                             "value": scores[metric], "n": scores["n"], **dict(zip(by, key))})
    return rows


def best_baseline(pooled: pd.DataFrame, horizon: int, metric: str) -> str:
    rows = pooled[(pooled["horizon"] == horizon) & (pooled["metric"] == metric)
                  & pooled["model"].isin(BASELINES)]
    return rows.loc[rows["value"].idxmin(), "model"]


def comparison_table(pooled: pd.DataFrame, by_fold: pd.DataFrame) -> pd.DataFrame:
    """Model-vs-baseline table: pooled metric, change vs the best baseline, fold variation."""
    rows = []
    for horizon in sorted(pooled["horizon"].unique()):
        for metric in METRICS:
            reference = best_baseline(pooled, horizon, metric)
            select = (pooled["horizon"] == horizon) & (pooled["metric"] == metric)
            reference_value = pooled[select & (pooled["model"] == reference)]["value"].item()
            folds = by_fold[(by_fold["horizon"] == horizon) & (by_fold["metric"] == metric)]
            reference_folds = folds[folds["model"] == reference].set_index("fold")["value"]
            for name in ALL_FORECASTERS:
                value = pooled[select & (pooled["model"] == name)]["value"].item()
                own_folds = folds[folds["model"] == name].set_index("fold")["value"]
                rows.append({
                    "horizon": horizon, "metric": metric, "model": name,
                    "kind": "baseline" if name in BASELINES else "model",
                    "value": value, "best_baseline": reference,
                    # negative = lower loss than the best baseline
                    "change_vs_best_baseline_pct": (value / reference_value - 1) * 100,
                    "fold_mean": float(own_folds.mean()),
                    "fold_std": float(own_folds.std(ddof=1)),
                    "folds": int(len(own_folds)),
                    "folds_beating_best_baseline": int((own_folds < reference_folds).sum()),
                })
    return pd.DataFrame(rows)


def diebold_mariano_table(evaluated: pd.DataFrame, horizon: int, reference: str) -> pd.DataFrame:
    """DM tests of each forecaster against `reference`, on QLIKE loss.

    Pooled: the loss differential is averaged across tickers for each date,
    giving one time series, then tested with h-1 Newey-West lags.
    Per ticker: the same test on each ticker's own series; the table reports how
    many tickers show a significantly lower or higher loss at the 5% level.
    """
    losses = {name: evaluation.qlike(evaluated["target"], evaluated[name])
              for name in ALL_FORECASTERS}
    rows = []
    for name in ALL_FORECASTERS:
        if name == reference:
            continue
        differential = pd.Series(losses[name] - losses[reference], index=evaluated.index)
        pooled = differential.groupby(evaluated["date"]).mean().sort_index()
        result = evaluation.diebold_mariano(pooled.to_numpy(), np.zeros(len(pooled)), horizon)
        better = worse = tested = 0
        for _, group in evaluated.groupby("ticker"):
            per_ticker = evaluation.diebold_mariano(
                differential.loc[group.sort_values("date").index].to_numpy(),
                np.zeros(len(group)), horizon)
            if per_ticker["p_value"] is None:
                continue
            tested += 1
            if per_ticker["p_value"] < 0.05:
                better += per_ticker["statistic"] < 0
                worse += per_ticker["statistic"] > 0
        rows.append({"horizon": horizon, "model": name, "benchmark": reference, "loss": "qlike",
                     "dm_statistic": result["statistic"], "p_value": result["p_value"],
                     "mean_loss_difference": result["mean_difference"], "n_dates": result["n"],
                     "tickers_tested": tested, "tickers_significantly_better": int(better),
                     "tickers_significantly_worse": int(worse)})
    return pd.DataFrame(rows)


# -------------------------------------------------------------- experiment ----

def run_experiment(prices: pd.DataFrame, benchmark_ticker: str, seed: int = SEED) -> dict:
    """Run the full walk-forward comparison. Returns DataFrames ready to store.

    forecasts   ticker, date, horizon, fold, target and one column per forecaster
                (daily variance), restricted to rows every forecaster covers
    metrics     long table: scope (pooled | fold | ticker), horizon, model, metric, value, n
    comparison  model-vs-baseline table with fold variation
    dm          Diebold-Mariano tests against the best baseline (by pooled QLIKE)
    folds       fold boundaries
    """
    panel = build_panel(prices, benchmark_ticker)
    forecast_frames, metric_frames, dm_frames, fold_rows = [], [], [], []
    for horizon in HORIZONS:
        folds = make_folds(panel, horizon)
        frame = panel[["ticker", "date", "hist_21", "ewma"]].copy()
        frame["target"] = panel[f"target_{horizon}"]
        frame["garch"] = garch_forecasts(panel, folds, horizon)
        frame["ridge"] = pooled_model_forecasts(panel, folds, horizon, make_ridge)
        frame["gbm"] = pooled_model_forecasts(panel, folds, horizon, lambda: make_gbm(seed))
        frame["fold"] = fold_of(frame["date"], folds)
        # one common sample, so every forecaster is scored on exactly the same rows
        evaluated = frame.dropna(subset=["target", "fold", *ALL_FORECASTERS]).copy()
        evaluated["fold"] = evaluated["fold"].astype(int)
        evaluated.insert(2, "horizon", horizon)

        rows = (metric_rows(evaluated, horizon, [], "pooled")
                + metric_rows(evaluated, horizon, ["fold"], "fold")
                + metric_rows(evaluated, horizon, ["ticker"], "ticker"))
        metrics = pd.DataFrame(rows)
        reference = best_baseline(metrics[metrics["scope"] == "pooled"], horizon, "qlike")
        dm_frames.append(diebold_mariano_table(evaluated, horizon, reference))
        forecast_frames.append(evaluated)
        metric_frames.append(metrics)
        used = set(evaluated["fold"])
        for fold in folds:
            if fold.number in used:
                tested = evaluated[evaluated["fold"] == fold.number]
                fold_rows.append({
                    "horizon": horizon, "fold": fold.number,
                    "train_start": fold.train_dates[0], "train_end": fold.train_dates[-1],
                    "cutoff": fold.cutoff, "test_start": tested["date"].min(),
                    "test_end": tested["date"].max(), "train_dates": len(fold.train_dates),
                    "test_rows": len(tested)})
        log.info("Volatility h=%d: %d folds, %s evaluated rows, best baseline %s",
                 horizon, len(used), f"{len(evaluated):,}", reference)

    metrics = pd.concat(metric_frames, ignore_index=True)
    pooled = metrics[metrics["scope"] == "pooled"]
    by_fold = metrics[metrics["scope"] == "fold"]
    return {
        "forecasts": pd.concat(forecast_frames, ignore_index=True),
        "metrics": metrics,
        "comparison": comparison_table(pooled, by_fold),
        "dm": pd.concat(dm_frames, ignore_index=True),
        "folds": pd.DataFrame(fold_rows),
        "params": {
            "seed": seed, "horizons": list(HORIZONS), "initial_train_days": INITIAL_TRAIN,
            "test_block_days": TEST_SIZE, "baselines": list(BASELINES), "models": list(MODELS),
            "ewma_lambda": features.EWMA_LAMBDA, "ridge_alpha": RIDGE_ALPHA,
            "gbm": GBM_PARAMS, "garch": "GARCH(1,1), zero mean, normal errors, refit each fold",
            "features": features.FEATURE_COLUMNS, "target": "log realized variance over t+1..t+h",
            "tickers": int(panel["ticker"].nunique()),
        },
    }
