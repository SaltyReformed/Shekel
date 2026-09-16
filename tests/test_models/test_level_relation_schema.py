"""The LEVEL RELATION's own keys, bounds and refusals (plan step ``balance:X-bj-1``).

``budget.account_anchor_history`` holds the bank's statement placements beside
the owner's true-ups since that step, and ``budget.anchor_releases`` holds
their withdrawals.  Every rule the two tables state about themselves is
graded here with a control SHOWN to fire, and the two disposal paths the
append-only refusal must PERMIT are graded as the positive controls --
because a refusal that also refuses the cascade is an import that can never
be deleted.

Every case that reaches a DEFERRED arm (the DELETE arms) COMMITS, because a
deferred constraint trigger fires at commit and not before; a case that
asserted on the flush alone would grade nothing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError

from app import ref_cache
from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
from app.models.account import AccountAnchorHistory
from app.models.anchor_release import (
    AnchorRelease,
    AnchorReleaseImmutableError,
)
from app.models.statement_import import StatementImport
from tests._test_helpers import constraint_name_from, create_account_of_type

_FILE_CHAIN = StatementBalanceEvidenceEnum.FILE_CHAIN
_UNCORROBORATED = StatementBalanceEvidenceEnum.UNCORROBORATED


def _import(db, account, *, stated="1085.00", stated_on=date(2026, 3, 9),
            period=(date(2026, 3, 1), date(2026, 3, 3)),
            file_name="seed.csv"):
    """Stage one import stating *stated* as of *stated_on* over *period*."""
    row = StatementImport(
        account_id=account.id,
        user_id=account.user_id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name=file_name,
        file_digest=file_name.ljust(64, "0")[:64],
        period_start=period[0],
        period_end=period[1],
        line_count=1,
        recorded_count=1,
        stated_balance=None if stated is None else Decimal(stated),
        stated_balance_on=None if stated is None else stated_on,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _level(db, account, statement_import, *, amount="1085.00",
           day=date(2026, 3, 3), evidence=_FILE_CHAIN, account_id=None):
    """Stage one bank level naming *statement_import*; returns it flushed."""
    level = AccountAnchorHistory(
        account_id=account_id or account.id,
        anchor_balance=Decimal(amount),
        observed_on=day,
        evidence_id=ref_cache.statement_balance_evidence_id(evidence),
        statement_import_id=statement_import.id,
    )
    db.session.add(level)
    db.session.flush()
    return level


def _release(db, level, *, by_import=None, day=date(2026, 3, 2)):
    """Stage one withdrawal of *level*; returns it flushed."""
    release = AnchorRelease(
        account_id=level.account_id,
        anchor_id=level.id,
        released_by_import_id=None if by_import is None else by_import.id,
        lines_changed_from=day,
    )
    db.session.add(release)
    db.session.flush()
    return release


def _refused_by(db, exc_info) -> str:
    """Return the constraint the flush named, rolling the session back."""
    db.session.rollback()
    return constraint_name_from(exc_info.value)


class TestABankLevelIsKeyedToItsFile:
    """The two composite keys, and what each makes unrepresentable."""

    def test_a_level_naming_ANOTHER_accounts_import_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """``fk_anchor_history_statement_import_account``."""
        theirs = _import(db, seed_second_user["account"])

        with pytest.raises(IntegrityError) as raised:
            _level(db, seed_user["account"], theirs)

        assert _refused_by(db, raised) == (
            "fk_anchor_history_statement_import_account"
        )

    def test_a_level_whose_amount_is_NOT_its_files_claim_is_refused(
        self, app, db, seed_user,
    ):
        """``fk_anchor_history_statement_import_claim``: the claim is the source.

        Written ``1085.01`` against a file stating ``1085.00``: a cent off,
        which is exactly the drift a maintained copy would admit.
        """
        mine = _import(db, seed_user["account"], stated="1085.00")

        with pytest.raises(IntegrityError) as raised:
            _level(db, seed_user["account"], mine, amount="1085.01")

        assert _refused_by(db, raised) == (
            "fk_anchor_history_statement_import_claim"
        )

    def test_an_import_stating_NO_balance_can_own_no_level(
        self, app, db, seed_user,
    ):
        """The rule ``ck_statement_imports_anchor_needs_a_claim`` stated in words.

        A NULL ``stated_balance`` on the referenced side matches no
        referencing row, so the same key refuses it -- structurally, with no
        CHECK to forget.
        """
        silent = _import(db, seed_user["account"], stated=None)

        with pytest.raises(IntegrityError) as raised:
            _level(db, seed_user["account"], silent, amount="0.00")

        assert _refused_by(db, raised) == (
            "fk_anchor_history_statement_import_claim"
        )

    def test_an_import_places_its_figure_on_AT_MOST_ONE_day(
        self, app, db, seed_user,
    ):
        """``uq_anchor_history_statement_import``; the owner's NULLs are distinct."""
        mine = _import(db, seed_user["account"])
        _level(db, seed_user["account"], mine, day=date(2026, 3, 3))

        with pytest.raises(IntegrityError) as raised:
            _level(db, seed_user["account"], mine, day=date(2026, 3, 2))

        assert _refused_by(db, raised) == "uq_anchor_history_statement_import"
        # Two owner rows on the same day are two facts, as they always were.
        for _ in range(2):
            db.session.add(AccountAnchorHistory(
                account_id=seed_user["account"].id,
                anchor_balance=Decimal("1.00"),
                observed_on=date(2026, 3, 3),
            ))
        db.session.flush()

    def test_an_OWNERS_row_takes_the_bottom_rung_by_default(
        self, app, db, seed_user,
    ):
        """``evidence_id`` is NOT NULL and an owner's default is uncorroborated.

        The one writer of an owner's level (``anchor_service``) names no
        evidence; the column's default is the rule, stated once on the model.
        """
        row = AccountAnchorHistory(
            account_id=seed_user["account"].id,
            anchor_balance=Decimal("500.00"),
            observed_on=date(2026, 3, 3),
        )
        db.session.add(row)
        db.session.flush()

        assert ref_cache.statement_balance_evidence_member(
            row.evidence_id,
        ) is _UNCORROBORATED
        assert row.statement_import_id is None
        with pytest.raises(IntegrityError):
            db.session.execute(text(
                "INSERT INTO budget.account_anchor_history "
                "(account_id, anchor_balance, observed_on, recorded_on, "
                " evidence_id) VALUES (:a, 1.00, '2026-03-03', '2026-03-03', "
                " NULL)"
            ), {"a": seed_user["account"].id})
        db.session.rollback()


