"""Shared setup and target-row helpers for the carry-forward service.

Both the mutating path (``carry_forward_unpaid`` in ``_execute``) and the
read-only path (``preview_carry_forward`` in ``_preview``) start from the
same validated periods and three-way-partitioned source rows, produced
once here so the two paths can never diverge.  The envelope target-row
lookup and the "finalised target" reasoning also live here so the
preview (which predicts) and the execution (which acts) reason about the
target canonical row from a single source of truth.
"""

import enum
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from app.exceptions import NotFoundError
from app.extensions import db
from app.models.transaction import Transaction
from app.services.cash_ledger import (
    AmountBasis,
    amount_basis,
    resolve_transaction_amount,
)
from app.services.generation_schedule import GenerationSchedule
from app.utils.balance_predicates import is_projected_clause


@dataclass(frozen=True)
class _CarryForwardContext:  # pylint: disable=too-many-instance-attributes
    """Internal: validated periods + partitioned source rows.

    Built by ``_build_carry_forward_context``; consumed by both
    ``carry_forward_unpaid`` and ``preview_carry_forward`` so the two
    paths see exactly the same partition (DRY: the partition logic
    lives once).

    Pylint: ``too-many-instance-attributes`` (9/7) -- these nine ARE one
    carry-forward request: the two validated periods, who and which scenario
    they belong to, the three-way partition of the rows to move, the
    pay-period schedule the envelope branch resolves against, and the amount
    basis it prices through.  Splitting them
    would put one request's facts in two objects both paths must then keep in
    step, which is the coupling this value exists to remove.  Mirrors the
    :class:`~app.services.recurrence.ResolvedRecurrence` precedent.
    """

    source_period: object  # DerivedPeriod
    target_period: object  # DerivedPeriod
    user_id: int
    scenario_id: int
    shadow_txns: List[Transaction]
    envelope_txns: List[Transaction]
    discrete_txns: List[Transaction]
    # The owner's pay-period schedule, its write window narrowed to the ONE
    # target period, resolved once for the whole request (plan step R4b-1).
    # Every envelope row asks the recurrence engine the same question about
    # the same target, and building this per row would repeat the schedule
    # query -- and the forward occurrence walk -- once per row.
    schedule: object  # GenerationSchedule
    # The request's amount basis (plan step X-au-c2b), for the same reason and
    # on the same terms: a rollover's leftover is the source row's BUDGET minus
    # what was spent, and its top-up adds that to the target's, so both ends
    # read what the row's amount RESOLVES to rather than the column a derived
    # row does not carry.  Pinned to ``user_id`` / ``scenario_id`` above, so it
    # is those two facts in the form the amount model takes them.
    basis: AmountBasis


