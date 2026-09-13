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
from flask_login import current_user

from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.cash_ledger import (
    derived_amount_basis,
    amounts_by_id,
    recorded_amounts_by_id,
    resolve_transfer_amount,
)
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import build_entry_sums_dict
from app.services.grid_view_service import due_captions_by_id
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
            :func:`~app.services.cash_ledger.amounts_by_id`.
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
    :func:`~app.services.cash_ledger.amounts_by_id`, the resolved amount
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
    # **A SETTLED salary row now costs a projection here, and it did not
    # before** (plan step balance:X-au-d, named by that step's adversarial
    # review rather than found later).  Such a row used to resolve through
    # amount rule 1 -- a column read -- and the read-time repair returned early
    # on ``is_projected``; it DECLARES its definition now, so rendering one
    # fragment runs ``paycheck_calculator.project_salary`` over the owner's
    # whole pay-period set.  Measured on the 2026-09-02 production clone: 8
    # statements, and one basis, for one row.
    #
    # It is not threaded from a read pass because a FRAGMENT has none: this is
    # the one-row render path, reached by an HTMX swap that loaded nothing else.
    # A pass-shaped caller (the grid, the fold, the reconcile panel) already
    # builds one basis for everything it loaded, which is what findings N-228 /
    # N-268 / N-309 are about; this is the shape those findings do not cover
    # and the cost is one derivation rather than one per row.
    basis = derived_amount_basis(txn.account.user_id, txn.scenario_id)
    return RenderAmounts(
        budgets=amounts_by_id([txn], basis),
        settled=recorded_amounts_by_id([txn]),
        retained=retained_settle_amounts_by_id([txn]),
    )


def transfer_budgets(xfer: Transfer) -> "dict[int, Decimal]":
    """Return ``{transfer_id: what this transfer's amount IS}``, one entry.

    The transfer twin of :attr:`RenderAmounts.budgets`, and the ONE producer
    every surface showing a transfer's figure reads (plan step **X-au-f**).
    The three transfer fragments -- the cell, the quick edit and the full-edit
    popover -- took ``xfer.amount`` straight off the parent until this step,
    which was correct exactly while a generated transfer STORED its figure.
    That column empties for a generated transfer at this step's cutover leaf,
    at which point a template reading it renders Jinja's ``None`` into a money
    box and posts the literal string ``"None"`` back through
    ``TransferUpdateSchema`` (which refuses it).  Asking the amount model
    instead is the same move ``routes/transactions/forms`` already made for the
    transaction popover.

    **It is a MAP rather than a scalar for the reason
    :func:`fragment_amounts` states**: two of the three fragments POST this
    figure back into a money box, and a missing scalar renders ``value=""`` in
    silence where an absent map raises.  **What it does NOT buy is protection
    from the WRONG map**, and an adversarial review is why that is written
    down: ``budgets`` is one context key over two id spaces that are not
    disjoint, so a transaction-keyed map handed to a transfer fragment answers
    a HIT rather than a ``KeyError``.  No path does that today -- the one route
    that could, ``transactions/forms.get_full_edit``, returns from inside its
    shadow branch before any transaction map is built -- and the guarantee is
    that a MISSING map is loud, not that a mismatched one is.

    **It is SEPARATE from :func:`transfer_settlement_amounts` rather than a
    third field on it, and the split is by what each surface renders.**  The
    cell and the quick edit show a PLAN and nothing else, while that producer
    loads the shadow pair and reads two settlement records to answer -- so
    bundling them would make every cell swap pay for two figures it does not
    display.  The popover, which shows all three, calls both.  *A first draft of
    this paragraph cited finding N-296 for that cost and an adversarial review
    opened the row: N-296 is a per-DEFINITION eager load in BATCH callers, whose
    remedy is `pricing_load_options` and whose step is `X-bm`.  It says nothing
    about this.  The argument above needs no citation.*

    **SINGLE-ROW reads only**, the boundary :attr:`Transfer.settled_on`
    documents, and the leaf that stated that boundary in advance has arrived.
    Plan step X-au-f-2 gave a loan payment's PARENT its producer (ruling
    **R-BAL10**), so this now builds a read pass and one call per row is what
    an N+1 would look like.  A transfer that owns its figure or reads its
    definition's series still costs no query -- the basis resolves nothing
    until a rule asks it -- and only a DERIVE-mode loan payment reaches the
    loan.  No batch surface exists to be wrong about: the grid renders a
    transfer's two SHADOWS as ordinary rows off its own ``budgets`` map
    (Transfer Invariant 5), and all nine parent-transfer render sites are
    one-row HTMX swaps.

    **The basis is BUILT here rather than threaded, exactly as the transaction
    twin builds one** (:func:`fragment_amounts`, whose comment carries the
    argument): a FRAGMENT has no read pass to take one from -- it is reached by
    an HTMX swap that loaded nothing else -- and a pass-shaped caller builds
    one basis for everything it loaded instead.  It is pinned off the
    transfer's OWN columns, which is what makes it right for the row being
    priced whichever of the nine sites called.

    Args:
        xfer: The transfer the fragment renders.  It states no ownership rule of
            its own and needs none: it reads neither ``user_id`` nor
            ``scenario_id`` and, on the OWN arm, issues no query at all, so
            there is nothing here to scope.  Eight of the nine callers reach it
            through an owner-scoped load; the ninth
            (``routes/transactions/forms.get_full_edit``) establishes ownership
            TRANSITIVELY, its shadow having passed ``_get_owned_transaction``
            and :func:`transfer_settlement_amounts` refusing a foreign or
            soft-deleted parent one line earlier.  Stated rather than claimed
            uniform, because an adversarial review measured the difference.

    Returns:
        ``{xfer.id: Decimal}``, holding exactly one entry.

    Raises:
        AmountUnresolvable: From the resolver, for a transfer whose rule cannot
            answer.  A refusal is never a fallback (see
            :mod:`app.services.cash_ledger._amount_source`).
    """
    return {
        xfer.id: resolve_transfer_amount(
            xfer, derived_amount_basis(xfer.user_id, xfer.scenario_id),
        ),
    }


