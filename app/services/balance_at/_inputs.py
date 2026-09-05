"""Balance-at-T seam -- shared input assembly and the fail-loud scenario guard.

The seam's private foundation: the batch-loaded per-account modelled-contribution
feed
(:func:`_contribution_inputs_for_accounts` / :func:`_contribution_inputs_for_account`),
the ONE per-kind dispatch site (:func:`_account_balance_map`), and the
:func:`_require_scenario` guard every public entry (bar the liability view --
see :mod:`._liability`) runs first.

Kept in one submodule so the view modules (:mod:`._kind_correct`,
:mod:`._cash_flow`, :mod:`._grid`, :mod:`._liability`) depend only on these
primitives and never on each other's internals.  The package's SOLID dependency
direction is ``<view module> -> _inputs``.  ``_inputs`` in turn depends on the
outer LOADER it issues its queries through
(:mod:`app.services.projection_inputs`, plus
:mod:`app.services.investment_projection` for the feed VALUE that loader
returns), and
the leaf PRODUCERS its dispatch fans out to -- the engine cluster
(:mod:`app.services.balance_at._kernel`) and the loan producer
(:func:`._positions.positions_period_map`, the one per-kind branch that lives in
the seam because it reads ``positions`` above the kernel) -- plus the one
PREDICATE its gate asks, :func:`._resolution.configured_loan`, which produces no
balance and is named apart from the producers for that reason.  None of those
imports ``_inputs`` back (``_positions`` takes its no-baseline guard straight
from :mod:`app.services.balance_at._context`, the C3b3 cycle break), so the
direction stays acyclic.

**There is ONE bundle here and it carries ONE concern** (plan step X-g3b-0).
This module used to assemble an ``_AssembledInputs`` of FOUR fields -- the three
that ARE a
:class:`~app.services.balance_at._asset_contributions.ContributionInputs`, plus a
``debt_schedules`` map -- and every caller then SLICED the three it wanted back
out of it.  Two defects rode on that shape, and both are gone with it:

* **The fourth field's VALUE was never read.**  Its only use in the app was the
  membership test ``account.id in inputs.debt_schedules``, so a map of fully
  resolved amortizations was built on every seam read to answer one boolean --
  and :func:`._positions.positions_period_map`, the consumer that test gated,
  re-derived the identical resolution itself.
* **That boolean was the seam's SECOND spelling of one predicate**, where
  :func:`._kind_correct.balance_at` wrote the rule out longhand and
  :func:`._liability.liability_owed_at_dates` decomposed it into two guard
  clauses.  The docstring on the scalar RECORDED the equivalence
  ("``resolved_loan(...) is None`` iff ``generate_debt_schedules`` would skip
  it") rather than collapsing it, and nothing enforced it.  All three now ask
  :func:`._resolution.configured_loan`, which is the one place the rule is
  stated.

So a caller that wants a contribution feed asks for a contribution feed, and the
loan gate asks one named predicate.  Nothing assembles a bundle to slice.

The feed's readers are the three :mod:`._kind_correct` entries listed on
:func:`_contribution_inputs_for_accounts` plus
:func:`._grid.grid_balance_view`, which joined them at plan step X-g3b when its
kind gate was deleted.  :mod:`._cash_flow` is not one and cannot become one: it
answers a pure transaction running balance and models nothing by contract.
"""

from collections import OrderedDict
from decimal import Decimal

from app.models.account import Account
from app.services.investment_projection import AccountPayrollFeed
from app.services.projection_inputs import (
    load_investment_params_for_accounts,
    load_payroll_feeds,
)

from ._asset_contributions import ContributionInputs
from ._context import BalanceContext, require_scenario
from . import _kernel
from ._positions import positions_period_map
from ._resolution import configured_loan

ZERO = Decimal("0")


