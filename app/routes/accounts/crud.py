"""
Shekel Budget App -- Account CRUD Routes

Account create / read / update / archive / hard-delete endpoints.
Split out of the historical monolithic ``app/routes/accounts.py``
in Commit 21 of the financial-calculation audit follow-up (F-1);
behaviour preserved verbatim from the pre-split file.

The optimistic-lock contract (commit C-17 / F-009) operates at two
tiers in this module's update / archive / hard-delete routes: a
pre-flush ``version_id`` comparison against the form value (catches
the sequential Tab-1 / Tab-2 race) and the SQLAlchemy
``version_id_col`` ``WHERE version_id = ?`` at flush time (catches
the truly-concurrent interleaving the form-side check cannot see).
Both layers convert ``StaleDataError`` into a flash + redirect so
the user can retry against fresh row state.

The C-28 / F-044 multi-tenant ownership guard for
``ref.account_types`` lives in :mod:`app.utils.account_validation`
(``_account_type_is_visible``, ``_visible_account_types``); routes
in this file call those helpers rather than inlining the guard.
"""

import logging
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import ref_cache
from app.enums import (
    AcctTypeEnum,
    CompoundingFrequencyEnum,
    EmployerContributionTypeEnum,
)
from app.exceptions import ValidationError
from app.extensions import db
from app.models.account import Account, AccountAnchorHistory
from app.models.asset_appreciation_params import AssetAppreciationParams
from app.models.interest_params import InterestParams
from app.models.investment_params import InvestmentParams
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.loan_params import LoanParams
from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._commit_helpers import (
    StaleConflictContext,
    commit_or_handle_stale,
    regenerate_and_commit_or_stale,
)
from app.routes._redirect_target import RedirectTarget
from app.routes.accounts._bp import accounts_bp
from app.services import (
    account_service,
    entry_service,
    pay_period_service,
    transfer_service,
)
from app.services.account_projection import AccountProjectionKind, classify_account
from app.utils import archive_helpers
from app.utils.account_validation import (
    _create_schema,
    _validate_update_account,
    _account_type_is_visible,
    _visible_account_types,
)
from app.utils.auth_helpers import fresh_login_required, get_or_404, require_owner

logger = logging.getLogger(__name__)

# Field allowlist for the account update route: which submitted form
# fields may be written back to the Account via setattr.
_ACCOUNT_UPDATE_FIELDS = {"name", "account_type_id", "sort_order", "is_active"}


# ── Account CRUD ───────────────────────────────────────────────────


@accounts_bp.route("/accounts")
@login_required
@require_owner
def list_accounts():
    """Redirect the retired ``/accounts`` table to the unified cockpit.

    Loop B P4 retired the standalone management table: the Net Worth
    Cockpit (``savings.dashboard``) now both displays AND manages
    accounts -- inline click-to-edit balances, a per-card kebab carrying
    Edit and Archive, and hard-delete relocated to the shared edit
    form's danger zone (developer ruling 2026-06-25, audit decision 12).
    The endpoint is kept as a permanent redirect, not deleted, so that
    external bookmarks of ``/accounts`` still resolve and the
    unauthenticated-redirect contract in
    ``tests/test_routes/test_auth_required.py`` stays green; every
    in-app caller was repointed directly at ``savings.dashboard``.
    """
    return redirect(url_for("savings.dashboard"))


@accounts_bp.route("/accounts/new", methods=["GET"])
@login_required
@require_owner
def new_account():
    """Display the account creation form.

    The type dropdown is scoped to seeded built-ins plus the current
    owner's custom types (commit C-28 / F-044).
    """
    return render_template(
        "accounts/form.html",
        account=None,
        account_types=_visible_account_types(current_user.id),
    )


