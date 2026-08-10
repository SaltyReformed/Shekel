"""
Shekel Budget App -- Loan route package: parameter management.

Initial loan-parameter creation, parameter updates, and the dated balance
true-up (an append-only ``user_trueup`` :class:`LoanAnchorEvent`; the
origination event write is retired -- the origination anchor is synthesized
from the immutable :class:`LoanParams`).  All three are
redirect-style POST handlers that flash and return to the dashboard.
"""

import logging
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models.account import Account
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
from app.services import (
    anchor_service,
    cash_ledger,
    loan_loaders,
    loan_posting_service,
    loan_recurrence_sync,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.account_validation import (
    INVALID_COLLATERAL_LINK,
    _validate_collateral_link,
)
from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.digit_strings import parse_row_id

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
            anchor_balance=cash_ledger.resolve_anchor(account).balance,
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
            anchor_balance=cash_ledger.resolve_anchor(account).balance,
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

    # NO origination LoanAnchorEvent is written (the read switch's final
    # commit retired it): the origination anchor is a verbatim copy of the
    # immutable LoanParams fields, so every consumer -- the genesis posting
    # walk and the resolver's replay fallback -- SYNTHESIZES it from the
    # params via ``loan_loaders.load_loan_anchor_facts``.  Only a user
    # balance true-up appends a ``user_trueup`` event (the operator's
    # assertion, a real fact with no other home).

    # Posting ledger (read switch): now that the params / origination rate
    # exist, reconcile the loan's full genesis ledger.  For a brand-new
    # loan this posts the OPENING (-original_principal onto the loan, its
    # positive onto a per-loan opening-equity account) in the baseline scenario
    # -- the payment-less case the all-scenarios sync covers by including the
    # baseline.  A loan that had payments settled before it was configured (not
    # yet resolvable, so uncorrected) also gets those payments' split
    # corrections back-posted here.
    loan_posting_service.sync_loan_postings_all_scenarios(account.id)
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

    # Posting ledger: a params edit can change the origination rate (via
    # ``_upsert_origination_rate`` above) OR the ``payment_day`` -- both move
    # the confirmed-payment split (the rate drives interest; ``payment_day``
    # drives the monthly-due-date eligibility boundary) AND any true-up's
    # ``owed_before`` (the running balance a later true-up corrects from), so
    # re-sync every scenario's full genesis ledger UNCONDITIONALLY, not only on
    # the rate path.
    loan_posting_service.sync_loan_postings_all_scenarios(account.id)
    # Re-bound the recurring payment before committing.  This edit can move
    # BOTH ends: a term / rate change moves the projected payoff (end_date,
    # R-4), and a PAYMENT-DAY change moves the loan's first contractual
    # installment (start_date, C9a) -- the one derived bound on this rule a
    # params edit can shift.
    loan_recurrence_sync.sync_recurring_payment_bounds(account.id)
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
      * UNCHANGED: the submission asserts the ``(date, balance)`` the
        governing ``user_trueup`` already asserts (the user double-clicked
        or a network retry replayed the submission), so nothing was
        written; the route treats it as idempotent success and redirects
        with an informational flash.  **It was a unique-index rejection
        until ruling R-EQ** (plan step X-f1c4b), which could not tell that
        retry from a deliberate re-assertion and refused both.

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

    if outcome is AnchorTrueUpOutcome.UNCHANGED:
        # Ruling R-EQ idempotent success path: the governing true-up already
        # asserts this (date, balance).  No new row, and the on-display value
        # is already what was submitted; flash an informational message and
        # redirect.
        flash(
            "Loan balance already recorded for that date.",
            "info",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # R-4: the true-up re-bases the balance, moving the projected payoff.
    # ``apply_loan_anchor_true_up`` already committed the event + posting
    # re-sync, so this sets the recurring payment's end_date and commits it in a
    # follow-on transaction (self-healing: a failure here re-syncs at the next
    # loan mutation).
    loan_recurrence_sync.sync_recurring_payment_bounds(account.id)
    db.session.commit()

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


@loan_bp.route("/accounts/<int:account_id>/loan/tracking-start", methods=["POST"])
@login_required
@require_owner
def record_tracking_start(account_id):
    """Record a mid-life-import tracking-start opening (a ``tracking_start`` event).

    For an already-amortizing loan the operator began tracking mid-life: the user
    asserts "when I started tracking, my real balance was $X as of date D."  The
    handler appends a ``tracking_start`` :class:`LoanAnchorEvent`, which the
    genesis walk loads as an ordinary ``is_opening=False`` balance ASSERTION
    (:func:`app.services.loan_loaders.load_loan_anchor_facts`) that RESETS the
    running balance at its own date -- so a date at or after it reads the
    operator's real balance rather than an amortized guess, while a date before
    it reads the origination opening held flat.  The origination fields on
    :class:`LoanParams` are untouched (they still drive the amortization schedule
    / projection).

    *It does NOT become the loan's opening, and this docstring said it did until
    plan step X-an-b*, citing ``loan_loaders._opening_anchor_fact`` -- a function
    step C1 deleted along with that behaviour.  Origination is the opening
    ALWAYS: opening at a mid-life tracking-start read the loan out of existence
    for its whole pre-tracking window (the false pre-opening zero, finding B-11).

    Validation chain (mirrors :func:`true_up_balance`, plus the ordering guard):

      1. ``_require_configured_loan`` rejects cross-owner / non-loan / unconfigured
         accounts.
      2. :class:`LoanAnchorTrueupSchema` (reused -- identical fields) enforces
         ``anchor_balance >= 0`` and ``anchor_date <= today``.
      3. The route enforces ``anchor_date >= params.origination_date`` (a loan
         cannot be tracked before it existed) and ``anchor_date`` STRICTLY BEFORE
         the earliest recorded payment's due date -- otherwise that payment would
         sort before the opening in the walk and be subsumed (dropped).  Both are
         route-level because the schema has no access to the loan.

    Outcomes mirror the true-up: COMMITTED (success flash + redirect) or
    UNCHANGED (idempotent success when the governing ``tracking_start`` already
    asserts this ``(date, balance)``).  The comparison is scoped to the
    ``tracking_start`` source, so a re-submitted opening is recognised even
    after true-ups have been recorded on later dates -- which a
    latest-row-of-any-source rule would have missed, because an opening is by
    definition the earliest anchor.

    A tracking-start is meant to be the FIRST anchor recorded (the opening).  A
    ``user_trueup`` dated earlier than the tracking-start is not rejected here;
    its only effect is cosmetic (the drift scorecard would show the opening's
    ``computed`` as that true-up's balance rather than 0) -- the genesis walk's
    reset-at-every-anchor still reconstructs the correct final balance, and both
    correction legs still sum to zero.
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
    anchor_balance = Decimal(str(data["anchor_balance"]))

    if anchor_date < params.origination_date:
        flash(
            "Tracking-start date cannot be before the loan's origination "
            f"date ({params.origination_date.isoformat()}).",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    scenario = get_baseline_scenario(current_user.id)
    scenario_id = scenario.id if scenario else None
    earliest_due = (
        loan_loaders.earliest_settled_payment_due_date(account.id, scenario_id)
        if scenario_id is not None else None
    )
    if earliest_due is not None and anchor_date >= earliest_due:
        flash(
            "Tracking-start date must be before your earliest recorded "
            f"payment ({earliest_due.strftime('%b %-d, %Y')}).",
            "danger",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    outcome = anchor_service.record_loan_tracking_start(
        account=account,
        anchor_balance=anchor_balance,
        anchor_date=anchor_date,
    )

    if outcome is AnchorTrueUpOutcome.UNCHANGED:
        flash(
            "Tracking-start balance already recorded for that date.",
            "info",
        )
        return redirect(url_for("loan.dashboard", account_id=account_id))

    # A tracking-start re-bases the opening balance, moving the projected payoff;
    # re-bound the recurring payment's window (mirrors the true-up route).
    # ``record_loan_tracking_start`` already committed the event + posting
    # re-sync, so this commits the bound in a follow-on transaction.
    loan_recurrence_sync.sync_recurring_payment_bounds(account.id)
    db.session.commit()

    logger.info(
        "Loan tracking-start: account %d set to $%s as of %s",
        account.id, anchor_balance, anchor_date,
    )
    flash(
        f"Recorded tracking-start balance of ${anchor_balance:,.2f} "
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
    emergent net-worth math never reads it.

    **Exactly ``""`` clears the link; anything else -- including the field
    being ABSENT -- is refused** (plan step X-ae).  Those are different inputs
    and this route used to answer them all the same way: ``""`` is the picker's
    own blank option, a deliberate "nothing secures this loan", while any other
    value, or no field at all, cannot come from the picker.  A browser
    rendering this form always submits the ``<select>``, so an absent field is
    a forged or truncated POST, not a choice.  Clearing on the second meant a
    forged field silently destroyed a real link under a success flash -- and,
    on 128 of the characters ``str.isdigit()`` accepts, raised into an
    unhandled 500 before it got that far (finding N-136).  A value that names
    no id now gets the same
    :data:`~app.utils.account_validation.INVALID_COLLATERAL_LINK` answer as an
    id naming no account, and nothing is written.

    **The submission is NOT stripped, and that is the ruling rather than an
    omission.**  A ``.strip()`` here re-opened the hole twice over: it maps
    every Unicode space to ``""`` -- ``"\\xa0".strip()`` is ``""`` -- so a
    forged non-breaking space took the CLEAR path under a success flash, the
    exact behaviour the paragraph above says is closed; and it normalised a
    value before applying the shared rule, so ``" 2 "`` linked here while the
    reconcile and companion doors refused it, leaving four doors with three
    behaviours.  Both were found by adversarial review of the first build.

    Every value that DOES name an id is validated by
    :func:`app.utils.account_validation._validate_collateral_link`
    (same-owner Asset target, no self-link, source is an amortizing
    liability) before it is written.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # The picker submits an Asset account id or "" (clear); the guard takes
    # everything else, so the validator below sees only ``None`` or a real id.
    # ``None`` (the field absent entirely) is NOT the blank option and does not
    # clear: a browser rendering this form always submits the select, so an
    # absent field is the same forged or truncated POST the guard refuses --
    # defaulting it to "" put it back on the clear path, which an adversarial
    # review caught destroying a real link under a success flash.
    submitted = request.form.get("collateral_account_id")
    collateral_account_id = None
    if submitted != "":
        collateral_account_id = parse_row_id(submitted)
        if collateral_account_id is None:
            flash(*INVALID_COLLATERAL_LINK)
            return redirect(url_for("loan.dashboard", account_id=account_id))

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
