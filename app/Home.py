"""FinSight: Overview page. Launch with `streamlit run app/Home.py` from the project root."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from components import data, ui  # noqa: E402
from components.formatting import format_metric, format_pct  # noqa: E402

version = ui.page("Overview")
companies = ui.company_options(version)
metrics = data.all_metrics(version)
names = dict(zip(companies["ticker"], companies["company_name"]))
company_metrics = metrics[metrics["ticker"].isin(companies["ticker"])]


def latest(name: str, period_type: str) -> pd.DataFrame:
    """Each company's most recent row for a metric."""
    rows = company_metrics[(company_metrics["metric_name"] == name)
                           & (company_metrics["period_type"] == period_type)]
    return rows.sort_values("period_end_date").groupby("ticker").tail(1)


one_year, pe = latest("return_1y", "point_in_time"), latest("pe_ratio", "ttm")
cards = st.columns(5)
cards[0].metric("Companies", len(companies))
cards[1].metric("Sectors", companies["sector_name"].nunique())
cards[2].metric("Latest price date", str(data.latest_price_date(version)))
cards[3].metric("Median 1Y return", format_pct(one_year["value"].median(), 1))
cards[3].caption(f"n = {int(one_year['value'].notna().sum())}")
cards[4].metric("Median P/E", format_metric(pe["value"].median(), "multiple"))
cards[4].caption(f"n = {int(pe['value'].notna().sum())}; N/A excluded")

st.subheader("Highest in the universe")
ui.note("Ranked on stored metrics. A ranking describes the data; it is not a recommendation.")


def top_table(name: str, period_type: str, title: str, n: int = 5) -> pd.DataFrame:
    rows = latest(name, period_type).dropna(subset=["value"]).nlargest(n, "value")
    note = [f" ({c})" if c != "INR" else "" for c in rows["reporting_currency"]]
    return pd.DataFrame({
        "Company": [names[t] for t in rows["ticker"]],
        title: [format_metric(v, "pct") + suffix for v, suffix in zip(rows["value"], note)],
    })


columns = st.columns(3)
for column, (name, period_type, title) in zip(columns, (
        ("return_1y", "point_in_time", "1Y return"),
        ("revenue_growth", "annual", "Revenue growth"),
        ("roe", "annual", "ROE"))):
    column.markdown(f"**{title}**")
    column.dataframe(top_table(name, period_type, title), hide_index=True, width="stretch")
ui.note("Revenue growth is for each company's latest fiscal year, in its reporting currency; "
        "a currency in brackets marks reporting-currency growth that is not directly comparable "
        "with INR growth.")

st.subheader("Universe")
universe = (companies.groupby("sector_name")
            .agg(Companies=("ticker", "size"),
                 Members=("company_name", lambda s: ", ".join(s)))
            .rename_axis("Sector").reset_index())
st.dataframe(universe, hide_index=True, width="stretch")

st.subheader("Data quality")
run = data.latest_run(version)
if run is None or pd.isna(run["total_records"]):
    st.info("No completed pipeline run with a quality summary yet.")
else:
    quality = st.columns(5)
    quality[0].metric("Records validated", f"{int(run['total_records']):,}")
    quality[1].metric("Pass rate", f"{run['pass_rate_pct']:.2f}%")
    quality[2].metric("Invalid records", f"{int(run['invalid_records']):,}")
    quality[3].metric("Warnings", f"{int(run['warning_count']):,}")
    quality[4].metric("Statement fields missing", f"{run['missing_pct']:.1f}%")
    ui.note(f"Pipeline run {int(run['run_id'])}, finished "
            f"{pd.Timestamp(run['finished_at']).strftime('%Y-%m-%d %H:%M UTC')}, status "
            f"'{run['status']}'. Details are on the Data Quality page.")

ui.footer(version)
