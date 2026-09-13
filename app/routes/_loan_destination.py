"""
Shekel Budget App -- What a LOAN Destination Decides at the Transfer Form's Doors

What a LOAN destination decides about a recurring transfer's validity bounds,
at BOTH of the generic transfer form's doors, and the form's affordance for it:

* the CREATE door (:func:`settle_first_occurrence`; plan step R7c-b, rulings
  **R-R60** and **R-R74** at plan step R7d-f-3): the first occurrence is the
  loan's first contractual installment, derived into the payload, and a stop
  stated where the loan holds no active payment is refused;
* the UPDATE door (:func:`settle_destination_for_update`; plan step R7d-f-4,
  rulings **R-R76** and **R-R77**): the same two rules for the two edits that
  make a definition a recurring transfer into a loan, and a loan payment
  cannot be pointed at another account at all
  (:data:`LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION`);
* the form's lock affordance (:class:`LoanDestinationLocks`,
  :func:`loan_destination_locks` for the create form and, since plan step
  R7d-f-5, :func:`loan_destination_locks_for_edit` for the edit form; ruling
  **R-R79**): the sets of destination ids the transfer form ships so
  ``recurrence_form.js`` can lock a bound row the moment a loan is chosen,
  computed from the producers the doors read so the browser decides no domain
  fact, and whether the destination may change at all.

The two doors share one derivation of the first occurrence
(:func:`settle_loan_start`), one reading of whether the loan holds a payment
(:func:`_loan_holds_no_active_payment`) and one ordered pair of closing-bound
refusals (:func:`_refuse_stops_for_loan_destination`), so an edit cannot author
what a create refuses (plan ledger row **REC-521**).

**Split out of :mod:`app.routes._transfer_creation_helpers` at plan step
R7d-f-5** (developer ruling **R-R78**, 2026-09-12), where it had accumulated
since R7c-b beside the four transfer-creation helpers until that module stood
at pylint's 1,000-line cap with this leaf's per-edit producer still to land --
the shape ruling **R-R75** took for ``balance_at/_context.py``: a pure move
graded by AST, then the step.  Route-layer module (leading underscore =
route-internal) rather than a service because every door here consumes Flask
globals (``flash``, ``current_user``, the redirect through
:class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask.  Nothing here
creates or mutates transfer shadow transactions -- the doors write into the
PAYLOAD and refuse, and the writes stay the route's -- so the transfer
invariants are unaffected.
"""
from dataclasses import dataclass
from datetime import date
from typing import Any

from flask import Response, flash
from flask_login import current_user

