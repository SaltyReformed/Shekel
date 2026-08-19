"""The write door records a statement once, and refuses what it cannot trust.

Plan step **bank_import:X-f6a-1**, ruling **R-FP**.  Three properties are what
this leaf is FOR, and all three are graded here:

* **it records what the bank said** -- the lines, with the bank's own posted day;
* **it is idempotent** -- re-importing an overlapping span records the ACT and
  no duplicate lines, which is R-FP's "re-importing a file cannot duplicate"
  read as a property of the door rather than of a key;
* **it moves NO money** -- no transaction, purchase, status or balance is
  touched, which is what makes this leaf separable from the matching that
  follows it.

The refusal tests are FIRING CONTROLS: an account mismatch, a file claimed by
another account, and a restated line are all states no ordinary use produces
(measured: 0 restatements across two real exports twelve days apart), so each
one is planted and its refusal asserted by type.
"""

import hashlib
from datetime import date
from decimal import Decimal

import pytest

from app.enums import StatementSourceEnum
from app.exceptions import StatementAccountMismatch, StatementLineConflict
from app.models.statement_import import (
    AccountExternalIdentity,
    BankStatementLine,
    StatementImport,
)
from app.models.journal_entry import Posting
from app.models.transaction import Transaction
from app.services import account_service
from app.models.statement_import import BankStatementLine
from app.services.statement_import import record_statement
from app.models.ref import AccountType

from tests._test_helpers import create_settled_cash_transaction

from . import _csv_builder as build

_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV

_ENTRIES = [
    (date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
    (date(2026, 3, 3), "1500.00", "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
    (date(2026, 3, 4), "-40.81", "POINT OF SALE DEBIT L340 FOOD LION"),
]


def _file(entries=None, start="100.00", account_number=None):
    """Return a well-formed payload over *entries*."""
    kwargs = {}
    if account_number is not None:
        kwargs["account_number"] = account_number
    rows = build.chained(start, entries or _ENTRIES, **kwargs)
    return build.build(rows)


def _record(seed_user, payload, file_name="statement.csv", account=None):
    """Run the door for the seeded user's checking account."""
    return record_statement(
        account_id=(account or seed_user["account"]).id,
        user_id=seed_user["user"].id,
        source=_SOURCE,
        file_name=file_name,
        payload=payload,
    )


def _second_account(db, seed_user):
    """Create a second cash account for the same owner."""
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    return account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Second Checking",
            anchor_balance=Decimal("0.00"),
            observed_on=date(2024, 1, 5),
        )
    )


