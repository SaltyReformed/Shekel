"""Balance-at-T seam -- a loan's balance at many dates: fold past, projection future.

Plan step **C3a/C3b** (``docs/audits/balance_architecture/README.md``).
:func:`positions` is the ONE total loan balance-at-T producer the seam's AMORTIZING
dispatch reads: the event FOLD
(:func:`app.services.balance_at._fold.fold_from_walk` over the read pass's memoized
walk) for a date at or before the resolver's NOW, and the forward schedule
projection after.  The seam's SCALAR
(:func:`app.services.balance_at.balance_at`) and its LIABILITY band
(:func:`app.services.balance_at.liability_owed_at_dates`) read it as of step C3b1;
the per-period map (:func:`app.services.balance_at._inputs._account_balance_map`'s
CONFIGURED-LOAN branch) reads :func:`positions_period_map` as of step C3b3, so every
loan balance surface -- point, band, and map -- now answers from this one
producer.

**The past reads the FOLD, not the postings -- that is the cutover's heart.**
Before this the seam's past balance was a sum of POSTINGS (the genesis reader
``confirmed_loan_balance_at``, since DELETED at plan step E1e).  Here it is the
fold over the loan's SOURCE events, which step B2 proves equal to the postings
on every day (``tests/test_services/test_loan_fold_oracle.py``): the postings
become a checked projection of the fold (asserted at write time since plan step
E1a), not the answer to "what do I owe".  A loan whose POSTING ledger is missing
(a cache miss) still folds correctly from its source facts, so the read is no
longer an outage when the cache is cold -- it is a repairable inconsistency
(B-8).

**The future is a FOLD over the forward PLAN (step C6b).**  It folds the
confirmed-present seed forward over the loan's
:func:`~app.services.balance_at._plan.loan_plan` -- its projected payment RECORDS
at their LIVE cash, then contractual synthesis beyond the record horizon -- rather
than walking the resolver's contractual schedule rows.  An overdue installment
with NO settled record no longer pays the loan down (finding B-9, killed here),
and a projected payment folds its LIVE cash, so the loan balance and the checking
side move together.  The seed and the origination boundary still come from the
resolver bundle (:func:`app.services.balance_at._kernel.generate_debt_schedules`),
and the plan is memoized on the read pass's context
(:meth:`~app.services.balance_at.BalanceContext.loan_plan`) so one build
serves every forward date and every producer that reads it.

**Why here, and not in the ``loan_ledger`` leaf.**  Section 3's end-state has the
loan ledger answering a date on its own, but the forward half composes the
resolver's seed (:func:`app.services.balance_at._kernel.generate_debt_schedules`)
with the seam-level plan (:func:`~app.services.balance_at._plan.loan_plan`, which
reads the resolver, the escrow lines, the projected shadows, and their live cash)
-- all above the pure leaf.  Composing them is a SEAM responsibility, not a leaf
one; the leaf stays pure.  (An earlier note here said this producer "can move to
``loan_ledger``" once the seed went fold-native -- step D2a made it fold-native,
and the note was WRONG: the D0b ruling is that a balance producer moves deeper
INTO the seam, never out to a public leaf, where it would need the very fence
Phase D deletes.)

**Proven equal before it was wired.**  C3a shipped this ADDITIVE, with an oracle
that parallel-ran it against the scalar it replaced on EVERY day past and future,
so C3b's cutover moved no money by proof, not hope (plan Section 7.2).  That oracle
retired with the cutover (the scalar IS this now); the ongoing every-day guarantee
is B2's fold-vs-reader oracle (``test_loan_fold_oracle.py``) plus the seam's own
tests.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from collections import OrderedDict
from datetime import date
from decimal import Decimal

from app.models.account import Account

from ._context import BalanceContext, _memoize_once, require_scenario
from ._fold import fold_from_walk
from . import _kernel
from ._plan import (
    fold_forward,
    memoized_plan,
    plan_payoff_date,
    plan_required_extra,
)


def window_sample_date(start_date: date, end_date: date, as_of: date) -> date:
    """Return the date that values a ``[start, end]`` window as of *as_of*.

    The begun/future sampling rule shared by every :func:`positions` caller that
    values a WINDOW rather than a point -- the per-period map
    (:func:`positions_period_map`) and the property equity chart's per-month debt
    line (:func:`app.services.balance_at.secured_loan_series`).  A window that has
    BEGUN (its start on or before *as_of*) is valued at ``min(end_date, as_of)``:
    its end for a fully-past window, *as_of* for the CURRENT one -- so the current
    window reads today's FOLD, not a projected end (the clamp C3b2 proved
    load-bearing).  A FUTURE window (start after *as_of*) is valued at ``end_date``,
    the projected balance at its close.  ONE home, so the map and the chart cannot
    drift on the boundary that clamp rests on.

    Args:
        start_date: The window's start (its ``period.start_date`` / month first).
        end_date: The window's end (its ``period.end_date`` / month end).
        as_of: The read pass's as-of.

    Returns:
        The date to sample :func:`positions` at for this window.
    """
    if start_date <= as_of:
        return min(end_date, as_of)
    return end_date


def _forward_seed(account: Account, ctx: BalanceContext, caller: str) -> Decimal:
    """Return the loan's projection SEED, or fail loud if it is not a loan.

    The one entry-guard-plus-seed both forward derivations share
    (:func:`loan_payoff_date` and :func:`loan_required_extra`).  It was copied
    into each, and the copy is exactly the hazard this arc exists to remove: the
    seed is the load-bearing input, so two copies are two places for the payoff
    and the target-date answer to start from different balances and disagree
    without anything failing.  ``duplicate-code`` is cross-FILE only, so nothing
    would have caught the drift.

    Args:
        account: The amortizing loan account.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        caller: The public function's name, for the fail-loud message.

    Returns:
        The loan's :attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`.

    Raises:
        ValueError: When ``scenario`` is None, or when *account* is not a
            configured loan.
    """
    require_scenario(ctx)
    debt_schedule = _kernel.generate_debt_schedules(
        [account], ctx,
    ).get(account.id)
    if debt_schedule is None:
        raise ValueError(
            f"{caller}() requires a configured loan; account {account.id} "
            f"({account.name!r}) has no LoanParams. The seam's AMORTIZING "
            f"dispatch degrades a non-loan account to the cash producer "
            f"before reaching here."
        )
    return debt_schedule.projection_seed


def positions(
    account: Account, ctx: BalanceContext, dates: list[date],
) -> dict[date, Decimal]:
    """Return *account*'s loan balance at each of *dates* -- fold past, projection future.

    The total loan balance-at-T producer (see the module docstring), applied to
    the whole date list so N dates cost one fold walk, not N.  It dispatches each
    date on the loan's own timeline:

    * **A date at or before the resolver's NOW, for an ORIGINATED loan: the fold**
      (:func:`app.services.balance_at._fold.fold_from_walk` over the read pass's memoized
      walk) over the loan's source events -- the past that step B2 proves equal to
      the sum-of-postings reader the seam read before the cutover.
    * **A date after the NOW, OR any date for a loan not yet originated by it: the
      forward PLAN fold** (:func:`~app.services.balance_at._plan.fold_forward` over
      the memoized :meth:`~app.services.balance_at.BalanceContext.loan_plan`)
      -- the confirmed-present seed
      (:attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`) folded
      forward over the loan's projected payment records and contractual synthesis,
      gated at ``owed_from`` (the loan owes ``0.00`` before it originates).  A
      not-yet-originated loan has no confirmed past for the fold to own, so the plan
      fold owns its whole timeline -- exactly the scalar's rule (its docstring's
      third case).

    **Loan-only, and fails loud otherwise.**  A non-configured account (no
    :class:`~app.models.loan_params.LoanParams`) is the seam dispatch's
    cash-degrade case, resolved before this is reached, so an account with no debt
    schedule is a caller error here rather than a silent wrong answer.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the fold and the resolver; its ``as_of`` is the
            resolver's NOW and the past/future boundary (the SAME ``ctx.as_of`` the
            scalar splits on, so the two cannot disagree about which dates are
            projected).
        dates: The calendar dates to value the loan at, in any order.  Duplicates
            collapse.

    Returns:
        ``{date: Decimal balance owed}`` -- one cent-quantized balance per distinct
        requested date.  ``{}`` for an empty *dates*.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a configured loan
            (the seam degrades a non-loan to the cash producer before reaching
            here).
    """
    require_scenario(ctx)
    debt_schedule = _kernel.generate_debt_schedules(
        [account], ctx,
    ).get(account.id)
    if debt_schedule is None:
        raise ValueError(
            f"positions() requires a configured loan; account {account.id} "
            f"({account.name!r}) has no LoanParams. The seam's AMORTIZING "
            f"dispatch degrades a non-loan account to the cash producer "
            f"(the cash fold) before reaching here."
        )
    # A date is PAST (reads the fold) iff the loan has originated by the NOW and
    # the date is at or before it; every other date -- future, or any date of a
    # loan not yet originated -- reads the forward projection.  This is the
    # scalar's ``as_of > ctx.as_of or owed_from > ctx.as_of`` predicate, negated.
    originated = debt_schedule.owed_from <= ctx.as_of
    past_dates: list[date] = []
    forward_dates: list[date] = []
    for on_date in dates:
        if originated and on_date <= ctx.as_of:
            past_dates.append(on_date)
        else:
            forward_dates.append(on_date)

    result: dict[date, Decimal] = {}
    if past_dates:
        # Fold the pass's MEMOIZED walk (:meth:`BalanceContext.loan_walk`): the
        # scalar, the per-period map, and the liability band all read this loan in
        # one render, so walking it once and sampling here is what keeps the cutover
        # from re-walking the loan per producer.
        result.update(
            fold_from_walk(ctx.loan_walk(account), past_dates),
        )
    if forward_dates:
        # The future is a FOLD over the loan's forward PLAN (step C6b): its
        # projected payment RECORDS at their live cash, then contractual synthesis
        # beyond the record horizon (``memoized_plan``, so one plan serves
        # every forward date), folded from the confirmed-present seed
        # (``fold_forward``).  The plan carries the origination gate too -- a date
        # before ``owed_from`` owes ``0.00``.  This replaces the resolver's
        # schedule walk: an overdue installment with NO settled record no longer
        # pays the loan down (finding B-9), and a projected payment folds its LIVE
        # cash, not the stored amount the walk amortized.
        result.update(fold_forward(
            debt_schedule.projection_seed, debt_schedule.owed_from,
            memoized_plan(account, ctx), forward_dates,
        ))
    return result


def positions_period_map(
    account: Account, ctx: BalanceContext,
) -> "OrderedDict[int, Decimal]":
    """Return a loan's per-period balance map, built from :func:`positions`.

    The per-period form of :func:`positions`, read by the seam's loan map
    dispatch (:func:`app.services.balance_at._inputs._account_balance_map`) as of step
    C3b3.  It reproduces the genesis per-period read switch the kernel's retired
    ``_build_amortizing_balance_map`` ran -- ledger past, projection future -- but
    from the ONE total loan producer instead of the sum-of-postings map plus a
    splice, so the scalar, the map, and the liability band all answer a loan from
    :func:`positions` and cannot disagree.

    **The sampling rule reproduces the retired splice's boundary.**  That splice
    (the deleted ``splice_confirmed_and_projected_loan_balances``) keyed on
    ``period.start_date <= ctx.as_of``: a BEGUN period read the confirmed ledger at
    its END, a FUTURE period the forward projection at its end.  This samples
    :func:`positions` at the date that reproduces each side:

    * **A BEGUN period (``period.start_date <= ctx.as_of``): valued at**
      ``min(period.end_date, ctx.as_of)``.  For a period that ENDED by the NOW the
      clamp is a no-op (``period.end_date``), and :func:`positions` folds the ledger
      there -- equal to the kernel's period-END-keyed confirmed balance (step B2
      proves the fold equals the sum-of-postings reader on every day).  For the
      CURRENT period (begun, but ending after the NOW) the clamp is ``ctx.as_of``,
      and that clamp is load-bearing: sampling at ``period.end_date`` would hand the
      current period to the forward PROJECTION (its end is after the NOW), moving it
      by any payment scheduled between the NOW and period end -- where the confirmed
      ledger holds today's balance flat to period end.  The clamp keeps the current
      period on the fold, matching the confirmed map.  (Under the one clock no
      confirmed posting is dated after today, so for the production read pass -- whose
      ``ctx.as_of`` is always today -- the confirmed map's period-END value for the
      current period IS its balance-at-today; the clamp reproduces it exactly.)
    * **A FUTURE period (``period.start_date > ctx.as_of``): valued at**
      ``period.end_date``.  :func:`positions` answers it from the forward PLAN fold
      (step C6b) at that date, so a future period reflects the loan's projected
      payment records and contractual synthesis rather than the resolver's
      contractual schedule walk the retired kernel forward map ran period-END-keyed.

    A not-yet-originated loan folds nothing: :func:`positions` routes every date
    (begun periods clamp to a date before ``owed_from``) to the projection's
    origination gate, which returns ``0.00`` before the loan exists -- exactly the
    kernel map's true-zero confirmed side for such a loan.

    **Equal to the retired ``_build_amortizing_balance_map`` for the production
    read pass, with one behaviour change adopted at the cutover.**  For a
    configured loan with an intact posting ledger this returns the same map the
    kernel produced (proven every-period by the C3b2 oracle, which retired with the
    cutover since the map it graded against is now this producer).  The one place
    it differs is a BROKEN loan (originated, no OPENING posting): this folds the
    loan's SOURCE facts and answers, where the kernel map RAISED for it -- the same
    E1 repairable-cache decision the scalar already took at step C3b1 (a cold
    posting cache is a repairable inconsistency, not a read-time outage).

    Args:
        account: The amortizing loan account (the caller owns the ownership check).
            Loan-only, and fails loud otherwise: a non-configured account (no
            :class:`~app.models.loan_params.LoanParams`) is the seam dispatch's
            cash-degrade case, resolved before this is reached, so
            :func:`positions` raises for it.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its ``as_of`` is the resolver's NOW and the begun/future boundary
            (the SAME ``ctx.as_of`` the kernel map splices on), and whose
            ``reported_periods()`` is the map's domain since plan step C2-c --
            each period's bounds DERIVED from the owner's paydays rather than
            read off the two stored columns plan step C4 drops.

    Returns:
        ``OrderedDict`` period_id -> cent-quantized ``Decimal`` balance, in
        payday order.  ``OrderedDict()`` for an owner with no pay periods.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a configured loan
            (:func:`positions`' own contract).
        PayCalendarError: The owner's paydays cannot define a calendar, which
            since plan step C2-c is reachable from every per-period seam entry
            rather than only from the recurrence pages -- see
            :meth:`~app.services.balance_at.BalanceContext.calendar`, where the
            reporting domain is derived, for the one state that produces it and
            the step that removes it.
    """
    # Fail loud at the entry on a missing baseline, as every public seam entry
    # does -- positions() guards too, but guarding here keeps the contract's
    # failure at the surface the consumer called rather than deep inside the
    # composed producer (the same defensive double-guard balance_at() runs).
    require_scenario(ctx)
    # The date to value each period at, reproducing the splice's begun/future
    # boundary (see the docstring).  positions() collapses duplicate dates, so a
    # boundary period landing on ctx.as_of costs nothing extra.
    window = ctx.reported_periods()
    sample_on: dict[int, date] = {
        period.period_id: window_sample_date(
            period.start_date, period.end_date, ctx.as_of,
        )
        for period in window
    }
    valued = positions(account, ctx, list(sample_on.values()))
    return OrderedDict(
        (period.period_id, valued[sample_on[period.period_id]])
        for period in window
    )


def loan_payoff_date(account: Account, ctx: BalanceContext) -> date | None:
    """Return *account*'s DERIVED payoff date -- the date its balance folds to zero.

    The payoff-date sibling of :func:`positions`: it composes the SAME confirmed
    present seed (:attr:`~app.services.balance_at._kernel.DebtSchedule.projection_seed`)
    and the SAME memoized forward plan
    (:meth:`~app.services.balance_at.BalanceContext.loan_plan`) and folds
    them to zero (:func:`~app.services.balance_at._plan.plan_payoff_date`), so the
    payoff is the date :func:`positions` shows the balance reaching ``0.00`` -- the
    chip, the equity chart, and the payoff cannot disagree about WHETHER the loan
    clears.  (They can differ on the DATE only in one rare edge: an overdue-but-
    projected installment that itself clears the loan folds at its past DUE date
    here but its future EFFECTIVE date in :func:`positions`; see
    :func:`~app.services.balance_at._plan.plan_payoff_date`.)  DERIVED, never
    stored: it replaces the persisted-from-a-blind-walk copies
    (``LoanState.payoff_date``, ``RecurrenceRule.end_date``) the arc retires (plan
    step C8).

    It is a FOLD-TO-ZERO, not ``plan[-1].date``: the plan runs PAST the contractual
    payoff (the ESTIMATED tail's extension), so a loan paying extra reaches zero at
    an EARLIER installment, and this returns that earlier date.  **The baseline is
    unmoved for a HEALTHY or OVERPAYING loan** -- the fold reaches zero at or before
    the contractual last installment, the same date the resolver's committed payoff
    reports.  An UNDERPAYING loan gets a slightly-LATER date -- the fold keeps
    paying the level payment past the contractual date until it clears (the
    extension) -- where the resolver's ``project_forward`` forces the contractual
    date via ``is_last_month`` (a phantom final payment); a drift so severe it never
    clears within the extension folds to ``None``.  The drift is what C7's
    payment-drift warning surfaces.

    ``None`` for a loan already retired (``projection_seed <= 0`` -- no forward
    crossing), negative amortization, or an underpayment too severe to clear within
    the post-contractual extension.  The caller reads
    :attr:`~app.services.balance_at.LoanFigures.is_retired` to tell the paid-off
    state (badge it) from the not-yet-cleared ones (recurrence stays indefinite).

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`
            -- its scenario scopes the resolution and plan, and its ``as_of`` is
            the projection's now (the ``max(due, as_of + 1d)`` clamp floor).

    Returns:
        The DUE date the loan's balance first folds to ``<= 0``, or ``None`` when
        the loan is already retired or never pays off.

    Raises:
        BaselineMissingError: When ``scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a configured loan
            (the seam degrades a non-loan to the cash producer before reaching
            here).
    """
    return plan_payoff_date(
        _forward_seed(account, ctx, "loan_payoff_date"),
        memoized_plan(account, ctx),
    )


def memoized_payoff(account: Account, ctx: BalanceContext) -> date | None:
    """Return *account*'s DERIVED payoff for this read pass, deriving it once.

    The seam's ONE funnel for the payoff (the :func:`loan_payoff_date` analog of
    :func:`~app.services.balance_at._plan.memoized_plan`): it fills the read pass's
    per-loan payoff cache (:attr:`~app.services.balance_at.BalanceContext.payoffs`)
    from :func:`loan_payoff_date` through the shared store-once primitive
    (``_context._memoize_once``), so the fold-to-zero runs at most once per account
    per pass.  A single ``/savings`` render asks for the payoff twice on one loan
    (the debt tile's :func:`~app.services.balance_at.loan_figures`, and the
    home-equity card's configured-loan test), and the property page asks again per
    secured loan -- one derivation now serves them all.

    **The context receives no deriver (plan step D-ctx-b).**  Like the plan funnel,
    this FILLS a public pass-through cache rather than injecting the derivation into
    a context method: :func:`loan_payoff_date` lives ABOVE the context, which cannot
    import it back without inverting the dependency arrow (finding N-25).

    Args:
        account: The loan account to derive the payoff for.  Must belong to
            ``ctx.user_id`` (the caller owns the ownership check), and must be a
            CONFIGURED loan -- :func:`loan_payoff_date` fails loud otherwise.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The memoized payoff date, or ``None`` when the loan is already retired or
        never pays off within its plan (the caller reads
        :attr:`~app.services.balance_at.LoanFigures.is_retired` to tell them apart).

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None.  A ``ValueError``
            subclass; ONE application-level handler answers it (plan step
            X-v2, ruling R-BW), so no caller pre-checks.
        ValueError: When *account* is not a
            configured loan -- on EVERY call (a raising derivation is never cached).
    """
    return _memoize_once(
        ctx, ctx.payoffs, account, lambda: loan_payoff_date(account, ctx),
    )


def loan_required_extra(
    account: Account, ctx: BalanceContext, target_date: date,
) -> Decimal | None:
    """Return the extra per payment *account* needs to be clear by *target_date*.

    The target-date calculator's answer (plan step C8f), composed from the SAME
    confirmed-present seed and the SAME memoized forward plan
    :func:`loan_payoff_date` folds -- so "when does my plan pay this off" and
    "what would it take to finish by X" are two questions asked of ONE model.

    It replaced ``loan_resolver.target_date_outlook``, which binary-searched the
    resolver's contractual schedule walk.  That walk amortizes an installment per
    month whether or not a payment stands behind it (finding B-9), so for a
    delinquent or drifted loan it retired the debt earlier than the fold and could
    answer "no extra needed" for a target the loan does not reach -- on the same
    page as a payoff chip that folds and says otherwise.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        target_date: The date the user wants the loan retired by.

    Returns:
        ``Decimal("0.00")`` when the current plan already clears the loan by
        *target_date*, the searched per-payment extra when one exists, or ``None``
        when the target is unreachable -- no planned payment lands by then (a past
        target, or one before the next installment), or the search exhausted its
        bound (see :func:`~app.services.balance_at._plan.plan_required_extra`).

    Raises:
        ValueError: When ``scenario`` is None, or when *account* is not a
            configured loan (the seam degrades a non-loan to the cash producer
            before reaching here).
    """
    return plan_required_extra(
        _forward_seed(account, ctx, "loan_required_extra"),
        memoized_plan(account, ctx),
        target_date,
    )
