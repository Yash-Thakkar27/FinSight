"""The Streamlit app: every page loads without error, reads only from PostgreSQL, and follows
the display rules.

Pages are run headlessly with Streamlit's AppTest against the project database, so these
tests are skipped when PostgreSQL is unreachable or holds no data. While a page runs, any
network connection to a host other than the local database raises, which is how "no API
calls" is checked rather than assumed.
"""

import re
import socket
from pathlib import Path

import pytest
from sqlalchemy import text
from streamlit.testing.v1 import AppTest

from config.settings import get_universe
from src.database.connection import get_engine

pytestmark = pytest.mark.integration

APP_DIR = Path(__file__).resolve().parents[1] / "app"
PAGES = {
    "Overview": APP_DIR / "Home.py",
    "Company Analysis": APP_DIR / "pages" / "01_Company_Analysis.py",
    "Comparable Companies": APP_DIR / "pages" / "02_Comparable_Companies.py",
    "Market Analytics": APP_DIR / "pages" / "03_Market_Analytics.py",
    "Risk Analytics": APP_DIR / "pages" / "04_Risk_Analytics.py",
    "Data Science Lab": APP_DIR / "pages" / "05_Data_Science_Lab.py",
    "Data Quality": APP_DIR / "pages" / "06_Data_Quality.py",
}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
# The judgement words the spec forbids (Section 11), matched as whole words. Disclaimers such as
# "not a recommendation" are allowed; generated comps sentences have a stricter test of their own.
BANNED = re.compile(r"\b(undervalued|overvalued|good investment|strong company|buy|sell|"
                    r"cheap|expensive|attractive)\b", re.IGNORECASE)


@pytest.fixture(scope="module", autouse=True)
def require_data():
    try:
        with get_engine().connect() as conn:
            loaded = conn.execute(text("SELECT count(*) FROM core.metrics")).scalar()
            lab = conn.execute(text("SELECT count(*) FROM core.ml_runs")).scalar()
    except Exception as exc:
        pytest.skip(f"project database not reachable: {type(exc).__name__}")
    if not loaded or not lab:
        pytest.skip("project database has no metrics or Data Science Lab results")


@pytest.fixture
def no_external_network(monkeypatch):
    """Fail any connection that is not to the local database."""
    attempts = []
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOCAL_HOSTS:
            attempts.append(address)
            raise AssertionError(f"the app tried to reach an external host: {address}")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    return attempts


def run_page(path: Path) -> AppTest:
    app = AppTest.from_file(str(path), default_timeout=90)
    app.run()
    return app


def page_text(app: AppTest) -> str:
    parts = [e.value for group in (app.markdown, app.caption, app.title, app.subheader,
                                   app.info, app.warning, app.error) for e in group]
    return "\n".join(str(p) for p in parts)


@pytest.mark.parametrize("name", list(PAGES))
def test_page_loads_without_errors_or_external_calls(name, no_external_network):
    app = run_page(PAGES[name])
    assert not app.exception, f"{name}: {[e.value for e in app.exception]}"
    assert not app.error, f"{name}: {[e.value for e in app.error]}"
    assert no_external_network == []
    text_on_page = page_text(app)
    # the footer required on every page, with sources and an as-of date filled in
    footer = re.search(r"FinSight is an analytics tool, not investment advice\. "
                       r"Data: (.+), as of (\d{4}-\d{2}-\d{2})\.", text_on_page)
    assert footer, f"{name}: footer missing"
    assert "yfinance" in footer.group(1)
    assert not BANNED.findall(text_on_page), f"{name}: {BANNED.findall(text_on_page)}"
    assert not re.findall(r"\bnan\b", text_on_page.lower()), f"{name}: a raw NaN is displayed"


def test_app_code_never_imports_a_network_client():
    """The app reads from PostgreSQL only: no data-source or HTTP library is imported."""
    forbidden = re.compile(r"^\s*(import|from)\s+(yfinance|requests|httpx|urllib|aiohttp)\b",
                           re.MULTILINE)
    for path in APP_DIR.rglob("*.py"):
        assert not forbidden.search(path.read_text()), path
    # and no model is trained or refitted from the app
    training = re.compile(r"\b(run_experiment|run_all|\.fit\(|fit_predict)\b")
    for path in APP_DIR.rglob("*.py"):
        assert not training.search(path.read_text()), path


