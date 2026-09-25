"""Tests for ``posting_service`` (Build-Order Steps 2 and 3).

``posting_service`` is the sole go-forward writer of the double-entry posting
ledger.  Step 2 posts settled **transfers**
(:func:`~app.services.posting_service.sync_transfer_postings`); Step 3 (Commit
4) adds settled ordinary **cash transactions**
(:func:`~app.services.posting_service.sync_transaction_postings`).  Both
reconcile a source's net posted ledger effect to a target by emitting one
balanced delta journal entry, idempotently.
:func:`~app.services.posting_service.account_posting_total`,
:func:`~app.services.posting_service.settled_transfer_effect`, and
:func:`~app.services.posting_service.posted_purchase_effect` are the
reconciliation helpers the oracle consumes (the transaction source's own,
``settled_transaction_effect``, went at plan step ``balance:X-bi-4a``).

The transfer tests pin the load-bearing properties with hand-computed
arithmetic.  **A settled transfer is TWO entries since plan step
``balance:X-bi-6-3``** (rulings **R-BAL45** and **R-BAL101**): one per side's
covering movement, on that movement's own day, each against the owner's
Transfers-in-transit account -- ``{from -amount, transit +amount}`` and
``{to +amount, transit -amount}`` -- so every figure a real account's ledger
carries below is what it was under the one-entry shape, and the transit account
nets to zero per transfer.  The cases that read "the transfer's entry" were
re-expressed for the pair when the shape moved (each docstring says how);
every per-real-account figure is unchanged:

  * **Sign + balance** -- a settle posts ``-amount`` on the from-account's
    ledger and ``+amount`` on the to-account's, each against transit, each
    entry summing to zero; the rule is class-independent (asset->asset AND
    asset->liability).
  * **Effective amount, not transfer amount** -- a settled shadow's RECORD
    (its covering movement's figure) overrides the nominal transfer amount
    (the value the balance calculator and the oracle use).
  * **Idempotency** -- a repeat sync computes ``delta = 0`` and writes
    nothing.
  * **Reversal reads the ledger** -- a revert / delete negates exactly what
    was posted, not the (possibly-edited) transfer amount; a revert ->
    edit-amount -> re-settle posts the new amount.
  * **Reconciliation** -- the per-account posting total equals the
    settled-shadow effect, and both net to zero after a reversal.
  * **Fail loud** -- a ``None`` scenario, a missing ledger-account pairing,
    and an unbalanced set of legs each raise :class:`PostingError`.

The transfer states are built through ``transfer_service`` (the sole transfer
writer, via the ``create_settled_transfer`` helper), so every shadow obeys
the transfer invariants exactly as production produces them.  Commit 5 wires
``posting_service`` into that service, so settling a transfer already
auto-posts its ledger entry; the settle tests below read that auto-posted
entry back, while the idempotency / reversal tests still invoke
``posting_service`` directly to prove a re-sync no-ops or reverses.

The Step-3 transaction tests (the ``TestTransaction*`` /
``TestSettledTransactionEffect`` classes at the foot of the file) cover the
plan's one cash-effect formula -- ``effective - Sigma(credit entries)``, signed
``+`` income / ``-`` expense -- with hand-computed worked examples (plain
income/expense, the debit-only envelope effect, the all-credit no-op), the
correct-by-construction reconcile (idempotency; reversal; the 2.8 CRITICAL
revert -> recategorize -> re-settle, proven at the service layer), counter-leg
routing into the per-category / Uncategorized-fallback account, the transfer-
shadow no-op guard, and fail-loud.  Step 3 has no service wiring yet (that is
Commit 6), so these tests build settled rows via direct ORM and invoke
``sync_transaction_postings`` directly.

**Absolute totals since Build-Order Step 5 (C6).**  ``create_account`` posts
each fixture account's OPENING anchor correction at create time (the seed
Checking's $1000.00, the ledger-suite Savings' $100.00 sentinel), and the
effect-time self-heal at the sync tails keeps those corrections current, so
``account_posting_total`` is an ABSOLUTE balance: ``latest anchor +
post-assertion settled effects``.  A settle dated today (the helper default)
rides on top of the opening; a settle dated in
the 2024 bootstrap period is attributed BEFORE the origination assertion and
is absorbed into the opening delta instead (the total returns to the anchor).
The ``settled_*_effect`` source-table readers are unchanged.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; test bodies bind fixtures by name.
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import (
    LedgerAccountClassEnum,
    PostingKindEnum,
    PostingSourceEnum,
    StatusEnum,
)
from app.extensions import db as _db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.services import (
    anchor_service,
    ledger_account_service,
    posting_service,
    status_seam,
    transfer_service,
)
from app.services._posting_write import (
    _emit_balanced_entry,
    _PostingLeg,
    emit_typed_source_deltas,
)
from app.services.cash_ledger import settled_cash_facts
from app.services.posting_service import PostingError
from app.exceptions import ValidationError
from app.utils.dates import display_today
from tests._test_helpers import (
    family_journal_filter,
    transfer_family_journal_filter,
    transit_ledger_account,
    figure_source_columns,
    add_txn,
    an_entered_day,
    create_account_of_type,
    create_envelope_txn,
    create_settled_cash_transaction,
    create_settled_transfer,
    linked_ledger_account,
    settlement_if_settling,
)
from app.services.settle_day import record_settle_day
from app.services.amount_ownership import state_own_amount
from app.models.amount_ownership import AmountOwnership


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ledger_id(account):
    """Return the LINKED ledger account id for *account* (never its twin)."""
    return linked_ledger_account(_db.session, account.id).id


def _entries_for_transfer(transfer_id):
    """Return every journal entry of the transfer's FAMILY, oldest first.

    The two per-movement entries a settle writes since plan step
    ``balance:X-bi-6-3`` (each linked by ``transaction_entry_id`` to one
    side's covering movement), plus anything the legacy one-entry source still
    links by ``transfer_id`` (``transfer_family_journal_filter``).
    """
    return (
        _db.session.query(JournalEntry)
        .filter(transfer_family_journal_filter(transfer_id))
        .order_by(JournalEntry.id)
        .all()
    )


def _transit_ledger_id(seed_user):
    """Return the owner's Transfers-in-transit ledger account id (a lookup)."""
    return transit_ledger_account(_db.session, seed_user["user"].id).id


def _side_entries(transfer_id):
    """Return ``{account_id: [entries]}`` of a transfer's family by SIDE.

    Each per-movement entry links its side's covering movement, whose
    ``account_id`` is the side's; the legacy ``transfer_id``-linked entries
    (none in a go-forward suite) would carry no movement and are keyed
    ``None``.
    """
    by_side: dict = {}
    for entry in _entries_for_transfer(transfer_id):
        movement = (
            _db.session.get(TransactionEntry, entry.transaction_entry_id)
            if entry.transaction_entry_id is not None else None
        )
        key = movement.account_id if movement is not None else None
        by_side.setdefault(key, []).append(entry)
    return by_side


def _covering_movement_of_side(transfer_id, account_id):
    """Return the covering movement of the transfer's shadow on *account_id*."""
    return (
        _db.session.query(TransactionEntry)
        .join(Transaction, TransactionEntry.transaction_id == Transaction.id)
        .filter(
            Transaction.transfer_id == transfer_id,
            Transaction.account_id == account_id,
            Transaction.is_deleted.is_(False),
            TransactionEntry.covers_settlement.is_(True),
        )
        .one()
    )


def _legs_by_ledger(entry_id):
    """Return ``{ledger_account_id: amount}`` for one entry's legs."""
    return {
        leg.ledger_account_id: leg.amount
        for leg in _db.session.query(Posting)
        .filter_by(journal_entry_id=entry_id)
        .all()
    }


def _scenario_id(seed_user):
    """Return the seed user's baseline scenario id."""
    return seed_user["scenario"].id


def _entries_for_transaction(transaction_id):
    """Return every journal entry for the row's FAMILY, oldest first.

    The row and its covering movement (plan step **X-bi-3a**): a settle
    through the seam mirrors the row's money onto a movement whose entries
    link by ``transaction_entry_id``, so a read keyed on ``transaction_id``
    alone misses the money.  Every figure asserted through this is unchanged.
    """
    return (
        _db.session.query(JournalEntry)
        .filter(family_journal_filter(transaction_id))
        .order_by(JournalEntry.id)
        .all()
    )


def _period_nets(entries):
    """Return ``{pay_period_id: {ledger_account_id: net}}`` over *entries*.

    The per-period reconciliation view the R2 attribution tests assert on:
    an INDEPENDENT re-grouping of the entries' legs by the entry's pay period
    and the leg's ledger account (never the service's own
    ``_posting_write.posted_by_period``), so the tests re-derive the nets the production
    reconcile must have produced.
    """
    nets: dict[int, dict] = {}
    for entry in entries:
        per_ledger = nets.setdefault(entry.pay_period_id, {})
        for leg in (
            _db.session.query(Posting)
            .filter_by(journal_entry_id=entry.id)
            .all()
        ):
            per_ledger[leg.ledger_account_id] = (
                per_ledger.get(leg.ledger_account_id, Decimal("0.00"))
                + leg.amount
            )
    return nets


def _period_nets_for_transaction(transaction_id):
    """Per-period, per-ledger nets over a transaction's journal entries."""
    return _period_nets(_entries_for_transaction(transaction_id))


def _period_nets_for_transfer(transfer_id):
    """Per-period, per-ledger nets over a transfer's journal entries."""
    return _period_nets(_entries_for_transfer(transfer_id))


def _ledger_total(ledger_account_id):
    """Return the net of all posting legs on one ledger account.

    Sums ``account_postings.amount`` directly by ledger account id -- the
    counter-account (category / fallback) analog of
    ``posting_service.account_posting_total``, which keys off a REAL account's
    linked ledger and so cannot be pointed at a category ledger account.
    """
    return (
        _db.session.query(
            _db.func.coalesce(_db.func.sum(Posting.amount), Decimal("0"))
        )
        .filter(Posting.ledger_account_id == ledger_account_id)
        .scalar()
    )


def _add_txn_entry(seed_user, txn, amount, *, is_credit):
    """Attach one purchase entry (debit or credit) to *txn* and flush.

    The shared ``add_entry`` helper only builds debit entries; the envelope
    tests need explicit ``is_credit`` control to exercise the
    ``effective - Sigma(credit)`` formula, so this sets it directly.
    """
    entry = TransactionEntry(
        **figure_source_columns(),
        transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
        user_id=seed_user["user"].id,
        amount=Decimal(amount),
        description="purchase",
        purchased_on=txn.pay_period.start_date,
        is_credit=is_credit,
    )
    _db.session.add(entry)
    _db.session.flush()
    return entry


def _resolve_category_ledger(seed_user, category_key, ledger_class):
    """Return the category ledger account for a seed category and class.

    Idempotent: returns the row ``sync_transaction_postings`` created during
    the test (the resolver respects the partial unique), so a leg's
    ``ledger_account_id`` can be asserted against it.  Passing
    ``category_key=None`` resolves the per-(owner, class) Uncategorized
    fallback.
    """
    category_id = (
        None if category_key is None
        else seed_user["categories"][category_key].id
    )
    return ledger_account_service.get_or_create_category_ledger_account(
        seed_user["user"].id, category_id, ledger_class,
    )


@pytest.fixture()
def savings(app, db, seed_user):  # pylint: disable=unused-argument
    """A second (Savings) account so a transfer has a destination.

    Created in the ``db`` fixture's app context (no nested context) so the
    returned account stays bound to the live session, the same pattern
    ``seed_user`` uses.
    """
    acct = create_account_of_type(
        seed_user, _db.session, "Savings", "Posting Savings",
    )
    _db.session.commit()
    return acct


# ---------------------------------------------------------------------------
# Settle: one balanced entry per confirmed transfer
# ---------------------------------------------------------------------------


class TestSyncSettlePostsBalancedEntry:
    """A settled transfer posts exactly two balanced entries, one per side."""

    def test_asset_to_asset_signs_balance_and_metadata(
        self, app, db, seed_user, savings,
    ):
        """Checking -> Savings $100 posts {Chk -100, T +100} and {Sav +100, T -100}.

        Arithmetic (ruling **R-BAL45**'s shape C, built at plan step
        ``balance:X-bi-6-3``): the from-side entry is -100.00 on Checking (a
        credit: money leaving) against +100.00 on Transfers in transit; the
        to-side entry is +100.00 on Savings (a debit: money entering) against
        -100.00 on transit.  Each entry sums to zero; transit nets 0.00 across
        the pair; Checking and Savings carry exactly the -100.00 / +100.00 the
        one-entry shape gave them.  Both ledgers are Asset class, but the
        builder never branches on class -- the sign follows direction.  Also
        pins the header metadata (the ``transfer_movement`` source kind, the
        link to the side's covering movement and NO ``transfer_id``, owner,
        scenario, period, the movement's own description) and the per-leg
        ``transfer`` posting kind.  Re-expressed from the one-entry shape when
        it moved; the per-account figures are unchanged.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            # Commit-5 wiring: settling through the transfer service already
            # auto-posted both entries; read them back (a re-sync would no-op).
            by_side = _side_entries(transfer.id)
            assert set(by_side) == {checking.id, savings.id}
            [from_entry] = by_side[checking.id]
            [to_entry] = by_side[savings.id]

            # Header metadata, each side linking ITS covering movement.
            for entry, account in ((from_entry, checking), (to_entry, savings)):
                movement = _covering_movement_of_side(transfer.id, account.id)
                assert entry.transfer_id is None
                assert entry.transaction_id is None
                assert entry.transaction_entry_id == movement.id
                assert entry.user_id == seed_user["user"].id
                assert entry.scenario_id == _scenario_id(seed_user)
                assert entry.pay_period_id == seed_user["bootstrap_period"].id
                assert entry.source_kind_id == ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER_MOVEMENT,
                )
                assert entry.description == movement.description
                # entry_date is a concrete civil date -- the movement's own
                # recorded day, materialized.
                assert entry.entry_date == movement.settled_on
                assert isinstance(entry.entry_date, date)
                # Every leg carries the transfer posting kind.
                kinds = {
                    leg.posting_kind_id
                    for leg in _db.session.query(Posting)
                    .filter_by(journal_entry_id=entry.id)
                    .all()
                }
                assert kinds == {
                    ref_cache.posting_kind_id(PostingKindEnum.TRANSFER),
                }
            assert from_entry.description == "Transfer to Posting Savings"
            assert to_entry.description == "Transfer from Checking"
            # Legs: -100 from Checking against +100 transit; +100 to Savings
            # against -100 transit; each entry sums to zero.
            from_legs = _legs_by_ledger(from_entry.id)
            assert from_legs == {
                checking_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            to_legs = _legs_by_ledger(to_entry.id)
            assert to_legs == {
                savings_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            assert sum(from_legs.values()) == Decimal("0.00")
            assert sum(to_legs.values()) == Decimal("0.00")
            # Transit nets to zero across the pair.
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            # Exactly two entries for the transfer.
            assert len(_entries_for_transfer(transfer.id)) == 2

    def test_asset_to_liability_signs(self, app, db, seed_user):
        """Checking -> Mortgage $250 posts -250 / +250 (pay-down), via transit.

        Arithmetic (plan Section 1, second worked example): paying down a
        liability is still from=-amount / to=+amount.  -250.00 on the Asset
        Checking ledger against +250.00 transit, +250.00 on the Liability
        Mortgage ledger against -250.00 transit; each entry sums to zero and
        transit nets to zero -- the sign rule is class-independent.
        Re-expressed for the pair at plan step ``balance:X-bi-6-3``.
        """
        with app.app_context():
            mortgage = create_account_of_type(
                seed_user, _db.session, "Mortgage", "Posting Mortgage",
            )
            _db.session.commit()
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], mortgage,
                seed_user["bootstrap_period"], amount=Decimal("250.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            mortgage_ledger = _ledger_id(mortgage)
            transit_ledger = _transit_ledger_id(seed_user)

            # Commit-5 wiring: the mortgage pay-down auto-posted on settle as
            # two per-movement entries (plan step ``balance:X-bi-6-3``); read
            # them back by side (a re-sync would no-op).
            by_side = _side_entries(transfer.id)
            [from_entry] = by_side[seed_user["account"].id]
            [to_entry] = by_side[mortgage.id]
            assert _legs_by_ledger(from_entry.id) == {
                checking_ledger: Decimal("-250.00"),
                transit_ledger: Decimal("250.00"),
            }
            assert _legs_by_ledger(to_entry.id) == {
                mortgage_ledger: Decimal("250.00"),
                transit_ledger: Decimal("-250.00"),
            }
            assert _ledger_total(transit_ledger) == Decimal("0.00")

    def test_settle_uses_effective_amount_not_transfer_amount(
        self, app, db, seed_user, savings,
    ):
        """A settled shadow's RECORD overrides the transfer amount.

        The transfer's nominal amount is $100, but the settled record is
        $97.50 (each side's covering movement carries it), so each side's
        leg is $97.50 -- the value the balance calculator and the oracle use.
        The postings must be -97.50 / +97.50 on the real accounts, NOT -100 /
        +100 (the plan Section 5 prose said ``xfer.amount``; the correct,
        oracle-reconciling value is the record, matching the Commit-3
        backfill).  Re-expressed for the pair at plan step
        ``balance:X-bi-6-3``.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_amount=Decimal("97.50"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(seed_user["account"])
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            # Commit-5 wiring: the divergent settled record auto-posted as two
            # per-movement entries (plan step ``balance:X-bi-6-3``); read them
            # back by side (a re-sync would no-op).
            by_side = _side_entries(transfer.id)
            [from_entry] = by_side[seed_user["account"].id]
            [to_entry] = by_side[savings.id]
            assert _legs_by_ledger(from_entry.id) == {
                checking_ledger: Decimal("-97.50"),
                transit_ledger: Decimal("97.50"),
            }
            assert _legs_by_ledger(to_entry.id) == {
                savings_ledger: Decimal("97.50"),
                transit_ledger: Decimal("-97.50"),
            }


#: A settle day in the PAST of this suite's frozen today (2026-03-20, set by
#: ``tests/test_services/conftest.py``), and inside the seeded period range.
#: Ruling **R-EJ** refuses a settle dated ahead of the clock, so a fixture that
#: means "this money moved" has to name a day that has happened.
_A_RECORDED_SETTLE_DAY = date(2026, 3, 9)


class TestSyncSettleEntryDate:
    """``entry_date`` is the shadow's recorded settle DAY."""

    def test_entry_date_is_the_rows_recorded_settle_day(
        self, app, db, seed_user, savings,
    ):
        """A settled transfer's postings are dated by its recorded settle day.

        The entry date is the shadow's stored ``settled_on``, unmodified: no
        conversion, no fallback.

        **The rule it grades has moved twice.**  Ruling R-DH (b) inverted it in
        2026-07-31: ``entry_date`` had been ``paid_at``'s UTC civil date and
        became the USER's, together with the cash fold and the loan fold,
        because an entry date is compared against and bucketed by plain ``DATE``
        columns that mean the user's civil days -- and the three writers had to
        move as one, since moving any alone would put a transfer's two legs on
        different days.  Plan step X-f1 then deleted the derivation entirely:
        the day is stored, so there is no zone left to get wrong HERE.  The zone
        rule now lives at the write door and is pinned there
        (``test_status_seam.py``); what this case still owns is that the writer
        reads the recorded day rather than re-deriving one.

        The day is a PAST one relative to this suite's frozen today, and since
        ruling **R-EJ** it has to be: a settled row asserts that money has
        already moved, so the write door refuses a settle dated ahead of the
        clock.  It was ``2026-05-09`` against a today of ``2026-03-20`` -- two
        months in its own future, which nothing had ever refused.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=_A_RECORDED_SETTLE_DAY,
            )
            _db.session.commit()
            # Commit-5 wiring: the settle auto-posted both sides; read them
            # back.  Each entry is dated by ITS movement's recorded day (plan
            # step ``balance:X-bi-6-3``), which the pair applier mirrors onto
            # both sides until ``X-bi-6-4`` lets them part.
            entries = _entries_for_transfer(transfer.id)
            assert len(entries) == 2
            # The recorded day, verbatim -- no conversion, no fallback.
            assert {entry.entry_date for entry in entries} == {
                _A_RECORDED_SETTLE_DAY,
            }

    def test_a_settled_transfer_cannot_be_left_without_a_day(
        self, app, db, seed_user, savings,
    ):
        """Clearing a settled transfer's day is REFUSED, not defaulted.

        **This test asserted the fallback until plan step X-f1**: with no
        ``paid_at`` recorded, ``entry_date`` -- which is NOT NULL -- took the pay
        period's ``start_date``.  That was a guess the reader could not see, and
        the day is a stored fact now, so there is nothing to fall back TO.  A
        transfer edit that would leave a settled pair undated is refused at the
        service, which is what keeps ``entry_date`` derivable at all.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            with pytest.raises(ValidationError) as exc:
                create_settled_transfer(
                    seed_user, _db.session, seed_user["account"], savings,
                    period, amount=Decimal("100.00"), settled_on=None,
                )
            assert "cannot be cleared" in str(exc.value)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestSyncIdempotency:
    """A repeat sync at the same target writes nothing."""

    def test_repeat_settle_is_noop(self, app, db, seed_user, savings):
        """Re-syncing an already-auto-posted settle returns [], no 2nd entry.

        Arithmetic: ``create_settled_transfer`` auto-posted +100 to the Savings
        ledger (Commit-5 wiring), so both manual re-syncs see current 100 ==
        target 100, delta 0, and write nothing.  This is the double-mark-done
        guard -- a repeated settle never double-posts.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            # Both manual re-syncs are no-ops: create_settled_transfer already
            # auto-posted the two per-movement entries, so current == target
            # and delta == 0 on each (the door reads each movement's own
            # state since plan step ``balance:X-bi-6-3``; there is no flag).
            first = posting_service.sync_transfer_postings(transfer)
            second = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert first == []
            assert second == []
            assert len(_entries_for_transfer(transfer.id)) == 2

    def test_cancel_with_nothing_posted_is_noop(
        self, app, db, seed_user, savings,
    ):
        """A sync of a never-posted, Projected transfer writes nothing.

        Arithmetic: current 0, target 0 (its movements are un-dated, so
        nothing posts), delta 0 -> no entry.  This is the projected ->
        cancelled path (a transfer cancelled before it ever settled has no
        ledger effect to reverse).  It passed ``settled=False`` until plan
        step ``balance:X-bi-6-3`` deleted the flag.
        """
        with app.app_context():
            transfer = transfer_service.create_transfer(
                transfer_service.TransferSpec(
                    user_id=seed_user["user"].id,
                    from_account_id=seed_user["account"].id,
                    to_account_id=savings.id,
                    pay_period_id=seed_user["bootstrap_period"].id,
                    scenario_id=_scenario_id(seed_user),
                    amount_ownership=AmountOwnership.own(Decimal("100.00")),
                    status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                    category_id=None,
                ),
            )
            _db.session.commit()

            result = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert result == []
            assert _entries_for_transfer(transfer.id) == []


# ---------------------------------------------------------------------------
# Reversal: negate exactly what was posted
# ---------------------------------------------------------------------------


class TestSyncReversal:
    """A reversal reads the posted amount back from the ledger."""

    def test_reverse_negates_posted_amount_not_transfer_amount(
        self, app, db, seed_user, savings,
    ):
        """A teardown posts the negation of what is posted, ignoring xfer.amount.

        Arithmetic: the settle posts +100 to the Savings ledger and -100 to
        Checking (each against transit).  Then the transfer amount is mutated
        to 999 (the value a naive ``target = xfer.amount`` reversal would
        use).  The reversal instead reads each side's posted net back and
        posts the delta to reach 0: -100 on Savings against +100 transit,
        +100 on Checking against -100 transit.  The Savings ledger nets back
        to its $100.00 opening; the reversal legs are 100, NOT 999.

        The door is the TEARDOWN twin (plan step ``balance:X-bi-6-3``, ruling
        **R-BAL101**): ``sync_transfer_postings`` reads each movement's own
        state and would find two live, dated movements and leave them posted;
        reversing a still-settled pair is what the delete door does, through
        ``reverse_transfer_postings_before_delete``.  It called the sync with
        ``settled=False`` until that step deleted the flag.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            # Mutate the transfer amount to a value a naive reversal would
            # wrongly use; the reversal must still negate the posted 100.
            state_own_amount(transfer, Decimal("999.00"))
            _db.session.flush()

            posting_service.reverse_transfer_postings_before_delete(transfer)
            _db.session.commit()

            by_side = _side_entries(transfer.id)
            [_, from_reversal] = by_side[checking.id]
            [_, to_reversal] = by_side[savings.id]
            assert _legs_by_ledger(to_reversal.id) == {
                savings_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            assert _legs_by_ledger(from_reversal.id) == {
                checking_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            # Settled then reversed: the Savings ledger nets back to its
            # $100.00 opening (the absolute Step-5 semantics).
            assert posting_service.account_posting_total(
                savings.id, _scenario_id(seed_user),
            ) == Decimal("100.00")
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            # Four entries survive (append-only correction, never an edit).
            assert len(_entries_for_transfer(transfer.id)) == 4

    def test_revert_edit_amount_resettle_posts_new_amount(
        self, app, db, seed_user, savings,
    ):
        """A revert -> edit-amount -> re-settle posts the new amount.

        Arithmetic: settle $100 (+100 on Savings), revert (-100, net 0), edit
        the amount to $150, re-settle (current 0 -> target 150, delta +150).
        The transfer's entries net to +150 on Savings -- the new settled
        record -- so the Savings total is its $100.00 opening + 150 =
        250.00.  Six entries since plan step ``balance:X-bi-6-3``: two per
        act, one per side.  The revert UN-DATES both movements (ruling
        **R-BAL61**), which is what the door's reconcile reads to reverse;
        the manual syncs between the doors are no-ops that prove the doors
        already left the ledger at target (they were told ``settled=`` until
        that step deleted the flag).
        """
        with app.app_context():
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()

            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            # Revert to Projected: the door un-dates the movements and its
            # reconcile reverses the pair; a manual sync then finds nothing.
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            # Edit the amount while Projected, then re-settle and re-post.
            transfer_service.update_transfer(
                transfer.id, user_id, amount_ownership=AmountOwnership.own(Decimal("150.00")),
            )
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.DONE),
                settle_day=an_entered_day(display_today()),
            )
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            # 100 (opening) + 100 (settle) - 100 (reverse) + 150 (re-settle)
            # = +250.
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("250.00")
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("150.00")
            assert _ledger_total(_transit_ledger_id(seed_user)) == Decimal("0.00")
            # Six entries: settle, reverse, re-settle -- two sides each.
            assert len(_entries_for_transfer(transfer.id)) == 6


# ---------------------------------------------------------------------------
# Reconciliation helpers
# ---------------------------------------------------------------------------


class TestReconciliationHelpers:
    """The per-account posting total equals the settled-shadow effect."""

    def test_helpers_match_after_settle_both_accounts(
        self, app, db, seed_user, savings,
    ):
        """After a $100 settle, both helpers agree on both accounts.

        Arithmetic: Savings (the to-account, income shadow) is +100; Checking
        (the from-account, expense shadow) is -100.
        ``settled_transfer_effect`` (sum over settled shadows) reports
        exactly those; ``account_posting_total`` (sum over postings) reports
        each on top of the account's opening -- Savings 100 + 100 = 200.00,
        Checking 1000 - 100 = 900.00 -- so the two independent tables agree
        on the transfer's contribution.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            checking = seed_user["account"]
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("900.00")
            assert posting_service.settled_transfer_effect(
                checking.id, scenario_id,
            ) == Decimal("-100.00")

    def test_helpers_net_to_zero_after_reverse(
        self, app, db, seed_user, savings,
    ):
        """A settled-then-reverted transfer reconciles at zero on both sides.

        Arithmetic: the transfer's postings net to zero (+100 then -100); the
        reverted income shadow is no longer settled, so the settled-shadow
        effect drops it to zero too.  Each account's posting total lands back
        exactly on its opening (Savings 100.00, Checking 1000.00) -- the
        transfer contributes nothing.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            for account, opening in (
                (savings, Decimal("100.00")),
                (seed_user["account"], Decimal("1000.00")),
            ):
                assert posting_service.account_posting_total(
                    account.id, scenario_id,
                ) == opening
                assert posting_service.settled_transfer_effect(
                    account.id, scenario_id,
                ) == Decimal("0.00")

    def test_settled_effect_uses_effective_amount(
        self, app, db, seed_user, savings,
    ):
        """The settled-shadow effect honours a divergent ``actual_amount``.

        Arithmetic: nominal $100, settled actual $97.50, so the effect
        reader reports +97.50 and the posting total carries it on top of
        the Savings opening: 100 + 97.50 = 197.50.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_amount=Decimal("97.50"),
            )
            _db.session.commit()
            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            assert posting_service.settled_transfer_effect(
                savings.id, scenario_id,
            ) == Decimal("97.50")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("197.50")


# ---------------------------------------------------------------------------
# The deploy resync re-books the legacy one-entry shape (R-BAL98)
# ---------------------------------------------------------------------------


def _real_account_nets_by_day(*ledger_ids):
    """Return ``{(ledger_account_id, entry_date): net}`` over the given ledgers.

    The grade ruling **R-BAL98** names: a re-book that changes the ledger's
    SHAPE must leave every real account's net per day exactly as it was.  An
    independent re-grouping of the legs by the entry's date and the leg's
    ledger account, zero-net keys dropped, never the service's own readers.
    """
    rows = (
        _db.session.query(
            Posting.ledger_account_id, JournalEntry.entry_date,
            _db.func.sum(Posting.amount),
        )
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(Posting.ledger_account_id.in_(ledger_ids))
        .group_by(Posting.ledger_account_id, JournalEntry.entry_date)
        .all()
    )
    return {
        (ledger_id, day): net for ledger_id, day, net in rows if net != 0
    }


class TestTheDeployResyncReBooksTheLegacyShape:
    """``resync_all_cash_postings`` moves a one-entry transfer to shape C once.

    Ruling **R-BAL98** (plan step ``balance:X-bi-6-3``): every production
    transfer booked as ONE entry (19 on the 2026-09-22 17:06 dump) is
    re-booked by the deploy's first hook through the go-forward
    ``sync_transfer_postings``' re-book half -- the legacy
    ``transfer`` source reversed at its own date, the two per-movement
    entries posted against transit -- and graded by three equalities: every
    real account's net per day byte-identical, transit netting zero per
    transfer, the trial balance closing.  This is the CI half of that grade
    (the clone rehearsal is the other): a settled transfer is forged into the
    legacy shape exactly as production holds it, and the real resync runs.
    """

    def test_one_pass_re_books_and_a_second_pass_writes_nothing(
        self, app, db, seed_user, savings,
    ):
        """One resync: legacy reversed, both sides posted, nets unchanged, then 0.

        Arithmetic: a $100 Checking -> Savings transfer settled on day D.
        Forged legacy state: ONE ``transfer``-source entry dated D,
        ``{Checking -100, Savings +100}``, and no movement-linked entry --
        production's shape before this step.  Per (real account, day) before:
        Checking D -100.00, Savings D +100.00 (each on its opening, which
        sits on an earlier day).  The resync reports (0, 1): the legacy
        entry reversed at D (its source nets zero), two per-movement entries
        posted at D -- {Checking -100, transit +100} and {Savings +100,
        transit -100} -- so per (real account, day) after == before, transit
        nets 0.00, the trial balance is 0.00, and the family holds four
        entries.  A second resync reports (0, 0) and adds nothing.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on

            # Forge the legacy shape: drop the go-forward per-movement entries
            # (raw SQL, as every legacy forge in the suite: the ORM's
            # append-only guard is about the app's own writes) and book the
            # one entry the pre-6-3 writer wrote, dated at the settle day.
            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries WHERE id IN ("
                "  SELECT je.id FROM budget.journal_entries je"
                "  JOIN budget.transaction_entries te"
                "    ON te.id = je.transaction_entry_id"
                "  JOIN budget.transactions sh ON sh.id = te.transaction_id"
                "  WHERE sh.transfer_id = :t)"
            ), {"t": transfer.id})
            legacy = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=_scenario_id(seed_user),
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=day,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER,
                ),
                transfer_id=transfer.id,
                description="Transfer: Checking to Posting Savings",
            )
            transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            _emit_balanced_entry(legacy, [
                _PostingLeg(checking_ledger, Decimal("-100.00"), transfer_kind),
                _PostingLeg(savings_ledger, Decimal("100.00"), transfer_kind),
            ])
            _db.session.commit()
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            before = _real_account_nets_by_day(checking_ledger, savings_ledger)
            assert before[(checking_ledger, day)] == Decimal("-100.00")
            assert before[(savings_ledger, day)] == Decimal("100.00")

            assert posting_service.resync_all_cash_postings() == (0, 1)
            _db.session.commit()

            # The legacy source nets to zero at its own date.
            legacy_net = (
                _db.session.query(_db.func.coalesce(_db.func.sum(Posting.amount), 0))
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(
                    JournalEntry.transfer_id == transfer.id,
                    JournalEntry.entry_date == day,
                    Posting.ledger_account_id == savings_ledger,
                )
                .scalar()
            )
            assert legacy_net == 0
            # Both sides posted against transit, at the same day.
            by_side = _side_entries(transfer.id)
            [from_entry] = by_side[checking.id]
            [to_entry] = by_side[savings.id]
            assert _legs_by_ledger(from_entry.id) == {
                checking_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            assert _legs_by_ledger(to_entry.id) == {
                savings_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            assert {from_entry.entry_date, to_entry.entry_date} == {day}
            # The three equalities.
            assert _real_account_nets_by_day(
                checking_ledger, savings_ledger,
            ) == before
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            assert _db.session.query(
                _db.func.coalesce(_db.func.sum(Posting.amount), 0)
            ).scalar() == 0
            assert len(_entries_for_transfer(transfer.id)) == 4

            assert posting_service.resync_all_cash_postings() == (0, 0)
            assert len(_entries_for_transfer(transfer.id)) == 4

    def test_a_refused_transfer_is_skipped_whole(
        self, app, db, seed_user, savings,
    ):
        """A transfer one side of which cannot post leaves NOTHING half-booked.

        The leaf's adversarial review, finding 1: the pair's door writes in
        sequence (the legacy reversal, then each side), so a refusal on the
        second side would leave the first side and the reversal committed by
        the batch -- Checking debited into transit, nothing arriving, a trial
        balance that still closes.  The resync runs each transfer under a
        SAVEPOINT and rolls a refused one back whole.  Arithmetic: the legacy
        shape forged as in the case above (the entry intact, both legs), then
        the TO-side movement re-pointed onto an account whose ledger pairing
        has been removed (an impossible state, the fail-loud fixture's) -- so
        the door reverses the legacy entry, posts the from side, and only
        then is refused.  The resync skips the transfer whole; the legacy
        entry still stands un-reversed, no per-movement entry exists, transit
        is untouched.  Mutation: without the savepoint the reversal and the
        from-side entry survive the refusal and the legacy entry is netted
        away -- so since leaf 3b the resync no longer refuses and the case
        fails at the raise (DID NOT RAISE, observed); a savepoint rolling back
        only PART of the door's writes is what the assertions after the raise
        still catch.

        **Re-expressed under rule 5 at leaf 3b, the developer confirming it
        2026-09-22** (ruling **R-BAL104**): this skipped transfer keeps a
        NONZERO legacy posting the account walk cannot see, so the resync no
        longer returns (0, 0) -- it REFUSES after its loop, naming the
        transfer as holder and as skipped, and the deploy's commit never
        runs.  Every other assertion is unchanged and runs in the caller's
        transaction right after the raise.  A skip holding no legacy net
        still returns (the next case); a legacy net netting zero in total but
        not per day still refuses (the one after).
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries WHERE id IN ("
                "  SELECT je.id FROM budget.journal_entries je"
                "  JOIN budget.transaction_entries te"
                "    ON te.id = je.transaction_entry_id"
                "  JOIN budget.transactions sh ON sh.id = te.transaction_id"
                "  WHERE sh.transfer_id = :t)"
            ), {"t": transfer.id})
            legacy = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=_scenario_id(seed_user),
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=day,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER,
                ),
                transfer_id=transfer.id,
                description="Transfer: Checking to Posting Savings",
            )
            transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            _emit_balanced_entry(legacy, [
                _PostingLeg(checking_ledger, Decimal("-100.00"), transfer_kind),
                _PostingLeg(savings_ledger, Decimal("100.00"), transfer_kind),
            ])
            _db.session.commit()
            # An unpaired account for the to-side movement to sit on: created
            # (which pairs it), its pairing removed (no legs on it), and the
            # to-side movement re-pointed onto it by raw SQL -- the legacy
            # entry keeps both legs, so the refusal comes from the second
            # side's missing pairing and nowhere earlier.
            unpaired = create_account_of_type(
                seed_user, _db.session, "Savings", "Unpaired Savings",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": unpaired.id})
            _db.session.execute(_db.text(
                "UPDATE budget.transaction_entries SET account_id = :u "
                "WHERE id = :m"
            ), {
                "u": unpaired.id,
                "m": _covering_movement_of_side(transfer.id, savings.id).id,
            })
            _db.session.commit()

            with pytest.raises(
                PostingError,
                match=(
                    rf"transfer\(s\) \[{transfer.id}\] still hold a nonzero "
                    rf"legacy .*\(skipped this pass: \[{transfer.id}\]\)"
                ),
            ):
                posting_service.resync_all_cash_postings()

            # Nothing half-booked: the legacy SOURCE still nets its whole
            # effect (un-reversed -- the entry's own legs are append-only, so
            # the net is what grades it), no per-movement entry exists,
            # transit is untouched.
            legacy_nets = {
                ledger_id: net
                for ledger_id, net in _db.session.query(
                    Posting.ledger_account_id, _db.func.sum(Posting.amount),
                )
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(JournalEntry.transfer_id == transfer.id)
                .group_by(Posting.ledger_account_id)
                .all()
            }
            assert legacy_nets == {
                checking_ledger: Decimal("-100.00"),
                savings_ledger: Decimal("100.00"),
            }
            assert _db.session.query(JournalEntry).filter(
                JournalEntry.source_kind_id == ref_cache.posting_source_id(
                    PostingSourceEnum.TRANSFER_MOVEMENT,
                ),
            ).count() == 0
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            assert _ledger_total(checking_ledger) == Decimal("900.00")

    def test_a_refused_transfer_holding_no_legacy_net_is_still_skipped(
        self, app, db, seed_user, savings, caplog,
    ):
        """A skip with NO legacy net still returns, skipped whole and logged.

        Ruling **R-BAL104** refuses the resync only for a transfer whose
        LEGACY source still nets nonzero; every other family keeps the
        2026-08-17 ruling -- one that cannot post is skipped and reported,
        never allowed to make the deploy unbootable -- and this is the case
        where the per-transfer SAVEPOINT still decides what the deploy
        commits.  Arithmetic: a $100.00 Checking -> Savings transfer settled
        go-forward (two movement entries, no legacy entry); the entry of the
        movement the door reaches FIRST (the lower id) deleted by raw SQL, so
        the door has a re-post to write, and the SECOND movement re-pointed
        onto an account whose ledger pairing is removed, so the door is
        refused after that write.  The resync returns (0, 0) and logs the
        skip naming the transfer and why (the door's own message, naming the
        account whose pairing is missing); the family still holds exactly
        the second movement's original entry (the first's re-post rolled
        back).
        Mutations: a refusal on ANY skip raises here; without the savepoint
        the first movement's re-post survives the refusal.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            first_id, second_id = sorted(
                _covering_movement_of_side(transfer.id, account.id).id
                for account in (checking, savings)
            )
            [second_entry_id] = [
                entry.id for entry in _entries_for_transfer(transfer.id)
                if entry.transaction_entry_id == second_id
            ]
            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries "
                "WHERE transaction_entry_id = :m"
            ), {"m": first_id})
            unpaired = create_account_of_type(
                seed_user, _db.session, "Savings", "Unpaired Savings",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": unpaired.id})
            _db.session.execute(_db.text(
                "UPDATE budget.transaction_entries SET account_id = :u "
                "WHERE id = :m"
            ), {"u": unpaired.id, "m": second_id})
            _db.session.commit()
            _db.session.expire_all()

            with caplog.at_level(
                logging.WARNING, logger="app.services.posting_service",
            ):
                assert posting_service.resync_all_cash_postings() == (0, 0)

            assert (
                f"skipped 1 transfer(s) whose family could not be posted: "
                f"[{transfer.id}].  Why: transfer {transfer.id}: No ledger "
                f"account is linked to account {unpaired.id};"
            ) in caplog.text
            assert [
                entry.id for entry in _entries_for_transfer(transfer.id)
            ] == [second_entry_id]

    def test_a_legacy_net_zero_in_total_but_not_per_day_still_refuses(
        self, app, db, seed_user, savings,
    ):
        """A skipped transfer's legacy residue refuses PER (period, day), not in total.

        Ruling **R-BAL104** refuses while a transfer holds a nonzero legacy
        posting "on any (period, day)", and the walk that cannot see it moves
        each DAY's balance: a settle / reversal pair straddling two days (the
        E1a review's H2 residue) nets zero in total and is still money on
        each day.  Arithmetic: a $100.00 Checking -> Savings transfer settled
        on D, its go-forward movement entries replaced by a straddling legacy
        pair -- {Checking -100, Savings +100} at D and {Checking +100,
        Savings -100} at D-8, $0.00 in total -- and its to-side movement
        re-pointed onto an unpaired account so the resync skips it.  The
        resync refuses naming the transfer and why it was skipped (the
        unpaired account); the pair still stands on both days (Checking
        -100.00 at D and +100.00 at D-8, Savings the opposite).  Mutation: a
        refusal that sums each transfer's legacy legs across days reads $0.00
        here and lets the resync finish.
        """
        with app.app_context():
            checking = seed_user["account"]
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            earlier = day - timedelta(days=8)
            _db.session.execute(_db.text(
                "DELETE FROM budget.journal_entries "
                "WHERE transaction_entry_id IN ("
                "  SELECT te.id FROM budget.transaction_entries te"
                "  JOIN budget.transactions sh ON sh.id = te.transaction_id"
                "  WHERE sh.transfer_id = :t)"
            ), {"t": transfer.id})
            transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            for entry_day, checking_leg in (
                (day, Decimal("-100.00")),
                (earlier, Decimal("100.00")),
            ):
                _emit_balanced_entry(
                    JournalEntry(
                        user_id=user_id,
                        scenario_id=_scenario_id(seed_user),
                        pay_period_id=seed_user["bootstrap_period"].id,
                        entry_date=entry_day,
                        source_kind_id=ref_cache.posting_source_id(
                            PostingSourceEnum.TRANSFER,
                        ),
                        transfer_id=transfer.id,
                        description="Transfer: Checking to Posting Savings",
                    ),
                    [
                        _PostingLeg(checking_ledger, checking_leg, transfer_kind),
                        _PostingLeg(savings_ledger, -checking_leg, transfer_kind),
                    ],
                )
            unpaired = create_account_of_type(
                seed_user, _db.session, "Savings", "Unpaired Savings",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": unpaired.id})
            _db.session.execute(_db.text(
                "UPDATE budget.transaction_entries SET account_id = :u "
                "WHERE id = :m"
            ), {
                "u": unpaired.id,
                "m": _covering_movement_of_side(transfer.id, savings.id).id,
            })
            _db.session.commit()

            with pytest.raises(
                PostingError,
                match=(
                    rf"transfer\(s\) \[{transfer.id}\] still hold a nonzero "
                    rf"legacy .*\(skipped this pass: \[{transfer.id}\]\)\.  "
                    rf"Why: transfer {transfer.id}: No ledger account is "
                    rf"linked to account {unpaired.id};"
                ),
            ):
                posting_service.resync_all_cash_postings()

            legacy_by_day = {
                (ledger_id, entry_day): net
                for ledger_id, entry_day, net in _db.session.query(
                    Posting.ledger_account_id, JournalEntry.entry_date,
                    _db.func.sum(Posting.amount),
                )
                .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
                .filter(JournalEntry.transfer_id == transfer.id)
                .group_by(Posting.ledger_account_id, JournalEntry.entry_date)
                .all()
            }
            assert legacy_by_day == {
                (checking_ledger, day): Decimal("-100.00"),
                (savings_ledger, day): Decimal("100.00"),
                (checking_ledger, earlier): Decimal("100.00"),
                (savings_ledger, earlier): Decimal("-100.00"),
            }

    def test_a_reverted_transfers_legacy_residue_heals_too(
        self, app, db, seed_user, savings,
    ):
        """Cross-date legacy residue on a REVERTED transfer nets to zero per date.

        The leaf's adversarial review, finding 5: the loan lineage probe no
        longer reads the legacy source, and a reverted transfer is neither
        settled nor holds a dated movement, so the resync's transfer arm must
        reach it by its LEGACY ENTRIES or the E1a review's H2 residue (a
        settle / reversal pair straddling two dates, net zero in total and
        not per date) would be healed by nothing.  Arithmetic: settle then
        revert (the movement entries net zero), then forge the residue: a
        legacy entry at D {Checking -100, Savings +100} and its reversal at
        D-8 {Checking +100, Savings -100}.  Per (real account, day) before
        the forge is the clean state; the resync reports (0, 1) and restores
        it exactly -- both legacy keys net zero -- and a second pass is
        (0, 0).
        """
        with app.app_context():
            checking = seed_user["account"]
            user_id = seed_user["user"].id
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            transfer_service.update_transfer(
                transfer.id, user_id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            clean = _real_account_nets_by_day(checking_ledger, savings_ledger)

            transfer_kind = ref_cache.posting_kind_id(PostingKindEnum.TRANSFER)
            for entry_day, checking_leg in (
                (day, Decimal("-100.00")),
                (day - timedelta(days=8), Decimal("100.00")),
            ):
                residue = JournalEntry(
                    user_id=user_id,
                    scenario_id=_scenario_id(seed_user),
                    pay_period_id=seed_user["bootstrap_period"].id,
                    entry_date=entry_day,
                    source_kind_id=ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER,
                    ),
                    transfer_id=transfer.id,
                    description="Transfer: Checking to Posting Savings",
                )
                _emit_balanced_entry(residue, [
                    _PostingLeg(checking_ledger, checking_leg, transfer_kind),
                    _PostingLeg(savings_ledger, -checking_leg, transfer_kind),
                ])
            _db.session.commit()
            assert _real_account_nets_by_day(
                checking_ledger, savings_ledger,
            ) != clean

            assert posting_service.resync_all_cash_postings() == (0, 1)
            _db.session.commit()

            assert _real_account_nets_by_day(
                checking_ledger, savings_ledger,
            ) == clean
            assert posting_service.resync_all_cash_postings() == (0, 0)

    def test_the_anchors_are_re_checked_once_after_every_source_is_re_booked(
        self, app, db, seed_user, savings,
    ):
        """A batch re-book under a true-up writes NO anchor correction.

        Ruling **R-BAL103** (developer, 2026-09-22): the deploy resync
        re-books EVERY source first, then re-checks each touched account's
        anchor corrections ONCE.  Arithmetic: Checking opens at $1,000.00; a
        $30.00 cash row and two Checking -> Savings transfers ($100.00 and
        $50.00, distinct amounts for the ad-hoc dedupe key) settle on day D,
        so Checking's ledger reads $820.00, and Checking is trued up to
        $820.00 as of D -- an assertion covering all three, its correction
        $0.00.  All three are then forged into the legacy shape (the row's
        movement entry into one ``transaction``-source entry, each
        transfer's two into one ``transfer``-source entry; every real leg and
        day unchanged).  The resync reports (1, 2); per (real account, day)
        the nets are unchanged, transit nets 0.00, and NOT ONE anchor
        correction entry is written.  Mutations, each observed firing (+2
        entries): the transfer arm re-checking inside its loop -- the first
        transfer's walk cannot see the $50.00 legacy entry (the walk reads
        no ``transfer_id``-linked entry) and books a -$50.00 true-up the
        second's re-check reverses; the row arm re-checking inside its loop
        -- the row's walk runs while both transfers are legacy and books a
        -$150.00 true-up the one re-check reverses.
        """
        with app.app_context():
            checking = seed_user["account"]
            period = seed_user["bootstrap_period"]
            transfers = [
                create_settled_transfer(
                    seed_user, _db.session, checking, savings, period,
                    amount=amount,
                )
                for amount in (Decimal("100.00"), Decimal("50.00"))
            ]
            _db.session.commit()
            day = _covering_movement_of_side(
                transfers[0].id, checking.id,
            ).settled_on
            assert _covering_movement_of_side(
                transfers[1].id, checking.id,
            ).settled_on == day
            row = create_settled_cash_transaction(
                seed_user, _db.session, period, Decimal("30.00"),
                account=checking, name="legacy row", settled_on=day,
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            assert _ledger_total(checking_ledger) == Decimal("820.00")
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("820.00"),
                observed_on=day,
            )

            for xfer in transfers:
                _fold_into_legacy_entry(
                    _entries_for_transfer(xfer.id),
                    source=PostingSourceEnum.TRANSFER,
                    description="Transfer: Checking to Posting Savings",
                    transfer_id=xfer.id,
                )
            _fold_into_legacy_entry(
                _entries_for_transaction(row.id),
                source=PostingSourceEnum.TRANSACTION,
                description=row.name, transaction_id=row.id,
            )
            _db.session.commit()
            before = _real_account_nets_by_day(checking_ledger, savings_ledger)
            corrections = _anchor_correction_entry_count()
            assert before[(checking_ledger, day)] == Decimal("-180.00")
            assert _db.session.query(JournalEntry).filter(
                JournalEntry.source_kind_id.in_([
                    ref_cache.posting_source_id(PostingSourceEnum.PURCHASE),
                    ref_cache.posting_source_id(
                        PostingSourceEnum.TRANSFER_MOVEMENT,
                    ),
                ]),
            ).count() == 0

            assert posting_service.resync_all_cash_postings() == (1, 2)
            _db.session.commit()

            assert _anchor_correction_entry_count() == corrections
            assert _real_account_nets_by_day(
                checking_ledger, savings_ledger,
            ) == before
            assert _ledger_total(_transit_ledger_id(seed_user)) == Decimal("0.00")
            assert _ledger_total(checking_ledger) == Decimal("820.00")
            assert posting_service.resync_all_cash_postings() == (0, 0)

    def test_the_one_re_check_corrects_what_the_transfer_re_book_moved(
        self, app, db, seed_user, savings,
    ):
        """The re-check after the loop WRITES when a re-booked transfer moves a correction.

        The case above proves the re-check writes nothing extra; this one
        proves it runs (ruling **R-BAL103** refused "no re-check in the
        resync").  Arithmetic: Checking opens at $1,000.00; a $100.00
        transfer to Savings settles on D and is forged into the legacy shape,
        which the account walk does not read; Checking is then trued up to
        $900.00 as of D, so the walk books a -$100.00 correction for money it
        cannot see and Checking's ledger reads $800.00.  The resync re-books
        the transfer (the -$100.00 now visible as a movement) and the one
        re-check reverses the correction: exactly ONE new anchor-correction
        entry, Checking +100.00, and the ledger reads $900.00.  Mutation: the
        transfer arm's hold removed -> the ledger stays at $800.00.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            day = _covering_movement_of_side(transfer.id, checking.id).settled_on
            _fold_into_legacy_entry(
                _entries_for_transfer(transfer.id),
                source=PostingSourceEnum.TRANSFER,
                description="Transfer: Checking to Posting Savings",
                transfer_id=transfer.id,
            )
            _db.session.commit()
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("900.00"),
                observed_on=day,
            )
            checking_ledger = _ledger_id(checking)
            assert _ledger_total(checking_ledger) == Decimal("800.00")
            newest = _db.session.query(_db.func.max(JournalEntry.id)).scalar()

            assert posting_service.resync_all_cash_postings() == (0, 1)
            _db.session.commit()

            assert _new_anchor_correction_legs(newest, checking_ledger) == [
                Decimal("100.00"),
            ]
            assert _ledger_total(checking_ledger) == Decimal("900.00")

    def test_the_one_re_check_corrects_what_a_row_re_date_moved(
        self, app, db, seed_user,
    ):
        """The re-check after the loop WRITES when a re-dated row crosses an assertion.

        The row arm's twin of the case above (its hold is separate, so it
        has its own control).  Arithmetic: Checking opens at $1,000.00; a
        $30.00 row settles on S; Checking is trued up to $1,000.00 as of S-1
        (the row after it, correction $0.00).  The row's settle day is then
        moved to S-1 behind the ledger's back (the stale-day forge of the
        R-DH hook test), so the assertion now covers it.  The resync re-dates
        the row -- (1, 0) -- and the one re-check books the correction the
        move owes: exactly ONE new anchor-correction entry, Checking +30.00,
        and the ledger reads the asserted $1,000.00.  Mutation: the row
        arm's hold removed -> the ledger stays at $970.00.
        """
        with app.app_context():
            checking = seed_user["account"]
            row = create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("30.00"), account=checking, name="re-dated row",
            )
            _db.session.commit()
            [movement] = row.entries
            earlier = movement.settled_on - timedelta(days=1)
            anchor_service.apply_anchor_true_up(
                account=checking, new_balance=Decimal("1000.00"),
                observed_on=earlier,
            )
            checking_ledger = _ledger_id(checking)
            assert _ledger_total(checking_ledger) == Decimal("970.00")
            _db.session.execute(
                _db.text(
                    "UPDATE budget.transactions SET settled_on = :day "
                    "WHERE id = :id"
                ),
                {"day": earlier, "id": row.id},
            )
            _db.session.execute(
                _db.text(
                    "UPDATE budget.transaction_entries "
                    "SET settled_on = :day, purchased_on = :day "
                    "WHERE transaction_id = :id"
                ),
                {"day": earlier, "id": row.id},
            )
            _db.session.commit()
            newest = _db.session.query(_db.func.max(JournalEntry.id)).scalar()

            assert posting_service.resync_all_cash_postings() == (1, 0)
            _db.session.commit()

            assert _new_anchor_correction_legs(newest, checking_ledger) == [
                Decimal("30.00"),
            ]
            assert _ledger_total(checking_ledger) == Decimal("1000.00")


def _new_anchor_correction_legs(after_entry_id, ledger_id):
    """Return the *ledger_id* legs of anchor corrections written after an entry."""
    return [
        amount for (amount,) in _db.session.query(Posting.amount)
        .join(JournalEntry, Posting.journal_entry_id == JournalEntry.id)
        .filter(
            JournalEntry.id > after_entry_id,
            Posting.ledger_account_id == ledger_id,
            JournalEntry.source_kind_id.in_([
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
                ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
            ]),
        )
        .order_by(JournalEntry.id)
    ]


def _fold_into_legacy_entry(entries, *, source, description, **link):
    """Replace *entries* by ONE entry of the legacy *source*, legs summed.

    The pre-``X-bi-6-3`` shape of a settled source: its movement entries'
    legs summed per ledger account (a transfer's transit legs net away), the
    entries deleted by raw SQL (the ORM's append-only guard is about the
    app's own writes), and one entry emitted at their shared day and period
    under *source*, linked by *link* (``transfer_id`` or ``transaction_id``).
    """
    [day] = {entry.entry_date for entry in entries}
    [period_id] = {entry.pay_period_id for entry in entries}
    legs: dict = {}
    for leg in _db.session.query(Posting).filter(
        Posting.journal_entry_id.in_([entry.id for entry in entries]),
    ):
        amount, _ = legs.get(leg.ledger_account_id, (Decimal("0.00"), None))
        legs[leg.ledger_account_id] = (amount + leg.amount, leg.posting_kind_id)
    [(user_id, scenario_id)] = {
        (entry.user_id, entry.scenario_id) for entry in entries
    }
    _db.session.execute(
        _db.text("DELETE FROM budget.journal_entries WHERE id = ANY(:ids)"),
        {"ids": [entry.id for entry in entries]},
    )
    _db.session.expire_all()
    _emit_balanced_entry(
        JournalEntry(
            user_id=user_id, scenario_id=scenario_id,
            pay_period_id=period_id, entry_date=day,
            source_kind_id=ref_cache.posting_source_id(source),
            description=description, **link,
        ),
        [
            _PostingLeg(ledger_id, amount, kind)
            for ledger_id, (amount, kind) in sorted(legs.items())
            if amount != 0
        ],
    )


def _anchor_correction_entry_count():
    """Return how many account opening / true-up journal entries exist."""
    return _db.session.query(JournalEntry).filter(
        JournalEntry.source_kind_id.in_([
            ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_OPENING),
            ref_cache.posting_source_id(PostingSourceEnum.ACCOUNT_TRUEUP),
        ]),
    ).count()


# ---------------------------------------------------------------------------
# Fail loud
# ---------------------------------------------------------------------------


class TestFailLoud:
    """Broken invariants raise PostingError rather than posting silently."""

    @pytest.mark.parametrize(
        "linkage",
        [
            {},
            {"transaction_id": 1, "transaction_entry_id": 2},
            {"transfer_id": 1},
        ],
        ids=["no link", "two links", "a transfer link"],
    )
    def test_a_typed_source_names_exactly_one_typed_link(
        self, app, db, seed_user, linkage,
    ):
        """``emit_typed_source_deltas`` refuses any link set but one typed FK.

        Plan step X-bi-3b: a header carrying two links lands in NONE of the
        ledger report's buckets, and a ``transfer_id`` header carries the
        ``transfer`` kind and never a transaction's -- so the refusal fires
        before a target is read, whatever *linkage* the caller spelled.
        """
        with app.app_context():
            txn = create_settled_cash_transaction(
                seed_user, _db.session, seed_user["bootstrap_period"],
                Decimal("10.00"),
            )
            with pytest.raises(ValueError, match="exactly one of"):
                emit_typed_source_deltas(
                    txn, targets={}, source=PostingSourceEnum.TRANSACTION,
                    description="x", log_label="x", **linkage,
                )

    def test_account_posting_total_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``account_posting_total`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.account_posting_total(
                    seed_user["account"].id, None,
                )

    def test_settled_transfer_effect_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``settled_transfer_effect`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.settled_transfer_effect(
                    seed_user["account"].id, None,
                )

    def test_missing_ledger_account_fails_loud(
        self, app, db, seed_user, savings,
    ):
        """A transfer whose account has no ledger pairing raises PostingError.

        Removing the Savings account's linked ledger row (an impossible state
        in production -- every account is paired) makes the sync fail loudly
        rather than post a one-legged or silently-wrong entry.  The row's
        postings go with it (``account_postings.ledger_account_id`` CASCADE),
        so the to-side movement reads as unposted and the door tries to post
        it -- into a pairing that no longer exists.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            # Drop the to-account's ledger pairing via raw SQL.
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": savings.id})
            _db.session.commit()

            # The pair's door reconciles from what is posted, so with the
            # Savings pairing gone the to-side movement's target cannot be
            # built; the refusal fires before anything is written.
            with pytest.raises(PostingError, match="ledger account"):
                posting_service.sync_transfer_postings(transfer)

    def test_a_half_pair_posts_its_half_into_transit(
        self, app, db, seed_user, savings,
    ):
        """A transfer with only its from-side shadow posts that side, in transit.

        **A declared behaviour change of plan step ``balance:X-bi-6-3``
        (rulings R-BAL45, R-BAL101), re-expressed from
        ``test_settle_missing_income_shadow_fails_loud``.**  The one-entry
        writer read the INCOME shadow's record for the pair and REFUSED when
        it was missing; shape C has no pair to read -- each side's movement
        posts on its own, against transit -- so a pair with one side is the
        in-transit state: money left Checking and has not arrived.  Transfer
        Invariant 1 (two shadows) is the transfer service's and the integrity
        checks' to police, not the ledger writer's, which neither refuses nor
        fabricates the missing side.

        Arithmetic: from a ledger the teardown brought to zero (both sides
        posted and reversed: transit 0.00), the income shadow is removed and
        the sync runs: it emits ONE entry, {Checking -100, transit +100}, and
        nothing for the missing side; transit holds +100.00 -- the half in
        flight.  (The to-side's earlier entries survive as a net-zero pair
        whose links the raw delete SET-NULLed; the readers drop unlinked
        residue.)  The leaf's adversarial review found the first cut reading
        back the entry the SETTLE had written; this grades the sync's own.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)
            posting_service.reverse_transfer_postings_before_delete(transfer)
            _db.session.commit()
            assert _ledger_total(transit_ledger) == Decimal("0.00")
            # Remove the income shadow (the income-type row on the to-account)
            # via raw SQL; its covering movement cascades with it.
            _db.session.execute(_db.text(
                "DELETE FROM budget.transactions "
                "WHERE transfer_id = :t AND account_id = :a"
            ), {"t": transfer.id, "a": savings.id})
            _db.session.commit()

            [posted] = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert _legs_by_ledger(posted.id) == {
                checking_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            assert _ledger_total(transit_ledger) == Decimal("100.00")
            # The missing side posted nothing: Savings sits on its opening.
            assert posting_service.account_posting_total(
                savings.id, _scenario_id(seed_user),
            ) == Decimal("100.00")
            assert posting_service.sync_transfer_postings(transfer) == []

    def test_emit_balanced_entry_rejects_single_leg(self, app, db, seed_user):
        """The builder refuses an entry with fewer than two legs.

        The service-side backstop for the ``COUNT(*) >= 2`` half of the
        deferred balanced-journal trigger: a one-legged entry fails loudly at
        the call site before any write.
        """
        with app.app_context():
            with pytest.raises(PostingError, match="at least 2 legs"):
                _emit_balanced_entry(
                    JournalEntry(),
                    [_PostingLeg(1, Decimal("100.00"), 1)],
                )

    def test_emit_balanced_entry_rejects_unbalanced_legs(
        self, app, db, seed_user,
    ):
        """The builder refuses legs that do not sum to zero.

        The service-side backstop for the ``SUM(amount) = 0`` half of the
        deferred balanced-journal trigger: an unbalanced pair (+100 / +50,
        summing to +150) fails loudly at the call site before any write.
        """
        with app.app_context():
            with pytest.raises(PostingError, match="sum to 0"):
                _emit_balanced_entry(
                    JournalEntry(),
                    [
                        _PostingLeg(1, Decimal("100.00"), 1),
                        _PostingLeg(2, Decimal("50.00"), 1),
                    ],
                )


# ===========================================================================
# Build-Order Step 3: ordinary cash transaction postings
# ===========================================================================


class TestTransactionSettlePostsBalancedEntry:
    """A settled ordinary transaction posts one balanced two-leg entry."""

    def test_plain_expense_signs_balance_and_metadata(
        self, app, db, seed_user,
    ):
        """A $50 Paid Groceries expense posts -50 / +50, summing to zero.

        Arithmetic (plan Section 1): a plain expense's money is its ONE
        covering movement (plan step ``balance:X-bi-4a``, ruling **R-BAL80**:
        the row itself posts nothing), worth ``50`` with the expense sign; the
        cash leg is -50.00 (a credit: money leaving Checking) and the category
        leg is +50.00 (a debit: the expense lands in Food: Groceries).  -50.00
        + 50.00 = 0.00.  Also pins the header metadata (the PURCHASE source
        kind, the movement link, owner / scenario / period) and the per-leg
        posting kind (expense).  It pinned a TRANSACTION-sourced entry linked
        by ``transaction_id`` through ``X-bi-3e``.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # Header metadata: the movement's link, the parent's owner / scenario
            # / period.
            [movement] = txn.covering_movements
            assert entry.transaction_entry_id == movement.id
            assert entry.transaction_id is None
            assert entry.transfer_id is None
            assert entry.user_id == seed_user["user"].id
            assert entry.scenario_id == _scenario_id(seed_user)
            assert entry.pay_period_id == period.id
            assert entry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.PURCHASE,
            )
            assert entry.description == "Groceries"
            assert isinstance(entry.entry_date, date)
            # Legs: -50 from Checking, +50 to Groceries, summing to zero.
            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("-50.00")
            assert legs[groceries_ledger] == Decimal("50.00")
            assert sum(legs.values()) == Decimal("0.00")
            # Every leg carries the expense posting kind.
            kinds = {
                leg.posting_kind_id
                for leg in _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .all()
            }
            assert kinds == {
                ref_cache.posting_kind_id(PostingKindEnum.EXPENSE),
            }
            assert len(_entries_for_transaction(txn.id)) == 1

    def test_income_signs(self, app, db, seed_user):
        """A $2000 Received Salary income posts +2000 / -2000.

        Arithmetic (plan Section 1, second worked example): income has no
        entries, so the effect is ``effective_amount`` (2000) with the income
        sign; the cash leg is +2000.00 (a debit: money entering Checking) and
        the category leg is -2000.00 (a credit: income earned in Income:
        Salary).  The sign follows the transaction TYPE, never the account
        class.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Salary", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            salary_ledger = _resolve_category_ledger(
                seed_user, "Salary", LedgerAccountClassEnum.INCOME,
            ).id

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("2000.00")
            assert legs[salary_ledger] == Decimal("-2000.00")
            assert sum(legs.values()) == Decimal("0.00")
            kinds = {
                leg.posting_kind_id
                for leg in _db.session.query(Posting)
                .filter_by(journal_entry_id=entry.id)
                .all()
            }
            assert kinds == {
                ref_cache.posting_kind_id(PostingKindEnum.INCOME),
            }

    def test_expense_uses_effective_actual_not_estimated(
        self, app, db, seed_user,
    ):
        """A manual ``actual_amount`` (not the estimate) drives the cash leg.

        Arithmetic: estimated $50 but settled actual $45, so
        ``effective_amount`` is 45 (``actual_amount`` overrides), and the
        expense cash leg is -45.00, NOT -50.00.  Locks that the service reads
        the ``effective_amount`` property (the value the balance calculator
        and the oracle use), not the raw estimate.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_amount="45.00",
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            assert legs[cash_ledger] == Decimal("-45.00")
            assert legs[groceries_ledger] == Decimal("45.00")

    def test_envelope_posts_debit_only_effect(self, app, db, seed_user):
        """A settled envelope's DEBIT purchases post, each on its own day; the close nothing.

        Arithmetic (plan Section 1 worked example, re-expressed under ruling
        **R-BAL77** at plan step ``balance:X-bi-4a``): a $200 Groceries
        envelope with entries $60 debit / $50 debit / $40 credit, marked Paid.
        The close books NOTHING -- an un-dated purchase is in flight, not the
        row's leg (through ``X-bi-3e`` the close booked the $110.00 remainder
        on the close day).  Dating the two debit purchases posts each: cash
        -60.00 / Groceries +60.00 and cash -50.00 / Groceries +50.00, -110.00
        of debit spending over the family; the $40 credit purchase posts
        nothing (its CC Payback posts when it settles), so there is no
        double-count.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Groceries Env",
                Decimal("200.00"),
            )
            sixty = _add_txn_entry(seed_user, txn, "60.00", is_credit=False)
            fifty = _add_txn_entry(seed_user, txn, "50.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            # The close: nothing to post, the purchases are in flight.
            assert posting_service.sync_transaction_postings(txn) == []

            record_settle_day(sixty, an_entered_day(display_today()))
            record_settle_day(fifty, an_entered_day(display_today()))
            entries = posting_service.sync_transaction_postings(txn)
            _db.session.commit()
            assert len(entries) == 2
            [(_, nets)] = _period_nets(entries).items()
            assert nets[cash_ledger] == Decimal("-110.00")
            assert nets[groceries_ledger] == Decimal("110.00")
            assert sum(nets.values()) == Decimal("0.00")


class TestTransactionAllCreditNoop:
    """An all-credit envelope has zero debit effect and posts nothing."""

    def test_all_credit_envelope_posts_nothing(self, app, db, seed_user):
        """An envelope whose only entry is a credit purchase posts no entry.

        Arithmetic: a single $40 credit entry, ``actual_amount`` = 40, so
        ``effect = effective(40) - credit_sum(40) = 0``.  The target is
        ``{cash: 0, category: 0}``, nothing is posted yet, so every delta is
        zero and the sync is a no-op (returns []) -- the entire $40 flows
        through the separate CC Payback instead.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "All Credit Env",
                Decimal("200.00"),
            )
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()

            result = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            assert result == []
            assert _entries_for_transaction(txn.id) == []


