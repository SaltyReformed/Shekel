"""Shekel Budget App -- the cash ledger WALK (plan step X-a).

Grades ``app.services.cash_ledger.walk_cash_ledger`` and its visible-day re-key
``dated_deltas`` -- the leaf a cash account's balance will fold over (step X-b)
and the posting writer will project (step X-d).  The walk is ADDITIVE at X-a: no
production surface reads it yet, so nothing here can move a shipped balance.

Every expected figure below is HAND-COMPUTED and written out in the test that
asserts it.  None is taken from another producer: the whole point of the leaf is
that the shipping projection is WRONG about these cases (findings cash D1-D3), so
grading it against that projection would prove the defect rather than the fix
(plan Section 7.2, finding N-7).

The load-bearing case is :class:`TestTheInstantPartition`.  It reproduces the
shape measured on production 2026-07-25: an assertion at 12:57:08 UTC with two
expenses settled at 13:07:11 and 13:07:18 -- the SAME UTC civil day, ten minutes
later.  A date-keyed partition absorbs them into the assertion and loses their
$108.15 of confirmed cash effect; the instant-keyed walk rides them on top.  The
two are separated by a test that a date-keyed implementation cannot pass, not by
a comment.
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from app.extensions import db
from app.models.account import AccountAnchorHistory
from app.services.cash_ledger import (
    cash_anchor_facts,
    dated_deltas,
    settled_cash_facts,
    walk_cash_ledger,
)
from app.utils.dates import DISPLAY_TIMEZONE, to_display_date
from tests._test_helpers import (
    append_balance_assertion,
    create_settled_cash_transaction,
    create_settled_transfer,
    create_savings_account,
    freeze_today,
    restamp_opening_assertion,
)


def _instant(year, month, day, hour=0, minute=0, second=0):
    """Return the aware-UTC instant of a wall-clock moment on the USER's day.

    The arguments are read as the DISPLAY timezone -- the clock the user is
    actually looking at -- and converted to UTC for storage, which is the
    direction production runs in: ``paid_at`` and ``created_at`` are stamped
    when the user acts and stored UTC.

    **It read them as UTC until ruling R-DH (b)** (2026-07-31), and the default
    ``hour=0`` then meant midnight UTC -- 7pm or 8pm the PREVIOUS Eastern day.
    So a fixture writing ``_instant(2026, 1, 15)`` to mean "this settled on the
    15th" pinned an event the fold correctly places on the 14th, and five tests
    in this class asserted figures for a day their own setup had not built.
    Reading the arguments as Eastern makes the helper mean what every call site
    already said it meant, and it preserves same-day ORDERING exactly: two
    moments on one day shift by the same offset.
    """
    return datetime(
        year, month, day, hour, minute, second, tzinfo=DISPLAY_TIMEZONE,
    ).astimezone(timezone.utc)


def _restamp_opening(account, at):
    """Pin the factory-written opening assertion's instant (shared builder)."""
    return restamp_opening_assertion(db.session, account, at)


def _assert_balance(account, period, balance, at):
    """Append one balance ASSERTION (true-up) at a pinned instant (shared)."""
    return append_balance_assertion(db.session, account, period, balance, at)


def _corrections(account, scenario):
    """Return ``{asserted_at: (balance_before, delta)}`` for readable asserts."""
    walk = walk_cash_ledger(account.id, scenario.id)
    return {
        correction.anchor.asserted_at: (
            correction.balance_before,
            correction.anchor.anchor_balance - correction.balance_before,
        )
        for correction in walk.anchor_corrections
    }


def _running_balance(account, scenario):
    """Return the walk's final running balance by summing :func:`dated_deltas`.

    :class:`TestDatedDeltasReconstructTheWalk` proves this equals the replay's
    own terminal balance by reconstructing that balance INDEPENDENTLY from
    ``anchor_corrections`` + the post-assertion ``source_facts``, so this helper
    is a convenience rather than the only statement of the total.
    """
    return sum(
        (delta for _day, delta in dated_deltas(
            walk_cash_ledger(account.id, scenario.id),
        )),
        Decimal("0.00"),
    )