def _auto_create_type_params(account, kind):
    """Auto-create the type-specific params row for a new parameterized account.

    Dispatches on the account's :class:`AccountProjectionKind`: interest
    accounts get an ``apy=0`` :class:`InterestParams`, investment/retirement
    accounts a default :class:`InvestmentParams`, and Property
    (appreciating) accounts an ``annual_appreciation_rate=0``
    :class:`AssetAppreciationParams`.  Each row carries an explicit zero
    sentinel (E-12: zero is a value, not missing) so no projection runs on a
    silent server-default before the user configures the real value on the
    type-specific setup page.  Does not commit; the caller owns the
    transaction boundary.

    Args:
        account: The freshly-created :class:`~app.models.account.Account`.
        kind: The account's :class:`AccountProjectionKind`.
    """
    if kind is AccountProjectionKind.INTEREST:
        if not db.session.query(InterestParams).filter_by(account_id=account.id).first():
            # #38: compounding frequency is a ref FK, so the auto-create
            # supplies the DAILY id explicitly (an FK id is not a static
            # literal).  HIGH-06: explicit ``apy=0`` (no server_default) so
            # no ghost 4.5% interest is ever projected.
            db.session.add(InterestParams(
                account_id=account.id, apy=Decimal("0"),
                compounding_frequency_id=ref_cache.compounding_frequency_id(
                    CompoundingFrequencyEnum.DAILY,
                ),
            ))
    elif kind is AccountProjectionKind.INVESTMENT:
        if not db.session.query(InvestmentParams).filter_by(account_id=account.id).first():
            # #38: employer-contribution type is a ref FK, so the
            # auto-create supplies the NONE id explicitly.
            db.session.add(InvestmentParams(
                account_id=account.id,
                employer_contribution_type_id=(
                    ref_cache.employer_contribution_type_id(
                        EmployerContributionTypeEnum.NONE,
                    )
                ),
            ))
    elif kind is AccountProjectionKind.APPRECIATING:
        # Property: the user sets the real rate on the property detail page.
        if not db.session.query(AssetAppreciationParams).filter_by(
            account_id=account.id,
        ).first():
            db.session.add(AssetAppreciationParams(
                account_id=account.id,
                annual_appreciation_rate=Decimal("0"),
            ))


def _setup_redirect_url(account, kind):
    """Return the post-create redirect URL for a new account.

    Parameterized accounts go to their type-specific setup page (the
    established setup-page pattern); everything else returns to the unified
    accounts cockpit (``savings.dashboard``, the retired ``/accounts`` table's
    successor).  Resolving the URL here keeps :func:`create_account` within
    Pylint's branch and return-count limits.

    Args:
        account: The freshly-created :class:`~app.models.account.Account`.
        kind: The account's :class:`AccountProjectionKind`.

    Returns:
        str: The ``url_for`` target to redirect to.
    """
    if kind is AccountProjectionKind.INTEREST:
        return url_for("accounts.interest_detail", account_id=account.id, setup=1)
    if kind is AccountProjectionKind.AMORTIZING:
        return url_for("loan.dashboard", account_id=account.id, setup=1)
    if kind is AccountProjectionKind.INVESTMENT:
        return url_for("investment.dashboard", account_id=account.id, setup=1)
    if kind is AccountProjectionKind.APPRECIATING:
        return url_for("accounts.property_detail", account_id=account.id, setup=1)
    return url_for("savings.dashboard")


