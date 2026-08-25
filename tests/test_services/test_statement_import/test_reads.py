"""What the statements page reads, and the scoping nothing was watching.

Plan step **bank_import:X-f6a-1**.  ``_reads.py`` had NO test at all until an
adversarial review said so: four public functions, three of them carrying an
``account_id`` filter, and the only coverage was four static-string assertions
in the route test.

**The scoping tests are the point.**  Deleting the ``account_id`` filter from
any of the three readers left every other test in the branch green -- while the
page rendered another account's bank descriptions, days, amounts and running
balances.  That is a disclosure of exactly the material this leaf exists to
record, and the route's 404 tests cannot see it: they prove you cannot ADDRESS
another account's page, not that your own page shows only your own lines.

``recorded_span``'s ``net_amount`` is the other hole they name: it is a money
figure rendered to the user and it was asserted nowhere, so summing absolute
values instead of signed ones would have gone unnoticed.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.enums import (
    StatementBalanceEvidenceEnum,
    StatementSourceEnum,
)
from app.models.ref import AccountType, StatementSource
from app.ref_seeds import _REF_TABLE_SEEDS
from app.services import account_service
from app.services.statement_import import (
    available_sources,
    import_history,
    recent_lines,
    record_statement,
    recorded_span,
)

from . import _csv_builder as build

_SOURCE = StatementSourceEnum.SECU_CHECKING_CSV

_ENTRIES = [
    (date(2026, 3, 2), "-25.00", "POINT OF SALE DEBIT L340 COFFEE"),
    (date(2026, 3, 3), "1500.00", "ACH DEPOSIT TOWN OF CLAYTON  PAYROLL"),
    (date(2026, 3, 4), "-40.81", "POINT OF SALE DEBIT L340 FOOD LION"),
]


def _record(seed_user, account, entries, number=build.ACCOUNT_NUMBER,
            name=build.ACCOUNT_NAME, file_name="statement.csv"):
    """Record *entries* against *account*."""
    payload = build.build(build.chained(
        "100.00", entries, account_number=number, account_name=name,
    ))
    return record_statement(
        account_id=account.id,
        user_id=seed_user["user"].id,
        source=_SOURCE,
        file_name=file_name,
        payload=payload,
    )


@pytest.fixture()
def two_accounts(db, seed_user):
    """Return the seeded account plus a SECOND one owned by the same user."""
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    other = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=checking_type.id,
            name="Second Checking",
            anchor_balance=Decimal("0.00"),
            observed_on=seed_user["bootstrap_period"].start_date,
        )
    )
    db.session.flush()
    return seed_user["account"], other


class TestEveryReaderIsScopedToItsOwnAccount:
    """One owner, two accounts: a reader must not blend them."""

    def test_recorded_span_counts_only_its_own_lines(
        self, app, db, seed_user, two_accounts,
    ):
        """Three lines here, one there -- and the span says three."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES)
        _record(
            seed_user, theirs,
            [(date(2026, 3, 9), "-99.00", "ELSEWHERE")],
            number="******9999", name="Second Checking",
            file_name="other.csv",
        )

        assert recorded_span(mine.id).line_count == 3
        assert recorded_span(theirs.id).line_count == 1

    def test_recorded_span_dates_only_its_own_lines(
        self, app, db, seed_user, two_accounts,
    ):
        """The other account's later day must not widen this account's span."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES)
        _record(
            seed_user, theirs,
            [(date(2026, 9, 9), "-99.00", "MUCH LATER")],
            number="******9999", name="Second Checking",
            file_name="other.csv",
        )

        span = recorded_span(mine.id)
        assert span.first_day == date(2026, 3, 2)
        assert span.last_day == date(2026, 3, 4)

    def test_recent_lines_returns_only_its_own(
        self, app, db, seed_user, two_accounts,
    ):
        """The disclosure test: another account's descriptions must not show."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES)
        _record(
            seed_user, theirs,
            [(date(2026, 3, 9), "-99.00", "SOMEBODY ELSES MERCHANT")],
            number="******9999", name="Second Checking",
            file_name="other.csv",
        )

        descriptions = [line.description for line in recent_lines(mine.id)]

        assert len(descriptions) == 3
        assert "SOMEBODY ELSES MERCHANT" not in descriptions

    def test_import_history_returns_only_its_own(
        self, app, db, seed_user, two_accounts,
    ):
        """The same, for the acts rather than the lines."""
        mine, theirs = two_accounts
        _record(seed_user, mine, _ENTRIES, file_name="mine.csv")
        _record(
            seed_user, theirs,
            [(date(2026, 3, 9), "-99.00", "ELSEWHERE")],
            number="******9999", name="Second Checking",
            file_name="theirs.csv",
        )

        assert [row.file_name for row in import_history(seed_user["user"].id, mine.id)] == [
            "mine.csv",
        ]


