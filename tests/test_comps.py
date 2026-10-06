"""Offline tests for comparable-company analysis. Expected values are hand-calculated."""

from datetime import date

import pandas as pd
import pytest

from src import formatting
from src.analytics import comps

FY26 = date(2026, 3, 31)
AS_OF = date(2026, 10, 6)

COMPANIES = pd.DataFrame([
    {"ticker": "T", "company_name": "Target Ltd", "entity_type": "company",
     "sector_type": "non_financial", "peer_group": "it"},
    {"ticker": "A", "company_name": "Alpha", "entity_type": "company",
     "sector_type": "non_financial", "peer_group": "it"},
    {"ticker": "B", "company_name": "Beta", "entity_type": "company",
     "sector_type": "non_financial", "peer_group": "it"},
    {"ticker": "C", "company_name": "Gamma", "entity_type": "company",
     "sector_type": "non_financial", "peer_group": "it"},
    {"ticker": "D", "company_name": "Delta", "entity_type": "company",
     "sector_type": "non_financial", "peer_group": "it"},
    {"ticker": "BANK", "company_name": "A Bank", "entity_type": "company",
     "sector_type": "bank", "peer_group": "banks"},
    {"ticker": "^IDX", "company_name": "Index", "entity_type": "index",
     "sector_type": None, "peer_group": None},
])


def metric_rows(metric_name, values: dict, period_type="annual", **extra):
    period_end = AS_OF if period_type == "ttm" else FY26
    return [{"ticker": ticker, "metric_name": metric_name, "period_type": period_type,
             "period_end_date": period_end, "value": value,
             "na_reason": None if value is not None else "N/A (not meaningful for banks)",
             "reporting_currency": "INR", "is_translated": False, "method": None,
             **extra.get(ticker, {})}
            for ticker, value in values.items()]


def result_row(result, metric_name):
    return next(r for r in result["rows"] if r["metric_name"] == metric_name)


# --- statistics ----------------------------------------------------------------

def test_peer_statistics():
    stats = comps.peer_statistics(pd.Series([10.0, 20.0, 30.0, 40.0]))
    # percentiles by linear interpolation: p25 at position 0.75 -> 17.5; p75 at 2.25 -> 32.5
    assert stats == {"n": 4, "min": 10.0, "p25": 17.5, "median": 25.0, "mean": 25.0,
                     "p75": 32.5, "max": 40.0}


def test_missing_peer_values_are_excluded_not_counted_as_zero():
    stats = comps.peer_statistics(pd.Series([10.0, None, 30.0, float("nan"), 20.0, 60.0]))
    # four peers have a value: 10, 20, 30, 60 -> median 25, mean 30 (a zero would give 20)
    assert (stats["n"], stats["median"], stats["mean"], stats["min"]) == (4, 25.0, 30.0, 10.0)
    empty = comps.peer_statistics(pd.Series([None, None], dtype="float64"))
    assert empty["n"] == 0 and empty["median"] is None and empty["mean"] is None


def test_fewer_than_four_peers_reports_only_min_median_max():
    three = comps.peer_statistics(pd.Series([10.0, 20.0, 60.0]))
    assert (three["n"], three["min"], three["median"], three["max"]) == (3, 10.0, 20.0, 60.0)
    assert three["p25"] is None and three["p75"] is None and three["mean"] is None
    one = comps.peer_statistics(pd.Series([10.0]))
    assert (one["n"], one["min"], one["median"], one["max"]) == (1, 10.0, 10.0, 10.0)
    four = comps.peer_statistics(pd.Series([10.0, 20.0, 30.0, 60.0]))
    assert four["p25"] == 17.5 and four["mean"] == 30.0 and four["p75"] == 37.5


def test_rank_and_position_label():
    three = pd.Series([10.0, 20.0, 60.0])
    # target 30 with peers 10, 20, 60: one peer is higher -> 2nd of 4 (target included)
    assert comps.rank_among_peers(30.0, three) == (2, 4)
    assert comps.position_label(30.0, three) == "2nd of 4"
    assert comps.position_label(70.0, three) == "1st of 4"
    assert comps.position_label(5.0, three) == "4th of 4"
    assert comps.position_label(20.0, three) == "2nd of 4"       # tied with a peer: better place
    assert comps.position_label(15.0, pd.Series([10.0, 20.0])) == "2nd of 3"
    # four or more peers: a percentile. 2 of 4 below -> 50th
    four = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert comps.position_label(25.0, four) == "50th percentile"
    assert comps.position_label(45.0, four) == "100th percentile"
    assert comps.position_label(None, four) is None
    assert comps.position_label(25.0, pd.Series([None], dtype="float64")) is None
    assert [comps.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 112)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st", "112th"]


