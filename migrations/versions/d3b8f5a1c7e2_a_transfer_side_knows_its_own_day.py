"""a transfer side knows its own day

Plan step **balance:X-bi-6-4c-3** of ``docs/audits/balance_architecture/README.md``
(rulings **R-BAL142**, **R-BAL143**, **R-BAL165**).  A transfer has two sides, and
since this step each keeps its own settle day with a basis saying how that day is
known.  A side with no evidence of its own BORROWS the other side's day, which is
the new ``borrowed`` basis.  Until now one day was written to both sides with one
basis, so two kinds of label are false on stored data, and this migration
relabels them.  **No day, no figure and no clearing link moves**; the basis is
metadata ABOUT a day, and every balance, fold and posting reads the day itself.

  1. **ref.settled_day_bases** gains ``borrowed``, inline-seeded so
     ``ref_cache.init()`` resolves ``SettledDayBasisEnum.BORROWED`` straight after
     a bare ``flask db upgrade`` (the dual seed ``c7d31f9a45e8`` states);
     ``ON CONFLICT (name) DO NOTHING`` keeps it idempotent against the
     entrypoint's reseed from ``app/ref_seeds.py``.
  2. **The relabel**, on the transfer side's SHADOW (``budget.transactions``) and
     its covering movement (``budget.transaction_entries``) together, over every
     transfer side carrying a day, live or soft-deleted (a restore re-exposes
     one).  Each arm is a PREDICATE, graded on the tree it runs on, never a list
     of ids:

     * ``observed`` is kept iff a statement match member named the side --
       the shadow itself (the row-shaped era) or ANY movement ever under it
       (``transaction_entries`` audit INSERTs by ``transaction_id``, so a
       ``$0.00`` round trip that re-created the movement keeps its evidence) --
       now or ever (member audit INSERTs).  Otherwise the day was copied from
       the other side by the matcher, which stated it for both, and the side
       BORROWS.
     * ``asserted`` is kept iff the shadow's or its movement's clearing link is
       set (the reconcile tick links its own leg only); otherwise it borrows.
     * ``entered`` is classified by the LATEST audit row of that shadow with
       ``settled_on`` among its ``changed_fields``: an UPDATE that moved the
       status INTO the settled band and wrote a day equal to its own
       America/New_York date is a Paid STAMP; one that wrote a day onto an
       already-settled dateless row BY THE MIGRATING ROLE is a backfill GUESS.
       Both borrow.  Anything else is a TYPED day and stays ``entered`` --
       including a day typed into the old one box before this step (ruling
       **R-BAL165**: typed on both ends), an app-role write onto a dateless
       settled row (the popover's documented typed repair), and a side whose day
       no audit row wrote (the log starts 2026-05-06 and keeps 365 days).  The
       direction is ruling R-BAL143's H3: a typed day relabelled ``borrowed``
       would be moved by the next statement, so the doubt lands on "typed".

**A relabel the day function would never produce is REFUSED** (the coordinator's
ruling, 2026-09-30, fail-closed).  A borrowed side's day IS its lender's
(``transfer_service._side_days.borrowed_day``), and this SQL cannot call that
function, so before any write it counts every side it would relabel that either
sits out of the settled band (a day the seam never leaves there) or differs from
a sibling that holds a day in the band.  Every settled pair any door writes is on
ONE day (the design's M1: 20 of 20 on the 2026-09-30 production dump), so the
count is 0 there; a non-zero count names the transfers and stops, so the release
rehearsal on a same-day dump surfaces it before production does.

**Measured on the 2026-09-30 00:11 production dump** (migrated to
``c4a4e7d1b9f2``): 20 settled live transfers, 40 sides.  26 relabel to
``borrowed`` -- 14 far sides ``observed`` with no member ever (their accounts had
no statement line ever) and 12 ``entered`` sides (4 pairs backfill GUESSES, one
of them a day backfilled onto a Paid pressed before the column existed, and 2
pairs Paid STAMPS) -- and the 14 Checking sides stay ``observed``.  0 refused.  The migration prints its own counts so the
operator compares them with the rehearsal's.

**The downgrade is the exact inverse for every relabelled side**: a ``borrowed``
side takes its sibling's basis when the sibling's is evidence, else ``entered``
-- the pre-step pair shape the upgrade read -- and each covering movement follows
its shadow; then the ref row goes.  What it cannot restore is a distinction the
older code had no reader for, and two sides holding two DIFFERENT evidenced days
(which only post-step code writes) stay as they are: the older code reads the
income side's day for the pair, as it always did.

**Not audited catalogue.**  ``ref.settled_day_bases`` stays outside
``AUDITED_TABLES``; the relabel UPDATEs are recorded by the existing triggers on
both budget tables.

Rulings: R-BAL143 (the relabel, by predicate), R-BAL165 (a pre-step typed day
stays typed on both sides); the refusal arm: the coordinator, 2026-09-30.

Revision ID: d3b8f5a1c7e2
Revises: c4a4e7d1b9f2
Create Date: 2026-09-30
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d3b8f5a1c7e2"
down_revision = "c4a4e7d1b9f2"
branch_labels = None
depends_on = None


# Literal SQL, single-quoted, for the cross-migration inline-seed guard
# (``tests/test_models/test_posting_ref_seed_parity.py``), which scans the chain
# for each enum value inside an ``INSERT INTO`` its own ref table.
_SEED_BORROWED_SQL = (
    "INSERT INTO ref.settled_day_bases (name) VALUES ('borrowed') "
    "ON CONFLICT (name) DO NOTHING"
)

_SETTLED = "SELECT id FROM ref.statuses WHERE is_settled"


def _basis(name: str) -> str:
    """Return a scalar subquery for one ``ref.settled_day_bases`` id.

    A migration resolves a ref row by NAME because its id differs between
    databases (``c7d31f9a45e8``'s helper, restated: a migration may not import
    the application).
    """
    return f"(SELECT id FROM ref.settled_day_bases WHERE name = '{name}')"


# Every transfer side carrying a day, with the arm the relabel files it under.
# ``*_relabel`` and ``entered_stamp`` / ``entered_guess`` become ``borrowed``;
# every other arm is kept and counted.
_SIDE_ARMS_SQL = f"""
WITH sides AS (
    SELECT t.id AS shadow_id, t.transfer_id, t.status_id, t.settled_on,
           b.name AS basis, t.reconciled_by_id AS link
    FROM budget.transactions t
    JOIN ref.settled_day_bases b ON b.id = t.settled_day_basis_id
    WHERE t.transfer_id IS NOT NULL AND t.settled_on IS NOT NULL
), movements_ever AS (
    SELECT e.transaction_id AS shadow_id, e.id AS entry_id
    FROM budget.transaction_entries e
    UNION
    SELECT (al.new_data->>'transaction_id')::int, al.row_id
    FROM system.audit_log al
    WHERE al.table_schema = 'budget' AND al.table_name = 'transaction_entries'
      AND al.operation = 'INSERT' AND al.new_data ? 'transaction_id'
), members_ever AS (
    SELECT m.transaction_entry_id AS entry_id, NULL::int AS shadow_id
    FROM budget.statement_match_members m
    UNION ALL
    SELECT (al.new_data->>'transaction_entry_id')::int,
           (al.new_data->>'transaction_id')::int
    FROM system.audit_log al
    WHERE al.table_schema = 'budget'
      AND al.table_name = 'statement_match_members'
      AND al.operation = 'INSERT'
), bank_named AS (
    SELECT me.shadow_id FROM members_ever me WHERE me.shadow_id IS NOT NULL
    UNION
    SELECT mv.shadow_id FROM movements_ever mv
    JOIN members_ever me ON me.entry_id = mv.entry_id
), linked AS (
    SELECT s.shadow_id FROM sides s WHERE s.link IS NOT NULL
    UNION
    SELECT e.transaction_id FROM budget.transaction_entries e
    WHERE e.covers_settlement AND e.reconciled_by_id IS NOT NULL
), day_writes AS (
    SELECT DISTINCT ON (al.row_id)
           al.row_id AS shadow_id, al.old_data, al.new_data, al.db_user,
           al.executed_at
    FROM system.audit_log al
    WHERE al.table_schema = 'budget' AND al.table_name = 'transactions'
      AND al.row_id IN (SELECT shadow_id FROM sides WHERE basis = 'entered')
      AND 'settled_on' = ANY(al.changed_fields)
    ORDER BY al.row_id, al.id DESC
), entered_arms AS (
    SELECT s.shadow_id,
        CASE
            WHEN w.shadow_id IS NULL THEN 'entered_unwritten'
            WHEN (w.old_data->>'status_id')::int NOT IN ({_SETTLED})
             AND (w.new_data->>'status_id')::int IN ({_SETTLED})
             AND (w.new_data->>'settled_on')::date
                 = (w.executed_at AT TIME ZONE 'America/New_York')::date
                THEN 'entered_stamp'
            WHEN w.old_data->>'settled_on' IS NULL
             AND (w.old_data->>'status_id')::int IN ({_SETTLED})
             AND w.db_user = current_user
                THEN 'entered_guess'
            WHEN w.old_data->>'settled_on' IS NULL
             AND (w.old_data->>'status_id')::int IN ({_SETTLED})
                THEN 'entered_app_repair'
            ELSE 'entered_typed'
        END AS arm
    FROM sides s
    LEFT JOIN day_writes w ON w.shadow_id = s.shadow_id
    WHERE s.basis = 'entered'
)
SELECT s.shadow_id, s.transfer_id, s.status_id, s.settled_on,
    CASE s.basis
        WHEN 'observed' THEN CASE
            WHEN s.shadow_id IN (SELECT shadow_id FROM bank_named)
                THEN 'observed_kept' ELSE 'observed_relabel' END
        WHEN 'asserted' THEN CASE
            WHEN s.shadow_id IN (SELECT shadow_id FROM linked)
                THEN 'asserted_kept' ELSE 'asserted_relabel' END
        WHEN 'entered' THEN (
            SELECT ea.arm FROM entered_arms ea
            WHERE ea.shadow_id = s.shadow_id)
        ELSE s.basis || '_kept'
    END AS arm
