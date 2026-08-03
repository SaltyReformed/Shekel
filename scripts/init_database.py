"""
Shekel Budget App -- Database Initialization

Detects fresh vs. existing databases and initializes accordingly:

- Fresh DB: creates all tables via SQLAlchemy metadata, materialises
  the ``system.audit_log`` infrastructure (table, trigger function,
  and per-table audit triggers), then stamps Alembic to mark every
  migration as applied.  The audit-infrastructure step is the
  difference vs. ``db.create_all()`` alone -- the audit triggers,
  function, and table are raw SQL outside SQLAlchemy's model registry,
  so a bare ``create_all`` would skip them and the entrypoint health
  check would refuse to start Gunicorn.  See audit finding F-028 and
  remediation Commit C-13.

- Existing DB: runs incremental Alembic migrations.  An existing DB
  that pre-dates Commit C-13 picks up the rebuild migration on the
  next ``flask db upgrade`` and the GRANT block inside the migration
  applies once the ``shekel_app`` role has been provisioned by
  ``scripts/init_db.sql``.

Database role policy:

    This script is part of the deployment pipeline -- not the
    application's request-time path -- so it always runs as the
    owner role (``DATABASE_URL``), never as the least-privilege app
    role (``DATABASE_URL_APP``).  ``DATABASE_URL_APP`` is overridden
    to the empty string (= unset, per the config resolver's contract)
    at the top of the file before ``create_app()`` reads it; this
    scopes the override to this process only and does not affect the
    Gunicorn process that ``entrypoint.sh`` exec's afterwards.

Usage:
    python scripts/init_database.py
"""

import os
import sys

# Force the owner role for this script.  ``app/config.py`` prefers
# ``DATABASE_URL_APP`` over ``DATABASE_URL`` when both are set, which
# is correct for the runtime app (least privilege) but wrong for
# this script (needs DDL: CREATE TABLE, CREATE TRIGGER, ...).
#
# Empty string rather than ``os.environ.pop``: config.py runs
# ``load_dotenv()`` at import time (override=False), which re-inserts
# a ``DATABASE_URL_APP`` line from a repo-local or bind-mounted
# ``.env`` into an absent key -- silently defeating a pop-based
# override (and, inside the dev container, pointing this script at a
# localhost DB that does not exist there).  An existing-but-empty key
# survives load_dotenv, and the config resolver documents
# empty-as-unset: it falls through to DATABASE_URL (covered by
# ``test_empty_database_url_app_falls_through``).  The assignment is
# process-local -- the parent shell's env is untouched, so ``exec
# gunicorn`` after this script still sees the real DATABASE_URL_APP
# and runs as the app role.
os.environ["DATABASE_URL_APP"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pylint: wrong-import-position -- the DATABASE_URL_APP override and the
# sys.path bootstrap above must run before these imports: the app config
# reads the environment at import time, and ``app`` only resolves once
# the repo root is on sys.path (sys.path[0] is scripts/ when invoked as
# ``python scripts/init_database.py``).
# pylint: disable=wrong-import-position
from alembic import command
from alembic.config import Config

from app import create_app, ref_cache
from app.audit_infrastructure import apply_audit_infrastructure
from app.extensions import db
from app.posting_infrastructure import (
    apply_ledger_append_only_privileges,
    apply_posting_infrastructure,
)
from app.services import (
    account_posting_service,
    loan_posting_service,
    posting_service,
)
# pylint: enable=wrong-import-position


def is_fresh_database():
    """Return True when the application's auth schema is empty.

    "Fresh" is defined as the absence of ``auth.users``: every other
    schema in the project depends on it (FKs from budget/salary), so
    if it does not exist neither does anything else.  Returns False
    when the table is present, which signals "run incremental
    migrations" to the caller.
    """
    result = db.session.execute(db.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.tables "
        "  WHERE table_schema = 'auth' AND table_name = 'users'"
        ")"
    ))
    return not result.scalar()


