"""
Shekel Budget App -- Shared Investment-Projection Inputs (F-22 / Commit 18).

Single home for the deduction-loader query and the
:func:`calculate_investment_inputs` kwargs splat that were duplicated
across the investment / retirement / savings / year-end consumers
pre-Commit-18.  The duplicates triggered pylint R0801 (similar-lines)
and -- more importantly -- meant the engine-input contract was defined
in four places at once, any one of which could drift independently.

Boundary discipline (``CLAUDE.md``: "services are isolated from Flask"):
this module imports no Flask symbol.  All inputs are plain data (user
id, account id, ORM model instances already loaded by the caller); the
return values are ORM lists, plain dicts, and the existing
:class:`~app.services.investment_projection.InvestmentInputs` DTO.

The deductions-loader and the projection-inputs wrapper live here
rather than in :mod:`app.services.investment_projection` because that
module's module-level docstring promises "no database access" -- the
contract Commit 28 / S6-01 set up so pure-data tests can construct
FakeDeduction / FakeContribution objects without a DB.  Placing the
DB-touching helpers in a sibling module preserves that boundary.
"""

import logging

from sqlalchemy.orm import joinedload, subqueryload

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.account import Account
from app.models.investment_params import InvestmentParams
from app.models.pay_period import PayPeriod
from app.models.paycheck_deduction import PaycheckDeduction
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.services.cash_ledger import AmountBasis, contributions_by_id
from app.services.investment_projection import (
    InvestmentInputs,
    PricedContribution,
    ShadowContributions,
    calculate_investment_inputs,
)
from app.utils.balance_predicates import status_contributes_to_balance

logger = logging.getLogger(__name__)


def load_active_salary_profiles(
    user_id: int, scenario_id: int,
) -> list[SalaryProfile]:
    """Return a user's active salary profiles for a scenario, primary first.

    The single home for the "active profiles in this scenario, raises and
    deductions eager-loaded" query the year-end summary
    (:mod:`app.services.year_end_summary_service._data`) and the analytics
    Taxes report (:mod:`app.services.tax_report_service`) both drive -- an
    R0801 duplicate otherwise, the same consolidation rationale as the
    deduction loaders below.  Ordered by ``(sort_order, name)`` so
    ``result[0]`` is the PRIMARY profile (the salary cockpit's
    default-profile rule the Taxes report relies on); the year-end summary,
    an order-independent sum across the profiles, is unaffected by the
    ordering.

    Args:
        user_id: The owning user.
        scenario_id: The scenario to scope the profiles to.

    Returns:
        The active :class:`~app.models.salary_profile.SalaryProfile` list,
        ordered ``(sort_order, name)`` with ``raises`` and ``deductions``
        eager-loaded.
    """
    return (
        db.session.query(SalaryProfile)
        .options(
            subqueryload(SalaryProfile.raises),
            subqueryload(SalaryProfile.deductions),
        )
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.scenario_id == scenario_id,
            SalaryProfile.is_active.is_(True),
        )
        .order_by(SalaryProfile.sort_order, SalaryProfile.name)
        .all()
    )


def load_active_accounts_with_types(user_id: int) -> list[Account]:
    """Return a user's active accounts with their ``account_type`` joined.

    The single home for the "active accounts, account_type eager-loaded"
    query the year-end summary and the analytics Taxes report (its Schedule
    A debt-account selection) both drive; joining the type avoids an N+1
    when the callers read ``account.account_type`` to classify each row
    (amortizing / interest-bearing / savings).

    Args:
        user_id: The owning user.

    Returns:
        The active :class:`~app.models.account.Account` list with
        ``account_type`` joined.
    """
    return (
        db.session.query(Account)
        .options(joinedload(Account.account_type))
        .filter(
            Account.user_id == user_id,
            Account.is_active.is_(True),
        )
        .all()
    )


def load_active_deductions_for_account(
    user_id: int, account_id: int,
) -> list[PaycheckDeduction]:
    """Return active paycheck deductions targeting a single account.

    The single-account variant of :func:`load_active_deductions_for_accounts`
    used by the investment-detail dashboard, which renders one account
    at a time.  Returned rows have their ``salary_profile`` relationship
    eagerly available via the join filter for downstream
    :func:`~app.services.investment_projection.adapt_deductions`
    consumption.

    Args:
        user_id: ID of the authenticated user (scopes via
            ``SalaryProfile.user_id``).
        account_id: ID of the investment / retirement account the
            deductions target.

    Returns:
        A list of :class:`PaycheckDeduction` rows (possibly empty).
    """
    return _active_deductions_query(user_id, [account_id]).all()


