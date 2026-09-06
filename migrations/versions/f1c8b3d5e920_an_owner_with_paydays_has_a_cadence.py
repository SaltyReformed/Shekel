"""An owner with paydays HAS a recorded cadence

Revision ID: f1c8b3d5e920
Revises: e2d7a94f61c3
Create Date: 2026-09-01 18:20:00.000000

Plan step ``pay_calendar:C4-b-2``.  Closes findings **P8** and **P35**.

**What was wrong.**  ``budget.pay_periods`` and ``budget.pay_schedule`` are
two halves of one fact -- the paydays an owner has, and the rhythm that
produced them -- and nothing in the schema held them together.  So the state
"paydays on file, no cadence row beside them" was representable, and
``pay_schedule_service.resolve_schedule`` had to answer for it.  Its answer
was to infer the cadence from the last period's stored length,
``(end_date - start_date) + 1``, and that inference is wrong in two ways at
once:

  * **It is CIRCULAR.**  Since plan step ``pay_calendar:C3-b``,
    ``pay_period_write.record_paydays`` derives the last period's end FROM the
    stored cadence -- ``start_date + (cadence_days - 1)`` -- so inverting it
    reads back the value that produced it.  A number that can be neither right
    nor wrong is not a measurement.
  * **It is unbounded ABOVE, where the column is not.**
    ``ck_pay_schedule_cadence_range`` bounds a STORED cadence to 1..365;
    ``ck_pay_periods_date_order`` bounds a period only to
    ``start_date < end_date``.  So a period spanning more than a year infers a
    cadence ``app.services.pay_calendar`` refuses, and since plan step
    ``pay_calendar:C2-c`` that refusal reaches every balance page --
    ``/grid``, ``/accounts/<id>``, the recurrence and savings surfaces -- as a
    bare 500, ``app/error_handlers.py`` having no arm for it.  That is finding
    **P35**, and four later steps widened its blast radius.

**What this does.**  Backfills any owner the state can still describe, then
adds ``fk_pay_periods_schedule``: ``budget.pay_periods.user_id`` REFERENCES
``budget.pay_schedule.user_id``.  The target is legal because
``uq_pay_schedule_user`` makes that column a superkey, so the key needs no new
column and stores no second pointer that could drift.  *It is NOT the
``fk_statement_matches_owner`` construction, which a first draft of this
docstring cited: that key is COMPOSITE and holds a denormalised COPY equal to
its source, where this is a single-column EXISTENCE key with no copy in it.
Two different constraint kinds for two different problems* (adversarial design
review, 2026-09-01).  With the
state unrepresentable, the inferring arm is DELETED in the same commit rather
than left standing over a database that can no longer produce its input.

**ON DELETE RESTRICT, and it is a ruling rather than an inheritance**
(``pay_calendar:R-PC41``, Josh, 2026-09-01).  The action governs exactly one
event: a ``budget.pay_schedule`` row deleted while its owner's pay periods
live.  Nothing in ``app/`` deletes such a row -- the three
``query(PaySchedule)`` sites are all SELECTs -- so the event has no live
source, and the only ways to reach it are a bug, a hand-run statement, or a
future door whose author has not thought about it.  Each wants a loud refusal.

*Of the four arms weighed, only two are distinguishable on this schema, and
that is measured rather than assumed.*  ``RESTRICT`` and ``NO ACTION`` refuse
identically here -- the check fires at the end of the ``DELETE`` statement, so
a parent removed and re-supplied in a later statement is refused either way --
and the real fork was CASCADE against an immediate refusal, with
``DEFERRABLE`` the third shape (it accepts the delete and checks at ``COMMIT``).
``RESTRICT`` is the better spelling of the refusal because it cannot be
deferred by a later ``SET CONSTRAINTS`` and its error names the setting.

*The plan proposed CASCADE and its stated reason was measured FALSE before the
ruling was taken.*  The reason given was that ``pay_periods.user_id`` and
``pay_schedule.user_id`` both cascade from ``auth.users``, so a user delete
needs an ordering CASCADE supplies.  Driving a real ``DELETE FROM auth.users``
against a clone of the developer's database, under all four candidate actions
and in BOTH referential-trigger orderings -- ``pay_periods_user_id_fkey`` was
dropped and recreated to flip which cascade fires last -- all eight
combinations SUCCEED.  RESTRICT passes that test exactly as CASCADE does, so
it was never an argument for either.  And the event is unreachable in
production regardless: ``transactions_account_id_fkey`` is itself
``ON DELETE RESTRICT``, so the ``auth.users -> budget.accounts`` cascade dies
first for any owner who has ever recorded a transaction.  Measured against the
head schema, not a stale one.

*What CASCADE would have cost, measured rather than argued.*  On that clone,
``DELETE FROM budget.pay_schedule WHERE user_id = 1`` under CASCADE left
``periods=0 txns=0 journal=0``: 63 pay periods, 1,057 transactions and every
journal entry, from one statement, silently.  The cadence is a SETTING and the
paydays are the RECORD; a record is not destroyed because a setting went away.

**The column keeps its ``auth.users`` key and that is not redundancy.**
``pay_periods_user_id_fkey`` carries ``ON DELETE CASCADE`` and is what lets a
user delete clear this table at all; without it, the new RESTRICT key would
REFUSE that delete, since the periods would still reference a schedule row the
user's own cascade was removing.  Two keys on one column with two different
actions are two facts, not one repeated -- the construction
``budget.statement_matches`` already carries.

**No index is added.**  A referencing-side index is what keeps the parent's
delete-time check cheap, and ``uq_pay_periods_user_start`` already leads with
``user_id``.  It survives plan step ``pay_calendar:C4-c``, which drops
``uq_pay_periods_user_index``.

**The backfill asks the PAYDAYS, not the derived column -- and that is a
deliberate departure from the arm it replaces.**  A first draft reused
``af8254074bef``'s expression verbatim, on the argument that reproducing the
deleted arm's own answer made the deletion behaviour-preserving.  An
adversarial design review refuted the argument on this revision's own words:
thirteen lines above, the docstring calls that expression a number which "can
be neither right nor wrong", and preserving a value you have just argued is
meaningless is not a safety property.  Worse, the arm recomputed it on every
request while the backfill freezes it into a column forever, in the same commit
that deletes the only code that would ever disagree with it.

So the backfill prefers ``last.start_date - previous.start_date`` and falls
back to ``(end_date - start_date) + 1`` only for an owner with exactly ONE
payday, where there is no second payday to measure against.  **Measured on a
constructed owner** (probe clone at head, 2026-09-01): paydays 2026-01-02,
2026-01-16 and 2026-01-30 -- fourteen days apart -- with the last row's stored
``end_date`` hand-edited to 2026-02-27, a 29-day span.  The old expression
writes **29**, so ``PayCadence.periods_per_year`` becomes
``round(365.2425 / 29) = 13`` against a true 26: every monthly equivalent on
``/savings``, ``/retirement`` and the Recurring surface wrong by 2x, silently
and permanently.  The new one writes **14**.  Ledger row **P28** is the class
that owner belongs to.

Ordering on ``start_date`` rather than ``period_index`` is the same choice one
level down, and ``app/services/pay_period_write._owner_periods`` already states
the rule: the payday is the fact and the ordinal is one of the two derived
columns ``C4-c`` drops, so reading in ordinal order sorts by the answer.

**What this costs is the "behaviour-preserving" claim, and only for an owner
whose two derivations disagree** -- which is an owner whose stored ``end_date``
already contradicts their own paydays, so no reading of them was ever right.
For every consistent owner the two arms return the same integer.  The bound
``last.start - previous.start >= 2`` holds structurally rather than by luck:
``ck_pay_periods_date_order`` requires ``start_date < end_date``, so two
paydays one day apart cannot both be stored.

**Zero rows on both databases, re-measured 2026-09-01** on the developer's dev
database and on production: one owner with paydays, one schedule row, zero
periods whose owner lacks one.  The backfill is therefore a no-op here and
exists for any database this chain has not seen.

**If an owner's inferred cadence falls outside 1..365 the migration ABORTS**,
on ``ck_pay_schedule_cadence_range``, and that is the correct outcome rather
than a gap.  Such an owner's calendar already raises on every balance page
today (finding **P35**); storing a clamped or invented number would make a
broken schedule look repaired.  A loud abort names the row for a human.

**The downgrade is value-lossless but is NOT a byte-exact inverse**, and says
so rather than claiming reversibility it does not have.  It drops the
constraint; it does NOT delete backfilled ``budget.pay_schedule`` rows.  Which
rows were inserted here is not recorded, and each one holds the cadence the
application was already answering for that owner, so removing them would
destroy a correct value to restore an absence.  Re-running ``upgrade`` after a
``downgrade`` inserts nothing new (``ON CONFLICT DO NOTHING``) and re-adds the
key, which is the fixed point that matters.

**Chain order is what keeps ``af8254074bef``'s downgrade working.**  That
revision's ``downgrade()`` drops ``budget.pay_schedule`` outright while
``budget.pay_periods`` survives -- which both produces the state this key
forbids and meets a dependent constraint.  It stays correct because Alembic
runs downgrades newest-first: this revision's ``downgrade()`` removes the key
84 steps before that one runs.  Tested rather than argued
(``tests/test_models/test_c4b2_pay_period_schedule_key.py``).

**Locking.**  ``ADD CONSTRAINT ... FOREIGN KEY`` takes SHARE ROW EXCLUSIVE on
both tables and scans ``budget.pay_periods`` once to validate.  Production
holds 63 rows, so it is instantaneous; a ``NOT VALID`` + ``VALIDATE`` split
would be complexity bought for a table that does not need it.

Review: Josh, 2026-09-01 -- APPROVED: ``ON DELETE RESTRICT`` on
``fk_pay_periods_schedule``, taken against CASCADE, NO ACTION and NO ACTION
DEFERRABLE with the measurements above in front of him (ruling
``pay_calendar:R-PC41``).  The revision adds one constraint and inserts
backfill rows; the only constraint its ``downgrade`` removes is the one this
``upgrade`` added.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f1c8b3d5e920'
down_revision = 'e2d7a94f61c3'
branch_labels = None
depends_on = None


#: One ``budget.pay_schedule`` row per owner who holds paydays without one.
#:
#: **It asks the PAYDAYS first and the stored span only as a last resort**, and
#: which one answers is decided per owner by whether a second payday exists:
#:
#:   * two or more paydays -> ``last.start_date - previous.start_date``.  The
#:     recorded paydays ARE the rhythm, and this reads them.
#:   * exactly one payday -> ``(end_date - start_date) + 1``.  There is no
#:     second payday to measure against, and this is the only evidence there
#:     is.  It is also reliable in exactly that case: a one-payday owner is the
#:     registration bootstrap, whose end was written as
#:     ``start + (cadence - 1)`` by the writer.
#:
#: ``DISTINCT ON (user_id) ... ORDER BY user_id, start_date DESC`` picks each
#: owner's LATEST payday; the ``lag`` window, computed before ``DISTINCT ON``,
#: carries the one before it.  Ordering on ``start_date`` rather than on
#: ``period_index`` is this arc's own rule -- the payday is the fact and the
#: ordinal is a derived column ``C4-c`` drops, so ordering by the ordinal would
#: sort by the answer.  It also gives the right row for an owner whose stored
#: ordinal is scrambled, which is a state
#: ``app/services/generation_schedule.py`` records as refused by nothing.
#:
#: PostgreSQL date subtraction yields an integer day count, so both arms are
#: plain integers.  The rolling columns are omitted so their server defaults
#: apply -- the default horizon lives in the column DDL and nowhere else.
#: ``ON CONFLICT (user_id) DO NOTHING`` leaves every owner who already has a
#: row untouched and makes a re-run inert.
_BACKFILL_CADENCE_SQL = (
    "INSERT INTO budget.pay_schedule (user_id, cadence_days) "
    "SELECT p.user_id, "
    "       COALESCE(p.start_date - p.prev_start, "
    "                (p.end_date - p.start_date) + 1) "
    "  FROM ( "
    "    SELECT DISTINCT ON (user_id) "
    "           user_id, start_date, end_date, "
    "           lag(start_date) OVER ( "
    "               PARTITION BY user_id ORDER BY start_date "
    "           ) AS prev_start "
    "      FROM budget.pay_periods "
    "     ORDER BY user_id, start_date DESC "
    "  ) p "
    "ON CONFLICT (user_id) DO NOTHING"
)


def upgrade():
    """Backfill every schedule-less owner, then make the state unstorable."""
    # Order is forced: the constraint validates the whole table on creation, so
    # an owner still missing a row would abort the ALTER rather than be
    # repaired by it.
    op.execute(_BACKFILL_CADENCE_SQL)
    op.create_foreign_key(
        constraint_name='fk_pay_periods_schedule',
        source_table='pay_periods',
        referent_table='pay_schedule',
        local_cols=['user_id'],
        remote_cols=['user_id'],
        source_schema='budget',
        referent_schema='budget',
        ondelete='RESTRICT',
    )


def downgrade():
    """Drop the key, and deliberately keep the rows the upgrade backfilled.

    Value-lossless, not byte-lossless, and the module docstring states which.
    The inserted rows are not identified anywhere, and each holds the cadence
    the application was already answering for that owner, so deleting them
    would destroy a correct value in order to restore an absence.  The next
    ``upgrade`` inserts nothing (``ON CONFLICT DO NOTHING``) and re-adds the
    key, which is the round trip that has to hold.
    """
    op.drop_constraint(
        'fk_pay_periods_schedule',
        'pay_periods',
        schema='budget',
        type_='foreignkey',
    )
