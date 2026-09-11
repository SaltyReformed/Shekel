"""a pay schedule is a sequence of eras

Creates ``budget.pay_eras`` -- one row per *how I have been paid since* --
backfills one era per owner from the rhythm ``budget.pay_schedule`` holds, and
drops that rhythm from the schedule row.  Plan step **pay_calendar:C17-a**,
ruling **R-PC58**; the relation's shape is the developer's ruling of
2026-09-11 (a new table beside the schedule row, and the era's
``effective_from`` IS the grid's phase).

**What it is for.**  ``budget.pay_schedule`` held ONE ``cadence_days``, one
``shift_id`` and one ``nominal_anchor`` per owner, and every batch that
recorded a payday overwrote all three.  So "correct my cadence going forward"
silently re-described every PAST payday too: an owner who moved from
fortnightly to weekly held paydays 14 and 35 days apart under one stored
cadence of 7, and no producer could ask what cadence a past payday ran at
(ledger row **N-492**).  An era is that fact with a home.  The three columns
leave the schedule row with it, because a value kept in two relations is two
sources (rule 14).

**The backfill states ONE era per owner who holds a payday**, from the row's
own cadence and convention, taking effect on the grid day at or below the
owner's earliest recorded payday.  The grid is the one ``nominal_anchor``
phases -- a day the writer stepped the owner's last batch from -- so the era's
``effective_from`` is that anchor walked back by whole cadences to the record's
opening:

    effective_from = MIN(start_date) - ((MIN(start_date) - nominal_anchor)
                                         modulo cadence_days)

with the modulus normalised to ``0..cadence-1`` because PostgreSQL's ``%``
keeps the dividend's sign.  For an owner whose convention is ``none`` that is
``MIN(start_date)`` itself whenever the record's opening sits on the grid,
which on production it does (63 paydays, every gap exactly 14 days, measured
2026-09-04 in ruling **R-PC55**).

**What the backfill states for a PIECEWISE owner, and why it does not
guess.**  An owner who moved from fortnightly to weekly (ledger row
**N-492**'s example) holds paydays 14 days apart under a stored cadence of 7:
the row already describes their past wrongly, and this migration cannot know
the earlier cadence.  It states the row's rhythm as ONE era from the record's
opening -- exactly the claim the row made -- rather than inventing a second
era, and the relation it creates is what lets an operator mint the earlier
era the row could never hold.  *A first draft refused any owner with a
recorded payday off the backfilled grid, and an adversarial review of C17-a
struck it twice over: such a refusal cannot SEE the fortnightly-to-weekly
owner (every 14-day gap is a multiple of 7), and it DOES refuse a legal
state -- ruling **R-PC47** says a recorded payday may fall off the cadence
and is warned about, never rewritten -- with a message that told the operator
to correct the record.*  What the upgrade still refuses is an owner holding
paydays and no ``nominal_anchor``, because the backfill then has no grid to
phase on (below).

**``effective_from`` for a backfilled era is a grid day AT OR BELOW the
record's opening**, walked back from the anchor by whole cadences.  Under a
displacing convention the opening is a CASH day, so that grid day can be one
whole cadence below the era's true first nominal payday -- ``$0.00``, since
every producer reads the phase modulo the cadence, but stated because the
model documents ``effective_from`` as the first nominal payday and for such
an owner it is the grid day before it.  Production's convention is ``none``,
where the two coincide.

**Why the schedule row with NO payday gets no era.**  Such a row (a state
``pay_period_admin.reset_pay_periods`` passes through inside one
transaction, and one the ``a1c7e5d20f43`` backfill left with a NULL anchor)
states no rhythm anybody was paid on; its cadence and convention were the
form's defaults or a batch that was later wiped.  An era is *how I have been
paid since*, and nobody was.  The application reads such an owner as having
no calendar, exactly as it reads an owner with no row.

**Downgrade restores the three columns from each owner's LATEST era** -- the
row the old schema would have held, since every batch overwrote it with the
most recent rhythm -- with ``nominal_anchor`` set to the era's
``effective_from``, a day on the same grid.  A multi-era owner's earlier eras
are not representable in the old schema and are dropped, which is the state
the old schema held for them.  **It REFUSES an owner holding the schedule
row and no era**, because the old schema requires a cadence there and this
migration holds no fact to write; the message names the rows.  Refusing is
the honest form of a downgrade that cannot be lossless (ledger row
**BAL-464**: a downgrade that runs clean and silently invents data grades a
property that is false).

Revision ID: 6fc77e86d76f
Revises: b7e4c1f38a20
Create Date: 2026-09-11
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "6fc77e86d76f"
down_revision = "b7e4c1f38a20"
branch_labels = None
depends_on = None


#: The three named keys ``budget.pay_eras`` carries besides the mixin's key to
#: ``auth.users``; named once because the downgrade drops them by name.
_FK_KIND = "fk_pay_eras_kind_id"
_FK_SHIFT = "fk_pay_eras_shift_id"
_FK_SCHEDULE = "fk_pay_eras_schedule"

#: The one member of ``ref.pay_cadence_kinds`` this step needs.  Seeded here so
#: a freshly upgraded database resolves the enum before the idempotent reseed
#: runs -- the dual-seed pattern every ref vocabulary uses.  The name matches
#: ``app.enums.PayCadenceKindEnum.FIXED_DAYS.value`` exactly.
_SEED_KINDS_SQL = "INSERT INTO ref.pay_cadence_kinds (name) VALUES ('fixed_days')"

#: Per owner holding a payday: the grid facts the backfill reads, and the day
#: it derives.  ``anchor_gap`` is ``MIN(start_date) - nominal_anchor`` reduced
#: to ``0..cadence-1`` -- the distance from the record's opening back to the
#: nearest grid day at or below it.
_OWNER_GRIDS_SQL = """
    SELECT s.user_id,
           s.cadence_days,
           s.shift_id,
           s.nominal_anchor,
           MIN(p.start_date) AS opening,
           (((MIN(p.start_date) - s.nominal_anchor) % s.cadence_days)
            + s.cadence_days) % s.cadence_days AS anchor_gap
      FROM budget.pay_schedule AS s
      JOIN budget.pay_periods AS p ON p.user_id = s.user_id
     GROUP BY s.user_id, s.cadence_days, s.shift_id, s.nominal_anchor
