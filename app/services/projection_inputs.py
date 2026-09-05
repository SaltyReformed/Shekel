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

**That boundary is why :func:`load_payroll_feeds` is here** (plan step
**salary:R14-b**).  Pricing an account's payroll feed means running the
paycheck engine, which means resolving a profile and its tax configs, which
means a session -- so the PRICING happens at this loader and the pure module
receives a finished
:class:`~app.services.investment_projection.AccountPayrollFeed`.  It is the
same split ``PricedContribution`` and ``ShadowContributions`` already sit on,
applied to the third and last input that was still arriving raw.
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
from app.services import income_service
from app.services.cash_ledger import AmountBasis, contributions_by_id
from app.services.investment_projection import (
    AccountPayrollFeed,
    InvestmentInputs,
    PricedContribution,
    ShadowContributions,
    calculate_investment_inputs,
)
from app.services.pay_calendar import PayCalendar
from app.utils.money import ZERO
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
    used by the investment-detail dashboard, which renders one account at a
    time -- for the contribution PROMPT it shows, which asks whether any
    deduction funds this account at all.  The dollars come off
    :func:`load_payroll_feeds` since plan step **salary:R14-b**; these rows
    are read for their existence, not for their amounts.

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
            # The BASIS's scenario, and closing finding **N-271** is what this
            # line is (plan step X-au-c2b, after an adversarial review).  The
            # query scoped by account, transfer, income type, period and soft
            # delete only, so one batch could straddle scenarios while every
            # row in it was priced against a single baseline basis.  That was
            # `$0.00` while every row is OWN and becomes a wrong figure at the
            # first cutover that makes a contribution shadow derived.  Scoping
            # the query is the remedy the row named, and it is the one that
            # keeps this batch's rows and its pricing in agreement by
            # construction rather than by the caller's care.
            Transaction.scenario_id == basis.scenario_id,
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


