"""
Shekel Budget App -- Recurrence-Form Route Helpers (F-24, F-26)

Recurrence-form and conflict-chooser helpers shared between the
transaction-template (:mod:`app.routes.templates`) and transfer-template
(:mod:`app.routes.transfers`) CRUD routes:

* :func:`build_recurrence_rule_from_form` -- consumes a Marshmallow-
  validated payload, pops the recurrence-related keys, and returns a
  fresh :class:`RecurrenceRule` (added to the session and flushed),
  ``None`` when no pattern was selected, or a Flask redirect
  :class:`Response` when validation fails (invalid pattern id,
  invalid start period for every-N-periods auto-offset).  [F-24]
* :func:`update_recurrence_rule_from_form` -- sibling of the builder
  for the pattern-changed-on-an-existing-rule branch: re-points the
  template's current :class:`RecurrenceRule` in place (preserving its
  id and the owning FK), pops the recurrence keys, and returns
  ``None`` on success or a redirect :class:`Response` for an invalid
  pattern id.  [F-24]
* :func:`resolve_recurrence_rule_for_update` -- dispatches the two
  update-form branches (re-point existing rule vs build + link a new
  one) so each ``update_*`` route resolves its recurrence rule with a
  single call.  [F-24]
* :func:`handle_stale_form_conflict` -- pre-flush optimistic-locking
  guard for the ``submitted_version != template.version_id``
  branch; logs both counters so post-mortem analysis can reconstruct
  the race; redirects.  [F-26 pair 1]
* The recurrence-conflict chooser (Loop B, P3) --
  :func:`parse_conflict_decisions`, :func:`render_recurrence_conflict_chooser`,
  and :func:`apply_conflict_decisions` (plus the :class:`ConflictChoice`,
  :class:`RecurrenceConflictKind`, and :class:`ConflictChooserContext`
  data holders).  When a template edit's regeneration collides with
  hand-edited upcoming instances, the update route renders a full-page
  chooser instead of committing: the pending edit is rolled back, and
  Apply re-runs the identical edit before resolving each instance
  (keep the override, or move it to the template's new value) through
  the kind's ``resolve_conflicts``.  Shared by the transaction-template
  and transfer-template routes; each supplies its own
  :class:`RecurrenceConflictKind`.  [Loop B P3, replacing the F-26 pair-2
  auto-keep advisory]

The first three helpers share a verbatim trio of inputs -- the form's
recurrence end date, the validation-error redirect target, and the
transaction-vs-transfer ``due_day_of_month`` flag -- bundled into the
frozen :class:`RecurrenceFormContext`.  :func:`handle_stale_form_conflict`
reuses :class:`~app.routes._commit_helpers.StaleConflictContext` (the
same bundle its commit-time sibling :func:`~app.routes._commit_helpers.handle_stale_conflict`
takes), adding only the submitted / current version counters.

The general commit-time stale-conflict wrappers
(``commit_or_handle_stale``, ``handle_stale_conflict``) used to live
here too; they moved to :mod:`app.routes._commit_helpers` once the
salary / savings / account CRUD routes needed them as well.

Route-layer module rather than service because these helpers consume
Flask ``flash`` / ``redirect`` / ``url_for`` (the last two via
:class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.
The leading underscore marks the module as route-internal.

Module-level flash-template constants centralise the canonical
"stale by another action" copy without forcing every caller through a
single wording (some routes name "while you were editing" -- the
update-template / update-transfer-template forms; others omit it --
archive / unarchive / hard-delete).
"""
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

from flask import Response, flash, render_template, request, url_for
from flask_login import current_user

from app.exceptions import RecurrenceConflict
from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import RecurrencePattern
from app.routes._commit_helpers import StaleConflictContext
from app.routes._redirect_target import RedirectTarget
from app.services import pay_period_service
from app.services.recurrence import (
    RecurrenceSpec,
    author_rule,
    calendar_for,
    reauthor_rule,
    recurrence_spec,
)
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.digit_strings import parse_row_id


# Stale-conflict flash templates.  The ``{noun}`` placeholder is
# substituted by the caller ("recurring transaction" /
# "recurring transfer") so the human label matches the route's
# domain without forcing the helper to know the route taxonomy.

STALE_EDITING_MESSAGE: str = (
    "This {noun} was changed by another action while you were "
    "editing.  Please reload and try again."
)
"""Flash template for routes invoked from an edit form (update_*)."""

