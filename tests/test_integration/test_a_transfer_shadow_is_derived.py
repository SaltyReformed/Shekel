"""Integration: a transfer shadow's amount is its parent's, structurally.

Plan step **X-au-g-2c-2**, rulings **R-FI**, **R-IN** and **R-IO**.

**Transfer Invariant 3 was MAINTAINED and is now STRUCTURAL.**  A shadow held a
COPY of its parent's figure, and two hand-written repairs kept the copy true --
``transfer_service._update``'s propagation and ``_restore``'s drift corrector,
which logged a warning and rewrote the copies that got away.  A shadow declares
``PARENT_TRANSFER`` and stores no figure at all now, so the two cannot disagree:
``ck_transactions_amount_ownership`` refuses a shadow that holds both, and the
value is READ rather than copied.

**Ruling R-IN (developer, 2026-09-01)** is why this covers EVERY transfer
shadow rather than loan payments alone: a loan payment's shadow IS a transfer
shadow and both declare the same relation, so the narrow version needed four
conditionals and a writer on the loan-settings routes that the wide one does
not need at all.

**Ruling R-IO (developer, 2026-09-01)** is the other half: *the figure the owner
types always wins*, and it must reach the P&I / escrow split as the cash, so an
under- or overpayment is allocated against what was really paid.

On production this moved ``$0.00``: measured 2026-09-01 at stamp
``a4c6f1d92b73``, all 350 shadows already equalled their parent to the cent and
``budget.loan_payment_settings`` held zero rows, so the loan arm is graded here
on a seeded mortgage.
"""

from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services import loan_ledger, transfer_service
from app.services.amount_ownership import owns_its_amount
from app.services.cash_ledger import (
    amount_basis,
    display_amounts_by_id,
    pricing_load_options,
)
from tests._test_helpers import (
    write_past_the_amount_seam,
    capture_sql_statements,
    create_transfer,
    shadow_amount,
)
from tests.test_integration.test_transfer_settle_freeze import (
    _derived_loan_transfer,
    _shadows,
)
from app.services.amount_ownership import state_own_amount

#: P&I 1,199.10 + escrow 300.00 on the seeded $200k / 6% / 360mo mortgage.
_CONTRACT = Decimal("1499.10")
#: What the owner types instead -- $174.10 SHORT of the installment.
_TYPED = Decimal("1325.00")
#: 200,000 * 0.06 / 12, the first installment's interest.
_INTEREST = Decimal("1000.00")
_ESCROW = Decimal("300.00")


def _plain_pair(seed_user, seed_periods, amount="250.00"):
    """A checking-to-savings transfer and both its legs -- no loan behind it."""
    from app.services import account_service  # pylint: disable=import-outside-toplevel

    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name="Sinking Fund",
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    _db.session.flush()
    xfer = create_transfer(
        seed_user, _db.session, seed_user["account"], savings,
        seed_periods[0], amount=Decimal(amount),
    )
    _db.session.flush()
    return xfer, _shadows(xfer.id)


class TestAShadowIsBornDerived:
    """The declaration is made by the constructor, not by a later writer."""

    def test_both_legs_declare_their_parent_and_store_nothing(
        self, app, db, seed_user, seed_periods,
    ):
        """``create_transfer`` births two derived legs and one owning parent.

        Three assertions per leg and they are not redundant: the DECLARATION
        says which relation prices it, the EMPTY column says it holds no copy,
        and the VALUE says the two together still answer the transfer's figure.
        A build that declared without emptying would fail the second and be
        refused by the CHECK; one that emptied without declaring would fail the
        first and refuse to resolve at all.
        """
        with app.app_context():
            xfer, legs = _plain_pair(seed_user, seed_periods)

            assert xfer.amount == Decimal("250.00")
            assert owns_its_amount(xfer) is True
            for leg in legs:
                assert leg.estimated_amount is None
                assert leg.amount_source_id == ref_cache.amount_source_id(
                    AmountSourceEnum.PARENT_TRANSFER,
                )
                assert shadow_amount(leg) == Decimal("250.00")

    def test_a_leg_cannot_hold_a_figure_while_it_declares_a_parent(
        self, app, db, seed_user, seed_periods,
    ):
        """The drift the deleted corrector repaired is unconstructible.

        This is the whole of "structural rather than maintained": there is no
        window -- soft-deleted, mid-edit or otherwise -- in which a leg's figure
        can disagree with its transfer, because the pair cannot be written.

        **The rival figure is written past the MAPPING since plan step
        X-au-k**, and it has to be: the pair is one attribute now, so
        ``state_own_amount`` would release the leg's declaration and produce a
        leg that legitimately owns the figure -- a different row, and not the
        drifted one this case is about. Reaching the private column is the only
        way left to construct the drift, and the database still refuses it.
        """
        with app.app_context():
            _xfer, legs = _plain_pair(seed_user, seed_periods)

            write_past_the_amount_seam(legs[0], Decimal("999.00"))
            # ``match`` names the constraint, because the constraint IS the
            # claim: a bare ``IntegrityError`` would be satisfied by an FK, a
            # NOT NULL or a unique index just as well.
            with pytest.raises(
                sqlalchemy.exc.IntegrityError,
                match="ck_transactions_amount_ownership",
            ):
                db.session.flush()


