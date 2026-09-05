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
  2. Skip if anything that stops the definition has already stopped it
     (``recurrence.has_ended``) -- an expired recurring template is no
     longer a future obligation. This is the filter the audit found
     missing from ``compute_committed_monthly``.  **It reads the COMPOSED
     closing since plan step R7d-e**: the bound the owner authored AND the
     stop the destination derives, through the one door
     (``recurring_definition.read_definition``).  Until then it read the
     rule's own two columns, and for a loan payment those hold the
     chokepoints' CACHE of the payoff (ruling **R-R56**) -- so a retired
     loan's payment stayed in both totals until some chokepoint happened to
     rewrite the column, and left on the day it did rather than on the day
     the loan was finished -- and where the cache was EARLIER than the
     payoff (ledger row **D35**'s shape) a LIVE payment left them early.
     **MOVES MONEY** on both surfaces in both directions; nothing moves only
     where the cached column agrees with the derived stop on whether the
     payment has ended on the day asked, which is what both live loans do
     on the dev database today.
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

**Both entry points take the READ PASS** (plan step R7d-e), the
:class:`~app.services.balance_at.BalanceContext` the route built, because
step 2 of the filter is a question about a definition's DESTINATION as well as
its rule and only the pass can fold a loan's balance.  A pass carries the
owner's schedule and the ``as_of`` the pass is read at, so the two cannot be
handed in disagreeing -- which is the same reason every producer beside this
one takes it.  Only a route builds one (developer ruling 2026-08-16); this
module TAKES it.

Every function here is a read: ORM template instances (or any object exposing
the same ``recurrence_rule`` / ``default_amount`` attributes -- duck-typing
supports the ``types.SimpleNamespace`` mock templates used in tests) and the
pass in, Decimal results out.  No Flask imports.
"""

from decimal import Decimal
from typing import Iterable, Union

from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services.balance_at import BalanceContext
from app.services.recurrence import RuleReading, cadence_of, has_ended
from app.services.recurring_definition import read_definition
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
    ``types.SimpleNamespace``; "does not repeat" is the ABSENCE of a
    ``budget.recurrence_rules`` row naming the definition, on both ORM kinds
    since plan step R2e-3 (which read as ``recurrence_rule_id IS NULL`` until
    plan step R-F6 moved the owning FK onto the rule).

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


def _committed_amount(template: RecurringTemplate) -> Decimal | None:
    """Return what *template* commits per occurrence, or ``None``.

    Filter steps 1 and 3 -- the two that need neither a schedule nor a walk: a
    definition that does not repeat commits nothing, and so does one with a
    missing or zero amount.  Held apart from step 2 so both entry points can
    ask them BEFORE the door is read: step 2 resolves the definition against
    the owner's schedule, folds its destination and walks its occurrences,
    which is real work to reach the same ``None`` a zero amount reaches for
    free.  The three are independent skips, so their order is ours to choose,
    and this order was plan step R7b-3's for the same reason.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` (or any
            object exposing ``recurrence_rule`` and ``default_amount``).

    Returns:
        The committed amount as a ``Decimal``, or ``None`` when the definition
        does not repeat or states no positive amount.
    """
    if template_rule(template) is None:
        return None
    amount = template.default_amount
    if amount is None:
        return None
    amount = Decimal(str(amount))
    if amount == 0:
        return None
    return amount


def monthly_or_none(
    template: RecurringTemplate, reading: RuleReading, ctx: BalanceContext,
) -> Decimal | None:
    """Return the monthly equivalent of a definition already READ, or ``None``.

    The layer under :func:`template_monthly_or_none` for a caller that has
    already read the definition through the composed door and holds the
    reading: the Recurring surface reads every definition once for its stop
    line and its next date, and its monthly column is a third question about
    that same reading rather than a second resolution of the rule
    (``CLAUDE.md`` rule 14, ONE WALK -- and the second resolution this deletes
    is the one ``test_a_loan_payment_is_RESOLVED_exactly_once_per_build`` had
    to scope itself around).

    Applies the shared filter (no rule -- which is how a definition says it
    does not repeat -- expired, missing/zero amount).  The returned Decimal is
    NOT quantized -- callers that aggregate first then round
    (``committed_monthly``) need full precision; callers that display a per-row
    value round at the display boundary with ``round_money``.

    **The conversion is one expression**, and the same one for every cadence:
    an amount times how often it happens in a year, over twelve.  Plan step
    R7a-2b replaced a seven-branch switch with it, so ``(2, MONTH)`` and
    ``(1, WEEK)`` -- the cadences plan step R8 makes authorable -- already
    total correctly rather than falling to a ``None`` the caller drops.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` ORM
            instance (or any object exposing ``recurrence_rule`` and
            ``default_amount``).  The recurrence rule is read via attribute
            access; loading is the caller's responsibility
            (``joinedload(.recurrence_rule)`` in the production routes).
        reading: *template* read through
            :func:`app.services.recurring_definition.read_definition` against
            *ctx* -- what its rule means, narrowed by what its destination
            allows, and every occurrence that names.  The expired filter reads
            the composed closing off it and judges it against the walk it
            already holds.
        ctx: The read pass.  Its ``as_of`` is the day the expired filter asks
            about; its ``calendar()`` supplies the cadence the conversion
            needs and the horizon the filter needs -- ONE schedule, so a
            paycheck-space template's monthly equivalent is measured against
            the same rhythm its stop was judged against.

    Returns:
        The full-precision Decimal monthly equivalent, or ``None`` if the
        template is filtered out by any of the shared-filter rules.  ``None``
        means "do not include this template in any monthly-equivalent total."

    Raises:
        RecurrenceResolutionError: The rule names a cadence this application
            does not model, so it has no derivable cadence.  A REFUSAL rather
            than a skip since plan step R7a-2b: a rule the app cannot read is a
            broken invariant, and dropping it silently understated every total
            this module feeds while the Recurring surface 500'd on the same
            row.
    """
    amount = _committed_amount(template)
    if amount is None:
        return None

    rule = template_rule(template)
    calendar = ctx.calendar()
    if has_ended(rule, reading, calendar, on=ctx.as_of):
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


def template_monthly_or_none(
    template: RecurringTemplate, ctx: BalanceContext,
) -> Decimal | None:
    """Return the monthly equivalent of one recurring template, or ``None``.

    :func:`monthly_or_none` for a caller that holds the template and the pass
    but has not read the definition: this reads it through the composed door
    (:func:`app.services.recurring_definition.read_definition`) and hands the
    reading down.  The ``/savings`` emergency-fund floor and the per-goal
    contribution floors take this entry; the Recurring surface, which has
    already read every definition once, takes the one below it.

    The two schedule-free skips are asked FIRST, here as well as there, so a
    definition that cannot contribute costs no door read.

    Args:
        template: A ``TransactionTemplate`` or ``TransferTemplate`` ORM
            instance, or any object exposing ``recurrence_rule``,
            ``default_amount`` and ``to_account_id`` -- the door's own
            duck-typed contract (:data:`~app.services.recurrence.
            RecurrenceOwner`).  **Must belong to ``ctx.user_id``**: the caller
            owns the ownership check, as every seam entry this reaches states.
        ctx: The read pass.  See :func:`monthly_or_none`.

    Returns:
        See :func:`monthly_or_none`.

    Raises:
        RecurrenceResolutionError: See :func:`monthly_or_none`; also raised by
            the door for a rule paired with another owner's pass.
        RecurrenceGenerationError: The resolved value names something the
            occurrence engine cannot walk -- a business-day shift, until plan
            step R8-d gives it a walk.
        BaselineMissingError: The definition pays into a configured loan and
            *ctx* has no baseline scenario (ruling **R-R30**), from the seam's
            own ``require_scenario``: the loan's stop is a fold over its plan,
            and a producer that needs a scenario REFUSES rather than answering
            a bound nothing derived.  A definition with no loan behind it
            still resolves for such an owner.
    """
    if _committed_amount(template) is None:
        return None
    return monthly_or_none(template, read_definition(template, ctx), ctx)


def committed_monthly(
    templates: Iterable[RecurringTemplate], ctx: BalanceContext,
) -> Decimal:
    """Sum monthly equivalents across a set of recurring templates.

    Routes every template through :func:`template_monthly_or_none`, which
    applies the shared filter (no rule, expired, missing/zero amount).
    Templates returning ``None`` contribute zero to the total; only non-None
    Decimals are summed.  The final result is rounded once at the boundary with
    ``round_money`` (ROUND_HALF_UP via ``app.utils.money``) -- intermediate
    sums stay at full Decimal precision so penny-level drift cannot accumulate.

    This is the single canonical aggregator behind both the Recurring
    surface's totals (the retired ``/obligations`` page's kernel) and the
    ``/savings`` emergency-fund baseline + per-goal contribution-floor figures.
    Per E-24 / HIGH-05, every consumer must call this function rather than
    inline its own filter+sum loop.

    Args:
        templates: Iterable of ORM template instances (``TransactionTemplate``,
            ``TransferTemplate``, or any duck-typed equivalent).  Callers are
            responsible for scoping the query (user_id, is_active, account_id,
            etc.); this function applies only the cross-cutting recurrence
            filter, not the data-ownership filter.  **Every one must belong to
            ``ctx.user_id``.**
        ctx: The read pass, one value for the whole set: these templates belong
            to one owner, so summing figures resolved against two schedules or
            judged on two days would be adding different units.

    Returns:
        The total monthly-equivalent Decimal, rounded to cents with
        ``ROUND_HALF_UP``.  Returns ``Decimal("0.00")`` if every input template
        is filtered out or the iterable is empty.

    Raises:
        RecurrenceResolutionError: A template's rule names a cadence this
            application does not model, so the total cannot be completed.  The
            whole sum is refused rather than shrunk by one row -- see
            :func:`monthly_or_none`, and note that this function's three
            callers (the Recurring surface, the emergency-fund floor and the
            per-goal contribution floors) each publish a figure a missing row
            would silently understate.
        RecurrenceGenerationError: See :func:`template_monthly_or_none`.
        BaselineMissingError: See :func:`template_monthly_or_none`.
    """
    total = Decimal("0")
    for template in templates:
        monthly = template_monthly_or_none(template, ctx)
        if monthly is not None:
            total += monthly
    return round_money(total)
