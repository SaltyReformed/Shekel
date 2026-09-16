"""a movement's figure names its source, and the settle's movement says so

Plan step **balance:X-bi-3a** (leaf 3a of ``X-bi-3``) of
``docs/audits/balance_architecture/README.md`` section 5, under ruling
**R-BAL39** (developer, 2026-09-15)::

    ref.movement_figure_sources                       NEW, seeded
    budget.transaction_entries.figure_source_id       NEW, NOT NULL, FK RESTRICT
    budget.transaction_entries.covers_settlement      NEW, NOT NULL, default false
    uq_transaction_entries_one_settlement_record      NEW partial unique index

**Every movement says WHO WROTE its figure.**  A row of ``transaction_entries``
is a movement -- a purchase recorded against an envelope, and since X-bi-3a the
COVERING MOVEMENT a settle writes for a bill, the payment row that records a
bill's money the way a purchase records an envelope's.  Three writers put a
figure on one, and until this revision nothing on the row said which: the
settle pricing it from the plan (``resolved``), a person stating it
(``typed``), or the bank's own line stating it (``observed``).  It is the
figure's twin of the settle-day basis ``c7d31f9a45e8`` gave the day beside it,
for the same reason (three writers, nothing said which) and for a reader:
``status_seam.Settlement.from_settle`` re-prices a ``derived`` record and
honours a ``corrected`` one, and once a bill's figure lives on its covering
movement (``balance:X-bi-4`` makes ``transactions.settled_amount`` derivable)
that distinction has no other home.  The catalogue is new rather than a reuse
of ``ref.settlement_bases``: that one's ``purchases`` member means nothing on a
movement, and its ``corrected`` member is defined as *a human typed it*, which
a bank-born purchase would have to claim falsely.

**No figure moves.**  The column is metadata about a figure; every balance,
fold and posting reads the figure itself, which this revision touches on no
row.

**``covers_settlement`` says which movement IS its parent's settlement
record.**  The status seam writes one covering movement per manual-branch
settle and must find its own mirror again on a re-settle, a revert and a day
correction; a settled row may legitimately hold real purchases beside it
(*Track individual purchases* unticked on a settled envelope, then a figure
typed over it), so the record's identity is a stored fact of the movement
and never a derivation over the row.  Default ``false``: every row this
migration meets is a purchase, and only the seam ever writes ``true``.  The
partial unique index holds the count at one per row.

**The backfill is a PREDICATE over the row's own day basis, never over its
parent** (ruling **R-BAL39**): a purchase whose settle day the BANK observed
carries a figure the bank's line stated -- a purchase born from a line is
written with both, and a purchase a line was matched to has both raised by the
match (``statement_match._moving`` writes an ``observed`` day and, where the
match reprices, the line's figure) -- so ``observed`` day -> ``observed``
figure; every other purchase was typed by a person, the only other writer this
table has had.  No purchase on this table is ``resolved`` before X-bi-3a's
seam writes the first covering movement, so that arm is empty by construction
here and is not stated.  The parent's ``settled_basis_id`` is deliberately NOT
consulted: an envelope's basis is ``purchases``, which says nothing about how
any one of its purchases was priced.

Measured 2026-09-15 on the developer's dev snapshot (rows to 2026-09-06): 100
purchases -- 39 with an ``observed`` day, 36 ``asserted``, 7 ``entered``, 18
undated -- so the backfill lands **39 ``observed`` / 61 ``typed``** there.

**NOT NULL on a populated table takes the three steps
``docs/coding-standards.md`` requires**: add nullable, backfill, then
``SET NOT NULL`` after :func:`_refuse_unclassified` proves zero NULLs survive
(a ``RuntimeError`` naming the diagnostic query otherwise).  Every row is
reached by one of the two arms, so the refusal cannot fire on data this schema
admits; it stands as the backstop the standard asks for.

**Not audited.**  ``ref.movement_figure_sources`` is a read-only seed
catalogue, excluded from ``app.audit_infrastructure.AUDITED_TABLES`` on the
criteria that keep ``ref.settled_day_bases`` and every other ref catalogue
out.  ``budget.transaction_entries`` is already audited, so its trigger records
the new column with no change here.

**Inline seed rationale.**  The three rows are seeded here so
``ref_cache.init()`` resolves ``MovementFigureSourceEnum`` immediately after a
bare ``flask db upgrade``; ``app/ref_seeds.py`` carries the identical rows for
the entrypoint's idempotent reseed (the dual-seed pattern
``tests/test_models/test_posting_ref_seed_parity.py`` guards).

**The downgrade is lossless for every value the older schema can hold.**  It
drops a column the code it returns to never read and the catalogue with it;
what is lost is the ``typed`` / ``observed`` distinction, which the older
schema had no way to hold and no reader for -- stated rather than hidden, and
it is not money.

Revision ID: b5c7e9a1d2f4
Revises: 0a4d2c3e89f8
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b5c7e9a1d2f4"
down_revision = "0a4d2c3e89f8"
branch_labels = None
depends_on = None


# The rows ``MovementFigureSourceEnum`` names, as literal SQL: the
# cross-migration inline-seed guard scans this chain for each enum value as a
# single-quoted literal inside an ``INSERT INTO`` its own ref table.
_SEED_MOVEMENT_FIGURE_SOURCES_SQL = (
    "INSERT INTO ref.movement_figure_sources (name) VALUES "
    "('resolved'), "
    "('typed'), "
    "('observed') "
    "ON CONFLICT (name) DO NOTHING"
)

_UNCLASSIFIED_SQL = (
    "SELECT COUNT(*) FROM budget.transaction_entries WHERE figure_source_id IS NULL"
)


def _source(name: str) -> str:
    """Return a scalar subquery for one ``ref.movement_figure_sources`` id.

    A migration resolves a ref row by its NAME because the id is assigned by
    the sequence and differs between databases; application code never does
    this (``ref_cache.movement_figure_source_id`` is that door).

    Args:
        name: The ``MovementFigureSourceEnum`` value to resolve.

    Returns:
        The SQL for a parenthesised scalar subquery yielding that row's id.
    """
    return f"(SELECT id FROM ref.movement_figure_sources WHERE name = '{name}')"


def classify_figure_sources(bind) -> None:
    """Run the two backfill arms against *bind*.

    **Module-level so a test can DRIVE it**, the chain's own pattern
    (``c7d31f9a45e8.classify_settle_days``): a backfill that classifies the
    provenance of money has to be executable by a control, because a test over
    a docstring grades a docstring.

    Order is load-bearing: OBSERVED first, and the second arm narrows on
    ``figure_source_id IS NULL`` so a purchase the bank stated cannot be
    re-claimed as typed.

    Args:
        bind: A SQLAlchemy connection to run the two ``UPDATE``s on.
    """
    # (1) OBSERVED -- the purchase's own day basis says the bank observed the
    # day, so the bank's line stated the figure (see the module docstring for
    # why the two arrive together).  The predicate is the row's OWN column,
    # never the parent's ``settled_basis_id``.
    bind.execute(sa.text(
        "UPDATE budget.transaction_entries e "
        f"SET figure_source_id = {_source('observed')} "
        "WHERE e.settled_day_basis_id = "
        "(SELECT id FROM ref.settled_day_bases WHERE name = 'observed')"
    ))
    # (2) TYPED -- every other purchase.  Undated, or dated by the owner's own
    # word or by a balance assertion's bound: a person typed the figure.
    bind.execute(sa.text(
        "UPDATE budget.transaction_entries e "
        f"SET figure_source_id = {_source('typed')} "
        "WHERE e.figure_source_id IS NULL"
    ))


def _refuse_unclassified(bind) -> None:
    """Stop before ``SET NOT NULL`` if any purchase escaped both arms.

    Unreachable on data this schema admits -- arm 2 is total over the rows arm
    1 left -- and kept as the backstop the NOT NULL standard requires, naming
    the count and the diagnostic query rather than dying as a bare
    ``NotNullViolation``.
    """
    count = bind.execute(sa.text(_UNCLASSIFIED_SQL)).scalar()
    if count:
        raise RuntimeError(
            f"{count} purchase(s) carry no figure source after the backfill; "
            f"the classifier is not total.  Diagnose with: {_UNCLASSIFIED_SQL}"
        )


def upgrade():
    """Create the catalogue, add the column, classify every purchase, NOT NULL.

    Order is load-bearing: the ref table exists before the foreign key targets
    it, the column is added nullable so the backfill has somewhere to write,
    and ``SET NOT NULL`` follows the zero-NULL proof.
    """
    op.create_table(
        "movement_figure_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_MOVEMENT_FIGURE_SOURCES_SQL)
    op.add_column(
        "transaction_entries",
        sa.Column("figure_source_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_transaction_entries_figure_source_id",
        "transaction_entries", "movement_figure_sources",
        ["figure_source_id"], ["id"],
        source_schema="budget", referent_schema="ref",
        ondelete="RESTRICT",
    )
    bind = op.get_bind()
    classify_figure_sources(bind)
    _refuse_unclassified(bind)
    op.alter_column(
        "transaction_entries", "figure_source_id",
        nullable=False, schema="budget",
    )
    op.add_column(
        "transaction_entries",
        sa.Column(
            "covers_settlement", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        schema="budget",
    )
    op.create_index(
        "uq_transaction_entries_one_settlement_record",
        "transaction_entries", ["transaction_id"],
        unique=True, schema="budget",
        postgresql_where=sa.text("covers_settlement = true"),
    )


def downgrade():
    """Drop the column and the catalogue; lossless for the older schema.

    The code this returns to never read either, and no balance, fold or posting
    reads the column (module docstring).
    """
    op.drop_index(
        "uq_transaction_entries_one_settlement_record",
        table_name="transaction_entries", schema="budget",
    )
    op.drop_column("transaction_entries", "covers_settlement", schema="budget")
    op.drop_constraint(
        "fk_transaction_entries_figure_source_id", "transaction_entries",
        schema="budget", type_="foreignkey",
    )
    op.drop_column("transaction_entries", "figure_source_id", schema="budget")
    op.drop_table("movement_figure_sources", schema="ref")
