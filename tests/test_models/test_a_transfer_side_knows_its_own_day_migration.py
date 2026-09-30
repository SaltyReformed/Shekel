"""Migration ``d3b8f5a1c7e2``: a transfer side knows its own day.

Plan step ``balance:X-bi-6-4c-3``, rulings **R-BAL143** (relabel, by
predicate) and **R-BAL165** (a pre-step typed day stays typed on both sides).
The revision seeds ``ref.settled_day_bases.borrowed`` and relabels every
transfer side whose basis its writer did not earn -- on the shadow and its
covering movement together -- and REFUSES a relabel the day function would
never produce.  No day, figure or link moves.

Driven against the test database, which is built at head, through the
revision's own module-level pieces: :func:`classify_sides` grades each arm of
the predicate on states the service writes (with the audit rows its triggers
write), :func:`refuse_unborrowable` fires on a side whose day is not its
sibling's, and the whole ``upgrade`` / ``downgrade`` pair is run over the
pre-step shapes, which the downgrade must restore exactly.

**Two arms cannot be told apart by the service alone here**, and are planted
where that is so: the test database's app writes and the migration run as ONE
role, so every write onto a settled dateless row reads as the backfill's
(``entered_guess``) until its audit row's ``db_user`` is re-labelled; and a
statement member that existed and was later removed is planted as the audit
INSERT the member's trigger would have written.
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.models.transaction import Transaction
from app.services import transfer_service
from app.services.settle_day import record_settle_day
from app.utils.dates import display_today
from tests._test_helpers import (
    an_asserted_day,
    an_entered_day,
    an_observed_day,
    create_savings_account,
    create_transfer,
    load_migration_module,
    on_both_sides,
    open_books_before_the_first_assertion,
    run_migration_callable,
)

_MIGRATION = load_migration_module(
    "d3b8f5a1c7e2_a_transfer_side_knows_its_own_day.py",
)


class TestTheRevision:
    """Where the revision sits in the chain."""

    def test_revision_and_down_revision(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "d3b8f5a1c7e2"
        assert _MIGRATION.down_revision == "c4a4e7d1b9f2"


def _transfer(seed_user, period, name):
    """Return a Projected $300.00 Checking -> Savings transfer named *name*."""
    savings = create_savings_account(
        seed_user, db.session, f"Savings {name}", Decimal("0.00"),
    )
    open_books_before_the_first_assertion(db.session, savings)
    xfer = create_transfer(
        seed_user, db.session, seed_user["account"], savings, period,
        amount=Decimal("300.00"), name=name,
    )
    db.session.commit()
    return xfer


def _shadows(xfer):
    """Return ``(expense, income)`` shadows of *xfer*."""
    rows = db.session.query(Transaction).filter_by(transfer_id=xfer.id).all()
    return (
        next(r for r in rows if r.account_id == xfer.from_account_id),
        next(r for r in rows if r.account_id == xfer.to_account_id),
    )


def _arms(xfer):
    """Return ``(expense arm, income arm)`` as :func:`classify_sides` files them."""
    arm_of = {
        shadow_id: arm
        for shadow_id, transfer_id, arm in _MIGRATION.classify_sides(
            db.session.connection(),
        )
        if transfer_id == xfer.id
    }
    expense, income = _shadows(xfer)
    return arm_of[expense.id], arm_of[income.id]


def _ny_today():
    """Return the DATABASE's America/New_York date for this transaction's ``now()``.

    The stamp arm compares a day with its own audit row's ``executed_at`` in
    that zone, and ``executed_at`` is ``now()`` -- so a stamp is built on this
    day, read in the same transaction as the write it describes.
    """
    return db.session.execute(
        text("SELECT (now() AT TIME ZONE 'America/New_York')::date"),
    ).scalar_one()


def _paid_press_before_the_step(seed_user, xfer):
    """Settle *xfer* as the pre-step Paid press did: status and an ``entered`` today, one write."""
    transfer_service.update_transfer(
        xfer.id, seed_user["user"].id,
        status_id=ref_cache.status_id(StatusEnum.DONE),
        side_days=on_both_sides(
            xfer.from_account_id, xfer.to_account_id,
            an_entered_day(_ny_today()),
        ),
    )
    db.session.commit()


def _link_the_expense_side(xfer):
    """Tick the expense side against Checking's latest assertion (the reconcile panel's link)."""
    anchor = (
        db.session.query(AccountAnchorHistory)
        .filter_by(account_id=xfer.from_account_id)
        .order_by(AccountAnchorHistory.id.desc())
        .first()
    )
    transfer_service.record_clearing(_shadows(xfer)[0], anchor.id)
    db.session.commit()


def _a_statement_once_named_the_expense_side(xfer):
    """Plant the audit INSERT a statement match member on the expense movement left.

    What every pre-step match wrote for the side it matched (the design's M3:
    one member per ``observed`` pair, on the Checking side).  Planted as the
    audit row rather than a member, because the predicate reads members EVER
    -- a member later removed still counts -- and that is the half this grades.
    """
    expense = _shadows(xfer)[0]
    (movement,) = [m for m in expense.entries if m.covers_settlement]
    db.session.execute(text(
        "INSERT INTO system.audit_log "
        "(table_schema, table_name, operation, row_id, new_data) "
        "VALUES ('budget', 'statement_match_members', 'INSERT', 1, "
        "CAST(:data AS jsonb))"
    ), {"data": json.dumps({"transaction_entry_id": movement.id})})
    db.session.commit()


class TestTheArms:
    """Each arm of the relabel predicate, on the state its writer leaves."""

    def test_a_pre_step_paid_press_is_a_stamp(self, app, seed_user, seed_periods_today):
        """Status into the band AND the write's own day, in one UPDATE: the press, not typing."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Stamp")
            _paid_press_before_the_step(seed_user, xfer)

            assert _arms(xfer) == ("entered_stamp", "entered_stamp")

    def test_a_day_typed_onto_a_settled_pair_is_typed(
        self, app, seed_user, seed_periods_today,
    ):
        """A later write of another day onto a pair already settled: the owner's own."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Typed")
            _paid_press_before_the_step(seed_user, xfer)
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(display_today() - timedelta(days=4)),
                ),
            )
            db.session.commit()

            assert _arms(xfer) == ("entered_typed", "entered_typed")

    def test_a_day_written_onto_a_dateless_settled_pair_is_a_guess_or_a_repair(
        self, app, seed_user, seed_periods_today,
    ):
        """The backfill's write is a guess; the same write by the APP role is a typed repair."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Dateless")
            _paid_press_before_the_step(seed_user, xfer)
            # The legacy shape (finding N-181): settled, no day, behind the
            # seam's back -- each covering movement un-dated with its row.
            for row in _shadows(xfer):
                record_settle_day(row, None)
                for movement in row.entries:
                    if movement.covers_settlement:
                        record_settle_day(movement, None)
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_entered_day(display_today() - timedelta(days=4)),
                ),
            )
            db.session.commit()

            assert _arms(xfer) == ("entered_guess", "entered_guess")

            db.session.execute(text(
                "UPDATE system.audit_log SET db_user = 'shekel_app' "
                "WHERE table_schema = 'budget' AND table_name = 'transactions' "
                "AND row_id IN (SELECT id FROM budget.transactions "
                "WHERE transfer_id = :t)"
            ), {"t": xfer.id})
            db.session.commit()

            assert _arms(xfer) == ("entered_app_repair", "entered_app_repair")

    def test_an_observed_side_no_statement_named_borrows(
        self, app, seed_user, seed_periods_today,
    ):
        """The matcher stated the bank's day for BOTH sides; no member ever named either."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Copied")
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_observed_day(display_today() - timedelta(days=4)),
                ),
            )
            db.session.commit()

            assert _arms(xfer) == ("observed_relabel", "observed_relabel")

    def test_a_movement_a_statement_ONCE_named_keeps_its_observation(
        self, app, seed_user, seed_periods_today,
    ):
        """A member since removed still counts: the predicate reads the member audit INSERTs."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Named")
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_observed_day(display_today() - timedelta(days=4)),
                ),
            )
            db.session.commit()
            _a_statement_once_named_the_expense_side(xfer)

            assert _arms(xfer) == ("observed_kept", "observed_relabel")

    def test_an_asserted_side_is_kept_only_where_the_tick_linked_it(
        self, app, seed_user, seed_periods_today,
    ):
        """The tick links its own leg; the day copied to the other side borrows."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Ticked")
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_asserted_day(display_today() - timedelta(days=4)),
                ),
            )
            db.session.commit()
            _link_the_expense_side(xfer)

            assert _arms(xfer) == ("asserted_kept", "asserted_relabel")

    def test_a_borrowed_side_is_left_alone(self, app, seed_user, seed_periods_today):
        """A side this step's own code wrote is already labelled, and is counted as kept."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Pressed")
            transfer_service.settle_transfer(xfer.id, seed_user["user"].id)
            db.session.commit()

            assert _arms(xfer) == ("borrowed_kept", "borrowed_kept")


