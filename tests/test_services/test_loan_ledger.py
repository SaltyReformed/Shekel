"""The loan fold: ``app.services.loan_ledger.fold_loan_balances``.

Plan step B1 (``docs/audits/balance_architecture/README.md``).  These tests pin
the fold's CONTRACT -- totality, the delta each event contributes, and the two
visibility rules it reproduces -- against HAND-COMPUTED figures.

**Hand-computed, deliberately.**  The fold and the posted ledger share the walk
(that is the design: the postings are a PROJECTION of the fold), so grading the
fold by comparing it to the ledger would be two readers of one walk agreeing --
"two wrong implementations agreeing is not a proof" (plan Section 8).  Every
expected balance below is arithmetic done here, in the docstring, from the loan's
terms.  The exhaustive every-day parallel run against the shipping seam is step
B2's job and a different question (is the posted cache faithful?); this is the
reference's own.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services import loan_ledger, loan_loaders
from app.services.loan_posting_service import confirmed_loan_balance_at
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_savings_account,
    create_settled_transfer,
    insert_tracking_start_event,
)

# The controlled loan: $250,000 originated 2025-01-01 at 6%, trued up to
# $100,000.00 as of 2026-01-05 (before every seeded period, so the true-up is the
# balance every payment amortizes down from).
_ORIGINATION_PRINCIPAL = Decimal("250000.00")
_ORIGINATION_DATE = date(2025, 1, 1)
_ANCHOR_BALANCE = Decimal("100000.00")
_ANCHOR_DATE = date(2026, 1, 5)
_RATE = Decimal("0.06")

# seed_periods: 10 biweekly periods from 2026-01-02.  Period 0 = 01-02..01-15.
_P0_START = date(2026, 1, 2)


def _make_loan(seed_user, db, **kwargs):
    """Build the controlled trued-up loan (origination + a user true-up)."""
    return create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=_ORIGINATION_PRINCIPAL,
        anchor_balance=_ANCHOR_BALANCE, anchor_date=_ANCHOR_DATE,
        rate=_RATE, origination_date=_ORIGINATION_DATE, name="Fold Loan",
        **kwargs,
    )


def _fold(loan, seed_user, on_dates):
    """Fold *loan*'s balance at *on_dates* (the fold loads its own calendar)."""
    return loan_ledger.fold_loan_balances(
        loan.id, seed_user["scenario"].id, on_dates,
    )


