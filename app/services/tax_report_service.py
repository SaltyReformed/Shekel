"""
Shekel Budget App -- Analytics Taxes Report Service (T-P3)

The producer behind the analytics Taxes tab (T-P4 renders it).  It fuses
the two T-P1/T-P2 building blocks into the four surfaces the locked Taxes
anatomy shows -- the refund estimate, the hero chips, the hybrid W-2
preview, and the Schedule A check -- for one tax year:

* **Refund** = withholding-to-date TOTAL (measured checkpoint + modeled
  remainder, from :mod:`tax_withholding_service`) minus the filing-time
  liability (from :mod:`tax_liability_service`), computed SEPARATELY for
  federal and NC state.  The federal refund ADDS the refundable Additional
  Child Tax Credit (T-P5), so a household whose CTC exceeds its tax still
  shows the ACTC as a refund.  Positive = refund, negative = owed (see
  :class:`RefundEstimate`).
* **Hybrid W-2 preview** = per-filer W-2 boxes built from the hybrid gross
  and the four hybrid withholding lines, labelled "measured through <stub
  date>, modeled after" via the carried measured / modeled split.
* **Schedule A check** = itemized estimate (mortgage interest REUSED from
  the year-end summary's ledger+schedule hybrid, plus the hybrid state
  income tax withheld) versus the standard deduction.  Informational only:
  the v1 LIABILITY stays standard-deduction based; the itemize election is
  out of scope and disclosed.

Single-filer identity (audit ruling): every ACTIVE salary profile in the
baseline scenario belongs to ONE filer (multiple jobs, one 1040).  Wages,
pre-tax, and withholding are SUMMED across the active profiles; the filing
inputs the liability needs -- filing status, dependent counts, W-4 Step
4(a) -- are taken from the PRIMARY profile (the salary cockpit's
first-by-``(sort_order, name)`` rule).  With the developer's single active
profile this degrades to the trivially-correct one-profile case.

Liability basis (T-P1 ruling + T-P5 extension): 4(a) in, 4(b) out,
nonrefundable CTC/ODC clamp on the liability with the unused child credit
paid out as the refundable ACTC, and the NC filing-status standard deduction
plus AGI-tiered per-child deduction -- all owned by
:func:`tax_liability_service.compute_annual_liability`; this module only
feeds it the hybrid wage and the modeled pre-tax.

Pre-tax modelling (assumptions disclosure): the YTD checkpoint captures no
pre-tax figure, so the annual pre-tax that reduces the liability's taxable
base is MODELED over the FULL year (``project_salary`` pre-tax totals across
every one of the year's periods), even for the elapsed periods whose
WITHHOLDING is measured.  Disclosed via ``pretax_modeled_for_elapsed``.

No duplicated data: the liability's own outputs (standard deduction,
dependent counts + credit amounts, state rate + state standard deduction,
tax year) live on the carried :class:`~app.services.tax_liability_service.AnnualLiability`;
the T-P4 assumptions card reads them from ``report.liability`` rather than
having them copied onto :class:`TaxAssumptions` (which carries only the
non-liability disclosures).

Boundary discipline: no Flask import (``today`` and the query results are
plain data).  DB reads (baseline scenario, active profiles, the year's pay
periods, and the debt accounts the Schedule A hybrid needs) live in the
service layer, mirroring the year-end summary orchestrator's precedent --
this module loads the year's periods exactly as ``_load_common_data`` does.
No tax arithmetic is re-implemented: the liability, the withholding hybrid,
the bracket ladder, the primary-profile rule, and the mortgage-interest
hybrid are all reused from their owning modules.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.services import net_worth_kernel, paycheck_calculator, tax_calculator
from app.services.projection_inputs import (
    load_active_accounts_with_types,
    load_active_salary_profiles,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.services.tax_config_service import load_tax_configs_for_year
from app.services.tax_liability_service import (
    AnnualLiability,
    compute_annual_liability,
)
from app.services.tax_withholding_service import (
    WithholdingComponents,
    compute_withholding_to_date,
)
from app.services.year_end_summary_service._income_tax import (
    _compute_mortgage_interest,
)

ZERO = Decimal("0")

# Effective-rate resolution: four decimal places (0.01%), matching the
# ``Numeric(5, 4)`` precision the seeded bracket rates (and therefore the
# marginal-rate chip) carry, so the two rate chips render at one precision.
_RATE_QUANTUM = Decimal("0.0001")

# The all-zero components: the summed measured/modeled sides degrade to this
# when there are no periods and no checkpoint.
_ZERO_COMPONENTS = WithholdingComponents(ZERO, ZERO, ZERO, ZERO, ZERO)


@dataclass(frozen=True)
class WithholdingSummary:
    """Per-filer withholding-to-date summed across the active profiles.

    ``total`` (= ``measured + modeled`` component-wise) is what the refund
    subtracts liability from and what the W-2 boxes are built on;
    ``measured`` and ``modeled`` are kept apart so the W-2 preview labels
    its per-box mix ("measured through <stub date>, modeled after").
    ``measured_through`` is the LATEST stub date any profile is measured
    through (exact for the single-profile case; the representative date for
    the multi-profile combined preview), or ``None`` when every profile is
    fully modeled.  ``has_checkpoint`` records whether ANY profile carried
    a checkpoint (the pre-tax-modelling disclosure keys off it).
    """

    total: WithholdingComponents
    measured: WithholdingComponents
    modeled: WithholdingComponents
    measured_through: date | None
    has_checkpoint: bool


@dataclass(frozen=True)
class RefundEstimate:
    """The refund producer's output: federal + state + total refund.

    State refund is ``withheld - liability``; federal refund is
    ``withheld - liability + refundable_actc`` (the refundable Additional
    Child Tax Credit, T-P5).  Positive = money back, negative = owed at
    filing.  The withheld, liability, and ACTC operands themselves live on
    the report's ``withholding`` / ``liability`` bundles, so only the three
    precomputed deltas the templates cannot compute are carried here.
    ``total_refund`` (federal + state) is the hero headline.
    """

    federal_refund: Decimal
    state_refund: Decimal
    total_refund: Decimal


@dataclass(frozen=True)
class W2Wages:
    """The four per-filer W-2 wage boxes.

    ``box1_wages`` (and ``box16_state_wages``, equal to it) is hybrid gross
    less the modeled annual pre-tax; ``box3_ss_wages`` caps gross at the
    year's SS wage base; ``box5_medicare_wages`` is raw gross.  Box 3 / 5
    use RAW gross (real payroll reduces the SS / Medicare base only by
    Section-125 pre-tax, not modelled in v1), and box 3 caps the COMBINED
    gross at one wage base -- an estimate for a multi-job filer whose real
    per-W-2 caps apply per employer.
    """

    box1_wages: Decimal
    box3_ss_wages: Decimal
    box5_medicare_wages: Decimal
    box16_state_wages: Decimal


@dataclass(frozen=True)
class W2Withheld:
    """The four per-filer W-2 withholding boxes (the hybrid lines).

    Each is the summed hybrid withholding line (measured checkpoint +
    modeled remainder) for its tax: federal, Social Security, Medicare, and
    state.
    """

    box2_federal: Decimal
    box4_ss_withheld: Decimal
    box6_medicare_withheld: Decimal
    box17_state_withheld: Decimal


@dataclass(frozen=True)
class W2Preview:
    """A per-filer, filing-time W-2 estimate (measured + modeled hybrid).

    ``wages`` / ``withheld`` carry the eight W-2 boxes; ``measured`` /
    ``modeled`` are the summed withholding-to-date sides (the five
    ``ytd``-shaped figures) so T-P4 labels the mix, and ``measured_through``
    is the stub date the "measured through <date>, modeled after" label
    reads (``None`` when fully modeled).
    """

    wages: W2Wages
    withheld: W2Withheld
    measured: WithholdingComponents
    modeled: WithholdingComponents
    measured_through: date | None


@dataclass(frozen=True)
class ItemizedComponents:
    """The itemizable Schedule A components the app can source.

    ``mortgage_interest`` REUSES the year-end summary's ledger-actual +
    schedule-projected hybrid; ``state_income_tax`` is the hybrid state
    income tax withheld.  ``property_tax`` is ``None`` -- there is no
    unambiguous property-tax source to query (escrow line items are
    free-text named, with no ref-table kind separating a tax line from
    insurance), so it is OMITTED.
    """

    mortgage_interest: Decimal
    state_income_tax: Decimal
    property_tax: Decimal | None


@dataclass(frozen=True)
class ScheduleACheck:
    """Informational itemize-vs-standard check (the liability stays standard).

    ``itemized_estimate`` sums the sourced ``components``;
    ``margin = itemized_estimate - standard_deduction`` (a POSITIVE margin
    means itemizing could lower taxable income -- the card copy is T-P4's
    job).  ``property_tax_included`` is ``False`` (no property-tax source);
    ``salt_cap_not_applied`` is ``True`` (no SALT cap constant is seeded, so
    none is applied).  The itemized election does NOT feed the v1 liability.
    """

    components: ItemizedComponents
    itemized_estimate: Decimal
    standard_deduction: Decimal
    margin: Decimal
    property_tax_included: bool
    salt_cap_not_applied: bool


@dataclass(frozen=True)
class TaxChips:
    """The hero band's rate + timing chips.

    ``effective_rate`` is ``(federal_liability + state_liability) /
    box1_wages`` quantised to four places, or ``None`` when box 1 wages are
    non-positive (zero-safe).  ``marginal_rate`` is the bracket rate
    containing federal taxable.  ``next_stub`` is the next payday strictly
    after ``today`` within the year, or ``None``.  The federal / NC refund
    chips of the same hero band read from the report's ``refund`` bundle
    (not duplicated here).
    """

    effective_rate: Decimal | None
    marginal_rate: Decimal
    next_stub: date | None


@dataclass(frozen=True)
class FilingInputs:
    """The primary profile's filing identity (IDs for logic, names to show).

    ``filing_status_id`` drives logic; ``filing_status_name`` is the raw ref
    name for display (the T-P4 card formats it the way the settings screen
    does, ``name|replace('_', ' ')|title``).  ``state_code`` is the primary
    profile's state.
    """

    filing_status_id: int
    filing_status_name: str
    state_code: str


@dataclass(frozen=True)
class ModellingDisclosures:
    """The modelling caveats the assumptions card surfaces.

    ``calibration_active`` / ``calibration_pay_stub_date`` describe the
    primary profile's calibration; ``checkpoint_as_of_date`` is the measured
    stub date (``None`` = fully modeled); ``pretax_modeled_for_elapsed`` is
    ``True`` when a checkpoint exists (the elapsed withholding is measured
    but its pre-tax is still modeled).  ``actc_modeled`` is always ``True``
    (T-P5: the refundable Additional Child Tax Credit IS now modeled, so the
    liability's zero clamp no longer swallows a CTC-heavy household's refund);
    ``phase_out_not_modeled`` is always ``True`` (the CTC/ACTC MAGI phase-outs
    -- $400k MFJ / $200k other -- are not applied; the developer's AGI is far
    below them).
    """

    calibration_active: bool
    calibration_pay_stub_date: date | None
    checkpoint_as_of_date: date | None
    pretax_modeled_for_elapsed: bool
    actc_modeled: bool
    phase_out_not_modeled: bool


@dataclass(frozen=True)
class TaxAssumptions:
    """The non-liability disclosures the T-P4 assumptions card renders.

    Carries only what is NOT already on ``report.liability`` (which owns the
    standard deduction, dependent counts + credit amounts, state rate +
    state standard deduction, and tax year the card also shows): the primary
    profile's ``filing`` identity, the ``disclosures`` caveats, and the
    multi-profile note.  ``filing_inputs_from`` names the primary profile
    ONLY when more than one profile is active (``None`` for the
    single-profile case).
    """

    filing: FilingInputs
    disclosures: ModellingDisclosures
    active_profile_count: int
    filing_inputs_from: str | None


@dataclass(frozen=True)
class TaxReport:  # pylint: disable=too-many-instance-attributes
    """The complete analytics Taxes tab dataset for one filer and year.

    One bundle per T-P4 card: ``refund`` + ``chips`` (hero band),
    ``liability`` (derivation ledger; also the SSOT for tax year, standard
    deduction, credits, and state rate the assumptions card shows),
    ``w2_preview`` (hybrid W-2), ``schedule_a`` (Schedule A check),
    ``assumptions`` (assumptions card).  ``withholding`` carries the shared
    measured/modeled split the hero and W-2 both read.

    ``primary_profile_id`` is the resolved primary profile's id, carried so
    the route can wire the YTD checkpoint card's form action (and its
    ``latest_checkpoint`` read) WITHOUT re-deriving the primary-profile
    rule -- this module's resolution stays the single implementation.

    Pylint: ``too-many-instance-attributes`` (8/7) -- suppressed because
    this is the one top-level bundle the Taxes tab renders: one cohesive
    field per locked-anatomy card plus the primary-profile pointer; the
    per-card sub-bundles already group everything groupable, so the only
    "fix" would be an artificial extra nesting level no consumer reads as
    a unit.
    """

    refund: RefundEstimate
    withholding: WithholdingSummary
    liability: AnnualLiability
    w2_preview: W2Preview
    schedule_a: ScheduleACheck
    chips: TaxChips
    assumptions: TaxAssumptions
    primary_profile_id: int


def compute_tax_report(user_id: int, year: int, today: date) -> TaxReport | None:
    """Compute the analytics Taxes tab dataset for *user_id* and *year*.

    Resolves the baseline scenario and the user's ACTIVE salary profiles
    (ordered ``(sort_order, name)`` -- the first is the PRIMARY filer whose
    filing inputs drive the liability), loads the year's pay periods the way
    the year-end orchestrator does, and fuses the T-P1 liability and T-P2
    withholding-to-date into the refund, chips, W-2 preview, and Schedule A
    surfaces.

    Args:
        user_id: The owning user (scopes every query below).
        year: The tax year to report on.
        today: The display-timezone "today" the caller resolves (the
            producer stays Flask-free); drives the next-payday chip only.

    Returns:
        The populated :class:`TaxReport`, or ``None`` when the user has no
        baseline scenario or no active salary profile (T-P4 renders an
        empty state).  A user with profiles but no pay periods for the year
        degrades to an all-modeled zero report (no crash).
    """
    scenario = get_baseline_scenario(user_id)
    if scenario is None:
        return None
    profiles = load_active_salary_profiles(user_id, scenario.id)
    if not profiles:
        return None

    primary = profiles[0]
    periods = _load_year_periods(user_id, year)
    configs = load_tax_configs_for_year(user_id, primary, year)

    withholding = _aggregate_withholding(user_id, year, profiles, periods)
    modeled_pretax = _aggregate_modeled_pretax(user_id, year, profiles, periods)

    liability = compute_annual_liability(
        user_id, primary, year, withholding.total.gross, modeled_pretax,
    )
    box1_wages = withholding.total.gross - modeled_pretax
    next_stub = _next_stub(periods, today)

    return TaxReport(
        refund=_build_refund(withholding, liability),
        withholding=withholding,
        liability=liability,
        w2_preview=_build_w2_preview(
            withholding, box1_wages, configs.get("fica_config"),
        ),
        schedule_a=_build_schedule_a(
            user_id, year, scenario.id, withholding, liability,
        ),
        chips=_build_chips(
            liability, box1_wages, configs.get("bracket_set"), next_stub,
        ),
        assumptions=_build_assumptions(primary, withholding, profiles),
        primary_profile_id=primary.id,
    )


# ── Data loading (year-end orchestrator precedent) ────────────────


def _load_year_periods(user_id: int, year: int) -> list:
    """Return the user's pay periods whose payday falls in *year*.

    Mirrors ``year_end_summary_service._data._load_common_data``: pay
    periods with ``start_date`` in the calendar year, ordered by
    ``period_index``.

    Args:
        user_id: The owning user.
        year: The calendar/tax year to scope periods to.

    Returns:
        The year's :class:`PayPeriod` list (possibly empty).
    """
    return (
        db.session.query(PayPeriod)
        .filter(
            PayPeriod.user_id == user_id,
            PayPeriod.start_date >= date(year, 1, 1),
            PayPeriod.start_date <= date(year, 12, 31),
        )
        .order_by(PayPeriod.period_index)
        .all()
    )


# ── Withholding + pre-tax aggregation (single-filer sum) ──────────


def _aggregate_withholding(
    user_id: int, year: int, profiles: list, periods: list,
) -> WithholdingSummary:
    """Sum withholding-to-date across the active profiles (one filer).

    Calls :func:`tax_withholding_service.compute_withholding_to_date` per
    profile -- so the measured-checkpoint + modeled-remainder hybrid (and
    its full-year cumulative context) is owned by T-P2, not re-derived --
    and sums the ``total`` / ``measured`` / ``modeled`` sides component-wise.
    ``measured_through`` is the LATEST stub date any profile is measured
    through (``None`` when all are fully modeled).

    Args:
        user_id: The owning user (per-user tax configs).
        year: The tax year.
        profiles: The active salary profiles.
        periods: The year's pay periods (passed to each profile's hybrid).

    Returns:
        The summed :class:`WithholdingSummary`.
    """
    totals: list[WithholdingComponents] = []
    measures: list[WithholdingComponents] = []
    models: list[WithholdingComponents] = []
    stub_dates: list[date] = []
    has_checkpoint = False

    for profile in profiles:
        wtd = compute_withholding_to_date(user_id, profile, year, periods)
        totals.append(wtd.total)
        measures.append(wtd.measured)
        models.append(wtd.projected)
        if wtd.checkpoint is not None:
            has_checkpoint = True
        if wtd.measured_through is not None:
            stub_dates.append(wtd.measured_through)

    return WithholdingSummary(
        total=_sum_components(totals),
        measured=_sum_components(measures),
        modeled=_sum_components(models),
        measured_through=max(stub_dates) if stub_dates else None,
        has_checkpoint=has_checkpoint,
    )


def _aggregate_modeled_pretax(
    user_id: int, year: int, profiles: list, periods: list,
) -> Decimal:
    """Sum the FULL-year modeled pre-tax across the active profiles.

    The checkpoint captures no pre-tax figure, so the annual pre-tax that
    reduces the liability's taxable base is modelled over EVERY one of the
    year's periods (not just the remainder): ``project_salary`` is run over
    the full period list and each breakdown's ``deductions.total_pre_tax``
    is summed.  Calibration-aware (matching the withholding hybrid), though
    calibration overrides only the tax lines, never the pre-tax deductions.

    Args:
        user_id: The owning user (per-user tax configs).
        year: The tax year (single-year config set).
        profiles: The active salary profiles.
        periods: The year's pay periods.

    Returns:
        The summed modeled annual pre-tax (``ZERO`` when there are no
        periods).
    """
    total = ZERO
    if not periods:
        return total
    for profile in profiles:
        tax_configs = load_tax_configs_for_year(user_id, profile, year)
        breakdowns = paycheck_calculator.project_salary(
            profile, periods, tax_configs, calibration=profile.calibration,
        )
        total += sum(
            (bd.deductions.total_pre_tax for bd in breakdowns), ZERO,
        )
    return total


def _sum_components(
    parts: list[WithholdingComponents],
) -> WithholdingComponents:
    """Sum a list of :class:`WithholdingComponents` field-by-field.

    Args:
        parts: The per-profile components to add.

    Returns:
        The component-wise sum (:data:`_ZERO_COMPONENTS` for an empty list).
    """
    if not parts:
        return _ZERO_COMPONENTS
    return WithholdingComponents(
        gross=sum((p.gross for p in parts), ZERO),
        federal=sum((p.federal for p in parts), ZERO),
        state=sum((p.state for p in parts), ZERO),
        social_security=sum((p.social_security for p in parts), ZERO),
        medicare=sum((p.medicare for p in parts), ZERO),
    )


# ── Surface builders ──────────────────────────────────────────────


def _build_refund(
    withholding: WithholdingSummary, liability: AnnualLiability,
) -> RefundEstimate:
    """Build the federal + state refund from withheld minus liability.

    Args:
        withholding: The summed withholding-to-date.
        liability: The filing-time liability (federal + state layers).

    Returns:
        The :class:`RefundEstimate` (positive = refund, negative = owed).
    """
    # Federal refund adds the refundable ACTC: withheld minus the
    # nonrefundable liability PLUS the refundable Additional Child Tax Credit
    # (T-P5).  For a household whose CTC exceeds its tax, the ACTC IS the
    # refund (the liability floors at zero).
    federal_refund = (
        withholding.total.federal
        - liability.federal.liability
        + liability.federal.refundable_actc
    )
    state_refund = withholding.total.state - liability.state.liability
    return RefundEstimate(
        federal_refund=federal_refund,
        state_refund=state_refund,
        total_refund=federal_refund + state_refund,
    )


def _build_w2_preview(
    withholding: WithholdingSummary, box1_wages: Decimal, fica_config,
) -> W2Preview:
    """Build the per-filer hybrid W-2 preview from the summed withholding.

    Box 3 (SS wages) caps the gross at the year's ``ss_wage_base``; with no
    FICA config the cap is unknown and the raw gross is used.  Box 1 / 16
    are the wage base (``box1_wages``); box 5 is raw gross; the withholding
    boxes are the hybrid lines.

    Args:
        withholding: The summed withholding-to-date.
        box1_wages: Hybrid gross less the modeled annual pre-tax.
        fica_config: The year's FicaConfig (or ``None``) for the SS cap.

    Returns:
        The populated :class:`W2Preview`.
    """
    gross = withholding.total.gross
    if fica_config is not None:
        ss_wages = min(gross, Decimal(str(fica_config.ss_wage_base)))
    else:
        ss_wages = gross
    return W2Preview(
        wages=W2Wages(
            box1_wages=box1_wages,
            box3_ss_wages=ss_wages,
            box5_medicare_wages=gross,
            box16_state_wages=box1_wages,
        ),
        withheld=W2Withheld(
            box2_federal=withholding.total.federal,
            box4_ss_withheld=withholding.total.social_security,
            box6_medicare_withheld=withholding.total.medicare,
            box17_state_withheld=withholding.total.state,
        ),
        measured=withholding.measured,
        modeled=withholding.modeled,
        measured_through=withholding.measured_through,
    )


def _build_schedule_a(
    user_id: int,
    year: int,
    scenario_id: int,
    withholding: WithholdingSummary,
    liability: AnnualLiability,
) -> ScheduleACheck:
    """Build the informational Schedule A itemize-vs-standard check.

    Mortgage interest REUSES the year-end summary's ledger-actual +
    schedule-projected hybrid: it loads the user's debt accounts, generates
    their amortization schedules via the shared
    :func:`net_worth_kernel.generate_debt_schedules`, and delegates to the
    same ``_compute_mortgage_interest`` the orchestrator calls (no second
    implementation).  The state income-tax component is the hybrid state
    withholding.  Property tax is omitted (no unambiguous source).

    Args:
        user_id: The owning user (scopes the debt-account load).
        year: The tax year (interest is summed in the year PAID).
        scenario_id: The baseline scenario (scopes schedules + ledger read).
        withholding: The summed withholding-to-date (state component).
        liability: The liability (its federal standard deduction).

    Returns:
        The populated :class:`ScheduleACheck`.
    """
    debt_accounts = _load_debt_accounts(user_id)
    debt_schedules = net_worth_kernel.generate_debt_schedules(
        debt_accounts, scenario_id,
    )
    mortgage_interest = _compute_mortgage_interest(
        year, debt_schedules, scenario_id,
    )
    state_income_tax = withholding.total.state
    itemized_estimate = mortgage_interest + state_income_tax
    standard_deduction = liability.federal.standard_deduction
    return ScheduleACheck(
        components=ItemizedComponents(
            mortgage_interest=mortgage_interest,
            state_income_tax=state_income_tax,
            property_tax=None,
        ),
        itemized_estimate=itemized_estimate,
        standard_deduction=standard_deduction,
        margin=itemized_estimate - standard_deduction,
        property_tax_included=False,
        salt_cap_not_applied=True,
    )


def _load_debt_accounts(user_id: int) -> list:
    """Return the user's active amortizing (loan) accounts.

    Mirrors ``_load_common_data``'s ``debt_accounts`` selection: the shared
    :func:`~app.services.projection_inputs.load_active_accounts_with_types`
    loader (account_type eager-loaded, no N+1) filtered to the accounts
    whose ``account_type.has_amortization`` is set.

    Args:
        user_id: The owning user.

    Returns:
        The active loan account list (possibly empty).
    """
    return [
        a for a in load_active_accounts_with_types(user_id)
        if a.account_type and a.account_type.has_amortization
    ]


def _build_chips(
    liability: AnnualLiability,
    box1_wages: Decimal,
    bracket_set,
    next_stub: date | None,
) -> TaxChips:
    """Build the hero-band rate + timing chips.

    ``effective_rate`` = ``(federal + state liability) / box1_wages``
    quantised to four places, ``None`` when box 1 is non-positive (zero-safe
    -- the zero-wage / zero-period degrade case).  ``marginal_rate`` is the
    bracket rate containing federal taxable (``ZERO`` when no bracket set
    resolves).

    Args:
        liability: The liability (federal taxable + both liabilities).
        box1_wages: The effective-rate denominator.
        bracket_set: The year's TaxBracketSet (or ``None``).
        next_stub: The precomputed next-payday date (or ``None``).

    Returns:
        The populated :class:`TaxChips`.
    """
    combined_liability = liability.federal.liability + liability.state.liability
    if box1_wages > ZERO:
        effective_rate = (combined_liability / box1_wages).quantize(
            _RATE_QUANTUM, rounding=ROUND_HALF_UP,
        )
    else:
        effective_rate = None

    brackets = bracket_set.brackets if bracket_set is not None else []
    marginal_rate = tax_calculator.marginal_rate_for(
        liability.federal.taxable, brackets,
    )
    return TaxChips(
        effective_rate=effective_rate,
        marginal_rate=marginal_rate,
        next_stub=next_stub,
    )


def _next_stub(periods: list, today: date) -> date | None:
    """Return the earliest payday strictly after *today* among *periods*.

    Args:
        periods: The year's pay periods (each ``start_date`` is its payday).
        today: The reference date.

    Returns:
        The next payday ``date`` after ``today``, or ``None`` when none
        remains in the year.
    """
    future = [p.start_date for p in periods if p.start_date > today]
    return min(future) if future else None


def _build_assumptions(
    primary, withholding: WithholdingSummary, profiles: list,
) -> TaxAssumptions:
    """Build the structured non-liability disclosures the T-P4 card renders.

    Filing inputs come from the PRIMARY profile; when more than one profile
    is active the ``filing_inputs_from`` field names it (else ``None``).
    ``pretax_modeled_for_elapsed`` fires when any profile carries a
    checkpoint (the elapsed WITHHOLDING is measured but its pre-tax is not).

    Args:
        primary: The primary :class:`SalaryProfile` (filing inputs + state +
            calibration).
        withholding: The summed withholding (checkpoint presence + date).
        profiles: The active profiles (for the multi-profile disclosure).

    Returns:
        The populated :class:`TaxAssumptions`.
    """
    calibration = getattr(primary, "calibration", None)
    calibration_active = bool(
        calibration is not None and getattr(calibration, "is_active", False)
    )
    return TaxAssumptions(
        filing=FilingInputs(
            filing_status_id=int(primary.filing_status_id),
            filing_status_name=primary.filing_status.name,
            state_code=primary.state_code,
        ),
        disclosures=ModellingDisclosures(
            calibration_active=calibration_active,
            calibration_pay_stub_date=(
                calibration.pay_stub_date if calibration_active else None
            ),
            checkpoint_as_of_date=withholding.measured_through,
            pretax_modeled_for_elapsed=withholding.has_checkpoint,
            actc_modeled=True,
            phase_out_not_modeled=True,
        ),
        active_profile_count=len(profiles),
        filing_inputs_from=primary.name if len(profiles) > 1 else None,
    )
