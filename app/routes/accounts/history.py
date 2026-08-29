"""
Shekel Budget App -- Balance-history card routes

The durable record of a balance assertion (ruling **R-EV**, plan step
X-f2-b): the cash detail page's Balance history card, its context builder,
and the HTMX fragment the card re-fetches itself through.

**Why it is not in :mod:`app.routes.accounts.detail`.**  That module owns the
cash detail PAGE -- the band's balance production contract, the horizon chips,
the chart, the interest parameters -- and this is a third subject beside it,
exactly as :mod:`app.routes.accounts.reconcile` is a third subject beside the
anchor write door.  ``detail`` also stood at 824 of pylint's 1000-line ceiling
before this step, so the alternative was another round of shaving prose off a
measured claim, which findings **N-152**, **N-156** and **N-201** rule against.

Services boundary: this module owns the HTTP-shaped concerns (the ownership
check, choosing which rows are shown by default, fragment rendering) and reads
every figure from :func:`app.services.balance_at.cash_anchor_history`.  No money
arithmetic happens here or in the template.
"""

from flask import render_template
from flask_login import current_user, login_required

from app.models.account import Account
from app.routes.accounts._bp import accounts_bp
from app.routes.accounts._cash_page import load_cash_account_or_404
from app.services import balance_at
from app.utils.auth_helpers import require_owner

#: How many assertions the card shows before the disclosure (developer ruling
#: 1, 2026-08-10).  The real Checking account carries 57 over 133 days and
#: grows by about 13 a month, so "render every row" -- the shape the loan
#: twin uses for its single anchor -- would make the log the page's dominant
#: element.  Twelve is roughly a month of this developer's recording rhythm.
RECENT_ASSERTIONS = 12


def _panel_id(account_id: int) -> str:
    """Return the balance-history card's DOM id for one account.

    PRIVATE, unlike :func:`app.routes.accounts.reconcile.panel_id`, and the
    difference is real rather than stylistic: that one is imported by
    ``detail`` to build the reconcile panel's context, while this module builds
    its OWN context, so nothing outside these four lines needs the name.  A
    first draft made it public citing a consumer in ``detail`` -- which imports
    ``panel_id`` from ``reconcile``, not from here -- and that would have left
    two sibling modules exporting one name for two different ids, the
    shadowing hazard ``reconcile`` went out of its way to avoid at parameter
    scope.

    Args:
        account_id: The account whose card id to compose.

    Returns:
        The DOM id string.
    """
    return f"balance-history-{account_id}"


def _split_by_recording(
    rows: "list[balance_at.CashAnchorRow]", limit: int,
) -> "tuple[list[balance_at.CashAnchorRow], list[balance_at.CashAnchorRow]]":
    """Partition *rows* into the most recently RECORDED *limit* and the rest.

    **The default view is chosen by when a row was RECORDED and rendered in the
    order the balances were TRUE, and the two being different keys is the
    point.**  A card that simply took the top *limit* of a list sorted by the
    day each balance was true would hide the row a back-dated write just
    created: on the real Checking account a balance recorded today for
    2026-07-15 sits 40 rows down, well past any sensible default.  Since giving
    that write a retrieval path is the whole of finding **N-205**, the rows
    shown are the ones most recently TYPED, and they keep the log's own
    chronological order.

    **That order is the log's, not a running argument**, and the distinction
    matters for how the card is read: with a back-dated row among them the
    shown set is NOT contiguous, so the row above another is not necessarily
    the assertion before it.  Every row's own ``ledger`` and ``correction``
    stay correct -- they are properties of the walk, fixed before any of this
    -- but a reader must not infer the pairing from adjacency on screen.

    **It ranks on the INSTANT, not on ``recorded_on``, and a first build of
    this function got that wrong.**  Every assertion typed in one bookkeeping
    session shares one recording DAY -- that is what a session is -- so ranking
    on the day left the whole set tied, stable-sorted back into
    ``observed_on`` order, and dropped the back-dated row off the bottom: the
    one case the split exists for, defeated by the normal case.  A test pins
    it.

    Ties on the instant fall back to the incoming order (newest ``observed_on``
    first), which is the account's own timeline and the honest secondary key.

    Args:
        rows: The producer's rows, newest ``observed_on`` first.
        limit: How many to show before the disclosure.

    Returns:
        ``(recent, earlier)``, each preserving the incoming order.  ``earlier``
        is empty when the account carries no more than *limit* assertions, and
        the template renders no disclosure then.
    """
    if len(rows) <= limit:
        return list(rows), []
    ranked = sorted(
        range(len(rows)), key=lambda index: rows[index].recorded_at,
        reverse=True,
    )
    shown = set(ranked[:limit])
    return (
        [row for index, row in enumerate(rows) if index in shown],
        [row for index, row in enumerate(rows) if index not in shown],
    )