def load_active_deductions_for_accounts(
    user_id: int, account_ids: list[int],
) -> dict[int, list[PaycheckDeduction]]:
    """Return active paycheck deductions keyed by target account id.

    Batch variant used by the savings / retirement / year-end services
    when they classify many accounts in one pass and need O(1) lookup
    by account id inside a per-account loop.  Pre-Commit-18 the three
    consumers each issued the same query with their own local
    ``account_ids`` list; centralising it removes the R0801 duplicate
    and makes the active-deduction filter shape a single point of
    truth.

    Args:
        user_id: ID of the authenticated user (scopes via
            ``SalaryProfile.user_id``).
        account_ids: List of target account ids.  Empty list returns
            an empty dict without issuing a query, so callers do not
            need to guard ``IN ()`` against PostgreSQL.

    Returns:
        Dict mapping ``target_account_id`` -> list of
        :class:`PaycheckDeduction`.  Accounts with no deductions are
        absent from the dict; callers should use ``dict.get(id, [])``.
    """
    if not account_ids:
        return {}
    grouped: dict[int, list[PaycheckDeduction]] = {}
    for ded in _active_deductions_query(user_id, account_ids).all():
        grouped.setdefault(ded.target_account_id, []).append(ded)
    return grouped


def _active_deductions_query(user_id: int, account_ids: list[int]):
    """Build the canonical active-deductions query.

    Owns the filter shape duplicated three times pre-Commit-18:
    ``SalaryProfile.user_id == user_id``,
    ``SalaryProfile.is_active.is_(True)``,
    ``PaycheckDeduction.target_account_id.in_(...)``, and
    ``PaycheckDeduction.is_active.is_(True)``.  ``.in_(...)`` works
    for both single-id and multi-id call sites, so both public
    loaders route through this builder.

    Args:
        user_id: ID of the authenticated user.
        account_ids: Non-empty list of target account ids.

    Returns:
        A SQLAlchemy ``Query`` object; the caller decides ``.all()``
        vs ``.scalar()`` etc.
    """
    return (
        db.session.query(PaycheckDeduction)
        .join(SalaryProfile)
        .filter(
            SalaryProfile.user_id == user_id,
            SalaryProfile.is_active.is_(True),
            PaycheckDeduction.target_account_id.in_(account_ids),
            PaycheckDeduction.is_active.is_(True),
        )
    )


def load_investment_params_for_accounts(
    accounts: list[Account],
) -> dict[int, InvestmentParams]:
    """Return :class:`InvestmentParams` keyed by id for INVESTMENT accounts.

    The single home for the investment-params batch load: the savings
    dashboard's :func:`_load_account_params` built this map inline pre-seam,
    and the forthcoming ``balance_at`` seam (Level 1 of the
    balance-architecture work) shares this loader so the "which accounts
    get an InvestmentParams row?" decision lives in exactly one place
    instead of being re-derived per surface.

    Membership is decided by the canonical classifier
    (:func:`app.services.account_projection.classify_account`), never by
    elimination: only accounts the classifier marks
    :data:`~app.services.account_projection.AccountProjectionKind.INVESTMENT`
    are loaded.  This deliberately excludes a parameterised physical
    asset -- a Property classifies as
    :data:`~app.services.account_projection.AccountProjectionKind.APPRECIATING`
    and carries its own params, so it must not be pulled in here.  An
    account whose ``account_type`` is unloaded / ``None`` classifies as
    PLAIN and is skipped.

    Args:
        accounts: Account model instances to scope to, each with its
            ``account_type`` relationship available for the classifier
            (the consumer is expected to have loaded it; the classifier
            issues no queries).  An empty list -- or a list containing
            no INVESTMENT accounts -- returns an empty dict without
            issuing an ``IN ()`` query against PostgreSQL.

    Returns:
        Dict mapping ``account_id`` -> :class:`InvestmentParams`.
        INVESTMENT accounts that have no params row are absent from the
        dict; callers should use ``dict.get(id)``.
    """
    inv_account_ids = [
        a.id for a in accounts
        if classify_account(a) is AccountProjectionKind.INVESTMENT
    ]
    if not inv_account_ids:
        return {}
    params_map: dict[int, InvestmentParams] = {}
    for ip in (
        db.session.query(InvestmentParams)
        .filter(InvestmentParams.account_id.in_(inv_account_ids))
        .all()
    ):
        params_map[ip.account_id] = ip
    return params_map


