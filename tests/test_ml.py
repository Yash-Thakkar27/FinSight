"""Offline tests for the Data Science Lab. No network, no database; synthetic data, fixed seeds."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.ml import anomaly_comparison, clustering, evaluation, features, regimes, volatility
from src.ml import stats_tests as st
from src.ml.run import data_snapshot_id, to_jsonable
from src.ml.splits import walk_forward_folds

# ------------------------------------------------------------ synthetic data ----

def synthetic_prices(n_days=460, tickers=("AAA", "BBB", "CCC"), seed=0, start="2022-01-03"):
    """Prices with volatility clustering for a few tickers and a benchmark '^IDX'."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=n_days)
    frames = []
    market_shock = rng.standard_normal(n_days)
    for i, ticker in enumerate(("^IDX",) + tuple(tickers)):
        variance = np.empty(n_days)
        returns = np.empty(n_days)
        variance[0] = 1e-4
        shocks = 0.6 * market_shock + 0.8 * rng.standard_normal(n_days)
        for t in range(n_days):
            if t:
                variance[t] = 2e-6 + 0.08 * returns[t - 1] ** 2 + 0.90 * variance[t - 1]
            returns[t] = np.sqrt(variance[t]) * shocks[t]
        close = 100 * (1 + i) * np.cumprod(1 + returns)
        spread = np.abs(rng.normal(0.01, 0.003, n_days)) + 0.002
        frames.append(pd.DataFrame({
            "ticker": ticker, "date": days.date, "adj_close": close,
            "high": close * (1 + spread), "low": close * (1 - spread),
            "volume": rng.integers(50_000, 150_000, n_days), "is_stale_quote": False}))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def small_experiment(monkeypatch):
    """Shrink the walk-forward settings so the full experiment runs in a few seconds."""
    monkeypatch.setattr(volatility, "INITIAL_TRAIN", 280)
    monkeypatch.setattr(volatility, "TEST_SIZE", 50)
    monkeypatch.setattr(volatility, "GBM_PARAMS", {"max_depth": 2, "learning_rate": 0.1,
                                                   "max_iter": 30, "min_samples_leaf": 20})


# ------------------------------------------------------------------- splits ----

def test_walk_forward_test_dates_never_precede_train_dates():
    dates = list(pd.bdate_range("2024-01-01", periods=120).date)
    folds = walk_forward_folds(dates, initial_train=60, test_size=20, horizon=5)
    assert [f.number for f in folds] == [1, 2, 3]
    for fold in folds:
        assert max(fold.train_dates) < fold.cutoff < min(fold.test_dates)
        assert not set(fold.train_dates) & set(fold.test_dates)
    # the window expands and the test blocks tile the dates after the initial window exactly
    assert [len(f.train_dates) for f in folds] == [55, 75, 95]
    tested = [d for f in folds for d in f.test_dates]
    assert tested == dates[60:]


def test_training_rows_are_purged_so_no_target_overlaps_the_test_block():
    dates = list(range(100))            # positions stand in for dates
    horizon = 5
    fold = walk_forward_folds(dates, initial_train=60, test_size=20, horizon=horizon)[0]
    # the last training row is 54: its target covers 55..59, all at or before the cutoff (59)
    assert fold.train_dates[-1] == 54 and fold.cutoff == 59 and fold.test_dates[0] == 60
    assert fold.train_dates[-1] + horizon <= fold.cutoff
    # without the purge, row 59's target would cover 60..64, inside the test block
    assert 59 not in fold.train_dates


def test_walk_forward_handles_a_short_last_block_and_bad_input():
    folds = walk_forward_folds(list(range(75)), initial_train=60, test_size=20, horizon=5)
    assert len(folds) == 1 and len(folds[0].test_dates) == 15
    with pytest.raises(ValueError, match="sorted and unique"):
        walk_forward_folds([3, 1, 2], 2, 1, 1)
    with pytest.raises(ValueError, match="exceed the horizon"):
        walk_forward_folds(list(range(50)), initial_train=5, test_size=10, horizon=5)


# ----------------------------------------------------------------- features ----

