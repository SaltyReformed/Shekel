"""
Shekel Budget App -- Recurrence Engine: WHAT a generated row's definition says

:class:`DerivedRowFields` -- the single statement of which columns a generated
row takes from its template rather than from its owner -- and its producer.

**One statement of the derived columns is what lets a regeneration UPDATE a row
instead of destroying and rebuilding it** (plan step R10-a, ruling **R-R19**).
Before it, the create path listed these fields inline and no update path
existed, so "make this row match its template again" could only be spelled as
"throw it away and build another" -- which took the owner's purchases, notes
and flags with it (finding **N-292**).

**THIS MODULE NO LONGER PRICES A PAYCHECK, and that is plan step X-au-d.**  It
carried ``_get_salary_profile`` and ``_get_transaction_amount``: the second ran
``paycheck_calculator.calculate_paycheck`` over the owner's whole schedule and
stored the answer in ``estimated_amount``, which ruling **R-FI** calls a cache
of a derivation and finding **N-224** measured as an app holding two answers to
one question.  A salary-linked template's row is now DECLARED derived
(:func:`_generated_amount_ownership`) and stores no figure, so a ROW's amount
has exactly one producer --
:class:`app.services.income_service.SalaryPricing`, read through amount rule 2.
(The projection itself is still spelled twice more, on the salary page and the
cockpit: finding **N-443**.)
**A generator that prices nothing cannot mis-price**, which is what retires the
whole class of defect ``a3f8b1c40d92`` was written to repair: the ``$502.45``
that a window-narrowed period list put into one stored salary row.
"""
from datetime import date
from typing import NamedTuple

from app.enums import AmountSourceEnum
from app.models.amount_ownership import AmountOwnership
from app.services import template_amount_service
from app.services.amount_ownership import derived_ownership
from app.services.recurrence_engine._plan import compute_due_date



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
        amount_ownership: WHERE this row's amount comes from, as ruling
            **R-FI**'s one attribute rather than a figure
            (:func:`_generated_amount_ownership`).  A definition that STATES
            its price gives the row that figure to own; one whose price is
            COMPUTED -- a salary-linked template since plan step X-au-d -- gives
            the row a declaration and no figure at all.  Plan step X-au-e moves
            the remaining templates onto their own price series, after which
            every generated row is derived and this field states one shape.
        due_date: Derived from the rule and the period by
            :func:`compute_due_date`.
    """

    account_id: int
    name: str
    category_id: int | None
    transaction_type_id: int
    amount_ownership: AmountOwnership
    due_date: date | None




def _derive_row_fields(template, rule, period):
    """Resolve what *template* and *period* derive on a generated row.

    The single producer of :class:`DerivedRowFields`, so the create path and
    the maintain path cannot disagree about what a generated row's definition
    says -- see that class for why one statement of it is what lets a
    regeneration UPDATE a row instead of destroying and rebuilding it.

    **It took a ``GenerationSchedule`` and looked *period* up on that value's
    calendar until pay-calendar plan step C2-f3c**, then the CALENDAR alone
    until plan step **X-au-d**.  Both parameters existed for one reason: the
    paycheck engine, which this module no longer runs.  A pricing pass needs
    the owner's whole schedule -- four of the engine's judgements read it, and
    narrowing it to a write window is what stored one salary row ``$502.45``
    low (migration ``a3f8b1c40d92``) -- so the requirement did not go away; it
    MOVED, to :meth:`app.services.income_service.SalaryPricing._net_by_period`,
    which derives the calendar it projects over and is the amount model's one
    walk to a paycheck.  Nothing left here reads a period beyond the one it is
    dating a row in.

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            being generated from.
        rule: The template's recurrence rule, already confirmed present by
            :func:`resolve_generation_plan` (``GenerationPlan.rule``).
        period: The :class:`~app.services.pay_calendar.DerivedPeriod` this row
            lives in, straight off its ``PlannedOccurrence``.

    Returns:
        The :class:`DerivedRowFields` for this (template, period) pair.
    """
    return DerivedRowFields(
        account_id=template.account_id,
        name=template.name,
        category_id=template.category_id,
        transaction_type_id=template.transaction_type_id,
        amount_ownership=_generated_amount_ownership(template),
        due_date=compute_due_date(rule, period),
    )


def _generated_amount_ownership(template):
    """Return WHERE a row generated from *template* takes its amount from.

    **Ruling R-FI's two states, asked of the DEFINITION** (plan step
    **X-au-d**).  A definition either STATES its price or has its price
    COMPUTED by something else, and that one question decides both halves of a
    generated row's amount:

    * a definition that states its price hands the row that figure to OWN,
      which is what every generated row did before this step; and
    * a definition whose price is computed hands the row a DECLARATION -- the
      ``template`` relation -- and no figure at all, so the computation has one
      home and the row cannot hold a stale copy of it.

    **The question is ``template_amount_service.owns_its_amount``, not "is this
    salary-linked", and the difference is rule 14.**  That predicate is already
    the app's single eligibility test for a stated price -- the write door, the
    backfill and every display apply it -- and for a transaction template it IS
    "no active salary profile names this template".  Spelling the salary test
    again here would be a second statement of one rule, which is exactly the
    duplication this step exists to remove; and it would go stale at plan step
    X-au-e, where the answer stops depending on salary at all.

    **A salary-linked template's ``default_amount`` is vestigial and stays
    so.**  Nothing reads it for such a template after this step: the row it
    used to seed is declared instead, and amount rule 2 prices the row from the
    profile.  It is still what the definition falls back to the moment the
    profile is archived, because ``routes/salary/profiles.delete_profile``
    opens the template's price series AT that scalar in the same unit of work
    (plan step X-au-a) -- so an archived profile leaves rows a definition can
    still price rather than rows nothing can.

    Args:
        template: The
            :class:`~app.models.transaction_template.TransactionTemplate`
            being generated from.

    Returns:
        The :class:`~app.models.amount_ownership.AmountOwnership` a row of this
        definition takes: ``own`` over the template's ``default_amount``, or
        ``derived`` naming :attr:`~app.enums.AmountSourceEnum.TEMPLATE`.
    """
    if template_amount_service.owns_its_amount(template):
        return AmountOwnership.own(template.default_amount)
    return derived_ownership(AmountSourceEnum.TEMPLATE)
