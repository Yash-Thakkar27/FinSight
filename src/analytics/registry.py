"""Metric registry: every metric FinSight computes is declared here once.

A metric is a pure function plus its metadata: formula id, unit, the formula as
text, and the sector types it is meaningful for. The registry is what
`core.formulas` is populated from, so a number shown in the app can always be
traced to a documented formula.

Sector applicability (Section 10.1 of the spec):

    Gross / EBITDA / EBIT margin, EV/EBITDA, EV/Revenue   non-financial only
    Current / quick ratio                                 non-financial only
    Debt/Equity, Debt/Assets, Net Debt/EBITDA             non-financial only (deposits are not debt)
    P/E, P/B, ROE, ROA, EPS growth, net margin            all
    NII growth, cost-to-income, loan/deposit              banks and NBFCs, if source fields exist
"""

from dataclasses import dataclass
from typing import Callable

ALL = ("non_financial", "bank", "nbfc", "insurance")
NON_FINANCIAL = ("non_financial",)
LENDERS = ("bank", "nbfc")

SECTOR_LABELS = {"non_financial": "non-financial companies", "bank": "banks", "nbfc": "NBFCs",
                 "insurance": "insurers"}


@dataclass(frozen=True)
class MetricSpec:
    name: str
    formula_id: str
    unit: str                       # pct | ratio | multiple | INR
    expression: str
    description: str
    applicable: tuple[str, ...]
    kind: str                       # fundamental | valuation | market
    inputs: tuple[str, ...] = ()    # line items read from the period itself
    prior_inputs: tuple[str, ...] = ()   # line items also read from one year earlier
    annual_only: bool = False
    func: Callable | None = None    # fundamental metrics: wide DataFrame -> Series


REGISTRY: dict[str, MetricSpec] = {}


def register(name: str, *, formula_id: str, unit: str, expression: str, description: str = "",
             applicable: tuple[str, ...] = ALL, kind: str = "fundamental",
             inputs: tuple[str, ...] = (), prior_inputs: tuple[str, ...] = (),
             annual_only: bool = False):
    """Decorator that registers a metric function under `name`."""
    def decorator(func: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"metric registered twice: {name}")
        if any(spec.formula_id == formula_id for spec in REGISTRY.values()):
            raise ValueError(f"formula_id used twice: {formula_id}")
        REGISTRY[name] = MetricSpec(name, formula_id, unit, expression, description, applicable,
                                    kind, inputs, prior_inputs, annual_only, func)
        return func
    return decorator


def is_applicable(metric_name: str, sector_type: str) -> bool:
    return sector_type in REGISTRY[metric_name].applicable


def not_applicable_reason(sector_type: str) -> str:
    """Text shown instead of a number, e.g. 'N/A (not meaningful for banks)'."""
    return f"N/A (not meaningful for {SECTOR_LABELS.get(sector_type, sector_type)})"


def specs(kind: str) -> list[MetricSpec]:
    return [spec for spec in REGISTRY.values() if spec.kind == kind]
