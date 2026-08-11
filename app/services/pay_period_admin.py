"""
Shekel Budget App -- Pay Period Admin Service

The structural / destructive pay-period operations -- the lock
classifier and extend / truncate / regenerate -- kept out of the heavily
imported read/generate ``pay_period_service`` so the destructive paths
live in one isolated place.  Flask-isolated: takes and returns plain
data, never imports ``request`` / ``session``; flushes / bulk-deletes,
never commits (the route owns the transaction).

**It DECIDES; it does not write** (plan step C3-b).  Every row this
module adds or removes goes through
:mod:`app.services.pay_period_write`, the one place in ``app/`` that
changes ``budget.pay_periods``, so the rule that a stored ``end_date``
/ ``period_index`` equals the derivation over the owner's paydays has a
single home.  What stays here are the two gates and the orchestration:
which periods may go (the lock classifier and the discard count), what
is repopulated afterwards, and which reconciles a wipe owes.

The gates' foundation is the reusable **lock classifier** in
``pay_period_locks``: the one place that decides whether a pay period
may be deleted or rebuilt.  Truncate and regenerate consult it before
touching anything; the settings UI renders its result as a per-period
lock badge.
"""

import logging
from dataclasses import replace
from datetime import date, timedelta
from operator import attrgetter

from sqlalchemy import or_

from app.exceptions import (
    PayPeriodDiscardRequired,
    PayPeriodLocked,
    PayPeriodResetBlocked,
    PayPeriodUnresolved,
    ValidationError,
)
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import (
    account_posting_service,
    loan_posting_service,
    pay_period_service,
    pay_period_write,
    pay_schedule_service,
    user_write_lock,
)
from app.services._recurrence_common import log_resource_access_denied
from app.services.pay_period_locks import classify_periods_bulk
from app.services.period_population import populate_periods_from_active_templates
from app.services.recurrence import (
    calendar_for,
    reauthor_rule,
    recurrence_spec,
)
from app.utils.balance_predicates import is_projected_clause, settled_status_ids
from app.utils.log_events import (
    ACCESS,
    EVT_RESOURCE_NOT_FOUND,
    log_event,
)

logger = logging.getLogger(__name__)


def extend_pay_periods(user_id, num_periods):
    """Append ``num_periods`` pay periods to the end of the user's schedule.

    Tail-append only: the new paydays fall after every existing one, so the
    ``period_index == calendar-order`` invariant the balance resolver relies on
    is preserved (only tail-append and tail-truncate do).
    :func:`~app.services.pay_period_write.record_paydays` creates the new
    periods EMPTY -- it does not run the recurrence engine -- so they are then
    repopulated with each active template's recurring rows.

    **It takes no cadence, and that is finding P29's fix** (plan step C3-b).
    ``cadence_days`` was an accepted parameter, forwarded from a Marshmallow
    field the extend card renders NO control for, so a direct POST could
    generate 7-day paychecks while ``budget.pay_schedule`` still said 14 --
    after which ``resolve_cadence``, the derived horizon and the next rolling
    top-up all used 14.  Extend CONTINUES an existing schedule, so the cadence
    is not a question it gets to ask: it reads the stored one.  The finding
    closes by the state becoming unreachable rather than by adding a write,
    which is what finding **P30** objected to.

    **The next payday is the latest PAYDAY plus the cadence**, not the latest
    ``end_date`` plus a day.  The two are equal once the derivation is
    materialised (plan step C3-b) and the payday spelling is the one that
    survives plan step C4, which drops the column the other reads.

    Args:
        user_id: The owning user's id.
        num_periods: How many periods to append (>= 1; the route's
            schema validates the range).

    Returns:
        The list of newly created :class:`~app.models.pay_period.PayPeriod`
        objects.

    Raises:
        ValidationError: When the user has no existing periods to extend
            from (they must generate first), or when ``record_paydays``
            refuses the batch.
    """
    # Serialize against concurrent structural mutations for this user so the
    # latest payday is read under the lock and the append cannot race another
    # extend / top-up into a duplicate payday.  ``uq_pay_periods_user_start``
    # is the hard guard; the lock keeps the racing loser from hitting it as a
    # 500.
    user_write_lock.lock_user_writes(user_id)

    existing = pay_period_service.get_all_periods(user_id)
    if not existing:
        raise ValidationError(
            "Generate your first pay-period schedule before extending it."
        )

    # Not ``None`` here: ``resolve_cadence`` answers ``None`` only for an owner
    # with no periods at all, which the refusal above has already excluded.
    cadence_days = pay_schedule_service.resolve_cadence(user_id)
    latest_payday = max(period.start_date for period in existing)
    new_periods = pay_period_write.record_paydays(
        user_id,
        latest_payday + timedelta(days=cadence_days),
        num_periods,
        cadence_days,
    )
    populate_periods_from_active_templates(user_id, new_periods)
    return new_periods


