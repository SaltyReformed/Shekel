"""
Shekel Budget App -- Recurrence-Form Route Helpers (F-24, F-26)

What a template's recurrence rule IS after a form submit, for the
transaction-template (:mod:`app.routes.templates`) and transfer-template
(:mod:`app.routes.transfers`) CRUD routes.  What happens to the ROWS that rule
already generated is the sibling module
:mod:`app.routes._recurrence_conflict_chooser`, split out at plan step R2e-1
when this one reached the 1,000-line cap.

The helpers:

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
* :func:`resolve_recurrence_rule_for_update` -- dispatches the three
  update-form branches (re-point an existing rule, build + link a new
  one, or CLEAR the recurrence the user set to "one-time / manual") so
  each ``update_*`` route resolves its recurrence rule with a single
  call.  [F-24; the clear branch is plan step R2e-1]
* :func:`handle_stale_form_conflict` -- pre-flush optimistic-locking
  guard for the ``submitted_version != template.version_id``
  branch; logs both counters so post-mortem analysis can reconstruct
  the race; redirects.  [F-26 pair 1]
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
import logging
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from flask import Response, flash

from app.extensions import db
from app.models.pay_period import PayPeriod
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import StaleConflictContext
from app.routes._redirect_target import RedirectTarget
from app.services.recurrence import (
    UNAVAILABLE_PATTERN_MESSAGE,
    PatternChoice,
    RecurrenceSpec,
    author_rule,
    calendar_for,
    modelled_pattern,
    pattern_choices_for,
    reauthor_rule,
    recurrence_spec,
)
from app.utils.log_events import (
    BUSINESS,
    EVT_RECURRENCE_RULE_NOT_EXCLUSIVE,
    log_event,
)

logger = logging.getLogger(__name__)


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


def edit_form_pattern_choices(template: Any) -> tuple[PatternChoice, ...]:
    """Return an EDIT form's pattern options, warning when the stored one is gone.

    The render-side counterpart of the write door below, and the reason both
    edit routes call one function rather than each assembling the picker: the
    stored pattern and the offered set can disagree, and a ``<select>`` answers
    that disagreement by SILENTLY picking its first option (see
    :func:`app.services.recurrence.pattern_choices_for`, which measured what
    that costs on each form).

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` being
            edited.  Read for ``recurrence_rule`` only; not mutated.

    Returns:
        The modelled choices, plus the stored pattern when the application no
        longer models it.
    """
    rule = template.recurrence_rule
    pattern_id = rule.pattern_id if rule is not None else None
    if pattern_id is not None and modelled_pattern(pattern_id) is None:
        flash(UNAVAILABLE_PATTERN_MESSAGE, "warning")
    return pattern_choices_for(pattern_id)


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

    **It does NOT validate the pattern id.**  That the id names a cadence the
    application MODELS -- narrower than "names a ``ref`` row", and the
    difference is a 500 -- is a property of the SUBMISSION, so it belongs to
    the submission's validator:
    :class:`~app.schemas.validation._helpers.RecurrencePatternField` refuses it
    before any route code runs (plan step R2e-2, developer ruling 2026-08-07).
    The check used to live here AND in
    :func:`update_recurrence_rule_from_form` -- one rule written twice, which
    a third caller would have had neither copy of.

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
            validation-error ``redirect`` target (an invalid start
            period), and the ``include_due_day_of_month``
            transaction-vs-transfer flag.

    Returns:
        * :class:`RecurrenceRule` -- newly added, flushed, ready to
          link.  The caller is responsible for setting any owning-row
          FK (e.g. ``template.recurrence_rule_id = rule.id``).
        * ``None`` -- no recurrence pattern was selected; the helper
          still popped every recurrence key from ``data``.
        * :class:`Response` -- a Flask redirect to ``ctx.redirect`` when the
          submitted start period is not this user's; the caller returns it
          directly so the route's control flow matches the pre-extraction
          shape.
    """
    pattern_id = data.pop("recurrence_pattern", None)

    if not pattern_id:
        # No pattern: drop every recurrence-related key so the caller's
        # model constructor does not receive stray kwargs.
        for key in _BASE_RECURRENCE_KEYS:
            data.pop(key, None)
        if ctx.include_due_day_of_month:
            data.pop(_DUE_DAY_KEY, None)
        return None

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
            pattern_id=pattern_id,
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
) -> None:
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
            ``end_date_value`` (copied verbatim onto ``rule.end_date``) and
            the ``include_due_day_of_month`` transaction-vs-transfer flag.
            Its ``redirect`` is unused here and kept only because the three
            helpers share one context object.

    Returns:
        ``None``.  **It cannot fail**, which is what plan step R2e-2 changed:
        the one failure it used to have -- an unmodelled ``recurrence_pattern``
        -- is refused by
        :class:`~app.schemas.validation._helpers.RecurrencePatternField` before
        the route reads the payload, so there is no redirect left to return and
        the signature says so.
    """
    pattern_id = data.pop("recurrence_pattern")

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
            pattern_id=pattern_id,
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


LOAN_PAYMENT_CANNOT_BE_ONE_TIME: str = (
    "A loan payment repeats for the life of the loan, so it cannot be made "
    "one-time. Choose a different pattern to change how often it repeats, or "
    "archive it to stop paying."
)
"""Refusal shown when an edit tries to clear a loan payment's recurrence."""


def _is_loan_payment(template: Any) -> bool:
    """Return whether *template* is a recurring loan payment.

    A :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row is
    present "only for recurring loan payments" (decision B), and it carries the
    standing ``extra_principal`` that
    ``recurring_transfer_query.loan_standing_extra`` threads into the balance
    seam's :class:`~app.services.balance_at._resolution.ResolvedLoan`.

    ``getattr`` because only ``TransferTemplate`` declares the relationship;
    these helpers are deliberately kind-agnostic, and a transaction template is
    never a loan payment.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate``.

    Returns:
        ``True`` when the template carries loan-payment settings.
    """
    return getattr(template, "settings", None) is not None


def _rule_is_exclusively_owned(rule: RecurrenceRule, template: Any) -> bool:
    """Return whether *rule* belongs to *template* and to nothing else.

    A recurrence rule is written only through
    :func:`app.services.recurrence.author_rule`, one fresh row per template,
    so 1:1 is the invariant -- 45 references over 45 distinct rules on the
    live clone.  It is not enforced by the schema, and which side should
    enforce it is finding **F-6**'s ruling to take.  Until then a DELETE must
    not act on the assumption: both template FKs are ``ON DELETE SET NULL``,
    so destroying a shared rule would strip a SECOND template's cadence with
    no error and no trace.

    Args:
        rule: The rule about to be deleted.
        template: The template clearing it.

    Returns:
        ``True`` when the rule is this owner's and no other template
        references it.
    """
    if rule.user_id != template.user_id:
        return False
    referencing = sum(
        db.session.query(model).filter(
            model.recurrence_rule_id == rule.id, model.id != template.id,
        ).count()
        for model in (TransactionTemplate, TransferTemplate)
    )
    return referencing == 0


def _clear_recurrence_rule(template: Any) -> None:
    """Detach and DELETE the template's recurrence rule.

    What "this no longer recurs" means on the write side: the template stops
    naming a rule AND the row it named ceases to exist.  Merely detaching
    would produce exactly the orphan finding **F-6** measures on the
    hard-delete path (5 such rows on production), from a second door.

    A rule that is NOT exclusively this template's is detached but kept, and
    the anomaly is logged rather than swallowed -- see
    :func:`_rule_is_exclusively_owned`.

    The FK is nulled and FLUSHED before the delete so the statement order of a
    destructive operation is legible here rather than being a property of the
    unit of work's dependency sort.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate`` whose
            recurrence is being cleared.  Mutated in place; a no-op when it
            names no rule.
    """
    rule = template.recurrence_rule
    if rule is None:
        return
    deletable = _rule_is_exclusively_owned(rule, template)
    template.recurrence_rule = None
    template.recurrence_rule_id = None
    db.session.flush()
    if deletable:
        db.session.delete(rule)
        return
    log_event(
        logger, logging.WARNING,
        EVT_RECURRENCE_RULE_NOT_EXCLUSIVE, BUSINESS,
        "Recurrence rule detached but not deleted -- not exclusively owned",
        user_id=template.user_id,
        template_id=template.id,
        recurrence_rule_id=rule.id,
    )


def resolve_recurrence_rule_for_update(
    template: Any,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
) -> Response | None:
    """Re-point, rebuild, or CLEAR a template's recurrence rule for an update.

    Dispatches the three update-form branches shared by
    :func:`app.routes.templates.update_template` and
    :func:`app.routes.transfers.templates.update_transfer_template`:

    * pattern present AND the template already owns a rule -> re-point
      that row in place via :func:`update_recurrence_rule_from_form`
      (its primary key and the template's ``recurrence_rule_id`` FK
      stay stable);
    * pattern present, no existing rule -> build a fresh rule via
      :func:`build_recurrence_rule_from_form` and link it onto
      ``template.recurrence_rule_id``;
    * pattern SUBMITTED AS EMPTY -> the user chose "one-time / manual", so any
      existing rule is cleared through :func:`_clear_recurrence_rule` --
      unless the template is a LOAN PAYMENT, which is refused (see
      :data:`LOAN_PAYMENT_CANNOT_BE_ONE_TIME`).

    **A submitted-empty pattern and an absent one are different requests**, and
    keeping them apart is what stops the third branch from breaking the
    partial-update contract.  Both schemas declare ``recurrence_pattern`` as
    ``allow_none``, so the form's "None (one-time / manual)" option survives
    ``_normalize_empty_inputs`` as a present ``None`` while a field the caller
    never submitted stays absent -- and only the first clears.  Without that
    distinction an amount-only PATCH, which submits no recurrence keys at all,
    would silently delete the template's cadence.

    **The third branch is new, and its absence was a live defect.**  The form
    has offered "None (one-time / manual)" since the recurring cluster
    shipped, and selecting it did nothing at all: the builder returned
    ``None``, this function assigned nothing, and the template kept both its
    rule and its cadence.  Worse than inert -- the caller then regenerated
    from the rule the user had just asked it to stop using.  Measured on a
    real edit of an every-paycheck template::

        rule_id before: 1   rows: 10
        rule_id after:  1   rows: 10
        (log) deleted_count=6  created_count=6

    **A LOAN PAYMENT is refused rather than cleared**, because clearing it
    produces a state the domain does not have: an amortizing loan still
    amortizes, so a payment that does not repeat leaves the loan with no
    cadence to project against.  It is not a cosmetic refusal -- measured, the
    clear silently re-dated a loan's payoff, because
    ``recurring_transfer_query.active_recurring_transfer_template`` finds a
    loan's payment by ``recurrence_rule_id IS NOT NULL`` and nulling that
    column drops the standing overpayment the balance seam threads::

        loan standing extra before: 250.00
        loan standing extra after:    0.00
        loan_payment_settings row still asserts: 250.00

    The template's own ``LoanPaymentSettings`` row would go on claiming an
    extra principal nothing reads.  The two real intents each have a door:
    change the cadence (pick another pattern) or stop paying (archive it).

    Whether the instances that rule already generated are swept is the
    CALLER's half of the same edit; see
    :func:`~app.routes._recurrence_conflict_chooser.regenerate_or_conflict_chooser`.

    The owning row's user scope comes from ``template.user_id`` -- the
    caller fetched the template through an owner-scoped ``get_or_404``,
    so this equals the pre-extraction ``current_user.id``.
    ``start_period_id`` is fixed at create time, so the builder is
    invoked with ``start_period_id=None`` and never performs the
    ``EVERY_N_PERIODS`` start-period owner re-check.

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate``
            being updated.  Accessed for ``recurrence_rule``,
            ``recurrence_rule_id`` (assigned when a new rule is built,
            cleared when none was selected), and ``user_id``.  Mutated in
            place.
        data: Marshmallow-validated payload; the recurrence keys are
            popped by the delegated helper.  Read for whether
            ``recurrence_pattern`` is PRESENT before that pop consumes it.
        ctx: The :class:`RecurrenceFormContext` forwarded unchanged to
            the delegated builder / updater (its ``end_date_value``,
            ``redirect`` target, and ``include_due_day_of_month`` flag).

    Returns:
        * ``None`` -- the rule was resolved; the caller continues to
          the field-update loop.
        * :class:`Response` -- a Flask redirect for an invalid
          recurrence pattern id; the caller returns it directly.
    """
    # Read BEFORE the delegated helper pops the key.
    recurrence_submitted = "recurrence_pattern" in data
    clearing = (
        recurrence_submitted
        and not data.get("recurrence_pattern")
        and template.recurrence_rule is not None
    )
    if clearing and _is_loan_payment(template):
        flash(LOAN_PAYMENT_CANNOT_BE_ONE_TIME, "danger")
        return ctx.redirect.to_response()

    if data.get("recurrence_pattern") and template.recurrence_rule:
        # Re-points the rule in place and cannot fail, so this branch has no
        # redirect to propagate -- it returns the same ``None`` the other two
        # branches do on success.
        update_recurrence_rule_from_form(
            template.recurrence_rule,
            data,
            ctx=ctx,
        )
        return None

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
    elif recurrence_submitted:
        _clear_recurrence_rule(template)
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


__all__ = [
    "LOAN_PAYMENT_CANNOT_BE_ONE_TIME",
    "STALE_EDITING_MESSAGE",
    "STALE_ACTION_MESSAGE",
    "RecurrenceFormContext",
    "build_recurrence_rule_from_form",
    "edit_form_pattern_choices",
    "update_recurrence_rule_from_form",
    "resolve_recurrence_rule_for_update",
    "handle_stale_form_conflict",
]
