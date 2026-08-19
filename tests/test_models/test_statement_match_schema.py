"""The match tables refuse what no writer should be trusted to avoid.

Plan step **bank_import:X-f6a-2**, rulings **R-FS** and **R-FV**.  Migration
``c1e7d4b3a850`` lands two tables whose whole job is to say that a set of bank
lines and a set of the app's own rows are ONE movement -- a claim the accept
door checks in Python and the schema must be able to hold independently.

**Every test here is a FIRING CONTROL** (``docs/plans/verification.md``
standard 4).  The door refuses each of these states before it writes, so
nothing in ordinary use reaches the database tier -- and a test that merely
asserted a constraint EXISTS would pass against one admitting everything.  Each
writes the state through the ORM, flushes, and asserts the refusal BY NAME,
which is the only tier that can see a future writer bypassing the door.

The shapes under test, and what each would cost if writable:

* **a member naming two subjects, or none** -- the exclusive arc.  A member
  naming a bank line AND a transaction would make "which side of the match is
  this" unanswerable, and every reader would have to guess;
* **a subject in two matches** -- one bank line explained twice, with both acts
  looking complete;
* **a member whose account disagrees with its act's, or with its subject's** --
  a match spanning two accounts, which is money booked against a statement that
  never showed it;
* **an act whose owner is not its account's owner**.

It also pins the two SUPERKEYS the composite keys target: they constrain
nothing on their own, so an arm that only tested a rejection could not see one
being dropped.
"""

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.audit_infrastructure import AUDITED_TABLES
from app.enums import StatementSourceEnum, StatusEnum, TxnTypeEnum
from app.models.account import Account
from app.models.ref import AccountType
from app.models.statement_import import BankStatementLine, StatementImport
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.services import account_service
from tests._test_helpers import load_migration_module

_MIGRATION = load_migration_module("c1e7d4b3a850_a_bank_line_is_this_row.py")


def _another_account(db, seed_user, name="Other Checking"):
    """Create a second cash account for the same owner.

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


def _a_line(db, seed_user, account=None, **overrides):
    """Stage and return one recorded bank line, with its import.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.
        account: The account it belongs to; the seeded checking one by default.
        **overrides: Line fields to replace.

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
        file_digest="b" * 64,
        period_start=date(2026, 3, 1),
        period_end=date(2026, 3, 31),
        line_count=1,
        recorded_count=1,
    )
    db.session.add(statement)
    db.session.flush()
    fields = {
        "account_id": account_id,
        "import_id": statement.id,
        "posted_on": date(2026, 3, 2),
        "transaction_on": date(2026, 3, 2),
        "amount": Decimal("-25.00"),
        "description": "POINT OF SALE DEBIT L340 COFFEE",
        "sequence_in_group": 0,
    }
    fields.update(overrides)
    line = BankStatementLine(**fields)
    db.session.add(line)
    db.session.flush()
    return line


def _a_match(db, seed_user, account=None):
    """Stage and return one match act.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.
        account: The account it is for; the seeded checking one by default.

    Returns:
        The staged :class:`~app.models.statement_match.StatementMatch`.
    """
    match = StatementMatch(
        account_id=(account or seed_user["account"]).id,
        user_id=seed_user["user"].id,
    )
    db.session.add(match)
    db.session.flush()
    return match


def _a_member(db, match, **overrides):
    """Stage and return one member of *match*.

    Args:
        db: The session fixture.
        match: The act it belongs to.
        **overrides: Member fields to replace.

    Returns:
        The staged
        :class:`~app.models.statement_match.StatementMatchMember`.
    """
    fields = {
        "match_id": match.id,
        "account_id": match.account_id,
        "bank_statement_line_id": None,
        "transaction_id": None,
        "transaction_entry_id": None,
    }
    fields.update(overrides)
    member = StatementMatchMember(**fields)
    db.session.add(member)
    db.session.flush()
    return member


