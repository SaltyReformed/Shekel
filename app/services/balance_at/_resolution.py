"""Balance-at-T seam -- the db-facing WHOLE-LOAN read that seeds the resolver.

Plan step **E1d-a** (``docs/audits/balance_architecture/README.md``).  The pure
:mod:`app.services.loan_resolver` takes plain data and returns a
:class:`~app.services.loan_resolver.LoanState`; this module is the db-facing entry
every surface (net worth, /savings tile, debt-strategy, home equity, the loan
route card, the equity chart) resolves a loan account through.  It loads the
loan's inputs ONCE, threads in the genesis-ledger confirmed view and the loan's
standing overpayment, and delegates -- so no surface can drift on HOW a loan is
resolved, and none can silently fall back to the contractual (extra-free)
trajectory.

**Why it lives INSIDE the seam** (plan step E1d-a; developer ruling 2026-07-24).
It was the public module ``app.services.loan_resolution``, one hop outside the
balance seam, kept honest by a hand-written W9909 completeness ruling -- the
Phase-D shape the arc has been deleting everywhere else.  Two reasons closed it:

* Its ONE production caller was the read pass's context memo, i.e. the seam.  A
  module with a single in-package caller is a private of that package.
* Plan step E1d makes the confirmed seed a FOLD of the loan's events -- a
  balance-at-T, produced in the seam.  Phase D's invariant is that every balance
  producer is private to ``balance_at`` (enforced by W9910), so the composer that
  consumes that seed belongs on the same side of the boundary as the seed.

Moving it in DELETES its fence entry rather than shrinking it: the
``app.services.loan_resolution`` W9909 scope is gone, and W9910 now protects the
whole chain structurally, name-independently.

**The read pass's memo lives here, not on the context** (plan step D-ctx-b's
rule, applied): the seam owns the derivation, the context owns the storage.
:func:`resolved_loan` fills the pass's public
:attr:`~app.services.balance_at.BalanceContext.loans` cache through the shared
store-once primitive, exactly as
:func:`~app.services.balance_at._plan.memoized_plan` fills ``plans``.

It also hosts one PURE (no-I/O) producer, :func:`contractual_schedule_from_origination`:
the property equity chart's from-origination contractual schedule, which seeds the
same resolver composer with a synthesized origination anchor instead of a confirmed
view.  It lives here beside :func:`resolve_loan_bundle` because it too composes the
loaders' anchor synthesis with the pure resolver; the caller supplies its one loaded
input (the rate-change feed), so the function itself stays I/O-free.

Boundary discipline (``CLAUDE.md``): no Flask symbol, no writes; all money is
:class:`~decimal.Decimal`.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.account import Account
from app.models.loan_params import LoanParams
from app.services import loan_resolver
from app.services.amortization_engine import AmortizationRow, RateChangeRecord
from app.services.loan_loaders import (
    load_loan_anchor_facts,
    load_loan_params,
    synthesize_origination_anchor,
)
from app.services.loan_payment_service import (
    LoanContext,
    confirmed_loan_view,
    load_loan_context,
)
from app.services.recurring_transfer_query import (
    loan_standing_extra_for_account,
)

from ._context import BalanceContext, _memoize_once


@dataclass(frozen=True)
class ResolvedLoan:
    """Everything one loan resolution produced, from ONE load of its inputs.

    The unit of work :class:`~app.services.balance_at.BalanceContext`
    memoizes: a loan's loaded inputs AND the :class:`LoanState` they resolve to,
    bundled so that every surface reading any part of a loan -- its balance, its
    schedule, its payment / rate / payoff, its payment feed -- reads ONE
    resolution rather than triggering its own.

    Before this bundle existed, a single ``/savings`` render ran the resolver
    ELEVEN times for two loans (measured 2026-07-13), because each consumer
    re-derived the loan from scratch: the balance maps, the trend window's
    honest-history gate, the liability band, the loan tile, the property-equity
    card, and the "ever paid off" probe each loaded and resolved independently.
    They agreed on the balance, which is what made the waste look harmless --
    but the probe among them resolved through a producer that CANNOT read the
    genesis ledger, and no gate could see that because there was no single
    resolution to compare against
    (``docs/audits/balance_architecture/followup_redundant_loan_resolution.md``).

    Attributes:
        params: The loan's :class:`~app.models.loan_params.LoanParams`.
        anchor_facts: The loan's anchor facts (the synthesized origination
            anchor plus any balance true-ups), from
            :func:`~app.services.loan_loaders.load_loan_anchor_facts`.
        context: The loan's loaded :class:`~app.services.loan_payment_service.LoanContext`
            (payments, rate changes, escrow lines, contractual P&I).  Carried
            because the payment feed answers questions the ``LoanState`` cannot
            -- "has this loan ever had a confirmed payment?" above all -- and
            re-loading it was one of the redundant resolutions.
        state: The resolved :class:`~app.services.loan_resolver.LoanState` as of
            the context's ``as_of``: the committed (plan-aware) schedule, the
            payment, the rate, and the life-of-loan interest.  No balance and no
            payoff -- the ``balance_at`` seam derives both from the fold (plan
            steps C8d / D2a).
        extra_principal: The loan's standing monthly overpayment
            (:func:`~app.services.recurring_transfer_query.loan_standing_extra_for_account`),
            loaded ONCE here and threaded into :func:`resolve_loan_seeded` so
            ``state``'s schedule / payoff already fold it.  Surfaced on the bundle
            so the seam's forward PLAN
            (:func:`app.services.balance_at._plan.loan_plan`) folds the SAME extra
            past the materialized-shadow horizon without re-reading it (finding
            N-15).  ``Decimal("0.00")`` when the loan has no recurring payment.
    """

    params: LoanParams
    anchor_facts: list
    context: LoanContext
    state: loan_resolver.LoanState
    extra_principal: Decimal


def resolved_loan(
    account: Account, ctx: BalanceContext,
) -> ResolvedLoan | None:
    """Return *account*'s resolution for this read pass, resolving it at most once.

    The seam's ONE funnel for a whole-loan read: it fills the read pass's per-loan
    resolution cache (:attr:`~app.services.balance_at.BalanceContext.loans`) from
    :func:`resolve_loan_bundle` through the shared store-once primitive
    (``_context._memoize_once``), so a loan is loaded and resolved at most once per
    pass however many surfaces ask.  Every seam consumer that wants a loan's
    schedule, payment, rate, payment feed, or standing extra goes through here, so
    the loan tile's figures, the net-worth hero, the liability band, and the debt
    card read ONE resolution -- identical BY CONSTRUCTION rather than by the luck
    of four producers agreeing.

    Before that memo existed, a single ``compute_dashboard_data`` call ran the
    resolver ELEVEN times for two loans (measured 2026-07-13), and the redundancy
    was not merely waste: one of the eleven resolved through a producer that could
    not read the genesis ledger, and the ten that agreed made the eleventh
    invisible.

    A ``None`` result (the account has no :class:`~app.models.loan_params.LoanParams`
    -- it is not a configured loan) is memoized TOO, so a non-loan account asked
    repeatedly does not re-issue its params query each time.  That is why the
    store-once primitive tests MEMBERSHIP rather than truthiness.

    **It is a seam function, not a context method** (plan step E1d-a, applying
    D-ctx-b's rule).  It was ``BalanceContext.resolved_loan``, which put a public
    method on an object every route legitimately holds -- the one surface W9910
    cannot see, since the gate reads imports and never attribute access (finding
    H1 of plan step D3's adversarial review, which is why ``_context`` carries the
    seam's last W9909 completeness ruling).  Moving the derivation here shrinks
    that surface: the context now stores, and the seam derives.

    Args:
        account: The account to resolve.  Must belong to ``ctx.user_id`` (the
            caller owns the ownership check -- the loaders trust it).
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`,
            whose ``scenario`` scopes the payment history and whose ``as_of`` is
            the date the loan is RESOLVED at.

    Returns:
        The pass's memoized :class:`ResolvedLoan`, or ``None`` when *account* is
        not a configured loan.
    """
    return _memoize_once(
        ctx.loans,
        account.id,
        lambda: resolve_loan_bundle(account.id, ctx.scenario_id, ctx.as_of),
    )


def resolve_loan_seeded(
    loan_inputs: loan_resolver.LoanInputs,
    scenario_id: int | None,
    as_of: date,
    extra_principal: Decimal,
) -> loan_resolver.LoanState:
    """Resolve a loan with its ledger view AND standing extra threaded in.

    The injection helper :func:`resolve_loan_bundle` routes every summary-surface
    resolution through (the bundle is what
    :class:`~app.services.balance_at.BalanceContext` memoizes), so no
    surface can drift on HOW a loan is resolved.  It threads two seeded inputs
    into the pure resolver:

    * The genesis-ledger confirmed view (:func:`confirmed_loan_view`, loaded
      here): its balance seeds the schedule composer's forward starting
      balance, and its ledger-derived rows become the schedule's confirmed
      slice, so the history and the projection cannot desync off-schedule.
      When the ledger cannot answer (``None`` -- a loan it has not opened, or
      one that has not originated by *as_of*) the composer falls back to its
      anchor replay, the pre-switch behaviour.  The loan's displayed BALANCE
      is not derived here at all (plan step D2a): the ``balance_at`` seam
      folds it from the loan's recorded events.
    * The loan's standing overpayment (``extra_principal``, loaded by the
      caller): applied to every forward month so ``LoanState``'s schedule,
      payoff, and interest are the COMMITTED (plan-aware) trajectory every
      summary surface shows, matching the loan detail page (step 8,
      ``docs/design/escrow_line_identity_refactor.md`` Sec. 16).  ``0.00`` for a
      loan with no recurring payment, so the injection is a safe no-op there.
      :func:`resolve_loan_bundle` loads it (not this helper) so it can ALSO
      surface the SAME figure on :attr:`ResolvedLoan.extra_principal` for the
      seam's forward plan to fold past the shadow horizon (finding N-15).

    Routing every resolution through here is what makes it structurally
    impossible for a summary surface to resolve a loan without its plan: a new
    caller cannot silently regress to the contractual trajectory, because the
    bundle chokepoint owns the loads.

    Args:
        loan_inputs: The loan's loaded :class:`LoanInputs` bundle.  Its
            ``loan_params`` identifies the loan to the confirmed-view load below,
            so it cannot be asked about a different one.
        scenario_id: The baseline scenario id, or ``None``.
        as_of: The evaluation date; typically ``date.today()``.
        extra_principal: The loan's standing overpayment, loaded ONCE by the
            caller (:func:`resolve_loan_bundle`) and threaded in, so the same
            figure the bundle surfaces on :attr:`ResolvedLoan.extra_principal`
            shapes ``state``'s schedule / payoff (no second read).

    Returns:
        The resolved :class:`~app.services.loan_resolver.LoanState`.
    """
    view = confirmed_loan_view(loan_inputs.loan_params, scenario_id, as_of)
    return loan_resolver.resolve_loan(
        loan_inputs, as_of, confirmed_view=view,
        extra_principal=extra_principal,
    )


def resolve_loan_bundle(
    account_id: int, scenario_id: int | None, as_of: date,
) -> ResolvedLoan | None:
    """Load a loan's inputs ONCE and resolve it -- the whole-loan read.

    The single db-facing loan read the whole app resolves through: it loads the
    loan's params, anchor facts, and context, runs
    :func:`resolve_loan_seeded`, and returns all four bundled as a
    :class:`ResolvedLoan`.  :class:`~app.services.balance_at.BalanceContext`
    memoizes it per ``(account, scenario, as_of)`` so a read pass resolves each
    loan exactly once no matter how many surfaces ask.

    Returning the loaded ``context`` alongside the ``state`` is what removes the
    last reason for a consumer to re-load: the loan tile previously called
    :func:`load_loan_context` and :func:`load_loan_anchor_facts` itself (to run a
    second ``date.max`` resolver probe), which is precisely the duplication this
    bundle exists to make unnecessary.

    Returns ``None`` when the account has no ``LoanParams`` row (it is not a
    configured loan); the caller skips it.  A configured loan is always
    resolvable -- its origination anchor fact is synthesized from the immutable
    params -- so there is no anchor-based short-circuit here.

    Args:
        account_id: The loan account to resolve.  The caller owns the ownership
            check (the loaders trust this arg).
        scenario_id: The active budget scenario (scopes the payment history and
            the genesis-ledger seed), or ``None`` when the user has no baseline
            -- the loan then resolves from its anchor with no payment feed, the
            documented degraded state.
        as_of: The evaluation date the loan is resolved AT (the resolver's
            "now": what counts as confirmed, and what the current balance is).

    Returns:
        The :class:`ResolvedLoan`, or ``None`` if the account has no
        ``LoanParams``.
    """
    params = load_loan_params(account_id)
    if params is None:
        return None
    anchor_facts = load_loan_anchor_facts(params)
    context = load_loan_context(account_id, scenario_id, params)
    # Load the standing extra ONCE here and thread it BOTH into the resolve (so
    # state's schedule / payoff fold it) and onto the bundle (so the seam's
    # forward plan folds the SAME figure past the shadow horizon -- N-15 -- with
    # no second read).
    extra_principal = loan_standing_extra_for_account(account_id)
    state = resolve_loan_seeded(
        loan_resolver.LoanInputs(
            params, anchor_facts, context.payments, context.rate_changes,
        ),
        scenario_id, as_of, extra_principal,
    )
    return ResolvedLoan(
        params=params,
        anchor_facts=anchor_facts,
        context=context,
        state=state,
        extra_principal=extra_principal,
    )


def contractual_schedule_from_origination(
    loan_params: LoanParams,
    rate_changes: list[RateChangeRecord] | None,
) -> list[AmortizationRow]:
    """Return a loan's pure contractual amortization schedule FROM ORIGINATION.

    The property equity chart's pre-tracking debt line (the (a) contractual
    back-projection,
    ``docs/plans/implementation_plan_property_equity_chart_rebuild.md``): a
    mid-life-imported loan's confirmed ledger opens at its ``tracking_start``, so
    :func:`resolve_loan_bundle`'s schedule begins there and the
    origination-to-tracking-start months are absent.  This producer supplies
    them as the contractual schedule the loan's origination terms imply
    (``original_principal`` amortized over ``term_months`` at the rate-period
    rates), so the chart can draw the pre-tracking debt as an ``estimated`` tier
    and clip it at the tracking-start seam.

    Forks NO amortization math: it seeds
    :func:`loan_resolver.compute_payoff_scenarios` with a synthesized ORIGINATION
    anchor (:func:`loan_loaders.synthesize_origination_anchor`) and an empty
    payment feed, evaluated as of ``origination_date``, and returns its
    ``original_forward`` -- the pure contractual reference (no override, no
    extra).  Seeding through the composer inherits the resolver's EXACT
    first-payment-date and remaining-term convention (``next_pay_date`` one month
    after origination on ``payment_day``, the full ``term_months`` remaining), so
    the back-projection lands on the same monthly grid as the resolved schedule
    and the two cannot drift at the seam.

    Pure: takes loaded data (the caller supplies ``rate_changes`` via
    :func:`loan_loaders.load_rate_changes`); performs no I/O.

    Args:
        loan_params: The loan's :class:`LoanParams` (its immutable
            ``origination_date`` / ``original_principal`` / ``term_months`` /
            ``payment_day`` / ARM cadence).
        rate_changes: The loan's :class:`RateChangeRecord` feed (the origination
            row plus any ARM adjustments), so each pre-tracking month is governed
            by its correct per-period rate.  Must contain the origination row
            (:func:`loan_resolver.resolve_periods` raises on an empty feed).

    Returns:
        The contractual :class:`AmortizationRow` list from origination to payoff,
        each ``is_confirmed=False`` (a contractual estimate, never recorded
        fact).  The caller clips it to the months before the loan's tracking
        start.
    """
    scenarios = loan_resolver.compute_payoff_scenarios(
        loan_inputs=loan_resolver.LoanInputs(
            loan_params,
            [synthesize_origination_anchor(loan_params)],
            None,
            rate_changes,
        ),
        extra_monthly=Decimal("0.00"),
        as_of=loan_params.origination_date,
        confirmed_view=None,
        extra_principal=Decimal("0.00"),
    )
    return list(scenarios.original_forward)