class TestTheImportRowCarriesWhatTheBankSaid:
    """The RECORD, where the receipt is transient.

    ``ImportedBalance``'s own docstring argues this matters because an anchor
    the import only assumed has to stay readable after the flash is gone -- and
    the field had no test at all, so forcing ``_imported_balance`` to return
    ``None`` left 233 tests passing.  Found by adversarial review 2026-08-23.
    """

    def test_it_carries_the_claim_the_day_and_the_evidence(
        self, app, db, seed_user, two_accounts,
    ):
        """All four facts, because the schema holds them as one.

        The fixture chains from `$100.00`, so its header states the closing its
        own lines imply and the file proves itself.
        """
        mine, _ = two_accounts
        _record(seed_user, mine, _ENTRIES)

        balance = import_history(seed_user["user"].id, mine.id)[0].balance

        assert balance is not None
        assert balance.stated == Decimal("1534.19")
        assert balance.effective_on == date(2026, 3, 4)
        assert balance.evidence is StatementBalanceEvidenceEnum.FILE_CHAIN
        assert balance.is_anchored

    def test_a_file_stating_NO_balance_carries_none(
        self, app, db, seed_user, two_accounts,
    ):
        """The absence is a value the page branches on, so it is asserted."""
        mine, _ = two_accounts
        payload = build.build(build.chained("100.00", _ENTRIES))
        without = b"\n".join(
            line for line in payload.split(b"\n")
            if not line.startswith(b"Balance as of")
        )
        record_statement(
            account_id=mine.id, user_id=seed_user["user"].id,
            source=StatementSourceEnum.SECU_CHECKING_CSV,
            file_name="nobalance.csv", payload=without,
        )

        assert import_history(seed_user["user"].id, mine.id)[0].balance is None


class TestTheSpanIsArithmeticallyTrue:
    """``net_amount`` is a money figure the page renders."""

    def test_the_net_is_the_SIGNED_sum(self, app, db, seed_user):
        """Signed, not absolute: -25.00 + 1500.00 - 40.81 = 1434.19.

        Summing magnitudes instead would give 1565.81 and read as a windfall.
        """
        _record(seed_user, seed_user["account"], _ENTRIES)

        assert recorded_span(seed_user["account"].id).net_amount == Decimal(
            "1434.19"
        )

    def test_the_net_is_decimal(self, app, db, seed_user):
        """It crosses the SQL boundary as Decimal, never float."""
        _record(seed_user, seed_user["account"], _ENTRIES)

        assert isinstance(
            recorded_span(seed_user["account"].id).net_amount, Decimal
        )

    def test_an_account_with_nothing_recorded_answers_rather_than_raising(
        self, app, db, seed_user,
    ):
        """A zero-count span with ``None`` days, not an absence to branch on."""
        span = recorded_span(seed_user["account"].id)

        assert span.line_count == 0
        assert span.first_day is None
        assert span.last_day is None
        assert span.net_amount == Decimal("0")


