"""
Shekel Budget App -- Savings cockpit: the loan debt-line derivations.

THE one place this package answers two questions about the user's loans:
WHICH of them still have a debt line ahead of them, and WHEN the last of
those lines ends.  Both are asked by two surfaces that render side by side
on ``/savings``, and both used to be answered twice:

* the cockpit's ``Debt-free <month>`` caption and the dashboard debt track
  read :func:`~.._metrics._compute_debt_summary`, which selected loans by
  their current BALANCE;
* the Horizon chart's ``Debt-free`` flag and its x-axis read
  :func:`~.._horizon._resolve_horizon_domain`, which selected them by the
  debt-line predicate.

They agree on every loan the developer holds today and part on one that has
NOT been borrowed yet: it owes ``$0.00``, so the balance rule dropped a
mortgage whose whole 30-year line is ahead of it, and the caption then
reported the date the OTHER loans finish.  Measured at **19 years** apart on
the developer's own mortgage rewritten into that state, and **28** on an
independent two-loan fixture (finding N-98, plan step X-q).  Aligning the two
rules would have been two producers kept in step by a rule a reader has to
remember; this module is the other answer -- one derivation, both surfaces
read it, and there is nothing left to keep in step.

**The money aggregates are NOT here, and that is deliberate.**  ``total_debt``,
``total_monthly_payments`` and the weighted-average rate answer "what do you
owe TODAY", where a loan that has not closed contributes nothing and its
payment is not yet being made.  Two questions, two membership rules, one place
each; what this module refuses is ONE question answered twice.

**Scope: amortizing loans only, and the surfaces must say so** (developer
ruling, finding N-99).  A revolving Credit Card has no forward model -- the
seam holds it FLAT at its current owed magnitude, so it never reaches zero and
can have no payoff date -- while the Horizon's liability band still sums it
into the chart.  Including it under today's model would mean nobody carrying a
card balance ever gets a date; the ruling is to keep the derivation over the
debts that HAVE a payoff model and to caption the result as what it measures.
**Those captions SHIPPED at plan step X-q3** (`bad97e6a`, closing finding
N-99): the cockpit footer reads "Loans paid off <mon>" and names the revolving
balance it excludes, the dashboard debt track reads "loans paid off <mon>", and
the Horizon's flag reads "All loans paid off".  (A milestone carried a machine
``kind`` beside that label until plan step X-s1 deleted it at both ends for
having no consumer; the label is the flag's only identity now, and the ruling on
what that costs is at ``_horizon._DEBT_FREE_MILESTONE_LABEL``.)
:func:`debt_without_payoff_model` below is what the footer's caveat sums.
A card that can carry a real payoff date is the credit-card arc's work
(``docs/plans/implementation_plan_credit_card.md``), and this module is where
it would be admitted.

No Flask imports: plain data in, plain data out.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.savings_dashboard_service._types import AccountProjection

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class LoanPayoffOutlook:
    """When the user's last LOAN line ends -- three states, told apart.

    The three are genuinely different answers -- "the last loan clears on D",
    "there are no loans left to clear", and "a loan is on a payment that never
    clears it, so there is no date at all" -- which is why this is one value
    rather than a nullable date.  A bare ``None`` collapses the last two, and
    a consumer that cannot tell them apart either captions a borrower as
    debt-free or stays silent where it owes them a warning: the cockpit renders
    all three (`_cockpit.html:283-296` -- a date, a warning, or "All loans paid
    off"; re-pinned at plan step X-t5 after X-t1 and X-t3 shifted the block,
    which is the THIRD re-pin in three steps -- so the durable anchor is the
    three ``payoff_outlook`` predicates in that footer, not the numbers) and the
    dashboard debt track renders only the date, which is a
    display decision that this value at least makes visible rather than
    structural.

    **The $900,000 incident is a DIFFERENT defect and is not this one** -- it
    was skip-and-max, and :func:`loan_payoff_outlook` is where it is refused.

    Attributes:
        all_clear_on: The DUE date the user's last debt-line loan folds to
            zero -- the latest :attr:`~app.services.balance_at.LoanFigures.payoff_date`
            across :func:`debt_line_loans`.  ``None`` when there are no such
            loans, and ``None`` when at least one of them never clears (read
            :attr:`never_clears` to tell those apart).

            **It can be in the PAST** (developer ruling, plan step X-q): the
            payoff is the DUE date the balance first reaches zero, and an
            overdue-but-still-projected installment that clears the loan folds
            at a due date behind today.  That is a fact about the loan's plan
            and this value reports it; a consumer that cannot RENDER a past
            date (the Horizon cannot draw a flag left of its own origin) owns
            that constraint at its own boundary rather than by getting a
            different answer here.
        never_clears: Whether at least one debt-line loan has NO payoff at its
            current payment.  Such a loan POISONS the date rather than being
            skipped: taking the latest payoff over the loans that DO clear
            reports the date the others finish, on a borrower who still owes.
            The caller must SAY this state ("No debt-free date at current
            payments") rather than simply omitting the date, because it is a
            different fact from having no loans.
    """

    all_clear_on: date | None
    never_clears: bool

    @property
    def is_loan_free(self) -> bool:
        """Whether the user has no loan debt line at all.

        The third state, derived rather than stored so it cannot contradict
        the other two: no date AND nothing unclearing means there was nothing
        to date.  A borrower whose loan never pays off is NOT loan-free, and a
        caller must not caption them as such.

        **Neither is a borrower whose only payoff is already behind them**, and
        that is the answer this replaced: the Horizon's domain resolver used to
        drop past payoffs loan by loan and then read the empty list as "no
        loans", so an overdue borrower was reported loan-free (plan step X-q1,
        developer ruling R-AY).  It cannot be re-expressed here, because this
        takes no reader ``today`` to filter on -- a past payoff is a date like
        any other, and only the CHART, which cannot draw a flag left of its own
        origin, has a reason to care where it falls.  Plan step X-q2 deleted
        the resolver's republished copy of this value, so there is no second
        place for the question to be answered from a clock again.

        Returns:
            ``True`` only when :func:`debt_line_loans` was empty.
        """
        return self.all_clear_on is None and not self.never_clears


def debt_line_loans(
    account_data: list[AccountProjection],
) -> list[AccountProjection]:
    """Return the AMORTIZING loan projections that still have a DEBT LINE.

    THE one "which loans still have a line ahead of them" selection IN THIS
    PACKAGE.  (The seam answers the same question over its own shape for the
    property equity chart -- ``property_equity_chart.py:409`` filters
    ``SecuredLoanSeries`` on the same ``is_retired`` -- so this is one of two
    call sites of one PREDICATE, not a second definition of it.)  Scoped to
    accounts carrying a :class:`~.._types.LoanDetail` -- which is exactly the
    configured-loan set, since ``_project_one_account`` fills that field for a
    loan and for nothing else -- see the module docstring for why a revolving
    liability is not here and what would admit it.

    The predicate is the seam's
    :attr:`~app.services.balance_at.LoanFigures.is_retired` -- the loan has
    ORIGINATED and the fold of its recorded events owes nothing -- and
    deliberately NOT ``is_paid_off``, which is that plus a confirmed-payment
    guard.  That guard is a BADGING rule, and the seam says so in terms: "Use
    ``is_retired`` to decide whether a loan has a debt line; use this to decide
    whether to CONGRATULATE the user."

    Asking the debt-line question with the congratulation predicate is finding
    B-16, and the shape it fires on is the one the app's own true-up UI
    produces: a loan paid off by a LUMP SUM recorded as a balance true-up has
    no payment rows, so it reads ``is_paid_off=False`` while owing ``$0.00``.
    It stayed in the ACTIVE set, and -- being retired, so having no forward
    crossing left to date -- fired the "never clears" branch: no date, every
    STRUCTURAL flag gone from the Horizon (the net-worth crossing flags are
    built from the trajectory and survived), and the axis cut back to the
    loan-free fallback window while the caption on the same page still read
    the real date.  Measured on the developer's own two loans: the axis ended
    **2036-12-31** where the debt line ends **2049-12-31**, and the presence of
    a payment ROW -- a badging detail -- was what decided between them.  The
    same collapse drew ``$197,049.32`` of phantom debt on the property equity
    chart, which is the incident the seam's contract was written by.

    A loan that has NOT been borrowed yet is INCLUDED: it owes ``$0.00`` today
    and its whole debt line is ahead of it, which is precisely what
    ``is_retired``'s origination half separates from a debt that is gone.

    Args:
        account_data: The per-account projections.

    Returns:
        The loan projections (those carrying a ``loan`` detail) that are not
        retired, in *account_data* order.
    """
    return [
        ad for ad in account_data
        if ad.loan is not None and not ad.loan.figures.is_retired
    ]


def loan_payoff_outlook(
    account_data: list[AccountProjection],
) -> LoanPayoffOutlook:
    """Return when the user's last debt-line loan clears.

    THE one derivation of the debt-free date, read by the cockpit caption, the
    dashboard debt track and the Horizon chart's flag and axis (plan step
    X-q).  It folds :func:`debt_line_loans` into the three-state
    :class:`LoanPayoffOutlook`: the latest payoff across them, unless one of
    them has none -- in which case there IS no date and the caller says so.

    An absent payoff POISONS the answer rather than being skipped.  This is
    the rule both producers already carried separately and it is load-bearing:
    dropping the loan and taking the latest of the rest reports the date the
    OTHER loans finish, so a borrower owing $900,000 on a loan the same page
    labels "No payoff at current payment" was told they go debt-free when
    their car loan ends.

    Args:
        account_data: The per-account projections (a configured loan carries a
            :class:`~.._types.LoanDetail`).

    Returns:
        The :class:`LoanPayoffOutlook` for this user.
    """
    payoffs: list[date] = []
    for loan_ad in debt_line_loans(account_data):
        payoff = loan_ad.loan.figures.payoff_date
        if payoff is None:
            return LoanPayoffOutlook(all_clear_on=None, never_clears=True)
        payoffs.append(payoff)
    return LoanPayoffOutlook(
        all_clear_on=max(payoffs) if payoffs else None, never_clears=False,
    )


def debt_without_payoff_model(
    account_data: list[AccountProjection],
) -> Decimal:
    """Return the owed magnitude of the debt no payoff date can cover.

    The figure that makes the caption HONEST rather than merely narrow
    (developer ruling on finding N-99, plan step X-q3).  Every liability that
    is not an amortizing loan has no forward model -- the seam holds it FLAT
    at its current owed magnitude, so it never reaches zero -- and is
    therefore invisible to :func:`loan_payoff_outlook`.  A user carrying a
    revolving card balance would otherwise read "Loans paid off Jun 2056" on a
    page whose own liability band never touches zero, with nothing saying why.

    The magnitude is ``abs()``, matching the net-worth reducer's convention
    (:func:`~.._net_worth.compute_net_worth_today` accumulates ``abs(balance)``
    into its liability total): a Credit Card is anchored owed-as-NEGATIVE, and
    this reports what is owed, not a signed balance.  (The name this sentence
    carried until plan step X-t5 -- ``_net_worth._sum_net_worth_totals`` -- has
    never existed in this tree, in any commit: an invented citation, written
    into the record by the same step that renamed the caption above it.)

    Args:
        account_data: The per-account projections (each answering
            ``is_liability`` and carrying a ``current_balance``).

    Returns:
        The total owed on liabilities with no payoff model, ``0.00`` when
        there are none.
    """
    return sum(
        (
            abs(ad.current_balance or ZERO) for ad in account_data
            if ad.is_liability and ad.loan is None
        ),
        ZERO,
    )
