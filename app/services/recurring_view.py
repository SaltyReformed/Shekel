"""
Shekel Budget App -- Unified Recurring-Definitions View Producer (Loop B, P1)

Single producer behind the unified ``/templates`` (Recurring) surface that
replaces the three parallel pages ``/templates`` + ``/transfers`` +
``/obligations``.  It shapes the user's active recurring definitions
(income, expense, and transfer templates) into one display model: a
summary band, three grouped sections with per-section subtotals, and per
row a defined amount, monthly + per-paycheck equivalents, an
engine-backed next date, and a share of its section's committed total.

Two units, one source of truth
------------------------------
Every monetary figure is produced in BOTH units so the page-wide
Monthly / Per-paycheck toggle can switch without recomputing money in the
template or in JS.  There is exactly one monthly source of truth --
``obligations_aggregator.template_monthly_or_none`` (E-24 / HIGH-05, also
behind the /savings emergency-fund baseline) -- and the per-paycheck value
is DERIVED from it by
:meth:`~app.services.pay_calendar.PayCadence.monthly_to_per_paycheck`.  The
toggle therefore only re-expresses the same committed figure in a different
unit; it never opens a second money path that could disagree with the first.

**The conversion is the OWNER's since plan step R7a-2a**, where this module
held it as a module-level ``MONTHS_PER_YEAR / PAY_PERIODS_PER_YEAR`` ratio --
a hardcoded 12/26, so the "per paycheck" column named a paycheck the owner
does not receive unless they are paid biweekly.  The cadence comes off the
:class:`~app.services.pay_calendar.PayCalendar` this module is already handed,
so the page resolves it once and no second load appears.  That change also
dropped a rounding step: the ratio was pre-computed and multiplied (``x *
(12/26)``, inexact twice), where the value's method divides once (``x * 12 /
ppy``).

Next dates are engine-backed, and the cadence phrase comes from the same read
-----------------------------------------------------------------------------
The next occurrence is the date the recurrence engine itself would assign
to the next generated instance: ``recurrence.read_rule`` walks the
rule's cadence and places each occurrence on a pay period -- the SAME
composition the generation seam makes since plan step R4b-2 -- and
``recurrence_engine.compute_due_date`` gives the instance's due date, so a
row's "next date" cannot disagree with the grid cell it points at.  This
retires the ``/obligations`` approximation (``_next_occurrence``) the audit
flagged.

**The Recurrence column is produced here too** (plan step R7a).  It was eight
Jinja branches over the closed ``pattern_id`` set until then;
``recurrence.describe`` words it from the two-axis meaning instead, so the
column survives plan step R7c dropping the columns those branches read.  Each
rule is read ONCE per render -- ``read_rule`` returns the meaning and the
placements together -- because the phrase and the next date are two questions
about one reading, and resolving twice would be a second resolution point in
one request.

**It takes the owner's whole schedule as a ``PayCalendar``**, not a list
of pay periods (plan step R4b-1).  A recurrence's first occurrence is measured
against the owner's schedule, so the value this surface passes has to BE that
schedule; taking ORM rows and rebuilding the calendar per row would be a second
producer of one answer, and taking a subset would date a row from a schedule
its owner does not have.

What appears vs what totals
---------------------------
The list is a management surface, so it shows EVERY active definition,
including the non-repeating (rule-less) definitions you still need to edit,
archive, or delete.  The summary band and the section subtotals, however,
sum only genuine recurring commitments -- ``template_monthly_or_none``
returns ``None`` for rule-less / expired / missing-or-zero-amount templates, so
those rows render with a blank equivalent and contribute nothing to any
total (matching the retired /obligations kernel exactly).

Boundary discipline (``CLAUDE.md`` Architecture): no Flask imports; inputs
are already-loaded ORM template lists (or any duck-typed equivalent, as the
tests build with ``types.SimpleNamespace``) plus the user's pay-period
schedule as a ``PayCalendar`` and an ``as_of`` date; output is a frozen
dataclass tree of ``Decimal`` / ``date``.  All money math is ``Decimal``; the
route/template only display.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.models.recurrence_rule import RecurrenceRule
from app.services.obligations_aggregator import (
    RecurringTemplate,
    template_monthly_or_none,
    template_rule,
)
from app.services.pay_calendar import PayCadence, PayCalendar
from app.services.recurrence import (
    RecurrenceDescription,
    RecurrenceResolutionError,
    ResolvedRecurrence,
    RuleReading,
    describe,
    placed_periods,
    read_rule,
    resolved_recurrence,
)
from app.services.recurrence_engine import compute_due_date
from app.utils.money import round_money

_HUNDRED = Decimal("100")
_PERCENT_QUANTUM = Decimal("0.1")


@dataclass(frozen=True)
class UnitPair:
    """A monetary figure in both display units.

    ``monthly`` and ``per_paycheck`` are the same underlying commitment
    expressed two ways, each rounded to cents.  Both are ``None`` together
    for a definition that is not a recurring commitment (rule-less, expired,
    or missing/zero amount) -- the page renders a blank equivalent for such a
    row rather than a misleading zero.
    """

    monthly: Decimal | None
    per_paycheck: Decimal | None


@dataclass(frozen=True)
class RecurringRow:
    """One recurring-definition row in a section.

    Attributes:
        template: The ORM template (``TransactionTemplate`` or
            ``TransferTemplate``) this row renders.  Carried whole so the
            template layer can read its name, badges, category / accounts, and
            the edit / archive / delete action links -- none of which the
            producer recomputes.
        equivalent: The monthly + per-paycheck commitment (:class:`UnitPair`),
            both ``None`` for a non-recurring definition.
        recurrence: How the definition repeats, in display terms
            (:class:`~app.services.recurrence.RecurrenceDescription`), or
            ``None`` when it does not repeat -- which is the absence of a
            rule naming the definition, since plan step R2e-3.  Produced
            here rather than in the template (plan step R7a): the phrase is a
            function of what the recurrence MEANS against the owner's
            schedule, and a template holds neither.
        next_date: The engine-assigned due date of the next occurrence on
            or after ``as_of``, or ``None`` when the definition has no
            future recurring occurrence (no rule, or expired).
        share_pct: This row's monthly equivalent as a percentage (0-100) of
            its section's committed monthly total, for the share bar; ``None``
            when the row does not contribute (non-recurring) or the section
            total is zero.
    """

    template: RecurringTemplate
    equivalent: UnitPair
    recurrence: RecurrenceDescription | None
    next_date: date | None
    share_pct: Decimal | None


@dataclass(frozen=True)
class ArchivedRow:
    """One archived recurring definition, for the collapsed Archived drawer.

    Archived definitions carry no monthly equivalent, no next date and no
    share -- they are inactive and excluded from every total -- so they are a
    different value from :class:`RecurringRow` rather than one with four fields
    permanently ``None``.  What they DO carry is how they repeated, which the
    drawer shows so an archived definition can be recognised before it is
    unarchived or permanently deleted.

    Attributes:
        template: The ORM template, carried whole for its name, kind, amount
            and action links.
        recurrence: How it repeats, or ``None`` for a non-repeating
            definition.
    """

    template: RecurringTemplate
    recurrence: RecurrenceDescription | None


@dataclass(frozen=True)
class RecurringSection:
    """One kind-grouped section (income, expenses, or transfers).

    ``rows`` are ordered by monthly-equivalent cost descending (the locked
    default landing order), non-recurring rows last.  ``subtotal`` is the
    section's committed total in both units; it equals
    ``obligations_aggregator.committed_monthly`` for the section by
    construction (same full-precision filter+sum, rounded once), so the two
    surfaces cannot drift.
    """

    rows: tuple[RecurringRow, ...]
    subtotal: UnitPair


@dataclass(frozen=True)
class SummaryBand:
    """The obligations kernel: committed income vs outflow, no projection.

    Measured from the recurring definitions themselves (never a balance
    projection), so it stands in for the retired /obligations monthly lens.

    Attributes:
        income: Committed recurring income, both units.
        expenses: Committed recurring expenses, both units.
        transfers_out: Committed recurring transfers, both units.
        net: ``income - expenses - transfers_out`` per unit, computed from
            the rounded section subtotals so the tile equals what the shown
            figures subtract to.
        expenses_pct_of_income: Expenses as a percentage (0-100) of income,
            or ``None`` when income is zero (no ratio to show).
    """

    income: UnitPair
    expenses: UnitPair
    transfers_out: UnitPair
    net: UnitPair
    expenses_pct_of_income: Decimal | None


@dataclass(frozen=True)
class RecurringView:
    """The full display model for the unified Recurring surface."""

    income: RecurringSection
    expenses: RecurringSection
    transfers: RecurringSection
    band: SummaryBand


def _unit_pair(
    monthly_full: Decimal | None, pay_cadence: PayCadence,
) -> UnitPair:
    """Round a full-precision monthly figure into both display units.

    ``monthly_full`` is the unquantized monthly equivalent from
    ``template_monthly_or_none`` (per-row) or a sum of such values (per
    section).  Rounding once here, at the display boundary, keeps
    intermediate sums at full precision so pennies cannot accumulate drift
    (the ``committed_monthly`` contract).  ``None`` in propagates to both
    fields so a non-recurring row shows a blank equivalent.

    Args:
        monthly_full: The unquantized monthly figure, or ``None`` for a
            definition that is not a recurring commitment.
        pay_cadence: How often the owner is paid, from the calendar this
            surface already holds.

    Returns:
        The :class:`UnitPair`, both fields rounded to cents, or both ``None``.
    """
    if monthly_full is None:
        return UnitPair(monthly=None, per_paycheck=None)
    return UnitPair(
        monthly=round_money(monthly_full),
        per_paycheck=round_money(
            pay_cadence.monthly_to_per_paycheck(monthly_full),
        ),
    )


def _share_pct(
    monthly_full: Decimal | None, section_total_full: Decimal,
) -> Decimal | None:
    """This row's share (0-100) of its section's committed monthly total.

    ``None`` when the row does not contribute (non-recurring) or the
    section has no committed total to take a share of (avoids a divide by
    zero and a meaningless bar).  Computed from the full-precision values
    so the share reflects the true proportion, then quantized for display.
    """
    if monthly_full is None or section_total_full == 0:
        return None
    return (monthly_full / section_total_full * _HUNDRED).quantize(
        _PERCENT_QUANTUM, rounding=ROUND_HALF_UP,
    )


def _next_occurrence(
    rule: RecurrenceRule, reading: RuleReading, as_of: date,
) -> date | None:
    """Engine-backed date of the next occurrence on or after ``as_of``.

    Reads the placements :func:`~app.services.recurrence.read_rule` already
    produced for this row -- the same walk that generates the grid instances --
    and ``compute_due_date`` gives the due date the generated instance would
    carry.  Returns the first such due date on or after ``as_of`` (the current
    period can match with a due date already past, so the search advances to
    the next matching period), or ``None`` when no matching period has a due
    date on or after ``as_of`` -- an expired rule whose remaining candidate
    periods are all in the past.  Otherwise this tracks the engine exactly: if
    the engine would still generate a future instance (e.g. an expired rule
    whose final period straddles ``as_of``), that instance's date is reported,
    matching the grid cell it points at.

    **``as_of`` is this surface's own display boundary, not the rule's** -- the
    rule's opening bound is its anchor, and putting a caller's window inside the
    producer is what defect D2 was.  So the bound is stated here and the
    PROJECTION is shared (:func:`~app.services.recurrence.placed_periods`),
    which is the same split the retired ``match_periods`` adapter fused: it
    both filtered and bounded, so a caller's window looked like a property of
    the recurrence.

    Args:
        rule: The definition's recurrence rule, for the due-date derivation.
        reading: What :func:`~app.services.recurrence.read_rule` already
            resolved and placed for this row.  Taken rather than re-walked so
            the page resolves each rule ONCE: the description and this date
            are two questions about one reading.
        as_of: The surface's display boundary.

    Returns:
        The next occurrence's due date, or ``None``.
    """
    for period in placed_periods(
        reading.placements, ending_on_or_after=as_of,
    ):
        due = compute_due_date(rule, period)
        if due >= as_of:
            return due
    return None


@dataclass(frozen=True)
class _PreparedRow:
    """One template with everything the two row passes need, computed once.

    Rows are built in two passes -- the section's committed total has to exist
    before any row's share of it can -- and this is what the first pass hands
    the second.  It exists so the second pass re-derives nothing: resolving a
    rule twice per render is what plan step R7a's read door was restructured to
    prevent.

    Attributes:
        template: The ORM template.
        monthly_full: Its unquantized monthly equivalent, or ``None`` when it
            is not a recurring commitment.
        rule: Its recurrence rule, or ``None`` when the definition does not
            repeat.
        reading: That rule read against the owner's schedule, or ``None``
            exactly when *rule* is -- the two are set and cleared together, so
            a row cannot be described from a reading of a rule it does not
            have.
    """

    template: RecurringTemplate
    monthly_full: Decimal | None
    rule: RecurrenceRule | None
    reading: RuleReading | None

    @property
    def resolved(self) -> ResolvedRecurrence | None:
        """The row's two-axis meaning, or ``None`` when it does not repeat."""
        return None if self.reading is None else self.reading.resolved

    def __post_init__(self) -> None:
        """Refuse a rule without its reading, or a reading without its rule.

        A check rather than a docstring guarantee, matching
        :class:`~app.services.recurrence.OccurrencePlacement`: the pair is one
        fact stated twice, so the second pass can test ONE of them and the
        two-condition guard that would otherwise be needed is a fence for a
        state the value should not permit.

        Raises:
            ValueError: When exactly one of *rule* and *reading* is set.
        """
        if (self.rule is None) != (self.reading is None):
            raise ValueError(
                f"prepared row for {self.template!r} carries rule="
                f"{self.rule!r} beside reading={self.reading!r}: a definition "
                f"has a rule and its reading together or neither, and a pair "
                f"that disagrees would describe a cadence the row does not "
                f"have."
            )


