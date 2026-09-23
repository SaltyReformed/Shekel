"""Entrypoint step 3 is ONE transaction, committed once (plan step balance:X-cv).

``scripts/init_database.py`` used to commit the migrations in
``migrations/env.py``'s own transaction BEFORE the three deploy hooks ran, so a
hook that refused left a stamp the previous image could not resolve and
``deploy/shekel-deploy.sh`` refused to re-pin it -- a manual dump restore
(ruling **R-BAL105**).  Now ``initialise_database`` opens a connection and a
transaction of its own, runs the migrations on it and everything else through a
session JOINED to it, and its commit is the only one (rulings **R-BAL113**,
**R-BAL118**).  These tests grade that from OUTSIDE the transaction: every
observation is read over a SEPARATE connection, which under READ COMMITTED sees
only what was committed, so a flush can never pass for a commit here.

**A real pending migration, not a stand-in.**  The per-test database is
already at head, so a no-op upgrade would move no stamp and prove nothing.
The ``pending_migration`` fixture builds a version directory holding every
real migration (symlinked) plus one probe revision on top of the real head
that creates ``budget.xcv_probe``, and points the one migration runner's
Alembic config at it (:mod:`app.migration_runner`, ruling **R-BAL114**) -- so
the stamp and a DDL change are really pending when the deploy runs.

**A real hook write.**  A settled loan payment whose corrections were cleared
(the loan backfill suite's own recipe) makes the SECOND hook post a
correction, so "every hook write unmoved" is asserted of a write a real hook
made, not of one the test forged.
"""
from __future__ import annotations

import logging
import pathlib
import textwrap
from datetime import date
from decimal import Decimal

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.exc import InternalError, InvalidRequestError

from app import migration_runner, ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
from app.models.category import Category
from app.services.posting_reads import PostingError
from tests._test_helpers import (
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
    ledger_accounts_for_account,
    load_init_database_module,
    load_migration_module,
    loan_income_shadow,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VERSIONS_DIR = _REPO_ROOT / "migrations" / "versions"
_INIT_DB = load_init_database_module()

# The loan backfill suite's teardown pair, run in real downgrade-chain order to
# reproduce a payment settled before the go-forward wiring (no correction).
_GENESIS_MIGRATION = load_migration_module(
    "f3d6b1a8c2e4_loan_genesis_postings_data_boundary.py",
)
_PAYMENT_MIGRATION = load_migration_module(
    "e2a9f1c7b4d6_backfill_loan_payment_split_postings.py",
)

_PROBE_REVISION = "xcvprobe0001"
_PROBE_MIGRATION = textwrap.dedent('''\
    """X-cv probe: a pending migration the one-transaction tests apply."""
    from alembic import op

    revision = "{revision}"
    down_revision = "{down_revision}"
    branch_labels = None
    depends_on = None


    def upgrade():
        """Create the probe table."""
        op.execute("CREATE TABLE budget.xcv_probe (id integer)")


    def downgrade():
        """Drop the probe table."""
        op.execute("DROP TABLE budget.xcv_probe")
''')

# The loan the hook writes for: a $1,000 payment on a $100,000 6% loan (the
# backfill suite's fixture), settled in period 1 with today frozen after it.
_TODAY = date(2026, 5, 15)
_P1 = 1


def _real_head() -> str:
    """Return the head revision of the repository's own migration chain."""
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


@pytest.fixture(name="pending_migration")
def _pending_migration(tmp_path, monkeypatch) -> str:
    """Put one real, pending migration on top of the head; return that head.

    Every real version file is symlinked into a temporary directory beside a
    probe revision whose ``down_revision`` is the real head, and the migration
    runner's ``Config`` is wrapped so its ``version_locations`` reads that
    directory.  Everything else about the config -- ``alembic.ini``,
    ``env.py``, the shared connection -- is the runner's own.
    """
    head = _real_head()
    versions = tmp_path / "versions"
    versions.mkdir()
    for migration in _VERSIONS_DIR.glob("*.py"):
        (versions / migration.name).symlink_to(migration)
    (versions / f"{_PROBE_REVISION}_probe.py").write_text(
        _PROBE_MIGRATION.format(
            revision=_PROBE_REVISION, down_revision=head,
        ),
        encoding="utf-8",
    )
    real_config = migration_runner.Config

    def _config_reading_the_probe(path):
        """Build the runner's config, reading versions from the probe dir."""
        config = real_config(path)
        config.set_main_option("version_locations", str(versions))
        return config

    monkeypatch.setattr(migration_runner, "Config", _config_reading_the_probe)
    return head


@pytest.fixture(name="uncorrected_payment")
def _uncorrected_payment(app, db, seed_user, seed_periods, monkeypatch) -> dict:
    """Commit a settled loan payment with its corrections cleared.

    The loan backfill -- the deploy's SECOND hook -- restores exactly one
    correction for it, which is the hook write these tests watch.

    Returns:
        The correction's key, as plain values a separate connection can query:
        ``source`` (the ``loan_payment`` source id), ``scenario``, ``period``
        and ``day``.
    """
    freeze_today(monkeypatch, _TODAY)
    loan = create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=Decimal("250000.00"),
        anchor_balance=Decimal("100000.00"), anchor_date=date(2026, 1, 10),
        rate=Decimal("0.06000"), origination_date=date(2025, 1, 1),
        name="X-cv Loan",
    )
    xfer = create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, seed_periods[_P1],
        amount=Decimal("1000.00"),
    )
    db.session.commit()
    shadow = loan_income_shadow(db.session, xfer.id, loan.id)
    _GENESIS_MIGRATION._remove_loan_genesis_postings(db.session)
    _PAYMENT_MIGRATION._remove_loan_payment_postings(db.session)
    db.session.commit()
    return {
        "source": ref_cache.posting_source_id(PostingSourceEnum.LOAN_PAYMENT),
        "scenario": shadow.scenario_id,
        "period": shadow.pay_period_id,
        "day": shadow.settled_on,
    }


