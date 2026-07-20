"""Balance-at-T seam -- a loan's RICH figures, with the balance deliberately absent.

The seam's fourth shape.  A loan tile wants more than a balance: the monthly
payment, the current rate, the payoff date, and whether the loan is retired.
Those are rich projection detail, not a balance-at-T, and the seam has always
been happy for a consumer to hold them.

What it must NOT hand out is the BALANCE.  ``LoanState.current_balance`` is a
balance-at-today, and the W9906 fence binds on function NAMES -- it cannot see
an attribute read.  So for as long as consumers held a ``LoanState``, the loan's
displayed balance reached the screen without passing the seam: the /savings loan
tile, the net-worth hero that reduces over it, the debt card, the Horizon's
index-0 liability point, and the property-equity card's mortgage leg were ALL
produced outside the one tested place, and the fence was structurally incapable
of noticing.  They agreed with the seam only because both paths happened to
bottom out in the same genesis ledger -- agreement by luck, not by construction,
which is the exact failure signature of the whole balance-bug family
(``docs/audits/balance_architecture/``).

:class:`LoanFigures` closes that by CONSTRUCTION rather than by policing: it
carries no balance, so a consumer holding one cannot render a wrong balance even
by accident.  A consumer that wants a loan's balance has exactly one way to get
it -- :func:`~app.services.balance_at.balance_at` -- which is the point.  This is
the same "do not hand ``current_balance`` to out-of-cluster callers" move that
:func:`~app.services.balance_at.debt_schedule_rows` makes for the
``DebtSchedule`` bundle (``followup_debt_schedule_attribute_fence.md``).

``is_paid_off`` lives here, not in a consumer, for the same reason: it is a
LEDGER-derived predicate over the loan's confirmed balance, so it belongs beside
the balance rules rather than in a dashboard module that would have to reach for
the balance to compute it.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.services.loan_resolution import ResolvedLoan
from app.services.resolution_context import BalanceContext

# The payoff derivation this module INJECTS into the read pass's memo (see
# :func:`loan_figures`).  ``_positions`` does not import this module, so the
# seam's internal graph stays a DAG.
from ._positions import loan_payoff_date

ZERO_MONEY = Decimal("0.00")


@dataclass(frozen=True)
class LoanTerms:
    """A loan's CONTRACT facts -- everything derivable without a scenario.

    The scenario-INDEPENDENT half of what a loan surface reads.  Every field here
    comes from the loan's params and its rate history evaluated at the pass's
    ``as_of``: no projected payment, no ledger walk, no baseline scenario.

    **Why this is its own type (plan step C8e).**  :class:`LoanFigures` used to
    carry these four fields alongside the scenario-scoped ones, and that mixture
    was invisible while every field happened to be scenario-independent.  Step C8d
    added the first scenario-scoped field (the DERIVED ``payoff_date``, which folds
    the loan's projected payments), and the mismatch surfaced immediately as an
    outage: the escrow editor, the rate-history swap, and the recurring-payment
    amount read ONLY ``monthly_payment`` / ``current_rate``, never a balance, yet
    they began raising the seam's ``require_scenario`` for a user with no baseline
    -- a state ``baseline_service`` exists to repair, and one a loan can outlive
    ("a loan configured while the baseline was gone").

    Splitting the two along the dependency they actually have is what fixes that at
    the root: a consumer takes the narrower value when the narrower value is what it
    needs, so fail-loud stays exactly where a scenario is genuinely required and
    nothing degrades into a guessed answer.  No balance here either, for the same
    reason :class:`LoanFigures` carries none.

    Attributes:
        monthly_payment: The loan's P&I payment as of the context's ``as_of``
            (the governing rate period's level payment).
        current_rate: The annual interest rate in effect on ``as_of``, as a
            decimal fraction -- the resolver-derived source of truth that
            replaced the retired ``LoanParams.interest_rate`` column.
        is_originated: Whether the loan EXISTS yet -- whether its
            ``origination_date`` has arrived by the read pass's ``as_of``.

            The seam publishes this because ``balance_at`` correctly answers
            ``$0.00`` for a loan that has not been borrowed yet, and a consumer
            that reads a zero balance as "this debt is gone" then reports the
            opposite of the truth.  Three did: the dashboard's debt track counted
            an unclosed mortgage's whole principal as REPAID (66.67% paid, on a
            borrower who had paid nothing), the property equity chart dropped a
            mortgage closing in 26 days and drew ten years of debt-free equity, and
            the year-end panel reported -$198,049.28 of principal "paid".  A zero
            balance means "owes nothing"; it does NOT mean "has no debt ahead of
            it", and only this flag separates the two.
        is_arm: Whether the loan is an adjustable-rate mortgage
            (``LoanParams.is_arm``).  Projection detail, not a balance: it tells
            /debt-strategy to caption its projection as rate-assumption-bound (the
            strategy holds the CURRENT rate fixed and does not re-apply future ARM
            adjustments).  It is here so that consumer no longer needs the whole
            ``ResolvedLoan`` -- it reached for ``ctx.resolved_loan(account).params``
            to read this ONE boolean, and a route holding a ``ResolvedLoan`` is a
            route one attribute read away from an unfenced loan balance.
    """

    monthly_payment: Decimal
    current_rate: Decimal
    is_originated: bool
    is_arm: bool


@dataclass(frozen=True)
class LoanFigures:
    """A loan's SCENARIO-SCOPED figures -- deliberately WITHOUT its balance.

    Every field is projection detail a loan tile renders beside the balance; the
    balance itself is not here, and its absence is the point (see the module
    docstring).

    **It COMPOSES :class:`LoanTerms` rather than re-declaring its fields** (plan
    step C8e).  Everything here needs the pass's baseline SCENARIO -- the payoff
    folds the loan's projected payments, the retired predicates read its
    scenario-scoped ledger walk -- while a loan's contract terms need none, so the
    two are separate values and a consumer takes the one it actually needs.
    Composing rather than copying is the same ruling
    :class:`~app.services.savings_dashboard_service._types._LoanAccountResult`
    carries: it used to copy these fields, and the copy "silently went stale the
    moment the seam grew ``is_originated``" -- a bundle hand-synchronised with the
    one it mirrors is the seam's fence with a hole in it.

    Attributes:
        terms: The loan's scenario-independent :class:`LoanTerms` (payment, rate,
            originated, ARM).  Read through here so there is ONE derivation of
            them, whichever value a consumer holds.
        payoff_date: The DERIVED payoff -- the date the loan's balance folds to
            zero (:func:`~app.services.balance_at.loan_payoff_date`, plan step
            C8), read off the pass's memo so every surface that shows a payoff
            shows the one the BALANCE reaches zero on.

            It used to be ``LoanState.payoff_date``: the last row of the
            resolver's committed schedule walk, which amortizes one contractual
            installment per month whether or not a payment stands behind it, and
            which forces a final row at the contractual date for a loan paying
            short (``is_last_month``) -- a phantom payment.  So the chip could
            disagree with the equity chart and the balance beside it about when
            the debt ends.  Now the payoff IS the balance reaching zero, from the
            same seed and the same plan :func:`~app.services.balance_at.positions`
            folds, and they cannot disagree.

            ``None`` in two cases the consumer must tell apart, and
            :attr:`is_retired` is what tells them apart: the loan is already
            RETIRED (no forward crossing left to date -- badge it), or it never
            pays off within its plan (negative amortization, or an underpayment
            too severe to clear the post-contractual extension -- say so; do not
            hide the chip).
        is_retired: Whether the loan is DONE -- it has ORIGINATED and its
            LEDGER-confirmed balance is ``<= 0``.  It has no debt line left: no
            balance now, and nothing scheduled ahead.

            THE single definition of "this loan has no debt to draw", which the
            property equity chart drops on.  A loan that has NOT been borrowed yet
            also owes ``$0.00`` and is emphatically not retired -- its whole debt
            line is ahead of it -- which is what the origination half of this test
            separates, and what kept a mortgage closing in 26 days on the chart.
        is_paid_off: Whether the loan is retired AND the ledger can show at least
            one confirmed payment for it.  **Strictly narrower than
            :attr:`is_retired`, and built on it, so the two cannot drift.**

            The extra confirmed-payment guard is a BADGING rule, not a debt rule:
            it keeps a brand-new loan with a degenerate ``$0`` opening anchor -- a
            misconfiguration, not an achievement -- from being badged "paid off" on
            the /savings tile and dropped from the debt card.

            Use :attr:`is_retired` to decide whether a loan has a debt line; use
            this to decide whether to CONGRATULATE the user.  Collapsing the two
            was measured: a mortgage paid off by a lump sum recorded as a balance
            true-up (no payment rows) reads ``is_paid_off=False``, so the equity
            chart charted it -- and since a zero-balance loan has an EMPTY
            schedule, the back-projection clip admitted its whole 360-row
            contractual walk and drew **$197,049.32** of debt the borrower does not
            owe, on the same page as an equity hero reporting ``$0.00``.
    """

    terms: LoanTerms
    payoff_date: date | None
    is_retired: bool
    is_paid_off: bool


def loan_terms(
    account: Account, ctx: BalanceContext,
) -> LoanTerms | None:
    """Return *account*'s CONTRACT terms, or ``None`` if it is not a loan.

    The scenario-INDEPENDENT read (plan step C8e): payment, rate, originated, ARM,
    all off the read pass's ONE memoized resolution
    (:meth:`~app.services.resolution_context.BalanceContext.resolved_loan`).  It
    needs no baseline scenario and derives no balance, so the loan's non-balance
    WRITE surfaces -- the escrow editor, the rate-history swap, the recurring
    payment's amount -- take this rather than the scenario-scoped
    :class:`LoanFigures` they used to.

    Those surfaces are why this exists.  They read only the payment and the rate,
    yet holding the wider bundle coupled them to a baseline scenario the moment
    step C8d gave it a scenario-scoped field, turning an escrow edit into a 500 for
    a user whose baseline is missing.  Narrowing the value narrows the dependency;
    the fail-loud stays on :func:`loan_figures`, where a scenario genuinely IS
    required.

    Args:
        account: The account to read.  A non-loan (no ``LoanParams``) returns
            ``None``.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.
            Its ``scenario`` may be ``None``.

    Returns:
        The :class:`LoanTerms`, or ``None`` when *account* is not a configured
        loan.
    """
    resolved = ctx.resolved_loan(account)
    if resolved is None:
        return None
    return _terms_from(resolved, ctx.as_of)


def _terms_from(resolved: ResolvedLoan, as_of: date) -> LoanTerms:
    """Build a resolved loan's :class:`LoanTerms`.

    THE one assembly, shared by :func:`loan_terms` and :func:`loan_figures`, so
    the terms a consumer reads are the same values whichever of the two values it
    holds -- the composition the wider bundle rests on.

    Args:
        resolved: The loan's
            :class:`~app.services.loan_resolution.ResolvedLoan`.
        as_of: The read pass's as-of.

    Returns:
        The loan's :class:`LoanTerms`.
    """
    return LoanTerms(
        monthly_payment=resolved.state.monthly_payment,
        current_rate=resolved.state.current_rate,
        is_originated=_is_originated(resolved, as_of),
        is_arm=bool(resolved.params.is_arm),
    )


def loan_figures(
    account: Account, ctx: BalanceContext,
) -> LoanFigures | None:
    """Return *account*'s scenario-scoped loan figures, or ``None`` if not a loan.

    Reads the read pass's ONE memoized resolution
    (:meth:`~app.services.resolution_context.BalanceContext.resolved_loan`), so
    these figures and the balance the same consumer reads from
    :func:`~app.services.balance_at.balance_at` come from the SAME resolution --
    identical by construction, not by two producers agreeing.  The payoff is the
    pass's ONE memoized derivation for the same reason: this is the single funnel
    every payoff consumer reads through (the loan card, the /savings cockpit and
    Horizon, the equity chart's axis), so it is where the derivation is INJECTED
    into the pass's memo
    (:meth:`~app.services.resolution_context.BalanceContext.loan_payoff` --
    injected rather than imported there, so the context never reaches up into the
    seam that imports it).

    **A CONFIGURED loan needs a baseline scenario here**, and that is the whole
    point of the :class:`LoanTerms` split (step C8e): every field on this value is
    scenario-scoped, so requiring one is honest rather than incidental.  A consumer
    that needs only the contract terms takes :func:`loan_terms` and is not bound by
    the guard at all.  The not-a-loan test still runs BEFORE the guard, so a caller
    using this purely as "is this a configured loan?" for a user with no baseline
    (:func:`app.services.home_equity_service.resolve_home_equity`) keeps answering.

    Args:
        account: The account to read.  A non-loan (no ``LoanParams``) returns
            ``None``; the caller renders its non-loan tile.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`.

    Returns:
        The :class:`LoanFigures`, or ``None`` when *account* is not a configured
        loan.

    Raises:
        ValueError: When *account* IS a configured loan and ``ctx`` has no
            baseline scenario (the payoff derivation's ``require_scenario``).
    """
    resolved = ctx.resolved_loan(account)
    if resolved is None:
        return None
    return LoanFigures(
        terms=_terms_from(resolved, ctx.as_of),
        payoff_date=ctx.loan_payoff(account, loan_payoff_date),
        is_retired=_is_retired(resolved, ctx.as_of),
        is_paid_off=_is_paid_off(resolved, ctx.as_of),
    )


def _is_originated(resolved: ResolvedLoan, as_of: date) -> bool:
    """Return whether the loan has come into existence by *as_of*.

    THE one definition of "does this loan exist yet", shared by
    :attr:`LoanFigures.is_originated` and :func:`_is_paid_off` so the seam cannot
    answer it two ways.  See :attr:`LoanFigures.is_originated` for why the seam
    publishes it at all.

    Args:
        resolved: The loan's
            :class:`~app.services.loan_resolution.ResolvedLoan`.
        as_of: The read pass's as-of.

    Returns:
        ``True`` when the loan's ``origination_date`` has arrived.
    """
    return resolved.params.origination_date <= as_of


def _is_retired(resolved: ResolvedLoan, as_of: date) -> bool:
    """Return whether the loan is DONE -- borrowed, and now owing nothing.

    THE one definition of "this loan has no debt line left", shared by
    :attr:`LoanFigures.is_retired` and :func:`_is_paid_off` (which is this plus a
    badging guard), so the seam cannot answer it two ways.

    ``resolved.state.current_balance`` is the ledger-confirmed balance (the read
    switch seeds it from ``confirmed_loan_balance_at``), so this asks the ONE
    producer that books what each payment actually paid.

    **A loan that has not ORIGINATED is not retired; it has not been taken out.**
    That guard is load-bearing, not defensive: ``current_balance`` is correctly
    ``0.00`` for a loan configured before it closes, so without it an unclosed
    mortgage reads as DONE -- dropped from the debt card, gone from the Horizon's
    liabilities, and erased from the property equity chart, which then draws ten
    years of debt-free equity on a house about to carry a mortgage.

    Args:
        resolved: The loan's
            :class:`~app.services.loan_resolution.ResolvedLoan`.
        as_of: The read pass's as-of, against which the loan's origination is
            tested.

    Returns:
        ``True`` when the loan has originated and the ledger says nothing is owed.
    """
    if not _is_originated(resolved, as_of):
        return False
    return resolved.state.current_balance <= ZERO_MONEY


def _is_paid_off(resolved: ResolvedLoan, as_of: date) -> bool:
    """Return whether the loan is retired AND has a confirmed payment behind it.

    Strictly narrower than :func:`_is_retired`, and BUILT ON IT so the two cannot
    drift.  The extra guard is a BADGING rule, not a debt rule: it keeps a
    brand-new loan with a degenerate ``$0`` opening anchor -- a misconfiguration,
    not an achievement -- from being badged "paid off" on the /savings tile.

    This replaced a ``resolve_loan(inputs, date.max)`` probe that could not have
    consulted the ledger even in principle -- ``confirmed_loan_view`` returns
    ``None`` for any ``as_of`` after today -- and so answered from the
    pre-read-switch anchor replay, which is BLIND TO MONEY: it advances one
    SCHEDULED step per confirmed payment and discards the cash.  A loan retired
    by one lump-sum payment read as still-owing (no badge, still active debt on
    the Horizon), and a loan paid SHORT could read as retired and VANISH from the
    debt card's total.  Both are regression-tested
    (``followup_redundant_loan_resolution.md``).

    **Do NOT use this to decide whether a loan has a debt line** -- that is
    :func:`_is_retired`.  A mortgage paid off by a LUMP SUM recorded as a balance
    true-up has no payment rows, so it reads ``False`` here while owing ``$0.00``.
    Charting on this predicate drew **$197,049.32** of phantom debt for such a
    loan (its zero balance means an EMPTY schedule, so the equity chart's
    back-projection clip admitted its entire contractual walk), beside an equity
    hero correctly reporting ``$0.00`` on the same page.

    Args:
        resolved: The loan's
            :class:`~app.services.loan_resolution.ResolvedLoan`.
        as_of: The read pass's as-of.

    Returns:
        ``True`` when the loan is retired and at least one payment is confirmed.
    """
    if not _is_retired(resolved, as_of):
        return False
    return any(p.is_confirmed for p in resolved.context.payments)
