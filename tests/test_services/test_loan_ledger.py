"""The loan fold: ``app.services.balance_at._fold.fold_loan_balances``.

Plan step B1 (``docs/audits/balance_architecture/README.md``).  These tests pin
the fold's CONTRACT -- totality, the delta each event contributes, and the two
visibility rules it reproduces -- against HAND-COMPUTED figures.  The fold moved
into the balance seam at step D-fold (*a fold is a balance*); the WALK it samples
and the two visibility rules it reproduces stay in the ``loan_ledger`` leaf (*a
walk is a fact*), and this file also pins those leaf primitives directly.  The
leaf's CALENDAR names left at pay-calendar plan step C2-d -- see
``TestTheFoldTakesNoCalendarAndThePackageHoldsNone`` for what replaced their
tests, and ``test_pay_calendar_value.py`` for the rule that replaced them.

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
from app.services.balance_at._fold import fold_loan_balances
from tests._test_helpers import (
    create_loan_account,
    create_loan_with_trueup,
    create_savings_account,
    create_settled_transfer,
    insert_tracking_start_event,
    posted_loan_balance_at,
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
    """Fold *loan*'s balance at *on_dates*."""
    return fold_loan_balances(
        loan.id, seed_user["scenario"].id, on_dates,
    )


def _settle(seed_user, db, loan, period, amount):
    """Settle a Checking -> loan payment, visible from its period start (C2).

    Pins the settle day to the period start so the payment is visible from that day
    under C2's settled-date clock -- the deterministic past date these
    hand-computed folds value the balance from.
    """
    return create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, period,
        amount=amount, settled_on=period.start_date,
    )