class TestTheRefusal:
    """A relabel the day function would never produce is refused, naming the transfer."""

    def test_a_side_whose_day_is_not_its_siblings_is_refused_before_any_write(
        self, app, seed_user, seed_periods_today,
    ):
        """Two copied bank days that differ: neither can BORROW the other's.

        So the upgrade stops before any write.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Parted")
            owner = seed_user["user"].id
            transfer_service.settle_transfer(
                xfer.id, owner,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_observed_day(display_today() - timedelta(days=4)),
                ),
            )
            transfer_service.update_transfer(
                xfer.id, owner,
                side_days=(transfer_service.SideDay(
                    xfer.to_account_id,
                    an_observed_day(display_today() - timedelta(days=2)),
                ),),
            )
            db.session.commit()
            assert _arms(xfer) == ("observed_relabel", "observed_relabel")

            with pytest.raises(RuntimeError, match=f"First transfer ids: {xfer.id}"):
                run_migration_callable(_MIGRATION.upgrade, db.session)
            db.session.rollback()

            assert _arms(xfer) == ("observed_relabel", "observed_relabel"), (
                "the refusal relabelled a side before it refused"
            )

    def test_sides_on_one_day_pass(self, app, seed_user, seed_periods_today):
        """The control: the same copied day on both sides relabels without a refusal."""
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Together")
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_observed_day(display_today() - timedelta(days=4)),
                ),
            )
            db.session.commit()
            relabel = [shadow.id for shadow in _shadows(xfer)]

            _MIGRATION.refuse_unborrowable(db.session.connection(), relabel)


def _bases_by_name():
    """Return ``{(table, row id): basis name}`` for every dated transfer side and its movement."""
    rows = db.session.execute(text(
        "SELECT 't' AS tbl, t.id, b.name FROM budget.transactions t "
        "JOIN ref.settled_day_bases b ON b.id = t.settled_day_basis_id "
        "WHERE t.transfer_id IS NOT NULL "
        "UNION ALL "
        "SELECT 'e', e.id, b.name FROM budget.transaction_entries e "
        "JOIN budget.transactions t ON t.id = e.transaction_id "
        "JOIN ref.settled_day_bases b ON b.id = e.settled_day_basis_id "
        "WHERE t.transfer_id IS NOT NULL AND e.covers_settlement"
    )).all()
    return {(row.tbl, row.id): row.name for row in rows}


class TestTheRoundTrip:
    """``downgrade`` inverts ``upgrade`` on the pre-step pairs, save one shape.

    A pair BOTH of whose sides relabel comes back ``entered`` on both, which
    inverts a pair that was ``entered`` (the press below) and not one that
    was ``observed`` or ``asserted`` on both sides: that shape is pinned last,
    as the migration's docstring states it.
    """

    def test_up_then_down_restores_every_label_and_the_ref_row(
        self, app, seed_user, seed_periods_today,
    ):
        """Four pre-step pairs: a press, a typed day, a matched bank day, a linked tick.

        Each pair holds ONE day and ONE basis on both sides -- what every door
        wrote before this step.  The first downgrade turns the head database's
        pairs into exactly that and deletes the ref row; the upgrade relabels
        by predicate, shadows and movements together; the second downgrade
        must give back the first one's labels, byte for byte.
        """
        with app.app_context():
            period = seed_periods_today[3]
            press = _transfer(seed_user, period, "Press")
            _paid_press_before_the_step(seed_user, press)
            typed = _transfer(seed_user, period, "Typed")
            _paid_press_before_the_step(seed_user, typed)
            transfer_service.update_transfer(
                typed.id, seed_user["user"].id,
                side_days=on_both_sides(
                    typed.from_account_id, typed.to_account_id,
                    an_entered_day(display_today() - timedelta(days=4)),
                ),
            )
            copied = _transfer(seed_user, period, "Copied")
            ticked = _transfer(seed_user, period, "Ticked")
            for xfer, day in (
                (copied, an_observed_day(display_today() - timedelta(days=3))),
                (ticked, an_asserted_day(display_today() - timedelta(days=2))),
            ):
                transfer_service.settle_transfer(
                    xfer.id, seed_user["user"].id,
                    side_days=on_both_sides(
                        xfer.from_account_id, xfer.to_account_id, day,
                    ),
                )
            db.session.commit()
            _a_statement_once_named_the_expense_side(copied)
            _link_the_expense_side(ticked)

            run_migration_callable(_MIGRATION.downgrade, db.session)
            before = _bases_by_name()
            assert "borrowed" not in set(before.values())
            assert db.session.execute(text(
                "SELECT count(*) FROM ref.settled_day_bases WHERE name = 'borrowed'"
            )).scalar_one() == 0

            run_migration_callable(_MIGRATION.upgrade, db.session)
            after = _bases_by_name()
            expected = {
                press: ("borrowed", "borrowed"),
                typed: ("entered", "entered"),
                copied: ("observed", "borrowed"),
                ticked: ("asserted", "borrowed"),
            }
            for xfer, (expense_basis, income_basis) in expected.items():
                expense, income = _shadows(xfer)
                for shadow, basis in ((expense, expense_basis), (income, income_basis)):
                    assert after[("t", shadow.id)] == basis, xfer.name
                    (movement,) = [
                        m for m in shadow.entries if m.covers_settlement
                    ]
                    assert after[("e", movement.id)] == basis, (
                        f"{xfer.name}: the movement did not follow its shadow"
                    )

            run_migration_callable(_MIGRATION.downgrade, db.session)
            assert _bases_by_name() == before

    def test_a_pair_both_of_whose_sides_relabel_comes_back_entered(
        self, app, seed_user, seed_periods_today,
    ):
        """The one shape the downgrade cannot invert, pinned as the docstring states it.

        Both sides ``observed`` and no member ever naming either: both relabel,
        and with no evidenced sibling left to copy from, the downgrade gives
        both ``entered``.  The matcher records a member for the side it
        matched, so no door wrote this; the 2026-09-30 dump holds none.
        """
        with app.app_context():
            xfer = _transfer(seed_user, seed_periods_today[3], "Unnamed")
            transfer_service.settle_transfer(
                xfer.id, seed_user["user"].id,
                side_days=on_both_sides(
                    xfer.from_account_id, xfer.to_account_id,
                    an_observed_day(display_today() - timedelta(days=4)),
                ),
            )
            db.session.commit()

            run_migration_callable(_MIGRATION.downgrade, db.session)
            run_migration_callable(_MIGRATION.upgrade, db.session)
            run_migration_callable(_MIGRATION.downgrade, db.session)

            bases = _bases_by_name()
            for shadow in _shadows(xfer):
                assert bases[("t", shadow.id)] == "entered"
