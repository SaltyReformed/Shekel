"""WHICH RULE prices a row: the amount model's CLASSIFICATION tier.

**Split out of :mod:`._amount_source` at plan step X-au-i**, the leaf that pushed
that module past ``max-module-lines``.  The cut follows the two questions the
package docstring already separates: *which rule owns this row's amount* is
here, and *what does that rule answer* stays next door.  Classification reads
only the row, its declared relation and the DEFINITION behind that relation; it
resolves no figure and reaches no producer, which is what makes it a tier rather
than an arbitrary half.

Nothing here imports from :mod:`._amount_source`, so the split introduces no
cycle: this module is strictly below it, exactly as
:mod:`._amount_basis` is.  :func:`declared_relation` is PUBLIC here because it
gained a second caller in the split -- ``resolve_transfer_amount`` next door asks
the same id-to-member question -- and a private name imported across modules is
finding **N-33**'s shape.  :func:`_is_loan_payment` stayed private: its only
caller is the refiner directly above it.

Boundary discipline (``CLAUDE.md`` Architecture / B6-01): plain data and ORM
rows in, an :class:`AmountRule` out; no Flask import, no writes.
"""

from __future__ import annotations

from enum import Enum

from app import ref_cache
from app.enums import AmountSourceEnum
from app.services import template_amount_service

class AmountRule(Enum):
    """Which producer a row's amount comes from -- ruling R-FI's five, plus the
    two the CC PAYBACK relation refines into (plan step **X-au-i**).

    The dispatch key.  An explicit enum rather than a pair of link tests, because
    the rules are not a partition over the links -- see this module's docstring
    for the two subset relations that make the refinement order load-bearing.

    **The two payback members are ONE relation refined, not two relations**
    (plan step X-au-i).  ``credit_source`` says *this row repays the card spend
    of that row*; which producer answers depends on how the SOURCE holds its
    money, and the app already has one published predicate for that --
    ``Transaction.tracks_purchases``.  An entry-capable source keeps its spend in
    purchases, so its card spend is the ones marked ``is_credit``
    (``CC_PAYBACK_PURCHASES``); a single-spend source goes on the card whole, so
    its card spend is the row's own resolved amount (``CC_PAYBACK_ROW``).  The
    two source kinds are DISJOINT by a write-door refusal rather than by
    convention: ``routes/transactions/mutations.py`` refuses Credit status on an
    entry-capable row and the grid renders no Credit control for one.  Measured
    on the 2026-08-20 production clone: 22 live paybacks, 10 with an
    entry-capable source and 12 with a single-spend one.  **21 of the 22
    reproduce their stored figure under the arm their source selects**; the
    exception is payback 2590, which stores ``$123.18`` against ``$181.58`` of
    credit purchases because a human typed over it -- finding **N-252**, and the
    one row migration ``d5c31f8b7e04`` reports on the way through.  It has
    settled, so no balance moves either way: a settled row is worth what it
    RECORDED (``row_valuation.fixed_contribution``), and the amount model is not
    consulted for one at all.

    **It is NOT what the ``amount_source_id`` column stores, and a first draft of
    this docstring said it was.**  That column names the RELATION that prices a
    row -- its definition, or its parent transfer
    (:class:`app.enums.AmountSourceEnum`) -- and the refinement between SALARY
    and TEMPLATE, or between LOAN_PAYMENT and TRANSFER, is a property of the
    DEFINITION rather than of the row, resolved live here.  Storing the rule
    would put a definition-level fact on every generated row, where two live
    routes falsify it (ruling **R-FK**, plan step X-au-c1).

    **The OWN member is the one that IS the column** (finding **N-262**, closed
    at plan step X-au-c2): a row owns its amount exactly when it carries no
    source, which is the same NULL-ness ``ck_transactions_amount_ownership``
    pairs with carrying a figure.  Before that leaf this member was inferred from
    ``is_override`` and from having left Projected -- states the CHECK cannot see
    -- so the schema and this dispatch could disagree about the same row.  The
    status gate now sits ABOVE the resolver rather than inside it: an excluded row
    is worth ``$0.00`` whatever prices it, and asking a Projected-only producer
    about a Cancelled row is how that used to become a refusal
    (:func:`app.services.cash_ledger.contributed_amount`).
    """

    OWN = "own"
    SALARY = "salary"
    TEMPLATE = "template"
    LOAN_PAYMENT = "loan_payment"
    TRANSFER = "transfer"
    CC_PAYBACK_PURCHASES = "cc_payback_purchases"
    CC_PAYBACK_ROW = "cc_payback_row"



