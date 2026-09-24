"""a pay stub is transcribed line by line

Revision ID: 5641f7729b68
Revises: 764461215480
Create Date: 2026-09-23

Plan step **salary:S11-a**, the tables leaf of ``S11`` ("a calibration is the
STUB TRANSCRIBED, line by line, dated"; ruling **R-SAL42**, amending
**R-SAL41** and **R-SAL9**; developer, four rounds, 2026-09-23)::

    ref.withholding_kinds                NEW, seeded (four rows)
    salary.paycheck_lines                + uq_paycheck_lines_id_profile
    salary.pay_stubs                     NEW, EMPTY
    salary.pay_stub_line_amounts         NEW, EMPTY
    salary.pay_stub_withholdings         NEW, EMPTY
    salary.pay_stub_one_offs             NEW, EMPTY
    salary.refuse_pay_stub_loss()        NEW trigger function, six attachments

**A table per kind of line** (round 4, fork 11).  The stub row holds its payday
(unique per profile, fork 4), its base pay (required, above zero) and the "Use
for pricing" switch (fork 8a', on by default).  Beneath it three small tables,
every column NOT NULL: one amount per PAYCHECK LINE, one amount per TAX (from
the new ``ref.withholding_kinds`` list, so a city tax later is one new row),
and named ONE-OFFS (fork 8b).  Amounts are ``>= 0``.  Gross, taxable wages and
net pay are NOT stored: they derive from the lines (rule 14).

**No figure moves.**  Every new table is created EMPTY; no door writes them
until ``S11-b`` and nothing prices from them until ``S11-c``.  The one change to
an existing table, ``uq_paycheck_lines_id_profile`` over ``(id,
salary_profile_id)``, is unique by construction (``id`` is the key), so it
cannot fail on any database and changes no row.

**The keys, and why each deletes the way it does.**

* ``fk_pay_stubs_salary_profile_id`` is ``ON DELETE RESTRICT``, not the
  ``CASCADE`` the raises, the paycheck lines, the calibration and the YTD
  checkpoints carry.  A transcribed stub is a record ("Nothing is ever
  deleted", fork 8a'), so a profile holding one cannot be hard-deleted -- nor,
  through the profile's own cascade from its user, can the owner.  The salary
  door archives a profile and never deletes one;
  ``fk_investment_params_salary_profile_id`` is ``RESTRICT`` on the same
  ground ("a profile is archived rather than deleted").
* ``fk_pay_stub_line_amounts_paycheck_line`` is ``ON DELETE RESTRICT`` (fork
  10: a paycheck line a stub names is ended, never deleted).  A profile delete
  reaches a stub two ways -- through the stub's own key, and through the
  paycheck line's cascade -- and ruling **R-CC32**'s principle is that the
  outcome must not depend on which PostgreSQL evaluates first.  Two guards
  make it so: the stub key's ``RESTRICT`` refuses at the first level (so the
  refusal names it), and the trigger family below refuses a ``DELETE`` of a
  stub however it arrives, a cascaded one included, so even a ``CASCADE`` on
  that key would be refused rather than let trigger order decide.
* A line amount carries its stub's ``salary_profile_id`` as a co-located key,
  held equal to the stub's by ``fk_pay_stub_line_amounts_pay_stub`` (onto
  ``uq_pay_stubs_id_profile``) and to the paycheck line's by
  ``fk_pay_stub_line_amounts_paycheck_line`` (onto the new
  ``uq_paycheck_lines_id_profile``).  So a stub cannot name another profile's
  line: the ``fk_transaction_entries_owner_*`` construction (ruling
  **R-BAL76**).
* The three child tables' stub keys are ``ON DELETE CASCADE``: a line of a stub
  is PART of it.  The stub itself cannot be deleted while the trigger below
  stands, so that cascade runs only under a deliberate lift.

**A stub is never deleted and never moved, for every routine writer** (ruling
**R-SAL44**, the developer's "Refuse delete and move", extended by **R-SAL46**,
"Keep the line-table guard").  One trigger function
(:mod:`app.pay_stub_infrastructure`) refuses a ``DELETE`` of a stub, a change
of its ``salary_profile_id``, and a ``TRUNCATE`` of any of the four stub
tables; the stub's payday, base pay, switch, notes and lines stay editable.
It is installed here, beside the tables it guards; ``scripts/init_database.py``
applies it on a fresh database and the test template re-applies it, like every
sibling family.  Its limits (a superuser disabling triggers) are stated in that
module.

**The CHECKs** are stated identically on the models (``app/models/pay_stub.py``);
autogenerate does not diff a CHECK, so ``tests/test_models/test_pay_stub.py``
compares :data:`_CHECKS` with the models' constraints, name for name.

**Audited.**  The four ``salary`` tables hold user-entered payroll records and
the entry door EDITS a stub in place ("the audit log keeps the old figures",
fork 4), so each gets ``audit_<table>`` here, the ``97f92340fffc`` shape, and
``AUDITED_TABLES`` in ``app/audit_infrastructure.py`` carries the matching
rows.  ``ref.withholding_kinds`` is a read-only seed catalogue and is not
audited, on the criteria that keep every other ref catalogue out.

**Inline seed rationale.**  The four rows are seeded here so ``ref_cache.init()``
resolves ``WithholdingKindEnum`` immediately after a bare ``flask db upgrade``;
``app/ref_seeds.py`` carries the identical rows for the idempotent reseed (the
dual-seed pattern ``tests/test_models/test_posting_ref_seed_parity.py``
guards).

**One import from ``app``**: :mod:`app.pay_stub_infrastructure`, so the
trigger's SQL has ONE home shared with ``scripts/init_database.py`` and the test
template -- the construction ``af07125d00f1`` uses for
:mod:`app.sighting_infrastructure`.  Nothing else from ``app`` is imported.
**Its cost, named:** a replay of this revision from an empty database runs the
module's CURRENT SQL, so if the family ever names a table or column newer than
this revision, the replay fails -- loudly, because the test template replays
the chain from empty on every image build.  ``d2e9f4a17c63`` records that
construction biting once; the remedy then is to freeze this revision's SQL
inline.

**The downgrade REFUSES while any stub exists** (ruling **R-SAL47**, "Refuse
while stubs exist"; the ``af07125d00f1`` precedent for user-entered data).  The
older schema has nowhere to hold a stub, so a rollback after the owner has
transcribed stubs (the operator step between ``S11-b`` and ``S11-c``, per
**R-SAL40**) would destroy them -- and once ``S11-c`` prices from them, silently
move projected paychecks.  It raises a ``RuntimeError`` naming the count, before
writing anything.  On a database holding no stub it drops the trigger function,
every table this creates and the superkey, in dependency order.
"""
from alembic import op
import sqlalchemy as sa

