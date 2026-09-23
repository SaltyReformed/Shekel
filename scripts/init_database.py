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

- Existing DB: runs incremental Alembic migrations, then the three
  deploy hooks that reconcile the posted ledger.  An existing DB
  that pre-dates Commit C-13 picks up the rebuild migration on the
  next ``flask db upgrade`` and the GRANT block inside the migration
  applies once the ``shekel_app`` role has been provisioned by
  ``scripts/init_db.sql``.

**Either path is ONE transaction, committed once** (plan step
balance:X-cv, ruling R-BAL105).  Everything runs on the connection
``db.session`` holds -- the migrations too, through Alembic's
shared-connection recipe in ``migrations/env.py`` -- so a hook that
refuses, or a migration that fails, leaves every row where the deploy
found it, ``alembic_version`` included (sequence counters, which
PostgreSQL does not roll back, still advance).  That stamp is
what ``deploy/shekel-deploy.sh`` reads to decide a rollback: unmoved,
the previous image can still resolve it, and the script re-pins that
image on its own instead of refusing and naming a dump to restore.

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

# The repository root: on sys.path for the imports below, and where
# ``alembic.ini`` and ``migrations/`` are read from, whatever the working
# directory (the entrypoint runs from it; a test process need not).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

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
from app.level_infrastructure import apply_level_infrastructure
from app.sighting_infrastructure import apply_sighting_infrastructure
from app.opening_infrastructure import ALL_ARMS, apply_opening_infrastructure
from app.append_only_infrastructure import (
    apply_append_only_infrastructure,
)
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


def _alembic_config():
    """Return the Alembic config that runs on the deploy's ONE connection.

    ``attributes["connection"]`` is Alembic's shared-connection recipe:
    ``migrations/env.py`` configures the migration context on the connection
    ``db.session`` already holds, inside the transaction
    :func:`initialise_database` opened, rather than opening and committing a
    connection of its own.  So the chain and its stamp commit with the rest of
    entrypoint step 3, or not at all.

    Returns:
        alembic.config.Config: The config every Alembic command here takes.
    """
    alembic_cfg = Config(os.path.join(_REPO_ROOT, "alembic.ini"))
    alembic_cfg.set_main_option(
        "script_location", os.path.join(_REPO_ROOT, "migrations"),
    )
    alembic_cfg.attributes["connection"] = db.session.connection()
    return alembic_cfg


def init_fresh_database():
    """Create the schema, the integrity infrastructure, and stamp Alembic.

    The steps below run in order -- with the append-only, level-within-file
    and last-sighting blocks between 4 and 5, each documented where it runs --
    every one on the deploy's ONE connection and none of them committing (plan
    step balance:X-cv): :func:`initialise_database` commits them together.  A
    failure part-way therefore leaves the database as empty as it found it,
    instead of a half-built schema that the next boot reads as "existing"
    (:func:`is_fresh_database` asks only for ``auth.users``) and tries to
    migrate from no stamp.

    1. ``db.metadata.create_all`` on that connection -- materialise every
       SQLAlchemy-modeled table.  This covers the ``ref``, ``auth``,
       ``budget``, and ``salary`` schemas.  Not ``db.create_all()``:
       Flask-SQLAlchemy runs that on a connection of its own and commits it.
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
    4. ``apply_opening_infrastructure`` -- materialise
       ``budget.account_books_opened_on`` and the two deferred
       constraint triggers that make a cash movement dated on or
       before its account's ``opened_on`` unstorable (plan step
       X-f3c-2b).  Raw SQL outside the model registry, exactly like
       the two above, so ``db.create_all`` does not produce it.  There
       is nothing to legalise on this path: the database is empty.
    5. ``apply_ledger_append_only_privileges`` -- revoke UPDATE/DELETE
       on the two ledger tables from ``shekel_app`` (review M1/R4).
       Required on this path specifically: ``init_db_role.sql`` ran
       BEFORE the tables existed (its table-guarded REVOKE skipped),
       and the stamp in step 6 marks the revoke migration
       (``e3c23fadb21d``) as applied without running it.
    6. ``alembic stamp head`` -- mark every migration as applied so
       subsequent ``flask db upgrade`` calls only apply
       newly-authored migrations.  On the same connection
       (:func:`_alembic_config`), so the stamp commits with the schema.
    """
    print("Fresh database detected. Creating all tables...")
    db.metadata.create_all(bind=db.session.connection())
    print("Tables created.")

    print("Materialising audit infrastructure (system.audit_log + triggers)...")
    apply_audit_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Audit infrastructure ready.")

    print("Materialising posting infrastructure (balanced-journal trigger)...")
    apply_posting_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Posting infrastructure ready.")

    print("Materialising books-boundary constraint (opening equity)...")
    # ``ALL_ARMS``: the fresh-database path materialises HEAD and never runs
    # the migration chain, so it wants whatever arms the module currently has.
    # A MIGRATION names its arms literally instead -- see that constant.
    apply_opening_infrastructure(
        lambda sql: db.session.execute(db.text(sql)),
        arms=ALL_ARMS,
    )
    print("Books-boundary constraint ready.")

    # The append-only refusal on the four account-history tables (plan step
    # X-f3c-2c, ruling R-HY; the fourth at balance:X-bj-1).  Same fresh-DB
    # reason as every block around it: the tables were created by
    # ``create_all`` above and the stamp below marks f4a7c2d9e51b applied
    # without running it, so this call is what installs the trigger on a
    # fresh database.
    print("Applying append-only refusal (account history tables)...")
    apply_append_only_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Append-only refusal ready.")

    # A bank level lies inside its statement's file (plan step balance:X-bj-1,
    # ruling R-GF): the cross-table bound that was a CHECK on the import row
    # while the placed day lived there.  Same fresh-DB reason as the block
    # above, same three-caller contract.
    print("Applying level-within-file bound (statement levels)...")
    apply_level_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Level-within-file bound ready.")

    # A bank line goes with its last sighting (plan step bank_import:X-f6b-1,
    # ruling R-BI10): the rule that makes a line no source stands behind
    # unrepresentable.  Same fresh-DB reason, same three-caller contract.
    print("Applying last-sighting rule (statement lines)...")
    apply_sighting_infrastructure(
        lambda sql: db.session.execute(db.text(sql))
    )
    print("Last-sighting rule ready.")

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
    print("Ledger append-only privileges ready.")

    # Stamp Alembic so it knows all migrations are "applied".
    command.stamp(_alembic_config(), "head")
    print("Alembic stamped to head.")