class TestItRecordsWhatTheBankSaid:
    """The ordinary import."""

    def test_it_writes_one_line_per_statement_line(self, app, db, seed_user):
        """Three lines in the file, three rows in the database."""
        outcome = _record(seed_user, _file())

        assert outcome.recorded_count == 3
        assert db.session.query(BankStatementLine).count() == 3

    def test_it_records_the_banks_posted_day(self, app, db, seed_user):
        """The whole point of the arc: the day the BANK says, not the app."""
        _record(seed_user, _file())

        days = sorted(
            row.posted_on
            for row in db.session.query(BankStatementLine).all()
        )
        assert days == [date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)]

    def test_it_records_the_signed_amounts(self, app, db, seed_user):
        """Positive INTO the account, matching ``settled_cash_leg``."""
        _record(seed_user, _file())

        amounts = sorted(
            Decimal(str(row.amount))
            for row in db.session.query(BankStatementLine).all()
        )
        assert amounts == [
            Decimal("-40.81"), Decimal("-25.00"), Decimal("1500.00"),
        ]

    def test_it_records_the_import_act_with_its_counts(
        self, app, db, seed_user,
    ):
        """Provenance: who, when, which file, and how much was new."""
        _record(seed_user, _file(), file_name="march.csv")

        row = db.session.query(StatementImport).one()
        assert row.file_name == "march.csv"
        assert row.line_count == 3
        assert row.recorded_count == 3
        assert row.period_start == date(2026, 3, 2)
        assert row.period_end == date(2026, 3, 4)
        assert row.user_id == seed_user["user"].id

    def test_it_derives_the_opening_and_closing_from_the_chain(
        self, app, db, seed_user,
    ):
        """Never from the file's own header, which was measured to lag."""
        outcome = _record(seed_user, _file())

        assert outcome.opening_balance == Decimal("100.00")
        assert outcome.closing_balance == Decimal("1534.19")

    def test_every_line_belongs_to_its_import_and_its_account(
        self, app, db, seed_user,
    ):
        """The composite key's two columns, as written."""
        _record(seed_user, _file())

        statement = db.session.query(StatementImport).one()
        for row in db.session.query(BankStatementLine).all():
            assert row.import_id == statement.id
            assert row.account_id == seed_user["account"].id

    def test_a_files_digest_is_the_digest_OF_THE_BYTES(
        self, app, db, seed_user,
    ):
        """So "is this the same file" is answerable where the name is not.

        Asserting the LENGTH, which is what this did first, passes for the
        digest of the empty string or of the file NAME -- neither of which
        answers the question the column exists for.
        """
        payload = _file()
        _record(seed_user, payload)

        assert db.session.query(StatementImport).one().file_digest == (
            hashlib.sha256(payload).hexdigest()
        )

    def test_two_different_files_get_different_digests(
        self, app, db, seed_user,
    ):
        """The other half: the digest has to DISCRIMINATE."""
        _record(seed_user, _file(), file_name="one.csv")
        _record(
            seed_user,
            _file(_ENTRIES + [(date(2026, 3, 5), "-9.99", "EXTRA")]),
            file_name="two.csv",
        )

        digests = {
            row.file_digest
            for row in db.session.query(StatementImport).all()
        }
        assert len(digests) == 2


class TestItMovesNoMoney:
    """The property that makes this leaf separable from the matching.

    **These were VACUOUS until an adversarial review measured them**: the
    ``seed_user`` fixture creates no ``Transaction`` at all, so the before /
    after snapshot compared ``[] == []`` and the clearing-link assertion
    counted zero rows in an empty table.  The guard the plan's separability
    argument rests on graded nothing.  They now settle a real transaction
    first, and snapshot the LEDGER as well as the rows -- because the balance
    is walked from the postings, not from ``budget.transactions``.
    """

    @pytest.fixture()
    def money_on_the_books(self, db, seed_user, seed_periods):
        """Settle a real transaction so there is something to move."""
        txn = create_settled_cash_transaction(
            seed_user, db.session, seed_periods[0], Decimal("125.00"),
            settled_on=date(2026, 3, 3), name="Groceries",
        )
        db.session.flush()
        return txn

    def _rows(self, db):
        """Return every field an import could plausibly disturb.

        The settlement record replaced ``actual_amount`` at plan step
        ``balance:X-au-c3``, so the snapshot follows it: a row's figure is now
        ``settled_amount`` beside the ``settled_basis_id`` that says how it is
        known, and both belong in a comparison whose whole job is to prove that
        recording a statement moves nothing.
        """
        return [
            (t.id, t.status_id, t.settled_on, t.settled_amount,
             t.settled_basis_id,
             t.estimated_amount, t.pay_period_id, t.reconciled_by_id)
            for t in db.session.query(Transaction)
            .order_by(Transaction.id).all()
        ]

    def _ledger(self, db):
        """Return the postings the balance is actually walked from."""
        return [
            (p.id, p.journal_entry_id, p.ledger_account_id, p.amount,
             p.posting_kind_id)
            for p in db.session.query(Posting).order_by(Posting.id).all()
        ]

    def test_no_transaction_row_is_touched(
        self, app, db, seed_user, money_on_the_books,
    ):
        """A statement import writes statement rows and nothing else."""
        before = self._rows(db)
        assert before, "fixture must put a real transaction on the books"

        _record(seed_user, _file())

        assert self._rows(db) == before

    def test_no_POSTING_is_touched(
        self, app, db, seed_user, money_on_the_books,
    ):
        """The stronger claim: the balance's own inputs do not move.

        Asserted on the double-entry postings rather than on a balance figure,
        because a balance that happened not to move could still hide a posting
        that did.
        """
        before = self._ledger(db)
        assert before, "fixture must put postings on the ledger"

        _record(seed_user, _file())

        assert self._ledger(db) == before

    def test_no_clearing_link_is_written(
        self, app, db, seed_user, money_on_the_books,
    ):
        """Deciding which statement showed a line is the NEXT leaf's job."""
        before = db.session.query(Transaction).filter(
            Transaction.reconciled_by_id.isnot(None)
        ).count()

        _record(seed_user, _file())

        assert db.session.query(Transaction).filter(
            Transaction.reconciled_by_id.isnot(None)
        ).count() == before

    def test_no_settle_DAY_is_corrected(
        self, app, db, seed_user, money_on_the_books,
    ):
        """The single fact the NEXT leaf exists to change, pinned here.

        The bank file states 2026-03-02..2026-03-04 and the settled row carries
        2026-03-03, so a matcher that started working early would have every
        opportunity to move it.
        """
        _record(seed_user, _file())

        assert money_on_the_books.settled_on == date(2026, 3, 3)


