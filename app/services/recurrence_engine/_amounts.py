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

**THIS MODULE PRICES NOTHING AT ALL, and that is plan step X-au-e.**  X-au-d
took the paycheck: this module carried ``_get_salary_profile`` and
``_get_transaction_amount``, the second running
``paycheck_calculator.calculate_paycheck`` over the owner's whole schedule and
storing the answer in ``estimated_amount``, which ruling **R-FI** calls a cache
of a derivation and finding **N-224** measured as an app holding two answers to
one question.  What that step LEFT was a fork -- a definition that states its
price handed its rows that figure to OWN -- and X-au-e deletes the fork rather
than one of its arms.  Every generated row is DERIVED now, priced by its
definition's own effective-dated series as of the row's OWN due date (amount
rule 3, ``template_amount_service.amount_as_of``).

**A generator that prices nothing cannot mis-price.**  That retires the class of
defect migration ``a3f8b1c40d92`` was written to repair -- the ``$502.45`` a
window-narrowed period list put into one stored salary row -- and it is what
dissolves two findings rather than fixing them:

* **N-247**, one date under two predicates: a template amount edit's sweep
  selects rows by their pay PERIOD's end while the series answers by a row's own
  DUE date, so an edit could rewrite a row whose due date preceded the date it
  stated.  A sweep that writes no figure decides nothing about money.
* **N-244**, the back-dated re-price: this module and the conflict chooser were
  its two writers of today's price onto a past row, and neither writes one now
  (``recurrence_engine._conflicts`` hands a row back to its definition instead
  of to a figure).
"""
from datetime import date
from typing import NamedTuple

from app.enums import AmountSourceEnum
from app.models.amount_ownership import AmountOwnership
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

    **The splat can no longer hand a DERIVED row back to its owner, and that
    is structural rather than guarded** (plan step **X-au-e**, the condition
    finding **N-437** was closed under).  This field held one of two values
    while generation still priced: ``own`` over a scalar for a definition that
    stated its price, ``derived`` for one whose price was computed.  Only the
    second remains, so the value this class can carry is a CONSTANT and there
    is no arm left that writes a figure onto a row -- the question has no site
    to be asked at rather than an answer that happens to be right.

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
            **R-FI**'s one attribute rather than a figure.  Since plan step
            X-au-e it states ONE shape for every generated row -- ``derived``
            naming :attr:`~app.enums.AmountSourceEnum.TEMPLATE` -- so the row
            carries a declaration and no figure, and its definition's own
            effective-dated series prices it as of its due date.  It was a
            fork on ``template_amount_service.owns_its_amount`` until that
            step, and the arm that fork selected is what stored the copy the
            cutover deleted.
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
        amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
        due_date=compute_due_date(rule, period),
    )