def migrate_existing_database():
    """Run incremental Alembic migrations against a populated database.

    On the deploy's ONE connection (:func:`_alembic_config`), committing
    nothing: the chain, its stamp included, commits with the three deploy hooks
    below or rolls back with them.
    """
    print("Existing database detected. Running migrations...")
    command.upgrade(_alembic_config(), "head")
    print("Migrations applied (they commit with the deploy hooks below).")


def resync_all_cash_postings_after_migration():
    """Re-date or re-book every settled cash source's postings after the chain is at head.

    Ruling **R-DH (b)** (2026-07-31,
    ``docs/audits/balance_architecture/archive/anchor_settle_partition.md``).  A journal
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
    on every deploy: a source already at target posts nothing.  Commits nothing
    itself: its re-posts commit with the migrations and the other two hooks in
    the deploy's ONE transaction (plan step balance:X-cv), where the deferred
    balanced-journal trigger validates every entry, so an unbalanced re-post
    aborts the deploy loud.  Until plan step ``X-bi-6-5`` the resync itself
    also refuses while any transfer still holds a nonzero legacy one-entry
    posting -- one whose family it had to skip (ruling **R-BAL104**).  Since
    X-cv that refusal rolls the migrations back with it, so the stamp stays
    where the previous image can resolve it and ``deploy/shekel-deploy.sh``
    re-pins that image on its own -- the manual dump restore ruling
    **R-BAL105** accepted until then is gone.  The release rehearsal on a
    same-day dump still meets it first.

    Returns:
        str: The line the deploy log prints once the one commit has landed --
        the count of sources CHANGED, not walked (finding N-133 / F8).  A
        steady-state deploy prints zeroes; a non-zero line is the operator's
        only evidence that a one-time re-date or re-book actually happened,
        and the one worth reading in the deploy log.
    """
    print("Resyncing settled cash postings (transactions + transfers)...")
    transactions, transfers = posting_service.resync_all_cash_postings()
    if transactions or transfers:
        return (
            f"Cash posting resync complete: RE-POSTED {transactions} "
            f"transaction(s) and {transfers} transfer(s); their journal "
            "entries were re-dated or re-booked.  To roll back past this "
            "deploy, follow deploy/shekel-deploy.sh's rollback instructions; "
            "it logs the pre-deploy dump it took."
        )
    return "Cash posting resync complete: already at target (0 changed)."


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
    service needs exists -- on the ``ref_cache`` :func:`_migrate_and_reconcile`
    loaded after the migrations, inside the same transaction, so it sees the
    ref rows they seeded -- delegating to the idempotent
    :func:`app.services.loan_posting_service.backfill_all_loan_postings`.

    Runs only on the existing-database path (the fresh-database branch stamps
    Alembic without running migrations and has no loan payments to post, and its
    ref tables are not seeded until after this host exits).  Idempotent and
    self-healing (reconcile-to-target), so it is safe on every deploy -- a
    payment already carrying a go-forward correction is at target and nothing is
    re-posted.  Commits nothing itself: the corrections commit in the deploy's
    ONE transaction (plan step balance:X-cv), where the deferred
    balanced-journal trigger validates every entry, so an unbalanced correction
    aborts the deploy loud.

    Returns:
        str: The line the deploy log prints once the one commit has landed.
    """
    print("Backfilling historical loan genesis ledger (opening/true-up/splits)...")
    posted = loan_posting_service.backfill_all_loan_postings()
    return f"Loan genesis-ledger backfill complete ({len(posted)} loan(s) reconciled)."


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
    sources, and the ``anchor_equity`` / ``interest_income`` /
    ``unrealized_change`` ledger-account kinds with the ``Unrealized`` class
    ruling R-FO's dispatch books into) exists: it re-uses the ``ref_cache``
    :func:`_migrate_and_reconcile` loaded for all three hooks, then delegates to
    the idempotent
    :func:`app.services.account_posting_service.backfill_all_account_anchor_postings`.

    Runs only on the existing-database path (the fresh-database branch stamps
    Alembic without running migrations and its ref tables are not seeded until
    after this host exits).  Idempotent and self-healing (reconcile-to-target),
    so it is safe on every deploy -- an account already carrying its go-forward
    corrections is at target and nothing is re-posted.  Commits nothing itself:
    the corrections commit in the deploy's ONE transaction (plan step
    balance:X-cv), where the deferred balanced-journal trigger validates every
    entry, so an unbalanced correction aborts the deploy loud.

    Returns:
        str: The line the deploy log prints once the one commit has landed.
    """
    print("Backfilling historical account anchor ledger (opening/true-up)...")
    posted = account_posting_service.backfill_all_account_anchor_postings()
    return (
        f"Account anchor-ledger backfill complete "
        f"({len(posted)} account(s) reconciled)."
    )