class TestItIsIdempotent:
    """R-FP's "re-importing a file cannot duplicate", as a property."""

    def test_the_same_file_twice_records_no_second_line(
        self, app, db, seed_user,
    ):
        """The second import adds nothing and says so."""
        _record(seed_user, _file())

        second = _record(seed_user, _file(), file_name="again.csv")

        assert second.recorded_count == 0
        assert second.already_known == 3
        assert db.session.query(BankStatementLine).count() == 3

    def test_the_second_import_is_still_RECORDED_as_an_act(
        self, app, db, seed_user,
    ):
        """Doing nothing is a fact worth keeping: it is the proof you checked."""
        _record(seed_user, _file())
        _record(seed_user, _file(), file_name="again.csv")

        assert db.session.query(StatementImport).count() == 2

    def test_an_overlapping_span_records_only_the_new_lines(
        self, app, db, seed_user,
    ):
        """The real shape: each export covers year-to-date, so every import
        after the first overlaps everything before it."""
        _record(seed_user, _file())

        extended = _ENTRIES + [
            (date(2026, 3, 5), "-12.79", "POINT OF SALE DEBIT L340 BJS"),
        ]
        second = _record(seed_user, _file(extended), file_name="later.csv")

        assert second.recorded_count == 1
        assert second.already_known == 3
        assert db.session.query(BankStatementLine).count() == 4

    def test_a_line_keeps_the_import_that_FIRST_recorded_it(
        self, app, db, seed_user,
    ):
        """Provenance survives a re-import rather than being overwritten."""
        first = _record(seed_user, _file())
        _record(seed_user, _file(), file_name="again.csv")

        assert {
            row.import_id
            for row in db.session.query(BankStatementLine).all()
        } == {first.import_id}

    def test_two_identical_charges_on_one_day_are_both_recorded(
        self, app, db, seed_user,
    ):
        """The ordinal's whole purpose, end to end.

        Without it the second charge is read as a duplicate of the first and
        never recorded -- money the bank took that the app would never see.
        """
        entries = [
            (date(2026, 3, 2), "-4.75", "COFFEE"),
            (date(2026, 3, 2), "-4.75", "COFFEE"),
        ]

        outcome = _record(seed_user, _file(entries))

        assert outcome.recorded_count == 2
        assert db.session.query(BankStatementLine).count() == 2

    def test_those_two_charges_survive_a_re_import_without_duplicating(
        self, app, db, seed_user,
    ):
        """Both halves at once: the ordinal must be STABLE as well as total."""
        entries = [
            (date(2026, 3, 2), "-4.75", "COFFEE"),
            (date(2026, 3, 2), "-4.75", "COFFEE"),
        ]
        _record(seed_user, _file(entries))

        second = _record(seed_user, _file(entries), file_name="again.csv")

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).count() == 2


