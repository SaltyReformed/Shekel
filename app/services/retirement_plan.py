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

* :class:`PlanPoint` -- WHICH plan, RESOLVED: the withdrawal rate and the merit
  horizon this plan is solved at, whether they came from the settings or from a
  what-if slider, plus the retire-later lever's month offset.  It is frozen and
  hashable because it is the memo key, and resolved because a memo key must be
  CANONICAL -- two spellings of one plan are the two derivations this module
  exists to remove.  Built through :attr:`RetirementInputs.stored_plan` and
  :meth:`RetirementInputs.plan_with`.
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
    resolve_retirement_date_provenance,
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

#: Percentage scaler, PUBLIC because the display boundary needs it: the blended
#: return is carried as the fraction the growth math takes and the assumptions
#: rail renders a percent, so the route scales it here rather than keeping a
#: fourth private copy of ``Decimal("100")``.  ``assumed_annual_return`` is
#: stored as a fraction; the blended average is formed in PERCENT and quantized
#: THERE before being handed back, which is where the two-decimal rounding
#: happens -- see :meth:`RetirementPicture.blended_return` for why that order is
#: load-bearing rather than cosmetic.
PCT_SCALE = Decimal("100")

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
    """WHICH retirement plan a picture is of -- RESOLVED, never a delta.

    Frozen and hashable because it is :func:`picture_at`'s memo key, and the
    memo is what makes the readiness hero and the lever card's baseline ONE
    object instead of two equal ones.  **A memo key must therefore be CANONICAL:
    two spellings of one plan are two derivations of one figure, which is the
    defect (row **P57**) this module exists to remove.**

    So the two assumptions a what-if can move are stored here as the VALUES they
    resolve to, not as the overrides they arrived as.  The first draft stored
    overrides, and it was measured wrong the same day (adversarial design
    review, 2026-08-16): the assumptions rail's merit-horizon input is a
    SAVEABLE setting and so renders pre-filled with the stored value, which
    means every real fragment request ships ``merit_raise_horizon_years=5``
    when 5 is exactly what is stored.  ``PlanPoint(merit_horizon_override=5)``
    and ``PlanPoint()`` are two keys for one plan, and the what-if panel
    derived its baseline and its "override" as two full projections agreeing in
    every digit -- P57's own sentence, restated inside the step that closed it.
    Resolved values cannot express that: 5 is 5 whichever door it came through.

    Built through :attr:`RetirementInputs.stored_plan` and
    :meth:`RetirementInputs.plan_with`, because resolving an assumption needs
    the owner's settings and this record deliberately does not carry them.

    Attributes:
        month_offset: Whole months added to EACH qualifying pension's own
            planned retirement date -- the retire-later lever's search axis.
            It stays a delta where the other two became values, and the reason
            is in :func:`app.services.retirement_dashboard_service.compute_pension_summary`:
            the offset shifts every pension SEPARATELY, growing each one's years
            of service and high-salary window from its own date.  An owner with
            pensions dated 2044 and 2046 delayed by a year retires against 2045
            AND 2047, which one resolved date cannot say.  ``0`` is the stored
            plan (:func:`app.utils.dates.add_months` with 0 months is the
            identity).
        swr: The fractional safe-withdrawal rate this plan is solved at, already
            resolved from the what-if or the stored settings.
        merit_horizon: The merit-raise horizon in years, likewise resolved.
        return_rate_override: A fractional annual return applied UNIFORMLY to
            every account, or ``None`` -- and this one is genuinely an OVERRIDE
            rather than a resolved value, because ``None`` does not stand for a
            stored number.  It means "each account grows at its own stored
            rate", which no single rate expresses.  Its input renders empty for
            the same reason (see ``dashboard.html``'s assumed-return row).
    """

    month_offset: int
    swr: Decimal
    merit_horizon: int
    return_rate_override: Decimal | None


