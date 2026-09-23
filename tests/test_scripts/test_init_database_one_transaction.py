"""Entrypoint step 3 is ONE transaction, committed once (plan step balance:X-cv).

``scripts/init_database.py`` used to commit the migrations in
``migrations/env.py``'s own transaction BEFORE the three deploy hooks ran, so a
hook that refused left a stamp the previous image could not resolve and
``deploy/shekel-deploy.sh`` refused to re-pin it -- a manual dump restore
(ruling **R-BAL105**).  Now the migrations, ``ref_cache`` and the three hooks
share the connection ``db.session`` holds, and ``initialise_database`` is the
only commit.  These tests grade that from OUTSIDE the transaction: every
observation is read over a SEPARATE connection, which under READ COMMITTED sees
only what was committed, so a flush can never pass for a commit here.

**A real pending migration, not a stand-in.**  The per-test database is
already at head, so a no-op upgrade would move no stamp and prove nothing.
The ``pending_migration`` fixture builds a version directory holding every
real migration (symlinked) plus one probe revision on top of the real head
that creates ``budget.xcv_probe``, and points the script's Alembic config at
it -- so the stamp and a DDL change are really pending when the deploy runs.

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
from sqlalchemy import text
from sqlalchemy.exc import InternalError

from app import ref_cache
from app.enums import PostingKindEnum, PostingSourceEnum
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
    probe revision whose ``down_revision`` is the real head, and the script's
    ``Config`` is wrapped so its ``version_locations`` reads that directory.
    Everything else about the config -- ``alembic.ini``, ``env.py``, the shared
    connection -- is the script's own.
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
    real_config = _INIT_DB.Config

    def _config_reading_the_probe(path):
        """Build the script's config, reading versions from the probe dir."""
        config = real_config(path)
        config.set_main_option("version_locations", str(versions))
        return config

    monkeypatch.setattr(_INIT_DB, "Config", _config_reading_the_probe)
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
        # Plain ids read up front: the hook runs inside the deploy's own
        # transaction, where the fixtures' expired ORM rows would reload.
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


class TestTheTransactionCommittedIsTheOneOpened:
    """A rollback inside the sequence cannot let the hooks commit without the migrations."""

    def test_a_rollback_inside_the_sequence_commits_nothing(
        self, app, db, pending_migration, uncorrected_payment, monkeypatch,
    ):
        """The second hook rolls back first, then writes: the deploy refuses to commit.

        ``ref_cache`` rolls back on a missing ref table, and a rollback ends the
        deploy's transaction -- the migration with it -- while what follows
        runs on a new one.  Without the check, the one commit would land the
        loan correction WITHOUT the migration and stamp it was run against.
        """
        real_backfill = _INIT_DB.loan_posting_service.backfill_all_loan_postings

        def _rollback_then_backfill():
            """Discard the deploy's transaction, then run the real backfill."""
            db.session.rollback()
            return real_backfill()

        monkeypatch.setattr(
            _INIT_DB.loan_posting_service,
            "backfill_all_loan_postings", _rollback_then_backfill,
        )
        with pytest.raises(RuntimeError, match="ended before its one commit"):
            _INIT_DB.initialise_database()

        assert _committed(db, uncorrected_payment) == {
            "stamp": pending_migration, "probe": False, "corrections": 0,
        }
        db.session.rollback()


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

        monkeypatch.setattr(_INIT_DB.command, "stamp", _fail)
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

        _INIT_DB.migrate_existing_database()
        db.session.rollback()

        assert app_logger.disabled is False
        assert logging.getLogger().handlers == root_handlers
