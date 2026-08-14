"""
Shekel Budget App -- Recurrence-Form Route Helpers (F-24, F-26)

What a template's recurrence rule IS after a form submit, for the
transaction-template (:mod:`app.routes.templates`) and transfer-template
(:mod:`app.routes.transfers`) CRUD routes.  What happens to the ROWS that rule
already generated is the sibling module
:mod:`app.routes._recurrence_conflict_chooser`, split out at plan step R2e-1
when this one reached the 1,000-line cap.  What an EDIT form's controls START
ON is :mod:`app.routes._recurrence_form_render`, split out at plan step R7b-4
when it reached that cap a second time -- the seam being SUBMISSION versus
STORED STATE: everything here reads a posted payload and writes, everything
there answers a read-only question about a definition already saved.

The helpers:

* :func:`build_recurrence_rule_for_create` -- the whole create-form
  preamble both kinds run: lift the closing bound out of the payload, build
  the context, build the rule.  Hoisted at plan step R7b-4, which is when it
  became hoistable;
* :func:`build_recurrence_rule_from_form` -- consumes a Marshmallow-
  validated payload, pops the recurrence-related keys, and returns a
  fresh :class:`RecurrenceRule` (added to the session and flushed) or
  ``None`` when no cadence was selected.  [F-24]
* :func:`update_recurrence_rule_from_form` -- sibling of the builder
  for the cadence-changed-on-an-existing-rule branch: re-points the
  template's current :class:`RecurrenceRule` in place (preserving its
  id and the owning FK), pops the recurrence keys, and returns
  ``None``; it cannot fail.  [F-24]
* :func:`resolve_recurrence_rule_for_update` -- dispatches the three
  update-form branches (re-point an existing rule, build + link a new
  one, or CLEAR the recurrence the user set to "Does not repeat") so
  each ``update_*`` route resolves its recurrence rule with a single
  call.  [F-24; the clear branch is plan step R2e-1]
* :func:`handle_stale_form_conflict` -- pre-flush optimistic-locking
  guard for the ``submitted_version != template.version_id``
  branch; logs both counters so post-mortem analysis can reconstruct
  the race; redirects.  [F-26 pair 1]
The first three helpers share a verbatim trio of inputs -- the form's
closing bound, the validation-error redirect target, and the
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
from typing import Any

from flask import Response, flash

from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import StaleConflictContext
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import RECURRENCE_END_BOUND_KEY
from app.services.loan_recurrence_sync import owns_validity_window
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    NEVER_ENDS,
    EndBound,
    RecurrenceSpec,
    author_rule,
    modelled_pattern,
    reauthor_rule,
    recurrence_spec_with_cadence,
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
    "recurrence_placement",
    "interval_n",
    "day_of_month",
    "month_of_year",
)

# The closing bound's three controls (plan step R7b-3).
# ``recurrence_end_mode`` carries the COMPOSED bound after the schema's
# ``@post_load``, which also consumes the two value keys -- but ONLY when a
# mode was submitted.  A submission that named none (a loan payment's disabled
# control, an amount-only PATCH, a crafted POST, a page cached from before this
# deploy) leaves the two values in the payload, so they are dropped here.
#
# **Popped on every branch, from ``resolve_recurrence_rule_for_update``'s own
# entry**, and an adversarial review of this step is why: they were dropped in
# the builder alone, so the re-point branch carried ``end_date`` through to the
# caller's field loop and was saved only by that route's separate ``setattr``
# allowlist -- a second, unrelated guard standing in for a contract this
# module's own comment claimed to hold.
_END_BOUND_KEYS: tuple[str, ...] = (
    RECURRENCE_END_BOUND_KEY,
    "end_date",
    "max_occurrences",
)


def _pop_end_bound_keys(data: dict[str, Any]) -> None:
    """Drop the closing bound's three form keys from *data*.

    Args:
        data: The validated payload, mutated in place.
    """
    for key in _END_BOUND_KEYS:
        data.pop(key, None)

_DUE_DAY_KEY: str = "due_day_of_month"

#: The OPENING bound's one control (plan step R7b-4), named beside the closing
#: bound's three because the two are the rule's validity window and the
#: helpers treat them the same way: PRESENT replaces, ABSENT leaves alone.
#: One key rather than three because one column holds it -- the closing bound
#: needs a mode to discriminate three shapes over two columns, and "unbounded
#: or a date" is what a nullable date already says.
_START_DATE_KEY: str = "start_date"


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
    ``resolve`` forwards unchanged: the form's closing bound, the
    validation-error redirect target, and whether the submitting schema
    exposes ``due_day_of_month`` (transaction templates) or not
    (transfer templates).  Collapsing the formerly-triplicated
    ``end_bound`` / ``redirect_endpoint`` / ``redirect_endpoint_kwargs``
    / ``include_due_day_of_month`` signature tail into one object both
    removes the duplication and clears the per-helper
    ``too-many-arguments`` count.

    Attributes:
        end_bound: When the recurrence STOPS, as the ONE value the submission
            composed (:class:`~app.services.recurrence.EndBound`), or ``None``
            when the form STATED NOTHING about it.

            The two are different requests and the helpers act on them
            differently -- a stated bound REPLACES the rule's, an absent one
            leaves it alone -- which is the same present-versus-absent
            distinction ``recurrence_unit`` turns on, and it is what lets a
            form whose bound is derived (a loan payment) render the control
            disabled and have the save mean "not mine to state" rather than
            "ends never".  It carried the raw ``end_date`` until plan step
            R7b-3, where a date was the only bound a form could state and the
            distinction had nothing to express.
        redirect: Where to redirect on a recoverable validation failure
            (a start period that is not this user's).
        include_due_day_of_month: ``True`` for transaction templates,
            ``False`` for transfer templates.  Transfer-template schemas
            do not expose ``due_day_of_month``; passing ``True`` for a
            transfer payload would silently set the column from a key
            the schema never validated.
    """

    end_bound: EndBound | None
    redirect: RedirectTarget
    include_due_day_of_month: bool = False


