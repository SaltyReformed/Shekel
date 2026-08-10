"""
Shekel Budget App -- Outstanding-purchase reconcile routes

The "which of these has your bank actually taken?" panel: its shared
context builder, the two DOM-id helpers its two mounts share, and its
GET / POST endpoints.

**Why it is not in :mod:`app.routes.accounts.anchor`.**  It was, and the
subject boundary was always there to cut along: ``anchor`` owns the WRITE
DOOR for a balance assertion -- form validation, the mutation, the
rendered refusals -- while everything here is about what is still
OUTSTANDING against an assertion that already landed.  ``anchor`` calls
:func:`prompt_fragment` after a successful true-up and that is the whole
of the coupling.

**Stated precisely, because a first version of this paragraph overstated
it**: ``anchor.py`` stood at 916 of pylint's 1000-line ceiling before plan
step X-f2-a, so the pressure was not pre-existing -- that step's own
difference-preview family is what reached the ceiling.  The split is
therefore the RIGHT cut made at the moment something forced a cut, not a
remedy for an already-binding gate.  Findings **N-152**, **N-156** and
**N-201** record the same ceiling on three SERVICE modules and rule the
same answer (a split, never a fourth round of shaving prose off a measured
claim); this is that answer applied a tier up.

**Two names left this module PUBLIC on the way out, deliberately.**
``app.routes.accounts.detail`` imported ``_panel_id`` and
``reconcile_context`` across module boundaries while the first was
underscore-private -- finding **N-33**'s shape, a private name that four
route modules reach for, so the name lies about its visibility.  A helper
with consumers in two modules is part of this module's interface;
:func:`panel_id` says so rather than being fenced by a convention nobody
can enforce.

Services boundary: this module owns the HTTP-shaped concerns (ownership
checks, form parsing, fragment rendering, ``HX-Trigger`` composition) and
delegates every read and write to :mod:`app.services.reconcile_service` and
:mod:`app.services.cash_ledger`.
"""

from flask import render_template, request
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import (
    cash_detail_wrong_type,
    load_cash_account_or_404,
)
from app.services import cash_ledger, reconcile_service
from app.utils.auth_helpers import require_owner
from app.utils.digit_strings import parse_row_ids


def panel_id(account_id: int) -> str:
    """Return the reconcile panel's DOM id for one account.

    Stated once so the POST's re-render lands on whichever copy of the panel
    submitted it: the modal prompt roots at a fixed id, the detail page's
    section at a per-account one, and both post to the same route.

    PUBLIC because it has consumers in two modules -- this one and
    ``app.routes.accounts.detail``, which renders the detail page's copy of the
    panel.  It was ``_panel_id`` and was imported across that boundary anyway,
    which is finding **N-33**'s shape: a name whose underscore claims a
    visibility its call sites do not respect.

    Args:
        account_id: The account whose panel id to compose.

    Returns:
        The DOM id string.
    """
    return f"reconcile-panel-{account_id}"


def reconcile_context(account: Account, panel: str) -> dict:
    """Assemble the reconcile panel's context for one account.

    The ONE builder both surfaces read: the post-true-up prompt
    (:func:`prompt_fragment`) and the permanent section on the cash
    account's detail page.  Two builders would be two answers to "what is still
    outstanding on this account", which is the shape plan step S1-c exists to
    remove.

    Args:
        account: The account to reconcile.  Caller owns the ownership check.
        panel: The DOM id the inner partial roots at, so a POST's re-render
            swaps in place.  Named ``panel`` rather than ``panel_id`` because
            :func:`panel_id` is now a module-level function and a parameter
            shadowing it would make the two indistinguishable at a glance.

    Returns:
        The template context.  ``outstanding`` is an
        :class:`~app.services.reconcile_service.OutstandingSet`, and it reports
        itself empty (the partial says so) for an account with nothing to
        reconcile or no assertion at all.
    """
    # The raw DAY, and both uses below are why the boundary offers it: an SQL
    # bound on the offer set, and a rendered caption.  Neither asks whether a
    # movement is inside the balance -- that question has one implementation
    # (``ReconciledThrough.covers``) and neither of these is a second one.
    observed_on = cash_ledger.reconciled_through(account.id).observed_day
    # An account with no assertion has nothing for a purchase to be INSIDE of,
    # so the empty set is built here rather than passed a sentinel day.  The
    # producer is never asked about a ``None`` day, which is what keeps its
    # ``observed_on`` non-optional.
    outstanding = (
        reconcile_service.OutstandingSet.empty()
        if observed_on is None
        else reconcile_service.outstanding_set(
            current_user.id, account.id, observed_on,
        )
    )
    return {
        "account": account,
        "outstanding": outstanding,
        "reconciled_through": observed_on,
        "anchor_balance": cash_ledger.resolve_anchor(account).balance,
        "panel_id": panel,
    }