class TestEnvelopeCreditDominatesKeepsExpenseSign:
    """L10: a credit-dominated expense envelope stays an OUTFLOW -- no sign flip.

    The confirmed cash effect is ``effective - Sigma(credit entries)``.  This
    pins that even when the credit portion dwarfs the debit, the tiny debit
    still books as a proper outflow and never as income.

    **What guarantees it is that the DEBIT total is positive, and that is a
    narrower claim than this test used to make** (ruling **bank_import:R-II**,
    developer 2026-08-31).  It read: *two structural invariants forbid a flip --
    ``TransactionEntry.amount`` is ``CHECK (amount > 0)`` and a settled
    envelope's figure is the sum of ALL entries, so ``effective -
    Sigma(credit) = Sigma(debit) >= 0`` always* -- and named its own dependency,
    that a change which *relaxed the positive-amount CHECK would fail here*.
    That CHECK is now ``amount <> 0``, so ``Sigma(debit) >= 0`` is no longer
    given and the old guarantee is genuinely gone.

    **It was not merely weakened, it stopped being desirable.**  An envelope
    whose refunds exceed its purchases really did net RECEIVE money, and
    booking that as a cash inflow is correct rather than a regression --
    :class:`TestEnvelopeRefundDominatesBooksAnInflow` is that case, asserted
    beside this one so the pair states the whole rule.  What survives here is
    the case this class was always about: a positive debit remainder, however
    small, books as an outflow, because the sign follows the transaction TYPE
    and never the relative size of the credit portion.
    """

    def test_credit_dominated_expense_still_posts_a_debit_outflow(
        self, app, db, seed_user
    ):
        """debit $1 / credit $99 -> the dated $1 posts -1.00; the $99 card never.

        Arithmetic (ruling **R-BAL77**, plan step ``balance:X-bi-4a``): the
        close posts nothing; dating the $1 debit purchase posts it, and the
        expense sign makes the cash leg -1.00 (a $1 checking outflow) and the
        Groceries leg +1.00.  The $99 card purchase leaves through its CC
        Payback, so a credit-heavy split never turns the expense into an
        inflow.  (Through ``X-bi-3e`` the close booked ``effective(100) -
        credit_sum(99) = 1`` as the row's own leg.)
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Credit-Heavy Env",
                Decimal("200.00"),
            )
            one = _add_txn_entry(seed_user, txn, "1.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "99.00", is_credit=True)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            assert posting_service.sync_transaction_postings(txn) == []
            record_settle_day(one, an_entered_day(display_today()))
            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            legs = _legs_by_ledger(entry.id)
            # The $1 debit books as a proper outflow -- NOT a positive (inflow)
            # leg, which a credits-exceed-effective sign flip would produce.
            assert legs[cash_ledger] == Decimal("-1.00")
            assert legs[cash_ledger] < Decimal("0.00")
            assert legs[groceries_ledger] == Decimal("1.00")
            assert sum(legs.values()) == Decimal("0.00")


class TestEnvelopeRefundDominatesBooksAnInflow:
    """An envelope whose REFUNDS exceed its purchases books a cash INFLOW.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  The twin of
    :class:`TestEnvelopeCreditDominatesKeepsExpenseSign`, and the case that
    class's premise used to make unrepresentable.  A merchant credit files as a
    NEGATIVE purchase against the envelope its merchant rule names, so an
    envelope can genuinely net receive money -- and the ledger must say so.

    **It also pins WHY the figure can be negative at all**, which is the part a
    reader would otherwise have to rediscover: a settle's record
    (``status_seam.Settlement``) refuses a negative figure, and the row's own
    ``settled_amount`` carried ``ck_transactions_settled_amount`` (``>= 0``)
    through plan step ``balance:X-bi-4b-1``, so no record could hold
    ``-49.00``.  An envelope settling from its purchases records NO figure of
    its own (``Settlement(None, None)``, no covering movement) and derives it
    from the entries at read time, which is exactly what lets a refund-dominated
    row be priced at all.  A future change that made such a row record a
    figure of its own would fail here rather than at a constructor with no
    test behind it.
    """

    def test_refund_dominated_envelope_books_a_positive_cash_leg(
        self, app, db, seed_user
    ):
        """debit $1.00 and a -$50.00 refund -> the family nets +49.00 cash, -49.00 category.

        Arithmetic (ruling **R-BAL77**, plan step ``balance:X-bi-4a``): the
        row settles from its purchases and its figure is ``sum(entries) =
        1.00 + (-50.00) = -49.00``; the close posts nothing, and dating both
        purchases posts each as its own movement -- the $1.00 as cash -1.00 /
        Groceries +1.00 and the refund as cash +50.00 / Groceries -50.00 --
        so the family nets ``+49.00`` into checking: money came back.  The
        Groceries net is ``-49.00``, a contra-expense, which is what a refund
        is.  The two still sum to zero.  (Through ``X-bi-3e`` the close booked
        the whole ``-49.00`` as the row's own leg.)
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Refund-Heavy Env",
                Decimal("200.00"),
            )
            one = _add_txn_entry(seed_user, txn, "1.00", is_credit=False)
            refund = _add_txn_entry(seed_user, txn, "-50.00", is_credit=False)
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                ),
            )
            _db.session.commit()

            # The figure is DERIVED, never recorded -- see the class docstring.
            assert txn.covering_movements == []

            cash_ledger = _ledger_id(seed_user["account"])
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            assert posting_service.sync_transaction_postings(txn) == []
            record_settle_day(one, an_entered_day(display_today()))
            record_settle_day(refund, an_entered_day(display_today()))
            entries = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            assert len(entries) == 2
            [(_, legs)] = _period_nets(entries).items()
            assert legs[cash_ledger] == Decimal("49.00")
            assert legs[cash_ledger] > Decimal("0.00")
            assert legs[groceries_ledger] == Decimal("-49.00")
            assert sum(legs.values()) == Decimal("0.00")


