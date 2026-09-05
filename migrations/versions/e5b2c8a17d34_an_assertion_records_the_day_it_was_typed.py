"""an assertion records the day it was typed

Finding **N-299**, developer ruling 2026-08-25 (option C of four).  The
balance-history card captions a row as back-dated when the day the balance was
TRUE differs from the day it was ENTERED, and those two days were read from two
different clocks.

``observed_on`` is the APPLICATION's civil day: it defaults to
``app.utils.dates.display_today()``, which ``time_machine`` fakes.  The entered
day was DERIVED from ``created_at``, which is ``server_default=db.func.now()``
and therefore stamped by POSTGRES.

**A test process CAN fake that, and an earlier draft of this file said it could
not.**  ``tests/_test_helpers._freeze_db_clock`` exists for exactly this class
(finding N-65) and ``tests/test_services/test_frozen_db_clock.py`` pins it on
this very column.  What is true is narrower: the weekly calendar SWEEP
deliberately declines to freeze the database clock, because ``tick=True`` is
what keeps ``created_at`` a real recording ORDER and ruling R-DH needs that
order to pick the governing balance.  The sweep's own docstring
(``tests/conftest.py``) calls the resulting mismatch "an artifact of this
instrument, not a defect in the test", and for a fixture-vs-server DATE
comparison that is right.

It is not right here, and that is the whole of this migration: the two days
being compared are not a fixture's and a server's, they are the two clocks the
CARD renders side by side.  Measured red on all five matrix dates since
2026-08-10, reproduced at ``SHEKEL_FAKE_TODAY=2026-12-31`` where the card said
*"entered Aug 24, 2026"* under a row observed 2026-12-31.

It is not only a test artifact.  A true-up submitted in the last second of a
civil day reads ``display_today()`` on one side and PostgreSQL's ``now()`` a
fraction of a second later on the other, so an ordinary same-day entry can be
captioned back-dated in production.

**``created_at`` is NOT replaced and keeps its one job**: ordering two
assertions that share an ``observed_on``, so the last one recorded is that
day's closing balance.  A DAY has no resolution to rank a bookkeeping session
with -- every assertion typed in one sitting shares one recording day -- which
is the split ``CashAnchorRow`` already stated between what a reader RANKS by
and what it SHOWS.  This column is the second half of that split: the shown
value gets the clock it is compared against.

**The backfill is the derivation it replaces, verbatim** --
``(created_at AT TIME ZONE 'America/New_York')::date``, the same expression
``observed_on``'s own backfill used and the same one
``balance_at._cash_flow.CashAnchorRow.recorded_on`` computed at read time.  So
no rendered caption moves on the day this lands: every existing row keeps the
entered day the card already showed it.

The zone is spelled as a literal rather than read from
``app.utils.dates.DISPLAY_TIMEZONE``, deliberately: a migration is a historical
record of what was done to the data, and it must keep meaning the same thing if
that constant is ever changed.

**The downgrade is value-lossless for every row this migration backfilled** and
lossy only for rows written after it: dropping the column returns those to the
derivation, which is wrong for exactly the rows whose two clocks disagreed --
the state the column exists to fix.  Stated rather than glossed.

**A CHECK ``recorded_on >= observed_on`` was built and WITHDRAWN**, and the
measurement is why.  Production maintains that invariant by construction and it
would have caught the ``_restamp_assertion`` defect at fixture-write time.  But
it collides with a PERVASIVE fixture idiom rather than a stray test: the cash
walk suites freeze today, stamp every assertion at that one instant, and set
``observed_on`` across a projection horizon -- 10 distinct row shapes over 7
modules assert a balance for a day AFTER the instant they were stamped at.
Those fixtures are already unfaithful to production (the write door refuses a
future ``observed_on``); the CHECK merely exposes it.  Migrating that idiom is
its own step and it is filed rather than folded in here.

Review: Josh, 2026-08-25 -- ruled option C over marking the tests
(``@pytest.mark.server_clock``, which grades a date-sensitive caption at no
calendar position at all), over freezing the database clock (which breaks the
recording order the same record depends on), and over making the shared
``CreatedAtMixin`` application-supplied (whose blast radius is every
append-only table).

Revision ID: e5b2c8a17d34
Revises: d1a4f7c9e620
Create Date: 2026-08-25 12:45:00.000000
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'e5b2c8a17d34'
down_revision = 'd1a4f7c9e620'
branch_labels = None
depends_on = None

#: The derivation this column replaces, verbatim -- see the module docstring.
_BACKFILL = sa.text("""
    UPDATE budget.account_anchor_history
    SET recorded_on = (created_at AT TIME ZONE 'America/New_York')::date
    WHERE recorded_on IS NULL
