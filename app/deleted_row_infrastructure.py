"""Shared definitions for the rule that a deleted row holds no money.

**A payment or purchase is never under a deleted row** (plan step
``credit_card:CC-5-4a-4``).  Deleting a row takes its movements off the books
(ruling **R-CC75**) and an archive hides only rows holding nothing
(**R-CC63**), so a hidden row holds no money -- unless a door writes some under
it afterwards, or hides it while it still holds some.  The first happened,
measured by the step's third review: a recurring $120.00 Hotel marked Paid and
then deleted still answered a stale second tab's popover, and saving an Actual
of $125.00 wrote a dated $125.00 payment record under the hidden row.  Its pay
period then locked ("holds a recorded payment or purchase") and Reset was
refused for good, with no screen able to reach the row.

**The rule has two directions, so it takes two attachments**, the lesson
:mod:`app.level_infrastructure` and :mod:`app.opening_infrastructure` record:
the FACT lives on ``budget.transaction_entries`` (which row a movement is
under) and the STATE on ``budget.transactions`` (whether that row is deleted),
so either can move without the other.

* **A movement may not ARRIVE under a deleted row** (ruling **R-CC89**,
  developer 2026-09-23: *"The database refuses any payment or purchase written
  under a deleted row, so no door, now or later, can do it."*).  It arrives by
  ``INSERT``, or by an ``UPDATE`` that points it at another row.  An
  ``UPDATE`` that leaves ``transaction_id`` where it was is not an arrival, so
  it passes, and so do the ``SET NULL`` updates other tables' keys make to a
  movement's other columns.  Every deleted row counts, a transfer's leg
  included.
* **A row may not be HIDDEN while it holds one** (ruling **R-CC92**, which
  extends R-CC89, developer 2026-09-23, "Refuse both ways": *"The database
  also refuses hiding a row that still holds a payment or purchase, except a
  transfer's half (BAL-532's, until X-bi-6-4). The check runs when the change
  is saved, so a door that takes the money off and hides the row in the same
  save still works."*).  **A transfer's leg is excepted, and the exception is
  a FENCE with an owner**: the transfer's soft delete still hides a leg
  holding its kept payment -- finding **balance:BAL-532**, owned by plan step
  ``balance:X-bi-6-4`` -- and refusing it would turn that door into an error
  until then.  **Deleting the ``transfer_id IS NULL`` clause alone would not
  retire the fence**, because ``balance:X-bi-6-4`` re-parents a transfer's
  movements onto ``budget.transfers`` (``transaction_id`` nullable under an
  exactly-one-parent check): after it the legs hold nothing, a movement
  arriving under a deleted TRANSFER names no row the arrival arm reads, and a
  transfer's soft delete hides a ``budget.transfers`` row no attachment here
  watches -- so R-CC92's "until X-bi-6-4" would become permanent without
  anyone deciding it.  What X-bi-6-4 owes the rule is three changes together:
  the arrival arm also refuses a movement whose TRANSFER parent is deleted,
  reading that parent's ``is_deleted`` under the same :data:`ROW_WRITE_LOCK`
  on ``budget.transfers`` (a read without it reopens, for transfers, the race
  ruling R-CC96 closed below); the hiding arm gains an attachment on
  ``budget.transfers``, excepting nothing; and the ``transfer_id IS NULL``
  clause goes from this arm's ``WHEN`` and its function.  (The payment
  refusal's sentence names its row, and ``budget.transfers.name`` is
  nullable: unreachable while no transfer reaches that sentence.)
  ``transfer_id`` is watched as well as ``is_deleted``, so a raw ``UPDATE``
  re-pointing a hidden leg away from its transfer cannot walk out of the
  exception.

**This module is the rule's database half.**  The arrival arm's words are the
two writers' own refusals -- :func:`app.services.entry_service.create_entry`
for a purchase and
:func:`app.services.status_seam._refusals.reject_settlement_on_a_deleted_row`
for a payment record -- and the pages' half is the two ownership doors
answering "not found" for a deleted row (``get_accessible_transaction`` and
``routes/transactions/_helpers._get_owned_transaction``).  The hiding arm has
no words of its own because no door reaches it in either order of a race: the
row delete takes the movements off first and the archive leaves a holding row
alone, and each takes the row's lock (below) BEFORE it reads what the row
holds.  Both arms are what make the rule hold for a bulk statement, a psql
session and a writer nobody enumerated, the same pairing
``ck_transaction_entries_positive_amount`` has with ``entry_service``'s refusal
of a purchase worth nothing.

**The rule holds under concurrency because the arrival arm LOCKS the row**
(ruling **R-CC96**, developer 2026-09-23, "Lock in both": *"the database
check locks the row, so no deleted row can hold money from any writer, listed
or not"*).  Under ``READ COMMITTED`` each arm reads committed data only, and a
row's soft delete takes ``FOR NO KEY UPDATE`` while a movement ``INSERT``'s
foreign-key check takes ``FOR KEY SHARE`` -- two locks that do not conflict.
So an arm without a lock of its own read the row as live while its delete was
open, the delete's deferred check found no movement because the purchase had
not committed, and both committed: the step's fourth review measured a $12.34
purchase added by one session while another deleted its Groceries row, ending
``(hidden, held) = (True, 1)``, the state this module exists to forbid.  The
arm now reads ``is_deleted`` under :data:`ROW_WRITE_LOCK`, so the two
serialise in either order: a hide already open makes the arrival wait, and
refuses it once the hide commits; an arrival already open makes the hide wait
until the movement has committed, when the hiding arm sees it and refuses the
hide.

**Why ``FOR NO KEY UPDATE`` and not the weaker ``FOR SHARE``.**  Either
conflicts with a hide; the difference is what the same transaction does NEXT.
A writer that inserts a movement and then writes the row -- a record's
insert followed by its status ``UPDATE``, or a purchase followed by the
payback sync's lock -- would have to upgrade a ``FOR SHARE`` this arm took,
and two such writers each holding it then wait on each other.  Measured
2026-09-23 on two raw connections, ``FOR SHARE`` then ``FOR NO KEY UPDATE`` on
one row: ``DeadlockDetected`` for one of the two, where taking ``FOR NO KEY
UPDATE`` first let both commit.  The app's own doors never reach that shape:
they take the owner's write lock and then this same row lock BEFORE their
insert (:mod:`app.services.row_write_lock`, rulings R-CC96 and R-CC100), so
for them the arm's lock is their own and waits on nothing, and the second
click of a race meets a door's sentence instead of this arm's raw error.  So
the strength is graded by the raw measurement alone: with ``FOR SHARE`` here
the app's race module still passes (the step's fifth review).  It is the RAW
writer's guarantee.

**The arrival arm is an immediate ``BEFORE`` row trigger**, as
:mod:`app.level_infrastructure` chose and for a sharper reason here.  A
trigger's ``WHEN`` can read only the movement's own columns, never its row's
``is_deleted``, so a deferred trigger would queue an event on EVERY movement
written, and a queued event makes DDL on ``budget.transaction_entries`` illegal
until the queue is drained -- the hazard
:mod:`app.opening_infrastructure` records costing two CI failures.  Immediate is
also correct for the one ordering that matters: SQLAlchemy's unit of work
writes a row before its children, so an un-delete and a write in one flush
pass, and a delete and a write in one flush are refused.

**The hiding arm is a DEFERRED constraint trigger, checked at COMMIT**, because
the ruling says so -- *"The check runs when the change is saved, so a door
that takes the money off and hides the row in the same save still works."* --
and because the ORDER of the two writes inside a save is not the rule's to
grade.  ``transaction_service.delete_transaction`` stages the movements'
``DELETE`` through the one removal act and then the row's ``is_deleted``;
**measured 2026-09-23 with this arm made immediate, that door still passed**,
because a read between the two (``credit_workflow``'s payback lookup)
autoflushes the ``DELETE`` first -- while the same two statements written row
first were refused.  An immediate check would make a correct door depend on an
incidental autoflush.  Its ``WHEN`` reads the row's own columns, so an
event is queued only when a non-transfer row is hidden, never on an ordinary
write -- but **a transaction that hides such a row and then runs DDL on
``budget.transactions`` must drain first** (``SET CONSTRAINTS ALL
IMMEDIATE``), the pattern :mod:`app.opening_infrastructure` describes.

**Three callers must produce identical infrastructure**, the contract
:mod:`app.sighting_infrastructure` states: the Alembic revision that installs
it (``c4a4e7d1b9f2``, CC-5-4a-4's), ``scripts/init_database.py`` (fresh
database, no migration chain) and ``scripts/build_test_template.py``
(re-applied idempotently so the latest in-code definition wins).
``scripts/build_test_db_image.py`` counts :data:`DELETED_ROW_TRIGGERS` on the
baked image so a template missing an arm is rebuilt rather than trusted.

**Caller contract: both tables must already exist.**  ``CREATE TRIGGER`` needs
its table, and each function body names the other table.
"""