def _committed(db, payment: dict) -> dict:
    """Read what is COMMITTED, over a connection of its own.

    Args:
        db: The Flask-SQLAlchemy handle.
        payment: The correction key :func:`_uncorrected_payment` returned.

    Returns:
        ``stamp`` (the ``alembic_version`` row), ``probe`` (whether the probe
        migration's table exists) and ``corrections`` (how many loan-payment
        corrections sit at the payment's key).
    """
    with db.engine.connect() as conn:
        return {
            "stamp": conn.execute(text(
                "SELECT version_num FROM public.alembic_version"
            )).scalar_one(),
            "probe": conn.execute(text(
                "SELECT to_regclass('budget.xcv_probe') IS NOT NULL"
            )).scalar_one(),
            "corrections": conn.execute(text(
                "SELECT count(*) FROM budget.journal_entries "
                "WHERE source_kind_id = :source AND scenario_id = :scenario "
                "  AND pay_period_id = :period AND entry_date = :day"
            ), payment).scalar_one(),
        }


class TestARefusalCommitsNothing:
    """A hook that refuses leaves the stamp AND every hook write where they were."""

    def test_a_hook_refusal_leaves_the_stamp_and_every_hook_write_unmoved(
        self, app, db, pending_migration, uncorrected_payment, monkeypatch,
    ):
        """The third hook refuses after the second has written: nothing commits.

        Before X-cv the probe migration and its stamp would already be
        committed here -- the case ``shekel-deploy`` cannot re-pin -- and the
        loan correction with them (the hook committed on its own).
        """
        def _refuse():
            """Stand in for an anchor walk that refuses an account."""
            raise PostingError("forged refusal: the third deploy hook")

        monkeypatch.setattr(
            _INIT_DB.account_posting_service,
            "backfill_all_account_anchor_postings", _refuse,
        )
        with pytest.raises(PostingError, match="forged refusal"):
            _INIT_DB.initialise_database()

        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }
        db.session.rollback()

    def test_an_unbalanced_entry_rolls_the_migration_back_at_the_one_commit(
        self, app, db, seed_user, pending_migration, uncorrected_payment,
        monkeypatch, capsys,
    ):
        """The deferred balanced-journal trigger still fires, now at the ONE commit.

        The third hook's place writes a one-legged entry.  The trigger is
        DEFERRED, so nothing refuses until COMMIT -- which is now the commit
        that also carries the migration, so PostgreSQL rolls the migration, its
        stamp and the second hook's correction back with the bad entry.
        """
        # Plain ids read up front: the fixtures' ORM rows belong to the test's
        # own session, which the deploy sets aside while it runs, so a reload
        # inside the hook would run outside the deploy's transaction.
        entry_key = {
            "u": seed_user["user"].id,
            "s": uncorrected_payment["scenario"],
            "p": uncorrected_payment["period"],
            "d": uncorrected_payment["day"],
            "src": ref_cache.posting_source_id(PostingSourceEnum.TRANSFER),
        }
        checking_ledger = ledger_accounts_for_account(
            db.session, seed_user["account"].id,
        )[0].id

        def _write_one_leg():
            """Stage a journal entry with a single $1.00 leg; return no ids."""
            entry_id = db.session.execute(text(
                "INSERT INTO budget.journal_entries "
                "  (user_id, scenario_id, pay_period_id, entry_date, "
                "   source_kind_id, description) "
                "VALUES (:u, :s, :p, :d, :src, 'X-cv one leg') RETURNING id"
            ), entry_key).scalar_one()
            db.session.execute(text(
                "INSERT INTO budget.account_postings "
                "  (journal_entry_id, ledger_account_id, amount, "
                "   posting_kind_id) "
                "VALUES (:e, :l, :a, :k)"
            ), {
                "e": entry_id, "l": checking_ledger, "a": Decimal("1.00"),
                "k": ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
            })
            return []

        monkeypatch.setattr(
            _INIT_DB.account_posting_service,
            "backfill_all_account_anchor_postings", _write_one_leg,
        )
        with pytest.raises(
            InternalError, match=r"has 1 posting\(s\); >= 2 required",
        ):
            _INIT_DB.initialise_database()
        db.session.rollback()
        # Raised BY the one commit: the line printed after it never ran, and
        # the hook sequence before it did.
        printed = capsys.readouterr().out
        assert "Backfilling historical account anchor ledger" in printed
        assert "Database initialised" not in printed

        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }


