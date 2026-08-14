"""
Shekel Budget App -- Recurrence occurrence preview

The read-only HTMX fragment that shows the next five pay periods a recurrence
would land in, and the helpers that build it.  The endpoint is deliberately
kind-agnostic -- both the transaction-template form and the transfer-template
form point their live preview at it (``_recurrence_fields.html``) -- so none of
this belongs inside the transaction-template CRUD module it used to live in.
``templates.preview_recurrence`` is now the route decorator and one call.

The preview reads request args and never writes: it resolves a TRANSIENT rule
through the same authoring seam a save goes through
(:func:`app.services.recurrence.build_transient_rule`), so what the user is
shown is what saving would produce.  Before plan step R2c-1 it built the rule by
hand and derived the ``Every N Periods`` phase inline, which is exactly the
kind of second copy of a derivation the seam exists to remove.

Route-layer module rather than service because these read ``request`` and
``current_user``; the leading underscore marks it route-internal.
"""
import logging
from datetime import date

from flask import request
from flask_login import current_user
from markupsafe import Markup

from app.enums import PeriodPlacementEnum, RecurrenceUnitEnum
from app.models.recurrence_rule import RecurrenceRule
from app.services import pay_period_service
from app.services.pay_calendar import DerivedPeriod, PayCalendar, calendar_for
from app.services.recurrence import (
    NEVER_ENDS,
    EndBoundInputError,
    RecurrenceResolutionError,
    RecurrenceSpec,
    build_transient_rule,
    end_bound_from_token,
    modelled_placement,
    modelled_unit,
    placed_periods,
    rule_occurrences,
)

logger = logging.getLogger(__name__)

#: How many upcoming occurrences the fragment lists.
PREVIEW_OCCURRENCE_LIMIT = 5


def _submitted_iso_date(field: str) -> date | None:
    """Return an ISO date query argument, or ``None`` when it is unusable.

    ``date.fromisoformat`` raises ``ValueError`` on anything that is not an
    ISO date, and this endpoint reads its values straight from
    ``request.args`` -- so ``?end_date=garbage`` was an unhandled 500 for any
    signed-in user.  Found by a neutral review of plan step R4a, which had
    closed the OTHER four unvalidated arguments through
    :class:`~app.services.recurrence.RecurrenceResolutionError` and then
    claimed the whole class; a value parsed BEFORE the seam cannot be refused
    by it.

    An unparseable bound is treated as ABSENT rather than refused, and the
    asymmetry with the day / month / interval refusals is deliberate: those
    are values the rule would be SAVED with, so a wrong one must not be
    previewed as though it were fine.  A bound that is not a date is not a
    bound either form can submit -- ``<input type="date">`` and the
    Marshmallow schema both produce ISO or nothing -- so it is a hand-crafted
    query string, and the honest preview of "no parseable bound" is the
    unbounded rule.

    **Both bounds share this one parser since plan step R7b-4**, which gave
    the OPENING bound a control: two copies of "parse it or drop it" is the
    shape that leaves one of them missing a fix the other got.  An
    out-of-RANGE opening bound is a different question and is NOT answered
    here -- ``resolve`` refuses it for every caller, and this endpoint's
    existing handler reports it (see :func:`recurrence_preview_fragment`).

    Args:
        field: The query-argument name to read.

    Returns:
        The parsed date, or ``None`` when absent or unparseable.
    """
    submitted = request.args.get(field)
    if not submitted:
        return None
    try:
        return date.fromisoformat(submitted)
    except ValueError:
        logger.info(
            "Recurrence preview ignored an unparseable %s for user %s",
            field, current_user.id,
        )
        return None


