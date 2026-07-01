"""Tests for the Build-Order Step 4 loan-payment posting service.

:mod:`app.services.loan_posting_service` posts the REAL principal / interest /
escrow / refund split of a confirmed loan payment as a balanced correction on
top of the Step-2 cash entry.  Commit 4 is PURE -- the service is not yet wired
into the transfer chokepoints (that is Commit 5) -- so these tests drive the
service functions directly after setting up a loan and settling payment
transfers through ``transfer_service`` (which auto-posts the Step-2 cash entry).

The split fixtures are SYNTHETIC with HAND-COMPUTED literals: a $100,000 balance
at 6% gives a clean $500.00 first-month interest (``100000 * 0.06 / 12``), so
every expected interest / principal / escrow / refund is computed by hand in the
test and shown in the docstring's arithmetic.  The loan's user-trueup anchor
($100,000) deliberately differs from its origination principal ($250,000), so an
asserted $500 interest also PROVES the split seeds from the latest anchor, not
origination (a built-in non-vacuity check).

All money is ``Decimal`` from strings.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
    TxnTypeEnum,
)
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.loan_params import LoanParams
from app.models.transaction import Transaction
from app.services import loan_posting_service, posting_service
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    insert_trueup_event,
    ledger_accounts_for_account,
)

# A 6% loan on a $100,000 anchor accrues exactly $500.00 the first month
# (100000 * 0.06 / 12); the round numbers keep every split hand-computable.
_ANCHOR_BALANCE = Decimal("100000.00")
_RATE = Decimal("0.06000")
_ANCHOR_DATE = date(2026, 1, 10)
_AS_OF = date(2026, 12, 31)
# Distinct from the anchor so a correct interest figure proves the walk seeds
# from the trueup anchor, not the (larger) origination principal.
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _loan_params(loan):
    """Return the loan account's :class:`LoanParams` row."""
    return _db.session.query(LoanParams).filter_by(account_id=loan.id).one()


def _make_loan(
    seed_user,
    *,
    anchor_balance=_ANCHOR_BALANCE,
    anchor_date=_ANCHOR_DATE,
    rate=_RATE,
    escrow_annual=None,
):
    """Create an amortizing loan with a controlled user-trueup anchor.

    Routes through the shared ``create_loan_account`` factory (origination
    anchor + rate), then appends a ``user_trueup`` anchor at *anchor_balance* /
    *anchor_date* -- the latest event, so the split walk seeds from it -- and an
    optional active escrow component.  Commits so the loan is fully resolvable.
    """
    loan = create_loan_account(
        seed_user, _db.session, name="Split Loan",
        principal=_ORIGINATION_PRINCIPAL, rate=rate, term=360,
        origination_date=_ORIGINATION_DATE, payment_day=1,
    )
    insert_trueup_event(_loan_params(loan), anchor_balance, anchor_date)
    if escrow_annual is not None:
        _db.session.add(EscrowComponent(
            account_id=loan.id, name="Tax & Insurance",
            annual_amount=escrow_annual,
        ))
    _db.session.commit()
    return loan


def _add_rate_change(loan, effective_date, rate):
    """Append a :class:`RateHistory` rate change (recasting the period)."""
    _db.session.add(RateHistory(
        account_id=loan.id, effective_date=effective_date, interest_rate=rate,
    ))
    _db.session.commit()


def _settle_payment(seed_user, loan, period, cash, actual=None):
    """Settle a Checking -> loan payment transfer; return its income shadow.

    Creates and settles the transfer through ``transfer_service`` (so the
    Step-2 cash entry auto-posts), then returns the loan-side income shadow the
    Step-4 correction books under.
    """
    xfer = create_settled_transfer(
        seed_user, _db.session, seed_user["account"], loan, period,
        amount=cash, actual_amount=actual,
    )
    return xfer, _income_shadow(xfer.id, loan.id)


def _income_shadow(transfer_id, loan_id):
    """Return the loan-side income shadow of a transfer."""
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    return (
        _db.session.query(Transaction)
        .filter_by(
            transfer_id=transfer_id,
            account_id=loan_id,
            transaction_type_id=income_type_id,
        )
        .one()
    )


def _linked_ledger_id(account):
    """Return the linked (Asset/Liability) ledger account id for *account*."""
    return ledger_accounts_for_account(_db.session, account.id)[0].id


