"""Migration ``af07125d00f1`` -- a line is held by its sightings (``bank_import:X-f6b-1``).

Both directions are driven through the migration's own shipped callables over
a world holding what production holds -- one import per line -- plus the two
shapes production does not yet: a line TWO imports sighted, and an import
that sighted nothing.  Every assertion reads the DATABASE, and the
migration's claims about itself are the ones graded hardest: that the
downgrade writes a line's columns back from the sighting of its EARLIEST
import by act order (never the sighting with the lowest id, which a backfill
writes in line order and a later import writes after), that it writes each
import's two counts back from the sightings, and that it REFUSES while an
import with no sighting exists rather than deleting it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatementSourceEnum
from app.extensions import db
from app.models.statement_import import (
    BankStatementLine,
    StatementImport,
    StatementLineSighting,
)
from tests._test_helpers import (
    load_migration_module,
    run_migration_callable as _run,
)

_MIGRATION = load_migration_module(
    "af07125d00f1_a_line_is_held_by_its_sightings.py",
)


def _sql(statement, **params):
    """Run one statement and return every row."""
    return db.session.execute(text(statement), params).all()


def _columns(table):
    """The column names of ``budget.<table>``, from the catalogue."""
    return {
        row[0] for row in _sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'budget' AND table_name = :t", t=table,
        )
    }


def _trigger_count(pattern):
    """How many non-internal triggers match *pattern*."""
    return _sql(
        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
        "AND tgname LIKE :p", p=pattern,
    )[0][0]


def _an_import(account, *, file_name, window, created_at):
    """One import over *window*, run at *created_at* -- through the head schema."""
    statement = StatementImport(
        account_id=account.id,
        user_id=account.user_id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name=file_name,
        file_digest=file_name.ljust(64, "0")[:64],
        declared_start=window[0],
        declared_end=window[1],
    )
    statement.created_at = created_at
    db.session.add(statement)
    db.session.flush()
    return statement


def _a_line(account, *, day, amount, ordinal=0):
    """One bank line, unsighted until a caller sights it."""
    line = BankStatementLine(
        account_id=account.id, posted_on=day, amount=Decimal(amount),
        sequence_in_group=ordinal,
    )
    db.session.add(line)
    db.session.flush()
    return line


def _sight(statement, line, **facts):
    """*statement*'s sighting of *line*, carrying *facts*."""
    sighting = StatementLineSighting(
        account_id=line.account_id, line_id=line.id, import_id=statement.id,
        **facts,
    )
    db.session.add(sighting)
    db.session.flush()
    return sighting


