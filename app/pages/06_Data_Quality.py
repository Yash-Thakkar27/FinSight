"""Data Quality: the latest pipeline run, its validation results, and completeness."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from components import charts, data, ui  # noqa: E402

version = ui.page("Data Quality")
run = data.latest_run(version)
if run is None or pd.isna(run["total_records"]):
    st.error("No completed pipeline run with a quality summary. Run "
             "`python scripts/update_data.py`.")
    ui.footer(version)
    st.stop()

run_id = int(run["run_id"])
finished = pd.Timestamp(run["finished_at"])
st.subheader("Latest run")
cards = st.columns(6)
cards[0].metric("Records", f"{int(run['total_records']):,}")
cards[1].metric("Valid", f"{int(run['valid_records']):,}")
cards[2].metric("Invalid", f"{int(run['invalid_records']):,}")
cards[3].metric("Warnings", f"{int(run['warning_count']):,}")
cards[4].metric("Pass rate", f"{run['pass_rate_pct']:.2f}%")
cards[5].metric("Duplicates removed", f"{int(run['duplicate_records']):,}")
ui.note(f"Last refresh: {finished.strftime('%Y-%m-%d %H:%M UTC')} (pipeline run {run_id}, "
        f"status '{run['status']}'). {run['missing_pct']:.1f}% of applicable statement fields "
        "have no value. A record is invalid when it fails an error-severity check; warnings "
        "mark records to review. Every figure is computed from this run's results.")
if run["notes"]:
    st.caption(f"Run notes: {run['notes']}")

st.subheader("Validation results")
show = st.radio("Show", ["Failures", "All results"], horizontal=True)
logs = data.quality_logs(version, run_id, "fail" if show == "Failures" else None)
filters = st.columns(4)
selections = {}
for column, (field, title) in zip(filters, (("ticker", "Company"), ("dataset", "Dataset"),
                                            ("check_name", "Check"), ("severity", "Severity"))):
    values = sorted(v for v in logs[field].dropna().unique())
    selections[field] = column.multiselect(title, values)
filtered = logs
for field, chosen in selections.items():
    if chosen:
        filtered = filtered[filtered[field].isin(chosen)]
st.dataframe(
    filtered.rename(columns={"ticker": "company", "check_name": "check",
                             "record_key": "record"})[
        ["company", "dataset", "check", "severity", "status", "message", "record"]],
    hide_index=True, width="stretch", height=380)
ui.note(f"{len(filtered):,} of {len(logs):,} rows. \"info\" rows are neutral observations, such "
        "as placeholder price rows on market holidays, which are flagged and kept.")

with st.expander("Results by check"):
    everything = data.quality_logs(version, run_id, None)
    by_check = (everything.groupby(["category", "check_name", "severity", "status"]).size()
                .unstack("status", fill_value=0).reset_index())
    for status in ("pass", "fail"):
        if status not in by_check.columns:
            by_check[status] = 0
    st.dataframe(by_check.rename(columns={"check_name": "check"}), hide_index=True,
                 width="stretch")

st.subheader("Completeness by company")
completeness = data.completeness(version)
table = completeness.pivot(index="ticker", columns="period_type", values="completeness_pct")
periods = completeness.pivot(index="ticker", columns="period_type", values="periods")
chart = table.rename(index=ui.short).rename(columns=str.capitalize).sort_values("Annual")
st.plotly_chart(charts.grouped_bars(chart, "Applicable statement fields with a value (%)",
                                    y_title="%"), width="stretch")
detail = pd.DataFrame({
    "Company": [ui.short(t) for t in table.index],
    "Annual (%)": table["annual"].round(1).to_numpy(),
    "Annual periods": periods["annual"].to_numpy(),
    "Quarterly (%)": table["quarterly"].round(1).to_numpy(),
    "Quarterly periods": periods["quarterly"].to_numpy(),
})
with st.expander("Completeness table"):
    st.dataframe(detail, hide_index=True, width="stretch")
ui.note("Fields that do not apply to a company's sector type (gross profit for a bank) are "
        "outside the denominator. A missing value is stored as NULL with a reason and is never "
        "replaced by zero.")

ui.footer(version)
