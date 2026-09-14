"""
Shekel Budget App -- Permanently deleting a transaction DEFINITION

**The ONE act that removes a ``budget.transaction_templates`` row and the
rows it still names**, shared by the two doors that perform it: the
definition's own hard-delete (``routes/templates/crud.hard_delete_template``)
and the account hard-delete, which disposes of the rule-less definitions its
guards let through (``routes/accounts/crud.hard_delete_account``, plan step
``balance:X-bi-7a``).  The second door was written against the first's body
rather than calling it, and an adversarial review of that step reproduced
the state ruling **R-JE** (finding **N-440**) exists to refuse: a settled,
soft-deleted, TEMPLATE-priced row left with ``template_id NULL`` -- priced by
nothing, unrestorable by the cutover's downgrade -- because ``transactions.
template_id`` is ``ON DELETE SET NULL`` until the family's cutover makes it
``RESTRICT``.  One body, so the two doors cannot part again.

**What the act is, in order.**  Every NON-SETTLED row the definition names is
deleted first -- after its purchase postings are reversed, because
``transaction_entries`` CASCADE from their parent and
``journal_entries.transaction_entry_id`` is ``ON DELETE SET NULL``, so a
purchase deleted without a reversal leaves both of its legs on their ledger
accounts with nothing left to explain them (ruling **R-FM**: a PROJECTED
envelope holds postings once a purchase records its bank day).  Then the
definition goes through the session, so ``amount_versions``' delete-orphan
and the rule's cascade run.  The delete is restricted to non-settled rows via
``Status.is_settled`` (CRIT-05 / E-22) so a race-window mark-done cannot
destroy Paid / Received history; **the caller has already asked
:func:`~app.utils.archive_helpers.template_has_paid_history`** -- soft-deleted
settled rows included -- and refused, which is what makes the restriction a
backstop rather than a filter that leaves a survivor.

Boundary discipline (``CLAUDE.md`` Architecture): an ORM row in, no Flask
import.  It MUTATES and does NOT commit -- the caller owns the unit of work.
"""

from app.extensions import db
from app.models.ref import Status
from app.models.transaction import Transaction
from app.services import posting_service


def rows_holding_purchase_postings(*scope):
    """Return the template rows matching *scope* that hold ledger postings.

    **A PROJECTED row can hold postings since plan step X-f3b** (ruling
    **R-FM**): a purchase whose bank posting day the owner recorded books its
    own balanced cash leg whatever its envelope's status is.  Every bulk
    statement over a definition's rows was written under the opposite premise
    -- "a Projected row has no postings, so a bulk archive / restore / delete
    cannot touch the ledger" -- and that premise is what fell.

    The narrowing is what keeps the cost honest: a template generates ~50 rows
    over the forward horizon and at most a handful can ever hold a purchase, so
    this returns the empty list with ONE indexed read in the ordinary case and
    the callers loop over nothing.  Moved here whole from
    ``routes/templates/crud`` at plan step ``balance:X-bi-7a``, with the
    delete that uses it; the archive and restore doors still call it.

    Args:
        *scope: The SQLAlchemy clauses selecting the rows the bulk statement is
            about to touch -- built by the caller, so this reader can never
            select a different set from the statement it guards.

    Returns:
        The matching :class:`~app.models.transaction.Transaction` rows.
    """
    return (
        db.session.query(Transaction)
        .filter(*scope, posting_service.posted_purchase_exists_clause())
        .all()
    )


def permanently_delete_definition(template) -> None:
    """Delete *template* and every non-settled row it names, in that order.

    The module docstring carries the order and why each step is where it is.
    The caller has refused on :func:`~app.utils.archive_helpers.template_has_paid_history`
    (and, at the definition's own door, on a standing merchant rule) BEFORE
    reaching here; the settled-row restriction below is the backstop for a
    caller that has not.

    Args:
        template: The :class:`~app.models.transaction_template.TransactionTemplate`
            to remove.  Its rows are selected by id, so it need not be loaded
            with them.
    """
    settled_status_ids = db.session.query(Status.id).filter(
        Status.is_settled.is_(True)
    ).scalar_subquery()
    delete_scope = (
        Transaction.template_id == template.id,
        Transaction.status_id.notin_(settled_status_ids),
    )
    for txn in rows_holding_purchase_postings(*delete_scope):
        posting_service.reverse_postings_before_delete(txn)
    db.session.query(Transaction).filter(
        *delete_scope,
    ).delete(synchronize_session="fetch")
    db.session.delete(template)