def test_percentile_rank():
    peers = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert comps.percentile_rank(25.0, peers) == 50.0      # 2 of 4 below
    assert comps.percentile_rank(5.0, peers) == 0.0        # below every peer
    assert comps.percentile_rank(45.0, peers) == 100.0     # above every peer
    assert comps.percentile_rank(20.0, peers) == 37.5      # 1 below + half of 1 equal = 1.5 / 4
    assert comps.percentile_rank(None, peers) is None
    assert comps.percentile_rank(25.0, pd.Series([None], dtype="float64")) is None
    # missing peers are ignored: 1 of 2 below
    assert comps.percentile_rank(25.0, pd.Series([10.0, None, 30.0])) == 50.0


def test_premium_and_difference_to_median():
    assert comps.premium_to_median(30.0, 25.0) == pytest.approx(20.0)      # 30 / 25 - 1
    assert comps.premium_to_median(20.0, 25.0) == pytest.approx(-20.0)
    assert comps.premium_to_median(30.0, 0.0) is None                       # no relative premium
    assert comps.premium_to_median(30.0, -5.0) is None
    assert comps.premium_to_median(None, 25.0) is None
    assert comps.difference_from_median(0.27, 0.23) == pytest.approx(0.04)


# --- peers ---------------------------------------------------------------------

def test_default_peers_are_the_peer_group_without_the_target():
    assert comps.default_peers(COMPANIES, "T") == ["A", "B", "C", "D"]
    assert comps.default_peers(COMPANIES, "B") == ["T", "A", "C", "D"]
    assert comps.default_peers(COMPANIES, "BANK") == []


def test_peer_warnings():
    assert comps.peer_warnings(COMPANIES, "T", ["A", "B"]) == []
    mixed = comps.peer_warnings(COMPANIES, "T", ["A", "B", "BANK"])
    assert len(mixed) == 1 and "mixes sector types (non_financial with bank)" in mixed[0]
    assert any("Fewer than two peers" in w for w in comps.peer_warnings(COMPANIES, "T", ["A"]))
    assert any("removed" in w for w in comps.peer_warnings(COMPANIES, "T", ["T", "A", "B"]))


# --- the comparison ------------------------------------------------------------