def _find_loan_ledger(loan_id, kind):
    """Return the per-loan ledger account of *kind*, or None if not created."""
    return (
        _db.session.query(LedgerAccount)
        .filter_by(
            loan_account_id=loan_id,
            kind_id=ref_cache.ledger_account_kind_id(kind),
        )
        .one_or_none()
    )


def _ledger_net(ledger_id, scenario_id):
    """Return the net of all posting legs on a ledger account in a scenario."""
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == ledger_id,
            JournalEntry.scenario_id == scenario_id,
        )
        .scalar()
    )


def _transfer_filtered_loan_net(transfer_id, ledger_id):
    """Sum a transfer's postings on one ledger -- replicating ``_posted_net``.

    Mirrors the Step-2 cash reader ``posting_service._posted_net`` exactly
    (``JournalEntry.transfer_id == transfer_id`` on one ledger), WITHOUT calling
    the private helper, so a test can prove a Step-4 correction (which carries a
    NULL ``transfer_id``) is invisible to that reader.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.transfer_id == transfer_id,
            Posting.ledger_account_id == ledger_id,
        )
        .scalar()
    )


def _correction_entries(shadow_id):
    """Return the loan_payment correction entries booked under a shadow."""
    return (
        _db.session.query(JournalEntry)
        .filter_by(
            transaction_id=shadow_id,
            source_kind_id=ref_cache.posting_source_id(
                PostingSourceEnum.LOAN_PAYMENT
            ),
        )
        .order_by(JournalEntry.id)
        .all()
    )


def _entry_legs(entry_id):
    """Return ``{ledger_account_id: (amount, posting_kind_id)}`` for an entry."""
    return {
        leg.ledger_account_id: (leg.amount, leg.posting_kind_id)
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


# Period indices (seed_periods starts 2026-01-02, biweekly) whose monthly
# due date (payment_day=1) lands in a DISTINCT month after the anchor:
#   period 1 start 2026-01-16 -> due 2026-02-01
#   period 3 start 2026-02-13 -> due 2026-03-01
#   period 5 start 2026-03-13 -> due 2026-04-01
_P1, _P2, _P3 = 1, 3, 5


# ---------------------------------------------------------------------------
# compute_loan_payment_splits -- hand-computed splits (the core)
# ---------------------------------------------------------------------------


class TestComputeLoanPaymentSplits:
    """The real split is computed from the actual cash, hand-computed literals."""

    def test_on_schedule_single_payment(self, app, db, seed_user, seed_periods):
        """One $1,000 payment: interest 500.00, principal 500.00, no escrow.

        Arithmetic: interest = round(100000 * 0.06 / 12) = 500.00; principal =
        cash - interest - escrow = 1000 - 500 - 0 = 500.00; excess = 0.  The
        500.00 interest also proves the walk seeds from the $100,000 trueup
        anchor (origination principal is $250,000, which would give $1,250).
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert len(splits) == 1
            split = splits[0]
            assert split.interest == Decimal("500.00")
            assert split.escrow == Decimal("0.00")
            assert split.principal == Decimal("500.00")
            assert split.excess == Decimal("0.00")

    def test_running_balance_across_payments(
        self, app, db, seed_user, seed_periods,
    ):
        """Three $1,000 payments accrue interest on the shrinking real balance.

        Arithmetic (6%/12 = 0.5% monthly):
          P1: interest round(100000.00*0.005)=500.00, principal 500.00,
              balance 99500.00
          P2: interest round(99500.00*0.005)=497.50, principal 502.50,
              balance 98997.50
          P3: interest round(98997.50*0.005)=494.99, principal 505.01,
              balance 98492.49
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            for period in (seed_periods[_P1], seed_periods[_P2], seed_periods[_P3]):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("500.00"), Decimal("500.00")),
                (Decimal("497.50"), Decimal("502.50")),
                (Decimal("494.99"), Decimal("505.01")),
            ]

    def test_extra_principal_lands_in_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,500 payment pays $1,000 principal -- the extra is captured.

        Arithmetic: interest 500.00; principal = 1500 - 500 - 0 = 1000.00.  The
        resolver's contractual replay would book only the scheduled principal
        and need a true-up; the ledger captures the extra automatically.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1500.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].principal == Decimal("1000.00")
            assert splits[0].excess == Decimal("0.00")

    def test_short_payment_gives_negative_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $400 payment under-covers interest: principal -100.00, balance rises.

        Arithmetic: interest 500.00; principal = 400 - 500 - 0 = -100.00
        (negative amortization, surfaced not clamped); the next-period balance
        would be 100000 - (-100) = 100100.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("400.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].interest == Decimal("500.00")
            assert splits[0].principal == Decimal("-100.00")
            assert splits[0].excess == Decimal("0.00")

    def test_configured_escrow_is_subtracted(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,200/yr escrow component subtracts $100.00/mo from principal.

        Arithmetic: monthly escrow = round(1200 / 12) = 100.00; interest 500.00;
        principal = 1000 - 500 - 100 = 400.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user, escrow_annual=Decimal("1200.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].escrow == Decimal("100.00")
            assert splits[0].principal == Decimal("400.00")

    def test_payoff_overpayment_routes_excess_to_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """Overpaying a $300 balance caps principal and refunds the rest.

        Arithmetic (anchor balance 300.00 @ 6%): interest round(300*0.005)=1.50;
        principal0 = 1000 - 1.50 - 0 = 998.50 > 300 -> principal capped at
        300.00, excess = 998.50 - 300.00 = 698.50; balance closes at 0.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].interest == Decimal("1.50")
            assert splits[0].principal == Decimal("300.00")
            assert splits[0].excess == Decimal("698.50")

    def test_payment_after_payoff_is_all_refund(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment on an already-closed loan is entirely a refund.

        Arithmetic: P1 (anchor 300 @ 6%) closes the loan (balance 0); P2's
        $500 cash accrues no interest and no escrow on the closed loan, so
        principal 0.00 and the whole 500.00 routes to refund.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("500.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert len(splits) == 2
            assert splits[1].interest == Decimal("0.00")
            assert splits[1].escrow == Decimal("0.00")
            assert splits[1].principal == Decimal("0.00")
            assert splits[1].excess == Decimal("500.00")

    def test_arm_rate_step_changes_interest(
        self, app, db, seed_user, seed_periods,
    ):
        """A mid-history rate step to 12% changes the later payment's interest.

        Arithmetic: P1 (period start 2026-01-16, governed by the 6% origination
        rate) interest 500.00, principal 500.00, balance 99500.00.  A rate
        change effective 2026-03-01 to 12% governs P2 (period start 2026-03-13):
        interest = round(99500 * 0.12 / 12) = 995.00, principal = 1000 - 995 =
        5.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _add_rate_change(loan, date(2026, 3, 1), Decimal("0.12000"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P3], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("500.00"), Decimal("500.00")),
                (Decimal("995.00"), Decimal("5.00")),
            ]

    def test_actual_amount_drives_the_split(
        self, app, db, seed_user, seed_periods,
    ):
        """The split uses effective (actual) cash -- the adversarial non-vacuity proof.

        Settling with a $1,300 ACTUAL over a $1,000 estimate must move principal
        to 1300 - 500 = 800.00 (not the estimate's 500.00); a split that read
        ``transfers.amount`` instead of the shadow's effective amount would
        fail here.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
                actual=Decimal("1300.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits[0].principal == Decimal("800.00")

    def test_pre_anchor_payment_is_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment whose due date precedes the anchor is not replayed.

        With the anchor moved to 2026-04-15, period 1's payment (pay-period
        start 2026-01-16, due 2026-02-01) came due BEFORE the anchor, so it is
        already baked into that balance and excluded -- leaving zero splits.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_date=date(2026, 4, 15))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits == []

    def test_projected_payment_is_excluded(
        self, app, db, seed_user, seed_periods,
    ):
        """An unsettled (Projected) payment is a future commitment, not history."""
        with app.app_context():
            loan = _make_loan(seed_user)
            # Settle one, then un-settle it back to Projected directly.
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            db.session.query(Transaction).filter(
                Transaction.transfer_id == shadow.transfer_id,
            ).update({"status_id": ref_cache.status_id(StatusEnum.PROJECTED)})
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits == []

    def test_no_loan_params_returns_empty(self, app, db, seed_user):
        """An account with no LoanParams is not yet resolvable -- no splits."""
        with app.app_context():
            checking = seed_user["account"]  # a plain Checking, no LoanParams
            splits = loan_posting_service.compute_loan_payment_splits(
                checking.id, seed_user["scenario"].id, _AS_OF,
            )
            assert splits == []


# ---------------------------------------------------------------------------
# sync_loan_payment_postings -- posts the balanced correction
# ---------------------------------------------------------------------------


class TestSyncLoanPaymentPostings:
    """Syncing posts one balanced correction per payment; the loan nets to principal."""

    def test_sync_posts_one_balanced_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """The correction is Loan -500 / Interest +500, summing to zero.

        Arithmetic: cash 1000, interest 500, principal 500.  The loan-linked
        ledger nets Step-2 cash (+1000) + correction loan leg (-500) = +500 ==
        principal; the loan_interest ledger nets +500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            entries = _correction_entries(shadow.id)
            assert len(entries) == 1
            entry = entries[0]
            assert entry.transfer_id is None
            assert entry.transaction_id == shadow.id

            loan_ledger = _linked_ledger_id(loan)
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            legs = _entry_legs(entry.id)
            assert legs[loan_ledger] == (
                Decimal("-500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
            )
            assert legs[interest_ledger.id] == (
                Decimal("500.00"),
                ref_cache.posting_kind_id(PostingKindEnum.INTEREST),
            )
            # Loan nets to the real principal; interest ledger holds the interest.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("500.00")
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("500.00")

    def test_sync_posts_each_payment_in_a_multi_payment_loan(
        self, app, db, seed_user, seed_periods,
    ):
        """Three payments each get a correction; the loan nets to summed principal.

        Arithmetic (the running-balance walk from
        ``test_running_balance_across_payments``): principals 500.00 + 502.50 +
        505.01 = 1507.51, so the loan-linked ledger nets the three Step-2 cash
        legs (+3000) plus the three correction loan legs (-1492.49) = 1507.51 ==
        anchor 100000 - final balance 98492.49.  A second whole-loan sync writes
        nothing (idempotent across every payment).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            shadows = []
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _, shadow = _settle_payment(
                    seed_user, loan, period, Decimal("1000.00"),
                )
                shadows.append(shadow)
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert all(len(_correction_entries(s.id)) == 1 for s in shadows)
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("1507.51")

            # Idempotent across the whole loan: a re-sync adds no entries.
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert all(len(_correction_entries(s.id)) == 1 for s in shadows)

    def test_resync_is_idempotent(self, app, db, seed_user, seed_periods):
        """A second sync at the same target writes no new entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert len(_correction_entries(shadow.id)) == 1

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert len(_correction_entries(shadow.id)) == 1

    def test_no_escrow_loan_drops_the_escrow_leg(
        self, app, db, seed_user, seed_periods,
    ):
        """A loan with no escrow components creates no loan_escrow ledger."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # no escrow component
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            assert _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_ESCROW,
            ) is None

    def test_payoff_posts_a_refund_leg(self, app, db, seed_user, seed_periods):
        """A payoff overpayment books a refund-receivable leg.

        Arithmetic (anchor 300 @ 6%, cash 1000): interest 1.50, principal
        300.00, excess 698.50.  The loan_refund ledger nets +698.50; the loan
        nets Step-2 cash 1000 - correction 700 = +300.00 == principal.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("300.00"))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            refund_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_REFUND,
            )
            assert refund_ledger is not None
            assert _ledger_net(refund_ledger.id, scenario_id) == Decimal("698.50")
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("300.00")

    def test_sync_never_touches_checking(
        self, app, db, seed_user, seed_periods,
    ):
        """The loan sync moves only loan ledgers -- Checking is unchanged.

        The Step-2 cash entry already moved Checking (-1000); the loan
        correction must not move it further, so Checking's posted total is
        identical before and after the sync.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            loan = _make_loan(seed_user)
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            checking_before = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            checking_after = posting_service.account_posting_total(
                checking.id, scenario_id,
            )
            assert checking_before == Decimal("-1000.00")
            assert checking_after == checking_before

    def test_correction_is_invisible_to_transfer_id_reader(
        self, app, db, seed_user, seed_periods,
    ):
        """The Step-2 cash reader (transfer_id-keyed) never sees the correction.

        The CRITICAL invariant (plan Section 5): the correction carries a NULL
        ``transfer_id``, so a reader filtering ``transfer_id == xfer.id`` on the
        loan ledger sums only the Step-2 cash (+1000), NOT the correction
        (-500) -- which is what keeps the cash path's reversals correct.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            xfer, _ = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_ledger = _linked_ledger_id(loan)
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            # The transfer-id-keyed reader sees only the cash leg.
            assert _transfer_filtered_loan_net(
                xfer.id, loan_ledger,
            ) == Decimal("1000.00")
            # But the full ledger (cash + correction) nets to principal.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("500.00")

    def test_anchor_move_reverses_pre_anchor_and_resplits_survivors(
        self, app, db, seed_user, seed_periods,
    ):
        """A new anchor reverses now-pre-anchor corrections AND re-splits the rest.

        P1 (period 1, due 02-01) and P2 (period 3, due 03-01) settle and sync
        against the 100000 @ 01-10 anchor, accruing interest 500.00 and
        round(99500*0.005)=497.50 = 997.50 total.  A new user-trueup anchor of
        90000 @ 02-15 then both pushes P1 pre-anchor (due 02-01 <= 02-15) and
        re-seeds P2 from 90000.  After re-sync the loan_interest ledger holds
        ONLY P2's re-split interest round(90000*0.005)=450.00 -- P1's 500.00
        reversed, P2's 497.50 re-split to 450.00 -- and P1 carries an
        original-plus-reversal entry pair.  This exercises the running-balance
        coupling and the stale reversal together (the hardest self-heal path).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, p1_shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            _settle_payment(
                seed_user, loan, seed_periods[_P2], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )
            assert _ledger_net(
                interest_ledger.id, scenario_id,
            ) == Decimal("997.50")

            # New anchor at 90000 on 02-15 subsumes P1 (due 02-01) and re-bases P2.
            insert_trueup_event(
                _loan_params(loan), Decimal("90000.00"), date(2026, 2, 15),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            # Only P2's re-split interest remains; P1's was reversed.
            assert _ledger_net(
                interest_ledger.id, scenario_id,
            ) == Decimal("450.00")
            # P1's correction was posted then reversed (original + reversal).
            assert len(_correction_entries(p1_shadow.id)) == 2

    def test_correction_is_disjoint_from_the_transaction_path(
        self, app, db, seed_user, seed_periods,
    ):
        """The Step-3 transaction poster refuses a loan income shadow (no-op).

        The Step-3 reader ``_posted_net_by_account`` is source-kind-agnostic, so
        the correction's ``transaction_id`` is safe only because the Step-3 PATH
        guards ``if txn.transfer_id is not None: return None`` -- and a loan
        income shadow always has a ``transfer_id``.  This pins that guard:
        after a loan payment is settled and synced, driving the income shadow
        through ``sync_transaction_postings`` posts nothing.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            entries_before = len(_correction_entries(shadow.id))

            result = posting_service.sync_transaction_postings(
                shadow, settled=True,
            )
            db.session.commit()

            assert result is None
            assert len(_correction_entries(shadow.id)) == entries_before


# ---------------------------------------------------------------------------
# reverse + stale-correction reversal
# ---------------------------------------------------------------------------


class TestReverseLoanPaymentPostings:
    """A correction reverses cleanly before a delete, and stale ones self-heal."""

    def test_reverse_zeroes_the_correction(
        self, app, db, seed_user, seed_periods,
    ):
        """Reversing a payment's correction returns the loan ledger to cash-only.

        After the reverse, the per-shadow loan_payment net is zero on every
        ledger, so the loan-linked ledger holds only the Step-2 cash (+1000)
        and the interest ledger nets to 0.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            interest_ledger = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.LOAN_INTEREST,
            )

            loan_posting_service.reverse_loan_payment_postings_for_shadow(shadow)
            db.session.commit()

            # The correction net (cash leg + reversal) is zero everywhere.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("1000.00")
            assert _ledger_net(interest_ledger.id, scenario_id) == Decimal("0")

    def test_reverse_is_a_noop_for_an_unposted_shadow(
        self, app, db, seed_user, seed_periods,
    ):
        """Reversing a never-synced payment writes nothing."""
        with app.app_context():
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            loan_posting_service.reverse_loan_payment_postings_for_shadow(shadow)
            db.session.commit()
            assert _correction_entries(shadow.id) == []

    def test_resync_reverses_an_unsettled_payment(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment that leaves the eligible set is reversed by the next sync.

        Settle + sync (one correction), then un-settle the payment (directly,
        standing in for the Commit-5 revert wiring) and re-sync: the now-stale
        correction is reversed to zero, so the loan ledger returns to cash-only.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            _, shadow = _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("500.00")

            # Un-settle (revert) the payment directly, then re-sync.
            db.session.query(Transaction).filter(
                Transaction.transfer_id == shadow.transfer_id,
            ).update({"status_id": ref_cache.status_id(StatusEnum.PROJECTED)})
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            # The correction is reversed; the loan holds only the Step-2 cash.
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("1000.00")