from app.enums import RecurrenceUnitEnum
from app.models.loan_params import LoanParams
from app.models.recurrence_rule import RecurrenceRule
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_form_refusals import (
    LOAN_PAYMENT_BOUND_IS_DERIVED,
    is_loan_payment_or_standing,
)
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import (
    RECURRENCE_END_BOUND_KEY,
    RECURRENCE_NEEDS_A_START,
    RECURRENCE_NOMINAL_DAY_KEY,
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.services import loan_loaders, loan_recurrence_sync
from app.services.balance_at import BalanceContext
from app.services.recurrence import (
    NEVER_ENDS,
    UNREADABLE_CADENCE_MESSAGE,
    EndBound,
    end_bound_from_columns,
    stored_cadence,
)
from app.services.recurring_transfer_query import (
    active_recurring_transfer_templates,
)


LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION: str = (
    "A loan payment pays the loan it was set up for, so it cannot be pointed "
    "at another account. Archive it to stop paying this loan, or set up a new "
    "transfer to pay another one."
)
"""Refusal shown when an edit would point a loan's STANDING payment elsewhere.

**Ruling R-R76** (developer 2026-09-12, taken at plan step R7d-f-4): a loan
payment cannot be moved onto a different account.  The twin of
:data:`~app.routes._recurrence_form_refusals.LOAN_PAYMENT_CANNOT_BE_ONE_TIME`,
and it covers the SAME UNION that refusal covers (the developer ruling of
2026-08-14 recorded on it): the loan's STANDING payment -- its oldest active
recurring transfer, the identity
:func:`~app.services.balance_at.is_standing_loan_payment` names and the edit
form locks both bound rows on -- OR a definition carrying a loan-payment
SETTINGS row (:func:`~app.routes._recurrence_form_refusals.is_loan_payment`),
which the loan dashboard's own door writes for a second payment too.  The
harm is the twin's one field over.  For the standing payment, a rule's
existence is how ``recurring_transfer_query`` FINDS a loan's payment, so
re-pointing the rule's owner leaves the loan amortizing with nothing
projecting a payment against it, exactly as deleting the rule does -- and the
moved row would carry the old loan's cached payoff (ruling **R-R56**) into a
column its new destination reads as the owner's word.  For a settings-carrying
payment the amount model prices the definition off the loan it pays into
(``cash_ledger._amount_source._loan_payment_cash``: a DERIVE-mode payment is
P&I plus escrow, which a savings destination cannot answer), so the moved row
would refuse on every read of its cash.  An adversarial review of this step
found the second half missing from the first cut, which asked the identity
alone.  The two real intents each have a door: archive it to stop paying this
loan, or create a payment for the other one.

Enforced in :func:`settle_destination_for_update`, and what it buys
structurally is stated there: every destination move that door settles is a
move of a NON-standing definition.
"""


def settle_first_occurrence(
    data: dict[str, Any], *, redirect: RedirectTarget,
) -> Response | None:
    """Derive a loan payment's first occurrence, or refuse a missing one.

    **The CREATE-form half of the rule the EDIT form states as
    ``LOAN_PAYMENT_BOUND_IS_DERIVED``** (plan step R7c-b, developer ruling
    2026-08-15).  A recurring loan payment's first occurrence is the loan's
    first contractual installment; the app writes it, so the form's control is
    locked and posts nothing, and this is what fills the gap the lock leaves.

    **It DERIVES before the rule is built rather than after**, which is the
    whole of what that ruling changed.  ``bind_rule_to_loan`` still runs later
    and is now a no-op for this path; before this, the generic transfer form
    authored a rule from the date the USER typed and had it silently replaced a
    few lines on -- the shape
    :func:`~app.services.loan_recurrence_sync.loan_cadence_start`'s own
    docstring records as worse than duplication.

    **The refusal is here rather than in the schema, for the reason the update
    path's is in the route**: whether a start is required depends on the
    DESTINATION -- a loan derives one, anything else must state one -- and a
    schema never learns which accounts are loans.  So
    ``TransferTemplateCreateSchema`` carries
    ``recurrence_start_is_required = False`` and this states the rule with the
    schema's own message, exactly as
    ``_recurrence_form_helpers.resolve_recurrence_rule_for_update`` does.

    **The CLOSING bound's create-side half lands here too** (plan step
    R7d-f-3, ruling **R-R60**), and since plan step R7d-f-4 the whole loan
    branch is the one this door SHARES with the update door
    (:func:`settle_destination_for_update`): :func:`settle_loan_start` writes
    the derived first occurrence, and :func:`_refuse_stops_for_loan_destination`
    refuses a stop stated where the destination loan holds no active
    recurring payment -- the definition being created IS the loan's payment
    the moment it exists, the oldest active transfer into the loan, which is
    the identity :func:`~app.services.balance_at.is_standing_loan_payment`
    names and the edit form locks both rows on, and the loan's own payment
    carries no authored stop (ruling **R-R59**) -- rather than saving it into
    the column the chokepoints then overwrite with the payoff, which is the
    closing-bound twin of the silently-replaced start this function was
    written to close (plan ledger row **N-512**).  A loan that already holds
    a payment makes this a SECOND transfer into it, whose stop is its owner's
    and binds beside the derived one, so only the inverted-window rule is
    asked of it.

    A submission naming NO cadence authors no rule and is left alone: "does
    not repeat" needs no first occurrence.

    Args:
        data: The validated payload, mutated in place.  Its ``to_account_id``
            must already be ownership-checked -- this reads the destination's
            loan parameters, so an unchecked id would be an IDOR.
        redirect: Where to send the user when the submission is refused.

    Returns:
        * ``None`` -- ``data`` now carries a first occurrence, or names no
          cadence at all; the caller continues.
        * :class:`Response` -- the refusal redirect, returned verbatim -- an
          unstated first occurrence for a non-loan destination, or one of the
          two closing-bound refusals a loan destination adds
          (:func:`_refuse_stops_for_loan_destination`).
    """
    if data.get("recurrence_unit") is None:
        return None
    to_account_id = data["to_account_id"]
    params = loan_loaders.load_loan_params(to_account_id)
    if params is not None:
        derived = settle_loan_start(
            data, params=params, unit=data["recurrence_unit"],
        )
        # A create states one bound for both rules: what it submits is what
        # it writes (an absent one authors the unbounded rule).
        bound = data.get(RECURRENCE_END_BOUND_KEY)
        return _refuse_stops_for_loan_destination(
            to_account_id, submitted=bound, written=bound,
            starts_on=derived.starts_on, redirect=redirect,
        )
    if data.get(RECURRENCE_STARTS_ON_KEY) is not None:
        return None
    for message in RECURRENCE_NEEDS_A_START[RECURRENCE_STARTS_ON_KEY]:
        flash(message, "danger")
    return redirect.to_response()


def settle_loan_start(
    data: dict[str, Any], *, params: LoanParams, unit: RecurrenceUnitEnum,
) -> loan_recurrence_sync.LoanCadenceStart:
    """Write a loan destination's derived first occurrence into *data*.

    The one derivation both doors run for a recurring transfer INTO a loan
    (plan step R7c-b at create, R7d-f-4 at update): the first occurrence is
    the loan's first contractual installment, read through the one producer
    :func:`~app.services.loan_recurrence_sync.loan_cadence_start`, and written
    into the payload BEFORE the rule is built so nothing is authored that a
    sync then replaces.  Whatever the payload said about ``starts_on`` is
    replaced.  The create form locks the control and posts nothing; a posted
    date -- a browser with the script off, a crafted POST, an edit form whose
    row is still open -- is exactly the input the door was written to stop
    being authored, and no worse an outcome than a discarded input.

    Args:
        data: The validated payload, mutated in place: ``starts_on`` and
            ``nominal_day`` are written.
        params: The destination loan's terms, loaded by the caller -- each
            door loads them ONCE, after the destination is ownership-checked
            (an unchecked read would be an IDOR), and decides on the answer
            whether it has a loan destination at all before anything else is
            settled.  The load stays the caller's so that decision precedes
            every other write into *data* (an adversarial review of plan step
            R7d-f-4 found the update door completing a partial submission's
            cadence before it knew the destination was a loan).
        unit: The cadence unit the derivation keys the nominal day on.

    Returns:
        The :class:`~app.services.loan_recurrence_sync.LoanCadenceStart` just
        written.
    """
    cadence_start = loan_recurrence_sync.loan_cadence_start(unit, params)
    data[RECURRENCE_STARTS_ON_KEY] = cadence_start.starts_on
    data[RECURRENCE_NOMINAL_DAY_KEY] = cadence_start.nominal_day
    return cadence_start


def _loan_holds_no_active_payment(account_id: int, user_id: int) -> bool:
    """Return whether the loan *account_id* holds no active recurring payment.

    **The door-time reading of the standing-payment identity, spelled once
    for its readers here**: the two doors' stop refusal
    (:func:`_refuse_stop_on_a_new_loan_payment`), the update door's
    unbounded-rule arm (:func:`settle_destination_for_update`, ruling
    **R-R77**) and the form's affordance (:func:`_loan_destination_lock_sets`).
    The standing payment is the loan's oldest ACTIVE recurring transfer -- the
    first of
    :func:`~app.services.recurring_transfer_query.active_recurring_transfer_templates`,
    the producer the read pass memoises for
    :func:`~app.services.balance_at.is_standing_loan_payment`.  A definition
    that does not exist yet -- or does not repeat yet, or pays into this loan
    only once this edit lands -- cannot be asked that predicate, but a loan
    with no such transfer makes whatever is created into it next the standing
    payment by construction.  Asked as EMPTINESS of the set rather than as
    ``None`` from the singular reader, so no oldest-first tie-break is
    invoked for a question that has none (ruling **R-R35**).  Asked of the
    producer directly rather than off a pass: a create resolves no loan
    before it generates, so there is no memo to read and building a pass
    here would fold the loan for one boolean.

    Args:
        account_id: A CONFIGURED loan's account.  The callers have already
            established that (``load_loan_params`` for the doors, the loan-id
            loader for the affordance); asked of a savings account this would
            answer ``True`` and mean nothing.
        user_id: The owner, scoping the query as the producer requires.

    Returns:
        ``True`` when no active recurring transfer pays into the loan.
    """
    return not active_recurring_transfer_templates(account_id, user_id)


def _refuse_stops_for_loan_destination(
    to_account_id: int, *,
    submitted: EndBound | None,
    written: EndBound | None,
    starts_on: date,
    redirect: RedirectTarget,
) -> Response | None:
    """Ask the two closing-bound rules a loan destination adds, in their order.

    The stop that may not be stated at all is refused BEFORE the stop that
    merely inverts, in the order the edit door asks the same two rules: a
    user told "this ends before it starts" would move a date they cannot
    state here at all.  One statement of the order for both doors (plan step
    R7d-f-4).

    **The two rules grade two different bounds**, and at create they happen
    to be one value.  The first is about what the OWNER STATES in this
    submission (ruling **R-R74**'s reading), so a stored stop an update
    leaves alone is not its subject; the second is about the pair the WRITE
    would leave stored, so a stored stop the update keeps beside the derived
    start is exactly its subject.

    Args:
        to_account_id: The configured loan the definition will pay into,
            ownership-checked by the caller.
        submitted: The closing bound this submission composed, or ``None``
            when it stated nothing.
        written: The closing bound the write would state -- the same value
            at create; at update the submission's, or the stored one an
            absent key leaves alone, or the unbounded rule where the loan
            derives the stop (ruling **R-R77**).
        starts_on: The first occurrence just derived from the loan's contract.
        redirect: Where to send the user when the submission is refused.

    Returns:
        The refusal redirect, or ``None`` when both rules pass.
    """
    refusal = _refuse_stop_on_a_new_loan_payment(
        to_account_id, submitted, redirect=redirect,
    )
    if refusal is not None:
        return refusal
    return _refuse_bound_before_derived_start(
        written, starts_on, redirect=redirect,
    )


def _refuse_stop_on_a_new_loan_payment(
    to_account_id: int, bound: EndBound | None, *, redirect: RedirectTarget,
) -> Response | None:
    """Refuse a stated stop where the definition would be its loan's payment.

    **The create door's half of ``LOAN_PAYMENT_BOUND_IS_DERIVED``** (plan step
    R7d-f-3, ruling **R-R60**, developer 2026-09-05), and since plan step
    R7d-f-4 the update door's too for the two edits that make a definition a
    loan's payment (:func:`settle_destination_for_update`).  The edit door
    refuses a stated bound for the loan's standing payment; neither door can
    ask that identity of a definition that is not that loan's recurring
    transfer yet, so both ask the same producer the other way round: a loan
    holding no active payment makes this definition its payment
    (:func:`_loan_holds_no_active_payment`).

    **What "stated" means HERE is a real stop -- a date or a count -- and not
    the key's presence**, which is the one place these doors read the wire
    differently from the standing payment's (developer ruling 2026-09-12,
    taken at R7d-f-3; ruling **R-R77** extends it to the update door).  The
    standing payment's locked control is server-disabled, so any key it
    receives is a crafted POST; the create form's server render cannot know
    the destination, so it emits the "Ends" select ENABLED with "Never"
    preselected, and an ordinary browser posts ``never`` unless the script's
    lock ran -- and an edit form whose definition is not the standing payment
    yet renders the control enabled for the same reason.  Refusing on
    presence would therefore make the script the control -- the thing R-R60
    demotes to an affordance -- and refuse an owner who chose nothing on a
    page rendered before the loan lost its payment.  A stated "Never" and an
    absent key author the same row here (``recurrence_spec_for_create`` reads
    an absent bound as unbounded, and the update door states ``NEVER_ENDS``
    for it under R-R77), the unbounded rule the loan's payment carries, so
    accepting both refuses nothing real.  The reason the edit door reads
    presence for the standing payment -- a posted START could be a stale echo
    of a derived date -- has no counterpart for a stop whose derived value is
    the constant ``NEVER_ENDS``.

    Cheapest disqualifier first: the query runs only when a real stop was
    stated, so an ordinary create of a loan payment costs no lookup here.

    Args:
        to_account_id: A configured loan the caller has already
            ownership-checked.
        bound: The composed closing bound THIS SUBMISSION states (``None``
            when it stated nothing; ``NEVER_ENDS`` when the form said
            "Never").  Never a stored bound: what an update leaves alone is
            not something its owner stated here (ruling **R-R77**).
        redirect: Where to send the user when the submission is refused.

    Returns:
        * ``None`` -- no real stop was stated, or the loan already holds a
          payment and this is a second transfer whose stop is its owner's.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    if bound is None or bound == NEVER_ENDS:
        return None
    if not _loan_holds_no_active_payment(to_account_id, current_user.id):
        return None
    flash(LOAN_PAYMENT_BOUND_IS_DERIVED, "danger")
    return redirect.to_response()


def _refuse_bound_before_derived_start(
    bound: EndBound | None, starts_on: date, *, redirect: RedirectTarget,
) -> Response | None:
    """Refuse a stated closing bound that precedes the DERIVED first occurrence.

    **The comparison the schema could not make**, and leaving it out was an
    unhandled 500.  ``RecurrenceFormFieldsMixin.build_end_bound`` runs
    ``require_end_bound_after_start`` at load time, which early-returns when
    ``starts_on`` is absent -- and absent is exactly what the loan branch
    produces, because the create form's "Starts on" control is locked and
    posts nothing -- and which, on an update, compares the OWNER's posted
    start rather than the one :func:`settle_loan_start` replaces it with.  So
    any past "Ends on" passed every validator and the pair reached the write
    door with the derived start beside it, generating a rule that names no
    occurrence at all: the write door does not refuse an inverted pair and no
    CHECK does (``refuse_inverted_window`` says why).  The create form's
    server render cannot lock the "Ends" control (it cannot know the
    destination), and where the loan already holds a payment the row stays
    the owner's on purpose -- a second transfer's stop binds beside the
    derived one -- so the form invites exactly this.

    Worded through
    :func:`~app.schemas.validation.end_bound_before_start_message`, the same
    sentence both other doors use, so a user who states an impossible window
    reads one refusal wherever they state it.

    Args:
        bound: The composed closing bound the write would state, or ``None``
            when nothing states one.
        starts_on: The first occurrence just derived from the loan's contract.
        redirect: Where to send the user when the submission is refused.

    Returns:
        * ``None`` -- nothing states a bound, or it is at or after the
          derived start.
        * :class:`Response` -- the refusal redirect, returned verbatim.
    """
    if bound is None:
        return None
    end_date = bound.columns().end_date
    if end_date is None or end_date >= starts_on:
        return None
    flash(end_bound_before_start_message(end_date, starts_on), "danger")
    return redirect.to_response()


def settle_destination_for_update(
    template: TransferTemplate,
    data: dict[str, Any],
    *,
    end_bound: EndBound | None,
    pass_ctx: BalanceContext,
    redirect: RedirectTarget,
) -> tuple[EndBound | None, Response | None]:
    """Settle what an UPDATE's destination decides about the rule's two bounds.

    **The update door's twin of :func:`settle_first_occurrence`** (plan step
    R7d-f-4, plan ledger row **REC-521**).  The create door derives a loan
    destination's first occurrence and refuses a stop stated where the loan
    holds no active payment; the update door enforced neither, in two shapes,
    because its refusals judge the STORED definition: a "does not repeat"
    transfer into a payment-less loan given a cadence (no rule, so
    :func:`~app.services.balance_at.is_standing_loan_payment` answered
    ``False`` and the authoring branch wrote the owner's typed start and
    stop), and a recurring transfer into savings MOVED onto a payment-less
    loan (the identity was judged against savings, then the field loop moved
    the column).  Either way the row was the loan's standing payment with an
    owner's word in both bound columns.  This runs BEFORE
    ``resolve_recurrence_rule_for_update`` and settles the definition the
    edit LEAVES rather than the one it found, through the producers the
    create door reads (:func:`settle_loan_start`,
    :func:`_refuse_stops_for_loan_destination`,
    :func:`_loan_holds_no_active_payment`), so an edit cannot author what a
    create refuses.

    Three rules, in the one order they can be asked in:

    1. **A loan payment cannot be pointed at another account** (ruling
       **R-R76**, :data:`LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION`): the loan's
       STANDING payment, or a definition carrying loan-payment settings --
       the union its twin refusal covers, spelled once for both and for the
       form's affordance as
       :func:`~app.routes._recurrence_form_refusals.is_loan_payment_or_standing`
       (plan step R7d-f-5).  Asked first, and of the stored
       definition, because it is what makes every move settled below a move
       of a NON-standing definition: its stored closing bound is its owner's
       word rather than a cached payoff, and the start written below cannot
       trip the presence refusal the edit door keeps for the standing payment
       (``refuse_recurrence_update``'s ``LOAN_PAYMENT_BOUND_IS_DERIVED`` arm
       reads ``starts_on``'s PRESENCE, and rule 2 writes that key).
    2. **The first occurrence is DERIVED** for the two edits that make the
       definition a recurring transfer into a loan -- adding a cadence to a
       definition that had none, or moving one that repeats onto a loan --
       whatever the payload said, exactly as at create.  Written into the
       payload, where both update branches read it: the authoring branch as
       the start it builds from, the re-point branch as a PRESENT key that
       replaces the stored date.  A partial submission that moves a recurring
       definition without restating its cadence is COMPLETED from the stored
       row (:func:`~app.services.recurrence.stored_cadence`) so the re-point
       branch runs and carries the derived start: "absent leaves the stored
       cadence alone" and "restate the stored cadence" are one request, and
       completing it is what keeps the derivation on one branch rather than
       giving this function a rule writer of its own.  A cadence the
       application cannot read cannot be completed, so that submission is
       refused with the form's own repair sentence
       (:data:`~app.services.recurrence.UNREADABLE_CADENCE_MESSAGE`).  Which
       cadence is :func:`_cadence_unit_to_settle`, asked only once the
       destination is known to be a loan (an adversarial review of this step
       found the first cut completing a partial move onto SAVINGS); the
       derivation is :func:`settle_loan_start` and rule 3 is
       :func:`_settle_stop_for_loan_destination`.
    3. **The closing bound is the create door's, one door over** (ruling
       **R-R77**, developer 2026-09-12): a REAL stop this submission states --
       a date or a count -- is refused where the destination loan holds no
       active payment, and a stop before the derived start is refused for any
       loan; where the loan derives the stop and the submission states
       "Never" or NOTHING, the write states the unbounded rule -- even where
       the moved definition stored a real stop.  That stop was the owner's
       word about a savings transfer, not about the loan, and the loan's own
       payment carries no stop (ruling **R-R59**); and a locked "Ends" row
       posts nothing, so "nothing" has to mean the unbounded rule for the
       affordance plan step R7d-f-5 adds to this form to be lockable at all.
       The update door's ordinary reading of an absent bound -- leave the
       stored one alone -- is kept for every other edit, a move onto a loan
       that already holds a payment included: a SECOND transfer's stop is its
       owner's, graded against the derived start.

    Args:
        template: The definition being edited, owner-checked by the route.
            Read for its stored destination, rule and settings row; NOT
            mutated -- the field loop moves the column afterwards.
        data: The validated partial payload, mutated in place: the derived
            ``starts_on`` and ``nominal_day`` are written for a loan
            destination, and the stored cadence keys for a partial move (rule
            2).  Every FK in it must already be ownership-checked -- this
            reads the destination's loan parameters, so an unchecked id would
            be an IDOR.
        end_bound: The closing bound the submission composed, or ``None``
            when it stated nothing about it (the route lifted it out of
            *data* before this).
        pass_ctx: The read pass the route built BEFORE any write.  Rule 1's
            identity is read off its loan-resolution memo, which the refusals
            after this read again for free.
        redirect: Where a refusal sends the user (the edit form).

    Returns:
        ``(end_bound, refusal)``.  *end_bound* is the closing bound the write
        states: *end_bound* as given, or ``NEVER_ENDS`` where rule 3 makes
        the stop the loan's and the submission stated none.  *refusal* is
        ``None`` when the edit may proceed, else the redirect the caller
        returns verbatim.
    """
    moving = (
        "to_account_id" in data
        and data["to_account_id"] != template.to_account_id
    )
    if moving and is_loan_payment_or_standing(template, pass_ctx):
        flash(LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION, "danger")
        return end_bound, redirect.to_response()
    # The two edits this door settles: a cadence added to a definition that
    # had none, or a definition that repeats moved.  Neither, and the edit is
    # an ordinary one (a rename, an amount, a re-pointed cadence that stays
    # put) -- cheapest disqualifier first, so nothing below runs for it.
    rule = template.recurrence_rule
    if rule is not None and not moving:
        return end_bound, None
    # The DESTINATION is settled before the cadence is: whether it is a loan
    # is what decides that anything at all is derived, and the partial
    # submission rule 2 completes must be completed for a loan alone.  Read
    # here, once, off the destination the ownership check has already passed.
    to_account_id = data.get("to_account_id", template.to_account_id)
    params = loan_loaders.load_loan_params(to_account_id)
    if params is None:
        return end_bound, None
    unit, refusal = _cadence_unit_to_settle(rule, data, redirect=redirect)
    if unit is None:
        return end_bound, refusal
    derived = settle_loan_start(data, params=params, unit=unit)
    return _settle_stop_for_loan_destination(
        rule, params,
        starts_on=derived.starts_on, end_bound=end_bound, redirect=redirect,
    )


def _cadence_unit_to_settle(
    rule: RecurrenceRule | None,
    data: dict[str, Any],
    *,
    redirect: RedirectTarget,
) -> tuple[RecurrenceUnitEnum | None, Response | None]:
    """Return the cadence the definition repeats on after an edit this door settles.

    Rule 2's first half (:func:`settle_destination_for_update`), asked once
    the caller knows the edit is one of the two it settles AND the
    destination is a loan: on WHICH cadence the definition repeats afterwards.
    The submitted one; or, for a partial submission that moves a repeating
    definition without restating its cadence, the stored one it leaves alone,
    which is COMPLETED into *data* so the re-point branch runs and carries
    the derived start.  A submitted ``None`` is "Does not repeat": nothing
    repeats after that edit, so there is nothing to derive and no stop to
    judge (whether the clear itself is allowed is
    ``refuse_recurrence_update``'s first rule); and a one-time transfer moved
    by a partial submission still does not repeat.

    Args:
        rule: The definition's stored rule, or ``None`` when it does not
            repeat yet.
        data: The validated partial payload.  Mutated ONLY for a partial move
            of a recurring definition, where the stored cadence's three keys
            are written.
        redirect: Where the one refusal here -- a stored cadence the
            application cannot read, so cannot complete -- sends the user.

    Returns:
        ``(unit, refusal)``.  *unit* is the cadence unit the loan branch
        derives for, or ``None`` when nothing repeats after this edit;
        *refusal* is set only beside a ``None`` unit, for the
        unreadable-cadence case.
    """
    if "recurrence_unit" in data:
        return data["recurrence_unit"], None
    if rule is None:
        return None, None
    reading = stored_cadence(rule)
    if reading is None:
        flash(UNREADABLE_CADENCE_MESSAGE, "danger")
        return None, redirect.to_response()
    data["recurrence_unit"] = reading.cadence.unit
    data["interval_n"] = reading.cadence.interval_n
    data["recurrence_placement"] = reading.placement
    return reading.cadence.unit, None


def _settle_stop_for_loan_destination(
    rule: RecurrenceRule | None,
    params: LoanParams,
    *,
    starts_on: date,
    end_bound: EndBound | None,
    redirect: RedirectTarget,
) -> tuple[EndBound | None, Response | None]:
    """Settle the closing bound for the loan an edit leaves a definition paying.

    Rule 3 of :func:`settle_destination_for_update`, once that door knows the
    edit is one it settles, that the destination is a configured loan, and
    has derived the first occurrence beside it.

    Args:
        rule: The definition's stored rule, or ``None`` when the edit is the
            one that authors it; read for the stored bound an absent key
            leaves alone.
        params: The destination loan's terms, loaded by the caller; its
            ``account_id`` is the destination the route has ownership-checked.
        starts_on: The first occurrence just derived from that loan's
            contract.
        end_bound: The closing bound the submission composed, or ``None``.
        redirect: Where a refusal sends the user.

    Returns:
        ``(end_bound, refusal)`` as :func:`settle_destination_for_update`
        documents them.
    """
    to_account_id = params.account_id
    # The bound the write WOULD state.  The submission's own where it stated
    # one; else the unbounded rule where the loan derives the stop (ruling
    # R-R77) -- asked here, so the producer is read at most once on any path:
    # the first refusal below asks it only for a REAL submitted stop, and
    # this arm only for an absent one; else the stored bound the absent key
    # leaves alone (an edit that authors a rule has none).
    written = end_bound
    stop_derived = False
    if written is None:
        stop_derived = _loan_holds_no_active_payment(
            to_account_id, current_user.id,
        )
        if stop_derived:
            written = NEVER_ENDS
        elif rule is not None:
            written = end_bound_from_columns(
                rule.end_date, rule.max_occurrences,
            )
    refusal = _refuse_stops_for_loan_destination(
        to_account_id, submitted=end_bound, written=written,
        starts_on=starts_on, redirect=redirect,
    )
    if refusal is not None:
        return end_bound, refusal
    # Handed to the recurrence step as a STATED bound only where the loan
    # derives the stop: a stored bound left alone is what an absent key
    # already means one step on, and handing it back would make that step
    # read it as the owner's fresh statement.
    return (NEVER_ENDS if stop_derived else end_bound), None


@dataclass(frozen=True)
class LoanDestinationLocks:
    """What the transfer form's script may lock, and what it may not touch.

    The browser's half of the rules the two doors enforce, emitted by the
    server from the same producers the doors read so the script computes no
    domain fact -- it tests membership and nothing else.  Held as one value
    rather than three context keys so the parts cannot be passed apart, and
    with the two invariants below enforced rather than documented.

    **Emitted by BOTH transfer forms since plan step R7d-f-5** (ruling
    **R-R79**).  A CREATE form's server render cannot lock either bound row --
    the destination is chosen in the form -- which is why the sets ride to the
    browser at all.  An EDIT form knows its template: it locks both rows
    server-side for the loan's standing payment
    (:class:`~app.routes._recurrence_form_render.RecurrenceStart`,
    :class:`~app.routes._recurrence_form_render.RecurrenceEnd`), and it emits
    these sets COMPUTED FOR THAT EDIT
    (:func:`loan_destination_locks_for_edit`), because the update door derives
    the same bounds for the two edits that make a definition a recurring
    transfer into a loan (:func:`settle_destination_for_update`) and the form
    otherwise invited a start the save replaces and a stop the save refuses.
    The server answers the STORED identity, the script answers the CHOSEN
    destination against sets the server graded, and the script never
    re-enables a control the server locked (it reads ``disabled`` once at
    load and leaves that flag alone) -- the two never answer one question.

    Attributes:
        start_derived_for: The destinations a transfer into which has its
            first occurrence derived from the loan's contract
            (:func:`settle_loan_start`), so the "Starts on" row locks the
            moment one is chosen.  On a create form every configured loan of
            the owner, a second transfer into a paid loan included (plan
            ledger row **D50**); on an edit form, the loans the door would
            derive for THIS definition -- see
            :func:`loan_destination_locks_for_edit`.
        stop_derived_for: The subset holding no active recurring payment
            (:func:`_loan_holds_no_active_payment`).  A transfer into one of
            these IS the loan's payment once saved, and the loan's own payment
            carries no authored stop (ruling **R-R59**), so the "Ends" row
            locks too and the doors refuse a stop stated anyway (rulings
            **R-R60**, **R-R77**).  A loan already holding a payment is in the
            first set and not this one: a second transfer's stop is its
            owner's.  Always a subset of *start_derived_for*, and
            :meth:`__post_init__` refuses any other pair.
        pinned_reason: ``None`` where the destination is the owner's to
            change.  On an edit form of a definition ruling **R-R76** pins to
            its loan (:func:`~app.routes._recurrence_form_refusals.is_loan_payment_or_standing`),
            the refusal's own sentence
            (:data:`LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION`): the form renders
            the destination control DISABLED with it as the help text, so the
            owner meets the rule before the save rather than as its flash.  A
            pinned definition has no destination choice to grade, so both
            sets are EMPTY -- :meth:`__post_init__` refuses a pinned value
            carrying either, because a list claiming "choosing this loan
            derives the start" for a choice the door REFUSES would be the
            affordance and the door answering one question two ways.  A
            disabled control posts nothing; ``to_account_id`` is optional on
            the update schema and an absent key is "not moving", so the form
            still submits and the door still refuses a crafted move.
    """

    start_derived_for: tuple[int, ...]
    stop_derived_for: tuple[int, ...]
    pinned_reason: str | None = None

    def __post_init__(self) -> None:
        """Refuse a value whose parts disagree.

        Two invariants, enforced for the reason
        :class:`~app.routes._recurrence_form_render.RecurrenceEnd` enforces
        its own: this project has been burned by an invariant the generated
        ``__init__`` did not enforce.

        Raises:
            ValueError: A pinned value carries a non-empty set, or a
                destination derives the stop without deriving the start.
        """
        if self.pinned_reason is not None and (
            self.start_derived_for or self.stop_derived_for
        ):
            raise ValueError(
                f"LoanDestinationLocks is pinned ({self.pinned_reason!r}) yet "
                f"names destinations that would derive a bound "
                f"({self.start_derived_for!r} / {self.stop_derived_for!r}): "
                f"every move of a pinned definition is refused, not derived, "
                f"so the pair disagrees with itself."
            )
        stray = set(self.stop_derived_for) - set(self.start_derived_for)
        if stray:
            raise ValueError(
                f"LoanDestinationLocks derives the stop for {sorted(stray)!r} "
                f"without deriving the start: every loan that derives the "
                f"stop is a loan, and every loan derives the start."
            )


def loan_destination_locks(user_id: int) -> LoanDestinationLocks:
    """Return which of *user_id*'s loan destinations lock which bound row on a CREATE form.

    Every configured loan derives the start; the payment-less subset derives
    the stop as well; nothing is pinned, because a definition that does not
    exist yet has no destination to be pinned to.  The edit form's producer is
    :func:`loan_destination_locks_for_edit`; both read
    :func:`_loan_destination_lock_sets`, which is where the two sets are
    spelled.

    Args:
        user_id: The owner rendering the create form.

    Returns:
        The :class:`LoanDestinationLocks`, both sets ascending by account id
        (the loan-id loader's order, preserved).
    """
    return _loan_destination_lock_sets(user_id, excluding=None)


def loan_destination_locks_for_edit(
    template: TransferTemplate, pass_ctx: BalanceContext,
) -> LoanDestinationLocks:
    """Return what the EDIT form of *template* may lock, and whether it may move.

    The sets :func:`settle_destination_for_update` would act on for THIS
    definition, decided in that door's own order so a reader can check the
    two against each other line by line (plan step R7d-f-5, ruling
    **R-R79**):

    1. A definition ruling **R-R76** pins to its loan -- the standing payment,
       or one carrying loan-payment settings -- may not move at all, so it
       has no destination choice to grade: both sets are empty and
       ``pinned_reason`` carries the refusal's sentence for the disabled
       control (the door's rule 1).
    2. A definition that already REPEATS derives nothing while it stays put --
       its stored start is its own, a second transfer into a paid loan's
       included (plan ledger row **D50**) -- so its stored destination leaves
       the sets, and every other loan stays: a MOVE onto one derives the start
       (the door's early return, then its rule 2).
    3. A definition that does not repeat yet is about to AUTHOR a rule, and
       the door derives for whatever loan the edit leaves it paying into,
       the stored one included -- so every configured loan stays (the door's
       rule 2 for the authoring shape).

    The standing payment's own rows are locked server-side whatever this
    answers, and a settings-carrying second payment's are open; for both the
    answer is "pinned", so the script is handed nothing to apply on a form
    whose move the save refuses.

    Args:
        template: The definition being edited, owner-checked by the route.
            Read for its stored destination, its rule and its settings row.
        pass_ctx: The read pass the route built for the render, whose
            loan-resolution memo answers the standing identity -- the same
            read :func:`~app.routes._recurrence_form_render.edit_form_recurrence_state`
            makes, so the second is free -- and whose ``user_id`` is the owner
            (a producer below the route takes the pass and no separate id,
            the 2026-08-16 ruling).

    Returns:
        The :class:`LoanDestinationLocks` for this edit.
    """
    if is_loan_payment_or_standing(template, pass_ctx):
        return LoanDestinationLocks(
            start_derived_for=(), stop_derived_for=(),
            pinned_reason=LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION,
        )
    excluding = (
        template.to_account_id if template.recurrence_rule is not None else None
    )
    return _loan_destination_lock_sets(pass_ctx.user_id, excluding=excluding)


def _loan_destination_lock_sets(
    user_id: int, *, excluding: int | None,
) -> LoanDestinationLocks:
    """Return the two sets for *user_id*'s loans, less *excluding*.

    One lookup per configured loan for the second set, through the producer
    that states the "active recurring transfer into this account" filter once
    (:func:`~app.services.recurring_transfer_query.active_recurring_transfer_templates`)
    rather than a second query spelling the same filter without the account
    clause.  The owner's loans are a handful; the cost is one query each on
    a form GET, carrying that producer's two eager loads (the settings row
    and the price series) for a boolean -- the price of one spelling.

    Args:
        user_id: The owner rendering the form.
        excluding: A destination to leave out of both sets -- the stored one
            of a definition that already repeats, which derives nothing while
            it stays put -- or ``None`` to grade every loan.

    Returns:
        The :class:`LoanDestinationLocks`, unpinned, both sets ascending by
        account id (the loan-id loader's order, preserved).
    """
    loan_ids = [
        account_id
        for account_id in loan_loaders.load_loan_account_ids_for_user(user_id)
        if account_id != excluding
    ]
    return LoanDestinationLocks(
        start_derived_for=tuple(loan_ids),
        stop_derived_for=tuple(
            account_id for account_id in loan_ids
            if _loan_holds_no_active_payment(account_id, user_id)
        ),
    )


__all__ = [
    "LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION",
    "LoanDestinationLocks",
    "loan_destination_locks",
    "loan_destination_locks_for_edit",
    "settle_destination_for_update",
    "settle_first_occurrence",
    "settle_loan_start",
]
