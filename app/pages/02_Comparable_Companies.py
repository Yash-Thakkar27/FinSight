"""Comparable Companies: a target against its peers, with peer statistics and positioning."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402
from components import charts, data, tables, ui  # noqa: E402

from src.analytics import comps  # noqa: E402

version = ui.page("Comparable Companies")
companies = data.companies(version)
options = ui.company_options(version)
names = dict(zip(options["ticker"], options["company_name"]))
target = ui.company_selector(version, "Target company", key="target")
default_peers = comps.default_peers(companies, target)
candidates = [t for t in options["ticker"] if t != target]
peers = st.sidebar.multiselect("Peers", candidates, default=default_peers,
                               format_func=lambda t: f"{names[t]} ({ui.short(t)})",
                               key=f"peers_{target}")
ui.note("Default peers are the other companies in the target's peer group. The target is "
        "excluded from every peer statistic, and a peer for which a metric is N/A is left out "
        "(never counted as zero).")

if not peers:
    st.warning("Select at least one peer to compare against.")
    ui.footer(version)
    st.stop()

result = comps.compare(data.all_metrics(version), companies, target, peers)
for warning in result["warnings"]:
    st.warning(warning)

st.subheader(f"{names[target]} against {len(result['peers'])} peer(s)")
metrics = data.all_metrics(version)
titles = {"operating": "Operating metrics (latest fiscal year)",
          "capital_structure": "Capital structure (latest fiscal year)",
          "valuation": "Valuation (current multiples)"}
for category, title in titles.items():
    st.markdown(f"**{title}**")
    table = comps.comps_table(metrics, companies, target, result["peers"], category)
    st.dataframe(tables.format_comps_table(table), width="stretch")
ui.note("N/A: the metric does not apply to that company or the source lacks an input. "
        "A dash in a peer-statistic row: with fewer than four peers that have a value, only "
        "min, median and max are shown. Growth for a company reporting in another currency is "
        "reporting-currency growth, and its valuation is translated to INR.")

st.subheader("Positioning")
position = tables.position_rows(result)
if position.empty:
    st.info("No metric has both a target value and a range of peer values to position against.")
else:
    st.plotly_chart(charts.position_chart(position, "Target within the range of its peers"),
                    width="stretch")
rows = [{"Metric": comps.LABELS.get(r["metric_name"], r["metric_name"]),
         "Position": r["position_label"] or "N/A", "Peers with a value": r["n_peers"]}
        for r in result["rows"]]
with st.expander("Position by metric"):
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("With four or more peers the position is a percentile (the share of peers "
               "below the target, ties counted as half). With fewer it is a rank among the "
               "target and its peers, highest first.")

st.subheader("Interpretation")
if result["interpretation"]:
    for sentence in result["interpretation"]:
        st.markdown(f"- {sentence}")
    st.caption("Generated from the stored figures by fixed templates. Descriptive only.")
else:
    st.info("No sentence could be generated: every metric lacks a target value or peer values.")

st.download_button(
    "Download comparison (CSV)", tables.comparison_export(result).to_csv(index=False),
    file_name=f"finsight_comps_{ui.short(target)}.csv", mime="text/csv")

ui.footer(version)
