"""
Shekel Budget App -- Recurring-Transfer Creation Route Helpers

Shared building blocks for the routes that spin up a recurring
TransferTemplate and seed its Transfer instances:

* :func:`app.routes.investment.create_contribution_transfer` -- a
  biweekly contribution transfer into an investment / retirement
  account.
* :func:`app.routes.loan.payment_transfer.create_payment_transfer` -- a monthly P&I +
  escrow payment transfer into a debt account.
* :func:`app.routes.transfers.templates.create_transfer_template` /
  :func:`app.routes.transfers.templates.unarchive_transfer_template` -- the
  generic transfer-template create / restore paths.

Those routes were near-forks: the investment and loan creators ran a
byte-identical validate -> verify-source-account -> build-rule ->
build-template -> flush -> generate -> commit skeleton, diverging only
in the amount derivation, the recurrence pattern, the template name,
and the user-facing copy.  The four helpers here capture the shared
steps so each route keeps only its genuinely-distinct middle.

Since plan step R7c-b the module also holds what a LOAN destination decides
about a recurring transfer's validity bounds, at BOTH of the generic transfer
form's doors: the create door (:func:`settle_first_occurrence`; rulings
**R-R60** and **R-R74** at plan step R7d-f-3) and, since plan step R7d-f-4,
the update door (:func:`settle_destination_for_update`; rulings **R-R76** and
**R-R77**).  The two share one derivation of the first occurrence
(:func:`settle_loan_start`), one reading of whether the loan holds a payment
(:func:`_loan_holds_no_active_payment`) and one ordered pair of closing-bound
refusals (:func:`_refuse_stops_for_loan_destination`), so an edit cannot
author what a create refuses (plan ledger row **REC-521**).

Route-layer module (leading underscore = route-internal) rather than a
service because every helper consumes Flask globals (``request``,
``flash``, ``redirect``, ``url_for``, ``current_user`` -- the redirect /
url_for pair via :class:`~app.routes._redirect_target.RedirectTarget`);
``CLAUDE.md::Architecture`` keeps services isolated from Flask.  None of
these helpers create or mutate transfer shadow transactions directly --
shadow atomicity stays inside ``transfer_recurrence.generate_for_template``
and ``transfer_service`` -- so the transfer invariants are unaffected by
routing a call through this module.
"""
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from flask import Response, abort, flash, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.enums import RecurrenceUnitEnum
from app.extensions import db
from app.models.account import Account
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_form_refusals import (
    LOAN_PAYMENT_BOUND_IS_DERIVED,
    is_loan_payment,
)
from app.routes._redirect_target import RedirectTarget
from app.schemas.validation import (
    RECURRENCE_END_BOUND_KEY,
    RECURRENCE_NEEDS_A_START,
    RECURRENCE_NOMINAL_DAY_KEY,
    RECURRENCE_STARTS_ON_KEY,
    end_bound_before_start_message,
)
from app.models.loan_params import LoanParams
from app.models.recurrence_rule import RecurrenceRule
from app.services import loan_loaders, loan_recurrence_sync, transfer_recurrence
from app.services.balance_at import BalanceContext, is_standing_loan_payment
from app.services.generation_schedule import GenerationSchedule
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
from app.utils.auth_helpers import get_or_404

logger = logging.getLogger(__name__)


# Canonical name-collision flash for the partial-unique transfer-template
# name index.  Shared so the wording stays identical across every flush
# site (coding-standards DRY); the create-template path overrides it with
# its own non-"recurring" wording.
TRANSFER_NAME_DUP_MESSAGE: str = (
    "A recurring transfer with that name already exists."
)

# Shared validation-failure flash for the contribution / payment transfer
# forms.  Byte-identical between the investment and loan creators
# pre-extraction.
_TRANSFER_VALIDATION_FLASH: str = "Please correct the errors and try again."


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


