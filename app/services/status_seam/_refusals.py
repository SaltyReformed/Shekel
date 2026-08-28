"""
Shekel Budget App -- Status Seam: the refusals

The invariants of a settlement stated as GUARDS, so every caller of the seam
inherits them rather than each door remembering one.  They are gathered here
because they are one subject -- what a row may and may not assert about its own
money -- and because :func:`._seam.apply_status_change` runs all five ahead of
any mutation, so a refused call leaves the row untouched.

Split out of the single ``status_seam`` module at plan step **X-au-c3**; see
:mod:`._record` for the ground the split was made on.

Two of the five are this project's answer to a rule that cannot be a CHECK
constraint, and both say so in their own docstrings: the settled-status
questions need ``ref.statuses.is_settled``, which a constraint on
``budget.transactions`` cannot see, and the ref convention keeps status ids out
of a schema.

Pure: reads columns and the settled-status predicate, raises or returns.  No
session, no mutation, no Flask.
"""

from datetime import date
from typing import Optional, Union

from app.exceptions import ValidationError
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.settle_day import SettleDay
from app.services.status_seam._record import Settlement
from app.utils.balance_predicates import settled_status_ids
from app.utils.dates import display_today


#: The rows this seam accepts.  ``Transfer`` carries no ``settled_on`` column --
#: a transfer's settle day lives on its two shadow ``Transaction`` rows --
#: so the dating half of the mechanics is skipped for one of the two.  The
#: branch is on the MODEL, never on ``hasattr``: a probe would silently skip the
#: maintenance for any future row that merely spelled the column differently,
#: and this arc has already paid for a ``hasattr``-shaped test -- plan step
#: X-aa's, whose lesson is Section 8's "``hasattr`` on a dataclass is not a
#: test".  (An earlier draft cited ruling R-CQ for that; R-CQ is the classifier
#: RENAME and carries no such lesson.)
StatusBearingRow = Union[Transaction, Transfer]

#: **It lives in this leaf rather than beside the mechanics that read it**, and
#: the reason is a dangling annotation a neutral review measured (2026-08-18):
#: ``reject_figure_without_settled_status`` annotates its row with this name,
#: and ``_seam`` imports ``_refusals`` -- so declaring it there left the string
#: annotation unresolvable, raising ``NameError`` under ``get_type_hints`` while
#: pylint saw nothing (it does not resolve names inside string annotations).
#: The lowest module that needs a shared name is where it belongs.


def reject_settle_day_without_settled_status(
    status_id: int, settle_day: Optional[SettleDay],
) -> None:
    """Refuse a settle day supplied for a status that is not settled.

    **One half of the settled-iff-dated invariant, stated once** (plan step
    X-f1, finding **N-183**).  A row carries the civil day its money moved if
    and only if it is in a settled status (Paid or Received), so a day
    handed in beside a Projected / Credit / Cancelled status is not a value to
    store -- it is a request to record a payment that has not happened.

    It is a module-level function rather than an inline check inside
    :func:`apply_status_change` because ONE caller has to ask the question
    BEFORE the seam can: ``transfer_service.create_transfer`` validates
    ``spec.settled_on`` against ``spec.status_id`` before any row exists, and
    for an unsettled create it never reaches the seam at all (its settle branch
    is gated on the status being settled), so the day would be silently
    dropped.  Two moments, one rule -- the alternative is the same sentence
    written twice, which is this arc's own root cause 1.

    Args:
        status_id: The ``ref.statuses.id`` the row is (or would be) in.
        settle_day: The day supplied beside it and how it is known
            (:class:`app.services.settle_day.SettleDay`), or ``None`` when the
            caller supplied none.  ``None`` is always accepted -- it means "no
            day was offered", which is legal for either kind of status.

    Raises:
        ValidationError: When *settle_day* is not ``None`` and *status_id* is
            not one of :func:`~app.utils.balance_predicates.settled_status_ids`.

            **A ``ValidationError`` (a 400) rather than a programming error,
            and NO form can reach it yet.**  Measured: no schema declares a
            settle day, no template renders one, and no ``app/`` caller passes
            ``settled_on`` to ``transfer_service.update_transfer`` -- so today
            the only way here is a service-layer mistake, for which a 400 is
            generous.  Plan step **X-f1c** is what makes it a user mistake with
            a correction: it puts the field on the full-edit door, and
            submitting a day while moving the row back to Projected becomes an
            ordinary form error.  The class is chosen for the door that is
            coming rather than re-picked when it lands; saying so beats a
            rationale in the present tense that is not yet true.
    """
    if settle_day is None:
        return
    if status_id in settled_status_ids():
        return
    raise ValidationError(
        f"A settle day ({settle_day.day.isoformat()}) was supplied for status "
        f"{status_id}, which is not a settled status.  A row records the day "
        "its money moved only while it is settled (Paid or Received); "
        "mark it settled to give it a day, or clear the day to "
        "leave it projected."
    )