STALE_ACTION_MESSAGE: str = (
    "This {noun} was changed by another action.  "
    "Please reload and try again."
)
"""Flash template for non-edit-form mutations (archive / unarchive /
hard-delete) where "while you were editing" would be misleading."""



# Keys the recurrence-rule helper pops from the validated form payload
# regardless of whether a pattern was selected.  Listed here as
# module-level constants so the "drop every recurrence key" logic
# stays in one place.

_BASE_RECURRENCE_KEYS: tuple[str, ...] = (
    "interval_n",
    "offset_periods",
    "day_of_month",
    "month_of_year",
    "end_date",
)

_DUE_DAY_KEY: str = "due_day_of_month"


@dataclass(frozen=True)
class RecurrenceFormContext:
    """Recurrence-form processing options shared across the F-24 helpers.

    A parameter object, not a single domain concept: it groups the three
    otherwise-independent knobs the helpers read so the verbatim-triplicated
    signature tail collapses to one argument (and ``resolve`` forwards it
    unchanged).

    Bundles the three inputs that :func:`build_recurrence_rule_from_form`,
    :func:`update_recurrence_rule_from_form`, and
    :func:`resolve_recurrence_rule_for_update` share verbatim and that
    ``resolve`` forwards unchanged: the form's recurrence end date, the
    validation-error redirect target, and whether the submitting schema
    exposes ``due_day_of_month`` (transaction templates) or not
    (transfer templates).  Collapsing the formerly-triplicated
    ``end_date_value`` / ``redirect_endpoint`` / ``redirect_endpoint_kwargs``
    / ``include_due_day_of_month`` signature tail into one object both
    removes the duplication and clears the per-helper
    ``too-many-arguments`` count.

    Attributes:
        end_date_value: The recurrence end date from the form; copied
            verbatim onto the rule's ``end_date``.
        redirect: Where to redirect on a recoverable validation failure
            (invalid pattern id, or -- for the builder -- an invalid
            every-N-periods start period).
        include_due_day_of_month: ``True`` for transaction templates,
            ``False`` for transfer templates.  Transfer-template schemas
            do not expose ``due_day_of_month``; passing ``True`` for a
            transfer payload would silently set the column from a key
            the schema never validated.
    """

    end_date_value: date | None
    redirect: RedirectTarget
    include_due_day_of_month: bool = False


