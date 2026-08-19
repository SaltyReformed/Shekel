"""
Shekel Budget App -- Recurrence Engine: WHAT a generated row's definition says

:class:`DerivedRowFields` -- the single statement of which columns a generated
row takes from its template rather than from its owner -- its producer, and the
salary-profile resolution and paycheck pricing that stand behind the one field
of it that is not a plain copy.

**One statement of the derived columns is what lets a regeneration UPDATE a row
instead of destroying and rebuilding it** (plan step R10-a, ruling **R-R19**).
Before it, the create path listed these fields inline and no update path
existed, so "make this row match its template again" could only be spelled as
"throw it away and build another" -- which took the owner's purchases, notes
and flags with it (finding **N-292**).
"""
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from app.services.recurrence_engine._plan import compute_due_date

logger = logging.getLogger(__name__)



class DerivedRowFields(NamedTuple):
    """What a template and a pay period DERIVE on a generated transaction.

    **THE one statement of which columns a generated row takes from its
    DEFINITION rather than from its owner**, and the reason
    :func:`regenerate_for_template` no longer destroys the rows it maintains.

    Both write paths consume it: :func:`generate_for_template` splats it into a
    new ``Transaction``, and :func:`regenerate_for_template` assigns it onto an
    existing one.  So a column added here is written on a NEW row and kept
    current on an EXISTING one from the same edit -- which is the property that
    made delete-and-recreate look necessary in the first place.  Before plan
    step R10-a the create path listed these fields inline and no update path
    existed, so "make the row match the template again" could only be spelled
    as "throw the row away and build another".  That cost the owner every
    column a template does NOT derive: the purchases recorded against the row
    (``transaction_entries`` CASCADE from their parent), its ``notes``, its
    ``is_envelope`` and ``companion_visible`` flags, its ``created_at``, and
    its own id -- finding **N-292**, measured at 3 purchase records worth
    ``$499.82`` on one live row, destroyed with no prompt by an edit as small
    as a rename.

    Every field here is derived and none is the owner's, which is what makes
    overwriting one on an existing row safe.  The three columns that decide
    whether the row is the RULE's at all -- ``is_override``, ``is_deleted`` and
    ``status_id`` -- are deliberately absent: they are the classification the
    caller applies BEFORE deciding to write, never something a write restates.

    **``amount_source_id`` is absent too, and that one is a considered
    omission rather than a category** (adversarial review of plan step R10-a).
    ``ck_transactions_amount_ownership`` pairs it with ``estimated_amount`` --
    exactly one of the two is ever set -- so assigning an amount onto a row
    already in the DERIVED state would violate the CHECK.  No writer sets that
    column today, so that state is unreachable; plan steps X-au-d..i are the
    ones that create it.  If they land before this is revisited the failure is
    a loud ``IntegrityError`` at flush, not a wrong figure, whereas carrying
    the field here would SILENTLY un-derive such a row.  Loud is the better
    failure, so the field stays out until the step that owns the semantics
    arrives.  Ledger row **N-293**.

    Attributes:
        account_id: The account the row's money moves through, from the
            template.  **The one derived field whose change is not always
            applicable**: ``fk_transaction_entries_parent_account`` binds a
            purchase's account to its parent's, so moving a row that holds
            purchases moves them too and invalidates any statement link they
            carry.  ``_maintain._classify_maintain_work`` routes that case to
            the owner, as a RETAINED conflict, instead of applying it.
        name: The template's name.  Also propagated to rows OUTSIDE this pass's
            reach by ``routes.templates.crud._apply_fields_and_propagate_rename``,
            which covers the historic and immutable rows a regeneration never
            touches.
        category_id: The template's category, or ``None``.
        transaction_type_id: Expense or income, from the template.
        estimated_amount: The period's own figure -- the template's default, or
            the paycheck calculator's answer for a salary-linked template, via
            :func:`_get_transaction_amount` against the OWNER's whole schedule.
        due_date: Derived from the rule and the period by
            :func:`compute_due_date`.
    """

    account_id: int
    name: str
    category_id: int | None
    transaction_type_id: int
    estimated_amount: Decimal
    due_date: date | None