def test_realized_and_forward_variance_alignment():
    returns = pd.Series([0.01, -0.02, 0.03, 0.01, -0.01])
    # trailing 2-day variance at index 2: (0.02^2 + 0.03^2) / 2 = 0.00065
    assert features.realized_variance(returns, 2).iloc[2] == pytest.approx(0.00065)
    assert pd.isna(features.realized_variance(returns, 2).iloc[0])
    # the target at index 0 covers indices 1 and 2: the same 0.00065, and never index 0 itself
    forward = features.forward_realized_variance(returns, 2)
    assert forward.iloc[0] == pytest.approx(0.00065)
    assert forward.iloc[2] == pytest.approx((0.01 ** 2 + 0.01 ** 2) / 2)
    assert forward.iloc[-2:].isna().all()          # the future is not known for the last h rows


def test_ewma_baseline_against_a_hand_computed_value():
    returns = pd.Series([0.01, -0.02, 0.03])
    ewma = features.ewma_variance(returns, lam=0.94)
    # s0 = 0.01^2 = 0.0001
    # s1 = 0.94 * 0.0001   + 0.06 * 0.0004 = 0.000118
    # s2 = 0.94 * 0.000118 + 0.06 * 0.0009 = 0.00016492
    assert ewma.tolist() == pytest.approx([0.0001, 0.000118, 0.00016492])
    # a leading missing return (the first day of a price series) is skipped, not treated as 0
    with_gap = features.ewma_variance(pd.Series([np.nan, 0.01, -0.02]))
    assert pd.isna(with_gap.iloc[0]) and with_gap.iloc[2] == pytest.approx(0.000118)


def test_parkinson_variance():
    high, low = pd.Series([110.0, 105.0]), pd.Series([100.0, 100.0])
    # mean(ln(1.10)^2, ln(1.05)^2) / (4 ln 2) = (0.0090840 + 0.0023805) / 2 / 2.772589
    expected = (np.log(1.10) ** 2 + np.log(1.05) ** 2) / 2 / (4 * np.log(2))
    assert features.parkinson_variance(high, low, 2).iloc[1] == pytest.approx(expected)
    assert expected == pytest.approx(0.0020675, abs=1e-6)


def test_features_at_t_do_not_change_when_later_data_is_altered():
    prices = synthetic_prices(200)
    one = prices[prices["ticker"] == "AAA"].assign(date=lambda d: pd.to_datetime(d["date"]))
    market = (prices[prices["ticker"] == "^IDX"].assign(date=lambda d: pd.to_datetime(d["date"]))
              .set_index("date")["adj_close"].pct_change())
    original = features.build_features(one.set_index("date"), market)

    cut = 150
    altered = one.copy()
    later = altered.index[cut + 1:]
    altered.loc[later, ["adj_close", "high", "low"]] *= 3.7        # a different future
    altered.loc[later, "volume"] = 1
    altered_market = market.copy()
    altered_market.iloc[cut + 1:] = 0.5
    changed = features.build_features(altered.set_index("date"), altered_market)

    # every feature on or before the cut is identical
    pd.testing.assert_frame_equal(original.iloc[:cut + 1], changed.iloc[:cut + 1])
    # and the alteration is real: features after the cut do differ
    assert not original.iloc[cut + 5:][features.FEATURE_COLUMNS].equals(
        changed.iloc[cut + 5:][features.FEATURE_COLUMNS])
    # the full feature set exists once 63 days of history are available
    assert original[features.FEATURE_COLUMNS].iloc[70:].notna().all().all()


# --------------------------------------------------------------- evaluation ----

def test_forecast_metrics():
    realized = np.array([0.0001, 0.0004])           # daily variances
    forecast = np.array([0.0004, 0.0004])
    scores = evaluation.forecast_metrics(realized, forecast)
    # annualized vol: realized sqrt(0.0252) = 0.15875 and sqrt(0.1008) = 0.31749;
    # the forecast is 0.31749 for both
    # errors: 0.15875 and 0 -> RMSE = 0.15875 / sqrt(2) = 0.11225, MAE = 0.07937
    assert scores["rmse"] == pytest.approx(0.11225, abs=1e-5)
    assert scores["mae"] == pytest.approx(0.07937, abs=1e-5)
    # QLIKE: ratio 0.25 -> 0.25 - ln(0.25) - 1 = 0.63629; ratio 1 -> 0. Mean 0.31815
    assert scores["qlike"] == pytest.approx(0.31815, abs=1e-5)
    assert evaluation.qlike(0.0003, 0.0003) == pytest.approx(0.0)
    # under-predicting variance by half costs more than over-predicting by double
    assert evaluation.qlike(0.0004, 0.0002) > evaluation.qlike(0.0002, 0.0004)