def _described(
    rule: RecurrenceRule | None, resolved: ResolvedRecurrence | None,
) -> RecurrenceDescription | None:
    """Turn a resolved recurrence into a row's description, or ``None``.

    **The ONE place this surface words a cadence.**  Both row kinds reach it:
    the active sections from the reading they already hold, the Archived
    drawer from a meaning-only resolve.  Written once because the two differ
    in how they OBTAIN the resolved value and not at all in what they do with
    it -- and a display contract expressed twice is one a later change updates
    once.

    **``None`` out means "does not repeat" and nothing else**, which is why
    the RULE is the discriminator rather than the resolved value.  They are
    not the same question: ``resolved_recurrence`` also answers ``None`` for
    an owner with no pay periods, and mapping that onto the same ``None``
    would render a quarterly bill as "One-time" -- a repeating commitment
    reported as a one-off, on the surface whose job is to say how things
    repeat.  An empty schedule is a broken invariant rather than a state to
    word: registration bootstraps a period, ``truncate_pay_periods`` always
    keeps the period its ``keep_through_period_id`` names, and
    ``reset_pay_periods`` deletes and regenerates inside one transaction, so no
    committed state has none.  (That truncate clause read "keeps index 0 by its
    own schema bound" until plan step C3-a, which re-keyed the form onto
    ``budget.pay_periods.id`` -- the guarantee got STRONGER, resting on the
    named period being on the keep side rather than on a Marshmallow floor of
    zero happening to sit below every real ordinal.)  It
    is refused here for the same reason :func:`_build_section` refuses every
    other broken invariant -- loudly.

    Args:
        rule: The definition's recurrence rule, or ``None`` when it does not
            repeat.
        resolved: What that rule MEANS, or ``None`` when there was no rule to
            resolve or no schedule to resolve it against.

    Returns:
        The :class:`~app.services.recurrence.RecurrenceDescription`, or
        ``None`` for a definition that does not repeat.

    Raises:
        RecurrenceResolutionError: When *rule* is present and *resolved* is
            not, which is an owner with no pay periods.
    """
    if rule is None:
        return None
    if resolved is None:
        raise RecurrenceResolutionError(
            f"recurrence rule {rule.id!r} could not be resolved against its "
            f"owner's schedule, which holds no pay periods.  Registration "
            f"bootstraps one, so this is a broken invariant; describing it as "
            f"a non-repeating definition would report a recurring commitment "
            f"as a one-off."
        )
    return describe(resolved)