def build_recurrence_rule_from_form(
    data: dict[str, Any],
    *,
    user_id: int,
    start_period_id: int | None,
    ctx: RecurrenceFormContext,
) -> RecurrenceRule | Response | None:
    """Build a :class:`RecurrenceRule` from a validated form payload.

    Pops every recurrence-related key from ``data`` so the caller's
    downstream ``TransactionTemplate`` / ``TransferTemplate``
    constructor does not receive stray kwargs, then authors the rule
    through :func:`app.services.recurrence.author_rule` -- the one door
    that resolves both of the table's cadence vocabularies together.
    This helper's own job is what only a ROUTE can do: read the form and
    owner-check the submitted start period.

    Args:
        data: Marshmallow-validated payload; mutated in place.  The
            helper pops ``recurrence_pattern``, ``interval_n``,
            ``offset_periods``, ``day_of_month``, ``month_of_year``,
            ``end_date``, and -- when ``ctx.include_due_day_of_month``
            is ``True`` -- ``due_day_of_month``.
        user_id: Owner of the resulting :class:`RecurrenceRule` row.
        start_period_id: From the form; the rule's "First paycheck".
            Caller pops it before calling the helper because the same
            value is later persisted on the :class:`RecurrenceRule`.
        ctx: The :class:`RecurrenceFormContext` carrying the form's
            ``end_date_value`` (copied verbatim onto the rule), the
            validation-error ``redirect`` target (invalid pattern id or
            invalid every-N-periods start period), and the
            ``include_due_day_of_month`` transaction-vs-transfer flag.

    Returns:
        * :class:`RecurrenceRule` -- newly added, flushed, ready to
          link.  The caller is responsible for setting any owning-row
          FK (e.g. ``template.recurrence_rule_id = rule.id``).
        * ``None`` -- no recurrence pattern was selected; the helper
          still popped every recurrence key from ``data``.
        * :class:`Response` -- a Flask redirect to ``ctx.redirect``; the
          caller returns it directly so the route's control flow matches
          the pre-extraction shape.
    """
    pattern_id_str = data.pop("recurrence_pattern", None)

    if not pattern_id_str:
        # No pattern: drop every recurrence-related key so the caller's
        # model constructor does not receive stray kwargs.
        for key in _BASE_RECURRENCE_KEYS:
            data.pop(key, None)
        if ctx.include_due_day_of_month:
            data.pop(_DUE_DAY_KEY, None)
        return None

    pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
    if pattern is None:
        flash("Invalid recurrence pattern.", "danger")
        return ctx.redirect.to_response()

    interval_n = data.pop("interval_n", 1)
    offset_periods = data.pop("offset_periods", 0)
    # Pop ``end_date`` from data even though the value comes from
    # ``ctx.end_date_value`` -- keeps the "all recurrence keys removed
    # from data" contract symmetric between the pattern and
    # no-pattern branches, so the caller's downstream model
    # constructor never receives ``end_date`` as a stray kwarg.
    data.pop("end_date", None)
    day_of_month = data.pop("day_of_month", None)
    month_of_year = data.pop("month_of_year", None)
    due_day_of_month = (
        data.pop(_DUE_DAY_KEY, None) if ctx.include_due_day_of_month else None
    )

    # Verify ownership of any submitted start period BEFORE it is
    # persisted onto the rule -- for every pattern, not just
    # EVERY_N_PERIODS.  ``start_period_id`` is written onto the rule
    # unconditionally below (``rule_kwargs["start_period_id"]``), and
    # ``recurrence_engine`` later dereferences ``rule.start_period.start_date``
    # as the generation boundary -- and ``resolve`` reads it as the rule's
    # opening bound -- so a cross-user period would both be stored as a
    # foreign FK and shift this owner's generation timing.
    # This matches the read-only preview path
    # (``templates.preview_recurrence``), which already owner-gates the
    # start period for all patterns; without this probe the persist path
    # was an IDOR the preview path was not (deep-quality-hunt #21).
    start_period = None
    if start_period_id is not None:
        start_period = db.session.get(PayPeriod, start_period_id)
        if start_period is None or start_period.user_id != user_id:
            flash("Invalid start period.", "danger")
            return ctx.redirect.to_response()

    # The offset auto-derivation this branch used to run inline -- "for
    # EVERY_N_PERIODS, phase the rule on the chosen start period" -- moved
    # into ``resolve``, which applies it on EVERY write rather than only on
    # create.  That is what closes defect D1: the update path had no such
    # derivation and wrote the schema default instead, re-phasing every
    # future occurrence on an amount-only edit.
    return author_rule(
        RecurrenceSpec(
            user_id=user_id,
            pattern_id=pattern.id,
            interval_n=interval_n,
            offset_periods=offset_periods,
            day_of_month=day_of_month,
            due_day_of_month=due_day_of_month,
            month_of_year=month_of_year,
            start_period_id=start_period_id,
            end_date=ctx.end_date_value,
        ),
        calendar_for(user_id),
    )


