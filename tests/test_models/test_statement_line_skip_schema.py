"""The skip table refuses what no writer should be trusted to avoid.

Plan step ``bank_import:X-gj-4a``, ruling **bank_import:R-JG**.  Migration
``bba3bd6a6c44`` lands ``budget.statement_line_skips``: the record that the
owner has decided one bank line explains nothing they budget for.

**Every test here is a FIRING CONTROL.**  The door
(:func:`~app.services.statement_match.skip_line`) refuses each of these states
before it writes, so nothing in ordinary use reaches the database tier -- and a
test that merely asserted a constraint EXISTS would pass against one admitting
everything.  Each writes the state through the ORM, flushes, and asserts the
refusal BY NAME, which is the only tier that can see a future writer bypassing
the door.

The shapes under test, and what each would cost if writable:

* **one line skipped twice** -- two records of one decision, so undoing the
  skip would leave the line still skipped and the owner unable to see why;
* **a skip whose account is not its line's** -- one account's decision filed
  against another's statement, which is what the composite key exists to make
  unrepresentable rather than merely unwritten;
* **a skip whose owner is not its account's owner** -- a decision attributed to
  someone who does not hold the account;
* **a skip surviving its own line** -- the CASCADE, which is what lets the
  import-repair door delete an import at all.  Asserted in the other direction
  from the three above: the state is unreachable because the database removes
  the row, not because it refuses one.

It also pins the audit registration, because a decision the owner made about
money the bank moved is destroyed by an ordinary DELETE and
``system.audit_log`` is the only thing that keeps it.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from app.enums import StatementSourceEnum
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.statement_line_skip import StatementLineSkip
from tests._test_helpers import load_migration_module

_MIGRATION = load_migration_module(
    "bba3bd6a6c44_a_skipped_bank_line_is_a_recorded_act.py",
)


def _a_line(db, seed_user, account=None):
    """Stage and return one recorded bank line, with its import.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.
        account: The account it belongs to; the seeded checking one by
            default.

    Returns:
        The staged :class:`~app.models.statement_import.BankStatementLine`.
    """
    account_id = (account or seed_user["account"]).id
    statement = StatementImport(
        account_id=account_id,
        user_id=seed_user["user"].id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name="statement.csv",
        file_digest="c" * 64,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        line_count=1,
        recorded_count=1,
    )
    db.session.add(statement)
    db.session.flush()
    line = BankStatementLine(
        account_id=account_id,
        import_id=statement.id,
        posted_on=date(2026, 3, 2),
        amount=Decimal("-25.00"),
        description="POINT OF SALE DEBIT L340 COFFEE",
        sequence_in_group=0,
    )
    db.session.add(line)
    db.session.flush()
    return line


def _a_skip(seed_user, line, **overrides):
    """Return an unsaved skip of *line*, with fields replaceable.

    Args:
        seed_user: The seeded user bundle.
        line: The bank line it disposes of.
        **overrides: Column values to replace.

    Returns:
        The unsaved
        :class:`~app.models.statement_line_skip.StatementLineSkip`.
    """
    fields = {
        "bank_statement_line_id": line.id,
        "account_id": line.account_id,
        "user_id": seed_user["user"].id,
    }
    fields.update(overrides)
    return StatementLineSkip(**fields)


class TestOneLineCarriesOneDecision:
    """``uq_statement_line_skips_line``, and it is not a nicety."""

    def test_a_line_may_be_skipped_once(self, app, db, seed_user):
        """The ordinary case, so the refusals below are about the second row."""
        line = _a_line(db, seed_user)
        db.session.add(_a_skip(seed_user, line))
        db.session.flush()

        assert db.session.query(StatementLineSkip).count() == 1

    def test_a_second_skip_of_the_same_line_is_refused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: two records of one decision.

        Undoing a skip deletes ONE row, so a line carrying two would still be
        skipped afterwards -- and the Reconcile page would show it nowhere,
        because it is neither in the pass nor on the tab it was removed from.
        """
        line = _a_line(db, seed_user)
        db.session.add(_a_skip(seed_user, line))
        db.session.flush()
        db.session.add(_a_skip(seed_user, line))

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            db.session.flush()

        assert "uq_statement_line_skips_line" in str(caught.value)