"""

#: Owners with paydays and no anchor: the backfill has no grid to phase.
_OWNERS_WITHOUT_ANCHOR_SQL = f"""
    SELECT user_id FROM ({_OWNER_GRIDS_SQL}) AS g
     WHERE g.nominal_anchor IS NULL
     ORDER BY user_id
"""

#: The backfill itself: one era per owner holding a payday, taking effect on
#: the grid day at or below the record's opening.
_BACKFILL_ERAS_SQL = f"""
    INSERT INTO budget.pay_eras
        (user_id, effective_from, kind_id, cadence_days, shift_id)
    SELECT g.user_id,
           g.opening - g.anchor_gap,
           (SELECT id FROM ref.pay_cadence_kinds WHERE name = 'fixed_days'),
           g.cadence_days,
           g.shift_id
      FROM ({_OWNER_GRIDS_SQL}) AS g
     WHERE g.nominal_anchor IS NOT NULL
"""

#: The post-backfill proof: every owner holding a payday holds exactly one era.
_OWNERS_WITH_PAYDAYS_AND_NO_ERA_SQL = """
    SELECT DISTINCT p.user_id
      FROM budget.pay_periods AS p
      LEFT JOIN budget.pay_eras AS e ON e.user_id = p.user_id
     WHERE e.id IS NULL
     ORDER BY p.user_id
"""

#: Downgrade: the latest era per owner, which is the rhythm the old schema
#: would have held.
_LATEST_ERAS_SQL = """
    SELECT DISTINCT ON (user_id)
           user_id, effective_from, cadence_days, shift_id
      FROM budget.pay_eras
     ORDER BY user_id, effective_from DESC
"""

_RESTORE_RHYTHM_SQL = f"""
    UPDATE budget.pay_schedule AS s
       SET cadence_days = e.cadence_days,
           shift_id = e.shift_id,
           nominal_anchor = e.effective_from
      FROM ({_LATEST_ERAS_SQL}) AS e
     WHERE e.user_id = s.user_id
"""

#: Downgrade guard: a schedule row the old schema cannot hold, because it has
#: no era to take a cadence from.
_ROWS_WITHOUT_ERA_SQL = """
    SELECT s.user_id
      FROM budget.pay_schedule AS s
      LEFT JOIN budget.pay_eras AS e ON e.user_id = s.user_id
     WHERE e.id IS NULL
     ORDER BY s.user_id
"""


def _refuse_anchorless_owners(bind):
    """Stop before writing when an owner's paydays have no grid to phase on."""
    anchorless = [
        row.user_id
        for row in bind.execute(sa.text(_OWNERS_WITHOUT_ANCHOR_SQL))
    ]
    if anchorless:
        raise RuntimeError(
            f"budget.pay_schedule row(s) for user(s) {anchorless} hold paydays "
            f"and no nominal_anchor, so this migration has no grid to phase "
            f"their era on.  Migration a1c7e5d20f43 backfilled the anchor for "
            f"every owner holding a payday and the writer sets it on every "
            f"batch, so reaching this means paydays were inserted outside the "
            f"application.  Repair by recording a batch through the app (which "
            f"writes the anchor) or by setting nominal_anchor to a day on the "
            f"owner's pay grid, then re-run."
        )


