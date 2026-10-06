"""Analytics package.

Importing the package imports every module that registers metrics, so the
registry is complete no matter which analytics module a caller imports first.
"""

from src.analytics import ratios, returns, risk, valuation  # noqa: F401
