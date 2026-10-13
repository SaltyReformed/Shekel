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

**There are no shadows to ask since plan step balance:X-bi-6-4d-3.**  A
transfer is one row, its two legs are read off it (**R-BAL13**: each worth
``resolve_transfer_amount`` on the parent), and
``ck_transactions_names_no_transfer`` refuses a row naming a transfer (ruling
**R-BAL258**).  So the step's end state is the one this module argued for, with
the copy deleted rather than derived: a claim below that was asked of each
shadow is asked of the transfer, or of its legs through the producers that
price them, with its figures unchanged (ruling **R-BAL167** class 1), and the
one case that planted a shadow -- a figure on it -- asserts the database
refuses it (class 2).  Three claims were not re-asked, and each case says so:
the two AD-HOC cases' ownership reads -- the retype case's and the
owner-stated amount case's -- which the transfer's own CHECK makes infallible
on an ad-hoc row, a refusal replacing the first; the two FORM cases' per-leg
period reads, because a leg's period IS its transfer's (``leg_of``); and the
per-leg "stores nothing", because no leg row is left to store anything.
"""

import re
from decimal import Decimal

import pytest
import sqlalchemy.exc

from app import ref_cache
from app.enums import AmountSourceEnum, StatusEnum
from app.extensions import db as _db
from app.models.escrow_line import EscrowComponentVersion, EscrowLine
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.services import account_service, loan_ledger, transfer_service
from app.services.amount_ownership import owns_its_amount
from app.services.cash_ledger import (
    derived_amount_basis,
    leg_amounts_by_key,
    transfer_pricing_load_options,
)
from app.services.transfer_legs import leg_of, planned_transfer_legs
from tests._test_helpers import (
    a_twin_of,
    assert_a_twin_is_refused,
    constraint_name_from,
    write_past_the_amount_seam,
    capture_sql_statements,
    create_transfer,
    generate_transfer_of,
    make_every_period_rule,
    open_books_before_the_first_assertion,
    transfer_amount,
    transfer_side_figure,
)
from tests.test_integration.test_transfer_settle_freeze import (
    _derived_loan_transfer,
)
from app.models.amount_ownership import AmountOwnership
from app.services.amount_ownership import declare_derived, derived_ownership
from tests._test_helpers import rendered_transfer_amount
from datetime import date
from app.services import template_amount_service

#: P&I 1,199.10 + escrow 300.00 on the seeded $200k / 6% / 360mo mortgage.
_CONTRACT = Decimal("1499.10")
#: What the owner types instead -- $174.10 SHORT of the installment.
_TYPED = Decimal("1325.00")
#: 200,000 * 0.06 / 12, the first installment's interest.
_INTEREST = Decimal("1000.00")
_ESCROW = Decimal("300.00")


def _plain_transfer(seed_user, seed_periods, amount="250.00"):
    """A checking-to-savings transfer -- no loan, no definition behind it.

    It was ``_plain_pair`` and returned the transfer AND its two shadow legs
    until plan step balance:X-bi-6-4d-3, where a transfer became one row
    (ruling **R-BAL167** class 4).
    """
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
    return xfer


class TestAShadowIsBornDerived:
    """The declaration is made by the constructor, not by a later writer.

    Since plan step balance:X-bi-6-4d-3 the constructor makes ONE row, so what
    the first case asked of the two legs it built is asked of the transfer
    (ruling **R-BAL167** class 1, with a refusal of either twin added), and
    the second, which planted a figure on a leg, asserts that row unstorable
    (class 2).
    """

    def test_the_transfer_owns_its_figure_and_neither_twin_can_be_stored(
        self, app, db, seed_user, seed_periods,
    ):
        """``create_transfer`` births one owning row, and no leg row beside it.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-3** (it was
        ``test_both_legs_declare_their_parent_and_store_nothing``; ruling
        **R-BAL167** class 1, with a refusal ADDED: it planted nothing, it read
        the two legs ``create_transfer`` made, so the twin refused below is a
        shape this case adds rather than one it planted).  It asserted three
        things per leg -- declared
        ``PARENT_TRANSFER``, stored nothing, worth the transfer's figure --
        because the leg was a ROW that could hold a copy.  No leg row is born
        now and the database refuses one on either side
        (``ck_transactions_names_no_transfer``, ruling **R-BAL258**), so the
        copy is not merely derived but absent.  What each leg is worth is the
        transfer's own resolution (**R-BAL13**), asserted as `$250.00` here
        where it was asserted per leg.  "Owns its figure" is the stored
        `$250.00` (``ck_transfers_amount_ownership`` pairs a stored figure with
        no source): an ``owns_its_amount`` read of this AD-HOC transfer could
        not fail under ``ck_transfers_adhoc_owns_amount``, so none is taken
        (the cp2c review's L-4; the AD-HOC retype case asserts that CHECK's
        refusal).
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)

            assert xfer.amount == Decimal("250.00")
            assert transfer_amount(xfer) == Decimal("250.00")
            db.session.commit()
            for is_income in (False, True):
                assert_a_twin_is_refused(
                    db.session, db.session.get(Transfer, xfer.id), is_income,
                )

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

        **Re-expressed at plan step balance:X-bi-6-4d-3** (ruling **R-BAL167**
        class 2): no leg row is born to write the figure onto, so the planted
        row is the twin :func:`~tests._test_helpers.a_twin_of` builds -- the
        shape ``_build_shadow`` built -- with the rival figure written past
        the seam.  That row breaks TWO checks, this one and
        ``ck_transactions_names_no_transfer`` (ruling **R-BAL258**).  The
        PostgreSQL ``CREATE TABLE`` reference states that a table's CHECK
        constraints are tested in alphabetical order of name, and the refusal
        reported was MEASURED as this one, on either side, before this case
        asserted it.  The same twin without the figure is refused by the other
        check (the case above), so the figure is what this check answers.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            db.session.commit()

            for is_income in (False, True):
                twin = a_twin_of(db.session.get(Transfer, xfer.id), is_income)
                write_past_the_amount_seam(twin, Decimal("999.00"))
                db.session.add(twin)
                # The constraint IS the claim: a bare ``IntegrityError`` would
                # be satisfied by an FK, a NOT NULL or a unique index just as
                # well.
                with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
                    db.session.flush()
                db.session.rollback()
                assert constraint_name_from(excinfo.value) == (
                    "ck_transactions_amount_ownership"
                ), f"the figure-carrying twin was refused by: {excinfo.value}"