def test_diebold_mariano():
    rng = np.random.default_rng(1)
    benchmark = 1.0 + rng.normal(0, 0.1, 400)
    better = benchmark - 0.05 + rng.normal(0, 0.05, 400)
    result = evaluation.diebold_mariano(better, benchmark, horizon=5)
    assert result["statistic"] < -5 and result["p_value"] < 0.001
    assert result["mean_difference"] == pytest.approx(-0.05, abs=0.01)
    # reversing the roles flips the sign and keeps the p-value
    reverse = evaluation.diebold_mariano(benchmark, better, horizon=5)
    assert reverse["statistic"] == pytest.approx(-result["statistic"])
    assert reverse["p_value"] == pytest.approx(result["p_value"])
    # no real difference: not significant
    same = evaluation.diebold_mariano(benchmark + rng.normal(0, 0.05, 400), benchmark, horizon=5)
    assert same["p_value"] > 0.05
    assert evaluation.diebold_mariano([1.0] * 5, [1.0] * 5)["statistic"] is None    # too short
    assert evaluation.diebold_mariano(benchmark, benchmark)["statistic"] is None    # identical


def test_newey_west_variance_matches_plain_variance_without_lags():
    series = np.array([1.0, 2.0, 3.0, 4.0])
    assert evaluation.newey_west_variance(series, 0) == pytest.approx(1.25)   # population variance
    # positive autocorrelation raises the long-run variance
    assert evaluation.newey_west_variance(series, 1) > 1.25


def test_benjamini_hochberg():
    # sorted: 0.005, 0.01, 0.03, 0.04 -> x n/rank: 0.02, 0.02, 0.04, 0.04 (already monotone)
    adjusted = evaluation.benjamini_hochberg([0.01, 0.04, 0.03, 0.005])
    assert adjusted.tolist() == pytest.approx([0.02, 0.04, 0.04, 0.02])
    # monotonicity is enforced: 0.01, 0.011 -> 0.02, 0.011 -> both 0.011
    assert evaluation.benjamini_hochberg([0.01, 0.011]).tolist() == pytest.approx([0.011, 0.011])
    assert evaluation.benjamini_hochberg([0.9, 0.8]).max() <= 1.0


def test_bootstrap_interval_contains_the_point_estimate():
    rng = np.random.default_rng(3)
    sample = rng.normal(0.5, 1.0, 300)
    for block in (1, 21):
        result = evaluation.bootstrap_ci(sample, np.mean, n_resamples=500, block=block, seed=7)
        assert result["ci_low"] < result["estimate"] < result["ci_high"]
        assert result["estimate"] == pytest.approx(sample.mean())
    # same seed, same interval
    again = evaluation.bootstrap_ci(sample, np.mean, n_resamples=500, block=21, seed=7)
    assert (again["ci_low"], again["ci_high"]) == (result["ci_low"], result["ci_high"])
    sharpes = st.sharpe_confidence_intervals(pd.DataFrame({"A": rng.normal(0.001, 0.01, 500)}),
                                             0.05, n_resamples=300)
    assert sharpes[0]["ci_low"] < sharpes[0]["statistic"] < sharpes[0]["ci_high"]


def test_block_bootstrap_draws_contiguous_blocks():
    index = evaluation.block_bootstrap_indices(100, 10, np.random.default_rng(0))
    assert len(index) == 100 and index.min() >= 0 and index.max() <= 99
    blocks = index.reshape(10, 10)
    assert (np.diff(blocks, axis=1) == 1).all()


# -------------------------------------------------- volatility experiment ----

def test_experiment_structure_and_common_sample(small_experiment):
    result = volatility.run_experiment(synthetic_prices(), "^IDX")
    forecasts, comparison = result["forecasts"], result["comparison"]
    assert set(forecasts["horizon"]) == {5, 21}
    assert forecasts[list(volatility.ALL_FORECASTERS) + ["target"]].notna().all().all()
    assert (forecasts[list(volatility.ALL_FORECASTERS)] > 0).all().all()
    # every forecaster is scored on the same rows
    pooled = result["metrics"][result["metrics"]["scope"] == "pooled"]
    assert pooled.groupby("horizon")["n"].nunique().eq(1).all()
    # the comparison table has every forecaster for every metric and horizon, with fold variation
    assert len(comparison) == 2 * 3 * 5
    assert set(comparison["kind"]) == {"baseline", "model"}
    assert (comparison["folds"] >= 2).all() and comparison["fold_std"].notna().all()
    best = comparison[comparison["model"] == comparison["best_baseline"]]
    assert (best["change_vs_best_baseline_pct"] == 0).all()
    # fold boundaries: each test block starts after its cutoff
    folds = result["folds"]
    assert (folds["test_start"] > folds["cutoff"]).all()
    assert (folds["train_end"] < folds["cutoff"]).all()
    assert set(result["dm"]["benchmark"]) <= set(volatility.BASELINES)