def _contribution_inputs_for_accounts(
    accounts: list[Account], ctx: BalanceContext,
) -> dict[int, ContributionInputs]:
    """Batch-load each account's modelled-contribution feed ONCE.

    The single loading point shared by the single-account entry
    (:func:`_contribution_inputs_for_account`, called by
    :func:`~app.services.balance_at.balance_map`,
    :func:`~app.services.balance_at._kind_correct._modelled_scalar`,
    :func:`~app.services.balance_at.investment_growth_since_anchor` and
    :func:`~app.services.balance_at.grid_balance_view`) and the
    batch one (:func:`~app.services.balance_at.build_maps`), so single- and
    batch-loading run identical logic and preserve the N+1 avoidance: one
    investment-params query and one payroll-feed load for the whole set.

    The two loaders are the shared building blocks the savings cockpit's
    ``_load_account_params`` and the year-end summary already use -- this seam
    reuses them rather than writing new inline param queries.

    **It TAKES a** :class:`~app.services.balance_at.BalanceContext`, and for
    exactly one thing: the memoized pay CALENDAR it hands
    :func:`~app.services.projection_inputs.load_payroll_feeds`.  It took
    none until pay-calendar plan step C2-f2d-3's fix pass, on the argument that
    "an argument nothing reads is one a caller can get wrong" -- true while the
    gross helper it then called read the schedule through its own SQL, and
    false the moment it began DERIVING a calendar, because this entry is called
    once per ACCOUNT and each call then derived the owner's whole schedule
    again.  Measured on the routes: `/savings` went 1 -> 7 derivations a render
    before the argument was threaded, growing with the owner's
    investment-account count.

    **The CLOCK and the SCENARIO are no longer threaded because there is
    nothing left to thread them to** (plan step **salary:R14-b**).  This
    paragraph read "they are still not threaded ... passing them MOVES MONEY,
    so it is ``C2-f3``'s to rule": the gross resolved against an implicit
    ``date.today()`` and against the owner's first active profile across all
    scenarios, and both were properties of
    ``income_service.get_current_gross_biweekly``, which this loader no longer
    calls.  Ruling **R-SAL2** answers the clock (the PERIOD is the clock, so a
    paycheck is priced against its own period's inputs and no figure here
    depends on when it was asked) and **R-SAL5** answers the profile (the
    funding profile is NAMED by the deduction and by
    ``budget.investment_params.salary_profile_id``, so nothing searches).  A
    question that cannot be asked needs no argument.

    Args:
        accounts: The accounts to load feeds for, each with its
            ``account_type`` relationship available for the classifier.  They
            belong to ONE user (the caller's).  An empty list returns an empty
            map without issuing any query.
        ctx: The read pass, for its memoized pay CALENDAR alone -- the domain
            :func:`~app.services.projection_inputs.load_payroll_feeds` prices
            the owner's paychecks over, and which it would otherwise derive
            again, once per call and therefore once per ACCOUNT.

    Returns:
        ``{account_id: ContributionInputs}``, TOTAL over *accounts* -- an
        account with no feed maps to
        :meth:`~app.services.balance_at._asset_contributions.ContributionInputs.absent`'s
        value rather than being absent, so a caller indexes rather than
        defaulting and a missing key is a defect instead of a silently unfunded
        account.  One account's feed is the same whoever it is loaded beside --
        structurally so since plan step salary:R14-b, a feed being priced per
        ACCOUNT with no set-wide figure to leak across one -- so batching is an
        optimisation and never a difference in the answer.
    """
    if not accounts:
        return {}

    # Every account in the set is owned by one user (the caller's), so the
    # user id the payroll-feed loader scopes by comes off any of them.
    user_id = accounts[0].user_id

    # The shared loader owns the canonical-classifier filter, so a
    # parameterised physical asset (Property -> APPRECIATING) is correctly
    # excluded here rather than re-derived by elimination.
    investment_params_map = load_investment_params_for_accounts(accounts)

    # Feed-scoping rule (mirrors savings ``_load_account_params``): price a
    # payroll feed ONLY for the investment accounts that HAVE an
    # InvestmentParams row.  ``_asset_contributions.contribution_events`` models
    # a feed ONLY for an INVESTMENT account whose ``investment_params`` is not
    # None, so a params-less account's feed is never consumed -- scoping to the
    # params map's keys is the canonical rule that keeps this seam, savings,
    # and year-end in agreement on which accounts get one.  It also keeps a
    # single-account read for a cash / interest / loan account free of the
    # paycheck-engine run, so routing those reads through the seam stays as
    # cheap as a direct producer call -- no O(N) paycheck regression in the
    # year-end savings-progress loop.
    #
    # **This is where the engine's per-period answer enters the seam** (plan
    # step salary:R14-b).  Two inputs used to be assembled here instead: the
    # deduction ROWS, flattened by an ``adapt_deductions`` adapter that this
    # tier then re-priced off the profile's stored annual salary, and ONE
    # ``income_service.get_current_gross_biweekly`` scalar sized at today's
    # paycheck.  Both were a series collapsed to a point, which is finding
    # **D45**.  ``load_payroll_feeds`` runs the engine instead, so the feed
    # arrives priced per payday and this loader states no arithmetic at all.
    #
    # **Two guards went with them and neither was satisfied -- both became
    # unrepresentable.**  A ``cadence_days is not None`` test stood here until
    # pay_calendar:C4-d (ruling R-PC45) because ``PayCalendar.cadence`` refused
    # an owner who had never stated one; such an owner has no calendar at all
    # now.  And the gross had to be handed to ONLY the accounts that could
    # consume one, or a non-investment account's feed would have depended on
    # which OTHER accounts shared its read -- the caller-dependent input this
    # loader exists to rule out.  A feed is per ACCOUNT by construction, so
    # there is no set-wide figure left to leak across one.
    # The PASS's projection memo.  This entry is called once per ACCOUNT by
    # four seam readers, and running the engine is the expensive half of the
    # loader, so without it the engine re-ran the owner's whole saved window
    # per account -- measured at 61 ``calculate_paycheck`` calls against ~7
    # before this step, on a 3-account 10-period fixture.
    feeds = load_payroll_feeds(
        user_id, ctx.calendar(), list(investment_params_map.keys()),
        investment_params_map, ctx.payroll_breakdowns,
    )
    return {
        account.id: ContributionInputs(
            investment_params=investment_params_map.get(account.id),
            feed=feeds.get(account.id, AccountPayrollFeed.absent()),
        )
        for account in accounts
    }