class TestAMemberNamesExactlyOneSubject:
    """``ck_statement_match_members_one_subject`` -- the exclusive arc."""

    def test_naming_no_subject_is_refused(self, app, db, seed_user):
        """A membership of nothing is a row every reader has to skip."""
        match = _a_match(db, seed_user)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_member(db, match)

        assert "ck_statement_match_members_one_subject" in str(caught.value)

    def test_naming_two_subjects_is_refused(self, app, db, seed_user):
        """A member on both sides makes the match's two halves unreadable."""
        match = _a_match(db, seed_user)
        line = _a_line(db, seed_user)
        transaction = _a_transaction(db, seed_user)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_member(
                db, match,
                bank_statement_line_id=line.id,
                transaction_id=transaction.id,
            )

        assert "ck_statement_match_members_one_subject" in str(caught.value)

    def test_naming_one_subject_is_accepted(self, app, db, seed_user):
        """The control: the arm admits the only shape a member may take."""
        match = _a_match(db, seed_user)
        line = _a_line(db, seed_user)

        _a_member(db, match, bank_statement_line_id=line.id)

        assert db.session.query(StatementMatchMember).count() == 1


def _a_transaction(db, seed_user, name="Electricity"):
    """Stage and return one transaction on the seeded checking account.

    **``seed_user`` creates NO transaction at all**, which is a trap this arc
    has already paid for once: a test in the previous leaf compared ``[] == []``
    and reported it as proof that nothing moved.  So these tests build their
    own row rather than looking one up.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.
        name: The row's name, unique per template here.

    Returns:
        The staged :class:`~app.models.transaction.Transaction`.  These are
        SCHEMA tests, about the keys rather than about what the row is worth,
        so a plain projected expense is all they need.
    """
    type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        account_id=seed_user["account"].id,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=type_id,
        name=name,
        default_amount=Decimal("25.00"),
        is_envelope=False,
    )
    db.session.add(template)
    db.session.flush()
    txn = Transaction(
        template_id=template.id,
        pay_period_id=seed_user["bootstrap_period"].id,
        scenario_id=seed_user["scenario"].id,
        account_id=seed_user["account"].id,
        status_id=ref_cache.status_id(StatusEnum.PROJECTED),
        name=name,
        category_id=seed_user["categories"]["Groceries"].id,
        transaction_type_id=type_id,
        estimated_amount=Decimal("25.00"),
    )
    db.session.add(txn)
    db.session.flush()
    return txn


class TestASubjectBelongsToAtMostOneMatch:
    """The three partial unique indexes."""

    def test_one_line_in_two_matches_is_refused(self, app, db, seed_user):
        """One bank line explained twice, with both acts looking complete."""
        line = _a_line(db, seed_user)
        _a_member(db, _a_match(db, seed_user), bank_statement_line_id=line.id)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_member(
                db, _a_match(db, seed_user), bank_statement_line_id=line.id,
            )

        assert "uq_statement_match_members_line" in str(caught.value)

    def test_one_transaction_in_two_matches_is_refused(
        self, app, db, seed_user,
    ):
        """One app row claimed by two statements."""
        transaction = _a_transaction(db, seed_user)
        _a_member(
            db, _a_match(db, seed_user), transaction_id=transaction.id,
        )

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_member(
                db, _a_match(db, seed_user), transaction_id=transaction.id,
            )

        assert "uq_statement_match_members_transaction" in str(caught.value)

    def test_two_members_naming_no_line_do_not_collide(
        self, app, db, seed_user,
    ):
        """The index is PARTIAL, and this is what that buys.

        Every member leaves two of the three columns NULL, so a non-partial
        index would let the FIRST member of the second match collide with the
        first member of the first on a column neither of them names.
        """
        match = _a_match(db, seed_user)
        transaction = _a_transaction(db, seed_user)
        line = _a_line(db, seed_user)

        _a_member(db, match, transaction_id=transaction.id)
        _a_member(db, match, bank_statement_line_id=line.id)

        assert db.session.query(StatementMatchMember).count() == 2


