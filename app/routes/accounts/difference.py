"""
Shekel Budget App -- The true-up form's DIFFERENCE preview

Ruling **R-EU** (plan step X-f2-a): the balance editor names what the
account's RECORDS produce for the day the form is about, what the user typed,
and the gap between them, BEFORE Save.

**Why it is not in :mod:`app.routes.accounts.anchor`.**  It was, and the
subject boundary was always there to cut along: ``anchor`` owns the WRITE DOOR
for a balance assertion -- form validation, the mutation, the rendered
refusals -- while everything here is a read-only PREVIEW of a save that has
not happened.  It writes nothing, it answers a GET, and its whole output is a
comparison.  The same cut :mod:`app.routes.accounts.reconcile` made one step
earlier, along the same seam.

**What forced the cut now is the 1000-line module ceiling**, which plan step
X-f2-b's acknowledgement re-key pushed ``anchor`` past (1023).  Findings
**N-152**, **N-156** and **N-201** record the same ceiling on three SERVICE
modules and rule the same answer -- a split, never another round of shaving
prose off a measured claim.  X-f2-a's own commit predicted this one: its
difference-preview family is what took ``anchor`` from 916 to the ceiling in
the first place.

The bodies are unchanged.  Two names stayed in ``anchor`` and are imported
here rather than copied: ``LOAN_ANCHOR_REFUSAL`` is the write door's own
copy for "a loan's balance is not a cash anchor" (ruling D4 / step A1, finding
B-15), and a message with readers in two modules is part of the interface --
finding **N-33**'s shape stated rather than fenced by convention.

The KIND TEST is not imported, and that is this step's review talking.  The
cut first promoted ``anchor._is_amortizing`` to public so both modules could
share it; that helper was ``classify_account(account) is AMORTIZING`` spelled a
second time, including its ``account_type is None`` case, so the cut was
widening a duplicate's visibility instead of deleting it.  Both modules ask the
shipped classifier now, which is the same door ``records_balance_at`` already
dispatches on.

Every URL, decorator, ownership check and template contract is preserved; the
blueprint and endpoint names are unchanged, so no ``url_for`` call site moved.
"""

from datetime import date
from decimal import Decimal

from flask import render_template, request
from flask_login import current_user, login_required

from app.exceptions import ValidationError
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts.anchor import LOAN_ANCHOR_REFUSAL
from app.services import anchor_service, balance_at
from app.services.account_projection import (
    AccountProjectionKind,
    classify_account,
)
from app.utils.account_validation import _anchor_schema
from app.utils.auth_helpers import get_or_404, require_owner

#: What the difference preview says when the date box holds something that is
#: not a day.  Its own sentence rather than the schema's, which names the FIELD
#: (``observed_on``) the user has never seen -- the form labels that box
#: "Balance as of".
_UNREADABLE_DAY = "Enter a date to compare this balance against."


def _preview_submission() -> tuple[Decimal | None, date | None, str | None]:
    """Parse the editor's two boxes LENIENTLY, through the write door's schema.

    The preview fires while the user is still typing, so it must answer a
    half-finished form rather than refuse one -- and it must nonetheless read
    both boxes by exactly the rules ``true_up`` will apply, or it would preview
    a figure the save then rejects.  Sharing ``_anchor_schema`` is what makes
    that structural rather than a claim: the cent quantization
    (``fields.Decimal(places=2)``) and the empty-string normalizer that lets a
    cleared date box mean "today" are the SAME code both paths run.  The whole
    query string is handed in, exactly as ``true_up`` hands in the whole form:
    ``BaseSchema.Meta.unknown = EXCLUDE`` drops what the schema does not
    declare, so a hand-kept list of field names here would be a SECOND copy of
    the schema's own -- able to drift in one direction only, which is the shape
    this step exists to remove.

    Two leniencies, each for a state ordinary typing produces:

    * **A balance box that does not parse yet** -- mid-keystroke, or cleared --
      is ``None`` rather than an error.  ``partial=`` is what expresses that:
      the field is ``required=True`` for the save, and asking the schema to
      relax exactly that one requirement is narrower than a second parser.
    * **A blank date box** is ``None``, which the caller resolves to the user's
      today through the same :func:`~app.services.anchor_service.resolve_observation_day`
      the write door uses (ruling R-ER).  The default is NOT applied here: a
      route that invented the day would be the second answer to "when is an
      assertion dated" that ruling deleted.

    An UNPARSEABLE DATE is the one thing that is refused rather than defaulted,
    and the difference matters: blank means "now", while ``2026-13-40`` means
    the user is mid-edit on a day that is not a day, and silently previewing
    today's figure under a date box showing something else would caption a
    figure with the wrong day -- this arc's own root defect, rendered.

    Returns:
        ``(recorded, submitted_day, refusal)``.  ``refusal`` is ``None`` unless
        the DATE box holds something unparseable, in which case both figures are
        ``None`` and the message is ready to render.
    """
    raw = dict(request.args)
    errors = _anchor_schema.validate(raw)
    if "observed_on" in errors:
        # Not ``flatten_schema_errors``: that renders "observed_on: Not a valid
        # date." -- a schema FIELD NAME, under a box the user sees labelled
        # "Balance as of".  The editor's own rejection surface can afford the
        # field name because it re-renders the labelled input beside it; a
        # caption floating under the form cannot.
        return None, None, _UNREADABLE_DAY

    if "anchor_balance" in errors:
        raw.pop("anchor_balance", None)
    data = _anchor_schema.load(raw, partial=("anchor_balance",))
    balance = data.get("anchor_balance")
    return (
        None if balance is None else Decimal(str(balance)),
        data.get("observed_on"),
        None,
    )


