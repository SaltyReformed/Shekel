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
from decimal import Decimal
from sqlalchemy.orm.attributes import flag_modified

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.ref import Status
from app.models.transfer import Transfer
from app.services import account_posting_service
from app.services import posting_service
from app.services.transfer_service import _settle
from app.services.transfer_service._amount import apply_amount_ownership
from app.services.transfer_service._endpoints import (
    _apply_endpoint_move,
    _resolve_endpoints,
)
from app.services.transfer_service._loan_posting import (
    _reject_installment_move_before_loan,
    _resync_vacated_loan,
    _reverse_loan_payment_before_it_leaves,
    _sync_loan_postings_if_loan,
)
from app.services.transfer_service._ownership import (
    _get_owned_category,
    _get_owned_period,
)
from app.services.row_valuation import recorded_figure
from app.services.status_seam import (
    correction_record,
    figure_for_status,
)
from app.services.settle_day import SettleDay
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
# ``actual_amount`` together determine what the settled shadow is WORTH
# (``COALESCE(actual_amount, estimated_amount)``) -- the magnitude posted;
# ``pay_period_id`` moves the entry's period, so a settled period move
# reconciles R2-correctly (the per-(account, period) reconcile reverses the old
# period and posts the new) AND fires the effect-time self-heal for the Step-5
# account-anchor corrections (F1).
#
# ``due_date`` IS here, and its inclusion is load-bearing: on a LOAN payment the
# due date is the installment the payment satisfies, which the genesis write walk
# dates every payment by (``loan_ledger.loan_event_stream``), orders on
# (``loan_ledger.replay_loan_events``, which applies its strict
# ``anchor_date < due_date`` post-anchor boundary against it) and keys its accrual
# periods off -- so moving it changes which payments an anchor SUBSUMES, which
# accrual period is charged, and therefore the POSTED balance.  Editing it
# without a reconcile would leave the posted ledger disagreeing with every live
# reader (the history rows, the payment
# table, the resolver's replay), silently, until an unrelated chokepoint happened
# to fire.  On a NON-loan transfer the cash reconcile is reconcile-to-target and
# writes nothing, so listing it costs one idempotent no-op round-trip.
#
# The remaining kwargs (``category_id`` / ``name`` / ``notes`` / ``is_override``)
# move none of these, so they raise no reconcile.  ``settle_day`` is deliberately
# NOT here: it moves no leg AMOUNT, and an unsettled transfer has no postings to
# re-date, so the set stays the cheap always-on pre-filter.  A SETTLED
# settle-day edit IS posting-relevant since step E1a -- it moves the day every
# posting counts from (the ``entry_date``, step C2's one clock) -- and
# ``_reconcile_postings_after_update`` runs the full reconcile for that case
# explicitly (the per-(period, date) reconcile re-dates the entries, finding
# N-13) plus the two endpoint accounts' anchor-correction resync (F1).  The
# reconcile is idempotent, so listing a field that did not move the effect is a
# harmless no-op; this set is the cheap pre-filter that avoids a ledger
# round-trip on a pure metadata edit.
#
# ``from_account_id`` / ``to_account_id`` ARE here, and they are plan step
# R10-b's addition.  An endpoint move changes WHICH ledger accounts a settled
# transfer's two legs sit on, which is a change of the posted effect in exactly
# the sense this set names -- ``_posting_write.reconcile_periods`` takes the
# per-ledger-account delta over the UNION of what is posted and what is
# targeted, so the vacated accounts reverse to zero and the new ones post the
# effect, converging in one pass.  What that reconcile does NOT reach is the
# vacated accounts' own anchor corrections and, when the vacated destination was
# an amortizing loan, that loan's genesis ledger; both are re-derived
# explicitly in :func:`_reconcile_postings_after_update`.
_POSTING_RELEVANT_FIELDS = frozenset(
    {
        "status_id", "amount", "settled_amount", "pay_period_id", "due_date",
        "from_account_id", "to_account_id",
    }
)