def validate_and_resolve_source_account(
    schema: Any,
    *,
    dest_account_id: int,
    redirect: RedirectTarget,
) -> tuple[Account, dict[str, Any]] | Response:
    """Validate a transfer form and resolve + check its source account.

    Shared head of
    :func:`app.routes.investment.create_contribution_transfer` and
    :func:`app.routes.loan.payment_transfer.create_payment_transfer`.  Runs the four
    pre-conditions both routes enforce before building anything:

    1. The submitted form validates against ``schema``.
    2. The ``source_account_id`` it carries resolves to a row owned by
       the current user (``get_or_404`` -> ``abort(404)`` for both
       not-found and not-yours, per the security response rule).
    3. The source account is active.
    4. The source account is not the destination account.

    Args:
        schema: An instantiated Marshmallow schema exposing
            ``source_account_id`` and an optional ``amount`` (the
            investment / loan transfer schemas).  Validated and loaded
            against ``request.form``.
        dest_account_id: The destination account id from the route URL,
            compared against the submitted source to reject self-transfers.
        redirect: Where to redirect on any recoverable validation
            failure -- invalid form, inactive source, or self-transfer
            (each route's own dashboard).

    Returns:
        * ``(source_account, data)`` -- the owned, active source
          :class:`Account` and the loaded payload, when every check
          passes.
        * :class:`Response` -- a Flask redirect to ``redirect`` for a
          recoverable failure (invalid form, inactive source,
          self-transfer); the caller returns it directly.

    Raises:
        werkzeug.exceptions.NotFound: via ``abort(404)`` when the source
            account does not exist or is not owned by the current user.
    """
    errors = schema.validate(request.form)
    if errors:
        flash(_TRANSFER_VALIDATION_FLASH, "danger")
        return redirect.to_response()

    data = schema.load(request.form)
    source_account_id = data["source_account_id"]

    source_account = get_or_404(Account, source_account_id)
    if source_account is None:
        abort(404)

    if not source_account.is_active:
        flash("Source account is inactive.", "danger")
        return redirect.to_response()

    if source_account_id == dest_account_id:
        flash("Source and destination accounts must be different.", "danger")
        return redirect.to_response()

    return source_account, data


def build_recurring_transfer_template(
    *,
    source_account: Account,
    dest_account: Account,
    name: str,
    default_amount: Decimal,
) -> TransferTemplate:
    """Construct + session-add a recurring :class:`TransferTemplate`.

    Shared template-construction step of the investment and loan
    transfer creators.  Builds the row from the resolved accounts, adds it to
    the session, and returns it; the caller flushes (via
    :func:`flush_template_or_namedup_redirect`) so name-collision
    handling stays at the route layer.

    **It no longer takes the recurrence rule, since plan step R-F6.**  The rule
    carries the owning FK now, so it is authored ONTO this template
    (:func:`app.services.recurrence.author_rule`) once the template exists --
    which is after the caller's name-collision flush, because ``author_rule``
    flushes and an earlier one would surface a duplicate name as an unhandled
    ``IntegrityError``.  A template returned from here does not repeat yet, and
    both callers make it repeat one call later.

    Loan-payment settings are intentionally NOT set here: an investment
    contribution and every generic transfer get NO
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row, so every
    reader defaults them to non-derive (decision B).  The loan-payment creator
    -- the only caller that needs it -- attaches ``template.settings`` itself on
    the returned row before the flush, keeping that loan-only concern at the
    loan call site.

    Args:
        source_account: The owned, active funding account
            (``from_account``).
        dest_account: The investment / loan destination account
            (``to_account``).
        name: Display name for the template.
        default_amount: Per-period transfer amount (Decimal).

    Returns:
        The added (not yet flushed) :class:`TransferTemplate`, with no
        loan-payment settings row (the caller attaches one only for a loan
        payment).
    """
    template = TransferTemplate(
        user_id=current_user.id,
        from_account_id=source_account.id,
        to_account_id=dest_account.id,
        name=name,
        default_amount=default_amount,
    )
    db.session.add(template)
    return template


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
    **R-R77**) and the form's affordance (:func:`loan_destination_locks`).
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
       the union its twin refusal covers.  Asked first, and of the stored
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
    if moving and (
        is_loan_payment(template) or is_standing_loan_payment(template, pass_ctx)
    ):
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
    """Which destinations the CREATE form's script locks a bound row for.

    The browser's half of two rules the door enforces, emitted by the server
    from the same producers the door reads so the script computes no domain
    fact -- it tests membership and nothing else.  Held as one value rather
    than two context keys so the two sets cannot be passed one without the
    other.  A CREATE form's server render cannot lock either row (the
    destination is chosen in the form), which is why the sets ride to the
    browser at all; an EDIT form knows its template and locks server-side
    through :class:`~app.routes._recurrence_form_render.RecurrenceStart` and
    :class:`~app.routes._recurrence_form_render.RecurrenceEnd`, and emits
    neither set.

    Attributes:
        start_derived_for: Every configured loan of the owner.  A transfer
            into any of them has its first occurrence derived from the loan's
            contract (:func:`settle_first_occurrence`, plan step R7c-b) -- a
            second transfer into a paid loan included (plan ledger row
            **D50**) -- so the "Starts on" row locks the moment one is
            chosen.
        stop_derived_for: The subset holding no active recurring payment
            (:func:`_loan_holds_no_active_payment`).  A transfer into one of
            these IS the loan's payment once created, and the loan's own
            payment carries no authored stop (ruling **R-R59**), so the
            "Ends" row locks too and the door refuses a stop stated anyway
            (ruling **R-R60**).  A loan already holding a payment is in the
            first set and not this one: a second transfer's stop is its
            owner's.
    """

    start_derived_for: tuple[int, ...]
    stop_derived_for: tuple[int, ...]