class TestItAbsorbsWhatALaterExportAdds:
    """Information GAINED is not a restatement, and it must not be discarded."""

    def test_a_transaction_day_arriving_later_is_recorded(
        self, app, db, seed_user,
    ):
        """The arm that MOVES A DATE, not just a display figure.

        A line recorded by an adapter that could not read the bank's stated
        transaction day carries NULL, and a match writes that day onto a
        matched purchase's ``purchased_on`` (ruling **R-FW**).  Without this
        arm a re-import of the very same file left the column NULL forever --
        the running-balance defect above, on a column that feeds a date write.
        Found by adversarial financial review 2026-08-18, which measured the
        re-import leaving it untouched.
        """
        stated = date(2026, 3, 1)
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 DATE 03-01 COFFEE")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        recorded = db.session.query(BankStatementLine).one()
        assert recorded.transaction_on == stated
        # Stand in for a row an older adapter wrote, which knew no such day.
        recorded.transaction_on = None
        db.session.flush()

        second = _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).one().transaction_on == stated

    def test_a_DISAGREEING_transaction_day_is_left_alone(
        self, app, db, seed_user,
    ):
        """Only NULL is filled -- a stated day is an observation, not a draft.

        THE FIRING CONTROL for the arm's ``is None`` guard: widen it to an
        unconditional write and this fails.
        """
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 DATE 03-01 COFFEE")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        recorded = db.session.query(BankStatementLine).one()
        recorded.transaction_on = date(2026, 2, 27)
        db.session.flush()

        _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")

        assert db.session.query(
            BankStatementLine,
        ).one().transaction_on == date(2026, 2, 27)

    def test_a_running_balance_arriving_later_is_recorded(
        self, app, db, seed_user,
    ):
        """The path the developer actually took, and it lost the data.

        The running-balance column is an export OPTION.  The 10-column file is
        what SECU gives you by default; the page then tells you to re-export
        with the column, and before this the second import recognised every
        line as already known and threw the balances away -- leaving NULL
        forever on the very fact the CSV was chosen over the OFX to obtain.
        """
        without = build.build(build.chained(
            "100.00", _ENTRIES, with_running=False,
        ))
        _record(seed_user, without, file_name="plain.csv")
        assert all(
            row.running_balance is None
            for row in db.session.query(BankStatementLine).all()
        )

        second = _record(seed_user, _file(), file_name="with-balance.csv")

        assert second.recorded_count == 0
        assert sorted(
            Decimal(str(row.running_balance))
            for row in db.session.query(BankStatementLine).all()
        ) == [Decimal("75.00"), Decimal("1534.19"), Decimal("1575.00")]

    def test_a_category_arriving_later_is_recorded(
        self, app, db, seed_user,
    ):
        """The same rule on the other optional fact."""
        bare = [build.row(date(2026, 3, 2), "-25.00", "COFFEE",
                          running="75.00")]
        _record(seed_user, build.build(bare))

        labelled = [build.row(date(2026, 3, 2), "-25.00", "COFFEE",
                              category="Food/Coffee", running="75.00")]
        _record(seed_user, build.build(labelled), file_name="second.csv")

        assert db.session.query(BankStatementLine).one().source_category == (
            "Food/Coffee"
        )