def _resolved_meaning(
    rule: RecurrenceRule | None, calendar: PayCalendar,
) -> ResolvedRecurrence | None:
    """Resolve *rule*'s cadence without walking a single occurrence.

    The Archived drawer's read: it shows how a definition repeated and never
    where its rows land.

    Takes the RULE rather than the template so the drawer reads it once, the
    way :func:`_build_section` does -- asking the same question from two frames
    is the shape this step spent the rest of its diff removing.

    Args:
        rule: The definition's recurrence rule, or ``None``.
        calendar: The owner's whole pay-period schedule.

    Returns:
        The resolved meaning, or ``None`` when the definition has no rule.

    Raises:
        RecurrenceResolutionError: When the rule names a cadence this
            application cannot derive.  See :func:`_build_section` for why this
            surface fails loud.
    """
    if rule is None:
        return None
    return resolved_recurrence(rule, calendar)


def _build_section(
    templates: list[RecurringTemplate], calendar: PayCalendar, as_of: date,
) -> RecurringSection:
    """Build one kind-grouped section: its rows and both-units subtotal.

    Every template becomes a row (management surface shows all active
    definitions); its monthly equivalent, when the aggregator returns one,
    also accumulates into the full-precision section total the subtotal and
    each row's share are measured against.  Rows are ordered by monthly cost
    descending with non-recurring rows last (the locked default), preserving
    the caller's incoming order among equals.  The subtotal rounds the
    full-precision total once, so it equals ``committed_monthly`` for the
    section by construction.

    **Each rule is READ once** (plan step R7a).  A row's cadence phrase and its
    next date are two questions about one reading, so the first pass takes
    :func:`~app.services.recurrence.read_rule` -- the single resolve-then-place
    composition -- and the second pass derives both from what it returned.

    **This is a fail-CLOSED read**, and plan step R4a is what changed it.  The
    retired matcher used to log a warning and answer ``[]`` for a rule it could
    not read; the resolution seam now raises
    :class:`~app.services.recurrence.RecurrenceResolutionError` (an unmodelled
    pattern, an interval below 1, a day or month outside its column's domain),
    so ONE such rule takes the whole Recurring surface to a 500 rather than
    rendering the other definitions beside a silently blank cell.  The
    overlapping-schedule refusal (``RecurrenceScheduleError``) that used to sit
    beside them is GONE, with the calendar that raised it: plan step C2-b2
    replaced it with :class:`~app.services.pay_calendar.PayCalendar`, which
    DERIVES each period's end from the next payday and so cannot be handed an
    overlapping schedule to refuse.  Every one of the refusals that remain is a
    state the CHECK constraints and the write door already refuse, and the
    project's disposition for a broken invariant is the loud one -- but the
    contract is stated rather than discovered.

    Raises:
        RecurrenceResolutionError: When a rule names a cadence this application
            cannot derive.
        PayCalendarError: The owner has no resolvable pay cadence -- no
            ``budget.pay_schedule`` row and no pay period to infer one
            from.  Every monetary figure here is a conversion against how often
            they are paid, so there is no honest figure to publish
            without it (plan step R7a-2a; see
            :func:`app.services.pay_calendar.cadence_for`).
    """
    # ONE cadence for the section, off the schedule this surface already
    # holds: every row belongs to the calendar's owner, so asking per row
    # would resolve one fact many times.  Derived here rather than taken as a
    # parameter because :func:`build_archived_rows` calls this module's other
    # entry point and must NOT need one -- see
    # :class:`TestAnAbsentCadenceIsRefused`.  It costs one frozen 1-field value
    # per section and no query: the calendar is already in memory.
    pay_cadence = calendar.cadence
    prepared: list[_PreparedRow] = []
    section_total_full = Decimal("0")
    for template in templates:
        monthly_full = template_monthly_or_none(template, as_of, calendar)
        rule = template_rule(template)
        prepared.append(_PreparedRow(
            template=template,
            monthly_full=monthly_full,
            rule=rule,
            reading=None if rule is None else read_rule(rule, calendar),
        ))
        if monthly_full is not None:
            section_total_full += monthly_full

    rows = [
        RecurringRow(
            template=item.template,
            equivalent=_unit_pair(item.monthly_full, pay_cadence),
            recurrence=_described(item.rule, item.resolved),
            next_date=(
                None if item.reading is None
                else _next_occurrence(item.rule, item.reading, as_of)
            ),
            share_pct=_share_pct(item.monthly_full, section_total_full),
        )
        for item in prepared
    ]
    rows.sort(
        key=lambda row: (
            row.equivalent.monthly is not None,
            row.equivalent.monthly
            if row.equivalent.monthly is not None
            else Decimal("0"),
        ),
        reverse=True,
    )
    return RecurringSection(
        rows=tuple(rows),
        subtotal=_unit_pair(section_total_full, pay_cadence),
    )


