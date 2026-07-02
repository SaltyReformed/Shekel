"""Tests for the genesis loan posting service (Build-Order Step 4 + read switch).

:mod:`app.services.loan_posting_service` posts a loan's confirmed history into the
double-entry ledger: the per-payment principal / interest / escrow / refund split
(layered on the Step-2 cash entry), and the once-per-loan OPENING plus every user
TRUE-UP as balanced anchor corrections.  Both derive from ONE chronological
running-balance walk that seeds at origination and RESETS at each anchor.  The
payment split is wired into the transfer chokepoints (auto-posted on settle via
``transfer_service``); the anchor corrections (``sync_loan_anchor_corrections``)
are inert -- these tests drive them directly.

The split fixtures are SYNTHETIC with HAND-COMPUTED literals: a $100,000 balance
at 6% gives a clean $500.00 first-month interest (``100000 * 0.06 / 12``), so
every expected interest / principal / escrow / refund is computed by hand and
shown in the docstring's arithmetic.  The loan's user-trueup anchor ($100,000)
deliberately differs from its origination principal ($250,000): under genesis the
walk seeds from origination and resets at the trueup, so an asserted $500 (vs the
$1,250 the origination balance would give) PROVES the reset, while a payment
placed BEFORE the trueup asserting $1,250 proves origination seeding (built-in
non-vacuity checks).

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
)
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.loan_features import EscrowComponent, RateHistory
from app.models.loan_params import LoanParams
from app.models.transaction import Transaction
from app.services import loan_posting_service, posting_service, transfer_service
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_settled_transfer,
    find_loan_ledger_account,
    insert_trueup_event,
    ledger_accounts_for_account,
    ledger_net,
    loan_correction_entries,
    loan_income_shadow,
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

    Delegates to the shared ``create_loan_with_trueup`` factory, pinning this
    suite's fixed origination principal / date (distinct from the anchor, so a
    correct interest figure proves the walk seeds from the trueup anchor).
    """
    return create_loan_with_trueup(
        seed_user, _db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=_ORIGINATION_DATE, escrow_annual=escrow_annual,
    )


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
    """Return the loan-side income shadow of a transfer (shared query helper)."""
    return loan_income_shadow(_db.session, transfer_id, loan_id)


def _linked_ledger_id(account):
    """Return the linked (Asset/Liability) ledger account id for *account*."""
    return ledger_accounts_for_account(_db.session, account.id)[0].id


def _find_loan_ledger(loan_id, kind):
    """Return the per-loan ledger account of *kind*, or None (shared helper)."""
    return find_loan_ledger_account(_db.session, loan_id, kind)


