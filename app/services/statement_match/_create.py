"""The door that turns a bank line the app has no row for INTO a purchase.

Ruling **R-FS**'s third shape, plan step ``bank_import:X-f6a-3b``.  **It MOVES
MONEY**: a line recorded here becomes a cash movement the app did not have,
dated the day the bank posted it.

**The card-swipe lines an envelope aggregates have no counterpart to match**,
because the app records a period's groceries as one envelope and the bank
records every swipe.  Measured on the developer's own 2026-08-16 statement
against a 2026-08-18 production clone: after every proposal the matcher offers,
**91 unmatched outflows** remain -- 74 of them card swipes worth `$3,383.49`,
the case R-FS names -- and they are not the same money at a different grain: in
period 9 the app holds 8 debit purchases, the matcher pairs ALL 8, and the bank
then shows 12 more the app has nothing for.  Over the assertion span the app's
own records state `+$3,000.46` of movement where the bank states `+$558.28`.

**Every bank line becomes a PURCHASE, and this door never creates a bare
transaction.**  A purchase is the app's record of one payment; a transaction is
a budget line that reserves.  Recording a payment as a budget line would
collapse the two facts a purchase keeps apart, and it would give a later
statement line nowhere to go.  So the only question the owner answers is which
budget line CONTAINS it, and there are exactly two answers:

* an existing envelope, from the set :func:`~._reads.destinations_for` offers;
* a NEW envelope this door creates in the line's own pay period, named from
  what the bank called it.

**The second arm always creates an ENVELOPE rather than a plain row**, so an
unbudgeted expense gets a container that can take later purchases too, and so
the app's shape does not depend on whether a category happened to be budgeted.

**Nothing here decides which** (ruling **R-FP**): the owner picks, and the
picking is what makes this a review rather than an import that rewrites a
budget.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, a
frozen dataclass out, no Flask import.  It MUTATES and does NOT commit -- the
route owns the unit of work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.statement_import import BankStatementLine
from app.models.transaction import Transaction
from app.services import (
    entry_service,
    posting_service,
    transaction_service,
)
from app.services.scenario_resolver import require_baseline_scenario
from app.utils.log_events import (
    BUSINESS,
    EVT_STATEMENT_LINE_RECORDED,
    log_event,
)

from ._accept import load_lines, record_match
from ._candidates import (
    MatchedSubjects,
    matched_subjects,
    purchase_candidate,
    unmatched_destinations,
)
from ._offers import PurchaseCreation, merchant_of
from ._scope import ReviewScope

_logger = logging.getLogger(__name__)

#: What a created envelope BUDGETS.  Zero, because nothing budgeted it: the
#: line is spending the plan did not anticipate, and inventing a budget equal to
#: the spend would make every unplanned purchase look planned.  A projected
#: envelope holds back ``max(estimated - posted - credit, unposted)``, so with a
#: purchase that already carries its posting day the reservation is ``0.00`` and
#: the purchase books its own cash -- the row states what happened and reserves
#: nothing.
_NO_BUDGET = Decimal("0.00")


@dataclass(frozen=True)
class CreatedPurchase:  # pylint: disable=too-many-instance-attributes
    """What recording one bank line as a purchase did.

    Pylint: too-many-instance-attributes -- **eight because the act genuinely
    produces eight facts**, with three separate consumers reading disjoint
    subsets: the structured log takes the three ids and both days, the flash
    takes the container's label and whether it was created plus the figure and
    the posting day, and the tests take the ids.  ``CandidateRow`` beside it
    carries the same disable for the same reason.  Splitting the container's
    three fields into a nested value would be the speculative shape rule 13
    forbids -- nothing asks for the container alone.

    Attributes:
        entry_id: The ``budget.transaction_entries`` row now holding the
            movement.
        transaction_id: The budget line that contains it.
        match_id: The ``budget.statement_matches`` act recording that this line
            IS that purchase, so the line stops being unexplained and a
            re-import does not re-offer it.
        envelope_label: What to call the container on screen.
        envelope_created: Whether that container was created by this act.  The
            receipt names it, because creating a budget line is a bigger thing
            to have done than filing a purchase under one that existed.
        amount: The purchase's own figure, POSITIVE -- what the bank took.
        posts_on: The day the bank took it.
        made_on: The day the bank says it was made, which is the purchase's own
            budget clock and is the posting day where the source states none.
    """

    entry_id: int
    transaction_id: int
    match_id: int
    envelope_label: str
    envelope_created: bool
    amount: Decimal
    posts_on: date
    made_on: date


def _load_line(
    creation: PurchaseCreation,
    matched: MatchedSubjects,
    scope: ReviewScope,
) -> BankStatementLine:
    """Return the submitted line, refusing one this door may not record.

    **The account scope and the already-matched refusal are
    :func:`~._accept.load_lines`', not restated here** (plan step
    ``bank_import:X-f6a-3c-2``).  This door records a match like every other,
    so *is this line on this account, and has something already claimed it* has
    to be ONE answer; it carried a second copy of both while that function was
    private, which is two places for a refusal to stop firing.  The
    already-matched half matters here for the same reason it does there:
    ``uq_statement_match_members_line`` refuses the second act anyway, but it
    arrives as an ``IntegrityError`` after a purchase has been created, which
    reaches the user as "Something went wrong" and logs a traceback for an
    ordinary stale page.

    The third refusal IS this door's own, because it is about what a purchase
    can be rather than about matching: the line must be money LEAVING.  A
    purchase is an expense (``ck_transaction_entries_positive_amount``), so a
    deposit or a refund cannot become one -- and 16 of the developer's own
    unexplained lines are inflows, including three card refunds, so this is the
    ordinary shape rather than a crafted request.

    Args:
        creation: What the owner submitted.
        matched: What this account's matches have already claimed, as of this
            act.
        scope: The pass, which is the ONE statement of which account's lines
            may be reached.

    Returns:
        The line.

    Raises:
        ValidationError: On any of the three.
    """
    line = load_lines(
        scope.account_id, frozenset({creation.line_id}), matched,
    )[0]
    if line.amount >= 0:
        raise ValidationError(
            "Only money LEAVING the account can be recorded as a purchase, "
            "and that line is money coming in.  Match it to the row it "
            "belongs to instead.  Nothing was changed."
        )
    return line


def _made_on(line: BankStatementLine) -> date:
    """Return the day the bank says the purchase was MADE.

    The stated transaction day where the source states one, else the day it
    posted -- the same fallback :attr:`~._offers.BankLine.happened_on` makes,
    and for the same reason: money cannot clear before it moves, so the posting
    day is the tightest bound a source stating nothing supports.  SECU states
    one on 182 of 361 lines.

    Args:
        line: The recorded line.

    Returns:
        Its budget-clock day.
    """
    return line.transaction_on or line.posted_on


def _period_holding(calendar, day: date) -> int:
    """Return the id of the pay period covering *day*, refusing if none does.

    **The day it was MADE decides, not the day it posted**, and the difference
    is a real one at a period boundary: a swipe made on the last day of one pay
    period and posted on the first day of the next belongs to the budget of the
    period it was made in.  ``purchased_on`` is the budget clock and its
    container's period is what the app budgets against, so placing the row by
    the posting day would file already-spent money against the wrong paycheck --
    and would raise ``check_purchase_date_in_period``'s out-of-period warning
    on a row this door had just built.

    Args:
        calendar: The PASS's own
            :class:`~app.services.pay_calendar.PayCalendar`, taken rather than
            loaded.  It asked ``calendar_for`` for itself until plan step
            ``bank_import:X-f6a-3c-2``, which is a second read of a fact the
            screen that offered this line had already resolved -- and under
            READ COMMITTED the two can differ, so a line could be OFFERED
            against one calendar and PLACED against another.
        day: The day the purchase was made.

    Returns:
        The covering period's id.

    Raises:
        ValidationError: When no SAVED period covers it -- the line predates
            the owner's first payday, or lies past the generated horizon.  130
            of the developer's own 361 lines are the first case, which
            :class:`~._reads.ReviewBounds` already reports rather than offering.
    """
    period = calendar.period_containing(day)
    if period is None:
        raise ValidationError(
            f"No pay period covers {day.isoformat()}, so there is no budget "
            f"for this purchase to belong to.  Extend your pay schedule to "
            f"cover that day first.  Nothing was changed."
        )
    return period.period_id


def _reject_ambiguous_destination(creation: PurchaseCreation) -> None:
    """Refuse a submission naming both destinations or neither.

    The two arms are exclusive by construction: a purchase has exactly one
    parent, so "put it in this envelope" and "make an envelope for it" cannot
    both be the answer.  Stated as a refusal rather than a precedence rule --
    a door that silently preferred one arm would record something the owner did
    not ask for.

    Args:
        creation: What the owner submitted.

    Raises:
        ValidationError: When both arms or neither are named.
    """
    named = sum((
        creation.transaction_id is not None,
        creation.new_envelope is not None,
    ))
    if named != 1:
        raise ValidationError(
            "Choose exactly one place for this purchase: an envelope you "
            "already have, or a new one.  Nothing was changed."
        )


def _reject_incomplete_new_envelope(creation: PurchaseCreation) -> None:
    """Refuse a NEW envelope stated by halves.

    A budget line needs a name AND a category: ``transactions.category_id`` is
    what every spending report groups by, and a row created without one would
    be invisible to the very analysis the purchase exists to feed.  The name is
    ``transactions.name``, which is NOT NULL.

    **It is the DOOR's refusal since plan step X-f6a-3c-2, not the schema's.**
    It was a ``@validates_schema`` rule on ``StatementPurchaseSchema``, which
    was right while one POST was one act: a nested schema error refuses the
    WHOLE payload, and once a POST carries a whole reviewed pass that means an
    owner who picked "a new envelope" on one line and left its category on the
    form's own default lost every other act they had ticked -- 124 proposals
    and 90 creations on the developer's own statement.  The ruled failure
    policy is that a refused item costs only itself, and a rule that can only
    refuse the whole submission cannot honour it.  Found by adversarial
    financial review 2026-08-19.

    It fires BEFORE :func:`_owned_category`, which would otherwise answer a
    missing category with "that category is not one of yours" -- a true
    sentence about the wrong problem.

    Args:
        creation: What the owner submitted.

    Raises:
        ValidationError: When the new-envelope arm is named without both of
            its own fields.
    """
    new = creation.new_envelope
    if new is None:
        return
    if new.name is None or new.category_id is None:
        raise ValidationError(
            "A new envelope needs both a name and a category.  Nothing was "
            "changed."
        )


def _existing_envelope(
    creation: PurchaseCreation,
    pay_period_id: int,
    scope: ReviewScope,
    matched: MatchedSubjects,
) -> Transaction:
    """Return the chosen envelope, refusing one the screen could not offer.

    **Resolved against the pass's own destination set rather than queried
    directly**, so the set this door may write into is exactly the set the
    screen may offer -- the same one-scope-for-reader-and-writer property
    :func:`~._accept.resolve_rows` rests on.  An envelope belonging to another
    user, another account, a cancelled or archived row, a settled row whose
    figure is a stored number, or one already matched to a bank line is not a
    destination and cannot be reached by crafting a request.

    **The already-matched half is asked of the claims THIS ACT read, not of the
    scope** (plan step ``bank_import:X-f6a-3c-2``), and on the developer's own
    data that is 15 lines rather than a hypothetical: 4 envelopes are both
    named by a proposal and offered as a destination, so a pass that accepts
    the proposals first leaves 15 creatable lines aimed at an envelope a match
    now claims.  Asking here is what gives those 15 the sentence about the
    envelope being gone rather than one about counting money twice from a tier
    deeper -- and, in the other order, what keeps a purchase out of an envelope
    whose own figure a match has already fixed.

    **The PERIOD is part of that set and a first version left it out**, so the
    screen offered the line's own period and the door accepted any of them --
    which let a crafted request file a swipe into a Groceries envelope
    eighteen months forward, or raise a closed past envelope's recorded cost in
    a period the line has nothing to do with.  It also made the two arms
    disagree with each other: :func:`_create_envelope` has always placed a new
    row by the day the purchase was MADE.  Found by adversarial security review
    2026-08-19.

    Args:
        creation: What the owner submitted.
        pay_period_id: The period holding the day the purchase was made, which
            is the only one whose envelopes the screen offers for this line.
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).
        matched: What this account's matches have already claimed, as of this
            act.

    Returns:
        The envelope.

    Raises:
        ValidationError: When the id names nothing the screen could have
            offered for this line.
    """
    offered = {
        destination.transaction_id
        for destination in unmatched_destinations(
            scope.destinations, matched,
        )
        if destination.pay_period_id == pay_period_id
    }
    if creation.transaction_id not in offered:
        raise ValidationError(
            "That envelope is not one this purchase can go into -- it may "
            "have been deleted or cancelled, it may already be matched to a "
            "statement line, or it may have closed at a fixed figure that a "
            "new purchase cannot change.  Reload the page and pick another.  "
            "Nothing was changed."
        )
    return db.session.get(Transaction, creation.transaction_id)


def _owned_category(
    creation: PurchaseCreation, scope: ReviewScope,
) -> Category:
    """Return the category the new envelope will carry, refusing another's.

    The IDOR probe every create route in this project performs before a write:
    a foreign ``category_id`` satisfies the foreign key -- the row exists -- and
    would link another user's category onto this owner's budget line.

    Args:
        creation: What the owner submitted.
        scope: The pass, which is the ONE statement of whose categories may be
            reached.

    Returns:
        The category.

    Raises:
        ValidationError: When the id names no category of this owner's.
    """
    category = (
        db.session.query(Category)
        .filter(
            Category.id == creation.new_envelope.category_id,
            Category.user_id == scope.owner_id,
            # ARCHIVED categories are not selectable targets for a new row --
            # ``category_service.list_active_categories`` is what the picker
            # renders and it filters on this, so accepting one here would be
            # the offer-versus-accept drift this door exists to close.
            Category.is_active.is_(True),
        )
        .one_or_none()
    )
    if category is None:
        raise ValidationError(
            "That category is not one of yours.  Reload the page and pick "
            "another.  Nothing was changed."
        )
    return category


def _create_envelope(
    creation: PurchaseCreation,
    category: Category,
    pay_period_id: int,
    scope: ReviewScope,
) -> Transaction:
    """Stage a new, empty envelope for this line's period.

    **Born Projected, budgeting nothing**, which is the two facts a budget line
    created from a statement can honestly state.  Projected because
    ``status_seam.apply_status_change`` is the ONE door to a settled status and
    a row may not be born in one (plan step ``balance:X-aj2`` is where that
    becomes structural); nothing because the spending was unplanned, and a
    budget equal to the spend would make it look planned.

    **It is an ENVELOPE (``is_envelope=True``), and that is the whole point of
    the arm.**  A plain row would be a budget line that is also its own single
    payment, so the next statement line for the same merchant would have
    nowhere to go; an envelope can hold that one too, and the row's cost stays
    the sum of what the bank actually showed.

    It OWNS its amount (``amount_source_id`` NULL beside a stored figure), which
    is what ``ck_transactions_amount_ownership`` pairs: a row with no template
    and no transfer has no derivation to read.

    Args:
        creation: What the owner submitted, for the envelope's name.
        category: The category the owner picked, already proved theirs.
        pay_period_id: The period holding the day the purchase was made.
        scope: The pass, which is the ONE statement of whose account this row
            belongs to.

    Returns:
        The staged, flushed :class:`~app.models.transaction.Transaction`.
    """
    envelope = Transaction(
        account_id=scope.account_id,
        pay_period_id=pay_period_id,
        # **The BASELINE scenario, unconditionally.**  A what-if scenario is a
        # hypothesis about money that has not moved, and a bank statement is
        # the opposite of one: this row records something that already
        # happened.  Reading the scenario off whatever row the period happened
        # to hold would file a real movement under a hypothesis the first time
        # the what-if work lands, which is exactly the class of silent
        # misplacement ``_candidates`` declines to guess at.
        scenario_id=require_baseline_scenario(scope.owner_id).id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=creation.new_envelope.name,
        category_id=category.id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
        estimated_amount=_NO_BUDGET,
        is_envelope=True,
    )
    db.session.add(envelope)
    db.session.flush()
    return envelope


def create_purchase_from_line(
    creation: PurchaseCreation, scope: ReviewScope,
) -> CreatedPurchase:
    """Record one bank line as a purchase, and match the line to it.

    The whole act, in the order its refusals have to happen: the line is
    checked, the destination is resolved against the set the screen may offer,
    and only then is anything staged.  The match itself goes through
    :func:`~._accept.record_match`, so the correspondence is written by the same
    function that writes every other one -- ruling **R-FT**'s table, ruling
    **R-FV**'s identity-only rule, and every guard those already carry.

    **It records the row it just built rather than asking a door to find it**
    (plan step ``bank_import:X-f6a-3c-2``).  It called ``accept_match`` with the
    new purchase's id until this step, which re-derived all 827 of the account's
    candidate rows to prove that id was in scope -- 3.593 s per line on the
    developer's own clone, over 91 of them, and finding **N-309**'s third payer.
    Worse, it made the act unshareable: a purchase created inside a pass cannot
    be in an offer set derived before the pass, so running a whole statement
    against one derivation refused **all 91** of these lines as "no longer
    available to match".  A door that created a row does not need to prove that
    row is offerable, so it states the candidate itself, through the same
    :func:`~._candidates.purchase_candidate` the offer set is built from.

    **The purchase is BORN carrying both of its days** (ruling **R-FW**): the
    day the bank says it was made, and the day the bank took the money.
    Recording them in one ``create_entry`` call rather than creating and then
    updating is what keeps a purchase the bank has already taken from existing,
    even briefly, as an outstanding one.  It is why the match this door records
    moves no day: the row already carries the ones the bank stated.

    Does NOT commit -- the caller owns the session boundary.

    Args:
        creation: What the owner submitted: one line, and one destination.
        scope: The pass's derived offer set (:class:`~._scope.ReviewScope`).
            **Required rather than defaulted**, for the reason
            :func:`~._accept.accept_match`'s is.

    Returns:
        The :class:`CreatedPurchase`.

    Raises:
        ValidationError: On any of this door's refusals or a settle door's.
            A 400: every one is reachable by an ordinary owner working from a
            stale page.
    """
    _reject_ambiguous_destination(creation)
    _reject_incomplete_new_envelope(creation)
    # ONE read of what this account's matches have claimed, for this act:
    # the line refusal, the destination refusal and the double-count guard
    # inside ``record_match`` all narrow with it, so they cannot disagree.
    matched = matched_subjects(scope.account_id)
    line = _load_line(creation, matched, scope)
    made_on = _made_on(line)

    # ONE period for both arms, resolved once: it is where the purchase is
    # BUDGETED, so it decides which envelopes may hold it and which period a
    # new one is created in.  Resolving it per arm is how the two came to
    # disagree.
    pay_period_id = _period_holding(scope.calendar, made_on)
    if creation.transaction_id is not None:
        envelope = _existing_envelope(creation, pay_period_id, scope, matched)
        created = False
    else:
        category = _owned_category(creation, scope)
        envelope = _create_envelope(
            creation, category, pay_period_id, scope,
        )
        created = True

    amount = -Decimal(str(line.amount))
    entry = entry_service.create_entry(
        transaction_id=envelope.id,
        user_id=scope.owner_id,
        details=entry_service.EntryDetails(
            amount=amount,
            # What the BANK called the merchant, not the whole line
            # (:func:`~._offers.merchant_of`).  The app's own purchases are
            # named "Walmart" and "Food Lion", and a purchase called
            # ``POINT OF SALE DEBIT L340 DATE 08-13 Amazon.com*5H2RA5V...``
            # would be the only row in the entries list nobody can read.  The
            # bank's full wording is not lost: it stays on the statement line,
            # which the match this door records ties to this purchase.
            description=merchant_of(line.description)[:200],
            purchased_on=made_on,
            settled_on=line.posted_on,
        ),
    )
    if created:
        # **A row created to hold something that has already happened says
        # so.**  A first version justified this by calling a Projected `$0.00`
        # row "carry-forward bait that would roll a NEGATIVE leftover", which is
        # measurably FALSE -- ``carry_forward_service`` clamps its leftover with
        # ``max(Decimal("0"), budget - entries)`` in both its preview and its
        # execute arm.  Found by adversarial design review 2026-08-19.
        #
        # The real reason is narrower and holds: this row records money that has
        # ALREADY left the account, and the app's vocabulary for that is the
        # settled band -- left Projected it would read on the grid as an unpaid
        # item for money already gone.  Carry-forward would settle it through
        # this very verb anyway; doing it here dates the close on the day the
        # bank posted, where carry-forward would date it whenever it next ran.
        transaction_service.settle_from_entries(
            envelope, settled_on=line.posted_on,
        )
        # **``settle_from_entries`` does NOT reconcile the ledger** -- it is the
        # envelope PRIMITIVE, and its docstring says carry-forward owes that
        # reconcile a different moment.  Both of its other callers pair it with
        # this line (`transaction_service._settle.settle_transaction`,
        # `carry_forward_service._execute`), and so does this one.  It is a
        # no-op on today's arithmetic -- `create_entry` already reconciled the
        # family while the row was Projected, and a close whose every purchase
        # is posted targets `0.00` -- but a contract kept by a cancellation
        # nobody asserts is finding **N-318**'s shape, one module over.  Found
        # by adversarial security review 2026-08-19.
        posting_service.sync_transaction_postings(
            envelope, settled=envelope.status.is_settled,
        )

    accepted = record_match(
        scope.owner_id,
        scope.account_id,
        [line],
        [purchase_candidate(entry)],
        matched,
    )

    recorded = CreatedPurchase(
        entry_id=entry.id,
        transaction_id=envelope.id,
        match_id=accepted.match_id,
        envelope_label=envelope.name,
        envelope_created=created,
        amount=amount,
        posts_on=line.posted_on,
        made_on=made_on,
    )
    log_event(
        _logger, logging.INFO, EVT_STATEMENT_LINE_RECORDED, BUSINESS,
        "A bank statement line was recorded as a purchase.",
        user_id=scope.owner_id,
        account_id=scope.account_id,
        line_id=line.id,
        entry_id=recorded.entry_id,
        transaction_id=recorded.transaction_id,
        match_id=recorded.match_id,
        envelope_created=created,
        amount=str(recorded.amount),
        posts_on=recorded.posts_on.isoformat(),
        made_on=recorded.made_on.isoformat(),
    )
    return recorded
