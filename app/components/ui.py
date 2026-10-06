"""Page furniture: configuration, header, footer, and the small notes the rules require."""

import pandas as pd
import streamlit as st

from components import data

TITLE = "FinSight"
SUBTITLE = "Financial Markets & Comparable Companies Analytics Platform"
DISCLAIMER = "FinSight is an analytics tool, not investment advice."

STYLE = """
<style>
  .block-container {padding-top: 2.2rem; max-width: 1280px;}
  h1, h2, h3 {color: #1f2a37; font-weight: 600; letter-spacing: 0;}
  .finsight-subtitle {color: #6b7280; font-size: 0.95rem; margin-top: -0.6rem;}
  .finsight-footer {color: #6b7280; font-size: 0.8rem; border-top: 1px solid #e5e7eb;
                    margin-top: 2.5rem; padding-top: 0.7rem;}
  .finsight-note {color: #6b7280; font-size: 0.85rem;}
  [data-testid="stMetricValue"] {font-size: 1.35rem; color: #1f4e79;}
  [data-testid="stMetricLabel"] {color: #4b5563;}
</style>
"""


def page(name: str, caption: str = ""):
    """Configure the page and draw the header. Returns the data version for the loaders."""
    st.set_page_config(page_title=f"{name} | {TITLE}", layout="wide")
    st.markdown(STYLE, unsafe_allow_html=True)
    st.title(TITLE if name == "Overview" else name)
    st.markdown(f'<div class="finsight-subtitle">{SUBTITLE}</div>', unsafe_allow_html=True)
    if caption:
        st.caption(caption)
    version = data.data_version()
    if version[0] == 0:
        st.error("No data has been loaded yet. Run `python scripts/update_data.py` and reload.")
        st.stop()
    return version


def footer(version) -> None:
    """The footer required on every page."""
    sources = ", ".join(data.data_sources(version)["name"])
    as_of = data.latest_price_date(version)
    st.markdown(f'<div class="finsight-footer">{DISCLAIMER} Data: {sources}, as of {as_of}.'
                "</div>", unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f'<div class="finsight-note">{text}</div>', unsafe_allow_html=True)


def company_options(version) -> pd.DataFrame:
    frame = data.companies(version)
    return frame[frame["entity_type"] == "company"].reset_index(drop=True)


def company_selector(version, label: str = "Company", key: str = "company") -> str:
    frame = company_options(version)
    names = dict(zip(frame["ticker"], frame["company_name"]))
    return st.sidebar.selectbox(label, list(names), key=key,
                                format_func=lambda t: f"{names[t]} ({t.replace('.NS', '')})")


def short(ticker: str) -> str:
    return ticker.replace(".NS", "")