def prompt_fragment(account: Account) -> str:
    """Return the out-of-band reconcile prompt, or ``""`` when there is none.

    Appended to a successful true-up's body so the modal lands in
    ``#modal-mount`` (``base.html``) whichever of the five surfaces opened the
    editor.  It is empty whenever the account has nothing outstanding, which is
    the steady state for a user who reconciles as they go -- the one-click
    true-up habit is not taxed by a prompt with nothing in it.

    PUBLIC for the same reason :func:`panel_id` is: its one caller
    (``app.routes.accounts.anchor._true_up_success_response``) is in another
    module, so an underscore here would be finding **N-33**'s shape rather than
    a boundary.

    **It is the panel's THIRD door and it takes the same kind gate** (finding
    **N-216**), which a first version of that fix missed because the finding
    named the two ROUTES.  The anchor editor opens on every kind except an
    amortizing one, so a Property or 401(k) true-up reached here -- and once
    the two routes 404'd those kinds, the modal still rendered with its
    checkboxes while its submit button POSTed to a door that refused.  htmx
    swaps only 2xx, so the button did nothing, silently and forever: strictly
    worse than before the gate, and the exact failure
    ``app/error_handlers.py`` already refuses to ship for a mutating fragment.
    Gating here rather than widening the two routes is what makes
    ``_reconcile_modal.html``'s own promise -- *"dismissing it loses nothing:
    the same list is a permanent section on the account's detail page"* --
    TRUE, because that page 404s these kinds too.

    **The consequence is stated rather than discovered**: an entry recorded
    against a transaction on one of those accounts has no reconcile surface at
    all and its reservation releases only through ``entry_service.update_entry``.
    Measured on production 2026-08-10: all 82 entries, and all 59 outstanding
    ones, are on Checking, so the set this closes is empty today.

    Args:
        account: The just-asserted account.

    Returns:
        The rendered fragment, or ``""`` -- for an account with nothing
        outstanding, and for one this panel does not serve.
    """
    if cash_detail_wrong_type(account):
        return ""
    context = reconcile_context(account, panel="reconcile-panel-modal")
    if context["outstanding"].is_empty:
        return ""
    return render_template("accounts/_reconcile_modal.html", **context)


@accounts_bp.route(
    "/accounts/<int:account_id>/reconcile", methods=["GET"],
)
@login_required
@require_owner
def reconcile_panel(account_id):
    """HTMX partial: re-render the account's outstanding-purchase list.

    The ``balanceChanged`` refresh target on the cash detail page's section.
    A true-up moves the day the list is computed against, so a list left
    un-refreshed would offer purchases that are no longer outstanding -- the
    same reason the band above it re-fetches on the same event.

    **The kind gate is finding N-216's fix.**  This route guarded on ownership
    alone while the page it belongs to and its other three fragments all went
    through :func:`~app.routes.accounts._cash_page.load_cash_account_or_404`,
    so ``GET /accounts/<loan id>/reconcile`` rendered a cash-reconciliation
    panel for an amortizing account -- captioned "every purchase recorded on
    this account has been matched to your bank", which is not a sentence about
    a loan.  It answered with an EMPTY list only because a loan's
    ``account_anchor_history`` carries just its origination row, so the offer
    set's date bound admitted nothing: a property of the data, not of the
    route, and one plan step X-f2-c2 removes when the set widens to
    transactions.
    """
    account = load_cash_account_or_404(account_id)
    return render_template(
        "accounts/_reconcile_purchases.html",
        **reconcile_context(account, panel=panel_id(account.id)),
    )


@accounts_bp.route(
    "/accounts/<int:account_id>/reconcile", methods=["POST"],
)
@login_required
@require_owner
def reconcile_purchases(account_id):
    """Record that the ticked purchases had reached the bank.

    The reconcile step's write door (ruling R-DH (d) / the R-M re-ruling).
    Each ticked entry's ``settled_on`` becomes the civil day the account's
    latest balance was asserted for, after which the projection stops holding
    that purchase's budget back -- the same predicate the balance walk applies
    to a settled transaction, on a date the USER supplied rather than one the
    engine guessed.

    **It is its own request, and that is deliberate.**  Folding it into
    ``apply_anchor_true_up`` would put it inside the transaction that function
    ROLLS BACK when a submission changes nothing (ruling R-EQ) while reporting
    idempotent success -- so a re-assert of the governing balance would silently
    discard every reconciliation the user had just made while the UI said it
    saved.  A separate transaction cannot be swallowed by another one's
    rollback.  The mechanism was an ``IntegrityError`` handler around a unique
    index until plan step X-f1c4b; the hazard is the same and so is the ruling.

    A submitted value that does not name a row is dropped by
    :func:`~app.utils.digit_strings.parse_row_ids` before the service is
    reached, and every id that survives is re-scoped there against the
    outstanding set (owner, account, debit, projected parent, still
    unrecorded), so a forged id matches nothing rather than raising -- the
    set-operation form of the project's "404 for both not-found and
    not-yours" rule.

    Returns the refreshed panel plus ``HX-Trigger: balanceChanged`` so every
    surface showing a projection recomputes.

    **The kind gate is finding N-216's fix**, on the WRITE half; see
    :func:`reconcile_panel` for the measurement.  The two doors take the same
    gate because a gate one member of a family can be written without is a gate
    the next member will be written without too -- which is how this pair came
    to be the exception in the first place.
    """
    account = load_cash_account_or_404(account_id)

    # The raw DAY: it bounds the offer set in SQL and is STAMPED onto every
    # ticked purchase as its posting day, neither of which is the "is this
    # inside the balance" question (that has one implementation, and this
    # writes the fact that implementation later reads).
    observed_on = cash_ledger.reconciled_through(account.id).observed_day
    if observed_on is None:
        # No balance has ever been asserted for this account, so there is
        # nothing for a purchase to be inside of.  Unreachable through the UI
        # (the panel renders no form in that state) and answered rather than
        # raised, because it is a legitimate empty state.
        return render_template(
            "accounts/_reconcile_purchases.html",
            **reconcile_context(account, panel=panel_id(account.id)),
        )

    reconcile_service.record_settled_days(
        current_user.id,
        account.id,
        parse_row_ids(request.form.getlist("entry_ids")),
        observed_on,
    )
    db.session.commit()

    return (
        render_template(
            "accounts/_reconcile_purchases.html",
            **reconcile_context(account, panel=panel_id(account.id)),
        ),
        200,
        {"HX-Trigger": "balanceChanged"},
    )
