"""
Shekel Budget App -- Why a submitted recurrence change may be REFUSED

The refusal half of :mod:`app.routes._recurrence_form_helpers`, split out at
plan step R7c-b when that module met the 1,000-line cap for the fourth time.
The seam is the one the module had already grown: everything here answers "may
this submission be applied at all", and everything there APPLIES one.  Each
refusal is a rule the form also expresses as an affordance -- a disabled
control, an unset ``<select>`` -- and disabling is never the guard, because a
client may post whatever it likes.

Four rules, and :func:`refuse_recurrence_update` asks them in the one order
they can be asked in:

1. a LOAN PAYMENT may not be made one-time
   (:data:`LOAN_PAYMENT_CANNOT_BE_ONE_TIME`);
2. a definition whose validity window the app DERIVES may not have one stated
   for it (:data:`LOAN_PAYMENT_BOUND_IS_DERIVED`);
3. a rule the form could not DISPLAY may not be cleared by the empty
   submission that state produces
   (:data:`UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`);
4. no edit may leave the rule stopping before it starts
   (:func:`refuse_inverted_window`).

**:class:`RecurrenceFormContext` is DEFINED here**, one layer below the
authoring helpers that also take it, and that is what keeps the split a
boundary rather than a pair of modules that need each other.  Until plan step
R7d-f the two refusal entries took the closing bound and the redirect as bare
arguments, because the context lived in the authoring module and importing it
here would have closed a cycle; the inverted-window refusal then came to need
a THIRD value of the caller's -- the read pass -- and three bare copies of a
parameter object's fields is the shape the object exists to remove.  So the
leaf moved (``CLAUDE.md`` rule 14's placement clause): the helpers import the
context from here, and every refusal reads it whole beside the pass.

Route-layer module rather than service because every refusal ``flash``es and
redirects (the latter via
:class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.  The
leading underscore marks the module as route-internal.
"""
from dataclasses import dataclass
from typing import Any

from flask import Response, flash