def amount_rule(txn) -> AmountRule:
    """Return which of R-FI's five rules owns *txn*'s amount.

    **One question to the COLUMN, then one to the DEFINITION.**  A row that
    carries no ``amount_source_id`` owns its figure and is priced by rule 1; a
    row that carries one names the RELATION that prices it, and the refinement
    inside that relation -- SALARY within a definition, LOAN_PAYMENT within a
    parent transfer -- is read live off the definition itself.  The refinement
    order is the rule: SALARY is tested before TEMPLATE because a salary profile
    names an ordinary transaction template, and LOAN_PAYMENT before TRANSFER
    because a loan payment is a transfer.  Testing them the other way round would
    place every paycheck as a template row and every loan payment as a plain
    shadow.

    **Nothing here reads ``is_override``, ``is_projected`` or ``is_deleted``, and
    that is finding N-262's fix** (plan step X-au-c2).  Those three are facts
    about whether a row COUNTS and about who last touched it, not about who owns
    its figure, and inferring ownership from them let four live doors write a row
    ``ck_transactions_amount_ownership`` admits and this dispatch refused -- the
    module docstring names all four.  What replaced them is the one statement of
    ownership the model has.  Two consequences worth stating because they used to
    be arms:

    * a row a human RE-PRICED owns its figure because the write door CLEARS its
      source and stores the typed amount, not because ``is_override`` is set --
      so the flag can go on carrying its other three facts (finding **N-238**,
      plan step X-au-h) without touching pricing;
    * a SETTLED row is priced by this dispatch like any other, because plan step
      X-au-c3 writes NO plan column at a settle -- what moved is recorded beside
      the plan, not into it.  No money reader asks this about a settled row:
      ``row_valuation.fixed_contribution`` answers from the record first, and the
      dispatch is reached only for a row whose money has not moved.

    **Soft deletion does not change the answer, deliberately.**  Being deleted is
    a statement about whether the row counts, and making it flip the rule would
    force ``amount_source_id`` to be REWRITTEN on every delete and restore -- a
    derived column beside a second writer, the shape this arc exists to remove.
    A deleted derived row resolves like any other and contributes nothing either
    way; the backfill's refusal to MINE a deleted row (migration
    ``a9d3c15e7f42``) is a question about evidence, not about ownership.

    Args:
        txn: The :class:`~app.models.transaction.Transaction` to classify.  Its
            ``template`` / ``transfer`` relationship is read only when it
            DECLARES the matching relation, so an undeclared row costs no lazy
            load at all.

    Returns:
        The :class:`AmountRule` that prices this row.

    Raises:
        KeyError: When the row names a relation this dispatch has no rule for.
            Unreachable through the FK, which admits only the seeded
            :class:`~app.enums.AmountSourceEnum` members; it is how a member
            ADDED without a rule beside it fails loudly instead of falling
            through to whichever branch happened to be last.
    """
    if txn.amount_source_id is None:
        return AmountRule.OWN
    return _RELATION_RULES[declared_relation(txn.amount_source_id)](txn)


def declared_relation(source_id: int) -> AmountSourceEnum:
    """Return the :class:`~app.enums.AmountSourceEnum` member *source_id* names.

    The id-to-member direction ``ref_cache`` does not publish, because every
    other consumer of a ref table compares a stored id against a cached one and
    needs no reverse map.  This dispatch is the exception: it branches on WHICH
    relation a row declared, so it must turn the stored id back into the member
    the rules are written against.  Derived from ``ref_cache.amount_source_id``
    rather than from a second query, so the two directions cannot disagree.

    Args:
        source_id: A row's stored ``amount_source_id`` (never ``None`` -- the
            caller has already tested for the OWN state).

    Returns:
        The member that id names.

    Raises:
        KeyError: When no member maps to *source_id*.  The FK to
            ``ref.amount_sources`` makes that unreachable for a seeded database.
    """
    return {
        ref_cache.amount_source_id(member): member
        for member in AmountSourceEnum
    }[source_id]


def _rule_within_definition(txn) -> AmountRule:
    """Refine the TEMPLATE relation into rule 2 or rule 3.

    A definition prices its rows either through a salary profile that names it
    or through its own effective-dated series, and which of the two is a fact
    about the DEFINITION read at this moment -- archiving the profile is what
    moves a template from the first to the second.

    ``template is None`` beside a declared relation is TEMPLATE, and that answer
    REFUSES one tier down (:func:`_stated_amount`).  A row whose definition was
    hard-deleted in this session still WAS generated by one, and asking the
    salary predicate about ``None`` would raise ``AttributeError`` -- an
    unhandled crash where every other unanswerable shape here raises the arc's
    own refusal.  Found by an adversarial review at plan step X-au-b.

    Args:
        txn: A row declaring :attr:`~app.enums.AmountSourceEnum.TEMPLATE`.

    Returns:
        :attr:`AmountRule.SALARY` or :attr:`AmountRule.TEMPLATE`.
    """
    return (
        AmountRule.SALARY
        if txn.template is not None
        and template_amount_service.is_salary_linked_template(txn.template)
        else AmountRule.TEMPLATE
    )


