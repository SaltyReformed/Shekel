"""
Shekel Budget App -- The Retirement Picture At A Plan Point (C2-f2d-2)

**ONE producer of "what does this retirement plan look like", and the ONE
loader a ``/retirement`` render runs on.**

Until plan step C2-f2d-2 there were two implementations of that one thing.
``retirement_dashboard_service.compute_gap_data`` computed the picture at the
STORED retirement date under optional what-if sliders; ``retirement_levers._probe``
computed it at a SHIFTED retirement date under the stored sliders.  They are the
same function over a union of parameters, written twice -- and on the default page
load the lever solver's month-0 probe recomputed, from its own loaded inputs, the
exact picture the readiness hero had already drawn beside it.  Measured on a
production clone: funded ratio ``0.7463``, required ``$1,120,707.00``, projected
after tax ``$836,398.65``, derived twice and agreeing.  Ledger row **P57**.

Agreeing is not the same as being one answer.  Two derivations agree until
somebody edits one of them, and nothing in the app can notice the day they part
-- the page would simply show a funded verdict and a lever card solved against a
different plan, both stated as fact.  This module replaces the second derivation
rather than reconciling it.

**The two values that make it one producer.**

* :class:`PlanPoint` -- WHICH plan.  The stored plan is
  :data:`STORED_PLAN`; every other point states how it differs, whether that is
  the retire-later lever's month offset or the assumptions rail's three what-if
  sliders.  It is frozen and hashable because it is the memo key.
* :class:`RetirementInputs` -- everything a render loads, loaded ONCE, and
  point-INDEPENDENT by construction: the read pass, the gap inputs, the stored
  retirement date, the projection context and its batch.  A point never reloads
  any of it, which is why the retire-later binary search can probe ten candidate
  dates for the price of one query set.

:func:`picture_at` is the join: it derives the picture at a point and memoizes it
on the inputs, so the readiness hero and the lever card asking for the same point
receive the same :class:`RetirementPicture` object rather than two equal ones.

All functions take plain data and return plain data.  No Flask imports.
"""

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCadence, PeriodWindow
from app.services.retirement_dashboard_service import (
    GapInputs,
    PensionSummary,
    compute_gap_net_biweekly,
    compute_pension_summary,
    load_gap_inputs,
    resolve_estimated_tax_rate,
    resolve_planned_retirement_date,
    resolve_swr_fraction,
)
from app.services.retirement_gap_calculator import (
    RetirementGapAnalysis,
    calculate_gap,
    funded_ratio_state,
)
from app.services.retirement_projection import (
    ProjectionBatch,
    RetirementProjectionContext,
    build_employer_salary_basis,
    build_projection_context,
    load_projection_batch,
    project_accounts_with_batch,
    resolve_projection_axis,
)
from app.utils.dates import add_months

# Percentage scaler.  ``assumed_annual_return`` is stored as a fractional
# Decimal; the blended average is formed in PERCENT and quantized there before
# being handed back as a fraction, which is where the two-decimal rounding
# happens.  See :meth:`RetirementPicture.blended_return` for why that order is
# load-bearing rather than cosmetic.
_PCT_SCALE = Decimal("100")

# Two-decimal quantum for the blended percentage, matching the assumptions
# rail's ``"%.2f"|format`` so the rate the contribution solver divides by is the
# rate the page displays, to the digit.
_PCT_QUANTUM = Decimal("0.01")

# Default assumed-annual-return percentage when no account carries weight to
# blend from.  7% matches the S&P 500's long-run inflation-adjusted total return
# (Damodaran historical-returns dataset, ~1928-2024) and is the conservative
# midpoint of common retirement-planning assumptions (5-10%).
_DEFAULT_RETURN_PCT = Decimal("7.00")

# Fork F1's explicit rate: an UNSET estimated retirement tax rate is treated as
# an explicit zero with a flag the assumptions panel surfaces, never as a
# truthiness fallback and never as a reason to skip the after-tax block.
_UNSET_TAX_RATE = Decimal("0")

# Funded means the quantized funded ratio reaches at least this value.
_FULLY_FUNDED = Decimal("1")


