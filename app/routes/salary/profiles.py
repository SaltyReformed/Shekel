"""
Shekel Budget App -- Salary route package: profile CRUD.

Create, list, edit, update, and soft-delete salary profiles, including
the auto-linked income transaction template created with each profile.
"""

import logging
from datetime import date

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup
from sqlalchemy.exc import SQLAlchemyError

from app.utils.auth_helpers import get_or_404, require_owner
from app.utils.dates import display_today
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.transaction_template import TransactionTemplate
from app.models.category import Category
from app.models.ref import (
    CalcMethod,
    DeductionTiming,
    FilingStatus,
    RaiseType,
)
from app import ref_cache
from app.enums import RecurrenceUnitEnum, TxnTypeEnum
from app.services import (
    account_service,
    paycheck_calculator,
    recurrence_engine,
    template_amount_service,
)
from app.services import pay_schedule_service
from app.services.balance_at import BalanceContext
from app.services.pay_calendar import PayCadence, cadence_for
from app.services.recurrence import RecurrenceSpec, author_rule
from app.services.generation_schedule import GenerationSchedule
from app.services.payroll_basis import PayrollBasis
from app.services.tax_config_service import load_tax_configs_for_year
from app.routes._commit_helpers import (
    DbErrorContext,
    StaleConflictContext,
    commit_or_handle_stale,
    handle_db_error,
    regenerate_commit_or_report,
)
from app.routes._redirect_target import RedirectTarget
from app.routes.salary._bp import salary_bp
from app.routes.salary._helpers import (
    _PROFILE_UPDATE_FIELDS,
    _create_schema,
    _get_investment_accounts,
    _regenerate_salary_transactions,
    _update_schema,
)

logger = logging.getLogger(__name__)


def _paychecks_per_year() -> "int | None":
    """Return how many paychecks the owner receives a year, or ``None``.

    **The form's read-only replacement for the ``pay_periods_per_year``
    dropdown** (plan step R-F16).  The engine divides the annual salary by this
    number, so the page has to state it or the gross it previews is
    unexplainable -- but it is not the owner's to choose HERE: it derives from
    ``budget.pay_schedule.cadence_days``, which the pay-period settings own,
    and offering a second control was the finding.

    ``None`` for an owner with no resolvable cadence, which the template
    renders as a pointer to generate a schedule.  Answered rather than raised:
    a form page must not 500 on the state the form itself is the fix for, and
    such an owner cannot create a profile either way (``_paycheck_template``
    needs a payday to seat the recurrence on).

    **Through ``resolve_cadence`` rather than ``cadence_for`` or a whole
    calendar**, and the two doors beside it are why each is wrong here.
    :func:`~app.services.pay_calendar.cadence_for` REFUSES the unresolvable
    owner, which is right for a producer of money and wrong for a form.
    **Since plan step ``pay_calendar:C4-d`` (ruling R-PC45) so does**
    :func:`~app.services.pay_calendar.calendar_for` -- this paragraph said it
    "answers without refusing", which was true of the empty cadence-less
    calendar that step deleted and is now false of both calendar doors.  It
    remains the wrong door here for its OTHER stated reason, which the step did
    not touch: it derives the owner's whole payday set to answer, 61 rows on
    production, on two pages that load a calendar for nothing else.
    ``resolve_cadence`` is the SOFT door and is what this form wants -- the one
    fact both of those read, asked directly, and answered rather than raised.

    Returns:
        The paycheck count as an ``int``, or ``None``.
    """
    cadence_days = pay_schedule_service.resolve_cadence(current_user.id)
    if cadence_days is None:
        return None
    return int(PayCadence(cadence_days=cadence_days).periods_per_year)


@salary_bp.route("/salary/new")
@login_required
@require_owner
def new_profile():
    """Display the salary profile creation form."""
    filing_statuses = db.session.query(FilingStatus).all()
    return render_template(
        "salary/form.html",
        profile=None,
        filing_statuses=filing_statuses,
        raise_types=[],
        deduction_timings=[],
        calc_methods=[],
        paychecks_per_year=_paychecks_per_year(),
        now_year=date.today().year,
    )


