"""Run the Data Science Lab and store its results.

Every task reads from PostgreSQL, runs with a fixed seed, and writes:
  * data/processed/ml_results/{task}/  result tables (Parquet) and summary.json
  * core.ml_* tables                   what the app displays
  * docs/model_cards/                  a model card with the actual numbers

A task's previous run is replaced, so the stored results are always those of
the latest run on the current data.
"""

import hashlib
import json
import logging
import math
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Connection, insert, text

from config.settings import PROCESSED_DIR, Universe
from src.analytics.peer_analytics import aligned_returns
from src.database import models, queries
from src.ml import anomaly_comparison, clustering, model_cards, regimes, stats_tests, volatility
from src.ml.features import TRADING_DAYS

log = logging.getLogger(__name__)

RESULTS_DIR = PROCESSED_DIR / "ml_results"
TASKS = ("volatility", "clustering", "stats_tests", "regimes", "anomaly_comparison")
SEED = 42
# variable -> the period type its latest value is taken from
SECTOR_TEST_VARIABLES = {"net_margin": "annual", "roe": "annual",
                         "volatility_3y": "point_in_time"}
PEER_MEDIAN_METRICS = {"net_margin": "annual", "roe": "annual", "revenue_growth": "annual",
                       "pe_ratio": "ttm", "pb_ratio": "ttm"}


def to_jsonable(value):
    """Convert numpy / pandas objects to plain JSON types; non-finite numbers become None."""
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return to_jsonable(value.to_dict("records"))
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return str(value)[:10]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def data_snapshot_id(prices: pd.DataFrame) -> str:
    """Short hash of the price data a run used, so results can be tied to their inputs."""
    frame = prices.sort_values(["ticker", "date"])[["ticker", "date", "adj_close", "volume"]]
    return hashlib.sha256(frame.to_csv(index=False).encode("utf-8")).hexdigest()[:12]


def records(frame: pd.DataFrame) -> list[dict]:
    return frame.astype(object).where(frame.notna(), None).to_dict("records")


def save_files(task: str, tables: dict, summary: dict, results_dir: Path) -> None:
    folder = results_dir / task
    folder.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        frame.to_parquet(folder / f"{name}.parquet", index=False)
    (folder / "summary.json").write_text(json.dumps(to_jsonable(summary), indent=2),
                                         encoding="utf-8")


def start_run(conn: Connection, task: str, seed: int, snapshot: str, params: dict,
              results: dict) -> int:
    conn.execute(text("DELETE FROM core.ml_runs WHERE task = :task"), {"task": task})
    return conn.execute(
        insert(models.MlRun.__table__).returning(models.MlRun.__table__.c.ml_run_id),
        [{"task": task, "seed": seed, "data_snapshot_id": snapshot,
          "params": to_jsonable(params), "results": to_jsonable(results)}],
    ).scalar_one()


def bulk_insert(conn: Connection, model, rows: list[dict]) -> None:
    if rows:
        conn.execute(insert(model.__table__), rows)


# ------------------------------------------------------------------ tasks ----

def run_volatility(conn, prices, universe, company_id, snapshot, seed, results_dir) -> dict:
    result = volatility.run_experiment(prices, universe.benchmark.ticker, seed)
    results = {"comparison": result["comparison"], "dm": result["dm"], "folds": result["folds"]}
    run_id = start_run(conn, "volatility", seed, snapshot, result["params"], results)

    metrics = result["metrics"].copy()
    metrics["ml_run_id"] = run_id
    for column in ("fold", "ticker"):
        if column not in metrics.columns:
            metrics[column] = None
    bulk_insert(conn, models.MlMetric, records(
        metrics[["ml_run_id", "scope", "horizon", "model", "metric", "fold", "ticker", "value",
                 "n"]]))

    forecasts = result["forecasts"]
    stored = pd.DataFrame({
        "ml_run_id": run_id, "company_id": forecasts["ticker"].map(company_id).astype(int),
        "date": forecasts["date"].dt.date, "horizon": forecasts["horizon"],
        "fold": forecasts["fold"],
        # stored as annualized volatility, the unit the app displays
        "realized": np.sqrt(forecasts["target"] * TRADING_DAYS),
        **{name: np.sqrt(forecasts[name] * TRADING_DAYS) for name in volatility.ALL_FORECASTERS},
    })
    bulk_insert(conn, models.MlForecast, records(stored))
    save_files("volatility", {"forecasts": forecasts, "metrics": result["metrics"],
                              "comparison": result["comparison"], "dm": result["dm"],
                              "folds": result["folds"]},
               {"params": result["params"], "data_snapshot_id": snapshot}, results_dir)
    return result