class TestASkipBelongsToItsLinesAccount:
    """``fk_statement_line_skips_line_account``, the co-located key."""

    def test_a_skip_naming_another_accounts_line_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """FIRING CONTROL: one account's decision on another's statement.

        The pair ``(bank_statement_line_id, account_id)`` is checked against
        ``uq_bank_statement_lines_id_account``, so the account cannot be a copy
        that drifts from the line's -- which is what would let a skip hide a
        line from an account that never showed it.
        """
        line = _a_line(db, seed_user)
        db.session.add(
            _a_skip(
                seed_user, line,
                account_id=seed_second_user["account"].id,
                user_id=seed_second_user["user"].id,
            ),
        )

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            db.session.flush()

        assert "fk_statement_line_skips_line_account" in str(caught.value)


class TestASkipsOwnerIsItsAccountsOwner:
    """``fk_statement_line_skips_owner``, keyed onto ``uq_accounts_id_user``."""

    def test_a_skip_attributed_to_a_non_owner_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """FIRING CONTROL: a decision credited to someone who does not hold it.

        ``user_id`` records WHO decided, and the pair key is what makes that
        answer true by construction rather than by the door remembering to
        pass the right one.
        """
        line = _a_line(db, seed_user)
        db.session.add(
            _a_skip(seed_user, line, user_id=seed_second_user["user"].id),
        )

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            db.session.flush()

        assert "fk_statement_line_skips_owner" in str(caught.value)


class TestASkipCannotOutliveItsLine:
    """The CASCADE, asserted as a state that CANNOT ARISE.

    The three classes above assert refusals.  This one asserts the opposite
    arm and is the reason ``ON DELETE CASCADE`` is right here where
    ``fk_statement_match_members_line_account`` deliberately takes NO ACTION: a
    match that has lost its lines still claims app rows and reports them as
    explained, while a skip claims nothing but its own line -- so a skip with
    no line is no record at all, and refusing to remove one would block the
    import-repair door ``delete_import`` exists to be.
    """

    def test_deleting_the_line_takes_its_skip(self, app, db, seed_user):
        """The line goes and the skip goes with it, in one statement."""
        line = _a_line(db, seed_user)
        db.session.add(_a_skip(seed_user, line))
        db.session.flush()

        db.session.delete(line)
        db.session.flush()

        assert db.session.query(StatementLineSkip).count() == 0

    def test_deleting_the_import_takes_the_skip_too(self, app, db, seed_user):
        """The cascade the repair door actually drives.

        ``delete_import`` removes the IMPORT; the lines go by
        ``fk_bank_statement_lines_import_account`` and the skips by this one,
        so the chain is two links long and neither is exercised by the case
        above.
        """
        line = _a_line(db, seed_user)
        db.session.add(_a_skip(seed_user, line))
        db.session.flush()
        statement = db.session.get(StatementImport, line.import_id)

        db.session.delete(statement)
        db.session.flush()

        assert db.session.query(StatementLineSkip).count() == 0


