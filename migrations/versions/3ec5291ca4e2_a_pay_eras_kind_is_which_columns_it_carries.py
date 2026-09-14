"""a pay era's kind is which columns it carries

Revision ID: 3ec5291ca4e2
Revises: ef32dfe4cd8e
Create Date: 2026-09-13

Plan step **pay_calendar:C17-d-2** (rulings **R-PC79** and **R-PC80**,
developer 2026-09-13; one commit with ``recurrence:R13``).  ``budget.pay_eras``
gains the two day-of-month parameter columns, ``cadence_days`` becomes
nullable, and ``kind_id`` is DROPPED with ``ref.pay_cadence_kinds``: the
three cadence kinds have DISJOINT stored shapes -- every-N-days is
``cadence_days`` present, semi-monthly is ``other_day`` present, monthly is
neither -- so the kind is readable off the row, and a stored ``kind_id``
beside those columns would be a derived value kept next to its source
(rule 14, ``balance:R-IY``) that no CHECK could tie to them without pinning
a seed id.  Three CHECKs make every storable row a legal era::

    ck_pay_eras_one_kind     cadence_days IS NULL
                             OR (nominal_day IS NULL AND other_day IS NULL)
    ck_pay_eras_nominal_day  nominal_day IS NULL OR (29..31, strictly above
                             the anchor's day, and the anchor's day IS the
                             meant day clamped into its month)
    ck_pay_eras_other_day    other_day IS NULL OR (1..31, not the day the
                             anchor means, and the lower of the two <= 27)

``ck_pay_eras_cadence_range`` stays; ``NULL`` passes ``BETWEEN``.  The
``nominal_day`` CHECK is ``budget.recurrence_rules``' three conjuncts on this
table's anchor (ruling ``recurrence:R-R3``; migration ``b6d41f0a9c27`` is
where the clamp equality was argued): presence IMPLIES the first month was
too short to carry the meant day, so absence has ONE meaning.

**The CHECK texts are stated here, frozen, and the model states them again**
(``app.models.pay_era.ONE_KIND_CHECK`` and its two siblings): a revision is
a record of what it installed, and the mapper's copy is what ``create_all``
and autogenerate read.  A deliberate duplicate needs a reconciler --
``tests/test_models/test_pay_era.py`` holds the two texts equal, and drives
each conjunct against the installed constraint.

**MOVES MONEY for a month-kind owner, and ``$0.00`` on production.**  Every
stored era is a fixed-days one -- ``ref.pay_cadence_kinds`` holds the one
member ``fixed_days`` and ``kind_id`` is ``NOT NULL`` -- so the upgrade
rewrites no row: it adds two NULL columns, relaxes one ``NOT NULL`` and
removes a column whose every value named the same member.  The upgrade
REFUSES if any era names a kind other than ``fixed_days`` (unreachable: the
vocabulary holds no other row), because such a row would become a fixed-days
era the moment the column went.

**Downgrade REFUSES a month-kind era** (one with ``cadence_days IS NULL``),
because the schema it restores has no columns to hold it and inventing a
day count would be a downgrade that silently rewrites money (ledger row
``balance:BAL-464``'s rule).  Otherwise it re-creates ``ref.pay_cadence_kinds``
seeded with ``fixed_days``, restores ``kind_id`` naming that member on every
row, re-adds its key and the ``NOT NULL`` on ``cadence_days``, and drops the
two month columns and the three CHECKs.
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3ec5291ca4e2"
down_revision = "ef32dfe4cd8e"
branch_labels = None
depends_on = None


#: The key ``C17-a`` (``6fc77e86d76f``) gave ``kind_id``; dropped here and
#: re-created by the downgrade under the same name.
_FK_KIND = "fk_pay_eras_kind_id"

#: The three CHECKs this revision installs, ``(name, text)``.  The texts are
#: the model's ``ONE_KIND_CHECK`` / ``NOMINAL_DAY_CHECK`` / ``OTHER_DAY_CHECK``
#: verbatim, held equal by ``test_pay_era.py`` rather than imported: a
#: revision records what it installed.
_CHECKS = (
    (
        "ck_pay_eras_one_kind",
        "cadence_days IS NULL OR (nominal_day IS NULL AND other_day IS NULL)",
    ),
    (
        "ck_pay_eras_nominal_day",
        "nominal_day IS NULL OR ("
        "nominal_day BETWEEN 29 AND 31 "
        "AND nominal_day > EXTRACT(day FROM effective_from) "
        "AND EXTRACT(day FROM effective_from) = LEAST(nominal_day, "
        "EXTRACT(day FROM (date_trunc('month', effective_from::timestamp) "
        "+ INTERVAL '1 month - 1 day'))))",
    ),
    (
        "ck_pay_eras_other_day",
        "other_day IS NULL OR ("
        "other_day BETWEEN 1 AND 31 "
        "AND other_day <> COALESCE(nominal_day, EXTRACT(day FROM effective_from)) "
        "AND LEAST(other_day, COALESCE(nominal_day, "
        "EXTRACT(day FROM effective_from))) <= 27)",
    ),
)

#: The one member the vocabulary ever held; the downgrade re-seeds it and
#: names every era with it.
_FIXED_DAYS = "fixed_days"

#: Upgrade guard: an era whose kind is not ``fixed_days`` would silently
#: become one when the column goes.  Unreachable while the vocabulary holds
#: one row, and asked rather than assumed.
_ERAS_NOT_FIXED_DAYS_SQL = """
    SELECT e.id
      FROM budget.pay_eras AS e
      JOIN ref.pay_cadence_kinds AS k ON k.id = e.kind_id
     WHERE k.name <> :fixed_days
     ORDER BY e.id