def init_fresh_database(app):
    """Create the schema, the audit + posting infrastructure, and stamp Alembic.

    Five steps in order:

    1. ``db.create_all()`` -- materialise every SQLAlchemy-modeled
       table.  This covers the ``ref``, ``auth``, ``budget``, and
       ``salary`` schemas.
    2. ``apply_audit_infrastructure`` -- materialise the
       ``system.audit_log`` table, the trigger function, the indexes,
       the per-table triggers (one per entry in
       :data:`app.audit_infrastructure.AUDITED_TABLES`), and the
       conditional ``shekel_app`` GRANT block.  ``db.create_all`` does
       not know about any of these -- they are raw SQL outside the
       SQLAlchemy model registry -- so this second step is what
       distinguishes fresh-DB initialisation post-C-13 from the
       previous bypass-of-audit-triggers behaviour that audit
       finding F-028 documents.
    3. ``apply_posting_infrastructure`` -- materialise the
       ``budget.assert_journal_entry_balanced`` function and the deferred
       ``ck_account_postings_balanced`` constraint trigger that enforces
       the per-journal-entry sum-to-zero / at-least-two-legs invariant.
       Like the audit trigger, these are raw SQL outside the model
       registry, so ``db.create_all`` (which made the
       ``budget.account_postings`` table) does not create them.
    4. ``apply_ledger_append_only_privileges`` -- revoke UPDATE/DELETE
       on the two ledger tables from ``shekel_app`` (review M1/R4).
       Required on this path specifically: ``init_db_role.sql`` ran
       BEFORE the tables existed (its table-guarded REVOKE skipped),
       and the stamp in step 5 marks the revoke migration
       (``e3c23fadb21d``) as applied without running it.
    5. ``alembic stamp head`` -- mark every migration as applied so
       subsequent ``flask db upgrade`` calls only apply
       newly-authored migrations.

    Args:
        app (flask.Flask): Application built by ``create_app()``.
            Used for the application context that ``db.create_all``
            and the ``session.execute`` calls require.
    """
    print("Fresh database detected. Creating all tables...")
    db.create_all()
    print("Tables created.")

    print("Materialising audit infrastructure (system.audit_log + triggers)...")
    apply_audit_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    db.session.commit()
    print("Audit infrastructure ready.")

    print("Materialising posting infrastructure (balanced-journal trigger)...")
    apply_posting_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    db.session.commit()
    print("Posting infrastructure ready.")

    # Ledger append-only posture (review M1/R4).  On the fresh-DB path the
    # tables were just created AFTER init_db_role.sql ran (its table-guarded
    # REVOKE skipped), and the Alembic stamp below marks the revoke migration
    # (e3c23fadb21d) as applied without running it -- so this call is what
    # closes UPDATE/DELETE for shekel_app on a fresh database.  A no-op when
    # the role does not exist; idempotent when it does.
    print("Applying ledger append-only privileges (shekel_app)...")
    apply_ledger_append_only_privileges(
        lambda sql: db.session.execute(db.text(sql))
    )
    db.session.commit()
    print("Ledger append-only privileges ready.")

    # Stamp Alembic so it knows all migrations are "applied".
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")
    with app.app_context():
        command.stamp(alembic_cfg, "head")
    print("Alembic stamped to head.")


def migrate_existing_database():
    """Run incremental Alembic migrations against a populated database."""
    print("Existing database detected. Running migrations...")
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "migrations")
    command.upgrade(alembic_cfg, "head")
    print("Migrations complete.")


