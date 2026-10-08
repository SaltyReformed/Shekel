"""Plan step ``balance:X-bi-6-4d-2``, design D8: a side's record follows its transfer's status.

:mod:`app.side_band_infrastructure`'s deferred constraint trigger: a
transfer's side record is DATED exactly while the transfer is settled, and a
settled transfer records both sides or -- closed at ``$0.00`` -- neither
(rulings **R-BAL82**, **R-BAL90**, **R-BAL141**; a revert keeps each record
un-dated, ruling **R-BAL61**).  The two drifts the balance arc's specification
says are "made unrepresentable here" -- a dated record under an unsettled
transfer (ruling **R-BAL79**'s "counted once" case) and an un-dated one under
a settled transfer (ruling **R-BAL147**'s ``UndatedSettleError``) -- are
refused by the database.

Each case plants its state the way a writer nobody enumerated would -- the
bare ORM and raw SQL, around every door -- and COMMITS, because the rule is
deferred: a case that only flushed would pass whatever the rule said.  Each
refusal sits beside the control that commits.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app import ref_cache
from app.enums import MovementFigureSourceEnum, SettledDayBasisEnum, StatusEnum
from app.extensions import db as _db
from app.models.transaction_entry import TransactionEntry
from app.services.settle_day import SettleDay
from app.side_band_infrastructure import (
    SIDE_BAND_TRIGGERS,
    apply_side_band_infrastructure,
)
from tests._test_helpers import (
    create_account_of_type,
    create_transfer,
    load_migration_module,
    refused_by_database_rule,
)

_MIGRATION = load_migration_module(
    "e616adf7fe22_a_transfer_side_s_payment_hangs_off_the_transfer.py",
)

#: The rule's two refusals, as the database words them.
_SETTLED_REFUSED = r"transfer \d+ is settled but its sides hold"
_UNSETTLED_REFUSED = r"transfer \d+ is not settled but \d+ of its payment records"


def _day(seed_user):
    """A day after every account's books opened."""
    return seed_user["bootstrap_period"].start_date + timedelta(days=1)


def _transfer(seed_user):
    """A Projected $500.00 Checking -> Savings transfer, committed."""
    savings = create_account_of_type(
        seed_user, _db.session, "Savings", "Band Savings",
        anchor_balance=Decimal("2000.00"),
        observed_on=seed_user["bootstrap_period"].start_date,
    )
    xfer = create_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_user["bootstrap_period"], amount=Decimal("500.00"),
    )
    _db.session.commit()
    return xfer


def _set_status(xfer, status):
    """Write *xfer*'s status in raw SQL, around the seam."""
    _db.session.execute(
        text("UPDATE budget.transfers SET status_id = :status WHERE id = :id"),
        {"status": ref_cache.status_id(status), "id": xfer.id},
    )


def _record(xfer, seed_user, *, income=False, dated=True):
    """Stage one side's payment record of *xfer*, dated on :func:`_day` or not."""
    day = _day(seed_user)
    entry = TransactionEntry(
        expense_transfer_id=None if income else xfer.id,
        income_transfer_id=xfer.id if income else None,
        account_id=xfer.to_account_id if income else xfer.from_account_id,
        owner_id=xfer.user_id,
        user_id=xfer.user_id,
        amount=Decimal("500.00"),
        description="Band record",
        purchased_on=day,
        covers_settlement=True,
        figure_source_id=ref_cache.movement_figure_source_id(
            MovementFigureSourceEnum.RESOLVED,
        ),
    )
    if dated:
        entry.settled_on = day
        entry.settled_day_basis_id = SettleDay(
            day=day, basis=SettledDayBasisEnum.OBSERVED,
        ).basis_id
    _db.session.add(entry)
    return entry


def _commit_refused(match):
    """Expect the next COMMIT to be refused by the band rule's *match*, then roll back."""
    with refused_by_database_rule(match):
        _db.session.commit()
    _db.session.rollback()