def build_recurrence_rule_for_create(
    data: dict[str, Any],
    *,
    user_id: int,
    redirect: RedirectTarget,
    include_due_day_of_month: bool,
) -> RecurrenceRule | None:
    """Run the whole create-form recurrence preamble, for either kind.

    The one call ``templates.create_template`` and
    ``transfers.create_transfer_template`` each make.  It reads the closing
    bound out of the payload -- the schema's ``@post_load`` composed it into
    ONE value under the mode key, and the route has to lift it before the
    builder pops the rest -- assembles the context, and builds the rule.

    **It exists because plan step R7b-4 made hoisting possible, and the
    ``duplicate-code`` gate is what said so.**  The two routes ran a
    byte-identical preamble and carried a one-sided suppression whose stated
    reason was that it could NOT be hoisted: the transfers side reused
    ``start_period_id`` afterwards, for its one-time-transfer branch, so a
    wrapper popping it internally would have had to thread it back out.  That
    field is the transfer form's alone now -- its recurrence meaning became a
    date -- and it is read ABOVE this call there, which leaves the two
    preambles identical with nothing left to thread.  The suppression is
    deleted rather than moved.

    Args:
        data: Marshmallow-validated payload; mutated in place.  Every
            recurrence key is popped, including the closing bound's three.
        user_id: Owner of the resulting :class:`RecurrenceRule` row.
        redirect: Where a recoverable failure would send the user.  Carried
            into the context rather than read: neither this function nor
            :func:`build_recurrence_rule_from_form` has a failure left, and
            the field is on the context because
            :func:`resolve_recurrence_rule_for_update` does.
        include_due_day_of_month: ``True`` for transaction templates, ``False``
            for transfer templates.

    Returns:
        The flushed :class:`RecurrenceRule`, or ``None`` when the form said
        "Does not repeat".
    """
    return build_recurrence_rule_from_form(
        data,
        user_id=user_id,
        ctx=RecurrenceFormContext(
            # ABSENT when the form stated no bound -- a disabled control, or a
            # partial update -- which the helpers read as "leave the stored one
            # alone" (plan step R7b-3).  On a CREATE there is no stored bound,
            # so the builder reads it as "ends never".
            end_bound=data.pop(RECURRENCE_END_BOUND_KEY, None),
            redirect=redirect,
            include_due_day_of_month=include_due_day_of_month,
        ),
    )