def truncate_pay_periods(
    user_id: int, keep_through_period_id: int, confirm_discard: bool = False,
) -> int:
    """Delete the schedule tail beyond the period *keep_through_period_id* names.

    **The public wire door, and it takes an ``id`` rather than an ordinal**
    (plan step C3-a, finding **P13**).  This parameter was
    ``keep_through_index``, a ``period_index`` the form posted as an
    ``<option value>`` and the discard-confirm 422 echoed into a re-submittable
    hidden field -- a user-supplied POSITION selecting which periods a CASCADE
    destroyed, across a browser round trip.  Safe only while nothing renumbers:
    from plan step C3-b (both columns materialised from the payday list) or C6
    (mid-schedule insert), an ordinal read in an earlier request names a
    DIFFERENT period than the user reviewed and takes its transactions, its
    transfers with both shadows, and its journal entries.  ``user_write_lock``
    cannot help -- the stale value crossed a REQUEST boundary, not a concurrent
    one.  Identity is ``id``, so the wire key is ``id``.

    Removes every pay period whose PAYDAY falls after the named period's
    (tail-truncate preserves the index==calendar invariant; only tail ops do).
    Two gates protect real data, checked in order before anything is deleted:

      1. **Hard locks (not overridable).** If any to-delete period is
         historical, holds a settled transaction, carries an unbalanced
         ledger account, or is a recurrence anchor, raise
         :class:`PayPeriodLocked` and delete nothing.
      2. **Discard gate (overridable).** If any to-delete period holds a
         row regeneration cannot reproduce -- hand-entered, override, or
         Credit/Cancelled -- and ``confirm_discard`` is False, raise
         :class:`PayPeriodDiscardRequired` and delete nothing.

    Deletion is a single bulk ``DELETE`` so PostgreSQL performs the whole
    cascade in one pass: transactions and transfers (and both shadows,
    preserving the transfer invariant) go, with
    ``recurrence_rules.start_period_id`` set NULL; DB-level audit triggers
    still fire.  **Balance ASSERTIONS do NOT go** -- ruling R-EO deleted
    ``account_anchor_history.pay_period_id``, so a schedule operation can
    no longer destroy the record of what the bank said.  The statement lives in
    :func:`~app.services.pay_period_write.retire_paydays` since plan step C3-b;
    the two gates below are what THIS door contributes.

    **Shortening the schedule can leave a settled row's cash day outside it,
    and that is ACCEPTED** (developer ruling 2026-08-11, which deleted the rule
    that refused it).  Removing the tail drops the new last period back to its
    cadence projection, so a row filed in a surviving period but settled in the
    days that projection gives up keeps counting against its paycheck while no
    column holds the day its money moved.  ``_cash_periods`` reports that as the
    ``period_timing`` remainder ruling R-DH split out for it, every column's
    identity stays exact, and the balance is right either way -- on the new last
    ``end_date`` the bank genuinely had not taken the money yet.

    **The named period is always KEPT, so THIS DOOR can never empty a
    schedule**, which three docstrings elsewhere rest on.  "This door" rather
    than "truncate", precisely: the ordinal form protected the ROUTE with a
    Marshmallow floor of zero, while the service beneath it emptied schedules
    routinely -- ``_regenerate_keep_through_index`` answered ``-1``, and
    :func:`_gate_deletable_tail` still selects every period when regenerate
    hands it
    ``None``.  The guarantee now rests on the resolve below instead: an id must
    name one of this owner's periods, and that period is on the KEEP side of
    the comparison by construction.

    Args:
        user_id: The owning user's id.
        keep_through_period_id: The ``budget.pay_periods.id`` of the last
            period to KEEP.  Must name one of *user_id*'s own periods.
        confirm_discard: When True, proceed past the discard gate (the
            user has acknowledged the loss).  Hard locks are never
            bypassed.

    Returns:
        The number of pay periods deleted (0 when the named period is
        already the last one -- an idempotent no-op).

    Raises:
        PayPeriodUnresolved: *keep_through_period_id* names no pay period of
            *user_id*'s.  **"No such period" and "not your period" raise the
            SAME message deliberately**, so a caller cannot use this door to
            learn whether another owner's id exists.  A stale id -- one the
            browser held from before a concurrent truncate -- lands here too,
            and refusing it is the point: the alternative is deleting a tail
            the user never reviewed.
        PayPeriodLocked: A to-delete period is hard-locked.
        PayPeriodDiscardRequired: A to-delete period holds unrecoverable
            rows and ``confirm_discard`` is False.
    """
    # Serialize against concurrent structural mutations so the resolve, the
    # classify and the bulk DELETE see one consistent set -- closes the
    # classify-then-DELETE TOCTOU against another extend / top-up /
    # truncate for this user.
    user_write_lock.lock_user_writes(user_id)

    periods = pay_period_service.get_all_periods(user_id)
    kept = next(
        (p for p in periods if p.id == keep_through_period_id), None,
    )
    if kept is None:
        _log_unresolved_period(user_id, keep_through_period_id)
        raise PayPeriodUnresolved(keep_through_period_id)
    return pay_period_write.retire_paydays(
        user_id, periods, _gate_deletable_tail(periods, kept, confirm_discard),
    )