def run_clustering(conn, prices, metrics, companies, universe, company_id, snapshot, seed,
                   results_dir) -> dict:
    returns = aligned_returns(prices)
    market = returns.pop(universe.benchmark.ticker)
    sectors = companies[companies["entity_type"] == "company"].set_index("ticker")["sector_name"]
    result = clustering.run_experiment(returns, market, metrics, sectors, seed)
    results = {
        "summary": result["summary"], "stability": result["stability"],
        "selection": result["selection"], "mismatches": result["mismatches"],
        "crosstab": {m: t.reset_index() for m, t in result["crosstab"].items()},
        "linkage": result["linkage"], "linkage_labels": result["linkage_labels"],
        "features": result["features"],
    }
    run_id = start_run(conn, "clustering", seed, snapshot, result["params"], results)
    assignments = result["assignments"].copy()
    assignments["company_id"] = assignments["ticker"].map(company_id).astype(int)
    assignments["ml_run_id"] = run_id
    bulk_insert(conn, models.MlClusterAssignment, records(assignments.drop(columns="ticker")))
    save_files("clustering", {"assignments": result["assignments"],
                              "selection": result["selection"], "features": result["features"]},
               {"params": result["params"], "summary": result["summary"],
                "stability": result["stability"], "mismatches": result["mismatches"],
                "data_snapshot_id": snapshot}, results_dir)
    return result


def run_stats_tests(conn, prices, metrics, companies, universe, snapshot, seed,
                    results_dir) -> dict:
    returns = aligned_returns(prices)
    market = returns[universe.benchmark.ticker]
    company_returns = returns.drop(columns=universe.benchmark.ticker)
    info = companies[companies["entity_type"] == "company"][["ticker", "sector_name",
                                                             "peer_group"]]
    with_value = metrics[metrics["value"].notna()]
    latest = (with_value.sort_values("period_end_date")
              .groupby(["ticker", "metric_name", "period_type"]).tail(1)
              .merge(info, on="ticker"))

    rows = stats_tests.return_distribution_tests(returns)
    rows += stats_tests.sharpe_confidence_intervals(returns, universe.risk_free_rate.value or 0.0,
                                                    seed=seed)
    peer_values = latest[latest["period_type"] == latest["metric_name"].map(PEER_MEDIAN_METRICS)]
    rows += stats_tests.peer_median_intervals(peer_values[["peer_group", "metric_name", "value"]],
                                              seed=seed)
    rows += stats_tests.correlation_significance(company_returns)
    for variable, period_type in SECTOR_TEST_VARIABLES.items():
        values = latest[(latest["metric_name"] == variable)
                        & (latest["period_type"] == period_type)].rename(
            columns={"sector_name": "sector"})
        rows += stats_tests.sector_difference_tests(values[["sector", "value"]], variable)
    rows += stats_tests.stress_correlation_test(company_returns, market, seed=seed)
    rolling = stats_tests.rolling_correlation_summary(company_returns)

    core = {"test", "subject", "statistic", "p_value", "p_adjusted", "effect_size", "ci_low",
            "ci_high", "n"}
    frame = pd.DataFrame(rows)
    tests = pd.DataFrame(to_jsonable(rows))
    summary = summarize_stat_tests(frame)
    params = {"seed": seed, "alpha": stats_tests.ALPHA, "bootstrap_resamples": 2000,
              "sharpe_block_days": stats_tests.SHARPE_BLOCK,
              "risk_free_rate": universe.risk_free_rate.value,
              "multiple_comparison_correction": "Benjamini-Hochberg",
              "sector_variables": list(SECTOR_TEST_VARIABLES),
              "peer_median_metrics": list(PEER_MEDIAN_METRICS),
              "n_days": int(len(returns)), "n_companies": int(company_returns.shape[1])}
    run_id = start_run(conn, "stats_tests", seed, snapshot, params,
                       {"summary": summary, "rolling_correlation": rolling})
    stored = []
    for row in to_jsonable(rows):
        stored.append({"ml_run_id": run_id,
                       **{k: row.get(k) for k in core},
                       "details": {k: v for k, v in row.items() if k not in core}})
    bulk_insert(conn, models.MlStatTest, stored)
    save_files("stats_tests", {"tests": tests.astype({"subject": "string"}),
                               "rolling_correlation": rolling},
               {"params": params, "summary": summary, "data_snapshot_id": snapshot}, results_dir)
    return {"tests": frame, "summary": summary, "params": params, "rolling": rolling}


