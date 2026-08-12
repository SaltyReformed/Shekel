"""
Shekel Budget App -- Transfer Service: the UPDATE verb

The one path that changes an existing transfer, and with it both shadow
:class:`~app.models.transaction.Transaction` rows.  Transfer Invariants 3-5
are maintained here: shadow amounts, statuses and periods always equal the
parent's, because every field a caller may move is mirrored in this module and
nowhere else.

Its tail is the posting reconcile (:func:`_reconcile_postings_after_update`),
which brings the double-entry ledger back in step after the kwargs land -- once
the FINAL amount and status are in place rather than at the field that moved.

Flask-isolated like the rest of the package: plain data in, ORM rows out, no
``request`` / ``session`` imports.  Flushes; does NOT commit.
"""

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from app import ref_cache
from app.enums import StatusEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.ref import Status
from app.models.transfer import Transfer
from app.services import account_posting_service
from app.services import posting_service
from app.services.transfer_service import _settle
from app.services.transfer_service._loan_posting import (
    _reject_installment_move_before_loan,
    _sync_loan_postings_if_loan,
)
from app.services.transfer_service._ownership import (
    _get_owned_category,
    _get_owned_period,
)
from app.services.transfer_service._status import (
    apply_settle_day_correction,
    apply_status_to_all_three,
)
from app.services.transfer_service._validation import (
    TransferRows,
    _validate_positive_amount,
    load_transfer_rows,
)
from app.utils.balance_predicates import enters_settled_band
from app.utils.log_events import (
    BUSINESS,
    EVT_TRANSFER_UPDATED,
    log_event,
)

logger = logging.getLogger(__name__)

# The ``update_transfer`` kwargs whose change can alter a transfer's posted
# double-entry ledger effect or its attribution, so a change to any of them
# triggers a posting reconcile (Build-Order Step 2; see
# ``posting_service.sync_transfer_postings``).  ``status_id`` flips the
# settled/unsettled target; ``amount`` (the estimated amount) and
# ``actual_amount`` together determine the settled shadow's ``effective_amount``
# (``COALESCE(actual_amount, estimated_amount)``) -- the magnitude posted;
# ``pay_period_id`` moves the entry's period, so a settled period move
# reconciles R2-correctly (the per-(account, period) reconcile reverses the old
# period and posts the new) AND fires the effect-time self-heal for the Step-5
# account-anchor corrections (F1).
#
# ``due_date`` IS here, and its inclusion is load-bearing: on a LOAN payment the
# due date is the installment the payment satisfies, which the genesis write walk
# (``loan_ledger.merge_anchor_and_payment_events``) orders
# payments by AND applies its strict ``anchor_date < due_date`` post-anchor
# boundary against -- so moving it changes which payments an anchor SUBSUMES, and
# therefore the POSTED balance.  Editing it without a reconcile would leave the
# posted ledger disagreeing with every live reader (the history rows, the payment
# table, the resolver's replay), silently, until an unrelated chokepoint happened
# to fire.  On a NON-loan transfer the cash reconcile is reconcile-to-target and
# writes nothing, so listing it costs one idempotent no-op round-trip.
#
# The remaining kwargs (``category_id`` / ``name`` / ``notes`` / ``is_override``)
# move none of these, so they raise no reconcile.  ``settled_on`` is deliberately
# NOT here: it moves no leg AMOUNT, and an unsettled transfer has no postings to
# re-date, so the set stays the cheap always-on pre-filter.  A SETTLED
# ``settled_on`` edit IS posting-relevant since step E1a -- it moves the day every
# posting counts from (the ``entry_date``, step C2's one clock) -- and
# ``_reconcile_postings_after_update`` runs the full reconcile for that case
# explicitly (the per-(period, date) reconcile re-dates the entries, finding
# N-13) plus the two endpoint accounts' anchor-correction resync (F1).  The
# reconcile is idempotent, so listing a field that did not move the effect is a
# harmless no-op; this set is the cheap pre-filter that avoids a ledger
# round-trip on a pure metadata edit.
_POSTING_RELEVANT_FIELDS = frozenset(
    {"status_id", "amount", "actual_amount", "pay_period_id", "due_date"}
)