def update_recurrence_rule_from_form(
    rule: RecurrenceRule,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
) -> Response | None:
    """Re-point an existing :class:`RecurrenceRule` from a form payload.

    Sibling of :func:`build_recurrence_rule_from_form` for the
    pattern-changed-on-an-existing-rule branch of the ``update_*``
    routes.  When a template already owns a rule, the edit mutates
    that same row in place -- preserving its primary key and the
    template's ``recurrence_rule_id`` FK -- rather than creating a
    new rule, then pops every recurrence key from ``data`` so the
    caller's downstream ``setattr`` loop never sees a stray kwarg.

    **It re-authors rather than assigns, and the difference is two
    closed defects.**  The pre-seam version wrote the payload onto the
    rule field by field, so what the form did not collect was written
    with the schema's DEFAULT rather than left alone -- that is defect
    **D1**: ``offset_periods`` went to 0 on an amount-only edit,
    re-phasing every future occurrence of an ``Every N Periods`` rule by
    one pay period.  Reading the rule's authored state back
    (:func:`app.services.recurrence.recurrence_spec`), replacing only the
    fields this form owns, and writing the whole value means the rule's
    start period still phases it and nothing the form does not collect is
    reset to a schema default.  ``interval_n`` needs no pattern-scoping
    for a related reason -- see the inline comment on the call.

    Args:
        rule: The existing :class:`RecurrenceRule` to mutate in place.
            The caller guarantees it is non-``None`` (the branch guard
            tests ``template.recurrence_rule``).
        data: Marshmallow-validated payload; mutated in place.  Pops
            ``recurrence_pattern``, ``interval_n``, ``offset_periods``,
            ``day_of_month``, ``month_of_year``, and -- when
            ``ctx.include_due_day_of_month`` is ``True`` --
            ``due_day_of_month``.
        ctx: The :class:`RecurrenceFormContext` carrying the form's
            ``end_date_value`` (copied verbatim onto ``rule.end_date``),
            the invalid-pattern ``redirect`` target, and the
            ``include_due_day_of_month`` transaction-vs-transfer flag.

    Returns:
        * ``None`` -- the rule was re-pointed successfully; the caller
          continues to the field-update loop.
        * :class:`Response` -- a Flask redirect emitted when the
          submitted ``recurrence_pattern`` id does not resolve to a
          :class:`RecurrencePattern`; the caller returns it directly
          so the route's control flow matches the pre-extraction shape.
    """
    pattern_id_str = data.pop("recurrence_pattern")
    pattern = db.session.get(RecurrencePattern, int(pattern_id_str))
    if pattern is None:
        flash("Invalid recurrence pattern.", "danger")
        return ctx.redirect.to_response()

    # The form's every-recurrence-key pops happen unconditionally, so the
    # caller's downstream ``setattr`` loop never sees a stray kwarg whichever
    # pattern was chosen.
    submitted_interval = data.pop("interval_n", 1)
    day_of_month = data.pop("day_of_month", None)
    month_of_year = data.pop("month_of_year", None)
    # The submitted phase is passed through, and resolution IGNORES it for any
    # rule that names a start period -- deriving the phase from that period
    # instead.  That is defect D1's fix, and it is scoped exactly where D1
    # bites: no template renders an offset input, so this value is always the
    # schema's default 0, which the pre-seam path wrote unconditionally and
    # thereby re-phased every future occurrence of an ``Every N Periods`` rule
    # on an amount-only edit.  A rule with NO start period has nothing to
    # derive from, so the payload remains its only statement of phase -- and
    # it cannot carry a stale non-zero one, because a period that is a rule's
    # anchor is HARD-LOCKED against deletion
    # (``pay_period_admin.PeriodLockReason.RECURRENCE_ANCHOR``).
    submitted_offset = data.pop("offset_periods", 0)

    # The rule's CURRENT authored state, with the form's fields replaced.
    # Everything the form does not collect -- ``start_period_id`` (fixed at
    # creation), ``start_date`` (the loan's origination bound),
    # ``max_occurrences`` -- rides through untouched, so this edit cannot
    # reset a field it never showed the user.
    #
    # ``interval_n`` needs no pattern-scoping here, and that is structural
    # rather than a tidier spelling of the old guard.  This form's interval
    # input is hidden for every pattern but EVERY_N_PERIODS and a hidden input
    # still SUBMITS, so the submitted value lands on a Quarterly rule's column
    # -- where it means nothing and nobody reads it.  ``interval_n`` carries
    # one meaning only, "repeat every N pay PERIODS", consulted by
    # ``match_periods`` in its EVERY_N_PERIODS branch, by
    # ``savings_goal_service.amount_to_monthly`` under the same condition, and
    # by ``_recurrence_macros.html`` inside the same branch.  The interval of
    # a MONTH- or YEAR-unit recurrence is a different fact, derived from the
    # pattern by ``resolve`` and stored nowhere (plan step R2d), so no value
    # this form can submit is able to say a Quarterly bill recurs monthly.
    # That is what makes the pattern-scoped guard unnecessary rather than
    # merely relocated, and it closes the reverse case the guard left open:
    # switching an every-4-paychecks rule to Quarterly used to make it read as
    # "every 4 months".
    current = recurrence_spec(rule)
    reauthor_rule(
        rule,
        replace(
            current,
            pattern_id=pattern.id,
            interval_n=submitted_interval,
            offset_periods=submitted_offset,
            day_of_month=day_of_month,
            due_day_of_month=(
                data.pop("due_day_of_month", None)
                if ctx.include_due_day_of_month
                else current.due_day_of_month
            ),
            month_of_year=month_of_year,
            end_date=ctx.end_date_value,
        ),
        calendar_for(rule.user_id),
    )
    return None


