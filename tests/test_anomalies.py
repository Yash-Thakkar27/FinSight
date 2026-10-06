"""Offline tests for anomaly detection and correlation. Expected values are hand-calculated."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.analytics import anomaly_detection as ad
from src.analytics import peer_analytics

# --- IQR and z-score on a fixed sample ---------------------------------------------

def test_iqr_bounds_and_flags():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 100.0])
    # Q1 = 3, Q3 = 7, IQR = 4 -> fences 3 - 6 = -3 and 7 + 6 = 13
    assert ad.iqr_bounds(values) == (-3.0, 13.0)
    assert ad.iqr_flags(values).tolist() == [False] * 8 + [True]
    # a wider multiplier moves the fences: 3 - 12 = -9 and 7 + 12 = 19
    assert ad.iqr_bounds(values, 3.0) == (-9.0, 19.0)
    # a sample with no spread has fences [5, 5]: floating-point noise is not an anomaly
    flat = pd.Series([5.0, 5.0, 5.0, 5.0, 5.0 + 1e-15, 5.1])
    assert ad.iqr_flags(flat).tolist() == [False] * 5 + [True]
    # exactly on the fence is not flagged; missing values are never flagged
    on_fence = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 13.0, None])
    assert not ad.iqr_flags(on_fence).any()


def test_modified_zscores():
    values = pd.Series([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    # median 4.5; absolute deviations 2.5, 0.5 x5, 2.5, 4.5 -> MAD = 0.5
    scores = ad.modified_zscores(values)
    assert scores.iloc[-1] == pytest.approx(0.6745 * 4.5 / 0.5)      # 9 -> +6.07
    assert scores.iloc[0] == pytest.approx(0.6745 * -2.5 / 0.5)      # 2 -> -3.37
    assert ad.modified_zscore_flags(values, 3.5).tolist() == [False] * 7 + [True]
    # the classical z-score of the same 9 is only (9 - 5) / 2.138 = 1.87: the outlier inflates
    # the standard deviation it is measured against, which MAD does not allow
    assert (9 - values.mean()) / values.std(ddof=1) == pytest.approx(1.871, abs=1e-3)


def test_modified_zscore_when_mad_is_zero():
    # more than half the sample is identical: MAD = 0, so the mean absolute deviation is used
    values = pd.Series([5.0] * 6 + [10.0])
    # mean absolute deviation = 5 / 7; scale = 1.253314 * 5 / 7 = 0.89522; M(10) = 5 / 0.89522
    assert ad.modified_zscores(values).iloc[-1] == pytest.approx(5 / (1.253314 * 5 / 7))
    assert ad.modified_zscores(values).iloc[0] == 0.0
    assert ad.modified_zscores(pd.Series([5.0, 5.0, 5.0])).isna().all()   # no spread: undefined


def test_a_classical_zscore_on_four_values_can_never_reach_three():
    # why a company's own 4 annual values are never z-scored: the bound is (n - 1) / sqrt(n) = 1.5
    most_extreme = pd.Series([0.0, 0.0, 0.0, 1e9])
    z = (most_extreme - most_extreme.mean()) / most_extreme.std(ddof=1)
    assert z.abs().max() == pytest.approx(1.5)
    rng = np.random.default_rng(0)
    for _ in range(200):
        sample = pd.Series(rng.standard_cauchy(4))
        assert ((sample - sample.mean()) / sample.std(ddof=1)).abs().max() <= 1.5 + 1e-9


# --- rolling window --------------------------------------------------------------

def test_rolling_statistics_use_only_earlier_observations():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 50.0, 6.0])
    scores = ad.rolling_modified_zscores(series, window=5, min_periods=5)
    # the first five have no full window behind them
    assert scores.iloc[:5].isna().all()
    # 50 is judged against 1..5: median 3, deviations 2, 1, 0, 1, 2 -> MAD 1
    assert scores.iloc[5] == pytest.approx(0.6745 * 47 / 1)
    # the spike does not shield itself: its own value is not in its window
    bounds = ad.rolling_iqr_bounds(series, window=5, min_periods=5, multiplier=1.5)
    # window 1..5: Q1 = 2, Q3 = 4, IQR = 2 -> [-1, 7]
    assert (bounds.at[5, "lower"], bounds.at[5, "upper"]) == (-1.0, 7.0)

    # changing a later observation leaves every earlier score unchanged
    altered = series.copy()
    altered.iloc[6] = -999.0
    pd.testing.assert_series_equal(ad.rolling_modified_zscores(altered, 5, 5).iloc[:6],
                                   scores.iloc[:6])


def market_prices(returns, ticker="AAA.NS", volume=None, start="2025-01-01"):
    closes = 100.0 * np.cumprod(1 + np.asarray(returns, dtype="float64"))
    days = pd.bdate_range(start, periods=len(closes)).date
    return pd.DataFrame({"ticker": ticker, "date": days, "adj_close": closes, "close": closes,
                         "volume": 1000 if volume is None else volume, "is_stale_quote": False})


def test_market_anomaly_is_flagged_with_the_required_wording():
    returns = [0.01, -0.01] * 40          # 80 quiet days alternating +1% / -1%
    returns[60] = 0.15                    # one +15% day
    flagged = ad.detect_market_anomalies(market_prices(returns), window=30, min_periods=20,
                                         iqr_multiplier=3.0, modified_zscore_threshold=3.5)
    spike_day = pd.bdate_range("2025-01-01", periods=80).date[60]
    spike = flagged[(flagged["date"] == spike_day) & (flagged["variable"] == "daily_return")]
    assert sorted(spike["method"]) == ["iqr", "modified_zscore"]
    assert spike["value"].iloc[0] == pytest.approx(0.15)
    assert flagged["message"].str.startswith("Potential data anomaly detected").all()
    assert "+15.0%" in spike["message"].iloc[0]
    # the quiet days before the spike are not flagged
    assert flagged[flagged["date"] < spike_day].empty
    for word in ("fraud", "manipulat", "misconduct", "suspicious"):
        assert not flagged["message"].str.contains(word, case=False).any()


def test_market_anomalies_ignore_placeholder_rows_and_zero_volume():
    returns = [0.01, -0.01] * 40
    prices = market_prices(returns)
    prices.loc[50, "volume"] = 0                       # a zero-volume day: no log volume
    prices.loc[55, "is_stale_quote"] = True            # a placeholder row
    flagged = ad.detect_market_anomalies(prices, window=30, min_periods=20)
    assert prices.loc[55, "date"] not in set(flagged["date"])
    assert not ((flagged["date"] == prices.loc[50, "date"])
                & (flagged["variable"] == "log_volume")).any()

    volume = np.full(80, 1000.0)
    volume[70] = 1_000_000                             # a volume spike 1,000x normal
    volume[:40] = [900, 1100] * 20                     # some spread so thresholds exist
    volume[40:70] = [900, 1100] * 15
    flagged = ad.detect_market_anomalies(market_prices(returns, volume=volume), 30, 20)
    spike = flagged[(flagged["variable"] == "log_volume")
                    & (flagged["date"] == prices.loc[70, "date"])]
    assert sorted(spike["method"]) == ["iqr", "modified_zscore"]
    assert "1,000,000 shares" in spike["message"].iloc[0]


# --- fundamentals ----------------------------------------------------------------

def statement_rows(ticker, sector_type, period_end, values):
    return [{"ticker": ticker, "sector_type": sector_type, "line_item": item,
             "period_end_date": period_end, "period_type": "annual", "original_value": value}
            for item, value in values.items()]


def test_fundamental_changes_and_pooled_flags():
    rows = []
    # nine steady companies: revenue +10%, net margin flat at 10%, debt flat
    for i in range(9):
        growth = 1.08 + 0.005 * i                      # +8.0% ... +12.0%
        for year, revenue in ((2025, 100.0), (2026, 100.0 * growth)):
            rows += statement_rows(f"C{i}", "non_financial", date(year, 3, 31), {
                "revenue": revenue, "net_income": revenue * 0.10, "ebitda": revenue * 0.20,
                "total_debt": 50.0})
    # one company whose revenue trebles and whose net margin falls from 10% to 2%
    rows += statement_rows("ODD", "non_financial", date(2025, 3, 31), {
        "revenue": 100.0, "net_income": 10.0, "ebitda": 20.0, "total_debt": 50.0})
    rows += statement_rows("ODD", "non_financial", date(2026, 3, 31), {
        "revenue": 300.0, "net_income": 6.0, "ebitda": 60.0, "total_debt": 50.0})
    # a bank: its EBITDA margin and debt are not examined
    rows += statement_rows("BANK", "bank", date(2025, 3, 31), {
        "revenue": 100.0, "net_income": 20.0, "ebitda": 5.0, "total_debt": 10.0})
    rows += statement_rows("BANK", "bank", date(2026, 3, 31), {
        "revenue": 110.0, "net_income": 22.0, "ebitda": 50.0, "total_debt": 900.0})

    raw = ad.fundamental_changes(pd.DataFrame(rows))
    odd = raw[raw["ticker"] == "ODD"].set_index("variable")["value"]
    assert odd["revenue_growth"] == pytest.approx(2.0)              # 300 / 100 - 1
    assert odd["net_margin_change"] == pytest.approx(-0.08)         # 2% - 10%
    assert odd["ebitda_margin_change"] == pytest.approx(0.0)
    assert odd["debt_growth"] == pytest.approx(0.0)
    bank = raw[raw["ticker"] == "BANK"]
    assert set(bank["variable"]) == {"revenue_growth", "net_margin_change"}

    # the ten companies form one peer group; the bank is alone in its own
    peer_group = {f"C{i}": "g" for i in range(9)} | {"ODD": "g", "BANK": "banks"}
    changes = ad.sector_adjust(raw, peer_group, min_group_size=3)
    odd_adjusted = changes[changes["ticker"] == "ODD"].set_index("variable")
    # the group's median revenue growth is between C4 (+10.0%) and C5 (+10.5%): 10.25%
    assert odd_adjusted.at["revenue_growth", "peer_group_median"] == pytest.approx(0.1025)
    assert odd_adjusted.at["revenue_growth", "adjusted_value"] == pytest.approx(2.0 - 0.1025)
    # a group of one has no median: its rows are left untested
    assert changes.loc[changes["ticker"] == "BANK", "adjusted_value"].isna().all()

    flagged = ad.detect_fundamental_anomalies(changes, iqr_multiplier=1.5,
                                              modified_zscore_threshold=3.5)
    flagged_keys = set(zip(flagged["ticker"], flagged["variable"], flagged["method"]))
    assert ("ODD", "revenue_growth", "iqr") in flagged_keys
    assert ("ODD", "revenue_growth", "modified_zscore") in flagged_keys
    assert ("ODD", "net_margin_change", "iqr") in flagged_keys
    assert not any(ticker.startswith("C") for ticker, _, _ in flagged_keys)
    assert not any(ticker == "BANK" for ticker, _, _ in flagged_keys)
    row = flagged[(flagged["ticker"] == "ODD") & (flagged["variable"] == "net_margin_change")
                  & (flagged["method"] == "iqr")].iloc[0]
    assert row["message"].startswith(
        "Potential data anomaly detected: change in net margin of -8.0 percentage points is "
        "-8.0 percentage points relative to its peer-group median (+0.0 percentage points)")
    assert row["value"] == pytest.approx(-0.08) and row["adjusted_value"] == pytest.approx(-0.08)
    assert row["peer_group_median"] == pytest.approx(0.0)
    assert flagged[flagged["ticker"] == "ODD"]["date"].eq(date(2026, 3, 31)).all()


def sector_move_changes():
    """25 companies in 5 peer groups of 5. Every bank's revenue grows about 33% (a sector-wide
    move, as after a merger wave); everyone else's grows about 10%. One pharma company grows 60%.
    """
    spread = [-0.02, -0.01, 0.0, 0.01, 0.02]
    rows, peer_group = [], {}
    for group, base in (("banks", 0.33), ("it", 0.10), ("auto", 0.10), ("consumer", 0.10),
                        ("pharma", 0.10)):
        for i, offset in enumerate(spread):
            ticker = f"{group}{i}"
            peer_group[ticker] = group
            rows.append({"ticker": ticker, "date": date(2024, 3, 31),
                         "variable": "revenue_growth", "value": base + offset})
    changes = pd.DataFrame(rows)
    changes.loc[changes["ticker"] == "pharma4", "value"] = 0.60
    return changes, peer_group


def test_a_sector_wide_move_no_longer_flags_every_company_in_the_sector():
    raw, peer_group = sector_move_changes()
    banks = {f"banks{i}" for i in range(5)}

    # Before: raw changes pooled across sectors. All five banks stand out from the other
    # twenty companies simply because their whole sector moved.
    before = ad.detect_fundamental_anomalies(raw, value_column="value")
    assert banks <= set(before["ticker"])
    assert set(before.loc[before["method"] == "iqr", "ticker"]) == banks | {"pharma4"}

    # After: each change is measured against its own peer group's median first.
    # The banks' residuals are -2 to +2 points like everyone else's, so none is flagged.
    changes = ad.sector_adjust(raw, peer_group)
    bank_rows = changes[changes["ticker"].isin(banks)]
    assert bank_rows["peer_group_median"].tolist() == pytest.approx([0.33] * 5)
    assert bank_rows["adjusted_value"].abs().max() == pytest.approx(0.02)
    after = ad.detect_fundamental_anomalies(changes)
    assert not banks & set(after["ticker"])
    # ... while the one company that moved differently from its own sector is still flagged
    assert set(after["ticker"]) == {"pharma4"}
    assert set(after["method"]) == {"iqr", "modified_zscore"}
    pharma = after.iloc[0]
    assert pharma["value"] == pytest.approx(0.60)
    assert pharma["peer_group_median"] == pytest.approx(0.10)       # median of 8, 9, 10, 11, 60
    assert pharma["adjusted_value"] == pytest.approx(0.50)


def test_too_few_observations_are_not_tested():
    changes = pd.DataFrame({"ticker": ["A", "B", "C"], "date": [date(2026, 3, 31)] * 3,
                            "variable": "revenue_growth", "value": [0.1, 0.1, 9.0]})
    assert ad.detect_fundamental_anomalies(changes, value_column="value").empty
    # a peer group with fewer than three values for a period gives no residual to test
    adjusted = ad.sector_adjust(changes, {"A": "g", "B": "g", "C": "other"}, min_group_size=3)
    assert adjusted["adjusted_value"].isna().all()
    assert ad.detect_fundamental_anomalies(adjusted).empty


# --- correlation -----------------------------------------------------------------

def test_correlation_rows_are_aligned_symmetric_and_windowed():
    days = pd.bdate_range("2023-01-02", "2026-01-30")
    rng = np.random.default_rng(42)
    base = rng.normal(0, 0.01, len(days))
    frames = []
    for ticker, returns in (("A", base), ("B", base), ("C", -base)):
        closes = 100 * np.cumprod(1 + returns)
        frames.append(pd.DataFrame({"ticker": ticker, "date": days.date, "adj_close": closes,
                                    "is_stale_quote": False}))
    prices = pd.concat(frames, ignore_index=True)
    # C is missing one day, and A has one placeholder row: both dates drop out for every pair
    prices = prices[~((prices["ticker"] == "C") & (prices["date"] == days[100].date()))]
    prices.loc[(prices["ticker"] == "A") & (prices["date"] == days[200].date()),
               "is_stale_quote"] = True

    rows = pd.DataFrame(peer_analytics.correlation_rows(prices))
    assert set(rows["window_label"]) == {"1y", "3y", "full"}
    full = rows[rows["window_label"] == "full"].set_index(["ticker_a", "ticker_b"])
    assert len(full) == 9                                   # 3 x 3, both orderings + diagonal
    assert full.at[("A", "A"), "correlation"] == pytest.approx(1.0)
    assert full.at[("A", "B"), "correlation"] > 0.99        # identical except around two dates
    assert full.at[("B", "C"), "correlation"] < -0.99       # mirror image
    assert full.at[("A", "C"), "correlation"] == full.at[("C", "A"), "correlation"]
    # every pair shares one sample: the same number of observations and date range
    assert full["n_observations"].nunique() == 1
    one_year = rows[rows["window_label"] == "1y"]
    assert one_year["n_observations"].iloc[0] < full["n_observations"].iloc[0]
    assert one_year["start_date"].iloc[0] > date(2025, 1, 30)


def test_window_longer_than_the_history_is_not_reported():
    days = pd.bdate_range("2025-06-02", "2026-01-30")
    rng = np.random.default_rng(7)
    frames = [pd.DataFrame({"ticker": t, "date": days.date, "is_stale_quote": False,
                            "adj_close": 100 * np.cumprod(1 + rng.normal(0, 0.01, len(days)))})
              for t in ("A", "B")]
    rows = pd.DataFrame(peer_analytics.correlation_rows(pd.concat(frames, ignore_index=True)))
    assert set(rows["window_label"]) == {"full"}            # under a year of history
