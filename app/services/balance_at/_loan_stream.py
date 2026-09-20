"""Balance-at-T seam -- a loan's ONE timeline: its recorded facts, then its plan.

Plan step **recurrence:R16-c-1** (rulings **R-R90** and **R-R72**).  A loan's
balance is a fold over its event stream, and until this step the seam folded
that stream TWICE: the settled walk from ``0.00`` over the recorded facts
(:func:`~app.services.loan_ledger.walk_loan_ledger`), and the forward plan from
a SEED -- the walk's balance at the read day -- over the projected rows and
estimated occurrences (``_plan_fold._split_plan``).  Two event lists, two seeds,
two output types, and a set-subtraction (``seed_slots``) keeping the two charge
calendars apart.  Each of those was one value with two homes.

This module builds the ONE stream: the recorded facts the pass has SEEN --
:meth:`~app.services.balance_at.BalanceContext.loan_walk` replays the facts
visible by the pass's ``as_of``, the spec's mark where recorded fact becomes
projection -- then the plan's payments as PROJECTIONS behind them
(:attr:`~app.services.loan_ledger.LoanEventStream.projections`), replayed once
by the leaf's :func:`~app.services.loan_ledger.replay_loan_stream` from the one
seed every loan has, its origination assertion.  The result is a
:class:`~app.services.loan_ledger.LoanLedgerWalk` holding the loan's WHOLE
timeline: :func:`~app.services.balance_at._fold.fold_from_walk` values any date
off it, past or future, by one prefix-sum over one dated-delta list; the payoff
is the first projected outcome whose balance reaches zero; the tax figure is
one sum over one list of outcomes; the loan page reads the same outcomes.

**What the merge changes, and what it does not.**  A projected event is never
placed before a recorded fact (:func:`~app.services.loan_ledger.projection_boundary`),
so the fact prefix of this walk is the pass's facts walk exactly -- and, for a
pass whose ``as_of`` is on or after every recorded fact (every production
pass), :func:`~app.services.loan_ledger.walk_loan_ledger`'s output exactly, so
the posted ledger -- which replays the facts and nothing else -- agrees with
every screen on every settled payment by construction.  On this tree the merge
moves no figure: the plan's own charge calendar (``_plan._charges_for``, every
contractual installment after the loan's latest assertion, minus the months the
recorded walk charged) rides the stream as its PROJECTED charges, walked behind
the facts in contract order with the projections exactly as the forward fold
interleaved them, and no event's date is rewritten.  ``R16-c-2`` replaces the
two calendars with ONE -- every contractual installment from origination -- and
deletes the projected list with the ``seed_slots`` partition.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.  Seam-PRIVATE -- W9910 refuses an import of it from
outside :mod:`app.services.balance_at`.
"""

from dataclasses import replace
from decimal import Decimal

from app.models.account import Account
from app.services.loan_ledger import (
    LoanCashEvent,
    LoanEventStream,
    LoanLedgerWalk,
    replay_loan_stream,
)

from ._context import BalanceContext
from ._memoize import _memoize_once
from ._plan import LoanForwardPlan, memoized_plan

_ZERO_MONEY = Decimal("0.00")


def merged_stream(
    facts: LoanEventStream, plan: LoanForwardPlan,
) -> LoanEventStream:
    """Return *facts* with *plan*'s charges and payments appended as the projection.

    The ONE composition of a loan's past and future, pure.  The plan's payments
    become :class:`~app.services.loan_ledger.LoanCashEvent` projections dated at
    their installment and visible on their effective day (``max(due, as_of +
    1d)``, ruling D1), in the forward fold's own ``(due_date, effective_date)``
    order, which the replay's stable sort preserves; the plan's charges become
    the stream's PROJECTED charges.  The replay walks both behind the recorded
    facts (:func:`~app.services.loan_ledger.projection_boundary`) in contract
    order among themselves -- which is where the forward fold applied them: to
    a balance the settled walk had finished with -- so a plan charge dated
    before the loan's last recorded fact (a skipped month before a later
    settled payment) is not cleared by that settled payment, and the merge
    moves none of the posted money that is ``R16-c-2``'s to move.  Nothing here
    re-dates an event: a charge's date is the accrual period's identity, and
    the loan page renders it.

    Args:
        facts: The loan's recorded stream as the pass sees it
            (:attr:`~app.services.loan_ledger.LoanLedgerWalk.stream` of the
            pass's memoized walk -- the facts visible by its ``as_of``, ruling
            R-R91).
        plan: The loan's :func:`~._plan.loan_plan` forward model.

    Returns:
        One :class:`~app.services.loan_ledger.LoanEventStream`: the facts'
        charges, payments, resets and periods, plus the plan's charges as
        projected charges and its payments as projections.
    """
    return replace(
        facts,
        projected_charges=list(plan.charges),
        projections=[
            LoanCashEvent(
                on_date=payment.due_date,
                cash=payment.cash,
                source=payment,
                visible_on=payment.effective_date,
            )
            for payment in sorted(
                plan.payments,
                key=lambda record: (record.due_date, record.effective_date),
            )
        ],
    )