def resolve_recurrence_rule_for_update(
    template: Any,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
) -> Response | None:
    """Re-point or rebuild a template's recurrence rule for an update.

    Dispatches the two update-form branches shared by
    :func:`app.routes.templates.update_template` and
    :func:`app.routes.transfers.templates.update_transfer_template`:

    * pattern present AND the template already owns a rule -> re-point
      that row in place via :func:`update_recurrence_rule_from_form`
      (its primary key and the template's ``recurrence_rule_id`` FK
      stay stable);
    * otherwise -> build a fresh rule via
      :func:`build_recurrence_rule_from_form` (or ``None`` when no
      pattern was selected) and link it onto
      ``template.recurrence_rule_id``.

    The owning row's user scope comes from ``template.user_id`` -- the
    caller fetched the template through an owner-scoped ``get_or_404``,
    so this equals the pre-extraction ``current_user.id``.
    ``start_period_id`` is fixed at create time, so the builder is
    invoked with ``start_period_id=None`` and never performs the
    ``EVERY_N_PERIODS`` start-period owner re-check.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate``
            being updated.  Accessed for ``recurrence_rule``,
            ``recurrence_rule_id`` (assigned when a new rule is built),
            and ``user_id``.  Mutated in place.
        data: Marshmallow-validated payload; the recurrence keys are
            popped by the delegated helper.
        ctx: The :class:`RecurrenceFormContext` forwarded unchanged to
            the delegated builder / updater (its ``end_date_value``,
            ``redirect`` target, and ``include_due_day_of_month`` flag).

    Returns:
        * ``None`` -- the rule was resolved; the caller continues to
          the field-update loop.
        * :class:`Response` -- a Flask redirect for an invalid
          recurrence pattern id; the caller returns it directly.
    """
    if data.get("recurrence_pattern") and template.recurrence_rule:
        return update_recurrence_rule_from_form(
            template.recurrence_rule,
            data,
            ctx=ctx,
        )

    rule_or_redirect = build_recurrence_rule_from_form(
        data,
        user_id=template.user_id,
        start_period_id=None,
        ctx=ctx,
    )
    if isinstance(rule_or_redirect, Response):
        return rule_or_redirect
    if rule_or_redirect is not None:
        template.recurrence_rule_id = rule_or_redirect.id
    return None


def handle_stale_form_conflict(
    ctx: StaleConflictContext,
    *,
    submitted: int,
    current: int,
) -> Response:
    """Optimistic-locking pre-flush form-side conflict handler (F-26).

    Mirror of :func:`app.routes._commit_helpers.handle_stale_conflict`
    for the ``submitted_version != template.version_id`` branch that
    fires before the commit attempt.  Logs both the submitted and
    current counters so post-mortem analysis can reconstruct the race
    (matching the byte-identical pre-extraction log messages on both
    the templates and transfers update routes); flashes the
    context-supplied message; redirects.  Does NOT roll back the
    session because no DB write has been attempted yet at the
    call site.

    Args:
        ctx: The :class:`~app.routes._commit_helpers.StaleConflictContext`
            shared with the commit-time handler -- its ``logger``
            (records originate at the route module so log grep by
            ``logger=app.routes.templates`` keeps working), ``log_label``
            / ``log_id`` for the log line, ``flash_message`` (callers
            compose it via :data:`STALE_EDITING_MESSAGE` substituting the
            route's domain noun), and ``redirect`` target (typically the
            edit form so the user can re-load).
        submitted: Version counter the form payload carried.
        current: Version counter on the row right now.  The two
            differ exactly when a concurrent edit has landed.

    Returns:
        A Flask redirect :class:`Response`.  The caller returns it
        directly so the route's control flow is identical to the
        pre-extraction shape.
    """
    ctx.logger.info(
        "Stale-form conflict on %s id=%d "
        "(submitted=%d, current=%d)",
        ctx.log_label, ctx.log_id, submitted, current,
    )
    flash(ctx.flash_message, "warning")
    return ctx.redirect.to_response()


# --- Recurrence-conflict chooser (Loop B, P3) --------------------------
#
# When a template edit's regeneration collides with hand-edited upcoming
# instances, the update route shows a full-page chooser: keep each override
# or move it to the template's new value.  These helpers are shared by the
# transaction-template and transfer-template update routes; each passes its
# own model, amount attribute, and ``resolve_conflicts`` callable, so the
# flow stays DRY across the two kinds.