def _replay_terminal_balance(account, scenario):
    """Reconstruct the walk's end balance WITHOUT :func:`dated_deltas`.

    The independent reference: take the LAST assertion's asserted balance and
    add every source dated strictly AFTER the day that assertion closes.  That
    is the replay's definition (ruling R-DH (a)) read off
    :class:`CashLedgerWalk`'s two lists directly, so comparing it against the
    summed dated deltas is a real cross-check rather than a producer graded on
    itself.

    The comparison is ``>`` on the civil DAY, not on an instant: a source
    sharing the assertion's day is inside the closing balance, so only a
    strictly later day rides on top.
    """
    walk = walk_cash_ledger(account.id, scenario.id)
    if not walk.anchor_corrections:
        return Decimal("0.00")
    last = walk.anchor_corrections[-1].anchor
    return last.anchor_balance + sum(
        (
            fact.delta for fact in walk.source_facts
            if fact.settled_on > last.observed_on
        ),
        Decimal("0.00"),
    )


def _linked_ledger_net(account, scenario, *, transaction_id=None):
    """Return the net posted on *account*'s LINKED ledger, optionally per row.

    The posted-side window the walk's deltas are graded against.  Reads a
    DIFFERENT join shape from anything production runs, so the two cannot share
    a lookup bug -- the same rule the loan reconciliation suite follows.
    """
    from app.models.journal_entry import JournalEntry, Posting  # pylint: disable=import-outside-toplevel
    from app.services.posting_reads import _ledger_account_for  # pylint: disable=import-outside-toplevel

    linked = _ledger_account_for(account.id)
    query = (
        db.session.query(db.func.coalesce(db.func.sum(Posting.amount), 0))
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            Posting.ledger_account_id == linked.id,
            JournalEntry.scenario_id == scenario.id,
        )
    )
    if transaction_id is not None:
        query = query.filter(JournalEntry.transaction_id == transaction_id)
    return Decimal(str(query.scalar()))