#: The kwargs a SETTLE owns outright when one runs.  Each is an input to
#: :func:`app.services.transfer_service._settle.settle`, which writes all three
#: columns itself -- so leaving them in the field-application loop below would
#: write each of them a second time.  That is not hypothetical tidiness: it is
#: exactly what this module did until plan step X-f2-c3, where every reconcile
#: tick stamped the settle day with the derived one and then rewrote it with the
#: statement's through ruling **R-ED**'s CORRECTION door.
_SETTLE_OWNED_FIELDS = frozenset({"status_id", "settled_amount", "settle_day"})


def _fields_the_settle_left(
    updates: "dict[str, object]",
) -> "dict[str, object]":
    """Return the kwargs still owed an application after a settle ran.

    :data:`_SETTLE_OWNED_FIELDS`, minus a ``settle_day`` that arrived as an
    explicit ``None``.  **A settle consumes a VALUE; that ``None`` is not one --
    it is a request to CLEAR the day**, which is a different act with its own
    door, and a settle that swallowed it would perform neither.  Left here it
    reaches
    :func:`~app.services.transfer_service._status.apply_settle_day_correction`,
    which refuses it in a sentence a user can act on; consumed instead, that
    designed refusal would silently become "the settle used today's date".  It
    cannot arrive from a route -- both PATCH schemas declare ``settled_on``
    non-nullable, so an empty input loads as ABSENT and the route never builds a
    ``settle_day`` key for it -- but a service caller can
    send it, and the refusal is the reason a settled transfer always carries the
    day its money moved.

    **``settled_amount=None`` no longer needs an exception, and losing it is
    plan step X-au-c3's** (see :func:`_apply_remaining_fields`' figure arm).  It
    used to mean "clear the column", because a settled transfer carrying no
    figure was a legal state -- every reader fell back to the row's plan.  A
    settled row now always records what moved, so there is no clearing act for a
    ``None`` to request: it means what a form means by an empty box, which is
    that nobody typed one.

    Args:
        updates: The update kwargs as submitted.

    Returns:
        The kwargs the settle did not consume.
    """
    return {
        key: value for key, value in updates.items()
        if key not in _SETTLE_OWNED_FIELDS or (
            value is None and key == "settle_day"
        )
    }


def _grade_submitted_figure(
    rows: TransferRows, updates: "dict[str, object]",
) -> None:
    """Read what a submitted FIGURE means, and refuse a real conflict.

    **Asked before any field is written**, which is the whole reason it is a
    separate gate rather than an arm among the writes: a refused request must
    leave the transfer and both shadows untouched, and ``is_override`` and
    ``amount`` are already staged by the time :func:`_apply_remaining_fields`
    runs.  It is the transfer twin of the ordering
    ``transaction_service._door._correction_for_status`` keeps.

    The reading is :func:`app.services.status_seam.figure_for_status`, shared
    with the transaction door so the two tables cannot come to phrase one money
    rule two ways.  What is graded is the status this update LEAVES, not the one
    it starts from: a figure riding a settle is legal (the settle owns it), a
    figure on a pair that stays settled is a CORRECTION, and a figure on
    anything else is asking to record money that has not moved.

    **An ECHO is DROPPED rather than refused, and that is why this mutates
    *updates* instead of only raising.**  Both full-edit forms re-submit the
    whole row, and the documented way to unlock a finalised transfer is to set
    Status to Projected in that same form -- so a revert arrives carrying the
    Actual box's untouched contents.  Refusing that would break the unlock path
    on every settled transfer, which is the trap ruling **R-EG** removed for the
    settle day.  A figure the user CHANGED is a different thing and is refused.

    The record is read off the EXPENSE leg, the leg the correction writes first
    and the leg :func:`._settle.settle` resolves its figures from; the parent
    carries no record at all.

    Args:
        rows: The transfer and both shadows, at their pre-update status.
        updates: The update kwargs as submitted.  Mutated in place: an echoed
            ``settled_amount`` has its key removed.

    Raises:
        ValidationError: When a ``settled_amount`` DIFFERING from what the pair
            records arrives beside a status that settles nothing.
    """
    if updates.get("settled_amount") is None:
        return
    figure = figure_for_status(
        rows.transfer,
        updates.get("status_id", rows.transfer.status_id),
        updates["settled_amount"],
        recorded_figure(rows.expense),
    )
    if figure is None:
        del updates["settled_amount"]


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
        submitted=updates.get("settled_amount"),
        settle_day=updates.get("settle_day"),
    )


