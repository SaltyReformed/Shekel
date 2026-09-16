"""A merchant's NEW-ENVELOPE answer coming to NAME a definition, and going back.

Leaf 7b-3 of plan step ``balance:X-bi-7b`` (``bank_import:X-f6c``'s shape,
ruling **R-BAL24**, finding **N-328**): a new-envelope answer mints ONE
rule-less definition the first time it fires and names it thereafter.  The
two halves of that sentence are the two functions here -- the flip the
create door performs (:func:`name_the_filed_definition`) and its inverse the
undo performs before it disposes of the definition
(:func:`unname_the_disposed_definition`) -- kept in one module so the
columns one writes are exactly the columns the other restores, and because
:mod:`._release` had no room for its half (ruling **balance:R-IR**: the
session that breaks a module splits it).

Boundary discipline (``CLAUDE.md`` Architecture): ORM rows in, plain data
out, no Flask import.  Both MUTATE the merchant-rule row and neither
commits -- the door that called owns the unit of work.
"""

from __future__ import annotations

from app.extensions import db
from app.models.merchant_rule import MerchantRule
from app.models.statement_import import BankStatementLine
from app.models.transaction import Transaction
from app.services import definition_delete

from ._bars import MerchantAnswers
from ._creations import NewEnvelope
from ._rules import RuleAnswer


def name_the_filed_definition(
    line: BankStatementLine,
    envelope: Transaction,
    answers: MerchantAnswers,
) -> "NewEnvelope | None":
    """Make the merchant's NEW-ENVELOPE answer NAME the definition it just filed into.

    **A new-envelope answer mints a rule-less definition the first time it
    fires and names it thereafter** (finding **N-328**, developer ruling
    2026-08-20; ruling **R-BAL24**).  The container a merchant's spending
    files into carries an identity ACROSS paychecks now -- the definition --
    where it carried a NAME, so the stored answer's two columns
    (``envelope_name``, ``category_id``) become the one (``template_id``):
    the same row, the TEMPLATE shape ``ck_merchant_rules_one_answer``
    admits, and from the next request
    :func:`~._placement._template_placement` finds or PLACES that
    definition's row in whatever paycheck a line falls in.

    **Only where the answer IS what was filed into, and only onto a PLACED
    envelope.**  The stored answer is rewritten when it is NEW-ENVELOPE with
    exactly the name and category of the envelope this act filed into --
    minted here, or a placed one of that name the answer's first firing
    converged on (:func:`~._placement._new_envelope_placement`) -- and that
    envelope is a rule-less definition's row (``Transaction.is_placed``,
    the placement's own convergence predicate).  An owner's own pick of a
    same-named RECURRING envelope rewrites nothing: the placement never
    converges on one, and a first build of this function keyed on the link
    alone, so that pick turned a NEW-ENVELOPE answer into a TEMPLATE answer
    naming the recurring definition -- a standing answer the owner never
    stated, routing every later import (found by 7b-3's adversarial review).
    A legacy link-less envelope names nothing and the rule stays as stated
    until the family's cutover.  An owner's own new envelope under a
    different name is a one-line choice and rewrites nothing either; a
    hand-typed name and category that happen to equal the stated answer ARE
    the answer coming true, and flip.

    The ORM row is loaded by its unique key here rather than threaded from
    the pass: ``answers.view.rules`` is the read model the pass derived once
    (:class:`~._rules.StandingRule`), and a write wants the row.  Within
    this same press the read model still says NEW-ENVELOPE, which is fine:
    the answer this act made true is RETURNED, the batch hands it to
    :class:`~._container.MintedEnvelopes`, and later lines of that answer
    converge on the definition whether this act minted it or found it.

    Args:
        line: The bank line, for its merchant.
        envelope: The row filed into, whose own name and category are what
            the stored answer is compared against and whose ``template_id``
            is the definition.
        answers: What the owner has said about this account's merchants.

    Returns:
        The NEW-ENVELOPE answer this act made TEMPLATE, or ``None`` when no
        stored answer was rewritten.
    """
    stated = answers.view.rules.get(line.merchant_id)
    if (
        stated is None
        or stated.answer is not RuleAnswer.NEW_ENVELOPE
        or not envelope.is_placed
        or stated.envelope_name != envelope.name
        or stated.category_id != envelope.category_id
    ):
        return None
    row = (
        db.session.query(MerchantRule)
        .filter(
            MerchantRule.merchant_id == line.merchant_id,
            MerchantRule.account_id == line.account_id,
        )
        .one()
    )
    row.template_id = envelope.template_id
    row.envelope_name = None
    row.category_id = None
    return NewEnvelope(name=stated.envelope_name, category_id=stated.category_id)


def unname_the_disposed_definition(container: Transaction) -> None:
    """Put a merchant's answer back to NEW-ENVELOPE before its definition goes.

    **The inverse of** :func:`name_the_filed_definition`, and the undo's
    half of ruling **R-GG**: an act that minted a definition also rewrote
    the merchant's stored NEW-ENVELOPE answer to TEMPLATE naming it, and an
    undo that removes what the act MADE removes that definition
    (``transaction_service.delete_transaction``'s step 5, when the container
    is its last row).  Left as it stood, the rule would either refuse the
    disposal (``deletion_refusal``'s merchant-rule arm, which the undo met on
    the register's own "DESTROYS 2 rows" promise -- found by both of 7b-3's
    adversarial reviews) or, without that arm, cascade away with the
    definition (``fk_merchant_rules_template_account`` is ``ON DELETE
    CASCADE``), taking an answer the owner stated.  So the rule is restated
    first, from the definition's own name and category, which ARE the
    NEW-ENVELOPE columns: the state before the press.

    **Only where the definition goes, and only a rule naming it.**  A
    container that is not its definition's last row leaves the definition
    standing and the rule names it still; a rule the owner has since
    restated elsewhere names no such definition and is untouched.  A rule
    the owner re-stated as TEMPLATE naming this same definition by hand is
    restored to NEW-ENVELOPE too -- the definition is gone, and *a new
    envelope called X* is the surviving meaning of that answer.  A
    definition a rule names is always an envelope with a category
    (``offerable_templates`` admits no other), so the NEW-ENVELOPE shape's
    CHECK holds.

    Args:
        container: The envelope the undo is about to remove.
    """
    if not definition_delete.is_last_row_of_its_definition(container):
        return
    definition = container.template
    for rule in (
        db.session.query(MerchantRule)
        .filter(MerchantRule.template_id == definition.id)
        .all()
    ):
        rule.template_id = None
        rule.envelope_name = definition.name
        rule.category_id = definition.category_id


__all__ = [
    "name_the_filed_definition",
    "unname_the_disposed_definition",
]