def upgrade():
    """Create the era relation, backfill it, drop the rhythm off the row."""
    bind = op.get_bind()
    _refuse_anchorless_owners(bind)

    op.create_table(
        "pay_cadence_kinds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        schema="ref",
    )
    op.execute(_SEED_KINDS_SQL)

    op.create_table(
        "pay_eras",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("kind_id", sa.Integer(), nullable=False),
        sa.Column("cadence_days", sa.Integer(), nullable=False),
        sa.Column("shift_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "cadence_days BETWEEN 1 AND 365",
            name="ck_pay_eras_cadence_range",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["auth.users.id"], ondelete="CASCADE",
            name="pay_eras_user_id_fkey",
        ),
        # An era belongs to an owner who holds a schedule row, exactly as a
        # payday does through fk_pay_periods_schedule.
        sa.ForeignKeyConstraint(
            ["user_id"], ["budget.pay_schedule.user_id"],
            name=_FK_SCHEDULE, ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["kind_id"], ["ref.pay_cadence_kinds.id"],
            name=_FK_KIND, ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shift_id"], ["ref.business_day_shifts.id"],
            name=_FK_SHIFT, ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "effective_from", name="uq_pay_eras_user_effective_from",
        ),
        schema="budget",
    )
    # User-controlled financial configuration, so it carries the audit
    # trigger every such table does (``app.audit_infrastructure``).
    op.execute("DROP TRIGGER IF EXISTS audit_pay_eras ON budget.pay_eras")
    op.execute(
        "CREATE TRIGGER audit_pay_eras "
        "AFTER INSERT OR UPDATE OR DELETE ON budget.pay_eras "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )

    op.execute(_BACKFILL_ERAS_SQL)
    eraless = [
        row.user_id
        for row in bind.execute(sa.text(_OWNERS_WITH_PAYDAYS_AND_NO_ERA_SQL))
    ]
    if eraless:
        raise RuntimeError(
            f"After the backfill, user(s) {eraless} hold paydays and no era.  "
            f"The anchor guard above should have refused them before anything "
            f"was written; reaching this means the backfill's SELECT and the "
            f"guard's disagree about who holds a payday."
        )

    op.drop_constraint(
        "ck_pay_schedule_cadence_range", "pay_schedule",
        type_="check", schema="budget",
    )
    op.drop_constraint(
        "fk_pay_schedule_shift_id", "pay_schedule",
        type_="foreignkey", schema="budget",
    )
    op.drop_column("pay_schedule", "nominal_anchor", schema="budget")
    op.drop_column("pay_schedule", "shift_id", schema="budget")
    op.drop_column("pay_schedule", "cadence_days", schema="budget")


def downgrade():
    """Restore the rhythm columns from each owner's latest era, drop the eras."""
    bind = op.get_bind()
    eraless = [
        row.user_id for row in bind.execute(sa.text(_ROWS_WITHOUT_ERA_SQL))
    ]
    if eraless:
        raise RuntimeError(
            f"budget.pay_schedule row(s) for user(s) {eraless} hold no era, "
            f"and the schema this downgrade restores requires a cadence on "
            f"every row.  This migration holds no fact to write there and "
            f"refuses to invent one.  Delete those rows (they hold no paydays "
            f"either, by fk_pay_periods_schedule's own backfill) or record a "
            f"batch for those owners, then re-run."
        )

    op.add_column(
        "pay_schedule",
        sa.Column("cadence_days", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "pay_schedule",
        sa.Column("shift_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.add_column(
        "pay_schedule",
        sa.Column("nominal_anchor", sa.Date(), nullable=True),
        schema="budget",
    )
    op.execute(_RESTORE_RHYTHM_SQL)
    op.alter_column(
        "pay_schedule", "cadence_days", nullable=False, schema="budget",
    )
    op.alter_column(
        "pay_schedule", "shift_id", nullable=False, schema="budget",
    )
    op.create_check_constraint(
        "ck_pay_schedule_cadence_range", "pay_schedule",
        "cadence_days BETWEEN 1 AND 365", schema="budget",
    )
    op.create_foreign_key(
        "fk_pay_schedule_shift_id", "pay_schedule",
        "business_day_shifts", ["shift_id"], ["id"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )

    op.execute("DROP TRIGGER IF EXISTS audit_pay_eras ON budget.pay_eras")
    # The keys go BEFORE the table, and the order is a lock rather than
    # tidiness: dropping a table that still references ``ref.*`` and
    # ``auth.users`` takes an exclusive lock on each referenced table to
    # remove its referential triggers, which any idle transaction holding a
    # share lock on a ``ref`` table blocks.  Dropping the constraints first
    # takes the weaker lock the referenced tables admit beside a reader.
    for name in (_FK_KIND, _FK_SHIFT, _FK_SCHEDULE, "pay_eras_user_id_fkey"):
        op.drop_constraint(
            name, "pay_eras", type_="foreignkey", schema="budget",
        )
    op.drop_table("pay_eras", schema="budget")
    op.drop_table("pay_cadence_kinds", schema="ref")
