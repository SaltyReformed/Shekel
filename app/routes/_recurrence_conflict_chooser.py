"""
Shekel Budget App -- Recurring-definition regeneration and its conflict chooser
(Loop B, P3)

The second half of a template edit.  :mod:`app.routes._recurrence_form_helpers`
resolves what the template's recurrence now IS; this module re-drives the rows
that recurrence already generated, and mediates the one case where doing so
would overwrite something the user changed by hand.

The flow, shared verbatim by the transaction-template and transfer-template
update routes (each supplying its own :class:`RecurrenceConflictKind`):

* :func:`regenerate_or_conflict_chooser` -- the entry point.  Regenerates the
  non-overridden future instances, and on a collision decides between applying
  the user's chooser decisions, rendering the chooser, or committing silently.
* :func:`render_recurrence_conflict_chooser` -- renders the full-page chooser,
  echoing the submitted edit as hidden inputs so Apply re-runs it.
* :func:`parse_conflict_decisions` / :func:`apply_conflict_decisions` -- read
  the chooser's per-instance keep/use decisions back and act on them.

Split out of ``_recurrence_form_helpers`` when that module reached the
1,000-line cap (plan step R2e-1).  The seam is the one that module's own
docstring already drew: resolving a rule from a form payload and regenerating a
template's instances are different jobs with different collaborators, sharing
only the two routes that call both.

Route-layer module (leading underscore = route-internal) rather than a service
because it consumes Flask ``flash`` / ``render_template`` / ``request`` /
``url_for``; ``CLAUDE.md::Architecture`` keeps services isolated from Flask
globals.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from flask import Response, flash, render_template, request, url_for
from flask_login import current_user

from app.exceptions import RecurrenceConflict
from app.extensions import db
from app.services.generation_schedule import GenerationSchedule
from app.services.pay_calendar import calendar_for
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.digit_strings import parse_row_id


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


def flash_retained_notice(conflict) -> None:
    """Tell the owner which rows the pass left alone, and why.

    ``RecurrenceConflict.retained`` names the rows a regeneration declined to
    touch because the owner has records against them -- purchases, a note, or a
    hand-entered actual -- and applying the definition change would have
    destroyed or re-attributed those (plan step R10-a, finding **N-292**).

    **It is a notice, not a prompt, and that asymmetry is deliberate.**  The
    other two conflict kinds ask a question because either answer is a
    reasonable outcome; here the pass has already taken the only safe one and
    the row is untouched.  What the owner cannot be left without is KNOWING,
    since the alternative is a rename that quietly does less than it says.
    Acting on such a row is ordinary grid work -- move the purchases, or remove
    the row -- and offering an "apply anyway" button here would be building a
    destructive action nobody asked for.

    Args:
        conflict: The caught :class:`~app.exceptions.RecurrenceConflict`.
    """
    if not conflict.retained:
        return
    count = len(conflict.retained)
    noun = "instance" if count == 1 else "instances"
    flash(
        f"{count} upcoming {noun} kept the value it already had, because you "
        f"have purchases or notes recorded against it. Nothing was changed or "
        f"removed there. Open the instance to move or clear those records if "
        f"you want the new setting applied to it.",
        "warning",
    )


def _build_conflict_choices(conflict, model, resolve_amount) -> list[ConflictChoice]:
    """Load the conflicted rows and shape them for the chooser.

    ``conflict.overridden`` / ``conflict.deleted`` are ids of ``model`` (a
    Transaction or Transfer).
    Rows are returned chronologically (undated last) so the chooser reads
    top-to-bottom in time order.  A vanished id (deleted between the raise
    and this load) is skipped.

    **The "Keep $X" figure is RESOLVED, not read off the amount column** (plan
    step X-au-c2b, added when an adversarial review found this reader).  It read
    ``getattr(row, amount_attr)`` -- the very column a derived row does not
    carry -- and rendered it through ``money()`` on a live screen, so the
    chooser would have offered "Keep $" with nothing after it at the first
    per-kind cutover.  The two kinds resolve through their own rule
    (:func:`~app.services.cash_ledger.resolve_transaction_amount` for a
    transaction, :func:`~app.services.cash_ledger.resolve_transfer_amount` for a
    transfer), which is why ``amount_attr`` is gone: the column NAME was only
    ever a way to spell "ask this kind what it is worth", and the amount model
    answers that question per kind already.
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
                your_amount=resolve_amount(row),
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
        resolve_amount: The kind's amount rule -- a one-argument callable
            answering what one row's amount RESOLVES to.  It was the row's
            amount COLUMN NAME until plan step X-au-c2b; a derived row does not
            carry that column, and the chooser renders the figure as money.
        regenerate_fn: The kind's ``regenerate_for_template(template,
            schedule, scenario_id, effective_from=...)`` callable, where
            ``schedule`` is a
            :class:`~app.services.generation_schedule.GenerationSchedule`.
            Both engines' functions are stored here rather than called by
            name, so their shared signature moves in one commit or not at
            all.
        resolve_fn: The kind's ``resolve_conflicts(ids, action, user_id,
            new_amount=...)`` callable.
        update_endpoint: The kind's update-route endpoint, resolved with the
            template id for the chooser's Apply action.
    """

    model: Any
    resolve_amount: object
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
        choices=_build_conflict_choices(
            ctx.conflict, ctx.kind.model, ctx.kind.resolve_amount,
        ),
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


@dataclass(frozen=True)
class PreEditTemplateState:
    """What a template looked like BEFORE the edit now being applied.

    One cohesive concept -- the before-image an update route captures so the
    regeneration decision can ask what this edit CHANGED -- rather than a bag
    of flags.  Both fields are read only by
    :func:`regenerate_or_conflict_chooser`, and each answers a "did X move"
    question that the template's post-edit state alone cannot: by the time
    that function runs, the route's ``setattr`` loop and
    :func:`resolve_recurrence_rule_for_update` have already overwritten both.

    Bundled rather than passed as two arguments because the second one takes
    the function past pylint's five-argument threshold, and because the next
    such gate belongs here rather than as a seventh parameter.

    Attributes:
        amount: The template's ``default_amount`` before the edit.  The
            conflict chooser is offered only when the new amount differs --
            it presents a keep-vs-use AMOUNT decision, so an edit that did
            not move the amount has nothing to ask about.
        had_recurrence_rule: Whether the template named a recurrence rule
            before the edit.  Distinguishes "the user just cleared the
            recurrence" -- which MUST re-drive the generated instances -- from
            "this template never recurred", which must not: a RULE-LESS
            transfer template's single generated Transfer is an ordinary
            auto-generated row, so sweeping on a rename would silently delete
            it.
    """

    amount: Decimal
    had_recurrence_rule: bool


def regenerate_or_conflict_chooser(
    template: Any,
    before: PreEditTemplateState,
    effective_from: date,
    kind: RecurrenceConflictKind,
    amount_drives_instances: bool,
) -> Response | None:
    """Regenerate a template's future rows, diverting to the conflict chooser.

    Shared by the transaction-template and transfer-template update routes
    (each passes its own :class:`RecurrenceConflictKind`).  Loads the
    baseline scenario and the owner's whole pay-period schedule, then
    regenerates the non-overridden future instances via
    ``kind.regenerate_fn``.  When the edit collides with
    hand-edited (override / soft-deleted) upcoming instances the regeneration
    raises; the branch then depends on the submit and on whether this edit is
    a real per-instance AMOUNT change (the chooser only offers a keep-vs-use
    AMOUNT decision):

      * Apply (chooser decisions present): resolve each conflicted instance
        per the user's keep/use choice, then return ``None`` so the caller
        commits the edit together with the resolutions.
      * First submit of an amount-changing edit (``amount_drives_instances``,
        the template STILL recurs, and ``default_amount`` differs from
        ``before.amount``): render the chooser, ROLL BACK the pending edit
        (nothing is persisted), and return the chooser
        :class:`~flask.Response` for the caller to return.
      * Any other conflicting edit -- a rename / rule / flag change, or a
        salary-linked template whose ``default_amount`` does not drive its
        instance amounts -- leaves the overrides as the regeneration
        preserved them and returns ``None`` so the caller commits.  This
        keep-silently branch is deliberate: nothing the user can see changed
        for those instances, so no prompt and no flash (the service still
        logs the override / delete counts for forensics).

    **A CLEARED recurrence regenerates too**, and the guard that skipped it
    was the second half of a live defect (see
    :func:`resolve_recurrence_rule_for_update` for the first).  The gate used
    to be "the template has a rule NOW"; a user who set the pattern to
    "Does not repeat" therefore left every future instance the deleted rule
    had already generated sitting on the grid.  The gate is now "the template
    IS or WAS recurring", and on a cleared recurrence the regeneration deletes
    the untouched projected rows from ``effective_from`` forward and generates
    nothing, because there is no rule left to generate from.  Settled rows are
    immutable and overridden ones raise as conflicts exactly as they do for
    any other edit (``_recurrence_common.partition_regeneration_rows``), so
    clearing a recurrence destroys neither history nor a deliberate
    per-instance change.

    A template that neither has nor had a rule is still skipped, and the
    distinction is load-bearing: a RULE-LESS transfer template's single
    Transfer is an ordinary auto-generated row, so a rename would otherwise
    sweep it away.  **That gate is what closes defect D16.**  A transfer that
    does not repeat used to carry a ``Once`` RULE, so it reached the
    regeneration below: the sweep hard-deleted the row and the pattern's own
    suppression guard generated nothing back.  Measured on a rename, with the
    transfer in a future period: 1 transfer + 2 shadows -> 0 + 0.  Plan step
    R2e-3 retired the pattern, so such a template is rule-less and this gate
    covers it.

    **The chooser additionally requires that the template STILL recurs.**  It
    asks one question -- "should your hand-edited instances move to the new
    amount?" -- and that question presumes future instances will be
    regenerated AT that amount.  On a cleared recurrence none will be, so the
    prompt would offer an amount decision about rows that are being deleted:
    measured before this guard, a clear-plus-amount edit rendered "Your other
    upcoming instances move to $99.99" over five rows the same request then
    removed.  Without the prompt the edit takes the keep-silently branch, so
    the hand-edited rows survive untouched -- the conservative answer, and the
    one the user can still revise on the grid.

    **The sweep is BASELINE-SCOPED.**  Only the baseline scenario is
    regenerated (as for every other edit), so rows this template generated
    into a non-baseline scenario are left in place -- and once the rule is
    deleted no later edit can reach them, because there is no recurrence left
    to clear a second time.

    Args:
        template: The edited template (its field updates already applied).
        before: The template's pre-edit state
            (:class:`PreEditTemplateState`) -- its amount and whether it
            named a recurrence rule.  Both are already overwritten on
            *template* by the time this runs.
        effective_from: The edit's effective date.
        kind: The per-kind config (:class:`RecurrenceConflictKind`).
        amount_drives_instances: Whether ``default_amount`` actually drives
            this template's generated instance amounts.  ``False`` for a
            salary-linked template (paycheck-calculated per period), which
            suppresses the chooser so a vestigial ``default_amount`` edit
            never mis-states a paycheck.  Transfers always pass ``True``.

    Returns:
        The chooser response to short-circuit to, or ``None`` to proceed to
        commit (no scenario, a template that neither has nor had a recurrence
        rule, no conflict, a non-amount edit, an edit that cleared the
        recurrence, or a conflict already resolved from Apply).
    """
    scenario = get_baseline_scenario(current_user.id)
    if scenario is None:
        return None
    if template.recurrence_rule is None and not before.had_recurrence_rule:
        return None
    schedule = GenerationSchedule.for_calendar(calendar_for(current_user.id))
    decisions = parse_conflict_decisions(request.form)
    try:
        kind.regenerate_fn(
            template, schedule, scenario.id, effective_from=effective_from,
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
        elif (
            # **A conflict is not automatically a QUESTION** (plan step R10-a,
            # adversarial review).  The chooser asks one thing -- keep this
            # instance's amount or move it to the template's -- and
            # ``_build_conflict_choices`` builds its rows from ``overridden``
            # and ``deleted`` alone.  A RETAINED row has no such question: the
            # pass already left it untouched.  Without this arm a
            # retained-only conflict rendered the chooser over an EMPTY list
            # and then rolled the edit back below, so an ordinary amount
            # change silently did nothing and said "some upcoming instances
            # were hand-edited" over no instances.
            (conflict.overridden or conflict.deleted)
            and amount_drives_instances
            and template.recurrence_rule is not None
            and template.default_amount != before.amount
        ):
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
            # No retained notice on this path: the rollback above discards the
            # whole pending edit, so telling the owner what a pass "kept" would
            # describe a pass that no longer happened.  The notice fires on the
            # two paths that reach the caller's commit, below.
            return chooser
        flash_retained_notice(conflict)
    return None




__all__ = [
    "ConflictChoice",
    "ConflictChooserContext",
    "PreEditTemplateState",
    "RecurrenceConflictKind",
    "apply_conflict_decisions",
    "flash_retained_notice",
    "parse_conflict_decisions",
    "regenerate_or_conflict_chooser",
    "render_recurrence_conflict_chooser",
]
