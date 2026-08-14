"""
Shekel Budget App -- Anchor True-up Service

Single authoritative implementation of the anchor true-up mutation
for every account kind that carries an append-only anchor history:
checking accounts via :class:`AccountAnchorHistory` and loan
accounts via :class:`LoanAnchorEvent` (E-18 / Commit 16, decision
D-C).  Both call sites switch on the same
:class:`AnchorTrueUpOutcome` enum so the route layer's response
composition is uniform.

The checking-anchor path -- :func:`apply_anchor_true_up` -- backs the
grid and Net Worth Cockpit HTMX anchor-edit endpoint (``true_up``).
Its transactional core:

  1. Append an ``AccountAnchorHistory`` row -- an account, a balance, and
     the day it was true.
  2. Commit.

**Step 3 used to be a bulk-clear of entry flags, and its deletion is ruling
R-DH (d)** (``docs/audits/balance_architecture/archive/anchor_settle_partition.md``,
plan step S1-c).  A true-up flipped ``is_cleared = TRUE`` on every entry dated
on or before the SERVER's today, so whether a purchase counted as already
inside the asserted balance depended on the order two buttons were pressed:
record the purchase then true up and it cleared, true up then record and it
never did.  There is no flag to write now -- reconciliation is derived from the
purchase's own recorded posting day -- so the true-up appends one assertion
and nothing else.  It mutated ``accounts`` too until ruling R-EH deleted the
anchor cache columns (plan step X-f1c3c); the assertion IS the state now.
Confirming which outstanding purchases the
statement showed is a SEPARATE, user-driven step
(``reconcile_service.record_settled_days``) that the route offers after this commit
lands; keeping it out of this transaction is deliberate, because a same-day
re-assert is swallowed here as idempotent success and any reconciliation riding
along would be silently rolled back with it.

The loan-anchor path -- :func:`apply_loan_anchor_true_up` -- backs
the loan dashboard's "Record loan balance as of date D" form.  It
shares the enum contract but operates on a different model and a
different mutation set:

  1. Append a ``user_trueup`` :class:`LoanAnchorEvent` row (the
     table is structurally append-only; no UPDATE/DELETE).
  2. Commit.

A loan trueup never mutates ``LoanParams`` -- the resolver
(:func:`app.services.loan_resolver.resolve_loan`) reads the latest
event to derive the displayed current balance, monthly payment,
schedule and payoff date, so a new event immediately changes every
loan surface consistently without writing a column.

**Both paths are APPEND-ONLY now, and the checking path's optimistic lock
went with the columns** (ruling R-EN, plan step X-f1c3c).  The C-17
``version_id_col`` on ``Account`` raised ``StaleDataError`` when a concurrent
commit bumped the counter between a route's SELECT and its UPDATE -- but a
true-up no longer UPDATEs ``accounts`` at all, so there is no such flush and no
``STALE_CONFLICT`` outcome on either path.  No ASSERTION is lost by that: two
assertions of different balances are two FACTS, the later-observed one is
current, and neither is overwritten.  This is the contract the loan path has
documented since Commit 16, now shared.

**The posted LEDGER is a separate question, and the shared contract never
answered it.**  Both paths re-sync the ledger after appending, and a re-sync is
a reconcile-to-target: read what is posted, subtract, INSERT the difference.
Two of those interleaved both subtract the same posted state.  The cash side
had been serialised by accident (the deleted ``version_id`` UPDATE autoflushed
and took a row lock before the walk) and the loan side never was at all.  Since
plan step X-f1c3c the reconcile takes a per-owner advisory lock for itself
(:mod:`app.services.user_write_lock`), so the guarantee belongs to the code
that needs it rather than to whichever caller happened to write a row first.

**An assertion is refused only when it CHANGES NOTHING, and that rule is this
module's** (ruling **R-EQ**, plan step X-f1c4b).  Both doors take the owner's
write lock, read the assertion that currently GOVERNS what the submission would
govern, and append only when the submission differs from it.  An identical
submission writes nothing and reports ``UNCHANGED``, which the routes render as
success -- so a double-click, a network retry and a back-and-resubmit are
absorbed, while a correction never is.

**Both doors carried a content-keyed UNIQUE INDEX for this until X-f1c4b, and
the index could not express the rule.**  ``uq_anchor_history_account_period_balance_day``
covered ``(account_id, anchor_balance, observed_on)`` and
``uq_loan_anchor_events_acct_date_bal_day`` covered ``(account_id, anchor_date,
anchor_balance, ((created_at AT TIME ZONE 'UTC')::date))``; each write door
translated the violation into idempotent success.  **A transport retry and a
deliberate re-assertion are byte-identical by construction**, so a key over the
row's own values must mis-classify one of them -- and it mis-classified the
correction: assert ``$500`` for a day, correct it to ``$600``, then re-assert
``$500`` for that day, and the index rejected the third write while the app
reported it saved and kept rendering ``$600``.  Comparing against the governing
row instead is exact in both directions, because "did this change anything" is a
question about STATE, which the row's contents alone cannot answer.

Two consequences worth stating, both measured before the indexes were dropped:

  * **The remaining exposure is a surplus audit row, not money.**  Two truly
    concurrent identical submissions could each pass the compare -- except they
    cannot, because the compare runs under the same per-owner lock the reconcile
    takes (:mod:`app.services.user_write_lock`), taken at the TOP of the door so
    the waiter re-reads the winner's row.  Even without it the cost was
    ``$0.00``: a duplicate assertion's correction delta is zero, a zero delta
    emits no legs (``account_posting_service._anchors``), and same-day
    corrections merge on one key.
  * **The lock moved EARLIER, not merely inward, and the "first lock" property
    belongs to the CALLER.**  It was taken inside the reconcile, several
    statements in; both doors now take it before their first read.  That is only
    the invariant :mod:`app.services.user_write_lock` states ("this lock must be
    the FIRST lock a transaction takes") when nothing the caller did earlier has
    already taken a row lock -- and ``lock_user_writes`` runs through
    ``db.session.execute``, which AUTOFLUSHES, so a caller that assigns to an ORM
    row before calling here emits that ``UPDATE`` first and inverts the order
    silently.  The three HTMX/loan doors do only reads beforehand.
    ``routes/accounts/crud.update_account`` took the lock at its own top for the
    same reason, against a deadlock between two of its OWN branches reproduced
    against a real database; **plan step X-f1e deleted the branch that raced**,
    so that route no longer reaches this module at all and keeps the lock purely
    to hold the invariant on its type-change path.  **None of that closes
    finding N-193**, whose cycle is settle-versus-truncate and is untouched.

Pre-Commit-16 this consolidation eliminates two byte-identical
``try/except`` blocks in ``app/routes/accounts.py``; the loan
principal true-up (E-18) introduced by Commit 16 will extend this
service rather than paste a third copy.

Services boundary: no Flask imports, no ``request``/``session``/
``current_app``/``render_template``.  The route owns the response
rendering; this module returns an outcome enum the route translates
into its template/header pair.  The session itself is the project's
SQLAlchemy ``db.session`` proxy, which IS Flask-bound -- consistent
with every other service in ``app/services/`` (e.g. ``entry_service``,
``balance_resolver``).

**A cash balance is asserted at exactly ONE door, and that is plan step X-f1e**
(finding **N-195**).  ``routes/accounts/crud.update_account`` -- the full-form
account edit -- used to be a second one: it accepted an ``anchor_balance`` and
staged an assertion through :func:`stage_anchor_true_up`, sharing the definition
but not the DECISION.  The two answered the same submission differently, because
that form PRE-FILLS the current balance: saving a rename re-submitted it
unchanged, which the route read as "no change" while ruling R-EQ's rule here
reads a submission as new when it changes what GOVERNS, the day included.
Aligning the route on this module's rule would have been worse -- a rename would
then assert today's balance and absorb purchases the user never reconciled -- so
the SURFACE was deleted rather than the gate.  What remains is
:func:`apply_anchor_true_up`, reached from ``accounts.true_up`` on every screen
that shows a balance.

**And the TABLE now has one writer too, which is a different claim** (ruling
**R-ES**, plan step X-f1e2).  One door means one place a USER asserts a balance;
one writer means one place a ROW is appended, and until X-f1e2
``account_service.create_account`` was the second -- it constructed the
origination assertion itself, so that one row was written with no owner write
lock, no ruling R-EQ compare and no shared log line.  It calls
:func:`stage_anchor_true_up` now.  The ``notes`` column those two writers
existed to be told apart in went with the ruling: nothing in ``app/`` read it,
and it was a second definition of "the opening" beside the positional one
:func:`app.services.cash_ledger.cash_anchor_facts` sets.

One consequence is worth stating where the rule lives: **the amortizing-kind
refusal is no longer duplicated at a route validator.**
``_validate_update_account`` carried its own copy because that door reached the
stager without passing :func:`apply_anchor_true_up`'s gate; with the door gone,
:class:`AmortizingAccountAnchorError` is raised in one place.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.loan_anchor_event import LoanAnchorEvent
from app.services import (
    account_posting_service,
    cash_ledger,
    loan_posting_service,
    pay_period_service,
)
from app.services.user_write_lock import lock_user_writes
from app.utils.dates import display_today


logger = logging.getLogger(__name__)


class AmortizingAccountAnchorError(ValueError):
    """Raised when a CASH anchor true-up targets an amortizing loan.

    A loan's balance is never ``accounts.current_anchor_balance`` -- it is
    ledger-derived, and its true-up path is
    :func:`apply_loan_anchor_true_up` (an append-only
    :class:`LoanAnchorEvent` plus a posting re-sync).  Writing the cash
    column instead creates a second, stored, never-reconciled loan balance
    (plan-of-record finding B-15: the real Mortgage's column was set to
    $1.00 with an HTTP 200 while the ledger said $177,277.97, and the grid
    then rendered the $1.00).  The cash entry point refuses the kind so
    that cannot recur; routes translate this into a client error naming
    the loan path (ruling D4, step A1).
    """


class AnchorTrueUpOutcome(enum.Enum):
    """Discriminant returned by :func:`apply_anchor_true_up`.

    The route picks a partial template + status code + headers from
    this; the service never touches the response layer.

    Members:
        COMMITTED: The new ``AccountAnchorHistory`` row was written
            and the commit succeeded.  Route renders the success
            partial (200) and, where relevant, the OOB swap + HX-Trigger.
        UNCHANGED: The submission matched the assertion that already
            GOVERNS, so nothing was written and the session was rolled
            back (ruling R-EQ).  Route treats this as idempotent success
            -- the state the caller asked for is the state that stands --
            and renders the success partial without re-issuing the commit.
            **It was ``DUPLICATE_SAME_DAY`` until plan step X-f1c4b**, when
            it named a unique-index violation rather than a decision: the
            name said "you sent this twice", which is exactly the thing a
            content key cannot know.  This one says what is true.
    """

    COMMITTED = "committed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class ObservationDay:
    """A civil day that has passed both of an assertion's bounds.

    **The bound is applied ONCE per write, and this type is what makes "once"
    structural** (plan step X-f1e2, ``ReconciledThrough``'s precedent one
    question over).  :func:`resolve_observation_day` alone mints one and
    :func:`stage_anchor_true_up` accepts nothing else.

    **Twice is not free, because the rule is CLOCK-DEPENDENT.**  The floor is
    ``min(earliest pay period start, today)``, so for an owner whose schedule is
    entirely in the future it moves FORWARD at midnight and a second application
    refuses the day the first produced -- and its refusal landed after the
    account row was flushed.  That is ``resolve then guard`` reading the clock
    twice, the defect ruling **R-ER** deletes, one layer up.

    **The attribute is NOT called ``day``, and that is not cosmetic.**
    ``datetime.date`` already has a ``.day`` -- the day of the MONTH -- so a raw
    date slipping past the annotation would satisfy the accessor and put an
    integer into an SQL bound.  A name a ``date`` cannot answer makes that an
    ``AttributeError`` at the first access instead.

    Attributes:
        civil_day: The bounded civil day, in the user's timezone (R-DH (b)).
    """

    civil_day: date


def resolve_observation_day(
    user_id: int, observed_on: date | None,
) -> ObservationDay:
    """Return the civil day an assertion is dated at, refusing an undatable one.

    **The ONE rule both writers of :class:`AccountAnchorHistory` ask** (ruling
    **R-ER**, plan step X-f1c4c): the origination assertion
    (``account_service.create_account``) and every later one
    (:func:`stage_anchor_true_up`).  It lives in THIS module because this module
    owns what an assertion is.  It was ``account_service``'s private
    ``_reject_undatable_observation`` while the factory was its only caller, and
    a second module reaching a private name is finding **N-33**'s shape rather
    than a way to share a rule.

    **It RESOLVES and refuses in one call, which closes the DEFAULT's half of a
    clock race and does not pretend to close the other half.**  Both callers
    previously defaulted an absent day to ``display_today()`` and then handed
    the result to a guard that read the clock AGAIN -- so a midnight tick
    between the two lines could refuse the function's own default.  Reachable,
    if barely: for a user whose earliest pay period starts tomorrow the floor
    becomes tomorrow the instant the day rolls, and the default (today) is then
    below it.  ``account_service``'s note about that race considered only the
    future arm, where the ``>`` test is indeed forgiving, and missed the floor.
    An absent day now returns *today* directly, so that case is unrepresentable:
    today is assertable by construction -- it is not in the future, and the
    floor is ``min(earliest, today)``.

    *A first version of this paragraph claimed the fusion made the race
    unrepresentable outright.  A neutral review refuted it and the correction is
    kept here rather than dropped, because the residue is a real window:* both
    forms PREFILL today into their date box (``routes/accounts/anchor``'s
    ``observed_on_value``, ``templates/accounts/form.html``), so the ordinary
    path submits a SUPPLIED day and takes the branch below.  For that branch the
    input's bound was computed at RENDER time and this floor is read at SUBMIT
    time, so the window is minutes or hours rather than two adjacent statements.
    It bites only a schedule that is entirely in the future, it errs toward
    refusing rather than accepting, and the refusal is rendered in place
    (``accounts._anchor_editor_error``) rather than swallowed.

    ``observed_on`` is USER-SUPPLIED and it is not merely a label: it opens the
    modelled-return window (``balance_at._asset_fold._AccrualWindow``, which
    materialises EVERY calendar day from it to the reader's horizon) and it is
    the first period a payroll contribution can be modelled into
    (``_asset_contributions``).  An unbounded value is therefore both a
    correctness defect and a work amplifier: a Property or 401(k) asserted "as
    of" year 1 would fabricate contribution history for every past period and
    fold over three quarters of a million days on every dashboard render.

    Two bounds, and each refuses for its own reason:

    * **Not in the future.**  A balance cannot have been observed on a day the
      user has not seen.  The loan door states the same rule on a different
      clock, which is finding **N-197**.
    * **Not before the earlier of the schedule's start and today.**  The
      accrual-window reason above; the floor takes the EARLIER of the two so a
      user whose periods are all still in the future can nonetheless assert what
      they hold today.  The bound is
      :func:`app.services.pay_period_service.earliest_recordable_day`, the SAME
      floor ruling R-EL gave the settle door -- one implementation, so the
      anchor doors and the settle doors cannot drift apart on where recordable
      history begins.  **That is a claim about the FLOOR only.**  "Not in the
      future" is still stated in three modules with three messages and two
      clocks -- here, ``status_seam.reject_future_settle_day``, and
      ``schemas/validation/loans.LoanAnchorTrueupSchema`` on ``date.today()``
      (finding **N-197**) -- so this function did not reduce that count.

    **It does NOT refuse an owner with no pay periods, and that split is ruling
    R-ER.**  The rule it replaced did, on the stated ground that "the account's
    anchor has a period to reference" -- which ruling R-EO falsified by deleting
    ``account_anchor_history.pay_period_id``.  The live reason belongs to
    ACCOUNT CREATION rather than to a day (the opening's posting reconcile
    derives each correction's period from the owner's calendar, finding
    **N-192**), so it stays there as ``account_service``'s own precondition.
    Asking it here would have re-imposed on the true-up door exactly the refusal
    ruling R-EO deleted from it -- a balance the user typed, refused for want of
    a budgeting artifact that has nothing to do with what their bank holds --
    and would have answered a true-up with a message about creating an account.

    **The clock is the USER's** (ruling R-DH (b)).  ``display_today()``, never
    ``date.today()``: the process's UTC day is already tomorrow at 8pm Eastern,
    so the server's clock would refuse an assertion the user is making right
    now, and would default one made this evening to tomorrow.

    Args:
        user_id: The owner whose pay-period schedule sets the floor.
        observed_on: The candidate civil day, or ``None`` to take the default.
            ``None`` is what an omitted form field and a caller with no opinion
            both mean: "the balance I am asserting is true now".

    Returns:
        The :class:`ObservationDay` the assertion carries -- *observed_on* when
        one was supplied and passed both bounds, else the user's today.  **A
        TYPE rather than a bare date, so the bound cannot be applied twice**:
        see :class:`ObservationDay` for the clock-roll and concurrent-rebuild
        windows a second application opens.

    Raises:
        ValidationError: When the day is in the future or precedes the owner's
            recorded history.  A 400 rather than a programming error: both are
            ordinary input from a date box, and each message names the offending
            value and the bound it broke so the surface can render it verbatim.
    """
    today = display_today()
    if observed_on is None:
        return ObservationDay(today)
    if observed_on > today:
        raise ValidationError(
            f"Cannot assert a balance for {observed_on.isoformat()}: that day "
            f"has not happened yet (today is {today.isoformat()}).  A balance "
            "states what an account held on a day you have already seen."
        )
    floor = pay_period_service.earliest_recordable_day(user_id)
    if observed_on < floor:
        raise ValidationError(
            f"Cannot assert a balance for {observed_on.isoformat()}: your "
            f"recorded history starts on {floor.isoformat()}.  Use a day on or "
            "after that, or generate earlier pay periods first."
        )
    return ObservationDay(observed_on)


def stage_anchor_true_up(
    *,
    account: Account,
    new_balance: Decimal,
    observed_on: ObservationDay,
) -> bool:
    """Append a dated balance ASSERTION for ``account`` without committing.

    The flush-only in-memory core of :func:`apply_anchor_true_up`.  It does NOT
    clear past-dated entries and does NOT commit -- the caller owns the
    transaction.

    **It is the ONE writer of :class:`AccountAnchorHistory`, and that is ruling
    R-ES** (plan step X-f1e2).  Its two callers are the ``apply`` wrapper
    immediately below (every later assertion, from every screen that shows a
    balance) and :func:`app.services.account_service.create_account` (the
    origination).  The account factory used to construct the row itself, which
    made the origination the one assertion in the app written without the
    owner's write lock, without ruling R-EQ's did-this-change compare and
    without the shared log line; routing it here is what makes those rules
    properties of the TABLE rather than of whichever function did the INSERT.

    *The history is worth one sentence because it inverts twice.*  The split
    existed to be SHARED with ``routes/accounts/crud.update_account``; plan step
    X-f1e1 deleted that door (finding **N-195**) and left this function with a
    single caller and a callerless ``notes`` parameter (finding **N-198**).
    Ruling R-ES then deleted the ``notes`` COLUMN -- unread by anything in
    ``app/``, and a second definition of "the opening" beside the positional one
    :func:`app.services.cash_ledger.cash_anchor_facts` already sets -- and gave
    the function its second caller back on better ground: not two SURFACES
    sharing a definition, but two EVENTS sharing a write door.

    **It decides whether there is anything to append, and that decision is
    ruling R-EQ.**  It takes the owner's write lock, asks
    :func:`app.services.cash_ledger.resolve_anchor` which assertion currently
    governs, and appends only when the submission differs from it.  Three
    properties are load-bearing and each is here rather than in a caller:

    * **The lock precedes the read.**  A compare-then-append is a
      read-modify-write, so an unserialised one lets two concurrent submissions
      each read the pre-state and both append.  It is taken here, with the read
      it protects, rather than at the door.  It is NOT a guarantee that the
      advisory lock is the transaction's FIRST lock: ``lock_user_writes``
      executes a statement and therefore AUTOFLUSHES, so a caller holding a
      dirty ORM row emits that ``UPDATE`` -- and takes its row lock -- before
      this line.  That ordering is the CALLER's to keep (finding **N-193**), and
      it is why ``routes/accounts/crud.update_account`` still takes the same
      re-entrant lock at its own top even though plan step X-f1e stopped it
      reaching this function at all.
    * **The governing assertion is asked for, never re-derived.**
      :func:`app.services.cash_ledger.governing_anchor_on` shares ONE query with
      ``resolve_anchor`` -- same tie-breaks, ``(observed_on, created_at, id)``
      DESC, matching the walk's replay -- and differs only in its horizon.  A
      local ``MAX``/``first()`` here would be a second statement of that rule,
      which is the defect class this module's own history is made of.
    * **The comparison is against the row governing the SUBMITTED DAY, not the
      account's latest row.**  Two things follow, and both were measured.  The
      deleted unique index asked "does an identical row exist anywhere", so
      re-asserting a balance that had since been superseded was refused and
      reported as saved.  But comparing against the LATEST row instead has the
      mirror-image fault: a submission for an EARLIER day can never equal it, so
      a double-click on a back-dated correction appends every time -- reproduced
      on the loan door by two independent reviews of this step.  A submission
      for day D can only change what is true at or after D, so D is the horizon.
      **Plan step X-f1c4c is what made that reachable here**, by giving the cash
      door the date field the loan door has carried since Commit 16; the rule was
      installed one leaf earlier, deliberately, so a user-typed day never met the
      content-keyed index it replaced.

    **The day arrives ALREADY BOUNDED, and it is a TYPE that says so** (ruling
    **R-ER** for the rule, plan step X-f1e2 for the type).
    :func:`resolve_observation_day` supplies the default and enforces both
    bounds, so the same two rules govern the origination assertion
    ``account_service.create_account`` writes and every later one written here.
    This function took a raw ``date | None`` and re-resolved it, which read as a
    writer declining to trust its caller and was really the clock being read
    twice: the floor is time-dependent, so a midnight roll -- or a schedule
    rebuild committing -- between a caller's resolve and this one refuses the day
    the caller just produced.  Both doors resolve exactly once now, each BEFORE
    the lock, because a refused submission must not take the owner's write lock
    and the resolver takes none of its own (one aggregate SELECT over pay
    periods).

    **What it stages shrank twice, and both shrinks are the same ruling
    applied one table apart.**  It used to re-point ``current_anchor_period_id``
    and write ``current_anchor_balance`` before appending the row; ruling R-EH
    deleted those columns as a denormalized copy of the row itself.  It used to
    file the row against a pay period; ruling R-EO deleted THAT, because a
    balance assertion is a fact about a bank and a schedule operation must not
    be able to destroy it.  What is left is the assertion: an account, a
    balance, and the day it was true.

    The amortizing-kind gate (:class:`AmortizingAccountAnchorError`) lives on
    :func:`apply_anchor_true_up`, deliberately NOT here.  It was placed there so
    the second door -- which refused the kind at its own route validator -- was
    not gated twice; plan step X-f1e deleted that door and its duplicate gate,
    so the rule is now stated exactly once, on the only public entry point that
    asserts a cash balance.

    Args:
        account: An attached :class:`Account` row.  Caller owns the
            ownership check.
        new_balance: The validated :class:`Decimal` balance being asserted.
        observed_on: The :class:`ObservationDay` the balance is asserted TRUE
            for (ruling **R-DH**).  Only :func:`resolve_observation_day` mints
            one, so an unbounded day cannot reach this line.

    Returns:
        ``True`` when an assertion was appended to the session; ``False`` when
        the submission matched the governing assertion and nothing was staged.
        The caller decides what an unchanged submission means for ITS
        transaction: :func:`apply_anchor_true_up` rolls back and reports
        ``UNCHANGED``.

    **It raises NOTHING, and saying so is a correction.**  It documented a
    ``RuntimeError`` "when the account carries no assertion at all, from
    ``cash_ledger.resolve_anchor``" -- but it does not call ``resolve_anchor``.
    It calls :func:`app.services.cash_ledger.governing_anchor_on`, which returns
    ``None`` on an account with no history precisely because that is an honest
    answer to a WRITER where it is a broken invariant to a reader.  The claim
    was true of an earlier draft and load-bearing in the wrong direction: an
    account with no assertions is exactly the state
    ``account_service.create_account`` is in when it calls here.  The
    ``ValidationError`` the day bounds raise now belongs to each door's own
    :func:`resolve_observation_day` call, above this function.
    """
    day = observed_on.civil_day
    # Ruling R-EQ: the lock comes before the READ the decision below is made
    # from.  See the function docstring for why it is here and not at either
    # door.
    lock_user_writes(account.user_id)
    governing = cash_ledger.governing_anchor_on(account.id, day)
    if governing is not None and (
        (governing.observed_on, governing.balance) == (day, new_balance)
    ):
        return False

    db.session.add(AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=new_balance,
        observed_on=day,
    ))
    # Both write doors reach this line, so the audit trail is uniform whichever
    # event wrote the assertion -- an origination or a later true-up.  The day
    # is the fact plan step X-f1c4c exists to record.
    logger.info(
        "Anchor assertion staged: account %d at $%s as of %s",
        account.id, new_balance, day.isoformat(),
    )
    return True


def apply_anchor_true_up(
    *,
    account: Account,
    new_balance: Decimal,
    observed_on: date | None = None,
) -> AnchorTrueUpOutcome:
    """Append a balance assertion for ``account``, re-base its postings, commit.

    Stages the assertion via :func:`stage_anchor_true_up`, re-bases the
    account's posted anchor corrections, and commits.  Returns an
    :class:`AnchorTrueUpOutcome` discriminant the caller translates into its
    rendered response.

    **The C-17 optimistic lock left this path at plan step X-f1c3c** (ruling
    R-EN), and the reason is that the path stopped writing the row the lock
    guarded.  ``version_id`` increments when the ORM UPDATEs ``accounts`` and
    on nothing else; once ruling R-EH deleted the anchor cache columns a
    true-up only INSERTs a history row, so ``StaleDataError`` became
    structurally unreachable here.  **Measured** against the dev database and
    rolled back: adding an ``AccountAnchorHistory`` row and flushing leaves
    ``version_id`` at 33, and the very next line writing
    ``current_anchor_balance`` takes it to 34.

    The step accepted what that means rather than working around it.  **An
    assertion history is append-only, so a second tab overwrites no
    ASSERTION**: two assertions of different balances are two facts, the
    later-observed one is current, and neither is lost -- verbatim the contract
    :func:`apply_loan_anchor_true_up` has documented since Commit 16.  This
    makes the cash half the same shape rather than the exception.  Bumping
    ``version_id`` deliberately to keep the lock alive was rejected: a write to
    ``accounts`` whose only purpose is to keep a lock alive is a mechanism with
    no fact under it.

    **That is a property of ONE table in a transaction that mutates three, and
    a first version of this paragraph stated it as though it covered the whole
    call.**  The ``sync_account_anchor_postings_all_scenarios`` below is a
    RECONCILE-TO-TARGET: it reads what is posted, subtracts that from what the
    assertions say, and INSERTs the difference into ``budget.journal_entries``
    / ``budget.account_postings``.  Nothing about append-only makes a
    read-modify-write safe, and the deleted ``version_id`` UPDATE had been
    serialising it by accident -- it autoflushed and took a row lock before the
    walk.  Measured with the interleave forced at the reconcile's read: two
    concurrent true-ups on an account reconciled at ``$4,000.00`` both answer
    200, both assertions survive, the resolver returns one of them -- and the
    linked ledger settles at ``$1,000.00`` against a resolved ``$2,000.00``,
    with the trial balance still ``$0.00`` because the anchor-equity leg
    mirrors the error.  The serialisation is now EXPLICIT and owned by the
    reconcile rather than by a column that happened to sit in front of it:
    :func:`app.services.user_write_lock.lock_user_writes`, taken inside the
    sync, so every other door into that same window (the settle self-heal, the
    direct anchor edit, the pay-period resync) is covered by the same rule.
    The waiting transaction re-reads under READ COMMITTED -- verified as the
    default on dev, test and production, with no override anywhere -- so it
    sees the winner's postings and reconciles to the true merged target.
    **Since plan step X-f1c4b the SAME lock is taken one layer up**, in
    :func:`stage_anchor_true_up`, because ruling R-EQ's compare-then-append is
    itself a read-modify-write.  It is re-entrant and transaction-scoped, so
    taking it twice costs nothing.  On THIS path it is also the transaction's
    first lock -- the route does only reads before calling (measured, statement
    by statement, by a neutral concurrency review) -- but that is a property of
    the route, not of the lock, and finding **N-193** stays open for the settle
    paths regardless.

    **It touches no entry, and that is ruling R-DH (d).**  It used to bulk-flip
    ``is_cleared`` on every entry dated on or before the server's today, which
    made "is this purchase already inside the balance the user just typed"
    an answer decided by the order two buttons were pressed.  The flag is
    gone; reconciliation is derived from each purchase's own recorded posting
    day.  Which outstanding purchases the statement actually showed is a
    separate step the route offers AFTER this commit succeeds
    (``reconcile_service.record_settled_days``) -- and it is deliberately not
    folded in here, because an UNCHANGED submission rolls this transaction
    back, so a reconciliation riding in it would be silently discarded while
    the UI reported a save.

    **The ``try`` / ``except IntegrityError`` around the re-sync left with the
    index** (ruling R-EQ, plan step X-f1c4b).  It existed to catch the F-103
    unique violation that the re-sync's autoflush surfaced and translate it into
    an outcome; with the decision made BEFORE anything is staged, an
    ``IntegrityError`` here is an unexpected constraint failure and its correct
    disposition is the 500 it now gets.

    Args:
        account: An attached :class:`Account` row.  Caller is
            responsible for the ownership check (route uses 404 for
            cross-owner access).
        new_balance: The validated :class:`Decimal` balance being asserted.
            Caller is responsible for constructing this from
            schema-validated form data via ``Decimal(str(...))``.
        observed_on: The civil day the balance is asserted TRUE for, or ``None``
            for the user's today.  Bounded HERE, by the shared
            :func:`resolve_observation_day` -- this function adds no rule of its
            own about the day and must not, or the two write doors would answer
            a back-dated submission differently.  It bounded it inside
            :func:`stage_anchor_true_up` until plan step X-f1e2; the resolve
            moved out to the doors so the account factory, which must refuse
            before it creates a row, does not make it the second application of
            a clock-dependent rule.

    Returns:
        AnchorTrueUpOutcome -- which response the route should render.
        ``UNCHANGED`` when the submission matched the governing assertion, in
        which case this function has rolled the session back and written
        nothing.

    Raises:
        ValidationError: When *observed_on* is in the future or precedes the
            owner's recorded history (:func:`resolve_observation_day`).  Raised
            before anything is staged and before the owner's write lock is
            taken, so the session is clean; the route renders it as a designed
            400 fragment.
        AmortizingAccountAnchorError: When ``account`` is an amortizing
            loan (``account_type.has_amortization``).  A loan's balance
            is ledger-derived and asserted through
            :func:`apply_loan_anchor_true_up`; the cash column must not
            become a second stored loan balance (B-15 / ruling D4).
            Raised BEFORE anything is staged, so the session is clean.
        IntegrityError: When the posting re-sync or the commit trips a
            constraint.  No longer caught here -- ruling R-EQ made the only
            reachable one (the deleted unique index) impossible -- so it
            propagates as the 500 an unexpected DB-level failure deserves.
            Flask's teardown removes the session, which rolls the transaction
            back and releases the advisory lock.
    """
    acct_type = account.account_type
    if acct_type is not None and acct_type.has_amortization:
        raise AmortizingAccountAnchorError(
            f"account {account.id} is an amortizing loan; assert its "
            "balance through apply_loan_anchor_true_up, never as a "
            "cash anchor"
        )

    # Bounded ONCE, here, above the lock (plan step X-f1e2).  The kind gate runs
    # first so an amortizing account is refused for what it IS before its day is
    # judged.
    day = resolve_observation_day(account.user_id, observed_on)

    if not stage_anchor_true_up(
        account=account, new_balance=new_balance, observed_on=day,
    ):
        # Ruling R-EQ: the submission IS the governing assertion, so there is
        # nothing to append and nothing for the reconcile to move.  Roll back
        # rather than returning on an open transaction -- the stager took the
        # owner's write lock to make its read safe, and only a commit or a
        # rollback releases it.
        # Read the id BEFORE the rollback: afterwards the instance is expired
        # and touching an attribute opens a fresh transaction purely to recover
        # a value already in hand.
        account_id = account.id
        db.session.rollback()
        logger.info(
            "Anchor true-up for account %d asserts the balance that already "
            "stands; nothing written (idempotent success)",
            account_id,
        )
        return AnchorTrueUpOutcome.UNCHANGED

    # Build-Order Step 5: the new assertion re-bases the account's
    # anchor corrections in EVERY scenario (anchor history is
    # per-account) -- the fresh history row autoflushes into the walk's
    # first query, so the reconcile books the true-up delta in the same
    # transaction.  An amortizing loan is a structural no-op (loans true-up
    # through :func:`apply_loan_anchor_true_up`).
    account_posting_service.sync_account_anchor_postings_all_scenarios(
        account.id,
    )
    db.session.commit()
    return AnchorTrueUpOutcome.COMMITTED


def _governing_loan_anchor(
    account_id: int, source_id: int, anchor_date: date,
) -> LoanAnchorEvent | None:
    """Return the event of ``source_id`` governing ``anchor_date``.

    The loan twin of :func:`app.services.cash_ledger.governing_anchor_on`, and
    the WRITER's question rather than a reader's (ruling **R-EQ**, plan step
    X-f1c4b): of the rows this door could have written for this date, which one
    stands.

    **The ``anchor_date <=`` bound is the whole point and was missing from the
    first version of this step.**  Ordering by date alone answers "the latest
    event of this source", so a submission for an EARLIER date could never
    compare equal and every double-click on a back-dated correction appended a
    duplicate -- reproduced twice, independently, against this door, which is
    the one that has had a user-supplied date field since Commit 16.  A
    submission for date D can only change what is true at or after D.

    **It is not a second copy of the resolver's latest-anchor rule.**  The
    resolver answers over :func:`app.services.loan_loaders.load_loan_anchor_facts`
    -- every source PLUS the synthesized origination, which has no stored row and
    can never be the thing a submission duplicates.  Sharing a query between the
    two would mean filtering the reader's synthesized fact back out, which is
    more coupling than the four lines it would save.  **Both now break a tie the
    SAME way, and this door is where that rule was already right**: its
    ``(anchor_date, created_at, id)`` DESC was the only TOTAL anchor ordering
    until plan step X-an-b gave the read path the ``id`` term too (finding
    **N-196**).  ``id`` is load-bearing -- ``created_at`` is evaluated at
    TRANSACTION START, so two rows written together share an instant.

    Args:
        account_id: The loan account whose anchors to search.
        source_id: The ``ref.loan_anchor_sources`` id to scope to (see
            :func:`_append_loan_anchor_and_sync` for why the scope is per
            source).
        anchor_date: The date the submission asserts for -- the comparison's
            horizon.

    Returns:
        The governing :class:`LoanAnchorEvent`, or ``None`` when the account has
        no stored anchor of that source at or before *anchor_date* -- in which
        case the submission is necessarily new.
    """
    return (
        db.session.query(LoanAnchorEvent)
        .filter(
            LoanAnchorEvent.account_id == account_id,
            LoanAnchorEvent.source_id == source_id,
            LoanAnchorEvent.anchor_date <= anchor_date,
        )
        .order_by(
            LoanAnchorEvent.anchor_date.desc(),
            LoanAnchorEvent.created_at.desc(),
            LoanAnchorEvent.id.desc(),
        )
        .first()
    )


def _append_loan_anchor_and_sync(
    *,
    account: Account,
    anchor_balance: Decimal,
    anchor_date: date,
    source: LoanAnchorSourceEnum,
) -> AnchorTrueUpOutcome:
    """Append one :class:`LoanAnchorEvent` of ``source`` and re-sync the ledger.

    The shared transactional core of :func:`apply_loan_anchor_true_up` (a
    ``user_trueup`` balance assertion) and :func:`record_loan_tracking_start`
    (the ``tracking_start`` opening of a mid-life-imported loan): the two differ
    ONLY in the anchor source, so they must not drift on the append + re-sync +
    idempotency handling.

    Appends ONE row to the append-only :class:`LoanAnchorEvent` table, then
    re-syncs the loan's genesis postings in EVERY scenario (the anchor is
    per-account, not per-scenario) via
    :func:`app.services.loan_posting_service.sync_loan_postings_all_scenarios` --
    which re-runs the running-balance walk so payments re-split from the new
    anchor.  The just-added event becomes visible to that walk because the sync's
    first query autoflushes it (load-bearing -- must NOT run under
    ``session.no_autoflush``).

    **Whether there is anything to append is decided here, by ruling R-EQ**, and
    the decision is the checking door's rule on this table: take the owner's
    write lock, read the event that currently GOVERNS, append only when the
    submission differs.  It replaced
    ``loan_posting_service.sync_all_scenarios_or_duplicate`` on this path (that
    helper survives for the ARM rate change, whose table is EDITABLE and whose
    unique key is therefore a real business rule rather than an idempotency
    guess) and with it the partial expression index
    ``uq_loan_anchor_events_acct_date_bal_day``.

    **"Governing" is scoped per SOURCE here and not on the checking side, and
    that difference is the tables', not a drift.**  A ``tracking_start`` and a
    ``user_trueup`` are DISTINCT FACTS even at the same date and balance:
    :func:`app.services.loan_loaders.load_loan_anchor_facts` loads both as
    assertions differing only in ``is_tracking_start``, and
    ``loan_posting_service.loan_balance_anchor_history`` renders that label on
    the loan dashboard's drift card.  A cross-source comparison would answer
    UNCHANGED to a ``tracking_start`` because a same-valued ``user_trueup``
    stands, silently dropping the label the user asked to record.
    ``AccountAnchorHistory`` carries one kind of fact and needs no such split.

    *An earlier version of this paragraph justified the split by claiming a
    ``tracking_start`` is privileged as the loan's OPENING, citing
    ``loan_loaders._opening_anchor_fact``.  That function was DELETED in the
    loan arc and the live loader states the opposite -- "Origination is the
    opening ALWAYS", and a ``tracking_start`` "RESETS the running balance at its
    own date like any true-up".  A neutral review caught it; the split is right
    and the reason was not.*

    Args:
        account: An attached :class:`Account` row for the loan.  Caller owns the
            ownership check.
        anchor_balance: The validated :class:`Decimal` balance to assert
            (``>= 0`` enforced at the schema layer, backstopped by
            ``ck_loan_anchor_events_balance_nonneg``).
        anchor_date: The date the balance is asserted for.  Caller enforces the
            source-appropriate bounds (see the two public wrappers).
        source: The :class:`~app.enums.LoanAnchorSourceEnum` provenance --
            ``USER_TRUEUP`` or ``TRACKING_START``.

    Returns:
        ``COMMITTED`` when the event was written and committed; ``UNCHANGED``
        when the submission matched the governing event of its own source, in
        which case nothing was written and the session was rolled back.
    """
    # Ruling R-EQ: the lock precedes the read the decision is made from, and on
    # this path it is also the transaction's first lock (finding N-193's
    # ordering invariant).  The all-scenario sync below takes the same
    # re-entrant lock again, harmlessly.
    lock_user_writes(account.user_id)
    source_id = ref_cache.loan_anchor_source_id(source)
    governing = _governing_loan_anchor(account.id, source_id, anchor_date)
    if governing is not None and (
        (governing.anchor_date, Decimal(str(governing.anchor_balance)))
        == (anchor_date, anchor_balance)
    ):
        # See the cash door: the id is read before the rollback expires it.
        account_id = account.id
        db.session.rollback()
        logger.info(
            "Loan anchor (%s) for account %d on %s asserts the balance that "
            "already stands; nothing written (idempotent success)",
            source.value, account_id, anchor_date,
        )
        return AnchorTrueUpOutcome.UNCHANGED

    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=anchor_date,
        anchor_balance=anchor_balance,
        source_id=source_id,
    ))
    loan_posting_service.sync_loan_postings_all_scenarios(account.id)
    db.session.commit()
    return AnchorTrueUpOutcome.COMMITTED


def apply_loan_anchor_true_up(
    *,
    account: Account,
    anchor_balance: Decimal,
    anchor_date: date,
) -> AnchorTrueUpOutcome:
    """Append a user-trueup :class:`LoanAnchorEvent` and commit.

    The loan analogue of :func:`apply_anchor_true_up` (E-18 / Commit
    16, decision D-C).  The loan resolver derives the displayed
    current balance, monthly payment, schedule and payoff date from
    the latest anchor event plus the confirmed payment stream, so a
    new trueup event immediately changes every loan surface
    consistently without mutating any column on
    :class:`LoanParams`.

    The function appends ONE row to :class:`LoanAnchorEvent`.  The
    table is structurally append-only (the model's
    ``before_update`` / ``before_delete`` event listeners refuse any
    ORM-mediated UPDATE or DELETE), so a correction of an earlier
    trueup is expressed as another append, never an edit.  The
    function does NOT mutate :class:`LoanParams.current_principal` --
    that column is non-authoritative seed (E-18) and is never written
    by the trueup flow.

    **There is no stale-form conflict on either path, and since plan step
    X-f1c3c that is stated the same way for both.**  A
    :class:`LoanAnchorEvent` is an INSERT-only row with no ``version_id``
    column, and the resolver is read-only.  Two concurrent trueup commits
    with different ``(anchor_date, anchor_balance)`` produce two rows, both
    legitimate; the resolver selects the latest by ``(anchor_date, created_at,
    event_id)`` DESC (the third term is X-an-b's; without it the last writer
    won only when the two differed in day).  The cash path used to differ -- it
    carried a ``STALE_CONFLICT`` outcome and a 409 -- and ruling R-EN deleted
    that, on the ground that this contract had documented since Commit 16.

    **What that ruling did NOT inherit from here is a concurrency guarantee,
    because there was none to inherit.**  Both paths then re-sync the posted
    ledger, and a re-sync is a read-modify-write with no unique index behind
    it; nothing serialised this one between Commit 16 and X-f1c3c.  It is
    serialised now, by the per-owner lock the reconcile takes for itself
    (:mod:`app.services.user_write_lock`).

    The ``UNCHANGED`` outcome mirrors the checking-anchor semantics: when a
    request submits the ``(anchor_date, anchor_balance)`` the governing
    ``user_trueup`` already asserts, nothing is written and the caller renders
    idempotent success.  This handles network retries and double-clicks on the
    Save button.  **It was a UTC-calendar-day unique index until plan step
    X-f1c4b** (ruling R-EQ), which refused a re-assertion of a balance that had
    since been superseded on the same recording day and reported it as saved.

    Args:
        account: An attached :class:`Account` row for the loan.
            Caller is responsible for the ownership check (route uses
            404 for cross-owner access) and for confirming the
            account type carries ``has_amortization=True`` (the
            route's ``_load_loan_account`` enforces this).
        anchor_balance: The validated :class:`Decimal` anchor balance
            to write.  Caller is responsible for constructing this
            from schema-validated form data via ``Decimal(str(...))``
            and for enforcing ``anchor_balance >= 0`` at the schema
            layer (the storage tier's
            ``ck_loan_anchor_events_balance_nonneg`` is the backstop).
        anchor_date: The date the user is asserting the balance for.
            Caller is responsible for enforcing
            ``anchor_date <= today`` and
            ``anchor_date >= params.origination_date`` at the
            schema/route layer; this function trusts the caller and
            persists whatever date it is given.

    Returns:
        AnchorTrueUpOutcome -- ``COMMITTED`` when a new event row was
        written and the commit succeeded; ``UNCHANGED`` when the submission
        asserts what the governing ``user_trueup`` already asserts.  Those are
        the only two members the enum has carried since ruling R-EN deleted
        ``STALE_CONFLICT`` (plan step X-f1c3c), so the two anchor paths return
        the same pair.
    """
    return _append_loan_anchor_and_sync(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
        source=LoanAnchorSourceEnum.USER_TRUEUP,
    )


def record_loan_tracking_start(
    *,
    account: Account,
    anchor_balance: Decimal,
    anchor_date: date,
) -> AnchorTrueUpOutcome:
    """Append a ``tracking_start`` opening :class:`LoanAnchorEvent` and commit.

    The mid-life-import opening flow: the operator started tracking an
    already-amortizing loan and asserts its real balance as of a date at/before
    the first recorded payment.  It is an ordinary ``is_opening=False`` balance
    ASSERTION that RESETS the genesis walk's running balance at its own date
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`); the origination
    fields on :class:`LoanParams` are untouched.  *It is NOT the loan's OPENING,
    and this said it was until plan step X-an-b*, citing
    ``loan_loaders._opening_anchor_fact`` -- deleted by step C1 along with the
    behaviour.  Origination is the opening ALWAYS: opening at a mid-life
    tracking-start read the loan out of existence for its whole pre-tracking
    window (finding B-11).

    Shares the append + all-scenario re-sync + duplicate rule of
    :func:`apply_loan_anchor_true_up` via :func:`_append_loan_anchor_and_sync`;
    the only difference is the anchor source, which is also the scope the
    duplicate rule compares within.  Like a true-up it never mutates
    :class:`LoanParams`.

    Args:
        account: An attached :class:`Account` row for the loan.  Caller is
            responsible for the ownership check and for confirming the account
            carries ``has_amortization=True``.
        anchor_balance: The validated :class:`Decimal` opening balance
            (``>= 0`` at the schema layer).
        anchor_date: The date the balance is asserted for.  Caller is
            responsible for enforcing ``origination_date <= anchor_date``,
            ``anchor_date <= today``, and that it is at/before the earliest
            recorded payment so no payment is left pre-opening.

    Returns:
        ``COMMITTED`` on a new committed event; ``UNCHANGED`` when the
        submission asserts what the governing ``tracking_start`` already asserts
        (idempotent success).  The comparison is scoped to this source, so a
        re-submitted opening is recognised even when true-ups have been recorded
        after it -- see :func:`_append_loan_anchor_and_sync`.
    """
    return _append_loan_anchor_and_sync(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
        source=LoanAnchorSourceEnum.TRACKING_START,
    )
