"""
Shekel Budget App -- Obligations Aggregator Service (E-24, HIGH-05)

Single canonical producer for "monthly equivalent of recurring template"
and "sum of monthly equivalents across a set of recurring templates."

Before this module, four near-identical loops aggregated monthly
equivalents (three in ``app/routes/obligations.py:summary``, one in
``savings_goal_service.compute_committed_monthly``). Only the three
``/obligations`` loops skipped a template whose recurrence ``end_date``
was in the past. ``compute_committed_monthly`` did not, so an expired
recurring expense or transfer kept inflating the emergency-fund
baseline and every per-goal contribution floor on ``/savings`` forever
while ``/obligations`` correctly excluded it -- the same obligation
showing as two different numbers on two pages (HIGH-05 / D6-05).

The shared filter applied here, in one place, by every consumer:

  1. Skip if the template has no recurrence rule (one-off charge or
     orphaned reference -- no defined cadence to monthly-equivalent).
  2. Skip if the rule's own CLOSING BOUND has already stopped it
     (``recurrence.has_ended``) -- an expired recurring template is no
     longer a future obligation. This is the filter the audit found
     missing from ``compute_committed_monthly``.
  3. Skip if ``default_amount is None`` or ``default_amount == 0``
     -- nothing to contribute.

**There is no fourth rule, and its removal is plan step R7a-2b's.**  The filter
used to end "skip if the conversion returns ``None`` -- a pattern this
application does not model has no cadence to normalize", which made this the
only surface in the app that answered a broken rule with silence: ``resolve``
RAISES for the same state, so the Recurring surface already 500'd on such a
rule through ``read_rule`` while THIS filter quietly left the same obligation
out of the emergency-fund baseline -- one row, counted on one page and not the
other.  ``recurrence.cadence_of`` raises now (ruled 2026-08-11), so the skip
has no subject.

**How often the owner is paid is an INPUT, not a constant** (plan step
R7a-2a).  The paycheck-space patterns' monthly equivalent used to be computed
against ``app.utils.money.PAY_PERIODS_PER_YEAR``, a hardcoded ``Decimal("26")``
-- so this "single canonical producer" produced a figure that was simply wrong
for an owner not paid biweekly.  Both entry points now take the owner's
schedule, resolved ONCE per request by the caller and threaded, never looked up
per row.  The month denominator (``MONTHS_PER_YEAR``) stays a constant, because
12 is a property of the calendar rather than of an owner.

**They take the whole :class:`~app.services.pay_calendar.PayCalendar` rather
than its cadence, since plan step R7b-3**, and the extra is what step 2 of the
filter needs: a recurrence may now stop after a COUNT of occurrences, and when
that count is spent depends on when the owner's paychecks fall.  Reading only
the cadence, this module could answer "has it expired" for a DATE bound and had
no answer at all for a count -- so a spent count would have gone on inflating
``/obligations`` and the ``/savings`` emergency-fund baseline forever, which is
the HIGH-05 defect this module exists to have fixed, reappearing on the other
bound.  Two savings call sites trade ``cadence_for``'s one query for
``calendar_for``'s two to pay for it.

**And the conversion itself is ONE expression** (plan step R7a-2b).  It lived
in ``savings_goal_service.amount_to_monthly`` as a seven-branch switch over
``pattern_id`` -- in a savings module, though no savings code called it and its
inputs are a recurrence and a pay cadence.  A monthly equivalent is
``amount * occurrences_per_year / 12`` for every cadence there is, so the
branches were seven spellings of one formula, each of which had to be written
again for every cadence plan step R8 adds.  The formula lives here, where this
module's own first sentence says it should; how often a cadence FIRES is
``recurrence.Cadence.occurrences_per_year``, which is the recurrence package's
to answer.

All functions are pure: they accept ORM template instances (or any
object exposing the same ``recurrence_rule`` / ``default_amount``
attributes -- duck-typing supports the ``types.SimpleNamespace`` mock
templates used in tests), ``as_of`` and the owner's pay cadence, and return
Decimal results.  No Flask imports.
"""

from datetime import date
from decimal import Decimal
from typing import Iterable, Union

from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.pay_calendar import PayCalendar
from app.services.recurrence import cadence_of, has_ended
from app.utils.money import MONTHS_PER_YEAR, round_money

# Either ORM template class exposes ``recurrence_rule`` and
# ``default_amount`` -- the aggregator reads them via attribute
# access. Test fixtures that build ``types.SimpleNamespace`` mock
# templates rely on the same duck-typed contract; the static type
# hint names the two production classes for IDE / pylint
# consumption.
RecurringTemplate = Union[TransactionTemplate, TransferTemplate]


def template_rule(template: RecurringTemplate):
    """Return *template*'s recurrence rule, or ``None`` when it does not repeat.

    The one accessor for "does this definition repeat", beside the
    ``RecurringTemplate`` type both readers duck-type over.  ``getattr`` rather
    than attribute access because the test fixtures build templates as
    ``types.SimpleNamespace``; "does not repeat" is ``recurrence_rule_id IS
    NULL`` on both ORM kinds since plan step R2e-3.

    Lives here rather than in ``recurring_view`` because this module owns the
    shared filter's step 1 and was already performing the same read inline --
    two spellings of one question in modules that import each other.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` (or any
            object exposing ``recurrence_rule``).

    Returns:
        The ``RecurrenceRule``, or ``None``.
    """
    return getattr(template, "recurrence_rule", None)