def summarize_stat_tests(frame: pd.DataFrame) -> dict:
    """Headline counts from the test rows, for the model card and the app."""
    def of(name):
        return frame[frame["test"] == name]

    normality, sharpe = of("jarque_bera"), of("sharpe_bootstrap_ci")
    correlation, stress = of("pearson_correlation"), of("stress_vs_calm_correlation").iloc[0]
    sectors = {}
    for _, row in of("kruskal_wallis").iterrows():
        posthoc = frame[(frame["test"] == "mann_whitney_posthoc")
                        & frame["subject"].str.startswith(f"{row['subject']}:")]
        sectors[row["subject"]] = {
            "h_statistic": row["statistic"], "p_value": row["p_value"],
            "epsilon_squared": row["effect_size"], "n": row["n"],
            "pairs_tested": int(len(posthoc)),
            "pairs_significant_after_bh": int(posthoc["significant"].fillna(False).sum()),
            "largest_pairwise_effect": float(posthoc["effect_size"].abs().max()),
        }
    return {
        "normality": {
            "series_tested": int(len(normality)),
            "rejected_at_5pct_after_bh": int((normality["p_adjusted"] < 0.05).sum()),
            "excess_kurtosis_min": float(normality["excess_kurtosis"].min()),
            "excess_kurtosis_median": float(normality["excess_kurtosis"].median()),
            "excess_kurtosis_max": float(normality["excess_kurtosis"].max()),
            "skewness_min": float(normality["skewness"].min()),
            "skewness_max": float(normality["skewness"].max()),
            "mean_share_beyond_3_sd": float(normality["share_beyond_3_sd"].mean()),
        },
        "sharpe": {
            "series": int(len(sharpe)),
            "intervals_excluding_zero": int(sharpe["excludes_zero"].sum()),
            "median_interval_width": float((sharpe["ci_high"] - sharpe["ci_low"]).median()),
        },
        "correlation": {
            "pairs": int(len(correlation)),
            "significant_unadjusted": int((correlation["p_value"] < 0.05).sum()),
            "significant_after_bh": int(correlation["significant"].sum()),
            "min": float(correlation["statistic"].min()),
            "median": float(correlation["statistic"].median()),
            "max": float(correlation["statistic"].max()),
        },
        "sector_differences": sectors,
        "stress_correlation": {
            "stressed": stress["mean_correlation_stressed"],
            "calm": stress["mean_correlation_calm"], "difference": stress["statistic"],
            "ci_low": stress["ci_low"], "ci_high": stress["ci_high"],
            "stressed_days": stress["stressed_days"], "calm_days": stress["calm_days"],
            "excludes_zero": stress["excludes_zero"],
        },
        "peer_median_intervals": int(len(of("peer_median_bootstrap_ci"))),
    }


