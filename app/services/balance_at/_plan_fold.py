"""Balance-at-T seam -- FOLDING a loan's forward plan into money.

Plan step **R16-a**, which split this half out of :mod:`._plan` when that module
went past pylint's 1000-line ceiling.  The line count going over is a statement
that a module holds more than one subject, and the two here are the seam's own
distinction one level up: :mod:`._plan` BUILDS the forward model -- what a loan
will be charged and what it will pay -- and this folds that model into the three
figures the seam publishes.

* :func:`fold_forward` -- the balance owed on each requested date.
* :func:`plan_payoff_date` -- the date the balance first reaches zero.
* :func:`plan_required_extra` -- what must be added per month to clear it by a
  target date.
* :func:`plan_interest_in_year` -- the interest the plan pays in a tax year.

**All four run ONE walk** (:func:`_split_plan`), so a loan's projected balance,
its derived payoff, its required extra and its projected interest cannot come to
disagree about what a future payment pays.  That walk merges the plan's CHARGES
with its PAYMENTS in contract order rather than charging a month inside each
payment, which is the whole of R16-a: fused, the payment COUNT was the clock, and
30 payments fourteen days apart charged the same interest as 30 a month apart.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE -- W9910 refuses an import of it from
outside :mod:`app.services.balance_at`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_CEILING, Decimal

from app.services.loan_ledger import (
    LoanCashEvent,
    LoanEventStream,
    replay_loan_events,
)

from ._fold import sample_cumulative
from ._plan import LoanForwardPlan

_ZERO_MONEY = Decimal("0.00")

# :func:`plan_required_extra`'s bisection stops once the bracket is this narrow,
# and the answer is the upper end rounded UP to the cent -- so the reported extra
# is at most a cent above the true threshold and never below it.
_EXTRA_SEARCH_TOLERANCE = Decimal("0.001")
_CENTS = Decimal("0.01")
# How many times that search may DOUBLE its upper bound looking for an extra that
# reaches the target before declaring the target unreachable.  Twenty doublings
# is a factor of a million on the loan's own balance; a target that survives it is
# not a rounding matter but a date no payment schedule can reach.
_EXTRA_SEARCH_DOUBLINGS = 20

@dataclass(frozen=True)
class _PlanSplit:
    """One planned payment's fold result: its visible date and the split parts.

    The per-payment output of :func:`_split_plan`, carrying what the two forward
    readers need -- the ``principal`` paydown for :func:`fold_forward` (the balance)
    and the ``interest`` for :func:`plan_interest_in_year` (the tax figure) -- keyed
    by the EFFECTIVE date the payment becomes visible on, plus the ``due_date`` that
    identifies its installment (so the tax reader can drop a slot a settled payment
    already covers).  Sharing ONE split is what keeps a loan's projected balance and
    its projected interest from disagreeing about what a future payment pays.

    Attributes:
        due_date: The contractual installment this payment satisfies -- its
            ``(year, month)`` slot identity, so :func:`plan_interest_in_year` can
            exclude an installment a settled payment already occupies.
        effective_date: When this payment's paydown becomes VISIBLE to a read
            (``max(due, as_of + 1d)``, ruling D1); both readers key their year /
            prefix-sum on it.
        interest: The interest this payment's cash paid, accrued on the running
            balance before it (an Expense leg, ``>= 0``).
        principal: The debt this payment paid down (``cash - interest - escrow``,
            capped at the balance; may be NEGATIVE for an underpayment).
        balance_after: The running balance AFTER this payment
            (:attr:`~app.utils.money.PaymentCashSplit.balance_after`) --
            what :func:`plan_payoff_date` scans for the first ``<= 0`` to find the
            date the loan clears.  ``fold_forward`` does not read it (it prefix-sums
            ``principal``); it is carried so the payoff derivation reuses the ONE
            fold rather than re-walking the plan.
    """

    due_date: date
    effective_date: date
    interest: Decimal
    principal: Decimal
    balance_after: Decimal


def _split_plan(
    seed: Decimal,
    plan: LoanForwardPlan,
    extra_monthly: Decimal = _ZERO_MONEY,
) -> list[_PlanSplit]:
    """Fold *plan* from *seed* in DUE order, returning each payment's split.

    The shared forward fold every reader runs, and since plan step
    **X-au-g-2c-3b-2** it is an ADAPTER rather than a fold: it maps the plan's
    records onto the loan replay's event vocabulary, runs
    :func:`~app.services.loan_ledger.replay_loan_events` -- the ONE rule, shared
    with the settled walk -- and maps each outcome back onto the two figures the
    forward readers need.  :func:`fold_forward` prefix-sums the ``principal`` side
    for the balance; :func:`plan_interest_in_year` sums the ``interest`` side for
    the tax figure, so the loan's projected balance and its projected interest
    come from ONE fold and cannot disagree.

    **It stated that rule itself until X-au-g-2c-3b-2**, and the duplication was
    forced rather than chosen: ``balance_at`` reaches ``loan_ledger`` and not the
    other way about, so the settled walk could not be handed this fold and wrote
    its own.  That is the same layering shape plan steps X-au-g-2c-3a (the
    allocation) and X-au-g-2c-3b-1 (the charge calendar) each found one tier
    down, and the remedy is the same: the rule moves to the tier every walk can
    reach.

    **The plan asserts nothing, so its stream carries no RESET.**  A forward
    projection starts from a seed the caller already resolved; only the settled
    walk replays a loan's anchors.

    **It stopped charging a month INSIDE the per-payment step at plan step
    R16-a**, and that is the step rather than a detail of it.  Charging per
    payment made the payment count the clock: measured on a production clone, 30
    payments of ``$531.94`` fourteen days apart charged the same ``$1,096.34`` as
    30 a month apart, split for split, so a loan paid twice as fast modelled
    identical interest.  Charging per PERIOD makes a second payment inside one
    period clear no fresh charge and pay pure principal.  For the
    one-payment-per-month shape every live loan is in the two are byte-identical,
    which is measured rather than argued
    (``tests/manual/verify_r7d_estimate_equality.py``).

    Args:
        seed: The balance the projection starts from.
        plan: The loan's :func:`._plan.loan_plan` forward model.  Its payments are
            pre-sorted onto ``(due_date, effective_date)`` here, which is the
            within-date order the fold has always used; the replay sorts stably
            on ``(date, kind)`` and adds no tie-break of its own, so that order
            survives (:class:`~app.services.loan_ledger.LoanEventStream`).
        extra_monthly: A HYPOTHETICAL extra added once per ACCRUAL PERIOD, for the
            what-if search (:func:`plan_required_extra`).  ``0.00`` -- the default,
            and what every real read passes -- folds the plan as it stands.  Per
            period rather than per payment since R16-a, which is what its name has
            always claimed: added per RECORD, "an extra $100 a month" was $2,600 a
            year for a definition paying every fortnight.  The loan's STANDING
            ``extra_principal`` is already inside each record's cash (the PLANNED
            tier's live D3 amount, the ESTIMATED tier's synthesis), so this is
            strictly the extra ON TOP of the user's current plan, which is the
            figure the target-date calculator reports.

    Returns:
        One :class:`_PlanSplit` per payment, in DUE order.
    """
    replay = replay_loan_events(
        seed,
        LoanEventStream(
            charges=plan.charges,
            payments=[
                LoanCashEvent(
                    on_date=payment.due_date,
                    cash=payment.cash,
                    source=payment,
                )
                for payment in sorted(
                    plan.payments,
                    key=lambda record: (record.due_date, record.effective_date),
                )
            ],
        ),
        extra_per_period=extra_monthly,
    )
    return [
        _PlanSplit(
            due_date=outcome.event.source.due_date,
            effective_date=outcome.event.source.effective_date,
            interest=outcome.split.interest,
            principal=outcome.split.principal,
            balance_after=outcome.split.balance_after,
        )
        for outcome in replay.payments
    ]


def plan_payoff_date(
    seed: Decimal,
    plan: LoanForwardPlan,
    extra_monthly: Decimal = _ZERO_MONEY,
) -> date | None:
    """Return the DUE date *plan* drives *seed* to zero on, or ``None``.

    The loan's derived payoff date: fold *plan* from *seed* in DUE order
    (:func:`_split_plan`, the SAME fold :func:`fold_forward` runs, so the payoff
    and the balance cannot disagree about when the loan clears) and return the DUE
    date of the FIRST payment whose running balance reaches ``<= 0`` -- the
    installment that pays the loan off.  This is a fold-to-zero, NOT
    ``plan[-1].date``: the plan runs PAST the contractual payoff (the ESTIMATED
    tail's extension, ``_plan._PAYOFF_EXTENSION_MONTHS``), so a loan paying extra
    reaches zero at an EARLIER installment (== the resolver's committed payoff) and
    an underpaying one at a LATER installment in the extension (a real date, where
    the resolver forces the contractual date via ``is_last_month``).

    Two ``None`` cases, kept distinct from a real payoff date so the caller can
    tell "already done" from "never pays off" (both differ from "pays off on date
    D"):

    * **Already retired** (``seed <= 0``): the loan owes nothing at the projection
      seed, so there is no FORWARD crossing to date.  The caller reads
      :attr:`~app.services.balance_at.LoanFigures.is_retired` for the paid-off
      state; this does not invent a future payoff for a loan already at zero (the
      first planned payment would otherwise look like a "payoff").
    * **Never reaches zero within the plan**: negative amortization (a payment
      below the period interest, so the balance grows), or an underpayment so
      severe the balance is still positive after the post-contractual extension.  A
      MILDER underpayment is NOT here -- the extension lets it clear a few months
      past the contractual date, and this returns that later date.  Recurrence
      stays indefinite; the ``None`` the retired case and this share is
      disambiguated by ``is_retired`` (retired here is False).

    The DUE date (contract time), not the EFFECTIVE (visible) date, is returned so
    the payoff month is the installment's own -- matching the resolver's
    ``committed_forward[-1].payment_date`` the payoff has always keyed on, and, for
    a normal future loan, equal to the effective date anyway (they differ only for
    an overdue-but-projected installment, which almost never clears a loan).

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present (an originated loan) or its opening balance (one not yet
            originated), the SAME
            :attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`
            :func:`fold_forward` folds.
        plan: The loan's :func:`._plan.loan_plan` forward model.
        extra_monthly: A HYPOTHETICAL extra added once per ACCRUAL PERIOD, used
            only by
            :func:`plan_required_extra`'s search.  ``0.00`` -- the default, and
            what every real read passes -- dates the plan as it stands.

    Returns:
        The DUE date the balance first reaches ``<= 0``, or ``None`` when the loan
        is already retired (``seed <= 0``) or never pays off.
    """
    if seed <= _ZERO_MONEY:
        return None
    for split in _split_plan(seed, plan, extra_monthly):
        if split.balance_after <= _ZERO_MONEY:
            return split.due_date
    return None


def plan_required_extra(
    seed: Decimal, plan: LoanForwardPlan, target_date: date,
) -> Decimal | None:
    """Return the extra PER MONTH that clears *seed* by *target_date*.

    The target-date calculator's answer, folded from the SAME plan and the SAME
    seed :func:`plan_payoff_date` and :func:`fold_forward` use (plan step C8f).
    It answers "what must I add each month to be done by then" -- per ACCRUAL
    PERIOD since plan step R16-a, which is what the panel has always printed
    (``loan/_payoff_results.html`` renders it ``/mo``) and what a fortnightly
    payer was never given: added per RECORD it was 26 helpings of "a month". Where
    "done" is the date the BALANCE reaches zero -- so the figure and the payoff
    chip beside it rest on one forward model.

    **Why it is not the schedule search it replaced.**  The retired
    ``loan_resolver.target_date_outlook`` binary-searched
    ``amortization_engine.project_forward``, which amortizes one contractual
    installment per month whether or not a payment stands behind it (finding
    B-9).  For a delinquent or drifted loan that walk retires the debt EARLIER
    than the fold does, so it could report "no extra needed" for a target the
    loan does not actually reach -- contradicting the payoff chip on the same
    screen, which folds.  Searching the fold removes the second model rather than
    relabelling its answer.

    Monotone, so a binary search is sound: every added cent is pure principal
    (:func:`~app.utils.money.apply_payment_cash` subtracts the standing interest
    and escrow first), so more extra can only move the zero-crossing earlier or
    leave it where it is.

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present, the SAME
            :attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`
            the balance folds.
        plan: The loan's :func:`._plan.loan_plan` forward model.  Its cash already
            carries the loan's STANDING ``extra_principal``, so the result is the
            amount needed ON TOP of the user's current plan.
        target_date: The date the user wants to be done by.

    **Every comparison here is on the EFFECTIVE date, not the due date.**  The
    payoff DATE this seam reports is the clearing installment's DUE date (contract
    time, matching what the loan card has always shown -- see
    :func:`plan_payoff_date`), but "will I be clear by X" is a question about when
    the money MOVES, and those differ for an overdue-but-still-projected payment:
    ruling D1 clamps its effective date to ``as_of + 1d`` while its due date stays
    in the past.  Comparing due dates let a target in the PAST look reachable --
    the fold "cleared" the loan on a past due date, so the search happily returned
    a six-figure extra for a date the user cannot pay on any more.

    Returns:
        ``Decimal("0.00")`` when the plan ALREADY clears the loan by
        *target_date* (including a loan that owes nothing), the searched
        per-month extra when one exists, or ``None`` for a target no extra
        reaches.  That last has two causes: no planned payment has even HAPPENED
        by then (a target in the past, or before the next installment lands), or
        -- the termination backstop below -- the search exhausted its doublings,
        which past the first guard means the split arithmetic stopped responding
        to more principal rather than that the date is genuinely out of reach.
    """
    if seed <= _ZERO_MONEY:
        return _ZERO_MONEY

    def _clears_by(extra: Decimal) -> bool:
        """Whether *extra* a month puts the balance at zero by the target.

        Keyed on the clearing payment's EFFECTIVE date -- when its cash actually
        moves -- so a past due date can never stand in for a payment that has not
        happened (see the note above).
        """
        for split in _split_plan(seed, plan, extra):
            if split.balance_after <= _ZERO_MONEY:
                return split.effective_date <= target_date
        return False

    if _clears_by(_ZERO_MONEY):
        return _ZERO_MONEY
    if not any(
        payment.effective_date <= target_date for payment in plan.payments
    ):
        # No planned payment has even happened by then, so no extra lands in
        # time: the target is in the past, or before the next installment.
        return None

    # An UPPER BOUND has to be found, not assumed.  The seed looks like one --
    # pay the whole balance as extra and the first installment clears it -- but
    # it is not: the allocation (:func:`~app.utils.money.apply_payment_cash`)
    # takes the standing interest and escrow out of the cash FIRST, so on a loan
    # whose period interest exceeds its payment cash even
    # ``seed`` leaves a residue.  Double until the bound genuinely reaches the
    # target, so the bisection below starts from an invariant that HOLDS rather
    # than one that looked obvious.
    #
    # The cap is a termination backstop, not an expected outcome: past the
    # no-payment guard above, some extra always clears the loan at the first
    # payment that lands by the target (the extra is unbounded principal), so the
    # ``else`` is not the "unreachable target" case -- that one already returned.
    # The cap exists so a future change to the split can never turn this into a
    # hang.
    low, high = _ZERO_MONEY, seed
    for _ in range(_EXTRA_SEARCH_DOUBLINGS):
        if _clears_by(high):
            break
        low, high = high, high * 2
    else:
        return None

    # Now the invariant the bisection needs holds: ``low`` misses, ``high``
    # reaches.  Narrow to well under a cent.
    while high - low > _EXTRA_SEARCH_TOLERANCE:
        mid = (low + high) / 2
        if _clears_by(mid):
            high = mid
        else:
            low = mid

    # Round UP to the cent, never to nearest.  The threshold sits between ``low``
    # and ``high``, so rounding to NEAREST can land below it -- and a fraction of
    # a cent short at the payoff boundary leaves a positive balance, which pushes
    # the payoff a whole INSTALLMENT past the target.  Measured on a randomized
    # sweep: 99 of 300 generated loans returned an extra that missed its target
    # by one month under half-up rounding.
    #
    # Both ends are tried so the answer is the EXACTLY minimal cent, not merely a
    # cent that works: when the true threshold falls on a whole cent, ``high`` is
    # a hair above it and its ceiling would overcharge the user by a cent.
    # ``ceil(low)`` is either that minimal cent or one below it, so testing it
    # first (one more fold) decides which.
    candidate = low.quantize(_CENTS, rounding=ROUND_CEILING)
    if _clears_by(candidate):
        return candidate
    return high.quantize(_CENTS, rounding=ROUND_CEILING)


def _paydown_steps(
    seed: Decimal, plan: LoanForwardPlan,
) -> list[tuple[date, Decimal]]:
    """Return each planned payment's paydown as a NEGATIVE change on its visible date.

    The balance reader's view of :func:`_split_plan`: each split's ``principal``
    paydown, negated and keyed by its EFFECTIVE (visible) date -- the steps
    :func:`_sample_from_steps` prefix-sums.

    Args:
        seed: The balance the projection starts from.
        plan: The loan's :func:`._plan.loan_plan` forward model.

    Returns:
        ``[(effective_date, balance_change), ...]`` in due order (balance_change is
        ``-principal``).
    """
    return [
        (split.effective_date, -split.principal)
        for split in _split_plan(seed, plan)
    ]


def _sample_from_steps(
    seed: Decimal,
    owed_from: date,
    steps: list[tuple[date, Decimal]],
    dates: list[date],
) -> dict[date, Decimal]:
    """Prefix-sum the paydown *steps* from *seed* and read each date off it.

    Re-keys the (contract-order) steps by their visible date, prefix-sums from the
    seed, and bisects for each requested date -- the same visible-order read
    :func:`app.services.balance_at._fold.fold_from_walk` makes for the ACTUAL past.  A
    date before *owed_from* owes ``0.00`` (the loan does not exist yet).

    Args:
        seed: The balance before any paydown.
        owed_from: The loan's ``origination_date``; a date before it owes ``0.00``.
        steps: The ``(effective_date, balance_change)`` steps from
            :func:`_paydown_steps`.
        dates: The dates to value, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per date.
    """
    # The prefix-sum and per-date read is the shared fold-sampling core; only the
    # origination gate (a date before the loan exists owes 0.00) is projection-specific.
    sampled = sample_cumulative(
        seed, sorted(steps, key=lambda step: step[0]), dates,
    )
    return {
        on_date: (_ZERO_MONEY if on_date < owed_from else balance)
        for on_date, balance in sampled.items()
    }


def fold_forward(
    seed: Decimal,
    owed_from: date,
    plan: LoanForwardPlan,
    dates: list[date],
) -> dict[date, Decimal]:
    """Fold the confirmed-present *seed* forward over *plan* to a balance per date.

    The projection half of :func:`app.services.balance_at.positions`, expressed as
    a fold rather than a schedule-row walk.  Splits each planned payment on the
    running balance in DUE (contract) order (:func:`_paydown_steps`), then re-keys
    each paydown by its EFFECTIVE (visible) date and prefix-sums
    (:func:`_sample_from_steps`) -- the SAME contract-order-split / visible-order-read
    shape :func:`app.services.balance_at._fold.fold_from_walk` uses for the ACTUAL past,
    so the past and the future fold consistently.  A date before ``owed_from`` (the
    loan's origination) owes ``0.00``.

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present for an originated loan, or the balance it will OPEN at for one
            not yet originated (the caller supplies the right one, as
            :attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`
            does today).
        owed_from: The loan's ``origination_date``; a date before it owes
            ``0.00``.
        plan: The loan's :func:`._plan.loan_plan` forward model.
        dates: The dates to value the loan at, in any order.  Duplicates collapse.

    Returns:
        ``{date: balance owed}`` -- one cent-quantized ``Decimal`` per requested
        date.  ``{}`` for an empty *dates*.
    """
    return _sample_from_steps(
        seed, owed_from, _paydown_steps(seed, plan), dates,
    )


def plan_interest_in_year(
    seed: Decimal,
    plan: LoanForwardPlan,
    year: int,
    exclude_slots: frozenset[tuple[int, int]] = frozenset(),
) -> Decimal:
    """Return the interest *plan*'s payments are projected to pay in *year*.

    The projected (future) half of the Schedule-A mortgage-interest figure
    (:func:`app.services.balance_at.loan_interest_in_year`), folded from the SAME
    forward payment records the loan's projected BALANCE folds
    (:func:`fold_forward` over :func:`_split_plan`) -- so the tax figure's future
    and the balance's future come from ONE model (step C6c; B-6 unified the settled
    PAST, this the future).  It sums each payment's accrued interest attributed to
    the year the payment is projected to be PAID: its EFFECTIVE date
    (``max(due, as_of + 1d)``, ruling D1), the visible / expected-paid date, so an
    overdue-but-still-projected payment's interest deducts in the year it is
    expected to clear rather than the closed year it was contractually due.

    An overdue installment with NO payment record contributes nothing: it is absent
    from *plan* entirely (:func:`._plan.loan_plan`'s ESTIMATED tier never synthesizes a
    strictly-past installment -- finding B-9), so a delinquent loan's unpaid past
    does not inflate its deduction.

    *exclude_slots* is the settled-slot MERGE: the caller passes the ``(year,
    month)`` installments its SETTLED half already counts, and this drops any plan
    record on one of them, so an installment counted as settled is not ALSO counted
    here.  The plan's own ESTIMATED tier de-dups the settled payments VISIBLE by
    ``as_of`` (``confirmed_shadows_through``, a UTC-visibility cut), but the caller's
    settled half sums the fold's WALK on a DISPLAY clock; the two cuts differ for a
    payment settled in the evening of a UTC-behind zone, so the caller closes the
    gap by handing this the WALK's slots.  See
    :func:`app.services.balance_at._loan_interest.loan_interest_in_year`.

    Args:
        seed: The balance the projection starts from -- the loan's confirmed
            present (:attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`),
            the SAME seed :func:`positions` folds, so the interest accrues on the
            balance the loan actually projects.
        plan: The loan's :func:`._plan.loan_plan` forward model.
        year: The calendar / tax year to sum projected interest within.
        exclude_slots: The ``(year, month)`` installment slots a settled payment
            already covers, dropped from the sum (default: none).  A record is still
            FOLDED (its paydown feeds later balances), only its interest is skipped.

    Returns:
        The interest projected to be paid in *year* as a cent-quantized
        ``Decimal`` (``0.00`` when no planned payment is visible in the year).
    """
    return sum(
        (
            split.interest
            for split in _split_plan(seed, plan)
            if split.effective_date.year == year
            and (split.due_date.year, split.due_date.month) not in exclude_slots
        ),
        _ZERO_MONEY,
    )