def _apply_actual_amount(rows: TransferRows, raw: object) -> None:
    """Mirror an ``actual_amount`` update onto both shadow transactions.

    The ``Transfer`` model has no ``actual_amount`` column, so this kwarg
    updates the two shadows directly.  ``None`` clears the settled
    amount; any other value is coerced to ``Decimal`` (a parse failure
    is a caller bug -> ``ValidationError``).

    Args:
        rows: The transfer and both shadows.
        raw: The submitted actual amount (``None`` or Decimal-coercible).

    Raises:
        ValidationError: If *raw* is not None and cannot be parsed as a
            Decimal.
    """
    if raw is not None:
        try:
            actual = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError(
                f"Invalid actual_amount: {raw!r}."
            ) from exc
    else:
        actual = None
    for shadow in rows.shadows:
        shadow.actual_amount = actual


#: The kwargs a SETTLE owns outright when one runs.  Each is an input to
#: :func:`app.services.transfer_service._settle.settle`, which writes all three
#: columns itself -- so leaving them in the field-application loop below would
#: write each of them a second time.  That is not hypothetical tidiness: it is
#: exactly what this module did until plan step X-f2-c3, where every reconcile
#: tick stamped ``settled_on`` with the derived day and then rewrote it with the
#: statement's through ruling **R-ED**'s CORRECTION door.
_SETTLE_OWNED_FIELDS = frozenset({"status_id", "actual_amount", "settled_on"})


def _fields_the_settle_left(
    updates: "dict[str, object]",
) -> "dict[str, object]":
    """Return the kwargs still owed an application after a settle ran.

    :data:`_SETTLE_OWNED_FIELDS`, minus the two of them that arrived as an
    explicit ``None``.  **A settle consumes a VALUE; a ``None`` is not one --
    it is a request to CLEAR the column**, which is a different act with its own
    door, and a settle that swallowed it would perform neither.

    * ``settled_on=None`` asks for a settled transfer carrying no record of when
      its money moved.  The balance walk REFUSES such a row, so it is a 500 on
      the grid rather than a bad value; left here, it reaches
      :func:`~app.services.transfer_service._status.apply_settle_day_correction`,
      which refuses it in a sentence a user can act on.  Consumed instead, that
      designed refusal would silently become "the settle used today's date".
    * ``actual_amount=None`` says the figure a human typed was wrong.  Left
      here, :func:`_apply_actual_amount` clears both legs.  Consumed instead,
      the settle would read it as "nobody typed one" and leave a figure the
      caller had just withdrawn.

    Neither can arrive from a route: both PATCH schemas declare ``settled_on``
    non-nullable, so an empty input loads as ABSENT, and no form posts an
    ``actual_amount`` for a transfer at all.  A service caller can send both,
    and the two refusals are the reason a settled transfer always carries a day
    and the reason a withdrawn figure stays withdrawn.

    Args:
        updates: The update kwargs as submitted.

    Returns:
        The kwargs the settle did not consume.
    """
    return {
        key: value for key, value in updates.items()
        if key not in _SETTLE_OWNED_FIELDS or (
            value is None and key != "status_id"
        )
    }