""")

#: Rows the backfill did not reach.  ``created_at`` is NOT NULL with a NOW()
#: server default, so this is empty by construction -- verified rather than
#: assumed, because a surviving NULL fails the ALTER below with a bare
#: constraint error naming no row and the operator needs the diagnostic.
_REMAINING_NULLS = sa.text("""
    SELECT count(*) FROM budget.account_anchor_history
    WHERE recorded_on IS NULL
""")

#: Rows whose entered day the derivation would NOT reproduce -- the ones a
#: downgrade destroys rather than merely un-stores.  Empty immediately after
#: the upgrade (the backfill IS the derivation); non-empty once any row has
#: been written by the app, if its two clocks ever disagreed.
_APP_DATED = sa.text("""
    SELECT id, account_id, recorded_on,
           (created_at AT TIME ZONE 'America/New_York')::date AS derived
    FROM budget.account_anchor_history
    WHERE recorded_on
          IS DISTINCT FROM (created_at AT TIME ZONE 'America/New_York')::date
    ORDER BY id
""")


def upgrade():
    """Add ``recorded_on``, backfill it, bind it, and enforce the day order."""
    op.add_column(
        'account_anchor_history',
        sa.Column('recorded_on', sa.Date(), nullable=True),
        schema='budget',
    )

    connection = op.get_bind()
    connection.execute(_BACKFILL)

    remaining = connection.execute(_REMAINING_NULLS).scalar()
    if remaining:
        raise RuntimeError(
            f"Cannot set budget.account_anchor_history.recorded_on NOT NULL: "
            f"{remaining} row(s) are still NULL after the backfill.  Inspect "
            f"them with: SELECT id, account_id, created_at, observed_on FROM "
            f"budget.account_anchor_history WHERE recorded_on IS NULL;"
        )

    op.alter_column(
        'account_anchor_history', 'recorded_on',
        existing_type=sa.Date(), nullable=False,
        schema='budget',
    )


def downgrade():
    """Drop the CHECK and the column, refusing where the day is not derivable.

    Refuses rather than destroying what it cannot rebuild, which is the
    standard its sibling ``c4a19e7b2d80`` set on this same table.
    """
    connection = op.get_bind()

    app_dated = connection.execute(_APP_DATED).fetchall()
    if app_dated:
        listed = "; ".join(
            f"id={row[0]} account={row[1]} recorded_on={row[2]} "
            f"(created_at derives {row[3]})"
            for row in app_dated
        )
        raise RuntimeError(
            f"Cannot drop budget.account_anchor_history.recorded_on: "
            f"{len(app_dated)} row(s) carry an entered day that ``created_at`` "
            f"does not derive -- {listed}.  Those are exactly the rows this "
            "column exists for: the application's civil day and PostgreSQL's "
            "instant disagreed, and dropping the column silently reverts each "
            "to the database's day.  To downgrade anyway, first reconcile each "
            "row by hand (UPDATE ... SET recorded_on = (created_at AT TIME "
            "ZONE 'America/New_York')::date), accepting that its card caption "
            "reverts to the day PostgreSQL recorded it."
        )

    op.drop_column(
        'account_anchor_history', 'recorded_on', schema='budget',
    )