def loan_timeline(account: Account, ctx: BalanceContext) -> LoanLedgerWalk:
    """Return *account*'s whole timeline for this read pass, replayed at most once.

    The seam's ONE funnel for a loan's merged walk: the pass's memoized facts
    (:meth:`~app.services.balance_at.BalanceContext.loan_walk`) and its memoized
    plan (:func:`~._plan.memoized_plan`) composed by :func:`merged_stream` and
    replayed by :func:`~app.services.loan_ledger.replay_loan_stream`, stored on
    the pass so the balance, the payoff, the required extra, the projected
    interest and the loan page all read one replay.

    **The facts are replayed twice per pass, once alone and once here, and that
    is the dependency order rather than waste.**  The plan needs the loan's
    resolution, the resolution seeds the schedule composer from the confirmed
    view, and the confirmed view is built from the FACTS walk -- so the facts
    must be walked before the plan can exist.  The two replays share one stream
    prefix and one rule, and the fact outcomes of this walk equal the pass's
    facts walk's by construction
    (:func:`~app.services.loan_ledger.projection_boundary`); what this costs is
    one replay of the settled payments, not a second load.

    Args:
        account: The loan account.  Must belong to ``ctx.user_id`` (the caller
            owns the ownership check) and be a configured loan -- the plan
            funnel resolves it, and an unconfigured account's plan is empty, so
            its timeline is its facts walk (also empty).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.

    Returns:
        The pass's memoized :class:`~app.services.loan_ledger.LoanLedgerWalk`
        holding the loan's facts and its projections.

    Raises:
        BaselineMissingError: When ``ctx.scenario`` is None (from the plan
            funnel, on every call; a raising derivation is never cached).
    """
    # Pylint: ``protected-access`` -- the loan twin of ``_cash_fold.cash_fold``'s
    # crossing, and the same design rather than a shortcut: this module owns
    # the timeline's derivation (it needs the plan, which sits above the
    # context) and the context owns per-pass storage.  The field is PRIVATE
    # because a merged walk carries balance-at-T -- ``dated_deltas`` over it,
    # prefix-summed, is ``positions()``'s own answer -- and Python has no
    # package-private, so the alternatives are a public cache no gate can see
    # or a disable at every reading call site.  One, here, named.
    cache = ctx._timelines  # pylint: disable=protected-access
    return _memoize_once(
        ctx, cache, account,
        lambda: replay_loan_stream(merged_stream(
            ctx.loan_walk(account).stream, memoized_plan(account, ctx),
        )),
    )


def what_if_timeline(
    account: Account, ctx: BalanceContext, extra_per_period: Decimal,
) -> LoanLedgerWalk:
    """Return *account*'s timeline replayed with a hypothetical per-period extra.

    The pay-off-sooner lever's preview and the target-date search: the SAME
    stream :func:`loan_timeline` replayed, with *extra_per_period* joining the
    cash at every charge behind the projection boundary (the replay's own rule:
    money not yet paid never reprices a recorded month).  ``0.00`` is the memo
    itself, so a caller that passes the default replays nothing.

    Args:
        account: The loan account (as for :func:`loan_timeline`).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`.
        extra_per_period: The hypothetical extra per accrual period.

    Returns:
        A :class:`~app.services.loan_ledger.LoanLedgerWalk` -- the pass's memo
        for ``0.00``, a fresh replay of the same stream otherwise.
    """
    base = loan_timeline(account, ctx)
    if extra_per_period == _ZERO_MONEY:
        return base
    return replay_loan_stream(base.stream, extra_per_period=extra_per_period)