def _reconcile_postings_after_update(
    xfer: Transfer,
    updates: dict[str, object],
    vacated: "tuple[int, ...]" = (),
    vacated_destination_id: "int | None" = None,
) -> None:
    """Bring the posting ledger back in step after an ``update_transfer`` edit.

    Extracted from :func:`update_transfer` (which was at its branch/statement
    budget) so the reconcile tail is one cohesive step.  Runs after every kwarg
    is applied and the session is flushed:

    * **Step-2 cash reconcile** when a magnitude / settled-sense / period field
      changed (``_POSTING_RELEVANT_FIELDS``).  Placed here -- NOT inside
      ``apply_status_to_all_three`` -- because ``actual_amount`` is applied AFTER
      ``status_id`` and the grid shadow-edit path can settle and set an actual
      in one call; the reconcile reads what the income shadow is worth,
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

    * **The accounts an ENDPOINT MOVE left behind (plan step R10-b), LAST**: the cash
      reconcile above heals the LEGS by itself -- ``reconcile_periods`` takes
      the per-ledger-account delta over the union of posted and target, so a
      vacated ledger account reverses to zero in the same pass the new one
      posts -- but two things it emits are scoped to the transfer's CURRENT
      endpoints and reach no further.  ``sync_transfer_postings``' own
      Step-5 self-heal names ``(from_account_id, to_account_id)``, so a vacated
      account's opening / true-up corrections are re-derived here instead; and
      when the vacated destination was an amortizing LOAN, that loan's genesis
      ledger and its recurring payment's window both still count a payment it
      no longer has (:func:`._loan_posting._resync_vacated_loan`).  Both walks
      are idempotent and neither is gated on the transfer being settled: a
      PROJECTED payment posts no cash but is still inside the payoff projection
      the loan's window is bounded by, so a projected payment moving off a loan
      moves that loan's payoff.

    Args:
        xfer: The updated, flushed :class:`Transfer`.
        updates: The ``update_transfer`` kwargs that were applied.
        vacated: The account IDs this update moved the transfer OFF
            (:attr:`_Endpoints.vacated`); empty for every update that names no
            account, which is every caller outside the recurrence engine and the
            non-repeating propagation.
        vacated_destination_id: Which of those was the DESTINATION, or ``None``.
            Only that one can have been a loan whose payment set counted this
            transfer; see the comment at the call.
    """
    # **The vacated walks below need no disjunct of their own**, and three
    # adversarial reviews of this step each flagged the one that stood here.
    # *vacated* is non-empty only when ``from_account_id`` or ``to_account_id``
    # is in *updates*, and both are members of
    # :data:`_POSTING_RELEVANT_FIELDS` -- so ``vacated`` implies
    # ``needs_reconcile`` by MEMBERSHIP in that set, and ``or vacated`` could
    # never be the term that admitted a call.  A guard no input can exercise is
    # the shape this step deleted from both retention predicates; the
    # implication is stated here instead, where the set it rests on is three
    # definitions up and visible.
    needs_reconcile = bool(_POSTING_RELEVANT_FIELDS & updates.keys())
    settle_day_edited = "settle_day" in updates
    if not (needs_reconcile or settle_day_edited):
        return
    current_status = db.session.get(Status, xfer.status_id)
    # A settled ``settled_on`` edit moves the day the event counts from (step
    # C2's one clock), which since step E1a IS a posting-relevant change: the
    # per-(period, date) reconcile reverses the stale-dated entry and re-posts
    # at the new settle date (finding N-13), and the loan sync's
    # checked-projection assert then verifies the ledger against the walk.
    if needs_reconcile or (settle_day_edited and current_status.is_settled):
        posting_service.sync_transfer_postings(
            xfer, settled=current_status.is_settled,
        )
        _sync_loan_postings_if_loan(xfer)
    if settle_day_edited and current_status.is_settled:
        account_posting_service.sync_account_anchor_postings(
            xfer.from_account_id, xfer.scenario_id,
        )
        account_posting_service.sync_account_anchor_postings(
            xfer.to_account_id, xfer.scenario_id,
        )
    # LAST, and the position is load-bearing rather than tidy: both walks below
    # read the vacated account's ledger, and until ``sync_transfer_postings``
    # above has reversed this transfer's legs off it that ledger still holds a
    # net for a transfer with no shadow there.  Run first instead, the account
    # walk raises ``PostingError`` -- *"Ledger account 8 holds a nonzero net for
    # transfer ids [409] but no active shadow on account 1 resolves them;
    # Transfer Invariant 1 is broken"* -- which is the invariant correctly
    # reporting a ledger this function had not finished moving.  Measured on a
    # production clone before the order was fixed.
    for account_id in vacated:
        account_posting_service.sync_account_anchor_postings(
            account_id, xfer.scenario_id,
        )
    # The LOAN half is the vacated DESTINATION's alone, and that narrowing is a
    # measurement rather than an economy.  A loan reached as a transfer's SOURCE
    # carries that transfer's EXPENSE shadow, and a loan's payment set is
    # ``loan_loaders.query_shadow_income`` -- INCOME shadows only -- so such a
    # transfer was never one of the loan's payments and there is no split to
    # re-derive when it leaves.  Its raw cash leg is reversed by
    # ``sync_transfer_postings`` above, which takes the per-ledger-account delta
    # over the union of posted and target.  Verified by removing the call: the
    # legacy-loan-source case stays green either way, where the destination
    # cases fail.
    if vacated_destination_id is not None:
        _resync_vacated_loan(vacated_destination_id, xfer.scenario_id)