def _log_unresolved_period(user_id: int, period_id: int) -> None:
    """Emit the F-144 access-denied trail for an id that resolved to nothing.

    **A new ownership-failure branch owes an ACCESS event**, which is the
    invariant ``utils.auth_helpers`` states for its own doors ("every
    ownership-failure branch in this module emits a structured ``log_event`` so
    probing patterns surface in dashboards and SOC alerting") and which plan
    step C3-a's first cut left silent: an authenticated owner sweeping ids
    through ``POST /pay-periods/truncate`` was correctly refused every time and
    recorded nowhere.

    The two branches ``get_or_404`` distinguishes are distinguished HERE too,
    and for its reasons: a missing pk is common in normal use -- the
    discard-confirm panel re-posts an id a concurrent truncate has since
    deleted -- so it is INFO, while a pk owned by somebody else is the IDOR
    signal and is WARNING.  **Splitting the LOG is not an oracle**: the caller
    raises one message for both, and an event goes to the log rather than to
    the response.

    ``get_or_404`` itself cannot be reused: it reads ``current_user`` and
    ``request.path``, and this module may not import Flask.  The cross-user
    half therefore goes through ``_recurrence_common.log_resource_access_denied``,
    the Flask-free helper built for exactly this shape, rather than through a
    second copy of its fixed keyword set.

    Args:
        user_id: The requesting owner -- never the row's owner.
        period_id: The submitted ``budget.pay_periods.id`` that resolved to
            none of *user_id*'s periods.
    """
    owner_id = (
        db.session.query(PayPeriod.user_id)
        .filter(PayPeriod.id == period_id)
        .scalar()
    )
    if owner_id is None:
        log_event(
            logger, logging.INFO,
            EVT_RESOURCE_NOT_FOUND, ACCESS,
            "Pay-period truncate named a non-existent primary key",
            user_id=user_id, model="PayPeriod", pk=period_id,
        )
        return
    log_resource_access_denied(
        logger, user_id=user_id, model="PayPeriod", pk=period_id,
        owner_id=owner_id,
    )


def _gate_deletable_tail(
    periods: "list[PayPeriod]",
    kept: "PayPeriod | None",
    confirm_discard: bool,
) -> "list[PayPeriod]":
    """Return the periods after *kept*, having refused if any may not go.

    The shared gate of truncate: :func:`truncate_pay_periods` reaches it with
    a period resolved from a submitted id, and :func:`regenerate_pay_periods`
    with one it computed itself.  Both refusals live here so the two callers
    cannot drift on which rows a truncate protects -- the split is plan step
    C3-a's, made because only ONE of them takes user input and so only one has
    an id to refuse.

    **It DECIDES; it does not delete** (plan step C3-b).  The removal is
    ``pay_period_write``'s, which carries it out beside whatever the same
    operation records and then re-materialises what survives.  That split is
    what lets regenerate be ONE write: the gate runs here, and the writer sees
    the operation's final payday set rather than the half-applied one an
    adversarial review caught it refusing against.  It also gets the
    re-materialisation for free -- without it a tail delete left the new last
    period holding its old successor's end (paydays
    ``[Jan 2, Jan 16, Feb 11]`` truncated through Jan 16 kept a stored end of
    Feb 10 where the derivation says Jan 29), so a THIRD gate would eventually
    have been needed to police a state the writer can simply not create.

    **The tail is defined by PAYDAY, not by ordinal**, and that is the same
    normalization the arc is about: ``start_date`` is the only fact in the row,
    ``period_index`` is derived from it, and plan step C4 drops the ordinal
    altogether.  Since plan step C3-b the two select the same rows by
    construction rather than by data: ``pay_period_write`` is the only writer
    of this table and reads the ordinal off the derivation, where it IS the
    position in payday order (0 disagreements on 61 production rows,
    2026-08-10).

    **But this function does not RELY on that, and an adversarial review of
    C3-a is why.**  Its first cut took the boundary from
    :func:`_regenerate_keep_through_period`, which picks by LIST POSITION over
    a list ``get_all_periods`` orders by ``period_index`` -- so the boundary
    was chosen in ordinal space and applied in payday space, and the two
    agreeing was an unfenced assumption about data rather than a property of
    the code.  That helper now works in payday order too, which makes the
    operation whole-cloth payday-keyed: no fence to add, and nothing for plan
    step C6's renumbering to invalidate.  This function itself is a filter and
    reads no order at all.

    Args:
        periods: The owner's periods, in any order, read under the caller's
            advisory lock.
        kept: The last period to KEEP, or ``None`` to delete every period in
            *periods*.  ``None`` is reachable only from regenerate, whose
            rebuildable tail can start at the very first period; it then
            generates a fresh schedule inside the same transaction, so no
            committed state is ever period-less.
        confirm_discard: When True, proceed past the discard gate.

    Returns:
        The periods that may be deleted, empty when *kept* is already the last.

    Raises:
        PayPeriodLocked: A to-delete period is hard-locked.
        PayPeriodDiscardRequired: A to-delete period holds unrecoverable
            rows and ``confirm_discard`` is False.
    """
    if kept is None:
        to_delete = list(periods)
    else:
        to_delete = [p for p in periods if p.start_date > kept.start_date]
    if not to_delete:
        return []

    locks = classify_periods_bulk(to_delete)
    blocking = {
        pid: reason for pid, reason in locks.items() if reason is not None
    }
    if blocking:
        # Posting-ledger protection, two layers: a period holding settled
        # (posted) transactions classifies SETTLED_TXN, and a period whose
        # journal entries do not net to zero per ledger account (a loan
        # opening / true-up correction, or attribution drift) classifies
        # LEDGER_POSTINGS -- so truncate refuses both.
        # journal_entries.pay_period_id is ON DELETE CASCADE, so deleting a
        # posted period disposes its ledger entries + legs at the DB tier
        # (outside the ORM, where the balanced-journal trigger never fires on
        # DELETE); that is safe only for a period whose postings net to zero
        # per account (a self-cancelling original + reversal pair).  Whoever
        # relaxes these locks MUST first reverse the postings
        # (posting_service.reverse_postings_before_delete / the loan sync).
        raise PayPeriodLocked(blocking)

    if not confirm_discard:
        discardable = _count_discardable_items([p.id for p in to_delete])
        if discardable > 0:
            raise PayPeriodDiscardRequired(discardable)

    return to_delete