"""

#: Downgrade guard: a day-of-month era has no home in the old schema.
_MONTH_KIND_ERAS_SQL = """
    SELECT id, user_id, effective_from
      FROM budget.pay_eras
     WHERE cadence_days IS NULL
     ORDER BY id
"""

_SEED_KINDS_SQL = "INSERT INTO ref.pay_cadence_kinds (name) VALUES (:fixed_days)"

_RESTORE_KIND_SQL = """
    UPDATE budget.pay_eras
       SET kind_id = (SELECT id FROM ref.pay_cadence_kinds WHERE name = :fixed_days)
"""


def upgrade():
    """Add the month columns, drop the kind column and its vocabulary."""
    bind = op.get_bind()
    foreign = [
        row.id for row in bind.execute(
            sa.text(_ERAS_NOT_FIXED_DAYS_SQL), {"fixed_days": _FIXED_DAYS},
        )
    ]
    if foreign:
        raise RuntimeError(
            f"budget.pay_eras row(s) {foreign} name a cadence kind other than "
            f"'fixed_days', which this revision would silently read as a "
            f"fixed-days era once kind_id is gone.  ref.pay_cadence_kinds has "
            f"only ever held 'fixed_days', so reaching this means the "
            f"vocabulary was extended under the application; state those "
            f"eras' rhythm through the era writer after this revision, or "
            f"remove them, then re-run."
        )

    op.add_column(
        "pay_eras", sa.Column("nominal_day", sa.SmallInteger(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "pay_eras", sa.Column("other_day", sa.SmallInteger(), nullable=True),
        schema="budget",
    )
    op.alter_column("pay_eras", "cadence_days", nullable=True, schema="budget")
    for name, text in _CHECKS:
        op.create_check_constraint(name, "pay_eras", text, schema="budget")

    # The key before the column, and the column before the table it
    # referenced, for the lock-order reason 6fc77e86d76f's downgrade gives.
    op.drop_constraint(_FK_KIND, "pay_eras", type_="foreignkey", schema="budget")
    op.drop_column("pay_eras", "kind_id", schema="budget")
    op.drop_table("pay_cadence_kinds", schema="ref")


def downgrade():
    """Restore the kind column and vocabulary; refuse a month-kind era."""
    bind = op.get_bind()
    month_kind = [
        (row.id, row.user_id, row.effective_from.isoformat())
        for row in bind.execute(sa.text(_MONTH_KIND_ERAS_SQL))
    ]
    if month_kind:
        raise RuntimeError(
            f"budget.pay_eras holds day-of-month era(s) (id, user_id, "
            f"effective_from) {month_kind}, and the schema this downgrade "
            f"restores has no columns for a monthly or semi-monthly rhythm.  "
            f"This revision holds no day count to write and refuses to "
            f"invent one.  Restate those owners' schedules on a fixed-days "
            f"rhythm through the application, or remove the eras, then "
            f"re-run."
        )

    op.create_table(
        "pay_cadence_kinds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(sa.text(_SEED_KINDS_SQL).bindparams(fixed_days=_FIXED_DAYS))

    op.add_column(
        "pay_eras", sa.Column("kind_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.execute(sa.text(_RESTORE_KIND_SQL).bindparams(fixed_days=_FIXED_DAYS))
    op.alter_column("pay_eras", "kind_id", nullable=False, schema="budget")
    op.create_foreign_key(
        _FK_KIND, "pay_eras", "pay_cadence_kinds", ["kind_id"], ["id"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )

    for name, _text in _CHECKS:
        op.drop_constraint(name, "pay_eras", type_="check", schema="budget")
    op.alter_column("pay_eras", "cadence_days", nullable=False, schema="budget")
    op.drop_column("pay_eras", "other_day", schema="budget")
    op.drop_column("pay_eras", "nominal_day", schema="budget")
