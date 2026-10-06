"""Walk-forward (expanding-window) splits for time series.

Random K-fold must never be used on time series: it trains on the future and
tests on the past. Here every fold trains on everything known up to a cutoff
date and is tested on the block of dates that follows.

The targets are forward-looking: the target at date s covers s+1 .. s+h. A
training row is only usable once its target has been fully observed, so the
last h rows before the cutoff are purged from training. Without the purge the
training targets would overlap the test period.

    |<------------- train rows ------------->|<- purged (h) ->|<---- test ---->|
    0                               cutoff-h   cutoff          cutoff+1 ...
"""

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Fold:
    number: int
    train_dates: tuple      # forecast origins whose targets are fully observed by `cutoff`
    cutoff: object          # last date of information available when the fold's model is fit
    test_dates: tuple       # forecast origins the fitted model is evaluated on


def walk_forward_folds(dates: Sequence, initial_train: int, test_size: int,
                       horizon: int) -> list[Fold]:
    """Expanding-window folds over sorted, unique dates.

    initial_train: number of dates before the first test block.
    test_size:     number of dates in each test block.
    horizon:       target horizon h; the last h dates up to the cutoff are purged from training.

    Fold k tests on dates[initial_train + k*test_size : initial_train + (k+1)*test_size] and
    trains on dates[0 : initial_train + k*test_size - horizon]. The last block may be shorter.
    """
    dates = list(dates)
    if sorted(set(dates)) != dates:
        raise ValueError("dates must be sorted and unique")
    if initial_train <= horizon:
        raise ValueError("initial_train must exceed the horizon")
    folds = []
    start = initial_train
    while start < len(dates):
        test = tuple(dates[start:start + test_size])
        train = tuple(dates[:start - horizon])
        folds.append(Fold(len(folds) + 1, train, dates[start - 1], test))
        start += test_size
    return folds
