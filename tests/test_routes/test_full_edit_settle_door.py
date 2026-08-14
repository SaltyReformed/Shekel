"""The full-edit popover settles like every other door (plan step X-ap).

**Finding N-219: a transaction had THREE settle doors and one booked a
different figure.**  The grid's Mark Paid button and the reconcile panel's tick
both call ``transaction_service.settle_transaction``, which settles an
envelope-tracked row at the sum of the purchases recorded against it.  The
Status dropdown on the full-edit popover did not: it flipped the status through
the status SEAM -- the mechanics primitive -- and reconciled the ledger, never
consulting the entries, so the row booked ``COALESCE(actual, estimated)``: its
BUDGET.

Measured on the production clone this suite's figures are taken from: row 2231
*Gas*, budget ``$80.00``, one logged purchase of ``$48.98``.  Mark Paid booked
``$48.98`` and returned ``$31.02`` of unspent budget to the projection; the
dropdown booked ``$80.00`` and returned nothing.  ``$31.02`` of spend that never
happened, from two controls in the same card, and the double-entry ledger
followed whichever was used.

What these pin, in the order the request runs them:

  * the dropdown books the ENTRY SUM, and the ledger agrees (the money control);
  * the two doors agree, asserted by settling the same shape through each;
  * a row already INSIDE the settled band is not re-settled -- ``Paid ->
    Settled`` is an archive of a row whose amount is already a fact;
  * a settled status that contradicts the row's TYPE is refused rather than
    silently substituted, and the dropdown does not offer it;
  * the Actual box is rendered exactly when the settle would honour a figure
    typed into it (ruling **R-FF**), and a submitted one is refused otherwise.
"""
# pylint: disable=redefined-outer-name
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture pattern;
# test bodies bind fixtures (``auth_client``, ``seed_user``, ...) by name.
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.transaction import Transaction
from app.services import transaction_service
from app.services.state_machine import allowed_transitions
from app.utils.dates import display_today
from tests._test_helpers import add_entry, create_envelope_txn
from app.services.row_valuation import owned_contribution


def _gas_envelope(seed_user, period):
    """Build production row 2231's shape: an $80.00 budget, one $48.98 purchase.

    Returns the flushed, Projected, envelope-tracked transaction.
    """
    txn = create_envelope_txn(
        seed_user, db.session, period, "Gas", Decimal("80.00"),
    )
    add_entry(
        db.session, seed_user, txn, Decimal("48.98"), display_today(),
    )
    db.session.commit()
    return txn


def _full_edit_save(auth_client, txn, status_id, **overrides):
    """PATCH exactly what the full-edit popover submits on Save.

    The form posts the row's whole state, not a delta -- the estimated amount,
    the period, the notes, the due date and the status -- which is why the
    handler's field writes and its status work interact at all.  Keyword
    overrides replace or add a field.

    **A FINALISED row submits less, and modelling that is load-bearing.**  The
    template disables the money / period / due-date inputs on an immutable row
    (the #26 lock's UI half) and a disabled input is omitted from the POST, so
    a payload carrying them would be refused by ``_finalised_edit_response``
    before any of this suite's subject matter ran -- a helper that tested the
    lock instead of the settle.
    """
    payload = {
        "version_id": str(txn.version_id),
        "status_id": str(status_id),
        "notes": txn.notes or "",
    }
    if not txn.status.is_immutable:
        payload["estimated_amount"] = str(txn.estimated_amount)
        payload["pay_period_id"] = str(txn.pay_period_id)
        payload["due_date"] = (
            txn.due_date.isoformat() if txn.due_date else ""
        )
    payload.update(overrides)
    return auth_client.patch(f"/transactions/{txn.id}", data=payload)


def _cash_leg(txn_id, account_id):
    """Return the net posted amount on the account's ledger for *txn_id*.

    Debit-positive, so a settled expense reads negative: this is what the
    double-entry ledger says the row cost, read back independently of the row.
    """
    # pylint: disable=import-outside-toplevel  -- collection-time safety, the
    # convention the shared helpers use.
    from tests._test_helpers import linked_ledger_account

    ledger_id = linked_ledger_account(db.session, account_id).id
    return (
        db.session.query(
            db.func.coalesce(db.func.sum(Posting.amount), Decimal("0"))
        )
        .join(JournalEntry, JournalEntry.id == Posting.journal_entry_id)
        .filter(
            JournalEntry.transaction_id == txn_id,
            Posting.ledger_account_id == ledger_id,
        )
        .scalar()
    )


