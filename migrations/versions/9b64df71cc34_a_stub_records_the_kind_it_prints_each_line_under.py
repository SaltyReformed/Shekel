"""a stub records the kind it prints each line under

Revision ID: 9b64df71cc34
Revises: 5641f7729b68
Create Date: 2026-09-23

Plan step **salary:S11-c-1**, ruling **R-SAL58** ("Stub records its kind",
developer 2026-09-23, which RETIRES **R-SAL56** before it was built)::

    salary.pay_stub_line_amounts   + paycheck_line_kind_id  NOT NULL,
                                     fk_pay_stub_line_amounts_paycheck_line_kind_id

**What changes.**  A stub line's amount already kept the stub's OWN figure for
a paycheck line (fork 2: a figure that disagrees with the app is listed, not
refused), and a one-off already kept its own kind; the KIND of a line amount --
the heading the stub prints it under -- was borrowed from the paycheck line, a
plan the owner edits.  So re-kinding a line after a stub named it re-derived
the stub's totals with no edit to the stub -- an earning turned deduction
moved its net past the printed net it was checked against (finding
**SAL-567**).  The ruling, as picked: *"Each stub line keeps the kind the stub
prints it under, like its amount. ... A line's kind stays editable, stub or
not: nothing to refuse, no trigger, no race, and a saved stub always adds up
by its own figures."*  The stub's kind and the
line's kind are two facts -- what the document printed on its date, and what
the app plans -- that are ALLOWED to differ (the entry door lists a mismatch
the way it lists an amount's), so this is a recorded input, not a second home
of one value (rule 14's tell is an invariant, and none is stated here).

**The backfill.**  Every stub entered before this revision was checked against
its lines' kinds at entry, so each row takes the kind of the line it names.
That is the kind the stub was checked under only while no named line's kind
has changed between the stub's entry and this revision, which is why the
ruling came with the developer's standing warning: *"until it ships, don't
change the kind of a line a stub names."*  The ``UPDATE`` fires
``audit_pay_stub_line_amounts`` like any write, so the log records each row's
kind being filled in.

**The key.**  ``fk_pay_stub_line_amounts_paycheck_line_kind_id`` onto
``ref.paycheck_line_kinds`` is ``ON DELETE RESTRICT``, the shape
``fk_pay_stub_one_offs_paycheck_line_kind_id`` has: a kind a stub records
cannot be removed from the catalogue under it.

**The downgrade REFUSES while any stub line's kind differs from its line's**
(the principle of ruling **R-SAL47**, "Refuse while stubs exist", for
user-entered data).  The schema it returns to reads every line amount's kind
off the paycheck line, so a stub recording a different kind would silently
take the line's -- and what it adds up to by kind would move: its gross, its
net, or the split between its pre-tax and post-tax deductions, depending
on which two kinds differ.  That is the one lossy case, so it is the one
refused: it raises a ``RuntimeError`` naming the count before writing
anything.  Where every row agrees with its line the column is a
restatement the older schema derives, and dropping it loses nothing.

No ``app`` import: the SQL is this revision's own.  ``$0.00``: a stub prices
nothing until ``salary:S11-c-2``.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "9b64df71cc34"
down_revision = "5641f7729b68"
branch_labels = None
depends_on = None


_SCHEMA = "salary"
_TABLE = "pay_stub_line_amounts"
_COLUMN = "paycheck_line_kind_id"
_FK = "fk_pay_stub_line_amounts_paycheck_line_kind_id"

#: Each stub line takes the kind of the paycheck line it names.
_BACKFILL_SQL = (
    "UPDATE salary.pay_stub_line_amounts AS amount "
    "SET paycheck_line_kind_id = line.paycheck_line_kind_id "
    "FROM salary.paycheck_lines AS line "
    "WHERE line.id = amount.paycheck_line_id"
)

#: The rows the fill left without a kind, listed for the refusal's diagnosis.
_UNFILLED_SQL = (
    "SELECT id, pay_stub_id, paycheck_line_id FROM salary.pay_stub_line_amounts "
    "WHERE paycheck_line_kind_id IS NULL ORDER BY id"
)

#: The rows the older schema would re-kind: a stub line recording a kind its
#: paycheck line does not have.
_MISMATCH_COUNT_SQL = (
    "SELECT COUNT(*) FROM salary.pay_stub_line_amounts AS amount "
    "JOIN salary.paycheck_lines AS line ON line.id = amount.paycheck_line_id "
    "WHERE amount.paycheck_line_kind_id <> line.paycheck_line_kind_id"
)


def _refuse_if_the_fill_left_a_row_empty(bind) -> None:
    """Stop the upgrade before the column is required if any row has no kind.

    The zero-NULL check ``.claude/rules/database.md`` asks of every NOT NULL
    on a populated table, with the diagnostic SELECT in the message.

    Args:
        bind: A SQLAlchemy connection to read the rows on.

    Raises:
        RuntimeError: When any stub line was left without a kind, naming
            each one.
    """
    unfilled = bind.execute(sa.text(_UNFILLED_SQL)).all()
    if unfilled:
        raise RuntimeError(
            f"{len(unfilled)} pay stub line(s) were left without a kind by the "
            f"fill from their paycheck lines: {[tuple(row) for row in unfilled]} "
            f"(id, pay_stub_id, paycheck_line_id).  Diagnose with: {_UNFILLED_SQL}"
        )


def _refuse_while_a_kind_differs(bind) -> None:
    """Stop the downgrade before it writes anything if a stub records its own kind.

    Args:
        bind: A SQLAlchemy connection to count the rows on.

    Raises:
        RuntimeError: When any stub line's kind differs from its paycheck
            line's, naming how many.
    """
    count = bind.execute(sa.text(_MISMATCH_COUNT_SQL)).scalar()
    if count:
        raise RuntimeError(
            f"{count} pay stub line(s) record a kind their paycheck line does "
            "not have; downgrading past 9b64df71cc34 would re-kind them to the "
            "line's and move what their stubs add up to by kind (ruling "
            "R-SAL58).  Correct each stub or its line so the two agree, then "
            "downgrade again."
        )


def upgrade():
    """Add the stub line's own kind, fill it from each named line, then require it.

    Order is load-bearing: the column arrives nullable so the rows that already
    exist can be filled, and becomes required only once every one is -- after
    the zero-NULL check the database rules ask for.  That check cannot fire on
    a database this chain built: each row's ``paycheck_line_id`` and
    ``salary_profile_id`` are both ``NOT NULL``, so the composite
    ``fk_pay_stub_line_amounts_paycheck_line`` holds every row to an existing
    paycheck line, whose own ``paycheck_line_kind_id`` is ``NOT NULL``, and the
    join matches every row.
    """
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Integer(), nullable=True),
        schema=_SCHEMA,
    )
    op.execute(_BACKFILL_SQL)
    _refuse_if_the_fill_left_a_row_empty(op.get_bind())
    op.alter_column(_TABLE, _COLUMN, nullable=False, schema=_SCHEMA)
    op.create_foreign_key(
        _FK, _TABLE, "paycheck_line_kinds",
        [_COLUMN], ["id"],
        source_schema=_SCHEMA, referent_schema="ref",
        ondelete="RESTRICT",
    )


def downgrade():
    """Drop the stub line's own kind, refusing first if any row differs from its line.

    Raises:
        RuntimeError: When any stub line records a kind its paycheck line
            does not have.
    """
    _refuse_while_a_kind_differs(op.get_bind())
    op.drop_constraint(_FK, _TABLE, schema=_SCHEMA, type_="foreignkey")
    op.drop_column(_TABLE, _COLUMN, schema=_SCHEMA)
