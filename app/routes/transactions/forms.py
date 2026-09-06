"""
Shekel Budget App -- Transaction route package: read-only HTMX partials.

The GET routes that return display / edit / create form fragments for the
grid: the display cell, the quick-edit and full-edit popovers, and the
quick-create / full-create / empty-cell placeholders.  None of these
mutate state.
"""

from typing import NamedTuple

from flask import render_template, request
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.models.ref import Status
from app.models.category import Category
from app.models.account import Account
from app.services import pay_period_service, transaction_service
from app.services.pay_calendar import FiledRow, calendar_for
from app.services.scenario_resolver import get_baseline_scenario
from app.services.state_machine import allowed_transitions
from app.utils.auth_helpers import require_owner
from app.utils.dates import display_today
from app.routes._period_options import period_move_options
from app.routes._render_helpers import (
    fragment_amounts,
    render_transaction_cell,
    transfer_settlement_amounts,
)
from app.routes.transactions._bp import transactions_bp
from app.routes.transactions._helpers import (
    _get_owned_transaction,
    _resolve_owned_fks,
    _resolve_owned_period,
)


@transactions_bp.route("/transactions/<int:txn_id>/cell", methods=["GET"])
@login_required
@require_owner
def get_cell(txn_id):
    """HTMX partial: return the display-mode cell content for a transaction."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_transaction_cell(txn)


@transactions_bp.route("/transactions/<int:txn_id>/quick-edit", methods=["GET"])
@login_required
@require_owner
def get_quick_edit(txn_id):
    """HTMX partial: return the minimal inline amount input."""
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404
    return render_template(
        "grid/_transaction_quick_edit.html",
        txn=txn,
        # What the row is worth NOW, which is what the field must be primed
        # with (plan step X-au-c2b).  It read ``txn.estimated_amount``, the
        # COLUMN: on a derived row that is empty, so the field would open
        # BLANK and a save would book whatever the user typed over a figure
        # they never saw.  Priming it with the resolved amount also makes the
        # save honest -- typing the same number back is a no-op, where
        # accepting a blank would not be.
        #
        # A MAP the template indexes, not the scalar this first published: an
        # adversarial review measured that a missing scalar renders
        # ``value=""`` in SILENCE while a missing map raises, and these two
        # forms are the surfaces where an empty figure is POSTED BACK.  A
        # fallback that ships a blank into a save is the shape this whole step
        # exists to delete, so it may not survive on the edit doors.
        budgets=fragment_amounts(txn).budgets,
        # **The SECOND render site of an Estimated box**, and it needs the same
        # withdrawal the full-edit popover got (plan step X-au-j).  An
        # adversarial review found this one ungated: nothing in
        # ``app/templates`` or ``app/static/js`` links here any more
        # (``grid_edit.js`` keeps tier-1 inline editing only for the
        # empty-cell quick-create), but the route is live under
        # ``@login_required @require_owner``, so an owner reaching it by URL
        # was offered a control the PATCH door now always rejects. The census
        # in ``repays_card_spend``'s docstring said "two surfaces"; it was
        # three.
        budget_correctable=not transaction_service.repays_card_spend(txn),
    )


@transactions_bp.route("/transactions/<int:txn_id>/full-edit", methods=["GET"])
@login_required
@require_owner
def get_full_edit(txn_id):
    """HTMX partial: return the full edit popover form.

    For shadow transactions (transfer_id IS NOT NULL), returns the
    transfer edit form instead of the transaction edit form so the
    user edits the parent transfer and both shadows stay in sync.
    """
    txn = _get_owned_transaction(txn_id)
    if txn is None:
        return "Not found", 404

    # --- Transfer detection: return transfer edit form for shadows ---
    if txn.transfer_id is not None:
        xfer = db.session.get(Transfer, txn.transfer_id)
        if xfer is None or xfer.is_deleted:
            # A deleted parent has no edit surface -- see the transfers
            # blueprint's own full-edit door for the whole argument, and for why
            # the refusal is scoped to the edit doors rather than to
            # ``_get_owned_transfer``.
            return "Not found", 404
        statuses = db.session.query(Status).all()
        categories = (
            db.session.query(Category)
            .filter_by(user_id=current_user.id)
            .order_by(Category.group_name, Category.item_name)
            .all()
        )
        # Current + future periods (plus the transfer's own) power the
        # period-move selector when a transfer is edited from a grid
        # shadow cell -- same set the transfers blueprint supplies.
        periods = period_move_options(
            calendar_for(current_user.id), xfer.pay_period_id,
        )
        # The pair's recorded and retained figures -- see the transfers
        # blueprint's own render site: ONE helper answers for both, so the two
        # doors onto this popover cannot show different figures.
        xfer_amounts = transfer_settlement_amounts(xfer, current_user.id)
        return render_template(
            "transfers/_transfer_full_edit.html",
            xfer=xfer,
            statuses=statuses,
            categories=categories,
            source_txn_id=txn.id,
            periods=periods,
            settled=xfer_amounts.settled,
            retained=xfer_amounts.retained,
            # The settle-day correction's bounds -- ``max`` from ruling R-EJ,
            # ``min`` from ruling R-EL.  The USER's today, never
            # ``date.today()``: the process clock is pinned to the display zone
            # in the deployed container but not in CI or a script, and the input
            # must not refuse a day the seam would accept.  The floor is the
            # SAME function the seam refuses below, not a second rule.
            today=display_today(),
            settle_day_min=pay_period_service.earliest_recordable_day(
                current_user.id,
            ),
            # Pre-hint (grid audit D2): the status dropdown disables
            # transitions the state machine would reject.
            allowed_status_ids=allowed_transitions(xfer),
        )

    statuses = db.session.query(Status).all()
    # Pay periods power the in-popover period-move selector.  Only the
    # current and future periods are offered -- moving an expense into an
    # already-closed period is not a supported workflow -- but the row's
    # own period is always included so a transaction that currently sits
    # in a past period stays selected (and is not silently re-pointed at
    # the first current period on save).  Periods are per-user; the PATCH
    # handler re-checks ownership of the submitted id (F-029).
    # ONE derivation for this render, threaded -- the shape of ledger row
    # **P68**, closed by C2-f3c (two derivations of one owner's calendar in
    # one render, nothing holding the two equal; **P69** is its open
    # sibling): the
    # ``<select>`` below is built from it and so is the card's context line,
    # which names the row's OWN paycheck.  Asking twice would be two answers to
    # one question with nothing holding them equal.
    #
    # **The row is loaded BEFORE the calendar is derived**, and each
    # ``require_period`` caller owes its own statement of that order.  That
    # method documents TWO states that reach its refusal and BOTH are closed
    # here, which a first draft of this comment did not say -- it argued only
    # the first and read as a proof (adversarial review, 2026-08-31).
    #
    #   * **A picture from more than one moment** (finding **N-358**).  This
    #     door is a GET, so ``db_transaction`` binds it to ``REPEATABLE READ,
    #     READ ONLY`` -- both reads see one snapshot.  It is not a fragment
    #     that splits its own transaction: no ``write_transaction`` block runs
    #     on this path, which is what makes the GET argument sound where it is
    #     unsound on ``/grid``.
    #   * **A row filed in ANOTHER owner's pay period -- and the leg that
    #     closes it MOVED, exactly as this comment predicted it would.**
    #     ``_get_owned_transaction`` scoped on ``txn.pay_period.user_id``, so
    #     the calendar built from ``current_user.id`` below and the owner of
    #     the period the row names were the same BY CONSTRUCTION.  That door
    #     reads ``txn.user_id`` since plan step ``pay_calendar:C13-b`` and no
    #     longer proves anything about the row's PERIOD's owner -- and it does
    #     not have to: ``fk_transactions_owner_period`` (plan step ``C13-a``)
    #     makes a row whose paycheck belongs to someone else UNSTORABLE, so
    #     the equality this leg used to establish by asking is now a fact the
    #     schema will not let be false.  The leg is replaced, not lost.
    calendar = calendar_for(current_user.id)
    periods = period_move_options(calendar, txn.pay_period_id)
    amounts = fragment_amounts(txn)
    return render_template(
        "grid/_transaction_full_edit.html",
        txn=txn,
        # The row's OWN paycheck, as the DERIVED value (plan step C4-a-5).  The
        # card printed ``txn.pay_period.label`` -- the ORM row's accessor, which
        # formats the STORED ``end_date`` -- while the ``<select>`` beside it
        # printed the derived one, so on a period whose stored end has gone
        # stale (findings **P12** / **P28**) one paycheck was labelled two ways
        # inside one card.  ``PayPeriod.label`` is deleted with this step, so
        # the wrong source is unreachable rather than merely unused.
        period=calendar.require_period(FiledRow.for_row(txn)),
        # See ``get_quick_edit`` for why the Estimated field is primed with the
        # RESOLVED amount rather than the column, and why it is a MAP.
        #
        # All three travel because :class:`RenderAmounts` is one value and a
        # surface may not publish two of it, and this card reads every one:
        # ``budgets`` primes the Estimated field, ``settled`` primes the Actual
        # box, and ``retained`` draws the re-book notice.  A draft of plan step
        # X-au-c3 drew that Actual box gated on ``locked`` and then deleted it
        # as unreachable -- every ``is_settled`` status is also ``is_immutable``,
        # so it rendered ``disabled`` on 100% of the rows it appeared on.  Being
        # disabled WAS the defect: a lock protects a budget decision, and what
        # the bank took is an observation.
        budgets=amounts.budgets,
        settled=amounts.settled,
        retained=amounts.retained,
        statuses=statuses,
        periods=periods,
        # The settle-day correction's bounds (rulings R-EJ / R-EL) -- see the
        # transfer branch above for why the clock is the display one and why the
        # floor comes from the seam's own function.
        today=display_today(),
        settle_day_min=pay_period_service.earliest_recordable_day(
            current_user.id,
        ),
        # Pre-hint (grid audit D2): the status dropdown disables transitions
        # the row cannot take.  ``offerable_status_ids`` is the state machine's
        # answer narrowed by the row's TYPE -- the map admits both Paid and
        # Received from Projected because it grades the status and never sees
        # ``transaction_type_id``, and exactly one of them is what an income or
        # an expense row settles as (plan step X-ap).
        allowed_status_ids=transaction_service.offerable_status_ids(txn),
        # Ruling **R-FF**, the same sentence the reconcile panel obeys: an
        # amount is correctable exactly when the settle verb takes its MANUAL
        # branch.  An envelope carrying purchases settles at ``sum(entries)``,
        # so an Actual box beside it would take a figure the settle discards.
        amount_correctable=not transaction_service.settles_from_entries(txn),
        # **A CC payback's ESTIMATE is not its own to state either**, which is
        # the same sentence one box over: a payback is worth the card spend of
        # the row it repays, so you change it by changing what went on the
        # card.  The box rendered and took a figure until now, and the next
        # entry mutation on the source silently overwrote it -- finding
        # **N-252**, ``$58.40`` live on the developer's own payback 2590.
        # ``transaction_service.repays_card_spend``'s docstring carries the
        # whole rule, including why a ROW-backed payback needs it for the
        # opposite reason (nothing overwrites it, so the lie sticks).
        budget_correctable=not transaction_service.repays_card_spend(txn),
        # WHICH repair the withdrawn box should name.  The two payback kinds
        # are corrected by different acts and only one of them involves
        # purchases -- ``transaction_service.repays_tracked_purchases``'s
        # docstring carries the fork.  Resolved here rather than in the
        # template, which displays a decision and never takes one.
        budget_from_purchases=transaction_service.repays_tracked_purchases(txn),
        # **Why this row may NOT be deleted, or ``None``** (plan step
        # ``bank_import:X-gb``).  The card renders the delete control exactly
        # when this is ``None`` and prints the sentence when it is not, and
        # ``delete_transaction`` re-asks the same function as the
        # crafted-request backstop -- the layering every guard in
        # ``_gates`` uses, with the rule in the service and the screen
        # displaying its answer.
        delete_refusal=transaction_service.deletion_refusal(txn),
        # **What deleting it would take back besides the row itself**
        # (:class:`~app.services.transaction_service.RowDeletion`): the CC
        # payback rows that go down with it, and the bank lines whose matches
        # it would empty.  The dialog NAMES them rather than counting them,
        # because the control destroys records and *"1 bank line"* over a
        # `$793.23` ACH payment is the *"Nothing moved."* sentence this arc has
        # already shipped once.
        #
        # It is the SAME function the door's own verb calls over the SAME row
        # set -- a draft read the row alone while the press also tore down its
        # payback chain, and an adversarial review measured a `$200.00` card
        # payment silently un-explained over a dialog naming no line at all.
        delete_preview=transaction_service.preview_deletion(txn),
    )


# ---- the empty-cell family ------------------------------------------
#
# The three fragments a cell with no transaction in it can show -- the dash,
# the quick-create input, the full-create popover -- are all addressed by the
# same coordinate, so they resolve it through one function.


class _GridCell(NamedTuple):
    """The grid coordinate one of the three empty-cell fragments renders for.

    Produced by :func:`_resolve_grid_cell`, the query-string prefix that
    :func:`get_quick_create`, :func:`get_full_create` and
    :func:`get_empty_cell` share: all three read the same four ids and
    ownership-check the same three of them, and until plan step **C2-f3e**
    each wrote that out for itself.

    Attributes:
        category: The row's :class:`~app.models.category.Category`, proved to
            belong to the requester.
        period_id: The ``budget.pay_periods.id`` of the column's paycheck, as
            the owner's own calendar answered it -- an id this owner holds by
            construction rather than one a comparison let through.  It is what
            the two create forms POST back as ``pay_period_id``, so it is the
            paycheck any row created from this cell is funded by.
        account: The viewed :class:`~app.models.account.Account`, proved to
            belong to the requester.
        transaction_type_id: Income or expense.  A reference-table id and so
            not ownership-checked; that it is also unvalidated is one of the
            ``request.args.get(..., type=int)`` sites plan step
            ``balance:X-ah`` owns.
    """

    category: Category
    period_id: int
    account: Account
    transaction_type_id: int


def _resolve_grid_cell():
    """Resolve and ownership-check the grid cell this request's query names.

    Plan step **C2-f3e**, closing ledger row **P51**.

    **The period is answered by the OWNER'S CALENDAR, and that is what makes
    the ownership check structural.**  It was a fourth
    :func:`_resolve_owned_fks` spec -- fetch ``budget.pay_periods`` by primary
    key, then compare ``row.user_id`` against the requester.  A calendar holds
    ONE owner's whole schedule and nothing else, so an id that is not in it is
    not this owner's, and a single lookup answers "no such period" and "not
    yours" with the identical 404 the security response rule asks for.  There
    is no comparison left for a later edit to drop.  This is the shape plan
    step C2-f2b put on ``grid.partials.mobile_this_period_summary`` and plan
    step C2-f3c put on ``_resolve_carry_forward_context`` one module over.

    **It did NOT take the last ORM ``PayPeriod`` out of this blueprint, and a
    first draft of this paragraph claimed it did.**  Measured with
    ``tests._test_helpers.pay_periods_hydrated``: after C2-f3e
    ``/transactions/<id>/cell``, ``/quick-edit`` and ``/full-edit`` each still
    hydrated exactly one, because :func:`._helpers._get_owned_transaction`
    walked ``txn.pay_period.user_id`` -- one of the ELEVEN such comparisons
    ledger row **P75** counted, beside EIGHT more that fetched the row by
    primary key.  **Plan step ``pay_calendar:C13-b`` retired all nineteen**,
    and what it did to THESE three is documented where the hydration now
    happens: :func:`app.routes._render_helpers.render_transaction_cell`, which
    reads ``pay_period.start_date`` for the due caption and used to get it
    free off the ownership walk.  The count did not fall; the load MOVED, and
    that docstring carries the re-measurement.

    **What it costs, stated rather than glossed**, because the honest
    comparison is not free: a primary-key ``session.get`` becomes
    :func:`~app.services.pay_calendar.calendar_for`'s two queries and a
    derivation over the owner's whole payday set.  That is once per fragment
    request, where the page that offers the fragment already derives one per
    render, and the two cheap probes are ordered FIRST so a request naming a
    foreign category or account is refused before any of it runs.  No row
    count is quoted here on purpose: the owner's payday set GROWS as the
    rolling top-up extends the schedule, and eleven docstrings in this
    repository already state that moving number, ten of them at a value it has
    since passed.

    Returns:
        ``(cell, None)`` on success, or ``(None, (message, 404))`` on the
        first ownership failure -- a Flask response tuple the caller returns
        directly to HTMX.  ``period_id`` is carried out of the DERIVED period
        rather than off the query string: the two are the same integer, and
        taking the confirmed one means no unchecked value can reach a form
        even if a later edit moves the guard.

    Raises:
        PayCalendarError: The owner's paydays cannot define a calendar (see
            :func:`~app.services.pay_calendar.calendar_for`).  Uncaught, as it
            is at every other route that derives one: the grid page these
            fragments are swapped into derives its own calendar to render at
            all, so an owner who can reach this door has one that derives.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership: prevent IDOR -- return the identical 404 for "does not exist"
    # and "belongs to another user" so an attacker cannot distinguish the two
    # cases and enumerate another user's category names.  See audit finding H1.
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return None, err

    # The lookup and its forensic half are ``_resolve_owned_period``'s since
    # plan step ``pay_calendar:C13-b``, which gave this shape a name so the
    # three WRITE doors could take it too.  They were silent -- they ran
    # through ``_resolve_owned_fks`` -- and now share this one's
    # ``log_refused_lookup`` call, which is that helper's own stated rule: an
    # owner-scoped lookup cannot tell "no such row" from "not yours", which is
    # the stronger security property and exactly why it must not also mean no
    # trail.  The body stays this blueprint's uniform ``"Not found"`` so a
    # fragment's 404 still says nothing about WHICH of its four ids was wrong.
    period, err = _resolve_owned_period(period_id, "Not found")
    if err is not None:
        return None, err

    return _GridCell(
        category=objs[Category],
        period_id=period.period_id,
        account=objs[Account],
        transaction_type_id=transaction_type_id,
    ), None


