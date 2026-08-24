"""The match tables refuse what no writer should be trusted to avoid.

Plan step **bank_import:X-f6a-2**, rulings **R-FS** and **R-FV**.  Migration
``c1e7d4b3a850`` lands two tables whose whole job is to say that a set of bank
lines and a set of the app's own rows are ONE movement -- a claim the accept
door checks in Python and the schema must be able to hold independently.
Migration ``d1a4f7c9e620`` lands the THIRD (plan step ``bank_import:X-f6f``,
ruling **R-GG**): what an act brought into EXISTENCE, which is not the same set
as what it names, and which the undo has to be able to find.

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
* **an act whose owner is not its account's owner**;
* **a creation naming two subjects, or none, or a subject a second act also
  claims to have made** -- the same three shapes on the creations relation,
  where a subject minted twice would be offered for removal twice and the
  second undo would find it gone.

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
from app.models.statement_match import (
    StatementMatch,
    StatementMatchCreation,
    StatementMatchMember,
)
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.models.transaction_template import TransactionTemplate
from app.services import account_service
from app.services.statement_match import matched_subjects
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


def _rows(db, table, row_id, column="id", value=None):
    """Return how many rows the DATABASE holds, bypassing the identity map.

    A raw ``DELETE`` leaves the ORM session's identity map untouched, so
    ``session.get`` answers from memory and a delete assertion written that way
    passes whether the row went or not -- the shape
    ``tests/test_services/test_statement_match`` has already paid for once.

    Args:
        db: The session fixture.
        table: The schema-qualified table name.
        row_id: The id to look for, when *column* is the default.
        column: The column to filter on.
        value: The value for a non-default *column*.

    Returns:
        The row count.
    """
    return db.session.execute(
        db.text(f"SELECT count(*) FROM {table} WHERE {column} = :value"),
        {"value": row_id if value is None else value},
    ).scalar()


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


class TestAMatchMayNotLoseItsBankLines:
    """Plan step **bank_import:X-f6a-4**: the asymmetry is the whole point.

    Losing APP ROWS is visible -- ``AcceptedGroup.agrees`` asks whether any row
    is left before it asks anything else -- so those keys still CASCADE, and
    refusing there would refuse an ordinary delete because of a record the user
    cannot see from the row they are deleting.

    Losing BANK LINES was not visible.  A match with no line asserts nothing
    about a bank, so ``accepted_groups`` cannot render it and no release button
    ever exists for it, while ``matched_subjects`` reads the member rows
    directly and goes on reporting its transactions as already matched -- which
    takes those rows out of every future proposal, permanently and invisibly.
    MEASURED on a production clone 2026-08-20, before the constraint changed:
    deleting one import took 361 lines and left the act standing with 0 line
    members and 1 transaction member.
    """

    def test_deleting_a_line_a_match_names_is_refused(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL for the ``NO ACTION`` key.

        The undo door releases the match first, so nothing in ordinary use
        reaches this -- which is exactly why it is asserted at the database
        tier, the only place that can see a future writer skipping that door.
        """
        line = _a_line(db, seed_user)
        match = _a_match(db, seed_user)
        _a_member(db, match, bank_statement_line_id=line.id)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            db.session.execute(db.text(
                "DELETE FROM budget.bank_statement_lines WHERE id = :id"
            ), {"id": line.id})
            db.session.flush()

        assert "fk_statement_match_members_line_account" in str(caught.value)

    def test_deleting_the_IMPORT_of_a_matched_line_is_refused(
        self, app, db, seed_user,
    ):
        """The cascade from the import reaches the same refusal.

        This is the path the repair door takes, and the one the measurement
        above walked: without the constraint the import delete succeeded and
        shredded the membership.
        """
        line = _a_line(db, seed_user)
        match = _a_match(db, seed_user)
        _a_member(db, match, bank_statement_line_id=line.id)

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db.session.execute(db.text(
                "DELETE FROM budget.statement_imports WHERE id = :id"
            ), {"id": line.import_id})
            db.session.flush()

    def test_an_UNMATCHED_line_still_deletes_freely(
        self, app, db, seed_user,
    ):
        """The refusal is about the match, not about bank lines in general.

        A guard that refused every line delete would make the repair door
        impossible, which is the opposite of what X-f6a-4 is for.
        """
        line = _a_line(db, seed_user)

        db.session.execute(db.text(
            "DELETE FROM budget.bank_statement_lines WHERE id = :id"
        ), {"id": line.id})
        db.session.flush()

        # Counted in the DATABASE, not read back through the session: the
        # identity map still holds the object a raw DELETE removed, so
        # ``session.get`` would answer from memory and pass whatever happened.
        assert _rows(db, "budget.bank_statement_lines", line.id) == 0

    def test_deleting_the_ACCOUNT_still_cascades_everything(
        self, app, db, seed_user,
    ):
        """Why ``NO ACTION`` and not ``RESTRICT``, asserted rather than argued.

        Both refuse the delete above; ``RESTRICT`` is checked per row as the
        delete happens, where ``NO ACTION`` defers to the end of the statement.
        A whole-account delete cascades to ``statement_matches`` (taking its
        members) and to ``statement_imports`` (taking its lines) inside ONE
        statement, and only the deferred check tolerates that ordering.
        """
        other = _another_account(db, seed_user, name="Doomed Checking")
        line = _a_line(db, seed_user, account=other)
        match = _a_match(db, seed_user, account=other)
        _a_member(db, match, bank_statement_line_id=line.id)

        db.session.execute(db.text(
            "DELETE FROM budget.accounts WHERE id = :id"
        ), {"id": other.id})
        db.session.flush()

        assert _rows(db, "budget.bank_statement_lines", line.id) == 0
        assert _rows(db, "budget.statement_matches", match.id) == 0
        assert _rows(db, "budget.statement_match_members", None,
                     "account_id", other.id) == 0


