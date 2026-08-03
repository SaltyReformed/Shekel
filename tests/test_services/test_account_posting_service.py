"""Tests for the account posting service (Build-Order Step 5, C5 + the C6 wiring).

:mod:`app.services.account_posting_service` posts a NON-loan account's anchor
assertions into the double-entry ledger: the once-per-account OPENING (its
earliest ``AccountAnchorHistory`` row) and a TRUE-UP per later row, each a
balanced correction driving the linked ledger's total to the asserted balance
as of the day it is the closing balance for.  The walk partitions source facts
by their settled CIVIL DAY (the stored ``settled_on``; transfers by the
income shadow's, period-start fallback) against each anchor's stored
``observed_on`` -- never by pay period (the plan review's CRITICAL-1) and never
by instant, which decided the question by click order and cost production
$4,001.42 (ruling R-DH).  These tests drive the walk and the sync entry points directly;
since C6 the lifecycle chokepoints are ALSO live underneath them --
``create_account`` posts each opening at fixture time and the effect-time
self-heal reconciles at every settle / revert -- so the explicit sync calls
double as idempotency proofs, and the step-count assertions reflect the eager
per-mutation reconcile (each intermediate state lands exactly on the anchor).

Fixtures are SYNTHETIC with HAND-COMPUTED literals, each docstring showing the
arithmetic.  Events are placed RELATIVE to the origination assertion's own
``observed_on`` (:func:`_origin_day`) in whole DAYS, through
``settle_instant_on`` so each lands at noon UTC on the intended civil day; a
case needing two events on ONE day uses a PINNED opening instead.  Both
conventions exist because a fixture that offsets from a RECORDING instant grades
a different case than it names -- twice now, from two different causes
(finding N-133 / F2, and the review of the F1 revert).  All money is ``Decimal``
from strings.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountKindEnum,
    PostingKindEnum,
    PostingSourceEnum,
)
from app.exceptions import UndatedSettleError
from app.extensions import db as _db
from app.models.account import AccountAnchorHistory
from app.models.journal_entry import JournalEntry, Posting
from app.models.ledger_account import LedgerAccount
from app.models.pay_period import PayPeriod
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.user import User
from app.services import (
    account_posting_service,
    account_service,
    anchor_service,
    balance_at,
    posting_service,
)
from app.services.anchor_service import AnchorTrueUpOutcome
from app.services.auth_service import hash_password
from app.utils.dates import to_display_date
from tests._test_helpers import (
    correction_net_in_period,
    create_account_of_type,
    create_loan_account,
    create_settled_cash_transaction,
    create_settled_transfer,
    ledger_net,
    observed_day_of,
    restamp_opening_assertion,
    settle_instant_on,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_account(seed_user, balance, type_name="Savings", name="Anchor Acct"):
    """Create an account with a controlled opening anchor; commit; return it."""
    account = create_account_of_type(
        seed_user, _db.session, type_name, name,
        anchor_balance=Decimal(balance),
    )
    _db.session.commit()
    return account


# A controlled mid-day instant for the fixtures that must place two events on
# ONE civil day (ruling R-DH).  12:00 EDT, so plus-or-minus an hour is provably
# the same ``America/New_York`` day -- which a whole-DAY offset from
# :func:`_origin_day` cannot express, since the two events must share one day.
# Deriving such an offset from the ambient clock is exactly how
# ``test_a_settle_on_the_openings_own_day_is_absorbed_either_order`` came to use
# a DAY offset and stop discriminating the rule it names (finding N-133 / F2).
_PINNED_OPENING_AT = datetime(2026, 3, 17, 16, 0, tzinfo=timezone.utc)
_PINNED_OPENING_DAY = date(2026, 3, 17)
_ONE_HOUR = timedelta(hours=1)


def _pin_opening(account, at=_PINNED_OPENING_AT):
    """Re-stamp the factory opening assertion to a controlled instant; return it.

    The shared builder ``tests/_test_helpers.restamp_opening_assertion``, which
    the cash-walk suite uses for the same reason: an event stream whose anchor
    is the wall clock is not a deterministic fixture.
    """
    restamp_opening_assertion(_db.session, account, at)
    return at


def _origin_day(account):
    """Return the civil day the factory origination assertion is ABOUT.

    The day the partition actually reads (``AccountAnchorHistory.observed_on``),
    which is what a fixture placing a settle "the day before" or "the day
    after" the opening must offset from.

    **It is NOT the origination's ``created_at``, and that distinction is this
    helper's whole reason to exist** (finding N-133, the review of the F1
    revert).  The two were the same day by construction while ``observed_on``
    was DERIVED from ``created_at``; plan step 2 made it a stored column and
    ``create_account_of_type`` began opening its accounts the day BEFORE today,
    so a fixture offsetting from ``created_at`` by one day landed on the
    opening's OWN day and silently graded the opposite case.  Measured on two
    tests here: ``test_a_settle_on_an_earlier_day_is_inside_the_opening`` and
    ``test_transfer_attribution_uses_income_shadow_day`` both stopped
    testing the strictly-earlier arm their docstrings name.  Offsetting from
    the day the rule reads makes the fixture say what it means.
    """
    row = (
        _db.session.query(AccountAnchorHistory)
        .filter_by(account_id=account.id)
        .order_by(AccountAnchorHistory.observed_on, AccountAnchorHistory.id)
        .first()
    )
    return row.observed_on


def _add_assertion(account, balance, created_at, pay_period_id=None):
    """Append a true-up ``AccountAnchorHistory`` row at a controlled instant.

    Mirrors ``anchor_service.stage_anchor_true_up`` (history row + the
    ``current_anchor_*`` cache write) but pins ``created_at`` explicitly, with
    ``observed_on`` derived from it, so the civil-day partition under test is
    exact.  Anchors against the account's current anchor period (the fixture
    bootstrap) unless *pay_period_id* names another -- which the R2 attribution
    cases need, a row's stored period being the slot its correction is FILED
    under and not something the observed day derives.  Flushes.
    """
    row = AccountAnchorHistory(
        account_id=account.id,
        pay_period_id=pay_period_id or account.current_anchor_period_id,
        anchor_balance=Decimal(str(balance)),
        created_at=created_at,
        # The civil day this assertion is the closing balance FOR, kept in step
        # with the pinned instant by the shared rule (ruling R-DH, plan step 2).
        observed_on=observed_day_of(created_at),
    )
    account.current_anchor_balance = Decimal(str(balance))
    _db.session.add(row)
    _db.session.flush()
    return row


def _ledger_of_kind(account_id, kind):
    """Return the account's ledger row of *kind* (linked / twin), or None."""
    return (
        _db.session.query(LedgerAccount)
        .filter_by(
            account_id=account_id,
            kind_id=ref_cache.ledger_account_kind_id(kind),
        )
        .one_or_none()
    )