@accounts_bp.route("/accounts", methods=["POST"])
@login_required
@require_owner
def create_account():
    """Create a new account."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("accounts.new_account"))

    data = _create_schema.load(request.form)

    # Multi-tenant guard (commit C-28 / F-044): the submitted
    # account_type_id must reference a seeded built-in or one of
    # this owner's own custom types.  A forged post that points at
    # another owner's custom type is collapsed into the same
    # "Invalid account type." response as a non-existent FK so the
    # response cannot be used to probe for the existence of other
    # owners' catalogues.
    if not _account_type_is_visible(data["account_type_id"], current_user.id):
        flash("Invalid account type.", "danger")
        return redirect(url_for("accounts.new_account"))

    # Check for duplicate name.
    existing = (
        db.session.query(Account)
        .filter_by(user_id=current_user.id, name=data["name"])
        .first()
    )
    if existing:
        flash("An account with that name already exists.", "warning")
        return redirect(url_for("accounts.new_account"))

    # ``anchor_balance`` is an optional Decimal field; the schema's
    # ``@pre_load`` strips empty submissions, so a missing key -- not a
    # falsy zero -- means "no opening balance".  Branch on presence
    # (``is None``), never on Decimal truthiness: a legitimately-entered
    # zero opening balance is a value, not a missing balance.
    raw_anchor = data.pop("anchor_balance", None)
    anchor_balance = (
        Decimal(str(raw_anchor)) if raw_anchor is not None else Decimal("0")
    )

    # E-19 (Commit 3): the canonical factory in
    # ``app.services.account_service.create_account`` materializes
    # the account row AND a matching origination AccountAnchorHistory
    # row, and resolves the anchor period from the user's pay-period
    # inventory.  If the user has zero pay periods, the factory
    # raises ``ValidationError``; this route converts that into a
    # redirect to ``/pay-periods/generate`` so the user can fix the
    # missing-periods state and retry.
    # ``account_type_id`` and ``name`` are required ``AccountCreateSchema``
    # fields, so they are always present in ``data``; lift them out into
    # the ``AccountSpec`` and forward any remaining validated columns
    # through ``**data`` (the same passthrough the prior ``**data`` splat
    # provided).
    account_type_id = data.pop("account_type_id")
    name = data.pop("name")
    try:
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=current_user.id,
                account_type_id=account_type_id,
                name=name,
                anchor_balance=anchor_balance,
                notes="origination",
            ),
            **data,
        )
    except ValidationError:
        flash(
            "Generate pay periods before creating an account so the "
            "account balance has a period to anchor against.",
            "warning",
        )
        return redirect(url_for("pay_periods.generate_form"))

    # Auto-create the type-specific params row and resolve the setup-page
    # redirect by projection kind.  The canonical classifier owns the
    # taxonomy, so a parameterised physical asset (Property -> APPRECIATING)
    # is never mistaken for an investment -- the bug a bare ``has_parameters
    # and not has_interest and not has_amortization`` predicate introduces.
    kind = classify_account(account)
    _auto_create_type_params(account, kind)
    db.session.commit()

    logger.info("Created account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' created.", "success")

    return redirect(_setup_redirect_url(account, kind))


@accounts_bp.route("/accounts/<int:account_id>/edit", methods=["GET"])
@login_required
@require_owner
def edit_account(account_id):
    """Display the account edit form."""
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    return render_template(
        "accounts/form.html",
        account=account,
        account_types=_visible_account_types(current_user.id),
    )


@accounts_bp.route("/accounts/<int:account_id>", methods=["POST"])
@login_required
@require_owner
def update_account(account_id):
    """Update an account.

    The form payload also accepts ``anchor_balance`` and writes it
    through with the same ``AccountAnchorHistory`` audit trail as
    :func:`true_up`, so both anchor-balance edit paths stay
    consistent.  Like that route, this route is owner-only but no
    longer step-up gated: anchor balances are reversible,
    version-locked values, not credentials.

    Optimistic locking (commit C-17 / F-009) operates in two layers:

      1. Stale-form check: the edit form ships ``version_id`` as a
         hidden input set to the row's counter at render time.  When
         the submitted value differs from the current
         ``Account.version_id``, the handler short-circuits with a
         flash + redirect (renders well in a non-HTMX flow) and
         records nothing.  This catches the sequential Tab-1/Tab-2
         race documented in the C-17 manual verification.

      2. SQLAlchemy ``version_id_col``: any concurrent flush that
         races past the stale-form check is still narrowed by
         ``WHERE version_id = ?`` at the database tier; the loser
         raises ``StaleDataError`` which the handler converts into
         the same flash + redirect path.  The two layers together
         close every interleaving the optimistic-lock contract is
         meant to cover.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Validation phase.  Delegates to a helper that returns either
    # ``(data, None)`` (proceed) or ``({}, (message, category))``
    # (reject).  Folding every non-mutating check into a single
    # gateway keeps the route's return count below Pylint's R0911
    # limit after the C-28 multi-tenant guard was added.
    data, failure = _validate_update_account(account, request.form, current_user.id)
    if failure is not None:
        flash(failure[0], failure[1])
        return redirect(url_for("accounts.edit_account", account_id=account_id))

    # Handle anchor balance update with audit trail.  Tracking
    # ``anchor_changed`` separately from ``new_anchor`` is necessary
    # because the in-place ``account.current_anchor_balance =
    # new_anchor`` mutates the field used for the equality check;
    # a later ``new_anchor != account.current_anchor_balance`` would
    # always be False and skip the reconcile call.  The flag is set
    # exactly when the balance actually changed.
    new_anchor = data.pop("anchor_balance", None)
    anchor_changed = False
    if new_anchor is not None:
        new_anchor = Decimal(str(new_anchor))
        if new_anchor != account.current_anchor_balance:
            anchor_changed = True
            current_period = pay_period_service.get_current_period(current_user.id)
            account.current_anchor_balance = new_anchor
            if current_period:
                account.current_anchor_period_id = current_period.id
                history = AccountAnchorHistory(
                    account_id=account.id,
                    pay_period_id=current_period.id,
                    anchor_balance=new_anchor,
                )
                db.session.add(history)

    for field, value in data.items():
        if field in _ACCOUNT_UPDATE_FIELDS:
            setattr(account, field, value)

    # Reconcile entries on checking true-ups and commit.  Both
    # operations live inside the same try/except because
    # ``clear_entries_for_anchor_true_up`` autoflushes the pending
    # Account mutation before issuing its own bulk UPDATE -- the
    # version-pinned WHERE clause is checked at autoflush time, so
    # ``StaleDataError`` would otherwise escape outside the catch.
    # See the matching comment in :func:`true_up`.
    checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)

    def _clear_anchor_entries_if_changed():
        """Clear checking entries on an anchor true-up (in-transaction step)."""
        if anchor_changed and account.account_type_id == checking_type_id:
            entry_service.clear_entries_for_anchor_true_up(
                current_user.id, account.id,
            )

    # The clear-entries step must run inside the same stale-race guard as
    # the commit (it flushes and can itself raise StaleDataError).
    conflict = regenerate_and_commit_or_stale(
        _clear_anchor_entries_if_changed,
        ctx=StaleConflictContext(
            logger=logger,
            log_label="update_account",
            log_id=account_id,
            flash_message=(
                "This account was changed by another action while you were "
                "editing.  Please reload and try again."
            ),
            redirect=RedirectTarget(
                "accounts.edit_account",
                {"account_id": account_id},
            ),
        ),
    )
    if conflict is not None:
        return conflict

    logger.info("Updated account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' updated.", "success")
    return redirect(url_for("savings.dashboard"))


