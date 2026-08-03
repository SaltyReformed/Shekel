"""Balance-at-T seam -- a loan's interest / principal PAID in a calendar year.

Plan steps **C3c** and **C6c** (``docs/audits/balance_architecture/README.md``).
The seam's paid-in-year figures, all folded from the loan's SOURCE events (the
running-balance walk :func:`app.services.loan_ledger.walk_loan_ledger`, sampled
through the read pass's memoized
:meth:`~app.services.balance_at.BalanceContext.loan_walk`) so a figure and
the balance it describes come from the ONE total producer and cannot disagree:

* :func:`loan_interest_paid_in_year` / :func:`loan_principal_paid_in_year` (step
  **C6c**) -- the loan-detail "Interest paid, YTD" / "Principal paid, YTD" chips:
  the interest / principal a loan's SETTLED payments actually paid in a year, and
  nothing projected.  They are the fold's own splits, so they replace the posting
  readers the chips read before (``confirmed_loan_interest_in_year`` /
  ``confirmed_loan_principal_in_year``, deleted at C6c): the postings are a
  projection of this same fold (B2 / plan E1), so the figure is unmoved, and a loan
  whose posting cache is cold now folds a real figure where the reader returned
  ``None`` and hid the chip.
* :func:`loan_interest_in_year` (steps **C3c** / **C6c**) -- the tax year's WHOLE
  mortgage-interest figure (Schedule A): the SETTLED interest above PLUS the
  interest still PROJECTED to be paid in the year, folded from the loan's forward
  payment PLAN (:func:`app.services.balance_at._plan.plan_interest_in_year`, step
  **C6c**) -- the SAME plan the loan's projected balance folds, so the deduction and
  the balance agree on the FUTURE as C3c made them agree on the PAST.  It replaced
  the ledger-reader-plus-schedule HYBRID that lived in ``tax_report_service``,
  closing B-6 (the Taxes tab no longer prints interest for a loan the seam values a
  different way).

**Two clocks, deliberately.**  The interest figure is a TAX figure, so it counts a
payment in the year the user PAID it on their WALL CLOCK
(:func:`app.utils.balance_predicates.settled_day`, the L9 rule) -- which diverges from
the balance ledger's UTC ``entry_date`` clock across the New Year (a settle at 8:05
PM EST Dec 31 is stored 01:05 UTC Jan 1, deductible in the OLD year).  This is why
interest-in-year is NOT ``positions().cum_interest`` keyed on the fold's UTC
visible date, and why it lives in its own function rather than on the balance
producer: the balance is a storage-clock quantity, the deduction a wall-clock one.

**One record per installment (the settled-slot merge).**  The settled half (fold)
and the projected half (plan) must not both count the same installment.  Step
**C6c** moved the projected half from the resolver's schedule rows onto the forward
PLAN (:func:`app.services.balance_at._plan.loan_plan`), but the merge STAYS -- and
it must de-duplicate against the SAME set the settled half sums.  The settled half
counts every payment in the fold's WALK (``walk.payment_splits`` -- clock-blind, it
splits every settled payment) attributed by its DISPLAY paid year (the L9 wall
clock).  So :func:`loan_interest_in_year` excludes from the projected sum every
installment slot a WALK payment occupies (:func:`_due_slot`), and hands that set to
:func:`~app.services.balance_at._plan.plan_interest_in_year`.

**Why the WALK, not the plan's own de-dup.**  ``loan_plan``'s ESTIMATED tier
already skips a slot covered by ``confirmed_shadows_through(as_of)``, and
de-duplicating against the WALK instead -- every settled payment, the settled
half's OWN set -- is what makes the two halves partition by construction rather
than by two bounds that happen to agree.

**The zone argument this paragraph used to make is FALSIFIED, and is recorded
here rather than deleted** (finding **N-180**).  It read: ``confirmed_shadows_through``
is a UTC-visibility subset while the tax ``as_of`` is a DISPLAY date, so an
evening settle whose instant rolled into the next UTC day is counted by the
settled half yet not excluded from the plan, and the installment is synthesized
twice.  That stopped being true at ruling **R-DH (b)**, which moved
:func:`app.services.loan_ledger.payment_visible_on` to the display timezone, and
it is doubly untrue since plan step X-f1 (ruling R-EC): the day is a STORED civil
day in the user's zone, converted by nothing.  A draft of this paragraph was
edited during that conversion to cite ``to_utc_civil_date(settled_on)`` -- a
function that has never existed in ``app/`` -- which is the invented-citation
class this arc keeps paying for, caught by a neutral review.  **Whether the two
sets can still differ for any other reason is UNVERIFIED**, so the de-dup stays
and N-180 owns the question.  The code was never wrong; only the reason written
beside it was.  De-duplicating against the WALK closes that
one-evening double-count; the plan's ``confirmed_shadows_through`` de-dup stays for
the BALANCE, whose seed excludes the same payments the plan re-adds so it nets.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections.abc import Callable
from decimal import Decimal

from app.models.account import Account
from app.services.loan_ledger import LoanLedgerWalk, LoanPaymentSplit
from app.services.loan_loaders import loan_payment_due_date
from app.utils.balance_predicates import settled_day

from ._context import BalanceContext
from . import _kernel
from ._inputs import _require_scenario
from ._plan import memoized_plan, plan_interest_in_year
from ._resolution import resolved_loan

_ZERO_MONEY = Decimal("0.00")


def loan_interest_paid_in_year(
    account: Account, ctx: BalanceContext, year: int,
) -> Decimal:
    """Return the interest *account*'s SETTLED payments actually paid in *year*.

    The loan-detail "Interest paid, YTD" chip (step **C6c**): the interest side of
    each settled payment's real split (:func:`app.services.loan_ledger.walk_loan_ledger`),
    attributed to the DISPLAY-timezone civil YEAR of its paid date
    (:func:`_paid_year`, the L9 tax basis) -- the interest actually PAID, and
    nothing projected.  This is the SETTLED half of :func:`loan_interest_in_year`
    on its own; the two share :func:`_settled_sum_in_year`, so the chip and the
    Schedule-A figure describe one set of payments.

    Folded from the loan's SOURCE events (the read pass's memoized walk), it
    replaces the posting reader ``confirmed_loan_interest_in_year`` the chip read
    before: the postings are a projection of this fold (B2 / plan E1), so the
    figure is unmoved, and a loan whose posting cache is cold folds a real figure
    where the reader returned ``None``.

    **TOTAL, never ``None``.**  A configured loan always walks (its origination
    anchor is synthesized), so this always returns a real ``Decimal`` -- ``0.00``
    for a loan that has paid no interest in *year*, or for a non-configured account
    (an empty walk).  The loan-detail page renders only for a configured loan, so
    the chip always shows the real figure.

    Args:
        account: The loan account whose paid interest to sum (the caller owns the
            ownership check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the walk, memoized so the balance hero and both
            YTD chips fold the loan once.
        year: The calendar year to sum interest paid within (the DISPLAY-tz civil
            year the chip is keyed to).

    Returns:
        The interest paid during *year* as a cent-quantized ``Decimal``.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
    """
    _require_scenario(ctx)
    return _settled_sum_in_year(
        ctx.loan_walk(account), year, lambda split: split.interest,
    )


def loan_principal_paid_in_year(
    account: Account, ctx: BalanceContext, year: int,
) -> Decimal:
    """Return the principal *account*'s SETTLED payments actually paid in *year*.

    The loan-detail "Principal paid, YTD" chip (step **C6c**), the paid-date
    sibling of :func:`loan_interest_paid_in_year`: the principal side of each
    settled payment's real split (:func:`app.services.loan_ledger.walk_loan_ledger`
    -- extra principal included, a payoff overpayment's refund excluded, so an
    extra or short payment counts honestly), attributed on the SAME display-tz paid
    year (:func:`_paid_year`).  Sharing :func:`_settled_sum_in_year` with the
    interest chip is what keeps the two chips describing one set of payments.

    Folded from the loan's SOURCE events, it replaces the posting reader
    ``confirmed_loan_principal_in_year`` the chip read before (unmoved by B2 / plan
    E1).  TOTAL, never ``None`` -- see :func:`loan_interest_paid_in_year`.

    Args:
        account: The loan account whose paid principal to sum (the caller owns the
            ownership check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            (its scenario scopes the memoized walk).
        year: The calendar year to sum principal paid within (the DISPLAY-tz civil
            year).

    Returns:
        The principal paid during *year* as a cent-quantized ``Decimal``.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
    """
    _require_scenario(ctx)
    return _settled_sum_in_year(
        ctx.loan_walk(account), year, lambda split: split.principal,
    )


def _settled_sum_in_year(
    walk: LoanLedgerWalk,
    year: int,
    part: Callable[[LoanPaymentSplit], Decimal],
) -> Decimal:
    """Sum a settled-payment split PART attributed to the display-tz paid *year*.

    The shared paid-in-year core of :func:`loan_interest_paid_in_year`,
    :func:`loan_principal_paid_in_year`, and the SETTLED half of
    :func:`loan_interest_in_year`: it sums *part* (``split.interest`` or
    ``split.principal``) over the walk's settled payment splits whose payment was
    PAID in *year* on the display clock (:func:`_paid_year`).  One derivation, so
    the interest chip, the principal chip, and the Schedule-A figure can never
    disagree on WHICH payments a year contains.

    Args:
        walk: The loan's :class:`~app.services.loan_ledger.LoanLedgerWalk`.
        year: The DISPLAY-tz civil year to sum within.
        part: The split field to sum -- ``lambda split: split.interest`` or
            ``lambda split: split.principal``.

    Returns:
        The cent-quantized sum of *part* over the payments paid in *year*
        (``0.00`` when none).
    """
    return sum(
        (
            part(split)
            for split in walk.payment_splits
            if _paid_year(split.income_shadow) == year
        ),
        _ZERO_MONEY,
    )


def loan_interest_in_year(
    account: Account, ctx: BalanceContext, year: int,
) -> Decimal:
    """Return *account*'s mortgage interest PAID during *year* -- fold + plan.

    The Schedule A / debt-interest figure for one loan and one tax year, from the
    same total producer the balance derives from (see the module docstring):

    * **SETTLED (past) interest -- the FOLD.**  Each settled payment's ACTUAL
      accrued interest (:attr:`~app.services.loan_ledger.LoanPaymentSplit.interest`,
      the interest the payment's real cash paid on the reset-aware running balance --
      correct even for an off-schedule extra / short payment, where the schedule's
      replayed figure is not), attributed to the DISPLAY-timezone civil YEAR of its
      paid date (:func:`app.utils.balance_predicates.settled_day`, the L9 tax basis).
      This reads the loan's SOURCE events, not the posting cache, so a loan the
      posting reader cannot value (no genesis opening posting) is still valued from
      its facts -- closing B-6 -- rather than falling back to the schedule.
    * **PROJECTED (future) interest -- the PLAN.**  Each of the loan's forward
      payment records (:func:`app.services.balance_at._plan.plan_interest_in_year`
      over :meth:`~app.services.balance_at.BalanceContext.loan_plan`), folded
      from the SAME ``projection_seed`` the loan's projected BALANCE folds and
      attributed to the year the payment is projected to be PAID (its EFFECTIVE
      date).  An overdue installment with NO record is absent from the plan (finding
      B-9), so a delinquent loan's unpaid past no longer inflates the deduction, and
      a projected payment folds its LIVE cash -- so the interest the tax figure
      projects and the balance the loan projects come from ONE forward model (step
      **C6c**).  It EXCLUDES every installment slot a settled payment already
      satisfies (``exclude_slots`` = the WALK's due slots), so no installment is
      counted in both halves (see the module docstring's two-clock note).

    **Loan-only, and total.**  A non-configured account (no
    :class:`~app.models.loan_params.LoanParams`) has no fold and no plan, so it
    contributes ``0.00`` -- matching the pre-C3c hybrid, where such an account was
    simply absent from the debt-schedule dict.  (The caller
    :func:`app.services.tax_report_service._build_schedule_a` selects only MORTGAGE
    accounts, but the figure is well defined for any loan.)

    Args:
        account: The loan account whose paid interest to sum (the caller owns the
            ownership check and the mortgage-kind selection).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the fold and the plan, and its memoized resolution
            supplies the ``projection_seed`` (the same seed :func:`positions` folds).
        year: The calendar / tax year to sum interest paid within.

    Returns:
        The loan's interest paid during *year* as a ``Decimal`` (``0.00`` for a
        non-configured account, or a configured loan that paid no interest in the
        year and has no projected payment landing in it).

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
    """
    _require_scenario(ctx)
    debt_schedule = _kernel.generate_debt_schedules(
        [account], ctx,
    ).get(account.id)
    if debt_schedule is None:
        # Not a configured loan (no LoanParams): it has neither a fold nor a plan,
        # so it contributes 0.00 -- matching the pre-C3c hybrid, where such an
        # account was simply absent from the debt-schedule dict.
        return _ZERO_MONEY

    walk = ctx.loan_walk(account)
    payment_day = resolved_loan(account, ctx).params.payment_day
    settled_interest = _settled_sum_in_year(
        walk, year, lambda split: split.interest,
    )
    # The installments a settled payment already satisfies -- excluded from the
    # projected half so no installment counts in both.  The set is the WALK's own
    # (every settled payment, the settled half's set), NOT the plan's
    # ``confirmed_shadows_through`` cut: see the module docstring's two-clock note.
    settled_slots = frozenset(
        _due_slot(split.income_shadow, payment_day)
        for split in walk.payment_splits
    )
    projected_interest = plan_interest_in_year(
        debt_schedule.projection_seed, memoized_plan(account, ctx), year,
        exclude_slots=settled_slots,
    )
    return settled_interest + projected_interest


def _paid_year(shadow) -> int:
    """Return the DISPLAY-timezone civil YEAR a settled payment was paid in.

    The tax attribution rule (L9): mortgage interest deducts in the year the user
    PAID it on their wall clock, so a payment's interest belongs to the civil year
    of the day its money moved -- the shadow's STORED ``settled_on``, read through
    the shared :func:`app.utils.balance_predicates.settled_day`.  This is the SAME
    attribution the posting ledger stamps each interest / principal leg's
    ``entry_date`` with, so the fold-based figure and the posted legs it projects
    agree on WHICH year a payment lands in -- they differ only in reading the
    fold's split rather than the posted net.

    **It derived the year from ``paid_at``'s display-timezone day until plan step
    X-f1** (ruling R-EC).  The stored day IS the user's civil day, so the wall-clock
    rule L9 states is now read rather than re-derived.

    Args:
        shadow: The settled loan-side income shadow (its ``pay_period`` is
            eager-loaded by :func:`~app.services.loan_loaders.settled_income_shadows`).

    Returns:
        The calendar year the payment was paid in, on the display-tz clock.
    """
    return settled_day(shadow.id, shadow.settled_on).year


def _due_slot(shadow, payment_day: int) -> tuple[int, int]:
    """Return the ``(year, month)`` installment slot a settled payment satisfies.

    The merge key that keeps a settled payment (in the fold's walk) and the plan's
    ESTIMATED synthesis of the same installment from both counting: the payment's
    contractual due (year, month), via the project's single due-date derivation
    (:func:`app.services.loan_loaders.loan_payment_due_date`).  Keyed by month rather
    than exact date so the exclusion still matches a plan record whose due date the
    resolver's biweekly-collision redistribution nudged within the month (the settled
    payment's OWN due month never shifts, only display rows do).

    Args:
        shadow: The settled loan-side income shadow.
        payment_day: The loan's contractual day-of-month due day (used only by the
            due-date fallback for a shadow with no stored ``due_date``).

    Returns:
        The ``(year, month)`` of the installment this payment satisfies.
    """
    due = loan_payment_due_date(shadow, payment_day)
    return (due.year, due.month)
