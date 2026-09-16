"""a paycheck has earning lines

Revision ID: 6c15d2a97b78
Revises: b5c7e9a1d2f4
Create Date: 2026-09-16 01:40:00.000000

Plan step **salary:R18-b** (the second leaf of R18; ruling **R-SAL38**,
developer 2026-09-15; ledger row **D59**)::

    ref.paycheck_line_kinds  +=  'taxable_earning', 'after_tax_earning'

**Two reference rows and nothing else; no figure moves.**  It sits above
``b5c7e9a1d2f4`` (``balance:X-bi-3a``) in the chain only because that
revision reached ``dev`` first; the two touch no table in common.  A paycheck
is base pay plus a list of lines and a line's KIND is its position in the
waterfall (R18-a renamed the table for it); this revision gives the vocabulary its two
EARNING kinds -- ``taxable_earning`` joins the gross (the FICA base, what
withholding annualises) and ``after_tax_earning`` joins the net, untaxed --
in the same commit as the engine arms that price them
(``app/services/paycheck_calculator/_lines.py``), so no tree holds a kind row
nothing prices and no tree holds an enum member without its row (the ref
cache refuses the latter at start-up).  Every paycheck the developer has
saved prices byte-identical, because no line of either kind exists yet: the
one he will enter (his ``$45.00`` taxable phone allowance) is leaf R18-d's
operator act, through the door.

Inline-seeded ``ON CONFLICT (name) DO NOTHING`` (the ``a1c8e4f2b7d6``
precedent) so a freshly upgraded database resolves the enum before the
idempotent reseed in ``app/ref_seeds.py`` runs; the reseed then finds both
present.

**The downgrade REFUSES, by name, any line of either kind** before it deletes
the rows: the FK is ``ON DELETE RESTRICT``, so the delete would fail anyway,
but a refusal that names the lines and says why is the R15-b shape -- an
earning line is money the old vocabulary cannot hold, and deleting it to make
the downgrade pass would move the paycheck it rode.  With no such line the
two rows are deleted and the enum's two members lose their rows, which is
the pre-R18-b tree's state exactly; R18-a's downgrade below this one then
narrows the name column back to ``VARCHAR(10)``.
"""
from alembic import op
from sqlalchemy import text


# Revision identifiers, used by Alembic.
revision = "6c15d2a97b78"
# Re-pointed from 0a4d2c3e89f8 onto balance:X-bi-3a's b5c7e9a1d2f4 the night
# both landed over R18-a (the coordinator's rule: whichever lands second
# re-points).  Nothing here reads a table that revision touches.
down_revision = "b5c7e9a1d2f4"
branch_labels = None
depends_on = None


#: The two earning kinds, spelled once; the enum
#: (``app.enums.PaycheckLineKindEnum``) and the reseed list carry the same
#: strings.
EARNING_KINDS = ("taxable_earning", "after_tax_earning")


def _earning_lines(connection):
    """Return ``(line id, line name, kind name)`` for every line of an earning kind."""
    return connection.execute(text(
        "SELECT l.id, l.name, k.name FROM salary.paycheck_lines l "
        "  JOIN ref.paycheck_line_kinds k ON k.id = l.paycheck_line_kind_id "
        " WHERE k.name = ANY(:kinds) ORDER BY l.id"
    ), {"kinds": list(EARNING_KINDS)}).all()


def upgrade():
    """Seed the two earning kinds, idempotently."""
    for name in EARNING_KINDS:
        op.execute(
            f"INSERT INTO ref.paycheck_line_kinds (name) VALUES ('{name}') "
            "ON CONFLICT (name) DO NOTHING"
        )


def downgrade():
    """Delete the two earning kinds; refuses while any line carries one."""
    connection = op.get_bind()
    lines = _earning_lines(connection)
    if lines:
        listed = "; ".join(
            f"line {line_id} ('{line_name}', {kind})" for line_id, line_name, kind in lines
        )
        raise RuntimeError(
            f"{len(lines)} payroll line(s) carry an earning kind the tree below "
            f"this revision cannot hold: {listed}.  An earning line is money the "
            f"old vocabulary has no place for, and deleting it to pass the "
            f"downgrade would move the paycheck it rode.  Delete each line "
            f"through the salary page first, or keep this revision."
        )
    for name in EARNING_KINDS:
        op.execute(f"DELETE FROM ref.paycheck_line_kinds WHERE name = '{name}'")