#: What a difference MEANS, as a name the template maps to copy rather than
#: re-deriving from the sign.  The verdict is decided in Python -- where it is
#: under test -- for the reason ``pace_pill`` / ``dti_badge`` take a computed
#: label rather than a number: a ``{% elif %}`` chain over a Decimal is a
#: SECOND statement of the sign convention, in the one language this project
#: forbids financial reasoning in.
DIFF_AGREES = "agrees"
DIFF_UNRECORDED_SPEND = "unrecorded_spend"
DIFF_UNACCOUNTED_MONEY = "unaccounted_money"

#: The same three names, handed to the partial so its branches compare against
#: THESE values rather than against re-typed literals.  A template literal and a
#: module constant are two spellings of one name that drift silently -- the
#: partial would simply stop matching and fall to its else arm, rendering the
#: wrong sentence with no error anywhere.
#:
#: **PUBLIC since plan step balance:X-f3c-3**, with :func:`difference_verdict`
#: beside it, because a second route module asks the same QUESTION OF A SIGN:
#: ``accounts.outstanding``'s card names what an account's latest declaration
#: says its books cannot explain, and "declared above the records" means money
#: unaccounted for on both surfaces.  A helper with consumers in two modules is
#: part of this module's interface -- finding **N-33**'s shape stated rather
#: than fenced by a convention nobody can enforce, exactly as
#: ``reconcile.panel_id`` says one module over.
#:
#: **What is shared is the CLASSIFIER and never the figure**, and a first draft
#: of this note got that wrong by calling the card's figure "the same
#: ``declared - records`` sign asked cumulatively rather than per save".  It is
#: not the same subtraction: this preview measures against
#: :func:`~app.services.balance_at.records_balance_at`, which is the running
#: balance just before the day's assertion and therefore contains every EARLIER
#: assertion's reset, while the card measures against the records alone.  The
#: two differ by the net of every prior correction, so **each surface writes its
#: own sentences** -- publishing two money figures under one vocabulary is the
#: defect this arc has now measured three times.
DIFF_VERDICT_NAMES = {
    "DIFF_AGREES": DIFF_AGREES,
    "DIFF_UNRECORDED_SPEND": DIFF_UNRECORDED_SPEND,
    "DIFF_UNACCOUNTED_MONEY": DIFF_UNACCOUNTED_MONEY,
}


def difference_verdict(difference: Decimal) -> str:
    """Return what *difference* MEANS, as one of the ``DIFF_*`` names.

    The sign convention is ``recorded - records``, so:

    * **negative** -- the bank holds LESS than the records account for: money
      left that was never recorded, or a payment that moved earlier than it was
      budgeted to.
    * **positive** -- the bank holds MORE: income not recorded, or a budgeted
      bill that never left.
    * **zero** -- the records agree with the statement, which is the outcome the
      user is aiming for and which reads as an empty state unless it is named.

    Args:
        difference: ``recorded - records``, cent-exact.

    Returns:
        One of :data:`DIFF_AGREES`, :data:`DIFF_UNRECORDED_SPEND`,
        :data:`DIFF_UNACCOUNTED_MONEY`.
    """
    if difference == 0:
        return DIFF_AGREES
    return (
        DIFF_UNRECORDED_SPEND if difference < 0 else DIFF_UNACCOUNTED_MONEY
    )


