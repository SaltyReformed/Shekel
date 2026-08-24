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
from sqlalchemy.exc import IntegrityError

from app import ref_cache
from app.enums import (
    StatementBalanceEvidenceEnum,
    StatementSourceEnum,
)
from app.exceptions import (
    StatementAccountMismatch,
    StatementBalanceUnexplained,
    StatementLineConflict,
)
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
    (date(2026, 3, 2), "-25.00",
     "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
    (date(2026, 3, 3), "1500.00",
     "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL (TOWN OF CLAYTON PAYROLL)"),
    (date(2026, 3, 4), "-40.81",
     "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
]

#: What SECU NAMES the merchant on each of those, in the same order -- the
#: parenthesised trailing token the adapter reads.  **Written out rather than
#: re-parsed here**: a fixture that re-ran the parse would move with it, so a
#: change to the adapter's rule would shift this list and its assertion
#: together and grade nothing.  The shared entries carry a token because every
#: one of the developer's 361 real lines does; a case that wants the ``None``
#: state says so with its own rows.
_MERCHANTS = ["Big Cheese Clayton", "TOWN OF CLAYTON PAYROLL", "Food Lion"]


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

    def test_it_records_the_merchant_the_bank_NAMED(
        self, app, db, seed_user,
    ):
        """The column a destination policy is keyed by, written by the adapter.

        Plan step X-f6a-3d.  What makes this worth its own case at THIS tier is
        that the adapter's answer has to reach the row: the parse is graded in
        ``test_secu_csv``, and this is the only place that says the value
        travels from the parse to the column rather than being dropped by the
        staging call.
        """
        _record(seed_user, _file())

        rows = db.session.query(BankStatementLine).order_by(
            BankStatementLine.posted_on,
        ).all()
        assert [row.merchant for row in rows] == _MERCHANTS

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

    def test_a_chained_file_is_PROVED_by_itself_and_the_ROW_says_so(
        self, app, db, seed_user,
    ):
        """The stored columns, not just the value the door returned.

        **The row itself had no assertion for an ANCHORED import**, so forcing
        ``balance_effective_on`` to ``period_end`` or the evidence to a
        constant left the whole suite green -- the column this step exists to
        add could have stored the wrong thing forever.  Found by adversarial
        review 2026-08-23.

        The fixture chains from `$100.00` through -25.00, +1500.00 and -40.81,
        so it closes at `$1,534.19` on 03-04 and its header states exactly
        that.
        """
        outcome = _record(seed_user, _file())

        row = db.session.query(StatementImport).one()
        assert row.stated_balance == Decimal("1534.19")
        assert row.balance_effective_on == date(2026, 3, 4)
        assert ref_cache.statement_balance_evidence_member(
            row.balance_evidence_id
        ) is StatementBalanceEvidenceEnum.FILE_CHAIN
        # The receipt and the row say ONE thing.
        assert outcome.balance.effective_on == row.balance_effective_on
        assert outcome.balance.evidence is StatementBalanceEvidenceEnum.FILE_CHAIN

    def test_the_stored_day_is_NOT_the_day_the_header_names(
        self, app, db, seed_user,
    ):
        """The 2026-08-16 LAG shape, end to end through the door.

        **Measured on the developer's own export**: its header reads
        ``$4,747.63`` as of 08-16 over a file listing two 08-14 lines worth
        ``-$1,006.72``, and the figure is 08-13's closing.  Until this test the
        shape reached only a pure unit -- no door-level case ever produced an
        ``effective_on`` DIFFERENT from ``period_end``, which is why mutating
        the stored day to ``period_end`` survived.  Found by adversarial review
        2026-08-23.

        Here the chain closes at `$1,534.19` on 03-04 and the header states
        `$1,559.19` -- the 03-03 cumulative -- as of 03-09, so the figure is
        placed at 03-03 while the row's span ends 03-04.
        """
        _record(
            seed_user,
            build.build(build.chained("100.00", _ENTRIES),
                        balance_as_of="03/09/2026",
                        stated_balance="1575.00"),
        )

        row = db.session.query(StatementImport).one()
        assert row.period_end == date(2026, 3, 4)
        assert row.stated_balance_on == date(2026, 3, 9)
        assert row.balance_effective_on == date(2026, 3, 3)

    def test_a_header_the_files_lines_CANNOT_REACH_records_no_anchor(
        self, app, db, seed_user,
    ):
        """A date-range export states TODAY's balance, not the range's closing.

        **Measured on the developer's own file**: he exported
        2026-01-02..2026-03-31 on 2026-08-23 and its header reads
        ``Balance as of 08/23/2026,2459.600000`` -- 145 days past its last line
        and `$255.41` from the `$2,715.01` its own 139 lines imply.  The
        movements explaining that difference are simply not in the file, so no
        day inside it can place the figure and refusing it would reject an
        honest export.  The CLAIM is recorded; the anchor is not.

        A PRIOR import supplies the opening, because "cannot be placed" is a
        statement about a known opening the figure fails to reconcile with --
        and it OVERLAPS, as a real consecutive export does, or the walk stops
        at the uncovered day between them and falls back to taking the figure
        at face value.
        """
        _record(seed_user, _file())

        _record(
            seed_user,
            build.build(
                build.chained(
                    "0.00",
                    [_ENTRIES[2],
                     (date(2026, 3, 6), "-10.00",
                      "POINT OF SALE DEBIT L340 FUEL")],
                    with_running=False,
                ),
                balance_as_of="08/16/2026", stated_balance="2501.31",
            ),
            file_name="range.csv",
        )

        row = (
            db.session.query(StatementImport)
            .filter_by(file_name="range.csv").one()
        )
        assert row.stated_balance == Decimal("2501.31")
        assert row.stated_balance_on == date(2026, 8, 16)
        assert row.balance_effective_on is None
        assert row.balance_evidence_id is None

    def test_a_chained_file_CONTRADICTING_itself_is_REFUSED(
        self, app, db, seed_user,
    ):
        """The only refusal: the file's own chain against its own header."""
        with pytest.raises(StatementBalanceUnexplained) as raised:
            _record(
                seed_user,
                build.build(build.chained("100.00", _ENTRIES),
                            balance_as_of="03/09/2026",
                            stated_balance="9999.99"),
            )

        assert raised.value.stated == Decimal("9999.99")
        assert raised.value.implied == Decimal("1534.19")
        assert db.session.query(StatementImport).count() == 0

    def test_recording_a_line_RELEASES_an_anchor_it_undercuts(
        self, app, db, seed_user,
    ):
        """The door's half of the release, through the door.

        A second export inserting a line into a day the first anchor had
        already priced means that anchor was solved without it.  Reproduced as
        a stored day two days early under a *corroborated* badge before the
        release existed.
        """
        _record(seed_user, _file())
        first = db.session.query(StatementImport).one()
        assert first.balance_effective_on == date(2026, 3, 4)

        # A later export the bank has INSERTED a line into, on a day the first
        # anchor already covers.
        _record(
            seed_user,
            build.build(build.chained(
                "100.00",
                _ENTRIES[:2] + [
                    (date(2026, 3, 3), "-5.00", "POINT OF SALE DEBIT L340 X"),
                    _ENTRIES[2],
                ],
            )),
            file_name="inserted.csv",
        )

        db.session.refresh(first)
        assert first.balance_effective_on is None
        assert first.balance_evidence_id is None

    def test_a_file_claiming_no_balance_records_NEITHER_column(
        self, app, db, seed_user,
    ):
        """Both-or-neither, which the database also refuses to break.

        ``ck_statement_imports_stated_balance_paired`` makes a half-written
        pair unstorable; this is the door agreeing with it.
        """
        payload = build.build(build.chained("100.00", _ENTRIES))
        without = b"\n".join(
            line for line in payload.split(b"\n")
            if not line.startswith(b"Balance as of")
        )

        _record(seed_user, without)

        row = db.session.query(StatementImport).one()
        assert row.stated_balance is None
        assert row.stated_balance_on is None

    def test_the_DATABASE_refuses_a_half_written_stated_balance(
        self, app, db, seed_user,
    ):
        """One fact in two columns, enforced where a future adapter cannot miss.

        The door writes both or neither today.  A second source adapter is one
        forgotten field from writing a figure with no day -- which asserts
        nothing about an account, and which the reader would then use to select
        an anchor "as of NULL".  Stated structurally so the guarantee does not
        rest on every adapter remembering.
        """
        _record(seed_user, _file())
        row = db.session.query(StatementImport).one()

        row.stated_balance = Decimal("2501.31")
        row.stated_balance_on = None

        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()

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


    def test_a_merchant_arriving_later_is_recorded(
        self, app, db, seed_user,
    ):
        """The arm a destination POLICY keys on (plan step X-f6a-3d).

        A line whose merchant is NULL joins no policy, so a row recorded by an
        adapter that could not name one would go on being offered a bare
        chooser forever -- even after an export that DOES name one had been
        imported over it.  Same rule, same direction, as the transaction-day
        arm above; the consequence here is a decision the owner has to make
        again rather than a date left wrong.
        """
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        recorded = db.session.query(BankStatementLine).one()
        assert recorded.merchant == "Big Cheese Clayton"
        # Stand in for a row an older adapter wrote, which named no merchant.
        recorded.merchant = None
        db.session.flush()

        second = _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).one().merchant == (
            "Big Cheese Clayton"
        )

    def test_a_DISAGREEING_merchant_is_left_alone(
        self, app, db, seed_user,
    ):
        """Only NULL is filled, and here that is a POLICY's key.

        THE FIRING CONTROL for the arm's ``is None`` guard: widen it to an
        unconditional write and this fails.  Overwriting would silently
        re-point every destination policy the owner had stated against the old
        name, which is a decision moving without anyone deciding it.
        """
        entries = [(date(2026, 3, 2), "-25.00",
                    "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)")]
        _record(seed_user, build.build(build.chained("100.00", entries)))
        recorded = db.session.query(BankStatementLine).one()
        recorded.merchant = "Cheese Shop"
        db.session.flush()

        _record(seed_user, build.build(build.chained(
            "100.00", entries,
        )), file_name="again.csv")

        assert db.session.query(BankStatementLine).one().merchant == (
            "Cheese Shop"
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


#: Two same-day same-amount lines: the shape whose ordinal was the only term of
#: the identity key the bank never stated.  Measured across the developer's
#: 2026-07-19, 2026-08-16 and 2026-08-18 exports, 0 of 1,041 real lines shared a
#: ``(day, amount)`` with another -- so every case below is PLANTED, and each is
#: a firing control for a refusal that used to fire on a file the bank had not
#: restated at all.
_TWINS = [
    (date(2026, 3, 2), "-4.75",
     "POINT OF SALE DEBIT L340 STARBUCKS #123 (Starbucks)"),
    (date(2026, 3, 2), "-4.75",
     "POINT OF SALE DEBIT L340 DUNKIN #456 (Dunkin)"),
]

#: A line on a LATER day, so a refusal over the twins costs it too.  This is
#: what makes the cost of a false refusal visible: the whole file is refused,
#: not the disputed line.
_LATER = (date(2026, 3, 5), "-99.00",
          "POINT OF SALE DEBIT L340 ACE HARDWARE (Ace Hardware)")


class TestAGroupIsReconciledAsASet:
    """Plan step X-f6a-4: the ordinal is a surrogate, not a comparison term."""

    def test_the_same_two_lines_in_the_OTHER_order_record_nothing(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: the bank re-orders one day's two equal debits.

        Measured against the positional code 2026-08-20, this refused the whole
        file and told the owner the bank had restated a line it had not.  Both
        lines are present in both files; only the ordinal this app assigned
        moved.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user, _file(list(reversed(_TWINS))), file_name="swapped.csv",
        )

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_reorder_does_not_cost_the_file_its_GENUINELY_new_lines(
        self, app, db, seed_user,
    ):
        """MONEY: what a false refusal actually costs is the rest of the export.

        The re-ordered pair is two lines; the file also carries a line the app
        has never seen.  Refusing the file loses that line -- and every other
        line after it -- until someone repairs the account by hand.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user,
            _file(list(reversed(_TWINS)) + [_LATER]),
            file_name="swapped_plus_new.csv",
        )

        assert second.recorded_count == 1
        assert db.session.query(BankStatementLine).filter_by(
            posted_on=date(2026, 3, 5),
        ).one().amount == Decimal("-99.00")

    def test_a_NEW_line_the_bank_lists_FIRST_is_recorded_as_the_new_one(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: a swipe finalizes onto an already-recorded day.

        SECU INSERTS such a line into that day's block rather than appending it
        -- the behaviour that made ``_refuse_restatement`` stop comparing
        running balances -- so the file lists the NEW line FIRST and the
        recorded one after it.  Under the positional rule that compared the new
        line against the recorded one's wording and refused the whole file.
        Exactly one line is new, and the recorded one keeps its own address.
        """
        _record(seed_user, _file([_TWINS[0]]))

        second = _record(
            seed_user,
            _file([_TWINS[1], _TWINS[0]]),
            file_name="inserted.csv",
        )

        assert second.recorded_count == 1
        rows = {
            row.description: row.sequence_in_group
            for row in db.session.query(BankStatementLine).all()
        }
        assert rows == {_TWINS[0][2]: 0, _TWINS[1][2]: 1}

    def test_a_second_IDENTICAL_charge_is_recorded_rather_than_dropped(
        self, app, db, seed_user,
    ):
        """MONEY: the same coffee twice at the same shop is two movements.

        This is what the ordinal exists for, asked of the set-wise
        reconciliation: pairing by wording must be by COUNT, or the second
        charge is read as a duplicate of the first and never recorded.
        """
        same = (date(2026, 3, 2), "-4.75", "POINT OF SALE DEBIT L340 COFFEE")
        _record(seed_user, _file([same]))

        second = _record(seed_user, _file([same, same]), file_name="twice.csv")

        assert second.recorded_count == 1
        assert db.session.query(BankStatementLine).count() == 2
        assert sorted(
            row.sequence_in_group
            for row in db.session.query(BankStatementLine).all()
        ) == [0, 1]

    def test_a_RESTATED_line_in_a_shared_group_is_still_refused(
        self, app, db, seed_user,
    ):
        """The refusal the policy is FOR survives the reconciliation change.

        One of the two lines keeps its wording and pairs; the other's wording
        is gone from the file and an unaccounted-for line stands in its place.
        That is the bank re-wording an observation, and ruling R-FL refuses it.
        """
        _record(seed_user, _file(_TWINS))

        restated = [
            _TWINS[0],
            (date(2026, 3, 2), "-4.75", "SOMETHING ELSE ENTIRELY"),
        ]

        with pytest.raises(StatementLineConflict) as caught:
            _record(seed_user, _file(restated), file_name="restated.csv")

        assert caught.value.recorded == _TWINS[1][2]
        assert caught.value.submitted == "SOMETHING ELSE ENTIRELY"
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_SHORTER_export_over_a_shared_group_refuses_nothing(
        self, app, db, seed_user,
    ):
        """A file covering less than the app holds contradicts nothing.

        The app holds both twins and the file states one.  Under the positional
        rule the surviving line landed at ordinal 0 and was compared against
        the OTHER twin's wording, which refused.  It is now what it is: a
        shorter export, recording nothing and refusing nothing.
        """
        _record(seed_user, _file(_TWINS))

        second = _record(
            seed_user, _file([_TWINS[1]]), file_name="shorter.csv",
        )

        assert second.recorded_count == 0
        assert db.session.query(BankStatementLine).count() == 2