class TestACleanRunCommitsOnce:
    """With no refusal, the migration and every hook write commit together."""

    def test_the_migration_and_every_hook_write_commit_together(
        self, app, db, pending_migration, uncorrected_payment,
    ):
        """The stamp moves to the probe, its table exists, the correction is posted."""
        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }

        _INIT_DB.initialise_database()

        assert _committed(db, uncorrected_payment) == {
            "stamp": _PROBE_REVISION, "probe": True, "corrections": 1,
        }

    def test_a_write_the_last_hook_left_unflushed_is_committed(
        self, app, db, seed_user, pending_migration, monkeypatch,
    ):
        """The deploy flushes the session before committing its connection.

        The deploy commits the CONNECTION, never the session, so the implicit
        flush a session ``commit()`` performs is not there: a row a hook only
        ``add()``-ed would be discarded when the session closes, silently.  The
        last hook here adds one and returns without flushing.
        """
        user_id = seed_user["user"].id

        def _add_without_flushing():
            """Stage one category row and return, leaving it unflushed."""
            db.session.add(Category(
                user_id=user_id, group_name="X-cv", item_name="Unflushed",
            ))
            return []

        monkeypatch.setattr(
            _INIT_DB.account_posting_service,
            "backfill_all_account_anchor_postings", _add_without_flushing,
        )

        _INIT_DB.initialise_database()

        with db.engine.connect() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM budget.categories "
                "WHERE user_id = :u AND item_name = 'Unflushed'"
            ), {"u": user_id}).scalar_one() == 1


def _with_stray_calls(monkeypatch, db, hook, *, before=None, after=None):
    """Run a real deploy hook's service between stray session calls.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        db: The Flask-SQLAlchemy handle whose session the hooks use.
        hook: ``(service module, function name)`` of the hook's real service:
            the loan backfill is the SECOND hook (the third follows it), the
            anchor backfill the THIRD and last.
        before: ``"commit"`` or ``"rollback"``, called on ``db.session`` before
            the real service runs; ``None`` for no call.
        after: The same, called after it.
    """
    module, name = hook
    real_service = getattr(module, name)

    def _stray_calls_around_the_service():
        """Call the stray session method(s) around the real service."""
        if before is not None:
            getattr(db.session, before)()
        posted = real_service()
        if after is not None:
            getattr(db.session, after)()
        return posted

    monkeypatch.setattr(module, name, _stray_calls_around_the_service)


_SECOND_HOOK = (_INIT_DB.loan_posting_service, "backfill_all_loan_postings")
_THIRD_HOOK = (
    _INIT_DB.account_posting_service, "backfill_all_account_anchor_postings",
)


