"""
Shekel Budget App -- Transaction route package: read-only HTMX partials.

The GET routes that return display / edit / create form fragments for the
grid: the display cell, the quick-edit and full-edit popovers, and the
quick-create / full-create / empty-cell placeholders.  None of these
mutate state.
"""

from flask import render_template, request
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import TxnTypeEnum
from app.extensions import db
from app.models.transfer import Transfer
from app.models.ref import Status
from app.models.category import Category
from app.models.pay_period import PayPeriod
from app.models.account import Account
from app.services import pay_period_service, transaction_service
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
        periods = period_move_options(current_user.id, xfer.pay_period_id)
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
    periods = period_move_options(current_user.id, txn.pay_period_id)
    amounts = fragment_amounts(txn)
    return render_template(
        "grid/_transaction_full_edit.html",
        txn=txn,
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
    )


@transactions_bp.route("/transactions/new/quick", methods=["GET"])
@login_required
@require_owner
def get_quick_create():
    """HTMX partial: return a quick-create input for an empty cell.

    Query params: category_id, period_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: prevent IDOR -- return identical 404 for "does
    # not exist" and "belongs to another user" so attackers cannot
    # distinguish the two cases.  See audit finding H1.
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    period = objs[PayPeriod]
    acct = objs[Account]

    # Look up the baseline scenario for hidden fields.
    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    return render_template(
        "grid/_transaction_quick_create.html",
        category=category,
        period=period,
        account_id=acct.id,
        scenario_id=scenario.id,
        transaction_type_id=transaction_type_id,
    )


@transactions_bp.route("/transactions/new/full", methods=["GET"])
@login_required
@require_owner
def get_full_create():
    """HTMX partial: return the full create popover form.

    Query params: category_id, period_id, account_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: same IDOR fix as get_quick_create (H1).
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    period = objs[PayPeriod]
    acct = objs[Account]

    scenario = get_baseline_scenario(current_user.id)
    if not scenario:
        return "No baseline scenario", 400

    # No ``statuses``: the create form has no status control -- a new
    # transaction is born Projected (the create route assigns it), so there is
    # nothing for the user to pick.  Status changes happen later through the
    # mark-done / cancel / credit / full-edit actions on the saved row.
    return render_template(
        "grid/_transaction_full_create.html",
        category=category,
        period=period,
        account_id=acct.id,
        scenario_id=scenario.id,
        transaction_type_id=transaction_type_id,
    )


@transactions_bp.route("/transactions/empty-cell", methods=["GET"])
@login_required
@require_owner
def get_empty_cell():
    """HTMX partial: return the empty cell placeholder.

    Used by Escape key to revert a quick-create form back to the dash.
    Query params: category_id, period_id, transaction_type_id.
    """
    category_id = request.args.get("category_id", type=int)
    period_id = request.args.get("period_id", type=int)
    account_id = request.args.get("account_id", type=int)
    transaction_type_id = request.args.get(
        "transaction_type_id", type=int,
        default=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
    )

    # Ownership check: same IDOR fix as get_quick_create (H1).
    objs, err = _resolve_owned_fks([
        (Category, category_id, "Not found"),
        (PayPeriod, period_id, "Not found"),
        (Account, account_id, "Not found"),
    ])
    if err is not None:
        return err
    category = objs[Category]
    account = objs[Account]

    return render_template(
        "grid/_transaction_empty_cell.html",
        category=category,
        # The ID, not the row.  The ORM lookup above is the OWNERSHIP check
        # (the IDOR fix H1) and nothing more; the partial builds one URL from
        # one integer, and its other render entry -- the desktop grid macro --
        # has only a ``DerivedPeriod`` to give it since plan step C2-f2b.  One
        # partial, two callers, one contract they can both keep.  The value is
        # the request's own ``period_id``, which ``_resolve_owned_fks`` has
        # just proved belongs to this user; reading it back off the row would
        # be the same integer by a longer route.
        period_id=period_id,
        account=account,
        txn_type_id=transaction_type_id,
    )