def _salary_category(user_id: int) -> Category:
    """Return this owner's ``Income: Salary`` category, creating it if absent.

    Every salary profile files its paycheck under one category, and a new owner
    has none until their first profile is created -- so the read and the create
    are one operation rather than a caller's two-step.

    Args:
        user_id: The owner.

    Returns:
        The persisted :class:`~app.models.category.Category`, flushed when
        newly created so it carries an id the caller can link.
    """
    existing = (
        db.session.query(Category)
        .filter_by(user_id=user_id, group_name="Income", item_name="Salary")
        .first()
    )
    if existing:
        return existing
    category = Category(
        user_id=user_id,
        group_name="Income",
        item_name="Salary",
        sort_order=0,
    )
    db.session.add(category)
    db.session.flush()
    return category


def _paycheck_template(
    data: dict, *, account_id: int, category_id: int, calendar,
) -> TransactionTemplate:
    """Create and flush the every-paycheck template a salary profile files through.

    The rule and the template are one operation: a salary profile's paycheck
    recurs every pay period by definition, so nothing chooses a cadence and
    nothing links the two afterwards.

    **The rule starts at the OPENING of the owner's schedule.**  Stated rather
    than implied since plan step R7c-b made ``starts_on`` required, and it is
    the same value an absent opening bound resolved to before -- so a new
    salary profile fans its paychecks across every pay period the owner has,
    closed ones included.  Plan ledger row **D34** carries whether it should.

    **The per-paycheck amount is the annual salary over the OWNER's paycheck
    count** (plan step R-F16).  It read a ``pay_periods_per_year`` off the
    submitted payload until then -- a second answer to a question the calendar
    this function already loads had already answered, and one that could
    disagree with it.  The docstring above is why there was never a second
    answer to give: a salary profile's paycheck recurs every pay period by
    definition, so the count of paychecks in a year IS the count of pay
    periods in a year.

    **The calendar is TAKEN rather than derived here** (pay-calendar plan step
    C2-f3c).  ``create_profile`` derives one anyway for the paycheck it then
    prices, so deriving a second one inside this helper made one POST answer
    "what is this owner's schedule" twice from two reads that a concurrent
    write could separate.

    Args:
        data: The validated create payload; read for the name and the annual
            salary.
        account_id: The non-loan deposit account the paychecks land in.
        category_id: This owner's ``Income: Salary`` category.
        calendar: The owner's :class:`~app.services.pay_calendar.PayCalendar`,
            read for the schedule's opening payday and for the paycheck count
            the annual salary is divided by.

    Returns:
        The flushed :class:`~app.models.transaction_template.TransactionTemplate`,
        carrying an id the profile can link.
    """
    template = TransactionTemplate(
        user_id=current_user.id,
        account_id=account_id,
        category_id=category_id,
        transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.INCOME),
        name=data["name"],
        default_amount=calendar.cadence.annual_to_per_paycheck(
            data["annual_salary"],
        ),
        is_active=True,
    )
    db.session.add(template)
    db.session.flush()
    # **The paycheck cadence is authored ONTO the template** (plan step R-F6):
    # the rule carries its owner's FK, so the definition has to exist first.
    # The order reversed here; the cadence itself is unchanged.
    author_rule(
        RecurrenceSpec(
            user_id=current_user.id,
            unit=RecurrenceUnitEnum.PERIOD,
            starts_on=calendar.opening_bound(),
        ),
        calendar,
        template,
    )
    return template