class TestTransactionIdempotency:
    """A repeat sync at the same target writes nothing."""

    def test_repeat_settle_is_noop(self, app, db, seed_user):
        """Re-syncing an already-posted settle returns [], no 2nd entry.

        Arithmetic: the first settle posts -50 / +50; the second sync sees
        current == target (zero deltas on every account and period) and writes nothing.  This
        is the double-mark-done guard -- a repeated settle never double-posts.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()

            first = posting_service.sync_transaction_postings(txn)
            second = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            assert len(first) == 1
            assert second == []
            assert len(_entries_for_transaction(txn.id)) == 1


class TestTransactionReversal:
    """A reversal reads the posted amount back from the ledger."""

    def test_reverse_nets_to_zero(self, app, db, seed_user):
        """Reverting a settled transaction nets both ledgers back to baseline.

        Arithmetic: settle posts -100 (Checking) / +100 (Groceries), the
        covering movement's leg.  The revert -- the seam un-dates the movement
        (ruling **R-BAL61**), and the reconcile then reverses exactly what is
        posted: +100 (Checking) / -100 (Groceries).  The category account
        nets to zero, the Checking total lands back on its $1000.00 anchor
        (the period-start settle was pre-assertion-absorbed and the self-heal
        re-based the opening at each step), and two family-linked entries
        survive (append-only correction, never an edit).  Through ``X-bi-3e``
        the reconcile took a ``settled=False`` flag for the revert; the row's
        own target is empty on every call now (ruling **R-BAL80**).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            groceries_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            [reversal] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            assert _ledger_total(groceries_ledger) == Decimal("0.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")
            assert len(_entries_for_transaction(txn.id)) == 2

    def test_revert_recategorize_resettle_posts_new_zeroes_old(
        self, app, db, seed_user,
    ):
        """Revert -> recategorize -> re-settle posts to NEW, zeroes OLD (2.8 CRITICAL).

        The exact scenario the per-site approach got wrong.  Arithmetic:

          1. Settle a $100 expense in category A (Groceries): cash -100, A +100.
          2. Revert through the seam (the movement is un-dated), recategorize
             to B (Rent) and reconcile (the single-PATCH revert): the reversal
             reads the LEDGER (category A), not the now-B ``category_id``, so
             it posts +100 cash / -100 A.  A nets to zero.
          3. Re-settle through the seam (the movement is re-dated; category
             now B) and reconcile: cash -100 / +100 B.

        Final books: category A nets to **zero**, category B carries the
        +100.00 expense, and Checking's total sits on its $1000.00 anchor
        (the period-start-dated -100 is pre-assertion-absorbed: the self-heal
        re-based the opening to +1100).  Reading the posted side from the
        ledger -- never from ``txn.category_id`` -- is what makes the
        per-category books correct.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            a_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id

            # 1. Settle in category A.
            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # 2. Revert, recategorize to B, then reconcile.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
            )
            txn.category_id = seed_user["categories"]["Rent"].id
            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # 3. Re-settle with the new category, on the day it first settled
            #    (period start, pre-assertion) so the absorption arithmetic
            #    above still holds; the seam re-dates the kept movement.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settle_day=an_entered_day(period.start_date),
                settlement=settlement_if_settling(
                    txn, ref_cache.status_id(StatusEnum.DONE),
                ),
            )
            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            b_ledger = _resolve_category_ledger(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            ).id
            assert _ledger_total(a_ledger) == Decimal("0.00")
            assert _ledger_total(b_ledger) == Decimal("100.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")
            # settle + revert + re-settle = three append-only entries.
            assert len(_entries_for_transaction(txn.id)) == 3

    def test_recategorize_while_settled_posts_cashless_reclassification(
        self, app, db, seed_user,
    ):
        """Recategorizing a still-settled transaction moves the category leg only.

        Arithmetic: settle a $100 expense in A (Groceries): cash -100, A +100.
        Then recategorize to B (Rent) and re-sync while STILL settled
        (``settled=True``, no revert).  The amount is unchanged, so the cash
        delta is zero and drops out; the entry carries TWO category legs and NO
        cash leg (A -100 / B +100).  This exercises the union reconcile's
        balanced-by-construction property in the cash-leg-absent case: the
        non-zero deltas still sum to zero and still yield >= 2 legs.  Net books:
        A nets to zero, B carries the +100 expense, Checking's total stays on
        its $1000.00 anchor (the pre-assertion -100 absorbed by the opening).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            a_ledger = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            ).id
            cash_ledger = _ledger_id(seed_user["account"])

            posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # Recategorize WITHOUT reverting; re-sync while still settled.
            txn.category_id = seed_user["categories"]["Rent"].id
            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            b_ledger = _resolve_category_ledger(
                seed_user, "Rent", LedgerAccountClassEnum.EXPENSE,
            ).id
            # Two category legs, no cash leg, summing to zero.
            legs = _legs_by_ledger(entry.id)
            assert legs == {
                a_ledger: Decimal("-100.00"),
                b_ledger: Decimal("100.00"),
            }
            assert cash_ledger not in legs
            assert sum(legs.values()) == Decimal("0.00")
            # Net books: A zero, B carries the expense, cash on its anchor.
            assert _ledger_total(a_ledger) == Decimal("0.00")
            assert _ledger_total(b_ledger) == Decimal("100.00")
            assert posting_service.account_posting_total(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("1000.00")


class TestTransactionCounterLegRouting:
    """The counter leg lands in the right category / fallback account."""

    def test_categorized_expense_lands_in_category_ledger(
        self, app, db, seed_user,
    ):
        """A categorized expense books its counter leg into the category row.

        The non-cash leg lands in the (owner, Groceries, Expense) ledger
        account -- a category row (``is_owner_bucket`` False, ``category_id`` set),
        NOT a real-account link.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            groceries = _resolve_category_ledger(
                seed_user, "Groceries", LedgerAccountClassEnum.EXPENSE,
            )
            cash_ledger = _ledger_id(seed_user["account"])
            legs = _legs_by_ledger(entry.id)
            non_cash = [lid for lid in legs if lid != cash_ledger]
            assert non_cash == [groceries.id]
            assert groceries.is_owner_bucket is False
            assert groceries.account_id is None
            assert groceries.category_id == seed_user["categories"][
                "Groceries"
            ].id

    def test_uncategorized_expense_lands_in_fallback(
        self, app, db, seed_user,
    ):
        """A NULL-category expense books its counter leg into the fallback.

        The non-cash leg lands in the per-(owner, Expense) Uncategorized
        fallback (``is_owner_bucket`` True, ``category_id`` NULL), the catch-all
        for a settled transaction whose category is NULL.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Misc", "50.00",
                status_enum=StatusEnum.DONE, category_key=None,
            )
            _db.session.commit()

            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            fallback = _resolve_category_ledger(
                seed_user, None, LedgerAccountClassEnum.EXPENSE,
            )
            cash_ledger = _ledger_id(seed_user["account"])
            legs = _legs_by_ledger(entry.id)
            non_cash = [lid for lid in legs if lid != cash_ledger]
            assert non_cash == [fallback.id]
            assert fallback.is_owner_bucket is True
            assert fallback.category_id is None
            assert fallback.class_id == ref_cache.ledger_account_class_id(
                LedgerAccountClassEnum.EXPENSE,
            )