def load_shadow_income_contributions_for_accounts(
    basis: AmountBasis,
    account_ids: list[int], period_ids: list[int],
) -> ShadowContributions:
    """Return PRICED shadow-income contributions across many accounts.

    Batch variant used by services that classify many accounts in one
    pass.  Returned records carry their original ``account_id`` so callers
    can group / partition downstream.  Returns an empty list when
    either ``account_ids`` or ``period_ids`` is empty so callers do
    not issue ``IN ()`` queries against PostgreSQL.

    **This is the BOUNDARY where a contribution is valued** (plan step
    X-au-c2, a developer ruling of 2026-08-12).  It used to return ORM rows and
    four readers in :mod:`app.services.investment_projection` each asked them
    for ``effective_amount`` behind its own copy of the
    ``status_contributes_to_balance`` screen.  That property cannot answer for
    a row whose amount is DERIVED -- such a row stores no figure -- and a module
    whose docstring promises no database access can never resolve one.  So the
    resolution happens HERE, where the session is: ONE
    :func:`~app.services.cash_ledger.contributions_by_id` call over the whole
    cross-account row set, which is also one paycheck-engine run rather than
    one per account (finding **N-228**, and what re-keying the basis on the
    OWNER rather than an ``Account`` bought).

    **It is also where a contribution is DATED, since plan step C2-f2c**, and
    that is the same argument applied to the same record's other derived fact.
    Every reader downstream buckets contributions by pay period and then needs
    that period's PAYDAY -- the YTD windows to compare it against the current
    period's, the timeline to stamp it on a
    :class:`~app.services.growth_engine.ContributionRecord`.  Carrying the id
    alone made each of them take the owner's whole period list as a lookup
    table, so three public signatures held a join this query can do in one
    ``JOIN``.  ``PayPeriod.start_date`` is the paydays' own column and the one
    plan step **C4** keeps, so this reads a fact rather than a derivation; the
    join is INNER, which drops nothing, because the filter below already
    excludes a ``NULL`` ``pay_period_id``.

    **Rows that contribute nothing are DROPPED rather than priced at zero.**
    :func:`~app.services.investment_projection._average_transfer_contribution`
    divides by the number of distinct pay periods it sees, so a Cancelled
    contribution carried through as ``$0.00`` would enlarge that denominator and
    silently lower the average.  The screen is applied before the pricing for
    the same reason the valuation gates before it resolves: an excluded row has
    no derived answer to give.

    The ``eager_status`` switch is gone with them.  It defaulted to ``False``
    while every consumer needed the status, so the retirement chain lazy-loaded
    it per row; the status is now read exactly once here, under a ``joinedload``
    that is no longer optional.

    Args:
        basis: The read pass's
            :class:`~app.services.cash_ledger.AmountBasis` -- the owner and the
            scenario these amounts resolve under, and the derivations they
            resolve through.  Taken rather than built here since plan step
            X-au-c2b, so a caller that also prices its own rows pays for the
            paycheck engine once (findings **N-268**, **N-269**).
        account_ids: Investment / retirement account ids to scope to.
        period_ids: Pay-period ids to scope the contribution window
            against.

    Returns:
        A :class:`~app.services.investment_projection.ShadowContributions` --
        the priced ``records`` (callers partition by ``account_id``
        themselves, typically a comprehension inside a per-account loop) and
        the ``linked_account_ids`` of every account that had a contribution
        shadow WHATEVER its status.  The second field is not decoration: an
        adversarial review found that screening the records alone flipped
        ``retirement_projection``'s ``none_linked`` for an account whose
        contributions were all Cancelled, telling the owner to link a
        contribution that already exists.

    Raises:
        AmountUnresolvable: From the amount model, for a contribution whose
            rule cannot price it.  A refusal is never a fallback.
    """
    if not account_ids or not period_ids:
        return ShadowContributions(records=[], linked_account_ids=frozenset())
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    rows = (
        db.session.query(Transaction, PayPeriod.start_date)
        .join(PayPeriod, PayPeriod.id == Transaction.pay_period_id)
        .options(joinedload(Transaction.status))
        .filter(
            Transaction.account_id.in_(account_ids),
            Transaction.transfer_id.isnot(None),
            Transaction.transaction_type_id == income_type_id,
            Transaction.pay_period_id.in_(period_ids),
            Transaction.is_deleted.is_(False),
        )
        .all()
    )
    counted = [
        (row, payday) for row, payday in rows
        if status_contributes_to_balance(row)
    ]
    amounts = contributions_by_id([row for row, _ in counted], basis)
    return ShadowContributions(
        records=[
            PricedContribution(
                account_id=row.account_id,
                payday=payday,
                amount=amounts[row.id],
                is_confirmed=row.status.is_settled,
            )
            for row, payday in counted
        ],
        # Taken from the UNSCREENED rows: a Cancelled contribution counts
        # nothing but is still a LINK, and the consumer asking whether an
        # account has one is asking a different question from the consumers
        # that sum amounts.
        linked_account_ids=frozenset(row.account_id for row, _ in rows),
    )