class TestTheLineListIsOrderedAndBounded:
    """What the page shows, and how much of it."""

    def test_lines_come_back_newest_POSTED_day_first(
        self, app, db, seed_user,
    ):
        """Ordered by the bank's own day, not by when they were recorded.

        An import backfilling an older span must not push newer lines down the
        page -- the question the section answers is "what does my bank say
        happened lately".
        """
        _record(seed_user, seed_user["account"], _ENTRIES)

        assert [line.posted_on for line in
                recent_lines(seed_user["account"].id)] == [
            date(2026, 3, 4), date(2026, 3, 3), date(2026, 3, 2),
        ]

    def test_the_limit_bounds_the_list(self, app, db, seed_user):
        """A page section, not an unbounded dump."""
        _record(seed_user, seed_user["account"], _ENTRIES)

        assert len(recent_lines(seed_user["account"].id, limit=2)) == 2

    def test_imports_come_back_newest_first(self, app, db, seed_user):
        """Most recent act at the top."""
        _record(seed_user, seed_user["account"], _ENTRIES, file_name="one.csv")
        _record(seed_user, seed_user["account"], _ENTRIES, file_name="two.csv")

        assert [row.file_name for row in
                import_history(seed_user["user"].id, seed_user["account"].id)][0] == "two.csv"


class TestTheOfferedSourcesAreTheUSABLEOnes:
    """The form's options are the intersection of the ref table and the
    adapter registry."""

    def test_every_offered_source_has_a_label_and_a_parser(
        self, app, db,
    ):
        """A source with no label could not be rendered; one with no parser
        would fail deeper in with a worse message."""
        offered = available_sources()

        assert offered
        assert all(option.label for option in offered)
        assert {option.value for option in offered} == {
            member.value for member in StatementSourceEnum
        }

    def test_the_label_comes_from_the_ref_table(self, app, db):
        """IDs for logic, strings for display -- and the string lives in ref.

        **The expected string changed at plan step ``bank_import:X-gc``.**  It
        read "SECU checking -- CSV with running balance", which named the
        format by a column SECU no longer exports: all four of the developer's
        exports on disk 2026-08-25 carry no balance column at all, and the help
        text rendered directly beneath this control has said the column is
        optional since plan step ``bank_import:X-f6e-1``.  Migration
        ``a1f4c7e0b839`` re-labels the row; ``app.ref_seeds`` carries the same
        value for a fresh bootstrap.  This test still grades what it always
        graded -- that the label is READ FROM ``ref`` rather than written into
        the reader -- and the value it pins is now the true one.
        """
        assert available_sources()[0].label == "SECU checking -- CSV export"

    def test_the_SEEDER_and_the_DATABASE_agree_about_that_label(
        self, app, db,
    ):
        """Leg 3 of the dual seed, for the one ref row that carries a LABEL.

        A ref value lives in three places -- the enum, the introducing
        migration's inline seed, and ``app.ref_seeds`` -- and
        ``tests/test_models/test_posting_ref_seed_parity.py`` grades all three
        for NAMES.  It cannot grade this one: ``display_name`` is not a name,
        and ``_seed_other_ref_tables`` INSERTS missing rows while leaving
        present ones alone, so a label changed in ``ref_seeds.py`` and nowhere
        else changes what a FRESH bootstrap says and nothing about the
        databases that already exist.  The two would then disagree silently and
        for ever.

        Plan step ``bank_import:X-gc`` created exactly that opportunity by
        re-labelling the row in a migration and in the seed file together; this
        is what makes the pair verifiable rather than a convention.  The test
        database is migration-built and then reseeded, so the stored value is
        the MIGRATION's -- which is precisely why comparing it against the
        SEED file catches the half that would otherwise be graded nowhere.
        """
        seeded = {
            entry["name"]: entry["display_name"]
            for entry in dict(_REF_TABLE_SEEDS)["StatementSource"]
        }
        stored = {
            row.name: row.display_name
            for row in db.session.query(StatementSource).all()
        }

        assert seeded == stored