class TestTransactionShadowFamily:
    """A transfer shadow's family posts through the ONE movement writer."""

    def test_a_shadow_reconciles_its_movement_against_transit(
        self, app, db, seed_user, savings,
    ):
        """``sync_transaction_postings`` on a shadow posts its side, into transit.

        Re-expressed from ``test_transfer_shadow_is_noop`` at plan step
        ``balance:X-bi-6-3`` (ruling **R-BAL101**): the guard that returned
        ``[]`` for a shadow is gone with ruling R-BAL45's interval, so a door
        reaching a shadow's family reconciles it -- its own TRANSACTION source
        target empty as every row's, its covering movement's leg against the
        owner's transit account under the ``transfer_movement`` source, never
        a category.  Proved from a ledger the teardown has brought to zero:
        the income shadow alone re-posts exactly {Savings +100, transit -100},
        and the expense side stays reversed.

        A settled shadow at target is a no-op through the same door, which the
        first assertion pins; the old test could not tell that no-op from the
        guard's.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            income_shadow = (
                _db.session.query(Transaction)
                .filter_by(transfer_id=transfer.id, account_id=savings.id)
                .one()
            )
            assert income_shadow.transfer_id is not None
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            # At target after the settle: the door writes nothing.
            assert posting_service.sync_transaction_postings(income_shadow) == []

            posting_service.reverse_transfer_postings_before_delete(transfer)
            _db.session.commit()
            assert _ledger_total(savings_ledger) == Decimal("100.00")  # opening

            [entry] = posting_service.sync_transaction_postings(income_shadow)
            _db.session.commit()

            assert entry.transaction_entry_id == _covering_movement_of_side(
                transfer.id, savings.id,
            ).id
            assert entry.transaction_id is None
            assert entry.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER_MOVEMENT,
            )
            assert _legs_by_ledger(entry.id) == {
                savings_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            # No transaction-sourced entry exists for the shadow (its own
            # target is empty), and the expense side stayed reversed.
            assert _db.session.query(JournalEntry).filter_by(
                transaction_id=income_shadow.id,
            ).count() == 0
            assert posting_service.account_posting_total(
                checking.id, _scenario_id(seed_user),
            ) == Decimal("1000.00")

    def test_a_movement_reconcile_reads_only_its_own_source_kind(
        self, app, db, seed_user, savings,
    ):
        """An entry linking the same movement under ANOTHER source is untouched.

        The hazard rulings **R-BAL100** and **R-BAL101** arm together: a loan
        payment's SPLIT correction links the loan-side movement's
        ``transaction_entry_id`` under the ``loan_payment`` source, beside the
        movement's own cash leg under ``transfer_movement``.  A reconcile that
        read the posted side by LINK alone would sum the split's legs into the
        cash leg's, compute a delta against the cash target, and REVERSE the
        split.  Every typed reconcile filters on its source kind as well as its
        link (``_posting_write.emit_typed_source_deltas``), so it must not.

        Planted by hand -- a balanced ``loan_payment`` entry of $30.00 linked
        to the income shadow's covering movement, between the transit row and
        the Savings row -- then the pair re-synced: the sync writes nothing
        (the cash leg is at target and the split is not its concern) and the
        family holds exactly the three entries planted.  Mutation, observed
        to FIRE on 2026-09-21: with the kind term dropped from the filter the
        sync emitted a fourth entry reversing the $30.00 (``== []`` failed).
        The planted legs themselves are append-only and so never asserted --
        a claim the ledger cannot falsify grades nothing.
        """
        with app.app_context():
            transfer = create_settled_transfer(
                seed_user, _db.session, seed_user["account"], savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            movement = _covering_movement_of_side(transfer.id, savings.id)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)
            planted = JournalEntry(
                user_id=seed_user["user"].id,
                scenario_id=_scenario_id(seed_user),
                pay_period_id=seed_user["bootstrap_period"].id,
                entry_date=movement.settled_on,
                source_kind_id=ref_cache.posting_source_id(
                    PostingSourceEnum.LOAN_PAYMENT,
                ),
                transaction_entry_id=movement.id,
                description="planted split beside the cash leg",
            )
            _emit_balanced_entry(planted, [
                _PostingLeg(
                    savings_ledger, Decimal("-30.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
                _PostingLeg(
                    transit_ledger, Decimal("30.00"),
                    ref_cache.posting_kind_id(PostingKindEnum.PRINCIPAL),
                ),
            ])
            _db.session.commit()

            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            # The movement's own family: the cash leg, the planted entry, and
            # nothing the sync added.
            assert len(_entries_for_transfer(transfer.id)) == 3


def _live_shadow_id(transfer_id, account_id):
    """Return the id of the transfer's LIVE shadow row on *account_id*."""
    return (
        _db.session.query(Transaction.id)
        .filter(
            Transaction.transfer_id == transfer_id,
            Transaction.account_id == account_id,
            Transaction.is_deleted.is_(False),
        )
        .scalar()
    )