from __future__ import annotations

from typing import Callable

#: The arrival arm's function: refuse a movement arriving under a deleted row.
_ARRIVAL_FUNCTION = "budget.refuse_movement_under_deleted_row"

#: The hiding arm's function: refuse a non-transfer row hidden holding one.
_HIDING_FUNCTION = "budget.refuse_hiding_a_row_holding_money"

#: ``(trigger name, schema-qualified table)`` for each attachment, the arrival
#: arm first.  Exported so the image check can count them and a lift can name
#: them.
DELETED_ROW_TRIGGERS: tuple[tuple[str, str], ...] = (
    ("ck_movement_row_not_deleted", "budget.transaction_entries"),
    ("ck_hidden_row_holds_nothing", "budget.transactions"),
)

#: The row lock the arrival arm takes on the movement's row, whatever state the
#: row is in -- the strength every door that writes money under a row, or hides
#: one, takes FIRST (ruling **R-CC96**, :mod:`app.services.row_write_lock`).
#: The module docstring's two concurrency paragraphs say why this strength.
ROW_WRITE_LOCK = "FOR NO KEY UPDATE"

_CREATE_ARRIVAL_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_ARRIVAL_FUNCTION}()
RETURNS TRIGGER AS $$
DECLARE
    v_hidden BOOLEAN;