def _rule_within_parent_transfer(txn) -> AmountRule:
    """Refine the PARENT_TRANSFER relation into rule 4 or rule 5.

    A shadow's parent is either a loan payment -- whose cash the loan derives --
    or an ordinary transfer, and which of the two is a fact about the transfer's
    TEMPLATE (:func:`_is_loan_payment`), read live so a template switched between
    modes changes rule at that moment.

    Args:
        txn: A row declaring
            :attr:`~app.enums.AmountSourceEnum.PARENT_TRANSFER`.

    Returns:
        :attr:`AmountRule.LOAN_PAYMENT` or :attr:`AmountRule.TRANSFER`.
    """
    return (
        AmountRule.LOAN_PAYMENT if _is_loan_payment(txn.transfer)
        else AmountRule.TRANSFER
    )


def _is_loan_payment(xfer) -> bool:
    """Return whether *xfer* is a loan payment rather than a generic transfer.

    The fact ``loan_payment_service`` keys its whole live-derive machinery on: a
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row hanging
    off the transfer's template (decision B).  A transfer with no template, or a
    template with no settings row, is an ordinary transfer -- an investment
    contribution, a savings sweep -- and rule 5 prices it.

    Read live off the relationship rather than remembered, so a template
    switched between modes changes rule at that moment.

    Args:
        xfer: The parent :class:`~app.models.transfer.Transfer`, or ``None``
            when the shadow's parent is gone.

    Returns:
        ``True`` when a loan payment's settings drive this transfer's cash.
    """
    if xfer is None or xfer.template is None:
        return False
    return xfer.template.settings is not None


def _rule_within_credit_source(txn) -> AmountRule:
    """Refine the CREDIT_SOURCE relation into rule 6 or rule 7.

    A payback repays the card spend of one source row, and what that source's
    card spend IS depends on how the source holds its money.
    ``Transaction.tracks_purchases`` is the app's one published answer to that
    question -- the same predicate ``settles_from_entries`` reads, the same one
    ruling **R-FF** is written against, and the same one
    ``routes/transactions/mutations.py`` refuses Credit status on -- so the two
    arms are disjoint by that refusal rather than by convention.

    **It is EQUIVALENT to sniffing ``source.entries`` on every state the app can
    reach, and that was measured rather than assumed** -- the alternative
    dispatch passes the whole suite, so the choice is a contract and not a
    behaviour today.  The two can only differ two ways and both are unreachable:
    an entry-capable source with NO entries has no payback at all (one is
    created only once ``total_credit > 0``), and a single-spend source can never
    acquire entries because ``entry_service`` refuses a parent that is not
    entry-capable (*"This transaction does not support individual purchase
    tracking"*).  Production agrees: all 12 row-backed paybacks have a source
    carrying zero entries.

    The published predicate is still the right one to read, and the reason is
    the equivalence itself: a sniff would be a SECOND spelling of the question
    whose correctness rests on a guard in another package continuing to refuse.
    That is the "one writer from wrong" shape this arc exists to remove, and it
    is why the two arms name ``tracks_purchases`` outright.

    ``credit_payback_for is None`` beside a declared relation refines to
    CC_PAYBACK_ROW, and that answer REFUSES one tier down
    (:func:`_credit_source`).  It mirrors :func:`_rule_within_definition`'s
    treatment of a missing definition and for the same reason: reading
    ``tracks_purchases`` off ``None`` would raise ``AttributeError`` from inside
    a dispatch, where the refusal that names the broken link is the useful
    failure.

    Args:
        txn: The payback whose rule is being decided.  Reads
            ``credit_payback_for`` and, through it, ``tracks_purchases``.

    Returns:
        The rule that prices it.
    """
    source = txn.credit_payback_for
    if source is not None and source.tracks_purchases:
        return AmountRule.CC_PAYBACK_PURCHASES
    return AmountRule.CC_PAYBACK_ROW


# WHICH RULE a declared relation refines into, keyed by the relation itself.  The
# same shape as ``_RULE_ANSWERS`` above and for the same reason: a member added
# to :class:`~app.enums.AmountSourceEnum` -- ``credit_card:CC4c``'s finance
# charge is the one already known to need one (finding **N-264**) -- raises at
# this lookup instead of silently taking whichever branch an ``if`` chain happened
# to end on.  ``tests/test_services/test_amount_source.py`` grades the table
# against the enum, so the completeness is a predicate rather than a comment.
_RELATION_RULES = {
    AmountSourceEnum.TEMPLATE: _rule_within_definition,
    AmountSourceEnum.PARENT_TRANSFER: _rule_within_parent_transfer,
    AmountSourceEnum.CREDIT_SOURCE: _rule_within_credit_source,
}
