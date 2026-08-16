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

**It takes the closing bound and the redirect rather than the
:class:`~app.routes._recurrence_form_helpers.RecurrenceFormContext` that
carries them**, and that is what keeps the split a boundary rather than a pair
of modules that need each other: the context is a parameter object for the
AUTHORING helpers, so importing it here would close a cycle.  These two values
are all a refusal reads of it.

Route-layer module rather than service because every refusal ``flash``es and
redirects (the latter via
:class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask globals.  The
leading underscore marks the module as route-internal.
"""
from typing import Any

from flask import Response, flash

from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import (
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.services.loan_recurrence_sync import owns_validity_window
from app.services.recurrence import (
    EndBound,
    end_bound_from_columns,
    stored_cadence,
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


def refuse_inverted_window(
    template: Any,
    data: dict[str, Any],
    *,
    end_bound: EndBound | None,
    redirect: RedirectTarget,
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

    Args:
        template: The template being updated.  A template with no rule is
            skipped: the create branch authors from a submission the schema has
            already compared.
        data: The validated payload, NOT mutated -- the delegated helper pops
            the recurrence keys afterwards and must still see them.
        end_bound: The submitted closing bound, or ``None`` when the form
            stated none.
        redirect: Where to send the user when the submission is refused.

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
        end_bound if end_bound is not None
        else end_bound_from_columns(rule.end_date, rule.max_occurrences)
    )
    end_date = bound.columns().end_date
    if starts_on is None or end_date is None or end_date >= starts_on:
        return None
    flash(end_bound_before_start_message(end_date, starts_on), "danger")
    return redirect.to_response()


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
    end_bound: EndBound | None,
    redirect: RedirectTarget,
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
        end_bound: The submitted closing bound, or ``None`` when the form
            stated none.
        redirect: Where a refusal sends the user.
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
        return redirect.to_response()
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
    states_a_bound = end_bound is not None or RECURRENCE_STARTS_ON_KEY in data
    if states_a_bound and owns_validity_window(template):
        flash(LOAN_PAYMENT_BOUND_IS_DERIVED, "danger")
        return redirect.to_response()
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
        return redirect.to_response()

    inverted = refuse_inverted_window(
        template, data, end_bound=end_bound, redirect=redirect,
    )
    if inverted is not None:
        return inverted
    return None


__all__ = [
    "LOAN_PAYMENT_BOUND_IS_DERIVED",
    "LOAN_PAYMENT_CANNOT_BE_ONE_TIME",
    "UNREPAIRED_CADENCE_CANNOT_BE_CLEARED",
    "is_loan_payment",
    "refuse_inverted_window",
    "refuse_recurrence_update",
]