from app.pay_stub_infrastructure import (
    apply_pay_stub_infrastructure,
    remove_pay_stub_infrastructure,
)


# Revision identifiers, used by Alembic.
revision = "5641f7729b68"
down_revision = "764461215480"
branch_labels = None
depends_on = None


_SCHEMA = "salary"

#: The tables gaining an audit trigger here (see the docstring).
_AUDITED_NEW_TABLES = (
    "pay_stubs",
    "pay_stub_line_amounts",
    "pay_stub_withholdings",
    "pay_stub_one_offs",
)

# The rows ``WithholdingKindEnum`` names, as literal SQL: the cross-migration
# inline-seed guard scans this chain for each enum value as a single-quoted
# literal inside an ``INSERT INTO`` its own ref table.
_SEED_WITHHOLDING_KINDS_SQL = (
    "INSERT INTO ref.withholding_kinds (name) VALUES "
    "('federal_income'), "
    "('state_income'), "
    "('social_security'), "
    "('medicare') "
    "ON CONFLICT (name) DO NOTHING"
)

# Every CHECK this revision creates, per table.  Stated identically on the
# models (``app/models/pay_stub.py``), by name, so the test that compares the
# two can read them as a mapping.
_CHECKS = {
    "pay_stubs": {
        "ck_pay_stubs_positive_base_pay": "base_pay > 0",
        "ck_pay_stubs_version_id_positive": "version_id > 0",
    },
    "pay_stub_line_amounts": {
        "ck_pay_stub_line_amounts_nonneg_amount": "amount >= 0",
    },
    "pay_stub_withholdings": {
        "ck_pay_stub_withholdings_nonneg_amount": "amount >= 0",
    },
    "pay_stub_one_offs": {
        "ck_pay_stub_one_offs_nonneg_amount": "amount >= 0",
        "ck_pay_stub_one_offs_name_not_blank": "btrim(name) <> ''",
    },
}