_CONFLICT_APPLY_MARKER = "conflict_apply"
_CONFLICT_DECISION_PREFIX = "conflict_decision_"
_DECISION_KEEP = "keep"
_DECISION_USE = "use"


@dataclass(frozen=True)
class ConflictChoice:
    """One conflicted upcoming instance, shaped for a chooser row.

    Attributes:
        row_id: The Transaction / Transfer id the decision applies to.
        due_date: The instance's due date (``None`` if unset), used to
            order and label the row chronologically.
        period_label: The owning pay period's ``label`` (e.g.
            ``"02/21 - 03/06"``).
        your_amount: The instance's current (hand-edited) amount -- the
            "Keep" side of the toggle.
        is_deleted_conflict: ``True`` when the conflict is a soft-deleted
            instance (Keep leaves it deleted; Use restores it), ``False``
            for an overridden one.
    """

    row_id: int
    due_date: date | None
    period_label: str
    your_amount: Decimal
    is_deleted_conflict: bool


def parse_conflict_decisions(form) -> dict[int, str] | None:
    """Parse the chooser's per-instance keep/use decisions from a POST form.

    Returns ``None`` for a first-time edit submit (no chooser marker), so
    the route knows to render the chooser; returns a ``{row_id: "keep" |
    "use"}`` map for the chooser's Apply submit.  Malformed ids or values
    are dropped -- every surviving id is re-checked against the real
    conflict set in :func:`apply_conflict_decisions` before any mutation,
    so a hand-crafted id cannot reach a row.

    Args:
        form: The request form (a ``MultiDict``).

    Returns:
        ``None`` when the form carries no chooser marker; otherwise the
        decision map (possibly empty).
    """
    if form.get(_CONFLICT_APPLY_MARKER) is None:
        return None
    decisions: dict[int, str] = {}
    for key in form:
        if not key.startswith(_CONFLICT_DECISION_PREFIX):
            continue
        value = form.get(key)
        if value not in (_DECISION_KEEP, _DECISION_USE):
            continue
        # The shared rule rather than a fourth local ``int()`` (plan step
        # X-ae): this one never crashed, but it read ``decision_١٠٦`` as row
        # 106 -- a spelling the chooser's own template cannot emit.
        row_id = parse_row_id(key[len(_CONFLICT_DECISION_PREFIX):])
        if row_id is None:
            continue
        decisions[row_id] = value
    return decisions


def _build_conflict_choices(conflict, model, amount_attr) -> list[ConflictChoice]:
    """Load the conflicted rows and shape them for the chooser.

    ``conflict.overridden`` / ``conflict.deleted`` are ids of ``model`` (a
    Transaction or Transfer); ``amount_attr`` names the row's amount column
    (``"estimated_amount"`` for transactions, ``"amount"`` for transfers).
    Rows are returned chronologically (undated last) so the chooser reads
    top-to-bottom in time order.  A vanished id (deleted between the raise
    and this load) is skipped.
    """
    choices = []
    for ids, is_deleted_conflict in (
        (conflict.overridden, False),
        (conflict.deleted, True),
    ):
        for row_id in ids:
            row = db.session.get(model, row_id)
            if row is None:
                continue
            period = row.pay_period
            choices.append(ConflictChoice(
                row_id=row_id,
                due_date=row.due_date,
                period_label=period.label if period else "",
                your_amount=getattr(row, amount_attr),
                is_deleted_conflict=is_deleted_conflict,
            ))
    choices.sort(key=lambda choice: (choice.due_date is None, choice.due_date or date.min))
    return choices


@dataclass(frozen=True)
class RecurrenceConflictKind:
    """Per-kind config for the recurring-definition edit + conflict flow.

    The transaction-template and transfer-template update routes differ
    only in the row model, its amount column, and the engine functions /
    endpoint that regenerate, resolve, and re-edit it; bundling those five
    lets :func:`regenerate_or_conflict_chooser` and the chooser helpers stay
    one shared, kind-agnostic implementation.

    Attributes:
        model: The instance row model (Transaction / Transfer) whose ids the
            conflict carries.
        amount_attr: The row's amount column name (``"estimated_amount"`` /
            ``"amount"``).
        regenerate_fn: The kind's ``regenerate_for_template(template,
            periods, scenario_id, effective_from=...)`` callable.
        resolve_fn: The kind's ``resolve_conflicts(ids, action, user_id,
            new_amount=...)`` callable.
        update_endpoint: The kind's update-route endpoint, resolved with the
            template id for the chooser's Apply action.
    """

    model: Any
    amount_attr: str
    regenerate_fn: Any
    resolve_fn: Any
    update_endpoint: str