def _correction_entries(account_id, scenario_id, source_enum):
    """Return the account's correction entries of *source_enum*, linked-scoped.

    Finds the ``account_opening`` / ``account_trueup`` journal entries in
    *scenario_id* that touch the account's LINKED ledger -- the way the
    reconcile scopes them to one account.
    """
    linked = _ledger_of_kind(account_id, LedgerAccountKindEnum.LINKED)
    return (
        _db.session.query(JournalEntry)
        .filter(
            JournalEntry.scenario_id == scenario_id,
            JournalEntry.source_kind_id == ref_cache.posting_source_id(
                source_enum,
            ),
            JournalEntry.id.in_(
                _db.session.query(Posting.journal_entry_id)
                .filter(Posting.ledger_account_id == linked.id)
            ),
        )
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


def _correction_net_in_period(account_id, scenario_id, source_enum, period_id):
    """Sum an account's correction legs on its LINKED ledger in ONE pay period.

    Resolves the account's linked ledger, then defers to the shared
    ``tests._test_helpers.correction_net_in_period`` -- the same reading the
    loan anchor suite makes, so the two halves of the R2 fix are graded by one
    query rather than two that happen to agree.
    """
    linked = _ledger_of_kind(account_id, LedgerAccountKindEnum.LINKED)
    return correction_net_in_period(
        _db.session, linked.id, scenario_id, source_enum, period_id,
    )


def _settle_expense(seed_user, account, amount, settled_on):
    """Settle an expense on *account* on a pinned civil DAY; return it.

    ``settled_on`` is the day the cash moved, pinned BEFORE the ledger
    emission so the posted entry and the walk's attribution agree, as in
    production (the C6 effect-time self-heal reads the emitted
    ``entry_date``s).  The transaction is placed in the seed bootstrap
    period; the walk attributes by the SETTLE DAY (ruling R-DH), so the
    period placement is immaterial.  It took an instant, or ``None`` for the
    period-start fallback, until plan step X-f1 removed both.
    """
    return create_settled_cash_transaction(
        seed_user, _db.session, seed_user["bootstrap_period"],
        Decimal(str(amount)), account=account, settled_on=settled_on,
    )


# ---------------------------------------------------------------------------
# walk_account_ledger -- the civil-day partition (the core)
# ---------------------------------------------------------------------------


class TestWalkAccountLedger:
    """The pure walk partitions sources by CIVIL DAY and resets at assertions.

    The day is the user's (``America/New_York``) and an assertion is the
    closing balance for it -- EVERY assertion, opening and true-up alike --
    ruling R-DH, ``docs/audits/balance_architecture/anchor_settle_partition.md``.
    It was an INSTANT partition until 2026-07-31, which decided the question by
    which button the user pressed first and cost production $4,001.42; the
    OPENING then carried an exception for one day, until finding N-133 / F1
    scored it against four months of that same account and found it the
    second-worst correction in the history.
    """

    def test_opening_only_walk(self, app, db, seed_user):
        """A fresh account walks to one opening correction from zero.

        Savings anchored $500.00 with no settled activity: one correction,
        the opening, with ledger_before 0.00 (so its delta is the full
        anchor, 500.00).
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 1
            assert corrections[0].anchor.is_opening is True
            assert corrections[0].anchor.anchor_balance == Decimal("500.00")
            assert corrections[0].ledger_before == Decimal("0.00")

    def test_a_settle_on_an_earlier_day_is_inside_the_opening(
        self, app, db, seed_user,
    ):
        """A settle dated an EARLIER DAY than the opening is in ledger_before.

        Savings anchored $500.00; a $200.00 expense settled the day BEFORE the
        origination assertion: the source is absorbed, so the opening's
        ledger_before is -200.00 (and its delta 500 - (-200) = +700.00 -- the
        anchor already reflected that spend).

        **The boundary is a DAY, inclusive, for every assertion kind** (ruling
        R-DH (a)).  This case is the strictly-earlier one, which no variant of
        the rule has ever disputed; the opening's OWN day is the case
        :meth:`test_a_settle_on_the_openings_own_day_is_absorbed_either_order`
        pins.  The fixture moved from "one hour before" to "one day before"
        because an hour is not a unit this partition has any more.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            _settle_expense(
                seed_user, account, "200.00", origin - timedelta(days=1),
            )
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 1
            assert corrections[0].ledger_before == Decimal("-200.00")

    def test_a_settle_on_the_openings_own_day_is_absorbed(
        self, app, db, seed_user,
    ):
        """A settle dated the opening's OWN day is ABSORBED.

        Savings anchored $500.00 with its opening pinned to 12:00 EDT; a $200.00
        expense settled an hour after it, and again an hour before it -- both the
        SAME civil day.  The opening's ``ledger_before`` is -200.00 both times:
        the settle is inside the asserted balance, so the opening's own delta is
        ``500 - (-200) = +700.00`` and the walk lands on $500.00, the balance
        the user typed.

        **This is ruling R-DH (a) with no exception for the opening** (finding
        N-133 / F1, ruled 2026-07-31).  An assertion is the CLOSING balance for
        its civil day, opening and true-up alike.  The opening carried an
        exception for one day -- it rode on top of its own day's sources -- and
        the exception was reverted once scored against production: on the
        developer's real Checking, four settles share the opening's civil day
        and stacking them on top made the walk read $4,804.00 for a day the bank
        showed $2,746.58, then book a -$1,986.16 correction at the next
        assertion where absorbing them books +$71.26.

        **It was parametrised over "an hour after" and "an hour before" until
        plan step X-f1, and the pair no longer describes two inputs.**  Its own
        docstring already recorded that the two fed byte-identical values --
        ``_source_net_days`` aggregates a day's sources to one ``(day, net)``
        pair before the partition runs -- and once the settle stores a DAY
        rather than an instant the two arms are literally the same fixture.
        Collapsed rather than left implying a discrimination the code cannot
        make; the regression insurance it was kept for is now carried by the
        column's type, since re-introducing an instant partition would need a
        column that can hold one.

        **This test could not fail before 2026-07-31.**  It passed
        ``origin + timedelta(days=1)`` while claiming "an hour after -- the same
        civil day", so it graded a settle on a LATER day, which rides on top
        under either rule.  The day offset was not careless -- the origination's
        instant is the ambient wall clock, so an hour offset really can cross
        midnight -- which is why the fix is a PINNED opening rather than a
        smaller offset.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            pinned = _pin_opening(account)
            settle = _settle_expense(
                seed_user, account, "200.00", _PINNED_OPENING_DAY,
            )
            _db.session.commit()

            # The precondition the whole case rests on: ONE civil day for both
            # events.  Without it this grades a different partition entirely,
            # which is the defect being repaired here.
            assert to_display_date(pinned) == _PINNED_OPENING_DAY
            assert settle.settled_on == _PINNED_OPENING_DAY

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 1
            assert corrections[0].ledger_before == Decimal("-200.00")

    def test_a_settle_dated_before_the_origination_is_absorbed(
        self, app, db, seed_user,
    ):
        """A settle dated before the origination assertion is inside it.

        The $200.00 expense carries the 2024 bootstrap period's start day, which
        precedes the origination assertion (test-run time, 2026+), so it is
        absorbed: ledger_before -200.00.

        **It reached that day through a FALLBACK until plan step X-f1** -- the
        row carried no ``paid_at`` and every reader substituted its pay period's
        ``start_date`` -- and this test was named for the fallback.  The day is
        a stored fact now and the substitution is gone, so the fixture states
        the day it always meant.  The figure is unchanged, because the day is.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            _settle_expense(
                seed_user, account, "200.00",
                seed_user["bootstrap_period"].start_date,
            )
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert corrections[0].ledger_before == Decimal("-200.00")

    def test_a_settled_row_with_no_day_is_REFUSED_by_this_walk_too(
        self, app, db, seed_user,
    ):
        """The POSTED walk refuses an undated settled row, as the source walk does.

        Pinned separately from ``test_cash_walk.py``'s twin because it is a
        different reader on a different table: this walk reaches
        ``balance_predicates.settled_day`` from ``_transaction_source_days``,
        which resolves the day of a row it found through the POSTINGS.  The two
        must not drift about what an undated settled row means, and the whole
        point of the accessor is that they cannot -- so the pin is here to prove
        the second call site really consults it.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            txn = _settle_expense(
                seed_user, account, "200.00",
                seed_user["bootstrap_period"].start_date,
            )
            _db.session.commit()
            # Break the row AFTER its postings exist, which is the only way to
            # reach this walk with one: a bulk update bypasses the ORM, exactly
            # as the real hazard does.
            _db.session.query(Transaction).filter(
                Transaction.id == txn.id,
            ).update({"settled_on": None}, synchronize_session=False)
            _db.session.commit()

            with pytest.raises(UndatedSettleError) as exc:
                account_posting_service.walk_account_ledger(
                    account.id, seed_user["scenario"].id,
                )
            assert str(txn.id) in str(exc.value)

    def test_transfer_attribution_uses_income_shadow_day(
        self, app, db, seed_user,
    ):
        """A transfer is attributed by its INCOME shadow's settled civil day.

        Two Checking -> Savings transfers into a $500.00-anchored Savings:
        $50.00 settled the day BEFORE the origination assertion (absorbed) and
        $150.00 the day AFTER (rides on top).  The opening's ledger_before is
        therefore exactly +50.00.

        The offsets are DAYS since ruling R-DH (2026-07-31): the partition
        reads a civil day, so an hour before the assertion is the SAME day and
        would no longer discriminate the two sides this test is about.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], account,
                seed_user["bootstrap_period"], amount=Decimal("50.00"),
                settled_on=origin - timedelta(days=1),
            )
            create_settled_transfer(
                seed_user, _db.session, seed_user["account"], account,
                seed_user["bootstrap_period"], amount=Decimal("150.00"),
                settled_on=origin + timedelta(days=1), name="Post-anchor",
            )
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 1
            assert corrections[0].ledger_before == Decimal("50.00")

    def test_trueup_day_partition(self, app, db, seed_user):
        """The CRITICAL-1 case: a true-up absorbs only settles up to its own day.

        Savings anchored $500.00 (origination, day T).  A $200.00 expense on
        T+1, a true-up asserting $350.00 on T+2, a $100.00 expense on T+3:

          opening: ledger_before 0.00              (delta +500.00)
          true-up: ledger_before 500 - 200 = 300.00 (delta +50.00 -- the
                   engine's answer was 300, the user asserted 350)

        The T+3 settle is dated after the true-up's day and perturbs nothing.
        A period-granular walk would have absorbed BOTH settles (all four
        events share one 14-day pay period) and mis-stated the true-up, which
        is what this test exists to catch.

        **The fixture spans DAYS where it used to span hours** (ruling R-DH,
        2026-07-31).  The partition is day-granular now, so hour offsets put
        every event on one day and the case would no longer discriminate
        day-granular from period-granular at all -- it would pass under both.
        The expected figures are unchanged; only the units the fixture
        separates its events by had to move to the units the rule reads.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            _add_assertion(
                account, "350.00", settle_instant_on(origin + timedelta(days=2)),
            )
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(days=3),
            )
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert len(corrections) == 2
            assert corrections[0].anchor.is_opening is True
            assert corrections[0].ledger_before == Decimal("0.00")
            assert corrections[1].anchor.is_opening is False
            assert corrections[1].ledger_before == Decimal("300.00")

    def test_two_assertions_on_one_day_both_absorb_it_in_recording_order(
        self, app, db, seed_user,
    ):
        """One civil day, both assertion kinds, ONE rule -- and the tie-break.

        Savings anchored $500.00 with its opening pinned to 12:00 EDT; a $75.00
        expense two hours later; a TRUE-UP asserting $425.00 an hour after that.
        All three share one civil day.  Both assertions close that day, so the
        day's sources walk FIRST and each assertion resets over what it finds:

          opening: ledger_before -75.00   (the $75 is inside the $500 typed)
          true-up: ledger_before 500.00   (the opening's reset, nothing after)

        **This is the discriminating control for ruling R-DH (a), and each
        figure is broken by a different mutant.**  Give the OPENING back its
        one-day-old exception and the first reads 0.00; drop the reset that
        makes each assertion supersede the walked total and the second reads
        -75.00.  (Making the TRUE-UP alone ride on top does NOT move the second
        figure -- the opening has already consumed the day's source through the
        monotonic pointer -- so that arm is covered by the sibling above, not
        here.)  It also pins
        the same-day tie-break the rule depends on: two assertions about one day
        apply in RECORDING order and the last is that day's closing balance,
        which is why the true-up sees the opening's $500.00 rather than the
        other way round.

        It was named ``test_same_instant_settle_is_absorbed`` -- the vocabulary
        of the INSTANT partition ruling R-DH deleted on 2026-07-31 -- and then
        ``test_a_trueup_absorbs_a_settle_the_opening_rode_on_top_of``, for the
        amendment reverted at finding N-133 / F1.  Both names described a rule
        the code no longer has.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            pinned = _pin_opening(account)
            settle = _settle_expense(
                seed_user, account, "75.00", _PINNED_OPENING_DAY,
            )
            _add_assertion(account, "425.00", pinned + 3 * _ONE_HOUR)
            _db.session.commit()

            # ONE civil day for all three events -- the precondition that makes
            # the two figures below a statement about the RULE rather than
            # about their dates.
            assert to_display_date(pinned) == _PINNED_OPENING_DAY
            assert settle.settled_on == _PINNED_OPENING_DAY
            assert to_display_date(
                pinned + 3 * _ONE_HOUR,
            ) == _PINNED_OPENING_DAY

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert corrections[0].anchor.is_opening is True
            assert corrections[0].ledger_before == Decimal("-75.00")
            assert corrections[1].anchor.is_opening is False
            assert corrections[1].ledger_before == Decimal("500.00")

    def test_reverted_source_drops_out(self, app, db, seed_user):
        """A reverted settle nets to zero in the ledger and leaves the walk.

        The $200.00 expense (paid T+1h) is reversed (the revert path's
        ``settled=False`` reconcile); a true-up at T+2h asserting $480.00
        (distinct from the $500.00 origination -- the F-103 same-day
        same-balance unique index would reject a literal duplicate) then
        sees ledger_before 500.00 -- the reverted source contributes
        nothing, whatever its settle day said.
        """
        with app.app_context():
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            posting_service.sync_transaction_postings(txn, settled=False)
            _add_assertion(account, "480.00", settle_instant_on(origin + timedelta(days=2)))
            _db.session.commit()

            corrections = account_posting_service.walk_account_ledger(
                account.id, seed_user["scenario"].id,
            )
            assert corrections[1].ledger_before == Decimal("500.00")

    def test_walk_refuses_amortizing_loan(self, app, db, seed_user):
        """Walking a loan is a caller bug and fails loudly.

        Loans book their anchor corrections through the loan posting
        package; the walk raising here (not just the equity resolver at
        mint time) is what keeps the two correction families structurally
        disjoint.
        """
        with app.app_context():
            loan = create_loan_account(seed_user, _db.session)
            _db.session.commit()
            with pytest.raises(ValueError, match="amortizing loan"):
                account_posting_service.walk_account_ledger(
                    loan.id, seed_user["scenario"].id,
                )

    def test_missing_account_returns_empty(self, app, db, seed_user):
        """A nonexistent account id walks to no corrections."""
        with app.app_context():
            assert account_posting_service.walk_account_ledger(
                10**9, seed_user["scenario"].id,
            ) == []


# ---------------------------------------------------------------------------
# sync_account_anchor_postings -- reconcile-to-target on the ledger
# ---------------------------------------------------------------------------


class TestSyncAccountAnchorPostings:
    """The reconcile posts balanced corrections and self-heals to target."""

    def test_opening_posts_balanced_correction(self, app, db, seed_user):
        """The opening books linked +anchor / equity -anchor, dated + attributed.

        Savings anchored $500.00: ONE ``account_opening`` entry with legs
        linked +500.00 / anchor-equity -500.00 (both kind ``opening``), both
        concrete source FKs NULL, ``entry_date`` the assertion's stored
        ``observed_on``, and ``pay_period_id`` the history row's own period.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("500.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            equity = _ledger_of_kind(
                account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert equity is not None
            assert ledger_net(
                _db.session, equity.id, scenario_id,
            ) == Decimal("-500.00")

            entries = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            )
            assert len(entries) == 1
            entry = entries[0]
            opening_kind = ref_cache.posting_kind_id(PostingKindEnum.OPENING)
            assert _entry_legs(entry.id) == {
                linked.id: (Decimal("500.00"), opening_kind),
                equity.id: (Decimal("-500.00"), opening_kind),
            }
            assert entry.transfer_id is None
            assert entry.transaction_id is None
            history_row = (
                _db.session.query(AccountAnchorHistory)
                .filter_by(account_id=account.id)
                .one()
            )
            assert entry.pay_period_id == history_row.pay_period_id
            # The correction is dated the day the assertion is the CLOSING
            # BALANCE for -- the stored ``observed_on``, read rather than
            # re-derived from ``created_at`` (ruling R-DH, plan step 2).  The
            # two are independent since the column shipped: this account's
            # opening is observed YESTERDAY while its row was recorded today.
            assert entry.entry_date == history_row.observed_on
            assert history_row.observed_on != to_display_date(
                history_row.created_at,
            )
            assert _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ) == []

    def test_zero_anchor_books_nothing(self, app, db, seed_user):
        """A $0.00 opening books no entry and mints no anchor-equity row.

        The zero-delta rule keeps a fresh $0 account hard-deletable: no
        entry, no equity twin (Guard 5 never engages).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "0.00")
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            ) == []
            assert _ledger_of_kind(
                account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            ) is None

    def test_sync_is_idempotent(self, app, db, seed_user):
        """A second sync at the same state writes no new entry."""
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()
            assert len(_correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            )) == 1
            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("500.00")

    def test_trueup_posts_delta_and_absolute_invariant(
        self, app, db, seed_user,
    ):
        """The CRITICAL-1 fixture reconciles to the absolute invariant.

        From the walk fixture (anchor 500, spend 200 on T+1, true-up 350 on
        T+2, spend 100 on T+3 -- DAY offsets since ruling R-DH, see
        ``test_trueup_day_partition`` for why hours no longer separate them):

          opening +500.00, true-up +50.00, sources -200.00 - 100.00
          => linked total 250.00 == latest anchor 350 + post-assertion -100
          equity nets -(500 + 50) = -550.00
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            _add_assertion(account, "350.00", settle_instant_on(origin + timedelta(days=2)))
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(days=3),
            )
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("250.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            equity = _ledger_of_kind(
                account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert ledger_net(
                _db.session, equity.id, scenario_id,
            ) == Decimal("-550.00")
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 1
            trueup_kind = ref_cache.posting_kind_id(PostingKindEnum.TRUEUP)
            assert _entry_legs(trueups[0].id) == {
                linked.id: (Decimal("50.00"), trueup_kind),
                equity.id: (Decimal("-50.00"), trueup_kind),
            }

    def test_trueup_self_heals_when_pre_assertion_source_changes(
        self, app, db, seed_user,
    ):
        """Reverting a pre-true-up settle re-bases the true-up on resync.

        Continuing the CRITICAL-1 fixture: the $200.00 pre-true-up expense
        is reverted, so the walk's true-up ledger_before moves 300 -> 500
        and its delta +50 -> -150.  The resync appends the balancing -200.00
        delta on the SAME (source, date) key -- no stale snapshot survives:

          linked: 500 (opening) - 150 (true-up, healed) - 100 (post settle)
                  = 250.00 == anchor 350 + post-assertion -100
          the true-up key now nets -150.00 across its two entries.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            _add_assertion(account, "350.00", settle_instant_on(origin + timedelta(days=2)))
            _settle_expense(
                seed_user, account, "100.00", origin + timedelta(days=3),
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            posting_service.sync_transaction_postings(txn, settled=False)
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("250.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 2
            trueup_linked_net = sum(
                (
                    _entry_legs(entry.id)[linked.id][0]
                    for entry in trueups
                ),
                Decimal("0"),
            )
            assert trueup_linked_net == Decimal("-150.00")

    def test_trueup_retired_by_matching_balance_reverses_to_zero(
        self, app, db, seed_user,
    ):
        """A true-up whose walked delta becomes zero reverses via its empty-target key.

        Anchor 500; a $200.00 expense dated T+1d; true-up 350.00 on T+2d
        posts +50.00 (ledger_before 300).  The user then reverts the $200
        and instead settles $150.00, also dated T+1d (still strictly before
        the true-up's day, which is what "pre-true-up" means now that the
        partition reads civil days rather than instants).  The
        C6 effect-time self-heal reconciles at EACH mutation, landing the
        ledger exactly on the anchor at every step:

          revert:     ledger_before 500, target -150, posted +50
                      -> delta -200.00 (linked total 500 - 150 = 350)
          settle 150: ledger_before 500 - 150 = 350 -- delta 0.  The
                      zero-delta correction still creates its (trueup,
                      day) KEY with an empty leg map, so the stale
                      -150.00 net REVERSES via the empty-target path
                      -> delta +150.00 (linked total 350 again)

        The final explicit resync books nothing (idempotent).  All three
        key entries are attributed to the history row's own period, and:

          linked: 500 (opening) + 0 (true-up net) - 150 = 350.00 == anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            txn = _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            trueup_row = _add_assertion(
                account, "350.00", settle_instant_on(origin + timedelta(days=2)),
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            posting_service.sync_transaction_postings(txn, settled=False)
            _settle_expense(
                seed_user, account, "150.00",
                origin + timedelta(days=1),
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("350.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 3
            assert all(
                entry.pay_period_id == trueup_row.pay_period_id
                for entry in trueups
            )
            assert sum(
                (_entry_legs(entry.id)[linked.id][0] for entry in trueups),
                Decimal("0"),
            ) == Decimal("0.00")

    def test_posted_only_key_reverses_into_the_period_it_corrected(
        self, app, db, seed_user,
    ):
        """A posted correction with no surviving anchor row reverses, R2-attributed.

        Posts the CRITICAL-1 true-up (+50.00), then surgically deletes its
        history row ALONE -- unreachable through production lifecycles
        (a period wipe CASCADEs the entry away with the row, and history is
        otherwise append-only), which is exactly why the reconcile's
        posted-only branch is defensive.  The resync sees the (trueup, day)
        key only on the posted side and reverses it, taking the period OF
        THE POSTINGS IT REVERSES (read back from the latest posted entry --
        the R2 rule), leaving:

          linked: 500 (opening) + 0 (true-up net) - 200 = 300.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            _settle_expense(
                seed_user, account, "200.00", origin + timedelta(days=1),
            )
            trueup_row = _add_assertion(
                account, "350.00", settle_instant_on(origin + timedelta(days=2)),
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()
            original_period_id = trueup_row.pay_period_id

            _db.session.delete(trueup_row)
            _db.session.flush()
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("300.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 2
            assert all(
                entry.pay_period_id == original_period_id
                for entry in trueups
            )
            assert sum(
                (_entry_legs(entry.id)[linked.id][0] for entry in trueups),
                Decimal("0"),
            ) == Decimal("0.00")

    def test_same_day_trueups_merge_to_one_entry_landing_later(
        self, app, db, seed_user,
    ):
        """Two same-UTC-day true-ups merge to one target on the LATER value.

        Both assertions sit on ONE future civil day, an hour apart: $600.00
        then $550.00 on a $500.00-anchored account.  Their deltas +100.00 and
        -50.00 share the (trueup, day) key and merge to ONE +50.00 entry; the
        ledger lands on 550.00.

        Noon UTC plus an hour is provably one ``America/New_York`` day (08:00
        and 09:00 EDT), which is the property the merge turns on -- the pair
        must not straddle midnight in the zone the partition reads.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            day = settle_instant_on(_origin_day(account) + timedelta(days=30))
            _add_assertion(account, "600.00", day)
            _add_assertion(account, "550.00", day + _ONE_HOUR)
            # The precondition the merge rests on: ONE civil day for both.
            assert to_display_date(day) == to_display_date(day + _ONE_HOUR)
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("550.00")
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 1
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            assert _entry_legs(trueups[0].id)[linked.id][0] == Decimal("50.00")

    def test_liability_anchor_keeps_ledger_native_sign(
        self, app, db, seed_user,
    ):
        """An owed-as-negative liability anchor posts with no sign branch.

        A Credit Card anchored -500.00 (the owed-as-negative convention):
        the opening books linked -500.00 / equity +500.00 -- the ledger
        presents the anchor faithfully, exactly like the engine (no class
        branch, no ``-abs`` normalization).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(
                seed_user, "-500.00", type_name="Credit Card", name="CC",
            )
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("-500.00")
            equity = _ledger_of_kind(
                account.id, LedgerAccountKindEnum.ANCHOR_EQUITY,
            )
            assert ledger_net(
                _db.session, equity.id, scenario_id,
            ) == Decimal("500.00")


class TestCorrectionPeriodAttribution:
    """An anchor correction books in the period containing its OWN day.

    The production defect these pin (finding N-161, plan step X-ai-r), and it
    had two halves.  The reconcile key was ``(source kind, entry date)`` with
    no period, so two assertions observed on ONE civil day could not be told
    apart and a reversal of one period's postings was filed against another;
    and the period the entry was FILED under was copied from the history row's
    stored ``pay_period_id``, which is a CACHE of the same day-to-period
    derivation and can be split from its own day by a clock.  Measured on a
    production clone: corrections of ``+$386.85`` and ``-$186.85`` had posted
    as ``+$3,054.36`` in one period and ``-$2,854.36`` in the next.

    The fixture is production's shape exactly -- the second assertion is
    recorded late on the LAST DAY of a period while its stored
    ``pay_period_id`` names the NEXT one (production's row was created 21:28
    Eastern on the boundary day).  The rule under test is that the LEDGER
    ignores that stored period and files both corrections in the period
    containing the day they assert, which is what ruling R-DH states
    (``account_service.resolve_anchor_period_id``: "the period is DERIVED from
    the day, not chosen beside it") and what the grid's "Book vs bank" row
    already does (``balance_at._cash_periods._assertion_sums`` buckets on
    ``observed_on``).  Reading the stored column instead put the two surfaces
    at odds by the whole correction on two of the clone's periods; deriving it
    agrees with the grid on all 61.
    """

    _ASSERTION_DAY = date(2026, 4, 3)

    def _period_containing_the_day(self, seed_user, index=1):
        """Add (and return) a pay period whose range contains the assertion day."""
        period = PayPeriod(
            user_id=seed_user["user"].id,
            start_date=date(2026, 3, 21), end_date=self._ASSERTION_DAY,
            period_index=index,
        )
        _db.session.add(period)
        _db.session.flush()
        return period

    def _period_after_the_day(self, seed_user, index=2):
        """Add (and return) the period that STARTS the day after the assertion."""
        period = PayPeriod(
            user_id=seed_user["user"].id,
            start_date=date(2026, 4, 4), end_date=date(2026, 4, 17),
            period_index=index,
        )
        _db.session.add(period)
        _db.session.flush()
        return period

    def test_the_stored_period_is_ignored_and_the_day_decides(
        self, app, db, seed_user,
    ):
        """A row mis-filed into the NEXT period still books in the day's period.

        Production's shape exactly: two assertions an hour apart on one civil
        day that is a period's LAST day, the second recorded late enough that
        its stored ``pay_period_id`` names the following period.  Both
        corrections must book in the period CONTAINING the day -- ``+100.00``
        and ``-50.00`` merging to one ``+50.00`` entry there -- and the period
        the second row names must hold nothing.

        The ledger otherwise disagrees with the grid's "Book vs bank" row,
        which buckets the same corrections on ``observed_on``, by the whole
        correction.  Measured on a production clone: deriving agrees with the
        grid on all 61 periods, reading the stored column on 59 of 61.

        Linked total ``500 opening + 100 - 50 = 550.00``, the later value.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            _pin_opening(account)
            containing = self._period_containing_the_day(seed_user)
            following = self._period_after_the_day(seed_user)
            at = settle_instant_on(self._ASSERTION_DAY)
            _add_assertion(account, "600.00", at, pay_period_id=containing.id)
            _add_assertion(
                account, "550.00", at + _ONE_HOUR, pay_period_id=following.id,
            )
            # The preconditions: ONE civil day, and the second row filed
            # against a period its own observed day falls outside.
            assert to_display_date(at) == to_display_date(at + _ONE_HOUR)
            assert following.start_date > self._ASSERTION_DAY
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, containing.id,
            ) == Decimal("50.00")
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, following.id,
            ) == Decimal("0.00")
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert all(
                entry.entry_date == self._ASSERTION_DAY for entry in trueups
            )
            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("550.00")

    def _assert_beyond_the_schedule(self, seed_user):
        """Post one true-up whose day no pay period contains; return the account.

        ``seed_user`` carries only its 2024 bootstrap period, so an assertion
        dated 2026 has no containing period and
        :func:`app.services.loan_ledger.resolve_anchor_pay_period` falls back
        to it -- the state a user reaches by asserting a balance past the end
        of their generated schedule.  ``600 - 500 = +100.00``.
        """
        account = _make_account(seed_user, "500.00")
        _pin_opening(account)
        _add_assertion(
            account, "600.00", settle_instant_on(self._ASSERTION_DAY),
        )
        _db.session.commit()
        account_posting_service.sync_account_anchor_postings(
            account.id, seed_user["scenario"].id,
        )
        _db.session.commit()
        return account

    def test_a_period_appearing_around_the_day_refiles_the_correction(
        self, app, db, seed_user,
    ):
        """Generating the period that contains the day moves the correction into it.

        The R2 case on the cash side, and it is reachable by an ordinary
        schedule extension: a correction filed against the fallback period is
        REVERSED there -- netting it to ``0.00`` -- and re-posted in the period
        that now contains its day.  A period-blind key compared the two as
        equal and left the correction in the fallback permanently.

        The total does not move: this is an attribution change, not a
        revaluation.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            fallback = seed_user["bootstrap_period"]
            account = self._assert_beyond_the_schedule(seed_user)
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, fallback.id,
            ) == Decimal("100.00")

            containing = self._period_containing_the_day(seed_user)
            _db.session.commit()
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, fallback.id,
            ) == Decimal("0.00")
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, containing.id,
            ) == Decimal("100.00")
            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("600.00")

    def test_the_refiled_correction_reconcile_is_idempotent(
        self, app, db, seed_user,
    ):
        """A re-sync after the re-file writes nothing and moves no period.

        The convergence half: the finer key must reach its fixpoint in one
        pass rather than shuttling the correction between the two periods.
        Asserts the re-filed STATE either side of the extra sync, so it cannot
        pass by nothing having moved in the first place.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            fallback = seed_user["bootstrap_period"]
            account = self._assert_beyond_the_schedule(seed_user)
            containing = self._period_containing_the_day(seed_user)
            _db.session.commit()
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()
            settled = len(_correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            ))
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, containing.id,
            ) == Decimal("100.00")

            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            assert len(_correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )) == settled
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, containing.id,
            ) == Decimal("100.00")
            assert _correction_net_in_period(
                account.id, scenario_id,
                PostingSourceEnum.ACCOUNT_TRUEUP, fallback.id,
            ) == Decimal("0.00")


# ---------------------------------------------------------------------------
# _sync entry points -- scenarios, loans, per-user resync
# ---------------------------------------------------------------------------


class TestSyncEntryPoints:
    """Scenario enumeration, the loan no-op, and the per-user resync."""

    def test_all_scenarios_covers_posted_scenarios_and_baseline(
        self, app, db, seed_user,
    ):
        """The all-scenarios sync posts in the baseline AND posted scenarios.

        A $40.00 expense settled (post-assertion) in a NON-baseline scenario
        puts a posting on the account's linked ledger there, so the
        all-scenarios sync reconciles both: the opening posts per scenario
        (anchors are per-account), and each scenario's total reflects its
        OWN sources -- baseline 500.00, what-if 500 - 40 = 460.00.
        """
        with app.app_context():
            baseline_id = seed_user["scenario"].id
            what_if = Scenario(
                user_id=seed_user["user"].id, name="What-if",
                is_baseline=False,
            )
            _db.session.add(what_if)
            _db.session.flush()
            account = _make_account(seed_user, "500.00")
            origin = _origin_day(account)
            txn = create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("40.00"), account=account, scenario=what_if,
            )
            txn.settled_on = origin + timedelta(days=1)
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings_all_scenarios(
                account.id,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, baseline_id,
            ) == Decimal("500.00")
            assert posting_service.account_posting_total(
                account.id, what_if.id,
            ) == Decimal("460.00")
            for scenario_id in (baseline_id, what_if.id):
                assert len(_correction_entries(
                    account.id, scenario_id,
                    PostingSourceEnum.ACCOUNT_OPENING,
                )) == 1

    def test_loan_account_is_a_noop_everywhere(self, app, db, seed_user):
        """A loan syncs nothing here and is excluded from the resync set.

        The loan carries an ``AccountAnchorHistory`` row like every account,
        but its corrections belong to the loan posting package; the account
        entry points skip it and the per-user enumerator never returns it.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            loan = create_loan_account(seed_user, _db.session)
            _db.session.commit()

            account_posting_service.sync_account_anchor_postings(
                loan.id, scenario_id,
            )
            all_scenarios = (
                account_posting_service
                .sync_account_anchor_postings_all_scenarios
            )
            all_scenarios(loan.id)
            _db.session.commit()

            for source in (
                PostingSourceEnum.ACCOUNT_OPENING,
                PostingSourceEnum.ACCOUNT_TRUEUP,
            ):
                assert _correction_entries(loan.id, scenario_id, source) == []
            resynced = (
                account_posting_service.resync_user_account_anchor_postings(
                    seed_user["user"].id,
                )
            )
            assert loan.id not in resynced

    def test_resync_user_posts_every_non_loan_account(
        self, app, db, seed_user,
    ):
        """The per-user resync enumerates non-loan accounts and posts openings.

        seed_user carries the fixture Checking ($1000.00 origination); add a
        Savings ($500.00) and a loan.  The resync returns exactly the two
        non-loan ids (ascending) and posts each opening: Checking 1000.00,
        Savings 500.00.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            checking = seed_user["account"]
            savings = _make_account(seed_user, "500.00")
            create_loan_account(seed_user, _db.session)
            _db.session.commit()

            resynced = (
                account_posting_service.resync_user_account_anchor_postings(
                    seed_user["user"].id,
                )
            )
            _db.session.commit()

            assert resynced == sorted([checking.id, savings.id])
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("500.00")

    def test_wired_create_posts_opening_unprompted(self, app, db, seed_user):
        """``create_account`` posts the opening with NO manual sync call (C6).

        The factory itself drives the all-scenarios sync after the ledger
        pairing, so a $750.00 account carries its balanced opening the
        moment the creating transaction commits -- nothing here invokes the
        posting package.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "750.00")
            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("750.00")
            assert len(_correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            )) == 1

    def test_wired_self_heal_absorbs_pre_assertion_settle(
        self, app, db, seed_user,
    ):
        """A pre-assertion settle re-bases the opening with NO manual sync (C6).

        A settle dated in the 2024 bootstrap period -- BEFORE the account's
        origination assertion -- so the effect-time self-heal at the
        ``sync_transaction_postings`` tail re-derives the opening in the same
        transaction: the opening key moves to +700.00 (500 - (-200)) and the
        account's total stays exactly on the 500.00 anchor.

        The row reached that day through the NULL-``paid_at`` fallback until
        plan step X-f1; it states the day directly now, and the figure is
        unchanged because the day is.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            _settle_expense(
                seed_user, account, "200.00",
                seed_user["bootstrap_period"].start_date,
            )
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("500.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            openings = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_OPENING,
            )
            assert sum(
                (_entry_legs(entry.id)[linked.id][0] for entry in openings),
                Decimal("0"),
            ) == Decimal("700.00")

    def test_wired_trueup_and_revert_self_heal_end_to_end(
        self, app, db, seed_user,
    ):
        """The true-up chokepoint books the delta; a revert re-bases it (C6).

        End to end with NO manual account-sync call anywhere:

          settle -200 at server-now, then assert $350.00 through
          ``anchor_service.apply_anchor_true_up`` (a later instant, so the
          settle is absorbed): the wiring books the true-up delta
          350 - (500 - 200) = +50.00 and the total lands on the anchor.

          revert the settle: the ``posting_service`` tail self-heal alone
          re-derives the true-up (ledger_before 500, delta -150; heal
          -200), keeping the total exactly on the 350.00 anchor.
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            txn = _settle_expense(
                seed_user, account, "200.00", _db.func.now(),
            )
            _db.session.commit()

            outcome = anchor_service.apply_anchor_true_up(
                account=account,
                new_balance=Decimal("350.00"),
                anchor_period=seed_user["bootstrap_period"],
            )
            assert outcome is AnchorTrueUpOutcome.COMMITTED
            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("350.00")
            linked = _ledger_of_kind(account.id, LedgerAccountKindEnum.LINKED)
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert len(trueups) == 1
            assert _entry_legs(trueups[0].id)[linked.id][0] == Decimal("50.00")

            posting_service.sync_transaction_postings(txn, settled=False)
            _db.session.commit()

            assert posting_service.account_posting_total(
                account.id, scenario_id,
            ) == Decimal("350.00")
            trueups = _correction_entries(
                account.id, scenario_id, PostingSourceEnum.ACCOUNT_TRUEUP,
            )
            assert sum(
                (_entry_legs(entry.id)[linked.id][0] for entry in trueups),
                Decimal("0"),
            ) == Decimal("-150.00")

    def test_baselineless_owner_skips_loudly_then_recovers(
        self, app, db, seed_user, caplog,
    ):
        """No baseline + no postings: skip with a loud log; resync recovers.

        A second user with pay periods but NO scenario: the all-scenarios
        sync has nowhere to post (postings are scenario-scoped), logs the
        skip, and writes nothing.  Once a baseline exists, the per-user
        resync posts the stranded opening -- the ``create_baseline``
        recovery path.
        """
        with app.app_context():
            user2 = User(
                email="second@shekel.local",
                password_hash=hash_password("testpass-2"),
                display_name="Second User",
            )
            _db.session.add(user2)
            _db.session.flush()
            period2 = PayPeriod(
                user_id=user2.id, start_date=seed_user["bootstrap_period"].start_date,
                end_date=seed_user["bootstrap_period"].end_date, period_index=0,
            )
            _db.session.add(period2)
            _db.session.flush()
            checking_type_id = seed_user["account"].account_type_id
            account2 = account_service.create_account(
                account_service.AccountSpec(
                    user_id=user2.id,
                    account_type_id=checking_type_id,
                    name="U2 Checking",
                    anchor_balance=Decimal("100.00"),
                    anchor_period_id=period2.id,
                ),
            )
            _db.session.commit()

            with caplog.at_level("WARNING"):
                sync_all = (
                    account_posting_service
                    .sync_account_anchor_postings_all_scenarios
                )
                sync_all(account2.id)
            _db.session.commit()
            assert "no baseline scenario" in caplog.text
            assert (
                _db.session.query(JournalEntry)
                .filter_by(user_id=user2.id)
                .count()
            ) == 0

            baseline2 = Scenario(
                user_id=user2.id, name="Baseline", is_baseline=True,
            )
            _db.session.add(baseline2)
            _db.session.commit()
            account_posting_service.resync_user_account_anchor_postings(
                user2.id,
            )
            _db.session.commit()
            assert posting_service.account_posting_total(
                account2.id, baseline2.id,
            ) == Decimal("100.00")


