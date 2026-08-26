"""WHICH budget line holds a purchase a bank line became, and when it CLOSES.

Split out of :mod:`._create` at plan step ``bank_import:X-ge``, when that
module passed this project's 1,000-line bound.  **The seam is a SUBJECT and not
a line count** -- the same cut :mod:`._rules` / :mod:`._stating` was made on,
and :mod:`._creations` before it.  :mod:`._create` answers *what is this bank
line, as a purchase*: which line may be recorded, which days it carries, what
the act brought into existence.  Everything here answers the other question the
door has to settle before it can write anything: **which budget line CONTAINS
it**, and what closing that container means.

Two subjects, two reasons to change.  The container's rules move when the
budget's shape does -- ruling **R-FX**'s settled-row clause, finding
**N-327**'s one-envelope-per-answer-per-period registry, ruling **R-ED**'s
"the seam owns the day" -- and the purchase's move when the bank's record does.
Nothing changed on the way across.

**The offer set is the SCREEN's, and that is the property everything here
rests on.**  A destination this module will accept is one
:func:`~._candidates.destinations_for` returns, narrowed by what the acting
pass has already claimed -- so a row the screen may not offer cannot be reached
by crafting a request, and a row it does offer cannot be refused a tier deeper.

Services-boundary discipline (``CLAUDE.md`` Architecture): plain data in, no
Flask import, no clock read.  It MUTATES and does NOT commit -- the route owns
the unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum, TxnTypeEnum
from app.exceptions import ValidationError
from app.extensions import db
from app.models.category import Category
from app.models.transaction import Transaction
from app.services import (
    posting_service,
    transaction_service,
)
from app.services.scenario_resolver import require_baseline_scenario
from app.services.settle_day import SettleDay

from ._candidates import MatchedSubjects, unmatched_destinations
from ._creations import (
    CreatedPurchase,
    NewEnvelope,
    PurchaseCreation,
    envelope_answer_key,
)
from ._scope import ReviewScope


#: What a created envelope BUDGETS.  Zero, because nothing budgeted it: the
#: line is spending the plan did not anticipate, and inventing a budget equal to
#: the spend would make every unplanned purchase look planned.  A projected
#: envelope holds back ``max(estimated - posted - credit, unposted)``, so with a
#: purchase that already carries its posting day the reservation is ``0.00`` and
#: the purchase books its own cash -- the row states what happened and reserves
#: nothing.
_NO_BUDGET = Decimal("0.00")


@dataclass
class MintedEnvelopes:
    """The envelopes ONE request has already created, so a press mints one each.

    Plan step ``bank_import:X-f6a-4``, finding **N-327**, developer ruling
    2026-08-20.  A merchant rule answering *a new envelope called X* used to
    mint one PER LINE: measured on the developer's own statement, a ``Lowe's``
    answer places 4 lines over 3 pay periods, so one press made 4 envelopes,
    two of them in the SAME period.  No figure was wrong -- each closes at its
    own purchases -- and what fragmented was the budget.

    **Scoped to ONE REQUEST, and that scope is the design rather than a
    limitation.**  The cross-STATEMENT half is answered by the SUGGESTION
    instead (:func:`~._placement._new_envelope_placement` degrades to a
    ``RECORD_IN`` against a same-named envelope already in the period, which
    the owner sees printed beside the line and may override).  Only the
    within-one-press half needs a write-side rule at all, because at render
    time the envelope this press is about to create does not yet exist for any
    select to name.

    Keying on the CATEGORY as well as the name and the period is deliberate:
    two answers naming one word under two categories are two budget lines, and
    merging them would file spending under a category the owner did not pick.

    **A refused item leaves nothing here, and the CALLER is what makes that
    true.**  :func:`~._batch.apply_reviewed` remembers an envelope only after
    the act that made it has RETURNED, so a creation rolled back inside its own
    SAVEPOINT (ruling **R-FZ**) leaves no entry pointing at a row that no
    longer exists.  A first implementation remembered inside the create door,
    one line above the refusal that kills the item -- and the very next line of
    the sweep then looked up an id the rollback had taken, and died on
    ``NoneType``.  The registry cannot be written where the write is not yet
    known to have survived.

    Attributes:
        by_key: ``{(name, category_id, pay_period_id): transaction_id}`` for
            what this request has minted.
    """

    by_key: "dict[tuple[str, int, int], int]"

    @classmethod
    def none_yet(cls) -> "MintedEnvelopes":
        """Return the empty registry one request starts with."""
        return cls(by_key={})

    def envelope_for(
        self, new_envelope: NewEnvelope, pay_period_id: int,
    ) -> "int | None":
        """Return the envelope this request already minted for that answer.

        Args:
            new_envelope: The answer the owner stated.
            pay_period_id: The period the purchase is budgeted in.

        Returns:
            The transaction id, or ``None`` when this request has minted none.
        """
        return self.by_key.get(
            envelope_answer_key(new_envelope, pay_period_id),
        )

    def remember(
        self, new_envelope: NewEnvelope, created: "CreatedPurchase",
    ) -> None:
        """Record that this request minted an envelope for that answer.

        **Called by the BATCH after the act RETURNED**, never by the door that
        creates -- see the class docstring for what a first version cost.

        Args:
            new_envelope: The answer the caller submitted, which it still
                holds.  Taken as an argument rather than carried out through
                *created*, because a value threaded through a return only so
                its own caller can read it back is a round trip.
            created: What the act did, for the envelope and its period.
        """
        self.by_key[
            envelope_answer_key(new_envelope, created.pay_period_id)
        ] = created.transaction_id


def reject_ambiguous_destination(creation: PurchaseCreation) -> None:
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


def reject_incomplete_new_envelope(creation: PurchaseCreation) -> None:
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
    :func:`~._resolve.resolve_rows` rests on.  An envelope belonging to another
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


def _minted_or_new(
    creation: PurchaseCreation,
    category: Category,
    pay_period_id: int,
    scope: ReviewScope,
    minted: MintedEnvelopes,
) -> "tuple[Transaction, bool]":
    """Return the envelope this purchase goes in, minting one only if needed.

    **One press mints one envelope per answer per pay period** (finding
    **N-327**).  A second line reaching the same answer in the same period
    records into the one the first line made, rather than making another beside
    it.

    **Recording into it is the act that already ships**, not a new one: it is
    exactly what :func:`_existing_envelope` does for an envelope the screen
    offered, including a settled one -- ruling **R-FX** admits a new purchase
    on a settled row when its recorded figure IS its purchases and the purchase
    states the day the bank took it, and both hold for a row this door created.
    Measured 2026-08-20 on a two-line sweep: the envelope's cash leg is
    ``0.00`` before and after the second purchase, because each purchase
    carries its own posting day and is its own cash movement.

    **It does NOT re-settle the envelope, and that matches the shipped path.**
    Recording into an already-settled envelope leaves its close day alone
    today, and doing otherwise here would give one arm of this door a re-dating
    rule the other arm does not have.

    Args:
        creation: What the owner submitted, for the envelope's name.
        category: The category they picked, already proved theirs.
        pay_period_id: The period holding the day the purchase was made.
        scope: The pass, which is the ONE statement of whose account this is.
        minted: What this REQUEST has already created.

    Returns:
        ``(envelope, created)`` -- the row, and whether this act made it.
    """
    already = minted.envelope_for(creation.new_envelope, pay_period_id)
    if already is not None:
        return db.session.get(Transaction, already), False
    return _create_envelope(creation, category, pay_period_id, scope), True


def _close_day(
    creation: PurchaseCreation,
    observed: SettleDay,
    envelope: Transaction,
    created: bool,
) -> "SettleDay | None":
    """Return the day this envelope should CLOSE on, or ``None`` to leave it.

    Three cases, and the middle one is the correction adversarial review found
    2026-08-20.

    * a container this act CREATED closes on the day the bank took the money it
      holds;
    * a container an EARLIER LINE OF THIS SAME PRESS created closes on the
      LATEST of those days.  Measured before this arm existed: two lines in one
      pay period submitted 01-05 then 01-09 left the envelope recording that it
      closed on **2024-01-05 while holding `$45.00` the bank did not take until
      01-09** -- and submitting them the other way round recorded 01-09, so the
      close day was whichever line happened to be filed first.  No figure moved
      (each purchase carries its own posting day, so the envelope's own cash leg
      is `0.00` either way), but a row may not record closing before money it
      holds.  **The LATEST is this arc's own rule for a group** -- a match's day
      is ``max(posted_on)`` over its lines (``MatchDays.of``) -- applied to the
      group a press files;
    * a container that ALREADY EXISTED keeps its own close day.  That is the
      shipped behaviour of the destination arm and it is deliberate: that row's
      close is a record the OWNER made, and re-dating it would edit their
      record rather than complete this press's own.

    Args:
        creation: What the owner submitted, which says which arm this is.
        observed: The bank line's own posting day, as a day a statement SHOWED
            (:func:`~._create._observed`).  **The DAY rather than the line, and
            that is what the split at plan step ``bank_import:X-ge`` made
            structural**: this decision needs one fact about the bank line and
            nothing else, so taking the ORM row would be a container module
            reaching into what a purchase is -- and it is what let the two
            modules cut apart without an import cycle.  It is built once per
            act by the door, which is also what stopped this function and the
            purchase it sits beside constructing the same value twice.
        envelope: The container the purchase goes in.
        created: Whether THIS act created it.

    Returns:
        The day to close on and HOW that day is known
        (:class:`app.services.settle_day.SettleDay`), or ``None`` when the close
        is not this act's to write.  The basis is always ``observed`` (plan step
        **X-az**): every day this function can return is a bank line's own
        posting day, so a statement is what showed it.
    """
    if created:
        return observed
    if creation.transaction_id is not None:
        return None
    # The new-envelope arm reusing what an earlier line of this press minted.
    if envelope.settled_on is not None and observed.day <= envelope.settled_on:
        return None
    return observed


def close_container(
    creation: PurchaseCreation,
    observed: SettleDay,
    envelope: Transaction,
    created: bool,
) -> None:
    """Close the container on the day the bank took the money it now holds.

    :func:`_close_day` decides WHETHER and WHICH day; this applies it, through
    whichever verb the container's own state calls for.  Extracted from
    :func:`create_purchase_from_line` at plan step ``bank_import:X-ga``, when
    ruling **R-GJ**'s bar took that function past ``max-locals``: the two
    honest answers to a design limit are to decompose or to disable, and this
    block was already one coherent act with one subject -- everything a
    container's CLOSE needs, and nothing the purchase needs.

    Args:
        creation: What the owner submitted, which says which arm this is.
        observed: The bank line's own posting day, as a day a statement SHOWED,
            for the reason :func:`_close_day` states.
        envelope: The container the purchase went in.
        created: Whether THIS act created it.
    """
    close_on = _close_day(creation, observed, envelope, created)
    if close_on is not None and not created:
        # **A container an EARLIER LINE OF THIS PRESS made, closing again on a
        # later day.**  ``settle_from_entries`` refuses an already-settled row
        # by design -- "the seam owns the day, and a caller that genuinely
        # means *this settled on a different day* corrects it on the row
        # afterwards" (ruling **R-ED**) -- so the correction goes through the
        # same identity transition ``_accept._apply_day`` uses for a settled
        # row a match re-dates: the row's OWN status, a new day.  One verb for
        # "a settled row's day moved", not a second one here.
        transaction_service.apply_requested_status(
            envelope, envelope.status_id, settle_day=close_on,
        )
    elif close_on is not None:
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
            envelope, settle_day=close_on,
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


def resolve_destination(
    creation: PurchaseCreation,
    pay_period_id: int,
    scope: ReviewScope,
    matched: MatchedSubjects,
    minted: MintedEnvelopes,
) -> "tuple[Transaction, bool]":
    """Return the budget line this purchase goes in, and whether we made it.

    The two arms of ruling **R-FX**, resolved in one place: an envelope the
    owner picked from the set the screen offers, or one this door creates for
    the line.  :func:`reject_ambiguous_destination` has already refused a
    submission naming both or neither, so the branch below is a dispatch
    rather than a preference.

    Args:
        creation: What the owner submitted.
        pay_period_id: The period holding the day the purchase was made.
        scope: The pass's derived offer set.
        matched: What this account's matches have already claimed, as of this
            act.
        minted: What this REQUEST has already created.

    Returns:
        ``(envelope, created)`` -- the row, and whether this act made it.

    Raises:
        ValidationError: When the named envelope is not one the screen could
            have offered, or the named category is not this owner's.
    """
    if creation.transaction_id is not None:
        return (
            _existing_envelope(creation, pay_period_id, scope, matched), False,
        )
    return _minted_or_new(
        creation, _owned_category(creation, scope), pay_period_id, scope,
        minted,
    )