@dataclass(frozen=True)
class TransferSettlementAmounts:
    """The TWO settlement maps the transfer full-edit popover must publish.

    The transfer half of :class:`RenderAmounts`, and deliberately only two of
    its three: the PLAN is :func:`transfer_budgets`, a producer of its own
    because the two fragments that show a plan and no record must not pay for
    a record they do not render.  What the parent does NOT
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


def render_transfer_cell(xfer: Transfer, **extra: Any) -> str:
    """Render a parent transfer's grid cell with the context it must carry.

    **The transfer twin of :func:`render_transaction_cell`, and this module's
    own opening paragraph is what it satisfies**: every HTMX response that
    re-renders a cell must ship the same context, *so the render has exactly one
    definition with a public name*.  The transaction cell had that; the transfer
    cell had SIX hand-assembled ``render_template`` calls across three modules
    and two blueprints, each repeating ``xfer``, a ``resolve_grid_account``
    lookup, and -- once plan step **X-au-f-1** made the figure a resolved one --
    a ``budgets`` map.

    **It was extracted at the moment the required context GREW, which is the
    only moment the omission is cheap.**  A seventh site added later without
    ``budgets`` is not a blank cell: Jinja raises on the subscript, so it is a
    500 on a live money surface, and on five of the six sites that surface is
    already an ERROR path -- a 409 conflict fragment or a designed 4xx -- where a
    500 replaces the message the user needed.  Naming the render once removes
    the way to forget.

    The ACCOUNT is resolved here rather than passed, because all six callers
    resolved the same thing from the same two arguments; it decides which
    direction arrow the cell draws.

    Args:
        xfer: The transfer to render.  Owner-established by the caller -- see
            :func:`transfer_budgets`, which states what that does and does not
            guarantee.
        **extra: Forwarded to ``render_template`` -- ``wrap_div=True``,
            ``conflict=True``, ``error=<message>``, the flags each caller adds
            on top of the shared context.

    Returns:
        Rendered HTML string.

    Raises:
        AmountUnresolvable: From :func:`transfer_budgets`, for a transfer whose
            rule cannot answer.  Unreachable while every transfer owns its
            figure; the leaves after this one are what give it a population.
    """
    return render_template(
        "transfers/_transfer_cell.html",
        xfer=xfer,
        account=resolve_grid_account(current_user.id, current_user.settings),
        budgets=transfer_budgets(xfer),
        **extra,
    )


def render_transaction_cell(txn: Transaction, **extra: Any) -> str:
    """Render the transaction cell template with its amount and entry context.

    Wraps render_template so every HTMX cell response includes the three
    amount maps the display reads (:class:`RenderAmounts`), the ``entry_sums``
    dict the progress indicator on tracked transactions needs, and the PAYDAY
    the due-date caption is compared against.

    **The caption is DECIDED by the route and drawn by the template**
    (pay-calendar plan step C4-a-1).  The partial computed it itself, as
    ``t.due_date != t.pay_period.start_date`` -- a lazy relationship load
    issued from inside the render, once per distinct paycheck on a page
    drawing N cells, and a template cannot be given a query budget.  Both
    surfaces that draw this cell now call the same producer
    (:func:`~app.services.grid_view_service.due_captions_by_id`), so a row
    cannot caption one way on the grid and another in the fragment the same
    click swaps in -- the rule :class:`RenderAmounts` above states for the
    three amount maps, applied to the fourth thing a cell draws.

    **The payday it needs is now this function's OWN load, and the count did
    not change** (plan step ``pay_calendar:C13-b``).  This paragraph used to
    say the read cost nothing here because every caller had already proved
    ownership through ``txn.pay_period.user_id``
    (``transactions/_helpers._get_owned_transaction`` and its siblings), so
    the relationship was loaded before this ran.  Those doors read
    ``txn.user_id`` now and hydrate nothing, so the reason is gone -- and the
    load is not: it MOVED here, as a lazy load, and that is the honest
    statement rather than either "still free" or "one statement worse".

    **Re-measured 2026-09-03 on ``GET /transactions/<id>/cell``, both
    spellings of the ownership door, same request:** 12 statements and 1
    ``PayPeriod`` hydration EITHER SIDE.  The only difference is WHERE the
    ``budget.pay_periods`` SELECT sits in the sequence -- fifth (the ownership
    walk) before, sixth (this due caption) after.  The 2026-08-27 measurement
    this paragraph quoted said 12 as well, so the number is unchanged and its
    reason is not.

    Deriving the owner's calendar here instead was built and rejected on the
    2026-08-27 measurement -- it ADDED two statements on top of the load the
    ownership check had already paid for, and bought only a re-check of an
    ownership fact the route had just established.  **The arithmetic behind
    that rejection has shifted and the conclusion has not**: it is now one
    lazy load against a two-query derivation, so the derivation is still the
    more expensive of the two.  ``start_date`` is the one column
    ``budget.pay_periods`` actually stores, carried through
    :func:`~app.services.pay_calendar.derive_periods` untouched, so it is the
    same date the grid's derived window publishes.

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
        due_captions=due_captions_by_id(
            [txn], {txn.pay_period_id: txn.pay_period.start_date},
        ),
        **extra,
    )
