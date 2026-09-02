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
from decimal import InvalidOperation
from typing import NamedTuple

from app.models.amount_ownership import AmountOwnership
from app.services.payroll_basis import PayrollBasis
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

    **The amount is carried as OWNERSHIP rather than as a figure, and that
    settles what used to be a deliberate omission** (plan step **X-au-k**,
    closing finding **N-293**).  This class carried ``estimated_amount`` and
    NOT ``amount_source_id``, because the two were separately mapped columns
    that ``ck_transactions_amount_ownership`` pairs one-to-one: splatting a
    figure onto a row already DERIVED wrote one half of the pair and aborted
    the whole template edit at flush.  Carrying both fields would have fixed
    the abort and introduced a silent un-derive, so the field stayed out and
    the abort was accepted as the better failure.  Neither is expressible now:
    :class:`~app.models.amount_ownership.AmountOwnership` is ONE attribute, so
    this class states the row's whole ownership or none of it.

    **What the splat can still do is hand a DERIVED row back to its owner**,
    which is ledger row **N-437** and is X-au-e's to answer -- that step stops
    generation pricing rows at all, at which point this class carries no amount
    and the question has no site left to ask it at.  It is unreachable today
    for a reason the DATABASE holds rather than a census: the maintain pass
    selects on ``template_id``, and ``ck_transactions_one_pricing_link`` makes
    that column exclusive with ``transfer_id``, which is the only link whose
    rows are derived before X-au-e runs.

    Attributes:
        account_id: The account the row's money moves through, from the
            template.  **The one derived field whose change is not always
            applicable**: ``fk_transaction_entries_parent_account`` binds a
            purchase's account to its parent's, so moving a row that holds
            purchases moves them too and invalidates any statement link they
            carry.  ``_recurrence_common.classify_maintain_work`` routes that case to
            the owner, as a RETAINED conflict, instead of applying it.
        name: The template's name.  Also propagated to rows OUTSIDE this pass's
            reach by ``routes.templates.crud._apply_fields_and_propagate_rename``,
            which covers the historic and immutable rows a regeneration never
            touches.
        category_id: The template's category, or ``None``.
        transaction_type_id: Expense or income, from the template.
        amount_ownership: The period's own figure, stated as the row OWNING it
            -- the template's default, or the paycheck calculator's answer for
            a salary-linked template, via :func:`_get_transaction_amount`
            against the OWNER's whole schedule.  It is ``own`` and never
            ``derived`` because a generated row still stores what its
            definition priced it at; plan step X-au-e is what stops that.
        due_date: Derived from the rule and the period by
            :func:`compute_due_date`.
    """

    account_id: int
    name: str
    category_id: int | None
    transaction_type_id: int
    amount_ownership: AmountOwnership
    due_date: date | None




def _derive_row_fields(template, rule, salary_profile, period, calendar):
    """Resolve what *template* and *period* derive on a generated row.

    The single producer of :class:`DerivedRowFields`, so the create path and
    the maintain path cannot disagree about what a generated row's definition
    says -- see that class for why one statement of it is what lets a
    regeneration UPDATE a row instead of destroying and rebuilding it.

    **It took a ``GenerationSchedule`` and looked *period* up on that value's
    calendar until pay-calendar plan step C2-f3c.**  The paycheck engine has
    priced from DERIVED periods since plan step C2-f2d-3, while the placement
    carried the ORM row, so this function converted one into the other with a
    ``period_by_id`` scan per generated row.  The placement now carries the
    derived period itself, so the round trip has no subject -- and with the
    lookup gone the pass's write WINDOW was the only thing this read off the
    schedule that it never used, which is why the parameter is the calendar.

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            being generated from.
        rule: The template's recurrence rule, already confirmed present by
            :func:`resolve_generation_plan` (``GenerationPlan.rule``).
        salary_profile: The active salary profile driving this template's
            amounts, or ``None`` -- resolved ONCE per pass by
            :func:`_get_salary_profile` and threaded in, rather than re-read per
            row.
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` this row
            lives in, straight off its ``PlannedOccurrence``.
        calendar: The OWNER's whole pay calendar -- never a window; see
            :func:`_get_transaction_amount` for the $502.45 that distinction
            was worth.

    Returns:
        The :class:`DerivedRowFields` for this (template, period) pair.
    """
    return DerivedRowFields(
        account_id=template.account_id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        amount_ownership=AmountOwnership.own(
            _get_transaction_amount(template, salary_profile, period, calendar),
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
    cannot be sourced from different reads.  **Since plan step
    balance:X-bh-1 that calendar is what the engine itself takes**, on the
    :class:`~app.services.payroll_basis.PayrollBasis`, so this function no
    longer hands it a window at all and there is no argument left here for a
    caller to narrow.  ``calculate_paycheck`` counts paydays for FOUR separate
    judgements, every one of which needs paydays the pass itself is not writing
    into: THIRD-PAYCHECK detection, the first-paycheck-of-month deductions, the
    FICA wage-base cumulative and a deduction's ANNUAL CAP (a partial context
    under-counted the cumulative and DEFERRED the cap, so the deduction kept
    being charged after it should have stopped).  The LAST of them was missing
    from an earlier draft of this paragraph and an adversarial review added it.
    ``period_population`` hands the engines only the NEWLY created periods, so
    a schedule extend used to answer all of them from a 1-3 period sample.

    **A FIFTH judgement read the period set until plan step balance:X-aw and no
    longer does**: the per-period GROSS, which distributed a rounding residue
    across whichever rows existed (finding **N-239**).  It is now the salary
    over the cadence and nothing else
    (:func:`~app.services.payroll_basis.gross_per_paycheck`).  **Ledger row
    N-390 closed at plan step balance:X-bh-2**, which made the calendar answer
    BELOW its opening payday too -- so a generated salary row is now priced
    against the owner's whole rhythm rather than against the part of it the
    app has rows for.

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
            PayrollBasis(salary_profile, calendar), period, tax_configs,
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