def metrics_frame():
    rows = metric_rows("ev_ebitda", {"T": 30.0, "A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0},
                       "ttm")
    rows += metric_rows("ebitda_margin", {"T": 0.27, "A": 0.20, "B": 0.22, "C": 0.24, "D": 0.26})
    rows += metric_rows("debt_to_equity", {"T": 0.10, "A": 0.10, "B": 0.30, "C": None, "D": 0.20})
    rows += metric_rows("pe_ratio", {"T": None, "A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0}, "ttm")
    return pd.DataFrame(rows)


def test_peer_statistics_exclude_the_target():
    result = comps.compare(metrics_frame(), COMPANIES, "T")
    row = result_row(result, "ev_ebitda")
    assert result["peers"] == ["A", "B", "C", "D"] and row["peer_tickers"] == ["A", "B", "C", "D"]
    assert set(row["peer_values"]) == {"A", "B", "C", "D"}              # T is not among them
    # peers 10, 20, 30, 40 -> median 25, mean 25. Including the target (30) would give 30 and 26.
    assert (row["n_peers"], row["peer_median"], row["peer_mean"]) == (4, 25.0, 25.0)
    assert (row["peer_min"], row["peer_p25"], row["peer_p75"], row["peer_max"]) == \
        (10.0, 17.5, 32.5, 40.0)
    assert row["target_value"] == 30.0
    assert row["percentile_rank"] == 62.5           # 2 below + half of 1 equal = 2.5 / 4
    assert (row["rank_position"], row["rank_of"]) == (2, 5)             # only D is higher
    assert row["position_label"] == "62nd percentile"
    assert row["premium_pct"] == pytest.approx(20.0)                    # 30 / 25 - 1
    assert row["difference"] == pytest.approx(5.0)

    # moving the target's own value changes its positioning but none of the peer statistics
    moved = metrics_frame()
    moved.loc[(moved["ticker"] == "T") & (moved["metric_name"] == "ev_ebitda"), "value"] = 500.0
    again = result_row(comps.compare(moved, COMPANIES, "T"), "ev_ebitda")
    for key in ("n_peers", "peer_min", "peer_p25", "peer_median", "peer_mean", "peer_p75",
                "peer_max"):
        assert again[key] == row[key], key
    assert again["percentile_rank"] == 100.0


def test_target_listed_as_its_own_peer_is_still_excluded():
    result = comps.compare(metrics_frame(), COMPANIES, "T", peers=["T", "A", "B"])
    row = result_row(result, "ev_ebitda")
    assert result["peers"] == ["A", "B"] and row["peer_median"] == 15.0
    assert any("removed" in w for w in result["warnings"])


def test_na_peers_drop_out_of_the_statistics():
    row = result_row(comps.compare(metrics_frame(), COMPANIES, "T"), "debt_to_equity")
    # C has no value: statistics are over A, B, D = 0.10, 0.30, 0.20
    assert row["n_peers"] == 3 and row["peer_median"] == pytest.approx(0.20)
    assert (row["peer_min"], row["peer_max"]) == (0.10, 0.30)
    assert row["peer_tickers"] == ["A", "B", "C", "D"]          # the peer set still lists C
    # three peers with a value: no quartiles or mean, and a rank instead of a percentile
    assert row["peer_p25"] is None and row["peer_p75"] is None and row["peer_mean"] is None
    assert row["percentile_rank"] is None
    assert row["position_label"] == "3rd of 4"       # target 0.10: B and D are higher, A ties


def test_percentage_metrics_report_a_gap_in_points_not_a_relative_premium():
    row = result_row(comps.compare(metrics_frame(), COMPANIES, "T"), "ebitda_margin")
    # peers 20%, 22%, 24%, 26% -> median 23%. Target 27%: 4 percentage points above.
    assert row["peer_median"] == pytest.approx(0.23)
    assert row["difference"] == pytest.approx(0.04)
    assert row["premium_pct"] is None
    assert row["interpretation"] == ("Target Ltd's EBITDA margin of 27.0% is 4.0 percentage "
                                     "points above the peer median of 23.0% (n=4).")


def test_interpretation_is_generated_from_the_numbers():
    result = comps.compare(metrics_frame(), COMPANIES, "T")
    assert result_row(result, "ev_ebitda")["interpretation"] == \
        "Target Ltd trades at 30.0x EV/EBITDA, 20.0% above the peer median of 25.0x (n=4)."
    assert result_row(result, "debt_to_equity")["interpretation"] == \
        "Target Ltd's debt/equity of 0.10 is below the peer median of 0.20 (n=3)."
    # a different target gives a different sentence from the same data
    other = comps.compare(metrics_frame(), COMPANIES, "A")
    # peers of A: T 30, B 20, C 30, D 40 -> median 30. A at 10: 66.7% below.
    assert result_row(other, "ev_ebitda")["interpretation"] == \
        "Alpha trades at 10.0x EV/EBITDA, 66.7% below the peer median of 30.0x (n=4)."


def test_sentence_is_skipped_when_an_input_is_missing():
    result = comps.compare(metrics_frame(), COMPANIES, "T")
    pe = result_row(result, "pe_ratio")
    assert pe["target_value"] is None and pe["interpretation"] is None      # no target value
    assert pe["target_na_reason"] == "N/A (not meaningful for banks)"
    assert pe["peer_median"] == 25.0                       # peer statistics are still reported
    assert result_row(result, "roe")["interpretation"] is None              # no data at all
    assert result_row(result, "roe")["n_peers"] == 0
    assert all(sentence for sentence in result["interpretation"])
    assert len(result["interpretation"]) == 3
    no_peers = comps.peer_statistics(pd.Series([], dtype="float64"))
    assert comps.interpret("X", "pe_ratio", 10.0, no_peers) is None


def test_in_line_and_possessive_wording():
    stats = comps.peer_statistics(pd.Series([20.0, 30.0]))
    assert comps.interpret("Alpha", "pe_ratio", 25.0, stats) == \
        "Alpha trades at 25.0x P/E, in line with the peer median of 25.0x (n=2)."
    margin = comps.peer_statistics(pd.Series([0.20, 0.30]))
    assert comps.interpret("Tata Consultancy Services", "net_margin", 0.25, margin) == (
        "Tata Consultancy Services' net margin of 25.0% is in line with the peer median "
        "of 25.0% (n=2).")


def test_currency_and_basis_caveats_are_appended():
    rows = metric_rows("pe_ratio", {"T": 30.0, "A": 20.0, "B": 30.0}, "ttm",
                       T={"is_translated": True, "reporting_currency": "USD",
                          "method": "latest_annual"})
    rows += metric_rows("revenue_growth", {"T": 0.05, "A": 0.04, "B": 0.06},
                        T={"reporting_currency": "USD"})
    result = comps.compare(pd.DataFrame(rows), COMPANIES, "T", peers=["A", "B"])
    pe = result_row(result, "pe_ratio")["interpretation"]
    assert pe.startswith("Target Ltd trades at 30.0x P/E, 20.0% above the peer median of 25.0x")
    assert "INR figures for T are translated from USD." in pe
    assert "uses the latest annual figure" in pe
    growth = result_row(result, "revenue_growth")["interpretation"]
    assert "This is reporting-currency growth (USD)" in growth
    # seen from a peer: the caveat names the company whose figures are translated
    from_peer = comps.compare(pd.DataFrame(rows), COMPANIES, "A", peers=["T", "B"])
    assert "Peer figures for T are translated to INR." in \
        result_row(from_peer, "pe_ratio")["interpretation"]
    assert "Growth for T is reporting-currency growth (USD)." in \
        result_row(from_peer, "revenue_growth")["interpretation"]


BANNED = ["undervalued", "overvalued", "good investment", "buy", "sell", "strong company",
          "cheap", "expensive", "attractive", "recommend", "should", "outperform"]


def test_interpretation_never_uses_judgemental_language():
    sentences = []
    for target in ("T", "A", "B", "C", "D"):
        sentences += comps.compare(metrics_frame(), COMPANIES, target)["interpretation"]
    assert len(sentences) >= 10
    for sentence in sentences:
        lowered = sentence.lower()
        assert not any(word in lowered for word in BANNED), sentence


def test_comps_table_puts_statistic_rows_under_the_companies():
    table = comps.comps_table(metrics_frame(), COMPANIES, "T", ["A", "B", "C", "D"], "valuation")
    assert list(table.index) == ["T", "A", "B", "C", "D", "Peer n", "Peer min", "Peer p25",
                                 "Peer median", "Peer mean", "Peer p75", "Peer max"]
    assert table.at["T", "ev_ebitda"] == 30.0
    assert table.at["Peer median", "ev_ebitda"] == 25.0        # from A-D only
    assert table.at["Peer n", "ev_ebitda"] == 4
    assert table.at["Peer n", "pb_ratio"] == 0 and pd.isna(table.at["Peer median", "pb_ratio"])
    # with three peers the quartile and mean rows are blank, min / median / max are not
    small = comps.comps_table(metrics_frame(), COMPANIES, "T", ["A", "B", "C"], "valuation")
    assert small.at["Peer median", "ev_ebitda"] == 20.0
    assert small.at["Peer max", "ev_ebitda"] == 30.0
    assert small.loc[["Peer p25", "Peer mean", "Peer p75"], "ev_ebitda"].isna().all()


# --- formatting ----------------------------------------------------------------

def test_number_formats():
    assert formatting.indian_grouping(12453000) == "1,24,53,000"
    assert formatting.indian_grouping(999) == "999"
    assert formatting.indian_grouping(100000) == "1,00,000"
    assert formatting.format_crore(1.2453e12) == "₹1,24,530 Cr"          # 1.2453e12 / 1e7
    assert formatting.format_crore(-5.0e8) == "-₹50 Cr"
    assert formatting.format_pct(0.1842) == "18.42%"
    assert formatting.format_multiple(24.68) == "24.7x"
    assert formatting.format_price(1245.3) == "₹1,245.30"
    assert formatting.format_price(124530.456) == "₹1,24,530.46"
    assert formatting.format_metric(0.25, "ratio") == "0.25"
    for formatter in (formatting.format_crore, formatting.format_pct, formatting.format_multiple,
                      formatting.format_price, formatting.format_ratio):
        assert formatter(None) == "N/A" and formatter(float("nan")) == "N/A"