@accounts_bp.route("/accounts/<int:account_id>/archive", methods=["POST"])
@login_required
@require_owner
def archive_account(account_id):
    """Archive an account (soft delete).

    The Account model carries a ``version_id_col`` (commit C-17),
    so a concurrent mutation interleaving with this archive will
    raise ``StaleDataError`` at flush time.  The handler converts
    it into a flash + redirect so the user can retry against the
    fresh row state instead of seeing a 500.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Guard: prevent archiving if active transfer templates reference this account.

    active_transfers = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == current_user.id,
            TransferTemplate.is_active.is_(True),
            db.or_(
                TransferTemplate.from_account_id == account_id,
                TransferTemplate.to_account_id == account_id,
            ),
        )
        .first()
    )
    if active_transfers:
        flash(
            "Cannot archive this account -- it is used by active recurring transfers. "
            "Archive those recurring transfers first.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    account.is_active = False
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="archive_account",
        log_id=account_id,
        flash_message=(
            "This account was changed by another action.  Please reload "
            "the page and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    ))
    if conflict is not None:
        return conflict
    logger.info("Archived account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' archived.", "info")
    return redirect(url_for("savings.dashboard"))


@accounts_bp.route("/accounts/<int:account_id>/unarchive", methods=["POST"])
@login_required
@require_owner
def unarchive_account(account_id):
    """Unarchive an account.

    See :func:`archive_account` for the optimistic-lock contract.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    account.is_active = True
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="unarchive_account",
        log_id=account_id,
        flash_message=(
            "This account was changed by another action.  Please reload "
            "the page and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    ))
    if conflict is not None:
        return conflict
    logger.info("Unarchived account: %s (id=%d)", account.name, account.id)
    flash(f"Account '{account.name}' unarchived.", "success")
    return redirect(url_for("savings.dashboard"))


@accounts_bp.route("/accounts/<int:account_id>/hard-delete", methods=["POST"])
@login_required
@require_owner
@fresh_login_required()
def hard_delete_account(account_id):
    """Permanently delete an account if it has no blocking dependents.

    Guard chain (checked in order):
      1. Ownership -- account exists and belongs to current user.
      2. Transfer template guard -- any TransferTemplate (active or
         archived) referencing this account blocks deletion because the
         FK is ON DELETE RESTRICT.
      3. Transaction template guard -- any TransactionTemplate (active
         or archived) referencing this account blocks deletion for the
         same FK reason.
      4. History check -- any non-deleted Transaction referencing this
         account triggers archive-instead-of-delete.

    Permanent delete cleanup:
      After all guards pass, remaining RESTRICT-FK rows must be
      explicitly removed before the account row can be deleted:
        - Transfer rows (soft-deleted or ghost ad-hoc) referencing this
          account, deleted through transfer_service to maintain shadow
          invariants.
        - Transaction rows (soft-deleted ghosts) referencing this
          account.
      CASCADE-FK dependents (LoanParams, InterestParams,
      InvestmentParams, AccountAnchorHistory, SavingsGoal, LoanFeatures)
      are auto-deleted by PostgreSQL when the account row is removed.
    """
    account = get_or_404(Account, account_id)
    if account is None:
        abort(404)

    # Guard 2: transfer templates with RESTRICT FK.
    blocking_xfer_template = (
        db.session.query(TransferTemplate)
        .filter(
            TransferTemplate.user_id == current_user.id,
            db.or_(
                TransferTemplate.from_account_id == account_id,
                TransferTemplate.to_account_id == account_id,
            ),
        )
        .first()
    )
    if blocking_xfer_template:
        flash(
            "Cannot delete this account -- it is used by recurring transfers. "
            "Delete those recurring transfers first.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    # Guard 3: transaction templates with RESTRICT FK.
    blocking_txn_template = (
        db.session.query(TransactionTemplate)
        .filter_by(account_id=account_id, user_id=current_user.id)
        .first()
    )
    if blocking_txn_template:
        flash(
            "Cannot delete this account -- it has recurring transactions. "
            "Delete those recurring transactions first.",
            "warning",
        )
        return redirect(url_for("savings.dashboard"))

    # Guard 4: transaction history (any non-deleted transaction).
    if archive_helpers.account_has_history(account.id):
        flash(
            f"'{account.name}' has transaction history and cannot be permanently "
            "deleted. It has been archived instead.",
            "warning",
        )
        if account.is_active:
            account.is_active = False
            conflict = commit_or_handle_stale(StaleConflictContext(
                logger=logger,
                log_label="hard_delete_account archive-fallback",
                log_id=account_id,
                flash_message=(
                    "This account was changed by another action.  "
                    "Please reload the page and try again."
                ),
                redirect=RedirectTarget("savings.dashboard"),
            ))
            if conflict is not None:
                return conflict
        return redirect(url_for("savings.dashboard"))

    # All guards passed -- permanently delete.
    # Step 1: delete remaining Transfer rows (soft-deleted or ghost
    # ad-hoc) through the transfer service to maintain shadow invariants.
    remaining_transfers = (
        db.session.query(Transfer)
        .filter(db.or_(
            Transfer.from_account_id == account_id,
            Transfer.to_account_id == account_id,
        ))
        .all()
    )
    for xfer in remaining_transfers:
        transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)

    # Step 2: delete remaining Transaction rows (soft-deleted ghosts
    # whose RESTRICT FK would block the account deletion).
    db.session.query(Transaction).filter(
        Transaction.account_id == account_id,
    ).delete(synchronize_session="fetch")

    # Step 3: explicitly delete CASCADE-FK dependents that lack ORM
    # relationships on Account.  Without explicit relationships,
    # SQLAlchemy's unit of work tries to SET NULL on their account_id
    # column before the DB-level CASCADE fires, violating NOT NULL.
    db.session.query(LoanParams).filter_by(account_id=account_id).delete()
    db.session.query(InterestParams).filter_by(account_id=account_id).delete()
    db.session.query(InvestmentParams).filter_by(account_id=account_id).delete()
    db.session.query(AssetAppreciationParams).filter_by(
        account_id=account_id,
    ).delete()
    db.session.query(EscrowComponent).filter_by(account_id=account_id).delete()
    db.session.query(RateHistory).filter_by(account_id=account_id).delete()
    db.session.query(SavingsGoal).filter_by(account_id=account_id).delete()

    # Step 4: delete the account.  AccountAnchorHistory is handled by
    # the ORM relationship cascade="all, delete-orphan" on Account.
    # The DELETE narrows by version_id thanks to the optimistic-lock
    # contract; a concurrent UPDATE that bumped the version since
    # this request loaded the row raises StaleDataError, which the
    # handler converts into a flash + redirect rather than a 500.
    account_name = account.name
    db.session.delete(account)
    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="hard_delete_account",
        log_id=account_id,
        flash_message=(
            "This account was changed by another action.  Please reload "
            "the page and try again."
        ),
        redirect=RedirectTarget("savings.dashboard"),
    ))
    if conflict is not None:
        return conflict

    flash(f"Account '{account_name}' permanently deleted.", "info")
    return redirect(url_for("savings.dashboard"))