def _apply_remaining_fields(
    rows: TransferRows, updates: "dict[str, object]",
) -> None:
    """Apply every field a SETTLE does not own, mirroring it across the rows.

    **Transfer Invariants 3-5 are a property of this function**: shadow
    amounts, statuses and periods equal the parent's because every field a
    caller may move is mirrored HERE and nowhere else.  That is why the arms
    stay together rather than being cut into per-field helpers -- the invariant
    is held by the list being in one place, and half a mirroring in a second
    module is how a shadow drifts.

    :data:`_SETTLE_OWNED_FIELDS` reach it only when this update did NOT settle,
    because a settle writes all three as one act and they are dropped before
    this runs.  So a ``status_id``, a ``settle_day`` or a ``settled_amount``
    among the arms below belongs to a non-settling change -- a revert, a cancel,
    an archive, or a CORRECTION to what a pair already recorded -- and each arm
    says what that means.

    Args:
        rows: The transfer and both shadows.
        updates: The kwargs left for this function to apply.

    Note:
        It takes no ``user_id``: the two ownership refusals it used to make now
        run before the first write (:func:`_reject_unowned_references`), so what
        is left here is assignment only.

    Raises:
        ValidationError: From an ownership check, an illegal transition, or
            the settle-day correction door.
        NotFoundError: From an unowned period or category.
        AmountUnresolvable: From the correction's echo comparison, for a
            settled leg whose settlement record is incomplete.
    """
    # ── status_id + the settlement RECORD ─────────────────────────
    # ONE seam pass carrying both, mirroring
    # :func:`app.services.transaction_service._door.apply_requested_status` one
    # table over -- a status change and a figure correction are INDEPENDENT
    # facts, and a door that treats them as alternatives drops whichever it
    # decides against.  That is not hypothetical: the transaction door returned
    # early after recording a figure, so a row moving Paid -> Settled while
    # carrying a corrected Actual recorded the figure and never archived.
    # (That was the terminal ARCHIVE status, deleted at plan step
    # **balance:X-am**.  The measurement is quoted as it was taken; what it
    # established -- two independent facts in one request are not alternatives
    # -- is about the DOOR and outlived the status it was found on.)
    #
    # The status half is a change that does NOT settle: a cancel, or a revert
    # out of the settled band.  Both transitions are verified before any
    # propagation, then applied
    # through the ONE status seam, which owns the F-048 defense-in-depth
    # ``settled_on`` synchronization and the ``status`` expire; see
    # :func:`app.services.transfer_service._status.apply_status_to_all_three`
    # for the full audit rationale.  No day is passed: the seam CLEARS the
    # column on the way out of the band, and there is no way in from here --
    # :func:`_dispatch_settle` has already taken every such move.
    #
    # The RECORD half is the Actual box's write door (developer ruling,
    # 2026-08-17).  **A transfer's DAY was correctable in place and its FIGURE
    # was not**, which is the exact asymmetry that ruling objects to: the day
    # travels through :func:`apply_settle_day_correction` and the figure was
    # REFUSED outright, so the only way to restate what the bank took was to
    # revert the transfer, edit, and settle it again -- and a revert RETAINS
    # the recorded figure, so the re-settle silently re-booked the old number
    # over the re-planned one.  The lock produced a wrong figure, not friction.
    #
    # ``new_status_id`` defaults to the pair's CURRENT status, so a figure
    # arriving alone reaches the seam as an identity transition -- the same
    # shape :func:`apply_settle_day_to_pair` uses for a day correction, and the
    # reason the seam stays the single writer of the settlement columns.
    #
    # The record goes to both SHADOWS and to neither the parent, which
    # ``apply_status_to_all_three`` owns: a transfer's money moves on its two
    # legs, so each leg records its own and the two are equal by Transfer
    # Invariant 3, exactly as their settle day is.
    new_status_id = updates.get("status_id", rows.transfer.status_id)
    # Resolved from the EXPENSE leg, the same leg :func:`._settle.settle` reads
    # its figures from and for the same reason: both legs carry the same record
    # (Transfer Invariant 3), so naming one means the choice is not made twice.
    # A figure only ever reaches here as a CORRECTION -- a settling one is the
    # settle's (:data:`_SETTLE_OWNED_FIELDS`), and one on an unsettled pair was
    # refused before any field was written (:func:`_grade_submitted_figure`).
    submitted = updates.get("settled_amount")
    correction = (
        None if submitted is None
        else correction_record(rows.expense, submitted)
    )
    if "status_id" in updates or correction is not None:
        apply_status_to_all_three(
            rows, new_status_id, settlement=correction,
        )

    # ── pay_period_id ──────────────────────────────────────────────
    # Ownership was refused before the first write
    # (:func:`_reject_unowned_references`); this arm only assigns.
    if "pay_period_id" in updates:
        new_period_id = updates["pay_period_id"]
        rows.transfer.pay_period_id = new_period_id
        for shadow in rows.shadows:
            shadow.pay_period_id = new_period_id

    # ── category_id ────────────────────────────────────────────────
    # Category updates apply to both shadows so the transaction
    # appears under the user-selected category in both account grids.
    if "category_id" in updates:
        new_cat_id = updates["category_id"]
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

    # ── due_date ──────────────────────────────────────────────────
    # The parent transfer is canonical; mirror to both shadows so the
    # three rows stay equal (Transfer Invariant 3).
    if "due_date" in updates:
        new_due = updates["due_date"]
        rows.transfer.due_date = new_due
        for shadow in rows.shadows:
            shadow.due_date = new_due

    # ── settle_day ────────────────────────────────────────────────
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
    if "settle_day" in updates:
        apply_settle_day_correction(rows, updates["settle_day"])