@dataclass(frozen=True)
class PlanPoint:
    """WHICH retirement plan a picture is of: the stored one, plus deltas.

    Frozen and hashable because it is :func:`picture_at`'s memo key, and the
    memo is what makes the readiness hero and the lever card's baseline ONE
    object instead of two equal ones.

    Every field defaults to "no deviation", so :data:`STORED_PLAN` is
    ``PlanPoint()`` and a caller states only what it varies.

    Attributes:
        month_offset: Whole months added to the stored retirement date -- the
            retire-later lever's axis.  ``0`` is the stored plan
            (:func:`app.utils.dates.add_months` with 0 months is the identity).
        swr_override: A fractional safe-withdrawal rate replacing the stored
            one, or ``None`` to use the stored rate.
        return_rate_override: A fractional annual return applied UNIFORMLY to
            every account, replacing each account's stored
            ``assumed_annual_return``, or ``None``.
        merit_horizon_override: A merit-raise horizon in years replacing the
            stored ``merit_raise_horizon_years``, or ``None``.
    """

    month_offset: int = 0
    swr_override: Decimal | None = None
    return_rate_override: Decimal | None = None
    merit_horizon_override: int | None = None


#: The plan as STORED: no delay, and every assumption read from the user's own
#: settings.  The ``/retirement`` page's own point, and the baseline every
#: what-if is stated as a delta against.
STORED_PLAN = PlanPoint()


@dataclass(frozen=True)
class RetirementInputs:
    """Everything one ``/retirement`` render loads, loaded exactly once.

    **Every field is point-INDEPENDENT**, which is the property that makes one
    load serve every candidate plan: the retire-later search probes up to ten
    dates and none of them re-queries.  What DOES vary with the point -- the
    salary path, the pension benefit, the employer salary basis, the projection
    axis and the per-account walk -- is derived in :func:`picture_at` from these.

    Built by :func:`load_retirement_inputs`, which a ROUTE calls; nothing below
    a route builds one, for the same reason nothing below a route builds a read
    pass (plan step C2-f2d-1, ledger row **P43**).

    Attributes:
        balance_ctx: The render's
            :class:`~app.services.balance_at.BalanceContext` -- the owner, the
            baseline scenario, the pinned clock, and the memos that resolve each
            loan and derive the pay calendar once for the whole render.
        gap: The :class:`~app.services.retirement_dashboard_service.GapInputs`
            bundle: settings, active pensions, active salary profiles, the
            current-pay snapshot, the stored merit horizon and the owner's pay
            cadence.
        base_date: The STORED plan's resolved retirement date (a pension's beats
            the settings', latest pension wins), or ``None`` when neither
            supplies one -- the page's no-horizon state.
        base_ctx: The projection context at the stored plan.  Complete and
            valid as it stands; :func:`picture_at` derives each point's context
            from it with :func:`dataclasses.replace`, which re-queries nothing
            because the account set and the period calendar do not move with a
            candidate date.
        batch: The date-independent projection batch (deductions, contributions,
            params, balances) loaded once from *base_ctx*.  Shared across every
            point, which is what makes its seed memo a hit rather than a second
            fold -- the single largest cost this step removes.
        picture_memo: ``{PlanPoint: RetirementPicture}``.  Not a field a caller
            reads: it is :func:`picture_at`'s store, held here because the
            memo's LIFETIME is the render's.  Excluded from ``repr`` and from
            equality for the reason ``ProjectionBatch.seed_memo`` is: a cache is
            not part of the value's identity.
    """

    balance_ctx: BalanceContext
    gap: GapInputs
    base_date: date | None
    base_ctx: RetirementProjectionContext
    batch: ProjectionBatch
    picture_memo: "dict[PlanPoint, RetirementPicture]" = field(
        default_factory=dict, repr=False, compare=False,
    )

    @property
    def stored_tax_rate(self) -> Decimal | None:
        """The estimated retirement tax rate as the user STORED it.

        On the INPUTS rather than on a picture because it is point-independent:
        no :class:`PlanPoint` varies it, so every picture derived from these
        inputs is computed at the same rate and a per-picture copy would be one
        fact under N keys.  The ``no_horizon`` lever state reads it without
        deriving a picture at all, which is the case that proves the placement.

        Returns:
            The stored fractional rate -- an explicit zero preserved (E-12) --
            or ``None`` when settings are absent or the column is NULL, which
            is what drives :attr:`tax_rate_missing`.
        """
        return resolve_estimated_tax_rate(self.gap.settings)

    @property
    def tax_rate_missing(self) -> bool:
        """Whether the owner has never stated an estimated retirement tax rate.

        Returns:
            True when the rate is unset (fork F1's flag, which the assumptions
            panel surfaces as "not set -- 0% assumed").  A stored ``0%`` is a
            real answer and returns False.
        """
        return self.stored_tax_rate is None

    @property
    def effective_tax_rate(self) -> Decimal:
        """The rate every after-tax figure here is actually computed at.

        Returns:
            The stored rate, or fork F1's explicit ``Decimal("0")`` when unset
            -- which is what keeps the after-tax block always populated instead
            of the analysis silently dropping its own frame.
        """
        stored = self.stored_tax_rate
        return _UNSET_TAX_RATE if stored is None else stored


