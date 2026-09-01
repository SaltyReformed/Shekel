"""A refund is a NEGATIVE purchase: transaction_entries.amount <> 0

Plan step ``bank_import:X-gj-2b``, ruling **bank_import:R-II**.
``ck_transaction_entries_positive_amount`` becomes ``amount <> 0`` so a merchant
credit can file as a contra-entry against the envelope its merchant rule names,
instead of as income under a spending category.

**The name is kept even though the rule changed**, and that is deliberate rather
than lazy: renaming a CHECK rewrites every citation of it in ``app/`` and in the
audit record, and this revision's whole point is that the constraint's SUBJECT
did not change -- it is still "what may a purchase's amount be".  What changed
is the answer.  The class docstring on
:class:`~app.models.transaction_entry.TransactionEntry` carries the new one.

**Why the ledger did not need the old rule.**  The app's purchase arithmetic is
sign-general and was measured so before this revision was written, through the
real doors on a clone of the developer's own data: a ``-28.29`` purchase and a
``+28.29`` purchase are exact mirrors in every graded measurement, with the
refund emitting ``{cash: +28.29, category: -28.29}`` -- a contra-expense --
against the purchase's ``{cash: -28.29, category: +28.29}``.  Trial balance
``0.00`` both ways, every journal entry balanced, counter accounts unmoved.
``budget.account_postings`` already carries its own amounts under
``ck_account_postings_amount_nonzero``, which is the same shape this revision
gives the purchase: the ledger has always modelled a signed figure.

**The real invariant is ``<> 0``, and positivity moved to the CREATE door.**  A
purchase worth nothing is not a purchase, and that much is a fact about the
table.  "A typed negative is a typo" is a fact about a hand-entry FORM composing
a NEW purchase, so it lives on that form (``EntryCreateSchema`` and the
add-purchase input) -- and deliberately NOT on the update door, where the amount
being edited may be a sign the BANK stated rather than one the owner typed
(developer ruling 2026-08-31).  The non-zero rule itself is now also stated at
the service tier (``entry_service._refusals._reject_zero_amount``), so every
caller meets it as a ``ValidationError`` rather than as this constraint's
``IntegrityError``.

**The downgrade is value-lossless only where no purchase is negative.**  It
cannot be otherwise -- a refund has nowhere to go in the old shape -- so it is
REFUSED rather than silently violated, naming the rows so an operator who means
it can dispose of them first.  Left to PostgreSQL the same state arrives as a
bare constraint violation naming no row, which is the failure this project files
findings about.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b8e4c1f7a903"
down_revision = "e2d7a94f61c3"
branch_labels = None
depends_on = None


_CONSTRAINT = "ck_transaction_entries_positive_amount"
_TABLE = "transaction_entries"
_SCHEMA = "budget"

#: The rule as it stood BEFORE this revision, frozen as a literal rather than
#: imported from the model.  The same construction ``e2d7a94f61c3`` freezes its
#: own predecessor's CHECK for, and for the same reason: a constant that moved
#: with the model would let a downgrade restore the NEW rule while reporting
#: that it had reverted.
_POSITIVE = "amount > 0"

#: The rule this revision installs -- ruling **R-II**'s whole DDL content.
_NONZERO = "amount <> 0"


def refuse_negative_purchases(bind) -> None:
    """Refuse the downgrade while any purchase carries a negative amount.

    **Module-level so a test can drive it**, which is the pattern this chain's
    guarded revisions use (``e4b8a71c0f36``, ``a9d3c15e7f42``): a guard nothing
    exercises is a guard nobody has seen work.  Called first by
    :func:`downgrade`, before any DDL, so a refused downgrade leaves the schema
    untouched.

    Args:
        bind: A SQLAlchemy connection to probe.

    Raises:
        RuntimeError: When any purchase is negative, naming the first 20 ids and
            the diagnostic SELECT.
    """
    rows = bind.execute(
        sa.text(
            f"SELECT id, amount FROM {_SCHEMA}.{_TABLE} "
            "WHERE amount < 0 ORDER BY id LIMIT 20"
        )
    ).fetchall()
    if not rows:
        return
    named = ", ".join(f"{row[0]} ({row[1]})" for row in rows)
    raise RuntimeError(
        f"Purchase(s) {named} (first 20) carry a negative amount, which is a "
        f"REFUND recorded against its envelope (ruling bank_import:R-II). "
        f"Restoring '{_POSITIVE}' would make those rows unrepresentable, and "
        "PostgreSQL would refuse the CHECK without naming one of them. Dispose "
        "of them first if you mean to revert -- withdraw the bank match that "
        "created each, which deletes the purchase -- then run this downgrade "
        "again. Diagnostic: SELECT id, transaction_id, amount, description "
        f"FROM {_SCHEMA}.{_TABLE} WHERE amount < 0 ORDER BY id;"
    )


def upgrade():
    """Widen the purchase amount rule from positive to merely non-zero."""
    op.drop_constraint(_CONSTRAINT, _TABLE, schema=_SCHEMA, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, _NONZERO, schema=_SCHEMA,
    )


def downgrade():
    """Restore the positive rule, refusing while a refund would violate it."""
    refuse_negative_purchases(op.get_bind())
    op.drop_constraint(_CONSTRAINT, _TABLE, schema=_SCHEMA, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, _POSITIVE, schema=_SCHEMA,
    )