def _dispatch_settle(
    rows: TransferRows, updates: "dict[str, object]",
) -> "bool | None":
    """Run the SETTLE when *updates* moves this transfer into the settled band.

    **The dispatch that makes a transfer's settle rule structural** (plan step
    X-f2-c3, ruling **R-FA**).  Moving a transfer into the settled band is not
    just a status change: an auto-derived loan payment books what it is LIVE
    worth rather than the creation-time escrow its estimate carries, the pair is
    dated by whoever knows the day, and an echoed prefill is not recorded as a
    human's figure.  What those rules ARE
    is :mod:`._settle`'s; this decides WHEN they apply.

    **Why here and not at each door.**  Four doors could move a transfer into
    the settled band and exactly ONE of them froze -- the grid's shadow "Mark
    Paid", which called the loan service itself and passed the answer down.  The
    transfers page's "Mark Done", the transfer full-edit Status dropdown and a
    transaction PATCH landing on a shadow all booked the stored estimate.  That
    is finding **N-219**'s shape on the transfer table: a ROUTE holding a money
    rule, so one control books a different figure from another for the same
    payment.  Putting it at the one chokepoint every transfer mutation already
    passes through means a FIFTH door cannot be written without it -- and
    :func:`settle_transfer` gives the doors that mean only this a name to call
    instead of a kwargs bag to assemble.

    Args:
        rows: The transfer and both shadows, at their pre-update status.  The
            caller-stated facts (``is_override``, ``amount``) are already
            applied, which is what lets the settle read the post-edit state.
        updates: The update kwargs as submitted.

    Returns:
        ``None`` when this update does not settle -- so the caller leaves every
        kwarg to the ordinary field application.  Otherwise whether the settle
        booked a HUMAN's figure.  The three states are distinct because the
        caller needs both answers from one question: DID a settle run (which
        decides who owns the three kwargs it consumes) and did it record a
        correction (which the reconcile writer counts).
    """
    if "status_id" not in updates:
        return None
    if not enters_settled_band(rows.transfer, updates["status_id"]):
        return None
    return _settle.settle(
        rows, updates["status_id"],
        submitted=updates.get("actual_amount"),
        settled_on=updates.get("settled_on"),
    )


def _reconcile_postings_after_update(xfer: Transfer, updates: dict[str, object]) -> None:
    """Bring the posting ledger back in step after an ``update_transfer`` edit.

    Extracted from :func:`update_transfer` (which was at its branch/statement
    budget) so the reconcile tail is one cohesive step.  Runs after every kwarg
    is applied and the session is flushed:

    * **Step-2 cash reconcile** when a magnitude / settled-sense / period field
      changed (``_POSTING_RELEVANT_FIELDS``).  Placed here -- NOT inside
      ``apply_status_to_all_three`` -- because ``actual_amount`` is applied AFTER
      ``status_id`` and the grid shadow-edit path can settle and set an actual
      in one call; the reconcile reads the income shadow's ``effective_amount``,
      so it must run once everything is in place or it would post the pre-edit
      estimate.  ``xfer.status_id`` is the post-update status, so its
      ``is_settled`` is the correct target sense.  Idempotent
      reconcile-to-target: a settle posts the effect, a revert / cancel reverses
      to zero, an unchanged effect writes nothing.
    * **Loan-payment genesis reconcile** last (a no-op for a non-loan transfer):
      a settle / revert / amount / actual / period edit of a loan payment
      re-reconciles that loan's confirmed-payment splits (coupled on the running
      balance) and its opening / true-up anchor corrections.
    * **Full reconcile on a settled ``settled_on`` edit too (E1a / N-13)**: a
      ``settled_on`` change moves the day every posting counts from (its
      ``entry_date``, step C2's one clock) without changing any leg amount, so
      the per-period reconcile used to write nothing and the entries kept
      their stale dates.  The reconcile is per-(period, DATE) now: the
      old-dated entries reverse at their own date, the effect re-posts at the
      new settle date, and the loan sync's checked-projection assert verifies
      the result -- so the fold and the ledger cannot disagree about WHEN.
    * **Step-5 account-anchor resync on a settled ``settled_on`` edit (F1)**:
      resync the two endpoint accounts' anchor corrections so a settled
      ``settled_on`` move cannot strand a stale anchor correction (their
      reconcile is anchor-walk-derived, not delta-keyed off this transfer).
      Only for a SETTLED transfer (a projected one posts nothing); a no-op for
      a loan endpoint (the account walk skips amortizing accounts).
      ``pay_period_id`` needs no such branch -- it is in
      ``_POSTING_RELEVANT_FIELDS``, so a period move reconciles R2-correctly
      and self-heals via the cash reconcile above.  Fires on ANY settled
      ``settled_on`` edit, not only a pure one: on the common settle path (status
      + ``settled_on`` together) the reconcile's tail self-heal already covers
      both endpoints, so these two idempotent walks are redundant there -- an
      accepted, safe cost.  It is deliberately NOT narrowed to
      ``not needs_reconcile``, because a future COMBINED edit (e.g. ``amount``
      + ``settled_on``) could move the attribution in a way the delta-keyed
      self-heal does not cover; an always-correct resync is the point of this
      seam.

    Args:
        xfer: The updated, flushed :class:`Transfer`.
        updates: The ``update_transfer`` kwargs that were applied.
    """
    needs_reconcile = bool(_POSTING_RELEVANT_FIELDS & updates.keys())
    settled_on_edited = "settled_on" in updates
    if not (needs_reconcile or settled_on_edited):
        return
    current_status = db.session.get(Status, xfer.status_id)
    # A settled ``settled_on`` edit moves the day the event counts from (step
    # C2's one clock), which since step E1a IS a posting-relevant change: the
    # per-(period, date) reconcile reverses the stale-dated entry and re-posts
    # at the new settle date (finding N-13), and the loan sync's
    # checked-projection assert then verifies the ledger against the walk.
    if needs_reconcile or (settled_on_edited and current_status.is_settled):
        posting_service.sync_transfer_postings(
            xfer, settled=current_status.is_settled,
        )
        _sync_loan_postings_if_loan(xfer)
    if settled_on_edited and current_status.is_settled:
        account_posting_service.sync_account_anchor_postings(
            xfer.from_account_id, xfer.scenario_id,
        )
        account_posting_service.sync_account_anchor_postings(
            xfer.to_account_id, xfer.scenario_id,
        )