def balance_history_context(
    account: Account, ctx: balance_at.BalanceContext,
) -> dict:
    """Assemble the Balance history card's context for one account.

    The ONE builder both mounts read -- the cash detail page's initial render
    and the fragment below -- so a card refreshed after a true-up cannot come
    to say something different from the one the page drew.

    **Its result is passed to the template under ONE name (``history``) rather
    than splatted**, and that is not a style choice: ``reconcile_context``
    beside it also publishes ``account`` and ``panel_id``, so splatting both
    into the page's context would silently re-root the reconcile panel at this
    card's DOM id and break its POST target.  It is also deliberately NOT part
    of ``detail._cash_detail_context``: that builder serves the band fragment
    too, which re-renders on every ``balanceChanged`` and has no use for an
    assertion log, so folding it in would walk the account's whole event stream
    again for a card the response does not carry.

    Args:
        account: The owned, attached :class:`Account`.  Caller owns the
            ownership check.
        ctx: The read pass's
            :class:`~app.services.balance_at.BalanceContext`.  Taken rather
            than built, so the page that renders this card beside its band
            resolves ONE read pass instead of two.

    Returns:
        The card's context: ``recent`` / ``earlier`` rows,
        ``opening`` (the :class:`app.services.balance_at.CashOpeningRow` the
        card pins as its oldest row -- see below),
        ``total`` (every assertion the account carries, so the header can name
        what is NOT shown), ``reconcilable`` (whether the ledger / correction
        columns mean anything for this account -- see
        :class:`app.services.balance_at.CashAnchorHistory`), and ``panel_id``.

        **``total`` counts assertions and the opening is NOT one of them**
        (plan step X-f3c-2b).  The header reads "N recorded", which is a count
        of balances the OWNER told the account it held; the books opening is
        not one of those, and on all nine production accounts nobody typed it
        at all -- the X-f3c-2a migration derived every one.  Counting it would
        also make the header disagree with the "Show all N" button beside it,
        which expands only the assertions.
    """
    history = balance_at.cash_anchor_history(account, ctx)
    recent, earlier = _split_by_recording(history.rows, RECENT_ASSERTIONS)
    return {
        "recent": recent,
        "earlier": earlier,
        # Outside the recent/earlier split ON PURPOSE: the opening is the
        # foundation every row above rests on, so it is pinned visible rather
        # than filed behind a disclosure whose whole content is assertions.
        # The template renders it in the table's ``<tfoot>``, which is where
        # "always shown, below everything" is expressible without duplicating
        # the row for the collapsed and the expanded state.
        "opening": history.opening,
        "total": len(history.rows),
        "reconcilable": history.reconcilable,
        "panel_id": _panel_id(account.id),
    }


@accounts_bp.route(
    "/accounts/<int:account_id>/balance-history", methods=["GET"],
)
@login_required
@require_owner
def balance_history(account_id):
    """HTMX partial: re-render the account's balance-assertion log.

    The ``balanceChanged`` refresh target on the cash detail page's card, and a
    GET because it writes nothing.  A true-up APPENDS an assertion, so a card
    left un-refreshed would omit the very row the user just recorded -- which
    is the defect this surface exists to close, reproduced one layer up.

    It refreshes on the same event the band and the reconcile panel above it
    do, so all three surfaces on the page move together rather than one of them
    describing a balance the other two have already replaced.
    """
    account = load_cash_account_or_404(account_id)
    return render_template(
        "accounts/_balance_history.html",
        history=balance_history_context(
            account, balance_at.BalanceContext.build(current_user.id),
        ),
    )