class TestAMatchCannotSpanTwoAccounts:
    """The composite foreign keys, which is what makes the scope structural."""

    def test_a_member_on_another_account_than_its_act_is_refused(
        self, app, db, seed_user,
    ):
        """A membership filed under an account its act does not name."""
        match = _a_match(db, seed_user)
        other = _another_account(db, seed_user)
        line = _a_line(db, seed_user, account=other)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_member(
                db, match,
                account_id=other.id,
                bank_statement_line_id=line.id,
            )

        assert "fk_statement_match_members_match_account" in str(caught.value)

    def test_a_member_naming_another_accounts_line_is_refused(
        self, app, db, seed_user,
    ):
        """The other direction: this account's act, that account's line.

        This is the arm a single-column foreign key could not express, and it
        is the one that matters -- it is how one bank's statement gets booked
        against another account's balance.
        """
        match = _a_match(db, seed_user)
        other = _another_account(db, seed_user, name="Third Checking")
        line = _a_line(db, seed_user, account=other)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_member(db, match, bank_statement_line_id=line.id)

        assert "fk_statement_match_members_line_account" in str(caught.value)


class TestTheSuperkeysExist:
    """The UNIQUE constraints the composite keys target.

    They constrain nothing on their own -- ``id`` is already the primary key on
    both tables -- so no rejection test can see one being dropped.  These pin
    them by NAME, which is what makes the composite keys above buildable.
    """

    @pytest.mark.parametrize("table, constraint", [
        ("bank_statement_lines", "uq_bank_statement_lines_id_account"),
        ("transaction_entries", "uq_transaction_entries_id_account"),
        ("statement_matches", "uq_statement_matches_id_account"),
    ])
    def test_the_superkey_is_present(self, app, db, table, constraint):
        """Each composite key's target exists under the name it is keyed on."""
        found = db.session.execute(
            db.text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = 'budget' AND t.relname = :table "
                "AND c.conname = :constraint AND c.contype = 'u'"
            ),
            {"table": table, "constraint": constraint},
        ).scalar()
        assert found == 1, f"{constraint} is missing from budget.{table}"


class TestTheMigrationAndTheAuditListAgree:
    """The migration's own list of new audited tables is not a second copy."""

    def test_every_new_table_is_audited(self, app, db):
        """A table the migration attaches a trigger to must be listed.

        The migration names its new tables in ``_AUDITED_NEW_TABLES`` and
        ``app.audit_infrastructure`` names every audited table; two lists that
        must agree are two lists that can drift, so the agreement is a test
        rather than a convention.
        """
        audited = {name for schema, name in AUDITED_TABLES if schema == "budget"}
        assert set(_MIGRATION._AUDITED_NEW_TABLES) <= audited

    def test_every_new_table_carries_its_trigger(self, app, db):
        """The trigger is attached, under the name the health check enumerates."""
        for table in _MIGRATION._AUDITED_NEW_TABLES:
            found = db.session.execute(
                db.text(
                    "SELECT 1 FROM pg_trigger g "
                    "JOIN pg_class t ON t.oid = g.tgrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE n.nspname = 'budget' AND t.relname = :table "
                    "AND g.tgname = :trigger"
                ),
                {"table": table, "trigger": f"audit_{table}"},
            ).scalar()
            assert found == 1, f"budget.{table} carries no audit trigger"


class TestAnActsOwnerIsItsAccountsOwner:
    """``fk_statement_matches_owner`` -- the co-located key."""

    def test_an_act_naming_another_user_is_refused(self, app, db, seed_user):
        """A match recorded against an account its claimed owner does not own."""
        from app.models.user import User  # local: schema test only
        from app.services import auth_service

        stranger = User(
            email="matchstranger@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger",
        )
        db.session.add(stranger)
        db.session.flush()

        db.session.add(StatementMatch(
            account_id=seed_user["account"].id, user_id=stranger.id,
        ))
        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            db.session.flush()

        assert "fk_statement_matches_owner" in str(caught.value)