def _ledger_net(ledger_id, scenario_id):
    """Return the net of a ledger account's posting legs (shared query helper)."""
    return ledger_net(_db.session, ledger_id, scenario_id)


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
    """Return the loan_payment corrections under a shadow (shared query helper)."""
    return loan_correction_entries(_db.session, shadow_id)


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

    def test_escrow_change_is_effective_dated_not_retroactive(
        self, app, db, seed_user, seed_periods,
    ):
        """Each payment's escrow is the version in effect ON its date, and a
        LATER escrow change never re-splits an already-past payment.

        Two escrow versions: $1,200/yr ($100.00/mo) from origination until
        2026-03-01, then $2,400/yr ($200.00/mo).  P1's pay-period start
        (2026-01-16) is in the first version -> escrow 100.00; the later
        payment's start (2026-03-13) is in the second -> escrow 200.00.  Then a
        THIRD version ($3,600/yr) effective 2026-06-01 (the second closed there)
        must leave BOTH earlier splits unchanged -- proving the split is
        immutable for a past date, the whole point of effective-dating escrow
        (the pre-fix code recomputed every payment at the current escrow, so the
        third change would have retroactively moved both to $300.00).
        """
        with app.app_context():
            loan = _make_loan(seed_user)  # no escrow via the helper
            # V1: $100/mo from origination, removed 2026-03-01.
            db.session.add(EscrowComponent(
                account_id=loan.id, name="Escrow",
                annual_amount=Decimal("1200.00"),
                effective_date=_ORIGINATION_DATE, end_date=date(2026, 3, 1),
            ))
            # V2: $200/mo from 2026-03-01 (open).
            db.session.add(EscrowComponent(
                account_id=loan.id, name="Escrow",
                annual_amount=Decimal("2400.00"),
                effective_date=date(2026, 3, 1),
            ))
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
            # Chronological: P1 start 2026-01-16 (V1 $100); P_late start
            # 2026-03-13 (V2 $200).  Distinct escrow proves the as-of keying.
            assert [s.escrow for s in splits] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

            # A future escrow change: close V2 at 2026-06-01 (flush first so the
            # active-name partial unique frees), then add a $300/mo V3.  Neither
            # past payment's date falls in V3's range, so both splits must hold.
            v2 = (
                db.session.query(EscrowComponent)
                .filter_by(account_id=loan.id, annual_amount=Decimal("2400.00"))
                .one()
            )
            v2.end_date = date(2026, 6, 1)
            db.session.flush()
            db.session.add(EscrowComponent(
                account_id=loan.id, name="Escrow",
                annual_amount=Decimal("3600.00"),
                effective_date=date(2026, 6, 1),
            ))
            db.session.commit()

            resplits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert [s.escrow for s in resplits] == [
                Decimal("100.00"), Decimal("200.00"),
            ]

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

    def test_pre_trueup_payment_is_split_from_origination(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due before the trueup is split from the ORIGINATION reset.

        Genesis walks EVERY confirmed payment from origination and applies each
        anchor as a running-balance reset, so a payment due BEFORE the trueup is
        NOT excluded (the Step-4 behavior) -- it is split on the origination
        balance the walk carries until the trueup resets it.  With the trueup
        moved to 2026-04-15, period 1's payment (pay-period start 2026-01-16, due
        2026-02-01) precedes it and accrues on the $250,000 origination principal:
        interest = round(250000 * 0.06 / 12) = 1250.00;
        principal = 1000 - 1250 - 0 = -250.00 (negative amortization, surfaced).
        The 1250.00 (vs the trueup's 500.00) is what proves the origination reset.
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
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")
            assert splits[0].principal == Decimal("-250.00")

    def test_mid_life_loan_resets_at_trueup(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment before the trueup splits from origination; one after, from the trueup.

        The genesis reset proven with a payment on each side of a trueup dated
        2026-02-15 (origination $250,000 @ 6%, trueup $100,000):
          P1 (period 1, due 2026-02-01, PRE-trueup): interest
            round(250000 * 0.005) = 1250.00, principal 1000 - 1250 = -250.00.
          Reset to 100000 at the 2026-02-15 trueup.
          P2 (period 3, due 2026-03-01, POST-trueup): interest
            round(100000 * 0.005) = 500.00, principal 1000 - 500 = 500.00.
        A from-origination walk with NO reset would accrue P2 on ~250000, not
        100000 -- so the distinct 500.00 is the reset's signature.
        """
        with app.app_context():
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 15))
            _settle_payment(seed_user, loan, seed_periods[_P1], Decimal("1000.00"))
            _settle_payment(seed_user, loan, seed_periods[_P2], Decimal("1000.00"))
            db.session.commit()

            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, seed_user["scenario"].id, _AS_OF,
            )
            assert [(s.interest, s.principal) for s in splits] == [
                (Decimal("1250.00"), Decimal("-250.00")),
                (Decimal("500.00"), Decimal("500.00")),
            ]

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

    def test_new_trueup_resplits_later_payments_and_keeps_earlier(
        self, app, db, seed_user, seed_periods,
    ):
        """A new trueup re-splits payments after it and KEEPS those before it.

        Genesis retires the Step-4 "pushed behind anchor" reversal: a payment
        before a new trueup is NOT reversed -- it keeps its split from the prior
        anchor, and only LATER payments re-split from the new value.

        P1 (period 1, due 02-01) and P2 (period 3, due 03-01) settle and sync
        against the 100000 @ 01-10 trueup: interest 500.00 and
        round(99500 * 0.005) = 497.50 = 997.50 total.  A NEW trueup of 90000 @
        02-15 falls BETWEEN them, so after re-sync P1 keeps its 500.00 (it is due
        before the new trueup) and P2 re-splits from 90000 to
        round(90000 * 0.005) = 450.00 -- the loan_interest ledger holds
        500 + 450 = 950.00, and P1's correction is NOT reversed (one entry, where
        Step 4 left an original-plus-reversal pair).
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

            # New trueup at 90000 on 02-15 falls between P1 (due 02-01) and P2
            # (due 03-01): it re-bases P2 but leaves P1 untouched.
            insert_trueup_event(
                _loan_params(loan), Decimal("90000.00"), date(2026, 2, 15),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            # P1's 500 kept, P2 re-split 497.50 -> 450: interest ledger 950.
            assert _ledger_net(
                interest_ledger.id, scenario_id,
            ) == Decimal("950.00")
            # P1's correction is NOT reversed -- genesis keeps pre-trueup splits.
            assert len(_correction_entries(p1_shadow.id)) == 1

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
        """Reversing a payment that carries no posted correction writes nothing.

        Commit 5 wires the split-posting into the transfer chokepoints, so a
        SETTLED loan payment auto-posts its correction; a genuinely unposted
        shadow is therefore a PROJECTED payment (never settled -> never
        synced).  Reversing it must be an idempotent no-op -- no entry written
        -- which is what the delete path relies on for a never-settled payment.
        """
        with app.app_context():
            loan = _make_loan(seed_user)
            xfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=loan.id,
                    pay_period_id=seed_periods[_P1].id,
                    scenario_id=seed_user["scenario"].id,
                    amount=Decimal("1000.00"),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            db.session.commit()
            shadow = _income_shadow(xfer.id, loan.id)

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


# ---------------------------------------------------------------------------
# sync_loan_anchor_corrections -- opening + true-up (the genesis read switch)
# ---------------------------------------------------------------------------


def _anchor_correction_entries(loan_id, scenario_id, source_enum):
    """Return a loan's anchor-correction entries of *source_enum* (linked-scoped).

    Finds the ``loan_opening`` / ``loan_trueup`` journal entries in *scenario_id*
    that touch the loan's linked ledger -- the way the reconcile scopes them to
    one loan (the linked ledger is per-account).
    """
    linked_id = ledger_accounts_for_account(_db.session, loan_id)[0].id
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                source_enum,
            ),
            JournalEntry.id.in_(
                _db.session.query(Posting.journal_entry_id)
                .filter(Posting.ledger_account_id == linked_id)
            ),
        )
        .all()
    )