def regenerate_pay_periods(
    user_id, new_start_date, num_periods, cadence_days, confirm_discard=False,
):
    """Rebuild the not-yet-started, unlocked tail from a corrected start.

    "Fix a mistake" without per-period date editing: truncate the
    rebuildable future tail (the first not-yet-started unlocked period
    onward), then generate a fresh ``num_periods``-long schedule from
    ``new_start_date`` at ``cadence_days`` and repopulate it with the
    active templates' recurring rows.  Periods that have already started,
    are historical, hold settled money or posted ledger entries, or anchor a
    recurrence rule are KEPT; if any such locked period sits inside the rebuildable tail the
    truncate step refuses (history cannot be rewritten under a settled
    paycheck).  The new cadence is persisted so later extends continue at
    it.

    The whole operation is one transaction the route commits: if the writer
    rejects ``new_start_date`` after the truncate has run, the route's rollback
    undoes the truncate too -- nothing partial ships.  *That rollback was a
    claim this docstring made and no route performed until plan step C3-b; an
    adversarial review found it.*  It is the route's and not this function's
    because the caller owns the transaction boundary (the module docstring's
    rule), and a service that rolled back would be deciding for a caller that
    may have staged work of its own.

    **The cadence is persisted by the writer, not here** (plan step C3-b).
    This function used to call ``upsert_schedule`` itself, unconditionally,
    which is one half of finding **P12**: a batch that created nothing still
    rewrote the forecast cadence.  ``record_paydays`` now applies the one rule
    -- a batch that RECORDED a payday sets the cadence -- so the three doors
    that had a copy of this line have none.

    Args:
        user_id: The owning user's id.
        new_start_date: First payday of the rebuilt tail.  Must fall at least
            :data:`~app.models.pay_period.MIN_MATERIALISABLE_CADENCE_DAYS`
            after the last RETAINED period's PAYDAY (re-checked by
            ``record_paydays``' forward-only rule).  It may now fall INSIDE the
            retained schedule's projected coverage, which is what makes
            "correct my cadence going forward" expressible: the old guard
            bounded on the retained ``end_date`` and so accepted only the
            single day after it.
        num_periods: How many periods to generate.
        cadence_days: Days between paydays for the rebuilt tail; also
            persisted as the user's forecast cadence, by the writer.
        confirm_discard: Forwarded to the truncate step -- when False and
            the rebuildable tail holds unrecoverable rows, raise
            :class:`PayPeriodDiscardRequired` and change nothing.

    Returns:
        The list of newly created :class:`~app.models.pay_period.PayPeriod`
        objects.

    Raises:
        PayPeriodLocked: A locked period sits inside the rebuildable tail.
        PayPeriodDiscardRequired: The tail holds unrecoverable rows and
            ``confirm_discard`` is False.
        ValidationError: ``new_start_date`` falls before the forward-only floor
            (``record_paydays``' rule).
    """
    # Serialize the whole rebuild -- boundary computation through the
    # truncate + regenerate -- for this user; re-entrant with the lock
    # ``truncate_pay_periods`` used to take before plan step C3-a split the
    # resolve off the delete, and which the generate below still relies on.
    user_write_lock.lock_user_writes(user_id)

    # The periods are read ONCE, under the lock, and threaded into both the
    # boundary computation and the delete.  Before plan step C3-a each of
    # those issued its own ``get_all_periods``, so the boundary was computed
    # against one snapshot and applied against another.
    periods = pay_period_service.get_all_periods(user_id)
    kept = _regenerate_keep_through_period(periods)
    # ONE write, and an adversarial review of plan step C3-b is why.  The
    # truncate and the rebuild used to be two calls, so everything downstream
    # saw the schedule BETWEEN them -- an interval this door then widens again.
    # The rule that measured it (the coverage rule) was deleted 2026-08-11; the
    # composition stays, because ``_write_derivation`` would otherwise
    # materialise that intermediate shape, shortening the newly-last survivor
    # to a cadence projection and logging it as a repair before undoing both.
    # The gate below still decides WHICH periods may go; the writer carries the
    # delete out beside the create so one derivation sees the end state.
    doomed = _gate_deletable_tail(periods, kept, confirm_discard)
    new_periods = pay_period_write.record_paydays(
        user_id, new_start_date, num_periods, cadence_days, retiring=doomed,
    )
    populate_periods_from_active_templates(user_id, new_periods)
    return new_periods