def _build_carry_forward_context(source_period_id, target_period_id,
                                 scenario_id, calendar):
    """Validate periods, query projected source rows, three-way partition.

    Pure read-only setup shared by ``carry_forward_unpaid`` (mutating)
    and ``preview_carry_forward`` (read-only).  Raises ``NotFoundError``
    if either period is missing or not the calendar owner's -- both
    callers want the same security response (404 at the route layer).

    The same-period short-circuit (``source == target``) returns an
    empty partition so callers can no-op cleanly without special
    casing in the loops.

    **Both periods are ANSWERED BY THE CALENDAR, and that is what makes the
    ownership check structural** (pay-calendar plan step C2-f3c).  Each was a
    ``db.session.get(PayPeriod, ...)`` followed by a hand-written
    ``row.user_id != user_id`` comparison -- correct, but a comparison a later
    edit could drop, and one this module had to remember to write twice.  A
    calendar is one owner's whole schedule and nothing else, so a period id
    belonging to anybody else is simply not in it: "no such period" and "not
    yours" are the same answer here because they are the same question, which
    is the project's 404-for-both security rule expressed as a lookup rather
    than as a guard.  It also drops two queries from every carry-forward
    render.

    Args:
        source_period_id: pay_period.id to carry forward FROM.
        target_period_id: pay_period.id to carry forward TO.
        scenario_id: scenario filter (mirrors the mutating path).
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, derived once by
            the route.  It carries the owner, so no ``user_id`` rides beside
            it -- two spellings of one fact with nothing reconciling them is
            the shape ruling P54 rejected.

    Returns:
        _CarryForwardContext.

    Raises:
        NotFoundError: if either period is missing or not the calendar
            owner's.
    """
    user_id = calendar.user_id

    source = calendar.period_by_id(source_period_id)
    if source is None:
        raise NotFoundError(f"Source pay period {source_period_id} not found.")

    target = calendar.period_by_id(target_period_id)
    if target is None:
        raise NotFoundError(f"Target pay period {target_period_id} not found.")

    # ONE schedule for the whole request, its window narrowed to the target
    # period the envelope rollovers write into (plan step R4b-1).  Every
    # envelope row asks the recurrence engine the same question about the same
    # target; building this per row would repeat the forward occurrence walk
    # once per row.  It is built from the calendar the route already derived,
    # so the request holds ONE derivation of the owner's schedule rather than
    # the two plan ledger row **P68** measured.
    schedule = GenerationSchedule.for_period_ids(calendar, {target.period_id})
    # ONE basis for the whole request, on the same terms as the schedule above:
    # every envelope row is priced against the same owner and scenario, and it
    # resolves nothing until the first row asks.
    basis = amount_basis(user_id, scenario_id)

    if source_period_id == target_period_id:
        return _CarryForwardContext(
            source_period=source,
            target_period=target,
            user_id=user_id,
            scenario_id=scenario_id,
            shadow_txns=[],
            envelope_txns=[],
            discrete_txns=[],
            schedule=schedule,
            basis=basis,
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
        schedule=schedule,
        basis=basis,
    )


