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

**A stale PRICE is NOT safe across a pass, and this paragraph used to say it
was.**  The argument was that a candidate's figure is
``gross - Sigma(card entries) - Sigma(posted purchases)``
(:func:`app.services.cash_ledger.cash_leg_of`), so only a parent and its own
child could move each other -- which
:func:`~._accept._reject_parent_and_its_own_purchase` refuses.  **Measured
FALSE on 2026-08-19**: settling a matched purchase writes a SIBLING CC
Payback's ``estimated_amount``, which that guard cannot see.

**So a price is never taken off this scope.**  What the scope holds is WHICH
rows may be offered, which no act changes;
:func:`~._candidates.repriced` re-reads and re-values every row an act names,
and :func:`~._resolve.resolve_rows` then refuses one that has moved since the
screen described it (finding **N-336**, plan step ``bank_import:X-f6d-3``).
The 3.593 s this step exists to save belongs to the 827-row SCAN, which is
still derived once; an act names one to four rows.  This paragraph asserted the
refuted reason until an adversarial review found it 2026-08-23.

Services-boundary discipline: reads only, plain data in, a frozen dataclass
out, no Flask import, no clock read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.exceptions import ValidationError
from app.services import pay_calendar
from app.services.cash_ledger import AmountBasis, baseline_amount_basis

from ._candidates import candidates_for, destinations_for
from ._creations import PurchaseDestination
from ._offers import Candidates


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
            READ COMMITTED -- which since plan step balance:X-i3 is true of
            the THREE POST doors that build a scope
            (``apply_statement_review``, ``state_merchant_rules``,
            ``statement_review_totals``) and not of the GET that renders the
            screen, whose whole request is one snapshot.
            ``apply_statement_review`` builds TWO, and that is the one place
            two scopes in one request is right: the second is a FRESH one for
            the ANSWER, taken only on the path that WROTE, because the pass it
            was applied against describes a state that no longer exists.
            **The parameter stays either way, and not only for the arms it
            still protects**: three reads of one fact in one request is this
            project's DRY violation rather than a cost, and the doors it
            protects are the ones that MOVE MONEY.
            **It is not one per REQUEST, and saying
            so would be false**: ``entry_credit_workflow._create_payback``
            reads its own, and a creation filing a purchase into an envelope
            whose payback was soft-deleted reaches it.  Narrowing the claim
            rather than widening the parameter, because that module is another
            arc's.
        basis: The owner's :class:`~app.services.cash_ledger.AmountBasis`.
            ONE per pass, for the reason ``calendar`` above it is one (plan
            step X-au-j, finding **N-309**): every producer under a read pass
            takes the pass's derivations rather than building its own.  It was
            built PER PRICED ROW until this step -- **609 salary-pricing and
            609 loan-pricing constructions** over 825 candidates, `4.7 s` to
            render, with the accept door paying it all again.

            **It belongs in the scope on the scope's own rule**, which is that
            a scope holds what a pass CANNOT change: a basis carries the
            owner's salary derivation and the scenario's loan derivation, and
            no act this package performs writes a salary profile, a payday, a
            loan parameter or an escrow line.  It holds no per-row answer, so
            :func:`~._candidates.repriced` still re-reads every row it values.
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
    basis: AmountBasis
    candidates: Candidates
    destinations: "tuple[PurchaseDestination, ...]"

    def period_holding(self, day: "date", subject: str) -> int:
        """Return the id of the pay period covering *day*, refusing if none does.

        **ONE statement of it for the whole package** (plan step
        ``bank_import:X-f6d-4``): :mod:`._create` places a purchase it records
        and :mod:`._variance` places the row it mints for a group's
        difference, and the two had the identical body apart from one noun.
        A second spelling of "which paycheck holds this day" is this arc's own
        root cause 1 on the column that decides which budget a movement lands
        in.

        **It is a method rather than a free function taking a calendar**,
        because the calendar it must use is THIS pass's.  Loading a second one
        is what :func:`~._create.create_purchase_from_line` was doing until
        plan step ``bank_import:X-f6a-3c-2``, and under READ COMMITTED the two
        can differ -- so a line could be OFFERED against one calendar and
        PLACED against another.  Taking it off the scope makes that
        unrepresentable rather than merely avoided.

        **Plan step balance:X-i3 does not retire that argument, and this is
        the one site in the package where saying so needs care.**  Every
        placement runs inside a POST, so the transaction is a command's and
        the two reads really are two snapshots.  It is also the only
        accommodation here whose two halves sit in DIFFERENT requests -- a
        line is offered by the GET and placed by the POST, which no
        per-request snapshot could ever reconcile -- so what makes the pairing
        safe is that the POST re-derives BOTH from one scope of its own, which
        is exactly what this method being a method enforces.

        **WHICH day a caller passes is the caller's decision and they differ**:
        a purchase is placed by the day it was MADE, because it has a budget
        clock of its own, and a residual by the day the match POSTS on,
        because it has none -- it IS the movement.

        Args:
            day: The civil day the money is placed by.
            subject: What is being placed, for the refusal's sentence -- "this
                purchase" or "the difference on this match".  Taken rather
                than composed here, because a refusal an owner reads has to
                name the act they performed.

        Returns:
            The covering period's id.

        Raises:
            ValidationError: When no SAVED period covers it.  **Both of
            ``period_containing``'s two answers reach here**: a day before the
            owner's first payday, and one past the generated horizon.  The
            review screen splits off only the FIRST
            (:func:`~._reads._split_at_calendar_open`), so a line posted past
            the last saved period reaches a caller -- which is why callers
            resolve this BEFORE they write anything.
        """
        period = self.calendar.period_containing(day)
        if period is None:
            raise ValidationError(
                f"No pay period covers {day.isoformat()}, so there is no "
                f"budget for {subject} to belong to.  Extend your pay "
                f"schedule to cover that day first.  Nothing was changed."
            )
        return period.period_id

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
            BaselineMissingError: From
                :func:`~app.services.scenario_resolver.require_baseline_scenario`,
                answered by the application-level handler that renders the
                setup-recovery page (ruling **R-BW**).  The RAISING form is
                right here on that ruling's own criterion: this pass's answer
                is UNDEFINED without a scenario, because a basis prices rows
                from a scenario's salary profiles and a scenario's loans and
                there is no honest figure to publish when nobody can say
                which.
        """
        calendar = pay_calendar.calendar_for(owner_id)
        # ONE basis for the pass (plan step X-au-j, finding **N-309**).  The
        # BASELINE pin and its Phase-1 deferral are stated once, in
        # ``cash_ledger.baseline_amount_basis``; it matches the ground
        # ``_candidates._transaction_candidates`` already gives for not
        # filtering the candidate scan on ``scenario_id``.
        #
        # A foreign-scenario row is REPORTED rather than raised in this
        # package: ``_candidates._price`` catches ``AmountUnresolvable`` and
        # the row leaves the candidate set into ``unpriceable``, which the
        # screen counts.  Unreachable today, and NOT the same answer the
        # reconcile panel gives -- said here because a first draft of this
        # comment claimed both passes "fail loud" and only one does.
        basis = baseline_amount_basis(owner_id)
        return cls(
            owner_id=owner_id,
            account_id=account_id,
            calendar=calendar,
            basis=basis,
            candidates=candidates_for(account_id, calendar, basis),
            destinations=tuple(destinations_for(owner_id, account_id)),
        )