class TestSyncLoanAnchorCorrections:
    """The opening + true-up corrections drive the linked ledger to -(owed)."""

    def test_opening_posts_original_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """An origination-only loan posts the opening: linked -P0, equity +P0.

        A loan with only its origination anchor ($250,000) and no payments posts
        ONE opening entry: owed_before is 0 at origination, so the loan-linked
        ledger gets 0 - 250000 = -250000.00 and the per-loan opening-equity ledger
        its negative, +250000.00.  The linked net is then -250000, so the reader
        (which negates it) reports the full $250,000 owed.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(
                seed_user, db.session, principal=_ORIGINATION_PRINCIPAL,
                rate=_RATE, origination_date=_ORIGINATION_DATE,
            )
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            equity = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _ledger_net(equity.id, scenario_id) == Decimal("250000.00")
            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) == 1
            assert _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_and_trueup_drive_linked_to_negative_verified(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + true-up land the linked net on -(verified balance).

        Origination $250,000 then a trueup to $100,000 (no payments):
          opening (owed_before 0): linked 0 - 250000 = -250000.00.
          trueup (owed_before 250000, the origination balance carried to the
            trueup date): linked 250000 - 100000 = +150000.00.
        Linked net -250000 + 150000 = -100000.00 -- exactly -(the $100,000 the
        user verified), the opening + trueup reproducing the resolver's anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)  # origination 250000, trueup 100000
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            linked_id = ledger_accounts_for_account(db.session, loan.id)[0].id
            equity = _find_loan_ledger(
                loan.id, LedgerAccountKindEnum.EQUITY_OPENING,
            )
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
            assert _ledger_net(equity.id, scenario_id) == Decimal("100000.00")

            opening = _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )
            trueup = _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )
            assert len(opening) == 1 and len(trueup) == 1
            assert _entry_legs(opening[0].id)[linked_id][0] == Decimal("-250000.00")
            assert _entry_legs(trueup[0].id)[linked_id][0] == Decimal("150000.00")

    def test_anchor_corrections_are_idempotent(
        self, app, db, seed_user, seed_periods,
    ):
        """A second sync at the same state writes no new anchor entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            before = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) + len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            assert before == 2

            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            after = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_OPENING,
            )) + len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            assert after == 2

    def test_trueup_matching_the_walk_books_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """A trueup equal to the walked balance posts no true-up entry.

        Origination $250,000 and a trueup ALSO of $250,000 (no payments): at the
        trueup date owed_before is 250000, so the correction 250000 - 250000 = 0
        books nothing.  Only the opening is posted; the linked net is -250000.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_balance=Decimal("250000.00"))
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-250000.00")
            assert _anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ) == []

    def test_opening_payments_trueup_reproduce_resolver_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """Opening + payment corrections + true-up net to -(resolver balance).

        The genesis payoff: three post-trueup $1,000 payments split to principal
        500.00 + 502.50 + 505.01 = 1507.51 (the running-balance walk from the
        $100,000 trueup), so the resolver's balance is 100000 - 1507.51 =
        98492.49.  With the opening (-250000) and true-up (+150000) added, the
        loan-linked net is (1507.51 payment nets) - 100000 = -98492.49 -- exactly
        -(the resolver balance), proving the from-origination sum-of-postings
        reproduces the resolver on a trued-up loan.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user)
            for period in (
                seed_periods[_P1], seed_periods[_P2], seed_periods[_P3],
            ):
                _settle_payment(seed_user, loan, period, Decimal("1000.00"))
            db.session.commit()

            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()

            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-98492.49")

    def test_trueup_self_heals_when_a_pre_trueup_payment_is_added(
        self, app, db, seed_user, seed_periods,
    ):
        """Adding a pre-trueup payment re-bases the true-up to hold -(verified).

        The true-up correction is owed_before - verified, and owed_before moves
        when a pre-trueup payment changes -- so the reconcile must self-heal, not
        leave a stale snapshot.  Origination $250,000, trueup $100,000 @
        2026-02-15: the opening (-250000) and true-up (250000 - 100000 = +150000)
        net the linked ledger to -100000.00.  Settling P1 (due 02-01, PRE-trueup,
        cash 1000) posts its split (interest round(250000 * 0.005) = 1250,
        principal -250) and grows owed_before to 250250 -- leaving the true-up
        STALE at +150000, so the linked net drifts to -100250.00.  Re-syncing the
        anchor corrections re-bases the true-up to 250250 - 100000 = +150250 (a
        +250 delta), returning the linked net to -100000.00 -- exactly -(the
        verified $100,000), the pre-trueup payment fully absorbed.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 15))
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")

            # A pre-trueup payment grows owed_before; the true-up goes stale.
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100250.00")  # stale true-up, pre-anchor split posted

            # Re-sync the anchor corrections: the true-up re-bases and self-heals.
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")  # self-healed (original + a +250 delta)

            # A further sync is a no-op: no new entry, the healed balance holds.
            trueups_after_heal = len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            ))
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert len(_anchor_correction_entries(
                loan.id, scenario_id, PostingSourceEnum.LOAN_TRUEUP,
            )) == trueups_after_heal
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")

    def test_payment_due_on_anchor_date_is_subsumed_by_the_reset(
        self, app, db, seed_user, seed_periods,
    ):
        """A payment due EXACTLY on the anchor date is walked before the reset, then subsumed.

        The strict ``anchor_date < monthly_due_date`` boundary means a payment due
        ON an anchor's date is PRE-anchor (subsumed).  The walk realises this by
        sorting a payment (tag 0) before an anchor (tag 1) on an equal date, so P1
        (period 1, due 2026-02-01 at payment_day=1) is split on the $250,000
        origination balance BEFORE the 2026-02-01 trueup resets to $100,000:
        interest round(250000 * 0.005) = 1250.00 lands in the interest ledger, but
        its principal is wiped by the reset -- the reader ends at the verified
        $100,000, exactly as the resolver (which does not replay P1).  A SWAPPED
        tie-break (reset first) would instead give interest 500.00 and a balance of
        99500.00, so this pins the ordering the whole trued-up-balance design rests
        on (no other fixture places a due date on an anchor date).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            # Trueup date EQUALS P1's 2026-02-01 due date (payment_day=1).
            loan = _make_loan(seed_user, anchor_date=date(2026, 2, 1))
            _settle_payment(
                seed_user, loan, seed_periods[_P1], Decimal("1000.00"),
            )
            db.session.commit()

            # P1 is split BEFORE the reset -> interest on the origination balance.
            splits = loan_posting_service.compute_loan_payment_splits(
                loan.id, scenario_id, _AS_OF,
            )
            assert len(splits) == 1
            assert splits[0].interest == Decimal("1250.00")

            # With opening + true-up the reader lands on the verified 100000 --
            # P1's principal subsumed by the reset (a swapped tie-break -> -99500).
            loan_posting_service.sync_loan_payment_postings(
                loan.id, scenario_id, _AS_OF,
            )
            loan_posting_service.sync_loan_anchor_corrections(
                loan.id, scenario_id, _AS_OF,
            )
            db.session.commit()
            assert posting_service.account_posting_total(
                loan.id, scenario_id,
            ) == Decimal("-100000.00")