class TestAnUnsettledTransfersRecordsAreUnDated:
    """Out of the settled band, no side's record is dated."""

    def test_a_dated_record_under_a_projected_transfer_is_refused(
        self, app, seed_user,
    ):
        """R-BAL79's drift: money dated on a transfer that has not settled."""
        del app
        xfer = _transfer(seed_user)
        _record(xfer, seed_user)
        _commit_refused(_UNSETTLED_REFUSED)

    def test_two_kept_records_under_a_projected_transfer_commit(
        self, app, seed_user,
    ):
        """CONTROL: a revert's shape, both sides kept un-dated (ruling R-BAL61)."""
        del app
        xfer = _transfer(seed_user)
        _record(xfer, seed_user, dated=False)
        _record(xfer, seed_user, income=True, dated=False)
        _db.session.commit()

    def test_reverting_a_transfer_whose_records_stay_dated_is_refused(
        self, app, seed_user,
    ):
        """The transfers attachment: the status leaves the band, the records do not."""
        del app
        xfer = _transfer(seed_user)
        _set_status(xfer, StatusEnum.DONE)
        _record(xfer, seed_user)
        _record(xfer, seed_user, income=True)
        _db.session.commit()

        _set_status(xfer, StatusEnum.PROJECTED)
        _commit_refused(_UNSETTLED_REFUSED)

    def test_a_revert_un_dating_both_in_one_transaction_commits(
        self, app, seed_user,
    ):
        """CONTROL: the writer's own revert, status and both records in one unit."""
        del app
        xfer = _transfer(seed_user)
        _set_status(xfer, StatusEnum.DONE)
        expense = _record(xfer, seed_user)
        income = _record(xfer, seed_user, income=True)
        _db.session.commit()

        _set_status(xfer, StatusEnum.PROJECTED)
        for record in (expense, income):
            record.settled_on = None
            record.settled_day_basis_id = None
        _db.session.commit()


class TestASettledTransferRecordsBothSidesOrNeither:
    """In the settled band, each side holds one dated record, or neither side holds one."""

    def test_an_undated_record_under_a_paid_transfer_is_refused(
        self, app, seed_user,
    ):
        """R-BAL147's drift: a settled payment whose record says it did not move."""
        del app
        xfer = _transfer(seed_user)
        _set_status(xfer, StatusEnum.DONE)
        _record(xfer, seed_user)
        _record(xfer, seed_user, income=True, dated=False)
        _commit_refused(_SETTLED_REFUSED)

    def test_a_record_on_one_side_only_is_refused(self, app, seed_user):
        """Money that left Checking and never arrived anywhere."""
        del app
        xfer = _transfer(seed_user)
        _set_status(xfer, StatusEnum.DONE)
        _record(xfer, seed_user)
        _commit_refused(_SETTLED_REFUSED)

    def test_a_paid_transfer_with_no_records_commits(self, app, seed_user):
        """CONTROL: the $0.00 close stores no record on either side."""
        del app
        xfer = _transfer(seed_user)
        _set_status(xfer, StatusEnum.DONE)
        _db.session.commit()

    def test_a_paid_transfer_with_both_sides_dated_commits(self, app, seed_user):
        """CONTROL: the settled shape every settle writes."""
        del app
        xfer = _transfer(seed_user)
        _set_status(xfer, StatusEnum.DONE)
        _record(xfer, seed_user)
        _record(xfer, seed_user, income=True)
        _db.session.commit()


class TestTheRulesAttachments:
    """The attachments the database carries are the ones the module names."""

    def test_the_constant_names_every_attachment_and_each_has_a_when(self, app):
        """pg_trigger agrees with SIDE_BAND_TRIGGERS; each is deferred and guarded by WHEN."""
        del app
        found = _db.session.execute(text(
            "SELECT t.tgname, c.relnamespace::regnamespace::text || '.' "
            "|| c.relname, t.tgqual IS NOT NULL, t.tgdeferrable, "
            "t.tginitdeferred "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE t.tgname = ANY(:names) AND NOT t.tgisinternal "
            "ORDER BY t.tgname"
        ), {"names": [name for name, _table in SIDE_BAND_TRIGGERS]}).all()
        assert [(row[0], row[1]) for row in found] == sorted(SIDE_BAND_TRIGGERS)
        assert all(row[2] and row[3] and row[4] for row in found), found

    def test_an_unknown_arm_is_refused(self):
        """A revision cannot name an arm the module does not build."""
        with pytest.raises(ValueError, match="unknown arm"):
            apply_side_band_infrastructure(lambda _sql: None, arms=("nope",))


class TestTheRevisionsCensus:
    """``e616adf7fe22`` refuses to install the rule over a stored transfer it would refuse."""

    def test_an_off_band_transfer_refuses_the_revision(self, app, seed_user):
        """A dated record under a Projected transfer, staged but not committed, is named."""
        del app
        xfer = _transfer(seed_user)
        _record(xfer, seed_user)
        _db.session.flush()
        with pytest.raises(RuntimeError, match=rf"\({xfer.id}, False, 1, 0\)"):
            _MIGRATION.refuse_off_band_transfers(_db.session.connection())
        _db.session.rollback()

    def test_a_clean_database_passes_the_census(self, app, seed_user):
        """CONTROL: a settled pair and a kept pair, both in band, refuse nothing."""
        del app
        settled = _transfer(seed_user)
        _set_status(settled, StatusEnum.DONE)
        _record(settled, seed_user)
        _record(settled, seed_user, income=True)
        _db.session.commit()
        _MIGRATION.refuse_off_band_transfers(_db.session.connection())