class TestTheDropdownBooksWhatTheRowCost:
    """The money control: production row 2231's $31.02."""

    def test_the_dropdown_settles_at_the_entry_sum(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Picking Paid in the popover books $48.98, not the $80.00 budget.

        Shown to FIRE: routing this door back to ``status_seam`` books
        ``$80.00`` and leaves ``actual_amount`` NULL.

        Arithmetic: one purchase of $48.98 against an $80.00 envelope, so
        ``actual_amount`` is 48.98 and the checking leg is -48.98.  The $31.02
        difference is budget that was never spent and must not be booked.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id

            resp = _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.DONE),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(StatusEnum.DONE)
            assert reloaded.actual_amount == Decimal("48.98")
            assert reloaded.estimated_amount == Decimal("80.00")
            # effective_amount = COALESCE(actual, estimated) = 48.98
            assert owned_contribution(reloaded) == Decimal("48.98")
            assert reloaded.settled_on == display_today()
            # The ledger books what the row cost, not what it budgeted.
            assert _cash_leg(txn_id, seed_user["account"].id) == Decimal(
                "-48.98",
            )

    def test_the_dropdown_and_mark_paid_book_the_same_figure(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Two controls, one card, one answer -- the whole point of the step.

        Two identically-shaped rows: one settled through the popover's Status
        dropdown, one through the grid's Mark Paid button.  Both must land on
        $48.98 with the same ledger effect.  Asserted as an EQUALITY between
        the two doors rather than against a constant, so a future change that
        moves both together still passes and a change that moves one fails.
        """
        with app.app_context():
            via_dropdown = _gas_envelope(seed_user, seed_periods_today[3])
            via_button = _gas_envelope(seed_user, seed_periods_today[4])
            dropdown_id, button_id = via_dropdown.id, via_button.id

            assert _full_edit_save(
                auth_client, via_dropdown,
                ref_cache.status_id(StatusEnum.DONE),
            ).status_code == 200
            assert auth_client.post(
                f"/transactions/{button_id}/mark-done",
            ).status_code == 200

            db.session.expire_all()
            dropdown_row = db.session.get(Transaction, dropdown_id)
            button_row = db.session.get(Transaction, button_id)
            assert owned_contribution(dropdown_row) == owned_contribution(button_row)
            assert dropdown_row.actual_amount == button_row.actual_amount
            assert dropdown_row.status_id == button_row.status_id
            assert _cash_leg(
                dropdown_id, seed_user["account"].id,
            ) == _cash_leg(button_id, seed_user["account"].id)

    def test_an_entry_less_envelope_still_books_its_budget(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Ruling **R-FJ** at the door: no purchases means the budget is spent.

        The half of finding N-230 that belongs to a DOOR.  An envelope with no
        logged purchases settles at its own $100.00 estimate here, because
        pressing Paid says the budget is finished and nothing has relocated the
        unspent money.  ``carry_forward_service`` -- which HAS relocated it,
        into the next period's row -- settles the same shape at $0.00 through
        ``settle_from_entries``, and that difference is the ruling.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Kayla's Spending Money", Decimal("100.00"),
            )
            db.session.commit()
            txn_id = txn.id

            assert _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.DONE),
            ).status_code == 200

            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert owned_contribution(reloaded) == Decimal("100.00")
            assert _cash_leg(txn_id, seed_user["account"].id) == Decimal(
                "-100.00",
            )

    def test_archiving_a_paid_row_does_not_re_settle_it(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """``Paid -> Settled`` stays inside the band, so it is not a settle.

        The control for ``enters_settled_band``'s second half.  A row already
        settled has an amount that is a FACT and an immutable status, so
        routing this transition to the settle verb would ask the envelope
        branch to re-price a row it refuses by precondition.  The archive must
        move the status and nothing else -- including the settle DAY, which
        must not jump to the day the user archived it.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200

            db.session.expire_all()
            paid = db.session.get(Transaction, txn_id)
            settled_day = paid.settled_on

            resp = _full_edit_save(
                auth_client, paid, ref_cache.status_id(StatusEnum.SETTLED),
                settled_on=settled_day.isoformat(),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            archived = db.session.get(Transaction, txn_id)
            assert archived.status_id == ref_cache.status_id(
                StatusEnum.SETTLED,
            )
            assert archived.actual_amount == Decimal("48.98")
            assert archived.settled_on == settled_day


class TestTheFieldWritesFlushInsideTheExceptionNet:
    """The derived-amount guard reads a LAZY relationship, so it FLUSHES.

    ``settles_from_entries`` resolves ``tracks_purchases``, which for a
    template-linked row is ``self.template.is_envelope`` -- a default
    ``lazy="select"`` relationship (``models/transaction.py:324``).  Reading it
    emits a SELECT, and a SELECT autoflushes the ``setattr`` loop's staged
    mutations as the version-pinned UPDATE.  That made the request's FIRST flush
    happen above the handler's own exception net and, worse, before
    ``is_override`` was written.

    Found by adversarial review after the step had shipped, and the comment it
    contradicted is the tell: the handler claimed its three excepts "cover the
    WHOLE tail", which was true of the tail while the first flush had moved
    above it.
    """

    def test_a_period_move_carrying_an_actual_does_not_500(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Moving a generated row into an already-populated period still works.

        ``idx_transactions_template_period_scenario`` is UNIQUE over
        ``(template, period, scenario)`` but only ``WHERE is_override = FALSE``
        -- so ``is_override = True`` is exactly what makes this move legal into
        a period the recurrence engine has already filled, which is every future
        period.  Written before the flush, the row leaves the index predicate
        and the move commits; written after it, the flush lands while the row is
        still inside the predicate and PostgreSQL rejects it as an
        ``IntegrityError`` raised above the net -- an unhandled 500 on an
        ordinary edit.

        The submitted ``actual_amount`` is what reaches the lazy load at all:
        the guard short-circuits on ``data.get("actual_amount") is not None``,
        and the popover prefills that box from the stored figure, so any row
        carrying an actual submits one on every Save.

        Shown to FIRE: moving ``is_override`` back below the guard raises
        ``IntegrityError`` out of the handler.
        """
        with app.app_context():
            source = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("300.00"),
            )
            source.template.is_envelope = False
            source.actual_amount = Decimal("45.00")
            # The destination period already holds this template's generated
            # row, non-override -- the state every future period is in.
            occupant = Transaction(
                template_id=source.template_id,
                pay_period_id=seed_periods_today[4].id,
                scenario_id=source.scenario_id,
                account_id=source.account_id,
                status_id=source.status_id,
                name=source.name,
                category_id=source.category_id,
                transaction_type_id=source.transaction_type_id,
                estimated_amount=Decimal("300.00"),
            )
            db.session.add(occupant)
            db.session.commit()
            source_id = source.id

            resp = _full_edit_save(
                auth_client, source,
                ref_cache.status_id(StatusEnum.PROJECTED),
                pay_period_id=str(seed_periods_today[4].id),
                actual_amount="45.00",
            )

            assert resp.status_code == 200, resp.data[:300]
            db.session.expire_all()
            moved = db.session.get(Transaction, source_id)
            assert moved.pay_period_id == seed_periods_today[4].id
            assert moved.is_override is True
            assert moved.actual_amount == Decimal("45.00")


class TestARevertTakesBackWhatTheSettleDerived:
    """A settle writes an envelope's actual; leaving the band takes it back.

    The seam already clears ``settled_on`` on the way out of the settled band.
    ``actual_amount`` on an envelope is the same kind of value -- ``sum(entries)``
    at the moment of the settle, written by ``settle_from_entries`` in one
    statement with the status and the day -- and was being left behind.

    **Production row 2281 *Groceries* is that state today**: Projected, carrying
    ``actual_amount = 533.08`` against a `$500.00` budget from a settle that was
    later reverted, so it projects at its SPEND rather than its budget.  Plan
    step X-ap made that figure UNREACHABLE as well as wrong -- the popover stops
    rendering an Actual box for a row whose amount the settle derives -- so the
    release is what keeps the removal of that box honest.
    """

    def test_reverting_an_envelope_releases_its_derived_actual(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Settle, then revert: the row is back to projecting its budget.

        $80.00 budget, one $48.98 purchase.  The settle books $48.98; the
        revert releases it, so the row projects at $80.00 again -- which is what
        Projected means for an envelope that has not finished being spent.

        Shown to FIRE: without the release the reverted row still reads $48.98.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id
            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
            ).status_code == 200

            db.session.expire_all()
            paid = db.session.get(Transaction, txn_id)
            assert paid.actual_amount == Decimal("48.98")

            resp = _full_edit_save(
                auth_client, paid, ref_cache.status_id(StatusEnum.PROJECTED),
            )

            assert resp.status_code == 200
            db.session.expire_all()
            reverted = db.session.get(Transaction, txn_id)
            assert reverted.actual_amount is None
            assert reverted.settled_on is None
            assert owned_contribution(reverted) == Decimal("80.00")
            # The settle's postings reverse with it: nothing is left booked.
            assert _cash_leg(txn_id, seed_user["account"].id) == Decimal("0.00")

    def test_a_BILLs_hand_typed_actual_survives_a_revert(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Only the DERIVED kind is released, and this is the control for it.

        A bill's ``actual_amount`` is a figure a HUMAN read off a statement
        (ruling **R-FB**).  Clearing that on a revert would delete the user's
        own correction -- so the release is gated on the same predicate the
        settle branches on and the edit doors offer a box on.

        A $500.00 bill corrected to $245.32 keeps $245.32 through the revert.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Electricity", Decimal("500.00"),
            )
            txn.template.is_envelope = False
            db.session.commit()
            txn_id = txn.id

            assert auth_client.post(
                f"/transactions/{txn_id}/mark-done",
                data={"actual_amount": "245.32"},
            ).status_code == 200
            db.session.expire_all()
            assert db.session.get(
                Transaction, txn_id,
            ).actual_amount == Decimal("245.32")

            paid = db.session.get(Transaction, txn_id)
            assert _full_edit_save(
                auth_client, paid, ref_cache.status_id(StatusEnum.PROJECTED),
            ).status_code == 200

            db.session.expire_all()
            assert db.session.get(
                Transaction, txn_id,
            ).actual_amount == Decimal("245.32")


class TestASettledStatusMustMatchTheRowsType:
    """Income settles as Received, an expense as Paid -- never the other one."""

    def test_an_expense_asked_to_settle_as_received_is_refused(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The state machine admits it; the type rule does not.

        ``state_machine``'s transaction map allows Projected -> Received
        because it grades the STATUS and never sees ``transaction_type_id``.
        Without this refusal the verb would substitute Paid silently and the
        row would land in a status the user did not pick.

        Shown to FIRE: deleting the refusal returns 200 with the row Paid.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id

            resp = _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.RECEIVED),
            )

            assert resp.status_code == 400
            assert b"settles as Paid" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )
            assert reloaded.actual_amount is None

    def test_the_dropdown_still_offers_the_ARCHIVE_from_a_paid_row(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Narrowing the offer set must not take the archive with it.

        ``Settled`` is in the settled BAND but it is not a TYPE-specific
        status: both an expense and an income row reach it, from Paid and from
        Received respectively.  A narrowing keyed on the whole band therefore
        removes it from every row's dropdown -- and the dropdown is the only
        control that offers the archive at all, so the transition becomes
        unreachable while ``state_machine`` still calls it legal and the seam
        still preserves the settle day across it.

        The regression this pins was shipped and caught by review: two
        assertions in this same PR -- ``is_archived``'s docstring and
        ``test_entry_service``'s -- both state that the dropdown offers Settled
        from Paid, which is what made it visible as a mistake rather than a
        decision.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            assert auth_client.post(
                f"/transactions/{txn.id}/mark-done",
            ).status_code == 200
            db.session.expire_all()
            paid = db.session.get(Transaction, txn.id)

            offerable = transaction_service.offerable_status_ids(paid)
            names = {ref_cache.status_id(m): m.value for m in StatusEnum}
            legal = allowed_transitions(paid)
            shown = (
                f"status={names[paid.status_id]} "
                f"legal={sorted(names[i] for i in legal)} "
                f"offered={sorted(names[i] for i in offerable)}"
            )

            assert ref_cache.status_id(StatusEnum.SETTLED) in offerable, shown
            assert ref_cache.status_id(StatusEnum.PROJECTED) in offerable
            assert ref_cache.status_id(StatusEnum.DONE) in offerable
            assert ref_cache.status_id(StatusEnum.RECEIVED) not in offerable

    def test_the_dropdown_does_not_offer_the_mismatched_status(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The pre-hint disables what the refusal would reject.

        Grid audit D2's rule ("options the state machine would reject render
        disabled, so an illegal transition cannot be picked instead of failing
        as a 400 after Save"), completed: the state machine's own answer is
        type-blind, so Received was offered on every expense row.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            offerable = transaction_service.offerable_status_ids(txn)

            assert ref_cache.status_id(StatusEnum.DONE) in offerable
            assert ref_cache.status_id(StatusEnum.RECEIVED) not in offerable
            assert ref_cache.status_id(StatusEnum.CANCELLED) in offerable


class TestTheActualBoxExistsOnlyWhereTheSettleHonoursIt:
    """Ruling **R-FF** on the popover: correctable iff the MANUAL branch."""

    def test_an_envelope_with_purchases_renders_no_actual_input(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The box the settle would ignore is not drawn.

        The reconcile panel already obeys this rule (``is_correctable``); this
        is the second door that offers an amount box.  A box whose figure the
        verb discards takes the user's typed number and drops it, silently, on
        a screen whose whole job is entering the true one.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")

            assert resp.status_code == 200
            assert b'name="actual_amount"' not in resp.data
            assert b"Actual comes from the purchases below" in resp.data

    def test_an_entry_less_envelope_still_renders_it(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """Correctable is about the BRANCH, not about being an envelope.

        An envelope with no purchases takes the verb's MANUAL branch, so a
        figure typed here IS honoured -- and the reconcile panel offers the
        same row a correction box for the same reason.  Removing the input for
        every tracked row would have made the two doors disagree again, one
        step after making them agree.
        """
        with app.app_context():
            txn = create_envelope_txn(
                seed_user, db.session, seed_periods_today[3],
                "Kayla's Spending Money", Decimal("100.00"),
            )
            db.session.commit()

            resp = auth_client.get(f"/transactions/{txn.id}/full-edit")

            assert resp.status_code == 200
            assert b'name="actual_amount"' in resp.data

    def test_a_submitted_actual_on_a_derived_row_is_refused(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """The stale-form and crafted-request backstop, and it books nothing.

        A form rendered before a purchase was added still carries the input.
        Submitting $60.00 against a row whose purchases sum to $48.98 must not
        be written and then silently overwritten by the settle.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id

            resp = _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.DONE),
                actual_amount="60.00",
            )

            assert resp.status_code == 400
            assert b"comes from the purchases" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(Transaction, txn_id)
            assert reloaded.status_id == ref_cache.status_id(
                StatusEnum.PROJECTED,
            )
            assert reloaded.actual_amount is None

    def test_an_empty_actual_box_is_not_a_figure(
        self, app, db, auth_client, seed_user, seed_periods_today,
    ):
        """An empty input states no amount, so it is not refused.

        ``actual_amount`` is ``allow_none``, so an empty box loads as an
        explicit ``None`` rather than being dropped.  Refusing that would break
        every save from a legacy form on an envelope row.
        """
        with app.app_context():
            txn = _gas_envelope(seed_user, seed_periods_today[3])
            txn_id = txn.id

            resp = _full_edit_save(
                auth_client, txn, ref_cache.status_id(StatusEnum.DONE),
                actual_amount="",
            )

            assert resp.status_code == 200
            db.session.expire_all()
            assert db.session.get(
                Transaction, txn_id,
            ).actual_amount == Decimal("48.98")
