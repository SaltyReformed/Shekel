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
  2. Skip if ``rule.end_date is not None and rule.end_date < as_of``
     -- an expired recurring template is no longer a future
     obligation. This is the filter the audit found missing from
     ``compute_committed_monthly``.
  3. Skip if ``default_amount is None`` or ``default_amount == 0``
     -- nothing to contribute.
  4. Skip if ``amount_to_monthly`` returns ``None`` -- a pattern this
     application does not model has no cadence to normalize.  (Step 1's
     no-rule skip is what excludes a NON-REPEATING definition; the two
     were separate cases until plan step R2e-3 retired the ``Once``
     pattern that was the second spelling of it.)

**How often the owner is paid is an INPUT, not a constant** (plan step
R7a-2a).  The paycheck-space patterns' monthly equivalent used to be computed
against ``app.utils.money.PAY_PERIODS_PER_YEAR``, a hardcoded ``Decimal("26")``
-- so this "single canonical producer" produced a figure that was simply wrong
for an owner not paid biweekly.  Both entry points now take the owner's
:class:`~app.services.pay_calendar.PayCadence`, resolved ONCE per request by
the caller and threaded, never looked up per row.  The month denominator
(``MONTHS_PER_YEAR``) stays a constant, because 12 is a property of the
calendar rather than of an owner.

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
from app.services.pay_calendar import PayCadence
from app.services.savings_goal_service import amount_to_monthly
from app.utils.money import round_money

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
    pay_cadence: PayCadence,
) -> Decimal | None:
    """Return the monthly equivalent of one recurring template, or None.

    Applies the shared filter (no rule -- which is how a definition says
    it does not repeat -- expired, missing/zero amount, unmodelled
    pattern). The returned Decimal is NOT quantized -- callers
    that aggregate first then round (``committed_monthly``) need full
    precision; callers that display a per-row value
    (``/obligations`` route loop) round at the display boundary with
    ``round_money``.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate``
            ORM instance (or any object exposing ``recurrence_rule``
            and ``default_amount``). The recurrence rule is read via
            attribute access; loading is the caller's responsibility
            (``joinedload(.recurrence_rule)`` in the production
            routes).
        as_of: Reference date used to evaluate ``rule.end_date``. A
            rule whose ``end_date`` is strictly before ``as_of`` is
            treated as expired and excluded. Callers pass
            ``date.today()`` for "as of now" semantics.
        pay_cadence: How often the owner is paid
            (:class:`~app.services.pay_calendar.PayCadence`).  Read only by the
            paycheck-space patterns; a monthly or annual template's equivalent
            is a property of the calendar alone.  Resolved once per request by
            the caller -- ``PayCalendar.cadence`` where the caller already has
            a schedule, ``pay_calendar.cadence_for`` where it does not.

    Returns:
        The full-precision Decimal monthly equivalent, or ``None`` if
        the template is filtered out by any of the shared-filter
        rules. ``None`` means "do not include this template in any
        monthly-equivalent total."
    """
    rule = template_rule(template)
    if rule is None:
        return None

    end_date = getattr(rule, "end_date", None)
    if end_date is not None and end_date < as_of:
        return None

    amount = template.default_amount
    if amount is None:
        return None
    amount = Decimal(str(amount))
    if amount == 0:
        return None

    return amount_to_monthly(
        amount, rule.pattern_id, rule.interval_n, pay_cadence,
    )


def committed_monthly(
    templates: Iterable[RecurringTemplate],
    as_of: date,
    pay_cadence: PayCadence,
) -> Decimal:
    """Sum monthly equivalents across a set of recurring templates.

    Routes every template through ``template_monthly_or_none``, which
    applies the shared filter (no rule, expired, missing/zero amount,
    unmodelled pattern). Templates returning ``None`` contribute zero to
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
        pay_cadence: How often the owner is paid, threaded to every
            per-template conversion (see ``template_monthly_or_none``).  One
            value for the whole set: these templates belong to one owner, so
            summing figures resolved against two cadences would be adding
            different units.

    Returns:
        The total monthly-equivalent Decimal, rounded to cents with
        ``ROUND_HALF_UP``. Returns ``Decimal("0.00")`` if every input
        template is filtered out or the iterable is empty.
    """
    total = Decimal("0")
    for template in templates:
        monthly = template_monthly_or_none(template, as_of, pay_cadence)
        if monthly is not None:
            total += monthly
    return round_money(total)
