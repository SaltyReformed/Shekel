"""ONE derivation of what a review pass over one account may act on.

Plan step ``bank_import:X-f6a-3c-2``.  Reviewing a statement is a pass over one
account, and until this step every ACT inside that pass derived the whole
account again for itself: 3.593 s of :func:`~._candidates.candidates_for` per
accept on the developer's own clone (827 candidate rows), against 215 acts the
screen offers -- **12.88 minutes of derivation to work one statement**, which is
finding **N-306**'s cost and finding **N-309**'s cause.  Measured again at the
end of this step, applying that same statement end to end against ONE of these:
**5.80 s**, of which 3.65 s is this one derivation.

**What belongs in a scope is what a pass CANNOT change**, and that is the whole
rule for what this holds:

* the owner's pay CALENDAR -- nothing here writes a payday;
* every row the account could offer, PRICED (:class:`~._offers.Candidates`);
* every budget line a bank line could become a purchase against.

**What is deliberately NOT here is what the pass DOES change**: which subjects
a match has already claimed.  Every act re-reads that for itself
(:func:`~._candidates.matched_subjects`) and narrows this scope through
:func:`~._candidates.unmatched_rows` / :func:`~._candidates.unmatched_destinations`,
so an item cannot be handed a row the item before it has just matched.  A scope
that had baked the claims in would have offered 15 of the developer's 91
creatable lines an envelope an earlier proposal in the same pass claims.

**Why a stale PRICE is safe across a pass, stated rather than assumed.**  A
candidate's figure is ``gross - Sigma(card entries) - Sigma(posted purchases)``
(:func:`app.services.cash_ledger.cash_leg_of`), so an act can only move another
row's price by adding a purchase to it or by stamping a posting day on one it
already holds -- both of which make that row and that purchase a parent and its
own child, which :func:`~._accept._reject_parent_and_its_own_purchase` refuses
across matches.  That guard reads the database, so each act flushes before the
next is validated (:mod:`._batch`).  Measured on the developer's own statement:
all 124 proposals produce byte-identical outcomes applied against one shared
scope and against a fresh derivation per act.

Services-boundary discipline: reads only, plain data in, a frozen dataclass
out, no Flask import, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services import pay_calendar
from app.services.cash_ledger import AmountBasis, amount_basis
from app.services.scenario_resolver import require_baseline_scenario

from ._candidates import candidates_for, destinations_for
from ._offers import Candidates, PurchaseDestination


@dataclass(frozen=True)
class ReviewScope:
    """Everything one review pass over one account may act on.

    Attributes:
        owner_id: The user the caller proved owns the account.
        account_id: The account whose statement is being reviewed.
        calendar: The owner's
            :class:`~app.services.pay_calendar.PayCalendar`.  ONE per pass for
            everything THIS package asks: three sites here asked
            ``calendar_for`` separately inside one request until adversarial
            financial review 2026-08-19, and two of them can disagree under
            READ COMMITTED.  **It is not one per REQUEST, and saying so would
            be false**: ``entry_credit_workflow._create_payback`` reads its
            own, and a creation filing a purchase into an envelope whose
            payback was soft-deleted reaches it.  Narrowing the claim rather
            than widening the parameter, because that module is another arc's.
        amounts: The pass's :class:`~app.services.cash_ledger.AmountBasis`,
            built ONCE for everything this package prices (plan step X-au-j).
            It is the calendar's twin one column over and it is here for the
            same reason: a basis holds the owner's live DERIVATIONS -- the
            paycheck engine over the whole pay-period set, each loan's P&I and
            escrow history -- and asking for them per ROW is finding **N-228**,
            which ``amount_basis``'s own docstring names.  Finding **N-309**
            measured this pass doing exactly that: **609 salary-pricing and 609
            loan-pricing constructions** over 825 candidates, `4.7 s` to render,
            and the accept door paying it again.  It is built for the BASELINE
            scenario because the candidate scan is baseline-only by the same
            argument ``_candidates`` states for not filtering on
            ``scenario_id``; a row from another scenario is REFUSED by
            ``resolve_transaction_amount`` rather than mispriced, which is the
            direction that fails loud.
        candidates: Every row the account could offer, priced
            (:func:`~._candidates.candidates_for`), before any claim is
            narrowed out.
        destinations: Every budget line a bank line could become a purchase
            against (:func:`~._candidates.destinations_for`), before any claim
            is narrowed out.
    """

    owner_id: int
    account_id: int
    calendar: "pay_calendar.PayCalendar"
    amounts: "AmountBasis"
    candidates: Candidates
    destinations: "tuple[PurchaseDestination, ...]"

    @classmethod
    def build(cls, owner_id: int, account_id: int) -> "ReviewScope":
        """Derive the scope for one pass over one account.

        The expensive call in the app's whole import path, made once.

        Args:
            owner_id: The user the caller proved owns the account.
            account_id: The account whose statement is being reviewed.

        Returns:
            The :class:`ReviewScope`.

        Raises:
            PayCalendarError: From
                :func:`~app.services.pay_calendar.calendar_for`, when the owner
                has paydays and no resolvable cadence.  Fails loud rather than
                rendering as a designed refusal: a matcher cannot bound a row
                without the calendar that says which paycheck it is budgeted
                in, and answering anyway is the unbounded state finding
                **N-312** records.
        """
        calendar = pay_calendar.calendar_for(owner_id)
        amounts = amount_basis(
            owner_id, require_baseline_scenario(owner_id).id,
        )
        return cls(
            owner_id=owner_id,
            account_id=account_id,
            calendar=calendar,
            amounts=amounts,
            candidates=candidates_for(account_id, calendar, amounts),
            destinations=tuple(destinations_for(owner_id, account_id)),
        )
