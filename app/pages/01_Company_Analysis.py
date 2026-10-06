"""Company Analysis: profile, key metrics and trends for one company."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from components import charts, data, ui  # noqa: E402
from components.formatting import fiscal_label, format_crore  # noqa: E402
from components.metrics import latest_metric, metric_card  # noqa: E402

from src.analytics.registry import is_applicable, not_applicable_reason  # noqa: E402

version = ui.page("Company Analysis")
ticker = ui.company_selector(version)
frequency = st.sidebar.radio("Statements", ["Annual", "Quarterly"], horizontal=True)
period_type = frequency.lower()

profile = data.company_profile(version, ticker)
metrics = data.company_metrics(version, ticker)
statements = data.company_statements(version, ticker, period_type)
wide = (statements.pivot_table(index="period_end_date", columns="line_item", values="value",
                               aggfunc="first").sort_index())
all_periods = list(wide.index)
labels = {p: fiscal_label(p, period_type) for p in all_periods}

if len(all_periods) > 1:
    first, last = st.sidebar.select_slider(
        "Period range", options=all_periods, value=(all_periods[0], all_periods[-1]),
        format_func=lambda p: labels[p])
    periods = [p for p in all_periods if first <= p <= last]
else:
    periods = all_periods
wide = wide.loc[periods]
currency = statements["original_currency"].iloc[0] if len(statements) else "INR"
translated = currency != "INR"

st.subheader(f"{profile['company_name']} ({ui.short(ticker)})")
info = st.columns(4)
info[0].markdown(f"**Sector**  \n{profile['sector_name']}")
info[1].markdown(f"**Industry**  \n{profile['industry_name']}")
info[2].markdown(f"**Peer group**  \n{profile['peer_group']} ({profile['sector_type']})")
shares = profile["shares_outstanding"]
info[3].markdown("**Shares outstanding**  \n"
                 + (f"{shares / 1e7:,.1f} Cr (as of {profile['shares_as_of']})"
                    if pd.notna(shares) else "N/A"))
ui.note(f"{len(all_periods)} {period_type} period(s) available from the source"
        + (f"; showing {len(periods)}." if len(periods) != len(all_periods) else ".")
        + (f" Statements are reported in {currency}: amounts in ₹ crore are translated from "
           f"{currency}, and ratios and growth are computed in {currency}." if translated else ""))

st.subheader("Key metrics")
annual_revenue = data.company_statements(version, ticker, "annual")
revenue_rows = annual_revenue[(annual_revenue["line_item"] == "revenue")
                              & annual_revenue["value"].notna()]
first_row, second_row = st.columns(4), st.columns(4)
if revenue_rows.empty:
    first_row[0].metric("Revenue", "N/A")
else:
    latest_revenue = revenue_rows.sort_values("period_end_date").iloc[-1]
    first_row[0].metric(f"Revenue ({fiscal_label(latest_revenue['period_end_date'], 'annual')})",
                        format_crore(latest_revenue["value"]))
    if translated:
        first_row[0].caption(f"translated from {currency}")
metric_card(first_row[1], metrics, "revenue_growth", "annual")
metric_card(first_row[2], metrics, "ebitda_margin", "annual")
metric_card(first_row[3], metrics, "net_margin", "annual")
metric_card(second_row[0], metrics, "roe", "annual")
metric_card(second_row[1], metrics, "debt_to_equity", "annual")
metric_card(second_row[2], metrics, "pe_ratio", "ttm")
metric_card(second_row[3], metrics, "ev_ebitda", "ttm")
price_row = latest_metric(metrics, "market_cap", "ttm")
if price_row is not None:
    ui.note(f"Margins, growth, ROE and debt/equity are for the latest fiscal year. P/E and "
            f"EV/EBITDA use the price on {price_row['as_of_date']}.")

st.subheader("Trends")
if wide.empty:
    st.info("No statement data for this selection.")
else:
    x = [labels[p] for p in wide.index]
    unit_note = "₹ crore" + (f", translated from {currency}" if translated else "")

    def series(item: str) -> pd.Series:
        return wide[item] / 1e7 if item in wide.columns else pd.Series(float("nan"),
                                                                       index=wide.index)

    def level_chart(column, item: str, title: str, metric_for_rule: str | None = None) -> None:
        values = series(item)
        if metric_for_rule and not is_applicable(metric_for_rule, profile["sector_type"]):
            column.markdown(f"**{title}**")
            column.info(not_applicable_reason(profile["sector_type"]))
        elif values.notna().sum() == 0:
            column.markdown(f"**{title}**")
            column.info("N/A (not available from the source for these periods)")
        else:
            column.plotly_chart(charts.bar_chart(x, values.round(0), f"{title} ({unit_note})"),
                                width="stretch")

    top = st.columns(3)
    level_chart(top[0], "revenue", "Revenue")
    level_chart(top[1], "ebitda", "EBITDA", "ebitda_margin")
    level_chart(top[2], "net_income", "Net income")

    bottom = st.columns(3)
    margin_rows = metrics[(metrics["period_type"] == period_type)
                          & metrics["metric_name"].isin(["gross_margin", "ebitda_margin",
                                                         "net_margin"])
                          & metrics["period_end_date"].isin(periods)]
    margins = margin_rows.pivot_table(index="period_end_date", columns="metric_name",
                                      values="value", aggfunc="first").dropna(axis=1, how="all")
    if margins.empty:
        bottom[0].markdown("**Margins**")
        bottom[0].info("N/A (no margin could be computed for these periods)")
    else:
        margins.index = [labels[p] for p in margins.index]
        margins.columns = [c.replace("_", " ").replace("ebitda", "EBITDA") for c in margins.columns]
        bottom[0].plotly_chart(charts.line_chart(margins, "Margins", y_format=".0%"),
                               width="stretch")
    level_chart(bottom[1], "total_debt", "Total debt", "debt_to_equity")
    level_chart(bottom[2], "free_cash_flow", "Free cash flow", "fcf_margin")

    with st.expander("Figures behind the charts"):
        table = pd.DataFrame({
            "Period": x,
            **{name: [format_crore(v) for v in wide[item]] if item in wide.columns
               else ["N/A"] * len(wide)
               for name, item in (("Revenue", "revenue"), ("EBITDA", "ebitda"),
                                  ("Net income", "net_income"), ("Total debt", "total_debt"),
                                  ("Free cash flow", "free_cash_flow"))},
        })
        st.dataframe(table, hide_index=True, width="stretch")
        st.caption("N/A means the source did not provide the figure or it does not apply to "
                   "this sector type. A missing value is never shown as zero.")

ui.footer(version)