class TestNothingInsideTheSequenceCanCommit:
    """A stray commit or rollback inside the deploy saves nothing early or partially.

    Rulings **R-BAL113** (the session joins the deploy's transaction, so a
    session commit does not reach it) and **R-BAL118** (the session holds only
    that one transaction, so a stray call is followed by a refusal, never by a
    transaction of the session's own).  Before X-cv's leaf 1b a stray COMMIT
    here committed the migrations on the spot, and a rollback followed by a
    commit committed the hooks' later writes WITHOUT the migrations.
    """

    def test_a_stray_rollback_saves_nothing(
        self, app, db, pending_migration, uncorrected_payment, monkeypatch,
    ):
        """The second hook rolls back first: its first query refuses.

        ``ref_cache`` rolls back on a missing ref table, and a rollback ends the
        deploy's transaction, the migration with it.  The session may not then
        begin a transaction of its own for the backfill to write into.
        """
        _with_stray_calls(monkeypatch, db, _SECOND_HOOK, before="rollback")
        with pytest.raises(InvalidRequestError) as refused:
            _INIT_DB.initialise_database()

        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }
        assert "Autobegin is disabled" in str(refused.value)
        db.session.rollback()

    def test_a_stray_commit_mid_sequence_saves_nothing(
        self, app, db, pending_migration, uncorrected_payment, monkeypatch,
    ):
        """The second hook commits its correction: the third hook's first query refuses.

        The commit is not passed to the deploy's transaction, so neither the
        migration nor the correction it flushed is committed by it, and the
        deploy then fails with nothing saved.
        """
        _with_stray_calls(monkeypatch, db, _SECOND_HOOK, after="commit")
        with pytest.raises(InvalidRequestError) as refused:
            _INIT_DB.initialise_database()

        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }
        assert "Autobegin is disabled" in str(refused.value)
        db.session.rollback()

    def test_a_rollback_then_a_commit_saves_nothing(
        self, app, db, pending_migration, uncorrected_payment, monkeypatch,
    ):
        """The escape ruling R-BAL118 closed: roll back, write, commit.

        Measured before the ruling: a session allowed to begin a transaction of
        its own after the rollback wrote the correction into it and the commit
        saved it, WITHOUT the migration the rollback undid.
        """
        _with_stray_calls(
            monkeypatch, db, _SECOND_HOOK, before="rollback", after="commit",
        )
        with pytest.raises(InvalidRequestError) as refused:
            _INIT_DB.initialise_database()

        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }
        assert "Autobegin is disabled" in str(refused.value)
        db.session.rollback()

    def test_a_rollback_after_the_last_statement_fails_the_one_commit(
        self, app, db, pending_migration, uncorrected_payment, monkeypatch,
    ):
        """The last hook rolls back after its work: the deploy's commit refuses."""
        _with_stray_calls(monkeypatch, db, _THIRD_HOOK, after="rollback")
        with pytest.raises(InvalidRequestError) as refused:
            _INIT_DB.initialise_database()

        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }
        assert "transaction is inactive" in str(refused.value)
        db.session.rollback()

    def test_a_commit_after_the_last_statement_is_harmless(
        self, app, db, pending_migration, uncorrected_payment, monkeypatch,
    ):
        """The last hook commits after its work: everything commits, once, as usual."""
        _with_stray_calls(monkeypatch, db, _THIRD_HOOK, after="commit")

        _INIT_DB.initialise_database()

        assert _committed(db, uncorrected_payment) == {
            "stamp": _PROBE_REVISION, "probe": True, "corrections": 1,
        }


class TestTheRunnerCommitsNothing:
    """``app.migration_runner`` never commits, whatever connection it is handed."""

    def test_a_connection_outside_a_transaction_is_not_committed(
        self, app, db, pending_migration, uncorrected_payment,
    ):
        """Handed a connection with no transaction, the runner opens one it never commits.

        Alembic begins and COMMITS a transaction of its own on a connection that
        has none, which is how a deploy whose transaction a stray rollback had
        already ended would have committed its migrations on the spot.  The
        connection closes uncommitted here, so the migration must be gone.
        """
        with db.engine.connect() as connection:
            migration_runner.upgrade_to_head(connection)

        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }


class TestTheDeployPutsTheCallersSessionBack:
    """The deploy borrows the app context's session slot and returns it untouched."""

    def test_the_session_held_before_is_the_session_held_after(
        self, app, db, seed_user,
    ):
        """A caller's session, and its uncommitted work, survive the deploy.

        The staged row is neither committed by the deploy's commit nor
        discarded by it: still pending in the same session afterwards, and
        invisible to a connection of its own.
        """
        user_id = seed_user["user"].id
        pending = Category(user_id=user_id, group_name="X-cv", item_name="Held")
        db.session.add(pending)
        held = db.session()

        _INIT_DB.initialise_database()

        assert db.session() is held
        assert pending in held.new
        with db.engine.connect() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM budget.categories "
                "WHERE user_id = :u AND item_name = 'Held'"
            ), {"u": user_id}).scalar_one() == 0
        db.session.rollback()