FROM sides s
ORDER BY s.transfer_id, s.shadow_id
"""

_RELABELLED_ARMS = frozenset({
    "observed_relabel", "asserted_relabel", "entered_stamp", "entered_guess",
})


def classify_sides(bind) -> "list[tuple[int, int, str]]":
    """Return ``(shadow id, transfer id, arm)`` for every transfer side with a day.

    Module-level so a test can DRIVE the predicate against constructed rows
    (``c7d31f9a45e8``'s rule: a guard nothing exercises is a guard nobody has
    seen work).

    Args:
        bind: A SQLAlchemy connection.

    Returns:
        One tuple per side, in transfer order.
    """
    return [
        (row.shadow_id, row.transfer_id, row.arm)
        for row in bind.execute(sa.text(_SIDE_ARMS_SQL))
    ]


def refuse_unborrowable(bind, relabel: "list[int]") -> None:
    """Refuse a relabel that leaves a borrowed day its function would not give.

    A side about to read ``borrowed`` must sit in the settled band, and its day
    must equal any sibling's day held in the band -- the other side's day when
    that side keeps its evidence, and the one day two borrowing sides share.
    Asked BEFORE any write.

    Args:
        bind: A SQLAlchemy connection.
        relabel: The shadow ids about to be relabelled.

    Raises:
        RuntimeError: Naming the count and the first transfers, when any side
            fails.  Nothing has been written.
    """
    if not relabel:
        return
    rows = bind.execute(sa.text(f"""
        SELECT DISTINCT s.transfer_id
        FROM budget.transactions s
        WHERE s.id = ANY(:ids)
          AND (s.status_id NOT IN ({_SETTLED})
               OR EXISTS (
                   SELECT 1 FROM budget.transactions t
                   WHERE t.transfer_id = s.transfer_id AND t.id <> s.id
                     AND t.status_id IN ({_SETTLED})
                     AND t.settled_on IS NOT NULL
                     AND t.settled_on <> s.settled_on))
        ORDER BY s.transfer_id
    """), {"ids": relabel}).scalars().all()
    if rows:
        raise RuntimeError(
            f"X-bi-6-4c-3 refuses to relabel: {len(rows)} transfer(s) have a "
            "side this migration would mark 'borrowed' whose day is not its "
            "other side's (or which carries a day outside the settled band), "
            "a state no settle door writes and the day function would never "
            f"produce. First transfer ids: {', '.join(map(str, rows[:20]))}. "
            "Repair each transfer's days through its popover (or revert and "
            "re-settle it) on the pre-step release, then re-run. Diagnostic: "
            "SELECT id, transfer_id, status_id, settled_on, "
            "settled_day_basis_id FROM budget.transactions WHERE transfer_id "
            "IN (<ids>) ORDER BY transfer_id, id;"
        )


def relabel_borrowed(bind, relabel: "list[int]") -> None:
    """Relabel each listed shadow and its dated covering movement ``borrowed``.

    Args:
        bind: A SQLAlchemy connection.
        relabel: The shadow ids to relabel.
    """
    if not relabel:
        return
    bind.execute(sa.text(
        "UPDATE budget.transactions "
        f"SET settled_day_basis_id = {_basis('borrowed')} "
        "WHERE id = ANY(:ids)"
    ), {"ids": relabel})
    bind.execute(sa.text(
        "UPDATE budget.transaction_entries "
        f"SET settled_day_basis_id = {_basis('borrowed')} "
        "WHERE transaction_id = ANY(:ids) AND covers_settlement "
        "AND settled_on IS NOT NULL"
    ), {"ids": relabel})


def upgrade():
    """Seed ``borrowed``, then relabel every side whose label it did not earn."""
    op.execute(_SEED_BORROWED_SQL)
    bind = op.get_bind()
    sides = classify_sides(bind)
    relabel = [shadow for shadow, _, arm in sides if arm in _RELABELLED_ARMS]
    refuse_unborrowable(bind, relabel)
    relabel_borrowed(bind, relabel)
    counts: dict[str, int] = {}
    for _, _, arm in sides:
        counts[arm] = counts.get(arm, 0) + 1
    # The counts are the migration's own measurement, printed so the operator
    # can compare them with the release rehearsal's.
    print(
        f"X-bi-6-4c-3: {len(sides)} dated transfer side(s); relabelled "
        f"{len(relabel)} to 'borrowed' ("
        + ", ".join(f"{arm} {counts[arm]}" for arm in sorted(counts))
        + ")."
    )


def downgrade():
    """Give each borrowed side its sibling's evidence basis, else ``entered``."""
    bind = op.get_bind()
    bind.execute(sa.text(f"""
        UPDATE budget.transactions s
        SET settled_day_basis_id = COALESCE(
            (SELECT t.settled_day_basis_id FROM budget.transactions t
             WHERE t.transfer_id = s.transfer_id AND t.id <> s.id
               AND t.settled_day_basis_id IS NOT NULL
               AND t.settled_day_basis_id <> {_basis('borrowed')}
             ORDER BY t.id LIMIT 1),
            {_basis('entered')})
        WHERE s.settled_day_basis_id = {_basis('borrowed')}
    """))
    bind.execute(sa.text(f"""
        UPDATE budget.transaction_entries e
        SET settled_day_basis_id = t.settled_day_basis_id
        FROM budget.transactions t
        WHERE e.transaction_id = t.id AND e.covers_settlement
          AND e.settled_day_basis_id = {_basis('borrowed')}
    """))
    op.execute("DELETE FROM ref.settled_day_bases WHERE name = 'borrowed'")
