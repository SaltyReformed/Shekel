"""Estimate surprises: the settled rows whose actual differed from the plan.

The one real signal the retired Variance tab carried, reused here through the
shared :func:`app.services.spending_analysis.resolved_actual_amount` kernel so
"what did this row actually cost" has one definition across the analytics
surfaces.

Boundary discipline: no Flask import, no query -- it reduces rows the window
module already loaded.  All money is ``Decimal``.
"""

from app.models.transaction import Transaction
from app.services import spending_analysis
from app.services.row_valuation import owned_amount
from app.utils.money import ZERO

from ._types import Surprise, Surprises

# Longest surprises list the rail shows, the ranked-rail top-N convention.
_MAX_SURPRISES = 5


def _build_surprises(txns: list[Transaction]) -> Surprises:
    """Build the estimate-surprises list and its net over the window.

    A surprise is a settled row whose resolved actual (via the shared
    :func:`spending_analysis.resolved_actual_amount` kernel) differs from its
    estimate.  The list is ranked by ``abs(delta)`` descending and capped at
    :data:`_MAX_SURPRISES`; the net sums EVERY surprise's delta so the
    headline reflects the whole window, not just the shown rows.

    Args:
        txns: The window's settled expenses.

    Returns:
        The :class:`Surprises` (capped rows + full net).
    """
    surprises: list[Surprise] = []
    net = ZERO
    for txn in txns:
        actual = spending_analysis.resolved_actual_amount(txn)
        # The ESTIMATE half, through the accessor that asserts this window is
        # settled-only (plan step X-au-c2b).  It read ``estimated_amount``, the
        # COLUMN; ``owned_amount`` is the same figure for a row that owns it
        # and a REFUSAL for one that does not, so a later cutover pointing a
        # derived row at this list fails loudly instead of subtracting a
        # ``None``.  It is deliberately not ``owned_contribution``: that
        # answers the entered actual where there is one, which would make every
        # surprise's delta zero by construction.
        estimated = owned_amount(txn)
        delta = actual - estimated
        if delta == ZERO:
            continue
        group_name, item_name = spending_analysis.category_names(txn)
        surprises.append(Surprise(
            transaction_id=txn.id,
            name=txn.name,
            group_name=group_name,
            item_name=item_name,
            estimated=estimated,
            actual=actual,
            delta=delta,
        ))
        net += delta

    # **The worst of the three unstable ranks, because a CAP follows it**
    # (finding **P74**, developer ruling 2026-08-25).  Keyed on ``abs(delta)``
    # alone, two rows that missed their estimate by the same amount were
    # separated only by the order ``query_settled_expenses`` happened to
    # return them in -- which carries no ``ORDER BY`` -- so at the boundary the
    # database decided WHICH ROW IS ON THE SCREEN, not merely in what order.
    # ``transaction_id`` is the row's identity, so the five shown are now a
    # function of the data.  ``net`` is unaffected either way: it sums every
    # surprise, not the shown ones.
    surprises.sort(key=lambda s: (-abs(s.delta), s.transaction_id))
    return Surprises(rows=surprises[:_MAX_SURPRISES], net=net)