def _drift(sql, **params):
    """Write one row AROUND the service by raw SQL, commit, and expire the session.

    Each case below writes a state no door writes (Transfer Invariants 3 and
    4) the way it has occurred -- past the services -- so the writer is
    asked about exactly the rows the database holds.
    """
    _db.session.execute(_db.text(sql), params)
    _db.session.commit()
    _db.session.expire_all()


class TestTheWriterBooksATransferMovementUnderItsLeg:
    """Leaf ``balance:X-bi-6-4a``: a transfer movement's parent is its LEG, never its shadow.

    Every door hands the writer the parent
    ``transfer_legs.movement_parent`` resolves, so a transfer movement's
    period, contributing gate and owner are its TRANSFER's -- the ones the
    cash fold has read since the leaf's first half (ruling **R-BAL106**).
    Each case writes a drift around the service and asks the pair's door.
    The period, gate and oracle cases are red on the tree before the leaf,
    where the writer and the oracle read the shadow row or the status; the
    dead-shadow case pins the one term the leaf ADDED (a leg posts its
    record alone), whose answer the old tree gave through the dead shadow's
    own gate.
    """

    def test_a_legs_entries_file_under_its_transfers_period(
        self, app, db, seed_user, seed_periods, savings,
    ):  # pylint: disable=unused-argument
        """R-JA, the parent decides: a shadow moved to another period moves no entry.

        ``$100.00`` Checking -> Savings settled in P; the CHECKING shadow's
        period is rewritten to F by SQL.  Tearing the pair down and posting
        it again through its door files both sides' entries under P, the
        transfer's: over the family Checking nets ``-100.00`` in P and F
        holds nothing.  Before the leaf the re-post read the shadow's period,
        so Checking's ``-100.00`` landed in F and P netted ``0.00``.
        """
        with app.app_context():
            period, drifted = seed_periods[0], seed_periods[5]
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings, period,
                amount=Decimal("100.00"),
            )
            _db.session.commit()
            _drift(
                "UPDATE budget.transactions SET pay_period_id = :p WHERE id = :id",
                p=drifted.id, id=_live_shadow_id(transfer.id, checking.id),
            )

            posting_service.reverse_transfer_postings_before_delete(transfer)
            reposted = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert [entry.pay_period_id for entry in reposted] == [
                period.id, period.id,
            ]
            per_period = _period_nets_for_transfer(transfer.id)
            assert drifted.id not in per_period
            assert per_period[period.id][_ledger_id(checking)] == (
                Decimal("-100.00")
            )
            assert per_period[period.id][_ledger_id(savings)] == (
                Decimal("100.00")
            )

    def test_a_transfer_deleted_around_the_service_holds_nothing(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """The gate is the TRANSFER's: its live shadows keep no leg posted.

        ``$100.00`` Checking -> Savings settled; the TRANSFER row alone is
        flagged deleted by SQL, its two shadows left live (Transfer Invariant
        4 drift).  The pair's door reverses both legs: Checking is back on its
        ``$1,000.00`` opening and Savings on its ``$100.00`` one, which is
        what the cash fold reads too (its leg arm gates on the transfer, so
        no fact names it).  Before the leaf the writer read each live
        shadow's gate and left both legs posted (``900.00`` / ``200.00``).
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = _scenario_id(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            _drift(
                "UPDATE budget.transfers SET is_deleted = TRUE WHERE id = :id",
                id=transfer.id,
            )

            reversed_entries = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert len(reversed_entries) == 2
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert not [
                fact for account in (checking, savings)
                for fact in settled_cash_facts(account.id, scenario_id)
                if fact.transfer_id == transfer.id
            ]

    def test_a_row_teardown_on_a_shadow_reverses_its_side_under_the_leg(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """The row teardown reaching a shadow types its movement by the LEG.

        ``$100.00`` Checking -> Savings settled; ``reverse_postings_before_delete``
        is handed the SAVINGS shadow.  Its movement is booked under the
        Savings leg, so the reversal is read back and written under the
        ``transfer_movement`` source: one entry, {Savings -100.00, transit
        +100.00}, and Savings is back on its ``$100.00`` opening while
        Checking keeps its side (``900.00``).  MUTATION: hand the writer the
        shadow row instead and it types the movement as a PURCHASE, reads
        nothing posted under that source, reverses nothing, and strands the
        ``$100.00`` -- the case ``posting_service`` has promised since plan
        step ``balance:X-bi-6-3`` ("a transfer shadow reaching here is
        reversed like any row") and no test held it to.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = _scenario_id(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            income_shadow = _db.session.get(
                Transaction, _live_shadow_id(transfer.id, savings.id),
            )
            entries_before = len(_entries_for_transfer(transfer.id))

            posting_service.reverse_postings_before_delete(income_shadow)
            _db.session.commit()

            [reversal] = _entries_for_transfer(transfer.id)[entries_before:]
            assert reversal.source_kind_id == ref_cache.posting_source_id(
                PostingSourceEnum.TRANSFER_MOVEMENT,
            )
            assert _legs_by_ledger(reversal.id) == {
                _ledger_id(savings): Decimal("-100.00"),
                _transit_ledger_id(seed_user): Decimal("100.00"),
            }
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("100.00")
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("900.00")

    def test_a_movement_under_a_dead_shadow_posts_nothing(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """A leg posts its RECORD alone: a dead shadow's movement reverses, the live side stays.

        ``$100.00`` Checking -> Savings settled; the CHECKING shadow alone is
        soft-deleted by SQL, the transfer left live and settled (Transfer
        Invariant 4 drift).  Its movement is no leg's record
        (``movement_parent`` gives its leg none: the join's live-shadow test
        over one loaded movement), so the pair's door reverses Checking's leg --
        Checking back on its ``$1,000.00`` opening -- and keeps Savings'
        (``$100.00`` opening + ``100.00`` = ``200.00``); the row door on the
        dead shadow then finds its family at target.  MUTATION: drop
        ``purchase_posts``' record term and the dead shadow's movement is
        booked under the LIVE transfer's leg and stays posted (Checking
        ``900.00``).
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = _scenario_id(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            dead_shadow_id = _live_shadow_id(transfer.id, checking.id)
            _drift(
                "UPDATE budget.transactions SET is_deleted = TRUE WHERE id = :id",
                id=dead_shadow_id,
            )

            [reversal] = posting_service.sync_transfer_postings(transfer)
            _db.session.commit()

            assert _legs_by_ledger(reversal.id) == {
                _ledger_id(checking): Decimal("100.00"),
                _transit_ledger_id(seed_user): Decimal("-100.00"),
            }
            assert posting_service.account_posting_total(
                checking.id, scenario_id,
            ) == Decimal("1000.00")
            assert posting_service.account_posting_total(
                savings.id, scenario_id,
            ) == Decimal("200.00")
            assert posting_service.sync_transaction_postings(
                _db.session.get(Transaction, dead_shadow_id),
            ) == []

    def test_the_oracle_counts_a_dated_leg_whatever_the_transfers_status(
        self, app, db, seed_user, savings,
    ):  # pylint: disable=unused-argument
        """The oracle states the writer's rule: a dated leg of a contributing transfer.

        ``$100.00`` Checking -> Savings settled; the TRANSFER's status alone
        is rewritten to Projected by SQL (a status drift: both movements stay
        dated).  The writer keeps both legs posted -- a movement posts iff it
        is dated under a contributing parent (ruling **R-BAL101**) -- and
        ``settled_transfer_effect`` agrees on both accounts: Checking
        ``900.00`` = ``1,000.00`` + ``-100.00``, Savings ``200.00`` =
        ``100.00`` + ``100.00``.  Before the leaf the oracle filtered on the
        settled STATUS and answered ``0.00`` on both, grading a correct
        ledger as ``$100.00`` off.
        """
        with app.app_context():
            checking = seed_user["account"]
            scenario_id = _scenario_id(seed_user)
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
                settled_on=display_today(),
            )
            _db.session.commit()
            _drift(
                "UPDATE budget.transfers SET status_id = :s WHERE id = :id",
                s=ref_cache.status_id(StatusEnum.PROJECTED), id=transfer.id,
            )

            assert posting_service.sync_transfer_postings(transfer) == []
            _db.session.commit()

            for account, opening, effect in (
                (checking, Decimal("1000.00"), Decimal("-100.00")),
                (savings, Decimal("100.00"), Decimal("100.00")),
            ):
                assert posting_service.settled_transfer_effect(
                    account.id, scenario_id,
                ) == effect
                assert posting_service.account_posting_total(
                    account.id, scenario_id,
                ) == opening + effect


class TestTransactionEntryDate:
    """``entry_date`` is the movement's recorded settle DAY, the row's mirrored.

    A sibling case, ``test_entry_date_falls_back_to_period_start_when_paid_at_``
    ``null``, was DELETED at plan step X-f1 rather than re-derived: it built a
    row whose settle day equalled its pay period's start, which is the exact
    value the deleted fallback produced, so the two rules coincide on it and
    nothing it asserted could tell them apart.  Measured by a neutral review --
    replacing the row's entry-date reader with the old ``return
    txn.pay_period.start_date`` left it PASSING while the case below failed.
    Since plan step ``balance:X-bi-4a`` the writer reads no row day at all:
    the posted entry is the covering movement's, dated by ITS ``settled_on``.
    """

    def test_entry_date_is_the_rows_recorded_settle_day(
        self, app, db, seed_user,
    ):
        """A settled transaction's postings are dated by its recorded settle day.

        The transaction twin of the transfer case above; see that docstring for
        how the rule moved from a UTC derivation (pre-R-DH (b)) to a display-zone
        one and then to a stored fact (plan step X-f1).
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_on=date(2026, 5, 9),
            )
            _db.session.commit()

            # The movement's day IS the row's (the seam mirrors it), and the
            # movement is what posts (ruling **R-BAL80**).
            [entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()
            assert entry.entry_date == date(2026, 5, 9)

class TestTransactionFailLoud:
    """Broken invariants raise PostingError rather than posting silently."""

    def test_missing_cash_ledger_fails_loud(self, app, db, seed_user):
        """A transaction whose account has no ledger pairing raises PostingError.

        Removing the Checking account's linked ledger row (an impossible state
        in production -- every account is paired) makes the settle sync fail
        loudly rather than post a one-legged or silently-wrong entry.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
            )
            _db.session.commit()
            _db.session.execute(_db.text(
                "DELETE FROM budget.ledger_accounts WHERE account_id = :a"
            ), {"a": seed_user["account"].id})
            _db.session.commit()

            with pytest.raises(PostingError, match="ledger account"):
                posting_service.sync_transaction_postings(txn)

    def test_posted_purchase_effect_none_scenario_fails_loud(
        self, app, db, seed_user,
    ):
        """A None scenario in ``posted_purchase_effect`` raises PostingError."""
        with app.app_context():
            with pytest.raises(PostingError, match="scenario_id"):
                posting_service.posted_purchase_effect(
                    seed_user["account"].id, None,
                )


class TestPostedPurchaseEffect:
    """The movement-effect oracle agrees with the posting total.

    ``settled_transaction_effect``, the transaction source's oracle, went at
    plan step ``balance:X-bi-4a`` with the row's own leg (ruling **R-BAL80**):
    ``posted_purchase_effect`` is the whole non-transfer half of the oracle's
    per-account invariant now -- every DATED movement of a contributing
    parent, on the movement's account, whatever the parent's status.  Its
    per-transaction credit-sum subquery, which a case here pinned as
    correlated rather than global, went with it: the movement oracle excludes
    a card purchase by the movement's own flag, one row at a time.
    """

    def test_effect_matches_posting_total(self, app, db, seed_user):
        """The signed movement effect rides on top of the opening.

        Arithmetic: a $50 Paid expense (-50) and a $2000 Received income
        (+2000) on Checking net to +1950.00 -- each settled row's money is its
        ONE covering movement.  ``posted_purchase_effect`` (a source-table
        query over the movements) reports exactly that; both settles are
        dated today (post-assertion), so ``account_posting_total`` (the ledger
        sum) carries the same +1950.00 on top of the $1000.00 opening --
        2950.00.  The two independent tables agree on the movements'
        contribution only because no transfers exist here, so the
        post-opening cash legs are entirely movement-sourced.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            expense = add_txn(
                _db.session, seed_user, period, "Groceries", "50.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_on=display_today(),
            )
            income = add_txn(
                _db.session, seed_user, period, "Salary", "2000.00",
                status_enum=StatusEnum.RECEIVED, is_income=True,
                category_key="Salary", settled_on=display_today(),
            )
            _db.session.commit()
            posting_service.sync_transaction_postings(expense)
            posting_service.sync_transaction_postings(income)
            _db.session.commit()

            scenario_id = _scenario_id(seed_user)
            account_id = seed_user["account"].id
            # -50 (expense) + 2000 (income) = 1950; ledger 1000 + 1950.
            assert posting_service.posted_purchase_effect(
                account_id, scenario_id,
            ) == Decimal("1950.00")
            assert posting_service.account_posting_total(
                account_id, scenario_id,
            ) == Decimal("2950.00")

    def test_effect_excludes_credit_portion(self, app, db, seed_user):
        """The oracle sums the DATED DEBIT movements and leaves a card purchase out.

        Arithmetic: a settled envelope with $60 + $50 debit purchases, both
        dated, and a $40 card purchase.  The two debit movements are -110.00
        of expense; the card purchase leaves through its CC Payback and is
        excluded by its own ``is_credit`` flag, exactly as the go-forward
        ``purchase_posts`` leaves it out of the ledger.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            txn = create_envelope_txn(
                seed_user, _db.session, period, "Groceries Env",
                Decimal("200.00"),
            )
            sixty = _add_txn_entry(seed_user, txn, "60.00", is_credit=False)
            fifty = _add_txn_entry(seed_user, txn, "50.00", is_credit=False)
            _add_txn_entry(seed_user, txn, "40.00", is_credit=True)
            record_settle_day(sixty, an_entered_day(display_today()))
            record_settle_day(fifty, an_entered_day(display_today()))
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.commit()

            assert posting_service.posted_purchase_effect(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-110.00")

    def test_effect_is_blind_to_the_parents_status(self, app, db, seed_user):
        """A dated purchase under a PROJECTED envelope counts exactly as one under a closed one.

        The narrowing the oracle lost at plan step ``balance:X-bi-4a``: it
        read purchases on UNSETTLED parents alone while the settled parent's
        own leg carried the rest.  A $30.00 purchase dated today under an open
        envelope and a $50.00 bill settled today net -80.00, one term.
        """
        with app.app_context():
            period = seed_user["bootstrap_period"]
            envelope = create_envelope_txn(
                seed_user, _db.session, period, "Open Env", Decimal("200.00"),
            )
            thirty = _add_txn_entry(seed_user, envelope, "30.00", is_credit=False)
            record_settle_day(thirty, an_entered_day(display_today()))
            add_txn(
                _db.session, seed_user, period, "Rent", "50.00",
                status_enum=StatusEnum.DONE, category_key="Rent",
                settled_on=display_today(),
            )
            _db.session.commit()

            assert posting_service.posted_purchase_effect(
                seed_user["account"].id, _scenario_id(seed_user),
            ) == Decimal("-80.00")


# ---------------------------------------------------------------------------
# Period attribution: corrections land in the period they correct (review R2)
# ---------------------------------------------------------------------------


class TestPeriodAttribution:
    """A reversal carries the period and date of the postings it reverses.

    The 2026-07-02 adversarial review's R2 rule (fixing H1): the supported
    revert-and-move PATCH applies the new ``pay_period_id`` BEFORE the
    end-of-handler reconcile (and, since ruling **R-BAL58**, the revert's own
    reconcile runs before the move and the handler reconciles again after
    it), so a reversal stamped with the source row's CURRENT period would
    land in the NEW period -- leaving the original entry
    and its reversal straddling two periods, where a later truncate of the new
    period CASCADE-deletes one half and permanently strands the other
    (``transaction_id`` SET NULL, unhealable).  The reconcile instead reads
    the posted side back per period, so the reversal nets the ORIGINAL period
    to zero and inherits the latest entry date it reverses.
    """

    def test_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods,
    ):
        """The reversal lands in the settle period, dated by the settle date.

        Settle a $100 expense in period P (paid 2026-01-05, so the settle
        entry is dated 2026-01-05 in P).  Simulate the revert-and-move PATCH
        exactly as the handler applies it -- ``pay_period_id`` moved to F and
        the settle day cleared BEFORE the reconcile -- then reconcile with
        ``settled=False``.  The reversal entry must carry period P (never F)
        and the inherited 2026-01-05 date (never F's start or today), so P
        nets to zero per ledger account and F holds nothing.  A later
        re-settle posts into F alone.
        """
        with app.app_context():
            period = seed_periods[0]
            moved_to = seed_periods[5]
            txn = add_txn(
                _db.session, seed_user, period, "Groceries", "100.00",
                status_enum=StatusEnum.DONE, category_key="Groceries",
                settled_on=date(2026, 1, 5),
            )
            _db.session.commit()
            cash_ledger = _ledger_id(seed_user["account"])

            [settle_entry] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()
            assert settle_entry.pay_period_id == period.id
            assert settle_entry.entry_date == date(2026, 1, 5)

            # The revert-and-move PATCH, exactly as ``_apply_regular_update``
            # applies it: the new period lands, and the status goes through the
            # SEAM -- which is what clears the settle day, since plan step X-f1
            # made the two one write.  Both land BEFORE the reconcile.
            txn.pay_period_id = moved_to.id
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.PROJECTED),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.PROJECTED)),
            )
            _db.session.flush()
            assert txn.settled_on is None
            [reversal] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()

            # The R2 rule: the reversal carries the ORIGINAL period and the
            # settle entry's date -- the pre-fix code stamped it with the NEW
            # period (moved_to) and the reversal-time fallback date.
            assert reversal.pay_period_id == period.id
            assert reversal.entry_date == date(2026, 1, 5)
            legs = _legs_by_ledger(reversal.id)
            assert legs[cash_ledger] == Decimal("100.00")

            # Period P nets to zero per ledger account; F holds nothing.
            per_period = _period_nets_for_transaction(txn.id)
            assert per_period[period.id][cash_ledger] == Decimal("0.00")
            assert moved_to.id not in per_period

            # A later re-settle posts into the NEW period alone, dated the day
            # the user re-settled it on -- the seam's stamp.  It dated at the
            # period's ``start_date`` until plan step X-f1, because the cleared
            # instant fell back to it; a re-settle records a real day now, and
            # the row's budget period and its cash day are allowed to disagree.
            status_seam.apply_status_change(
                txn, ref_cache.status_id(StatusEnum.DONE),
                settlement=settlement_if_settling(txn, ref_cache.status_id(StatusEnum.DONE)),
            )
            _db.session.flush()
            [resettle] = posting_service.sync_transaction_postings(txn)
            _db.session.commit()
            assert resettle.pay_period_id == moved_to.id
            assert resettle.entry_date == display_today()
            per_period = _period_nets_for_transaction(txn.id)
            assert per_period[period.id][cash_ledger] == Decimal("0.00")
            assert per_period[moved_to.id][cash_ledger] == Decimal("-100.00")

    def test_transfer_revert_and_move_reverses_into_the_original_period(
        self, app, db, seed_user, seed_periods, savings,
    ):
        """The transfer reversal lands in the settle period, like a transaction.

        Settle a $100 Checking -> Savings transfer in period P (auto-posted at
        creation as two per-movement entries), then revert AND move it to F
        in one PATCH through the transfer door -- the revert un-dates both
        movements (ruling **R-BAL61**) and the door's reconcile reverses what
        is posted: each side's reversal entry carries P, both real ledgers
        and transit net to zero in P, and F holds nothing -- the transfer
        twin of the transaction case above.  Re-expressed at plan step
        ``balance:X-bi-6-3``: it moved the parent's period by hand and called
        the sync with ``settled=False``; the door reads the movements now, so
        the revert-and-move is made the way the PATCH makes it.
        """
        with app.app_context():
            period = seed_periods[0]
            moved_to = seed_periods[5]
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                period, amount=Decimal("100.00"),
            )
            _db.session.commit()
            checking_ledger = _ledger_id(checking)
            savings_ledger = _ledger_id(savings)
            transit_ledger = _transit_ledger_id(seed_user)

            transfer_service.update_transfer(
                transfer.id, seed_user["user"].id,
                status_id=ref_cache.status_id(StatusEnum.PROJECTED),
                pay_period_id=moved_to.id,
            )
            _db.session.commit()

            by_side = _side_entries(transfer.id)
            [_, from_reversal] = by_side[checking.id]
            [_, to_reversal] = by_side[savings.id]
            assert from_reversal.pay_period_id == period.id
            assert to_reversal.pay_period_id == period.id
            assert _legs_by_ledger(to_reversal.id) == {
                savings_ledger: Decimal("-100.00"),
                transit_ledger: Decimal("100.00"),
            }
            assert _legs_by_ledger(from_reversal.id) == {
                checking_ledger: Decimal("100.00"),
                transit_ledger: Decimal("-100.00"),
            }
            per_period = _period_nets_for_transfer(transfer.id)
            assert per_period[period.id][savings_ledger] == Decimal("0.00")
            assert per_period[period.id][checking_ledger] == Decimal("0.00")
            assert per_period[period.id][transit_ledger] == Decimal("0.00")
            assert moved_to.id not in per_period