def can_reset_pay_periods(user_id: int) -> bool:
    """Return whether a full reset is currently offered to the user.

    The read-only UI predicate: reset is offered only when the user has no
    settled transactions, the same bound :func:`reset_pay_periods`
    enforces.  The settings page calls this to show or hide the reset
    control; the service's own gate (which raises
    :class:`~app.exceptions.PayPeriodResetBlocked`) remains the
    authoritative defense, so a stale page that posts anyway is still
    refused.

    Args:
        user_id: The owning user's id.

    Returns:
        ``True`` when the user has zero settled (non-deleted)
        transactions, else ``False``.
    """
    return _settled_transaction_count(user_id) == 0


def reset_pay_periods(user_id, new_start_date, num_periods, cadence_days):
    """Wipe and rebuild the user's WHOLE schedule, re-anchoring accounts.

    The bounded first-time-setup correction.  Unlike regenerate -- which
    rebuilds only the not-yet-started, unlocked future tail and can never
    touch the anchor period or historical periods -- reset deletes EVERY
    pay period (anchor, historical, current, future) and generates a fresh
    schedule from ``new_start_date``, then re-anchors each account onto it
    with its balance preserved.

    Bounded for safety: it refuses if the user has ANY settled
    transaction.  Once a paycheck has settled, rewriting the schedule
    under it would corrupt history, so those users use regenerate instead.

    The whole operation is ONE transaction the route commits.

    **It used to need an obstacle cleared, and the obstacle is gone.**  An
    account carried a ``NOT NULL`` FK to its anchor pay period, so a reset had
    to delete the old anchor period before it could re-point the anchor,
    leaving the FK dangling mid-transaction; the FK was declared
    ``NO ACTION DEFERRABLE INITIALLY IMMEDIATE`` (Phase 0) purely so this --
    its only caller -- could issue ``SET CONSTRAINTS ... DEFERRED``.  Ruling
    R-EH deleted the columns and ruling R-EO deleted the assertion's own pay
    period, so nothing an account owns points at a period any more: no
    deferral, no re-anchoring, and no window in which the schema is
    inconsistent.

    Steps, all in one transaction:

      1. Refuse if any settled transaction exists (delete nothing).
      2. Take the per-user advisory lock (a structural mutation, like
         extend / truncate / regenerate).
      3. Capture the recurrence rules that carry an explicit start period
         (the cascade NULLs those).  There is no balance to capture: since
         ruling R-EO the assertions do not reference a pay period and survive
         the wipe untouched.
      4. Bulk-DELETE every pay period.  PostgreSQL cascades it in one
         pass: transactions and transfers (+ both shadows, preserving the
         transfer invariant) go and the rules' ``start_period_id`` is set
         NULL; audit triggers still fire.  Anchor history is NOT in that
         cascade any more (ruling R-EO).
      5. Generate the fresh schedule from ``new_start_date``.
      6. Re-point the captured rules to the new first period; repopulate the
         new periods from the active templates.
      7. Re-sync each of the user's loans' genesis postings onto the
         rebuilt schedule (:func:`loan_posting_service.resync_user_loan_postings`).
         A loan's opening / true-up ledger entries carry a ``pay_period_id``
         and so CASCADE-delete with the wiped periods, but they exist
         independently of any settled transaction, so the zero-settled gate
         does NOT keep them; their SOURCE facts (``LoanParams`` and the
         ``user_trueup`` ``LoanAnchorEvent`` rows) survive, so this re-derives
         and re-posts them attributed to the new periods.  Then the same for
         the non-loan accounts' anchor corrections (Build-Order Step 5,
         :func:`account_posting_service.resync_user_account_anchor_postings`):
         the wipe took their correction ENTRIES, which are keyed on a pay
         period, but not the assertions those entries derive from -- so this
         re-derives every one of the user's real assertions onto the rebuilt
         schedule rather than one fabricated opening per account.
    The new cadence is persisted by step 5's writer rather than by a line of
    this function's own (plan step C3-b): ``record_paydays`` applies the one
    rule -- a batch that RECORDED a payday sets the forecast cadence -- so the
    three doors that each held a copy of that call now hold none.

    Args:
        user_id: The owning user's id.
        new_start_date: First payday of the rebuilt schedule.
        num_periods: How many periods to generate.
        cadence_days: Days between paydays for the new schedule; also
            persisted as the user's cadence, by the writer.

    Returns:
        The list of newly created :class:`~app.models.pay_period.PayPeriod`
        objects.

    Raises:
        PayPeriodResetBlocked: The user has at least one settled
            transaction; nothing is changed.
        ValidationError: ``record_paydays`` rejects the batch (an invalid
            start date or cadence).
    """
    # Reset is gated on zero settled transactions.  Build-Order Step 3 note:
    # this same gate keeps the CASH double-entry postings consistent across a
    # reset -- the wipe below deletes the user's pay periods, and
    # journal_entries.pay_period_id is ON DELETE CASCADE, so a period holding
    # settled (posted) transactions would dispose its cash journal entries + legs
    # at the DB tier (outside the ORM, where the balanced-journal trigger never
    # fires on DELETE).  Because any settled row blocks the reset entirely, no
    # SETTLED-transaction posting is ever wiped.  Whoever relaxes this gate MUST
    # first reverse those transactions' postings
    # (posting_service.reverse_postings_before_delete).
    #
    # The gate does NOT protect a LOAN's genesis postings: a loan's opening /
    # true-up entries exist without any settled transaction (a payment-less
    # configured loan posts its opening at params-create), so the wipe DOES
    # CASCADE-delete them.  That is safe because their source facts (LoanParams,
    # user_trueup LoanAnchorEvent) survive the wipe, and step 8 below re-posts
    # them onto the rebuilt schedule in this same transaction (review M2 / R7).
    settled = _settled_transaction_count(user_id)
    if settled > 0:
        raise PayPeriodResetBlocked(settled)

    # Serialize against concurrent structural mutations for this user.
    user_write_lock.lock_user_writes(user_id)

    anchored_rule_ids = _rule_ids_with_start_period(user_id)

    # Wipe ALL the user's periods (the cascade handles the dependents) and
    # build the new schedule in ONE write, so the writer derives and
    # materialises the end state rather than the period-less moment between
    # them.
    periods = pay_period_service.get_all_periods(user_id)
    new_periods = pay_period_write.record_paydays(
        user_id, new_start_date, num_periods, cadence_days, retiring=periods,
    )
    _repoint_recurrence_rules(anchored_rule_ids, new_periods[0])
    populate_periods_from_active_templates(user_id, new_periods)
    # Re-post the loan genesis (opening / true-up) corrections the period
    # CASCADE wiped: their source facts survived, so this re-derives them
    # onto the rebuilt schedule inside this transaction (review M2 / R7).
    loan_posting_service.resync_user_loan_postings(user_id)
    # Same for the NON-loan accounts' anchor corrections (Build-Order Step
    # 5): the wipe CASCADEd their opening / true-up ENTRIES with the old
    # periods, but no longer their assertions (ruling R-EO), so this re-derives
    # every real assertion's correction onto the rebuilt schedule.  Post-reset
    # is clean by construction: the zero-settled gate guarantees no posted
    # source effects survive, so each account walks to exactly the balance its
    # latest assertion declares.
    account_posting_service.resync_user_account_anchor_postings(user_id)
    return new_periods


