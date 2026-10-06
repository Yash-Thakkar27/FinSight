"""Share counts on the current (post-split, post-bonus) basis, for EPS.

A split or bonus issue changes the share count without changing the company.
To compare EPS across years every period's share count must be on the same
basis: the current one. The cumulative split factor for a period is the
product of the ratios of every split or bonus issue after its period end.

The source is not consistent about this. It restates period-end share counts
to the current basis, and usually the weighted-average counts too, but not
always: HDFC Bank's FY2023 and FY2024 weighted averages are still on the
share count before its 1:1 bonus of August 2025, while FY2025 is restated.
Multiplying every earlier figure by the factor would therefore double-count
the ones the source already restated.

So each figure is tested before it is restated. Against a reference count
known to be on the current basis, a figure still on the old basis is about
1/factor of the reference and a restated one is about equal to it. The
geometric midpoint, 1/sqrt(factor), separates the two. The decision is
recorded with the figure, and a figure close to the midpoint is marked
marginal so it can be reviewed.
"""

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

RESTATED = "restated_by_finsight"            # source figure was on the old basis: multiplied
ALREADY_RESTATED = "already_restated_at_source"
NO_SPLIT = "no_split_after_period"
UNVERIFIED = "basis_unverified"              # a split followed, but there is no reference
NOT_REPORTED = "no_share_count_reported"

# Within this distance (in log terms) of the midpoint, the decision is marked marginal.
MARGINAL_LOG_DISTANCE = 0.15

# Preference order for the EPS denominator.
SHARE_BASES = (
    ("weighted_average_shares_diluted", "weighted_average_diluted"),
    ("weighted_average_shares_basic", "weighted_average_basic"),
    ("shares_outstanding", "period_end"),
)


@dataclass(frozen=True)
class AdjustedShares:
    shares: float | None        # on the current basis; None if it cannot be established
    restatement: str            # RESTATED | ALREADY_RESTATED | NO_SPLIT | UNVERIFIED | NOT_REPORTED
    split_factor: float
    as_reported: float | None
    marginal: bool = False


def cumulative_split_factor(splits: pd.Series, period_end: date) -> float:
    """Product of the ratios of all splits and bonus issues after `period_end`.

    splits: ratio per effective date (2.0 for a 2-for-1 split or a 1:1 bonus).
    A split on the period-end date itself is treated as already reflected.
    """
    if splits is None or len(splits) == 0:
        return 1.0
    later = splits[pd.to_datetime(splits.index) > pd.Timestamp(period_end)]
    return float(later.prod()) if len(later) else 1.0


def restate_to_current_basis(reported: float | None, factor: float,
                             reference: float | None) -> AdjustedShares:
    """Put one reported share count on the current basis.

    reference: a share count for the same company known to be on the current
    basis (see the module docstring). Without one, a figure that precedes a
    split cannot be classified and is returned as None rather than guessed.
    """
    if reported is None or pd.isna(reported) or reported <= 0:
        return AdjustedShares(None, NOT_REPORTED, factor, None)
    reported = float(reported)
    if factor == 1.0:
        return AdjustedShares(reported, NO_SPLIT, 1.0, reported)
    if reference is None or pd.isna(reference) or reference <= 0:
        return AdjustedShares(None, UNVERIFIED, factor, reported)

    log_ratio = math.log(reported / reference)
    midpoint = math.log(1 / math.sqrt(factor)) if factor > 1 else math.log(math.sqrt(1 / factor))
    on_old_basis = log_ratio < midpoint if factor > 1 else log_ratio > midpoint
    marginal = abs(log_ratio - midpoint) < MARGINAL_LOG_DISTANCE
    if on_old_basis:
        return AdjustedShares(reported * factor, RESTATED, factor, reported, marginal)
    return AdjustedShares(reported, ALREADY_RESTATED, factor, reported, marginal)


def adjusted_shares_for_eps(period: dict, factor: float,
                            current_shares: float | None) -> tuple[AdjustedShares, str | None]:
    """The EPS denominator for one period, and which share count it is.

    period: reported share counts for the period, keyed by canonical line item.
    Preference: weighted-average diluted, then weighted-average basic, then
    period-end shares. Returns (adjusted shares, share basis); basis is None
    when no usable share count exists.

    The period-end count is checked against the company's current share
    count. A weighted average is checked against that period's own restated
    period-end count, which is the closest like-for-like figure.
    """
    period_end = restate_to_current_basis(period.get("shares_outstanding"), factor, current_shares)
    unverified = False
    for item, basis in SHARE_BASES:
        if item == "shares_outstanding":
            candidate = period_end
        else:
            reference = period_end.shares if period_end.shares is not None else current_shares
            candidate = restate_to_current_basis(period.get(item), factor, reference)
        if candidate.shares is not None:
            return candidate, basis
        unverified = unverified or candidate.restatement == UNVERIFIED
    return AdjustedShares(None, UNVERIFIED if unverified else NOT_REPORTED, factor, None), None