def load_retirement_inputs(balance_ctx: BalanceContext) -> RetirementInputs:
    """Load a ``/retirement`` render's point-independent inputs, once.

    **A ROUTE calls this and hands the result down.**  Before plan step
    C2-f2d-2 the verdict producer and the lever solver each ran this whole load
    for themselves: measured on a production clone, one page render issued 179
    queries of which 86 -- 48% -- were the second copy.

    Args:
        balance_ctx: The render's
            :class:`~app.services.balance_at.BalanceContext`, pinned once by the
            route.  Its owner scopes every query below and its ``as_of`` is the
            one clock every picture derived from these inputs is measured at.

    Returns:
        The :class:`RetirementInputs` bundle, with an empty picture memo.

    Raises:
        PayCalendarError: The owner has no resolvable pay cadence -- no
            ``budget.pay_schedule`` row and no pay period to infer one from.
            See :func:`app.services.retirement_dashboard_service.load_gap_inputs`.
    """
    gap = load_gap_inputs(balance_ctx)
    base_date = resolve_planned_retirement_date(gap.pensions, gap.settings)
    base_ctx = build_projection_context(
        balance_ctx,
        gap.pay.all_periods,
        gap.pay.current_period,
        base_date,
        None,
        build_employer_salary_basis(
            gap.salary_profiles, base_date, gap.merit_horizon_years,
        ),
    )
    return RetirementInputs(
        balance_ctx=balance_ctx,
        gap=gap,
        base_date=base_date,
        base_ctx=base_ctx,
        batch=load_projection_batch(base_ctx),
    )