def _contribution_inputs_for_account(
    account: Account, ctx: BalanceContext,
) -> ContributionInputs:
    """Return ONE account's modelled-contribution feed.

    The single-account entry, expressed as the batch loader over a one-element
    set rather than as its own query pair -- which is what keeps a scalar read,
    a period map and the grid's column set from being given different feeds for
    one account.  The shape plan Section 8 rules a defect is the alternative: a
    second loader that answers the same question a second way, agreeing only
    while nobody edits one of them.

    Args:
        account: The account to load the feed for.  Its ``account_type`` drives
            the classifier and its ``user_id`` scopes the payroll-feed
            loader.
        ctx: The read pass, for its memoized pay CALENDAR alone -- see the
            batch loader.

    Returns:
        The account's
        :class:`~app.services.balance_at._asset_contributions.ContributionInputs`
        -- the empty bundle (:meth:`ContributionInputs.absent`'s value) for an
        account with no feed, which is a real answer and not a missing one.
    """
    return _contribution_inputs_for_accounts([account], ctx)[account.id]


def _account_balance_map(
    account: Account,
    ctx: BalanceContext,
    inputs: ContributionInputs,
) -> OrderedDict[int, Decimal]:
    """Dispatch ONE account's per-period balance map.

    The seam's single per-period dispatch site, shared by
    :func:`~app.services.balance_at.balance_map` and
    :func:`~app.services.balance_at.build_maps`.  It has exactly two arms:

    * **CONFIGURED LOANS** read the seam's own :func:`positions_period_map`
      (plan step C3b3): the fold for begun periods, the projection for the
      future, from the ONE total loan producer
      (:func:`app.services.balance_at.positions`) -- so the scalar, the map, and
      the liability band all answer a loan from ``positions`` and cannot
      disagree.  This one per-kind branch lives HERE in the seam, not in the
      kernel's dispatcher, because ``positions`` sits ABOVE
      :mod:`app.services.balance_at._kernel` (at its module-size cap, and it
      cannot import the seam back).
    * **Every other kind** hands its
      :class:`~app.services.balance_at._asset_contributions.ContributionInputs`
      to :func:`app.services.balance_at._kernel.build_account_balance_map`,
      which since plan step X-g2b dispatches on nothing at all -- every non-loan
      kind is ONE event replay (ruling R-AD).

    **The loan gate is the seam's ONE predicate** (plan step X-g3b-0),
    :func:`._resolution.configured_loan`.  It used to be ``account.id in
    inputs.debt_schedules`` -- membership in a map of resolved amortizations
    this module assembled and then discarded the values of -- which was a second
    spelling of what the scalar wrote out longhand and a third of what the
    liability band decomposed into two guard clauses.  The three were equivalent
    by an argument recorded in a docstring rather than by construction: the
    schedule map is built from the AMORTIZING subset alone, so membership in it
    IS ``AMORTIZING and resolved_loan(...) is not None``.  Asking one named
    predicate costs nothing -- the resolution is memoized on the pass either
    way, and ``positions_period_map`` re-derives it regardless -- and it is the
    difference between an equivalence a reader must re-derive and one the code
    cannot lose.

    It takes no live override map (ruling R-Q, plan step X-c2b2): the cash fold
    builds its own over its own plan, so there is no basis left for a caller to
    choose -- and the asymmetry that made the choice load-bearing (the plain
    path auto-built a live map from ``None`` while the interest path used
    stored amounts) is gone with it.

    Args:
        account: The account to project.
        ctx: The read pass's :class:`~app.services.balance_at.BalanceContext`,
            whose ``reported_periods()`` is the output domain (plan step
            C2-c: it was an argument, and all eight callers filled it with the
            same value).
        inputs: The account's
            :class:`~app.services.balance_at._asset_contributions.ContributionInputs`
            (:func:`_contribution_inputs_for_account` for a single read, or the batch
            map's entry).  Consumed by the replay arm only -- a configured loan
            models no contribution.

    Returns:
        The OrderedDict period_id -> Decimal balance.  **Never ``None``**: it
        answered ``None`` for an account with ``current_anchor_period_id IS
        NULL``, a state the schema forbade and the column no longer exists to
        express (finding N-73, plan step X-f1c3a), so the arm reproduced a
        contract nothing could satisfy and made every consumer carry a
        ``balances is None`` branch for it.
    """
    # A Mortgage-typed account with no LoanParams is NOT a configured loan, so
    # it falls through to the replay here rather than reaching positions()'s
    # fail-loud for an unconfigured loan.  Both halves of that rule live in the
    # predicate; see its docstring for why neither implies the other.
    if configured_loan(account, ctx) is not None:
        return positions_period_map(account, ctx)
    return _kernel.build_account_balance_map(account, ctx, inputs)


# The seam's fail-loud no-baseline guard.  It lives on the context (the object
# that OWNS the scenario) rather than here, and is re-exported so every seam
# entry keeps calling ``_require_scenario(ctx)`` under one name; see
# :func:`app.services.balance_at.require_scenario` for the contract and
# the one entry (``liability_owed_at_dates``) that deliberately skips it.
_require_scenario = require_scenario