class TestFoldIsTotal:
    """The fold cannot answer ``None`` and cannot raise -- the arc's whole premise.

    The posting readers are PARTIAL: ``confirmed_loan_balance_at`` answers
    ``None`` with no opening posting and RAISES for a future date.  A partial
    function cannot be the single source, so every caller composes it with
    something else -- a projection, a seed, a flag, a fallback -- and every
    composition is a new producer that can disagree with the others.  Every piece
    of machinery this arc deletes exists to manage that partiality (plan Section
    1).  These tests pin the property that makes the deletion possible.
    """

    def test_a_date_before_every_event_folds_to_zero(
        self, app, db, seed_user, seed_periods,
    ):
        """Before the loan's first event the fold is 0.00 -- an empty prefix.

        The loan originates 2025-01-01; asked about 2024-12-31 the fold has no
        events to apply, and the fold of an empty stream is 0.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            folded = _fold(loan, seed_user, [date(2024, 12, 31)])
            assert folded[date(2024, 12, 31)] == Decimal("0.00")

    def test_an_unconfigured_loan_folds_to_zero_and_does_not_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """No LoanParams at all: 0.00 everywhere, never ``None``, never a raise.

        The ledger reader answers ``None`` here (its needs-setup sentinel) and the
        seam's AMORTIZING branch degrades to the cash producer.  The fold takes no
        position: it reports the honest fold of a stream with no events.  A caller
        that must tell "owed nothing" from "no loan" asks ``origination_date``.
        """
        with app.app_context():
            plain = create_savings_account(
                seed_user, db.session, name="Plain",
                anchor_balance=Decimal("2500.00"),
            )
            db.session.commit()
            folded = _fold(
                plain, seed_user, [date(2026, 1, 2), date(2026, 3, 1)],
            )
            assert folded == {
                date(2026, 1, 2): Decimal("0.00"),
                date(2026, 3, 1): Decimal("0.00"),
            }

    def test_a_future_date_does_not_raise(
        self, app, db, seed_user, seed_periods,
    ):
        """A future date holds the last RECORDED balance flat instead of raising.

        ``confirmed_loan_balance_at`` raises for ``as_of > today`` -- the
        partiality that forces its callers to fork on the clock.  The fold does
        not: it reports what it knows.  That is NOT the projection the seam shows
        (PLANNED payments arrive at C3), so this pins the no-raise contract only,
        not a forward balance.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            far = date(2099, 1, 1)
            assert _fold(loan, seed_user, [far])[far] == (
                _ANCHOR_BALANCE
            )

    def test_no_dates_folds_to_an_empty_map(
        self, app, db, seed_user, seed_periods,
    ):
        """An empty date list is an empty map, not a raise or a default date."""
        with app.app_context():
            loan = _make_loan(seed_user, db)
            assert _fold(loan, seed_user, []) == {}

    def test_duplicate_and_unsorted_dates_are_answered_independently(
        self, app, db, seed_user, seed_periods,
    ):
        """Each date is folded on its own; order and duplicates do not matter.

        The fold walks once and samples, so a caller may pass its dates in any
        order.  Duplicates collapse (a dict key), and a date's answer never
        depends on which dates accompany it.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            late, early = date(2026, 1, 20), date(2024, 12, 31)
            folded = _fold(loan, seed_user, [late, early, late])
            assert folded == {
                late: Decimal("100000.00"),
                early: Decimal("0.00"),
            }

    def test_a_loan_whose_owner_has_no_calendar_FAILS_LOUD(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The one state the fold refuses -- and it names the repair.

        Totality is over the QUESTION (any date, any account), not over a corrupt
        database.  An anchor's visible-on date is derived from the pay period
        CONTAINING it, so an owner with no pay periods has no calendar to date an
        assertion against.  The posting writer already refuses that state
        (``reconcile_loan_anchor_corrections`` raises ``PostingError``); folding a
        zero instead would be exactly the "no record" vs "no debt" confusion this
        arc exists to end.

        This is the input that breaks the bold claim in
        :func:`fold_loan_balances`'s docstring, so it is pinned here rather than
        left to the one shape (an unconfigured account) where the defect cannot
        fire -- an account with NO anchors never consults the calendar at all.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            monkeypatch.setattr(
                "app.services.loan_ledger._fold.owner_pay_periods",
                lambda account_id: [],
            )
            with pytest.raises(ValueError, match="no pay periods"):
                _fold(loan, seed_user, [date(2026, 1, 20)])


class TestTheFoldLoadsItsOwnCalendar:
    """The fold takes no period list, and that is a correctness property.

    An anchor's visible-on date is derived from the period CONTAINING it, so a
    PARTIAL calendar silently moves the answer: the containing period is missing,
    ``find_period_containing_date`` returns nothing, the ``periods[0]`` fallback
    fires, and the anchor lands on the wrong date.  Measured while reviewing this
    commit: the same loan on the same dates, folded against the owner's full
    calendar versus a window excluding the true-up's period, differed by
    $150,000.00.

    That mattered because the balance seam's period argument IS a window in
    production -- the grid passes six periods -- and step C3 points the seam's
    AMORTIZING branch at this fold.  So the parameter is gone: the fold loads the
    whole calendar itself, with the same query the posting WRITER uses, and a
    caller has no way to hand it a different one.
    """

    def test_fold_loan_balances_takes_no_period_argument(self):
        """The divergence vector is absent by signature, not by discipline.

        A structural guard: if someone re-introduces a ``periods`` parameter, the
        grid's six-period window reaches it again and the $150,000.00 divergence
        is back with every other gate silent.
        """
        import inspect
        params = inspect.signature(loan_ledger.fold_loan_balances).parameters
        assert list(params) == ["loan_account_id", "scenario_id", "dates"]

    def test_owner_pay_periods_returns_the_WHOLE_calendar_ascending(
        self, app, db, seed_user, seed_periods,
    ):
        """Every period the owner has, in ``period_index`` order -- never a window."""
        with app.app_context():
            loan = _make_loan(seed_user, db)
            loaded = loan_ledger.owner_pay_periods(loan.id)
            assert [p.id for p in loaded] == [p.id for p in seed_periods]
            assert [p.period_index for p in loaded] == sorted(
                p.period_index for p in seed_periods
            )

    def test_owner_pay_periods_is_scoped_to_the_accounts_own_owner(
        self, app, db, seed_user, seed_second_user, seed_periods,
    ):
        """Another user's periods never enter the calendar (it joins the account).

        The loader takes an ACCOUNT, not an owner id, so a loan cannot be paired
        with someone else's calendar even by a caller's mistake.
        """
        with app.app_context():
            from app.services import pay_period_service
            pay_period_service.generate_pay_periods(
                user_id=seed_second_user["user"].id,
                start_date=date(2026, 1, 2), num_periods=4, cadence_days=14,
            )
            db.session.commit()
            loan = _make_loan(seed_user, db)
            owners = {
                p.user_id for p in loan_ledger.owner_pay_periods(loan.id)
            }
            assert owners == {seed_user["user"].id}


class TestFoldValue:
    """Hand-computed balances: the fold's arithmetic, checked against paper."""

    def test_the_opening_folds_to_the_true_up_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """With no payments the fold is the LATEST anchor: $100,000.00.

        Two anchors: origination ($250,000.00 on 2025-01-01) and the true-up
        ($100,000.00 on 2026-01-05).  The walk seeds at 0.00, the origination
        resets to $250,000.00, the true-up resets to $100,000.00.  Deltas:
        +250,000.00 then -150,000.00, summing to $100,000.00 -- the assertion the
        operator actually made, NOT the origination principal.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            on = date(2026, 1, 20)
            assert _fold(loan, seed_user, [on])[on] == (
                Decimal("100000.00")
            )

    def test_one_payment_folds_off_its_REAL_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """A $1,000.00 payment on a $100,000.00 balance at 6% pays down $500.00.

        Hand-computed, and the whole point of folding CASH rather than a schedule:

            interest  = round(100,000.00 * 0.06 / 12) = $500.00
            escrow    = $0.00 (no escrow line on this loan)
            principal = 1,000.00 - 500.00 - 0.00      = $500.00
            balance   = 100,000.00 - 500.00           = $99,500.00

        The contractual P&I is irrelevant: principal is whatever the cash left
        after interest and escrow, so an extra or short payment lands here
        automatically.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
            )
            db.session.commit()
            on = seed_periods[1].end_date
            assert _fold(loan, seed_user, [on])[on] == (
                Decimal("99500.00")
            )

    def test_an_extra_payment_folds_off_more_principal(
        self, app, db, seed_user, seed_periods,
    ):
        """$1,500.00 on the same balance pays down $1,000.00, not $500.00.

            interest  = $500.00 (unchanged -- same balance, same rate)
            principal = 1,500.00 - 500.00 = $1,000.00
            balance   = 100,000.00 - 1,000.00 = $99,000.00

        The negative control for the one above: the SAME loan, a different cash
        amount, a different balance.  If the fold read the schedule instead of the
        cash, both tests would report $99,500.00.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1500.00"),
            )
            db.session.commit()
            on = seed_periods[1].end_date
            assert _fold(loan, seed_user, [on])[on] == (
                Decimal("99000.00")
            )

    def test_two_payments_compound_on_the_reduced_balance(
        self, app, db, seed_user, seed_periods,
    ):
        """The second payment accrues on what the first left -- one running balance.

            payment 1: interest = round(100,000.00 * .005) = $500.00
                       principal = 1,000.00 - 500.00       = $500.00
                       balance                              = $99,500.00
            payment 2: interest = round( 99,500.00 * .005) = $497.50
                       principal = 1,000.00 - 497.50       = $502.50
                       balance                              = $98,997.50

        If the two payments were split independently (each against the anchor)
        the second would report $497.50 of interest against $500.00 and land at
        $99,000.00.  The $2.50 is the running balance doing its job.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for period in (seed_periods[1], seed_periods[3]):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], loan,
                    period, amount=Decimal("1000.00"),
                )
            db.session.commit()
            on = seed_periods[3].end_date
            assert _fold(loan, seed_user, [on])[on] == (
                Decimal("98997.50")
            )

    def test_the_fold_is_a_step_function_between_events(
        self, app, db, seed_user, seed_periods,
    ):
        """The balance holds flat between events and steps exactly at one.

        The day BEFORE the payment's period starts it is still $100,000.00; from
        the period's first day it is $99,500.00 and stays there. One walk, sampled
        at four dates, is one call: the fold takes a date LIST for exactly this.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
            )
            db.session.commit()
            start = seed_periods[1].start_date
            folded = _fold(loan, seed_user, [
                start - timedelta(days=1), start,
                start + timedelta(days=1), seed_periods[2].start_date,
            ])
            assert folded[start - timedelta(days=1)] == Decimal("100000.00")
            assert folded[start] == Decimal("99500.00")
            assert folded[start + timedelta(days=1)] == Decimal("99500.00")
            assert folded[seed_periods[2].start_date] == Decimal("99500.00")


class TestFoldReproducesTodaysVisibilityRule:
    """The fold counts an event on the day the SHIPPING readers count it.

    These pin a rule the plan calls WRONG and step C2 deletes (ruling R-A).  They
    are here so B2's parallel run is a clean equality and C3's cutover provably
    moves no money -- and so C2 has an explicit test to FLIP rather than a silent
    behaviour change.  See ``loan_ledger/_visible.py``.
    """

    def test_a_payment_counts_from_its_PERIOD_START_not_its_due_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Payment visibility is the pay period's start, not the installment date.

        The loan's ``payment_day`` is the 1st, so a payment settled into period 1
        (2026-01-16..01-29) satisfies the 2026-02-01 installment -- a date INSIDE
        period 2.  The fold counts it from 2026-01-16 (its period's start), which
        is 16 days BEFORE the installment it pays.  That is today's rule
        faithfully: the posted split correction carries the payment's
        ``pay_period_id`` and the reader bounds on that period's start.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            shadow_period = seed_periods[1]
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                shadow_period, amount=Decimal("1000.00"),
            )
            db.session.commit()
            # The installment this payment satisfies is in FEBRUARY...
            params = loan_loaders.load_loan_params(loan.id)
            shadows = loan_loaders.settled_income_shadows(
                loan.id, seed_user["scenario"].id,
            )
            due = loan_loaders.loan_payment_due_date(
                shadows[0], params.payment_day,
            )
            assert due == date(2026, 2, 1)
            # ...yet the balance already stepped on the period's START in JANUARY.
            folded = _fold(loan, seed_user, [
                shadow_period.start_date, due,
            ])
            assert folded[shadow_period.start_date] == Decimal("99500.00")
            assert folded[due] == Decimal("99500.00")

    def test_an_anchor_counts_from_its_PERIOD_START_not_its_own_date(
        self, app, db, seed_user, seed_periods,
    ):
        """N-10, pinned: an anchor is visible from its period's start -- days EARLY.

        A tracking-start asserted 2026-01-08 sits in period 0 (2026-01-02..01-15),
        so ``LEAST(anchor_date, period.start)`` makes it visible from 2026-01-02 --
        SIX DAYS before the operator asserted anything.  The balance the fold
        reports on 2026-01-02 is a balance nobody claimed on that date.

        This is the honest reproduction of a dishonest rule, and it is the test
        step C2 must FLIP: under one clock (D5/R-A) the answer on 2026-01-02
        becomes $0.00 -- the loan's tracking had not begun -- and $80,000.00 only
        from 2026-01-08.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Import Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            insert_tracking_start_event(
                loan_loaders.load_loan_params(loan.id),
                Decimal("80000.00"), date(2026, 1, 8),
            )
            db.session.commit()
            folded = _fold(loan, seed_user, [
                _P0_START, date(2026, 1, 7), date(2026, 1, 8),
            ])
            assert folded[_P0_START] == Decimal("80000.00")
            assert folded[date(2026, 1, 7)] == Decimal("80000.00")
            assert folded[date(2026, 1, 8)] == Decimal("80000.00")

    def test_an_anchor_predating_every_period_counts_from_its_own_date(
        self, app, db, seed_user, seed_periods,
    ):
        """The ``LEAST``'s other arm: an anchor before period 0 keeps its own date.

        The origination is 2025-01-01, a year before the user's first pay period.
        ``journal_entries.pay_period_id`` is NOT NULL, so the writer files it under
        the EARLIEST period (2026-01-02) -- which would push a 2025 fact into 2026
        and report the loan owing NOTHING for all of 2025.  ``LEAST`` restores the
        anchor's own civil date, so the fold reports $250,000.00 from 2025-01-01.

        The loan here carries ONLY its origination (no true-up), so the figure is
        unambiguous.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Old Loan",
                principal=_ORIGINATION_PRINCIPAL, rate=_RATE,
                origination_date=_ORIGINATION_DATE, term=360,
            )
            db.session.commit()
            folded = _fold(loan, seed_user, [
                date(2024, 12, 31), _ORIGINATION_DATE, date(2025, 6, 1),
            ])
            assert folded[date(2024, 12, 31)] == Decimal("0.00")
            assert folded[_ORIGINATION_DATE] == _ORIGINATION_PRINCIPAL
            assert folded[date(2025, 6, 1)] == _ORIGINATION_PRINCIPAL


class TestFoldAgreesWithTheShippingReader:
    """A first parallel run: the fold reads SOURCE, the reader reads POSTINGS.

    Narrow on purpose.  It proves the two answer alike on one shape's every day;
    making it exhaustive -- every generated shape, every day, plus real data --
    is step B2, which gates all of Phase C.  ``sampling is forbidden`` there: a
    14-day sample once scored perfect while wrong by $178,103.41 on 22% of days.
    """

    def test_fold_equals_the_ledger_reader_on_every_day(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """Every day from the opening to the last period: fold == posted ledger.

        Two disjoint paths to one number -- the fold walks the loan's anchors and
        settled payment rows; the reader sums the posted journal legs the sync
        wrote -- so an agreement on every day is evidence the postings faithfully
        project the fold. That equality is what C3's cutover rests on.
        """
        from tests._test_helpers import freeze_today
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for period in (seed_periods[1], seed_periods[3], seed_periods[5]):
                create_settled_transfer(
                    seed_user, db.session, seed_user["account"], loan,
                    period, amount=Decimal("1000.00"),
                )
            db.session.commit()
            last = seed_periods[6].start_date
            # The reader refuses a future as_of, so pin today past the window.
            freeze_today(monkeypatch, last + timedelta(days=1))

            days = []
            day = _ORIGINATION_DATE
            while day <= last:
                days.append(day)
                day += timedelta(days=1)
            folded = _fold(loan, seed_user, days)

            mismatches = [
                (day, folded[day], read)
                for day in days
                if (read := confirmed_loan_balance_at(
                    loan.id, seed_user["scenario"].id, day,
                )) != folded[day]
            ]
            assert not mismatches, (
                f"fold and posted ledger disagree on "
                f"{len(mismatches)}/{len(days)} days: {mismatches[:5]}"
            )
            # Guard the loop itself: an agreement over three days would prove
            # nothing.  This spans origination to the last payment's period --
            # the whole pre-true-up year plus the payment window, day by day.
            assert days[0] == _ORIGINATION_DATE and days[-1] == last
            assert len(days) > 400


class TestFoldNegativeControls:
    """Every guard gets a control that is SHOWN to fire (plan Section 7.3)."""

    @pytest.mark.parametrize("bad_principal,expected", [
        (Decimal("0.00"), Decimal("100000.00")),
        (Decimal("500.00"), Decimal("99500.00")),
    ])
    def test_the_value_assertions_track_the_split_they_claim_to(
        self, app, db, seed_user, seed_periods, monkeypatch,
        bad_principal, expected,
    ):
        """Forcing the split's principal moves the fold by exactly that much.

        The teeth check.  ``test_one_payment_folds_off_its_REAL_principal``
        asserts $99,500.00; if the fold ignored the split's ``principal`` that
        assertion could pass for the wrong reason.  Here the split is replaced by
        one returning a chosen principal, and the fold tracks it: principal 0.00
        leaves the anchor untouched, principal 500.00 reproduces the real answer.
        So the fold provably reads the split, and the value tests are not passing
        unconditionally.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
            )
            db.session.commit()

            real_split = loan_ledger._fold.split_one_payment

            def fake(shadow, balance, periods, monthly_escrow):
                split, _after = real_split(
                    shadow, balance, periods, monthly_escrow,
                )
                forced = type(split)(
                    income_shadow=split.income_shadow,
                    interest=split.interest, escrow=split.escrow,
                    principal=bad_principal, excess=split.excess,
                )
                return forced, balance - bad_principal

            monkeypatch.setattr(
                "app.services.loan_ledger._fold.split_one_payment", fake,
            )
            on = seed_periods[1].end_date
            assert _fold(loan, seed_user, [on])[on] == expected