@dataclass(frozen=True)
class RetirementInputs:
    """Everything one ``/retirement`` render loads, loaded exactly once.

    **Nothing here is re-read per plan**, which is what makes one load serve
    every candidate: the retire-later search probes up to ten dates and none of
    them queries.  What DOES vary with the point -- the salary path, the
    pension benefit, the employer salary basis, the projection axis and the
    per-account walk -- is derived in :func:`picture_at` from these.

    **The precise invariant, because "point-independent" is not quite true of
    ``base_ctx`` and an earlier draft of this paragraph claimed it was**
    (adversarial reviews, 2026-08-16): that context carries a
    ``planned_retirement_date`` and a ``return_rate_override``, both of which
    :func:`_derive_picture` replaces per point.  Sharing one ``batch`` across
    plans is therefore safe because of a property of the LOADER, not of the
    context: :func:`~app.services.retirement_projection.load_projection_batch`
    reads only ``balance_ctx`` and ``accounts`` -- never the horizon, the
    return override or the employer basis.  (It read two PERIOD fields as well
    until pay-calendar plan step C2-f2d-3 deleted them from that context; both
    are derived from ``balance_ctx`` now, which this list already named, so the
    property is unchanged and the surface it rests on is smaller.)
    Teaching it to read one of those would silently hand every
    point in a render the stored plan's batch, so the property is pinned by
    ``tests/test_services/test_retirement_plan.py``'s
    ``TestTheBatchIsHorizonIndependent`` rather than left to this note.

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
        base_ctx: The account set and period calendar the projection runs over,
            as a context.  Its three point-dependent fields are placeholders --
            :func:`_derive_picture` replaces all three for every point,
            including the stored one -- so the employer salary basis is left
            ``None`` here rather than built and thrown away, which is what an
            earlier draft did once per render for nothing.
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
    def stored_plan(self) -> PlanPoint:
        """The plan as STORED: no delay, every assumption from the settings.

        The ``/retirement`` page's own point, and the baseline every what-if is
        stated against.  It is a property of the INPUTS rather than a module
        constant because a resolved plan belongs to an owner: 4% is this
        owner's stored withdrawal rate, not a universal one.

        Returns:
            The stored :class:`PlanPoint`.
        """
        return self.plan_with()

    def plan_with(
        self, *, swr_override=None, return_rate_override=None,
        merit_horizon_override=None,
    ) -> PlanPoint:
        """Resolve a what-if against this owner's stored settings.

        **The canonicalising door.**  An override that equals the stored value
        resolves to the same :class:`PlanPoint` as no override at all, so the
        memo cannot hold two keys for one plan -- which matters because the
        merit-horizon input is pre-filled with the stored value and therefore
        submits it on every single fragment request.

        Args:
            swr_override: A fractional safe-withdrawal rate, or ``None`` for the
                stored one.
            return_rate_override: A uniform fractional annual return, or
                ``None`` to leave each account on its own stored rate.
            merit_horizon_override: A merit-raise horizon in years, or ``None``
                for the stored one.

        Returns:
            The resolved :class:`PlanPoint`, at no delay.
        """
        return PlanPoint(
            month_offset=0,
            swr=(
                swr_override if swr_override is not None
                else resolve_swr_fraction(self.gap.settings)
            ),
            merit_horizon=(
                merit_horizon_override if merit_horizon_override is not None
                else self.gap.merit_horizon_years
            ),
            return_rate_override=return_rate_override,
        )

    @property
    def date_provenance(self) -> dict:
        """WHO owns the resolved retirement date, with the facts to say so.

        On the inputs because it is point-independent and because the
        alternative was a three-hop reach-through from the readiness shaper
        (``picture.inputs.gap.pensions``, ``picture.inputs.gap.settings``) --
        which is what kept that module importing the resolver's own.

        Returns:
            ``date`` / ``source`` / ``pension_id`` / ``pension_name`` -- see
            :func:`~app.services.retirement_dashboard_service.resolve_retirement_date_provenance`.
        """
        return resolve_retirement_date_provenance(
            self.gap.pensions, self.gap.settings,
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
        # The three point-dependent fields are placeholders: every picture
        # replaces the horizon, the return override AND the employer salary
        # basis, so building a basis here would run the whole salary projection
        # once per render and discard it.
        base_date,
        None,
        None,
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
        inputs: The :class:`RetirementInputs` this picture was derived FROM.
            A picture is meaningless without them -- the same figures under a
            different owner or a different clock are a different answer -- so
            this is provenance, not a convenience bag.  It is not the shape
            :func:`app.services.retirement_levers._contribution_outcome`
            refuses: that is a LEAF taking two numbers, and handing it a whole
            picture would make it depend on a record it does not read.  A
            consumer that wants a point-independent fact should read it off a
            NAMED property of the inputs (:attr:`RetirementInputs.date_provenance`,
            :attr:`RetirementInputs.tax_rate_missing`) rather than walking
            ``picture.inputs.gap.<field>``, which is the reach-through an
            adversarial review caught here on 2026-08-16.
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
        ) / PCT_SCALE

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
            weighted_return / total_balance * PCT_SCALE
        ).quantize(_PCT_QUANTUM)
    return _DEFAULT_RETURN_PCT


def picture_at(
    inputs: RetirementInputs, point: PlanPoint,
) -> RetirementPicture:
    """The retirement picture at *point*, derived once per render.

    **The memo is the whole point of this function existing rather than
    :func:`_derive_picture` being called directly.**  The ``/retirement`` page
    renders the readiness verdict at the stored plan and then runs the
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
    merit_horizon = point.merit_horizon
    retirement_date = (
        None if inputs.base_date is None
        else add_months(inputs.base_date, point.month_offset)
    )
    # The render's ONE day, threaded into all three producers that open a
    # salary path (pay-calendar plan step C2-f2e, ledger row **P55**).  Each
    # read ``date.today().year`` for itself, and this function runs once per
    # PLAN POINT -- the retire-later lever probes about ten -- so a render
    # crossing a New Year could project the verdict card's path from year N and
    # the lever card's from N+1.
    as_of = inputs.balance_ctx.as_of
    pension = compute_pension_summary(
        gap.pensions, merit_horizon, as_of, point.month_offset,
    )
    # Every point-dependent field replaced together, from a context the render
    # built once: the account query and the period calendar do not move with a
    # candidate date, so this re-queries nothing.
    ctx = replace(
        inputs.base_ctx,
        planned_retirement_date=retirement_date,
        return_rate_override=point.return_rate_override,
        employer_salary_basis=build_employer_salary_basis(
            gap.salary_profiles, retirement_date, merit_horizon, as_of,
            gap.pay_cadence,
        ),
    )
    axis = resolve_projection_axis(ctx)
    projections = project_accounts_with_batch(ctx, inputs.batch, axis)
    net = calculate_gap(
        net_biweekly_pay=compute_gap_net_biweekly(
            gap, retirement_date, pension.salary_by_year, merit_horizon, as_of,
        ),
        pay_cadence=gap.pay_cadence,
        monthly_pension_income=pension.monthly_income,
        retirement_account_projections=projections,
        safe_withdrawal_rate=point.swr,
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