class TestTheClosingBalancePartition:
    """An assertion is the CLOSING BALANCE for its civil day (ruling R-DH (a)).

    The production shape, reproduced: opening $1,000.00, then an assertion of
    $2,932.41 on 2026-07-24, with expenses settling either side of it on that
    same civil day.

    **This class asserted the opposite until 2026-07-31**, when the INSTANT
    partition it pinned rendered the developer's own grid at ``-$4,021.37``
    against a true ``-$19.95``: three payments recorded in the nine seconds
    AFTER an anchor were subtracted from a bank balance that already contained
    them.  Neither instant the partition compared is a fact about money --
    ``paid_at`` is ``db.func.now()`` at the click (``status_seam.py:105``) and
    an ``AccountAnchorHistory`` row has no date column at all -- so it decided
    which of two BUTTONS was pressed first and spent that answer on cash.  See
    ``docs/audits/balance_architecture/anchor_settle_partition.md``.
    """

    def test_a_settle_recorded_after_the_assertion_is_still_absorbed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """$108.15 and $131.60 recorded 10 minutes later move NOTHING.

        Hand-computed: both expenses carry the assertion's own civil day, so the
        day's closing balance is the asserted $2,932.41 and the walk ends there.

        **The ruling, and what it costs and buys.**  It buys the developer's
        actual workflow: read the bank, enter the anchor, then tick off what
        cleared -- an order in which every ticked row is already inside the
        number just entered.  It costs the case where a payment genuinely clears
        AFTER the balance was read on the same day; that one is absorbed here and
        surfaces at the next assertion.  Measured over four months of real data
        the trade is decisive: the correction the model must plug falls from
        ``$40,554.34`` gross / ``-$6,998.90`` net to ``$14,286.82`` /
        ``-$940.06``, and this rule is the only one under which the walk lands on
        the balance the bank actually shows.

        Both fixture rows are PLAIN transactions, so each would move its full
        amount; on the real account the second row's purchases were entirely
        credit-card, so its confirmed cash effect is $0.00 (see
        :class:`TestSourceFactValuation`'s credit-entry case).
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[6]
        _restamp_opening(account, _instant(2026, 1, 1))
        _assert_balance(
            account, period, Decimal("2932.41"),
            _instant(2026, 7, 24, 12, 57, 8),
        )
        for amount, at in (
            (Decimal("108.15"), _instant(2026, 7, 24, 13, 7, 11)),
            (Decimal("131.60"), _instant(2026, 7, 24, 13, 7, 18)),
        ):
            create_settled_cash_transaction(
                seed_user, db.session, period, amount, paid_at=at,
            )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("2932.41")

    def test_a_settle_before_the_assertion_is_absorbed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A $50.00 expense settled 57 minutes EARLIER moves nothing.

        The user asserted $2,932.41 at 12:57:08 having already spent the $50.00
        at 12:00:00, so it is inside the asserted figure.  Hand-computed: the
        assertion's ``balance_before`` is ``1000.00 - 50.00 = 950.00`` and the
        walk ends on the asserted $2,932.41 exactly.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[6]
        _restamp_opening(account, _instant(2026, 1, 1))
        asserted_at = _instant(2026, 7, 24, 12, 57, 8)
        _assert_balance(account, period, Decimal("2932.41"), asserted_at)
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("50.00"),
            paid_at=_instant(2026, 7, 24, 12, 0, 0),
        )
        db.session.commit()

        before, delta = _corrections(account, scenario)[asserted_at]
        assert before == Decimal("950.00")
        assert delta == Decimal("1982.41")
        assert _running_balance(account, scenario) == Decimal("2932.41")

    def test_a_settle_at_exactly_the_assertion_instant_is_absorbed(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The boundary is ``<=``, matching the account posting walk.

        A source attributed at the very instant of the assertion is subsumed by
        its reset -- the same ``sources[i][0] <= fact.asserted_at`` boundary
        ``account_posting_service.walk_account_ledger`` applies, so the read fold
        and the posted ledger partition one boundary rather than two.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[6]
        _restamp_opening(account, _instant(2026, 1, 1))
        asserted_at = _instant(2026, 7, 24, 12, 57, 8)
        _assert_balance(account, period, Decimal("2932.41"), asserted_at)
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("77.00"),
            paid_at=asserted_at,
        )
        db.session.commit()

        before, _delta = _corrections(account, scenario)[asserted_at]
        assert before == Decimal("923.00")
        assert _running_balance(account, scenario) == Decimal("2932.41")

    def test_both_same_day_settles_go_with_the_assertion_whatever_the_order(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The discriminating control: one civil day, ONE answer (ruling R-DH (a)).

        Both expenses carry the civil date 2026-07-24, exactly as the assertion
        does -- one recorded hours BEFORE it and one hours AFTER.  The assertion
        is that day's closing balance, so both are inside it and neither moves
        the walk.  Hand-computed: ``balance_before`` is
        ``1000.00 - 40.00 - 60.00 = 900.00`` and the walk ends on the asserted
        $2,932.41.

        **This is the order-independence property, at walk grain.**  The whole
        defect was that these two rows got different treatment for no reason a
        user could see or control; here they get the same one.  An
        instant-keyed implementation cannot pass this test -- it answers
        ``2872.41`` -- which is the same discriminating role the class had
        before, pointed the other way.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[6]
        _restamp_opening(account, _instant(2026, 1, 1))
        asserted_at = _instant(2026, 7, 24, 12, 57, 8)
        _assert_balance(account, period, Decimal("2932.41"), asserted_at)
        earlier = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("40.00"),
            paid_at=_instant(2026, 7, 24, 9, 0, 0), name="before",
        )
        later = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("60.00"),
            paid_at=_instant(2026, 7, 24, 20, 0, 0), name="after",
        )
        db.session.commit()

        facts = {
            fact.transaction_id: fact
            for fact in settled_cash_facts(account.id, scenario.id)
        }
        # One civil day for the assertion and both settles -- which is exactly
        # the information the rule acts on.
        assert facts[earlier.id].settled_on == date(2026, 7, 24)
        assert facts[later.id].settled_on == date(2026, 7, 24)
        assert to_display_date(asserted_at) == date(2026, 7, 24)

        before, _delta = _corrections(account, scenario)[asserted_at]
        assert before == Decimal("900.00")
        assert _running_balance(account, scenario) == Decimal("2932.41")


class TestEveryAssertionIsReplayed:
    """The past is the assertion history, not today's assertion carried back."""

    def test_three_assertions_each_reset_the_running_balance(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Hand-computed corrections and a per-date fold across the history.

        Stream: opening $1,000.00 (2026-01-01); -$200.00 (2026-02-01);
        assert $900.00 (2026-03-01); -$300.00 (2026-04-01); assert $500.00
        (2026-05-01).

        ``balance_before`` at the March assertion is ``1000 - 200 = 800``, so its
        correction is ``+100.00``; at the May assertion it is ``900 - 300 = 600``,
        so its correction is ``-100.00``.  Prefix-summing the dated deltas
        therefore reads 1000 / 800 / 900 / 600 / 500 across the five event days --
        the balance the user actually asserted at each point, where the shipping
        scalar answers today's $500.00 for every one of them (finding B-18).
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("200.00"),
            paid_at=_instant(2026, 2, 1), name="feb spend",
        )
        march = _instant(2026, 3, 1)
        _assert_balance(account, period, Decimal("900.00"), march)
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("300.00"),
            paid_at=_instant(2026, 4, 1), name="apr spend",
        )
        may = _instant(2026, 5, 1)
        _assert_balance(account, period, Decimal("500.00"), may)
        db.session.commit()

        corrections = _corrections(account, scenario)
        assert corrections[march] == (Decimal("800.00"), Decimal("100.00"))
        assert corrections[may] == (Decimal("600.00"), Decimal("-100.00"))

        running = Decimal("0.00")
        seen = []
        for _day, delta in dated_deltas(
            walk_cash_ledger(account.id, scenario.id),
        ):
            running += delta
            seen.append(running)
        assert seen == [
            Decimal("1000.00"), Decimal("800.00"), Decimal("900.00"),
            Decimal("600.00"), Decimal("500.00"),
        ]


