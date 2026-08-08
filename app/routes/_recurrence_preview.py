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

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.services import pay_period_service, recurrence_engine
from app.services.recurrence import (
    PeriodCalendar,
    RecurrenceResolutionError,
    RecurrenceSpec,
    SchedulePeriod,
    build_transient_rule,
    modelled_pattern,
)

logger = logging.getLogger(__name__)

#: How many upcoming occurrences the fragment lists.
PREVIEW_OCCURRENCE_LIMIT = 5


def owned_preview_start_period() -> PayPeriod | None:
    """Return the submitted start period when this user owns it, else ``None``.

    Ownership check: another user's period is rejected rather than used, so
    the preview cannot disclose someone else's pay-period structure (audit
    finding H3).  Falling through to ``None`` leaves the preview on this
    user's own schedule.

    Returns:
        The owned :class:`~app.models.pay_period.PayPeriod`, or ``None`` when
        none was submitted or it is not this user's.
    """
    start_period_id = request.args.get("start_period_id", type=int)
    if start_period_id is None:
        return None
    start_period = db.session.get(PayPeriod, start_period_id)
    if start_period is None or start_period.user_id != current_user.id:
        return None
    return start_period


def _submitted_end_date() -> date | None:
    """Return the submitted ``end_date``, or ``None`` when it is unusable.

    ``date.fromisoformat`` raises ``ValueError`` on anything that is not an
    ISO date, and this endpoint reads the value straight from
    ``request.args`` -- so ``?end_date=garbage`` was an unhandled 500 for any
    signed-in user.  Found by a neutral review of plan step R4a, which had
    closed the OTHER four unvalidated arguments through
    :class:`~app.services.recurrence.RecurrenceResolutionError` and then
    claimed the whole class; this one never reaches the resolution seam, so
    the seam cannot refuse it.

    An unparseable bound is treated as ABSENT rather than refused, and the
    asymmetry with the day / month / interval refusals is deliberate: those
    are values the rule would be SAVED with, so a wrong one must not be
    previewed as though it were fine.  A bound that is not a date is not a
    bound the form can submit either -- ``<input type="date">`` and the
    Marshmallow schema both produce ISO or nothing -- so it is a hand-crafted
    query string, and the honest preview of "no parseable end date" is the
    unbounded rule.

    Returns:
        The parsed bound, or ``None`` when absent or unparseable.
    """
    submitted = request.args.get("end_date")
    if not submitted:
        return None
    try:
        return date.fromisoformat(submitted)
    except ValueError:
        logger.info(
            "Recurrence preview ignored an unparseable end_date for user %s",
            current_user.id,
        )
        return None


def build_preview_rule(
    pattern_id: int,
    start_period: PayPeriod | None,
    calendar: PeriodCalendar,
) -> RecurrenceRule:
    """Build an unsaved, fully resolved rule from the preview request args.

    Goes through the authoring seam like every other writer, but via
    :func:`~app.services.recurrence.build_transient_rule`, which resolves
    without touching the session -- so the previewed rule carries the columns
    the saved one would, including the ``Every N Periods`` phase this route
    used to derive for itself.

    Takes the pattern's ID rather than its ``ref`` row (plan step R2e-2): the
    caller now validates the submitted id against what the application MODELS,
    which needs no row, and the row it used to fetch was written onto the
    transient rule's ``pattern`` relationship for a reader that does not
    exist -- ``recurrence_engine.match_periods`` dispatches on the
    ``pattern_id`` argument it is passed separately, and the transient rule is
    discarded immediately afterwards.

    Args:
        pattern_id: The ``ref.recurrence_patterns`` id being previewed,
            already checked as modelled by the caller.
        start_period: The owner-checked start period, or ``None``.
        calendar: The user's pay-period schedule.

    Returns:
        The transient :class:`~app.models.recurrence_rule.RecurrenceRule`.
    """
    return build_transient_rule(
        RecurrenceSpec(
            user_id=current_user.id,
            pattern_id=pattern_id,
            interval_n=request.args.get("interval_n", type=int, default=1),
            day_of_month=request.args.get("day_of_month", type=int),
            month_of_year=request.args.get("month_of_year", type=int),
            start_period_id=start_period.id if start_period else None,
            end_date=_submitted_end_date(),
        ),
        calendar,
    )