class TestABankLevelLiesInsideItsFile:
    """``budget.level_lies_within_file``, attached to BOTH tables.

    The bound was ``ck_statement_imports_effective_day_within_file`` while the
    placed day lived on the import row; it spans two tables now, so it is a
    function with two attachments -- one on the level's INSERT, one on the
    file's span UPDATE -- and each attachment gets its control here.
    """

    @pytest.mark.parametrize(("period", "day"), [
        # Before the day before the first line.
        ((date(2026, 3, 1), date(2026, 3, 3)), date(2026, 2, 27)),
        # After the last line.
        ((date(2026, 3, 1), date(2026, 3, 3)), date(2026, 3, 4)),
        # Inside the lines but after the header's own day (stated 03-09).
        ((date(2026, 3, 1), date(2026, 3, 12)), date(2026, 3, 10)),
    ])
    def test_a_level_OUTSIDE_the_file_is_refused_on_insert(
        self, app, db, seed_user, period, day,
    ):
        """The INSERT arm, at each of the three bounds."""
        mine = _import(db, seed_user["account"], period=period)

        with pytest.raises(InternalError, match="level_lies_within_file"):
            _level(db, seed_user["account"], mine, day=day)
        db.session.rollback()

    def test_the_day_BEFORE_the_first_line_is_inside(self, app, db, seed_user):
        """A file whose figure IS its opening places it one day before its lines."""
        mine = _import(db, seed_user["account"])

        level = _level(db, seed_user["account"], mine, day=date(2026, 2, 28))

        assert level.id is not None

    def test_an_OWNERS_level_is_bounded_by_no_file(self, app, db, seed_user):
        """The arm asks nothing of a row naming no import."""
        db.session.add(AccountAnchorHistory(
            account_id=seed_user["account"].id,
            anchor_balance=Decimal("500.00"),
            observed_on=date(1999, 1, 1),
        ))
        db.session.flush()

    def test_moving_the_files_span_out_from_under_its_level_is_refused(
        self, app, db, seed_user,
    ):
        """The UPDATE arm: the hole an insert-only trigger would have left.

        ``UPDATE statement_imports SET period_end = ...`` beneath a placement
        committed cleanly under a trigger on the level alone; this is the
        control the design review asked for.
        """
        mine = _import(db, seed_user["account"])
        _level(db, seed_user["account"], mine, day=date(2026, 3, 3))
        # Committed, so each refusal's rollback leaves the world standing for
        # the next column -- a flushed-only world vanishes with the first
        # rollback and the second UPDATE would hit no row and refuse nothing.
        db.session.commit()
        import_id = mine.id

        for column, value in (
            ("period_end", "2026-03-02"),
            ("period_start", "2026-03-05"),
            ("stated_balance_on", "2026-03-02"),
        ):
            with pytest.raises(InternalError, match="level_lies_within_file"):
                db.session.execute(text(
                    f"UPDATE budget.statement_imports SET {column} = :v "
                    "WHERE id = :id"
                ), {"v": value, "id": import_id})
            db.session.rollback()

    def test_the_files_span_may_move_where_its_level_stays_inside(
        self, app, db, seed_user,
    ):
        """Positive control for the UPDATE arm: a wider span is admitted."""
        mine = _import(db, seed_user["account"])
        _level(db, seed_user["account"], mine, day=date(2026, 3, 3))

        db.session.execute(text(
            "UPDATE budget.statement_imports SET period_end = '2026-03-08' "
            "WHERE id = :id"
        ), {"id": mine.id})
        db.session.flush()