class TestTheMigrationRepairsWhatTheConstraintCannotSee:
    """Plan step **bank_import:X-f6a-4**: a foreign key cannot see an absence.

    ``ADD CONSTRAINT FOREIGN KEY`` validates dangling REFERENCES.  A match with
    ZERO line members breaks no reference -- it is a missing row, not a wrong
    one -- so the constraint stops the state being PRODUCED and says nothing
    about databases that already carry it.  And the recipe was published: the
    refusal message this arc shipped before the repair door told the owner the
    situation "needs a human before anything overwrites it", and hand-run SQL
    against ``statement_imports`` is exactly what makes one.

    Migration ``e4a7c0f13b92`` therefore deletes such acts before it swaps the
    key.  Measured on the 2026-08-20 production clone: **0** of them, because
    production holds no imports at all -- so the statement is a no-op there and
    exists for the databases that are not production.  Found by adversarial
    financial review 2026-08-20.
    """

    #: The migration's own repair statement, read from the migration rather
    #: than restated -- a second spelling here would let the two drift, and the
    #: one that matters is the one that runs at deploy.
    _REPAIR = load_migration_module(
        "e4a7c0f13b92_a_match_may_not_lose_its_bank_lines.py",
    )._REPAIR_LINELESS_ACTS

    def test_it_deletes_an_act_that_holds_no_bank_line(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the state the constraint alone would leave standing."""
        txn = _a_transaction(db, seed_user)
        lineless = _a_match(db, seed_user)
        _a_member(db, lineless, transaction_id=txn.id)
        db.session.flush()

        db.session.execute(db.text(self._REPAIR))
        db.session.flush()

        assert _rows(db, "budget.statement_matches", lineless.id) == 0
        assert _rows(db, "budget.statement_match_members", None,
                     "match_id", lineless.id) == 0

    def test_it_leaves_an_act_that_HOLDS_one_alone(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL against over-deleting: a real match must survive."""
        line = _a_line(db, seed_user)
        txn = _a_transaction(db, seed_user)
        held = _a_match(db, seed_user)
        _a_member(db, held, bank_statement_line_id=line.id)
        _a_member(db, held, transaction_id=txn.id)
        db.session.flush()

        db.session.execute(db.text(self._REPAIR))
        db.session.flush()

        assert _rows(db, "budget.statement_matches", held.id) == 1
        assert _rows(db, "budget.statement_match_members", None,
                     "match_id", held.id) == 2

    def test_it_frees_the_rows_the_lineless_act_was_claiming(
        self, app, db, seed_user,
    ):
        """MONEY-ADJACENT: this is what the deletion is FOR.

        A lineless act is invisible on the review screen and yet still claims
        its transactions through ``matched_subjects``, so those rows can never
        be offered or matched again.  Freeing them is the point of removing it,
        not a side effect.
        """
        txn = _a_transaction(db, seed_user)
        lineless = _a_match(db, seed_user)
        _a_member(db, lineless, transaction_id=txn.id)
        db.session.flush()
        assert txn.id in matched_subjects(seed_user["account"].id).transactions

        db.session.execute(db.text(self._REPAIR))
        db.session.flush()
        db.session.expire_all()

        assert txn.id not in matched_subjects(
            seed_user["account"].id,
        ).transactions


def _an_entry(db, seed_user, transaction, amount="25.00"):
    """Stage and return one purchase under *transaction*.

    Args:
        db: The session fixture.
        seed_user: The seeded user bundle.
        transaction: The parent budget row.
        amount: Its figure, as a string.

    Returns:
        The staged
        :class:`~app.models.transaction_entry.TransactionEntry`.  A SCHEMA
        fixture: it exists so a creation record has a second KIND of subject
        to name, not to be worth anything.
    """
    entry = TransactionEntry(
        transaction_id=transaction.id,
        account_id=transaction.account_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="Kroger",
        purchased_on=seed_user["bootstrap_period"].start_date,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def _a_creation(db, match, **overrides):
    """Stage and return one creation record of *match*.

    Args:
        db: The session fixture.
        match: The act that made the subject.
        **overrides: Creation fields to replace.

    Returns:
        The staged
        :class:`~app.models.statement_match.StatementMatchCreation`.
    """
    fields = {
        "match_id": match.id,
        "account_id": match.account_id,
        "transaction_id": None,
        "transaction_entry_id": None,
        "created_version_id": 1,
    }
    fields.update(overrides)
    creation = StatementMatchCreation(**fields)
    db.session.add(creation)
    db.session.flush()
    return creation


class TestACreationNamesExactlyOneAppRow:
    """``ck_statement_match_creations_one_subject`` -- the exclusive arc.

    **There is no bank-line arm and its ABSENCE is the constraint** (ruling
    **R-GG**): a match act cannot bring a line into existence -- an import does
    that, and the line is what the act is ABOUT.  The column this table
    replaced needed a CHECK to say so; here it is unspellable, which is why no
    test below asserts it.
    """

    def test_naming_no_subject_is_refused(self, app, db, seed_user):
        """A creation of nothing is a row the undo would have to skip."""
        match = _a_match(db, seed_user)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_creation(db, match)

        assert "ck_statement_match_creations_one_subject" in str(caught.value)

    def test_naming_two_subjects_is_refused(self, app, db, seed_user):
        """One record cannot say two rows were created by one act."""
        match = _a_match(db, seed_user)
        transaction = _a_transaction(db, seed_user)
        entry = _an_entry(db, seed_user, transaction)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_creation(
                db, match,
                transaction_id=transaction.id,
                transaction_entry_id=entry.id,
            )

        assert "ck_statement_match_creations_one_subject" in str(caught.value)

    def test_a_zero_revision_is_refused(self, app, db, seed_user):
        """``OptimisticLockMixin`` starts every counter at 1.

        A zero would compare equal to nothing the row could ever carry, so an
        undo would silently decline to remove a row it created -- the failure
        mode a NOT NULL alone cannot see.
        """
        match = _a_match(db, seed_user)
        transaction = _a_transaction(db, seed_user)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_creation(
                db, match, transaction_id=transaction.id,
                created_version_id=0,
            )

        assert (
            "ck_statement_match_creations_version_positive" in str(caught.value)
        )

    def test_naming_one_subject_is_accepted(self, app, db, seed_user):
        """The control: the arm admits the only shape a creation may take."""
        match = _a_match(db, seed_user)
        transaction = _a_transaction(db, seed_user)

        _a_creation(db, match, transaction_id=transaction.id)

        assert db.session.query(StatementMatchCreation).count() == 1


class TestASubjectIsCreatedByAtMostOneAct:
    """``uq_statement_match_creations_transaction`` / ``..._entry``.

    Two acts each claiming to have minted one row would each offer to remove
    it, and the second undo would find it gone.
    """

    def test_one_transaction_created_by_two_acts_is_refused(
        self, app, db, seed_user,
    ):
        """The partial unique index, shown to fire."""
        transaction = _a_transaction(db, seed_user)
        _a_creation(db, _a_match(db, seed_user), transaction_id=transaction.id)

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_creation(
                db, _a_match(db, seed_user), transaction_id=transaction.id,
            )

        assert "uq_statement_match_creations_transaction" in str(caught.value)

    def test_two_creations_naming_no_entry_do_not_collide(
        self, app, db, seed_user,
    ):
        """The index is PARTIAL: a NULL is not a claim.

        Without the ``WHERE`` clause the second transaction-creation here
        would collide on ``transaction_entry_id IS NULL``, and the table could
        hold exactly one creation of each kind for the whole database.
        """
        first = _a_transaction(db, seed_user, name="Water")
        second = _a_transaction(db, seed_user, name="Gas")

        _a_creation(db, _a_match(db, seed_user), transaction_id=first.id)
        _a_creation(db, _a_match(db, seed_user), transaction_id=second.id)

        assert db.session.query(StatementMatchCreation).count() == 2


class TestACreationCannotSpanTwoAccounts:
    """``fk_statement_match_creations_transaction_account``.

    A creation naming another account's row would let one account's undo
    delete a budget line belonging to another -- the composite key is what
    makes that unwritable rather than merely unwritten.
    """

    def test_a_creation_naming_another_accounts_row_is_refused(
        self, app, db, seed_user,
    ):
        """Shown to fire against a row that really exists."""
        other = _another_account(db, seed_user)
        match = _a_match(db, seed_user)
        foreign = _a_transaction(db, seed_user, name="Elsewhere")
        foreign.account_id = other.id
        db.session.flush()

        with pytest.raises(sqlalchemy.exc.IntegrityError) as caught:
            _a_creation(db, match, transaction_id=foreign.id)

        assert (
            "fk_statement_match_creations_transaction_account"
            in str(caught.value)
        )


class TestASubjectTakesItsCreationRecordWithIt:
    """``ON DELETE CASCADE`` on both subject keys, and it is deliberate.

    A row the owner deletes themselves takes its creation record with it, so
    an undo has nothing to remove and nothing to refuse.  Refusing an ordinary
    delete because of a record the user cannot see from the row they are
    deleting is the dead end finding **N-302** records one table over.
    """

    def test_deleting_the_created_row_removes_the_record(
        self, app, db, seed_user,
    ):
        """Read from the DATABASE, not the identity map."""
        match = _a_match(db, seed_user)
        transaction = _a_transaction(db, seed_user, name="Vanishing")
        creation = _a_creation(db, match, transaction_id=transaction.id)
        creation_id = creation.id

        db.session.execute(
            db.text("DELETE FROM budget.transactions WHERE id = :id"),
            {"id": transaction.id},
        )
        db.session.flush()

        assert _rows(
            db, "budget.statement_match_creations", creation_id,
        ) == 0

    def test_deleting_the_ACT_removes_the_record(self, app, db, seed_user):
        """The act's own key cascades too, so a release leaves nothing."""
        match = _a_match(db, seed_user)
        transaction = _a_transaction(db, seed_user, name="Kept")
        creation = _a_creation(db, match, transaction_id=transaction.id)
        creation_id = creation.id

        db.session.execute(
            db.text("DELETE FROM budget.statement_matches WHERE id = :id"),
            {"id": match.id},
        )
        db.session.flush()

        assert _rows(
            db, "budget.statement_match_creations", creation_id,
        ) == 0
        assert _rows(db, "budget.transactions", transaction.id) == 1, (
            "the CASCADE runs from the act to its record, never on to the row "
            "the record names -- removing that is the release DOOR's decision"
        )


class TestTheCreationsTableIsAudited:
    """Every new table in ``budget`` carries the audit trigger."""

    def test_it_is_on_the_audited_list(self, app, db):
        """``app.audit_infrastructure.AUDITED_TABLES`` names it."""
        assert ("budget", "statement_match_creations") in AUDITED_TABLES

    def test_it_carries_its_trigger(self, app, db):
        """Under the name the entrypoint health check enumerates."""
        found = db.session.execute(
            db.text(
                "SELECT 1 FROM pg_trigger g "
                "JOIN pg_class t ON t.oid = g.tgrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = 'budget' "
                "AND t.relname = 'statement_match_creations' "
                "AND g.tgname = 'audit_statement_match_creations'"
            ),
        ).scalar()
        assert found == 1


class TestTheOldMarkerIsGone:
    """``statement_match_members.created_version_id`` was DROPPED.

    Two relations in one column is what left the create-a-purchase arm's
    container unrecordable; a column left behind would be a second place for a
    writer to put the fact and for a reader to miss it.
    """

    def test_the_column_no_longer_exists(self, app, db):
        """Asked of the catalogue, which is the only tier that can see it."""
        found = db.session.execute(
            db.text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'budget' "
                "AND table_name = 'statement_match_members' "
                "AND column_name = 'created_version_id'"
            ),
        ).scalar()
        assert found == 0