def build_recurrence_rule_from_form(
    data: dict[str, Any],
    *,
    user_id: int,
    ctx: RecurrenceFormContext,
) -> RecurrenceRule | None:
    """Build a :class:`RecurrenceRule` from a validated form payload.

    Pops every recurrence-related key from ``data`` so the caller's
    downstream ``TransactionTemplate`` / ``TransferTemplate``
    constructor does not receive stray kwargs, then authors the rule
    through :func:`app.services.recurrence.author_rule` -- the one door
    that resolves both of the table's cadence vocabularies together.

    **It cannot fail on user input, since plan step R7b-4**, and the
    signature says so: it used to owner-check a submitted
    ``start_period_id`` and return a redirect :class:`Response` when the
    period was not this user's.  The recurrence no longer has a start
    PERIOD -- its opening bound is a date -- and the one caller that still
    submits one does so for its own non-recurring job, so that check moved
    to the route that owns the field
    (``transfers.create_transfer_template``).  A kind-agnostic helper
    checking a field only one kind submits was the coupling; moving it
    removed the coupling and the failure mode together.

    **It does NOT validate the cadence.**  That the submitted axes name values
    the application MODELS -- narrower than "name ``ref`` rows", and the
    difference is a 500 -- is a property of the SUBMISSION, so it belongs to
    the submission's validator:
    :class:`~app.schemas.validation._helpers.RecurrenceUnitField` and
    :class:`~app.schemas.validation._helpers.PeriodPlacementField` refuse it
    before any route code runs (plan step R2e-2 on the pattern field those two
    replaced, developer ruling 2026-08-07).  That the whole
    ``(interval, unit, placement)`` TRIPLE can be stored is a property of no
    single field, so it is
    :func:`~app.schemas.validation._helpers.validate_authorable_cadence`'s.
    The check used to live here AND in
    :func:`update_recurrence_rule_from_form` -- one rule written twice, which
    a third caller would have had neither copy of.

    Args:
        data: Marshmallow-validated payload; mutated in place.  The
            helper pops ``recurrence_unit``, ``recurrence_placement``,
            ``interval_n``, ``day_of_month``, ``month_of_year``,
            ``start_date``, the closing
            bound's three (:data:`_END_BOUND_KEYS`), and -- when
            ``ctx.include_due_day_of_month`` is ``True`` --
            ``due_day_of_month``.
        user_id: Owner of the resulting :class:`RecurrenceRule` row.
        ctx: The :class:`RecurrenceFormContext` carrying the form's
            ``end_bound`` (written onto the rule, or ``NEVER_ENDS`` when the
            form stated none) and the ``include_due_day_of_month``
            transaction-vs-transfer flag.  Its ``redirect`` is unused here now
            that the helper has no failure, and kept only because the three
            helpers share one context object.

    Returns:
        * :class:`RecurrenceRule` -- newly added, flushed, ready to
          link.  The caller is responsible for setting any owning-row
          FK (e.g. ``template.recurrence_rule_id = rule.id``).
        * ``None`` -- no recurrence pattern was selected, so the template does
          not repeat; the helper still popped every recurrence key from
          ``data``.
    """
    unit = data.pop("recurrence_unit", None)

    if unit is None:
        # Does not repeat: drop every recurrence-related key so the caller's
        # model constructor does not receive stray kwargs.
        #
        # **``start_date`` is popped HERE as well as below, and leaving it out
        # was a 500** -- caught by the browser drive
        # (``tests/manual/verify_recurrence_form.py``) with the whole pytest
        # suite green, which is the case that mandate exists for.  The box is
        # hidden with ``#recurrence-fields`` when the form says "does not
        # repeat", and a hidden input still SUBMITS: the payload carried
        # ``start_date`` through this early return into
        # ``TransactionTemplate(**data)``, whose constructor has no such
        # keyword.  No test payload included the key on this branch, because
        # every one of them was written by hand; a browser posts every control
        # the page renders.
        for key in _BASE_RECURRENCE_KEYS:
            data.pop(key, None)
        _pop_end_bound_keys(data)
        data.pop(_START_DATE_KEY, None)
        if ctx.include_due_day_of_month:
            data.pop(_DUE_DAY_KEY, None)
        return None

    placement = data.pop("recurrence_placement")
    interval_n = data.pop("interval_n", 1)
    # Pop the closing bound's keys even though the value comes from
    # ``ctx.end_bound`` -- keeps the "all recurrence keys removed from data"
    # contract symmetric between the repeats and does-not-repeat branches, so
    # the caller's downstream model constructor never receives one as a stray
    # kwarg.
    _pop_end_bound_keys(data)
    day_of_month = data.pop("day_of_month", None)
    month_of_year = data.pop("month_of_year", None)
    # The OPENING bound (plan step R7b-4).  Popped unconditionally, like every
    # other recurrence key, so the caller's model constructor never sees it.
    #
    # On a CREATE, an absent key and a stated empty are the same request and
    # only here: there is no stored bound to leave alone, so both author an
    # unbounded rule.  That is exactly the asymmetry
    # :attr:`RecurrenceFormContext.end_bound` records for the closing bound,
    # and for the same reason -- see the ``end_bound`` line below.
    start_date = data.pop(_START_DATE_KEY, None)
    due_day_of_month = (
        data.pop(_DUE_DAY_KEY, None) if ctx.include_due_day_of_month else None
    )

    # The offset auto-derivation this branch used to run inline -- "for an
    # every-N-paychecks rule, phase it on the chosen start period" -- moved
    # into ``resolve``, which applies it on EVERY write rather than only on
    # create.  That is what closes defect D1: the update path had no such
    # derivation and wrote the schema default instead, re-phasing every
    # future occurrence on an amount-only edit.
    #
    # **No decode step, since plan step R7b-2**: the form now POSTS the two
    # axes, so what the user authored reaches the write door unchanged and
    # ``encode_cadence`` chooses the pattern that stores it.  The translation
    # that used to sit here read a submitted pattern id back into a cadence,
    # which made the form's vocabulary and the door's differ by one hop for no
    # reason once the picker could state the cadence itself.
    #
    # ``offset_periods`` is not read from the payload at all -- the schemas no
    # longer declare it (defect D8) and the spec no longer carries it (plan
    # step R7b-4) -- so ``resolve`` derives the phase from the opening bound
    # this call states.
    return author_rule(
        RecurrenceSpec(
            user_id=user_id,
            unit=unit,
            interval_n=interval_n,
            placement=placement,
            day_of_month=day_of_month,
            due_day_of_month=due_day_of_month,
            month_of_year=month_of_year,
            start_date=start_date,
            # A create form that stated no bound authors an UNBOUNDED rule:
            # there is no stored bound to leave alone, so absence and "never"
            # are the same request here and only here.
            end_bound=(
                NEVER_ENDS if ctx.end_bound is None else ctx.end_bound
            ),
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
            ``recurrence_unit``, ``recurrence_placement``, ``interval_n``,
            ``day_of_month``, ``month_of_year``, and -- when
            ``ctx.include_due_day_of_month`` is ``True`` --
            ``due_day_of_month``.
        ctx: The :class:`RecurrenceFormContext` carrying the form's
            ``end_bound`` (which REPLACES the rule's when stated and leaves it
            alone when ``None``) and the ``include_due_day_of_month``
            transaction-vs-transfer flag.  Its ``redirect`` is unused here and
            kept only because the three helpers share one context object.

    Returns:
        ``None``.  **It cannot fail on user input**, which is what plan step
        R2e-2 changed: the one failure it used to have -- an unmodelled cadence
        -- is refused by the schema's axis fields and by
        :func:`~app.schemas.validation._helpers.validate_authorable_cadence`
        before the route reads the payload, so there is no redirect left to
        return and the signature says so.  It still RAISES
        ``RecurrenceResolutionError`` for a triple no closed-set pattern
        stores, which after those two validators is a broken invariant rather
        than a submission.
    """
    unit = data.pop("recurrence_unit")
    placement = data.pop("recurrence_placement")

    # The form's every-recurrence-key pops happen unconditionally, so the
    # caller's downstream ``setattr`` loop never sees a stray kwarg whichever
    # cadence was chosen.
    submitted_interval = data.pop("interval_n", 1)
    day_of_month = data.pop("day_of_month", None)
    month_of_year = data.pop("month_of_year", None)
    # The OPENING bound, on the SAME present-versus-absent rule the closing
    # one runs on (plan step R7b-4).  PRESENCE is read before the pop, and it
    # is not the same question as the value: the schema declares the field
    # ``allow_none``, so clearing the box arrives as a stated ``None`` that
    # means "unbounded" and MUST overwrite a stored date, while a form that
    # rendered the control disabled -- a loan payment's, whose bound the app
    # derives -- omits the key entirely and must leave the stored date alone.
    # Collapsing the two would make a loan edit erase the origination bound
    # that keeps its payments from generating before the loan exists.
    stated_start_date = _START_DATE_KEY in data
    submitted_start_date = data.pop(_START_DATE_KEY, None)
    # The bound's keys too, on THIS branch as well: the route reads the mode
    # into ``ctx.end_bound`` before calling, and a submission that named none
    # still leaves its two value keys behind (see :data:`_END_BOUND_KEYS`).
    _pop_end_bound_keys(data)
    # **The phase is no longer read from the payload at all** (defect D8).  The
    # schemas stopped declaring ``offset_periods`` at plan step R7b-2, so the
    # rule's STORED phase rides through ``recurrence_spec_with_cadence``
    # untouched and there is no submitted value able to overwrite it.
    #
    # That finishes what plan step R2d started on defect D1.  Resolution
    # already IGNORED the submitted phase for any rule naming a start period,
    # deriving it from that period instead; the remaining exposure was a rule
    # with NO start period, for which the payload was the only statement of
    # phase -- and the field no form rendered an input for therefore submitted
    # the schema's default 0 on every edit.  Deleting the field is what makes
    # "an amount-only edit cannot re-phase a cadence" structural rather than
    # conditional on a column being set.

    # The rule's CURRENT authored state, with the form's fields replaced.
    # Anything the form does not collect rides through untouched, so this edit
    # cannot reset a field it never showed the user.  BOTH validity bounds are
    # collected now (plan step R7b-4), so neither rides unconditionally: each
    # replaces the stored value when the form stated it and leaves it alone
    # when the form did not.  See the two lines below.
    #
    # ``interval_n`` needs no pattern-scoping here, and since plan step R7b-1
    # it cannot reach a column at all for a calendar cadence.  This form's
    # interval input is hidden for every pattern but EVERY_N_PERIODS and a
    # hidden input still SUBMITS, so the submitted value used to land verbatim
    # on a Quarterly rule's column, where it meant nothing and nobody read it.
    # ``decode_pattern`` now discards it for any pattern that names its own
    # interval and ``encode_cadence`` writes 1, so the column holds one meaning
    # rather than "whatever the form happened to post".  ``interval_n`` carries
    # one meaning only, "repeat every N pay PERIODS", consulted by the
    # occurrence engine's PERIOD-unit walk, by ``obligations_aggregator``'s
    # monthly equivalent under the same condition (through
    # ``recurrence.cadence_of``), and by ``_recurrence_macros.html`` inside
    # the same branch.  The interval of
    # a MONTH- or YEAR-unit recurrence is a different fact, derived from the
    # pattern by ``resolve`` and stored nowhere (plan step R2d), so no value
    # this form can submit is able to say a Quarterly bill recurs monthly.
    # That is what makes the pattern-scoped guard unnecessary rather than
    # merely relocated, and it closes the reverse case the guard left open:
    # switching an every-4-paychecks rule to Quarterly used to make it read as
    # "every 4 months".
    # **Read with the SUBMITTED cadence, never the stored one**, and the
    # difference is the repair path this form advertises: an edit page may be
    # showing a rule whose stored pattern the application no longer models
    # (``edit_form_cadence`` renders the controls UNSET and
    # ``UNAVAILABLE_PATTERN_MESSAGE`` says to choose a cadence before saving),
    # and reading that rule's cadence on the way to REPLACING it raises.
    # Measured against ``origin/dev``: routing this through ``recurrence_spec``
    # turned the one action the surface tells the user to take into a 500.
    #
    # Since plan step R7b-2 the submitted cadence needs no decoding -- the form
    # states the axes -- so what arrives here is what the user chose.
    current = recurrence_spec_with_cadence(
        rule,
        interval_n=submitted_interval,
        unit=unit,
        placement=placement,
    )
    reauthor_rule(
        rule,
        replace(
            current,
            day_of_month=day_of_month,
            due_day_of_month=(
                data.pop("due_day_of_month", None)
                if ctx.include_due_day_of_month
                else current.due_day_of_month
            ),
            month_of_year=month_of_year,
            start_date=(
                submitted_start_date if stated_start_date
                else current.start_date
            ),
            # The form states the WHOLE closing bound, never half of one
            # (plan step R7b-3): the "Ends" control's three shapes are one
            # value, so a save replaces it outright and there is no merging a
            # posted date into whatever the rule already carried.
            #
            # A form that stated NOTHING leaves the stored bound untouched --
            # ``current`` already carries it.  That is what a loan payment's
            # disabled control produces, and what an amount-only PATCH
            # produces, and neither may be read as "ends never".
            end_bound=(
                current.end_bound if ctx.end_bound is None else ctx.end_bound
            ),
        ),
        calendar_for(rule.user_id),
    )


LOAN_PAYMENT_CANNOT_BE_ONE_TIME: str = (
    "A loan payment repeats for the life of the loan, so it cannot be made "
    "one-time. Choose a different pattern to change how often it repeats, or "
    "archive it to stop paying."
)
"""Refusal shown when an edit tries to clear a loan payment's recurrence."""


UNREPAIRED_CADENCE_CANNOT_BE_CLEARED: str = (
    "This recurring definition uses a repeat pattern that is no longer "
    "available, so the form could not show you how often it repeats -- and an "
    "empty choice here would delete the schedule. Nothing was saved. Choose "
    "how often it repeats, then save."
)
"""Refusal shown when an edit would clear a rule the form could not display.

**The half of :data:`~app.services.recurrence.UNAVAILABLE_PATTERN_MESSAGE`'s
promise that has to live on the SERVER.**  That message tells the user "saving
it unchanged will be refused", and before plan step R7b-2 the picker kept that
promise by keeping the stored pattern as a trailing selected ``<option>``: the
save then carried an id the write door refused.  The two-axis controls carry no
pattern id, so they render UNSET -- which means the unit ``<select>``'s FIRST
entry is selected, and that entry is the empty "Does not repeat" one whose save
DELETES the rule and sweeps its future rows.

An unrepaired edit and a deliberate clear are therefore the same bytes on the
wire, and no hidden field can separate them -- a client may drop one.  The
server can, from two facts it already holds: the stored rule names a pattern
this application does not model, and the submission names no cadence.  A form
that could not offer this rule's cadence cannot have collected the user's
intent to remove it, so the empty submission is refused rather than acted on.
"""


LOAN_PAYMENT_BOUND_IS_DERIVED: str = (
    "A loan payment runs from the loan's first installment until it is paid "
    "off, so when it starts and stops is not something you set. Change the "
    "loan's terms to move either one, or archive the payment to stop it early."
)
"""Refusal shown when a submission states a bound the app DERIVES.

**The server half of a control the form renders disabled**, and it needs both
halves for the reason ``UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`` does: disabling
is an affordance, and a client may post whatever it likes.

``loan_recurrence_sync.sync_recurring_payment_bounds`` owns BOTH of a loan
payment's validity bounds -- the opening one is the loan's first contractual
installment, the closing one its projected payoff, and it rewrites them on
every payoff-affecting edit -- so a bound accepted here would be silently
discarded by the next such edit, which is worse than refusing it.  The OPENING
half is worse still: it is what keeps a payment from generating before the
loan originates, measured at $3,220.92 of phantom cash debits on a mortgage
closing one month out.

Refusing also keeps the two shapes of "a rule stops" from ever meeting on one
row: a submitted COUNT beside the sync's DATE is the pair
``ck_recurrence_rules_single_end_bound`` refuses, and while
:class:`~app.services.recurrence.EndBound` makes that unwritable, this is what
stops the user's stated bound being thrown away without a word.

**Which definitions it fires for is
``loan_recurrence_sync.owns_validity_window``, not
:func:`is_loan_payment`** (plan step R7b-4).  Those are different questions and
asking the second was a defect an adversarial review of plan step R7b-3 found:
a template can carry loan-payment SETTINGS without being the template that
module writes bounds for, and its form then locked a control for a value
nothing wrote.  See that predicate's docstring.
"""


def is_loan_payment(template: Any) -> bool:
    """Return whether *template* is a recurring loan payment.

    Public since plan step R7b-3, which gave it a second caller: the transfer
    edit route asks it to decide whether the "Ends" control renders locked.

    **Not the only place the question is asked**, and an adversarial review
    corrected an earlier claim here that said so:
    ``cash_ledger._amount_source._is_loan_payment`` answers the same
    ``settings is not None`` question about a TRANSFER row.  Pre-existing, and
    a wider concern than this step -- what is fixed here is the claim.

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
    * pattern SUBMITTED AS EMPTY -> the user chose "Does not repeat", so any
      existing rule is cleared through :func:`_clear_recurrence_rule` --
      unless the template is a LOAN PAYMENT (see
      :data:`LOAN_PAYMENT_CANNOT_BE_ONE_TIME`) or its stored rule names a
      pattern this application no longer models (see
      :data:`UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`), either of which is
      refused.

    **A submitted-empty pattern and an absent one are different requests**, and
    keeping them apart is what stops the third branch from breaking the
    partial-update contract.  Both schemas declare ``recurrence_unit`` as
    ``allow_none``, so the form's "Does not repeat" option survives
    ``_normalize_empty_inputs`` as a present ``None`` while a field the caller
    never submitted stays absent -- and only the first clears.  Without that
    distinction an amount-only PATCH, which submits no recurrence keys at all,
    would silently delete the template's cadence.

    **The third branch is new, and its absence was a live defect.**  The
    transaction form has offered a "does not repeat" option since the recurring
    cluster shipped, and selecting it did nothing at all: the builder returned
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

    Args:
        template: The ``TransactionTemplate`` or ``TransferTemplate``
            being updated.  Accessed for ``recurrence_rule``,
            ``recurrence_rule_id`` (assigned when a new rule is built,
            cleared when none was selected), and ``user_id``.  Mutated in
            place.
        data: Marshmallow-validated payload; the recurrence keys are
            popped by the delegated helper.  Read for whether
            ``recurrence_unit`` and ``start_date`` are PRESENT before those
            pops consume them.
        ctx: The :class:`RecurrenceFormContext` forwarded unchanged to
            the delegated builder / updater (its ``end_bound``,
            ``redirect`` target, and ``include_due_day_of_month`` flag).

    Returns:
        * ``None`` -- the rule was resolved; the caller continues to
          the field-update loop.
        * :class:`Response` -- a Flask redirect for one of the three
          refusals above; the caller returns it directly.
    """
    # Read BEFORE the delegated helper pops the key.
    recurrence_submitted = "recurrence_unit" in data
    clearing = (
        recurrence_submitted
        and data.get("recurrence_unit") is None
        and template.recurrence_rule is not None
    )
    # A loan payment may not be made one-time, and WHICH definitions that
    # covers is the UNION of two questions rather than either alone (developer
    # ruling 2026-08-14, taken on the measurement below).
    #
    # ``is_loan_payment`` asks whether the template carries
    # ``LoanPaymentSettings``, which is the right question for the standing
    # ``extra_principal`` half of the harm this refusal names.
    # ``owns_validity_window`` asks whether ``loan_recurrence_sync`` writes
    # this template's bounds, which is the right question for the rest of it:
    # clearing the recurrence nulls ``recurrence_rule_id``, and that is how
    # ``recurring_transfer_query.active_recurring_transfer_template`` FINDS a
    # loan's payment -- so the loan goes on amortizing with nothing projecting
    # a payment against it.
    #
    # **Measured on a production clone 2026-08-14: neither live loan payment
    # satisfies the first predicate** (transfer templates 2 "Mortgage" and 9
    # "Van Payment" carry no settings row), so asking it alone left both of the
    # developer's real loans clearable.  The plan step R7b-3 finding that first
    # named this predicate read it as too BROAD; it is too NARROW where it
    # matters, and the union is what makes the refusal cover the set the harm
    # is measured on without giving up the set it was written for.
    if clearing and (
        is_loan_payment(template) or owns_validity_window(template)
    ):
        flash(LOAN_PAYMENT_CANNOT_BE_ONE_TIME, "danger")
        return ctx.redirect.to_response()
    # A loan payment's validity bounds are DERIVED -- the opening one from the
    # loan's first contractual installment, the closing one from its projected
    # payoff -- so a submission stating EITHER is refused rather than accepted
    # and then discarded by the next payoff-affecting edit.  The form renders
    # both controls disabled, which is why this is reachable only by a crafted
    # POST -- and why it is checked anyway: disabling is the affordance, the
    # refusal is the rule.  See LOAN_PAYMENT_BOUND_IS_DERIVED.
    #
    # ONE guard over both bounds because ONE writer owns both, and asking
    # ``owns_validity_window`` is what keeps this refusal and that writer on
    # the same set (plan step R7b-4).
    states_a_bound = ctx.end_bound is not None or _START_DATE_KEY in data
    if states_a_bound and owns_validity_window(template):
        flash(LOAN_PAYMENT_BOUND_IS_DERIVED, "danger")
        return ctx.redirect.to_response()
    if clearing and modelled_pattern(
        template.recurrence_rule.pattern_id,
    ) is None:
        flash(UNREPAIRED_CADENCE_CANNOT_BE_CLEARED, "danger")
        return ctx.redirect.to_response()

    if data.get("recurrence_unit") is not None and template.recurrence_rule:
        # Re-points the rule in place and cannot fail, so this branch has no
        # redirect to propagate -- it returns the same ``None`` the other two
        # branches do on success.
        update_recurrence_rule_from_form(
            template.recurrence_rule,
            data,
            ctx=ctx,
        )
        return None

    rule = build_recurrence_rule_from_form(
        data,
        user_id=template.user_id,
        ctx=ctx,
    )
    if rule is not None:
        template.recurrence_rule_id = rule.id
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
    "LOAN_PAYMENT_BOUND_IS_DERIVED",
    "LOAN_PAYMENT_CANNOT_BE_ONE_TIME",
    "UNREPAIRED_CADENCE_CANNOT_BE_CLEARED",
    "STALE_EDITING_MESSAGE",
    "STALE_ACTION_MESSAGE",
    "RecurrenceFormContext",
    "build_recurrence_rule_for_create",
    "build_recurrence_rule_from_form",
    "is_loan_payment",
    "update_recurrence_rule_from_form",
    "resolve_recurrence_rule_for_update",
    "handle_stale_form_conflict",
]
