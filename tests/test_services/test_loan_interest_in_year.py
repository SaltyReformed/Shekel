"""C3c: balance_at.loan_interest_in_year -- interest PAID in a year, one producer.

Plan step **C3c** (``docs/audits/balance_architecture/README.md``).  Direct unit
coverage of the balance seam's ONE loan-interest producer, which replaced the
ledger-reader-plus-schedule HYBRID that lived in ``tax_report_service``
(``_compute_mortgage_interest`` / ``_loan_year_interest``, both deleted).  The LIVE
Schedule-A wiring and hand-computed value stay in ``test_tax_report_service.py``
(``TestScheduleAMortgageInterest``); these pin the producer's INTERNALS the live
value test alone does not reach:

* the SETTLED (past) interest is the FOLD's actual per-payment figure -- correct
  for an off-schedule payment where the schedule replay is not -- attributed to
  each payment's DISPLAY-timezone civil paid YEAR (the L9 tax basis, which diverges
  from the balance ledger's UTC clock across the New Year);
* the PROJECTED (future) interest is the schedule's still-unconfirmed rows, with
  the settled-slot MERGE that keeps an early-settled installment from counting
  twice (the de-dup relocated here from the retired hybrid -- it survives until
  step C6 replaces schedule rows with payment records);
* the figure comes from the loan's SOURCE events, so it answers even for a loan the
  posting cache cannot value (no genesis opening posting) -- closing B-6, where the
  old hybrid fell back to the schedule and reported the wrong number.

Relocated from the retired ``test_tax_mortgage_interest.py`` (which tested the
deleted hybrid); the value assertions are hand-computed, never the producer as its
own oracle (plan N-7).
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.extensions import db
from app.services import balance_at, net_worth_kernel
from app.services.loan_posting_service import confirmed_loan_interest_in_year
from app.services.resolution_context import BalanceContext
from tests._test_helpers import (
    SPLIT_LOAN,
    clear_loan_ledger,
    create_loan_with_trueup,
    create_settled_transfer,
    freeze_today,
)

ZERO = Decimal("0.00")


def _paid_utc(year, month, day):
    """Return a noon-UTC settle instant, so its civil date is unambiguous."""
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def _split_loan(seed_user):
    """Create a bare SPLIT_LOAN (origination + true-up), no payments yet."""
    (orig_principal, orig_date, rate, anchor_balance,
     anchor_date, _p1, _p2, _p3) = SPLIT_LOAN
    return create_loan_with_trueup(
        seed_user, db.session, origination_principal=orig_principal,
        anchor_balance=anchor_balance, anchor_date=anchor_date, rate=rate,
        origination_date=orig_date,
    )


def _unconfirmed_year_interest(loan, ctx, year, *, exclude_slots=frozenset()):
    """Sum the loan's unconfirmed schedule interest in *year* (test-side oracle).

    An INDEPENDENT recomputation of the producer's projected term from the raw
    schedule rows, so the test asserts the producer sums the two halves rather than
    checking a value against itself.
    """
    return sum(
        (
            row.interest
            for row in net_worth_kernel.debt_schedule_rows([loan], ctx)[loan.id]
            if not row.is_confirmed
            and row.payment_date.year == year
            and (row.payment_date.year, row.payment_date.month) not in exclude_slots
        ),
        ZERO,
    )


class TestLoanInterestInYearValue:
    """The figure is the FOLD's actual interest, on the paid-date (display) basis."""

    def test_current_year_is_fold_actual_plus_projected(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Off-schedule confirmed history (fold-actual) + the year's projection.

        Trued to $100,000 @ 6% on 2026-01-10, then a $2,000 (extra) and a $1,000
        payment, both PAID mid-2026 and both begun by the frozen 2026-04-01:

          P1 ($2,000, due 02-01): interest round(100000 * 0.005) = 500.00.
          P2 ($1,000, due 03-01): interest round(98500 * 0.005) = 492.50 -- on the
            REAL balance the extra payment left, NOT the schedule replay's ~99,900.

        Fold-actual 2026 interest = 992.50, which the schedule replay does not
        reproduce.  The producer is that fold-actual PLUS the year's still-projected
        rows, both halves non-zero.  The fold-actual equals the posting reader (the
        postings are a projection of the same fold), so the settled half is pinned
        two independent ways.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 4, 1))
            scenario_id = seed_user["scenario"].id
            _, _, _, _, _, p1, p2, _ = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("2000.00"),
                paid_at=_paid_utc(2026, 2, 15),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p2], amount=Decimal("1000.00"),
                paid_at=_paid_utc(2026, 3, 15),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            # The settled half, hand-computed AND equal to the posting reader.
            fold_actual = confirmed_loan_interest_in_year(loan.id, scenario_id, 2026)
            assert fold_actual == Decimal("992.50")

            # P1/P2 have begun by 2026-04-01, so their rows are confirmed and out
            # of the projection; no early-settled slot to exclude here.
            projected = _unconfirmed_year_interest(loan, ctx, 2026)
            assert projected > ZERO  # genuine hybrid, not a vacuous zero

            result = balance_at.loan_interest_in_year(loan, ctx, 2026)
            assert result == fold_actual + projected

    def test_interest_deducts_in_the_year_it_was_paid(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """A 2026-scheduled payment PAID in 2025 deducts in 2025 (pure fold).

        Mortgage interest deducts in the year PAID.  A period-``P1`` payment
        (scheduled 2026-02-01) settled 2025-12-20 attributes its 500.00 interest to
        2025 -- a year with NO schedule rows (the loan's first row is 2026), so the
        figure is PURE fold and the projection is structurally zero.  A
        payment-DATE basis would report 0.00 for 2025 (the negative control).
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            _, _, _, _, _, p1, _p2, _p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                paid_at=_paid_utc(2025, 12, 20),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            # 2025 carries no schedule rows, so the figure is pure fold.
            assert _unconfirmed_year_interest(loan, ctx, 2025) == ZERO
            assert balance_at.loan_interest_in_year(
                loan, ctx, 2025,
            ) == Decimal("500.00")

    def test_new_years_eve_evening_settle_deducts_in_the_display_year(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """THE L9 CASE: 8:05 PM EST Dec 31 deducts in the OLD (display) year.

        A payment settled 2025-12-31 20:05 Eastern is stored 2026-01-01 01:05 UTC,
        so the balance ledger's UTC clock (and the fold's visible date) is already
        2026.  The tax figure follows the user's WALL clock (L9): the 500.00
        interest deducts in 2025.  2025 has NO schedule rows, so the figure is pure
        fold -- and a UTC-basis attribution (the fold's balance clock) would report
        0.00 here, which is exactly the defect that made interest-in-year a
        dedicated display-clock function rather than the fold's UTC-keyed
        cum-interest.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            _, _, _, _, _, p1, _p2, _p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                paid_at=datetime(2026, 1, 1, 1, 5, tzinfo=timezone.utc),
            )
            db.session.commit()
            ctx = BalanceContext.build(seed_user["user"].id)

            assert balance_at.loan_interest_in_year(
                loan, ctx, 2025,
            ) == Decimal("500.00")