class TestTheDecisionIsAudited:
    """A skip is destroyed by an ordinary DELETE, so the trail is the record."""

    def test_the_table_is_in_the_audited_list(self, app, db):
        """``app.audit_infrastructure.AUDITED_TABLES`` names it.

        The whole argument for deleting a skip rather than appending an
        "unskipped" row (ruling **R-JG**) rests on this: the forensic record is
        infrastructure that already exists.  If the table were not audited, an
        undo would destroy the decision with nothing recording that it was ever
        taken, and the ruling's reasoning would be false.
        """
        assert ("budget", "statement_line_skips") in AUDITED_TABLES

    def test_the_migration_and_the_audit_list_agree(self, app, db):
        """Two lists that must agree are two lists that can drift."""
        audited = {
            name for schema, name in AUDITED_TABLES if schema == "budget"
        }
        assert _MIGRATION._AUDITED_NEW_TABLE in audited

    def test_the_table_carries_its_trigger(self, app, db):
        """Under the name the entrypoint health check enumerates."""
        found = db.session.execute(
            db.text(
                "SELECT 1 FROM pg_trigger g "
                "JOIN pg_class t ON t.oid = g.tgrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = 'budget' AND t.relname = :table "
                "AND g.tgname = :trigger"
            ),
            {
                "table": _MIGRATION._AUDITED_NEW_TABLE,
                "trigger": f"audit_{_MIGRATION._AUDITED_NEW_TABLE}",
            },
        ).scalar()

        assert found == 1

    def test_deleting_a_skip_writes_the_whole_row_to_the_audit_log(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for ruling R-JG's own argument.

        The ruling says an append-only table is unnecessary BECAUSE the audit
        trigger keeps the deleted row.  That is a claim about the database, so
        it is measured rather than asserted: skip a line, undo it, and read the
        DELETE row back with its whole payload.
        """
        line = _a_line(db, seed_user)
        skip = _a_skip(seed_user, line)
        db.session.add(skip)
        db.session.flush()
        skip_id = skip.id

        db.session.delete(skip)
        db.session.flush()

        old = db.session.execute(
            db.text(
                "SELECT old_data FROM system.audit_log "
                "WHERE table_name = 'statement_line_skips' "
                "AND operation = 'DELETE' AND row_id = :row_id"
            ),
            {"row_id": skip_id},
        ).scalar()
        assert old is not None
        assert old["bank_statement_line_id"] == line.id
        assert old["user_id"] == seed_user["user"].id


class TestTheColumnsAreWhatTheRulingSaid:
    """The absences are decisions, so they are pinned like the presences.

    Ruling **R-JH**: no ``applied_by_rule``, because a standing *never a
    purchase* answer bars the create door and files no skip -- the column would
    be ``false`` on every row that can exist.  The locked direction gives the
    Skipped tab "the same card with Undo" and no free text, so no reason
    column either.  A later leaf adding one silently would be adding a fact
    nobody ruled on.
    """

    def test_it_carries_exactly_the_five_columns_ruled(self, app, db):
        """Named rather than counted, so a rename is as visible as an add."""
        assert {
            column.name for column in StatementLineSkip.__table__.columns
        } == {
            "id",
            "bank_statement_line_id",
            "account_id",
            "user_id",
            "created_at",
        }

    def test_every_column_is_NOT_NULL_in_the_DATABASE(self, app, db):
        """The premise :func:`~._undisposed.skipped` rests on, asked of the DB.

        **That reader carries no ``IS NOT NULL`` guard where its sibling
        :func:`~._undisposed._spoken_for` does**, and the asymmetry is only
        safe because this column cannot be NULL.  It is not a style point: in
        SQL, ``x NOT IN (subquery containing NULL)`` is NULL for every row, so
        ONE null here would make
        :func:`~._undisposed.undisposed` admit nothing -- an empty Reconcile
        inbox and a grid badge of 0 for every account, with no error anywhere
        and a real ``off_by`` still on the hero.

        So the premise is graded rather than assumed, and it is asked of
        ``information_schema`` rather than of the model: a migration relaxing
        the column, or a drift between ``db.create_all()`` and the chain, is
        exactly what a model-only assertion cannot see.  Named by adversarial
        security review 2026-09-02.
        """
        nullable = db.session.execute(
            db.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "AND table_name = 'statement_line_skips' "
                "AND is_nullable = 'YES'"
            ),
        ).scalars().all()

        assert nullable == []
