"""Return calculations. All use adjusted close, so they are split-, bonus- and dividend-adjusted.

Inputs are a price Series for one security, indexed by date (ascending), with
placeholder rows already removed. A return over a horizon is null when the
history does not reach back far enough: nothing is extrapolated.
"""

from datetime import date

import pandas as pd

from src.analytics.registry import register

# A price up to this many calendar days before a target date still counts as that date's price.
MAX_PRICE_STALENESS_DAYS = 7


def daily_returns(prices: pd.Series) -> pd.Series:
    """Simple daily returns: P_t / P_t-1 - 1. The first observation has none."""
    return prices.pct_change().dropna()


def cumulative_returns(returns: pd.Series) -> pd.Series:
    """Cumulative return path: product(1 + r) - 1."""
    return (1 + returns).cumprod() - 1


def price_on_or_before(prices: pd.Series, target: date,
                       max_staleness_days: int = MAX_PRICE_STALENESS_DAYS) -> float | None:
    """Last price on or before `target`, or None if there is none within the staleness limit."""
    target = pd.Timestamp(target)
    history = prices.loc[:target]
    if history.empty or (target - history.index[-1]).days > max_staleness_days:
        return None
    return float(history.iloc[-1])


def start_price(prices: pd.Series, as_of: date, *, months: int = 0, years: int = 0) -> float | None:
    """Price at the start of a trailing window ending at `as_of`."""
    target = pd.Timestamp(as_of) - pd.DateOffset(months=months, years=years)
    return price_on_or_before(prices, target.date())


@register("return_1m", formula_id="F_RETURN_1M", unit="pct", kind="market",
          expression="P(as_of) / P(as_of - 1 month) - 1",
          description="Adjusted-close return over the trailing month.")
@register("return_3m", formula_id="F_RETURN_3M", unit="pct", kind="market",
          expression="P(as_of) / P(as_of - 3 months) - 1",
          description="Adjusted-close return over the trailing three months.")
@register("return_1y", formula_id="F_RETURN_1Y", unit="pct", kind="market",
          expression="P(as_of) / P(as_of - 1 year) - 1",
          description="Adjusted-close return over the trailing year.")
def trailing_return(prices: pd.Series, as_of: date, *, months: int = 0,
                    years: int = 0) -> float | None:
    """Simple return from the start of the trailing window to `as_of`. None if history is short."""
    end = price_on_or_before(prices, as_of)
    start = start_price(prices, as_of, months=months, years=years)
    if end is None or start is None or start <= 0:
        return None
    return end / start - 1


@register("return_ytd", formula_id="F_RETURN_YTD", unit="pct", kind="market",
          expression="P(as_of) / P(last trading day of previous calendar year) - 1",
          description="Calendar year-to-date adjusted-close return.")
def ytd_return(prices: pd.Series, as_of: date) -> float | None:
    end = price_on_or_before(prices, as_of)
    start = price_on_or_before(prices, date(pd.Timestamp(as_of).year - 1, 12, 31))
    if end is None or start is None or start <= 0:
        return None
    return end / start - 1


@register("cagr_3y", formula_id="F_CAGR_3Y", unit="pct", kind="market",
          expression="(P(as_of) / P(as_of - 3 years)) ^ (1/3) - 1",
          description="Compound annual growth rate of adjusted close over three years.")
def cagr(prices: pd.Series, as_of: date, years: int) -> float | None:
    """Compound annual growth rate over `years`. None if history is too short."""
    end = price_on_or_before(prices, as_of)
    start = start_price(prices, as_of, years=years)
    if end is None or start is None or start <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def window_prices(prices: pd.Series, as_of: date, years: int) -> pd.Series | None:
    """Prices in the trailing `years` window, or None if history is too short.

    The window starts at the last price on or before (as_of - years), so the
    first daily return in it is the move from that starting price.
    """
    target = pd.Timestamp(as_of) - pd.DateOffset(years=years)
    if price_on_or_before(prices, target.date()) is None:
        return None
    start = prices.loc[:target].index[-1]
    return prices.loc[start:pd.Timestamp(as_of)]


def window_returns(prices: pd.Series, as_of: date, years: int) -> pd.Series | None:
    """Daily returns inside the trailing `years` window, or None if history is too short."""
    window = window_prices(prices, as_of, years)
    return None if window is None else daily_returns(window)