class TestARelease:
    """``budget.anchor_releases``: one withdrawal per level, keyed to it."""

    def test_a_level_is_withdrawn_AT_MOST_ONCE(self, app, db, seed_user):
        """``uq_anchor_releases_anchor``."""
        mine = _import(db, seed_user["account"])
        level = _level(db, seed_user["account"], mine)
        _release(db, level)

        with pytest.raises(IntegrityError) as raised:
            _release(db, level)

        assert _refused_by(db, raised) == "uq_anchor_releases_anchor"

    def test_a_release_naming_ANOTHER_accounts_level_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """``fk_anchor_releases_anchor_account``."""
        theirs = _import(db, seed_second_user["account"])
        level = _level(db, seed_second_user["account"], theirs)

        with pytest.raises(IntegrityError) as raised:
            db.session.add(AnchorRelease(
                account_id=seed_user["account"].id,
                anchor_id=level.id,
                released_by_import_id=None,
                lines_changed_from=date(2026, 3, 2),
            ))
            db.session.flush()

        assert _refused_by(db, raised) == "fk_anchor_releases_anchor_account"

    def test_a_release_naming_ANOTHER_accounts_import_as_cause_is_refused(
        self, app, db, seed_user, seed_second_user,
    ):
        """``fk_anchor_releases_import_account``."""
        mine = _import(db, seed_user["account"])
        level = _level(db, seed_user["account"], mine)
        theirs = _import(db, seed_second_user["account"], file_name="t.csv")

        with pytest.raises(IntegrityError) as raised:
            _release(db, level, by_import=theirs)

        assert _refused_by(db, raised) == "fk_anchor_releases_import_account"