class TestEveryStatementRunsOnTheDeploysConnection:
    """The migrations, ``ref_cache`` and the three hooks share ONE connection."""

    def test_one_backend_serves_the_migration_and_the_hooks(
        self, app, db, pending_migration, uncorrected_payment,
    ):
        """Every statement the deploy issues reaches the same PostgreSQL backend.

        Flask-SQLAlchemy's session ignores a session-level bind, so a deploy
        that only configured one would run the hooks on a pooled connection of
        their own, outside the transaction the migration ran in.  The backend
        of every statement is recorded, and the record must hold the probe
        migration's DDL and the loan hook's write, so it cannot pass by having
        seen neither.
        """
        statements = []

        def _record(conn, _cursor, statement, *_args):
            """Note which backend ran *statement*."""
            statements.append((
                conn.connection.dbapi_connection.get_backend_pid(), statement,
            ))

        event.listen(db.engine, "before_cursor_execute", _record)
        try:
            _INIT_DB.initialise_database()
        finally:
            event.remove(db.engine, "before_cursor_execute", _record)

        assert any("xcv_probe" in sql for _pid, sql in statements)
        assert any(
            sql.lstrip().upper().startswith("INSERT INTO BUDGET.JOURNAL_ENTRIES")
            for _pid, sql in statements
        )
        assert len({pid for pid, _sql in statements}) == 1
        assert _committed(db, uncorrected_payment)["corrections"] == 1


def _empty_the_database(db) -> None:
    """Make the per-test database FRESH: no application schema, no stamp.

    Drops the five application schemas and the stamp, then replays
    ``scripts/init_db.sql`` -- entrypoint step 2, which re-creates the empty
    schemas and an empty ``public.alembic_version`` -- so the database is
    exactly what step 3 meets on a first boot.  The per-test database is
    dropped after every test, so nothing outlives this.
    """
    db.session.execute(text(
        "DROP SCHEMA ref, auth, budget, salary, system CASCADE"
    ))
    db.session.execute(text("DROP TABLE public.alembic_version"))
    db.session.execute(text(
        (_REPO_ROOT / "scripts" / "init_db.sql").read_text(encoding="utf-8")
    ))
    db.session.commit()


def _fresh_state(db) -> dict:
    """Read what a fresh build has COMMITTED, over a connection of its own."""
    with db.engine.connect() as conn:
        return {
            "users_table": conn.execute(text(
                "SELECT to_regclass('auth.users') IS NOT NULL"
            )).scalar_one(),
            "stamps": conn.execute(text(
                "SELECT array_agg(version_num) FROM public.alembic_version"
            )).scalar_one(),
        }


class TestAFreshBuildIsOneTransaction:
    """The first-boot branch commits its schema and its stamp together, or neither."""

    def test_a_failure_before_the_stamp_leaves_the_database_empty(
        self, app, db, monkeypatch,
    ):
        """The stamp fails after every table was created: no table survives.

        Before X-cv each block committed as it went, so this failure left
        ``auth.users`` behind -- a database the next boot reads as "existing"
        and tries to migrate from an empty stamp.
        """
        _empty_the_database(db)

        def _fail(*_args, **_kwargs):
            """Stand in for a stamp that fails."""
            raise RuntimeError("forged stamp failure")

        monkeypatch.setattr(_INIT_DB, "stamp_head", _fail)
        with pytest.raises(RuntimeError, match="forged stamp failure"):
            _INIT_DB.initialise_database()

        assert _fresh_state(db) == {"users_table": False, "stamps": None}
        db.session.rollback()

    def test_a_clean_build_commits_the_schema_and_the_stamp_together(
        self, app, db,
    ):
        """The tables exist and the stamp names the head, both committed."""
        _empty_the_database(db)

        _INIT_DB.initialise_database()

        assert _fresh_state(db) == {
            "users_table": True, "stamps": [_real_head()],
        }


class TestTheDeployKeepsTheAppsLogging:
    """Migrating on the deploy's connection leaves the app's logging alone."""

    def test_migrating_on_the_shared_connection_leaves_app_loggers_enabled(
        self, app, db,
    ):
        """``env.py`` skips alembic.ini's ``fileConfig`` when handed a connection.

        ``fileConfig`` disables every logger that exists when it runs, so before
        X-cv every ``app.*`` logger in the deploy host went silent once the
        migrations had run -- the cash resync's skipped-transfer warning among
        them.  The root logger's handlers are asserted too: ``fileConfig``
        replaces them with alembic.ini's WARNING console.
        """
        app_logger = logging.getLogger("app.services.xcv_probe")
        root_handlers = list(logging.getLogger().handlers)

        with db.engine.connect() as connection:
            connection.begin()
            _INIT_DB.migrate_existing_database(connection)

        assert app_logger.disabled is False
        assert logging.getLogger().handlers == root_handlers