@salary_bp.route("/salary", methods=["POST"])
@login_required
@require_owner
def create_profile():
    """Create a new salary profile with auto-linked template."""
    errors = _create_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.new_profile"))

    data = _create_schema.load(request.form)

    # ONE read pass for the whole POST (plan step R7d-c-1): the baseline
    # scenario this branch refuses on, the owner's schedule every step below
    # reads, and the pass the generate runs in are one value rather than three
    # lookups that have to agree.
    ctx = BalanceContext.build(current_user.id)
    if ctx.scenario is None:
        flash(Markup(
            "No baseline scenario found. Please "
            '<a href="/register" class="alert-link">register a new account</a> '
            "to set up your budget."
        ), "danger")
        return redirect(url_for("salary.cockpit"))

    salary_category = _salary_category(current_user.id)

    # Get the default deposit account -- a NON-LOAN (non-amortizing) account.
    # A loan's balance is ledger-derived, not a transaction sum (ruling D4 /
    # finding N-11): depositing salary income onto a loan would have the
    # recurrence engine generate raw income transactions onto it
    # (``recurrence_engine.generate_for_template`` copies ``template.account_id``)
    # -- a cash leg the loan fold cannot see, the shape the transaction-create
    # routes (``_reject_transaction_on_loan``) and the template form also
    # refuse.  ``active_accounts_query(amortizing=False)`` is the shared
    # kind-boundary composer the grid's account pickers use (ruling D4 / A1).
    account = account_service.active_accounts_query(
        current_user.id, amortizing=False,
    ).first()
    if not account:
        flash(Markup(
            'You need an active account that is not a loan before creating a '
            'salary profile. '
            '<a href="' + url_for("accounts.new_account") + '" class="alert-link">'
            'Create an account</a>.'
        ), "danger")
        return redirect(url_for("salary.cockpit"))

    # Capture the requester id before the DB work below: the failure path
    # builds its DbErrorContext after a failed flush, where re-reading the
    # then-expired current_user attribute would touch the rolled-back
    # session (PendingRollbackError) rather than yield the id.
    user_id = current_user.id

    # The template's opening bound and per-paycheck amount, the generate pass,
    # and the net-pay recompute below all read the pass's own derivation of it
    # (pay-calendar plan step C2-f3c; plan step R7d-c-1 moved it onto the pass).
    calendar = ctx.calendar()

    try:
        template = _paycheck_template(
            data,
            account_id=account.id,
            category_id=salary_category.id,
            calendar=calendar,
        )

        # Create the salary profile
        profile = SalaryProfile(
            user_id=current_user.id,
            scenario_id=ctx.scenario_id,
            template_id=template.id,
            filing_status_id=data["filing_status_id"],
            name=data["name"],
            annual_salary=data["annual_salary"],
            state_code=data["state_code"],
            qualifying_children=data.get("qualifying_children", 0),
            other_dependents=data.get("other_dependents", 0),
            additional_income=data.get("additional_income", 0),
            additional_deductions=data.get("additional_deductions", 0),
            extra_withholding=data.get("extra_withholding", 0),
        )
        db.session.add(profile)
        db.session.flush()

        # Generate income transactions via recurrence engine.  The schedule
        # is the OWNER's whole one, off the same calendar the paycheck engine
        # prices against (plan step R4b-1).  ONE derivation answers both
        # (pay-calendar plan steps C2-f2d-3, C2-f3c).
        schedule = GenerationSchedule.for_pass(ctx)
        periods = calendar.saved()
        recurrence_engine.generate_for_template(
            template, schedule, ctx.scenario_id,
        )

        # Update the template's default_amount from gross to net so that
        # any future fallback (e.g. missing tax configs for a period)
        # uses the net amount rather than the gross.
        ref_period = (
            calendar.period_containing(date.today())
            or (periods[0] if periods else None)
        )
        if ref_period:
            # Resolved for the REFERENCE PERIOD's own tax year, matching the
            # key every other paycheck for this profile is computed under.
            tax_configs = load_tax_configs_for_year(
                current_user.id, profile, ref_period.start_date.year,
            )
            init_breakdown = paycheck_calculator.calculate_paycheck(
                PayrollBasis(profile, calendar), ref_period, tax_configs,
            )
            # Through the amount's one write door (plan step X-au-a).  The
            # profile above is already flushed and active, so the door sees a
            # salary-linked template: the column moves and NO version is
            # recorded, because a paycheck-calculated figure is derived, not a
            # price anybody stated.
            template_amount_service.set_amount(
                template, init_breakdown.earnings.net_pay,
                effective_on=display_today(),
            )

        db.session.commit()
    except SQLAlchemyError:
        # Narrow catch (C-46 / F-145): DB-tier failures (FK, CHECK,
        # NUMERIC range, OperationalError, etc.) produce the user-
        # facing flash + redirect.  Non-SQLAlchemy exceptions
        # (TypeError, AttributeError, decimal arithmetic) propagate
        # to the Flask 500 handler so they surface as bugs rather
        # than being silently swallowed.
        return handle_db_error(DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to create salary profile",
            log_args=(user_id,),
            flash_message="Failed to create salary profile. Please try again.",
            redirect=RedirectTarget("salary.new_profile"),
        ))

    logger.info("user_id=%d created salary profile %d", current_user.id, profile.id)
    flash(f"Salary profile '{profile.name}' created.", "success")
    return redirect(url_for("salary.edit_profile", profile_id=profile.id))