def reject_figure_without_settled_status(
    row: StatusBearingRow, new_status_id: int,
) -> None:
    """Refuse a submitted FIGURE for a status that settles nothing.

    **The ONE statement of that refusal for both tables**, and it is here
    because it had two (plan step X-au-c3).  ``transaction_service._door`` and
    ``transfer_service._update`` each spelled the same sentence in their own
    words, which is this arc's own root cause 1 -- two spellings of one money
    rule -- and the two would drift the first time either message was improved.

    **It is a refusal rather than a drop, and the distinction is a user's typed
    number.**  Both update schemas set ``Meta.unknown = EXCLUDE``, so a figure
    this tier discarded would vanish with no message.  A FORM never reaches it:
    :func:`~app.services.status_seam.figure_for_status` drops an echoed box on the way OUT of the
    settled band, which is ruling **R-EG**'s argument applied to the figure, so
    what lands here is a caller asserting both facts on purpose.

    The noun comes from the MODEL class, never from a caller-supplied label --
    the reason :data:`~app.services.status_seam.StatusBearingRow` gives for the
    dating branch it makes the same way.

    Args:
        row: The row the figure arrived for.  The CALLER establishes that a
            figure did arrive -- this asks only whether the status can hold one.
        new_status_id: The ``ref.statuses.id`` the row is moving to, which for a
            figure-only edit is the row's own (an identity transition).

    Raises:
        ValidationError: When *new_status_id* is not a settled status.  A 400 at
            either route.
    """
    if new_status_id in settled_status_ids():
        return
    noun = "Transfer" if isinstance(row, Transfer) else "Transaction"
    raise ValidationError(
        f"{noun} {row.id} is not settling, so a figure has nothing to record: "
        "an amount here states what MOVED. Mark it paid to record what left "
        "the account, or change its own amount to re-price the plan."
    )


def reject_settlement_without_settled_status(
    status_id: int, settlement: Optional[Settlement],
) -> None:
    """Refuse a settlement record offered for a status that is not settled.

    The settlement twin of :func:`reject_settle_day_without_settled_status`, and
    it exists for the same reason on the same rule: a row records what moved if
    and only if it is in a settled status, so a record handed in beside a
    Projected / Credit / Cancelled status is not a value to store -- it is a
    request to book money that has not moved.

    ``None`` is always accepted here and means "no record was offered", which is
    legal for either kind of status; :func:`apply_status_change` is what refuses
    the other direction -- a row ENTERING the settled band with no record.

    Args:
        status_id: The ``ref.statuses.id`` the row is moving to.
        settlement: The record offered beside it, or ``None``.

    Raises:
        ValidationError: When *settlement* is not ``None`` and *status_id* is
            not one of :func:`~app.utils.balance_predicates.settled_status_ids`.
    """
    if settlement is None:
        return
    if status_id in settled_status_ids():
        return
    raise ValidationError(
        f"A settlement record was supplied for status {status_id}, which is "
        "not a settled status.  A row records what moved only while it is "
        "settled (Paid or Received); mark it settled to record a "
        "figure, or leave it projected, which records nothing."
    )


def reject_settle_day_without_a_record(
    row: Transaction,
    settle_day: Optional[SettleDay],
    settlement: Optional[Settlement],
) -> None:
    """Refuse a settle DAY on a row that neither records nor is recording one.

    **``ck_transactions_settle_day_needs_a_record`` said in words**, at the one
    door that writes both columns.  The constraint is the surviving half of a
    repealed biconditional: a row asserting the day its money moved must record
    WHAT moved, while the reverse -- a record with no day -- is the legal
    RETAINED state a revert leaves.

    **It exists because the constraint was the only thing saying it, and a
    CHECK cannot hold a conversation.**  The full-edit popover offers the
    settle-day box to an UNDATED settled row deliberately -- that row most needs
    to state the real day (finding **N-181**) -- but a row predating the
    settlement record carries no record either, so stating the day alone
    violated the CHECK and surfaced as an ``IntegrityError`` rendered "invalid
    reference": a message naming nothing the user could act on, for a save no
    re-typing would fix.  The repair is to state BOTH halves, which the Actual
    box beside the day box makes expressible, and this message says so.

    Three ways past it, and they are the three legal shapes: no day is being
    asserted; a record arrives in the same call (every settle); or the row
    already carries one (every ordinary day correction).

    Args:
        row: The row being written.
        settle_day: The day this call asserts and how it is known
            (:class:`app.services.settle_day.SettleDay`), or ``None``.
        settlement: The record this call writes, or ``None``.

    Raises:
        ValidationError: When a day is asserted for a row that records nothing
            and is being given nothing to record.  A 400: it is reachable from
            the correction box on a legacy row, and the message is the repair.
    """
    if settle_day is None or settlement is not None:
        return
    if row.settled_basis_id is not None:
        return
    raise ValidationError(
        f"Transaction {row.id} records nothing that moved, so it cannot state "
        "the day the money moved: the two are one assertion. Enter what the "
        "bank actually took in the Actual box as well as the day, and both are "
        "recorded together."
    )