def build_preview_rule(
    unit: RecurrenceUnitEnum,
    placement: PeriodPlacementEnum,
    start_date: date | None,
    calendar: PayCalendar,
) -> RecurrenceRule:
    """Build an unsaved, fully resolved rule from the preview request args.

    Goes through the authoring seam like every other writer, but via
    :func:`~app.services.recurrence.build_transient_rule`, which resolves
    without touching the session -- so the previewed rule carries the columns
    the saved one would, including the ``Every N Periods`` phase this route
    used to derive for itself.

    Takes the AXES the form authors rather than a pattern id (plan step
    R7b-2): the preview reads the same two controls the save posts, so neither
    can read one submission as a different cadence than the other.  Before that
    it took the closed-set id and decoded it here, which was one translation
    the form no longer needs -- and the ``ref`` row it fetched before plan step
    R2e-2 was written onto the transient rule's ``pattern`` relationship for a
    reader that does not exist.

    ``interval_n`` is read straight from the query args and NOT bounded here:
    the authoring seam refuses a non-positive interval, which is the caller's
    ``RecurrenceResolutionError`` handler's job -- see :func:`preview_fragment`
    for why every bound is stated once, on the column and its mirror in
    ``resolve``, rather than a third time on this endpoint.

    Args:
        unit: The submitted cadence unit, already checked as modelled by the
            caller.
        placement: The submitted placement, likewise.
        start_date: The submitted opening bound, already parsed by the caller,
            or ``None``.
        calendar: The user's pay-period schedule.

    Returns:
        The transient :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    return build_transient_rule(
        RecurrenceSpec(
            user_id=current_user.id,
            unit=unit,
            interval_n=request.args.get("interval_n", type=int, default=1),
            placement=placement,
            day_of_month=request.args.get("day_of_month", type=int),
            month_of_year=request.args.get("month_of_year", type=int),
            # The OPENING bound (plan step R7b-4).  It replaced an
            # owner-checked ``start_period_id``, and the ownership probe went
            # with it rather than being relocated: a DATE names nothing of
            # anyone else's, so the disclosure this endpoint was guarded
            # against (audit finding H3 -- another user's pay-period
            # structure) is not expressible in the argument any more.
            start_date=start_date,
            # Composed through the SUBMISSION door, not the storage one (plan
            # step R7b-3).  These are query args -- a submission -- so a
            # mistake in them is user input, and
            # ``end_bound_from_columns`` would have reported it as a row
            # written around ``ck_recurrence_rules_single_end_bound``: a log
            # line asserting corrupted data that does not exist.  The form
            # posts the same three controls the save does, so the preview
            # reads one submission the way the save reads it.
            end_bound=end_bound_from_token(
                request.args.get(
                    "recurrence_end_mode", default=NEVER_ENDS.token,
                ),
                end_date=_submitted_iso_date("end_date"),
                max_occurrences=request.args.get(
                    "max_occurrences", type=int,
                ),
            ),
        ),
        calendar,
    )


def render_preview_html(
    preview_periods: list[DerivedPeriod],
) -> Markup:
    """Render the occurrence-preview HTML fragment for *preview_periods*.

    Args:
        preview_periods: The matched
            :class:`~app.services.pay_calendar.DerivedPeriod` values to list --
            the calendar's own view of a pay period, which is what
            :func:`~app.services.recurrence.rule_occurrences` answers in.  Only
            the two dates are rendered.

    Returns:
        The fragment markup.
    """
    items = "".join(
        f"<li>{p.start_date.strftime('%b %d, %Y')} - {p.end_date.strftime('%b %d, %Y')}</li>"
        for p in preview_periods
    )
    html = (
        f"<small class='text-muted'>Next {len(preview_periods)} occurrences:</small>"
        f"<ul class='list-unstyled mb-0 ms-2'><small>{items}</small></ul>"
    )
    return Markup(html)


def recurrence_preview_fragment() -> str:
    """Return the preview fragment for the recurrence the request describes.

    The whole body of ``templates.preview_recurrence``, beside the three
    helpers it composes rather than in the transaction-template CRUD module
    that merely routes to it.

    **Both submitted AXES are checked against what the application MODELS**
    (plan step R2e-2's rule, on the two fields plan step R7b-2 replaced its one
    with).  They used to be checked against the ``ref`` table, and the two are
    not the same set: a row no enum member names passes a table lookup and then
    raises inside the authoring seam :func:`build_preview_rule` goes through --
    so the preview would 500 on the same input the picker refuses to offer.

    An ABSENT unit is the form's "does not repeat" option, which has no
    occurrences to preview, and it keeps its own message because users read it.
    An absent PLACEMENT is not the same state: a placement is the cadence's
    second half rather than an optional refinement, so it takes the unmodelled
    answer (via the ``or 0`` below, which no ``ref`` row can carry) rather than
    being defaulted into a schedule the save would not produce.

    **Every OTHER query arg is unvalidated, and plan step R4a made that a 200
    instead of a 500.**  ``interval_n`` / ``day_of_month`` / ``month_of_year``
    / ``recurrence_end_mode`` / ``end_date`` / ``max_occurrences`` are read
    straight from ``request.args``; the two form
    schemas bound them, nothing bounds this endpoint, and it is reachable by
    anyone signed in.  Measured at R4a: ``?interval_n=0`` raised out of the
    authoring seam, ``?month_of_year=13`` raised ``ValueError`` from
    ``monthrange(year, 13)`` inside the matcher R4a deleted,
    ``?day_of_month=-5`` raised ``ValueError`` from ``date(y, m, -5)``, and
    ``?day_of_month=32`` / ``?month_of_year=99`` answered 200 with a silently
    clamped or modulo-wrapped date.  Catching the ONE exception the resolution
    seam raises answers all five, and keeps each bound stated once -- on the
    column and its mirror in :func:`app.services.recurrence.resolve` -- rather
    than a third time here.

    The two DATE bounds are the exception, and it took a second review to
    see the first of them: they are parsed BEFORE the seam and so cannot be
    refused by it.
    :func:`_submitted_iso_date` handles it -- BOTH bounds, through one
    parser since plan step R7b-4 -- and the docstring there says why an
    unparseable bound is dropped rather than refused.  An out-of-RANGE
    ``start_date`` is refused by the seam like everything else, so the
    handler below reports it.

    Returns:
        The fragment markup, or a muted one-line explanation when there is
        nothing to preview.
    """
    unit_id = request.args.get("recurrence_unit", type=int)
    if not unit_id:
        return "<small class='text-muted'>No preview for this cadence</small>"

    # The placement is REQUIRED once a unit is named: it is the second axis of
    # the cadence, not an optional refinement, and defaulting it here would let
    # the preview show a schedule the save would not produce.
    #
    # ONE refusal for both axes, because they share a disposition and a
    # reachability: the form posts ids it derived from
    # ``cadence_options``, so neither can be unmodelled through any click.  The
    # ABSENT unit above keeps its own message because that one IS reachable --
    # it is what "does not repeat" posts, and its copy is read by users.
    unit = modelled_unit(unit_id)
    placement = modelled_placement(
        request.args.get("recurrence_placement", type=int) or 0,
    )
    if unit is None or placement is None:
        return "<small class='text-muted'>Unknown cadence</small>"

    # The schedule is resolved BEFORE the rule: the authoring seam measures a
    # rule's first occurrence against it, so an empty schedule is refused
    # rather than anchored against nothing.  ONE calendar, from the same door
    # the SAVE goes through, serves the authoring seam, the match and the
    # empty-schedule check below -- which is what stops the preview from
    # resolving against a different schedule than the save would (plan step
    # R4b-1), and since plan step C2-b2 it is the same door and the same TYPE.
    calendar = calendar_for(current_user.id)
    if not calendar.periods:
        return "<small class='text-muted'>No pay periods generated yet</small>"

    start_date = _submitted_iso_date("start_date")

    # ``effective_from`` is a DISPLAY choice -- "show me the next five from
    # here" -- and so the route's, not the rule's: the rule's own opening
    # bound is its anchor.
    #
    # It follows the submitted "Starts on" when there is one, so the list
    # opens where the user says the rule does rather than at today's
    # paycheck.  That is not the same as APPLYING the bound -- the rule's
    # anchor does that, and it does it whether or not this window agrees --
    # but showing five occurrences the user cannot see the start of is a
    # preview that answers a question nobody asked (this is what the retired
    # "First paycheck" select drove, kept because the reasoning was about the
    # WINDOW rather than about the control).
    if start_date is not None:
        effective_from = start_date
    else:
        current_period = pay_period_service.get_current_period(current_user.id)
        effective_from = (
            current_period.start_date if current_period
            else calendar.opening_bound()
        )

    try:
        rule = build_preview_rule(
            unit, placement, start_date, calendar,
        )
        # ``effective_from`` is this ROUTE's display choice, made above --
        # "show me the next five from here" -- never the rule's opening bound,
        # which is its anchor.  The retired ``match_periods`` adapter applied
        # the bound for its callers, which is how a caller's window came to
        # look like a property of the recurrence (defect D2); the PROJECTION is
        # still shared, so this surface and the generation seam cannot come to
        # disagree about which periods a rule fires in.
        matching = placed_periods(
            rule_occurrences(rule, calendar),
            ending_on_or_after=effective_from,
        )
    except (RecurrenceResolutionError, EndBoundInputError) as exc:
        # The submitted arguments do not name a recurrence this application can
        # resolve OR can store.  The user gets a muted line either way, because
        # from the form's side both mean "there is nothing to preview for what
        # you typed" -- but the LOG carries the refusal's own message, which
        # names the field, the value and the constraint it broke.  Dropping it
        # would waste the one thing the door goes to length to produce.
        #
        # **The unstorable half arrives here because ``_author`` ENCODES before
        # it resolves**, which an adversarial review of plan step R7b-2 made
        # the order: a cadence with no closed-set pattern is refused before the
        # month walk touches it, so ``(10000, YEAR)`` is this muted line rather
        # than a ``ValueError`` out of ``date()`` that no handler here catches.
        logger.info(
            "Recurrence preview refused unresolvable arguments for user %s: %s",
            current_user.id, exc,
        )
        return "<small class='text-muted'>No preview for this cadence</small>"

    preview_periods = matching[:PREVIEW_OCCURRENCE_LIMIT]
    if not preview_periods:
        return "<small class='text-muted'>No matching periods found</small>"
    return render_preview_html(preview_periods)


__all__ = [
    "PREVIEW_OCCURRENCE_LIMIT",
    "build_preview_rule",
    "recurrence_preview_fragment",
    "render_preview_html",
]