@dataclass(frozen=True)
class RetirementPicture:
    """The whole retirement picture at ONE plan point.

    What the readiness hero renders, what the chart plots, and what each
    retire-later probe compares -- one record, produced in one place
    (:func:`picture_at`).

    **Everything derivable is a PROPERTY rather than a field.**  The funded
    ratio, the tax-rate facts, the safe-withdrawal rate and the blended return
    are all functions of what is stored here, and storing them beside their own
    inputs is the denormalization this arc exists to remove: a stored copy is a
    thing that can disagree with what it was copied from.

    Attributes:
        inputs: The :class:`RetirementInputs` this picture was derived from.
            Carried so a consumer takes ONE object -- the settings, the
            pensions, the salary profiles, the pay cadence and the clock are all
            reachable through it, and every one of them was a separately
            published key on the dict this record replaced.
        point: The :class:`PlanPoint` this is the picture OF.
        retirement_date: The horizon this picture was projected to -- the stored
            date shifted by ``point.month_offset`` -- or ``None`` when the owner
            has set no retirement date at all.
        axis: The :class:`~app.services.pay_calendar.PeriodWindow` every
            projection below ran over: the owner's own paychecks from the read
            pass's clock to :attr:`retirement_date`.  Carried rather than
            rebuilt (plan step C2-e): its LENGTH is the countdown's
            "paychecks remaining" and the contribution lever's annuity factor
            folds over exactly it, so a rebuild that came back a different
            length would solve for a contribution that does not close the gap.
        projections: One dict per retirement / investment account (see
            :func:`app.services.retirement_projection._project_one_account`).
        pension: The :class:`~app.services.retirement_dashboard_service.PensionSummary`
            at this point -- the summed monthly benefit, the salary-by-year
            series behind it, and the per-pension derivation entries the page
            footer states one line each from.
        net: The NET-frame :class:`~app.services.retirement_gap_calculator.RetirementGapAnalysis`
            (Gate A ruling 2): every figure after the estimated retirement tax,
            so the verdict compares like with like.  Computed at the EXPLICIT
            (possibly fork-F1 zero) rate, which is what keeps the after-tax
            fields always populated.
    """

    inputs: RetirementInputs
    point: PlanPoint
    retirement_date: date | None
    axis: PeriodWindow
    projections: list[dict]
    pension: PensionSummary
    net: RetirementGapAnalysis

    @property
    def as_of(self) -> date:
        """The read pass's clock: the day the axis opens after.

        Returns:
            The pinned ``as_of``, so a page reporting "years remaining" beside
            this projection measures it from the same day the projection did.
        """
        return self.inputs.balance_ctx.as_of

    @property
    def pay_cadence(self) -> PayCadence:
        """How often the owner is paid.

        Returns:
            The owner's :class:`~app.services.pay_calendar.PayCadence`, which is
            what turns one paycheck into monthly income.
        """
        return self.inputs.gap.pay_cadence

    @property
    def safe_withdrawal_rate(self) -> Decimal:
        """The fractional SWR this picture's required savings were solved at.

        Read off the analysis rather than stored a second time: ``calculate_gap``
        already carries the rate it used, so there is no copy to keep in step.

        Returns:
            The fractional rate (``0.04`` for the 4% rule).
        """
        return self.net.safe_withdrawal_rate

    @property
    def blended_return(self) -> Decimal:
        """The annual return fraction this picture's growth actually ran at.

        ONE definition, where there were three per render before plan step
        C2-f2d-2: the assumptions rail's displayed rate, the readiness chart's
        needed-path rate, and the contribution lever's annuity rate each derived
        it, and the lever's is the one that divides the shortfall to produce the
        "contribute $X per period" the user is told to act on.  Three
        derivations of the rate a solver divides by is three chances for the
        page to display a return its own advice was not solved at.

        A uniform ``return_rate_override`` IS the blend -- every account's weight
        carries the same rate -- which is why it short-circuits rather than being
        blended against stored rates it has already replaced.

        **The percent round-trip is deliberate.**  The average is formed in
        percent and quantized to two decimals THERE, then scaled back to a
        fraction, because the quantized percent is what the rail displays: doing
        the division in fractions instead would hand the annuity factor a rate
        with more digits than the page states.

        Returns:
            The fractional annual return (e.g. ``Decimal("0.105")`` for 10.5%).
        """
        if self.point.return_rate_override is not None:
            return self.point.return_rate_override
        return _stored_blend_percent(
            self.projections, self.inputs.batch.params_by_account,
        ) / _PCT_SCALE

    @property
    def funded_state(self) -> tuple[Decimal | None, bool]:
        """The after-tax funded ratio and the zero-requirement state.

        Returns:
            ``(funded_ratio, no_savings_needed)`` -- see
            :func:`app.services.retirement_gap_calculator.funded_ratio_state`.
        """
        return funded_ratio_state(self.net)

    @property
    def is_funded(self) -> bool:
        """Whether this plan reaches full funding.

        Funded means the requirement is zero (the pension covers the whole gap)
        or the quantized funded ratio reaches 100%.

        Returns:
            bool -- the predicate the retire-later binary search bisects on.
        """
        funded_ratio, no_savings_needed = self.funded_state
        return no_savings_needed or funded_ratio >= _FULLY_FUNDED


def _stored_blend_percent(projections, params_by_account) -> Decimal:
    """Balance-weight each account's STORED annual return, as a percentage.

    **The params come off the render's batch, not a query.**  This loop issued
    one ``InvestmentParams`` lookup per account and ran three times per render
    until plan step C2-f2d-2, while
    :func:`~app.services.retirement_projection.load_projection_batch` had already
    loaded every one of those rows in a single ``IN``.

    Zero is a real value (E-12), on both terms.  A stable-value sleeve at
    exactly 0.00% return must contribute its balance to the denominator -- a
    truthiness check dropped it entirely, and two $100k accounts at 0% and 7%
    then reported 7.00% instead of the true blended 3.50%.  A zero BALANCE is
    equally real: it contributes weight zero rather than being skipped.

    Args:
        projections: The per-account projection dicts, each carrying its
            ``account`` and its ``current_balance``.
        params_by_account: The batch's ``{account_id: InvestmentParams}``;
            accounts with no params row are absent and contribute nothing,
            because no stored rate is known for them.

    Returns:
        The weighted average as a Decimal PERCENT quantized to two decimals, or
        :data:`_DEFAULT_RETURN_PCT` when no account carries a known rate.
    """
    total_balance = Decimal("0")
    weighted_return = Decimal("0")
    for proj in projections:
        params = params_by_account.get(proj["account"].id)
        if params is None or params.assumed_annual_return is None:
            continue
        # INDEXED, not defaulted: ``_project_one_account`` writes
        # ``current_balance`` on EVERY projection dict it returns, so a missing
        # key is a producer defect and fails loud rather than substituting a
        # different account's fact.
        balance = proj["current_balance"]
        total_balance += balance
        weighted_return += balance * params.assumed_annual_return
    if total_balance > 0:
        return (
            weighted_return / total_balance * _PCT_SCALE
        ).quantize(_PCT_QUANTUM)
    return _DEFAULT_RETURN_PCT