def _apply_remaining_fields(
    rows: TransferRows, user_id: int, updates: "dict[str, object]",
) -> None:
    """Apply every field a SETTLE does not own, mirroring it across the rows.

    **Transfer Invariants 3-5 are a property of this function**: shadow
    amounts, statuses and periods equal the parent's because every field a
    caller may move is mirrored HERE and nowhere else.  That is why the arms
    stay together rather than being cut into per-field helpers -- the invariant
    is held by the list being in one place, and half a mirroring in a second
    module is how a shadow drifts.

    The three fields it does NOT hold are :data:`_SETTLE_OWNED_FIELDS`, which
    have already been written as one act when this update settles; a
    ``status_id`` or ``settled_on`` that reaches the arms below therefore
    belongs to a NON-settling change, and each arm says what that means.

    Args:
        rows: The transfer and both shadows.
        user_id: The owner, for the period and category ownership checks.
        updates: The kwargs left for this function to apply.

    Raises:
        ValidationError: From an ownership check, a malformed
            ``actual_amount``, or the settle-day correction door.
        NotFoundError: From an unowned period or category.
    """
    # ── status_id ──────────────────────────────────────────────────
    # A status change that does NOT settle: a cancel, a revert out of the
    # settled band, or an archive of a row whose money already moved.  All three
    # transitions are verified before any propagation, then applied through the
    # ONE status seam, which owns the F-048 defense-in-depth ``settled_on``
    # synchronization and the ``status`` expire; see
    # :func:`app.services.transfer_service._status.apply_status_to_all_three`
    # for the full audit rationale.  No day is passed: the seam CLEARS the
    # column on the way out of the band, and there is no way in from here --
    # :func:`_dispatch_settle` has already taken every such move.
    if "status_id" in updates:
        apply_status_to_all_three(rows, updates["status_id"])

    # ── pay_period_id ──────────────────────────────────────────────
    if "pay_period_id" in updates:
        new_period_id = updates["pay_period_id"]
        _get_owned_period(new_period_id, user_id)
        rows.transfer.pay_period_id = new_period_id
        for shadow in rows.shadows:
            shadow.pay_period_id = new_period_id

    # ── category_id ────────────────────────────────────────────────
    # Category updates apply to both shadows so the transaction
    # appears under the user-selected category in both account grids.
    if "category_id" in updates:
        new_cat_id = updates["category_id"]
        if new_cat_id is not None:
            _get_owned_category(new_cat_id, user_id)
        rows.transfer.category_id = new_cat_id
        for shadow in rows.shadows:
            shadow.category_id = new_cat_id

    # ── name ───────────────────────────────────────────────────────
    # Name is display metadata on the transfer only.  Shadow names
    # are derived from account names and do not change here.
    if "name" in updates:
        rows.transfer.name = updates["name"]

    # ── notes ──────────────────────────────────────────────────────
    # Notes live on the transfer only; shadow transactions do not
    # carry independent notes.
    if "notes" in updates:
        rows.transfer.notes = updates["notes"]

    # ── actual_amount ──────────────────────────────────────────────
    # A correction to a row that is ALREADY settled -- the user re-read the
    # statement.  A figure arriving with a settle is the settle's
    # (:data:`_SETTLE_OWNED_FIELDS`), which is what keeps the echo rule and this
    # verbatim write from both landing on one column in one call.
    if "actual_amount" in updates:
        _apply_actual_amount(rows, updates["actual_amount"])

    # ── due_date ──────────────────────────────────────────────────
    # The parent transfer is canonical; mirror to both shadows so the
    # three rows stay equal (Transfer Invariant 3).
    if "due_date" in updates:
        new_due = updates["due_date"]
        rows.transfer.due_date = new_due
        for shadow in rows.shadows:
            shadow.due_date = new_due

    # ── settled_on ────────────────────────────────────────────────
    # The ONE caller that legitimately supplies a day is the user CORRECTING
    # it (ruling R-ED).  Both mark-done routes used to pass one and did not
    # mean it: their value overrode the seam's preserve rule and re-dated a
    # replayed settle (finding N-178, plan step X-f1b0).  It assigned the
    # column on both shadows here until plan step X-f1b, which made that a
    # second write door for the column the seam owns (finding N-183).
    #
    # **A day arriving WITH a settle no longer reaches here** (plan step
    # X-f2-c3).  It did, and that made every reconcile tick write the column
    # twice -- the derived day, then the statement's -- with a settle routed
    # through the door built for a correction.  The settle takes the day at the
    # status flip now; what is left here is a correction to a row whose money
    # had already moved, which is what this door has always been for.
    if "settled_on" in updates:
        apply_settle_day_correction(rows, updates["settled_on"])