class TestWhoOwnsTheFigureAfterAnEdit:
    """Ruling R-IO at the one door that states a transfer's amount."""

    def test_a_definition_driven_amount_moves_both_legs_and_writes_neither(
        self, app, db, seed_user, seed_periods,
    ):
        """An amount stated without ``is_override`` leaves both legs derived.

        The recurrence maintain pass sends exactly this -- the definition's
        figure, no ownership claim -- and it must not un-declare anything.  The
        legs follow the new figure because they READ it, which is the copy and
        its two repairs replaced by one arrow.
        """
        with app.app_context():
            xfer, _legs = _plain_pair(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id, amount=Decimal("400.00"),
            )
            db.session.commit()

            for leg in _shadows(xfer.id):
                assert leg.estimated_amount is None
                assert owns_its_amount(leg) is False
                assert shadow_amount(leg) == Decimal("400.00")

    def test_an_owner_stated_amount_makes_both_legs_own_it(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_override`` in the same call takes the figure back for the pair.

        Ruling **R-IO**: the figure a human types always wins.  The door
        translates the caller's own statement into the model's column -- it does
        not READ the stored flag to decide a price, which is finding N-262's
        rule; ``is_override`` here is the kwarg this call carried.
        """
        with app.app_context():
            xfer, _legs = _plain_pair(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount=Decimal("400.00"), is_override=True,
            )
            db.session.commit()

            for leg in _shadows(xfer.id):
                assert leg.estimated_amount == Decimal("400.00")
                assert owns_its_amount(leg) is True
                assert shadow_amount(leg) == Decimal("400.00")

    def test_clearing_the_flag_hands_a_taken_leg_back_with_no_new_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_override=False`` alone re-declares both legs.

        The conflict resolver's *use the definition* action sends exactly this
        -- the flag cleared, and an amount only when the caller has a new one
        (``transfer_recurrence.resolve_conflicts``).  The behaviour this
        replaces resumed deriving the moment the flag cleared, because pricing
        READ the flag; ownership is declared now, so somebody has to write it
        back, and this is the case that says who.

        **A first draft of ``apply_amount_ownership`` ran only under
        ``"amount" in updates`` and did not reach this at all**, leaving both
        legs owning the figure they had been frozen at -- for ever, since no
        later act would look at them again.  That is the drift this step exists
        to make unconstructible, reintroduced by the step itself.
        """
        with app.app_context():
            xfer, _legs = _plain_pair(seed_user, seed_periods)
            user_id = seed_user["user"].id

            transfer_service.update_transfer(
                xfer.id, user_id, amount=Decimal("400.00"), is_override=True,
            )
            db.session.commit()
            assert all(
                owns_its_amount(leg) for leg in _shadows(xfer.id)
            )

            transfer_service.update_transfer(
                xfer.id, user_id, is_override=False,
            )
            db.session.commit()

            for leg in _shadows(xfer.id):
                assert leg.estimated_amount is None
                assert owns_its_amount(leg) is False
                assert shadow_amount(leg) == Decimal("400.00")

    def test_a_bare_period_move_does_NOT_freeze_a_legs_derivation(
        self, app, db, seed_user, seed_periods,
    ):
        """``is_override=True`` with no amount leaves both legs deriving.

        A period move sets the flag too -- carry-forward and the transfers page
        both do it -- and the flag froze a leg's live figure against every later
        derivation, which is finding **N-238**'s recorded exposure.  It cannot
        here: ownership is DECLARED, and a move states no figure to own.

        The loan is the shape where it is visible, because a leg's derivation is
        not its parent's amount there.  The escrow is raised after the move: a
        frozen leg would still read ``$1,499.10``.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                pay_period_id=seed_periods[1].id,
                is_override=True,
            )
            db.session.commit()

            for leg in _shadows(xfer.id):
                assert owns_its_amount(leg) is False

            # The VERSION carries the figure, not the line: ``EscrowLine`` has
            # no ``annual_amount`` at all, so writing one there sets an unmapped
            # attribute and changes nothing -- which is how a first draft of
            # this case "passed".
            version = (
                db.session.query(EscrowComponentVersion)
                .join(EscrowLine, EscrowLine.id == EscrowComponentVersion.line_id)
                .filter(EscrowLine.account_id == loan_id)
                .one()
            )
            version.annual_amount = Decimal("4800.00")
            db.session.commit()

            # P&I 1,199.10 + the NEW escrow 400.00.  A frozen leg reads the old
            # 1,499.10, so this figure is what says the derivation survived.
            assert {shadow_amount(leg) for leg in _shadows(xfer.id)} == {
                Decimal("1599.10"),
            }

    def test_a_bare_period_move_does_not_TAKE_a_leg_back_either(
        self, app, db, seed_user, seed_periods,
    ):
        """A move states no figure, so it changes no ownership in EITHER direction.

        The complement of the case above, and the one that cost money in a
        draft.  ``carry_forward_service`` moves a transfer with
        ``is_override=True`` and NO amount; an earlier
        ``apply_amount_ownership`` re-declared both legs whenever it was reached
        without a figure, so carrying a hand-priced payment forward silently
        handed it back to its parent.

        On a plain transfer that is invisible -- the leg's derivation IS its
        parent's amount -- so the shape that shows it is a DERIVE-mode loan
        payment, where handing back means reverting ``$1,325.00`` to the
        contract's ``$1,499.10``.  ``$174.10`` of a real payment, discarded by a
        period move.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id

            transfer_service.update_transfer(
                xfer.id, user_id, amount=_TYPED, is_override=True,
            )
            db.session.commit()
            assert {shadow_amount(leg) for leg in _shadows(xfer.id)} == {_TYPED}

            transfer_service.update_transfer(
                xfer.id, user_id,
                pay_period_id=seed_periods[1].id,
                is_override=True,
            )
            db.session.commit()

            for leg in _shadows(xfer.id):
                assert owns_its_amount(leg) is True
                assert leg.estimated_amount == _TYPED
                assert shadow_amount(leg) == _TYPED

    def test_the_FORM_s_period_move_echoes_the_amount_and_takes_nothing(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The BROWSER's payload, not a hand-picked one -- and it is the arm that bites.

        **The two cases above call the service with the CARRY-FORWARD payload**
        (``pay_period_id`` and ``is_override``, no ``amount``), and an
        adversarial review of this step pointed out that no browser sends that.
        ``routes/transfers/mutations.py`` records as a MEASURED fact that "this
        form renders the Amount input on every editable row and an HTML form
        posts every input it renders", and it raises ``is_override`` when the
        amount changed **or the PERIOD did** -- so a period-only save arrives
        with the amount ECHOED BACK UNCHANGED beside a raised flag.

        Taking ownership on the flag alone would convert this derive-mode loan
        payment's two legs to OWN, frozen for ever at ``transfers.amount`` --
        the stale creation-time ``$1.00``, against a ``$1,499.10`` installment.
        That is finding **N-238**'s exposure being ADDED by the step whose
        docstring claims to remove it, and it is why ownership requires the
        stated figure to have MOVED rather than merely to have been posted.

        `feedback_a_route_test_must_post_what_the_template_emits`: a payload
        assembled by hand grades an arm a browser never takes.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            assert {shadow_amount(leg) for leg in _shadows(xfer_id)} == {
                _CONTRACT,
            }

            # Every field the edit form renders for an editable row, with the
            # amount echoed at its CURRENT value and only the period moved.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(xfer.amount),
                    "pay_period_id": str(seed_periods[1].id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            # The PREMISE of this case: the period really did move.  Without
            # it, a regression that silently dropped ``pay_period_id`` would
            # leave the case green while it graded a no-op save.
            assert db.session.get(Transfer, xfer_id).pay_period_id == (
                seed_periods[1].id
            )
            assert all(
                leg.pay_period_id == seed_periods[1].id
                for leg in _shadows(xfer_id)
            )
            for leg in _shadows(xfer_id):
                assert owns_its_amount(leg) is False, (
                    "an echoed amount is not an authored one"
                )
                assert leg.estimated_amount is None
                assert shadow_amount(leg) == _CONTRACT

    def test_the_FORMs_period_move_does_not_HAND_BACK_a_taken_leg(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """PROBE: the intersection neither period-move case above covers.

        ``test_a_bare_period_move_does_not_TAKE_a_leg_back_either`` starts from
        a TAKEN leg but sends the CARRY-FORWARD payload (no ``amount``).
        ``test_the_FORM_s_period_move_echoes_the_amount_and_takes_nothing``
        sends the BROWSER's payload but starts from a DERIVED leg.  Neither
        grades a taken leg moved by the form.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

            # 1. The owner types $1,325.00 through the real form.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(_TYPED),
                    "pay_period_id": str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            for leg in _shadows(xfer_id):
                assert owns_its_amount(leg) is True, "the typed figure is owned"
                assert shadow_amount(leg) == _TYPED

            # 2. The owner moves ONLY the period, through the same form -- so
            #    the amount box echoes the $1,325.00 it now renders.
            xfer = db.session.get(Transfer, xfer_id)
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(xfer.amount),
                    "pay_period_id": str(seed_periods[1].id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            # The PREMISE of this case: the period really did move.  Without
            # it, a regression that silently dropped ``pay_period_id`` would
            # leave the case green while it graded a no-op save.
            assert db.session.get(Transfer, xfer_id).pay_period_id == (
                seed_periods[1].id
            )
            assert all(
                leg.pay_period_id == seed_periods[1].id
                for leg in _shadows(xfer_id)
            )
            for leg in _shadows(xfer_id):
                assert shadow_amount(leg) == _TYPED, (
                    "a period move must not revert the owner's typed figure "
                    "to the contract"
                )
                assert owns_its_amount(leg) is True

    def test_an_UNRELATED_field_save_does_not_HAND_BACK_a_taken_leg(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """PROBE: the echo WITHOUT the flag -- the door the period move does not use.

        The route raises ``is_override`` only on an amount or period DELTA
        (``mutations.py``), so a save that changes NOTES, CATEGORY or STATUS
        carries the echoed amount and NO flag at all.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(_TYPED),
                    "pay_period_id": str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data
            db.session.expire_all()
            for leg in _shadows(xfer_id):
                assert shadow_amount(leg) == _TYPED

            # A NOTES-only save: same amount, same period, same status.
            xfer = db.session.get(Transfer, xfer_id)
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(xfer.amount),
                    "pay_period_id": str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "escrow went up in March",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            for leg in _shadows(xfer_id):
                assert shadow_amount(leg) == _TYPED, (
                    "a notes-only save must not revert the owner's figure"
                )
                assert owns_its_amount(leg) is True

    def test_a_later_definition_write_hands_a_taken_leg_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The re-declaration is what makes drift unconstructible over time.

        A leg an owner took once must not be left behind at a stale figure when
        its definition later moves.  Without the ``else`` arm in
        ``_apply_amount`` this pair would read ``$400.00`` for ever while the
        transfer said ``$500.00`` -- the exact drift the deleted corrector
        existed to repair, reintroduced by the step that removed it.
        """
        with app.app_context():
            xfer, _legs = _plain_pair(seed_user, seed_periods)
            user_id = seed_user["user"].id

            transfer_service.update_transfer(
                xfer.id, user_id, amount=Decimal("400.00"), is_override=True,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, user_id, amount=Decimal("500.00"),
            )
            db.session.commit()

            for leg in _shadows(xfer.id):
                assert leg.estimated_amount is None
                assert owns_its_amount(leg) is False
                assert shadow_amount(leg) == Decimal("500.00")


class TestALoanPaymentsLegsReadTheLoan:
    """Rule 4 through the declaration, with no read-time repair in front."""

    def test_both_legs_are_worth_the_contract_not_the_stored_figure(
        self, app, db, seed_user, seed_periods,
    ):
        """The parent's stale ``$1.00`` is not what either leg is worth.

        The fixture's stored ``default_amount`` is a deliberately stale
        ``$1.00``, so a leg worth ``$1.00`` is one the loan did not price.  Both
        legs, because the cutover declares both -- a version declaring only the
        checking side would leave the loan-side income leg answering the
        parent, and that leg is the one the payment feed reads.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            legs = _shadows(xfer.id)
            basis = amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert xfer.amount == Decimal("1.00")
            priced = display_amounts_by_id(legs, basis)
            assert set(priced.values()) == {_CONTRACT}
            for leg in legs:
                assert leg.estimated_amount is None

    def test_a_typed_figure_beats_the_contract_and_reaches_the_split(
        self, app, db, seed_user, seed_periods,
    ):
        """Ruling **R-IO**, end to end, on an UNDERPAYMENT.

        The owner types ``$1,325.00`` over a ``$1,499.10`` contractual
        installment and marks it Paid in one save -- the shape the transfers
        page sends, which auto-sets ``is_override`` when the amount box moves.

        Two things must hold and the second is what the ruling added.  The
        settle books what the owner typed, not the contract: booking
        ``$1,499.10`` would report a short payment as exactly on schedule and
        lose the ``$174.10``.  And that figure is the CASH the split allocates,
        so the loan's own books show the shortfall where it fell --
        interest ``$1,000.00`` (200,000 * 0.06 / 12) and escrow ``$300.00`` are
        taken in full, and principal absorbs the rest at
        ``1,325.00 - 1,000.00 - 300.00 = 25.00`` instead of the ``$199.10`` a
        full installment would have paid down.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id
            scenario_id = seed_user["scenario"].id

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount=_TYPED,
                is_override=True,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            for leg in _shadows(xfer.id):
                assert leg.settled_amount == _TYPED

            splits = loan_ledger.compute_loan_payment_splits(
                loan_id, scenario_id,
            )
            assert len(splits) == 1
            split = splits[0]
            assert split.interest == _INTEREST
            assert split.escrow == _ESCROW
            assert split.principal == _TYPED - _INTEREST - _ESCROW
            assert (
                split.interest + split.escrow + split.principal + split.excess
                == _TYPED
            )


class TestAnOwnerTypedFigureShowsBeforeItSettles:
    """R-IO on the PROJECTED row, not only through the settle."""

    def test_the_grid_shows_the_typed_figure_not_the_contract(
        self, app, db, seed_user, seed_periods,
    ):
        """What a screen shows for a hand-priced loan payment is the owner's figure.

        ``test_a_typed_figure_beats_the_contract_and_reaches_the_split`` grades
        ruling **R-IO** through the SETTLE, and an adversarial review pointed
        out that leaves the projected display ungraded: a regression that wrote
        both legs correctly but left ``amount_rule`` still classifying them
        ``LOAN_PAYMENT`` would book the right figure and show the wrong one for
        every day between the edit and the settle.

        ``display_amounts_by_id`` is what the grid publishes (ruling **R-Q**),
        so it is what is asked here.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            legs = _shadows(xfer.id)
            basis = amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert set(display_amounts_by_id(legs, basis).values()) == {
                _CONTRACT,
            }

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount=_TYPED, is_override=True,
            )
            db.session.commit()

            fresh = amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            shown = display_amounts_by_id(_shadows(xfer.id), fresh)
            assert set(shown.values()) == {_TYPED}


class TestAModeFlipRewritesNoRow:
    """Why ruling R-IN's wider scope needs no writer on the loan routes."""

    def test_track_payment_changes_what_the_legs_are_worth_without_touching_them(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """Flipping a MANUAL payment to auto-track re-prices its existing rows.

        **This is the case that decided the scope** (ruling **R-IN**).  The loan
        dashboard's one-click "track the contract" creates or flips the
        ``loan_payment_settings`` row on a definition whose transfers are
        already generated.  Under the narrower design -- only loan-payment
        shadows declared -- those legs would still have been OWNING a stale
        figure at that moment, so the route would have needed a writer to
        declare them, and the button's own docstring promise ("no shadow
        regeneration is needed") would have become false.

        Every transfer shadow is derived from birth, so the flip writes no row
        at all: what changes is which RULE prices them, read live off the
        template (ruling **R-FK**).

        **The ``updated_at`` assertion below is NOT the measurement a draft of
        this docstring claimed it was**, and saying so beats leaving a stronger
        claim standing.  It named the mutation "a writer added to
        ``track_payment`` to declare the legs" -- but that writer is
        ``declare_derived``, which would set ``estimated_amount`` to the ``None``
        it already holds and the source to the id it already holds.  SQLAlchemy
        emits no UPDATE for a net-zero attribute history, so ``updated_at``'s
        ``onupdate`` never fires and the assertion passes.  It can only catch a
        writer that changes a VALUE, which is a weaker and different thing.

        What the case does hold: the legs' declaration is unchanged across the
        flip, and what they are WORTH moves from the parent's stale ``$1.00`` to
        the loan's ``$1,499.10`` -- so the route reprices without a writer, which
        is the scope argument (**R-IN**) itself.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id
            xfer.template.settings.derive_from_loan = False
            db.session.commit()

            before = _shadows(xfer.id)
            assert {shadow_amount(leg) for leg in before} == {Decimal("1.00")}
            stamps = {leg.id: (leg.amount_source_id, leg.updated_at)
                      for leg in before}

            resp = auth_client.post(f"/accounts/{loan_id}/loan/track-payment")
            assert resp.status_code in (200, 302), resp.data

            db.session.expire_all()
            after = _shadows(xfer.id)
            assert {shadow_amount(leg) for leg in after} == {_CONTRACT}
            for leg in after:
                assert leg.estimated_amount is None
                assert (leg.amount_source_id, leg.updated_at) == stamps[leg.id]


class TestTheAmountModelsOwnEagerLoad:
    """``pricing_load_options`` is what keeps a derived row set off an N+1."""

    def test_a_loaded_row_set_prices_with_no_further_query(
        self, app, db, seed_user, seed_periods,
    ):
        """Pricing legs loaded WITH the options issues no statement at all.

        The direct control on the claim the options carry: before this step a
        shadow OWNED its figure, so ``amount_rule`` answered from the column and
        touched no relationship; a shadow walks ``transfer -> template ->
        settings`` now, once per row, on the busiest surfaces in the app.

        Zero rather than "fewer", because zero is the claim: everything the five
        rules read is already in the identity map, so a rule that started
        reading an UNLISTED relationship would show up here as a statement
        rather than as a slow page nobody measures.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            scenario_id = seed_user["scenario"].id
            db.session.commit()
            db.session.expire_all()

            legs = (
                db.session.query(Transaction)
                .options(*pricing_load_options())
                .filter(
                    Transaction.transfer_id.isnot(None),
                    Transaction.scenario_id == scenario_id,
                )
                .all()
            )
            assert len(legs) >= 2
            basis = amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            # The LOAN resolve is a query and is not what this measures, so it
            # is paid once outside the capture.
            display_amounts_by_id(legs[:1], basis)

            priced, statements = capture_sql_statements(
                lambda: display_amounts_by_id(legs, basis),
            )

            assert statements == [], (
                "pricing a loaded row set must issue no query; got "
                f"{[text for text, _params in statements]}"
            )
            assert set(priced.values()) == {_CONTRACT}

    def test_without_the_options_the_same_row_set_DOES_query(
        self, app, db, seed_user, seed_periods,
    ):
        """The negative control: the options are what make the zero above.

        Without it the case would pass just as well on a set SQLAlchemy had
        already loaded for some other reason, and would keep passing if the
        options were deleted -- which is the shape a green gate that measures
        nothing takes.
        """
        with app.app_context():
            xfer, _shadow = _derived_loan_transfer(seed_user, seed_periods)
            scenario_id = seed_user["scenario"].id
            db.session.commit()
            db.session.expire_all()

            legs = (
                db.session.query(Transaction)
                .filter(
                    Transaction.transfer_id.isnot(None),
                    Transaction.scenario_id == scenario_id,
                )
                .all()
            )
            basis = amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            display_amounts_by_id(legs[:1], basis)

            _priced, statements = capture_sql_statements(
                lambda: display_amounts_by_id(legs, basis),
            )

            assert statements, (
                "an unloaded row set must reach the database for its parents"
            )