def test_overview_shows_universe_and_quality_figures(no_external_network):
    app = run_page(PAGES["Overview"])
    metrics = {m.label: m.value for m in app.metric}
    universe = get_universe()
    assert metrics["Companies"] == str(len(universe.companies))
    assert metrics["Sectors"] == str(len({c.sector for c in universe.companies}))
    assert re.fullmatch(r"-?\d+\.\d%", metrics["Median 1Y return"])
    assert re.fullmatch(r"\d+\.\dx", metrics["Median P/E"])
    assert re.fullmatch(r"\d+\.\d\d%", metrics["Pass rate"])
    assert len(app.dataframe) >= 4                      # three rankings and the universe table


def test_bank_shows_na_with_reason_never_a_number(no_external_network):
    app = AppTest.from_file(str(PAGES["Company Analysis"]), default_timeout=90)
    app.run()
    app.sidebar.selectbox[0].select("HDFCBANK.NS").run()
    assert not app.exception
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["EBITDA margin"] == "N/A"
    assert metrics["Debt / equity"] == "N/A"
    assert metrics["EV / EBITDA"] == "N/A"
    assert metrics["Net margin"] != "N/A" and metrics["P/E"] != "N/A"
    captions = "\n".join(c.value for c in app.caption)
    assert "not meaningful for banks" in captions
    assert "not meaningful for banks" in "\n".join(i.value for i in app.info)   # EBITDA chart


def test_usd_reporter_is_flagged_as_translated(no_external_network):
    app = AppTest.from_file(str(PAGES["Company Analysis"]), default_timeout=90)
    app.run()
    app.sidebar.selectbox[0].select("INFY.NS").run()
    assert not app.exception
    captions = "\n".join(c.value for c in app.caption)
    assert "translated from USD" in captions
    assert "reporting-currency growth (USD)" in captions
    assert "translated from USD" in page_text(app)
    # quarterly statements also load
    app.sidebar.radio[0].set_value("Quarterly").run()
    assert not app.exception and not app.error


def test_comps_page_recomputes_for_a_custom_peer_set(no_external_network):
    app = AppTest.from_file(str(PAGES["Comparable Companies"]), default_timeout=90)
    app.run()
    assert not app.exception
    default_peers = app.sidebar.multiselect[0].value
    target = app.sidebar.selectbox[0].value
    assert len(default_peers) == 4 and target not in default_peers        # target excluded
    assert len(app.dataframe) >= 3                                        # the three tables
    sentences = [m.value for m in app.markdown if m.value.startswith("- ")]
    assert any("peer median" in s and "(n=4)" in s for s in sentences)

    # two peers: a rank replaces the percentile and the caveat about few peers is absent (n=2)
    app.sidebar.multiselect[0].set_value(default_peers[:2]).run()
    assert not app.exception
    sentences = [m.value for m in app.markdown if m.value.startswith("- ")]
    assert any("(n=2)" in s for s in sentences)

    # a bank among IT peers triggers the mixed-sector warning
    app.sidebar.multiselect[0].set_value(default_peers[:2] + ["HDFCBANK.NS"]).run()
    assert not app.exception
    assert any("mixes sector types" in w.value for w in app.warning)

    # no peers: a prompt, not a crash
    app.sidebar.multiselect[0].set_value([]).run()
    assert not app.exception
    assert any("at least one peer" in w.value for w in app.warning)


def test_lab_page_shows_stored_results_for_each_horizon(no_external_network):
    app = AppTest.from_file(str(PAGES["Data Science Lab"]), default_timeout=90)
    app.run()
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Volatility forecasting", "Peer clustering",
                                           "Statistical tests", "Anomalies and regimes",
                                           "Model cards"]
    text_on_page = page_text(app)
    assert "does not train or re-run any model" in text_on_page
    assert "not a trading signal" in text_on_page
    horizon = next(r for r in app.radio if r.label.startswith("Horizon"))
    horizon.set_value(21).run()
    assert not app.exception
    # every model card is rendered from the file on disk
    assert text_on_page.count("# Model card:") == 5
