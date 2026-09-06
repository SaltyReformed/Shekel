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
from app.services.cash_ledger import AmountBasis, resolve_transaction_amount
from app.utils.money import ZERO

from ._types import Surprise, Surprises

# Longest surprises list the rail shows, the ranked-rail top-N convention.
_MAX_SURPRISES = 5


def _build_surprises(
    txns: list[Transaction], basis: AmountBasis,
) -> Surprises:
    """Build the estimate-surprises list and its net over the window.

    A surprise is a settled row whose resolved actual (via the shared
    :func:`spending_analysis.resolved_actual_amount` kernel) differs from its
    estimate.  The list is ranked by ``abs(delta)`` descending and capped at
    :data:`_MAX_SURPRISES`; the net sums EVERY surprise's delta so the
    headline reflects the whole window, not just the shown rows.

    Args:
        txns: The window's settled expenses.
        basis: The read pass's :class:`~app.services.cash_ledger.AmountBasis`,
            built once by the caller.  It is REQUIRED rather than optional
            because a row whose plan is derived cannot be priced without one,
            and an optional basis would put the refusal back one branch later.

    Returns:
        The :class:`Surprises` (capped rows + full net).
    """
    surprises: list[Surprise] = []
    net = ZERO
    for txn in txns:
        actual = spending_analysis.resolved_actual_amount(txn)
        # The ESTIMATE half, through the amount model's ONE plan producer.
        # It read ``owned_amount``, the cheap accessor that answers a row's own
        # ``estimated_amount`` column and REFUSES a row whose plan is derived --
        # chosen deliberately as a tripwire, so that the first cutover to point
        # a derived row at this list would fail loudly rather than subtract a
        # ``None``.  That tripwire FIRED: the amount-source cutovers declared
        # 934 rows derived, 116 of them settled, and this list met one on real
        # data.  Routing the reader to ``resolve_transaction_amount`` is what
        # the tripwire was for; the refusal stays where it belongs, in
        # ``owned_amount``, for the readers that genuinely own their rows.
        #
        # It reproduces what the cutovers emptied rather than re-pricing.
        # Measured against the pre-cutover figures in ``system.audit_log`` on a
        # production clone, 2026-09-05, by
        # ``tests/manual/measure_settled_derived_plan_reproduction.py``: of the
        # 116 settled rows the cutovers declared, the 78 EXPENSE ones -- 59
        # template-priced and 19 transfer shadows -- ALL reproduce their stored
        # plan, with no refusals.  The seven that differ are salary INCOME,
        # which neither query feeding this list can return (both filter
        # ``transaction_type_id`` to the expense type), and they differ because
        # ``salary.calibration_overrides`` is REPLACED in place rather than
        # effective-dated, so a past paycheck re-derives under today's
        # calibration -- findings **N-441** and **N-535**, owned by salary:S1.
        #
        # *The headline was WRONG in a first draft of this comment, which said
        # "109 of 109 settled EXPENSE rows".  109 was the count over every
        # settled declared row, income included, and stating it as an expense
        # denominator inflated the population this reader actually sees by
        # every income row that happened to agree.  An adversarial review
        # caught it by reconciling against the cutovers' own censuses.  The
        # probe now partitions by rule and by type, which is why it is a
        # committed artifact rather than a number.*
        #
        # A back-dated price version can still move an ALREADY-SETTLED row's
        # resolved plan -- the template form's "Amount effective from" accepts a
        # past date -- so this is a measurement of today's data, not an
        # invariant the schema holds.
        #
        # It is deliberately not ``owned_contribution``: that answers the
        # entered actual where there is one, which would make every surprise's
        # delta zero by construction.
        estimated = resolve_transaction_amount(txn, basis)
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
