"""
Shekel Budget App -- Effective-dated resolution

The ONE "which row is in effect on a day" walk for a series of rows that
each carry an ``effective_date`` and supersede one another: the row with the
greatest ``effective_date`` at or before the day, and ``None`` before the
earliest row (the series did not exist yet).  Escrow line versions
(:mod:`app.services.escrow_calculator`) and a card's APR rows
(:mod:`app.services.card_apr`) both resolve through it; it was spelled in
each until plan step credit_card:CC-3's review found the second copy (rule 14:
one walk).  Two more spellings of the family remain, each with a fallback of
its own -- :func:`app.services.rate_period_engine._rate_at_date` (a base
rate before the first row) and
:func:`app.services.template_amount_service._version_in_effect` (the series
holds FLAT before its first row) -- and folding them onto this leaf is
ledger row **BAL-524**'s, owned by plan step ``balance:X-ct`` (filed
2026-09-18).

A leaf: the standard library only.
"""

from collections.abc import Iterable
from datetime import date
from typing import TypeVar

#: Any row carrying an ``effective_date``; the caller's type comes back.
Row = TypeVar("Row")


def in_effect_on(rows: Iterable[Row], on_date: date) -> Row | None:
    """Return the row in effect on *on_date*, or ``None`` before the first.

    Args:
        rows: Rows each exposing an ``effective_date``, in any order.
        on_date: The day to resolve.

    Returns:
        The row with the greatest ``effective_date <= on_date``; ``None``
        when no row is dated on or before *on_date*.
    """
    candidates = [row for row in rows if row.effective_date <= on_date]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.effective_date)