def test_no_forecast_changes_when_data_after_the_forecast_date_is_altered(small_experiment):
    """End-to-end leakage test for every forecaster, including the fitted models."""
    prices = synthetic_prices()
    original = volatility.run_experiment(prices, "^IDX")["forecasts"]

    dates = sorted(prices["date"].unique())
    cut = dates[400]
    altered = prices.copy()
    later = altered["date"] > cut
    rng = np.random.default_rng(99)
    shock = np.cumprod(1 + rng.normal(0, 0.05, int(later.sum())))
    altered.loc[later, "adj_close"] = altered.loc[later, "adj_close"] * shock
    altered.loc[later, "high"] = altered.loc[later, "adj_close"] * 1.08
    altered.loc[later, "low"] = altered.loc[later, "adj_close"] * 0.92
    altered.loc[later, "volume"] = 7
    changed = volatility.run_experiment(altered, "^IDX")["forecasts"]

    key = ["ticker", "date", "horizon"]
    before = original[original["date"] <= pd.Timestamp(cut)].set_index(key).sort_index()
    after = changed[changed["date"] <= pd.Timestamp(cut)].set_index(key).sort_index()
    assert len(before) > 300
    for name in volatility.ALL_FORECASTERS:
        pd.testing.assert_series_equal(before[name], after[name], check_exact=False, rtol=1e-9,
                                       obj=name)
    # the alteration is real: later forecasts and the targets that reach past the cut do change
    later_before = original[original["date"] > pd.Timestamp(cut)].set_index(key)["ewma"]
    later_after = changed[changed["date"] > pd.Timestamp(cut)].set_index(key)["ewma"]
    assert not later_before.equals(later_after)


def test_experiment_is_reproducible(small_experiment):
    prices = synthetic_prices()
    first = volatility.run_experiment(prices, "^IDX", seed=42)
    second = volatility.run_experiment(prices, "^IDX", seed=42)
    pd.testing.assert_frame_equal(first["comparison"], second["comparison"])
    pd.testing.assert_frame_equal(first["forecasts"], second["forecasts"])


# --------------------------------------------------------------- clustering ----

def planted_returns(seed=5, n_days=600):
    """Two groups of four stocks, each driven by its own factor."""
    rng = np.random.default_rng(seed)
    factors = rng.normal(0, 0.012, (n_days, 2))
    market = pd.Series(factors.mean(axis=1) + rng.normal(0, 0.002, n_days))
    columns = {}
    for group in (0, 1):
        for i in range(4):
            columns[f"G{group}_{i}"] = (factors[:, group] * (1.0 + 0.5 * group)
                                        + rng.normal(0, 0.005, n_days))
    return pd.DataFrame(columns), market


def cluster_metrics(tickers):
    rows = []
    for ticker in tickers:
        group = int(ticker[1])
        for name, value in (("net_margin", 0.10 + 0.15 * group), ("roe", 0.12 + 0.10 * group),
                            ("revenue_growth", 0.05 + 0.10 * group)):
            rows.append({"ticker": ticker, "metric_name": name, "period_type": "annual",
                         "period_end_date": date(2026, 3, 31), "value": value})
    return pd.DataFrame(rows)


def test_clustering_recovers_planted_groups_and_is_reproducible():
    returns, market = planted_returns()
    sectors = pd.Series({t: f"sector{t[1]}" for t in returns.columns})
    metrics = cluster_metrics(returns.columns)
    first = clustering.run_experiment(returns, market, metrics, sectors, seed=42)
    assert first["summary"]["kmeans"]["k"] == 2 and first["summary"]["hierarchical"]["k"] == 2
    assert first["summary"]["kmeans"]["ari_vs_sector"] == pytest.approx(1.0)
    assert first["summary"]["hierarchical"]["ari_vs_sector"] == pytest.approx(1.0)
    assert first["stability"]["hierarchical"]["bootstrap_mean_ari"] > 0.95
    assert all("contains only" in line for line in first["mismatches"]["hierarchical"])
    # same seed, same clusters and same projection
    second = clustering.run_experiment(returns, market, metrics, sectors, seed=42)
    pd.testing.assert_frame_equal(first["assignments"], second["assignments"])
    assert first["stability"] == second["stability"]


