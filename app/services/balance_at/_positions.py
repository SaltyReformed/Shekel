"""Balance-at-T seam -- a loan's balance at many dates: fold past, projection future.

Plan step **C3a/C3b** (``docs/audits/balance_architecture/README.md``).
:func:`positions` is the ONE total loan balance-at-T producer the seam's AMORTIZING
dispatch reads: the event FOLD
(:func:`app.services.loan_ledger.fold_from_walk` over the read pass's memoized
walk) for a date at or before the resolver's NOW, and the forward schedule
projection after.  The seam's SCALAR
(:func:`app.services.balance_at.balance_at`) and its LIABILITY band
(:func:`app.services.balance_at.liability_owed_at_dates`) read it as of step C3b;
the per-period map cuts over in the same step's map commit.

**The past reads the FOLD, not the postings -- that is the cutover's heart.**
Before this the seam's past balance was a sum of POSTINGS
(:func:`app.services.loan_posting_service.confirmed_loan_balance_at`).  Here it is
the fold over the loan's SOURCE events, which step B2 proves equal to the postings
on every day (``tests/test_services/test_loan_fold_oracle.py``): the postings
become a checked projection of the fold (plan step E1), not the answer to "what do
I owe".  A loan whose POSTING ledger is missing (a cache miss) still folds
correctly from its source facts, so the read is no longer an outage when the cache
is cold -- it is a repairable inconsistency (B-8).

**The future reproduces today's behaviour on purpose (plan C3 is a REFACTOR).**
It walks the resolver's UNCONFIRMED schedule rows through the existing
:func:`app.services.account_projection.forward_balance_at_date`, seeded from the
same :func:`app.services.net_worth_kernel.generate_debt_schedules` bundle the
scalar uses -- so no money moves.  The schedule projection's known
overdue-installment paydown (finding B-9) is preserved deliberately; ruling D1's
payment-RECORDS plan replaces it at step C6, which is where the baseline
consciously moves and the schedule-row primitives finally delete.

**Why here, and not in the ``loan_ledger`` leaf.**  Section 3's end-state has the
loan ledger answering a date on its own, but the PRESERVE-behaviour forward half
needs the RESOLVER's schedule and seed
(:meth:`~app.services.resolution_context.BalanceContext.resolved_loan`,
:func:`~app.services.account_projection.forward_balance_at_date`), both W9906-fenced
to the seam + engine cluster.  Composing the fold with the resolver is a SEAM
responsibility, not a leaf one; the leaf stays pure.  Step C6 makes this fold-native
(no resolver, no schedule rows), at which point it can move to ``loan_ledger``.

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
from app.services import net_worth_kernel
from app.services.account_projection import forward_balance_at_date
from app.services.loan_ledger import fold_from_walk
from app.services.resolution_context import BalanceContext

from ._inputs import _require_scenario


def positions(
    account: Account, ctx: BalanceContext, dates: list[date],
) -> dict[date, Decimal]:
    """Return *account*'s loan balance at each of *dates* -- fold past, projection future.

    The total loan balance-at-T producer (see the module docstring), applied to
    the whole date list so N dates cost one fold walk, not N.  It dispatches each
    date on the loan's own timeline:

    * **A date at or before the resolver's NOW, for an ORIGINATED loan: the fold**
      (:func:`app.services.loan_ledger.fold_from_walk` over the read pass's memoized
      walk) over the loan's source events -- the past that step B2 proves equal to
      the sum-of-postings reader the seam read before the cutover.
    * **A date after the NOW, OR any date for a loan not yet originated by it: the
      forward projection** (:func:`~app.services.account_projection.forward_balance_at_date`)
      over the resolver's unconfirmed schedule rows, seeded from
      :attr:`~app.services.net_worth_kernel.DebtSchedule.projection_seed` and gated
      at ``owed_from`` (the loan owes ``0.00`` before it originates).  A not-yet-originated
      loan has no confirmed past for the fold to own, so the projection owns its
      whole timeline -- exactly the scalar's rule (its docstring's third case).

    **Loan-only, and fails loud otherwise.**  A non-configured account (no
    :class:`~app.models.loan_params.LoanParams`) is the seam dispatch's
    cash-degrade case, resolved before this is reached, so an account with no debt
    schedule is a caller error here rather than a silent wrong answer.

    Args:
        account: The amortizing loan account (the caller owns the ownership
            check).
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
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
        ValueError: When ``scenario`` is None (callers that resolve a nullable
            baseline must guard first), or when *account* is not a configured loan
            (the seam degrades a non-loan to the cash producer before reaching
            here).
    """
    _require_scenario(ctx)
    debt_schedule = net_worth_kernel.generate_debt_schedules(
        [account], ctx,
    ).get(account.id)
    if debt_schedule is None:
        raise ValueError(
            f"positions() requires a configured loan; account {account.id} "
            f"({account.name!r}) has no LoanParams. The seam's AMORTIZING "
            f"dispatch degrades a non-loan account to the cash producer "
            f"(balance_as_of_date) before reaching here."
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
    for on_date in forward_dates:
        # Returned verbatim: the schedule rows and the seed are already
        # cent-quantized by the resolver (matching forward_balance_at_date's own
        # contract), so this stays penny-exact with the scalar.
        result[on_date] = forward_balance_at_date(
            debt_schedule.schedule, on_date,
            debt_schedule.projection_seed, debt_schedule.owed_from,
        )
    return result


def positions_period_map(
    account: Account, ctx: BalanceContext, periods: list,
) -> "OrderedDict[int, Decimal]":
    """Return a loan's per-period balance map, built from :func:`positions`.

    The per-period form of :func:`positions`, and the producer step C3b's map
    commit points the seam's AMORTIZING map dispatch at.  It reproduces the
    genesis per-period read switch the kernel's ``_build_amortizing_balance_map``
    runs today -- ledger past, projection future -- but from the ONE total loan
    producer instead of the sum-of-postings map plus a splice, so the scalar, the
    map, and the liability band all answer a loan from :func:`positions` and cannot
    disagree.

    **The sampling rule reproduces the splice's boundary.**  The kernel map splices
    on ``period.start_date <= ctx.as_of``
    (:func:`app.services.account_projection.splice_confirmed_and_projected_loan_balances`):
    a BEGUN period reads the confirmed ledger at its END, a FUTURE period the
    forward projection at its end.  This samples :func:`positions` at the date that
    reproduces each side:

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
      ``period.end_date``.  :func:`positions` answers it from the forward projection
      -- the identical ``forward_balance_at_date`` walk over the same resolver
      schedule and seed the kernel's
      :func:`~app.services.account_projection.compute_forward_loan_period_balance_map`
      runs, so the future side matches by construction.

    A not-yet-originated loan folds nothing: :func:`positions` routes every date
    (begun periods clamp to a date before ``owed_from``) to the projection's
    origination gate, which returns ``0.00`` before the loan exists -- exactly the
    kernel map's true-zero confirmed side for such a loan.

    **Equal to ``_build_amortizing_balance_map`` for the production read pass, and
    the map commit adopts one behaviour change.**  For a configured loan with an
    intact posting ledger this returns the same map the kernel produces today
    (proven every-period by ``test_loan_positions_period_map_oracle.py``).  The one
    place it will differ once wired is a BROKEN loan (originated, no OPENING
    posting): this folds the loan's SOURCE facts and answers, where the kernel map
    RAISES :class:`~app.services.posting_reads.LoanLedgerNotOpenedError` -- the same
    E1 repairable-cache decision the scalar already took at step C3b1 (a cold
    posting cache is a repairable inconsistency, not a read-time outage).

    **Additive and unwired** (plan step C3b2, mirroring C3a's :func:`positions`):
    only its oracle calls it; the map dispatch cuts over in step C3b3's commit.

    Args:
        account: The amortizing loan account (the caller owns the ownership check).
            Loan-only, and fails loud otherwise: a non-configured account (no
            :class:`~app.models.loan_params.LoanParams`) is the seam dispatch's
            cash-degrade case, resolved before this is reached, so
            :func:`positions` raises for it.
        ctx: The read pass's :class:`~app.services.resolution_context.BalanceContext`
            -- its ``as_of`` is the resolver's NOW and the begun/future boundary
            (the SAME ``ctx.as_of`` the kernel map splices on).
        periods: The pay periods to key the map by, in the desired output order.

    Returns:
        ``OrderedDict`` period_id -> cent-quantized ``Decimal`` balance, in
        *periods* order.  ``OrderedDict()`` for an empty *periods*.

    Raises:
        ValueError: When ``scenario`` is None (callers that resolve a nullable
            baseline must guard first), or when *account* is not a configured loan
            (:func:`positions`' own contract).
    """
    # Fail loud at the entry on a missing baseline, as every public seam entry
    # does -- positions() guards too, but guarding here keeps the contract's
    # failure at the surface the consumer called rather than deep inside the
    # composed producer (the same defensive double-guard balance_at() runs).
    _require_scenario(ctx)
    # The date to value each period at, reproducing the splice's begun/future
    # boundary (see the docstring).  positions() collapses duplicate dates, so a
    # boundary period landing on ctx.as_of costs nothing extra.
    sample_on: dict[int, date] = {}
    for period in periods:
        if period.start_date <= ctx.as_of:
            sample_on[period.id] = min(period.end_date, ctx.as_of)
        else:
            sample_on[period.id] = period.end_date
    valued = positions(account, ctx, list(sample_on.values()))
    return OrderedDict(
        (period.id, valued[sample_on[period.id]]) for period in periods
    )
