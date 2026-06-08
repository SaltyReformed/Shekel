"""Shared setup and target-row helpers for the carry-forward service.

Both the mutating path (``carry_forward_unpaid`` in ``_execute``) and the
read-only path (``preview_carry_forward`` in ``_preview``) start from the
same validated periods and three-way-partitioned source rows, produced
once here so the two paths can never diverge.  The envelope target-row
lookup and the "finalised target" reasoning also live here so the
preview (which predicts) and the execution (which acts) reason about the
target canonical row from a single source of truth.
"""

from dataclasses import dataclass
from typing import List

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.transaction import Transaction
from app.utils.balance_predicates import is_projected_clause


@dataclass(frozen=True)
class _CarryForwardContext:
    """Internal: validated periods + partitioned source rows.

    Built by ``_build_carry_forward_context``; consumed by both
    ``carry_forward_unpaid`` and ``preview_carry_forward`` so the two
    paths see exactly the same partition (DRY: the partition logic
    lives once).
    """

    source_period: object  # PayPeriod
    target_period: object  # PayPeriod
    user_id: int
    scenario_id: int
    shadow_txns: List[Transaction]
    envelope_txns: List[Transaction]
    discrete_txns: List[Transaction]


def _build_carry_forward_context(source_period_id, target_period_id,
                                 user_id, scenario_id):
    """Validate periods, query projected source rows, three-way partition.

    Pure read-only setup shared by ``carry_forward_unpaid`` (mutating)
    and ``preview_carry_forward`` (read-only).  Raises ``NotFoundError``
    if either period is missing or not owned by *user_id* -- both
    callers want the same security response (404 at the route layer).

    The same-period short-circuit (``source == target``) returns an
    empty partition so callers can no-op cleanly without special
    casing in the loops.

    Args:
        source_period_id: pay_period.id to carry forward FROM.
        target_period_id: pay_period.id to carry forward TO.
        user_id: defense-in-depth ownership check.
        scenario_id: scenario filter (mirrors the mutating path).

    Returns:
        _CarryForwardContext.

    Raises:
        NotFoundError: if either period is missing or not owned.
    """

    source = db.session.get(PayPeriod, source_period_id)
    if source is None or source.user_id != user_id:
        raise NotFoundError(f"Source pay period {source_period_id} not found.")

    target = db.session.get(PayPeriod, target_period_id)
    if target is None or target.user_id != user_id:
        raise NotFoundError(f"Target pay period {target_period_id} not found.")

    if source_period_id == target_period_id:
        return _CarryForwardContext(
            source_period=source,
            target_period=target,
            user_id=user_id,
            scenario_id=scenario_id,
            shadow_txns=[],
            envelope_txns=[],
            discrete_txns=[],
        )

    # Routed through ``is_projected_clause`` (D6-09 / MED-02) so the
    # source-period projected-only query, the discrete-template bulk
    # UPDATE, and the discrete-adhoc bulk UPDATE below share one
    # definition of the rule with every other Projected SQL filter.
    projected_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id == source_period_id,
            Transaction.scenario_id == scenario_id,
            is_projected_clause(Transaction),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )

    shadow_txns: List[Transaction] = []
    envelope_txns: List[Transaction] = []
    discrete_txns: List[Transaction] = []
    for txn in projected_txns:
        if txn.transfer_id is not None:
            shadow_txns.append(txn)
        elif txn.template is not None and txn.template.is_envelope:
            # Envelope ROLLOVER folds the unspent leftover into the
            # template's next-period canonical (created via
            # recurrence_engine.generate_for_template).  An ad-hoc
            # envelope row (is_envelope set, no template) has no next
            # canonical, so it intentionally falls through to the
            # discrete bucket and moves whole, carrying its entries.
            # Keep this check template-gated -- do NOT switch it to
            # txn.tracks_purchases.
            envelope_txns.append(txn)
        else:
            discrete_txns.append(txn)

    return _CarryForwardContext(
        source_period=source,
        target_period=target,
        user_id=user_id,
        scenario_id=scenario_id,
        shadow_txns=shadow_txns,
        envelope_txns=envelope_txns,
        discrete_txns=discrete_txns,
    )


def _target_canonical_rows(source_txn, target_period, scenario_id, *,
                           include_deleted):
    """Return the target period's rows for *source_txn*'s template+scenario.

    Single-sources the ``(template_id, pay_period_id, scenario_id)``
    target lookup so the preview and the execution can never query
    different rows.  The preview path passes ``include_deleted=True``
    (it must distinguish "no row at all" from "only soft-deleted rows",
    a known engine-skip condition); the mutating path passes
    ``include_deleted=False`` (it bumps a live row).

    Args:
        source_txn: The source transaction; its ``template_id`` is read.
        target_period: The PayPeriod the canonical lives in.
        scenario_id: Scenario filter for the lookup.
        include_deleted: When False, soft-deleted rows are excluded in
            SQL (the mutating path); when True, they are returned so the
            caller can inspect them (the preview path).

    Returns:
        A list of matching Transaction rows.
    """
    query = db.session.query(Transaction).filter(
        Transaction.template_id == source_txn.template_id,
        Transaction.pay_period_id == target_period.id,
        Transaction.scenario_id == scenario_id,
    )
    if not include_deleted:
        query = query.filter(Transaction.is_deleted.is_(False))
    return query.all()


def _is_finalised(target_row):
    """True if *target_row* cannot receive a rollover bump.

    A row is finalised when it has no status or an immutable one (Paid,
    Received, Settled, Credit, Cancelled).  Bumping it would silently
    override the user's prior status decision, so both the preview
    (blocks) and the mutating path (raises) gate on this one rule.
    """
    return target_row.status is None or target_row.status.is_immutable


def _target_status_label(target_row):
    """The target row's status name, or ``"<unset>"`` when status is None."""
    return (
        target_row.status.name
        if target_row.status is not None
        else "<unset>"
    )
