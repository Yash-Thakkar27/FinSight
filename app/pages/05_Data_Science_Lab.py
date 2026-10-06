"""Data Science Lab: stored results of the models and statistical tests. Nothing is trained here."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402
from components import PROJECT_ROOT, charts, data, ui  # noqa: E402

MODEL_LABELS = {"hist_21": "Trailing 21-day (baseline)", "ewma": "EWMA (baseline)",
                "garch": "GARCH(1,1)", "ridge": "Ridge regression", "gbm": "Gradient boosting"}
CARDS = {"Volatility forecasting": "volatility_forecasting.md",
         "Peer clustering": "peer_clustering.md", "Statistical tests": "statistical_tests.md",
         "Regime detection": "regime_detection.md",
         "Anomaly comparison": "anomaly_isolation_forest.md"}

version = ui.page("Data Science Lab",
                  "Stored results from `python scripts/run_ml.py`. The app displays them; it "
                  "does not train or re-run any model.")
volatility_run = data.ml_run(version, "volatility")
if volatility_run is None:
    st.error("No Data Science Lab results are stored yet. Run `python scripts/run_ml.py`.")
    ui.footer(version)
    st.stop()

ui.note(f"Run of {pd.Timestamp(volatility_run['created_at']).strftime('%Y-%m-%d %H:%M UTC')}, "
        f"seed {volatility_run['seed']}, data snapshot `{volatility_run['data_snapshot_id']}`. "
        "Volatility is forecast for risk measurement. Nothing here predicts prices or returns, "
        "and nothing is a trading signal.")
tab_volatility, tab_clusters, tab_tests, tab_other, tab_cards = st.tabs(
    ["Volatility forecasting", "Peer clustering", "Statistical tests", "Anomalies and regimes",
     "Model cards"])

# ------------------------------------------------------------------ volatility ----
with tab_volatility:
    options = ui.company_options(version)
    names = dict(zip(options["ticker"], options["company_name"]))
    controls = st.columns([2, 1, 2])
    ticker = controls[0].selectbox("Company", list(names), format_func=lambda t: names[t])
    horizon = controls[1].radio("Horizon (trading days)", volatility_run["params"]["horizons"],
                                horizontal=True)
    model = controls[2].selectbox("Model", ["ridge", "garch", "gbm"],
                                  format_func=lambda m: MODEL_LABELS[m])
    forecasts = data.ml_forecasts(version, ticker, int(horizon))
    if forecasts.empty:
        st.info("No stored forecasts for this company and horizon.")
    else:
        frame = forecasts.set_index(pd.to_datetime(forecasts["date"]))
        figure = go.Figure()
        figure.add_trace(go.Scatter(x=frame.index, y=frame["realized"], name="Realized",
                                    mode="lines", line=dict(color=charts.LIGHT_GREY, width=1.2)))
        figure.add_trace(go.Scatter(x=frame.index, y=frame["ewma"], name=MODEL_LABELS["ewma"],
                                    mode="lines", line=dict(color=charts.GREY, width=1.2,
                                                            dash="dot")))
        figure.add_trace(go.Scatter(x=frame.index, y=frame[model], name=MODEL_LABELS[model],
                                    mode="lines", line=dict(color=charts.ACCENT, width=2)))
        figure.update_layout(**charts.base_layout(
            f"{names[ticker]}: forecast vs realized volatility over the next {horizon} days"))
        figure.update_yaxes(tickformat=".0%", title_text="annualized volatility")
        st.plotly_chart(figure, width="stretch")
        ui.note(f"{len(frame):,} out-of-sample forecasts across {frame['fold'].nunique()} "
                "walk-forward folds. Each forecast was made with data available on its date; "
                "the realized line is what happened over the following days.")

    st.markdown("**Models against the baselines, all companies pooled**")
    comparison = pd.DataFrame(volatility_run["results"]["comparison"])
    rows = comparison[comparison["horizon"] == horizon]
    table = pd.DataFrame({
        "Metric": rows["metric"].str.upper(),
        "Forecaster": rows["model"].map(MODEL_LABELS),
        "Value": rows["value"].map(lambda v: f"{v:.4f}"),
        "Change vs best baseline": [
            "best baseline" if m == b else f"{c:+.1f}%"
            for m, b, c in zip(rows["model"], rows["best_baseline"],
                               rows["change_vs_best_baseline_pct"])],
        "Fold mean ± sd": [f"{m:.4f} ± {s:.4f}"
                           for m, s in zip(rows["fold_mean"], rows["fold_std"])],
        "Folds better than best baseline": [
            "–" if m == b else f"{w} of {n}"
            for m, b, w, n in zip(rows["model"], rows["best_baseline"],
                                  rows["folds_beating_best_baseline"], rows["folds"])],
    })
    st.dataframe(table, hide_index=True, width="stretch", height=38 * (len(table) + 1))
    ui.note("Lower is better for all three metrics. RMSE and MAE are in annualized volatility; "
            "QLIKE is on variance. A negative change means lower loss than the best baseline. "
            "\"Fold mean ± sd\" is the variation across walk-forward folds.")

    fold_metrics = data.ml_metrics(version, "fold")
    qlike = fold_metrics[(fold_metrics["horizon"] == horizon) & (fold_metrics["metric"] == "qlike")]
    by_fold = qlike.pivot(index="fold", columns="model", values="value")[
        ["ewma", "garch", "ridge", "gbm"]].rename(columns=MODEL_LABELS)
    by_fold.index = [f"Fold {int(i)}" for i in by_fold.index]
    st.plotly_chart(charts.line_chart(by_fold, "QLIKE by walk-forward fold",
                                      highlight=MODEL_LABELS["ridge"]), width="stretch")

    st.markdown("**Is the difference statistically significant?**")
    dm = pd.DataFrame(volatility_run["results"]["dm"])
    dm = dm[dm["horizon"] == horizon]
    st.dataframe(pd.DataFrame({
        "Forecaster": dm["model"].map(MODEL_LABELS), "Against": dm["benchmark"].map(MODEL_LABELS),
        "Diebold–Mariano statistic": dm["dm_statistic"].map(lambda v: f"{v:+.2f}"),
        "p-value": dm["p_value"].map(lambda v: f"{v:.4f}"),
        "Companies significantly better": [
            f"{b} of {t}" for b, t in zip(dm["tickers_significantly_better"],
                                          dm["tickers_tested"])],
        "Significantly worse": dm["tickers_significantly_worse"],
    }), hide_index=True, width="stretch")
    ui.note("Diebold–Mariano test on QLIKE loss; a negative statistic means lower loss than the "
            "benchmark. The pooled result can be significant while the difference for an "
            "individual company is not: see the last two columns.")

# ------------------------------------------------------------------- clusters ----
with tab_clusters:
    cluster_run = data.ml_run(version, "clustering")
    if cluster_run is None:
        st.info("No clustering results stored.")
    else:
        results, clusters = cluster_run["results"], data.ml_clusters(version)
        method = st.radio("Method", ["hierarchical", "kmeans"], horizontal=True,
                          format_func=lambda m: {"hierarchical": "Hierarchical, on return "
                                                 "correlations", "kmeans": "K-means, on company "
                                                 "features"}[m])
        summary, stability = results["summary"][method], results["stability"][method]
        cards = st.columns(4)
        cards[0].metric("Clusters (k)", summary["k"])
        cards[1].metric("Silhouette", f"{summary['silhouette']:.3f}")
        cards[2].metric("Agreement with sectors (ARI)", f"{summary['ari_vs_sector']:.2f}")
        cards[3].metric("Bootstrap stability (ARI)", f"{stability['bootstrap_mean_ari']:.2f}")
        cards[3].caption(f"5th–95th percentile {stability['bootstrap_p05_ari']:.2f} to "
                         f"{stability['bootstrap_p95_ari']:.2f}")
        ui.note("Adjusted Rand index (ARI): 1 means the clusters match the sector labels "
                "exactly, about 0 means no better than chance.")
        plot = clusters.assign(label=clusters["ticker"].map(ui.short),
                               cluster=clusters[f"{method}_cluster"])
        explained = cluster_run["params"]["pca_explained_variance"]
        left, right = st.columns([3, 2])
        left.plotly_chart(charts.scatter(
            plot, "pc1", "pc2", "cluster", "sector", "label",
            "Companies on the first two principal components of their features",
            f"PC1 ({explained[0]:.0%} of variance)", f"PC2 ({explained[1]:.0%})"),
            width="stretch")
        left.caption("Colour is the cluster; marker shape is the sector. The projection is of "
                     "the K-means features and is for viewing only.")
        crosstab = pd.DataFrame(results["crosstab"][method]).set_index("sector")
        crosstab.columns = [f"Cluster {c}" for c in crosstab.columns]
        right.markdown("**Clusters by sector**")
        right.dataframe(crosstab, width="stretch")
        right.markdown("**Where they agree and differ**")
        for line in results["mismatches"][method]:
            right.markdown(f"- {line}")

# ---------------------------------------------------------------------- tests ----
with tab_tests:
    tests_run = data.ml_run(version, "stats_tests")
    if tests_run is None:
        st.info("No statistical test results stored.")
    else:
        summary = tests_run["results"]["summary"]
        normal, sharpe_summary, stress = (summary["normality"], summary["sharpe"],
                                          summary["stress_correlation"])
        st.markdown("**Sharpe ratios with 95% bootstrap intervals**")
        sharpe = data.ml_stat_tests(version, "sharpe_bootstrap_ci").sort_values("statistic")
        figure = go.Figure(go.Scatter(
            x=sharpe["statistic"], y=sharpe["subject"].map(ui.short), mode="markers",
            marker=dict(color=charts.ACCENT, size=8),
            error_x=dict(type="data", symmetric=False, color=charts.GREY, thickness=1,
                         array=sharpe["ci_high"] - sharpe["statistic"],
                         arrayminus=sharpe["statistic"] - sharpe["ci_low"])))
        figure.add_vline(x=0, line_color=charts.LIGHT_GREY)
        figure.update_layout(**charts.base_layout("", 620, hovermode="closest"))
        figure.update_xaxes(title_text="Sharpe ratio (full history)")
        figure.update_yaxes(tickfont=dict(size=9))
        st.plotly_chart(figure, width="stretch")
        ui.note(f"The interval excludes zero for {sharpe_summary['intervals_excluding_zero']} of "
                f"{sharpe_summary['series']} series; the median interval is "
                f"{sharpe_summary['median_interval_width']:.2f} wide. Moving-block bootstrap, "
                "21-day blocks. Differences between these Sharpe ratios are not established.")

        left, right = st.columns(2)
        left.markdown("**Are daily returns normal?**")
        left.write(
            f"Normality is rejected for {normal['rejected_at_5pct_after_bh']} of "
            f"{normal['series_tested']} series (Jarque–Bera, Benjamini–Hochberg corrected). "
            f"Excess kurtosis ranges from {normal['excess_kurtosis_min']:.2f} to "
            f"{normal['excess_kurtosis_max']:.2f}. On average "
            f"{normal['mean_share_beyond_3_sd']:.2%} of days are more than three standard "
            "deviations from the mean, against 0.27% under normality.")
        right.markdown("**Do correlations rise when markets are stressed?**")
        right.write(
            f"Average pairwise correlation is {stress['stressed']:.2f} on the "
            f"{int(stress['stressed_days'])} highest-volatility days and {stress['calm']:.2f} on "
            f"the other {int(stress['calm_days'])}: a difference of {stress['difference']:+.2f} "
            f"(95% interval {stress['ci_low']:+.2f} to {stress['ci_high']:+.2f}).")

        st.markdown("**Do sectors differ?** (Kruskal–Wallis)")
        sectors = pd.DataFrame([
            {"Variable": name.replace("_", " "), "H statistic": f"{v['h_statistic']:.2f}",
             "p-value": f"{v['p_value']:.4f}", "Effect size (ε²)": f"{v['epsilon_squared']:.2f}",
             "Companies": v["n"],
             "Pairs significant after correction":
                 f"{v['pairs_significant_after_bh']} of {v['pairs_tested']}"}
            for name, v in summary["sector_differences"].items()])
        st.dataframe(sectors, hide_index=True, width="stretch")
        ui.note("ε² is the share of rank variance explained by sector. With five companies per "
                "sector few pairwise differences can reach significance after correction, "
                "however large; effect sizes carry the information.")

        correlation = summary["correlation"]
        st.markdown("**Which correlations differ from zero?**")
        st.write(f"{correlation['significant_after_bh']} of {correlation['pairs']} company pairs "
                 f"after Benjamini–Hochberg correction ({correlation['significant_unadjusted']} "
                 f"before). Correlations range from {correlation['min']:.2f} to "
                 f"{correlation['max']:.2f}. With over a thousand observations even a small "
                 "correlation is significant, so its size matters more than its p-value.")

        with st.expander("Bootstrap intervals for peer medians"):
            medians = data.ml_stat_tests(version, "peer_median_bootstrap_ci")
            st.dataframe(pd.DataFrame({
                "Peer group | metric": medians["subject"],
                "Median": medians["statistic"].round(3), "95% low": medians["ci_low"].round(3),
                "95% high": medians["ci_high"].round(3), "Companies": medians["n"],
            }), hide_index=True, width="stretch")
            st.caption("Ratios and margins are fractions (0.15 = 15%); multiples are in turns. "
                       "With five companies an interval mostly spans the group's range.")

# ---------------------------------------------------------- anomalies, regimes ----
with tab_other:
    anomaly_run, regime_run = data.ml_run(version, "anomaly_comparison"), data.ml_run(
        version, "regimes")
    if anomaly_run is not None:
        st.markdown("**Anomaly detection: three methods on the same data**")
        counts = anomaly_run["results"]["summary"]
        observations = counts["observations"]
        st.dataframe(pd.DataFrame([
            {"Method": {"iqr": "IQR fences", "modified_zscore": "Modified z-score",
                        "isolation_forest": "Isolation Forest"}[name],
             "Company-days flagged": f"{n:,}", "Share of observations": f"{n / observations:.2%}"}
            for name, n in counts["flagged_by"].items()]), hide_index=True, width="stretch")
        ui.note(f"Of {observations:,} company-days, {counts['flagged_by_all_three']:,} are "
                f"flagged by all three methods, {counts['flagged_by_exactly_two']:,} by two and "
                f"{counts['flagged_by_exactly_one']:,} by one. Each flag reads \"Potential data "
                "anomaly detected\": a reason to look at a data point, not a statement about "
                "a company. The Isolation Forest's count is set by its 2% contamination "
                "parameter.")
    if regime_run is not None:
        st.markdown("**Market regimes (descriptive)**")
        regimes = data.ml_regimes(version)
        regimes["date"] = pd.to_datetime(regimes["date"])
        st.plotly_chart(charts.line_chart(
            regimes.set_index("date")[["p_turbulent"]].rename(
                columns={"p_turbulent": "Probability of the turbulent regime"}),
            "Nifty 50: smoothed probability of the high-volatility regime", y_format=".0%",
            height=260), width="stretch")
        by_regime = pd.DataFrame(regime_run["results"]["by_regime"])
        st.dataframe(pd.DataFrame({
            "Regime": by_regime["regime"], "Days": by_regime["days"],
            "Share of days": by_regime["share_of_days"].map(lambda v: f"{v:.1%}"),
            "Nifty 50 volatility": by_regime["market_volatility"].map(lambda v: f"{v:.1%}"),
            "Mean stock volatility": by_regime["mean_stock_volatility"].map(lambda v: f"{v:.1%}"),
            "Mean pairwise correlation": by_regime["mean_pairwise_correlation"].map(
                lambda v: f"{v:.2f}"),
        }), hide_index=True, width="stretch")
        st.warning("Descriptive segmentation, not a trading signal. The probabilities are "
                   "smoothed over the whole sample, so a day's label depends on what happened "
                   "afterwards and could not have been known at the time.")

# ---------------------------------------------------------------- model cards ----
with tab_cards:
    st.write("Each card states the purpose, data, features, validation scheme, results against "
             "the baseline, limitations, and what the model must not be used for. The cards are "
             "written by the run from its own results.")
    for title, filename in CARDS.items():
        path = PROJECT_ROOT / "docs" / "model_cards" / filename
        with st.expander(f"{title}  ·  docs/model_cards/{filename}"):
            if path.exists():
                st.markdown(path.read_text(encoding="utf-8"))
            else:
                st.info("Card not found. Run `python scripts/run_ml.py` to generate it.")

ui.footer(version)
