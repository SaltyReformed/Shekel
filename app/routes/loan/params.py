"""
Shekel Budget App -- Loan route package: parameter management.

Initial loan-parameter creation, parameter updates, and the dated balance
true-up (an append-only :class:`LoanAnchorEvent`).  All three are
redirect-style POST handlers that flash and return to the dashboard.
"""

import logging
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import LoanAnchorSourceEnum
from app.extensions import db
from app.models.account import Account
from app.models.loan_anchor_event import LoanAnchorEvent
from app.models.loan_features import RateHistory
from app.models.loan_params import LoanParams
from app.models.ref import AccountType
from app.routes.loan._bp import loan_bp
from app.routes.loan._helpers import (
    _PARAM_FIELDS,
    _create_schema,
    _require_configured_loan,
    _trueup_schema,
    _update_schema,
)
from app.services import anchor_service
from app.services.anchor_service import AnchorTrueUpOutcome
from app.utils.account_validation import _validate_collateral_link
from app.utils.auth_helpers import get_or_404, require_owner

logger = logging.getLogger(__name__)


@loan_bp.route("/accounts/<int:account_id>/loan/setup", methods=["POST"])
@login_required
@require_owner
def create_params(account_id):
    """Create initial loan parameters."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    account_type = db.session.get(AccountType, account.account_type_id)
    if account_type is None or not account_type.has_amortization:
        flash("This account type does not support loan parameters.", "warning")
        return redirect(url_for("savings.dashboard"))

    # Check if params already exist.
    existing = db.session.query(LoanParams).filter_by(account_id=account.id).first()
    if existing:
        flash("Loan parameters already configured.", "info")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return render_template(
            "loan/setup.html", account=account, account_type=account_type,
        )

    data = _create_schema.load(request.form)

    # Type-specific term validation.
    max_term = account_type.max_term_months
    if max_term and data.get("term_months", 0) > max_term:
        flash(
            f"Term cannot exceed {max_term} months for {account_type.name}.",
            "danger",
        )
        return render_template(
            "loan/setup.html", account=account, account_type=account_type,
        )

    # DH-#56: ``interest_rate`` is no longer a LoanParams column -- it
    # seeds the loan's origination RateHistory row (the resolver's
    # base / period-0 rate).  Pop it before constructing LoanParams.
    # The schema's ``@pre_load`` (E-28 / HIGH-06) already divided the
    # form percent by 100, so the value is the storage-domain fraction
    # the ``rate_history.interest_rate`` CHECK (0..1) accepts.
    origination_rate = data.pop("interest_rate")

    params = LoanParams(account_id=account.id, **data)
    db.session.add(params)
    db.session.flush()

    # Origination RateHistory row (DH-#56): every loan carries a rate
    # row effective at origination so the resolver derives its
    # period-0 / base rate from RateHistory rather than a stored scalar.
    # ``monthly_pi=None`` lets the rate-period engine derive the
    # origination P&I from the original principal and term (exact for an
    # on-schedule loan).
    db.session.add(RateHistory(
        account_id=account.id,
        effective_date=params.origination_date,
        interest_rate=origination_rate,
        monthly_pi=None,
    ))

    # Origination LoanAnchorEvent (E-18 / Commit 15; closes F-9).
    # The loan resolver requires at least one event per loan; Commit
    # 12's migration backfilled events for every pre-existing loan,
    # but new loans created post-migration need an explicit
    # origination event written here so the dashboard's resolver
    # call does not raise ValueError on first render.  Mirrors
    # ``account_service.create_account``'s paired-row insert pattern
    # for :class:`AccountAnchorHistory`.
    db.session.add(LoanAnchorEvent(
        account_id=account.id,
        anchor_date=params.origination_date,
        anchor_balance=params.original_principal,
        source_id=ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.ORIGINATION,
        ),
    ))
    db.session.commit()

    logger.info("Created loan params for account %d", account.id)
    flash("Loan parameters configured.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/params", methods=["POST"])
@login_required
@require_owner
def update_params(account_id):
    """Update loan parameters."""
    account, params, account_type = _require_configured_loan(account_id)

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _update_schema.load(request.form)

    # Type-specific term validation.
    max_term = account_type.max_term_months
    if max_term and data.get("term_months", 0) > max_term:
        flash(
            f"Term cannot exceed {max_term} months for {account_type.name}.",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # DH-#56: ``interest_rate`` is no longer a LoanParams column; when
    # submitted it edits the loan's ORIGINATION rate -- upsert the
    # RateHistory row effective at origination (the resolver's period-0
    # rate).  The schema's ``@pre_load`` (E-28 / HIGH-06) already
    # converted the form percent to the storage-domain fraction.  The
    # remaining params flow through the ``_PARAM_FIELDS`` setattr loop
    # (``interest_rate`` is no longer a member).
    if "interest_rate" in data:
        _upsert_origination_rate(params, data["interest_rate"])

    for field, value in data.items():
        if field in _PARAM_FIELDS:
            setattr(params, field, value)

    db.session.commit()
    logger.info("Updated loan params for account %d", account.id)
    flash("Loan parameters updated.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))


def _upsert_origination_rate(params, rate):
    """Set the loan's origination (period-0) rate to ``rate``.

    DH-#56 retired ``LoanParams.interest_rate``; the loan's base /
    period-0 rate now lives in the :class:`RateHistory` row effective at
    ``origination_date``.  The "Loan Parameters" form's rate field edits
    that origination rate, so this updates the existing origination row
    (the common case -- ``create_params`` and the DH-#56 migration both
    seed one for every loan) or inserts it if somehow absent (defensive).
    Does not commit; the caller commits with the rest of the update.

    Args:
        params: The loan's :class:`LoanParams` row.
        rate: The new origination rate as a storage-domain fraction.
    """
    origination_row = (
        db.session.query(RateHistory)
        .filter_by(
            account_id=params.account_id,
            effective_date=params.origination_date,
        )
        .first()
    )
    if origination_row is not None:
        origination_row.interest_rate = rate
    else:
        db.session.add(RateHistory(
            account_id=params.account_id,
            effective_date=params.origination_date,
            interest_rate=rate,
            monthly_pi=None,
        ))


@loan_bp.route("/accounts/<int:account_id>/loan/trueup", methods=["POST"])
@login_required
@require_owner
def true_up_balance(account_id):
    """Append a dated balance true-up :class:`LoanAnchorEvent` (E-18 D-C / Commit 16).

    Mirrors the checking-account anchor true-up UX (see
    :func:`app.routes.accounts.true_up`) for loan accounts.  The user
    asserts "the lender reports my balance is $X as of date D"; the
    handler appends a single ``user_trueup`` event and the resolver
    (:func:`app.services.loan_resolver.resolve_loan`) replays
    confirmed payments forward from that event to derive every loan-
    touching display surface.  The table is structurally
    append-only -- a correction is expressed as another append, never
    an edit -- so the new event becomes the active anchor without
    mutating any prior row.

    Validation chain:

      1. ``_load_loan_account`` rejects cross-owner / non-loan
         accounts with the project's "404 for not-found and not-yours"
         response.
      2. :class:`LoanAnchorTrueupSchema` enforces ``anchor_balance >= 0``
         and ``anchor_date <= today`` -- a future trueup is not a
         historical assertion and is rejected before any DB work.
      3. The route enforces ``anchor_date >= params.origination_date``
         here rather than in the schema because the schema does not
         have access to the loan's origination date; folding the
         check into the schema would require coupling
         :class:`LoanParams` into the schemas module.  A
         pre-origination trueup is rejected with a flash and a
         redirect; no event is written.

    Outcomes (mirroring the checking semantics):

      * COMMITTED: a new ``LoanAnchorEvent`` row is written and
        committed; the user is redirected back to the dashboard with
        a success flash.
      * DUPLICATE_SAME_DAY: the partial unique expression index
        ``uq_loan_anchor_events_acct_date_bal_day`` rejected the
        INSERT (the user double-clicked or a network retry replayed
        the same submission on the same UTC calendar day); the route
        treats this as idempotent success -- the prior request
        committed the same value this one was trying to submit -- and
        redirects with an informational flash.

    The function does NOT mutate :class:`LoanParams.current_principal`.
    The column is non-authoritative seed (E-18 / Commit 15) and the
    resolver reads the event log, not the column.
    """
    account, params, _ = _require_configured_loan(account_id)

    errors = _trueup_schema.validate(request.form)
    if errors:
        flash(
            "Please correct the highlighted errors and try again.",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    data = _trueup_schema.load(request.form)
    anchor_date = data["anchor_date"]
    # Schema returns ``anchor_balance`` as Decimal because the field
    # is declared with ``places=2`` (marshmallow's Decimal field
    # constructs from a string internally); explicit reconstruction
    # via ``Decimal(str(...))`` is defensive against future schema
    # tweaks that might return a different numeric type.
    anchor_balance = Decimal(str(data["anchor_balance"]))

    if anchor_date < params.origination_date:
        flash(
            "Anchor date cannot be before the loan's origination "
            f"date ({params.origination_date.isoformat()}).",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    outcome = anchor_service.apply_loan_anchor_true_up(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
    )

    if outcome is AnchorTrueUpOutcome.DUPLICATE_SAME_DAY:
        # F-103 idempotent success path: the prior request committed
        # the same (date, balance) tuple this request was trying to
        # submit.  No new row, but the on-display value is already
        # correct; flash an informational message and redirect.
        flash(
            "Loan balance already recorded for that date.",
            "info",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    logger.info(
        "Loan trueup: account %d set to $%s as of %s",
        account.id, anchor_balance, anchor_date,
    )
    flash(
        f"Recorded loan balance of ${anchor_balance:,.2f} "
        f"as of {anchor_date.strftime('%b %-d, %Y')}.",
        "success",
    )
    return redirect(url_for("loan.dashboard", account_id=account_id))


@loan_bp.route("/accounts/<int:account_id>/loan/collateral", methods=["POST"])
@login_required
@require_owner
def update_collateral(account_id):
    """Set or clear the asset that secures this loan (home-equity link).

    Writes the nullable ``collateral_account_id`` self-link on the loan
    account so a mortgage / HELOC can be grouped with the Property it is
    secured by and equity rendered.  The link is presentation only -- the
    emergent net-worth math never reads it.  An empty or malformed
    submission clears the link; a non-empty value is validated by
    :func:`app.utils.account_validation._validate_collateral_link`
    (same-owner Asset target, no self-link, source is an amortizing
    liability) before it is written.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # The picker submits an Asset account id or "" (clear).  A non-digit
    # value can only come from a forged form; treat it as a clear rather
    # than crashing -- the validator below is the authority on legality.
    raw = (request.form.get("collateral_account_id") or "").strip()
    collateral_account_id = int(raw) if raw.isdigit() else None

    failure = _validate_collateral_link(
        collateral_account_id, account, current_user.id,
    )
    if failure is not None:
        flash(failure[0], failure[1])
        return redirect(url_for("loan.dashboard", account_id=account_id))

    account.collateral_account_id = collateral_account_id
    db.session.commit()
    logger.info("Updated collateral link for account %d", account.id)
    flash("Secured-by link updated.", "success")
    return redirect(url_for("loan.dashboard", account_id=account_id))