class TestLedgerAgreesWithTheGridOnAssertionPeriods:
    """The posted ledger and the grid bucket a true-up into the SAME period.

    The property ruling R-EA was decided on, pinned as a test rather than left
    as a measurement in a docstring (plan step X-ai-r; finding N-169).  Two
    independent implementations answer "which pay period did this balance
    assertion book in": the WRITER derives it through
    ``loan_ledger.resolve_anchor_pay_period`` and stamps
    ``journal_entries.pay_period_id``; the READER
    (``balance_at._cash_periods._assertion_sums``) buckets the walk's
    corrections by ``spans.containing(observed_on)`` and renders the result as
    the grid's "Book vs bank" row.  They agreed on 61 of 61 periods of a
    production clone when the ruling was taken; nothing in the tree held them
    together afterwards, which is how the ledger and the grid came to disagree
    by a whole correction in the first place.

    **Scoped to TRUE-UPS, and that is not a convenience.**  ``_assertion_sums``
    folds ``anchor_corrections[1:]`` -- ruling R-I moves the OPENING into the
    fold's seed, where it back-projects rather than stepping the balance on its
    own day -- while the ledger keeps an ``account_opening`` entry in a period.
    Comparing the opening would compare two different questions.
    """

    def test_trueup_periods_match_the_grids_book_vs_bank_row(
        self, app, db, seed_user,
    ):
        """Every period's posted true-up net equals the grid's book_vs_bank.

        Three assertions across two periods on one account, so the comparison
        has something to disagree about: +100.00 and -50.00 in the first
        period and +250.00 in the second.  The ledger's per-period true-up net
        must equal the figure the grid renders for that period, cell for cell,
        with at least one period non-zero (a comparison of all-zeros grades
        nothing).
        """
        with app.app_context():
            scenario_id = seed_user["scenario"].id
            account = _make_account(seed_user, "500.00")
            _pin_opening(account)
            first = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=date(2026, 3, 21), end_date=date(2026, 4, 3),
                period_index=1,
            )
            second = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=date(2026, 4, 4), end_date=date(2026, 4, 17),
                period_index=2,
            )
            _db.session.add_all([first, second])
            _db.session.flush()
            _add_assertion(
                account, "600.00", settle_instant_on(date(2026, 3, 25)),
            )
            _add_assertion(
                account, "550.00", settle_instant_on(date(2026, 4, 1)),
            )
            _add_assertion(
                account, "800.00", settle_instant_on(date(2026, 4, 10)),
            )
            _db.session.commit()
            account_posting_service.sync_account_anchor_postings(
                account.id, scenario_id,
            )
            _db.session.commit()

            ctx = balance_at.BalanceContext.build(
                seed_user["user"].id, as_of=date(2026, 4, 17),
            )
            periods = (
                _db.session.query(PayPeriod)
                .filter_by(user_id=seed_user["user"].id)
                .order_by(PayPeriod.period_index)
                .all()
            )
            grid = balance_at.grid_balance_view(account, ctx, periods)

            compared = 0
            for period in periods:
                column = grid.columns.get(period.id)
                if column is None:
                    continue
                assert _correction_net_in_period(
                    account.id, scenario_id,
                    PostingSourceEnum.ACCOUNT_TRUEUP, period.id,
                ) == column.book_vs_bank
                compared += 1
            assert compared == len(periods)
            # The comparison has teeth only if something is non-zero: the
            # first period books 600-500 = +100.00 then 550-600 = -50.00
            # (net +50.00) and the second 800-550 = +250.00.
            assert grid.columns[first.id].book_vs_bank == Decimal("50.00")
            assert grid.columns[second.id].book_vs_bank == Decimal("250.00")