def _generated_transfer(seed_user, seed_periods, series="500.00"):
    """A TEMPLATE-LINKED transfer, priced by its definition.

    It was ``_generated_pair`` and returned the transfer AND its two shadow
    legs until plan step balance:X-bi-6-4d-3, where a transfer became one row
    (ruling **R-BAL167** class 4).

    **The sibling of :func:`_plain_transfer`, and the difference is load-bearing
    rather than cosmetic since plan step X-au-f.**  An ad-hoc transfer may not
    declare a relation at all -- ``ck_transfers_adhoc_owns_amount`` refuses it,
    because nobody generated it and no definition states its price -- so a case
    whose subject is *the definition speaking* cannot be built on one.  Before
    X-au-f the definition spoke by sending a FIGURE, which an ad-hoc row could
    accept; it speaks by stating an ``AmountOwnership`` now (ruling
    **R-BAL11**), and the schema refuses that on a row with no template.  The
    three cases that used ``_plain_pair`` (now :func:`_plain_transfer`) for it
    were therefore asserting against a shape the app cannot produce.

    The price is stated through ``template_amount_service.set_amount``, the one
    write door, which is what every app-side template create calls.
    """
    savings = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            name="Sinking Fund",
            account_type_id=seed_user["account"].account_type_id,
            anchor_balance=Decimal("100.00"),
        ),
    )
    _db.session.flush()
    # RE-EXPRESSED at plan step pay_calendar:C18-a under CLAUDE.md rule 5,
    # developer-confirmed 2026-09-22 ("Yes: open books before rows"): the
    # destination's books open before the schedule, as the seeded account's
    # do.  ``create_account`` opens them TODAY, after the first paycheck this
    # case generates into, and since ruling R-PC85 a definition's occurrences
    # start above the books of every account it moves money in.
    open_books_before_the_first_assertion(_db.session, savings)
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=savings.id,
        name="Sinking Fund Contribution",
        default_amount=Decimal(series),
    )
    _db.session.add(template)
    _db.session.flush()
    template_amount_service.set_amount(
        template, Decimal(series), effective_on=date(2000, 1, 1),
    )
    # The transfer is the ENGINE's (:func:`generate_transfer_of`, plan step
    # balance:X-ch): derived, parent and both legs, on the definition's own
    # date.  A hand-built one owned its figure and was linked afterwards, a
    # shape no producer writes since X-au-f.
    make_every_period_rule(_db.session, template)
    return generate_transfer_of(template, seed_periods[0])


def _reread(xfer_id):
    """Return the transfer *xfer_id* as the database now holds it."""
    _db.session.expire_all()
    return _db.session.get(Transfer, xfer_id)