@dataclass(frozen=True)
class ConflictChooserContext:
    """Everything the recurrence-conflict chooser page renders from.

    Bundled because :func:`render_recurrence_conflict_chooser` is a public
    route helper whose inputs are one cohesive concept: the pending edit,
    its kind, and where Apply / Cancel go.

    Attributes:
        conflict: The caught :class:`RecurrenceConflict` (the conflicted
            row ids).
        kind: The row model / amount / resolver bundle
            (:class:`RecurrenceConflictKind`).
        template_name: The edited template's new name (framing sentence).
        new_amount: The template's new amount (the "Use" figure).
        effective_from: The edit's effective date (framing sentence).
        action_url: Where Apply posts (the same update endpoint).
        cancel_url: Where Cancel returns (the list), abandoning the edit.
    """

    conflict: RecurrenceConflict
    kind: RecurrenceConflictKind
    template_name: str
    new_amount: Decimal
    effective_from: date
    action_url: str
    cancel_url: str


def render_recurrence_conflict_chooser(ctx: ConflictChooserContext, form) -> str:
    """Render the full-page conflict chooser for a pending template edit.

    Loads the conflicted instances into chooser rows and echoes the
    submitted edit ``form`` as hidden inputs (minus the CSRF token, which
    the chooser re-issues) so Apply re-runs the identical edit before
    resolving.  Renders and returns HTML only -- no mutation and no commit
    happen here; the caller rolls back the pending edit after this returns.

    Args:
        ctx: The pending-edit conflict context (see
            :class:`ConflictChooserContext`).
        form: The submitted edit form, echoed so Apply reproduces it.

    Returns:
        The rendered chooser page HTML.
    """
    echo = form.to_dict(flat=True)
    echo.pop("csrf_token", None)
    return render_template(
        "recurrence_conflict_chooser.html",
        choices=_build_conflict_choices(ctx.conflict, ctx.kind.model, ctx.kind.amount_attr),
        template_name=ctx.template_name,
        new_amount=ctx.new_amount,
        effective_from=ctx.effective_from,
        echo=echo,
        action_url=ctx.action_url,
        cancel_url=ctx.cancel_url,
        apply_marker=_CONFLICT_APPLY_MARKER,
        decision_prefix=_CONFLICT_DECISION_PREFIX,
        decision_keep=_DECISION_KEEP,
        decision_use=_DECISION_USE,
    )


def apply_conflict_decisions(
    *,
    kind: RecurrenceConflictKind,
    conflict: RecurrenceConflict,
    decisions: dict[int, str],
    new_amount: Decimal,
    user_id: int,
) -> None:
    """Apply the chooser's per-instance keep/use decisions.

    Only ids genuinely in the raised conflict set (``conflict.overridden``
    + ``conflict.deleted``) are acted on; a submitted id outside that set
    is ignored, so the chooser can never mutate an arbitrary owned row.
    "use" ids are realigned to ``new_amount`` (clearing the override /
    soft-delete) through ``kind.resolve_fn(..., "update", ...)``; "keep"
    ids are recorded through ``kind.resolve_fn(..., "keep", ...)`` for the
    audit trail (the regeneration already left them untouched).
    ``kind.resolve_fn`` ownership-checks every id and, on the transaction
    side, refuses transfer shadows.

    Args:
        kind: The row model / amount / resolver bundle; only
            ``kind.resolve_fn`` is used here.
        conflict: The caught :class:`RecurrenceConflict` (the id allow-list).
        decisions: The ``{row_id: "keep" | "use"}`` map from
            :func:`parse_conflict_decisions`.
        new_amount: The template's new amount applied to "use" ids.
        user_id: The requesting user's id (passed through for the ownership
            checks inside ``kind.resolve_fn``).
    """
    allowed = set(conflict.overridden) | set(conflict.deleted)
    use_ids = [
        rid for rid, choice in decisions.items()
        if choice == _DECISION_USE and rid in allowed
    ]
    keep_ids = [
        rid for rid, choice in decisions.items()
        if choice == _DECISION_KEEP and rid in allowed
    ]
    kind.resolve_fn(use_ids, "update", user_id, new_amount=new_amount)
    kind.resolve_fn(keep_ids, "keep", user_id)