class TestSourceFactValuation:
    """A settled row is worth its ``effective_amount``, signed by direction."""

    def test_income_adds_and_expense_subtracts(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """+$250.00 income and -$75.00 expense: 1000 + 250 - 75 = 1175.00."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("250.00"),
            is_income=True, paid_at=_instant(2026, 2, 1), name="pay",
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("75.00"),
            paid_at=_instant(2026, 2, 2), name="spend",
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("1175.00")

    def test_actual_amount_wins_over_the_estimate(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A $100.00 estimate that settled at $84.20 is worth $84.20.

        ``effective_amount`` prefers ``actual_amount`` when populated, so the
        walk values a settled row at what really moved: 1000 - 84.20 = 915.80.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("100.00"),
            actual_amount=Decimal("84.20"),
            paid_at=_instant(2026, 2, 1), name="spend",
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("915.80")

    def test_a_still_projected_row_is_not_in_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """PLANNED rows belong to the reader, not the clock-free walk (R-G).

        A projected row's effective date is ``max(its date, as_of + 1d)``, which
        depends on the reader's as-of; putting it in the walk would make this
        leaf's output a function of the wall clock -- the corruption shape plan
        step A3 removed from the loan walk.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum, TxnTypeEnum  # pylint: disable=import-outside-toplevel
        from app.models.transaction import Transaction  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        db.session.add(Transaction(
            account_id=account.id,
            pay_period_id=period.id,
            scenario_id=scenario.id,
            status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            name="unpaid bill",
            transaction_type_id=ref_cache.txn_type_id(TxnTypeEnum.EXPENSE),
            estimated_amount=Decimal("500.00"),
        ))
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")

    def test_a_credit_card_entry_never_leaves_checking(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """An envelope's CREDIT entries are excluded from the cash effect.

        The shape this valuation exists for, and the one an ``effective_amount``
        walk gets wrong.  A $200.00 grocery envelope settled with a $120.00 debit
        purchase and an $80.00 credit-card purchase moved only $120.00 out of
        checking -- the credit portion leaves when its CC Payback sibling
        settles, so counting it here debits the money twice.  Hand-computed:
        1000 - 120 = 880.00, and the walk's delta is -$120.00 not -$200.00.

        Measured on production 2026-07-25 before the shared
        :func:`~app.services.cash_ledger.settled_cash_leg` was adopted: an
        ``effective_amount`` walk diverged from the posted ledger on 10 of the
        real Checking account's 130 settled rows, by up to $181.58.
        """
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import StatusEnum  # pylint: disable=import-outside-toplevel
        from app.services import (  # pylint: disable=import-outside-toplevel
            entry_service, posting_service, status_seam,
        )
        from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel
        from tests._test_helpers import create_envelope_txn  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        txn = create_envelope_txn(
            seed_user, db.session, period, "Groceries", Decimal("200.00"),
        )
        for amount, is_credit, day in (
            (Decimal("120.00"), False, date(2026, 1, 5)),
            (Decimal("80.00"), True, date(2026, 1, 6)),
        ):
            db.session.add(TransactionEntry(
                transaction_id=txn.id,
                user_id=seed_user["user"].id,
                amount=amount,
                description="purchase",
                entry_date=day,
                is_credit=is_credit,
                is_cleared=True,
            ))
        db.session.flush()
        # Routed through PRODUCTION's own rule rather than hand-set: the
        # ``effective - credit`` formula collapses to the debit-only outflow
        # ONLY because the settled actual is the sum of ALL entries, so a test
        # that stipulated that premise would keep passing if production stopped
        # honouring it.
        txn.actual_amount = entry_service.compute_actual_from_entries(
            list(txn.entries),
        )
        assert txn.actual_amount == Decimal("200.00")
        db.session.flush()
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
        )
        txn.paid_at = _instant(2026, 2, 1)
        posting_service.sync_transaction_postings(txn, settled=True)
        db.session.commit()

        fact, = settled_cash_facts(account.id, scenario.id)
        assert fact.delta == Decimal("-120.00")
        assert _running_balance(account, scenario) == Decimal("880.00")
        # The claim the whole ``settled_cash_leg`` move rests on: the walk's
        # delta IS the amount the writer booked on the linked ledger, in the
        # SAME sign.  Asserting the sign here is what keeps plan step X-d from
        # wiring the writer onto a negated feed -- a flip that still balances
        # every entry, so nothing else would catch it.
        assert _linked_ledger_net(
            account, scenario, transaction_id=txn.id,
        ) == fact.delta

    def test_a_transfer_shadow_participates_like_any_other_row(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Transfer Invariant 5: transfers reach the walk as their shadow rows.

        A $300.00 transfer out of Checking is an ordinary settled expense row to
        this walk, exactly as it is to the projection engine -- neither queries
        ``Transfer``.  Hand-computed: 1000 - 300 = 700.00.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("0.00"),
            anchor_period_id=period.id,
        )
        create_settled_transfer(
            seed_user, db.session, account, savings, period,
            amount=Decimal("300.00"), paid_at=_instant(2026, 2, 1),
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("700.00")


class TestAttributionIsOneKey:
    """One instant per fact; the civil day it counts from falls out of it."""

    def test_a_null_paid_at_falls_back_to_the_period_start(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """8 of 146 settled prod rows carry no ``paid_at``; the fallback is real.

        The day becomes the row's pay-period ``start_date``, returned
        UNCONVERTED -- the same civil day
        :func:`app.services.posting_service._civil_settle_date` gives the entry
        dating, because both delegate to
        :func:`app.utils.dates.to_display_civil_date`.

        **The "unconverted" half is the load-bearing one** (ruling R-DH (b)).
        The fallback is already a civil date and was never an instant, so a rule
        that manufactured midnight and converted it to the display zone would
        shift this row a day EARLIER and could carry it into the previous pay
        period.  Measured on production 2026-07-31: 4 of the real Checking
        account's settled rows carry no ``paid_at`` and 3 of the 4 would cross a
        period boundary under that mistake.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[3]
        _restamp_opening(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("12.34"),
            paid_at=None, name="legacy settle",
        )
        db.session.commit()

        fact, = settled_cash_facts(account.id, scenario.id)
        assert fact.settled_on == period.start_date

    def test_the_settled_day_is_the_users_day_not_the_utc_day(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settle at 23:30 Eastern counts from the user's day, not UTC's.

        The discriminating instant: 2026-03-03 23:30 Eastern is already
        2026-03-04 in UTC.  Picking one where the two zones agree would pin
        nothing.

        **Ruling R-DH (b) inverted this test** (2026-07-31; it asserted the UTC
        day, and was named for it).  The balance ledgers were on the STORAGE
        clock because ``journal_entries.entry_date`` was stamped through
        ``utc_civil_date``; that writer moved to the user's day WITH both folds,
        so the equality this test guards still holds and now holds on the
        calendar the ``DATE`` columns it is compared against actually mean.
        Measured on production: 22 of 139 settled Checking rows land on a
        different day under UTC, 5 of them in a different PAY PERIOD, and two
        evening sessions were split across two UTC days -- the shape that
        defeats the closing-balance partition above.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[2]
        _restamp_opening(account, _instant(2026, 1, 1))
        txn = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("5.00"),
            paid_at=_instant(2026, 3, 3, 23, 30),
        )
        db.session.commit()

        # The STORED instant really is the next UTC day, so the assertion below
        # is a zone choice and not a coincidence.
        assert txn.paid_at.astimezone(timezone.utc).date() == date(2026, 3, 4)

        fact, = settled_cash_facts(account.id, scenario.id)
        assert fact.settled_on == date(2026, 3, 3)


class TestTheWalkSeesOnlyItsOwnRows:
    """Scope: this account, this scenario, contributing rows only."""

    def test_a_non_contributing_row_is_excluded_whatever_it_carries(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A soft-deleted settled envelope must not reach the walk.

        The guard that matters most for money: ``settled_cash_leg`` is TOTAL and
        returns ``0.00`` for a non-contributing row, but without the SQL
        exclusion the row would still enter the stream -- and a deleted envelope
        carrying an $80.00 credit entry is precisely the shape that used to
        value at a fabricated ``+$80.00`` inflow.  Both defences are pinned: the
        row is absent, AND the valuation of it is zero.
        """
        from app.models.transaction_entry import TransactionEntry  # pylint: disable=import-outside-toplevel
        from app.services.cash_ledger import settled_cash_leg  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        txn = create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("80.00"),
            paid_at=_instant(2026, 2, 1), name="deleted envelope",
        )
        db.session.add(TransactionEntry(
            transaction_id=txn.id,
            user_id=seed_user["user"].id,
            amount=Decimal("80.00"),
            description="credit purchase",
            entry_date=date(2026, 2, 1),
            is_credit=True,
            is_cleared=True,
        ))
        txn.is_deleted = True
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert settled_cash_leg(txn) == Decimal("0.00")
        assert _running_balance(account, scenario) == Decimal("1000.00")

    def test_another_scenarios_rows_do_not_enter_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Assertions are per-ACCOUNT; settled rows are per-SCENARIO.

        The split ``walk_cash_ledger`` documents as a deliberate contract: the
        same assertions replay in every scenario, each against that scenario's
        own rows.  A what-if scenario's spending must not move the baseline.
        """
        from app.models.scenario import Scenario  # pylint: disable=import-outside-toplevel

        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        other = Scenario(
            user_id=seed_user["user"].id, name="What if", is_baseline=False,
        )
        db.session.add(other)
        db.session.flush()
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("400.00"),
            scenario=other, paid_at=_instant(2026, 2, 1), name="what-if spend",
        )
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")
        # ...and the SAME assertions replay in the other scenario, against its
        # own row.
        assert _running_balance(account, other) == Decimal("600.00")

    def test_another_accounts_rows_do_not_enter_the_walk(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """A settled row on a sibling account of the same user is excluded."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        savings = create_savings_account(
            seed_user, db.session, "Savings", Decimal("50.00"),
            anchor_period_id=period.id,
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("25.00"),
            account=savings, paid_at=_instant(2026, 2, 1), name="other acct",
        )
        db.session.commit()

        assert settled_cash_facts(account.id, scenario.id) == []
        assert _running_balance(account, scenario) == Decimal("1000.00")

    def test_a_liability_accounts_negative_anchor_walks_ledger_native(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The walk never branches on account class -- the sign follows TYPE.

        A Credit Card carries an owed-as-NEGATIVE anchor, and a direct charge on
        it is an EXPENSE, so it must make the balance more negative.
        Hand-computed: an asserted -$300.00 with a $75.00 charge settled after
        it walks to -$375.00.  Nothing in the walk consults the account's class
        to get that right, which is what makes the claim structural.
        """
        from tests._test_helpers import create_account_of_type  # pylint: disable=import-outside-toplevel

        _account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        card = create_account_of_type(
            seed_user, db.session, "Credit Card", "Visa", Decimal("-300.00"),
        )
        _restamp_opening(card, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("75.00"),
            account=card, paid_at=_instant(2026, 2, 1), name="charge",
        )
        db.session.commit()

        assert _running_balance(card, scenario) == Decimal("-375.00")


class TestPreOpeningSources:
    """A settle attributed BEFORE the account's first assertion (finding N-37).

    Live on production 2026-07-25: two accounts carry the shape (Fidelity
    Savings 1 row, the Money Market 4).  The behaviour was pinned here so plan
    step X-b's ruling had something to flip, exactly as finding N-34 was gated
    before C2b fixed it.

    **RULED 2026-07-25 (R-I), and the ruling did NOT change this leaf.**  The
    fold back-projects the first assertion over the records it already contains;
    ``dated_deltas`` keeps emitting a pre-opening row at its own day, because the
    posted ledger holds the same partial sum there and re-keying it would break
    the walk-vs-ledger equality plan step X-d rests on.  So both assertions below
    stand as WALK contracts, and the READER's answer -- the one the ruling is
    about -- is graded in ``test_cash_fold.py``'s ``TestTheOpeningMovesIntoTheSeed``.
    """

    def test_it_is_absorbed_into_the_opening_and_the_total_is_right(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """At and after the opening the answer is correct, and that is the half
        the walk owes.

        Hand-computed: a $500.00 expense attributed 2026-01-15, an opening
        asserting $1,000.00 on 2026-02-01.  ``balance_before`` is -$500.00, so
        the opening's correction is $1,500.00 and the total lands on $1,000.00
        -- the asserted balance, with the pre-opening row absorbed exactly as
        the posted ledger absorbs it.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        opening_at = _instant(2026, 2, 1)
        _restamp_opening(account, opening_at)
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("500.00"),
            paid_at=_instant(2026, 1, 15), name="pre-opening",
        )
        db.session.commit()

        before, delta = _corrections(account, scenario)[opening_at]
        assert before == Decimal("-500.00")
        assert delta == Decimal("1500.00")
        assert _running_balance(account, scenario) == Decimal("1000.00")

    def test_the_prefix_before_the_opening_is_the_un_absorbed_partial_sum(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """PINNED as a LEAF contract, and deliberately not a balance.

        ``dated_deltas`` emits the pre-opening source at its OWN day, so a
        prefix taken before the opening reads -$500.00: a balance the account
        never had.  It is faithful to the POSTED ledger, which holds the same
        partial sum there, so the leaf does not unilaterally re-key it.

        Ruling R-I (2026-07-25) settled what a READER answers there -- the first
        assertion back-projected over these records, ``$1,500.00`` on this shape
        -- and put it in the FOLD, which is why this stayed green through X-b
        rather than flipping.  The pairing is the point: the leaf's partial sum
        keeps X-d's walk-vs-ledger equality, and the fold's seed keeps the
        balance honest.  ``test_cash_fold.py`` asserts the $1,500.00 against this
        same fixture shape.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 2, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("500.00"),
            paid_at=_instant(2026, 1, 15), name="pre-opening",
        )
        db.session.commit()

        steps = dated_deltas(walk_cash_ledger(account.id, scenario.id))
        prefix = sum(
            (delta for day, delta in steps if day <= date(2026, 1, 20)),
            Decimal("0.00"),
        )
        assert prefix == Decimal("-500.00")


class TestTheWalkReadsNoClock:
    """Its output is a function of the account's data alone."""

    def test_the_walk_is_identical_under_two_different_todays(
        self, db, seed_user, seed_periods, monkeypatch,
    ):  # pylint: disable=unused-argument,too-many-arguments,too-many-positional-arguments
        """Freezing today at two dates yields byte-identical deltas.

        A walk that read the clock would make the ledger a function of when the
        sync happened to run -- the corruption generator plan step A3 removed
        from the loan side (``4e46a0a8``).
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("42.00"),
            paid_at=_instant(2026, 2, 1),
        )
        db.session.commit()

        freeze_today(monkeypatch, date(2026, 2, 15))
        early = dated_deltas(walk_cash_ledger(account.id, scenario.id))
        freeze_today(monkeypatch, date(2030, 12, 31))
        late = dated_deltas(walk_cash_ledger(account.id, scenario.id))
        assert early == late


class TestDegenerateShapes:
    """Total over the states a caller can actually reach."""

    def test_an_account_with_no_assertion_history_walks_empty(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Production-unreachable, but honestly empty rather than a raise.

        A caller that must distinguish "no account" asks the account row, never
        this emptiness -- the totality rule the loan fold already follows.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        db.session.query(AccountAnchorHistory).filter_by(
            account_id=account.id,
        ).delete()
        db.session.commit()

        walk = walk_cash_ledger(account.id, scenario.id)
        assert walk.source_facts == []
        assert walk.anchor_corrections == []
        assert dated_deltas(walk) == []

    def test_an_opening_with_no_prior_activity_corrects_from_zero(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The first assertion's ``balance_before`` is the empty prefix, 0.00."""
        account, scenario = seed_user["account"], seed_user["scenario"]
        opening = _restamp_opening(account, _instant(2026, 1, 1))
        db.session.commit()

        facts = cash_anchor_facts(account.id)
        assert [fact.is_opening for fact in facts] == [True]
        before, delta = _corrections(account, scenario)[
            opening.created_at.astimezone(timezone.utc)
        ]
        assert before == Decimal("0.00")
        assert delta == Decimal("1000.00")


class TestDatedDeltasReconstructTheWalk:
    """The re-key preserves the walk's total and its within-day chronology."""

    def test_the_deltas_sum_to_the_walks_final_running_balance(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """Summing every dated delta reproduces the replay's end state.

        Hand-computed: opening $1,000.00, assert $2,000.00 after a -$100.00
        settle (correction ``2000 - 900 = +1100``), then a -$250.00 settle after
        it.  Total ``1000 - 100 + 1100 - 250 = 1750.00``.

        Graded twice, and the second grading is the point: the summed dated
        deltas must equal the balance reconstructed straight off the walk's own
        two lists (the last assertion plus every source after it), so the re-key
        is checked against the replay rather than against itself.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("100.00"),
            paid_at=_instant(2026, 2, 1), name="pre",
        )
        _assert_balance(
            account, period, Decimal("2000.00"), _instant(2026, 3, 1),
        )
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("250.00"),
            paid_at=_instant(2026, 4, 1), name="post",
        )
        db.session.commit()

        assert _running_balance(account, scenario) == Decimal("1750.00")
        assert _replay_terminal_balance(account, scenario) == Decimal("1750.00")

    def test_two_assertions_at_one_instant_keep_the_loaders_order(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The stable re-sort is load-bearing, so it gets a test.

        ``CashAnchorFact`` carries no ``id``, so once loaded a same-instant pair
        can only be ordered by the stability of the sort in
        ``merge_anchor_and_cash_events``.  Two assertions at the identical
        instant must replay in insertion order -- $700.00 then $800.00 -- so the
        LAST one wins and the walk ends on $800.00, not $700.00.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        same = _instant(2026, 3, 1, 9, 0, 0)
        _assert_balance(account, period, Decimal("700.00"), same)
        _assert_balance(account, period, Decimal("800.00"), same)
        db.session.commit()

        facts = cash_anchor_facts(account.id)
        assert [fact.anchor_balance for fact in facts] == [
            Decimal("1000.00"), Decimal("700.00"), Decimal("800.00"),
        ]
        assert _running_balance(account, scenario) == Decimal("800.00")

    def test_a_source_reads_before_an_assertion_sharing_its_day(
        self, db, seed_user, seed_periods,
    ):  # pylint: disable=unused-argument
        """The re-key mirrors the walk's own tie-break, so both read alike.

        Immaterial to the prefix sum (addition commutes), and pinned so the two
        chronologies cannot silently diverge.
        """
        account, scenario = seed_user["account"], seed_user["scenario"]
        period = seed_periods[0]
        _restamp_opening(account, _instant(2026, 1, 1))
        create_settled_cash_transaction(
            seed_user, db.session, period, Decimal("30.00"),
            paid_at=_instant(2026, 3, 1, 8, 0, 0), name="morning",
        )
        _assert_balance(
            account, period, Decimal("5000.00"), _instant(2026, 3, 1, 17, 0, 0),
        )
        db.session.commit()

        same_day = [
            delta for day, delta in dated_deltas(
                walk_cash_ledger(account.id, scenario.id),
            )
            if day == date(2026, 3, 1)
        ]
        # The source (-30.00) first, then the assertion's correction
        # (5000.00 - 970.00 = 4030.00).
        assert same_day == [Decimal("-30.00"), Decimal("4030.00")]
