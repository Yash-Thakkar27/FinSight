"""Number formatting. The one place display formats are defined (Section 5 of the spec).

Used by the interpretation text, the Streamlit app and the exports, so a figure
looks the same wherever it appears:

    ₹1,24,530 Cr     18.42%     24.7x     ₹1,245.30     N/A
"""

import math

NA = "N/A"
CRORE = 1e7


def is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and not math.isfinite(value))


def indian_grouping(number: int) -> str:
    """Indian digit grouping: the last three digits, then groups of two.

    12453000 -> '1,24,53,000'
    """
    sign = "-" if number < 0 else ""
    digits = str(abs(number))
    if len(digits) <= 3:
        return sign + digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups + [tail])


def format_crore(value_inr) -> str:
    """Absolute INR as whole ₹ crore: 1.2453e12 -> '₹1,24,530 Cr'."""
    if is_missing(value_inr):
        return NA
    crore = round(value_inr / CRORE)
    return f"{'-' if crore < 0 else ''}₹{indian_grouping(abs(crore))} Cr"


def format_price(value) -> str:
    """A rupee price to two decimals: 1245.3 -> '₹1,245.30'."""
    if is_missing(value):
        return NA
    paise = round(abs(value) * 100)
    sign = "-" if value < 0 and paise else ""
    return f"{sign}₹{indian_grouping(paise // 100)}.{paise % 100:02d}"


def format_pct(fraction, decimals: int = 2) -> str:
    """A fraction as a percentage: 0.1842 -> '18.42%'."""
    return NA if is_missing(fraction) else f"{fraction * 100:.{decimals}f}%"


def format_multiple(value, decimals: int = 1) -> str:
    """A valuation multiple: 24.68 -> '24.7x'."""
    return NA if is_missing(value) else f"{value:.{decimals}f}x"


def format_ratio(value, decimals: int = 2) -> str:
    return NA if is_missing(value) else f"{value:.{decimals}f}"


def format_metric(value, unit: str) -> str:
    """Format a metric value by its registered unit."""
    if unit == "pct":
        return format_pct(value, 1)
    if unit == "multiple":
        return format_multiple(value)
    if unit == "INR":
        return format_crore(value)
    return format_ratio(value)