def _reject_unowned_references(
    user_id: int, updates: "dict[str, object]",
) -> None:
    """Refuse an unowned period or category BEFORE any field is written.

    The two user-scoped FKs :func:`update_transfer` accepts, checked with the
    other pre-write gates rather than at the arm that assigns them.  They ran
    inside :func:`_apply_remaining_fields`, which is AFTER
    :func:`_dispatch_settle` has written the status, the pair's day and the
    settlement record to both shadows -- so an unowned id raised with the
    settlement already staged, and the route's ``NotFoundError`` exit returns
    404 without rolling back (unlike ``_error_transfer_response``, which does).
    Nothing persists today, because Flask-SQLAlchemy's teardown removes the
    session, but it made this module's own rule -- every refusal before the
    first write -- untrue of two of its refusals (neutral review, 2026-08-18).

    Ownership is re-checked at the route boundary too (commit C-27 / F-043);
    this is the service tier's own, so a caller that skips the route cannot
    write across an ownership line.

    Args:
        user_id: The owner every referenced row must belong to.
        updates: The update kwargs as submitted.

    Raises:
        NotFoundError: If a submitted ``pay_period_id`` or ``category_id`` is
            not *user_id*'s.  The security response rule collapses "not found"
            and "not yours" to one answer.
    """
    if "pay_period_id" in updates:
        _get_owned_period(updates["pay_period_id"], user_id)
    if updates.get("category_id") is not None:
        _get_owned_category(updates["category_id"], user_id)