def test_missing_fundamentals_are_imputed_and_recorded():
    returns, _ = planted_returns()
    metrics = cluster_metrics(returns.columns)
    metrics = metrics[~((metrics["ticker"] == "G0_0") & (metrics["metric_name"] == "roe"))]
    table, imputed = clustering.fundamental_features(metrics, list(returns.columns))
    assert imputed == [("G0_0", "roe")]
    # the median of the other seven: 3 x 0.12 and 4 x 0.22 -> 0.22
    assert table.at["G0_0", "roe"] == pytest.approx(0.22)
    assert table.notna().all().all()


def test_correlation_distance():
    base = np.random.default_rng(0).normal(0, 0.01, 300)
    frame = pd.DataFrame({"A": base, "B": base * 2, "C": -base})
    distance = clustering.correlation_distance(frame)
    assert distance.at["A", "B"] == pytest.approx(0.0, abs=1e-12)     # move together
    assert distance.at["A", "C"] == pytest.approx(2.0)                # exact opposites
    assert (np.diag(distance) == 0).all()


# ---------------------------------------------------------- statistical tests ----

def test_normality_test_separates_normal_from_fat_tailed_returns():
    rng = np.random.default_rng(11)
    frame = pd.DataFrame({"normal": rng.normal(0, 0.01, 2000),
                          "fat": rng.standard_t(3, 2000) * 0.01})
    rows = {r["subject"]: r for r in st.return_distribution_tests(frame)}
    assert rows["normal"]["p_adjusted"] > 0.05 and abs(rows["normal"]["excess_kurtosis"]) < 0.5
    assert rows["fat"]["p_adjusted"] < 0.001 and rows["fat"]["excess_kurtosis"] > 2
    assert rows["fat"]["share_beyond_3_sd"] > rows["normal"]["share_beyond_3_sd"]


def test_correlation_significance_is_corrected_for_multiple_comparisons():
    rng = np.random.default_rng(2)
    frame = pd.DataFrame(rng.normal(0, 1, (120, 12)), columns=[f"S{i}" for i in range(12)])
    frame["S1"] = frame["S0"] * 0.8 + rng.normal(0, 0.6, 120)       # one real relationship
    rows = st.correlation_significance(frame)
    assert len(rows) == 66                                           # 12 choose 2
    table = pd.DataFrame(rows)
    assert (table["p_adjusted"] >= table["p_value"] - 1e-12).all()
    assert table.loc[table["subject"] == "S0 | S1", "significant"].item()
    # 65 unrelated pairs: correction leaves at most as many significant as before it
    unrelated = table[table["subject"] != "S0 | S1"]
    assert unrelated["significant"].sum() <= (unrelated["p_value"] < 0.05).sum()
    assert unrelated["significant"].sum() <= 1


def test_sector_difference_tests_report_effect_sizes():
    values = pd.DataFrame({
        "sector": ["A"] * 5 + ["B"] * 5 + ["C"] * 5,
        "value": [1, 2, 3, 4, 5, 11, 12, 13, 14, 15, 2, 3, 4, 5, 6],
    })
    rows = st.sector_difference_tests(values, "margin")
    overall = rows[0]
    assert overall["test"] == "kruskal_wallis" and overall["p_value"] < 0.01
    assert overall["effect_size"] == pytest.approx(overall["statistic"] / 14)   # H / (n - 1)
    pairs = {r["subject"]: r for r in rows[1:]}
    assert len(pairs) == 3
    # A and B do not overlap at all: U = 0, rank-biserial = -1, exact p = 2/252 = 0.0079
    separated = pairs["margin: A vs B"]
    assert separated["effect_size"] == pytest.approx(-1.0)
    assert separated["p_value"] == pytest.approx(2 / 252, abs=1e-4)
    assert abs(pairs["margin: A vs C"]["effect_size"]) < 0.5        # mostly overlapping
    assert st.sector_difference_tests(values[values["sector"] == "A"], "margin") == []


def test_peer_median_interval_stays_within_the_group():
    values = pd.DataFrame({"peer_group": "g", "metric_name": "roe",
                           "value": [0.10, 0.12, 0.15, 0.20, 0.40]})
    row = st.peer_median_intervals(values, n_resamples=500)[0]
    assert row["statistic"] == 0.15 and row["n"] == 5
    assert 0.10 <= row["ci_low"] <= 0.15 <= row["ci_high"] <= 0.40