def picture_at(
    inputs: RetirementInputs, point: PlanPoint,
) -> RetirementPicture:
    """The retirement picture at *point*, derived once per render.

    **The memo is the whole point of this function existing rather than
    :func:`_derive_picture` being called directly.**  The ``/retirement`` page
    renders the readiness verdict at :data:`STORED_PLAN` and then runs the
    retire-later search, whose month-0 probe is that same point; without the
    memo the page computes one picture twice and displays it as two independent
    facts (ledger row **P57**).  With it, the lever card's baseline IS the object
    the hero rendered -- not an equal one.

    Keyed on the point's VALUES, because that is what the answer is a function
    of: two callers asking for the same plan are asking one question.

    Args:
        inputs: The render's :class:`RetirementInputs`, which owns the memo.
        point: The :class:`PlanPoint` to picture.

    Returns:
        The :class:`RetirementPicture` at *point*.
    """
    if point not in inputs.picture_memo:
        inputs.picture_memo[point] = _derive_picture(inputs, point)
    return inputs.picture_memo[point]


def _derive_picture(
    inputs: RetirementInputs, point: PlanPoint,
) -> RetirementPicture:
    """Compute the picture at *point* from the render's loaded inputs.

    BOTH sides of the gap move with the plan: a later date extends the
    merit-horizon salary path, the pension's years of service and its
    high-salary window, the employer salary basis and the growth horizon, and it
    re-derives the income target from that longer path -- so the required target
    moves as well as the projected balance.  Every one of those is recomputed
    here; nothing that was LOADED is.

    Args:
        inputs: The render's loaded inputs.
        point: The plan point to derive.

    Returns:
        The :class:`RetirementPicture`, uncached (:func:`picture_at` caches it).
    """
    gap = inputs.gap
    merit_horizon = (
        point.merit_horizon_override
        if point.merit_horizon_override is not None
        else gap.merit_horizon_years
    )
    retirement_date = (
        None if inputs.base_date is None
        else add_months(inputs.base_date, point.month_offset)
    )
    pension = compute_pension_summary(
        gap.pensions, merit_horizon, point.month_offset,
    )
    # Every point-dependent field replaced together, from a context the render
    # built once: the account query and the period calendar do not move with a
    # candidate date, so this re-queries nothing.
    ctx = replace(
        inputs.base_ctx,
        planned_retirement_date=retirement_date,
        return_rate_override=point.return_rate_override,
        employer_salary_basis=build_employer_salary_basis(
            gap.salary_profiles, retirement_date, merit_horizon,
        ),
    )
    axis = resolve_projection_axis(ctx)
    projections = project_accounts_with_batch(ctx, inputs.batch, axis)
    net = calculate_gap(
        net_biweekly_pay=compute_gap_net_biweekly(
            gap.salary_profiles, retirement_date, gap.pay,
            pension.salary_by_year, merit_horizon,
        ),
        pay_cadence=gap.pay_cadence,
        monthly_pension_income=pension.monthly_income,
        retirement_account_projections=projections,
        safe_withdrawal_rate=(
            point.swr_override if point.swr_override is not None
            else resolve_swr_fraction(gap.settings)
        ),
        # The EXPLICIT rate (fork F1): an unset rate is a zero with a flag, so
        # the after-tax fields are always populated and the readiness verdict
        # never silently drops its own frame.
        estimated_tax_rate=inputs.effective_tax_rate,
    )
    return RetirementPicture(
        inputs=inputs,
        point=point,
        retirement_date=retirement_date,
        axis=axis,
        projections=projections,
        pension=pension,
        net=net,
    )