def loan_destination_locks(user_id: int) -> LoanDestinationLocks:
    """Return which of *user_id*'s loan destinations lock which bound row.

    One lookup per configured loan for the second set, through the producer
    that states the "active recurring transfer into this account" filter once
    (:func:`~app.services.recurring_transfer_query.active_recurring_transfer_templates`)
    rather than a second query spelling the same filter without the account
    clause.  The owner's loans are a handful; the cost is one query each on
    a form GET, carrying that producer's two eager loads (the settings row
    and the price series) for a boolean -- the price of one spelling.

    Args:
        user_id: The owner rendering the create form.

    Returns:
        The :class:`LoanDestinationLocks`, both sets ascending by account id
        (the loan-id loader's order, preserved).
    """
    loan_ids = loan_loaders.load_loan_account_ids_for_user(user_id)
    return LoanDestinationLocks(
        start_derived_for=tuple(loan_ids),
        stop_derived_for=tuple(
            account_id for account_id in loan_ids
            if _loan_holds_no_active_payment(account_id, user_id)
        ),
    )


def flush_template_or_namedup_redirect(
    *,
    redirect: RedirectTarget,
    name_dup_message: str = TRANSFER_NAME_DUP_MESSAGE,
) -> Response | None:
    """Flush the session, translating a name-collision into flash+redirect.

    Wraps the
    ``try: db.session.flush() except IntegrityError`` idiom the transfer
    creators / updaters share, where the partial-unique index on the
    template name surfaces a concurrent or duplicate name as an
    :class:`IntegrityError`.  Rolls back and converts it into the
    canonical "name already exists" flash + redirect rather than a 500.

    Args:
        redirect: Where to redirect on collision.
        name_dup_message: Flash text for the collision; defaults to
            :data:`TRANSFER_NAME_DUP_MESSAGE`.  The generic create-
            template path passes its own non-"recurring" wording.

    Returns:
        * ``None`` -- the flush succeeded; the caller continues.
        * :class:`Response` -- the collision redirect; the caller
          returns it directly.
    """
    try:
        db.session.flush()
        return None
    except IntegrityError:
        db.session.rollback()
        flash(name_dup_message, "warning")
        return redirect.to_response()


def generate_transfers_for_all_periods(
    template: TransferTemplate,
    *,
    effective_from=None,
) -> None:
    """Seed a template's Transfer instances across the user's pay periods.

    The shared ``resolve baseline scenario -> load the owner's schedule ->
    transfer_recurrence.generate_for_template`` idiom used by the
    investment / loan / transfers create paths (and the unarchive
    restore path).  Shadow-transaction atomicity is owned by
    ``generate_for_template``; this helper only orchestrates its inputs.

    **It REQUIRES the baseline scenario (ruling R-BW), and the silent no-op it
    replaces was ledger row F-9.**  Every caller is a CREATE that reports
    success to the user afterwards, so "generate nothing and return normally"
    told them a recurring transfer existed that did not -- the same outcome the
    adjacent missing-period branch is written to refuse
    (``transfers/_instances.ONE_TIME_TRANSFER_NEEDS_PERIOD``).  The raise is
    answered by the one application-level handler, which rolls the pending
    create back and renders the repair.

    **The scenario and the schedule come off ONE read pass since plan step
    R7d-c-1**, where they were a ``require_baseline_scenario`` beside a
    ``calendar_for``.  ``BalanceContext.scenario_id`` makes the same R-BW
    refusal with the same exception, so the raise below is unchanged.

    Args:
        template: The flushed :class:`TransferTemplate` whose recurrence
            rule drives generation.
        effective_from: Optional lower bound passed through to
            ``generate_for_template``; ``None`` (the default) generates
            across every period, matching the create paths, while the
            unarchive path passes ``date.today()`` to fill only forward.

    Raises:
        BaselineMissingError: When the owner has no baseline scenario, so
            there is nothing to generate INTO.  Unreachable through any door
            today -- registration writes one and nothing deletes one -- which
            is why this changes no live behaviour.
    """
    ctx = BalanceContext.build(current_user.id)
    # Dereferenced BEFORE the schedule is built, so the R-BW refusal still
    # runs ahead of any derivation -- argument evaluation is left to right, so
    # naming it inline would derive the owner's calendar and then raise.
    scenario_id = ctx.scenario_id
    transfer_recurrence.generate_for_template(
        template,
        GenerationSchedule.for_pass(ctx),
        scenario_id,
        effective_from=effective_from,
    )


__all__ = [
    "LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION",
    "LoanDestinationLocks",
    "loan_destination_locks",
    "settle_destination_for_update",
    "settle_first_occurrence",
    "settle_loan_start",
    "TRANSFER_NAME_DUP_MESSAGE",
    "validate_and_resolve_source_account",
    "build_recurring_transfer_template",
    "flush_template_or_namedup_redirect",
    "generate_transfers_for_all_periods",
]
