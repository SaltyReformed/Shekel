"""The statement tables refuse what no writer should be trusted to avoid.

Plan step **bank_import:X-f6a-1**, ruling **R-FP**.  Migration
``3f408018a71c`` lands four tables whose whole job is to hold a record the app
did not author, and the constraints below are what make that record trustworthy
independently of the import door.

**Every test here is a FIRING CONTROL** (``docs/plans/verification.md`` standard
4).  The door already refuses each of these states in Python, so nothing in
ordinary use reaches the database tier -- and a test that merely asserted a
constraint EXISTS would pass against one that admitted everything.  Each test
writes the state through the ORM, flushes, and asserts the refusal BY NAME, which
is the only tier that can see a future writer bypassing the door.

The shapes under test, and what each would cost if writable:

* **a duplicated line identity** -- the same bank line recorded twice, which is
  double-counted money the moment the next leaf matches it;
* **two lines claiming one external id** -- a source's own id corroborates
  identity, and two rows claiming it would make the corroboration meaningless;
* **a line whose account disagrees with its import's** -- one bank's statement
  filed under another account, the defect the composite key exists to prevent;
* **the CHECKs** on counts and periods, each of which encodes a sentence the
  door states in Python and the schema must state independently.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import StatementSourceEnum
from app.models.statement_import import (
    AccountExternalIdentity,
    BankStatementLine,
    StatementImport,
)
from app.models.account import Account
from app.models.ref import AccountType, StatementSource
from app.services import account_service
from tests._test_helpers import load_migration_module

_MIGRATION = load_migration_module("3f408018a71c_the_bank_says_what_happened.py")


def _another_account(db, seed_user, name="Other Checking"):
    """Create a second cash account for the same owner.

    Anchored on the seeded bootstrap period's own day: ``create_account``
    refuses an assertion earlier than the owner's recorded history, so a
    hard-coded date would couple this helper to whichever period fixture the
    calling test happens to use.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.
        name: The new account's name (unique per user).

    Returns:
        The created :class:`~app.models.account.Account`.
    """
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name=name,
            anchor_balance=Decimal("0.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
    )
    db.session.flush()
    return account


def _an_import(db, seed_user, **overrides):
    """Stage and return one statement import for the seeded checking account."""
    fields = {
        "account_id": seed_user["account"].id,
        "user_id": seed_user["user"].id,
        "source_id": ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        "file_name": "statement.csv",
        "file_digest": "a" * 64,
        "period_start": date(2026, 3, 1),
        "period_end": date(2026, 3, 31),
        "line_count": 2,
        "recorded_count": 2,
    }
    fields.update(overrides)
    row = StatementImport(**fields)
    db.session.add(row)
    db.session.flush()
    return row


def _a_line(db, statement, **overrides):
    """Stage and return one recorded line under *statement*."""
    fields = {
        "account_id": statement.account_id,
        "import_id": statement.id,
        "posted_on": date(2026, 3, 2),
        "transaction_on": date(2026, 3, 2),
        "amount": Decimal("-25.00"),
        "description": "POINT OF SALE DEBIT L340 COFFEE",
        "sequence_in_group": 0,
    }
    fields.update(overrides)
    row = BankStatementLine(**fields)
    db.session.add(row)
    db.session.flush()
    return row


class TestALineIsUniqueByItsIdentity:
    """``uq_bank_statement_lines_identity`` -- the anti-duplicate key."""

    def test_the_same_identity_twice_is_refused(self, app, db, seed_user):
        """Re-recording one bank line is double-counted money downstream."""
        statement = _an_import(db, seed_user)
        _a_line(db, statement)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_line(db, statement)

        assert "uq_bank_statement_lines_identity" in str(caught.value)

    def test_a_different_ORDINAL_is_accepted(self, app, db, seed_user):
        """Two genuinely distinct charges sharing a day and an amount.

        The key must admit this: refusing it would silently drop real money,
        which is a worse failure than the duplicate the key exists to stop.
        """
        statement = _an_import(db, seed_user)
        _a_line(db, statement, sequence_in_group=0)

        _a_line(db, statement, sequence_in_group=1)

        assert db.session.query(BankStatementLine).count() == 2

    def test_a_different_DAY_is_accepted(self, app, db, seed_user):
        """Same amount, different day, ordinal 0 on both."""
        statement = _an_import(db, seed_user)
        _a_line(db, statement, posted_on=date(2026, 3, 2))

        _a_line(db, statement, posted_on=date(2026, 3, 3))

        assert db.session.query(BankStatementLine).count() == 2

    def test_a_different_AMOUNT_is_accepted(self, app, db, seed_user):
        """Same day, different amount."""
        statement = _an_import(db, seed_user)
        _a_line(db, statement, amount=Decimal("-25.00"))

        _a_line(db, statement, amount=Decimal("-26.00"))

        assert db.session.query(BankStatementLine).count() == 2

    def test_the_same_identity_under_ANOTHER_account_is_accepted(
        self, app, db, seed_user,
    ):
        """Two accounts may each show a $25 debit on one day.

        The key is per account, and a key that was not would make one
        account's statement refuse another's ordinary line.
        """
        other = _another_account(db, seed_user)
        _a_line(db, _an_import(db, seed_user))

        _a_line(db, _an_import(db, seed_user, account_id=other.id))

        assert db.session.query(BankStatementLine).count() == 2


class TestAnExternalIdIsClaimedAtMostOnce:
    """``uq_bank_statement_lines_external_id`` -- the partial unique index."""

    def test_two_lines_claiming_one_external_id_are_refused(
        self, app, db, seed_user,
    ):
        """An id that two rows can claim corroborates nothing."""
        statement = _an_import(db, seed_user)
        _a_line(db, statement, external_id="FIT-1")

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_line(db, statement, posted_on=date(2026, 3, 9),
                    external_id="FIT-1")

        assert "uq_bank_statement_lines_external_id" in str(caught.value)

    def test_many_lines_with_NO_external_id_are_accepted(
        self, app, db, seed_user,
    ):
        """The index is PARTIAL, and every SECU CSV line is in this state.

        A non-partial unique index would refuse the second line of every
        import from a source that has no ids -- which is the source this leaf
        actually ships.
        """
        statement = _an_import(db, seed_user)
        _a_line(db, statement, posted_on=date(2026, 3, 2))
        _a_line(db, statement, posted_on=date(2026, 3, 3))

        _a_line(db, statement, posted_on=date(2026, 3, 4))

        assert db.session.query(BankStatementLine).count() == 3


class TestALinesAccountIsItsImports:
    """``fk_bank_statement_lines_import_account`` -- agreement, not a copy."""

    def test_a_disagreeing_account_is_refused(
        self, app, db, seed_user,
    ):
        """One bank's statement filed under another account.

        The composite key makes the disagreement unrepresentable rather than
        merely untested -- the same construction
        ``fk_transaction_entries_parent_account`` uses one table over.
        """
        statement = _an_import(db, seed_user)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_line(db, statement, account_id=seed_user["account"].id + 999)

        assert "fk_bank_statement_lines_import_account" in str(caught.value)

    def test_deleting_the_account_takes_its_imports_and_lines(
        self, app, db, seed_user,
    ):
        """The CASCADE chain: accounts -> imports -> lines.

        Without it, hard-deleting an account would be refused by a statement
        line nobody could reach to remove.
        """
        statement = _an_import(db, seed_user)
        _a_line(db, statement)
        db.session.flush()

        db.session.execute(
            sqlalchemy.text("DELETE FROM budget.accounts WHERE id = :i"),
            {"i": seed_user["account"].id},
        )

        assert db.session.query(BankStatementLine).count() == 0
        assert db.session.query(StatementImport).count() == 0


class TestTheImportsOwnCheckConstraints:
    """Each encodes a sentence the door also states in Python."""

    def test_a_period_ending_before_it_starts_is_refused(
        self, app, db, seed_user,
    ):
        """A span cannot run backwards."""
        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _an_import(
                db, seed_user,
                period_start=date(2026, 3, 31), period_end=date(2026, 3, 1),
            )

        assert "ck_statement_imports_period_ordered" in str(caught.value)

    def test_an_import_of_zero_lines_is_refused(self, app, db, seed_user):
        """A parse that found nothing is not an import."""
        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _an_import(db, seed_user, line_count=0, recorded_count=0)

        assert "ck_statement_imports_line_count_positive" in str(caught.value)

    def test_recording_more_lines_than_the_file_held_is_refused(
        self, app, db, seed_user,
    ):
        """Writing lines the file did not contain is the shape that would make
        ``already_known`` negative."""
        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _an_import(db, seed_user, line_count=2, recorded_count=3)

        assert "ck_statement_imports_recorded_within_file" in str(caught.value)

    def test_recording_FEWER_lines_than_the_file_held_is_accepted(
        self, app, db, seed_user,
    ):
        """The overlap case, which is every import after the first."""
        row = _an_import(db, seed_user, line_count=306, recorded_count=19)

        assert row.id is not None

    def test_a_negative_sequence_is_refused(self, app, db, seed_user):
        """The ordinal counts from zero."""
        statement = _an_import(db, seed_user)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_line(db, statement, sequence_in_group=-1)

        assert "ck_bank_statement_lines_sequence_non_negative" in str(
            caught.value
        )


class TestALineMustMoveMoney:
    """``ck_bank_statement_lines_amount_real_nonzero``, which also blocks NaN."""

    def test_a_zero_amount_is_refused(self, app, db, seed_user):
        """A line that moves nothing is not a movement."""
        statement = _an_import(db, seed_user)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_line(db, statement, amount=Decimal("0.00"))

        assert "ck_bank_statement_lines_amount_real_nonzero" in str(caught.value)

    def test_a_NaN_amount_is_refused_by_the_DATABASE(self, app, db, seed_user):
        """The second barrier behind the parser's finiteness check.

        PostgreSQL's ``numeric`` accepts ``NaN`` happily and orders it ABOVE
        every real number, so ``NaN <> 0`` is TRUE -- the obvious constraint
        admits it, which is exactly what the first draft of this one did.  The
        ``< 'NaN'`` term is what refuses it.  It matters because a NaN amount
        is invisible to every equality-based matcher and makes the account's
        page unrenderable.
        """
        statement = _an_import(db, seed_user)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_line(db, statement, amount=Decimal("NaN"))

        assert "ck_bank_statement_lines_amount_real_nonzero" in str(caught.value)


class TestTheAccountMappingIsUniqueBothWays:
    """Two constraints, and each refuses a different real mistake."""

    def _identity(self, db, account_id, external, user_id=None, **overrides):
        """Stage one external identity."""
        fields = {
            "account_id": account_id,
            "user_id": user_id,
            "source_id": ref_cache.statement_source_id(
                StatementSourceEnum.SECU_CHECKING_CSV
            ),
            "external_account_id": external,
        }
        fields.update(overrides)
        row = AccountExternalIdentity(**fields)
        db.session.add(row)
        db.session.flush()
        return row

    def test_one_external_account_maps_to_one_of_an_owners_accounts(
        self, app, db, seed_user,
    ):
        """Importing one bank statement under two accounts is refused."""
        owner = seed_user["user"].id
        other = _another_account(db, seed_user)
        self._identity(db, seed_user["account"].id, "******3820", owner)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            self._identity(db, other.id, "******3820", owner)

        assert "uq_account_external_identities_owner_source_account" in str(
            caught.value
        )

    def test_ANOTHER_OWNER_may_hold_the_same_masked_number(
        self, app, db, seed_user,
    ):
        """The scope is per owner, and a global key would be a lockout.

        This adapter's identifier is SECU's MASK, a 10,000-value space, so two
        owners at one credit union collide on the last four digits with
        probability 1/10,000 per pair.  Under a global key the loser could
        never import their own statements, and the refusal would disclose that
        some other account in the system held their number.
        """
        from app.models.user import User
        from app.services import auth_service

        stranger = User(
            email="stranger@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger",
        )
        db.session.add(stranger)
        db.session.flush()
        their_account = Account(
            user_id=stranger.id,
            account_type_id=seed_user["account"].account_type_id,
            name="Their Checking",
        )
        db.session.add(their_account)
        db.session.flush()
        self._identity(
            db, seed_user["account"].id, "******3820", seed_user["user"].id,
        )

        self._identity(
            db, their_account.id, "******3820", stranger.id,
        )

        assert db.session.query(AccountExternalIdentity).count() == 2

    def test_an_identity_whose_owner_is_not_its_accounts_is_refused(
        self, app, db, seed_user,
    ):
        """``fk_account_external_identities_owner`` -- agreement, not a copy.

        Without it, ``user_id`` would be a column a writer could set wrong, and
        the owner-scoped uniqueness above would be scoped by a lie.
        """
        from app.models.user import User
        from app.services import auth_service

        stranger = User(
            email="other-owner@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Other Owner",
        )
        db.session.add(stranger)
        db.session.flush()

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            self._identity(
                db, seed_user["account"].id, "******3820", stranger.id,
            )

        assert "fk_account_external_identities_owner" in str(caught.value)

    def test_one_account_has_one_identity_per_source(
        self, app, db, seed_user,
    ):
        """"What does this source call this account" has ONE answer."""
        owner = seed_user["user"].id
        self._identity(db, seed_user["account"].id, "******3820", owner)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            self._identity(db, seed_user["account"].id, "******9999", owner)

        assert "uq_account_external_identities_account_source" in str(
            caught.value
        )


class TestTheSeededAdapterCatalogue:
    """The ref row the migration writes, asserted against the migration's own
    SQL rather than a hand-copied twin."""

    def test_the_migration_seeds_the_ENUM_MEMBERS_own_value(self):
        """The invariant, not a literal compared to a literal in one repo.

        ``available_sources`` looks the label up by ``member.value`` and
        ``ref_cache`` resolves the id by name, so the seeded name MUST be the
        enum's value -- and asserting the string against itself, which is what
        this test did first, could not fail for any reason related to that.
        """
        assert (
            StatementSourceEnum.SECU_CHECKING_CSV.value
            in _MIGRATION.SEED_SOURCES_SQL
        )

    def test_the_seeded_row_exists_and_is_labelled(self, app, db):
        """A source with no label cannot be offered on the upload form."""
        row = (
            db.session.query(StatementSource)
            .filter_by(name="secu_checking_csv")
            .one()
        )

        assert row.name == StatementSourceEnum.SECU_CHECKING_CSV.value
        assert row.display_name

    def test_every_enum_member_resolves_through_the_cache(self, app, db):
        """IDs for logic, strings for display -- the project-wide invariant."""
        for member in StatementSourceEnum:
            assert isinstance(ref_cache.statement_source_id(member), int)