class TestTheAppendOnlyRefusalOnTheTwoTables:
    """What the shared trigger refuses and what it PERMITS, table by table."""

    def test_a_bank_level_goes_WITH_its_import(self, app, db, seed_user):
        """Positive control: deleting an import with a placed level SUCCEEDS.

        The disposal the DELETE arm must admit; committed, because the arm is
        deferred and a delete that only flushed would report nothing.  The
        release naming that level cascades with it.
        """
        mine = _import(db, seed_user["account"])
        level = _level(db, seed_user["account"], mine)
        release = _release(db, level)
        db.session.commit()
        level_id, release_id = level.id, release.id

        db.session.delete(mine)
        db.session.commit()

        assert db.session.get(AccountAnchorHistory, level_id) is None
        assert db.session.get(AnchorRelease, release_id) is None

    def test_a_bank_level_may_NOT_be_picked_off_while_its_import_stands(
        self, app, db, seed_user,
    ):
        """The DELETE arm on the level, at COMMIT."""
        mine = _import(db, seed_user["account"])
        level = _level(db, seed_user["account"], mine)
        db.session.commit()

        db.session.execute(text(
            "DELETE FROM budget.account_anchor_history WHERE id = :id"
        ), {"id": level.id})
        with pytest.raises(InternalError, match="append-only; DELETE rejected"):
            db.session.commit()
        db.session.rollback()

    def test_a_release_may_NOT_be_picked_off_while_its_level_stands(
        self, app, db, seed_user,
    ):
        """The DELETE arm on the release: an un-release would reinstate a level."""
        mine = _import(db, seed_user["account"])
        level = _level(db, seed_user["account"], mine)
        release = _release(db, level)
        db.session.commit()

        db.session.execute(text(
            "DELETE FROM budget.anchor_releases WHERE id = :id"
        ), {"id": release.id})
        with pytest.raises(InternalError, match="append-only; DELETE rejected"):
            db.session.commit()
        db.session.rollback()

    def test_a_release_admits_ONE_update_and_refuses_every_other(
        self, app, db, seed_user,
    ):
        """The UPDATE arm: the key's own SET NULL passes; nothing else does.

        Four refusals -- the day, the level, nulling the cause while ALSO
        changing another column, and nulling the cause BY HAND while the
        import it names still stands (the hole the step's adversarial code
        review found: the transition is the referential action's, which runs
        only once the import is gone) -- and then the admitted transition,
        reached the one way it can be: by deleting the cause.
        """
        mine = _import(db, seed_user["account"])
        level = _level(db, seed_user["account"], mine)
        cause = _import(db, seed_user["account"], file_name="cause.csv")
        release = _release(db, level, by_import=cause)
        db.session.commit()
        release_id, cause_id = release.id, cause.id

        for statement in (
            "UPDATE budget.anchor_releases SET lines_changed_from = "
            "'2026-03-01' WHERE id = :id",
            "UPDATE budget.anchor_releases SET anchor_id = anchor_id + 1000 "
            "WHERE id = :id",
            "UPDATE budget.anchor_releases SET released_by_import_id = NULL, "
            "lines_changed_from = '2026-03-01' WHERE id = :id",
            "UPDATE budget.anchor_releases SET released_by_import_id = NULL "
            "WHERE id = :id",
        ):
            with pytest.raises(
                InternalError, match="append-only; UPDATE rejected",
            ):
                db.session.execute(text(statement), {"id": release_id})
            db.session.rollback()

        db.session.execute(text(
            "DELETE FROM budget.statement_imports WHERE id = :id"
        ), {"id": cause_id})
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(AnchorRelease, release_id).released_by_import_id is None

    def test_nulling_an_ALREADY_null_cause_is_not_the_admitted_transition(
        self, app, db, seed_user,
    ):
        """The transition needs an OLD cause: a no-op UPDATE is still an UPDATE."""
        mine = _import(db, seed_user["account"])
        level = _level(db, seed_user["account"], mine)
        release = _release(db, level)
        db.session.commit()

        with pytest.raises(InternalError, match="append-only; UPDATE rejected"):
            db.session.execute(text(
                "UPDATE budget.anchor_releases SET released_by_import_id = NULL "
                "WHERE id = :id"
            ), {"id": release.id})
        db.session.rollback()

    def test_TRUNCATE_of_the_releases_is_refused(self, app, db, seed_user):
        """The statement arm, the one spelling no row trigger sees."""
        with pytest.raises(InternalError, match="TRUNCATE rejected"):
            db.session.execute(text("TRUNCATE budget.anchor_releases"))
        db.session.rollback()

    def test_the_ORM_names_the_refusal(self, app, db, seed_user):
        """The listener pair: a named Shekel exception at the call site."""
        mine = _import(db, seed_user["account"])
        level = _level(db, seed_user["account"], mine)
        release = _release(db, level)

        db.session.commit()
        release_id = release.id

        release = db.session.get(AnchorRelease, release_id)
        release.lines_changed_from = date(2026, 3, 1)
        with pytest.raises(AnchorReleaseImmutableError, match="UPDATE rejected"):
            db.session.flush()
        db.session.rollback()

        release = db.session.get(AnchorRelease, release_id)
        db.session.delete(release)
        with pytest.raises(AnchorReleaseImmutableError, match="DELETE rejected"):
            db.session.flush()
        db.session.rollback()

    def test_an_account_takes_its_levels_and_releases_with_it(
        self, app, db, seed_user,
    ):
        """The original disposal path still passes with the two new owners."""
        account = create_account_of_type(
            seed_user, db.session, "Savings", "Disposable",
            anchor_balance=Decimal("100.00"),
        )
        db.session.commit()
        mine = _import(db, account)
        level = _level(db, account, mine)
        release = _release(db, level)
        db.session.commit()
        level_id, release_id, account_id = level.id, release.id, account.id

        db.session.execute(text(
            "DELETE FROM budget.accounts WHERE id = :id"
        ), {"id": account_id})
        db.session.commit()

        assert db.session.get(AccountAnchorHistory, level_id) is None
        assert db.session.get(AnchorRelease, release_id) is None
