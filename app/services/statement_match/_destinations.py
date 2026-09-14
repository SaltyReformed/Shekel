"""What budget line a bank line could BECOME a purchase against.

**Split out of :mod:`._candidates` at plan step ``balance:X-bi-7b``**, when
that module crossed this project's 1,000-line bound (ruling **balance:R-IR**:
the session that breaks a module splits it).  The seam is the one its own
docstring already drew -- two questions about two acts.  :mod:`._candidates`
answers *what has this account recorded that a statement could be showing*
(:func:`~._candidates.candidates_for`), and this answers *what budget line
could a statement line BECOME a purchase against*
(:func:`destinations_for`), which is ruling **R-FS**'s third shape.  Nothing
changed on the way across except the one field the step added
(``PurchaseDestination.recurs``).

**One scope, shared by the screen that offers and the door that writes**: a
row this does not return cannot be reached by crafting a request, and a row
it does return cannot be refused by the write door for being out of scope --
the property ``reconcile_service`` is built on and
:func:`~._candidates.matched_subjects` narrows per act.

Services-boundary discipline (``CLAUDE.md`` Architecture): reads only, plain
data in, frozen dataclasses out, no Flask import, no clock read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import joinedload

from app import ref_cache
from app.enums import SettlementBasisEnum
from app.extensions import db
from app.models.transaction import Transaction
from app.utils.balance_predicates import balance_contributing_clause

from ._creations import PurchaseDestination

if TYPE_CHECKING:  # pragma: no cover -- annotations only
    from app.services.pay_calendar import PayCalendar


def destinations_for(
    account_id: int, calendar: "PayCalendar",
) -> "list[PurchaseDestination]":
    """Return every budget line a bank line could become a purchase against.

    **ONE scope, shared by the screen that offers a destination and the door
    that writes into it** (:func:`~._container._existing_envelope`), which is the
    property :func:`~._resolve.resolve_rows` rests on: a row this does not return
    cannot be reached by crafting a request, and a row it does return cannot be
    refused by the write door.  Every clause below is one of those doors'.

    **It lived beside :func:`~._candidates.candidates_for` because it is the
    same kind of answer about the other act** (plan step
    ``bank_import:X-f6a-3c-2``), and because a review pass derives both
    together and threads them: it was in ``_reads`` while that module was the
    only caller, and the write doors reached across for it.  It has its own
    module since plan step ``balance:X-bi-7b`` (the module docstring says
    why); the pass still derives the two together.

    Scope, and what each clause is:

    * on THIS account, and its pay period is one the OWNER'S CALENDAR holds --
      a statement is one bank's record of one account.  Ownership through the
      paycheck; ``C13-b`` REFUSED ``Transaction.user_id``.  **The ids come
      from the calendar
      rather than from a correlated subquery on ``pay_periods.user_id``, and
      that is what makes the span lookup below total** (pay-calendar plan step
      C4-a-4): the scan filters on
      :meth:`~app.services.pay_calendar.PayCalendar.saved_by_id`'s own keys and
      then indexes that same mapping, so a row it cannot place is
      unconstructible rather than skipped.  It is the clause
      :func:`~._candidates._transaction_candidates` already carries, for its stated reason --
      inside a COMMAND the two reads are separate snapshots under READ
      COMMITTED, so a concurrent payday INSERT between them is expressible, and
      scoping by the calendar's own ids means the query simply does not ask
      about a period the calendar has not got;
    * it TRACKS PURCHASES -- ``entry_service.create_entry`` refuses a parent
      that does not, and a purchase needs a container that can hold more than
      one;
    * it is not a TRANSFER and not INCOME -- both are ``create_entry``
      refusals: a transfer's legs are the transfer service's, and money coming
      in is not a purchase;
    * it CONTRIBUTES to a balance and is not soft-deleted
      (:func:`~app.utils.balance_predicates.balance_contributing_clause`) -- a
      Credit or Cancelled row records no cash, so a purchase filed under one
      would post nothing (ruling **R-FM**);
    * if it has SETTLED, its recorded figure IS its purchases.  **This is the
      money clause** (:func:`~app.services.entry_service._doors
      ._reject_settled_addition`): on a ``purchases`` basis a new purchase
      raises what the row cost by exactly its own amount and the row's cash leg
      does not move, so the movement is recorded; on a stored-figure basis the
      gross cannot rise, and ``settled_cash_leg`` then subtracts money the gross
      never held -- measured on a production clone, `-163.95` became `+203.67`
      while the anchor true-up moved `$0.00`.

    **A SIXTH clause stood here until plan step balance:X-am** (ruling
    **balance:R-HA**): the row must not be ARCHIVED, because an archived row's
    purchases were history (finding **N-229**) and
    :func:`~._candidates._purchase_candidates` declined to offer one.  The terminal
    ``Settled`` status is deleted, so both arms dropped the clause in one
    commit and still agree on what they offer.

    **Whether it is ITSELF MATCHED is NOT a clause here**, and that is this
    step's change rather than a relaxation: it is
    :func:`~._candidates.unmatched_destinations`,
    applied by the screen against the claims it read and by
    :func:`~._container._existing_envelope` against the claims that ACT read.  The
    rule is unchanged -- ``accept_match``'s
    :func:`~._accept._reject_parent_and_its_own_purchase` refuses a purchase
    whose parent another match already names, so offering such an envelope
    would render a chooser whose submission always fails.  What changed is
    WHEN it is asked, and it had to: measured on the developer's own statement,
    4 envelopes (2225, 2228, 2389, 2581) are both named by a proposal and
    offered as a destination, so **15 of the 91 creatable lines aim at an
    envelope an earlier item in the same pass claims**.  A snapshot carrying
    the clause baked in would have offered all 15 and refused them a tier
    deeper, with the sentence about counting money twice rather than the one
    about the envelope being gone.

    **Finding N-317 says this clause is wider than the money needs, and the
    developer's ruling of 2026-08-19 is that it STAYS WHOLE**: a money guard is
    not narrowed for a `$0.00` benefit.  The row is OPEN in ``ledger.md`` with
    its diagnosis corrected -- an earlier closure argued the clause protects a
    projected envelope holding no entries, whose leg moves `+111.02` when a
    purchase is added, and adversarial review measured that shape unreachable
    through this clause: a match SETTLES the envelope it names, and a
    zero-entry settle records a STORED FIGURE, which the money clause above
    already refuses.

    Args:
        account_id: The cash account the statement is for.
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`, built by the read
            pass.  **It IS the ownership scope**, which is why no ``owner_id``
            sits beside it -- the rule :func:`~._candidates.candidates_for` states for its
            own signature, applied here at pay-calendar plan step C4-a-4: the
            periods it carries are exactly that owner's, so a second parameter
            naming the owner would be a second statement of whose rows may be
            offered and the two could disagree.  It is also where each offered
            row's SPAN comes from, DERIVED, where this producer read
            ``txn.pay_period.end_date`` -- a stored copy of a derivable fact
            that plan step ``pay_calendar:C4-c`` dropped.

    Returns:
        One :class:`~._creations.PurchaseDestination` per offerable row, oldest
        pay period first and then by name -- a deterministic order, so the
        chooser a screen shows does not depend on what the planner returned.
        **Ordered by the paycheck's own PAYDAY rather than by its id**, which
        is what "oldest" means: the two agree on every schedule written
        forward, and plan step ``pay_calendar:C6`` inserts a payday
        MID-SCHEDULE by design, which would give the newest row the newest id
        in the middle of the sequence.
    """
    purchases_basis = ref_cache.settlement_basis_id(
        SettlementBasisEnum.PURCHASES,
    )
    # The owner's SAVED paychecks, keyed the way a row names one.  This ONE
    # mapping is both halves of the answer -- the scan's ownership scope on the
    # line below, and the span every offered row is labelled by -- so the two
    # cannot describe different schedules and the lookup cannot miss.
    spans = calendar.saved_by_id()
    rows = (
        db.session.query(Transaction)
        .options(
            # ``tracks_purchases`` below reads ``template.is_envelope`` for
            # every template-generated row, so the template travels with the
            # scan for the same reason ``_transaction_candidates`` loads it:
            # a predicate in the comprehension must not cost a query per row.
            # **``Transaction.pay_period`` is NOT loaded beside it** since
            # pay-calendar plan step C4-a-4: the relationship was here to read
            # the period's stored span, and the span now comes off ``spans``.
            joinedload(Transaction.template),
        )
        .filter(
            Transaction.account_id == account_id,
            Transaction.transfer_id.is_(None),
            balance_contributing_clause(),
            Transaction.pay_period_id.in_(spans.keys()),
        )
        .all()
    )
    offered = [
        PurchaseDestination(
            transaction_id=txn.id,
            name=txn.name,
            category_id=txn.category_id,
            # Indexed rather than searched, and a ``KeyError`` here is
            # unconstructible: the filter above IS this mapping's key set.
            period=spans[txn.pay_period_id],
            is_settled=txn.status.is_settled,
            # The row's identity ACROSS periods, which is what a merchant
            # rule names (plan step X-f6a-3d) -- and whether a CADENCE stands
            # behind it, which is what tells a recurring envelope from a
            # one-off's (plan step balance:X-bi-7b).  The rule rides on the
            # template's joined load above, so this costs no query per row.
            template_id=txn.template_id,
            recurs=txn.recurs,
        )
        for txn in rows
        if txn.tracks_purchases
        and not txn.is_income
        and (
            not txn.status.is_settled
            or txn.settled_basis_id == purchases_basis
        )
    ]
    offered.sort(key=lambda d: (d.period.start_date, d.label))
    return offered