@salary_bp.route("/salary/<int:profile_id>/edit")
@login_required
@require_owner
def edit_profile(profile_id):
    """Display the salary profile edit form with raises and deductions."""
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    filing_statuses = db.session.query(FilingStatus).all()
    raise_types = db.session.query(RaiseType).all()
    deduction_timings = db.session.query(DeductionTiming).all()
    calc_methods = db.session.query(CalcMethod).all()
    investment_accounts = _get_investment_accounts(current_user.id)

    # The danger zone (restyled in P3) lists the user's deactivated
    # profiles with a Reactivate action.  Passed now so the producer
    # contract is complete; the current form template ignores it.
    inactive_profiles = (
        db.session.query(SalaryProfile)
        .filter_by(user_id=current_user.id, is_active=False)
        .order_by(SalaryProfile.sort_order, SalaryProfile.name)
        .all()
    )

    return render_template(
        "salary/form.html",
        profile=profile,
        filing_statuses=filing_statuses,
        raise_types=raise_types,
        deduction_timings=deduction_timings,
        calc_methods=calc_methods,
        investment_accounts=investment_accounts,
        inactive_profiles=inactive_profiles,
        paychecks_per_year=_paychecks_per_year(),
        now_year=date.today().year,
    )


@salary_bp.route("/salary/<int:profile_id>", methods=["POST"])
@login_required
@require_owner
def update_profile(profile_id):
    """Update a salary profile and recalculate linked transactions.

    Optimistic locking (commit C-18 / F-010): the edit form ships
    ``version_id`` as a hidden input.  When the submitted value
    differs from the row's current counter, the handler short-
    circuits with a flash + redirect so the audit trail records
    only the winner.  ``StaleDataError`` raised at flush time --
    e.g. by a concurrent edit that races past the form-side check
    -- is caught and converted to the same flash + redirect.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    errors = _update_schema.validate(request.form)
    if errors:
        flash("Please correct the highlighted errors and try again.", "danger")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    data = _update_schema.load(request.form)

    # Stale-form check (commit C-18 / F-010).
    submitted_version = data.pop("version_id", None)
    if submitted_version is not None and submitted_version != profile.version_id:
        logger.info(
            "Stale-form conflict on update_profile id=%d "
            "(submitted=%d, current=%d)",
            profile_id, submitted_version, profile.version_id,
        )
        flash(
            "This salary profile was changed by another action while you "
            "were editing.  Please reload and try again.",
            "warning",
        )
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    for field_name, value in data.items():
        if field_name in _PROFILE_UPDATE_FIELDS:
            setattr(profile, field_name, value)

    # Update linked template amount.  ``profile.template`` is eager
    # (lazy="joined"), so this touches no DB and stages safely before the
    # guard below picks up the commit.
    if profile.template and "annual_salary" in data:
        # The owner's paycheck count, off their cadence and from nowhere else
        # (plan step R-F16).  ``cadence_for`` rather than a whole calendar:
        # this needs the count and not the paydays.
        template_amount_service.set_amount(
            profile.template,
            cadence_for(current_user.id).annual_to_per_paycheck(
                data["annual_salary"],
            ),
            effective_on=display_today(),
        )
        if "name" in data:
            profile.template.name = data["name"]

    # Regenerate transactions and commit under the canonical optimistic-lock
    # guard (C-18 / F-010): the regeneration flushes, so it must run inside
    # the same stale-race guard as the commit.  ``StaleDataError`` and any
    # other DB error are reported by regenerate_commit_or_report (no
    # IntegrityError branch -- a profile edit has no expected unique
    # collision).
    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="update_profile",
            log_id=profile_id,
            flash_message=(
                "This salary profile was changed by another action while "
                "you were editing.  Please reload and try again."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to update salary profile %d",
            log_args=(current_user.id, profile_id),
            flash_message="Failed to update salary profile. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ),
    )
    if response is not None:
        return response

    logger.info("user_id=%d updated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' updated.", "success")
    return redirect(url_for("salary.edit_profile", profile_id=profile_id))


@salary_bp.route("/salary/<int:profile_id>/delete", methods=["POST"])
@login_required
@require_owner
def delete_profile(profile_id):
    """Soft-delete a salary profile and deactivate its template.

    Optimistic locking (commit C-18 / F-010): the
    ``is_active = False`` flush is version-pinned by SQLAlchemy.
    A concurrent edit raises ``StaleDataError`` which the handler
    converts into a flash + redirect.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    profile.is_active = False
    if profile.template:
        profile.template.is_active = False
        # The template's amount stops being DERIVED the moment the profile is
        # archived (plan step X-au-a): with no ACTIVE profile the recurrence
        # engine prices its rows from ``default_amount``
        # (``recurrence_engine._get_transaction_amount``), so that column
        # becomes the definition's stated price and the write door opens its
        # series at it.  Without this the template would satisfy
        # ``owns_its_amount`` while holding NO version -- an eligible
        # definition with an empty series, which is the one gap
        # ``amount_as_of`` reports as ``None`` and which plan step X-au-b's
        # resolver is specified to REFUSE rather than fall back on.  Found by
        # adversarial review; measured at 58 rows on production's one salary
        # template.
        template_amount_service.set_amount(
            profile.template, profile.template.default_amount,
            effective_on=display_today(),
        )

    conflict = commit_or_handle_stale(StaleConflictContext(
        logger=logger,
        log_label="delete_profile",
        log_id=profile_id,
        flash_message=(
            "This salary profile was changed by another action.  "
            "Please reload and try again."
        ),
        redirect=RedirectTarget("salary.cockpit"),
    ))
    if conflict is not None:
        return conflict
    logger.info("user_id=%d deactivated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' deactivated.", "info")
    return redirect(url_for("salary.cockpit"))


