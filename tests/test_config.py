"""Offline tests for configuration files. No network, no database."""

import pytest
import yaml
from pydantic import ValidationError

from config.settings import (
    CONFIG_DIR,
    DatabaseSettings,
    RiskFreeRate,
    Universe,
    get_universe,
)

SECTOR_TYPES = {"non_financial", "bank", "nbfc", "insurance"}


def _universe_dict(companies):
    return {
        "benchmark": {"ticker": "^NSEI", "name": "Nifty 50"},
        "risk_free_rate": {"value": None},
        "companies": companies,
    }


def _company(ticker="AAA.NS", sector_type="non_financial"):
    return {
        "ticker": ticker, "name": "A", "sector": "S", "industry": "I",
        "peer_group": "g", "sector_type": sector_type,
    }


def test_universe_yaml_loads_and_is_well_formed():
    universe = get_universe()
    assert len(universe.companies) == len(set(universe.tickers))
    assert all(c.ticker.endswith(".NS") for c in universe.companies)
    assert {c.sector_type for c in universe.companies} <= SECTOR_TYPES
    assert universe.trading_days_per_year == 252


def test_every_peer_group_has_one_sector_type_and_at_least_two_members():
    # Comps exclude the target, so a peer group of 1 would have no peers at all.
    groups = {}
    for c in get_universe().companies:
        groups.setdefault(c.peer_group, []).append(c.sector_type)
    for group, types in groups.items():
        assert len(types) >= 2, group
        assert len(set(types)) == 1, group


def test_duplicate_tickers_are_rejected():
    with pytest.raises(ValidationError, match="duplicate tickers"):
        Universe.model_validate(_universe_dict([_company(), _company()]))


def test_unknown_sector_type_is_rejected():
    with pytest.raises(ValidationError):
        Universe.model_validate(_universe_dict([_company(sector_type="fintech")]))


def test_risk_free_rate_value_requires_provenance():
    assert RiskFreeRate(value=None).value is None
    with pytest.raises(ValidationError, match="requires instrument, source and as_of_date"):
        RiskFreeRate(value=0.065)
    rate = RiskFreeRate(value=0.065, instrument="x", source="y", as_of_date="2026-01-01")
    assert rate.value == 0.065


def test_field_map_statements_have_units_and_sources():
    field_map = yaml.safe_load((CONFIG_DIR / "field_map.yaml").read_text(encoding="utf-8"))
    for statement in ("income", "balance", "cashflow"):
        for canonical, spec in field_map[statement].items():
            assert spec["unit"] in {"INR", "INR_per_share", "shares"}, canonical
            assert spec["sources"], canonical
            assert set(spec.get("applies_to", [])) <= SECTOR_TYPES, canonical
    # capex is negative at source and stored as a positive outflow
    assert field_map["cashflow"]["capex"]["sign"] == -1


def database(**overrides) -> DatabaseSettings:
    values = {"postgres_user": "finsight", "postgres_password": "pw", "postgres_db": "finsight",
              "postgres_host": "localhost", "postgres_port": 5433, "postgres_sslmode": None}
    return DatabaseSettings(_env_file=None, **(values | overrides))


def test_database_url_for_local_and_hosted_databases():
    assert database().url == "postgresql+psycopg://finsight:pw@localhost:5433/finsight"
    hosted = database(postgres_host="ep-x.aws.neon.tech", postgres_port=5432,
                      postgres_sslmode="require")
    assert hosted.url == \
        "postgresql+psycopg://finsight:pw@ep-x.aws.neon.tech:5432/finsight?sslmode=require"
    # a generated password with URL-special characters is percent-encoded
    tricky = database(postgres_password="p@ss/w:rd#1")
    assert tricky.url == \
        "postgresql+psycopg://finsight:p%40ss%2Fw%3Ard%231@localhost:5433/finsight"


def test_app_requirements_are_a_pinned_subset_of_the_project_requirements():
    """The hosted app installs app/requirements.txt: same versions as the root file."""
    def pins(path):
        return {line.split("==")[0]: line.split("==")[1].split()[0]
                for line in path.read_text().splitlines()
                if "==" in line and not line.startswith("#")}

    root = CONFIG_DIR.parent
    app, project = pins(root / "app" / "requirements.txt"), pins(root / "requirements.txt")
    assert {"streamlit", "plotly", "pandas", "SQLAlchemy", "psycopg[binary]"} <= set(app)
    assert all(project.get(name) == version for name, version in app.items())
