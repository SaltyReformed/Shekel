"""
Shekel Budget App -- The Outstanding difference card's routes

Plan step **balance:X-f3c-3**: what an account's own books cannot explain,
beside whether an imported bank statement has checked the days it accumulated
over.  Its context builder, its DOM-id helper, and the HTMX fragment the card
re-fetches itself through.

**It moves no money and offers no act.**  The figure is the INSTRUMENT plan
step X-f3c-4's acceptance act needs -- the act that books the difference as an
uncategorized transaction is the NEXT leaf, and it will hang its control on
this card.  Building the card one step early is what lets the developer see
the figure, and the evidence behind it, before anything offers to move it.

**Why it is not in :mod:`app.routes.accounts.history`.**  That module owns the
balance-history card -- the RECORD of every balance an owner has declared --
and its own template says what it is: *"a link rather than an editor keeps this
card what its own module says it is, a RECORD and not a write door"*.  X-f3c-4
puts a money-moving control on the outstanding difference, so the figure wants
a surface that can hold one, and the cut is the same one
:mod:`app.routes.accounts.difference` and :mod:`app.routes.accounts.reconcile`
already made out of ``anchor``: the record, the task and the write door are
different subjects.

**ONE service call, and no composition here.**
:func:`app.services.outstanding_difference.outstanding_difference` resolves
both halves -- what the books cannot explain, and whether an imported statement
accounts for the days it accumulated over -- because the span the verdict must
be about is the DIFFERENCE's own.  A route that called the two producers
separately would hold a figure and a span as independent arguments and could
pair them wrongly, which is the shape finding **N-354** closed one layer down.

**``detail`` could not take this card either** -- it stood at 917 of pylint's
1000-line ceiling before this step, which findings **N-152**, **N-156** and
**N-201** rule against shaving prose to fit.

Services boundary: this module owns the HTTP-shaped concerns (the ownership
check, the verdict NAME, the fragment render) and reads every figure from that
one service.  No money arithmetic happens here or in the template.
"""

from flask import render_template
from flask_login import current_user, login_required

from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.routes.accounts.difference import (
    DIFF_VERDICT_NAMES,
    difference_verdict,
)
from app.services import balance_at, outstanding_difference as service
from app.utils.auth_helpers import require_owner


def _panel_id(account_id: int) -> str:
    """Return the Outstanding difference card's DOM id for one account.

    PRIVATE, unlike :func:`app.routes.accounts.reconcile.panel_id`, and for the
    reason ``history._panel_id`` is: this module builds its OWN context and
    publishes the id inside it, so nothing outside these four lines needs the
    name.  Making it public would put a SECOND ``panel_id`` on the import path
    of ``detail``, which already imports ``reconcile``'s -- the exact shadowing
    hazard that module went out of its way to avoid, and one that would silently
    root this card's refresh at the reconcile panel's id.

    Args:
        account_id: The account whose card id to compose.

    Returns:
        The DOM id string.
    """
    return f"outstanding-difference-{account_id}"


def outstanding_context(
    account: Account, ctx: balance_at.BalanceContext,
) -> dict:
    """Assemble the Outstanding difference card's context for one account.

    The ONE builder both mounts read -- the cash detail page's initial render
    and the fragment below -- so a card refreshed after a true-up cannot come
    to say something different from the one the page drew.

    **The only thing decided here is the VERDICT NAME**, and it is the true-up
    form's own (``accounts.difference.difference_verdict``) rather than a
    second mapping: both surfaces ask what ``declared - records`` MEANS, in the
    same direction, so stating the sign convention twice is how the two would
    come to read one sign two ways.

    Args:
        account: The owned, attached :class:`Account`.  Caller owns the
            ownership check.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.  Taken rather
            than built, so the page that renders this card beside its band and
            its history resolves ONE read pass for all three.

    Returns:
        The card's context: ``difference`` (the
        :class:`~app.services.balance_at.CashOutstandingDifference`, or
        ``None`` -- see below), ``verdict`` (which of the three
        ``DIFF_*`` names the difference's sign means), ``reconciliation``
        (the
        :class:`~app.services.outstanding_difference.SpanAgreement`, or
        ``None`` when the account holds no recorded bank line at all),
        ``verdicts`` (the three names, so the template compares against THEM
        rather than against re-typed literals), ``panel_id`` and
        ``account_id``.

        **``difference`` is ``None`` for an account this question does not
        apply to**, and the template renders nothing at all then: an account
        whose balance carries a modelled tier -- an HYSA accruing interest, a
        brokerage compounding -- is not being reconciled against a bank
        statement, and the same subtraction there is its GAIN (ruling
        **R-FO**, finding **N-213**).  The cash detail page serves those kinds
        too, so the card has to be able to not exist.
    """
    resolved = service.outstanding_difference(account, ctx)
    if resolved is None:
        return {
            "difference": None,
            "panel_id": _panel_id(account.id),
            "account_id": account.id,
        }
    return {
        "difference": resolved.difference,
        "verdict": difference_verdict(resolved.difference.amount),
        # ``None`` when the account has no recorded bank line at all -- an
        # absence rather than an empty comparison, and the card says so in its
        # own words rather than rendering a zero-of-zero tally.
        "reconciliation": resolved.reconciliation,
        "verdicts": DIFF_VERDICT_NAMES,
        "panel_id": _panel_id(account.id),
        "account_id": account.id,
    }


@accounts_bp.route(
    "/accounts/<int:account_id>/outstanding-difference", methods=["GET"],
)
@login_required
@require_owner
def outstanding_difference(account_id):
    """HTMX partial: re-render what the account's books cannot explain.

    The ``balanceChanged`` refresh target on the cash detail page's card, and a
    GET because it writes nothing.

    **Both sides of the figure move on that event, which is why it listens.**
    A true-up APPENDS an assertion, so the declaration the difference is
    measured against becomes a different one on a different day; and the
    reconcile panel's own POST records settle days, which changes what the
    books produce.  A card left un-refreshed would state a difference against a
    balance the page above it has already replaced.
    """
    account = load_cash_account_or_404(account_id)
    return render_template(
        "accounts/_outstanding_difference.html",
        books_difference=outstanding_context(
            account, balance_at.BalanceContext.build(current_user.id),
        ),
    )