def _apply_transfer_updates(transfer_id, user_id, updates, *, settle_only=False):
    """Apply *kwargs* to a transfer and both shadows; report the settle's answer.

    **The body both public doors share** -- :func:`update_transfer`, which takes
    an arbitrary field bag, and :func:`settle_transfer`, which takes a settle.
    Keeping one body is what stops the named verb from becoming a second
    implementation of the mirroring, the reconcile tail and the telemetry: two
    doors, one act.

    See :func:`update_transfer` for the accepted kwargs and the failure modes;
    they are that function's contract, stated once there.

    Args:
        transfer_id: The primary key of the transfer to update.
        user_id: The expected owner (defense-in-depth).
        updates: The fields to update, as a dict.
        settle_only: Whether the CALLER asked for a settle and nothing else
            (:func:`settle_transfer`).  It changes what happens when the
            transfer turns out to be settled ALREADY: a settle is idempotent, so
            there is nothing to do and the kwargs must not be applied as an
            ordinary field edit.  Without it the verb DEGRADED -- an adversarial
            review measured it writing ``actual_amount`` verbatim past the echo
            rule and re-dating both legs through the R-ED correction door, while
            returning ``False`` to say it had booked nothing.  A caller that
            genuinely means "edit these fields on a settled transfer" says so by
            calling :func:`update_transfer`.
    """
    rows = load_transfer_rows(transfer_id, user_id)

    # R-C: refuse an edit that would move a loan payment before its loan, before
    # any field is applied.  See :func:`_reject_installment_move_before_loan`.
    _reject_installment_move_before_loan(rows.transfer, user_id, updates)

    # ── is_override ────────────────────────────────────────────────
    # Applied FIRST, and the position is load-bearing rather than tidy: the
    # flag says WHO OWNS THIS TRANSFER'S AMOUNT, and the settle dispatch below
    # derives a figure only when the answer is "not the operator".  The
    # transfer edit route auto-sets it whenever a template-linked transfer's
    # amount moves, so a combined "retype the amount and mark it Paid" save
    # arrives carrying it -- and reading the PRE-edit flag there would freeze a
    # derived figure straight over the number the user had just typed.  Every
    # branch between here and the dispatch is a caller-stated FACT; the
    # derivation comes after all of them.
    if "is_override" in updates:
        flag = bool(updates["is_override"])
        rows.transfer.is_override = flag
        for shadow in rows.shadows:
            shadow.is_override = flag

    # ── amount ─────────────────────────────────────────────────────
    if "amount" in updates:
        new_amount = _validate_positive_amount(updates["amount"])
        rows.transfer.amount = new_amount
        for shadow in rows.shadows:
            shadow.estimated_amount = new_amount

    # ── the SETTLE ─────────────────────────────────────────────────
    # When this update moves the transfer into the settled band, ONE act writes
    # the amount, the status and the settle day for all three rows; the three
    # kwargs it consumes are then dropped so the loop below cannot write any of
    # them a second time.
    settled = _dispatch_settle(rows, updates)
    if settled is not None:
        remaining = _fields_the_settle_left(updates)
    elif settle_only:
        # Already in the settled band, so there is no settle to run -- but the
        # STATUS still goes through, and dropping it too was a defect this
        # branch shipped for one test run.  Two things depend on it: the
        # transition is VERIFIED, so marking an archived (``Settled``) or
        # ``Cancelled`` transfer Done stays the designed 400 it has always
        # been; and the write REPAIRS a pair whose shadows drifted out of the
        # parent's status, which is the state a bulk ``status_id`` update
        # leaves and which the posting reconcile below then refuses as an
        # undated settle.  What is dropped is the pair that DEGRADED: an
        # ``actual_amount`` that would be written verbatim past the echo rule,
        # and a ``settled_on`` that would re-date money already recorded.
        remaining = {"status_id": updates["status_id"]}
    else:
        remaining = updates

    _apply_remaining_fields(rows, user_id, remaining)

    db.session.flush()

    # The ORIGINAL updates, not *remaining*: what the caller ASKED to change is
    # what decides whether the ledger needs re-deriving, and a settle's
    # ``status_id`` is precisely the field that says it does.
    _reconcile_postings_after_update(rows.transfer, updates)

    log_event(
        logger, logging.INFO, EVT_TRANSFER_UPDATED, BUSINESS,
        "Transfer updated",
        user_id=user_id,
        transfer_id=transfer_id,
        # Sorting the field list keeps the structured log deterministic
        # so dashboards can group by ``fields_changed`` without spurious
        # cardinality from kwarg ordering.
        fields_changed=sorted(updates.keys()),
    )
    return rows.transfer, bool(settled)