# The Recurring surface is the single list both kinds cancel back to.
_RECURRING_LIST_ENDPOINT = "templates.list_templates"


def regenerate_or_conflict_chooser(
    template, old_amount, effective_from, kind, amount_drives_instances,
):
    """Regenerate a template's future rows, diverting to the conflict chooser.

    Shared by the transaction-template and transfer-template update routes
    (each passes its own :class:`RecurrenceConflictKind`).  Loads the
    baseline scenario and pay periods, then regenerates the non-overridden
    future instances via ``kind.regenerate_fn``.  When the edit collides with
    hand-edited (override / soft-deleted) upcoming instances the regeneration
    raises; the branch then depends on the submit and on whether this edit is
    a real per-instance AMOUNT change (the chooser only offers a keep-vs-use
    AMOUNT decision):

      * Apply (chooser decisions present): resolve each conflicted instance
        per the user's keep/use choice, then return ``None`` so the caller
        commits the edit together with the resolutions.
      * First submit of an amount-changing edit (``amount_drives_instances``
        and ``default_amount`` differs from ``old_amount``): render the
        chooser, ROLL BACK the pending edit (nothing is persisted), and
        return the chooser :class:`~flask.Response` for the caller to return.
      * Any other conflicting edit -- a rename / rule / flag change, or a
        salary-linked template whose ``default_amount`` does not drive its
        instance amounts -- leaves the overrides as the regeneration
        preserved them and returns ``None`` so the caller commits.  This
        keep-silently branch is deliberate: nothing the user can see changed
        for those instances, so no prompt and no flash (the service still
        logs the override / delete counts for forensics).

    Args:
        template: The edited template (its field updates already applied).
        old_amount: The template's amount BEFORE this edit; the chooser is
            offered only when ``template.default_amount`` now differs.
        effective_from: The edit's effective date.
        kind: The per-kind config (:class:`RecurrenceConflictKind`).
        amount_drives_instances: Whether ``default_amount`` actually drives
            this template's generated instance amounts.  ``False`` for a
            salary-linked template (paycheck-calculated per period), which
            suppresses the chooser so a vestigial ``default_amount`` edit
            never mis-states a paycheck.  Transfers always pass ``True``.

    Returns:
        The chooser response to short-circuit to, or ``None`` to proceed to
        commit (no recurrence rule / scenario, no conflict, a non-amount edit,
        or a conflict already resolved from Apply).
    """
    scenario = get_baseline_scenario(current_user.id)
    if scenario is None or template.recurrence_rule is None:
        return None
    periods = pay_period_service.get_all_periods(current_user.id)
    decisions = parse_conflict_decisions(request.form)
    try:
        kind.regenerate_fn(
            template, periods, scenario.id, effective_from=effective_from,
        )
    except RecurrenceConflict as conflict:
        if decisions is not None:
            apply_conflict_decisions(
                kind=kind,
                conflict=conflict,
                decisions=decisions,
                new_amount=template.default_amount,
                user_id=current_user.id,
            )
        elif amount_drives_instances and template.default_amount != old_amount:
            chooser = render_recurrence_conflict_chooser(
                ConflictChooserContext(
                    conflict=conflict,
                    kind=kind,
                    template_name=template.name,
                    new_amount=template.default_amount,
                    effective_from=effective_from,
                    action_url=url_for(
                        kind.update_endpoint, template_id=template.id,
                    ),
                    cancel_url=url_for(_RECURRING_LIST_ENDPOINT),
                ),
                request.form,
            )
            db.session.rollback()
            return chooser
    return None


__all__ = [
    "STALE_EDITING_MESSAGE",
    "STALE_ACTION_MESSAGE",
    "RecurrenceFormContext",
    "build_recurrence_rule_from_form",
    "update_recurrence_rule_from_form",
    "resolve_recurrence_rule_for_update",
    "handle_stale_form_conflict",
    "ConflictChoice",
    "RecurrenceConflictKind",
    "ConflictChooserContext",
    "parse_conflict_decisions",
    "render_recurrence_conflict_chooser",
    "apply_conflict_decisions",
    "regenerate_or_conflict_chooser",
]