def resync_all_cash_postings_after_migration():
    """Re-date every settled cash source's postings after the chain is at head.

    Ruling **R-DH (b)** (2026-07-31,
    ``docs/audits/balance_architecture/anchor_settle_partition.md``).  A journal
    entry's ``entry_date`` is derived by
    ``balance_predicates.settled_day``, which moved from the UTC civil day
    to the USER's on that date, together with both folds that read it.  Every
    entry written before then carries the old day, so the STORED ledger and the
    readers disagree for any settle recorded between midnight UTC and the user's
    midnight -- on production, one ``$1,910.95`` mortgage payment stamped
    2026-07-02 00:38:53 UTC that belongs to the evening of 2026-07-01.

    Like its two siblings below this cannot run inside an Alembic migration: it
    needs ``ref_cache`` and the service layer, and this migration host builds the
    app with ``init_ref_cache=False`` (the pre-migration bootstrap window; see
    the ``3104f87`` deploy fix).  And like them it must NOT be a raw-SQL
    restatement of the dating rule -- one statement of "which civil day did this
    settle on" is the property the whole balance arc exists to hold, so it drives
    the go-forward sync instead
    (:func:`app.services.posting_service.resync_all_cash_postings`).

    **It runs FIRST of the three, and the order is the dependency direction.**
    The anchor walk computes each correction's ``ledger_before`` from the source
    postings on the account's linked ledger, so the sources are brought to target
    before the corrections that sit on top of them are reconciled.  (The anchor
    walk reads posting AMOUNTS grouped by source rather than their dates, so this
    ordering is defence rather than a live coupling -- stated so a later reader
    does not reorder it on the assumption that it is arbitrary.)

    Runs only on the existing-database path (a fresh database has no settled
    sources).  Idempotent and self-healing via reconcile-to-target, so it is safe
    on every deploy: a source already at target posts nothing.  Commits in one
    transaction; the deferred balanced-journal trigger validates every entry at
    that COMMIT, so an unbalanced re-post aborts the deploy loud.
    """
    print("Re-dating settled cash postings (transactions + transfers)...")
    # Fresh transaction + ref_cache init, matching the two hooks below (see the
    # loan backfill for the idle-read-transaction rationale).  This hook runs
    # FIRST, so it is the one that opens ref_cache for the sequence.
    db.session.rollback()
    ref_cache.init(db.session)
    transactions, transfers = posting_service.resync_all_cash_postings()
    db.session.commit()
    # CHANGED, not walked (finding N-133 / F8).  A steady-state deploy prints
    # zeroes; a non-zero line is the operator's only evidence that a one-time
    # re-date actually happened, and the one worth reading in the deploy log.
    if transactions or transfers:
        print(
            f"Cash posting re-date complete: RE-POSTED {transactions} "
            f"transaction(s) and {transfers} transfer(s).  These sources' "
            "journal entries moved to a different entry_date; a rollback "
            "ACROSS this dating change must re-run the hook under the old "
            "image, not only swap the container."
        )
    else:
        print("Cash posting re-date complete: already at target (0 changed).")


def backfill_loan_payment_postings_after_migration():
    """Post the historical loan genesis ledger after the chain is at head.

    Build-Order Step 4 + the read switch.  Posts every loan's opening, true-up,
    and confirmed-payment corrections.  This backfill cannot run inside an
    Alembic migration: it needs the ``ref_cache`` / service layer, and this
    migration host builds the app with ``init_ref_cache=False`` (the
    pre-migration bootstrap window; see the ``3104f87`` deploy fix), so
    ``ref_cache`` is off while migrations run.  Unlike the Step-2 / Step-3 cash
    backfills, the loan split is a running-balance walk over rate periods and
    effective-dated escrow -- not a one-line SQL formula -- so it cannot be
    reproduced in raw SQL without duplicating the money-critical split engine.
    So it runs HERE, once the chain has reached head and every ref row (the
    posting kinds / sources, the ledger-account kinds) and schema object the
    service needs exists: initialise ``ref_cache`` against the now-migrated
    database, then delegate to the idempotent
    :func:`app.services.loan_posting_service.backfill_all_loan_postings`.

    Runs only on the existing-database path (the fresh-database branch stamps
    Alembic without running migrations and has no loan payments to post, and its
    ref tables are not seeded until after this host exits).  Idempotent and
    self-healing (reconcile-to-target), so it is safe on every deploy -- a
    payment already carrying a go-forward correction is at target and nothing is
    re-posted.  Commits the corrections in one transaction; the deferred
    balanced-journal trigger validates every entry at that COMMIT, so an
    unbalanced correction aborts the deploy loud.
    """
    print("Backfilling historical loan genesis ledger (opening/true-up/splits)...")
    # Discard the idle read transaction ``is_fresh_database()`` opened before the
    # migrations ran, so ``ref_cache.init`` reads on a FRESH transaction that sees
    # the migration-seeded Step-4 ref rows.  Correct today under READ COMMITTED
    # regardless, but this makes it isolation-independent and releases the stale
    # transaction rather than carrying it across the reads.
    db.session.rollback()
    ref_cache.init(db.session)
    posted = loan_posting_service.backfill_all_loan_postings()
    db.session.commit()
    print(f"Loan genesis-ledger backfill complete ({len(posted)} loan(s) reconciled).")


