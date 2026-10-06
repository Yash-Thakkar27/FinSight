"""Risk metrics on daily simple returns of adjusted close.

Annualization uses 252 trading days (config: trading_days_per_year).
"""

import math

import numpy as np
import pandas as pd

from src.analytics.registry import register

TRADING_DAYS = 252
MIN_OBSERVATIONS = 30      # fewer daily returns than this and a risk metric is not reported
ZERO_VOLATILITY = 1e-12    # annualized volatility below this is treated as zero


def annualized_return(returns: pd.Series, trading_days: int = TRADING_DAYS) -> float | None:
    """Arithmetic annualization: mean daily return x trading days."""
    if len(returns) < MIN_OBSERVATIONS:
        return None
    return float(returns.mean() * trading_days)


@register("volatility_1y", formula_id="F_VOLATILITY_1Y", unit="pct", kind="market",
          expression="std(daily returns, sample) * sqrt(252)",
          description="Annualized volatility over the trailing year.")
@register("volatility_3y", formula_id="F_VOLATILITY_3Y", unit="pct", kind="market",
          expression="std(daily returns, sample) * sqrt(252)",
          description="Annualized volatility over the trailing three years.")
def annualized_volatility(returns: pd.Series, trading_days: int = TRADING_DAYS) -> float | None:
    """Sample standard deviation (ddof = 1) of daily returns x sqrt(trading days)."""
    if len(returns) < MIN_OBSERVATIONS:
        return None
    return float(returns.std(ddof=1) * math.sqrt(trading_days))


@register("downside_deviation_1y", formula_id="F_DOWNSIDE_DEVIATION_1Y", unit="pct", kind="market",
          expression="sqrt(mean(min(r, 0)^2)) * sqrt(252)",
          description="Annualized downside deviation against a 0% daily target, trailing year.")
@register("downside_deviation_3y", formula_id="F_DOWNSIDE_DEVIATION_3Y", unit="pct",
          kind="market", expression="sqrt(mean(min(r, 0)^2)) * sqrt(252)",
          description="Annualized downside deviation against a 0% daily target, three years.")
def downside_deviation(returns: pd.Series, target: float = 0.0,
                       trading_days: int = TRADING_DAYS) -> float | None:
    """Root mean square of returns below `target`, annualized.

    The mean is taken over **all** observations (returns at or above the target
    contribute zero), which is the lower-partial-moment form used in the
    Sortino ratio. It reflects how often losses happen as well as how large
    they are; the standard deviation of only the negative returns would not.
    """
    if len(returns) < MIN_OBSERVATIONS:
        return None
    shortfall = np.minimum(returns.to_numpy(dtype="float64") - target, 0.0)
    return float(math.sqrt(np.mean(shortfall ** 2)) * math.sqrt(trading_days))


@register("sharpe_1y", formula_id="F_SHARPE_1Y", unit="ratio", kind="market",
          expression="(mean(daily returns) * 252 - Rf) / (std(daily returns) * sqrt(252))",
          description="Sharpe ratio over the trailing year. Rf is the configured annual "
                      "risk-free rate; null when no rate is configured.")
@register("sharpe_3y", formula_id="F_SHARPE_3Y", unit="ratio", kind="market",
          expression="(mean(daily returns) * 252 - Rf) / (std(daily returns) * sqrt(252))",
          description="Sharpe ratio over the trailing three years.")
def sharpe_ratio(returns: pd.Series, risk_free_rate: float | None,
                 trading_days: int = TRADING_DAYS) -> float | None:
    """(annualized return - risk-free rate) / annualized volatility."""
    if risk_free_rate is None:
        return None
    volatility = annualized_volatility(returns, trading_days)
    if volatility is None or volatility < ZERO_VOLATILITY:
        return None        # undefined without dispersion (and guards floating-point residue)
    return (annualized_return(returns, trading_days) - risk_free_rate) / volatility


@register("max_drawdown_1y", formula_id="F_MAX_DRAWDOWN_1Y", unit="pct", kind="market",
          expression="min over t of P_t / max(P_s for s <= t) - 1",
          description="Largest peak-to-trough decline over the trailing year.")
@register("max_drawdown_3y", formula_id="F_MAX_DRAWDOWN_3Y", unit="pct", kind="market",
          expression="min over t of P_t / max(P_s for s <= t) - 1",
          description="Largest peak-to-trough decline over the trailing three years.")
def max_drawdown(prices: pd.Series) -> dict | None:
    """Largest decline from a running peak.

    Returns {'max_drawdown': negative fraction (0 if prices never fell),
             'peak_date': date of the peak before the worst trough,
             'trough_date': date of the worst trough}, or None for fewer than 2 prices.
    """
    if len(prices) < 2:
        return None
    running_peak = prices.cummax()
    drawdown = prices / running_peak - 1
    trough_date = drawdown.idxmin()
    peak_date = prices.loc[:trough_date].idxmax()
    return {"max_drawdown": float(drawdown.loc[trough_date]),
            "peak_date": pd.Timestamp(peak_date).date(),
            "trough_date": pd.Timestamp(trough_date).date()}


def correlation_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    """Pearson correlation of daily returns over an aligned date range.

    `returns` has one column per security. Only dates on which every security
    has a return are used (inner join), so all pairs share the same sample.
    """
    return returns.dropna(how="any").corr()
