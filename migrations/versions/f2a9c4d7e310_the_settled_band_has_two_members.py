"""the settled band has two members, not three

Plan step **X-am** of ``docs/audits/balance_architecture/README.md`` section 5,
closing finding **N-177**.  Deletes the ``Settled`` status -- the ARCHIVE --
from ``ref.statuses``.

Review: Josh, 2026-08-27 -- APPROVED, having been given the option space:
delete it and RULE why (**balance:R-HA**), against keeping it terminal with its
holes closed, or keeping the name and dropping its terminality.

**What the status was.**  ``Settled`` was reachable from exactly one control --
the Status ``<select>`` in the grid's full-edit popover and its transfer twin --
and from nowhere else: the application has one ``status_id`` writer
(``status_seam._seam.apply_status_change``) and no service ever computed this
id for it.  Picking it moved a Paid or Received row into a state the state
machine gave no outgoing edge but identity, so the row could never be reverted,
re-priced, matched against a bank statement, or have a purchase added to or
removed from it -- while the delete control on the same card still removed it
and reversed its postings.  Its only content beyond ``Paid`` was *and you may
never revert*: ``is_immutable`` is already true for Paid and Received, so
``state_machine.finalised_edit_rejection`` already locked the money, period,
category and due-date fields on both.

**Nothing has ever carried it, and the evidence that carries that is the AUDIT
TRAIL rather than the dumps.**  Stated in the order it actually constrains,
measured 2026-08-27:

  * **The trigger-backed trail is the load-bearing part.**  ``audit_transactions``
    and ``audit_transfers`` fire AFTER INSERT, UPDATE and DELETE, and
    ``system.audit_log`` holds 1,591 rows for those two tables since
    2026-05-07 -- 208 of them status changes and 229 of them DELETEs.  **Not
    one names status 6, in ``old_data`` or ``new_data``.**  That is what closes
    the hole the snapshots cannot: a row that had been archived and then hard
    deleted between two dumps would have left a DELETE row naming it.
  * **The snapshots agree and are ONE population, not many.**  18 ``pg_dump``
    archives read zero, but they span 2026-08-05 to 2026-08-27 and are
    successive pre-deploy captures of the same database (997-1012 transactions
    each), so they are one heavily autocorrelated observation with 18 witnesses.
    Two older artifacts reach further back and read zero too: the 2026-05-13
    pre-pg18 plain dump and the 2026-05-01 SQL dump.
  * **The live databases are also one population.**  Production reads zero;
    dev reads zero and is a CLONE of production, so counting it twice would be
    counting the same rows twice.
  * **Soft-deleted rows are included** -- ``status_id = 6`` is zero on
    ``budget.transactions`` without an ``is_deleted`` filter.

  What is NOT covered: 2026-05-01 to 2026-05-07, before the audit triggers
  existed (revision ``a5be2a99ea14`` installed them; the live database carried
  none until then).  The 2026-05-01 dump is the only observation inside that
  window and it reads zero.

  The same probe reads 152 / 18 for ``Paid`` on the newest dump, matching the
  live database exactly, so it fires.

**The re-point below is not a formality, because the door stays open until this
ships.**  An owner may archive a row between that measurement and this
migration running, so the two UPDATEs are a real backfill and not a no-op
written for symmetry.  A row that took the archive stays SETTLED here: it keeps
``settled_on``, ``settled_amount``, ``settled_basis_id`` and every posting, and
lands on the settled status its own TYPE takes -- Received for income, Paid
otherwise, which is ``transaction_service.settled_status_member``'s rule -- so
the only fact it loses is the archive flag, and what it gains back is the
ability to be corrected.

**REFUSING instead was considered and is wrong.**  A refusal would name a
repair that does not exist: the app has no unarchive door, by construction, so
an owner meeting it could not clear the blockage from any screen.  That is
finding **N-302**'s shape -- and ``entry_service._refusals.removal_refusal``
already records it against this exact status.

**THE ONE SHAPE WHERE THE NARROWING IS NOT INERT, written down because it is
not obvious.**  Most readers consume the settled band as a MEMBERSHIP test and
see no change: the removed id had no rows.  A few consume it as a COMPLEMENT --
``posting_reads`` filters ``notin_(settled | balance-excluded)`` and
``status_seam._seam`` clears the settle day on ``new_status_id not in
settled_status_ids()``.  For those, a surviving ``Settled`` row would flip from
*settled* to *not settled* the moment the new CODE runs, and a save on such a
row would clear its settle day and null ``reconciled_by_id``.

That requires new code against an un-migrated database, which the deployed path
cannot produce -- the container entrypoint migrates at step 3 before it serves,
and ``deploy/shekel-deploy.sh`` refuses a target that cannot resolve the current
stamp.  It is reachable only by a host ``flask run`` against a dev database
nobody upgraded, and only if that database holds an archived row, which none
does.  Recorded rather than guarded: a guard here would be code for a state the
deploy makes unreachable, and the honest mitigation is that the migration and
the code ship in the same commit.

**The DELETE cannot orphan a row, and no hand-written census is what makes that
true.**  ``transactions_status_id_fkey`` and ``transfers_status_id_fkey`` are
both ``ON DELETE RESTRICT``, so a row this migration failed to re-point makes
step 3 raise and rolls the whole revision back with the stamp untouched -- which
is the state ``deploy/shekel-deploy.sh`` can still revert the image pin from.
A ``SELECT count(*)`` guard above the DELETE would restate what the constraint
already holds and could only be tested by removing the constraint.

**The downgrade restores the STATUS, not which rows wore it.**  It re-inserts
the row (by name, from the sequence -- ``ref_cache`` resolves statuses by name
and nothing stores this id, so its numeric value is not a fact anything
depends on).  It cannot know which rows the upgrade re-pointed, and no
``system.*`` provenance table is created to tell it: on every database this
ships against the answer is the empty set, and the two such tables that exist
(``system.pre_origination_purge``, ``system.loan_due_date_backfill``) are backed
by no model and are offered for deletion by every autogenerate since -- a
standing hazard revision ``6376c2b8e6db`` had to write a paragraph about.  A
third one recording nothing is not worth that.  Where a re-point does happen it
is recorded anyway, for free: the ``audit_transactions`` / ``audit_transfers``
AFTER UPDATE triggers write ``old_data``, ``new_data`` and ``changed_fields``
into ``system.audit_log``, which the nightly 03:30 retention job prunes -- so
that record is a recovery aid within its window, not a mechanism this downgrade
depends on.

Revision ID: f2a9c4d7e310
Revises: e6b2c07d3f19
Create Date: 2026-08-27 20:05:00.000000
"""
import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'f2a9c4d7e310'
down_revision = 'e6b2c07d3f19'
branch_labels = None
depends_on = None