def run_regimes(conn, prices, universe, snapshot, seed, results_dir) -> dict:
    returns = aligned_returns(prices)
    market = returns.pop(universe.benchmark.ticker)
    result = regimes.run_experiment(returns, market, seed)
    run_id = start_run(conn, "regimes", seed, snapshot, result["params"],
                       {"summary": result["summary"], "by_regime": result["by_regime"],
                        "by_threshold_regime": result["by_threshold_regime"]})
    daily = result["daily"].copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    daily["threshold_regime"] = daily["threshold_regime"].astype("Int64")
    daily["ml_run_id"] = run_id
    bulk_insert(conn, models.MlRegime, records(daily))
    save_files("regimes", {"daily": result["daily"], "by_regime": result["by_regime"],
                           "by_threshold_regime": result["by_threshold_regime"]},
               {"params": result["params"], "summary": result["summary"],
                "data_snapshot_id": snapshot}, results_dir)
    return result


def run_anomaly_comparison(conn, prices, universe, company_id, snapshot, seed,
                           results_dir) -> dict:
    settings = universe.anomalies
    result = anomaly_comparison.run_experiment(
        prices, settings.rolling_window, settings.rolling_min_periods,
        settings.market_iqr_multiplier, settings.modified_zscore_threshold, seed)
    run_id = start_run(conn, "anomaly_comparison", seed, snapshot, result["params"],
                       {"summary": result["summary"], "overlap": result["overlap"]})
    flags = result["flags"].copy()
    flags["company_id"] = flags["ticker"].map(company_id).astype(int)
    flags["ml_run_id"] = run_id
    flags["methods_agreeing"] = flags["methods_agreeing"].astype(int)
    columns = [c.name for c in models.MlAnomalyFlag.__table__.columns]
    bulk_insert(conn, models.MlAnomalyFlag, records(flags[columns]))
    save_files("anomaly_comparison", {"flags": result["flags"], "overlap": result["overlap"]},
               {"params": result["params"], "summary": result["summary"],
                "data_snapshot_id": snapshot}, results_dir)
    return result


def run_all(conn: Connection, universe: Universe, tasks=TASKS, seed: int = SEED,
            results_dir: Path = RESULTS_DIR, cards_dir: Path | None = None) -> dict:
    """Run the requested tasks, store results, and write the model cards."""
    prices = queries.load_prices_ohlc(conn)
    metrics = queries.load_metrics(conn)
    companies = queries.load_companies(conn)
    company_id = dict(zip(companies["ticker"], companies["company_id"]))
    snapshot = data_snapshot_id(prices)
    context = {"snapshot": snapshot, "seed": seed,
               "price_start": str(prices["date"].min()), "price_end": str(prices["date"].max()),
               "n_companies": int((companies["entity_type"] == "company").sum())}
    out = {}
    for task in tasks:
        start = time.perf_counter()
        if task == "volatility":
            out[task] = run_volatility(conn, prices, universe, company_id, snapshot, seed,
                                       results_dir)
        elif task == "clustering":
            out[task] = run_clustering(conn, prices, metrics, companies, universe, company_id,
                                       snapshot, seed, results_dir)
        elif task == "stats_tests":
            out[task] = run_stats_tests(conn, prices, metrics, companies, universe, snapshot,
                                        seed, results_dir)
        elif task == "regimes":
            out[task] = run_regimes(conn, prices, universe, snapshot, seed, results_dir)
        elif task == "anomaly_comparison":
            out[task] = run_anomaly_comparison(conn, prices, universe, company_id, snapshot,
                                               seed, results_dir)
        else:
            raise ValueError(f"unknown task: {task}")
        log.info("Data Science Lab: %s finished in %.1fs", task, time.perf_counter() - start)
    written = model_cards.write_all(out, context, cards_dir)
    for path in written:
        log.info("Model card written: %s", path)
    out["_context"] = context
    return out