def _target_canonical_rows(source_txn, target_period_id, scenario_id, *,
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
        target_period_id: The ``budget.pay_periods.id`` the canonical lives
            in.  An id rather than a period since pay-calendar plan step
            C2-f3c: it is the only thing this ever read off one.
        scenario_id: Scenario filter for the lookup.
        include_deleted: When False, soft-deleted rows are excluded in
            SQL (the mutating path); when True, they are returned so the
            caller can inspect them (the preview path).

    Returns:
        A list of matching Transaction rows.
    """
    query = db.session.query(Transaction).filter(
        Transaction.template_id == source_txn.template_id,
        Transaction.pay_period_id == target_period_id,
        Transaction.scenario_id == scenario_id,
    )
    if not include_deleted:
        query = query.filter(Transaction.is_deleted.is_(False))
    return query.all()


def _is_finalised(target_row):
    """True if *target_row* cannot receive a rollover bump.

    A row is finalised when it has no status or an immutable one (Paid,
    Received, Credit, Cancelled).  Bumping it would silently
    override the user's prior status decision, so both the preview
    (blocks) and the mutating path (raises) gate on this one rule.
    """
    return target_row.status is None or target_row.status.is_immutable


class _TargetKind(enum.Enum):
    """Where an envelope rollover's unspent leftover lands in the target.

    Computed once by :func:`_classify_leftover_target` (read-only) and
    consumed by both the preview (to describe) and the execution (to
    act), so the destination decision lives in exactly one place and the
    two paths can never drift.
    """

    TOP_UP = "top_up"        # exactly one mutable row exists -> bump it
    GENERATE = "generate"    # empty + active template -> engine creates canonical
    CREATE = "create"        # no usable row -> create a fresh override row
    AMBIGUOUS = "ambiguous"  # >1 mutable row -> refuse (corrupt state)


@dataclass(frozen=True)
class _TargetResolution:
    """The destination decision for one envelope leftover rollover.

    Read-only output of :func:`_classify_leftover_target`.

    Attributes:
        kind: One of the :class:`_TargetKind` outcomes.
        row: The row to bump -- set only for ``TOP_UP``; ``None``
            otherwise.
        base: The destination row's pre-bump amount -- what the existing row's
            amount RESOLVES to for ``TOP_UP`` (plan step X-au-c2b; it was that
            row's ``estimated_amount`` COLUMN, which a derived row does not
            carry), the template's ``default_amount`` for ``GENERATE``, and
            ``Decimal("0")`` for ``CREATE`` (a fresh row starts empty and the
            caller's bump folds the leftover on top).  ``None`` for
            ``AMBIGUOUS``.
    """

    kind: _TargetKind
    row: Optional[Transaction] = None
    base: Optional[Decimal] = None


def _classify_leftover_target(source_txn, target_period, basis, schedule):
    """Decide where an envelope leftover lands in the destination period.

    Pure read-only classification shared by ``carry_forward_unpaid``
    (which acts on the result) and ``preview_carry_forward`` (which
    describes it), so the destination decision lives in one place and the
    two paths can never diverge.

    The rule -- find a *mutable* (still-Projected) destination row to top
    up; if none exists, create one:

      * ``AMBIGUOUS`` -- more than one mutable row matches ``(template,
        period, scenario)``.  The partial unique index already prevents
        two non-override canonicals, so this is a corrupt pre-existing
        state; the caller refuses rather than guess which open row to
        credit.
      * ``TOP_UP`` -- exactly one mutable row exists; bump it.
      * ``GENERATE`` -- no rows at all and the template is active in the
        destination; the recurrence engine would create the canonical,
        which the caller then bumps.
      * ``CREATE`` -- otherwise (inactive template, only finalised rows,
        or only soft-deleted rows); the caller creates a fresh override
        row carrying the leftover.

    Uses ``recurrence_engine.can_generate_in_period`` (a read-only
    predictor) rather than ``generate_for_template`` (which would create
    rows), so calling this never has a side effect.

    Args:
        source_txn: The envelope source row being carried forward; its
            ``template`` / ``template_id`` drive the lookup.
        target_period: The destination
            :class:`~app.services.pay_calendar.DerivedPeriod`.
        basis: The request's :class:`~app.services.cash_ledger.AmountBasis`;
            its ``scenario_id`` filters the lookup and the recurrence-engine
            prediction, and it prices the TOP_UP row's pre-bump base.
        schedule: The request's
            :class:`~app.services.generation_schedule.GenerationSchedule`
            (``ctx.schedule``), threaded so the prediction resolves against the
            owner's schedule without re-reading it once per envelope row.

    Returns:
        _TargetResolution describing the destination decision.
    """
    # Pylint: ``import-outside-toplevel`` -- deferred import:
    # recurrence_engine and carry_forward_service sit at the same service
    # layer; the deferred form documents the one-way dependency
    # (carry-forward depends on recurrence-engine, never the reverse).
    from app.services import recurrence_engine  # pylint: disable=import-outside-toplevel

    all_rows = _target_canonical_rows(
        source_txn, target_period.period_id, basis.scenario_id,
        include_deleted=True,
    )
    non_deleted = [r for r in all_rows if not r.is_deleted]
    mutable = [r for r in non_deleted if not _is_finalised(r)]

    if len(mutable) > 1:
        return _TargetResolution(_TargetKind.AMBIGUOUS)
    if len(mutable) == 1:
        return _TargetResolution(
            _TargetKind.TOP_UP, row=mutable[0],
            base=resolve_transaction_amount(mutable[0], basis),
        )
    if (not non_deleted
            and source_txn.template is not None
            and recurrence_engine.can_generate_in_period(
                source_txn.template, target_period.period_id,
                basis.scenario_id, schedule=schedule,
            )):
        return _TargetResolution(
            _TargetKind.GENERATE, base=source_txn.template.default_amount,
        )
    return _TargetResolution(_TargetKind.CREATE, base=Decimal("0"))
