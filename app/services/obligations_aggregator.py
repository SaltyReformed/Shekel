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
  4. Skip if the pattern is ONCE (``amount_to_monthly`` returns
     ``None`` -- a one-time obligation is not a recurring
     commitment).

The pay-period and month constants come from ``app.utils.money``
(``PAY_PERIODS_PER_YEAR``, ``MONTHS_PER_YEAR``); per E-24 / HIGH-05
the 26/12 factor is defined exactly once in the project.

All functions are pure: they accept ORM template instances (or any
object exposing the same ``recurrence_rule`` / ``default_amount``
attributes -- duck-typing supports the ``types.SimpleNamespace`` mock
templates used in tests) and ``as_of``, and return Decimal results.
No Flask imports.
"""

from datetime import date
from decimal import Decimal
from typing import Iterable, Union

from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.savings_goal_service import amount_to_monthly
from app.utils.money import round_money

# Either ORM template class exposes ``recurrence_rule`` and
# ``default_amount`` -- the aggregator reads them via attribute
# access. Test fixtures that build ``types.SimpleNamespace`` mock
# templates rely on the same duck-typed contract; the static type
# hint names the two production classes for IDE / pylint
# consumption.
RecurringTemplate = Union[TransactionTemplate, TransferTemplate]


def template_monthly_or_none(
    template: RecurringTemplate,
    as_of: date,
) -> Decimal | None:
    """Return the monthly equivalent of one recurring template, or None.

    Applies the shared filter (no rule, expired, missing/zero amount,
    ONCE pattern). The returned Decimal is NOT quantized -- callers
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

    Returns:
        The full-precision Decimal monthly equivalent, or ``None`` if
        the template is filtered out by any of the shared-filter
        rules. ``None`` means "do not include this template in any
        monthly-equivalent total."
    """
    rule = getattr(template, "recurrence_rule", None)
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

    return amount_to_monthly(amount, rule.pattern_id, rule.interval_n)


def committed_monthly(
    templates: Iterable[RecurringTemplate],
    as_of: date,
) -> Decimal:
    """Sum monthly equivalents across a set of recurring templates.

    Routes every template through ``template_monthly_or_none``, which
    applies the shared filter (no rule, expired, missing/zero amount,
    ONCE pattern). Templates returning ``None`` contribute zero to
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

    Returns:
        The total monthly-equivalent Decimal, rounded to cents with
        ``ROUND_HALF_UP``. Returns ``Decimal("0.00")`` if every input
        template is filtered out or the iterable is empty.
    """
    total = Decimal("0")
    for template in templates:
        monthly = template_monthly_or_none(template, as_of)
        if monthly is not None:
            total += monthly
    return round_money(total)