@salary_bp.route("/salary/<int:profile_id>/reactivate", methods=["POST"])
@login_required
@require_owner
def reactivate_profile(profile_id):
    """Reactivate a soft-deleted salary profile (inverse of delete_profile).

    Restores ``is_active`` on the profile and its linked template, then
    regenerates the salary transactions so the grid picks the income back
    up.  An already-active profile is a no-op with an info flash rather
    than a 404 (it is owned and simply needs no action).

    Optimistic locking (commit C-18 / F-010): the reactivation flushes
    (regeneration) then commits under the canonical
    :func:`regenerate_commit_or_report` guard, so a concurrent edit's
    ``StaleDataError`` converts to a flash + redirect like the sibling
    mutation routes.
    """
    profile = get_or_404(SalaryProfile, profile_id)
    if profile is None:
        abort(404)

    if profile.is_active:
        flash(f"Salary profile '{profile.name}' is already active.", "info")
        return redirect(url_for("salary.edit_profile", profile_id=profile_id))

    profile.is_active = True
    if profile.template:
        profile.template.is_active = True

    response = regenerate_commit_or_report(
        lambda: _regenerate_salary_transactions(profile),
        stale_ctx=StaleConflictContext(
            logger=logger,
            log_label="reactivate_profile",
            log_id=profile_id,
            flash_message=(
                "This salary profile was changed by another action.  "
                "Please reload and try again."
            ),
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ),
        error_ctx=DbErrorContext(
            logger=logger,
            log_message="user_id=%d failed to reactivate salary profile %d",
            log_args=(current_user.id, profile_id),
            flash_message="Failed to reactivate salary profile. Please try again.",
            redirect=RedirectTarget("salary.edit_profile", {"profile_id": profile_id}),
        ),
    )
    if response is not None:
        return response

    logger.info("user_id=%d reactivated salary profile %d", current_user.id, profile_id)
    flash(f"Salary profile '{profile.name}' reactivated.", "success")
    return redirect(url_for("salary.edit_profile", profile_id=profile_id))