def template_monthly_or_none(
    template: RecurringTemplate,
    as_of: date,
    calendar: PayCalendar,
) -> Decimal | None:
    """Return the monthly equivalent of one recurring template, or None.

    Applies the shared filter (no rule -- which is how a definition says
    it does not repeat -- expired, missing/zero amount).  The returned Decimal
    is NOT quantized -- callers that aggregate first then round
    (``committed_monthly``) need full precision; callers that display a per-row
    value round at the display boundary with ``round_money``.

    **The conversion is one expression**, and the same one for every cadence:
    an amount times how often it happens in a year, over twelve.  Plan step
    R7a-2b replaced a seven-branch switch with it, so ``(2, MONTH)`` and
    ``(1, WEEK)`` -- the cadences plan step R8 makes authorable -- already
    total correctly rather than falling to a ``None`` the caller drops.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate``
            ORM instance (or any object exposing ``recurrence_rule``
            and ``default_amount``). The recurrence rule is read via
            attribute access; loading is the caller's responsibility
            (``joinedload(.recurrence_rule)`` in the production
            routes).
        as_of: Reference date used to evaluate the rule's closing bound. A
            rule the bound has already stopped is treated as expired and
            excluded. Callers pass ``date.today()`` for "as of now" semantics.
        calendar: The owner's whole pay-period schedule.  It answers TWO
            questions here and that is why it replaced the bare
            :class:`~app.services.pay_calendar.PayCadence` at plan step
            R7b-3: the cadence, off ``calendar.cadence``, which is all the
            conversion needs -- and, for a COUNT-bounded rule, when that
            count is spent, which depends on when the paychecks fall and so
            cannot be answered from the cadence alone.  Resolved once per
            request by the caller and threaded, never looked up per row.

    Returns:
        The full-precision Decimal monthly equivalent, or ``None`` if
        the template is filtered out by any of the shared-filter
        rules. ``None`` means "do not include this template in any
        monthly-equivalent total."

    Raises:
        RecurrenceResolutionError: The rule names a pattern this application
            does not model, so it has no derivable cadence.  A REFUSAL rather
            than a skip since plan step R7a-2b: a rule the app cannot read is
            a broken invariant, and dropping it silently understated every
            total this module feeds while the Recurring surface 500'd on the
            same row.  Since plan step R7b-3 a COUNT-bounded rule can also
            raise it from the filter itself, which resolves such a rule against
            *calendar* -- the same disposition, one step earlier.
        RecurrenceGenerationError: For a count-bounded rule only, when the
            resolved value names something the occurrence engine cannot walk
            (``recurrence.has_ended``).
    """
    rule = template_rule(template)
    if rule is None:
        return None

    # The AMOUNT guards run first, and the order matters since plan step
    # R7b-3: step 2 resolves a COUNT-bounded rule against the owner's schedule
    # and walks its occurrences, which is real work to reach the same ``None``
    # a zero amount reaches for free.  The three are independent skips, so
    # their order is ours to choose.
    amount = template.default_amount
    if amount is None:
        return None
    amount = Decimal(str(amount))
    if amount == 0:
        return None

    if has_ended(rule, calendar, on=as_of):
        return None

    # ONE division, and the denominator is an exact integer: a monthly
    # equivalent is an amount times how often it happens in a year, over
    # twelve.  Dividing by ``interval_n`` HERE rather than taking
    # ``occurrences_per_year`` is what keeps it to one rounding -- that
    # quotient is inexact for an interval that does not divide its unit's
    # year, and multiplying money by it moved 31,072 displayed cents in a
    # 52,000,000-case sweep, wrongly (see ``Cadence.units_per_year``).
    cadence = cadence_of(rule)
    return (
        amount * cadence.units_per_year(calendar.cadence)
        / (cadence.interval_n * MONTHS_PER_YEAR)
    )


def committed_monthly(
    templates: Iterable[RecurringTemplate],
    as_of: date,
    calendar: PayCalendar,
) -> Decimal:
    """Sum monthly equivalents across a set of recurring templates.

    Routes every template through ``template_monthly_or_none``, which
    applies the shared filter (no rule, expired, missing/zero amount).
    Templates returning ``None`` contribute zero to
    the total; only non-None Decimals are summed. The final result is
    rounded once at the boundary with ``round_money`` (ROUND_HALF_UP
    via ``app.utils.money``) -- intermediate sums stay at full
    Decimal precision so penny-level drift cannot accumulate.

    This is the single canonical aggregator behind both the
    ``/obligations`` page totals and the ``/savings`` emergency-fund
    baseline + per-goal contribution-floor figures. Per E-24 /
    HIGH-05, every consumer must call this function rather than
    inline its own filter+sum loop.

    Args:
        templates: Iterable of ORM template instances
            (``TransactionTemplate``, ``TransferTemplate``, or any
            duck-typed equivalent). Callers are responsible for
            scoping the query (user_id, is_active, account_id, etc.);
            this function applies only the cross-cutting recurrence
            filter, not the data-ownership filter.
        as_of: Reference date for the expired-rule filter (see
            ``template_monthly_or_none``).
        calendar: The owner's whole pay-period schedule, threaded to every
            per-template conversion (see ``template_monthly_or_none``).  One
            value for the whole set: these templates belong to one owner, so
            summing figures resolved against two schedules would be adding
            different units.

    Returns:
        The total monthly-equivalent Decimal, rounded to cents with
        ``ROUND_HALF_UP``. Returns ``Decimal("0.00")`` if every input
        template is filtered out or the iterable is empty.

    Raises:
        RecurrenceResolutionError: A template's rule names a pattern this
            application does not model, so the total cannot be completed.  The
            whole sum is refused rather than shrunk by one row -- see
            :func:`template_monthly_or_none`, and note that this function's
            three callers (the Recurring surface, the emergency-fund floor and
            the per-goal contribution floors) each publish a figure a missing
            row would silently understate.
    """
    total = Decimal("0")
    for template in templates:
        monthly = template_monthly_or_none(template, as_of, calendar)
        if monthly is not None:
            total += monthly
    return round_money(total)