def _anchor_difference_context(account: Account) -> dict:
    """Assemble the difference preview's context for one account.

    Ruling **R-EU**: the form shows what the account's RECORDS produce for the
    day it names, what the user typed, and the gap -- before Save.

    **The figure is ``balance_at.records_balance_at``, not the account's current
    balance, and that is the whole correction this leaf was rebuilt for.**  An
    assertion RESETS the cash walk, so "what the app reports for that day"
    becomes the user's own declared figure the moment they declare one -- and a
    difference measured against it is zero by construction, or on a CORRECTION
    is the gap between two successive guesses with a sign that can oppose the
    real one (measured on production Checking 2026-04-15: ``-$45.86`` against
    the previous entry, ``-$92.29`` against the records).

    Four states, each a real one:

    * **a refusal** -- an amortizing loan (a raced kind change; see
      :func:`_anchor_kind_refusal`), or a day that cannot be asserted.
      Previewing a save guaranteed to be refused is the dead-end affordance
      ruling R-ET's corollary deletes, so the region says why instead.
    * **a MODELLED account** -- ``records_balance_at`` answers ``None``, because
      an HYSA accruing interest or a Property appreciating is not being
      reconciled against a bank statement (see that function for the scope
      argument and finding **N-213**).  The region renders empty rather than
      captioning a model-vs-market gap as untracked spend.
    * **a reconciliation** -- the ordinary state.
    * **nothing typed yet** -- the balance box holds nothing parseable, so there
      is no comparison to draw.

    **What it deliberately does NOT do is preview the form's PREFILL.**  The
    editor opens with the governing balance already in the box (ruling R-EE's
    one-click habit), so a preview fired on form-open would compare a figure the
    user has not entered against the records and caption the gap as the app's
    fault -- reproduced by an adversarial review: a settled, fully posted
    ``$150.00`` expense rendered as "money Shekel has not accounted for", and
    pressing Enter on that prefill drops the ``$150.00`` out of the projection.
    The region is therefore mounted EMPTY and fills on the first real edit; see
    ``grid/_anchor_edit.html`` for the trigger set that expresses it.

    Args:
        account: The owned, attached :class:`Account` being previewed.

    Returns:
        The template context: ``refusal`` (str or ``None``), and when there is a
        comparison to draw, ``observed_on`` / ``records`` / ``recorded`` /
        ``difference`` / ``verdict``.  ``difference`` is ``None`` whenever there
        is nothing to compare.
    """
    empty = {"refusal": None, "difference": None}
    if classify_account(account) is AccountProjectionKind.AMORTIZING:
        return {"refusal": LOAN_ANCHOR_REFUSAL, "difference": None}

    recorded, submitted_day, refusal = _preview_submission()
    if refusal is not None:
        return {"refusal": refusal, "difference": None}
    try:
        day = anchor_service.resolve_observation_day(
            current_user.id, submitted_day,
        ).civil_day
    except ValidationError as exc:
        return {"refusal": str(exc), "difference": None}
    if recorded is None:
        return empty

    records = balance_at.records_balance_at(
        account, balance_at.BalanceContext.build(current_user.id), day,
    )
    if records is None:
        return empty

    difference = recorded - records
    return {
        "refusal": None,
        "observed_on": day,
        "records": records,
        "recorded": recorded,
        "difference": difference,
        "verdict": difference_verdict(difference),
    }


@accounts_bp.route(
    "/accounts/<int:account_id>/anchor-difference", methods=["GET"],
)
@login_required
@require_owner
def anchor_difference(account_id):
    """HTMX partial: the true-up editor's recorded-vs-ledger difference.

    Ruling **R-EU** (plan step X-f2-a).  A GET because it WRITES NOTHING: it
    reads the two boxes off the query string, asks the balance seam what the
    account was worth on the day they name, and renders the comparison.

    **It is fetched by the editor rather than rendered with it.**
    ``anchor_form`` is one click on the grid, per account, and today it costs
    two cheap reads (``cash_ledger.resolve_anchor`` and the schedule floor);
    :func:`~app.services.balance_at.records_balance_at` assembles the account's
    whole event stream, which is dashboard-sized work.  Keeping it out of the
    editor's own render leaves the primary control as fast as it is now.  That
    is the FIRST-PAINT trade; it does not make the total cheaper, and the honest
    claim is the narrow one.

    **The preview cannot go stale against its FORM**: the region re-fetches on
    every change to the form it lives in, and the response carries the day it
    was computed for, so a figure and its caption move together.  It CAN go
    stale against the ledger -- another tab's true-up, or the reconcile panel's
    own POST, both fire ``balanceChanged``, which this region deliberately does
    not listen for (re-folding on every balance event would cost a fold per
    event for a figure nobody is looking at unless the editor is open).
    """
    account = get_or_404(Account, account_id)
    if account is None:
        return "Account not found", 404
    return render_template(
        "accounts/_anchor_difference.html",
        **DIFF_VERDICT_NAMES,
        **_anchor_difference_context(account),
    )
