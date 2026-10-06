"""Risk Analytics: volatility, drawdown, Sharpe, downside deviation and correlation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from components import charts, data, ui  # noqa: E402
from components.formatting import format_metric  # noqa: E402

from config.settings import get_universe  # noqa: E402

EXPLANATIONS = {
    "volatility": ("Annualized volatility",
                   "How much daily returns vary, scaled to a year. Higher means the price has "
                   "moved around more. It treats gains and losses alike, and it understates how "
                   "often very large moves occur because returns have fat tails."),
    "max_drawdown": ("Maximum drawdown",
                     "The largest fall from a previous high to a later low within the window, "
                     "with the dates of that peak and trough."),
    "sharpe": ("Sharpe ratio",
               "Return earned above the risk-free rate per unit of volatility. With a few years "
               "of data it is imprecise: the Data Science Lab shows that none of these Sharpe "
               "ratios is statistically distinguishable from zero."),
    "downside_deviation": ("Downside deviation",
                           "Like volatility, but only falls count. It is the root mean square "
                           "of daily returns below 0%, taken over all days (days with a gain "
                           "count as zero), annualized. This is the Sortino form."),
}

version = ui.page("Risk Analytics")
universe = get_universe()
rate = universe.risk_free_rate
window = st.sidebar.radio("Window", ["1y", "3y"], format_func=lambda w: f"Trailing {w[0]} year"
                          + ("s" if w != "1y" else ""), horizontal=True)
correlation_window = st.sidebar.selectbox("Correlation window", ["1y", "3y", "full"], index=1)

metrics = data.all_metrics(version)
companies = data.companies(version)
names = dict(zip(companies["ticker"], companies["company_name"]))
point = metrics[metrics["period_type"] == "point_in_time"]


def column(name: str) -> tuple[pd.Series, pd.DataFrame]:
    """Values of one risk metric for the chosen window, and the full rows behind them."""
    rows = point[point["metric_name"] == f"{name}_{window}"].set_index("ticker")
    return rows["value"], rows


volatility, _ = column("volatility")
drawdown, drawdown_rows = column("max_drawdown")
sharpe, _ = column("sharpe")
downside, _ = column("downside_deviation")
order = [t for t in companies["ticker"] if t in volatility.index]
table = pd.DataFrame({
    "Company": [names[t] for t in order],
    "Volatility": [format_metric(volatility.get(t), "pct") for t in order],
    "Downside deviation": [format_metric(downside.get(t), "pct") for t in order],
    "Sharpe": [format_metric(sharpe.get(t), "ratio") for t in order],
    "Max drawdown": [format_metric(drawdown.get(t), "pct") for t in order],
    "Peak": [(drawdown_rows.at[t, "input_fields"] or {}).get("peak_date", "N/A")
             if t in drawdown_rows.index else "N/A" for t in order],
    "Trough": [(drawdown_rows.at[t, "input_fields"] or {}).get("trough_date", "N/A")
               if t in drawdown_rows.index else "N/A" for t in order],
})
as_of = point["as_of_date"].max()
st.subheader(f"Risk metrics, trailing {window[0]} year{'s' if window != '1y' else ''}")
st.dataframe(table, hide_index=True, width="stretch", height=38 * (len(table) + 1))
rate_text = (f"{rate.value:.4%} ({rate.instrument}, as of {rate.as_of_date})"
             if rate.value is not None else "not configured, so Sharpe is N/A")
ui.note(f"As of each ticker's latest price (latest {as_of}). Annualized with "
        f"{universe.trading_days_per_year} trading days. Risk-free rate: {rate_text}. "
        "N/A means the price history is shorter than the window.")

st.subheader("Correlation of daily returns")
pairs = data.correlation_matrix(version, correlation_window)
if pairs.empty:
    st.info("The price history does not cover this window.")
else:
    sector = companies.set_index("ticker")["sector_name"].fillna("Benchmark")
    tickers = sorted(pairs["ticker_a"].unique(), key=lambda t: (sector[t] == "Benchmark",
                                                                sector[t], t))
    matrix = pairs.pivot(index="ticker_a", columns="ticker_b", values="correlation")
    matrix = matrix.loc[tickers, tickers]
    matrix.index = matrix.columns = [ui.short(t) for t in tickers]
    st.plotly_chart(charts.heatmap(matrix, "Companies ordered by sector"), width="stretch")
    ui.note(f"{int(pairs['n_observations'].iloc[0]):,} trading days from "
            f"{pairs['start_date'].iloc[0]} to {pairs['end_date'].iloc[0]}, common to every "
            "ticker so all pairs share one sample.")

st.subheader("What these measures mean")
formulas = data.formulas(version).set_index("metric_name")
for key, (title, text) in EXPLANATIONS.items():
    with st.expander(title):
        st.write(text)
        name = f"{key}_{window}"
        if name in formulas.index:
            st.code(formulas.at[name, "expression"], language=None)
            if formulas.at[name, "description"]:
                st.caption(formulas.at[name, "description"])
with st.expander("Correlation"):
    st.write("How closely two stocks' daily returns move together, from -1 (opposite) to +1 "
             "(in step). It measures same-day, linear co-movement only, and it tends to rise "
             "when markets are volatile.")
    st.code("Pearson correlation of daily adjusted-close returns on shared dates", language=None)

ui.footer(version)