def _migrate_and_reconcile():
    """Bring an existing database to head and run the three deploy hooks, uncommitted.

    ``ref_cache`` is loaded ONCE, after the chain has reached head: the
    transaction sees every ref row the migrations seeded (its own writes), so
    the three hooks share it.  Each hook used to roll back and re-load it on a
    fresh transaction of its own, which is what one transaction removes.  The
    hooks run in the order the cash resync's docstring explains.

    Returns:
        list[str]: Each hook's line for the deploy log, in run order, printed
        by :func:`initialise_database` only once they have committed.
    """
    migrate_existing_database()
    ref_cache.init(db.session)
    completed = [resync_all_cash_postings_after_migration()]
    completed.append(backfill_loan_payment_postings_after_migration())
    completed.append(backfill_all_account_anchor_postings_after_migration())
    return completed


def initialise_database():
    """Run entrypoint step 3 as ONE transaction and commit it once (plan step balance:X-cv).

    Ruling **R-BAL105**'s future step.  Before it the migrations committed in
    ``migrations/env.py``'s own transaction BEFORE the three deploy hooks ran,
    so a hook that refused (ruling **R-BAL104**'s legacy-net refusal, the loan
    sync's checked-projection assert, an unbalanced entry at a hook's commit,
    the anchor walk's refusals)
    left a stamp the previous image could not resolve, and
    ``deploy/shekel-deploy.sh`` refused to re-pin it: a manual dump restore.
    Now the fresh-database build or the migrations, ``ref_cache`` and the three
    hooks all run on the connection ``db.session`` holds, and this is the ONLY
    commit.  Any exception before it propagates, the process exits non-zero, and
    PostgreSQL rolls the whole transaction back, ``alembic_version`` included.

    **The transaction committed is checked to be the one opened.**  A
    ``rollback()`` inside the sequence would end it and let what follows run on
    a new one, so this commit would land the hooks' writes WITHOUT the
    migrations they were run against.  One such rollback is reachable today:
    ``ref_cache.init`` rolls back when a ref table is missing (``_load_rows``;
    its warning names the table).  None of the three hooks' services commits or
    rolls back (census 2026-09-22: every ``commit()`` / ``rollback()`` under
    ``app/services/`` belongs to a request-path door the hooks do not call;
    their SAVEPOINTs are ``begin_nested`` blocks, which never end this
    transaction).  **A stray COMMIT is NOT caught in time, and that is an open
    gap**: a session ``commit()`` inside the sequence would already have
    committed the migrations before this check raised (a connection-level or SQL
    ``COMMIT`` is not seen here at all), which is the manual-restore case this
    step removes.  None is reachable today (the census above); the developer
    ruled the structural close -- the deploy owns the transaction on its own
    connection and the session joins it, so a commit inside cannot reach it --
    for a later X-cv leaf.
    """
    db.session.connection()
    opened = db.session().get_transaction()
    if is_fresh_database():
        init_fresh_database()
        completed = []
    else:
        completed = _migrate_and_reconcile()
    if db.session().get_transaction() is not opened:
        raise RuntimeError(
            "init_database: the deploy's transaction ended before its one "
            "commit -- a rollback() (or a commit()) ran inside the sequence, "
            "so what followed no longer ran with the migrations.  Refusing to "
            "commit.  ref_cache rolls back when a ref table is missing: look "
            "for its warning above."
        )
    db.session.commit()
    print("Database initialised: ONE transaction, committed.")
    for line in completed:
        print(line)


if __name__ == "__main__":
    # init_ref_cache=False: this migration host builds the app only for an
    # Alembic context and runs BEFORE the migrations seed new ref rows, so the
    # strict ref_cache row-check must not fire on the pre-migration database
    # (it raises on a missing row in an existing ref table -- the exact
    # bootstrap window a row-adding migration like Step 3's creates).
    flask_app = create_app(init_ref_cache=False)
    with flask_app.app_context():
        initialise_database()