def top_up_rolling_window(user_id, as_of=None):
    """Generate periods to keep the rolling window N ahead of today.

    The on-request continuous-mode top-up, called from the grid and
    dashboard entry points (the only routes that consume future
    periods).  No scheduler exists, so the window is refilled lazily on
    page load.

    Cheap and idempotent.  When rolling is disabled (or the user has no
    schedule row) it does ZERO write work and takes NO lock -- one tiny
    schedule read.  Otherwise it counts the current-and-future periods
    (``end_date >= as_of``, which INCLUDES the period containing
    ``as_of``, so "keep N ahead" counts the current period as one of the
    N) and, only if short of the target, takes the per-user advisory
    lock, RE-COUNTS under it (another request may have just filled the
    window), and appends exactly the deficit via
    :func:`extend_pay_periods` (which repopulates the new periods).

    Correctness against a duplicate payday comes from
    ``UNIQUE(user_id, start_date)``; the lock + re-count is the UX
    layer that lets a racing loser cleanly create nothing instead of
    hitting that constraint as a 500.

    **It passes no cadence, and that is not a saving of one argument.**  It
    used to hand ``extend_pay_periods`` the schedule row's own
    ``cadence_days``, which is exactly what ``resolve_cadence`` answers for an
    owner who has a row -- and this function returns before the append unless
    one exists.  A redundant pass-through of a value the callee re-reads is a
    second place for the two to come apart; plan step C3-b deleted the
    parameter at every door (finding **P29**).

    Args:
        user_id: The owning user's id.
        as_of: Reference date for "current and future" (defaults to
            today).

    Returns:
        The number of pay periods created (0 when rolling is disabled,
        the window is already full, or a concurrent top-up filled it
        first).
    """
    if as_of is None:
        as_of = date.today()

    schedule = pay_schedule_service.get_schedule(user_id)
    if schedule is None or not schedule.rolling_enabled:
        return 0

    target = schedule.rolling_target_periods
    if _future_period_count(user_id, as_of) >= target:
        return 0

    # A deficit exists: serialize concurrent top-ups, then re-count under
    # the lock so a request that lost the race re-reads a now-full window
    # and creates nothing.
    user_write_lock.lock_user_writes(user_id)
    deficit = target - _future_period_count(user_id, as_of)
    if deficit <= 0:
        return 0

    # No handler.  This is an opportunistic write on a READ path -- ``/grid``
    # and ``/dashboard`` call it with no handler of their own -- so anything
    # raised here is a 500 on both of the app's main screens.
    #
    # The FORWARD-ONLY floor passes by construction: the batch continues the
    # stored cadence and every payday it records falls after the last existing
    # one.  That is the only refusal this comment can prove, and a first draft
    # of it claimed all of them -- caught by an adversarial review of the
    # coverage-rule deletion, which reached the 500 by running it.
    # ``reject_unmaterialisable_batch`` still refuses a STORED cadence below
    # ``MIN_MATERIALISABLE_CADENCE_DAYS``, and ``ck_pay_schedule_cadence_range``
    # admits 1, so a legacy owner holding one 500s here on both screens,
    # permanently.  That is ledger row **pay_calendar:P33**, owned by C4 -- the
    # step that drops the stored ``end_date`` this floor exists to protect and
    # legalises a one-day cycle -- and it is NOT swallowed here meanwhile,
    # because the state is a schedule this app cannot render rather than a
    # refusal to shrug off.  The refusal that USED to reach this line (the
    # coverage rule, deleted 2026-08-11) was swallowed with a WARNING, and an
    # opportunistic writer needing a swallow was the clearest evidence that
    # rule did not belong on a read path.
    return len(extend_pay_periods(user_id, deficit))