def _build_band(
    income: UnitPair,
    expenses: UnitPair,
    transfers_out: UnitPair,
) -> SummaryBand:
    """Assemble the summary band from the three section subtotals.

    ``net`` subtracts expenses and transfers from income in each unit using
    the already-rounded subtotals, so the tile equals what the shown section
    totals subtract to.  Section subtotals are never ``None`` (an empty
    section rounds ``Decimal("0")``), so the arithmetic is always defined.
    ``expenses_pct_of_income`` is ``None`` when income is zero.
    """
    net = UnitPair(
        monthly=income.monthly - expenses.monthly - transfers_out.monthly,
        per_paycheck=(
            income.per_paycheck
            - expenses.per_paycheck
            - transfers_out.per_paycheck
        ),
    )
    if income.monthly is not None and income.monthly > 0:
        expenses_pct_of_income = (
            expenses.monthly / income.monthly * _HUNDRED
        ).quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP)
    else:
        expenses_pct_of_income = None
    return SummaryBand(
        income=income,
        expenses=expenses,
        transfers_out=transfers_out,
        net=net,
        expenses_pct_of_income=expenses_pct_of_income,
    )


def build_view(
    income_templates: list[RecurringTemplate],
    expense_templates: list[RecurringTemplate],
    transfer_templates: list[RecurringTemplate],
    calendar: PayCalendar,
    as_of: date,
) -> RecurringView:
    """Produce the unified Recurring surface's full display model.

    Args:
        income_templates: The user's active recurring income
            ``TransactionTemplate`` rows.
        expense_templates: The user's active recurring expense
            ``TransactionTemplate`` rows.
        transfer_templates: The user's active recurring ``TransferTemplate``
            rows.
        calendar: The owner's whole pay-period schedule
            (:class:`~app.services.pay_calendar.PayCalendar`), which the
            engine-backed next dates are measured against.
        as_of: Reference date -- "now" for the expired-rule filter and the
            next-occurrence search.  Callers pass ``date.today()``.

    Returns:
        A :class:`RecurringView`: the summary band plus income, expenses,
        and transfers sections, each with cost-descending rows and a
        both-units subtotal.  Every figure is a ``Decimal`` rounded to
        cents; the caller only displays.

    Raises:
        RecurrenceResolutionError: When a rule names a cadence this application
            cannot derive -- the fail-closed read :func:`_build_section`
            documents.
        PayCalendarError: The owner has no resolvable pay cadence -- no
            ``budget.pay_schedule`` row and no pay period to infer one
            from.  Every row's per-paycheck column here is a conversion against how often
            they are paid, so there is no honest figure to publish
            without it (plan step R7a-2a; see
            :func:`app.services.pay_calendar.cadence_for`).
    """
    income_section = _build_section(income_templates, calendar, as_of)
    expense_section = _build_section(expense_templates, calendar, as_of)
    transfer_section = _build_section(transfer_templates, calendar, as_of)
    band = _build_band(
        income_section.subtotal,
        expense_section.subtotal,
        transfer_section.subtotal,
    )
    return RecurringView(
        income=income_section,
        expenses=expense_section,
        transfers=transfer_section,
        band=band,
    )