def day_is_in_the_future(day: date) -> bool:
    """Return whether *day* is later than the user's today (ruling **R-EJ**).

    **The PREDICATE behind :func:`reject_future_settle_day`, published so a
    SCREEN can ask it.**  A door that raises answers *may I do this*, and a
    control that must not be rendered needs *would this be refused* -- and the
    project's own rule is that a screen may not offer a control whose
    submission can never succeed, a shape the statement-match package has now
    closed six times.  Before this, the only way to ask was a second
    ``day > display_today()`` comparison at the call site, which is one money
    rule spelled twice.

    Added at plan step ``bank_import:X-gf-1`` for
    :func:`app.services.statement_match._leftovers._recordable_inflows`, whose
    card would otherwise render a tick for a bank line the bank dates in the
    future -- and whose door wrote the row before this refusal fired.  Found by
    adversarial financial review 2026-08-27.

    Args:
        day: The civil day to test.

    Returns:
        Whether it is after the user's today
        (:func:`~app.utils.dates.display_today`), which is the display-timezone
        clock every settle door already reads.
    """
    return day > display_today()


def reject_future_settle_day(settle_day: Optional[SettleDay]) -> None:
    """Refuse a settle day that has not happened yet (ruling **R-EJ**).

    A row carries a settle day if and only if it is settled, and settled means
    the money HAS moved -- so a day in the future is not a fact about money, it
    is a forecast in a fact column.  ``Transaction``'s class docstring specified
    this rule before any door could reach it: *"a 'not in the future' rule is
    not expressible in a CHECK (it is not immutable) and lives at the write door
    instead, exactly as ruling R-M's purchase-date guard does for an entry."*
    Plan step X-f1c is that write door, and the first one where a USER supplies
    the day.

    **What it costs to omit, MEASURED end to end through the live routes** (two
    independent derivations, one number -- the step's own trace and a neutral
    adversarial review).  A settled source counts from its own ``settled_on``
    (``cash_ledger.dated_deltas``), and
    :func:`app.services.cash_ledger.walk_cash_ledger` absorbs one into an
    assertion only when the assertion is dated ON OR AFTER it -- so a
    future-dated settle rides on top of every assertion until that day arrives.
    On a ``$1,000`` anchor a ``$100`` expense settled three days ago reads
    ``$900``; PATCH its day forward and the route answers ``200`` with the
    balance back at ``$1,000``.  **Already-spent money, back in the projection.**

    **And it is the LIKELY input, not an exotic one.**  The correction box tells
    the user to correct the day against their statement, and a statement's most
    common disagreement is a PENDING item carrying a FUTURE posting date.

    **The opposite rule on ``TransactionEntry.settled_on`` is not a
    contradiction.**  A future ENTRY posting day is the CONSERVATIVE direction --
    no assertion closes over it, so the debit stays reserved and the balance
    stays low -- so that door bounds only from below and says so.  A future
    ``Transaction.settled_on`` points the other way: it takes settled money OUT
    of the balance.  The two fields' rationales do not transfer.

    **The recorded clearing fact does not undo that** (plan step X-f3a-1, ruling
    **R-FL**), and an adversarial review was right to ask: a LINKED purchase is
    cleared whatever its day says, so a linked entry moved to a future day would
    release its reservation and put already-reserved money back in the
    projection -- the very failure this refusal exists to prevent, arriving
    through the exempt door.  It cannot happen, because
    ``entry_service.update_entry`` RELEASES the link whenever the posting day
    moves: a future-dated entry is therefore always unlinked, and the day rule
    answers it exactly as this paragraph describes.

    It lives here, beside :func:`reject_settle_day_without_settled_status` and
    the ``datetime`` refusal, for the reason those are here: ONE door, every
    write path, a value the seam does not accept rather than a check each caller
    has to remember.  Both date inputs also carry ``max`` = today, so the browser
    refuses first and this is the backstop -- the same layering
    ``accounts/form.html`` uses for an anchor's ``observed_on``.

    **The clock is the USER's** (ruling R-DH (b)).  ``display_today()``, never
    ``date.today()``: the process's UTC day is already tomorrow at 8pm Eastern,
    so the server's clock would refuse a settle the user is making right now.

    Args:
        settle_day: The candidate day and how it is known
            (:class:`app.services.settle_day.SettleDay`), or ``None`` when none
            was supplied.  ``None`` is always accepted -- it means "derive the
            day from the status", which is the everyday path.  The BASIS is not
            read: no provenance makes a day that has not happened a fact, and a
            bank line dated tomorrow is a pending item rather than a posting.

    Raises:
        ValidationError: When *settle_day*'s day is later than the user's today.  A
            400 rather than a programming error, because plan step X-f1c makes
            it reachable by an ordinary user typing in the correction box; the
            route layer renders it as a designed error fragment.
    """
    if settle_day is None:
        return
    if not day_is_in_the_future(settle_day.day):
        return
    today = display_today()
    raise ValidationError(
        f"A settle day of {settle_day.day.isoformat()} has not happened yet "
        f"(today is {today.isoformat()}).  A row records the day its money "
        "moved, so the day cannot be in the future -- if the payment is "
        "scheduled rather than made, leave it Projected."
    )