def load_payroll_feeds(
    user_id: int,
    calendar: "PayCalendar",
    account_ids: "list[int]",
    params_by_account: "dict[int, InvestmentParams]",
    breakdowns: "dict[int, dict] | None" = None,
) -> "dict[int, AccountPayrollFeed]":
    """Price each account's payroll feed through the PAYCHECK ENGINE.

    **The producer plan step salary:R14-b puts in place of the feed's own
    arithmetic** (ruling **R-SAL2**).  What a payroll deduction takes from a
    paycheck, and what gross an employer contribution is a percentage of, are
    both facts the paycheck engine establishes when it prices the paycheck.
    This runs :func:`~app.services.income_service.project_profile` -- the ONE
    spelling of a profile's projection since ``salary:R14-a`` -- once per
    profile that funds any of these accounts, and folds the resulting
    :class:`~app.services.paycheck_calculator.DeductionLine`\\ s by the
    ``target_account_id`` they already carry.

    It answers R-SAL2's three questions at their source rather than
    re-deriving any of them:

    * **WHOSE salary** -- each deduction is priced inside its OWN profile's
      paycheck, because that is the profile whose deductions the engine walked.
      A two-job owner's two profiles are two projections, and no reader picks
      between them.
    * **WHICH gross** -- the paycheck's own, raises applied as of its payday.
    * **WHICH clock** -- the period's, never ``date.today()``.  Nothing here
      reads a clock; the CALENDAR is the domain.

    **The profiles are NAMED, never searched**, which is what retires
    ``income_service.get_current_gross_biweekly`` rather than re-pointing it.
    That helper resolved a profile with an unordered ``.first()`` across the
    owner's active profiles -- a measured **39%** swing on a two-job owner,
    flipping between renders -- and answered ``$0.00`` whenever no period
    covered today, which silently deleted the whole contribution plan at
    onboarding and after a horizon lapse.  Here the employee half's profile is
    the one the DEDUCTION belongs to and the employer half's is the one
    ``budget.investment_params.salary_profile_id`` names (**R-SAL5**), so
    there is no search to be non-deterministic about and no clock to answer
    zero against.

    **An unknown funding profile models NO employer money** (developer,
    2026-09-04): the account's ``gross_by_payday`` is empty, which is what
    :attr:`~app.services.investment_projection.AccountPayrollFeed.funds_employer`
    reports and what the surfaces render as *the funding job is not set*.
    Unknown covers three states and they are one answer: the column is
    ``NULL``, it names a profile the owner has ARCHIVED (an employer
    contribution from a job they have left is not money they receive), or it
    names a profile that is not theirs -- the forged FK ``salary:R14-a``
    closed at the write door and this scopes against a second time, because a
    read that trusts a column's ownership is a read that can be made to price
    a stranger's salary.

    Args:
        user_id: The owner these accounts and profiles belong to.  Every
            profile query here is scoped by it.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`.
            Its saved window is the domain every priced payday comes from; a
            projection past it is the feed's hold rule, not this loader's.
        account_ids: The accounts to price a feed for.  An empty list returns
            an empty map without issuing a query.
        params_by_account: ``{account_id: InvestmentParams}`` from
            :func:`load_investment_params_for_accounts`, read for the
            ``salary_profile_id`` that funds each employer contribution.
        breakdowns: An optional ``{profile_id: {payday: PaycheckBreakdown}}``
            memo the caller owns, filled here for a profile it does not yet
            hold.  **Running the engine is the expensive half of this
            function** -- a projection walks the owner's whole saved window
            and each paycheck replays the year's prior paydays for its FICA
            and annual-cap cumulatives -- and the balance seam asks for a feed
            once per ACCOUNT, so without a memo the engine re-ran the same
            profile once per account per entry.  ``None`` means "no memo",
            which is right for the two route callers: each asks once per
            render.

    Returns:
        ``{account_id: AccountPayrollFeed}``, TOTAL over *account_ids* -- an
        account no payroll funds maps to
        :meth:`~app.services.investment_projection.AccountPayrollFeed.absent`'s
        value rather than being absent, so a caller indexes rather than
        defaulting and a missing key is a defect instead of a silently
        unfunded account.
    """
    if not account_ids:
        return {}

    deductions_by_account = load_active_deductions_for_accounts(
        user_id, account_ids,
    )
    # Every profile that funds any of these accounts, from BOTH sides: the
    # profile each active deduction belongs to, and the one each account's
    # params name.  Named rather than searched -- see the docstring.
    wanted = {
        ded.salary_profile_id
        for rows in deductions_by_account.values() for ded in rows
    }
    wanted.update(
        params.salary_profile_id
        for account_id in account_ids
        if (params := params_by_account.get(account_id)) is not None
        and params.salary_profile_id is not None
    )
    profiles = _load_funding_profiles(user_id, wanted)

    paydays = [period.start_date for period in calendar.saved()]
    # KEYED ON THE BREAKDOWN'S OWN PERIOD ID, not paired with ``paydays`` by
    # position.  ``PaycheckBreakdown.period`` is a
    # :class:`~app.services.paycheck_calculator.PeriodInfo` carrying the
    # ``budget.pay_periods.id`` the paycheck was priced for -- structurally
    # never ``None`` for a projection over the SAVED window -- so the payday
    # is looked up rather than inferred from ordering.  A ``zip`` against
    # ``calendar.saved()`` gives the same answer today and is a maintenance
    # contract between two producers rather than a key, which is the shape
    # ``CLAUDE.md`` rule 14 names; it would also truncate silently if the two
    # ever differed in length.
    payday_by_period_id = {
        period.period_id: period.start_date for period in calendar.saved()
    }
    breakdowns_by_profile = {} if breakdowns is None else breakdowns
    for profile_id, profile in profiles.items():
        if profile_id not in breakdowns_by_profile:
            breakdowns_by_profile[profile_id] = {
                payday_by_period_id[breakdown.period.period_id]: breakdown
                for breakdown in income_service.project_profile(
                    profile, calendar,
                )
            }
    return {
        account_id: AccountPayrollFeed(
            employee_by_payday=_employee_by_payday(
                account_id, deductions_by_account.get(account_id, []),
                breakdowns_by_profile, paydays,
            ),
            gross_by_payday=_gross_by_payday(
                params_by_account.get(account_id), breakdowns_by_profile,
            ),
            # PRESENCE, beside the amounts and never derived from them: an
            # account funded by a $0.00 deduction is still WIRED UP, and
            # ``/retirement``'s prompt asks that question rather than a
            # dollar one.
            is_payroll_linked=bool(deductions_by_account.get(account_id)),
        )
        for account_id in account_ids
    }