def _bump_parent_version_if_a_leg_moved(rows: TransferRows) -> None:
    """Move the PARENT's optimistic-lock counter when a shadow-only write lands.

    **A transfer and its two shadows are ONE thing, so the aggregate's version
    is what a stale form must be caught by** (developer ruling, 2026-08-18).
    Both full-edit popovers pin ``Transfer.version_id``, but the two facts they
    can correct in place -- the settle DAY (ruling **R-ED**) and the settled
    FIGURE -- live on the shadows alone.  SQLAlchemy bumps a version counter
    only for a row it actually UPDATEs, so those saves left the parent's counter
    untouched and the C-18 conflict cell could not fire.

    **Measured before the fix, on the live route**: two tabs open on one settled
    transfer, both holding ``version_id = 2``.  Tab A corrects the figure to
    ``$214.37`` -- 200 OK, parent still ``2``.  Tab B then saves its prefilled
    ``$200.00`` against the same pin -- 200 OK, both legs now record
    ``$200.00``.  A lost update on a money figure, reported as success, with the
    figure the user read off their statement gone and no conflict shown.

    **Gated on a NET change, not on "a write was attempted".**
    ``Session.is_modified`` compares each attribute against its committed value,
    so an echoed prefill -- which ``correction_record`` already resolves to "no
    record to write" -- bumps nothing.  A version that moved when nothing did
    would turn every second tab into a spurious 409.

    ``flag_modified`` is what forces the parent into the flush: the row has no
    field of its own to change, and an assignment of an unchanged value is
    dropped from the UPDATE (SQLAlchemy's ``_collect_update_commands`` skips a
    net-zero change), which is the same rule that hid the defect.  It writes
    ``status_id`` back at its current value -- chosen because it is the one
    column every path through this module has already loaded, and
    ``flag_modified`` REFUSES an attribute absent from the object state (which
    ``updated_at`` is on a freshly-expired instance, measured).  The row's
    ``updated_at`` still refreshes, because the mixin's ``onupdate`` fires for
    any UPDATE of the row.

    Args:
        rows: The transfer and both shadows, after every field write.
    """
    if not any(db.session.is_modified(shadow) for shadow in rows.shadows):
        return
    if db.session.is_modified(rows.transfer):
        # The parent is already in the flush, so its counter moves anyway.
        return
    flag_modified(rows.transfer, "status_id")


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

    # The ENDPOINTS this update leaves the transfer with, resolved and refused
    # first because the guard below GRADES against the resulting destination:
    # re-pointing a payment at another loan moves which origination date it is
    # judged by without moving its own installment at all.  Resolving here is
    # also what ownership-checks both accounts before any refusal can name one
    # (plan step R10-b).
    endpoints = _resolve_endpoints(rows, user_id, updates)

    # R-C: refuse an edit that would move a loan payment before its loan, before
    # any field is applied.  See :func:`_reject_installment_move_before_loan`.
    _reject_installment_move_before_loan(
        rows.transfer, user_id, updates, endpoints.to_account,
    )

    # The FIGURE's own gate, in the same place and for the same reason: a
    # refused request must leave all three rows untouched, and the first field
    # write is two statements below.  It also DROPS an echoed box here, so the
    # settle dispatch and the field arms below both see the payload the user
    # actually meant.
    _grade_submitted_figure(rows, updates)

    # The two OWNERSHIP refusals, hoisted here for the same rule.  They ran at
    # the arms that assign them, which is after the settle has already written
    # both shadows.
    _reject_unowned_references(user_id, updates)

    # The AMOUNT's own refusal, hoisted for the same rule and by plan step
    # R10-b's adversarial review.  It ran at the arm that assigns it, two
    # writes later -- so ``update_transfer(to_account_id=<other>,
    # amount=Decimal("-5"))`` moved the pair between accounts and reversed a
    # loan payment's split BEFORE deciding the amount was illegal.  Validating
    # here leaves the refusal where every other one is: ahead of the first
    # write.  ``None`` when the caller states no amount, which the arm below
    # distinguishes by asking *updates*, not this value.
    amount = (
        _validate_positive_amount(updates["amount"])
        if "amount" in updates else None
    )

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

    # ── from_account_id / to_account_id ────────────────────────────
    # A caller-stated fact like the flag above, and applied with it for the
    # same reason: the settle dispatch below reads which account the transfer
    # is left pointing AT.  See :func:`_apply_endpoint_move`.
    #
    # A loan payment's SPLIT correction is reversed FIRST, while the pair is
    # still on the loan -- the same reverse-before / resync-after sequence the
    # DELETE path runs, and for the same reason: the loan-side reconcile finds
    # a loan's payments through the account its income shadow sits on, so a
    # correction whose shadow has already moved is invisible to every later
    # pass.  See :func:`._loan_posting._reverse_loan_payment_before_it_leaves`
    # for the `-$4.17` that measured it.  The resync half is in
    # :func:`_reconcile_postings_after_update`.
    if endpoints.vacated_destination_id is not None:
        _reverse_loan_payment_before_it_leaves(rows.transfer)
    _apply_endpoint_move(rows, endpoints)

    # ── amount, and WHO OWNS each row's figure ─────────────────────
    # Asked for an ``is_override`` too, not only for an ``amount``: clearing
    # the flag is the conflict resolver handing a row back to its definition,
    # and a leg left OWNING its frozen figure through that act is the drift
    # this step exists to make unconstructible (plan step X-au-g-2c-2).
    if "amount" in updates or "is_override" in updates:
        apply_amount_ownership(
            rows, stated_amount=amount,
            stated_override=(
                bool(updates["is_override"])
                if "is_override" in updates else None
            ),
        )

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
        # transition is VERIFIED, so marking a ``Cancelled`` transfer Done
        # stays the designed 400 it has always been; and the write REPAIRS a
        # pair whose shadows drifted out of the
        # parent's status, which is the state a bulk ``status_id`` update
        # leaves and which the posting reconcile below then refuses as an
        # undated settle.  What is dropped is the pair that DEGRADED: an
        # ``actual_amount`` that would be written verbatim past the echo rule,
        # and a ``settle_day`` that would re-date money already recorded.
        remaining = {"status_id": updates["status_id"]}
    else:
        remaining = updates

    _apply_remaining_fields(rows, remaining)

    _bump_parent_version_if_a_leg_moved(rows)

    db.session.flush()

    # The ORIGINAL updates, not *remaining*: what the caller ASKED to change is
    # what decides whether the ledger needs re-deriving, and a settle's
    # ``status_id`` is precisely the field that says it does.
    _reconcile_postings_after_update(
        rows.transfer, updates, endpoints.vacated,
        endpoints.vacated_destination_id,
    )

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
    submitted: Decimal | None = None,
    settle_day: SettleDay | None = None,
) -> bool:
    """Settle a transfer: both legs and the parent, on the day the money moved.

    **The named verb, and the twin of ``transaction_service.settle_transaction``**
    (ruling **R-FA**).  A door that means "this transfer reached the bank" says
    so by calling this, rather than by assembling the status, the day and the
    amount into an :func:`update_transfer` bag and trusting three kwargs to add
    up to one act.  What settling MEANS is
    :func:`app.services.transfer_service._settle.settle`; this is the door onto
    it, and it exists because the act had no name: its rules were spread over
    four modules and its settle day was written twice per tick, the second
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
        submitted: The figure a HUMAN supplied, when a door collected one --
            the reconcile panel's amount box.  ``None`` means nobody typed one,
            and the settle then books what the row is worth.
        settle_day: The civil day the money moved and HOW that day is known
            (:class:`app.services.settle_day.SettleDay`), when the caller knows
            it -- the reconcile tick's statement day on the ``asserted`` basis,
            the matcher's bank day on ``observed``.  ``None`` leaves the
            pair-day rule in force (the user's today, ``entered``, on a first
            settle).

    Returns:
        Whether the settle booked *submitted* as a human's CORRECTION --
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
    if submitted is not None:
        updates["settled_amount"] = submitted
    if settle_day is not None:
        updates["settle_day"] = settle_day
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

    **A change that moves the transfer between ACCOUNTS moves both legs**, and
    that arm is plan step R10-b's: a transfer's endpoints are two of the six
    columns a recurring definition states, and until that step this door could
    write only four of them -- so a definition's account change reached its
    generated rows by DESTROYING and rebuilding every one of them, and a
    NON-repeating transfer refused the same edit outright because nothing could
    carry it.  Both are gone.  See :func:`_resolve_endpoints` for what a move
    is refused for and :func:`_apply_endpoint_move` for what it writes.

    Accepted kwargs:
        amount         -- New transfer amount (positive Decimal).
        status_id      -- New status for transfer and both shadows.
        pay_period_id  -- New period for transfer and both shadows.
        from_account_id -- New SOURCE account for the transfer and its expense
                          shadow, whose display name is re-derived with it.
                          May not be an amortizing loan (a disbursement is not
                          modelled) and may not equal the destination.
        to_account_id  -- New DESTINATION for the transfer and its income
                          shadow, likewise re-named.  Re-grades the payment
                          against the destination loan's origination (ruling
                          R-C) and re-reconciles the loan it left.
        category_id    -- New category (expense shadow only).
        name           -- New display name (transfer only, not shadows).
        notes          -- New notes (transfer only, not shadows).
        settled_amount -- What MOVED, recorded on both shadows only: a
                          transfer's money moves on its two legs and the parent
                          carries no such column.  A figure arriving WITH a
                          settling ``status_id`` is the settle's own, subject to
                          its echo rule (:data:`_SETTLE_OWNED_FIELDS`).  One
                          arriving on a pair that is ALREADY settled is a
                          CORRECTION to what it recorded -- the Actual box --
                          and is applied in the same seam pass as the status,
                          because what the bank took is an observed fact and an
                          observation gets corrected when the statement
                          disagrees (developer ruling, 2026-08-17).  One
                          arriving on any other status is REFUSED: an amount
                          states what MOVED, and this pair's money has not.
        due_date       -- Due date for the transfer and both shadows
                          (Date or None).
        settle_day     -- The civil day the money moved and HOW that day is
                          known, for both shadows
                          (:class:`app.services.settle_day.SettleDay` or None).
                          **The key is not a column name** (plan step X-az):
                          ``Transfer`` has no ``settled_on`` column, only a
                          read-only property over its income leg, and the value
                          carries the day's basis as well as the day.
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
