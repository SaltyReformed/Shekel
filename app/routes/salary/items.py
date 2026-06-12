"""
Shekel Budget App -- Salary route package: line items (raises + deductions).

Add, edit, and delete the two parallel families of salary line item -- pay
raises and paycheck deductions -- both of which regenerate the linked
salary transactions on every change.  The two families are deliberately
co-located and kept as explicit parallel implementations: they differ on
the model, schema, percentage conversion, unique constraint, user-facing
messages, and HTMX partial, so a single generic CRUD helper would couple
two distinct domains behind a many-parameter interface (a worse
abstraction than the parallel code).  The shared cross-cutting concern --
committing the regenerated transactions and reporting every recoverable
failure (the stale-lock conflict, the expected unique-constraint
collision, and other DB errors) -- IS factored out, through
:func:`app.routes._commit_helpers.regenerate_commit_or_report`.
"""

import logging
from decimal import Decimal

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.utils.auth_helpers import get_or_404, get_owned_via_parent, require_owner
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.salary_raise import SalaryRaise
from app.models.paycheck_deduction import PaycheckDeduction
from app import ref_cache
from app.enums import CalcMethodEnum
from app.utils.db_errors import is_unique_violation
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    UniqueViolationContext,
    handle_db_error,
    regenerate_commit_or_report,
)
from app.routes._redirect_target import RedirectTarget
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _DEDUCTION_UPDATE_FIELDS,
    _PAYCHECK_DEDUCTIONS_UNIQUE_CONSTRAINT,
    _RAISE_UPDATE_FIELDS,
    _SALARY_RAISES_UNIQUE_CONSTRAINT,
    _deduction_schema,
    _deduction_update_schema,
    _raise_schema,
    _raise_update_schema,
    _regenerate_salary_transactions,
    _respond_after_deduction_change,
    _respond_after_raise_change,
)

logger = logging.getLogger(__name__)


