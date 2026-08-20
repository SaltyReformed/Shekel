"""The UNDO door: a recorded import stops being permanent.

Plan step **bank_import:X-f6a-4**, finding **N-302**.  Recording what a bank
said is append-only everywhere else, which left every refusal TERMINAL: a
restated line, or a first import that named the wrong Shekel account, ended
that account's ability to import at all, while the refusal's own message
promised a repair nothing in ``app/`` could perform.

**Four properties are what this door is FOR, and all four are graded here:**

* it removes the import and the lines IT first recorded, and nothing else;
* it RELEASES every match naming one of those lines rather than letting the
  database shred the membership -- and the settle days those matches wrote
  STAY, because they are the app's own record;
* it forgets the source-account pairing exactly when the last import from that
  source goes, which is what makes a wrongly-recorded pairing repairable;
* after it runs, the account can import again -- the round trip that closes
  N-302, asserted end to end rather than inferred from the parts.

The refusals are FIRING CONTROLS: an unknown id and another owner's import are
states no ordinary use produces, so each is planted and its refusal asserted.
"""

from datetime import date

import pytest

from app import ref_cache
from app.enums import StatementSourceEnum, StatusEnum
from app.exceptions import StatementLineConflict, ValidationError
from app.models.statement_import import (
    AccountExternalIdentity,
    BankStatementLine,
    StatementImport,
)
from app.models.statement_match import StatementMatch, StatementMatchMember
from app.services import statement_match
from app.services.statement_import import delete_import, record_statement
from app.services.statement_match import MatchSubmission, matched_subjects

from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_scope,
    a_transaction,
    an_import,
)

from . import _csv_builder as build
from .test_record import _second_account

_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV

_ENTRIES = [
    (date(2026, 3, 2), "-25.00",
     "POINT OF SALE DEBIT L340 COFFEE (Big Cheese Clayton)"),
    (date(2026, 3, 4), "-40.81",
     "POINT OF SALE DEBIT L340 FOOD LION (Food Lion)"),
]


def _file(entries=None, start="100.00"):
    """Return a well-formed payload over *entries*."""
    return build.build(build.chained(start, entries or _ENTRIES))


def _import(seed_user, payload=None, file_name="statement.csv"):
    """Record a real statement through the real door."""
    return record_statement(
        account_id=seed_user["account"].id,
        user_id=seed_user["user"].id,
        source=_SOURCE,
        file_name=file_name,
        payload=payload if payload is not None else _file(),
    )


def _undo(seed_user, import_id):
    """Run the undo door for the seeded user's checking account."""
    return delete_import(
        import_id, seed_user["user"].id, seed_user["account"].id,
    )


def _match(seed_user, line, txn):
    """Accept a real match between one line and one row, through the door."""
    return statement_match.accept_match(
        MatchSubmission(
            line_ids=frozenset({line.id}),
            transaction_ids=frozenset({txn.id}),
            entry_ids=frozenset(),
        ),
        a_scope(seed_user),
    )


class TestItRemovesWhatThatImportRecorded:
    """The import, its lines, and nothing beyond them."""

    def test_it_removes_the_import_and_its_lines(self, app, db, seed_user):
        """The act and the observations it wrote go together."""
        outcome = _import(seed_user)

        removal = _undo(seed_user, outcome.import_id)

        assert removal.lines_removed == 2
        assert db.session.query(StatementImport).count() == 0
        assert db.session.query(BankStatementLine).count() == 0

    def test_a_line_ANOTHER_import_recorded_stays(self, app, db, seed_user):
        """A line names the import that FIRST recorded it, and only that one.

        MONEY-ADJACENT: re-importing an overlapping span records the ACT and no
        duplicate lines, so the second import owns none of the first's.
        Deleting the second must therefore take nothing at all -- if it took
        the span it merely re-saw, an owner tidying up a redundant import would
        silently destroy the record the first import holds.
        """
        first = _import(seed_user)
        second = _import(seed_user, file_name="again.csv")
        assert second.recorded_count == 0

        removal = _undo(seed_user, second.import_id)

        assert removal.lines_removed == 0
        assert db.session.query(BankStatementLine).count() == 2
        assert db.session.query(StatementImport).one().id == first.import_id

    def test_it_reports_the_span_and_the_file_it_removed(
        self, app, db, seed_user,
    ):
        """Every figure is counted as the act runs, because afterwards it is gone."""
        outcome = _import(seed_user, file_name="ytd.csv")

        removal = _undo(seed_user, outcome.import_id)

        assert removal.file_name == "ytd.csv"
        assert removal.period_start == date(2026, 3, 2)
        assert removal.period_end == date(2026, 3, 4)
        assert removal.matches_released == 0