def backfill_all_account_anchor_postings_after_migration():
    """Post every non-loan account's anchor genesis ledger after the chain is at head.

    Build-Order Step 5, C7.  Posts every NON-loan account's opening and
    true-up anchor corrections (the equity counter-leg of each
    ``AccountAnchorHistory`` assertion), so after this the trial balance closes
    app-wide: every non-loan linked ledger sums to an ABSOLUTE balance.  Like
    the loan genesis backfill this cannot run inside an Alembic migration -- it
    needs the ``ref_cache`` / service layer, and this migration host builds the
    app with ``init_ref_cache=False`` (the pre-migration bootstrap window; see
    the ``3104f87`` deploy fix) so ``ref_cache`` is off while migrations run.
    Unlike the Step-2 / Step-3 cash backfills, an anchor correction is a
    moment-granular walk over the account's assertions against its linked
    ledger, not a one-line SQL formula, so it cannot be reproduced in raw SQL
    without duplicating that walk.  So it runs HERE, once the chain has reached
    head and every ref row (the ``account_opening`` / ``account_trueup``
    sources, the ``anchor_equity`` ledger-account kind) exists: it re-uses the
    ``ref_cache`` this host initialised for the loan backfill above, then
    delegates to the idempotent
    :func:`app.services.account_posting_service.backfill_all_account_anchor_postings`.

    Runs only on the existing-database path (the fresh-database branch stamps
    Alembic without running migrations and its ref tables are not seeded until
    after this host exits).  It rolls back and re-initialises ``ref_cache`` on a
    fresh transaction first -- redundant after the loan backfill above committed,
    but it keeps the hook self-contained and correct when invoked in isolation
    (the deploy sequence and the backfill suite both call it directly).
    Idempotent and self-healing (reconcile-to-target), so it is safe on every
    deploy -- an account already carrying its go-forward corrections is at target
    and nothing is re-posted.  Commits the corrections in one transaction; the
    deferred balanced-journal trigger validates every entry at that COMMIT, so an
    unbalanced correction aborts the deploy loud.
    """
    print("Backfilling historical account anchor ledger (opening/true-up)...")
    # Fresh transaction + ref_cache re-init, so the hook is correct in isolation
    # (see the loan backfill above for the idle-read-transaction rationale).
    db.session.rollback()
    ref_cache.init(db.session)
    posted = account_posting_service.backfill_all_account_anchor_postings()
    db.session.commit()
    print(
        f"Account anchor-ledger backfill complete "
        f"({len(posted)} account(s) reconciled)."
    )


if __name__ == "__main__":
    # init_ref_cache=False: this migration host builds the app only for an
    # Alembic context and runs BEFORE the migrations seed new ref rows, so the
    # strict ref_cache row-check must not fire on the pre-migration database
    # (it raises on a missing row in an existing ref table -- the exact
    # bootstrap window a row-adding migration like Step 3's creates).
    flask_app = create_app(init_ref_cache=False)
    with flask_app.app_context():
        if is_fresh_database():
            init_fresh_database(flask_app)
        else:
            migrate_existing_database()
            resync_all_cash_postings_after_migration()
            backfill_loan_payment_postings_after_migration()
            backfill_all_account_anchor_postings_after_migration()