def _future_period_count(user_id, as_of):
    """Count the user's current-and-future periods (``end_date >= as_of``).

    Includes the period containing ``as_of`` (the current period), so
    this is the count the rolling target is compared against: "keep N
    ahead" counts the current period as one of the N.

    Args:
        user_id: The owning user's id.
        as_of: The reference date.

    Returns:
        The number of periods whose ``end_date`` is on or after
        ``as_of``.
    """
    return (
        db.session.query(PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.end_date >= as_of,
        )
        .count()
    )


def _regenerate_keep_through_period(
    periods: "list[PayPeriod]",
) -> "PayPeriod | None":
    """Return the last period regenerate KEEPS, or ``None`` to keep none.

    Everything up to and including the last period that has already started or
    is locked is kept; the first NOT-YET-STARTED AND unlocked period is where
    the rebuildable tail begins, so this returns the period BEFORE it.  "Not
    yet started" is ``start_date > today`` STRICTLY: a period whose
    ``start_date == today`` is the current in-progress period
    (``get_current_period`` matches ``start_date <= today <= end_date``), so on
    a payday it is kept, not rebuilt.  When there is no rebuildable future tail
    (every period has started or is locked), it returns the LAST period -- the
    truncate is then a no-op and regenerate degrades to an append from
    ``new_start_date``.

    **It returns a PERIOD rather than an ordinal, and ``None`` rather than
    ``-1``** (plan step C3-a).  The sentinel had to be a number below every
    real ``period_index`` because the truncate it fed compared ordinals; with
    :func:`_gate_deletable_tail` comparing PAYDAYS there is no "one before the
    first payday" to name, and inventing one would be an ordinal surviving in
    a function whose whole point is that ordinals do not.

    **It walks in PAYDAY order, and an adversarial review of C3-a is why.**
    Its first cut walked ``periods`` as handed over -- which
    ``pay_period_service.get_all_periods`` orders by ``period_index`` -- and
    returned ``periods[position - 1]``, so the boundary was chosen in ORDINAL
    space while the delete that consumes it selects in PAYDAY space.  The two
    agree on every schedule this app can currently write, which made the
    disagreement an unfenced assumption about data rather than a property of
    the code; sorting here removes the assumption instead of asserting it, and
    keeps working when plan step C6 lets a payday be inserted mid-schedule.

    Args:
        periods: The owner's periods, in any order, read under the caller's
            advisory lock.  Taken as an argument rather than re-queried so the
            boundary and the delete that consumes it see one snapshot.

    Returns:
        The last :class:`~app.models.pay_period.PayPeriod` to keep, or ``None``
        when the rebuildable tail starts at the very first period (or the owner
        has no periods at all) -- both meaning "keep none of them".
    """
    if not periods:
        return None
    by_payday = sorted(periods, key=attrgetter("start_date"))
    today = date.today()
    locks = classify_periods_bulk(by_payday)
    for position, period in enumerate(by_payday):
        if period.start_date > today and locks[period.id] is None:
            return by_payday[position - 1] if position > 0 else None
    return by_payday[-1]


def _count_discardable_items(period_ids):
    """Count rows in the periods that regeneration cannot reproduce.

    A row needs the user's confirmation before truncate / regenerate
    wipes it when it is hand-entered (no template), a manual override, or
    carries a deliberate non-Projected status (Credit / Cancelled --
    settled rows are already hard-locked upstream, so they never reach
    here).  Transfer shadows always carry ``template_id IS NULL``, so the
    transaction scan excludes them (``transfer_id IS NULL``) and transfers
    are counted once on their own table via the parallel predicate
    (``transfer_template_id`` in place of ``template_id``).  That way a
    recurring transfer (regenerable) does not falsely trip the gate while
    an ad-hoc transfer does.  The not-Projected test routes through
    ``balance_predicates.is_projected_clause`` (negated) so no inline
    status-id comparison lives here (D6-09).

    Args:
        period_ids: The pay-period ids being deleted.

    Returns:
        The number of unrecoverable rows (non-shadow transactions plus
        transfers; a transfer counts once, not its two shadows).
    """
    txn_count = db.session.query(Transaction.id).filter(
        Transaction.pay_period_id.in_(period_ids),
        Transaction.is_deleted.is_(False),
        Transaction.transfer_id.is_(None),
        or_(
            Transaction.template_id.is_(None),
            Transaction.is_override.is_(True),
            ~is_projected_clause(Transaction),
        ),
    ).count()
    transfer_count = db.session.query(Transfer.id).filter(
        Transfer.pay_period_id.in_(period_ids),
        Transfer.is_deleted.is_(False),
        or_(
            Transfer.transfer_template_id.is_(None),
            Transfer.is_override.is_(True),
            ~is_projected_clause(Transfer),
        ),
    ).count()
    return txn_count + transfer_count


