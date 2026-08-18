"""
Shekel Budget App -- Shared Transaction-Cell Render Helper

The cross-blueprint home for rendering a transaction's grid cell.
Every HTMX response that re-renders a cell -- the transaction CRUD and
status routes, and the entries CRUD routes' out-of-band cell refresh --
must ship the same context (notably ``entry_sums`` and ``budgets``, which drive
the amount display and the envelope progress), so the render has exactly one
definition with a public name instead of a module-private helper imported across
blueprint packages.  Follows the package-level shared-helper convention
of ``app/routes/_commit_helpers.py``.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from flask import render_template

from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.cash_ledger import (
    amount_basis,
    display_amounts_by_id,
    recorded_amounts_by_id,
)
from app.services.entry_service import build_entry_sums_dict
from app.services.transaction_service import retained_settle_amounts_by_id
from app.services.transfer_service import load_transfer_rows


@dataclass(frozen=True)
class RenderAmounts:
    """The THREE amount maps every surface showing a row's figure must publish.

    A row is a PLAN until its money moves and a RECORD of what moved once it has
    (plan step **X-au-c3**), and a screen shows the record where there is one and
    the plan otherwise.  All three are needed to render one cell, so they are
    resolved by ONE call and travel together -- a caller cannot take the budget
    and forget the settlement.

    **That pairing is a defect fixed rather than a convenience.**  An adversarial
    review of plan step X-au-c2b found the display rule written twice and
    differently, because each surface assembled its own context: the grid laid
    the seam's override map over the resolved one while every HTMX fragment
    published the resolved map ALONE under the same key, so one row showed two
    figures on two surfaces the same click opened.  Two independently-passed maps
    are the same shape of mistake one map further on.

    **The third map is the developer's 2026-08-17 rule that whatever will be
    booked against the account is shown wherever the row is shown.**  A row
    reverted out of the settled band KEEPS what it recorded and a re-settle
    honours it, so its plan is what the balance counts and its retained figure is
    what a tick books.  Those are two different numbers about one row, and the
    second was visible on no surface but the reconcile panel -- the grid and the
    full-edit popover both showed a ``$500.00`` plan for a row that would book
    ``$245.32``.  It rides in this dataclass for the same reason the second one
    does: so no surface can publish two of the three.

    Attributes:
        budgets: ``{transaction_id: what the row's amount IS}`` --
            :func:`~app.services.cash_ledger.display_amounts_by_id`.
        settled: ``{transaction_id: what its money DID}``, ``None`` per row that
            has not settled or records nothing --
            :func:`~app.services.cash_ledger.recorded_amounts_by_id`.  The
            TOTAL read, not the refusing one, because a FRAGMENT is an edit
            control: a settled row carrying no record can only be repaired from
            a surface that draws, and the surfaces that COUNT money
            (``routes/grid/page``) keep the refusal.
        retained: ``{transaction_id: what a tick WOULD book}``, ``None`` per row
            where that is already on screen --
            :func:`~app.services.transaction_service.retained_settle_amounts_by_id`.
    """

    budgets: dict[int, Decimal]
    settled: "dict[int, Decimal | None]"
    retained: "dict[int, Decimal | None]"


def fragment_amounts(txn: Transaction) -> RenderAmounts:
    """Return all three amount maps for ONE fragment's row.

    The single-row door onto the amount model (plan step X-au-c2b), for the HTMX
    fragments that re-render one cell or one card.  Every template that shows a
    row's amount reads this map rather than ``txn.estimated_amount``, because
    under the amount model a derived row stores nothing in that column and the
    cell would render an empty string where a figure belongs.

    **It answers by the same rule the pages do** --
    :func:`~app.services.cash_ledger.display_amounts_by_id`, the resolved amount
    superseded by a live recompute.  An adversarial review found that rule
    written twice and differently: the grid merged the seam's override map over
    its resolved one while every fragment published the resolved map ALONE under
    the same context key, so a drifted salary row showed its live net on the
    grid and its stale column in the quick-edit box the same click opened -- and
    that box is what a save posts back from.

    **It takes ONE row, and the signature is the guard.**  It took ``*rows`` and
    pinned the basis off ``rows[0]``, which needed a paragraph about cross-owner
    sets to be safe; every fragment renders exactly one row, so taking one row
    deletes the question rather than documenting it.  A batch surface takes the
    READ PASS's basis instead
    (:meth:`~app.services.balance_at.BalanceContext.amounts`).

    **The SETTLEMENT and RETAINED maps join it at plan step X-au-c3**, and it
    is the same argument one step on: a row that has settled shows what it
    RECORDED, and a row that was reverted shows what a re-settle will book, so a
    surface publishing the budget alone would show the plan for a row whose
    money has already moved or is about to move at a different figure.  One call
    answers all three so no surface can take one and forget the others.

    Args:
        txn: The row the fragment renders.

    Returns:
        A :class:`RenderAmounts` whose three maps hold one entry each, keyed
        for the templates and the entry builders, which all index a map.
    """
    basis = amount_basis(txn.account.user_id, txn.scenario_id)
    return RenderAmounts(
        budgets=display_amounts_by_id([txn], basis),
        settled=recorded_amounts_by_id([txn]),
        retained=retained_settle_amounts_by_id([txn]),
    )


@dataclass(frozen=True)
class TransferSettlementAmounts:
    """The TWO settlement maps the transfer full-edit popover must publish.

    The transfer half of :class:`RenderAmounts`, and deliberately only two of
    its three: a transfer's PLAN is ``xfer.amount``, a column the parent carries
    itself, so there is no budget map to resolve.  What the parent does NOT
    carry is a settlement record -- a transfer's money moves on its two shadow
    legs and each records its own -- so both maps below are read off a leg.

    **Both are keyed by the TRANSFER's id, not the shadow's.**  The template's
    subject is the transfer, and a map keyed by something the template does not
    have would be a second lookup for it to get wrong.  The re-key is safe
    because a pair carries ONE record (Transfer Invariant 3), which is the same
    fact ``Transfer.settled_on`` reads for the day.

    **They are maps rather than scalars for the reason ``fragment_amounts``
    states**: a missing scalar renders ``value=""`` in silence while a missing
    map raises, and this form is a surface where an empty figure would be POSTED
    BACK into the settlement record.

    Attributes:
        settled: ``{transfer_id: what the pair's money DID}``, ``None`` when the
            transfer has not settled or its legs record nothing.
        retained: ``{transfer_id: what a re-settle WOULD re-book}``, ``None``
            when that is already on screen.
    """

    settled: "dict[int, Decimal | None]"
    retained: "dict[int, Decimal | None]"


def transfer_settlement_amounts(
    xfer: Transfer, user_id: int,
) -> TransferSettlementAmounts:
    """Return the pair's recorded and retained figures, keyed by transfer id.

    The transfer twin of :func:`fragment_amounts`, for the ONE surface that
    needs it: the full-edit popover, which since plan step X-au-c3 carries an
    Actual box (what the bank took, prefilled from the record) and the re-book
    notice (what a re-settle would honour).  Both render sites call this rather
    than assembling it, because the popover is reachable from two blueprints --
    the transfers page and a grid SHADOW cell -- and a rule written at each is
    how one click shows a different figure from another.

    **It asks the two published producers rather than reading the columns.**
    :func:`~app.services.cash_ledger.recorded_amounts_by_id` is what every other
    EDIT surface prefills a settled row's figure from, and
    :func:`~app.services.transaction_service.retained_settle_amounts_by_id` is
    built from the same function the settle verb honours
    (``status_seam.honoured_correction``) -- so what this popover promises and
    what a tick books cannot drift.  Neither is transaction-specific: both are
    pure reads of a row's own settlement record, and a shadow carries one
    exactly as a plain row does.

    **The EXPENSE leg answers**, which is the leg
    ``transfer_service._settle.settle`` resolves its figures from and the leg
    the correction door writes first.  Either would answer the same (Transfer
    Invariant 3 -- both legs carry the same record), and naming one means the
    choice is not made twice.  It is deliberately NOT the income leg
    ``Transfer.settled_on`` reads: that one matches ``posting_service._entry_date``,
    which is a fact about the DAY, and pinning each read to the function it must
    agree with is what keeps either from silently becoming "whichever row came
    back first".

    Args:
        xfer: The transfer the popover is rendering.
        user_id: The owner, for the loader's defense-in-depth ownership check.

    Returns:
        A :class:`TransferSettlementAmounts` whose two maps hold one entry each.

    Raises:
        NotFoundError: If *xfer* is not *user_id*'s or is soft-deleted.
        ValidationError: If the shadow pair is corrupt -- fail loud, because a
            popover drawn over a broken pair offers controls that cannot work.
        AmountUnresolvable: From the settlement read, for a leg whose record
            CONTRADICTS itself.  A leg that records nothing answers ``None``
            instead: that row is the one the popover exists to repair.
    """
    rows = load_transfer_rows(xfer.id, user_id)
    settled = recorded_amounts_by_id([rows.expense])
    retained = retained_settle_amounts_by_id([rows.expense])
    return TransferSettlementAmounts(
        settled={xfer.id: settled[rows.expense.id]},
        retained={xfer.id: retained[rows.expense.id]},
    )


def render_transaction_cell(txn: Transaction, **extra: Any) -> str:
    """Render the transaction cell template with its amount and entry context.

    Wraps render_template so every HTMX cell response includes the three
    amount maps the display reads (:class:`RenderAmounts`) and the
    ``entry_sums`` dict the progress indicator on tracked transactions needs.

    Args:
        txn: The Transaction object to render.
        **extra: Additional keyword arguments forwarded to
            render_template (e.g. ``wrap_div=True``, ``wrap_oob=True``,
            ``conflict=True``).

    Returns:
        Rendered HTML string.
    """
    amounts = fragment_amounts(txn)
    return render_template(
        "grid/_transaction_cell.html",
        txn=txn,
        budgets=amounts.budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        entry_sums=build_entry_sums_dict([txn], amounts.budgets),
        **extra,
    )