@transactions_bp.route("/transactions/new/quick", methods=["GET"])
@login_required
@require_owner
def get_quick_create():
    """HTMX partial: return a quick-create input for an empty cell.

    Query params: category_id, period_id, account_id, transaction_type_id.
    """
    cell, err = _resolve_grid_cell()
    if err is not None:
        return err

    # Look up the baseline scenario for hidden fields.  Not part of the cell
    # coordinate: :func:`get_empty_cell` renders a dash for an owner who has
    # none, and answering it 400 would be a new refusal rather than a shared
    # one.
    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    return render_template(
        "grid/_transaction_quick_create.html",
        category=cell.category,
        # The ID, not the row -- the whole ``PayPeriod`` was being carried for
        # this one integer (plan step C2-f3e, ledger row **P51**), which left
        # the three create partials stating their period contract two ways.
        period_id=cell.period_id,
        account_id=cell.account.id,
        scenario_id=scenario.id,
        transaction_type_id=cell.transaction_type_id,
    )


@transactions_bp.route("/transactions/new/full", methods=["GET"])
@login_required
@require_owner
def get_full_create():
    """HTMX partial: return the full create popover form.

    Query params: category_id, period_id, account_id, transaction_type_id.
    """
    cell, err = _resolve_grid_cell()
    if err is not None:
        return err

    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    # No ``statuses``: the create form has no status control -- a new
    # transaction is born Projected (the create route assigns it), so there is
    # nothing for the user to pick.  Status changes happen later through the
    # mark-done / cancel / credit / full-edit actions on the saved row.
    return render_template(
        "grid/_transaction_full_create.html",
        category=cell.category,
        # The ID, not the row -- see :func:`get_quick_create`.
        period_id=cell.period_id,
        account_id=cell.account.id,
        scenario_id=scenario.id,
        transaction_type_id=cell.transaction_type_id,
    )


@transactions_bp.route("/transactions/empty-cell", methods=["GET"])
@login_required
@require_owner
def get_empty_cell():
    """HTMX partial: return the empty cell placeholder.

    Used by Escape key to revert a quick-create form back to the dash.
    Query params: category_id, period_id, account_id, transaction_type_id.
    """
    cell, err = _resolve_grid_cell()
    if err is not None:
        return err

    return render_template(
        "grid/_transaction_empty_cell.html",
        category=cell.category,
        # IDS, not rows.  The partial builds one URL and reads nothing else off
        # either; its other render entry -- the desktop grid macro -- has only
        # a ``DerivedPeriod`` to give it since plan step C2-f2b.  The ACCOUNT
        # moved the same way here: both callers hold an ORM row, so it was not
        # the two-types defect **P51** records, but it was the same whole row
        # carried for one integer, and leaving it made the family's contract
        # false in its own header comment.
        period_id=cell.period_id,
        account_id=cell.account.id,
        txn_type_id=cell.transaction_type_id,
    )