def _checks_for(table: str) -> list[sa.CheckConstraint]:
    """Return *table*'s CHECK constraints from :data:`_CHECKS`.

    Args:
        table: The unqualified table name.

    Returns:
        One named :class:`sqlalchemy.CheckConstraint` per entry.
    """
    return [
        sa.CheckConstraint(sqltext, name=name)
        for name, sqltext in _CHECKS[table].items()
    ]


def _create_withholding_kinds() -> None:
    """Create and seed ``ref.withholding_kinds``.

    Its single-column primary key and ``UNIQUE (name)`` take PostgreSQL's
    generated names, the exemption every ``ref`` lookup table has (developer
    ruling 2026-08-14, ledger row ``recurrence:F-3``).
    """
    op.create_table(
        "withholding_kinds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_WITHHOLDING_KINDS_SQL)


def _create_pay_stubs() -> None:
    """Create ``salary.pay_stubs``, the stub row."""
    op.create_table(
        "pay_stubs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("salary_profile_id", sa.Integer(), nullable=False),
        sa.Column("payday", sa.Date(), nullable=False),
        sa.Column("base_pay", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "use_for_pricing", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        # The mixin-carried columns render after the model's own; order is
        # load-bearing nowhere (``app/models/mixins.py``).
        sa.Column(
            "version_id", sa.Integer(), server_default="1", nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        *_checks_for("pay_stubs"),
        sa.ForeignKeyConstraint(
            ["salary_profile_id"], ["salary.salary_profiles.id"],
            name="fk_pay_stubs_salary_profile_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "salary_profile_id", "payday",
            name="uq_pay_stubs_profile_payday",
        ),
        sa.UniqueConstraint(
            "id", "salary_profile_id",
            name="uq_pay_stubs_id_profile",
        ),
        schema=_SCHEMA,
    )


def _create_pay_stub_line_amounts() -> None:
    """Create ``salary.pay_stub_line_amounts``, one amount per paycheck line."""
    op.create_table(
        "pay_stub_line_amounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pay_stub_id", sa.Integer(), nullable=False),
        sa.Column("paycheck_line_id", sa.Integer(), nullable=False),
        sa.Column("salary_profile_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        *_checks_for("pay_stub_line_amounts"),
        sa.ForeignKeyConstraint(
            ["pay_stub_id", "salary_profile_id"],
            ["salary.pay_stubs.id", "salary.pay_stubs.salary_profile_id"],
            name="fk_pay_stub_line_amounts_pay_stub",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paycheck_line_id", "salary_profile_id"],
            [
                "salary.paycheck_lines.id",
                "salary.paycheck_lines.salary_profile_id",
            ],
            name="fk_pay_stub_line_amounts_paycheck_line",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pay_stub_id", "paycheck_line_id",
            name="uq_pay_stub_line_amounts_stub_line",
        ),
        schema=_SCHEMA,
    )


def _create_pay_stub_withholdings() -> None:
    """Create ``salary.pay_stub_withholdings``, one amount per tax."""
    op.create_table(
        "pay_stub_withholdings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pay_stub_id", sa.Integer(), nullable=False),
        sa.Column("withholding_kind_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        *_checks_for("pay_stub_withholdings"),
        sa.ForeignKeyConstraint(
            ["pay_stub_id"], ["salary.pay_stubs.id"],
            name="fk_pay_stub_withholdings_pay_stub_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["withholding_kind_id"], ["ref.withholding_kinds.id"],
            name="fk_pay_stub_withholdings_withholding_kind_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pay_stub_id", "withholding_kind_id",
            name="uq_pay_stub_withholdings_stub_kind",
        ),
        schema=_SCHEMA,
    )


def _create_pay_stub_one_offs() -> None:
    """Create ``salary.pay_stub_one_offs``, the named one-off amounts."""
    op.create_table(
        "pay_stub_one_offs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pay_stub_id", sa.Integer(), nullable=False),
        sa.Column("paycheck_line_kind_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        *_checks_for("pay_stub_one_offs"),
        sa.ForeignKeyConstraint(
            ["pay_stub_id"], ["salary.pay_stubs.id"],
            name="fk_pay_stub_one_offs_pay_stub_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paycheck_line_kind_id"], ["ref.paycheck_line_kinds.id"],
            name="fk_pay_stub_one_offs_paycheck_line_kind_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pay_stub_id", "name",
            name="uq_pay_stub_one_offs_stub_name",
        ),
        schema=_SCHEMA,
    )


def _refuse_while_stubs_exist(bind) -> None:
    """Stop the downgrade before it writes anything if any stub exists.

    Ruling **R-SAL47**: a transcribed stub is the owner's record of a real
    document, and the schema this downgrade returns to has nowhere to hold it.

    Args:
        bind: A SQLAlchemy connection to count the stubs on.

    Raises:
        RuntimeError: When ``salary.pay_stubs`` holds any row, naming how many.
    """
    count = bind.execute(sa.text("SELECT COUNT(*) FROM salary.pay_stubs")).scalar()
    if count:
        raise RuntimeError(
            f"{count} transcribed pay stub(s) exist in salary.pay_stubs; "
            "downgrading past 5641f7729b68 would destroy them (ruling "
            "R-SAL47).  Export or remove them deliberately -- lifting "
            "app.pay_stub_infrastructure first -- then downgrade again."
        )


def upgrade():
    """Create the catalogue, the superkey, the four tables and their triggers.

    Order is load-bearing: the catalogue and the ``paycheck_lines`` superkey
    exist before the keys that target them, ``pay_stubs`` before its three
    children, and all four tables before the refusal family attaches to them.
    """
    _create_withholding_kinds()
    op.create_unique_constraint(
        "uq_paycheck_lines_id_profile", "paycheck_lines",
        ["id", "salary_profile_id"], schema=_SCHEMA,
    )
    _create_pay_stubs()
    _create_pay_stub_line_amounts()
    _create_pay_stub_withholdings()
    _create_pay_stub_one_offs()

    # Trigger name ``audit_<table>``: the name the deploy's check
    # (``app.audit_infrastructure.require_audit_triggers``) counts by prefix
    # and looks up per table, so that a short count names each table missing
    # its trigger (``_audit_trigger_name``).  The shared
    # ``system.audit_trigger_func`` already exists.
    for table in _AUDITED_NEW_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS audit_{table} ON {_SCHEMA}.{table}")
        op.execute(
            f"CREATE TRIGGER audit_{table} "
            f"AFTER INSERT OR UPDATE OR DELETE ON {_SCHEMA}.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
        )

    # A stub is never deleted and never moved (ruling R-SAL44).
    apply_pay_stub_infrastructure(op.execute)


def downgrade():
    """Drop the trigger function, the four tables, the superkey and the catalogue.

    Refuses first, writing nothing, while any stub exists (ruling R-SAL47).
    Otherwise the refusal family goes first, so its function is not left
    behind; each table's triggers and constraints go with the table, children
    first.  ``DROP TABLE`` is not a ``DELETE`` or a ``TRUNCATE``, so the
    family's own arms never see it.

    Raises:
        RuntimeError: When ``salary.pay_stubs`` holds any row.
    """
    _refuse_while_stubs_exist(op.get_bind())
    remove_pay_stub_infrastructure(op.execute)
    op.drop_table("pay_stub_one_offs", schema=_SCHEMA)
    op.drop_table("pay_stub_withholdings", schema=_SCHEMA)
    op.drop_table("pay_stub_line_amounts", schema=_SCHEMA)
    op.drop_table("pay_stubs", schema=_SCHEMA)
    op.drop_constraint(
        "uq_paycheck_lines_id_profile", "paycheck_lines",
        schema=_SCHEMA, type_="unique",
    )
    op.drop_table("withholding_kinds", schema="ref")