def settle_transfer(
    transfer_id,
    user_id,
    *,
    actual_amount: Decimal | None = None,
    settled_on: date | None = None,
) -> bool:
    """Settle a transfer: both legs and the parent, on the day the money moved.

    **The named verb, and the twin of ``transaction_service.settle_transaction``**
    (ruling **R-FA**).  A door that means "this transfer reached the bank" says
    so by calling this, rather than by assembling the status, the day and the
    amount into an :func:`update_transfer` bag and trusting three kwargs to add
    up to one act.  What settling MEANS is
    :func:`app.services.transfer_service._settle.settle`; this is the door onto
    it, and it exists because the act had no name: its rules were spread over
    four modules and its ``settled_on`` was written twice per tick, the second
    time through the door ruling **R-ED** built for a user CORRECTING a day.

    **Both legs and the parent move in this one call**, which is ``CLAUDE.md``
    transfer invariants 3 and 4 held structurally rather than by each caller
    remembering them.  ``transaction_service.settle_transaction`` REFUSES a
    shadow outright and names this module as where one goes.

    **The status is DONE for all three rows, including the INCOME leg.**  The
    Paid/Received split is a display convention for ordinary rows and it is
    meaningless for a pair whose whole point is that one leg is each, so one
    status covers all three.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        transfer_id: The transfer to settle.
        user_id: The expected owner (defense-in-depth).
        actual_amount: The figure a HUMAN supplied, when a door collected one --
            the reconcile panel's amount box.  ``None`` means nobody typed one,
            and the settle then books what the row is worth.
        settled_on: The civil day the money moved, when the caller knows it --
            the reconcile tick's statement date.  ``None`` leaves the pair-day
            rule in force (the user's today on a first settle).

    Returns:
        Whether the settle booked *actual_amount* as a human's CORRECTION --
        False when nobody typed one, and False when the figure was an echo of
        what the row would book anyway.  A transfer ALREADY in the settled band
        is an idempotent no-op that writes nothing and returns False: a settle
        records that money moved, and it has already been recorded.

    Raises:
        NotFoundError: If the transfer does not exist or does not belong to
            *user_id*.
        ValidationError: On an illegal transition, a soft-deleted shadow, a
            non-positive derivation, or the seam's settle-day refusals.
        PostingError: From the ledger reconcile, on a broken invariant.
    """
    updates = {"status_id": ref_cache.status_id(StatusEnum.DONE)}
    if actual_amount is not None:
        updates["actual_amount"] = actual_amount
    if settled_on is not None:
        updates["settled_on"] = settled_on
    _, corrected = _apply_transfer_updates(
        transfer_id, user_id, updates, settle_only=True,
    )
    return corrected


