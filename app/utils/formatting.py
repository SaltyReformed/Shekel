"""
Shekel Budget App -- Formatting Utilities

Helpers for converting between display and storage representations.
"""

from decimal import Decimal

_HUNDRED = Decimal("100")


def pct_to_decimal(value):
    """Convert a percentage value to its decimal representation.

    Converts user-facing percentage inputs (e.g. 6.5 for 6.5%) to
    the decimal form used in storage and calculations (0.065).

    Args:
        value: A numeric value (str, float, Decimal, or int).

    Returns:
        Decimal -- the value divided by 100.
    """
    return Decimal(str(value)) / _HUNDRED
