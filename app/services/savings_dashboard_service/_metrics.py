"""
Shekel Budget App -- Savings Dashboard: emergency-fund and debt metrics.

Average monthly expenses (the higher of recent settled expenses and the
committed-template floor), the aggregate debt summary and its DTI band,
the canonical current-period paycheck breakdown producer, and the liquid
balance sum that feeds the emergency fund.  No Flask imports.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import selectinload

from app import ref_cache
from app.enums import AcctTypeEnum, TxnTypeEnum
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import (
    escrow_calculator,
    obligations_aggregator,
    paycheck_calculator,
)
from app.services.paycheck_calculator import PayrollBasis
from app.services.savings_dashboard_service._debt_line import (
    LoanPayoffOutlook,
    debt_without_payoff_model,
    loan_payoff_outlook,
)
from app.services.pay_calendar import PayCadence, PayCalendar, PeriodWindow
from app.services.row_valuation import owned_contribution
from app.services.savings_dashboard_service._types import (
    AccountProjection,
    _DashboardCoreData,
)
from app.services.tax_config_service import load_tax_configs_for_year
from app.utils.balance_predicates import settled_status_ids
from app.utils.money import round_money

_RATE_PLACES = Decimal("0.00001")
_DTI_HEALTHY_THRESHOLD = Decimal("36")
_DTI_HIGH_THRESHOLD = Decimal("43")


@dataclass(frozen=True)
class DtiMetrics:
    """The debt-to-income block -- present as a whole, or absent as a whole.

    ONE nullable field replacing three parallel ones (plan step X-s3, ruling
    R-BD, finding N-106).  ``dti_ratio``, ``dti_label`` and
    ``gross_monthly_income`` were three keys written together in one branch and
    set to ``None`` together in the other, so "the user has no income data" was
    a state spelled three ways -- and read as three predicates across two
    templates (the cockpit footer tested ``dti_ratio``, the dashboard debt
    track tested ``dti_ratio`` for its tooltip and ``dti_label`` for its
    badge).  A ``DtiMetrics | None`` makes the half-populated combination
    unrepresentable and leaves the consumers ONE thing to ask.

    **ONE stored fact**, and the band label is a :attr:`label` property over
    it.  Storing a value that is a pure function of another field is how a
    summary comes to contradict itself, and this package already had the
    answer -- see that property.

    It carried ``gross_monthly_income`` too, the engine-derived denominator,
    and X-s3's own adversarial review found it had ZERO ``app/`` readers
    (AST-verified; no template renders it either) -- an input already spent
    computing :attr:`ratio`, which is byte-for-byte the reason plan step X-s1
    refused to carry a milestone's ``date`` into the chart payload.  Applying
    this step's own thesis to this step's own new code deleted it (developer
    ruling, 2026-07-28).  The biweekly -> monthly conversion it used to pin
    directly stays pinned through the ratio: :attr:`ratio` is
    ``total_monthly_payments / gross_monthly``, and the numerator is asserted
    on its own, so a wrong gross still shows.

    Attributes:
        ratio: Monthly debt payments as a PERCENT of gross monthly income,
            quantized to one decimal place (e.g. ``Decimal("34.2")``).
    """

    ratio: Decimal

    @property
    def label(self) -> str:
        """The health band this ratio falls in: healthy / moderate / high.

        DERIVED, not stored, so it cannot contradict :attr:`ratio` -- the same
        reason :attr:`~.._debt_line.LoanPayoffOutlook.is_loan_free` is a
        property ("derived rather than stored so it cannot contradict the other
        two"), and the same reason ruling R-AZ deleted the Horizon's stored copy
        of that value one step earlier.  Storing it would let a future edit set
        a ratio of 50% beside a 'healthy' badge, and nothing would catch it.

        Returns:
            ``'healthy'``, ``'moderate'`` or ``'high'`` per
            :func:`_get_dti_label`'s thresholds.
        """
        return _get_dti_label(self.ratio)


@dataclass(frozen=True)
class DebtSummary:
    """What is owed, what it costs, when it ends, and how much is repaid.

    THE shape of the debt summary, stated ONCE (plan step X-s3, ruling R-BD,
    finding N-106).  It was a dict assembled across four modules -- six keys
    here, three more mutated in by the DTI applier, a tenth added by a copy in
    ``dashboard_service._pulse``, an eleventh mutated in by the dashboard route
    -- so no single place said what a consumer could read, and the contract
    lived in a comment at the top of ``dashboard/_tracks.html`` because there
    was nowhere else to put it.

    **It CARRIES the payoff outlook rather than copying fields out of it**
    (ruling R-AW, applied where the copy happened).  The dict flattened
    :class:`~.._debt_line.LoanPayoffOutlook`'s two STORED fields and dropped
    the derived third, so ``is_loan_free`` -- the state that says "every loan
    you have is paid off" -- had no reader anywhere in ``app/`` and the cockpit
    footer re-derived it as a Jinja fall-through that renders nothing.  That is
    the same defect X-r deleted from the per-account projection dict one
    package over: a consumer bundle mirroring a value object field by field
    goes stale the moment the value object grows, and here it had already gone
    stale by omission.

    **FOUR questions, FOUR membership rules, and this is the ONE place they are
    stated** -- every other mention in this package cites here rather than
    restating, because a rule written down five times is a rule that can
    disagree with itself, which the first draft of this very docstring did:

    ======================  ===========================  ==================
    field                   rule                         reduces over
    ======================  ===========================  ==================
    the money figures       owed TODAY (balance > 0)     ``loan_ads``
    payoff_outlook          has a DEBT LINE ahead        ``loan_ads``
    principal_paid_fraction ALL LOANS EVER originated    ``loan_ads``
    revolving_debt          liabilities that are NOT     ``account_data``
                            loans (no payoff model)
    ======================  ===========================  ==================

    **Three of the four share one list, and the fourth cannot.**
    :func:`~.._debt_line.debt_without_payoff_model` exists to sum what the loan
    rules exclude, so it takes the full ``account_data`` by necessity, not by
    oversight -- and it reads a SUPERSET, so it cannot disagree with the loan
    rules about a loan.  Said explicitly because the first draft of this step
    claimed "every reducer is handed that same list", which is the safety
    argument for the whole merge and was false of exactly one reducer.

    **The third rule arrived at plan step X-u** (ruling R-BS, finding N-109).
    It was a SECOND narrow producer -- the budget dashboard's tracks section ran
    the whole load -> params -> project pipeline twice per render to get it,
    measured, and the two producers had to keep agreeing on which loans count by
    inspection.  They now reduce over ONE projection of one loan set, each
    applying its own rule inside itself, so the agreement is structural; and
    because that is the exact question ruling X-q settled at a measured cost of
    19 years, the merge changed NEITHER predicate -- both reducers are the ones
    that shipped, called with the same list they were already called with.

    **On the field ``/savings`` does not render.**  ``/savings`` builds this
    summary too and has no rail to put :attr:`principal_paid_fraction` on, which
    invites the objection rulings R-BG and R-BH answered elsewhere in this
    package (a surface with no reader is deleted).  It does not apply: those two
    turned on ZERO ``app/`` readers anywhere, and this field has a live one
    (``routes/dashboard.py`` -> ``dashboard/_tracks.html``).  This value object
    was ALREADY a two-consumer union before X-u -- ``weighted_avg_rate``,
    ``revolving_debt`` and two of the outlook's three states are read only by
    ``/savings``, and the dashboard track reads a strict subset -- so the merge
    makes the union symmetric rather than adding a new class of thing.  The cost
    on ``/savings`` is one reduce over an already-built list: no query, no seam
    read.  Carrying the whole object to each consumer is ruling R-AW's rule, and
    it is what stops a field the summary grows from going missing at one end.

    Attributes:
        total_debt: Principal owed today across the loans that still owe.
        total_monthly_payments: PITI across the same loans -- the seam's
            monthly principal + interest plus each loan's escrow resolved to
            today.
        weighted_avg_rate: The principal-weighted average of the loans'
            CURRENT rates (an ARM contributes its in-effect rate), as a
            fraction quantized to five places; ``0.00000`` when nothing is
            owed.
        payoff_outlook: The seam-derived
            :class:`~.._debt_line.LoanPayoffOutlook`, carried WHOLE -- the one
            derivation the Horizon chart's flag and axis read as well.
        revolving_debt: The owed magnitude of every liability with no payoff
            model (today, a revolving card), which the payoff date cannot
            speak for and the caption therefore names (plan step X-q3).
        principal_paid_fraction: The aggregate fraction of ORIGINAL principal
            repaid across every loan that has originated, a ``Decimal`` in
            ``[0, 1]`` -- the budget dashboard's debt-rail position.  ``None``
            when no loan has originated yet, which is a state this summary can
            be in while being non-``None`` itself: a borrower whose only loan is
            a mortgage that has not closed has a debt line and a payoff date but
            has repaid nothing of anything.  It is a FRACTION, never a percent;
            the 0-100 scaling and the ``float`` cast are presentation and happen
            at the dashboard route's serialization boundary.  Its rule is the
            third row of the table above; :func:`_compute_principal_paid_fraction`
            carries WHY that rule and not another.
        dti: The :class:`DtiMetrics` block, or ``None`` when the user has no
            income data to compute it from.
    """

    total_debt: Decimal
    total_monthly_payments: Decimal
    weighted_avg_rate: Decimal
    payoff_outlook: LoanPayoffOutlook
    revolving_debt: Decimal
    principal_paid_fraction: Decimal | None
    dti: DtiMetrics | None


def _sum_liquid_balances(account_data: list[AccountProjection]) -> Decimal:
    """Sum the current balances of liquid accounts for the emergency fund.

    Args:
        account_data: The per-account projections from
            ``_compute_account_projections``.

    Returns:
        The total liquid balance as a Decimal.
    """
    total_savings = Decimal("0.00")
    for ad in account_data:
        acct_type = ad.account.account_type
        if acct_type is not None and acct_type.is_liquid:
            total_savings += ad.current_balance
    return total_savings


def _get_current_paycheck_breakdown(balance_ctx, all_periods, current_period):
    """Compute the canonical paycheck breakdown for the current period.

    The single income producer this module uses for any engine-derived
    income figure (MED-06 / F-032).

    **A dead ``duplicate-code`` suppression sat on the body of this function
    and is deleted** (pay-calendar plan step C2-f2d-3).  It justified itself by
    naming ``dashboard_service`` as running the same resolve-profile ->
    load-configs -> ``calculate_paycheck`` sequence; that module has neither a
    ``SalaryProfile`` query nor a ``load_tax_configs_for_year`` call, so the
    rationale described code that no longer exists and the disable suppressed
    NOTHING -- measured by deleting it and re-running ``pylint app/``, which
    stays at 10.00/10 with no ``duplicate-code`` message.  It survived every
    gate because ``useless-suppression`` cannot see a stale ``duplicate-code``
    disable, which is finding **N-154**; this is a measured instance of it.
    **The sequence itself is still written THREE times** -- here,
    ``retirement_dashboard_service._compute_current_pay`` and
    ``income_service.get_current_gross_biweekly`` -- which is reported rather
    than merged here (``CLAUDE.md`` rule 6: collapsing it changes what two
    other pages produce).  Both consumers -- the savings-goal
    trajectory's net biweekly pay and the DTI denominator's gross
    monthly income -- route through this helper so the page cannot
    silently disagree with the paycheck engine on the same period.
    Pre-Commit-26 the DTI denominator read the off-engine
    ``annual_salary / pay_periods`` recompute, which dropped applicable
    ``SalaryRaise`` rows; the engine applies raises period-by-period
    via ``apply_raises`` and is therefore the only correct source for
    a raise-aware monthly gross.

    **It takes the read PASS rather than an owner id** (plan step R-F16, on
    the ruling ``pay_calendar:C2-f2d-1`` set).  The engine needs the owner's
    paycheck COUNT as well as their profile, that count comes off the pay
    calendar the pass already memoizes, and a producer holding a bare id could
    only have derived a second one.  Dropping the id also makes a mismatched
    (owner, pass) pair unrepresentable here.

    Args:
        balance_ctx: The read pass.  Its ``user_id`` scopes the profile query
            and its memoized calendar supplies the cadence.
        all_periods: The owner's whole saved schedule as a
            :class:`~app.services.pay_calendar.PeriodWindow` (passed through
            to the paycheck engine for 3rd-paycheck detection and the
            FICA SS wage-base cap's cumulative-wage tracking).
        current_period: The current
            :class:`~app.services.pay_calendar.DerivedPeriod`, or ``None``.

    Returns:
        :class:`PaycheckBreakdown` for the current period under the
        user's active salary profile, or ``None`` if ``current_period``
        is ``None`` or no active profile exists.  Callers treat
        ``None`` as "no income data on the page" rather than as a zero
        amount, since absence of an income source is structurally
        different from a real zero (E-12).
    """
    if current_period is None:
        return None

    profile = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=balance_ctx.user_id, is_active=True)
        .first()
    )
    if profile is None:
        return None

    # The CURRENT PERIOD's own tax year, not the clock's -- the same key
    # every other paycheck for this profile is computed under.
    tax_configs = load_tax_configs_for_year(
        balance_ctx.user_id, profile, current_period.start_date.year,
    )
    return paycheck_calculator.calculate_paycheck(
        PayrollBasis(profile, balance_ctx.calendar().cadence),
        current_period, all_periods, tax_configs,
    )


def _checking_account_ids(accounts):
    """IDs of the user's checking accounts.

    The single source for the checking-account scope shared by the two
    operands of :func:`_compute_avg_monthly_expenses` (DH-#29): both the
    committed-template floor and the recent-settled-expenses average
    measure outflow from these accounts, so the set is derived once here
    and threaded into both.  Resolved by the CHECKING ref-type id (IDs
    for logic), not a name string.

    Args:
        accounts: List of Account model instances.

    Returns:
        List of integer account IDs whose type is the CHECKING ref type.
    """
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)
    return [
        acct.id for acct in accounts
        if acct.account_type_id == checking_type_id
    ]


def _recent_settled_expenses_monthly(
    checking_ids: list[int],
    all_periods: PeriodWindow,
    current_period,
    scenario_id: int,
    pay_cadence: PayCadence,
) -> Decimal:
    """Average monthly settled checking expenses over the last 6 periods.

    Sums settled expense transactions on the user's checking accounts
    across the most recent 6 periods (at or before the current period)
    and converts the per-period average to a monthly figure at the OWNER's
    pay cadence (a hardcoded 26/12 until plan step R7a-2a, which reported a
    weekly-paid owner's spending at half its true monthly rate and so
    understated the emergency fund they need).  Scoped to the same
    checking-account set
    as :func:`_committed_expense_floor` (DH-#29) so the two operands of
    :func:`_compute_avg_monthly_expenses`'s ``max()`` measure the same
    "outflow from checking" universe -- a settled expense on a
    non-checking account (e.g. a transfer's expense shadow on a
    savings/HSA source) is excluded here just as it is from the floor,
    rather than inflating only the historical operand.

    Args:
        checking_ids: IDs of the user's checking accounts (the
            :func:`_checking_account_ids` set the floor also uses).
        all_periods: The owner's saved schedule as a
            :class:`~app.services.pay_calendar.PeriodWindow`.
        current_period: The current
            :class:`~app.services.pay_calendar.DerivedPeriod`, or ``None``.
        scenario_id: The baseline scenario's id, from the read pass's
            raising accessor -- never a nullable (plan step X-v2).
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`), which is what
            turns a per-PERIOD average into a per-MONTH one.

    Returns:
        The monthly average as a Decimal.  ``Decimal("0.00")`` when
        there is no current period, no checking account, or no recent
        periods.

    **This function took the nullable SCENARIO OBJECT and answered
    ``Decimal("0.00")`` for a user with no baseline** -- a fabricated monthly
    expense feeding the emergency-fund runway, and the THIRD surviving guard
    in a step whose ruling R-BY says exactly two survive.  Both of X-v2's
    adversarial reviews found it independently.  It is also the site finding
    N-112's own row named as the reason the census "wants an AST pass", and
    the AST census X-v built STILL missed it -- because the predicate arrives
    as a PARAMETER, not as an attribute or a local alias.  The census that
    replaces a grep needs the same scepticism the grep earned.
    """
    if current_period is None or not checking_ids:
        return Decimal("0.00")

    recent_periods = [
        p for p in all_periods
        if p.period_index <= current_period.period_index
    ][-6:]
    if not recent_periods:
        return Decimal("0.00")

    recent_period_ids = [p.period_id for p in recent_periods]
    # Both halves of "settled checking EXPENSE" are asked in SQL rather than in
    # a Python ``if`` beside the valuation (plan step X-au-c2).  They were, and
    # the row set was every status: the loop's guard was what kept a Projected
    # row away from the amount read, so the accessor's precondition rested on a
    # conditional a later edit could reorder rather than on the query.  Asking
    # here makes it structural -- ``owned_contribution`` below can only ever see
    # a row that has SETTLED, which answers from the settlement it RECORDED
    # (plan step X-au-c3) rather than from its plan -- and loads only the rows
    # that are summed.  ``settled_status_ids()`` is exactly the ``is_settled``
    # set it replaces (``ref_seeds``: Paid, Received, Settled).
    recent_txns = (
        db.session.query(Transaction)
        .filter(
            Transaction.pay_period_id.in_(recent_period_ids),
            Transaction.account_id.in_(checking_ids),
            Transaction.scenario_id == scenario_id,
            Transaction.is_deleted.is_(False),
            Transaction.transaction_type_id == ref_cache.txn_type_id(
                TxnTypeEnum.EXPENSE,
            ),
            Transaction.status_id.in_(settled_status_ids()),
        )
        # ``owned_contribution`` resolves through
        # ``row_valuation.settled_figure``, which sums a ``purchases``-basis
        # row's OWN entries rather than reading a stored copy (plan step
        # X-au-c3).  Without this the metric issues one SELECT per settled
        # envelope where it used to read a column.
        .options(selectinload(Transaction.entries))
        .all()
    )

    total_expenses = sum(
        (owned_contribution(txn) for txn in recent_txns), Decimal("0.00"),
    )

    per_period = total_expenses / len(recent_periods)
    return pay_cadence.per_paycheck_to_monthly(per_period)


def _committed_expense_floor(
    user_id: int, checking_ids: list[int], calendar: PayCalendar,
    as_of: date,
) -> Decimal:
    """Committed monthly expense floor from active checking templates.

    Sums the monthly-normalized commitment of active expense templates
    and active outgoing transfer templates on the user's checking
    accounts, via the canonical obligations aggregator (E-24 / HIGH-05)
    -- so the same skip-non-repeating / skip-expired filter the
    /obligations page applies governs the emergency-fund baseline.

    Args:
        user_id: Integer ID of the current user.
        checking_ids: IDs of the user's checking accounts (the
            :func:`_checking_account_ids` set the historical operand
            also uses).
        calendar: The owner's whole pay-period schedule, threaded into the
            aggregator so a paycheck-space template's monthly equivalent is
            measured against the owner's real rhythm -- and so a
            count-bounded template that has spent its count leaves the
            baseline, which needs the paydays and not just their spacing
            (plan step R7b-3).
        as_of: The read pass's day.  It was ``date.today()`` here until
            pay-calendar plan step C2-f2d-3 (ledger row **P55**): the
            aggregator decides whether a bounded template still commits
            anything AS OF the day it is given, so a bare clock read put this
            floor on a different day from the settled-expense operand it is
            compared against by ``max()``.

    Returns:
        The committed monthly floor as a Decimal.  ``Decimal("0.00")``
        when the user has no checking account.
    """
    if not checking_ids:
        return Decimal("0.00")

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    active_expense_templates = (
        db.session.query(TransactionTemplate)
        .filter(
            TransactionTemplate.user_id == user_id,
            TransactionTemplate.account_id.in_(checking_ids),
            TransactionTemplate.transaction_type_id == expense_type_id,
            TransactionTemplate.is_active.is_(True),
        )
        .all()
    )
    active_transfer_templates = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == user_id,
            TransferTemplate.from_account_id.in_(checking_ids),
            TransferTemplate.is_active.is_(True),
        )
        .all()
    )
    return obligations_aggregator.committed_monthly(
        list(active_expense_templates) + list(active_transfer_templates),
        as_of,
        calendar,
    )


def _compute_avg_monthly_expenses(
    user_id: int, core: _DashboardCoreData, calendar: PayCalendar,
) -> Decimal:
    """Compute average monthly expenses for emergency fund coverage.

    Uses the higher of: historical settled expenses from the last 6
    periods, or the committed monthly baseline from active templates.
    Both operands are scoped to the user's checking accounts (DH-#29)
    so the ``max()`` compares like with like -- the "outflow from
    checking" universe the committed floor (E-24) defines -- rather than
    pairing an all-accounts historical figure against a checking-only
    floor.

    **Takes the read-pass bundle rather than four of its fields** (plan step
    R7a-2a).  It had unpacked ``accounts`` / ``all_periods`` /
    ``current_period`` / ``scenario_id`` at the call site and threaded them
    through, and the owner's pay cadence -- which BOTH operands need -- would
    have been a fifth.  All four are reachable from
    :class:`~.._types._DashboardCoreData` (the period SET through its
    ``balance_ctx`` since pay-calendar plan step C2-f2d-3), so the bundle is
    the honest argument and the caller stops restating its contents; the
    cadence does NOT, and is passed separately for the reason that class's
    docstring gives.

    Args:
        user_id: Integer ID of the current user.
        core: The read pass's loaded bundle -- its accounts scope the checking
            set, and its pass's reported periods and scenario scope the
            historical operand.
        calendar: The owner's whole pay-period schedule.  ``calendar.cadence``
            converts BOTH operands into month space -- one value for both, so
            the ``max()`` cannot compare figures measured against two rhythms
            -- and the committed operand needs the whole schedule to tell
            whether a count-bounded template still commits anything.

    Returns:
        The higher of the two monthly figures, as a Decimal.
    """
    checking_ids = _checking_account_ids(core.accounts)
    historical = _recent_settled_expenses_monthly(
        checking_ids, core.balance_ctx.reported_periods(), core.current_period,
        core.balance_ctx.scenario_id, calendar.cadence,
    )
    floor = _committed_expense_floor(
        user_id, checking_ids, calendar, core.balance_ctx.as_of,
    )
    return max(historical, floor)


def _loan_ad_current_principal(ad: AccountProjection) -> Decimal | None:
    """Return a loan projection's contributing current balance, or None.

    The single definition of "which loan accounts contribute to the debt
    summary's owed-today aggregates" (its ``total_debt``,
    ``total_monthly_payments``, and weighted-average rate).  A loan
    contributes its seam-derived current balance when that balance is
    positive; otherwise it contributes nothing and the caller skips it.

    **The BALANCE is the whole predicate, and that is the right one for this
    question** (plan step X-q).  These three figures answer "what do you owe
    TODAY": a retired loan owes nothing, and a loan that has not been borrowed
    yet owes nothing and is not yet paying anything either -- both read
    ``$0.00`` here and both are correctly out.  The question "which loans have
    a debt line AHEAD of them" is a different one with a different set, and it
    lives at :func:`~.._debt_line.debt_line_loans`; this function used to test
    ``is_paid_off`` as well, which was the CONGRATULATION predicate answering a
    money question (finding B-16's class).  It was rescued only by the balance
    test beside it: ``is_paid_off`` implies ``is_retired`` implies the fold at
    the pass's as-of is ``<= 0``, and ``current_balance`` is
    :func:`~app.services.balance_at.balance_at` at that same as-of, which for
    an originated loan is that same fold -- so the arm could never change an
    answer.  It is deleted rather than re-pointed: a predicate that cannot
    fire reads as a rule and is not one.

    The principal-paid progress fraction does NOT use this predicate -- it sums
    over ALL loans ever originated (see
    :func:`_compute_principal_paid_fraction`), keeping retired loans in both of
    its sums so the marker stays monotonic.  The displayed debt balance, by
    contrast, is owed-today, which is exactly what this predicate scopes.

    Args:
        ad: A per-account projection carrying ``current_balance`` (a loan
            entry from ``_compute_account_projections``).

    Returns:
        The loan's seam-derived current balance as a positive ``Decimal``
        when it contributes, or ``None`` when that balance is zero or
        negative.
    """
    # Seam-derived current_balance (E-18 / Commit 15).  Same dollar
    # figure as the loan card; replaces the previous read of the
    # non-authoritative ``LoanParams.current_principal`` column that
    # produced F-008's stored-vs-engine divergence.
    principal = ad.current_balance
    if principal <= Decimal("0.00"):
        return None
    return principal


def _compute_principal_paid_fraction(
    loan_ads: list[AccountProjection],
) -> Decimal | None:
    """Aggregate fraction of original principal paid across ALL loans ever.

    Computes ``(sum(original_principal) - sum(current_balance)) /
    sum(original_principal)`` over EVERY loan the pipeline surfaces, not
    just the loans still carrying a balance.  A RETIRED loan stays in
    BOTH the numerator and the denominator, contributing
    ``Decimal("0.00")`` to the current-balance sum -- so its full
    ``original_principal`` lands in the "paid" portion of the numerator.

    This "all loans ever originated" basis (locked 2026-06-12 in
    ``docs/design/dashboard_card_audit.md``, Rebuild decisions item 4) is
    what makes the debt-track marker MONOTONIC: paying a single loan all
    the way off only adds its principal to the paid portion and never
    removes anything from the denominator, so the fraction can only rise,
    reaches exactly ``1`` at full payoff of every loan, and stays there --
    it never jumps backward the way the prior active-loans-only basis did
    when one loan dropped out of both sums at payoff.  The displayed
    balance label remains active-loans-only; that is
    :func:`_compute_debt_summary`'s concern, not this marker's.

    "All loans the pipeline surfaces" is, reachably, all of the user's
    NON-ARCHIVED (``is_active=True``) loan accounts that have a
    ``LoanParams`` row AND HAVE ORIGINATED.  Archived accounts are
    filtered out upstream by ``_load_dashboard_core_data``
    (``is_active=True``) and never reach the projections, so they cannot
    be included; a loan with no ``LoanParams`` row carries no
    ``original_principal`` and is likewise not a loan-ad here.  RETIRED
    loans, by contrast, remain active accounts and DO appear
    carrying a ``loan`` detail whose seam figures have
    ``is_retired`` set, so the all-loans-ever set is fully
    reachable.  The predicate is ``is_retired``
    and not ``is_paid_off`` as of plan step X-q: "this loan owes nothing" is
    the question this marker asks, and a loan retired by a lump-sum true-up
    answers it whether or not the ledger can BADGE it.  A loan the user has
    configured but not yet BORROWED (a mortgage closing next month) is
    excluded from both sums -- see the loop.

    ``original_principal`` is a NOT NULL, ``> 0`` column on
    :class:`~app.models.loan_params.LoanParams`, so any real loan-ad
    supplies a positive denominator.  ``None`` is returned when NO loan has
    originated (the denominator would be zero) -- which includes both "the user
    has no loans" and "every loan the user has is still unborrowed"; a fully
    paid-off loan set returns ``Decimal("1")``, not ``None``.

    **The 2026-06-12 ruling cited above gets that last part WRONG, and it is
    cited anyway because it is the authority for the BASIS, not for the null
    case.**  It says "None (rail renders without a marker) only when the user has
    no loans at all" (``dashboard_card_audit.md``, same item).  The unborrowed
    loan the ``is_originated`` skip below excludes -- which post-dates that
    ruling by three weeks (plan step X-o, finding N-98) -- is the second way the
    denominator reaches zero, and a reader who follows the citation must not
    take the null clause with it.  Pinned at
    ``TestPrincipalPaidFraction::test_fraction_none_when_every_loan_is_unborrowed``.

    Args:
        loan_ads: The projections that carry a ``loan`` detail -- the loan
            subset of ``_compute_account_projections`` output, ALREADY
            filtered, exactly as :func:`_accumulate_loan_debt` takes it.  It
            selected that subset itself until plan step X-u, back when its
            caller was a producer of its own; taking the filtered list is what
            lets :func:`_compute_debt_summary` state "which projections are
            loans" ONCE for all THREE of the reducers it hands that list to, so
            the loan membership rules can differ without the loan SET being able
            to.  An unfiltered list is not silently tolerated: the loop below
            dereferences ``ad.loan.figures`` with no guard, so a non-loan raises
            immediately.

    Returns:
        The principal-paid fraction as a ``Decimal`` in ``[0, 1]`` (a
        loan whose current balance somehow exceeds its original principal
        is clamped to ``0`` so the marker never renders to the left of the
        rail), or ``None`` when no loan has originated.
    """
    total_original = Decimal("0.00")
    total_current = Decimal("0.00")
    for ad in loan_ads:
        # A loan that has not been BORROWED yet is in NEITHER sum.  Its principal
        # is not money the user owes, and none of it has been repaid.  Counting it
        # would put its full original principal in the denominator against a
        # current balance of $0.00 -- the seam's correct answer for a debt that
        # does not exist yet -- and report every cent of it as PAID: an unclosed
        # $200,000 mortgage beside a never-paid $100,000 auto loan read 66.67%
        # repaid on a borrower who had repaid nothing.  It would also break this
        # marker's one design invariant below: the fraction would COLLAPSE from
        # 66.67% to 0% on closing day, when the mortgage's balance steps from
        # $0.00 to $200,000.
        if not ad.loan.figures.terms.is_originated:
            continue
        # ALL loans ever: every loan-ad contributes its original
        # principal to the denominator.  A RETIRED loan contributes
        # Decimal("0.00") to the current-balance sum (regardless of the
        # resolver's as-of-today figure) so its full principal counts as
        # paid; a loan that still owes contributes its seam-derived current
        # balance, never below zero.  The predicate is ``is_retired`` and not
        # ``is_paid_off`` (plan step X-q): "this loan owes nothing" is the
        # question here, and a loan retired by a lump-sum true-up with no
        # payment rows answers it -- it is simply not BADGED.  The two agree on
        # the figure either way (a retired loan's balance folds to <= $0.00,
        # so the ``max(current, 0)`` below adds exactly nothing on either
        # predicate), which is why this is a vocabulary fix and not a
        # behaviour change.
        total_original += ad.loan.params.original_principal
        if ad.loan.figures.is_retired:
            continue
        current = ad.current_balance
        total_current += max(current, Decimal("0.00"))

    if total_original <= Decimal("0.00"):
        return None

    fraction = (total_original - total_current) / total_original
    # A current balance above the original principal (negative paid
    # fraction) is meaningless for a payoff marker; clamp to 0.
    if fraction < Decimal("0"):
        return Decimal("0")
    return fraction


def _accumulate_loan_debt(
    loan_ads: list[AccountProjection], escrow_map: dict[int, list],
    as_of: date,
) -> tuple[Decimal, Decimal, Decimal]:
    """Sum the owed-today debt metrics across the loans that still owe.

    Walks the per-account loan projections, skipping any whose seam-derived
    current balance is zero or negative, and accumulates the running totals the
    debt summary reports.

    **It no longer collects payoff dates** (plan step X-q).  It used to derive
    the debt-free date inside this loop, over the loans that owe money TODAY --
    a different set from the loans that still have a debt line, and the
    difference is a mortgage that has not closed yet: it owes ``$0.00``, so it
    was skipped here and the caption reported the date the OTHER loans finish,
    19 years early on the developer's own data (finding N-98).  The date now
    comes from :func:`~.._debt_line.loan_payoff_outlook`, which the Horizon
    chart reads as well.

    Args:
        loan_ads: Per-account projections that carry a ``loan`` detail (the
            loan subset of ``_compute_account_projections`` output).
        escrow_map: Dict mapping account_id to list of EscrowLine (with versions).
        as_of: The read pass's day, which resolves each escrow LINE to its
            active version.  It was ``date.today()`` here until pay-calendar
            plan step C2-f2d-3 (ledger row **P55**) -- a bare clock read
            deciding which escrow version prices the PITI total that the DTI
            ratio beside it divides, on a page whose every other figure is
            measured at the pass's day.

    Returns:
        ``(total_debt, total_monthly, weighted_rate_sum)`` -- the running sums.
    """
    total_debt = Decimal("0.00")
    total_monthly = Decimal("0.00")
    weighted_rate_sum = Decimal("0.00")

    for ad in loan_ads:
        principal = _loan_ad_current_principal(ad)
        if principal is None:
            continue

        # DH-#56: the loan's CURRENT rate (resolver-derived,
        # ``state.current_rate``), replacing the retired
        # ``LoanParams.interest_rate`` column.  weighted_avg_rate now
        # reflects the rate the loan is actually accruing at today --
        # for a changed ARM the in-effect rate, not the stale origination
        # value the dropped column had drifted from.
        rate = ad.loan.figures.terms.current_rate
        monthly_pi = ad.loan.figures.terms.monthly_payment

        # Include escrow (property tax, insurance) for PITI total, resolved to
        # today's active version per line via the shared as-of function.
        lines = escrow_map.get(ad.account.id, [])
        monthly_escrow = escrow_calculator.escrow_monthly_as_of(lines, as_of)
        monthly_total = round_money(monthly_pi + monthly_escrow)

        total_debt += principal
        total_monthly += monthly_total
        weighted_rate_sum += rate * principal

    return total_debt, total_monthly, weighted_rate_sum


def _compute_debt_summary(
    account_data: list[AccountProjection],
    escrow_map: dict[int, list],
    gross_monthly: Decimal,
    as_of: date,
) -> DebtSummary | None:
    """Compute aggregate debt metrics across the user's loan accounts.

    THE one construction site for :class:`DebtSummary` (plan step X-s3): every
    field, including the DTI block, is set here.  The DTI keys used to be
    MUTATED in afterwards by a separate applier, so the object a template read
    was never fully built anywhere and "which fields does a debt summary have"
    was answerable only by reading the modules in call order.

    Uses per-account data already computed by _compute_account_projections:
    ``current_balance`` directly, the original principal, payment and rate off
    the ``loan`` detail's contract row and seam figures (plan steps X-r /
    X-t1), and the payoff through
    :func:`~.._debt_line.loan_payoff_outlook`.  Escrow components are loaded
    separately and included in the monthly total so DTI reflects PITI
    (principal, interest, taxes, insurance).

    **Four figures, four membership rules, answered in four places on purpose**
    (plan steps X-q and X-u).  :class:`DebtSummary`'s docstring is where those
    rules are stated; this function is where they are APPLIED, and the only
    thing worth repeating here is what the application guarantees.

    **The three LOAN rules are safe because they share one list.**  ``loan_ads``
    is computed once below and handed to each of them, so a loan rule can differ
    in what it does with a loan and never in which loans it was shown.  (The
    fourth figure, ``revolving_debt``, is about the liabilities that are NOT
    loans and takes ``account_data`` -- a superset, so it cannot disagree with
    the three about a loan.  The table in :class:`DebtSummary` says which is
    which.)  The fraction reached this function at plan step X-u (finding N-109)
    from a producer that ran its own load and its own projection to build its
    own list -- two lists that agreed because two docstrings said they must.

    Deriving the payoff date here, over the owed-today set, is what put a
    19-year contradiction between this caption and the Horizon's flag on one
    page (finding N-98); it comes from
    :func:`~.._debt_line.loan_payoff_outlook` instead, the ONE derivation that
    flag reads as well.  The outlook is carried WHOLE rather than flattened into
    fields, which is ruling R-AW; see :class:`DebtSummary`.

    Args:
        account_data: The per-account projections from
            _compute_account_projections.
        escrow_map: Dict mapping account_id to list of EscrowLine (with versions).
        gross_monthly: The engine-derived gross MONTHLY income the DTI block
            is computed from -- the owner's paycheck converted at their own
            cadence by the caller; ``0.00`` when the user has no salary data,
            which is what makes :attr:`DebtSummary.dti` ``None``.
        as_of: The read pass's day, which resolves each loan's escrow version
            inside :func:`_accumulate_loan_debt` (ledger row **P55**).

    Returns:
        The :class:`DebtSummary`, or ``None`` if no loan accounts with params
        exist -- so a user whose only liability is a card has no payoff caption
        to qualify, which is why the ``revolving_debt`` caveat rides here.
    """
    loan_ads = [ad for ad in account_data if ad.loan is not None]
    if not loan_ads:
        return None

    total_debt, total_monthly, weighted_rate_sum = (
        _accumulate_loan_debt(loan_ads, escrow_map, as_of)
    )

    if total_debt > Decimal("0.00"):
        weighted_avg_rate = (weighted_rate_sum / total_debt).quantize(
            _RATE_PLACES, rounding=ROUND_HALF_UP
        )
    else:
        weighted_avg_rate = Decimal("0.00000")

    total_monthly_payments = round_money(total_monthly)
    return DebtSummary(
        total_debt=round_money(total_debt),
        total_monthly_payments=total_monthly_payments,
        weighted_avg_rate=weighted_avg_rate,
        # The seam-derived outlook, carried WHOLE (ruling R-AW).  Flattening it
        # dropped ``is_loan_free`` -- "every loan you have is paid off" -- and a
        # consumer cannot miss a field that was never copied, so the cockpit
        # footer fell through that state in silence for as long as the copy
        # existed.  Its other two states are the ones the caller must SAY rather
        # than omit: a debt-line loan that never clears at its current payment is
        # a different fact from having no loans left, and the loan detail page
        # already names it in words on the same condition ("No payoff at current
        # payment", plan C8d).
        payoff_outlook=loan_payoff_outlook(loan_ads),
        # What the payoff date CANNOT speak for (plan step X-q3, finding
        # N-99): every liability with no forward model -- today, a revolving
        # card -- is invisible to the derivation, so the caption says so
        # instead of implying the user is out of debt on a date that only
        # covers their loans.
        revolving_debt=debt_without_payoff_model(account_data),
        # The debt rail's position (plan step X-u, ruling R-BS, finding N-109).
        # It reduces over the SAME ``loan_ads`` the two loan rules above do and
        # applies its own all-loans-ever rule inside itself, so the rules stay
        # distinct while the loan SET stays one.  It was a second producer that
        # re-ran this whole pipeline to get here, which is the redundancy the
        # finding measured; what it is NOT is a re-decision of any rule.
        principal_paid_fraction=_compute_principal_paid_fraction(loan_ads),
        dti=_dti_metrics(total_monthly_payments, gross_monthly),
    )


def _get_dti_label(dti_pct: Decimal) -> str:
    """Return the DTI health label based on conventional thresholds.

    Boundaries: < 36% is healthy, 36%--43% is moderate, > 43% is high.
    36.0% is moderate (not healthy).  43.0% is moderate (not high).

    Args:
        dti_pct: DTI as a percentage (e.g. Decimal("34.2")).

    Returns:
        'healthy', 'moderate', or 'high'.
    """
    if dti_pct < _DTI_HEALTHY_THRESHOLD:
        return "healthy"
    if dti_pct > _DTI_HIGH_THRESHOLD:
        return "high"
    return "moderate"


def _dti_metrics(
    total_monthly_payments: Decimal, gross_monthly: Decimal,
) -> DtiMetrics | None:
    """Derive the DTI block from monthly debt payments and gross biweekly pay.

    A PURE function returning the whole block or ``None`` (plan step X-s3,
    ruling R-BD).  It used to MUTATE a debt-summary dict, writing three keys in
    one branch and three ``None`` s in the other -- so the "no income data"
    state was spelled three times and read as three predicates by two
    templates, and the summary object was never fully constructed in any one
    place.

    **It takes the MONTHLY gross, and the paycheck-to-monthly conversion moved
    OUT at plan step R7a-2a.**  This function used to take the per-paycheck
    gross and convert it here against a hardcoded
    ``PAY_PERIODS_PER_YEAR / MONTHS_PER_YEAR``, justified as "a structural
    property of the 26-period pay schedule (Shekel is a biweekly app)" -- a
    justification that was false of the schema it described, since
    ``cadence_days`` is user-selectable 1..365.  Giving it the owner's cadence
    instead was the obvious repair and it was the wrong shape twice over: a DTI
    is a ratio of two MONTHLY figures, so the unit conversion was never this
    function's job; and resolving a cadence for it meant reading the owner's
    schedule on a page that answers ``None`` here whenever no salary is
    configured, which put a 500 in front of an owner with a mortgage and no
    salary profile.  The caller converts, behind the one condition that decides
    whether there is anything to convert.

    Args:
        total_monthly_payments: The debt summary's PITI total.
        gross_monthly: Engine-derived gross MONTHLY income, already converted
            from the owner's paycheck at their own cadence and rounded to
            cents by the caller.  ``0.00`` when no salary is configured.

    Returns:
        The :class:`DtiMetrics`, or ``None`` when ``gross_monthly`` is zero --
        no income source, which a consumer must distinguish from a real zero
        ratio (E-12).
    """
    if gross_monthly <= Decimal("0.00"):
        return None
    return DtiMetrics(ratio=(
        total_monthly_payments / gross_monthly * Decimal("100")
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