def update_transfer(transfer_id, user_id, **kwargs):
    """Update a transfer and propagate changes to shadow transactions.

    Enforces invariants 3-5: shadow amounts, statuses, and periods
    always match the parent transfer.

    **A change that moves the transfer INTO the settled band is a SETTLE**, and
    this hands that act whole to
    :func:`app.services.transfer_service._settle.settle` -- so a door may state
    a settling ``status_id`` among its other fields and still get the freeze,
    the pair's day and the correction rule.  A door that means ONLY that should
    call :func:`settle_transfer`, which says so.

    Accepted kwargs:
        amount         -- New transfer amount (positive Decimal).
        status_id      -- New status for transfer and both shadows.
        pay_period_id  -- New period for transfer and both shadows.
        category_id    -- New category (expense shadow only).
        name           -- New display name (transfer only, not shadows).
        notes          -- New notes (transfer only, not shadows).
        actual_amount  -- Actual settled amount (both shadows only;
                          the Transfer model has no actual_amount
                          column).
        due_date       -- Due date for the transfer and both shadows
                          (Date or None).
        settled_on     -- The civil day the money moved, for both shadows
                          (DateTime or None).
        is_override    -- Override flag (transfer and both shadows).

    Any other kwargs are silently ignored (consistent with the
    BaseSchema EXCLUDE pattern).

    Args:
        transfer_id: The primary key of the transfer to update.
        user_id:     The expected owner (defense-in-depth).
        **kwargs:    The fields to update; see "Accepted kwargs" above.
                     Any key not listed there is silently ignored.

    Returns:
        The updated Transfer object.

    Raises:
        NotFoundError: If the transfer does not exist or does not
            belong to user_id.
        ValidationError: If validation fails (non-positive amount,
            wrong period owner, data integrity issues).
    """
    xfer, _ = _apply_transfer_updates(transfer_id, user_id, kwargs)
    return xfer