def test_stress_correlation_detects_higher_correlation_in_volatile_periods():
    rng = np.random.default_rng(4)
    n = 800
    turbulent = np.zeros(n, dtype=bool)
    turbulent[200:320] = turbulent[560:680] = True
    common = rng.normal(0, 1, n)
    weight = np.where(turbulent, 0.9, 0.2)                # stocks load on the common factor more
    scale = np.where(turbulent, 0.03, 0.01)
    market = pd.Series(common * scale)
    stocks = pd.DataFrame({f"S{i}": (weight * common + np.sqrt(1 - weight ** 2)
                                     * rng.normal(0, 1, n)) * scale for i in range(6)})
    result = st.stress_correlation_test(stocks, market, n_resamples=200)[0]
    assert result["mean_correlation_stressed"] > result["mean_correlation_calm"] + 0.3
    assert result["ci_low"] > 0 and result["excludes_zero"]
    assert result["stressed_days"] + result["calm_days"] == result["n"]


# ------------------------------------------------------ regimes and anomalies ----

def test_markov_model_finds_planted_volatility_regimes():
    rng = np.random.default_rng(8)
    truth = np.zeros(900, dtype=int)
    truth[300:420] = truth[650:760] = 1
    market = pd.Series(rng.normal(0, 1, 900) * np.where(truth == 1, 0.025, 0.007),
                       index=pd.bdate_range("2022-01-03", periods=900))
    fitted = regimes.fit_markov_regimes(market)
    assert fitted["volatility"]["turbulent"] > 2 * fitted["volatility"]["calm"]
    assert (fitted["regime"].to_numpy() == truth).mean() > 0.90
    stocks = pd.DataFrame({f"S{i}": market * 1.2 + rng.normal(0, 0.004, 900) for i in range(3)})
    result = regimes.run_experiment(stocks, market)
    by_regime = result["by_regime"].set_index("regime")
    assert (by_regime.at["turbulent", "market_volatility"]
            > by_regime.at["calm", "market_volatility"])
    assert result["summary"]["agreement_with_threshold_rule"] > 0.7
    assert set(result["daily"].columns) == {"date", "p_turbulent", "regime", "threshold_regime"}


def test_anomaly_methods_agree_on_an_obvious_spike():
    prices = synthetic_prices(400, tickers=("AAA", "BBB"))
    prices = prices[prices["ticker"] != "^IDX"].reset_index(drop=True)
    spike_day = sorted(prices["date"].unique())[300]
    after = (prices["ticker"] == "AAA") & (prices["date"] >= spike_day)
    prices.loc[after, "adj_close"] *= 1.30                 # a +30% jump on one day
    result = anomaly_comparison.run_experiment(prices)
    flagged = result["flags"]
    spike = flagged[(flagged["ticker"] == "AAA") & (flagged["date"] == spike_day)].iloc[0]
    assert spike["methods_agreeing"] == 3
    assert spike["message"].startswith("Potential data anomaly detected")
    summary = result["summary"]
    assert summary["flagged_by_all_three"] >= 1
    # the forest flags about the share it was told to (2%), by construction
    share = summary["flagged_by"]["isolation_forest"] / summary["observations"]
    assert share == pytest.approx(anomaly_comparison.CONTAMINATION, abs=0.01)
    assert (result["overlap"]["jaccard"].between(0, 1)).all()
    assert anomaly_comparison.jaccard(pd.Series([True, True, False]),
                                      pd.Series([True, False, False])) == 0.5


# ------------------------------------------------------------------ storage ----

def test_results_are_converted_to_plain_json():
    converted = to_jsonable({
        "a": np.float64(1.5), "b": np.int64(3), "c": float("nan"), "d": np.bool_(True),
        "e": pd.Timestamp("2026-03-31"), "f": pd.DataFrame({"x": [1, 2]}),
        "g": np.array([1.0, np.inf]), 5: (1, 2)})
    assert converted == {"a": 1.5, "b": 3, "c": None, "d": True, "e": "2026-03-31",
                         "f": [{"x": 1}, {"x": 2}], "g": [1.0, None], "5": [1, 2]}


def test_data_snapshot_id_changes_only_when_the_data_does():
    prices = synthetic_prices(50)
    assert data_snapshot_id(prices) == data_snapshot_id(prices.sample(frac=1, random_state=1))
    changed = prices.copy()
    changed.loc[0, "adj_close"] += 0.01
    assert data_snapshot_id(prices) != data_snapshot_id(changed)
    assert len(data_snapshot_id(prices)) == 12
