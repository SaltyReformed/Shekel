"""
Shekel Budget App -- Loan route package: escrow + rate-history management.

The HTMX partial routes that add a rate-history entry and add / remove escrow
lines.  Both escrow routes rebuild the escrow list through the shared
:func:`_render_escrow_list` tail (resolve today's active lines, recompute monthly
escrow + total payment, emit the OOB payment-summary swap), so that parallel
render lives in exactly one place.
"""

import logging
from datetime import date

from flask import flash, render_template, request
from flask_login import login_required

from app.extensions import db
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.loan_features import RateHistory
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _RATE_HISTORY_UNIQUE_CONSTRAINT,
    _compute_total_payment,
    _escrow_schema,
    _load_loan_account,
    _rate_schema,
    _resolve_loan_state,
    build_loan_band_chart,
)
from app.services import (
    escrow_calculator,
    loan_loaders,
    loan_posting_service,
    loan_recurrence_sync,
)
from app.utils.auth_helpers import require_owner
from app.utils.money import ZERO

logger = logging.getLogger(__name__)


def _render_rate_history(account, params, band_chart=None):
    """Re-query and render the rate-history partial for a loan account.

    The shared reload + render used by both the duplicate-submit
    (IntegrityError) and the success paths of :func:`add_rate_change`, so
    the descending-effective-date ordering and the template kwargs live in
    exactly one place.

    Args:
        account: ORM :class:`Account` instance for the loan.
        params: ORM :class:`LoanParams` instance.
        band_chart: The freshly recomputed band-chart dict
            (:func:`._helpers.build_loan_band_chart`) on the SUCCESS path only,
            so the partial can emit the hidden refresh carrier ``loan_detail.js``
            reads to rebuild the now-visible balance band (a rate change
            re-amortizes the loan).  ``None`` on the duplicate-submit path, where
            nothing re-amortized -- the template then omits the carrier and the
            band (and any active payoff preview) is left untouched.

    Returns:
        The rendered ``loan/_rate_history.html`` partial.
    """
    rate_history = (
        db.session.query(RateHistory)
        .filter_by(account_id=account.id)
        .order_by(RateHistory.effective_date.desc())
        .all()
    )
    # DH-#56: the OOB swap shows the resolver-derived current rate (the
    # rate in effect today after the just-committed rate history), not the
    # retired ``LoanParams.interest_rate`` column.  Resolve once here so
    # the swapped Overview "Interest Rate" reflects the new change.
    state = _resolve_loan_state(account, params)
    return render_template(
        "loan/_rate_history.html",
        account=account,
        params=params,
        rate_history=rate_history,
        current_rate=state.current_rate,
        band_chart=band_chart,
        oob_swaps=True,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/rate", methods=["POST"])
@login_required
@require_owner
def add_rate_change(account_id):
    """Record a variable-rate change (HTMX)."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None or params is None:
        return "Account not found", 404

    errors = _rate_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _rate_schema.load(request.form)

    # A rate change cannot predate origination: period 0 starts at
    # ``origination_date`` and the origination RateHistory row (DH-#56)
    # is the loan's base / period-0 rate.  A pre-origination row would
    # become the earliest entry, displacing the true origination row in
    # the dashboard's ``origination_rate`` derivation
    # (``rate_history[-1]``) and in ``_origination_rate``'s ``min()``.
    # Enforced in the route (not the schema) because the schema has no
    # access to the loan's origination date -- mirroring the
    # ``anchor_date >= origination_date`` guard in
    # :func:`true_up_balance`.  ``effective_date == origination_date``
    # itself collides with the seeded origination row and is rejected by
    # the existing same-date unique-constraint path below.
    if data["effective_date"] < params.origination_date:
        return (
            "Rate change effective date cannot be before the loan's "
            f"origination date ({params.origination_date.isoformat()}).",
            400,
        )

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load`` already
    # converted the form percent to the storage-domain fraction.

    entry = RateHistory(
        account_id=account.id,
        effective_date=data["effective_date"],
        interest_rate=data["interest_rate"],
        monthly_pi=data.get("monthly_pi"),
        notes=data.get("notes"),
    )
    db.session.add(entry)

    # DH-#56: the prior ``params.interest_rate = data["interest_rate"]``
    # mirror-write is gone.  The retired column drifted to the latest
    # rate on every change (corrupting the resolver's period-0/base rate
    # for a backdated or out-of-order change); RateHistory is now the
    # sole source of truth and the resolver derives the current rate from
    # it, so no scalar needs maintaining here.
    # Build-Order Step 4: a rate change moves the interest split of every
    # confirmed post-anchor payment, in every scenario.  The shared helper
    # re-syncs those corrections in the same transaction as the new rate row and
    # translates a same-effective-date duplicate (which its flush surfaces) into
    # the idempotent re-render below; a non-rate IntegrityError propagates from
    # the helper (the correct 500 disposition).
    if not loan_posting_service.sync_all_scenarios_or_duplicate(
        account.id, _RATE_HISTORY_UNIQUE_CONSTRAINT,
    ):
        # Same-effective-date double-submit (F-104 / C-22): the composite
        # unique ``uq_rate_history_account_effective_date`` rejected the second
        # INSERT when the user clicked Save twice in a row.  Flash a clear
        # message and re-render the rate history without the proposed
        # duplicate.  A legitimate same-day correction is expressed by editing
        # the existing row, not by appending another.
        logger.info(
            "Duplicate rate-history entry prevented for account %d on %s",
            account.id, data["effective_date"],
        )
        flash(
            "A rate change with that effective date already exists. "
            "Edit the existing entry to correct it.",
            "warning",
        )
        return _render_rate_history(account, params)

    # R-4: a rate change re-amortizes the loan, moving the projected payoff, so
    # re-bound the recurring payment's end_date before committing.
    loan_recurrence_sync.sync_recurring_payment_end_date(account.id)
    db.session.commit()
    logger.info("Recorded rate change for loan %d: %s", account.id, data["interest_rate"])
    # The re-amortization moves the whole balance trajectory, but this HTMX swap
    # replaces only the rate-history card (+ the OOB rate chip); the always-
    # visible balance band would otherwise show the pre-change line until a full
    # reload.  Recompute the band and hand it to the partial's refresh carrier so
    # ``loan_detail.js`` rebuilds ``#loan-balance-chart`` from it on this swap.
    return _render_rate_history(
        account, params, build_loan_band_chart(account, params),
    )


def _active_line_with_name(account_id, name):
    """Return the account's ACTIVE escrow line with ``name``, or None.

    Decision C (route-enforced active-name uniqueness): a line is active iff its
    latest version -- greatest ``effective_date`` -- is not a removal tombstone.
    That predicate depends on a line's child versions, so it cannot be a raw
    partial unique index; the ``ix_escrow_lines_account_name`` index serves this
    lookup.  A line whose latest version is a tombstone is removed, so its name
    is free to reuse under a new line.

    Args:
        account_id: The loan account to search.
        name: The candidate escrow line name.

    Returns:
        The matching active :class:`~app.models.escrow_line.EscrowLine`, or
        ``None`` when no active line carries that name.
    """
    for line in loan_loaders.load_escrow_lines(account_id):
        if line.name != name:
            continue
        latest = max(
            line.versions, key=lambda v: v.effective_date, default=None,
        )
        if latest is not None and not latest.is_removed:
            return line
    return None


def _tombstone_line_today(line):
    """Mark an escrow line removed as of today (idempotent).

    Appends a removal tombstone version (``is_removed``, $0) at today's date, so
    the line resolves to 0 from today forward while its history stays intact --
    the supersession analogue of the old ``end_date`` stamp.  No-op when the line
    already resolves to inactive today (a repeat delete).  When a version already
    exists AT today (a same-day add or amount change), that version is converted
    to the tombstone in place rather than appending a second: the
    ``(line_id, effective_date)`` unique forbids two versions on one date, and a
    line added and removed the same day never contributed (matching the legacy
    zero-length range).

    Args:
        line: The :class:`~app.models.escrow_line.EscrowLine` to remove.
    """
    if not escrow_calculator.resolve_active_lines([line], date.today()):
        return  # already inactive as of today
    today_version = next(
        (v for v in line.versions if v.effective_date == date.today()), None,
    )
    if today_version is not None:
        today_version.is_removed = True
        today_version.annual_amount = ZERO
        return
    db.session.add(EscrowComponentVersion(
        line_id=line.id, effective_date=date.today(),
        annual_amount=ZERO, is_removed=True,
    ))


def _render_escrow_list(account, params):
    """Reload the escrow lines and render the list partial with the OOB tail.

    The shared reload used by both :func:`add_escrow` and :func:`delete_escrow`:
    load the account's lines, resolve them to today's active set, recompute the
    monthly-escrow badge + total payment, and emit the out-of-band
    payment-summary swap -- so that render logic lives in one place.

    Args:
        account: ORM :class:`~app.models.account.Account` for the loan.
        params: ORM :class:`~app.models.loan_params.LoanParams`, or None.

    Returns:
        The rendered ``loan/_escrow_list.html`` partial.
    """
    escrow_lines = loan_loaders.load_escrow_lines(account.id)
    escrow_components = escrow_calculator.resolve_active_lines(
        escrow_lines, date.today(),
    )
    monthly_escrow = escrow_calculator.calculate_monthly_escrow(escrow_components)
    total_payment = _compute_total_payment(account, params, escrow_components)
    return render_template(
        "loan/_escrow_list.html",
        account=account,
        escrow_components=escrow_calculator.build_escrow_display(
            escrow_components,
        ),
        monthly_escrow=monthly_escrow,
        total_payment=total_payment,
        oob_swaps=True,
    )


@loan_bp.route("/accounts/<int:account_id>/loan/escrow", methods=["POST"])
@login_required
@require_owner
def add_escrow(account_id):
    """Add an escrow line (HTMX): a new line plus its opening version today."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    errors = _escrow_schema.validate(request.form)
    if errors:
        return "Please correct the highlighted errors and try again.", 400

    data = _escrow_schema.load(request.form)

    # E-28 / HIGH-06 (Commit 24): the schema's ``@pre_load`` converted the form
    # percent to the storage-domain fraction before validation, so
    # ``data["inflation_rate"]`` is stored verbatim.

    # Reject a duplicate ACTIVE line name (decision C); a removed line's name is
    # reusable, so only an active collision blocks the add.
    if _active_line_with_name(account.id, data["name"]) is not None:
        return "An escrow component with that name already exists.", 400

    # New line + its opening version effective today.  The operator-facing
    # effective-date field is a later step; the add defaults the version to
    # today, reproducing the legacy CURRENT_DATE-defaulted ``effective_date``.
    line = EscrowLine(account_id=account.id, name=data["name"])
    db.session.add(line)
    db.session.flush()  # assign line.id for the version FK
    db.session.add(EscrowComponentVersion(
        line_id=line.id,
        effective_date=date.today(),
        annual_amount=data["annual_amount"],
        inflation_rate=data.get("inflation_rate"),
    ))
    db.session.commit()

    logger.info("Added escrow line '%s' to loan %d", data["name"], account.id)
    return _render_escrow_list(account, params)


@loan_bp.route(
    "/accounts/<int:account_id>/loan/escrow/<int:line_id>/delete",
    methods=["POST"],
)
@login_required
@require_owner
def delete_escrow(account_id, line_id):
    """Remove an escrow line (HTMX): append a removal tombstone as of today."""
    account, params, _account_type = _load_loan_account(account_id)
    if account is None:
        return "Account not found", 404

    line = db.session.get(EscrowLine, line_id)
    if line is None or line.account_id != account.id:
        return "Component not found", 404

    _tombstone_line_today(line)
    db.session.commit()
    logger.info("Removed escrow line %d from loan %d", line_id, account.id)
    return _render_escrow_list(account, params)