def _settled_transaction_count(user_id: int) -> int:
    """Count the user's non-deleted settled transactions (the reset gate).

    Scopes through :class:`PayPeriod` because ``Transaction`` carries no
    ``user_id`` of its own.  "Settled" reuses the canonical
    ``balance_predicates.settled_status_ids`` (Paid / Received / Settled)
    and excludes soft-deleted rows -- exactly how the lock classifier
    decides a period is settled, so a row that does not lock a period also
    does not block a reset.  A settled transfer is counted via its settled
    shadow transactions (transfer invariant 3: a shadow's status equals
    its parent's), so no separate transfer scan is needed.

    Args:
        user_id: The owning user's id.

    Returns:
        The number of settled, non-deleted transactions the user has.
    """
    return (
        db.session.query(Transaction.id)
        .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
        .filter(
            PayPeriod.user_id == user_id,
            Transaction.status_id.in_(settled_status_ids()),
            Transaction.is_deleted.is_(False),
        )
        .count()
    )


def _rule_ids_with_start_period(user_id: int) -> list[int]:
    """Return ids of the user's rules that carry an explicit start period.

    Captured BEFORE the wipe: the pay-period delete cascade SET-NULLs
    every rule's ``start_period_id``, so after the wipe a rule the cascade
    nulled is indistinguishable from one that was legitimately NULL (no
    explicit start).  Only the rules returned here are re-pointed to the
    new schedule's first period, so a rule that never had an explicit
    start keeps none and its generation continues to default to the first
    candidate period.

    Args:
        user_id: The owning user's id.

    Returns:
        The ids of the user's recurrence rules whose ``start_period_id``
        is currently non-NULL.
    """
    rows = db.session.query(RecurrenceRule.id).filter(
        RecurrenceRule.user_id == user_id,
        RecurrenceRule.start_period_id.isnot(None),
    ).all()
    return [row[0] for row in rows]


def _repoint_recurrence_rules(rule_ids: list[int], first_period) -> None:
    """Re-point the rules that carried a start period onto the new schedule.

    The wipe cascade SET-NULLs every rule's ``start_period_id``, so the rules
    captured by :func:`_rule_ids_with_start_period` are re-pointed at the new
    first period: a rule that had an explicit start keeps one, and that period
    correctly classifies as a ``RECURRENCE_ANCHOR``.

    **Re-authored rather than bulk-updated, and that is what re-phases them.**
    This used to be one ``query.update()`` writing ``start_period_id`` and a
    hardcoded ``offset_periods = 0``.  The zero was load-bearing for
    EVERY_N_PERIODS -- the occurrence engine's PERIOD-unit walk fires where
    ``(period_index - offset_periods) % interval_n == 0``, so re-pointing the
    start without re-phasing would fire the rule on the wrong periods -- but it
    was a hand-maintained copy of a derivation that lives in
    ``app.services.recurrence``, which computes
    ``first_period.period_index % interval_n``: the same 0, from the rule the
    zero was transcribed from.  Going through the seam means the phase is
    derived rather than transcribed.

    **The rules WITHOUT a start period are deliberately left alone**, and that
    is a change from the first cut of this function, which re-authored every
    rule the owner had.  It did so because ``anchor_date`` was a stored value
    measured partly from the schedule's OPENING PAYDAY, so a reset stranded
    every start-period-less rule on a first occurrence from the deleted
    schedule.  Plan step R2d removed the stored anchor: the two-axis view is
    computed on demand, so a reset cannot strand it.

    Exactly two columns of ``budget.recurrence_rules`` depend on the schedule
    at all.  ``start_period_id`` is NULL before and after for these rules, and
    ``offset_periods`` is a function of the start period alone -- so
    ``resolve`` returns the value already stored whenever there is no start
    period to derive from.  The wide pass therefore wrote nothing on this set,
    and the narrow one is the same behaviour stated honestly rather than a
    saving.  **It is NOT an UPDATE saving**: assigning identical values to an
    instance loaded in this transaction emits no statement at all, because
    SQLAlchemy compares each attribute against its committed state before
    building the UPDATE (measured on the pinned 2.0.49 -- 0 statements for the
    identical-reassign shape, 1 for the control that changes a value).

    What the narrowing does give up, stated because it is not nothing: the
    wide pass resolved every rule the owner had, so an unresolvable one would
    have raised.  That is a worse behaviour rather than a lost safety -- it
    would abort a schedule reset over a rule the reset does not touch.

    The schedule is loaded ONCE and threaded through every rule rather than
    re-queried per rule.  Runs before the repopulation pass, so the
    regenerated rows use the corrected phase.

    Args:
        rule_ids: The recurrence-rule ids that carried an explicit start
            period before the wipe.
        first_period: The new schedule's first
            :class:`~app.models.pay_period.PayPeriod` (index 0).
    """
    if not rule_ids:
        return
    rules = db.session.query(RecurrenceRule).filter(
        RecurrenceRule.id.in_(rule_ids),
        # Redundant against ``_rule_ids_with_start_period``'s own filter, and
        # kept: an id list is the kind of argument that acquires a second
        # caller, and the seam refuses a spec resolved against another user's
        # calendar anyway.
        RecurrenceRule.user_id == first_period.user_id,
    ).all()
    if not rules:
        return
    calendar = calendar_for(first_period.user_id)
    for rule in rules:
        reauthor_rule(
            rule,
            replace(recurrence_spec(rule), start_period_id=first_period.id),
            calendar,
        )
    db.session.flush()