class TestWhoOwnsTheFigureAfterAnEdit:
    """Ruling R-IO at the one door that states a transfer's amount.

    **Asked of the TRANSFER since plan step balance:X-bi-6-4d-3** (ruling
    **R-BAL167** class 1).  Each case asked its two shadow legs whether they
    owned the figure, what they stored and what they were worth; a transfer is
    one row since then and both its legs are read off it (**R-BAL13**), so the
    same three questions are asked of the transfer -- ``owns_its_amount``,
    ``amount`` and :func:`~tests._test_helpers.transfer_amount` -- with every
    figure unchanged.  A leg's ownership was a COPY of the transfer's:
    ``transfer_service._amount.apply_amount_ownership`` wrote the parent's
    stated ownership onto each leg (at ``5f46d6973``), so asking the transfer
    asks what the legs copied.  The one state where the two differed is an
    AD-HOC transfer no edit that STATED an amount has touched (that function
    ran only when an ownership was stated, so a bare period move or a
    notes-only save left it too) -- it owns its figure from birth while its
    legs were born derived -- and the case that read that difference,
    ``test_an_AD_HOC_retype_lands_on_the_transfer_and_raises_no_flag``, says
    so.
    """

    def test_a_definition_driven_amount_moves_both_legs_and_writes_neither(
        self, app, db, seed_user, seed_periods,
    ):
        """The DEFINITION's ownership leaves both legs derived, reading its series.

        The recurrence maintain pass sends exactly this -- an
        ``AmountOwnership`` naming TEMPLATE, no figure and no ownership claim
        for the operator -- and it must not un-declare anything.  The legs
        follow the new price because they READ it, which is the copy and its
        two repairs replaced by one arrow.

        **The caller states no FIGURE since plan step X-au-f** (ruling
        **R-BAL11**): it sent ``amount=$400.00`` with an ``amount_authored``
        of ``False``, which said *the definition re-priced this* by handing the
        service the definition's own number to store.  The definition's number
        lives in its SERIES now, so the act is to state WHO prices the row and
        let the series answer -- and this case re-states the series to
        ``$400.00`` first, so the figure it asserts is the same one it always
        asserted and reaches the legs by the mechanism that actually carries it.
        """
        with app.app_context():
            xfer = _generated_transfer(seed_user, seed_periods)

            # The definition re-prices itself, through the one write door.
            template_amount_service.set_amount(
                xfer.template, Decimal("400.00"),
                effective_on=xfer.due_date,
            )
            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
            )
            db.session.commit()

            edited = _reread(xfer.id)
            assert edited.amount is None
            assert owns_its_amount(edited) is False
            assert transfer_amount(edited) == Decimal("400.00")

    def test_an_owner_stated_amount_is_what_the_transfer_stores_and_resolves(
        self, app, db, seed_user, seed_periods,
    ):
        """An owner-stated amount, ``is_override`` in the same call, lands on the transfer.

        Ruling **R-IO**: the figure a human types always wins.  The door
        translates the caller's own statement into the model's column -- it does
        not READ the stored flag to decide a price, which is finding N-262's
        rule; ``is_override`` here is the kwarg this call carried.  The same
        call took the figure back for both shadow legs until plan step
        balance:X-bi-6-4d-3.

        **Renamed at plan step balance:X-bi-6-4d-3's checkpoint 2d-a** (it was
        ``test_an_owner_stated_amount_makes_both_legs_own_it``, a claim about
        the legs it no longer asserts).  **Its ownership read went at
        checkpoint 2c-a** (ruling **R-BAL167** class 1, the cp2c review's L-4
        applied to this case).
        The fixture is AD-HOC, whose legs were born derived and were TAKEN by
        this statement; the transfer owns its figure from birth
        (``ck_transfers_adhoc_owns_amount``), so ``owns_its_amount`` asked of
        it could not fail.  The refusal that grades that CHECK is the AD-HOC
        retype case's, and a TAKE that changes ownership is graded on a
        template-linked transfer by
        ``test_stating_the_definitions_ownership_hands_a_taken_leg_back``.
        The `$400.00` stored and resolved stands.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(Decimal("400.00")),
                is_override=True,
            )
            db.session.commit()

            edited = _reread(xfer.id)
            assert edited.amount == Decimal("400.00")
            assert transfer_amount(edited) == Decimal("400.00")

    def test_stating_the_definitions_ownership_hands_a_taken_leg_back(
        self, app, db, seed_user, seed_periods,
    ):
        """The hand-back is an OWNERSHIP now, and the bare flag no longer is one.

        The conflict resolver's *use the definition* action sends exactly this
        (``transfer_recurrence.resolve_conflicts``).  **It sent
        ``is_override=False`` and, when it had one, a figure, until plan step
        X-au-f** -- and this case was named for that spelling.  Ruling
        **R-BAL11** replaced the three parameters with one: the resolver states
        ``derived_ownership(TEMPLATE)``, and a bare flag says nothing about the
        amount at all, because the flag means *this row is the OWNER's, not the
        rule's* (X-au-h) and no longer decides a price.

        **That is the whole of what changed, and the ACT is unchanged**: the
        pair goes back to being priced by its definition, both legs stop owning
        the figure they were frozen at, and the drift this step exists to make
        unconstructible stays unconstructible.  The behaviour this all replaces
        resumed deriving the moment the flag cleared, because pricing READ the
        flag; ownership is declared now, so somebody has to write it back, and
        this is the case that says who.

        **A first draft of ``apply_amount_ownership`` ran only under
        ``"amount" in updates`` and did not reach this at all**, leaving both
        legs owning the figure they had been frozen at -- for ever, since no
        later act would look at them again.

        The pair is TEMPLATE-linked because the act requires it: a definition
        cannot speak for an ad-hoc transfer, and
        ``ck_transfers_adhoc_owns_amount`` refuses the declaration outright.
        """
        with app.app_context():
            xfer = _generated_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id
            xfer_id = xfer.id

            transfer_service.update_transfer(
                xfer_id, user_id,
                amount_ownership=AmountOwnership.own(Decimal("400.00")),
                is_override=True,
            )
            db.session.commit()
            assert owns_its_amount(_reread(xfer_id)) is True

            # A BARE flag states nothing about the amount, so it leaves the
            # transfer exactly as it stands -- asserted, because "the
            # hand-back moved to the ownership" is only true if the old
            # spelling stopped performing it.
            transfer_service.update_transfer(
                xfer_id, user_id, is_override=False,
            )
            db.session.commit()
            assert owns_its_amount(_reread(xfer_id)) is True

            transfer_service.update_transfer(
                xfer_id, user_id,
                amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
            )
            db.session.commit()

            edited = _reread(xfer_id)
            assert edited.amount is None
            assert owns_its_amount(edited) is False
            assert transfer_amount(edited) == Decimal("500.00")

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
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id
            xfer_id = xfer.id

            transfer_service.update_transfer(
                xfer_id, seed_user["user"].id,
                pay_period_id=seed_periods[1].id,
                is_override=True,
            )
            db.session.commit()

            assert owns_its_amount(_reread(xfer_id)) is False

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

            # P&I 1,199.10 + the NEW escrow 400.00.  A frozen transfer reads
            # the old 1,499.10, so this figure is what says the derivation
            # survived.
            assert transfer_amount(_reread(xfer_id)) == Decimal("1599.10")

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
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id
            xfer_id = xfer.id

            transfer_service.update_transfer(
                xfer_id, user_id, amount_ownership=AmountOwnership.own(_TYPED),
                is_override=True,
            )
            db.session.commit()
            assert transfer_amount(_reread(xfer_id)) == _TYPED

            transfer_service.update_transfer(
                xfer_id, user_id,
                pay_period_id=seed_periods[1].id,
                is_override=True,
            )
            db.session.commit()

            moved = _reread(xfer_id)
            assert owns_its_amount(moved) is True
            assert moved.amount == _TYPED
            assert transfer_amount(moved) == _TYPED

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
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            assert transfer_amount(xfer) == _CONTRACT

            # Every field the edit form renders for an editable row, with the
            # amount echoed at its CURRENT value and only the period moved.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": rendered_transfer_amount(xfer),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(seed_periods[1].id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            # The PREMISE of this case: the period really did move.  Without
            # it, a regression that silently dropped ``pay_period_id`` would
            # leave the case green while it graded a no-op save.
            moved = db.session.get(Transfer, xfer_id)
            assert moved.pay_period_id == seed_periods[1].id
            assert owns_its_amount(moved) is False, (
                "an echoed amount is not an authored one"
            )
            assert moved.amount is None
            assert transfer_amount(moved) == _CONTRACT

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
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

            # 1. The owner types $1,325.00 through the real form.
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(_TYPED),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data
            typed_over = _reread(xfer_id)
            assert owns_its_amount(typed_over) is True, "the typed figure is owned"
            assert transfer_amount(typed_over) == _TYPED

            # 2. The owner moves ONLY the period, through the same form -- so
            #    the amount box echoes the $1,325.00 it now renders.
            xfer = db.session.get(Transfer, xfer_id)
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": rendered_transfer_amount(xfer),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(seed_periods[1].id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            # The PREMISE of this case: the period really did move.  Without
            # it, a regression that silently dropped ``pay_period_id`` would
            # leave the case green while it graded a no-op save.
            moved = db.session.get(Transfer, xfer_id)
            assert moved.pay_period_id == seed_periods[1].id
            assert transfer_amount(moved) == _TYPED, (
                "a period move must not revert the owner's typed figure "
                "to the contract"
            )
            assert owns_its_amount(moved) is True

    def test_an_UNRELATED_field_save_does_not_HAND_BACK_a_taken_leg(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """PROBE: the echo WITHOUT the flag -- the door the period move does not use.

        The route raises ``is_override`` only on an amount or period DELTA
        (``mutations.py``), so a save that changes NOTES, CATEGORY or STATUS
        carries the echoed amount and NO flag at all.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id = xfer.id

            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": str(_TYPED),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data
            assert transfer_amount(_reread(xfer_id)) == _TYPED

            # A NOTES-only save: same amount, same period, same status.
            xfer = db.session.get(Transfer, xfer_id)
            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "version_id": str(xfer.version_id),
                    "amount": rendered_transfer_amount(xfer),
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "pay_period_id":str(xfer.pay_period_id),
                    "status_id": str(xfer.status_id),
                    "notes": "escrow went up in March",
                },
            )
            assert resp.status_code == 200, resp.data

            assert transfer_amount(_reread(xfer_id)) == _TYPED, (
                "a notes-only save must not revert the owner's figure"
            )

    def test_the_LEG_door_echo_leaves_the_parent_alone(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """The grid's door onto a transfer's amount, echoing what it rendered.

        **Re-expressed at plan step balance:X-bi-6-1 (ruling R-BAL87).**  It
        graded the SHADOW door -- ``/transactions/<shadow>/quick-edit`` priming
        a box with the RESOLVED amount and ``PATCH /transactions/<shadow>``
        re-expressed as a transfer update -- which is deleted: the grid draws
        a transfer's LEG off its parent, and the leg's popover is the
        TRANSFER form asked with ``?leg_account_id=``, whose box and companion
        the transfer PATCH grades.  So the door the grid uses renders the SAME
        figure the transfers page's popover renders, and an untouched save
        through it -- the payload read out of the rendered fragment, which is
        the only way to grade a door whose rendered value may not be the
        column -- must leave the parent's stored amount and both legs'
        derivation alone.  On a derive-mode loan payment the contract's figure
        and the stored column differ, which is what made the old shadow door
        cost real money (it rewrote the parent to the resolved figure and
        called it a human's); the leg's door is graded on the same row.

        The leg asked from is the transfer's from-side, where it was the
        expense twin's account, and "still the contract's" is asked of the
        transfer, where it was asked of each twin, until plan step
        balance:X-bi-6-4d-3 stopped the twins being born (ruling R-BAL167
        class 1; the account is the same one).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            xfer_id, leg_account_id = xfer.id, xfer.from_account_id
            stored_before = xfer.amount

            edit = auth_client.get(
                f"/transfers/{xfer_id}/full-edit?leg_account_id={leg_account_id}",
            )
            assert edit.status_code == 200
            body = edit.data.decode()
            shown = re.search(r'name="amount"[^>]*value="([^"]*)"', body)
            companion = re.search(
                r'name="amount_as_rendered"[^>]*value="([^"]*)"', body,
            )
            assert shown is not None, "the leg's popover renders a figure"
            assert companion is not None, (
                "the leg's door owes the companion like every other door"
            )
            assert companion.group(1) == shown.group(1)
            assert f'name="leg_account_id" value="{leg_account_id}"' in body

            resp = auth_client.patch(f"/transfers/instance/{xfer_id}", data={
                "amount": shown.group(1),
                "amount_as_rendered": companion.group(1),
                "version_id": str(xfer.version_id),
                "leg_account_id": str(leg_account_id),
            })
            assert resp.status_code == 200, resp.data
            assert f'data-leg-account-id="{leg_account_id}"' in resp.data.decode()

            db.session.expire_all()
            echoed = db.session.get(Transfer, xfer_id)
            assert echoed.amount == stored_before, (
                "an echo through the leg door must not rewrite the parent"
            )
            assert owns_its_amount(echoed) is False, "still the contract's"

    def test_an_AD_HOC_retype_lands_on_the_transfer_and_raises_no_flag(
        self, app, db, auth_client, seed_user, seed_periods,
    ):
        """An ad-hoc retype lands on the transfer as typed and raises no flag.

        **Renamed and re-expressed at plan step balance:X-bi-6-4d-3** (it was
        ``test_an_AD_HOC_retype_now_TAKES_both_legs``; ruling **R-BAL167**
        class 1).  What it was written to state -- an ad-hoc retype TAKES both
        legs, where it once left them derived, the arm that changed at plan
        step X-au-h -- was a change in the legs' ROWS, and no leg row is born
        since that step.  An ad-hoc transfer owns its figure from birth and
        both legs are read off it (**R-BAL13**), so there is no derived leg
        left to take and nothing for the X-au-h arm to change.  What stands is
        the rest of the case: the retype lands on the one row as `$400.00` and
        the route raises no ``is_override`` for a row with no template.  The
        paragraphs below describe the three-row shape this case was written
        against.

        **"The row still owns it" is a REFUSAL, not an ``owns_its_amount``
        read** (ruling **R-BAL167** class 1 with a refusal ADDED, the cp2c
        review's L-4: the case planted nothing, so the refusal is a shape it
        adds, not one it planted; labelled class 2 until checkpoint 2d-a).
        ``ck_transfers_adhoc_owns_amount`` refuses an ad-hoc transfer that
        declares a derived figure, so ``owns_its_amount`` is ``True`` for every
        ad-hoc row the database holds and the two reads it took could not
        fail.  The case asserts the database refuses the other state instead,
        on the retyped row.  Where the legs were rows the precondition
        discriminated -- they were born derived beside a parent that owned
        its figure -- and that difference is what the transfer cannot have.

        The old discriminator was ``is_override``, and
        ``routes/transfers/mutations.py`` raises that flag only for a
        template-linked row -- so an AD-HOC transfer's retype fell to the
        definition arm and left both legs DERIVED.  Authorship is now stated by
        the door for every row, so an ad-hoc retype takes them.

        **It moves no money and cannot**: an ad-hoc transfer has no template,
        ``loan_payment_settings`` is keyed by ``transfer_template_id``, so it
        can never be a derive-mode loan payment -- the arm where a leg's
        derivation differs from its parent's figure.  Both arms answer the
        typed figure.  What changes is that three rows now STORE it where one
        did, which is the shape X-au-m finishes.

        Graded because an adversarial review found the paragraph documenting the
        old arm had been deleted along with the comparison it described, leaving
        the change undocumented AND untested in either direction.
        """
        with app.app_context():
            xfer = _plain_transfer(seed_user, seed_periods)
            xfer_id = xfer.id
            assert xfer.transfer_template_id is None, "the ad-hoc precondition"

            resp = auth_client.patch(
                f"/transfers/instance/{xfer_id}",
                data={
                    "amount": "400.00",
                    "amount_as_rendered": rendered_transfer_amount(xfer),
                    "status_id": str(xfer.status_id),
                    "notes": "",
                },
            )
            assert resp.status_code == 200, resp.data

            db.session.expire_all()
            retyped = db.session.get(Transfer, xfer_id)
            assert retyped.is_override is False, (
                "the route raises the flag only for a template-linked row"
            )
            assert retyped.amount == Decimal("400.00")
            assert transfer_amount(retyped) == Decimal("400.00")

            # It cannot stop owning the figure: no definition prices an
            # ad-hoc transfer, so the database refuses the declaration.
            declare_derived(retyped, AmountSourceEnum.TEMPLATE)
            with pytest.raises(sqlalchemy.exc.IntegrityError) as excinfo:
                db.session.flush()
            db.session.rollback()
            assert constraint_name_from(excinfo.value) == (
                "ck_transfers_adhoc_owns_amount"
            ), f"the derived ad-hoc transfer was refused by: {excinfo.value}"

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
            xfer = _generated_transfer(seed_user, seed_periods)
            user_id = seed_user["user"].id

            transfer_service.update_transfer(
                xfer.id, user_id, amount_ownership=AmountOwnership.own(Decimal("400.00")),
                is_override=True,
            )
            db.session.commit()
            transfer_service.update_transfer(
                xfer.id, user_id, amount_ownership=derived_ownership(AmountSourceEnum.TEMPLATE),
                # The definition re-prices the pair (R-JR): not a human, so the
                # taken leg is handed back rather than left at a stale figure.

            )
            db.session.commit()

            handed_back = _reread(xfer.id)
            assert handed_back.amount is None
            assert owns_its_amount(handed_back) is False
            assert transfer_amount(handed_back) == Decimal("500.00")


def _both_legs(xfer):
    """Return *xfer*'s two legs, from-side first, as the grid would draw them.

    :func:`~app.services.transfer_legs.leg_of` on each endpoint: the value a
    surface prices with :func:`~app.services.cash_ledger.leg_amounts_by_key`,
    which is how a leg is read since plan step ``balance:X-bi-6-1`` (ruling
    **R-BAL87**).  These cases priced the two shadow ROWS with
    ``amounts_by_id`` until plan step ``balance:X-bi-6-4d-3`` stopped the
    shadows being born (ruling **R-BAL167** class 1).
    """
    return [leg_of(xfer, xfer.from_account_id), leg_of(xfer, xfer.to_account_id)]


class TestALoanPaymentsLegsReadTheLoan:
    """Rule 4 through the declaration, with no read-time repair in front."""

    def test_both_legs_are_worth_the_contract_not_the_stored_figure(
        self, app, seed_user, seed_periods,
    ):
        """The definition's ``$1.00`` is not what either leg is worth.

        The fixture's definition states a deliberately stale ``$1.00``, so a leg
        worth ``$1.00`` is one the loan did not price.  It asked both legs
        because the cutover declared both shadow rows, and a version declaring
        only the checking side would have left the loan-side income leg
        answering the parent.  Since plan step balance:X-bi-6-4d-3 both legs
        resolve through ONE ``resolve_transfer_amount`` call on one transfer,
        so the two cannot differ: ``len(priced) == 2`` checks that the two
        legs' keys are distinct, not that two answers agree.

        **The parent stores NOTHING since plan step X-au-f**, and the assertion
        below is what replaces ``xfer.amount == Decimal("1.00")``: the stale
        figure the legs had to beat is not merely unread now, it is gone, and
        what the legs must beat is the SERIES the definition still states.  The
        discrimination is the same -- ``$1.00`` against the contract's
        ``$1,499.10`` -- and one stored copy shorter.

        **The legs are the transfer's since plan step balance:X-bi-6-4d-3**
        (ruling **R-BAL167** class 1): both are priced through
        ``leg_amounts_by_key``, where the two shadow rows were priced through
        ``amounts_by_id``, and the per-leg "stores nothing" check went with the
        rows -- the one row left is asserted storing nothing above it.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )

            assert xfer.amount is None
            assert template_amount_service.amount_as_of(
                xfer.template, xfer.due_date,
            ) == Decimal("1.00")
            priced = leg_amounts_by_key(_both_legs(xfer), basis)
            assert len(priced) == 2
            assert set(priced.values()) == {_CONTRACT}

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

        What each side booked is read off its record, where it was read off
        each twin row (ruling R-BAL167 class 1, plan step balance:X-bi-6-4d-2).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id
            scenario_id = seed_user["scenario"].id

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(_TYPED),

                is_override=True,
                status_id=ref_cache.status_id(StatusEnum.DONE),
            )
            db.session.commit()

            db.session.expire_all()
            for account_id in (xfer.from_account_id, loan_id):
                assert transfer_side_figure(
                    db.session, xfer.id, account_id,
                ) == _TYPED

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

        What the grid publishes for a transfer's legs is asked here: it was
        ``amounts_by_id`` over the shadow rows (ruling **R-Q**), and since
        plan step ``balance:X-bi-6-1`` it is ``leg_amounts_by_key`` over the
        legs (``routes/grid/_items.py``); this case asked the first until plan
        step balance:X-bi-6-4d-3 stopped the shadows being born (ruling
        R-BAL167 class 1).
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            basis = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            assert set(leg_amounts_by_key(_both_legs(xfer), basis).values()) == {
                _CONTRACT,
            }

            transfer_service.update_transfer(
                xfer.id, seed_user["user"].id,
                amount_ownership=AmountOwnership.own(_TYPED),  is_override=True,
            )
            db.session.commit()

            fresh = derived_amount_basis(
                seed_user["user"].id, seed_user["scenario"].id,
            )
            shown = leg_amounts_by_key(_both_legs(_reread(xfer.id)), fresh)
            assert len(shown) == 2
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

        **Asked of the TRANSFER since plan step balance:X-bi-6-4d-3** (ruling
        **R-BAL167** class 1): no shadow row is born, both legs are read off
        the transfer (**R-BAL13**), so what they are worth is
        :func:`~tests._test_helpers.transfer_amount` and the row the flip must
        not write is the transfer -- its declaration (``TEMPLATE``) and its
        ``updated_at``, stamped where each shadow's were, with the caveat
        above unchanged.  The figures are the same two.
        """
        with app.app_context():
            xfer = _derived_loan_transfer(seed_user, seed_periods)
            loan_id = xfer.to_account_id
            xfer_id = xfer.id
            xfer.template.settings.derive_from_loan = False
            db.session.commit()

            before = _reread(xfer_id)
            assert transfer_amount(before) == Decimal("1.00")
            stamp = (before.amount_source_id, before.updated_at)

            resp = auth_client.post(
                f"/accounts/{loan_id}/loan/payments/{before.template.id}/track",
            )
            assert resp.status_code in (200, 302), resp.data

            after = _reread(xfer_id)
            assert transfer_amount(after) == _CONTRACT
            assert after.amount is None
            assert (after.amount_source_id, after.updated_at) == stamp


class TestTheAmountModelsOwnEagerLoad:
    """``transfer_pricing_load_options`` keeps a priced leg set off an N+1.

    **Re-expressed onto TRANSFER legs at plan step balance:X-bi-6-4d-3**
    (ruling **R-BAL167** class 1).  The pair loaded SHADOW rows with
    ``pricing_load_options`` and priced them with ``amounts_by_id``; no shadow
    is born since that step, and a leg is read off its transfer (**R-BAL13**),
    so the row set is the legs :func:`~app.services.transfer_legs.planned_transfer_legs`
    loads -- the cash fold's plan-half loader -- with and without
    :func:`~app.utils.amount_relationships.transfer_pricing_load_options`,
    priced by :func:`~app.services.cash_ledger.leg_amounts_by_key`, the batch
    the grid publishes.  ``pricing_load_options`` composes that same tuple
    under ``Transaction.transfer``, so the parent's pricing graph is the one
    being graded in both shapes.

    **The tuple's zero-statement claim was pinned before this step too**: the
    shadow-row pair priced rows loaded with ``pricing_load_options``, which
    composes this same tuple, and
    ``test_loan_installments.test_the_priced_feed_reads_every_pricing_table``
    pins it by a statement count.  What is new here is narrower: the old
    warm-up priced one leg, which loaded the one definition's ``settings``
    before the capture opened, and this warm-up pays the LOAN resolve and
    nothing else -- it asks the basis's loan derivation directly -- so
    dropping the per-DEFINITION settings chain from the tuple now shows as a
    statement, as dropping the per-ROW period chain always did (both
    measured).
    """

    @staticmethod
    def _legs_and_warm_basis(seed_user, seed_periods, options):
        """Load the loan payment's checking legs and pay the loan resolve.

        Returns the legs and a basis whose loan derivation already holds the
        loan, so the capture measures the legs' own loads alone.  The
        installment's dates are read off the transfer BEFORE the session is
        expired, so reading them loads nothing the capture would miss.
        """
        xfer = _derived_loan_transfer(seed_user, seed_periods)
        checking, loan_id = xfer.from_account_id, xfer.to_account_id
        due_date, period_start = xfer.due_date, xfer.pay_period.start_date
        scenario_id = seed_user["scenario"].id
        _db.session.commit()
        _db.session.expire_all()

        legs = planned_transfer_legs(checking, scenario_id, options=options)
        basis = derived_amount_basis(seed_user["user"].id, scenario_id)
        # The LOAN resolve is a query and is not what this measures, so it is
        # paid once outside the capture -- through the loan derivation itself,
        # so no leg's relationships are loaded by the warm-up.
        assert basis.loans.derive_cash(
            due_date, period_start, loan_id, Decimal("0.00"),
        ) == _CONTRACT
        return legs, basis

    def test_a_loaded_row_set_prices_with_no_further_query(
        self, app, seed_user, seed_periods,
    ):
        """Pricing legs loaded WITH the options issues no statement at all.

        The direct control on the claim the options carry: a loan payment's
        leg is priced off its transfer, which walks ``template -> settings``
        and its own pay period (rule 4's derive arm), once per row, on the
        busiest surfaces in the app.

        Zero rather than "fewer", because zero is the claim: everything the
        rule reads is already in the identity map, so a rule that started
        loading a per-row relationship the tuple does not list (a transfer's
        movements, say), or issuing a statement of its own, would show up
        here as a statement rather than as a slow page nobody measures.  Two
        things it cannot see.  The transfer's ``lazy="joined"`` relationships
        -- ``from_account``, ``to_account``, ``status`` and ``category``
        (``app/models/transfer.py``) -- are loaded by the loader's OWN
        statement, so a rule that started reading one adds no statement here
        (measured: one reading ``from_account`` added none).  And this fixture
        is a derive-mode loan payment, so the series chain
        (``template -> amount_versions``), which only rule 3 and rule 4's
        manual arm read, is not exercised here.
        """
        with app.app_context():
            legs, basis = self._legs_and_warm_basis(
                seed_user, seed_periods, transfer_pricing_load_options(),
            )
            assert len(legs) >= 2

            priced, statements = capture_sql_statements(
                lambda: leg_amounts_by_key(legs, basis),
            )

            assert statements == [], (
                "pricing a loaded row set must issue no query; got "
                f"{[text for text, _params in statements]}"
            )
            assert set(priced.values()) == {_CONTRACT}

    def test_without_the_options_the_same_row_set_DOES_query(
        self, app, seed_user, seed_periods,
    ):
        """The negative control: the options are what make the zero above.

        Without it the case would pass just as well on a set SQLAlchemy had
        already loaded for some other reason, and would keep passing if the
        options were deleted -- which is the shape a green gate that measures
        nothing takes.
        """
        with app.app_context():
            legs, basis = self._legs_and_warm_basis(seed_user, seed_periods, ())

            _priced, statements = capture_sql_statements(
                lambda: leg_amounts_by_key(legs, basis),
            )

            assert statements, (
                "an unloaded row set must reach the database for its parents"
            )