class TestFoldIsTotal:
    """The fold cannot answer ``None`` and cannot raise -- the arc's whole premise.

    The posting readers this arc inherited were PARTIAL: they answered ``None``
    with no opening posting and RAISED for a future date.  A partial function
    cannot be the single source, so every caller composes it with something else
    -- a projection, a seed, a flag, a fallback -- and every composition is a new
    producer that can disagree with the others.  Every piece of machinery this arc
    deletes exists to manage that partiality (plan Section 1); the readers
    themselves were deleted at plan step E1e, once the fold had taken every one
    of their callers.  These tests pin the property that made that possible.
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

        The posting reader raised for ``as_of > today`` -- the partiality that
        forced its callers to fork on the clock.  The fold does not: it reports
        what it knows.  That is NOT the projection the seam shows (PLANNED
        payments arrive at C3), so this pins the no-raise contract only, not a
        forward balance.
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

    def test_the_fold_needs_no_owner_calendar(
        self, app, db, seed_user, seed_periods, monkeypatch,
    ):
        """The fold consults NO owner calendar -- an anchor counts from its own date.

        Totality is over the QUESTION (any date, any account).  Pre-C2 an anchor's
        visible-on date was ``LEAST(anchor_date, containing period.start)``, so the
        fold loaded the owner's whole calendar and RAISED when it was empty.  The
        one clock counts an anchor from its OWN date, so the fold reaches no
        calendar at all.

        **Pinned against the calendar doors this arc has built, and the
        REWRITE found the old guard had no teeth.**  It used to make
        ``loan_ledger.owner_pay_periods`` raise if the fold called it, patched
        on ``_visible`` -- but the posting writer imported that name DIRECTLY,
        so the patched attribute was a binding nothing in ``app/`` ever read.
        The fixture built a loan through the writer with the poison installed
        and never tripped it.  Pay-calendar plan step C2-d deleted the loader,
        and poisoning its successor properly is what exposed that.

        **Three bindings, and the ORDER is now load-bearing.**
        ``calendar_for`` is imported by name in ``_posting_reconcile``, so
        patching the defining module alone would leave the binding both anchor
        writers actually call.  Those writers legitimately DO consult the
        calendar -- filing an anchor correction is what C2-d pointed at it --
        so the loan is built FIRST and the poison installed afterwards, around
        the fold alone.  A poison installed before setup grades the fixture.

        This is not every route to a calendar: ``recurrence._authoring``'s own
        ``calendar_for`` and ``pay_period_service.get_all_periods`` are two
        more, deliberately un-poisoned, and a fold regression reaching either
        would slip past.  The STRUCTURAL half of the claim is
        ``test_the_leaf_owns_no_database_session`` below.
        """
        def _must_not_call(user_id):
            raise AssertionError(
                "the fold must not consult the owner calendar (step C2)"
            )
        with app.app_context():
            loan = _make_loan(seed_user, db)
            for binding in (
                "app.services.pay_calendar.calendar_for",
                "app.services.pay_calendar._loader.calendar_for",
                "app.services._posting_reconcile.calendar_for",
            ):
                monkeypatch.setattr(binding, _must_not_call)
            # Trued up to $100,000 as of 2026-01-05; a later date holds it flat.
            assert _fold(loan, seed_user, [date(2026, 1, 20)])[
                date(2026, 1, 20)
            ] == _ANCHOR_BALANCE


class TestTheFoldTakesNoCalendarAndThePackageHoldsNone:
    """The fold takes no period list, and since C2-d the package holds no calendar.

    Pre-C2 an anchor's visible-on date was derived from the period CONTAINING it,
    so a PARTIAL calendar silently moved the answer (a window excluding the
    true-up's period shifted the same loan by $150,000.00), and the fold had to
    load the owner's WHOLE calendar itself rather than take one -- the balance
    seam's period argument IS a six-period window in production (the grid).  Step
    C2's one clock counts an anchor from its OWN date, so the fold consults no
    calendar at all; the no-period-argument property below stays a structural
    guard against re-introducing that coupling.

    **The three calendar names are GONE as of pay-calendar plan step C2-d.**
    ``owner_pay_periods``, ``find_period_containing_date`` and
    ``resolve_anchor_pay_period`` had one consumer left between them -- the two
    anchor-posting writers -- and those now file through
    ``pay_calendar.PayCalendar.filing_period``.  The tests that pinned the
    locator's three branches went with it, because the branches themselves did:
    that chain is one clamp now, proven equal to it over 1,800 (shape, day) pairs
    in ``test_pay_calendar_value.py`` and re-measured against production at the
    cutover.  What replaces them here is the property their deletion CREATED --
    the leaf is pure.
    """

    def test_fold_loan_balances_takes_no_period_argument(self):
        """The divergence vector is absent by signature, not by discipline.

        A structural guard: the fold must never take a ``periods`` parameter -- a
        caller's window would either be silently ignored (C2 folds off event
        dates) or, if a future change wired it in, reintroduce the $150,000.00
        partial-calendar divergence with every other gate silent.
        """
        import inspect
        params = inspect.signature(fold_loan_balances).parameters
        assert list(params) == ["loan_account_id", "scenario_id", "dates"]

    def test_the_package_exports_no_calendar_name(self):
        """The three deleted names are gone from the public surface, not shadowed.

        A deletion that left the names importable would let a future consumer
        reach the old three-branch chain and file an anchor by a second rule --
        the exact duplication plan step C2 exists to remove.  Asserted on
        ``__all__`` and on the module attributes, because a stale ``__all__``
        entry and a stale re-export fail differently.
        """
        for name in (
            "owner_pay_periods",
            "find_period_containing_date",
            "resolve_anchor_pay_period",
        ):
            assert name not in loan_ledger.__all__, name
            assert not hasattr(loan_ledger, name), name

    def test_the_leaf_owns_no_database_session(self):
        """``owner_pay_periods`` was the package's ONE query, and it is deleted.

        The structural half of the claim above: rather than proving one consumer
        does not call one loader, this proves no module in the leaf OWNS a
        session -- neither importing :mod:`app.extensions` nor reaching a
        ``.query`` off a model, the two ways this codebase spells one.

        **"Owns", not "is pure", and the weaker word is the honest one.**
        :func:`walk_loan_ledger` still takes ids and reaches
        :mod:`app.services.loan_loaders`, which holds the session; a walk is a
        function of the facts a loader hands it, and that collaborator is
        declared in the package docstring.  What this pins is that the leaf
        stopped issuing queries of its OWN, which is the property C2-d created
        and the one a future calendar loader would break.

        Read through the AST rather than by substring, so a docstring that
        MENTIONS ``db.session`` -- this file's siblings do -- cannot fail it,
        and so a spelling the substring scan missed cannot pass.
        """
        # Pylint: import-outside-toplevel -- deferred import is the file-wide
        # test convention.
        import ast  # pylint: disable=import-outside-toplevel
        import pathlib  # pylint: disable=import-outside-toplevel

        offenders = []
        for module in sorted(pathlib.Path(loan_ledger.__file__).parent.glob("*.py")):
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "app.extensions":
                    offenders.append(f"{module.name}: imports app.extensions")
                elif isinstance(node, ast.Import) and any(
                    alias.name == "app.extensions" for alias in node.names
                ):
                    offenders.append(f"{module.name}: imports app.extensions")
                elif isinstance(node, ast.Attribute) and node.attr == "query":
                    offenders.append(f"{module.name}: reaches a .query")
        assert offenders == [], (
            f"loan_ledger owns no session as of plan step C2-d; found {offenders}"
        )


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
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1000.00"))
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
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1500.00"))
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
                _settle(seed_user, db, loan, period, Decimal("1000.00"))
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
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1000.00"))
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


class TestFoldCountsAnEventOnTheDayItHappened:
    """The one clock (step C2, ruling R-A): an event counts from the day it HAPPENED.

    A payment counts from its SETTLED date, an anchor from its OWN date -- the
    same day each posting carries in ``entry_date``, so the fold and the shipping
    readers agree by construction (B2).  These flipped the two tests that pinned
    the pre-C2 boundary rules (a payment from its period start, an anchor from
    ``LEAST(anchor_date, period.start)``, N-10).  See ``loan_ledger/_visible.py``.
    """

    def test_a_payment_counts_from_its_SETTLED_date(
        self, app, db, seed_user, seed_periods,
    ):
        """Payment visibility is the SETTLED date -- not the period start, not the due date.

        A $1,000 payment budgeted to period 1 (2026-01-16..01-29) satisfies the
        2026-02-01 installment but SETTLES on 2026-01-20.  Under the one clock the
        balance steps on 2026-01-20 (the settled date), not on 2026-01-16 (the
        period start, the pre-C2 rule) and not on 2026-02-01 (the due date, which
        the split MATH still keys on).  Three axes, one visibility answer.

        The loan is trued up to $100,000 as of 2026-01-05, so before the payment
        the balance is $100,000; the $1,000 pays $500 interest + $500 principal,
        stepping to $99,500.
        """
        with app.app_context():
            loan = _make_loan(seed_user, db)
            settled = date(2026, 1, 20)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[1], amount=Decimal("1000.00"),
                settled_on=settled,
            )
            db.session.commit()
            # The installment this payment satisfies is 2026-02-01 (the split key).
            params = loan_loaders.load_loan_params(loan.id)
            shadows = loan_loaders.settled_income_shadows(
                loan.id, seed_user["scenario"].id,
            )
            due = loan_loaders.loan_payment_due_date(
                shadows[0], params.payment_day,
            )
            assert due == date(2026, 2, 1)
            folded = _fold(loan, seed_user, [
                seed_periods[1].start_date,        # period start (pre-C2 rule)
                settled - timedelta(days=1),       # day before settle
                settled,                           # the settled date
                due,                               # the installment date
            ])
            # NOT visible from the period start (pre-C2) or the day before settle...
            assert folded[seed_periods[1].start_date] == Decimal("100000.00")
            assert folded[settled - timedelta(days=1)] == Decimal("100000.00")
            # ...steps exactly on the settled date, and stays down through the due date.
            assert folded[settled] == Decimal("99500.00")
            assert folded[due] == Decimal("99500.00")

    def test_an_anchor_counts_from_its_own_date(
        self, app, db, seed_user, seed_periods,
    ):
        """N-10 closed: an anchor is visible from its OWN date, never days early.

        A tracking-start asserted 2026-01-08 sits in period 0 (2026-01-02..01-15).
        Under the pre-C2 ``LEAST(anchor_date, period.start)`` it was visible from
        2026-01-02 -- six days before the operator asserted anything (N-10).  The
        one clock counts it from 2026-01-08: on 2026-01-02..01-07 the balance is
        the ORIGINATION principal held flat (the C1 plateau, $250,000 -- the loan
        exists, its tracking just has not begun), stepping to $80,000 on 01-08.
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
                date(2024, 12, 31), _P0_START, date(2026, 1, 7), date(2026, 1, 8),
            ])
            # Before origination: 0.00.  From origination to the tracking-start:
            # the $250,000 plateau (NOT visible-early at the period start).
            assert folded[date(2024, 12, 31)] == Decimal("0.00")
            assert folded[_P0_START] == _ORIGINATION_PRINCIPAL
            assert folded[date(2026, 1, 7)] == _ORIGINATION_PRINCIPAL
            # Steps to the tracking-start value on its OWN date.
            assert folded[date(2026, 1, 8)] == Decimal("80000.00")

    def test_an_anchor_predating_every_period_counts_from_its_own_date(
        self, app, db, seed_user, seed_periods,
    ):
        """An anchor before period 0 counts from its own date -- one clock, no calendar.

        The origination is 2025-01-01, a year before the user's first pay period.
        ``journal_entries.pay_period_id`` is NOT NULL, so the writer files the
        opening under the EARLIEST period (2026-01-02) -- which, read by a
        period-bounded reader, would push a 2025 fact into 2026 and report the loan
        owing NOTHING for all of 2025.  The one clock counts the anchor from its
        OWN civil date instead, so the fold reports $250,000.00 from 2025-01-01
        with no calendar consulted at all.

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


class TestFoldAgreesWithThePostedSum:
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
                _settle(seed_user, db, loan, period, Decimal("1000.00"))
            db.session.commit()
            last = seed_periods[6].start_date
            # Pin today past the window so every settled payment is posted.
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
                if (read := posted_loan_balance_at(
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
            _settle(seed_user, db, loan, seed_periods[1], Decimal("1000.00"))
            db.session.commit()

            real_split = loan_ledger._walk.split_one_payment

            def fake(shadow, balance, periods, monthly_escrow, due_date):
                split, _after = real_split(
                    shadow, balance, periods, monthly_escrow, due_date,
                )
                forced = type(split)(
                    income_shadow=split.income_shadow,
                    interest=split.interest, escrow=split.escrow,
                    principal=bad_principal, excess=split.excess,
                    due_date=split.due_date, period=split.period,
                )
                return forced, balance - bad_principal

            monkeypatch.setattr(
                "app.services.loan_ledger._walk.split_one_payment", fake,
            )
            on = seed_periods[1].end_date
            assert _fold(loan, seed_user, [on])[on] == expected