BEGIN
    -- Re-saving a movement where it already is brings nothing to its row.
    IF TG_OP = 'UPDATE' AND NEW.transaction_id = OLD.transaction_id THEN
        RETURN NEW;
    END IF;
    -- Locked whatever its state: a filter on is_deleted would lock nothing
    -- while the row is live, and a hide committing after this read would
    -- then leave the movement under a hidden row.
    SELECT is_deleted INTO v_hidden FROM budget.transactions
    WHERE id = NEW.transaction_id {ROW_WRITE_LOCK};
    IF v_hidden THEN
        RAISE EXCEPTION
            'transaction % was deleted: a payment or purchase cannot be '
            'recorded under it (rule {_ARRIVAL_FUNCTION}, ruling R-CC89)',
            NEW.transaction_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

#: Asked of the row as it stands at COMMIT, not as the event saw it: a row
#: hidden and then restored, or re-parented to a transfer, in the same
#: transaction holds nothing this rule forbids.
_CREATE_HIDING_FUNCTION_SQL = f"""
CREATE OR REPLACE FUNCTION {_HIDING_FUNCTION}()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM budget.transactions t
        WHERE t.id = NEW.id
          AND t.is_deleted
          AND t.transfer_id IS NULL
          AND EXISTS (
              SELECT 1 FROM budget.transaction_entries e
              WHERE e.transaction_id = t.id
          )
    ) THEN
        RAISE EXCEPTION
            'transaction % was deleted while it still holds a recorded '
            'payment or purchase: take them off the books first (rule '
            '{_HIDING_FUNCTION}, ruling R-CC92)',
            NEW.id;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


#: Each arm, as ``(trigger name, table, function, kind, firing clause)`` --
#: the firing clause is everything ``CREATE <kind>`` says between the trigger's
#: name and ``EXECUTE FUNCTION``.  One row per arm, so an arm's name, table,
#: function and firing rule cannot be edited apart.
_ARMS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        *DELETED_ROW_TRIGGERS[0], _ARRIVAL_FUNCTION, "TRIGGER",
        "BEFORE INSERT OR UPDATE OF transaction_id ON {table} FOR EACH ROW",
    ),
    (
        *DELETED_ROW_TRIGGERS[1], _HIDING_FUNCTION, "CONSTRAINT TRIGGER",
        "AFTER UPDATE OF is_deleted, transfer_id ON {table} "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.is_deleted AND NEW.transfer_id IS NULL)",
    ),
)


def _detach_sql(name: str, table: str) -> str:
    """Return the guarded ``DROP TRIGGER`` for one arm's attachment."""
    return f"DROP TRIGGER IF EXISTS {name} ON {table}"


def _attach_sql(arm: tuple[str, str, str, str, str]) -> str:
    """Return one :data:`_ARMS` row's ``CREATE TRIGGER`` (or ``CONSTRAINT TRIGGER``)."""
    name, table, function, kind, clause = arm
    return (
        f"CREATE {kind} {name} {clause.format(table=table)} "
        f"EXECUTE FUNCTION {function}()"
    )


def apply_deleted_row_infrastructure(executor: Callable[[str], object]) -> None:
    """Idempotently install both arms of the deleted-row rule.

    Each arm's function is created or replaced, then its attachment dropped
    and made again -- PostgreSQL has no ``CREATE TRIGGER IF NOT EXISTS`` -- so
    a second run leaves exactly what the first did.

    **Nothing to legalise first**: the arrival arm grades a movement as it
    arrives and the hiding arm a row as it is hidden, never a row already
    stored, so neither refuses what the database holds when it is first
    applied.  The revision that installs them refuses to run while a hidden
    row that is not a transfer's leg holds one (ruling **R-CC82**), which is
    what makes the hiding arm's rule true of the stored rows too; a hidden
    leg's is **BAL-532**'s.

    Args:
        executor: Single-argument callable that accepts a SQL string and runs
            it.  Pass ``op.execute`` from inside an Alembic migration; pass
            ``lambda s: session.execute(text(s))`` from inside a SQLAlchemy
            session.  Errors propagate -- the caller owns the outer
            transaction.
    """
    executor(_CREATE_ARRIVAL_FUNCTION_SQL)
    executor(_CREATE_HIDING_FUNCTION_SQL)
    for arm in _ARMS:
        executor(_detach_sql(*arm[:2]))
        executor(_attach_sql(arm))


def remove_deleted_row_infrastructure(executor: Callable[[str], object]) -> None:
    """Undo :func:`apply_deleted_row_infrastructure`: attachments first, then functions.

    A function a trigger still names cannot be dropped, hence the order.  Each
    statement is guarded with ``IF EXISTS``, so running this on a database
    that never had the rule, or twice, changes nothing.

    Args:
        executor: The callable :func:`apply_deleted_row_infrastructure` takes.
    """
    for arm in _ARMS:
        executor(_detach_sql(*arm[:2]))
    for _name, _table, function, _kind, _clause in _ARMS:
        executor(f"DROP FUNCTION IF EXISTS {function}()")