def _derive_row_fields(template, rule, salary_profile, period, schedule):
    """Resolve what *template* and *period* derive on a generated row.

    The single producer of :class:`DerivedRowFields`, so the create path and
    the maintain path cannot disagree about what a generated row's definition
    says -- see that class for why one statement of it is what lets a
    regeneration UPDATE a row instead of destroying and rebuilding it.

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            being generated from.
        rule: The template's recurrence rule, already confirmed present by
            :func:`resolve_generation_plan` (``GenerationPlan.rule``).
        salary_profile: The active salary profile driving this template's
            amounts, or ``None`` -- resolved ONCE per pass by
            :func:`_get_salary_profile` and threaded in, rather than re-read per
            row.
        period: The :class:`~app.models.pay_period.PayPeriod` this row lives in
            -- the ORM row, because the row being written carries its id.
        schedule: The pass's
            :class:`~app.services.generation_schedule.GenerationSchedule`.  The
            amount is priced against ``schedule.calendar`` -- the OWNER's WHOLE
            schedule, never the write window; see :func:`_get_transaction_amount`
            for the $502.45 that distinction was worth.

    Returns:
        The :class:`DerivedRowFields` for this (template, period) pair.
    """
    # The paycheck engine takes DERIVED periods (pay-calendar plan step
    # C2-f2d-3) while the row being written takes the ORM row's id, so the
    # pricing period is looked up on the schedule's own calendar.  It is TOTAL
    # for this argument rather than merely usually present, and it takes TWO
    # of ``GenerationSchedule.__post_init__``'s arms rather than one: the
    # calendar-IS-the-schedule check makes every ``periods`` id a calendar id,
    # and the separate stray-window check makes every ``write_periods`` id a
    # ``periods`` id.  *period* comes from ``write_periods``.
    priced_period = schedule.calendar.period_by_id(period.id)
    return DerivedRowFields(
        account_id=template.account_id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        estimated_amount=_get_transaction_amount(
            template, salary_profile, priced_period, schedule.calendar,
        ),
        due_date=compute_due_date(rule, period),
    )




def _get_salary_profile(template):
    """Return the ACTIVE salary profile driving this template's amounts, or None.

    Generation needs the PROFILE, not the boolean -- it prices each row from it
    (:func:`_get_transaction_amount`) -- and plan step X-au-d deletes both
    together when generation stops pricing salary rows.  Its boolean twin is
    :func:`app.services.template_amount_service.is_salary_linked_template`,
    which moved out of this module at plan step X-au-a so that the recurrence
    engine can read the amount series at X-au-e without closing an import loop.

    **Both read the same RELATIONSHIP rather than issuing two queries**, so
    "an active salary profile names this template" has one implementation: an
    adversarial review noted the split the moment the boolean moved out.  The
    collection is identity-mapped, which is also what lets a caller archiving a
    profile see its own pending change (see the twin's docstring).

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            to read.

    Returns:
        The active :class:`~app.models.salary_profile.SalaryProfile`, or ``None``.
    """
    return next(
        (profile for profile in template.salary_profiles if profile.is_active),
        None,
    )




