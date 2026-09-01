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
direction is ``<view module> -> _inputs``.  ``_inputs`` in turn depends on two
groups and nothing else: the outer LOADERS it issues its queries through
(:mod:`app.services.projection_inputs`, :mod:`app.services.income_service`), and
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
from app.services import income_service
from app.services.investment_projection import adapt_deductions
from app.services.projection_inputs import (
    load_active_deductions_for_accounts,
    load_investment_params_for_accounts,
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
    investment-params query, one deductions query, and one raise-aware gross
    fetch for the whole set.

    The three loaders are the shared building blocks the savings cockpit's
    ``_load_account_params`` and the year-end summary already use -- this seam
    reuses them rather than writing new inline param queries.

    **It TAKES a** :class:`~app.services.balance_at.BalanceContext`, and for
    exactly one thing: the memoized pay CALENDAR it hands
    :func:`~app.services.income_service.get_current_gross_biweekly`.  It took
    none until pay-calendar plan step C2-f2d-3's fix pass, on the argument that
    "an argument nothing reads is one a caller can get wrong" -- true while that
    helper read the schedule through its own SQL, and false the moment it began
    DERIVING a calendar, because this entry is called once per ACCOUNT and each
    call then derived the owner's whole schedule again.  Measured on the routes:
    `/savings` went 1 -> 7 derivations a render before the argument was
    threaded, growing with the owner's investment-account count.
    **The CLOCK and the SCENARIO are still not threaded**, which is the part of
    the old paragraph that survives: that helper's ``as_of`` and ``scenario_id``
    keywords stay unpassed, so the gross resolves against an implicit
    ``date.today()`` and the owner's first active profile across all scenarios
    rather than against ``ctx.as_of`` and ``ctx.scenario`` -- the unnamed-clock
    shape :func:`._kind_correct.balance_at` describes in its own "two dates,
    deliberately distinct" note.  Passing them MOVES MONEY on a historical read,
    so it is ``C2-f3``'s to rule rather than a fix pass's to smuggle.

    Args:
        accounts: The accounts to load feeds for, each with its
            ``account_type`` relationship available for the classifier.  They
            belong to ONE user (the caller's).  An empty list returns an empty
            map without issuing any query.
        ctx: The read pass, for its memoized pay CALENDAR alone -- the one
            input :func:`~app.services.income_service.get_current_gross_biweekly`
            needs and would otherwise derive again, once per call and therefore
            once per ACCOUNT.  Its clock and its scenario are deliberately NOT
            threaded; see above.

    Returns:
        ``{account_id: ContributionInputs}``, TOTAL over *accounts* -- an
        account with no feed maps to
        :meth:`~app.services.balance_at._asset_contributions.ContributionInputs.absent`'s
        value rather than being absent, so a caller indexes rather than
        defaulting and a missing key is a defect instead of a silently unfunded
        account.  One account's feed is the same whoever it is loaded beside
        (see the gross's per-account arm below), so batching is an optimisation
        and never a difference in the answer.
    """
    if not accounts:
        return {}

    # Every account in the set is owned by one user (the caller's), so the
    # user id for the deductions / gross loaders comes off any of them.
    user_id = accounts[0].user_id

    # The shared loader owns the canonical-classifier filter, so a
    # parameterised physical asset (Property -> APPRECIATING) is correctly
    # excluded here rather than re-derived by elimination.
    investment_params_map = load_investment_params_for_accounts(accounts)

    # Deduction-scoping rule (mirrors savings ``_load_account_params``):
    # load deductions ONLY for the investment accounts that HAVE an
    # InvestmentParams row.  ``_asset_contributions.contribution_events`` models
    # a feed ONLY for an INVESTMENT account whose ``investment_params`` is not
    # None, so deductions for a params-less account are never consumed --
    # scoping to the params map's keys is the canonical rule that keeps this
    # seam, savings, and year-end in agreement on which accounts get a
    # deduction feed.
    deductions_by_account = (
        load_active_deductions_for_accounts(
            user_id, list(investment_params_map.keys()),
        ) if investment_params_map else {}
    )

    # Same investment-only scoping as the deductions above: the gross is the
    # employer-match cap basis the contribution tier consumes ONLY for an
    # account with investment params, so a set with no investment account never
    # reads it.  Skipping the paycheck-engine fetch there keeps a single-account
    # read for a cash / interest / loan account free of the engine run (the
    # value would be unused), so routing those reads through the seam stays as
    # cheap as a direct producer call -- no O(N) paycheck regression in the
    # year-end savings-progress loop.
    salary_gross_biweekly = (
        income_service.get_current_gross_biweekly(user_id, ctx.calendar())
        if investment_params_map else ZERO
    )

    # The gross reaches only the accounts that can CONSUME one, and that arm is
    # what makes the Returns contract above true by construction rather than by
    # a downstream gate.  The fetch is scoped to the SET (it is skipped entirely
    # when no account has params), so handing the set's gross to every member
    # would make a non-investment's feed depend on which OTHER accounts shared
    # its read: ``_contribution_inputs_for_account(checking)`` would carry
    # ``0`` while ``_contribution_inputs_for_accounts([checking, roth])`` gave
    # the same account the user's real gross.  Nothing reads it there -- the
    # contribution tier short-circuits on kind and params first
    # (``_asset_contributions.contribution_events``) -- so today the difference
    # is invisible, which is exactly why it must not be left to a docstring:
    # the caller-dependent input is the shape this loader exists to rule out.
    # ADAPTED here, at the ORM boundary, rather than at the point of use
    # (plan step R-F16).  The adapter needs the owner's paycheck count -- a
    # calendar-year deduction cap is spread across the year's paychecks -- and
    # this loader is where a pass with a memoized calendar is in scope; the
    # consumer below the seam is pure.
    #
    # **Asked only when it can be ANSWERED, and that is not a nicety.**
    # ``PayCalendar.cadence`` REFUSES an owner who has never stated one, and
    # this loader is on the SEAM -- the grid, /savings and /investments all
    # reach it -- so resolving it unconditionally would turn a rendering page
    # into a 500 for that owner.  It costs no figure to skip: an owner with no
    # resolvable cadence has no ``budget.pay_schedule`` row and therefore no
    # pay period either -- one fact rather than two since plan step C4-b-2,
    # where it took ``fk_pay_periods_schedule`` to make it so and
    # ``resolve_cadence`` used to infer a cadence off the last period instead.
    # So there is no period for a per-period
    # contribution to be modelled over and the fold reads nothing here either
    # way.  Measured at R-F16: the unconditional form raised
    # ``PayCalendarError`` for exactly this owner where the pre-step code
    # returned a balance.
    #
    # The comprehension iterates every key rather than filtering on a truthy
    # row list, because ``load_active_deductions_for_accounts`` builds its map
    # with ``setdefault(...).append(...)`` -- no key is ever empty, so a
    # ``if rows`` clause here would be a guard that cannot fire.  The empty
    # case is the ``{}`` above it, which is reached when the owner has no
    # investment account (the deduction query is scoped to that map's keys).
    adapted_by_account = {
        acct_id: adapt_deductions(rows, ctx.calendar().cadence)
        for acct_id, rows in deductions_by_account.items()
    } if ctx.calendar().cadence_days is not None else {}
    return {
        account.id: ContributionInputs(
            investment_params=investment_params_map.get(account.id),
            deductions=adapted_by_account.get(account.id, []),
            salary_gross_biweekly=(
                salary_gross_biweekly
                if account.id in investment_params_map else ZERO
            ),
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
            the classifier and its ``user_id`` scopes the deduction / gross
            loaders.
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
