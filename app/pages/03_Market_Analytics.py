"""Market Analytics: returns, volatility, drawdown and volume over a chosen period."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from components import charts, data, ui  # noqa: E402

from config.settings import get_universe  # noqa: E402
from src.analytics import returns as returns_module  # noqa: E402

TRADING_DAYS = 252
ROLLING_WINDOW = 21

version = ui.page("Market Analytics")
universe = get_universe()
benchmark = universe.benchmark.ticker
options = ui.company_options(version)
names = dict(zip(options["ticker"], options["company_name"]))
selected = st.sidebar.multiselect("Companies", list(names), default=list(names)[:3],
                                  format_func=lambda t: f"{names[t]} ({ui.short(t)})")
latest_date = data.latest_price_date(version)
default_start = (pd.Timestamp(latest_date) - pd.DateOffset(years=1)).date()
picked = st.sidebar.date_input("Date range", value=(default_start, latest_date),
                               max_value=latest_date)
show_benchmark = st.sidebar.checkbox(f"Show benchmark ({universe.benchmark.name})", value=True)

if not isinstance(picked, (tuple, list)) or len(picked) != 2:
    st.info("Pick both a start and an end date.")     # mid-selection: only one date so far
    ui.footer(version)
    st.stop()
start, end = picked

if not selected:
    st.warning("Select at least one company.")
    ui.footer(version)
    st.stop()

tickers = tuple(selected) + ((benchmark,) if show_benchmark else ())
prices = data.price_history(version, tickers, start, end)
traded = prices[~prices["is_stale_quote"]]
if traded.empty:
    st.info("No prices in this date range.")
    ui.footer(version)
    st.stop()

display = {t: (universe.benchmark.name if t == benchmark else ui.short(t)) for t in tickers}
adjusted = traded.pivot(index="date", columns="ticker", values="adj_close").rename(columns=display)
adjusted.index = pd.to_datetime(adjusted.index)
adjusted = adjusted[[display[t] for t in tickers if display[t] in adjusted.columns]]
daily = adjusted.apply(lambda column: returns_module.daily_returns(column.dropna()))
benchmark_name = universe.benchmark.name if show_benchmark else None

ui.note(f"{len(adjusted)} trading days from {adjusted.index[0].date()} to "
        f"{adjusted.index[-1].date()}. Returns use adjusted close (dividends, splits and bonus "
        "issues included). Placeholder rows on market holidays are excluded.")

cumulative = (1 + daily.fillna(0)).cumprod() - 1
st.plotly_chart(charts.line_chart(cumulative, "Cumulative return", y_format=".0%",
                                  highlight=benchmark_name), width="stretch")

left, right = st.columns(2)
left.plotly_chart(charts.histogram({c: daily[c].dropna() for c in daily.columns},
                                   "Distribution of daily returns", "daily return", ".1%"),
                  width="stretch")
rolling = daily.rolling(ROLLING_WINDOW).std(ddof=1) * np.sqrt(TRADING_DAYS)
right.plotly_chart(charts.line_chart(rolling.dropna(how="all"),
                                     f"Rolling {ROLLING_WINDOW}-day volatility (annualized)",
                                     y_format=".0%", highlight=benchmark_name), width="stretch")

left, right = st.columns(2)
wealth = 1 + cumulative
drawdown = wealth / wealth.cummax() - 1
left.plotly_chart(charts.line_chart(drawdown, "Drawdown from the running peak", y_format=".0%",
                                    highlight=benchmark_name), width="stretch")
volume = (traded[traded["ticker"].isin(selected)]
          .pivot(index="date", columns="ticker", values="volume").rename(columns=display))
volume.index = pd.to_datetime(volume.index)
right.plotly_chart(charts.line_chart(volume / 1e5, "Daily volume (lakh shares)"), width="stretch")

st.subheader("Summary for the selected period")
summary = pd.DataFrame({
    "Period return": cumulative.iloc[-1].map(lambda v: f"{v:.1%}"),
    "Annualized volatility": (daily.std(ddof=1) * np.sqrt(TRADING_DAYS)).map(lambda v: f"{v:.1%}"),
    "Worst day": daily.min().map(lambda v: f"{v:.1%}"),
    "Best day": daily.max().map(lambda v: f"{v:.1%}"),
    "Max drawdown": drawdown.min().map(lambda v: f"{v:.1%}"),
    "Trading days": daily.count(),
})
st.dataframe(summary, width="stretch")
st.caption("Computed for the dates selected above, so these differ from the stored 1-year "
           "and 3-year metrics on the Risk Analytics page.")

ui.footer(version)