# ── Raises ─────────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/raises", methods=["POST"])
@login_required
@require_owner
def add_raise(profile_id):
    """Add a raise to a salary profile."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    errors = _raise_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    data = _raise_schema.load(request.form)
    # Handle checkbox -- form sends "on" or nothing
    data["is_recurring"] = request.form.get("is_recurring") == "on"

    # Convert percentage input (e.g. 3 → 0.03) for storage.
    if data.get("percentage") is not None:
        data["percentage"] = Decimal(str(data["percentage"])) / Decimal("100")

    salary_raise = SalaryRaise(salary_profile_id=profile.id, **data)
    db.session.add(salary_raise)

    # Capture the requester id on the clean session up front; the failure
    # path builds its DbErrorContext after a failed flush, where reading the
    # expired current_user attribute would hit the rolled-back session.
    user_id = current_user.id

    try:
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except IntegrityError as exc:
        # Duplicate-raise double-submit (F-051 / C-23): the composite
        # unique ``uq_salary_raises_profile_type_year_month`` rejects
        # the second INSERT when the user clicks Save twice in a row,
        # the browser retries on a flaky network, or the back button
        # is used to re-submit the form.  Roll back and treat as
        # idempotent success: the user lands on the edit page with
        # the raise they intended to create regardless of which
        # request reached the database first, so neither path
        # surfaces the constraint name as a 500.
        db.session.rollback()
        if not is_unique_violation(exc, _SALARY_RAISES_UNIQUE_CONSTRAINT):
            logger.exception(
                "user_id=%d failed to add raise to profile %d "
                "(unexpected IntegrityError)",
                user_id, profile_id,
            )
            flash("Failed to add raise. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile_id))
        logger.info(
            "Duplicate salary raise prevented on profile %d "
            "(idempotent success)", profile_id,
        )
        flash(
            "A raise with that type and effective date already "
            "exists on this profile.",
            "info",
        )
        return _respond_after_raise_change(profile)
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): the IntegrityError branch
        # above covers unique-constraint and other constraint
        # violations.  Remaining DB-tier errors (DataError on
        # numeric range, OperationalError on connection loss,
        # etc.) land here.  Non-SQLAlchemy exceptions propagate
        # to the 500 handler.
        return handle_db_error(DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to add raise to profile %d",
            log_args=(user_id, profile_id),
            flash_message="Failed to add raise. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ))

    logger.info("user_id=%d added raise to profile %d", current_user.id, profile_id)
    flash("Raise added.", "success")

    return _respond_after_raise_change(profile)


@salary_bp.route("/salary/raises/<int:raise_id>/delete", methods=["POST"])
@login_required
@require_owner
def delete_raise(raise_id):
    """Remove a raise from a salary profile.

    Optimistic locking (commit C-18 / F-010): the DELETE statement is
    version-pinned by SQLAlchemy; a concurrent edit raises
    :class:`StaleDataError`, converted to a flash + redirect by the
    canonical :func:`regenerate_commit_or_report` guard.
    """
    salary_raise = get_owned_via_parent(
        SalaryRaise, raise_id, "salary_profile",
    )
    if salary_raise is None:
        abort(404)

    profile = salary_raise.salary_profile

    # Stage the deletion (no DB I/O yet); the flush + commit happen inside
    # the stale guard below, so a concurrent-edit StaleDataError raised by
    # the delete's flush is caught there.
    db.session.delete(salary_raise)

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="delete_raise",
            log_id=raise_id,
            flash_message=(
                "This raise was changed by another action.  "
                "Please reload and try again."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to delete raise %d from profile %d",
            log_args=(current_user.id, raise_id, profile.id),
            flash_message="Failed to remove raise. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d deleted raise %d from profile %d",
        current_user.id, raise_id, profile.id,
    )
    flash("Raise removed.", "info")

    return _respond_after_raise_change(profile)


@salary_bp.route("/salary/raises/<int:raise_id>/edit", methods=["POST"])
@login_required
@require_owner
def update_raise(raise_id):
    """Update an existing raise on a salary profile.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input populated by app.js.  A stale
    submission is rejected with a flash + redirect; the
    SQLAlchemy-tier check catches the truly-concurrent case at
    flush time and produces the same response.

    Recoverable failures during the regenerate + commit are delegated to
    :func:`regenerate_commit_or_report`, which returns the flash +
    redirect for each: the flush-time :class:`StaleDataError`
    (C-18/F-010), the expected duplicate-key
    :class:`~sqlalchemy.exc.IntegrityError` (F-051/C-23, surfaced as a
    warning), and any other DB error (C-46/F-145, a danger flash).  The
    route keeps only the input-validation and stale-form pre-check guard
    clauses.
    """
    salary_raise = get_owned_via_parent(
        SalaryRaise, raise_id, "salary_profile",
    )
    if salary_raise is None:
        abort(404)

    profile = salary_raise.salary_profile

    errors = _raise_update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    data = _raise_update_schema.load(request.form)
    data["is_recurring"] = request.form.get("is_recurring") == "on"

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != salary_raise.version_id:
        logger.info(
            "Stale-form conflict on update_raise id=%d "
            "(submitted=%d, current=%d)",
            raise_id, submitted_version, salary_raise.version_id,
        )
        flash(
            "This raise was changed by another action while you were "
            "editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    # Convert percentage input (e.g. 3 → 0.03) for storage.
    if data.get("percentage") is not None:
        data["percentage"] = Decimal(str(data["percentage"])) / Decimal("100")

    for field_name, value in data.items():
        if field_name in _RAISE_UPDATE_FIELDS:
            setattr(salary_raise, field_name, value)

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="update_raise",
            log_id=raise_id,
            flash_message=(
                "This raise was changed by another action while you were "
                "editing.  Please reload and try again."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to update raise %d on profile %d",
            log_args=(current_user.id, raise_id, profile.id),
            flash_message="Failed to update raise. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
        # Duplicate-key collision on update (F-051 / C-23): the user edited
        # this raise's (type, year, month) tuple onto one another active
        # raise on the same profile already holds -- a recoverable warning,
        # not a 500.  Any other IntegrityError falls through to error_ctx.
        on_integrity=UniqueViolationContext(
            logger=logger,
            constraint=_SALARY_RAISES_UNIQUE_CONSTRAINT,
            log_message=(
                "Duplicate-key conflict on update_raise id=%d "
                "(another raise already covers this profile/type/date)"
            ),
            log_args=(raise_id,),
            flash_message=(
                "Another raise on this profile already covers that "
                "type and effective date.  Edit or remove it before "
                "applying these changes."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
    )
    if response is not None:
        return response

    logger.info("user_id=%d updated raise %d on profile %d", current_user.id, raise_id, profile.id)
    flash("Raise updated.", "success")

    return _respond_after_raise_change(profile)


# ── Deductions ─────────────────────────────────────────────────────


@salary_bp.route("/salary/<int:profile_id>/deductions", methods=["POST"])
@login_required
@require_owner
def add_deduction(profile_id):
    """Add a deduction to a salary profile."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    errors = _deduction_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    data = _deduction_schema.load(request.form)
    data["inflation_enabled"] = request.form.get("inflation_enabled") == "on"

    # Convert percentage inputs (e.g. 6 → 0.06) for storage.
    if data["calc_method_id"] == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE):
        data["amount"] = Decimal(str(data["amount"])) / Decimal("100")
    if data.get("inflation_rate") is not None:
        data["inflation_rate"] = Decimal(str(data["inflation_rate"])) / Decimal("100")

    deduction = PaycheckDeduction(salary_profile_id=profile.id, **data)
    db.session.add(deduction)

    # Capture the requester id on the clean session up front; the failure
    # path builds its DbErrorContext after a failed flush, where reading the
    # expired current_user attribute would hit the rolled-back session.
    user_id = current_user.id

    try:
        _regenerate_salary_transactions(profile)
        db.session.commit()
    except IntegrityError as exc:
        # Duplicate-deduction double-submit (F-052 / C-23): the
        # composite unique ``uq_paycheck_deductions_profile_name``
        # rejects the second INSERT when the user clicks Save
        # twice in a row, the browser retries on a flaky network,
        # or a deactivated deduction with the same name still
        # exists on the profile.  Roll back and treat as
        # idempotent success: the user lands on the edit page with
        # the deduction they intended to create regardless of
        # which request reached the database first.
        db.session.rollback()
        if not is_unique_violation(exc, _PAYCHECK_DEDUCTIONS_UNIQUE_CONSTRAINT):
            logger.exception(
                "user_id=%d failed to add deduction to profile %d "
                "(unexpected IntegrityError)",
                user_id, profile_id,
            )
            flash("Failed to add deduction. Please try again.", "danger")
            return redirect(url_for("salary.edit_profile", profile_id=profile_id))
        attempted_name = data.get("name", "")
        logger.info(
            "Duplicate paycheck deduction prevented on profile %d "
            "(name=%r, idempotent success)",
            profile_id, attempted_name,
        )
        flash(
            f"A deduction named '{attempted_name}' already exists "
            f"on this profile.  Edit or reactivate it instead of "
            f"creating a duplicate.",
            "info",
        )
        return _respond_after_deduction_change(profile)
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): the IntegrityError branch
        # above covers unique-constraint and other constraint
        # violations.  Remaining DB-tier errors (DataError on
        # numeric range, OperationalError on connection loss,
        # etc.) land here.  Non-SQLAlchemy exceptions propagate
        # to the 500 handler.
        return handle_db_error(DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to add deduction to profile %d",
            log_args=(user_id, profile_id),
            flash_message="Failed to add deduction. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ))

    logger.info("user_id=%d added deduction to profile %d", current_user.id, profile_id)
    flash(f"Deduction '{deduction.name}' added.", "success")

    return _respond_after_deduction_change(profile)


@salary_bp.route("/salary/deductions/<int:ded_id>/delete", methods=["POST"])
@login_required
@require_owner
def delete_deduction(ded_id):
    """Remove a deduction from a salary profile.

    Optimistic locking (commit C-18 / F-010): the DELETE statement is
    version-pinned by SQLAlchemy; a concurrent edit raises
    :class:`StaleDataError`, converted to a flash + redirect by the
    canonical :func:`regenerate_commit_or_report` guard.
    """
    deduction = get_owned_via_parent(
        PaycheckDeduction, ded_id, "salary_profile",
    )
    if deduction is None:
        abort(404)

    profile = deduction.salary_profile

    # Stage the deletion (no DB I/O yet); the flush + commit happen inside
    # the stale guard below, so a concurrent-edit StaleDataError raised by
    # the delete's flush is caught there.
    db.session.delete(deduction)

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="delete_deduction",
            log_id=ded_id,
            flash_message=(
                "This deduction was changed by another action.  "
                "Please reload and try again."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to delete deduction %d from profile %d",
            log_args=(current_user.id, ded_id, profile.id),
            flash_message="Failed to remove deduction. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d deleted deduction %d from profile %d",
        current_user.id, ded_id, profile.id,
    )
    flash("Deduction removed.", "info")

    return _respond_after_deduction_change(profile)


@salary_bp.route("/salary/deductions/<int:ded_id>/edit", methods=["POST"])
@login_required
@require_owner
def update_deduction(ded_id):
    """Update an existing deduction on a salary profile.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input populated by app.js.  A stale
    submission is rejected with a flash + redirect; the
    SQLAlchemy-tier check catches the truly-concurrent case at
    flush time and produces the same response.

    Recoverable failures during the regenerate + commit are delegated to
    :func:`regenerate_commit_or_report`, which returns the flash +
    redirect for each: the flush-time :class:`StaleDataError`
    (C-18/F-010), the expected name-collision
    :class:`~sqlalchemy.exc.IntegrityError` (F-052/C-23, surfaced as a
    warning), and any other DB error (C-46/F-145, a danger flash).  The
    route keeps only the input-validation and stale-form pre-check guard
    clauses.
    """
    deduction = get_owned_via_parent(
        PaycheckDeduction, ded_id, "salary_profile",
    )
    if deduction is None:
        abort(404)

    profile = deduction.salary_profile

    errors = _deduction_update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    data = _deduction_update_schema.load(request.form)
    data["inflation_enabled"] = request.form.get("inflation_enabled") == "on"

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != deduction.version_id:
        logger.info(
            "Stale-form conflict on update_deduction id=%d "
            "(submitted=%d, current=%d)",
            ded_id, submitted_version, deduction.version_id,
        )
        flash(
            "This deduction was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile.id))

    # Convert percentage inputs (e.g. 6 → 0.06) for storage.
    if data["calc_method_id"] == ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE):
        data["amount"] = Decimal(str(data["amount"])) / Decimal("100")
    if data.get("inflation_rate") is not None:
        data["inflation_rate"] = Decimal(str(data["inflation_rate"])) / Decimal("100")

    for field_name, value in data.items():
        if field_name in _DEDUCTION_UPDATE_FIELDS:
            setattr(deduction, field_name, value)

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="update_deduction",
            log_id=ded_id,
            flash_message=(
                "This deduction was changed by another action while you "
                "were editing.  Please reload and try again."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to update deduction %d on profile %d",
            log_args=(current_user.id, ded_id, profile.id),
            flash_message="Failed to update deduction. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
        # Name-collision rename (F-052 / C-23): the user renamed this
        # deduction onto a name another active or inactive deduction on the
        # same profile already holds -- a recoverable warning, not a 500.
        # Any other IntegrityError falls through to error_ctx.
        on_integrity=UniqueViolationContext(
            logger=logger,
            constraint=_PAYCHECK_DEDUCTIONS_UNIQUE_CONSTRAINT,
            log_message=(
                "Duplicate-name conflict on update_deduction id=%d "
                "(another deduction with this name exists on the profile)"
            ),
            log_args=(ded_id,),
            flash_message=(
                "Another deduction on this profile already uses that "
                "name.  Choose a different name or remove the existing "
                "deduction first."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile.id}),
        ),
    )
    if response is not None:
        return response

    logger.info(
        "user_id=%d updated deduction %d on profile %d",
        current_user.id, ded_id, profile.id,
    )
    flash(f"Deduction '{deduction.name}' updated.", "success")

    return _respond_after_deduction_change(profile)