class TestTheAccountMappingIsAFactNotAGuess:
    """Ruling R-FP: the first import records it, every import after checks it."""

    def test_the_first_import_records_what_the_source_calls_the_account(
        self, app, db, seed_user,
    ):
        """The mapping exists because the user stated it, not because the app
        inferred it."""
        _record(seed_user, _file())

        identity = db.session.query(AccountExternalIdentity).one()
        assert identity.account_id == seed_user["account"].id
        assert identity.external_account_id == build.ACCOUNT_IDENTITY

    def test_a_second_import_of_the_same_account_records_no_second_mapping(
        self, app, db, seed_user,
    ):
        """One account has ONE identity per source."""
        _record(seed_user, _file())
        _record(seed_user, _file(), file_name="again.csv")

        assert db.session.query(AccountExternalIdentity).count() == 1

    def test_a_file_for_a_DIFFERENT_account_is_refused(
        self, app, db, seed_user,
    ):
        """Importing the card's export into checking, the case R-FP names."""
        _record(seed_user, _file())

        with pytest.raises(StatementAccountMismatch) as caught:
            _record(
                seed_user, _file(account_number="******9999"),
                file_name="wrong.csv",
            )

        assert caught.value.submitted == "Checking ******9999"
        assert caught.value.recorded == build.ACCOUNT_IDENTITY

    def test_the_identity_carries_the_account_NAME_not_just_the_mask(
        self, app, db, seed_user,
    ):
        """The mask alone cannot tell two of one owner's accounts apart.

        SECU masks the number to its last four digits, so two accounts whose
        numbers end alike mask identically -- and comparing only the mask would
        ACCEPT a file imported against the wrong account, which is the failure
        direction this mapping exists to prevent.
        """
        _record(seed_user, _file())

        with pytest.raises(StatementAccountMismatch):
            _record(
                seed_user,
                build.build(build.chained(
                    "100.00", _ENTRIES, account_name="Savings",
                )),
                file_name="savings.csv",
            )

    def test_a_file_another_account_already_claims_is_refused(
        self, app, db, seed_user,
    ):
        """The arm a one-directional check would miss.

        Comparing only against THIS account's recorded identity would let a
        statement already imported under account A be imported again under
        account B as a "first import" -- the same bank statement recorded
        twice, under two accounts.
        """
        _record(seed_user, _file())
        other = _second_account(db, seed_user)
        db.session.flush()

        with pytest.raises(StatementAccountMismatch):
            _record(seed_user, _file(), account=other)

    def test_a_refused_import_stages_nothing(self, app, db, seed_user):
        """"Nothing was imported" is true BEFORE the rollback, not because of it.

        Asserted without rolling back deliberately: the door validates
        everything it can refuse before it stages a single row, so the session
        is already clean when the refusal is raised.  A test that rolled back
        first would pass equally against a door that wrote and then relied on
        the route remembering to undo it.
        """
        _record(seed_user, _file())
        before = db.session.query(BankStatementLine).count()

        with pytest.raises(StatementAccountMismatch):
            _record(
                seed_user, _file(account_number="******9999"),
                file_name="wrong.csv",
            )

        assert db.session.query(BankStatementLine).count() == before
        assert db.session.query(StatementImport).count() == 1


class TestARecordedLineMayNotBeQuietlyRestated:
    """An observation rewritten is what ruling R-FL exists to prevent."""

    def test_a_changed_description_at_a_known_identity_is_refused(
        self, app, db, seed_user,
    ):
        """Same day, same amount, same ordinal -- different text."""
        _record(seed_user, _file())

        changed = list(_ENTRIES)
        changed[0] = (date(2026, 3, 2), "-25.00", "SOMETHING ELSE ENTIRELY")

        with pytest.raises(StatementLineConflict) as caught:
            _record(seed_user, _file(changed), file_name="restated.csv")

        assert caught.value.posted_on == date(2026, 3, 2)
        assert caught.value.submitted == "SOMETHING ELSE ENTIRELY"

    def test_a_changed_running_balance_is_NOT_a_conflict(
        self, app, db, seed_user,
    ):
        """A running balance is not a fact about a line, so it cannot restate one.

        It is a prefix sum over the bank's LISTING ORDER, and SECU lists a
        day's card debits sorted by ascending magnitude rather than by arrival
        -- so a swipe that finalizes onto an already-listed day is INSERTED,
        and every later line on that day legitimately gets a different balance
        while both files verify their own chain.  Comparing it per line refused
        an honest, more-complete re-export of the user's own year-to-date
        statement and left that account unable to import ever again.
        """
        _record(seed_user, _file())

        second = _record(
            seed_user, _file(start="9999.00"), file_name="later.csv",
        )

        assert second.recorded_count == 0

    def test_an_unchanged_re_import_is_NOT_a_conflict(
        self, app, db, seed_user,
    ):
        """The refusal must not fire on the ordinary case.

        A guard that refuses the correct write is worse than none, and the
        correct write here -- re-importing an unchanged year-to-date export --
        is what the user does every single time.
        """
        _record(seed_user, _file())

        second = _record(seed_user, _file(), file_name="again.csv")

        assert second.recorded_count == 0