def _load_funding_profiles(
    user_id: int, profile_ids: "set[int]",
) -> "dict[int, SalaryProfile]":
    """Return the ACTIVE salary profiles among *profile_ids* this owner holds.

    The one place the three ways a funding profile can be unknown collapse
    into one answer (plan step **salary:R14-b**): the id is absent, the
    profile is archived, or the profile belongs to someone else.  Filtering
    here rather than at each reader is what makes "no funding profile" a
    single state the feed can report, instead of three branches each caller
    would have to remember.

    Args:
        user_id: The owner.  Scopes the query, so a ``salary_profile_id``
            pointing at a stranger's profile resolves to nothing.
        profile_ids: The profile ids named by the deductions and the accounts'
            params.  Empty returns an empty map without a query.

    Returns:
        ``{profile_id: SalaryProfile}`` for the ids that are this owner's and
        active, with the relationships the paycheck engine reads eager-loaded.
    """
    if not profile_ids:
        return {}
    rows = (
        db.session.query(SalaryProfile)
        .options(
            subqueryload(SalaryProfile.raises),
            subqueryload(SalaryProfile.deductions),
        )
        .filter(
            SalaryProfile.id.in_(profile_ids),
            SalaryProfile.user_id == user_id,
            SalaryProfile.is_active.is_(True),
        )
        .all()
    )
    return {profile.id: profile for profile in rows}


def _employee_by_payday(
    account_id: int,
    deductions: "list[PaycheckDeduction]",
    breakdowns_by_profile: dict,
    paydays: "list[date]",
) -> "dict[date, Decimal]":
    """Fold the engine's deduction lines for ONE account, per payday.

    Reads the amount off the
    :class:`~app.services.paycheck_calculator.DeductionLine` the engine
    already priced -- raise-aware, inflation-escalated, cadence-placed and
    clamped to the line's own calendar-year cap -- rather than pricing
    anything here.  Pre- and post-tax lines both count: what an account
    RECEIVES does not depend on which side of the tax line the deduction sits.

    Args:
        account_id: The account whose lines to keep.
        deductions: The account's active deductions, read only for WHICH
            profiles fund it; the amounts come off the breakdowns.
        breakdowns_by_profile: ``{profile_id: {payday: PaycheckBreakdown}}``.
        paydays: Every payday the calendar reaches, so the map is TOTAL and a
            cadence skip is an explicit ``$0.00`` rather than a gap.

    Returns:
        ``{payday: Decimal}`` over every payday, or ``{}`` when no active
        profile of this owner's funds the account.
    """
    # DISTINCT profiles, keyed by id: an account funded by three of one
    # profile's deductions must read that profile's breakdown ONCE, or every
    # line on it would be counted as many times as the account has
    # deductions.  The lines themselves are then filtered by
    # ``target_account_id`` below, which is what keeps a sibling deduction
    # feeding a DIFFERENT account out of this sum.
    # No membership guard: ``_active_deductions_query`` already joins each
    # deduction to an ACTIVE profile of this owner, and
    # :func:`_load_funding_profiles` applies exactly those two filters over a
    # SUPERSET of these ids -- so a deduction whose profile is missing from
    # the map is not a state either query can produce.  A guard here would be
    # one that cannot fire, which ``CLAUDE.md`` rule 1 forbids shipping.
    funding_ids = {ded.salary_profile_id for ded in deductions}
    if not funding_ids:
        return {}
    funding = [breakdowns_by_profile[pid] for pid in funding_ids]
    return {
        payday: sum(
            (
                line.amount
                for by_payday in funding
                if (breakdown := by_payday.get(payday)) is not None
                for line in (breakdown.deductions.pre_tax
                             + breakdown.deductions.post_tax)
                if line.target_account_id == account_id
            ),
            ZERO,
        )
        for payday in paydays
    }