class TestItReleasesRatherThanOrphans:
    """A match may not lose its bank lines, and the days it wrote stay."""

    def test_it_releases_a_match_naming_one_of_its_lines(
        self, app, db, seed_user,
    ):
        """The act is DELETED, not left standing with an empty bank side.

        A match with no line asserts nothing about a bank, so the review screen
        cannot render it and no release button ever exists for it -- while
        ``matched_subjects`` reads the member rows directly and goes on
        reporting its rows as claimed.  Measured on a production clone
        2026-08-20, deleting an import by hand left exactly that: an act with 0
        line members and 1 transaction member.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        _match(seed_user, line, txn)
        assert db.session.query(StatementMatch).count() == 1

        removal = _undo(seed_user, statement.id)

        assert removal.matches_released == 1
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.query(StatementMatchMember).count() == 0

    def test_the_settle_day_the_match_wrote_is_UNCHANGED(
        self, app, db, seed_user,
    ):
        """MONEY: the delete removes what the BANK said, not what the app knows.

        A settle day is the app's own record of when money moved, and the bank
        is still the best evidence it has -- so reverting a correction in order
        to tidy a relation would throw away the fact and keep the bookkeeping.
        This is ``release_match``'s own rule, asked of the door that calls it.
        """
        statement = an_import(seed_user)
        posted = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=posted,
        )
        txn = a_transaction(seed_user, amount="180.00")
        _match(seed_user, line, txn)
        db.session.refresh(txn)
        assert txn.settled_on == posted

        _undo(seed_user, statement.id)

        db.session.refresh(txn)
        assert txn.settled_on == posted
        assert txn.status_id == ref_cache.status_id(StatusEnum.DONE)

    def test_the_released_rows_are_MATCHABLE_again(self, app, db, seed_user):
        """What comes back is the QUESTION, which is the point of a release.

        FIRING CONTROL for the defect this door exists not to create: if the
        act were left standing without its lines, this row would report as
        already-matched forever and no door could free it.
        """
        statement = an_import(seed_user)
        line = a_bank_line(seed_user, statement, amount="-180.00")
        txn = a_transaction(seed_user, amount="180.00")
        _match(seed_user, line, txn)
        assert txn.id in matched_subjects(seed_user["account"].id).transactions

        _undo(seed_user, statement.id)

        claimed = matched_subjects(seed_user["account"].id)
        assert txn.id not in claimed.transactions
        assert claimed.lines == frozenset()

    def test_a_match_spanning_TWO_imports_is_released_whole(
        self, app, db, seed_user,
    ):
        """The other import's line becomes unexplained again, and stays recorded.

        A group is one movement; releasing half of it is not a smaller group,
        it is a broken one.  So the other import's line survives the delete --
        it belongs to an import nobody removed -- and is simply unmatched.
        """
        first = an_import(seed_user)
        second = an_import(seed_user)
        posted = seed_user["bootstrap_period"].start_date
        kept = a_bank_line(
            seed_user, first, amount="-80.00", posted_on=posted,
            description="ONE",
        )
        going = a_bank_line(
            seed_user, second, amount="-100.00", posted_on=posted,
            description="TWO",
        )
        txn = a_transaction(seed_user, amount="180.00")
        statement_match.accept_match(
            MatchSubmission(
                line_ids=frozenset({kept.id, going.id}),
                transaction_ids=frozenset({txn.id}),
                entry_ids=frozenset(),
            ),
            a_scope(seed_user),
        )

        removal = _undo(seed_user, second.id)

        assert removal.matches_released == 1
        assert db.session.query(StatementMatch).count() == 0
        assert db.session.get(BankStatementLine, kept.id) is not None
        assert matched_subjects(seed_user["account"].id).lines == frozenset()


class TestThePairingOutlivesNoImportThatTaughtIt:
    """Which bank account this is, is a fact learned from an import."""

    def test_the_last_import_takes_the_pairing_with_it(
        self, app, db, seed_user,
    ):
        """What makes a WRONGLY-recorded pairing repairable (N-302's second shape).

        Recorded on a first import and then used to refuse every later file
        naming a different account -- correct, and until this step
        unrepairable: an owner who chose the wrong Shekel account once could
        never import that account's statements again.
        """
        outcome = _import(seed_user)
        assert db.session.query(AccountExternalIdentity).count() == 1

        removal = _undo(seed_user, outcome.import_id)

        assert removal.identity_forgotten is True
        assert db.session.query(AccountExternalIdentity).count() == 0

    def test_a_surviving_import_from_that_source_KEEPS_the_pairing(
        self, app, db, seed_user,
    ):
        """FIRING CONTROL: forgetting on any delete would be the wrong rule.

        The pairing protects the recorded lines from another account's
        statement.  While an import from that source survives there are lines
        to protect, so the guard must stay armed.
        """
        first = _import(seed_user)
        second = _import(seed_user, file_name="again.csv")

        removal = _undo(seed_user, second.import_id)

        assert removal.identity_forgotten is False
        assert db.session.query(AccountExternalIdentity).count() == 1
        assert first.import_id == db.session.query(StatementImport).one().id


class TestItRefusesWhatIsNotThisOwnersImport:
    """The set-operation form of "404 for both not-found and not-yours"."""

    def test_an_unknown_import_id_is_refused(self, app, db, seed_user):
        """A stale page naming an import someone else already deleted."""
        with pytest.raises(ValidationError) as caught:
            _undo(seed_user, 999999)

        assert "no longer there" in str(caught.value)

    def test_ANOTHER_accounts_import_is_refused(self, app, db, seed_user):
        """Scoping is by account as well as by owner.

        The route proves ownership of the ACCOUNT; this door is what stops an
        id from one of the owner's own accounts reaching another's statements.
        """
        outcome = _import(seed_user)
        other = _second_account(db, seed_user)

        with pytest.raises(ValidationError):
            delete_import(
                outcome.import_id, seed_user["user"].id, other.id,
            )

        assert db.session.query(StatementImport).count() == 1
        assert db.session.query(BankStatementLine).count() == 2

    def test_a_refusal_writes_nothing(self, app, db, seed_user):
        """It refuses BEFORE it stages anything, not by rolling back."""
        _import(seed_user)
        before = db.session.query(BankStatementLine).count()

        with pytest.raises(ValidationError):
            _undo(seed_user, 999999)

        assert db.session.query(BankStatementLine).count() == before
        assert db.session.query(AccountExternalIdentity).count() == 1


class TestTheAccountCanImportAgain:
    """The round trip N-302 asks for, asserted end to end."""

    def test_a_deleted_span_can_be_re_imported_in_full(
        self, app, db, seed_user,
    ):
        """Delete then re-import restores exactly what was there.

        This is the whole point: the repair is not "the rows are gone", it is
        "the account works again".  A door that removed the lines but left
        anything behind -- a pairing that now refuses the same file, a match
        holding its rows, an ordinal the identity key collides on -- would pass
        every test above and fail this one.
        """
        first = _import(seed_user)
        _undo(seed_user, first.import_id)

        second = _import(seed_user, file_name="redo.csv")

        assert second.recorded_count == first.recorded_count == 2
        assert db.session.query(BankStatementLine).count() == 2
        assert db.session.query(AccountExternalIdentity).count() == 1

    def test_a_RESTATED_line_is_repaired_by_deleting_its_import(
        self, app, db, seed_user,
    ):
        """The dead end N-302 names, walked from end to end.

        The bank restates a line; the import refuses, correctly and
        permanently, because a recorded observation may not be overwritten.
        Before this step nothing in ``app/`` could clear the recorded line, so
        that account could never import again.
        """
        first = _import(seed_user)
        restated = list(_ENTRIES)
        restated[0] = (date(2026, 3, 2), "-25.00", "SOMETHING ELSE ENTIRELY")

        with pytest.raises(StatementLineConflict):
            _import(seed_user, _file(restated), file_name="restated.csv")

        _undo(seed_user, first.import_id)
        repaired = _import(
            seed_user, _file(restated), file_name="restated.csv",
        )

        assert repaired.recorded_count == 2
        assert db.session.query(BankStatementLine).filter_by(
            description="SOMETHING ELSE ENTIRELY",
        ).count() == 1


class TestTheDeletesAreAudited:
    """`system.audit_log` is what makes a destructive door forensically safe."""

    def test_every_removed_line_leaves_an_audit_row(
        self, app, db, seed_user,
    ):
        """The trigger fires on a CASCADED delete, not only a direct one.

        The lines go with the import at the database tier, so the audit trail
        depends on PostgreSQL firing row triggers on cascade -- which it does,
        and which is asserted rather than assumed because the whole forensic
        story of this door rests on it.
        """
        outcome = _import(seed_user)
        db.session.flush()

        _undo(seed_user, outcome.import_id)
        db.session.flush()

        deleted = db.session.execute(db.text(
            "SELECT count(*) FROM system.audit_log "
            "WHERE table_name = 'bank_statement_lines' AND operation = 'DELETE'"
        )).scalar()
        assert deleted == 2