def _get_transaction_amount(template, salary_profile, period, calendar):
    """Determine the transaction amount, using paycheck calculator if salary-linked.

    Resolves tax configs for the period's OWN tax year via the shared
    ``load_tax_configs_for_year`` SSOT, which substitutes the latest
    CONFIGURED year at or before it when that year has none.  The salary
    projection page and the
    live net-pay recompute (``income_service.live_projected_net``) resolve
    the SAME way (DH-#30), so the grid's stored income amount and the
    salary page's live-calculated net pay agree on which year's brackets
    and FICA wage base/cap apply -- they cannot silently diverge.

    **The engine's period set must be the OWNER's WHOLE schedule, and passing
    the caller's window instead was a live money defect** (plan ledger row
    **D25**, closed at plan step R4b-1).  Since plan step **R-F16** this takes
    the CALENDAR rather than a period sequence, which makes that a property of
    the type instead of a rule stated here: a
    :class:`~app.services.pay_calendar.PayCalendar` is built only from a
    complete payday set, and its cadence -- the paycheck count the engine
    divides by -- comes off the same derivation as its periods, so the two
    cannot be sourced from different reads.  ``calculate_paycheck`` reads the
    period set for FIVE separate judgements, every one of which needs periods
    the pass itself is not writing into: the annual rounding reconciliation
    (``_gross_biweekly_for_period``), THIRD-PAYCHECK detection
    (``_is_third_paycheck``), the first-paycheck-of-month deductions
    (``_is_first_paycheck_of_month``), the FICA wage-base cumulative
    (``_get_cumulative_wages``), and a deduction's ANNUAL CAP
    (``_cumulative_deduction_before``, whose own docstring names the identical
    hazard -- a partial context under-counts the cumulative and defers the cap,
    so the deduction keeps being charged after it should have stopped).  The
    fifth was missing from an earlier draft of this paragraph and an
    adversarial review added it.  ``period_population`` hands the engines only
    the NEWLY created periods, so a schedule extend used to answer all four
    from a 1-3 period sample.

    Measured 2026-08-08 on a streamed clone of production: transaction 2756,
    pay period 2028-06-29 -- the THIRD paycheck of June 2028 -- was generated
    by an extend at **$2,814.45** where the whole schedule gives
    **$3,316.90**.  The extend could not see the other two June paychecks, so
    it did not know this was a third one and applied the deductions a third
    paycheck skips: the stored amount is **$502.45 low**.  Every future extend
    landing on a third paycheck would have written another.

    **What the stale amount did and did not reach, measured rather than
    reasoned.**  The balance projection and the grid CELL both recompute
    projected salary income at read time
    (``income_service.live_projected_net``, threaded through
    ``cash_ledger.live_amount_overrides``), so neither ever showed the stale
    figure: on an unmigrated clone the live recompute answers $3,316.90 for
    that row, and a period-by-period balance diff over both accounts and all
    61 periods moves by exactly the three deleted ``Phone Allowance`` income
    rows and by nothing else.  What the stale column DOES reach is the grid's
    inline amount editor, which pre-fills from
    ``Transaction.estimated_amount`` -- and saving that form sets
    ``is_override = True`` (``routes/transactions/mutations.py``), the very
    flag that EXCLUDES a row from the live recompute.  So the wrong figure was
    one click away from becoming the projection, permanently.
    """
    if salary_profile is None:
        return template.default_amount

    try:
        # Local imports: the tax-config / paycheck fallback tests patch the
        # SOURCE modules (app.services.tax_config_service.load_tax_configs and
        # app.services.paycheck_calculator.calculate_paycheck -- the
        # testing-standards-preferred patch target).  A module-level
        # ``from ... import`` would bind the name once at import and not see
        # the patch, so these imports stay local.
        # Pylint: ``import-outside-toplevel`` -- kept local so the fallback
        # tests' patches of app.services.paycheck_calculator take effect.
        from app.services import paycheck_calculator  # pylint: disable=import-outside-toplevel
        # Pylint: ``import-outside-toplevel`` -- kept local so the fallback
        # tests' patch of
        # app.services.tax_config_service.load_tax_configs_for_year takes
        # effect; a module-level import would bind the name before the patch.
        from app.services.tax_config_service import load_tax_configs_for_year  # pylint: disable=import-outside-toplevel

        # Resolve the period's own tax year, substituting the latest
        # CONFIGURED year at or before it when that year has none (else
        # future-year periods would produce zero withholding and the grid
        # would disagree with the salary page).  The rule is owned ONCE by
        # load_tax_configs_for_year, the SSOT shared with the salary
        # projection and the year-end summary (DH-#30).
        tax_configs = load_tax_configs_for_year(
            salary_profile.user_id, salary_profile, period.start_date.year,
        )

        # Load calibration override if the profile has one.
        calibration = getattr(salary_profile, "calibration", None)

        breakdown = paycheck_calculator.calculate_paycheck(
            paycheck_calculator.PayrollBasis(salary_profile, calendar.cadence),
            period, calendar.saved(), tax_configs,
            calibration=calibration,
        )
        return breakdown.earnings.net_pay

    except (InvalidOperation, ZeroDivisionError, TypeError, KeyError) as exc:
        logger.error(
            "Paycheck calculation failed for salary profile %d in "
            "period %s: %s. Using template default_amount.",
            salary_profile.id,
            period.start_date,
            exc,
        )
        return template.default_amount