def _gross_by_payday(
    params: "InvestmentParams | None", breakdowns_by_profile: dict,
) -> "dict[date, Decimal]":
    """Return the FUNDING profile's per-payday gross, or an empty map.

    The employer contribution's basis (**R-SAL5**): the gross of the paycheck
    the profile named by ``budget.investment_params.salary_profile_id`` was
    paid on each payday.  Empty when that profile is unknown -- absent,
    archived, or not this owner's, the three states
    :func:`_load_funding_profiles` has already collapsed into "not in the
    map" -- which is the developer's 2026-09-04 ruling that such an account
    models no employer money at all.

    Args:
        params: The account's :class:`InvestmentParams`, or ``None``.
        breakdowns_by_profile: ``{profile_id: {payday: PaycheckBreakdown}}``.

    Returns:
        ``{payday: Decimal}`` gross per payday, or ``{}``.
    """
    profile_id = getattr(params, "salary_profile_id", None)
    by_payday = breakdowns_by_profile.get(profile_id)
    if by_payday is None:
        return {}
    return {
        payday: breakdown.earnings.gross_biweekly
        for payday, breakdown in by_payday.items()
    }


def build_investment_projection_inputs(
    params: InvestmentParams,
    feed: "AccountPayrollFeed",
    contributions: list,
    current_period,
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

    Callers supply the ``feed`` (from :func:`load_payroll_feeds`) and
    ``contributions`` because the per-consumer contribution-loading
    queries differ in scenario / status filters (savings + year-end
    apply ``balance_excluded_status_ids`` + scenario scoping;
    investment dashboard does not).  Forcing a one-size query inside
    this helper would silently change the per-period contribution
    average those consumers compute; passing pre-loaded data
    preserves each surface's existing filter contract.

    **A verification gate stated here matched NOTHING and was deleted** (plan
    step C2-f3a, ledger row **P52**); the lesson is kept because it is about
    gates and not about the argument that has since gone.  It named a ``grep``
    for the kwarg-self-binding splat below as a duplicate-canary and concluded
    "the gate passes when only this helper site has it".  ``grep`` is
    LINE-based and the one call site carrying the pattern closed on the NEXT
    line, so the expression matched zero lines: the gate passed because
    nothing matched, which is indistinguishable from passing because one thing
    matched -- a safety that is not a predicate, which
    ``docs/plans/conventions.md`` says is worse than no safety at all.  The
    duplication it claimed to police IS real -- the splat sat in four services
    before Commit 18 -- and it is really policed, by pylint's
    ``duplicate-code``, which CI enforces as a hard gate.

    Args:
        params: :class:`InvestmentParams` row for the account.
        feed: The account's
            :class:`~app.services.investment_projection.AccountPayrollFeed`
            from :func:`load_payroll_feeds` -- what its payroll puts in per
            payday, priced by the paycheck engine.
        contributions: List of
            :class:`~app.services.investment_projection.PricedContribution`
            records already filtered to this account.
        current_period: The
            :class:`~app.services.pay_calendar.DerivedPeriod` covering the read
            pass's clock, or ``None``.  **Both callers pass that type** since
            pay-calendar plan step C2-f2d-3; this said "an ORM ``PayPeriod`` on
            ``/retirement``, a ``DerivedPeriod`` on ``/investment``" until then,
            and only ``start_date`` is read either way.

    Returns:
        :class:`InvestmentInputs` carrying the current period's contribution,
        employer params, annual contribution limit and YTD contributions the
        growth engine and the per-period cards need.
    """
    return calculate_investment_inputs(
        investment_params=params,
        feed=feed,
        all_contributions=contributions,
        current_period=current_period,
    )


# Public API -- re-exported types for callers that only import from
# this module so they do not also need to reach into
# ``app.services.investment_projection`` for the DTO.
__all__ = [
    "Account",
    "AccountPayrollFeed",
    "InvestmentInputs",
    "InvestmentParams",
    "build_investment_projection_inputs",
    "load_active_deductions_for_account",
    "load_active_deductions_for_accounts",
    "load_investment_params_for_accounts",
    "load_payroll_feeds",
    "load_shadow_income_contributions_for_account",
    "load_shadow_income_contributions_for_accounts",
]
