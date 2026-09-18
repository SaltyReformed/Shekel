"""The cash readers RESET at the owner's levels and at nothing else (``balance:X-bj-1``).

Ruling **R-JN**: the level relation holds the bank's placements beside the
owner's true-ups from this step, and until ``X-f3c-5`` the cash fold keeps
resetting at the owner's rows alone -- a bank's closing is an observation that
moves no balance (**R-IS**), and a step that moves no money may not replay it.
Every cash-side reader composes ONE predicate
(:func:`app.utils.balance_predicates.owner_declared_clause`), and each is
graded here against a world holding a bank level that would move its answer
if it leaked through: on the production clone the leak moved Checking's fold
by ``$578.71`` and its ``asserted_total`` by the same, so this is the axis the
defect lives on.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from app import ref_cache
from app.enums import StatementBalanceEvidenceEnum, StatementSourceEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.statement_import import StatementImport
from app.services import cash_ledger
from app.services.statement_import import bank_levels


def _bank_level(account, *, amount, day, period):
    """Stage one statement placing *amount* on *day*, and return its level."""
    statement = StatementImport(
        account_id=account.id,
        user_id=account.user_id,
        source_id=ref_cache.statement_source_id(
            StatementSourceEnum.SECU_CHECKING_CSV
        ),
        file_name="bank.csv",
        file_digest="bank.csv".ljust(64, "0"),
        declared_start=period[0],
        declared_end=period[1],
        stated_balance=Decimal(amount),
        stated_balance_on=period[1],
    )
    db.session.add(statement)
    db.session.flush()
    level = AccountAnchorHistory(
        account_id=account.id,
        anchor_balance=Decimal(amount),
        observed_on=day,
        evidence_id=ref_cache.statement_balance_evidence_id(
            StatementBalanceEvidenceEnum.FILE_CHAIN,
        ),
        statement_import_id=statement.id,
    )
    db.session.add(level)
    db.session.flush()
    return level


class TestABankLevelResetsNothingUntilTheFlip:
    """One world: the seeded owner's origination, one bank level after it."""

    def _world(self, seed_user):
        """A bank level LATER than every owner row, so a leak would govern."""
        account = seed_user["account"]
        owner = cash_ledger.resolve_anchor(account)
        later_day = owner.observed_on + timedelta(days=23)
        bank = _bank_level(
            account, amount="9999.00", day=later_day,
            period=(owner.observed_on, later_day),
        )
        # The relation holds both; the bank row is the newer day.
        assert bank.observed_on > owner.observed_on
        assert [level.statement_import_id for level, _ in bank_levels(
            account.id,
        )] == [bank.statement_import_id]
        return account, owner, bank

    def test_the_resolver_still_answers_the_OWNERS_latest(
        self, app, db, seed_user,
    ):
        """``resolve_anchor`` / ``governing_anchor``: the grid header's figure."""
        account, owner, _bank = self._world(seed_user)

        assert cash_ledger.resolve_anchor(account).anchor_id == owner.anchor_id
        assert cash_ledger.governing_anchor(account.id).anchor_id == (
            owner.anchor_id
        )

    def test_the_write_doors_compare_against_the_OWNERS_row(
        self, app, db, seed_user,
    ):
        """``governing_anchor_on``: ruling R-EQ's did-this-change compare."""
        account, owner, bank = self._world(seed_user)

        governing = cash_ledger.governing_anchor_on(account.id, bank.observed_on)

        assert governing.anchor_id == owner.anchor_id
        assert governing.balance != Decimal("9999.00")

    def test_the_replay_holds_the_OWNERS_rows_alone(self, app, db, seed_user):
        """``cash_anchor_facts``: THE RESET.  The bank row is not replayed."""
        account, _owner, bank = self._world(seed_user)

        facts = cash_ledger.cash_anchor_facts(account.id)

        assert bank.id not in {fact.anchor_id for fact in facts}
        assert all(
            fact.anchor_balance != Decimal("9999.00") for fact in facts
        )

    def test_the_clearing_boundary_is_the_OWNERS_latest_day(
        self, app, db, seed_user,
    ):
        """``reconciled_through``: a bank placement extends no clearing."""
        account, owner, _bank = self._world(seed_user)

        assert cash_ledger.reconciled_through(account.id).observed_day == (
            owner.observed_on
        )

    def test_the_books_bound_reads_the_OWNERS_earliest(
        self, app, db, seed_user,
    ):
        """``earliest_assertion_day``: a bank level EARLIER than every owner row.

        The one reader whose leak would move in the other direction, so its
        bank row is placed BEFORE the origination rather than after it.
        """
        account = seed_user["account"]
        owner = cash_ledger.resolve_anchor(account)
        earlier = owner.observed_on - timedelta(days=365)
        _bank_level(
            account, amount="1.00", day=earlier,
            period=(earlier, earlier),
        )

        assert cash_ledger.earliest_assertion_day(account.id) == (
            owner.observed_on
        )

    def test_an_OWNERS_true_up_still_governs_as_before(
        self, app, db, seed_user,
    ):
        """The predicate narrows to the owner; it does not narrow the owner.

        A true-up appended through the one writer is read by every reader
        above, which is the half a wrong predicate (say, ``IS NOT NULL``)
        would break while every case above still passed.
        """
        account, _owner, bank = self._world(seed_user)
        later = AccountAnchorHistory(
            account_id=account.id,
            anchor_balance=Decimal("4242.00"),
            observed_on=bank.observed_on,
        )
        db.session.add(later)
        db.session.flush()

        assert cash_ledger.resolve_anchor(account).anchor_id == later.id
        assert cash_ledger.reconciled_through(account.id).observed_day == (
            bank.observed_on
        )
        assert later.id in {
            fact.anchor_id
            for fact in cash_ledger.cash_anchor_facts(account.id)
        }
        assert cash_ledger.resolve_anchor(account).balance == Decimal("4242.00")