@pytest.mark.xdist_group("sighting_relation_ddl")
class TestTheRoundTrip:
    """Head -> down -> up, over one line two imports sighted."""

    def test_down_keeps_the_EARLIEST_imports_sighting_and_up_restores_both(
        self, db, seed_user,
    ):
        """The shipped downgrade and upgrade, graded on the rows they leave.

        **The two-element case the order claim needs.**  The LATER import is
        given the LOWER sighting id -- its sighting is written first -- and
        the EARLIER instant, so that "the earliest import's sighting" and
        "the sighting with the lowest id" and "the first sighting written"
        are three different rows.  The downgrade must keep the earliest
        import's wording, which is what ``import_id`` meant.

        Run in the ``db`` fixture's own app context rather than a nested one,
        for the reason ``test_one_level_relation_migration`` gives.
        """
        account = seed_user["account"]
        earlier = _an_import(
            account, file_name="earlier.csv",
            window=(date(2026, 3, 1), date(2026, 3, 31)),
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        later = _an_import(
            account, file_name="later.csv",
            window=(date(2026, 3, 15), date(2026, 4, 15)),
            created_at=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        )
        shared = _a_line(account, day=date(2026, 3, 20), amount="-25.00")
        # The LATER import's sighting takes the lower id, deliberately.
        later_sighting = _sight(
            later, shared, description="LATER WORDING",
            transaction_on=date(2026, 3, 19), running_balance=Decimal("9.00"),
        )
        earlier_sighting = _sight(
            earlier, shared, description="EARLIER WORDING",
            source_category="Food/Coffee", external_id="FIT-1",
        )
        assert later_sighting.id < earlier_sighting.id
        only_later = _a_line(account, day=date(2026, 4, 10), amount="-5.00")
        _sight(later, only_later, description="ONLY THE LATER ONE SAW THIS")
        db.session.commit()
        shared_id, only_later_id = shared.id, only_later.id
        earlier_id, later_id = earlier.id, later.id

        _run(_MIGRATION.downgrade, db.session)

        # The line carries the EARLIEST import's facts again -- its wording,
        # its id, its category -- and that import's id; the later sighting's
        # transaction day and running balance are not what the old column
        # held, because the old column held ONE source's account of the line.
        assert _sql(
            "SELECT import_id, description, transaction_on, external_id, "
            "running_balance, source_category "
            "FROM budget.bank_statement_lines WHERE id = :l", l=shared_id,
        ) == [(earlier_id, "EARLIER WORDING", None, "FIT-1", None,
               "Food/Coffee")]
        assert _sql(
            "SELECT import_id, description "
            "FROM budget.bank_statement_lines WHERE id = :l", l=only_later_id,
        ) == [(later_id, "ONLY THE LATER ONE SAW THIS")]
        # The counts are written back from the sightings: the earlier import
        # sighted one line and was first to it; the later sighted two and was
        # first to one.
        assert _sql(
            "SELECT id, period_start, period_end, line_count, recorded_count "
            "FROM budget.statement_imports ORDER BY id",
        ) == [
            (earlier_id, date(2026, 3, 1), date(2026, 3, 31), 1, 1),
            (later_id, date(2026, 3, 15), date(2026, 4, 15), 2, 1),
        ]
        assert _sql("SELECT to_regclass('budget.statement_line_sightings')") == [
            (None,),
        ]
        assert "declared_start" not in _columns("statement_imports")
        assert _trigger_count("rm\\_line\\_left\\_unsighted") == 0
        assert _trigger_count("ck\\_file\\_span\\_holds\\_level") == 1
        # The old shape's own keys are back, by name.
        assert _sql(
            "SELECT count(*) FROM pg_constraint WHERE conname IN "
            "('fk_bank_statement_lines_import_account', "
            " 'ck_statement_imports_line_count_positive', "
            " 'ck_statement_imports_recorded_within_file', "
            " 'ck_statement_imports_period_ordered')",
        ) == [(4,)]
        assert _sql(
            "SELECT count(*) FROM pg_indexes WHERE indexname = "
            "'uq_bank_statement_lines_external_id'",
        ) == [(1,)]

        _run(_MIGRATION.upgrade, db.session)

        # One sighting per line from the import the old column named; the
        # second sighting is not representable below this revision, so it is
        # honestly gone -- the later import now sights only its own line.
        assert _sql(
            "SELECT line_id, import_id, description, external_id, "
            "source_category FROM budget.statement_line_sightings "
            "ORDER BY line_id, import_id",
        ) == [
            (shared_id, earlier_id, "EARLIER WORDING", "FIT-1", "Food/Coffee"),
            (only_later_id, later_id, "ONLY THE LATER ONE SAW THIS", None,
             None),
        ]
        assert _sql(
            "SELECT id, declared_start, declared_end "
            "FROM budget.statement_imports ORDER BY id",
        ) == [
            (earlier_id, date(2026, 3, 1), date(2026, 3, 31)),
            (later_id, date(2026, 3, 15), date(2026, 4, 15)),
        ]
        assert _columns("bank_statement_lines") == {
            "id", "account_id", "posted_on", "amount", "merchant_id",
            "sequence_in_group",
        }
        assert _columns("statement_imports").isdisjoint(
            {"period_start", "period_end", "line_count", "recorded_count"},
        )
        assert _trigger_count("rm\\_line\\_left\\_unsighted") == 1
        assert _trigger_count("audit\\_statement\\_line\\_sightings") == 1
        assert _trigger_count("ck\\_file\\_span\\_holds\\_level") == 1
        assert _sql(
            "SELECT count(*) FROM pg_constraint WHERE conname = "
            "'bank_statement_lines_account_id_fkey'",
        ) == [(1,)]

    def test_the_backfill_carries_the_merchant_WORD_from_the_merchant_row(
        self, db, seed_user,
    ):
        """A backfilled sighting names the word the CSV parenthesised.

        The old line held the merchant KEY alone; the word that minted it is
        the merchant row's name, and that is what the source called it.
        """
        account = seed_user["account"]
        statement = _an_import(
            account, file_name="named.csv",
            window=(date(2026, 3, 1), date(2026, 3, 31)),
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        merchant_id = _sql(
            "INSERT INTO budget.merchants (account_id, name) "
            "VALUES (:a, 'Food Lion') RETURNING id", a=account.id,
        )[0][0]
        line = _a_line(account, day=date(2026, 3, 4), amount="-40.81")
        line.merchant_id = merchant_id
        _sight(statement, line, description="POINT OF SALE (Food Lion)")
        db.session.commit()
        line_id = line.id

        _run(_MIGRATION.downgrade, db.session)
        _run(_MIGRATION.upgrade, db.session)

        assert _sql(
            "SELECT merchant FROM budget.statement_line_sightings "
            "WHERE line_id = :l", l=line_id,
        ) == [("Food Lion",)]

    def test_the_downgrade_REFUSES_an_id_two_sources_hold_on_two_lines(
        self, db, seed_user,
    ):
        """The other state the old per-account unique index cannot hold.

        Two imports (standing in for two sources) each sight their own line
        under the id string ``FIT-1``.  The old schema keys one id per
        account, so the downgrade names the collision up front rather than
        raising ``IntegrityError`` from its ``create_index`` half-way.
        """
        account = seed_user["account"]
        csv = _an_import(
            account, file_name="csv.csv",
            window=(date(2026, 3, 1), date(2026, 3, 31)),
            created_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        )
        feed = _an_import(
            account, file_name="feed.dat",
            window=(date(2026, 3, 1), date(2026, 3, 31)),
            created_at=datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc),
        )
        _sight(
            csv, _a_line(account, day=date(2026, 3, 2), amount="-1.00"),
            description="X", external_id="FIT-1",
        )
        _sight(
            feed, _a_line(account, day=date(2026, 3, 3), amount="-2.00"),
            description="Y", external_id="FIT-1",
        )
        db.session.commit()

        with pytest.raises(RuntimeError, match="1 external id"):
            _run(_MIGRATION.downgrade, db.session)
        db.session.rollback()

        assert _trigger_count("rm\\_line\\_left\\_unsighted") == 1
        assert "declared_start" in _columns("statement_imports")

    def test_the_downgrade_REFUSES_while_an_import_sighted_nothing(
        self, db, seed_user,
    ):
        """The one state the old schema cannot hold: named, not deleted.

        A zero-line sync may hold a placed level; deleting it here would move
        money on the way down.  The refusal names the count and the door
        that removes such imports with their levels released.  Nothing is
        changed: the sighting trigger still stands afterwards.
        """
        account = seed_user["account"]
        _an_import(
            account, file_name="quiet-window.csv",
            window=(date(2026, 5, 1), date(2026, 5, 7)),
            created_at=datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc),
        )
        db.session.commit()

        with pytest.raises(RuntimeError, match="1 statement import"):
            _run(_MIGRATION.downgrade, db.session)
        db.session.rollback()

        assert _trigger_count("rm\\_line\\_left\\_unsighted") == 1
        assert "declared_start" in _columns("statement_imports")