from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import (
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.services.balance_at import BalanceContext
from app.services.loan_recurrence_sync import is_standing_loan_payment
from app.services.recurrence import (
    EndBound,
    end_bound_from_columns,
    stored_cadence,
)
from app.services.recurring_definition import authored_closing

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

**The half of :data:`~app.services.recurrence.UNREADABLE_CADENCE_MESSAGE`'s
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

``loan_recurrence_sync`` owns BOTH of the loan's standing payment's validity
bounds, each in its own way since ruling **R-R29**: the OPENING bound is
WRITTEN -- the loan's first contractual installment, re-synced on every
payoff-affecting edit -- and the CLOSING bound is DERIVED, the loan's payoff
resolved through the composed door on every read.  A start accepted here
would be silently discarded by the next such edit, which is worse than
refusing it, and the OPENING half is worse still: it is what keeps a payment
from generating before the loan originates, measured at $3,220.92 of phantom
cash debits on a mortgage closing one month out.  A stop accepted here would
be discarded the same way until plan step R7d-g stops the syncs, and after it
would state a plan the loan does not know about -- so the loan's own payment
runs to the payoff and archiving is the door to stop it early (ruling
**R-R59**, developer 2026-09-05, taken at plan step R7d-f: the control stays
locked).

Refusing also keeps the two shapes of "a rule stops" from ever meeting on one
row: a submitted COUNT beside the sync's DATE is the pair
``ck_recurrence_rules_single_end_bound`` refuses, and while
:class:`~app.services.recurrence.EndBound` makes that unwritable, this is what
stops the user's stated bound being thrown away without a word.

**Which definitions it fires for is
``loan_recurrence_sync.is_standing_loan_payment``, not
:func:`is_loan_payment`** (plan step R7b-4; read off the pass since R7d-f).
Those are different questions and asking the second was a defect an
adversarial review of plan step R7b-3 found: a template can carry loan-payment
SETTINGS without being the loan's standing payment, and its form then locked a
control for a value nothing wrote.  See that predicate's docstring.
"""


@dataclass(frozen=True)
class RecurrenceFormContext:
    """Recurrence-form processing options shared across the F-24 helpers.

    A parameter object, not a single domain concept: it groups the three
    otherwise-independent knobs the helpers read so the verbatim-triplicated
    signature tail collapses to one argument (and ``resolve`` forwards it
    unchanged).

    Bundles the three inputs that
    :func:`~app.routes._recurrence_form_helpers.recurrence_spec_from_form`,
    :func:`~app.routes._recurrence_form_helpers.update_recurrence_rule_from_form`,
    :func:`~app.routes._recurrence_form_helpers.resolve_recurrence_rule_for_update`
    and the two refusal entries below share verbatim: the form's closing
    bound, the validation-error redirect target, and whether the submitting
    schema exposes ``due_day_of_month`` (transaction templates) or not
    (transfer templates).  Collapsing the formerly-triplicated
    ``end_bound`` / ``redirect_endpoint`` / ``redirect_endpoint_kwargs``
    / ``include_due_day_of_month`` signature tail into one object both
    removes the duplication and clears the per-helper
    ``too-many-arguments`` count.

    **Defined in THIS module since plan step R7d-f**, having lived in the
    authoring helpers: the refusals read two of its fields and could not
    import it across the one-way seam, so they took those two as bare
    arguments until the inverted-window door needed the read pass beside
    them.  See the module docstring.

    **It does NOT carry the read pass**, and the omission is deliberate: the
    create preamble builds this context with no stored definition to read and
    no pass in hand, and a field half the constructors leave empty is an
    optional the other half must remember to check.  The pass is the UPDATE
    path's own argument (``pass_ctx``), passed beside this object by the two
    entries that judge a stored definition.

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


def refuse_inverted_window(
    template: Any,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
    pass_ctx: BalanceContext,
) -> Response | None:
    """Refuse an UPDATE that would leave the rule stopping before it starts.

    **The update door's half of a rule the schema can only half see** (plan
    step R7c-b).  ``require_end_bound_after_start`` compares the two values a
    SUBMISSION states; an update states at most one of them and keeps the
    stored other, so the pair the save would actually write is invisible to it.
    Two ordinary edits produce it and both were unhandled 500s:

    * clearing "Starts on" while setting an earlier "Ends on" -- the cleared
      date box drops the key, the stored start is kept, and the new bound lands
      below it;
    * moving "Starts on" PAST a stored ``end_date`` without touching the "Ends"
      control, which no reviewer listed and which the schema cannot see at all
      because the bound is not in the payload.

    **There is no CHECK behind this**, which is why it is a door rather than a
    convenience.  ``ck_recurrence_rules_valid_window`` was held back on a
    developer ruling: the columns carry DERIVED loan-payment windows as well as
    authored ones, and an empty derived window is a correct answer a constraint
    cannot tell from a user's mistake.  See
    :func:`~app.schemas.validation.end_bound_before_start_message`, which both
    doors word the refusal with.

    Reads the EFFECTIVE pair on both sides -- submitted where the form stated
    it, stored where it did not -- which is the same present-versus-absent rule
    :func:`update_recurrence_rule_from_form` applies when it writes them.

    **The stored half of the pair is read through the composed door's own
    arm, never off the column** (plan step ``recurrence:R7d-f``).  Until then
    this read ``rule.end_date`` directly and carried a SKIP for the definition
    whose window the app derives: a loan cleared before its first installment
    stores an inverted pair through the sync's own production door (plan step
    ``recurrence:R7d-h``), and refusing on it made this door the constraint
    the developer declined to add -- on an edit whose only unlocked control is
    the "Repeats" select, so the owner was told "ends before it starts" with
    nothing to fix it.
    :func:`~app.services.recurring_definition.authored_closing` answers
    ``NEVER_ENDS`` for that definition -- its column is the chokepoints' cache
    of the payoff and not the owner's word (ruling **R-R56**) -- so its stored
    pair cannot invert here, and the skip is DELETED rather than kept: the
    refusal and the door read one arm, and the fence is structurally
    unnecessary.  Every other definition's stored bound is its owner's and is
    graded exactly as before, a second recurring transfer into the same loan
    included.

    The stored rule is NOT resolved here, deliberately.  The arm needs the
    loan's identity and the two bound columns, neither of which decodes the
    cadence, so a rule whose stored pattern the application no longer models
    still reaches its repair save without this door raising on the way -- the
    same property the render side states for its own read of the bound.  And
    the arm is asked only once the columns already read inverted: an
    adversarial review of this step found the first cut asking it on every
    edit, which resolved the destination loan for one boolean on a rename.

    Args:
        template: The template being updated.  A template with no rule is
            skipped: the create branch authors from a submission the schema has
            already compared.
        data: The validated payload, NOT mutated -- the delegated helper pops
            the recurrence keys afterwards and must still see them.
        ctx: The form context, read for the submitted closing bound (``None``
            when the form stated none) and for where a refusal sends the user.
        pass_ctx: The read pass the standing-payment identity is read from
            (:func:`~app.services.recurring_definition.authored_closing`).

    Returns:
        * ``None`` -- the window is well-formed, or nothing this edit states
          can invert it.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    rule = template.recurrence_rule
    if rule is None or data.get("recurrence_unit") is None:
        return None
    starts_on = (
        data[RECURRENCE_STARTS_ON_KEY] if RECURRENCE_STARTS_ON_KEY in data else rule.starts_on
    )
    bound = (
        ctx.end_bound if ctx.end_bound is not None
        else end_bound_from_columns(rule.end_date, rule.max_occurrences)
    )
    end_date = bound.columns().end_date
    if starts_on is None or end_date is None or end_date >= starts_on:
        return None
    # The pair reads INVERTED off the columns.  A stored bound is read as the
    # DOOR reads it before it is refused back at the owner: for the loan's
    # standing payment that column is the cache and not the owner's word, so
    # the arm answers ``NEVER_ENDS`` and there is nothing to refuse.  Asked
    # only here -- an ordinary edit of any definition never reaches this line
    # and never resolves the loan for one boolean; a STATED bound was already
    # refused for the standing payment one rule up, so the arm is asked only
    # about a stored one.  The arm can only REMOVE an inversion, never make
    # one, so asking it after the column test answers exactly what asking it
    # before would.
    if ctx.end_bound is None:
        end_date = authored_closing(template, bound, pass_ctx).columns().end_date
        if end_date is None:
            return None
    flash(end_bound_before_start_message(end_date, starts_on), "danger")
    return ctx.redirect.to_response()


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


def refuse_recurrence_update(
    template: Any,
    data: dict[str, Any],
    *,
    ctx: RecurrenceFormContext,
    pass_ctx: BalanceContext,
    recurrence_submitted: bool,
) -> Response | None:
    """Return why this update's recurrence may not be applied, or ``None``.

    Every refusal :func:`resolve_recurrence_rule_for_update` makes, asked
    together and BEFORE any of its three branches writes anything -- so the
    dispatcher holds the dispatch and this holds the rules.  Split out at plan
    step R7c-b, when the inverted-window door made a fourth and pushed the
    dispatcher past pylint's ``too-many-return-statements``; decomposing is
    this project's answer to that count, never a disable, and it is the same
    move ``transfers.templates._settle_create_references`` records one module
    over.

    The four, in the one order they can be asked in -- each later one assumes
    the earlier ones passed:

    1. a LOAN PAYMENT may not be made one-time;
    2. a definition whose validity window the app DERIVES may not have one
       stated for it;
    3. a rule the form could not DISPLAY may not be cleared by the empty
       submission that state produces;
    4. no edit may leave the rule stopping before it starts.

    Args:
        template: The template being updated.  Read only; not mutated.
        data: The validated payload.  Read for PRESENCE before the delegated
            helpers pop the recurrence keys; not mutated.
        ctx: The form context: the submitted closing bound (``None`` when the
            form stated none) and where a refusal sends the user.
        pass_ctx: The read pass.  Read for ONE fact, whether *template* is
            the standing payment of the loan it pays into
            (:func:`~app.services.loan_recurrence_sync.is_standing_loan_payment`),
            which two of the four rules turn on and the fourth reads through
            the composed door's arm -- and read only when one of them can
            fire, because answering it resolves the loan.  Built by the route BEFORE any write, as
            the 2026-08-16 ruling has it (a producer below the route takes the
            pass and never builds one); regeneration afterwards builds its own,
            as a writer must.
        recurrence_submitted: Whether ``recurrence_unit`` is present at all,
            read by the caller before this runs because the two of them share
            it and a second ``in`` test is a second statement of "did the form
            mention the recurrence".

    Returns:
        * ``None`` -- the update may proceed.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    clearing = (
        recurrence_submitted
        and data.get("recurrence_unit") is None
        and template.recurrence_rule is not None
    )
    states_a_bound = (
        ctx.end_bound is not None or RECURRENCE_STARTS_ON_KEY in data
    )
    # ONE identity for the two rules that turn on it (plan step R7d-f): is
    # this the standing payment of the loan it pays into.  Asked ONLY when a
    # submission clears the recurrence or states a bound -- the identity is
    # read off the pass's loan resolution, and on this pre-write pass nothing
    # else has resolved the loan yet, so asking it on every edit would resolve
    # the loan for one boolean on an amount-only PATCH (an adversarial review
    # of this step measured the first cut doing exactly that).  The
    # inverted-window door below reads the same memo through the composed
    # door's arm, and only once a stored pair already reads inverted.
    standing_payment = (
        (clearing or states_a_bound)
        and is_standing_loan_payment(template, pass_ctx)
    )
    # A loan payment may not be made one-time, and WHICH definitions that
    # covers is the UNION of two questions rather than either alone (developer
    # ruling 2026-08-14, taken on the measurement below).
    #
    # ``is_loan_payment`` asks whether the template carries
    # ``LoanPaymentSettings``, which is the right question for the standing
    # ``extra_principal`` half of the harm this refusal names.
    # ``is_standing_loan_payment`` asks whether this template IS the loan's
    # payment, which is the right question for the rest of it: clearing the
    # recurrence DELETES the rule row, and its existence is how
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
    if clearing and (is_loan_payment(template) or standing_payment):
        flash(LOAN_PAYMENT_CANNOT_BE_ONE_TIME, "danger")
        return ctx.redirect.to_response()
    # The loan's standing payment's validity bounds are the app's -- the
    # opening one WRITTEN from the loan's first contractual installment, the
    # closing one DERIVED from its payoff with no authored stop (ruling
    # **R-R59**) -- so a submission stating EITHER is refused rather
    # than accepted and then discarded by the next payoff-affecting edit.  The
    # form renders both controls disabled, which is why this is reachable only
    # by a crafted POST -- and why it is checked anyway: disabling is the
    # affordance, the refusal is the rule.  See LOAN_PAYMENT_BOUND_IS_DERIVED.
    #
    # ONE guard over both bounds because ONE identity decides both (plan step
    # R7d-f split what each bound's rule MEANS without splitting the set it
    # applies to), and reading it off the pass is what keeps this refusal, the
    # form's two locks and the composed door on one producer.
    #
    # **ABSENCE is the signal, for BOTH halves** (developer ruling
    # 2026-08-15).  Each locked control renders ``disabled`` and a disabled
    # control posts nothing, so a form that may not state a bound states
    # nothing about it -- one meaning per wire state, and the same one the
    # partial-update contract already gives an omitted key.
    #
    # Plan step R7c-b briefly made the opening control ``readonly`` instead,
    # because the schema requires a first occurrence beside any chosen cadence
    # and disabling it would have made a locked form unsubmittable.  A readonly
    # input DOES post, which broke this refusal both ways: renaming a loan
    # payment answered LOAN_PAYMENT_BOUND_IS_DERIVED and saved nothing, and
    # weakening the test to "refuse a DIFFERENT date" would have made
    # "not mine to state" and "I chose exactly the derived value" the same
    # bytes -- so a loan re-amortised between render and save would refuse an
    # innocent rename on a stale echo.  The schema rule moved to CREATE instead
    # (``RecurrenceFormFieldsMixin.validate_recurrence_states_a_start``), which
    # is where the money it guards actually is.
    if states_a_bound and standing_payment:
        flash(LOAN_PAYMENT_BOUND_IS_DERIVED, "danger")
        return ctx.redirect.to_response()
    # **The SAME question ``edit_form_cadence`` renders unset on** (plan step
    # R7c-b).  ``UNREPAIRED_CADENCE_CANNOT_BE_CLEARED`` is the server half of a
    # promise the form makes, so the two must be one predicate: a rule the form
    # could not display renders the unit ``<select>`` on its first entry, and
    # that entry is the empty "Does not repeat" whose save DELETES the rule.
    # This asked only about ``pattern_id`` while the form derived the unit and
    # the placement from it too, so any widening of one without the other would
    # have let an unchanged save destroy exactly the rule the repair path
    # exists to fix.  ``stored_cadence`` is that one predicate.
    if clearing and stored_cadence(template.recurrence_rule) is None:
        flash(UNREPAIRED_CADENCE_CANNOT_BE_CLEARED, "danger")
        return ctx.redirect.to_response()

    inverted = refuse_inverted_window(
        template, data, ctx=ctx, pass_ctx=pass_ctx,
    )
    if inverted is not None:
        return inverted
    return None


__all__ = [
    "LOAN_PAYMENT_BOUND_IS_DERIVED",
    "LOAN_PAYMENT_CANNOT_BE_ONE_TIME",
    "UNREPAIRED_CADENCE_CANNOT_BE_CLEARED",
    "RecurrenceFormContext",
    "is_loan_payment",
    "refuse_inverted_window",
    "refuse_recurrence_update",
]