def render_preview_html(
    preview_periods: list[SchedulePeriod],
) -> Markup:
    """Render the occurrence-preview HTML fragment for *preview_periods*.

    Args:
        preview_periods: The matched
            :class:`~app.services.recurrence.SchedulePeriod` values to list --
            the resolver's own view of a pay period, which is what
            ``match_periods`` answers in since plan step R4b-1.  Only the two
            dates are rendered, so the fragment is unchanged.

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

    The submitted pattern is checked against what the application MODELS (plan
    step R2e-2).  It used to be checked against the ``ref`` table, and the two
    are not the same set: a row ``RecurrencePatternEnum`` does not name passes
    a table lookup and then raises inside the authoring seam
    :func:`build_preview_rule` goes through -- so the preview would 500 on the
    same input the picker refuses to offer.

    An ABSENT pattern is the form's "does not repeat" option, which has no
    occurrences to preview.  A second guard used to sit beside it for the
    ``Once`` pattern that meant the same thing; plan step R2e-3 retired it, so
    the empty submission is the only non-recurring input left.

    **Every OTHER query arg is unvalidated, and plan step R4a made that a 200
    instead of a 500.**  ``interval_n`` / ``day_of_month`` / ``month_of_year``
    / ``end_date`` are read straight from ``request.args``; the two form
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

    ``end_date`` is the exception, and it took a second review to see: it is
    parsed BEFORE the seam and so cannot be refused by it.
    :func:`_submitted_end_date` handles it, and the docstring there says why
    an unparseable bound is dropped rather than refused.

    Returns:
        The fragment markup, or a muted one-line explanation when there is
        nothing to preview.
    """
    pattern_id = request.args.get("recurrence_pattern", type=int)
    if not pattern_id:
        return "<small class='text-muted'>No preview for this pattern</small>"

    if modelled_pattern(pattern_id) is None:
        return "<small class='text-muted'>Unknown pattern</small>"

    periods = pay_period_service.get_all_periods(current_user.id)
    if not periods:
        return "<small class='text-muted'>No pay periods generated yet</small>"

    start_period = owned_preview_start_period()

    # ``effective_from`` is a DISPLAY choice -- "show me the next five from
    # here" -- and so the route's, not the rule's: the rule's own opening
    # bound is its anchor.
    if start_period is not None:
        effective_from = start_period.start_date
    else:
        current_period = pay_period_service.get_current_period(current_user.id)
        effective_from = (
            current_period.start_date if current_period
            else periods[0].start_date
        )

    try:
        # The schedule is resolved BEFORE the rule: the authoring seam
        # measures a rule's first occurrence against it, so an empty schedule
        # is refused rather than anchored against nothing.  Built from the
        # periods already loaded, so this adds no query -- and ONE calendar
        # serves both the authoring seam and the match, which is what stops
        # the preview from resolving against a different schedule than the
        # save would (plan step R4b-1).
        calendar = PeriodCalendar.from_pay_periods(periods, current_user.id)
        rule = build_preview_rule(pattern_id, start_period, calendar)
        matching = recurrence_engine.match_periods(
            rule, calendar, effective_from,
        )
    except RecurrenceResolutionError as exc:
        # The submitted arguments do not name a recurrence this application
        # can resolve.  The user gets the same muted line an unmodelled
        # pattern gets, because from the form's side both mean "there is
        # nothing to preview for what you typed" -- but the LOG carries the
        # refusal's own message, which names the field, the value and the
        # constraint it broke.  Dropping it would waste the one thing the
        # door goes to length to produce.
        logger.info(
            "Recurrence preview refused unresolvable arguments for user %s: %s",
            current_user.id, exc,
        )
        return "<small class='text-muted'>No preview for this pattern</small>"

    preview_periods = matching[:PREVIEW_OCCURRENCE_LIMIT]
    if not preview_periods:
        return "<small class='text-muted'>No matching periods found</small>"
    return render_preview_html(preview_periods)


__all__ = [
    "PREVIEW_OCCURRENCE_LIMIT",
    "build_preview_rule",
    "owned_preview_start_period",
    "recurrence_preview_fragment",
    "render_preview_html",
]