def load_shadow_income_contributions_for_account(
    basis: AmountBasis,
    account_id: int, period_ids: list[int],
) -> ShadowContributions:
    """Return PRICED shadow-income contributions into a single account.

    Used by the investment-detail dashboard.  Filters to
    transfer-shadow income rows in the supplied period window so
    :func:`calculate_investment_inputs` can derive the YTD contribution
    total and the contribution timeline can layer historical receipts.
    Returns an empty list when ``period_ids`` is empty so callers do
    not issue an ``IN ()`` query against PostgreSQL.

    Args:
        basis: The read pass's amount basis (see the batch variant).
        account_id: ID of the investment / retirement account.
        period_ids: Pay-period ids to scope the contribution window
            against.

    Returns:
        A list of :class:`~app.services.investment_projection.PricedContribution`
        records (see the batch variant for what pricing at this boundary buys).
    """
    return load_shadow_income_contributions_for_accounts(
        basis, [account_id], period_ids,
    )


def build_investment_projection_inputs(
    params: InvestmentParams,
    deductions: list,
    contributions: list,
    current_period,
    salary_gross_biweekly,
) -> InvestmentInputs:
    """Build :class:`InvestmentInputs` for one account.

    The single home for the keyword splat into
    :func:`~app.services.investment_projection.calculate_investment_inputs`
    that was duplicated across the investment / retirement / savings /
    year-end services pre-Commit-18.  Centralising the splat removes
    the R0801 duplicate and means a future signature change to
    ``calculate_investment_inputs`` only needs to update one site.

    **The owner's period LIST left this signature at plan step C2-f2c**, and
    the ``too-many-arguments`` disable that justified six went with it.  The
    list served the wrapped function's two YTD windows alone, as a lookup from
    a contribution's pay period to that period's payday;
    :func:`load_shadow_income_contributions_for_accounts` dates each
    contribution now, so there is nothing left to look up.

    Callers supply ``deductions`` (already adapted via
    :func:`~app.services.investment_projection.adapt_deductions`) and
    ``contributions`` because the per-consumer contribution-loading
    queries differ in scenario / status filters (savings + year-end
    apply ``balance_excluded_status_ids`` + scenario scoping;
    investment dashboard does not).  Forcing a one-size query inside
    this helper would silently change the per-period contribution
    average those consumers compute; passing pre-loaded data
    preserves each surface's existing filter contract.

    Positional rather than keyword-only because the verification gate
    (`grep -nE "salary_gross_biweekly=salary_gross_biweekly,\\s*\\)"
    app/services/`) treats the kwarg-self-binding pattern as the
    duplicate-canary; positional consumer calls do not match the
    pattern, so the gate passes when only this helper site has it.

    Args:
        params: :class:`InvestmentParams` row for the account.
        deductions: List of adapted deduction objects
            (:class:`~app.services.investment_projection.AdaptedDeduction`
            or equivalent), already filtered to this account.
        contributions: List of
            :class:`~app.services.investment_projection.PricedContribution`
            records already filtered to this account.
        current_period: The pay period covering the read pass's clock, or
            ``None``.  Anything carrying a ``start_date``: an ORM
            :class:`~app.models.pay_period.PayPeriod` on ``/retirement``, a
            :class:`~app.services.pay_calendar.DerivedPeriod` on
            ``/investment``.
        salary_gross_biweekly: Raise-aware engine gross per pay period
            (typically from
            :func:`app.services.income_service.get_current_gross_biweekly`).

    Returns:
        :class:`InvestmentInputs` carrying the periodic contribution,
        employer params, annual contribution limit, YTD contributions,
        and engine gross-biweekly fields the growth engine needs.
    """
    return calculate_investment_inputs(
        investment_params=params,
        deductions=deductions,
        all_contributions=contributions,
        current_period=current_period,
        salary_gross_biweekly=salary_gross_biweekly,
    )


# Public API -- re-exported types for callers that only import from
# this module so they do not also need to reach into
# ``app.services.investment_projection`` for the DTO.
__all__ = [
    "Account",
    "InvestmentInputs",
    "InvestmentParams",
    "build_investment_projection_inputs",
    "load_active_deductions_for_account",
    "load_active_deductions_for_accounts",
    "load_investment_params_for_accounts",
    "load_shadow_income_contributions_for_account",
    "load_shadow_income_contributions_for_accounts",
]
