"""
Shekel Budget App -- WHICH RELATIONSHIPS pricing a row walks.

One function, and it exists so that the eager load the amount model needs is
stated ONCE rather than copied into every loader that feeds it.
:mod:`app.services.cash_ledger._amount_source` owns the five rules; this owns
the relationship graph those rules traverse, so a rule that starts reading a new
relationship adds it here in the same edit and every routed loader gets it.

**It is a ``utils`` leaf rather than a member of the cash-ledger package, and
the reason is a tier rather than a preference.**  ``app.services.loan_loaders``
is one of the loan TERM primitives the cash ledger itself imports
(``cash_ledger._loan_installment`` -> ``load_loan_params`` /
``load_rate_changes`` / ``loan_payment_due_date``;
``cash_ledger._loan_pricing`` -> ``load_escrow_lines``), and plan step X-au-g-2a
moved rule 4's producer DOWN precisely so that arrow runs ONE way.  Its
``query_shadow_income`` returns nothing but transfer shadows, every one of them
DERIVED since plan step X-au-g-2c-2, so it is the loader that most needs this --
and it cannot ask the cash ledger for it, at module level or at call time, because
``cyclic-import`` (R0401) traces function-level imports too.  A leaf both tiers
can reach is the same shape :mod:`app.services.row_valuation` already is, and
this one is smaller: it names SQLAlchemy and four models and no service at all.

:mod:`app.services.cash_ledger` re-exports it, so a caller ABOVE the amount
model asks the model for its own load and only the tiers beneath it name this
module directly.
"""

from sqlalchemy.orm import selectinload

from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate


def pricing_load_options() -> tuple:
    """Return the loader options a caller resolving MANY rows should apply.

    Every relationship below is ``lazy="select"``, so a loader that omits them
    makes the resolver issue queries per row.

    Five chains, covering EIGHT relationships.  An adversarial review counted
    seven at plan step X-au-b after a first draft named two; the eighth was
    found by this step's own query-count control, which is worth stating
    because a census is exactly the kind of claim that reads as complete:

    * ``Transaction.template`` -> ``salary_profiles`` -- amount rule 2's
      refinement (``_amount_source._rule_within_definition``) asks whether an
      ACTIVE profile names the definition;
    * ``Transaction.template`` -> ``amount_versions`` -- rule 3 resolves the
      definition's price series as of the row's own due date;
    * ``Transaction.transfer`` -> ``template`` -> ``settings`` -- rule 4's
      refinement (``_amount_source._is_loan_payment``) asks whether a loan
      payment's settings row hangs off the parent's definition;
    * ``Transaction.transfer`` -> ``template`` -> ``amount_versions`` -- rule 5
      and rule 4's MANUAL arm both price through
      ``_amount_source.resolve_transfer_amount``;
    * ``Transaction.pay_period`` -- rule 4's DERIVE arm dates the installment it
      prices, and ``loan_loaders.loan_payment_due_date`` reads the period on
      EVERY call rather than only on its no-``due_date`` fallback, which that
      function's own docstring records.  **It was missing from the first draft
      of this set**, and the control in
      ``test_a_transfer_shadow_is_derived.TestTheAmountModelsOwnEagerLoad`` is
      what found it: three ``budget.pay_periods`` reads against an assertion of
      zero.  Before this step the obligation was one loader's --
      ``loan_loaders.query_shadow_income`` eager-loads it, and every caller of
      that derivation came through there -- and declaring every shadow derived
      is what spread it to the grid and the cash fold.

    **The per-ROW chains are a true N+1 and the per-DEFINITION ones are not**: a
    template's collections are identity-mapped, so 44 templates served 452 rows
    on the 2026-08-12 production clone, while ``Transaction.transfer`` is one
    query per shadow.  That asymmetry is why this stopped being a paragraph of
    advice at plan step X-au-g-2c-2.  Before that step a shadow OWNED its
    figure, so ``amount_rule`` answered ``OWN`` off the column and never touched
    the relationship; a shadow is DERIVED now -- **350 of them on production,
    measured 2026-09-01 at stamp ``a4c6f1d92b73``** -- so every surface that
    loads one walks to its parent, and a loader that forgets pays per row.

    ``selectinload`` throughout rather than ``joinedload``: three of the four
    chains end in a COLLECTION, and a joined load over two collections
    multiplies the row count.

    **It is deliberately NOT conditional on which rows a caller holds.**  A
    loader does not know which rule will price each row -- that is the amount
    model's own dispatch -- and an option for a relationship no row in the set
    uses costs one empty ``SELECT ... IN ()``-shaped query at most, where a
    missing one costs a query per row.

    Returns:
        A tuple of SQLAlchemy loader options, splatted into ``Query.options``.
    """
    return (
        selectinload(Transaction.template).selectinload(
            TransactionTemplate.salary_profiles,
        ),
        selectinload(Transaction.template).selectinload(
            TransactionTemplate.amount_versions,
        ),
        selectinload(Transaction.transfer).selectinload(
            Transfer.template,
        ).selectinload(TransferTemplate.settings),
        selectinload(Transaction.transfer).selectinload(
            Transfer.template,
        ).selectinload(TransferTemplate.amount_versions),
        selectinload(Transaction.pay_period),
    )


def valuation_load_options() -> tuple:
    """Return the loads a CONTRIBUTION pass needs: pricing, plus the entries.

    :func:`pricing_load_options` covers what the five amount RULES read -- what
    a row's amount IS.  A pass that asks what a row is WORTH reads one more
    relationship on top: ``Transaction.entries``, for the envelope reservation
    (``cash_ledger._amounts._entry_aware_amount``) and for a ``purchases``-basis
    settlement (``row_valuation.settled_figure``, which sums them).

    **It is a separate function rather than more of the one above, because the
    two questions have two answers** -- the same split
    :mod:`app.services.cash_ledger._amount_source` and
    :mod:`app.services.cash_ledger._amounts` already are.  A caller that only
    resolves amounts (the grid's quick-edit fragment, a single-row render)
    should not pay for a purchase load it never reads.

    **It exists because two loaders came to state the same set** (plan step
    X-au-g-2c-2): ``routes/grid/page.py``'s window loader and
    ``cash_ledger._facts``'s fold loader both want entries beside the pricing
    relationships, and spelling that twice is what ``duplicate-code`` reported
    the moment the second one gained the pricing half.  The gate was right --
    two loaders feeding one valuation is one decision, not two.

    Returns:
        A tuple of SQLAlchemy loader options, splatted into ``Query.options``.
    """
    return (selectinload(Transaction.entries), *pricing_load_options())