# The archive's display name.  A migration must not import ``app.enums``
# (revision ``c4e91a7b2d38``'s rule, followed by every revision since), so the
# string is repeated here rather than read from the enum this same commit
# deletes the member from.
_ARCHIVE = "Settled"

# A transfer settles as Paid on both legs whatever direction it runs -- the
# income/expense split is meaningless for a pair whose whole point is that one
# leg is each, which is why ``state_machine``'s transfer map excludes Received
# outright.  So the transfer re-point has one target and needs no type test.
_REPOINT_TRANSFERS = sa.text("""
    UPDATE budget.transfers
       SET status_id = (SELECT id FROM ref.statuses WHERE name = 'Paid')
     WHERE status_id = (SELECT id FROM ref.statuses WHERE name = :archive)
""")

# A transaction settles as Received when it is income and Paid otherwise.  The
# type is read from ``ref.transaction_types`` rather than from a hardcoded id,
# for the reason every other revision reads a ref id by name: the integer is a
# per-database fact and the name is the stable one.
_REPOINT_TRANSACTIONS = sa.text("""
    UPDATE budget.transactions t
       SET status_id = (
               SELECT s.id FROM ref.statuses s
                WHERE s.name = CASE
                    WHEN tt.name = 'Income' THEN 'Received'
                    ELSE 'Paid'
                END
           )
      FROM ref.transaction_types tt
     WHERE tt.id = t.transaction_type_id
       AND t.status_id = (SELECT id FROM ref.statuses WHERE name = :archive)
""")

_DELETE_ARCHIVE = sa.text(
    "DELETE FROM ref.statuses WHERE name = :archive"
)

_RESTORE_ARCHIVE = sa.text("""
    INSERT INTO ref.statuses (name, is_settled, is_immutable,
                              excludes_from_balance)
    VALUES (:archive, true, true, false)
    ON CONFLICT (name) DO NOTHING
""")


def upgrade():
    """Re-point every archived row onto its type's settled status, then delete it.

    Ordered so the FK can do the checking: both UPDATEs run before the DELETE,
    and ``ON DELETE RESTRICT`` refuses the DELETE if either missed a row.
    """
    bind = op.get_bind()
    bind.execute(_REPOINT_TRANSFERS, {"archive": _ARCHIVE})
    bind.execute(_REPOINT_TRANSACTIONS, {"archive": _ARCHIVE})
    bind.execute(_DELETE_ARCHIVE, {"archive": _ARCHIVE})


def downgrade():
    """Re-insert the archive status.  Which rows wore it is not restored.

    ``ON CONFLICT (name) DO NOTHING`` against ``statuses_name_key`` so a repeat
    downgrade is inert rather than a unique violation.  The id comes from the
    sequence: nothing stores it and ``ref_cache`` resolves the row by name.
    """
    op.get_bind().execute(_RESTORE_ARCHIVE, {"archive": _ARCHIVE})