class TestLoanInterestInYearMerge:
    """One row per installment: an early-settled slot is not counted twice."""

    def test_early_settled_slot_counted_exactly_once(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """An early-settled payment's due slot leaves the projected term.

        A payment settled BEFORE its pay period begins is in the FOLD (its actual
        interest, at its paid date) yet its schedule row stays
        ``is_confirmed=False`` -- so without the settled-slot merge its installment
        counts twice.

        Frozen 2026-02-10: P1 (due 02-01, begun) and P3 (due 04-01, EARLY) both
        settle.  Fold interest = P1 500.00 (100000 * 0.005) + P3 497.50
        (round(99500 * 0.005)) = 997.50, both paid 2026.  The producer is fold +
        projection MINUS the April slot; a naive fold + every unconfirmed row reads
        HIGHER by exactly the April row's interest -- the double count the merge
        kills.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 2, 10))
            scenario_id = seed_user["scenario"].id
            _, _, _, _, _, p1, _p2, p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                paid_at=_paid_utc(2026, 2, 5),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p3], amount=Decimal("1000.00"),
                paid_at=_paid_utc(2026, 2, 10),
            )
            db.session.commit()
            # Premise: P3's period has not begun by the frozen today (early settle).
            assert seed_periods[p3].start_date > date(2026, 2, 10)
            ctx = BalanceContext.build(seed_user["user"].id)

            fold_actual = confirmed_loan_interest_in_year(loan.id, scenario_id, 2026)
            assert fold_actual == Decimal("997.50")

            # The April (P3 due) slot still projects an unconfirmed row ...
            debt = net_worth_kernel.debt_schedule_rows([loan], ctx)[loan.id]
            april_rows = [
                row for row in debt
                if not row.is_confirmed
                and (row.payment_date.year, row.payment_date.month) == (2026, 4)
            ]
            assert len(april_rows) == 1
            assert april_rows[0].interest > ZERO
            naive_projected = _unconfirmed_year_interest(loan, ctx, 2026)

            # ... but the producer excludes P1's (02) and P3's (04) settled slots.
            result = balance_at.loan_interest_in_year(loan, ctx, 2026)
            assert result == fold_actual + naive_projected - april_rows[0].interest
            assert result < fold_actual + naive_projected  # the merge fired


class TestLoanInterestAnswersFromTheFold:
    """B-6: the figure comes from source events, not the posting cache."""

    def test_cleared_ledger_still_answers_from_the_fold(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """No genesis opening posting -> the FOLD answers, where the old hybrid did not.

        The old hybrid asked the posting reader, which returns ``None`` for a loan
        with no opening posting, and then fell back to the schedule -- reporting
        0.00 for a 2025 payment (no 2025 rows) that genuinely paid 500.00 of
        interest.  The producer folds the loan's SOURCE events, so a cleared posting
        cache does not change the answer.  This is B-6 closed: the interest figure
        and the balance both come from the total fold.
        """
        with app.app_context():
            freeze_today(monkeypatch, date(2026, 6, 1))
            scenario_id = seed_user["scenario"].id
            _, _, _, _, _, p1, _p2, _p3 = SPLIT_LOAN
            loan = _split_loan(seed_user)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[p1], amount=Decimal("1000.00"),
                paid_at=_paid_utc(2025, 12, 20),
            )
            db.session.commit()

            ctx = BalanceContext.build(seed_user["user"].id)
            before = balance_at.loan_interest_in_year(loan, ctx, 2025)
            assert before == Decimal("500.00")

            # Break the posting cache: no opening posting -> the reader declines.
            clear_loan_ledger(loan.id)
            db.session.commit()
            assert confirmed_loan_interest_in_year(
                loan.id, scenario_id, 2025,
            ) is None

            # The fold-based producer is unchanged: it never read the postings.
            ctx_after = BalanceContext.build(seed_user["user"].id)
            assert balance_at.loan_interest_in_year(
                loan, ctx_after, 2025,
            ) == Decimal("500.00")