def build_archived_rows(
    templates: list[RecurringTemplate], calendar: PayCalendar,
) -> tuple[ArchivedRow, ...]:
    """Shape the Archived drawer's rows for one template kind.

    A second entry point rather than a fourth section of :func:`build_view`,
    because the two answer different callers: the drawer renders only on the
    full page, while ``build_view`` is rebuilt on every Monthly /
    Per-paycheck toggle, and folding archived definitions into it would load
    and resolve them on a request that never shows them.

    It exists at all because the drawer's Recurrence column used to be raw ORM
    handed to a template that computed the phrase itself.  Since plan step R7a
    the phrase is a function of what a recurrence MEANS against the owner's
    schedule, which a template cannot ask -- so the drawer gets a producer like
    the active sections have.

    Only the description is resolved: an archived definition generates nothing,
    so its occurrences are never walked.

    Args:
        templates: The user's archived templates of one kind, in the order the
            drawer shows them.
        calendar: The owner's whole pay-period schedule.

    Returns:
        One :class:`ArchivedRow` per template, in the given order.

    Raises:
        RecurrenceResolutionError: When a rule names a cadence this application
            cannot derive -- the same fail-closed disposition the active
            sections have carried since plan step R4a; see
            :func:`_build_section`.
    """
    rows = []
    for template in templates:
        rule = template_rule(template)
        rows.append(ArchivedRow(
            template=template,
            recurrence=_described(rule, _resolved_meaning(rule, calendar)),
        ))
    return tuple(rows)
