"""
Shekel Budget App -- the grid's doors on a RULE-LESS definition's row

Plan step ``balance:X-bi-7a`` (ruling **R-BAL20**, every plan item has exactly
one definition).  Five "no cadence" readings of ``template_id IS NULL`` on
the transaction doors were re-keyed onto ``Transaction.recurs``, so a row
whose definition has NO rule -- template-linked, no cadence: a cleared cadence
today, every one-off once the family's cutover mints it a definition --
behaves as the one-off it is.  No production row changes behaviour (no
rule-less transaction definition exists on the 2026-09-12 restore), so each
case here MAKES one, the way the edit door does: the engine's row under a
cadence, then the cadence cleared (dis-associate; delete-orphan).

Pinned per door, each beside its recurring control so the two arms are
graded against each other and not against a shape the app never writes:

* DELETE -- hard, and the dialog says so (``preview.soft``, the service's
  own arm); **and since plan step ``balance:X-bi-7b`` the definition goes
  with its LAST row** (rulings R-BAL23 / R-BAL27), stays while it holds
  another, and the delete is REFUSED while a standing merchant rule names
  the definition -- the sentence on the card and the door's 400 are one
  rule (``deletion_refusal``);
* due date -- the popover offers the input, a MOVE lands, a CLEAR is refused
  with a sentence that names the rule rather than "invalid reference";
* period move -- ``is_override`` is NOT flipped (developer 2026-09-13: the
  flip's MOVE half keys on ``recurs``);
* typed figure -- ``is_override`` IS still flipped until ``X-bi-7b``'s
  restate door (the same ruling), so the figure survives a definition edit.
"""
import re
from datetime import timedelta
from decimal import Decimal

from app.extensions import db
from app.models.merchant import Merchant
from app.models.merchant_rule import MerchantRule
from app.models.template_amount_version import TemplateAmountVersion
from app.models.transaction import Transaction
from app.models.transaction_template import TransactionTemplate
from app.utils.dates import display_today
from tests._test_helpers import (
    generate_row_of,
    make_expense_template,
    resolved_amount,
    state_template_price,
)


def _one_off(seed_user, seed_periods_today):
    """The engine's row of a definition whose cadence was then cleared.

    A plain helper rather than a fixture: the row has to be born inside the
    test's own ``app_context`` to be persistent in its session.
    """
    template = make_expense_template(
        db.session, seed_user, amount="162.25", name="Kayla's Kindle",
        category_key="Groceries",
    )
    row = generate_row_of(template, seed_periods_today[0])
    template.recurrence_rule = None
    db.session.commit()
    assert row.template_id == template.id
    assert row.recurs is False
    return row


def _recurring(seed_user, seed_periods_today):
    """The control: the engine's row of a definition that still repeats."""
    template = make_expense_template(
        db.session, seed_user, amount="100.00", name="Streaming",
        category_key="Groceries",
    )
    row = generate_row_of(template, seed_periods_today[0])
    db.session.commit()
    assert row.recurs is True
    return row


def _rendered_companion(auth_client, row):
    """Read the popover's rendered-figure companion, as a browser posts it."""
    edit = auth_client.get(f"/transactions/{row.id}/full-edit")
    assert edit.status_code == 200
    companion = re.search(
        r'name="estimated_amount_as_rendered"[^>]*value="([^"]*)"',
        edit.data.decode(),
    )
    assert companion is not None
    return companion.group(1)


def _rule_naming(seed_user, template):
    """State a standing merchant rule filing a merchant's spending into *template*."""
    merchant = Merchant(account_id=template.account_id, name="Amazon")
    db.session.add(merchant)
    db.session.flush()
    db.session.add(MerchantRule(
        user_id=seed_user["user"].id,
        account_id=template.account_id,
        merchant_id=merchant.id,
        template_id=template.id,
        never_a_purchase=False,
    ))
    db.session.commit()


class TestDelete:
    """``_delete._leaves_the_table`` keys soft-versus-hard on ``recurs``."""

    def test_a_rule_less_definitions_last_row_takes_the_definition_with_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The row goes, and so do the definition and its series (R-BAL23, R-BAL27).

        A one-off is a rule-less definition plus its placed row; with the row
        gone the definition defines nothing, so ``delete_transaction`` disposes
        of it through ``definition_delete.permanently_delete_definition`` --
        the one act the definition's own door and the account door call.  The
        ``template_amount_versions`` row goes with it (delete-orphan), which
        is what "the definition goes" has to mean for a series to have one
        home.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            row_id = one_off.id
            template_id = one_off.template_id
            assert db.session.query(TemplateAmountVersion).filter_by(
                transaction_template_id=template_id,
            ).count() == 1

            resp = auth_client.delete(f"/transactions/{row_id}")

            assert resp.status_code == 200
            assert db.session.get(Transaction, row_id) is None
            assert db.session.get(TransactionTemplate, template_id) is None
            assert db.session.query(TemplateAmountVersion).filter_by(
                transaction_template_id=template_id,
            ).count() == 0

    def test_a_row_of_a_definition_holding_another_row_leaves_it_standing(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Not the last row: the row goes hard, the definition and its sibling stay.

        The shape a bank-born envelope takes across paychecks (R-BAL24), and
        a cleared cadence's survivors today.  **The sibling counted here is
        SOFT-DELETED**, on purpose: a soft-deleted one-off is still its
        definition's (10.8), and a predicate that skipped tombstones would
        dispose of the definition under one -- a TEMPLATE-priced row priced
        by nothing, ruling R-JE's state.
        """
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="20.00", name="Two Rows",
                category_key="Groceries",
            )
            first = generate_row_of(template, seed_periods_today[0])
            second = generate_row_of(template, seed_periods_today[1])
            template.recurrence_rule = None
            second.is_deleted = True
            db.session.commit()
            first_id, second_id, template_id = first.id, second.id, template.id
            assert first.recurs is False

            resp = auth_client.delete(f"/transactions/{first_id}")

            assert resp.status_code == 200
            assert db.session.get(Transaction, first_id) is None
            assert db.session.get(Transaction, second_id) is not None
            assert db.session.get(TransactionTemplate, template_id) is not None

    def test_the_last_row_is_refused_while_a_merchant_rule_names_the_definition(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """R-BAL23: the delete would cascade an owner's stated answer away.

        ``fk_merchant_rules_template_account`` is ON DELETE CASCADE, so the
        row's delete -- which disposes of the definition -- is refused with
        the sentence, on the card (the control is withheld and the sentence
        printed) and at the door (a designed 400); row, definition and rule
        all stand.  A rule is never un-stated (R-GS), so restating it is what
        frees the row.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            row_id, template_id = one_off.id, one_off.template_id
            _rule_naming(seed_user, one_off.template)
            rule_count = db.session.query(MerchantRule).count()

            card = auth_client.get(f"/transactions/{row_id}/full-edit")
            assert card.status_code == 200
            html = card.data.decode()
            assert "where a merchant&#39;s bank spending goes" in html
            assert "Delete this row" not in html

            resp = auth_client.delete(f"/transactions/{row_id}")

            assert resp.status_code == 400, resp.data
            assert b"bank spending goes" in resp.data
            assert db.session.get(Transaction, row_id) is not None
            assert db.session.get(TransactionTemplate, template_id) is not None
            assert db.session.query(MerchantRule).count() == rule_count

    def test_a_merchant_rule_does_not_refuse_a_row_that_is_not_the_last(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The firing control for the arm above: nothing is disposed of, so nothing cascades."""
        with app.app_context():
            template = make_expense_template(
                db.session, seed_user, amount="20.00", name="Two Rows",
                category_key="Groceries",
            )
            first = generate_row_of(template, seed_periods_today[0])
            generate_row_of(template, seed_periods_today[1])
            template.recurrence_rule = None
            db.session.commit()
            _rule_naming(seed_user, template)
            first_id = first.id

            resp = auth_client.delete(f"/transactions/{first_id}")

            assert resp.status_code == 200, resp.data
            assert db.session.get(Transaction, first_id) is None
            assert db.session.query(MerchantRule).count() == 1

    def test_a_recurring_definitions_row_is_still_soft_deleted(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the tombstone the engine reads is kept where a rule stands."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            resp = auth_client.delete(f"/transactions/{recurring.id}")
            assert resp.status_code == 200
            db.session.refresh(recurring)
            assert recurring.is_deleted is True

    def test_the_dialog_promises_what_the_door_will_do(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """One sentence per arm, and it is the SERVICE's arm (``preview.soft``).

        The dialog read the row's link until this step; keyed there it would
        have told the owner of a one-off that *the recurring transaction
        behind it keeps generating the others* over a row about to be gone
        for good.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            recurring = _recurring(seed_user, seed_periods_today)
            html = auth_client.get(
                f"/transactions/{one_off.id}/full-edit",
            ).data.decode()
            assert "This cannot be undone." in html
            assert "This occurrence will not come back" not in html

            html = auth_client.get(
                f"/transactions/{recurring.id}/full-edit",
            ).data.decode()
            assert "This occurrence will not come back" in html
            assert "This cannot be undone." not in html


class TestDueDate:
    """``_gates._reject_generated_due_date_edit`` and the popover's input."""

    def test_the_popover_offers_the_input_on_a_rule_less_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """An INPUT where no rule derives the day; TEXT where one does."""
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            recurring = _recurring(seed_user, seed_periods_today)
            html = auth_client.get(
                f"/transactions/{one_off.id}/full-edit",
            ).data.decode()
            assert re.search(r'<input type="date" name="due_date"', html)
            assert "Set by this recurring transaction" not in html

            html = auth_client.get(
                f"/transactions/{recurring.id}/full-edit",
            ).data.decode()
            assert not re.search(r'<input type="date" name="due_date"', html)
            assert "Set by this recurring transaction" in html

    def test_moving_the_date_lands_and_the_price_follows_it(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The date is the owner's to state (R-BAL22), and rule 3 reads it.

        **Measured rather than claimed** (an adversarial review of this step
        found the gate's first docstring saying the move "prices nothing
        differently", true only of the one-version series R-BAL21 gives a
        one-off at the doors leaf): a CLEARED cadence keeps every version its
        series held, so a row moved across a version boundary re-prices
        through amount rule 3 -- `$162.25` before the boundary, `$170.00`
        after it -- with no figure of its own and no flag, exactly as any
        derived row is priced on its own day.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            # The series: `$162.25` from today (the create door's version),
            # `$170.00` from a week on; the row sits before the boundary.
            today = display_today()
            state_template_price(
                one_off.template, "170.00", effective_on=today + timedelta(days=7),
            )
            db.session.commit()
            assert one_off.due_date < today + timedelta(days=7)
            assert resolved_amount(one_off) == Decimal("162.25")
            moved_to = today + timedelta(days=10)

            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "due_date": moved_to.isoformat(),
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(one_off)
            assert one_off.due_date == moved_to
            assert one_off.estimated_amount is None
            assert one_off.is_override is False
            assert resolved_amount(one_off) == Decimal("170.00")
            assert auth_client.get("/grid").status_code == 200

    def test_clearing_the_date_is_refused_with_the_reason(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A CLEAR meets the rule's own sentence, not a constraint's.

        ``ck_transactions_template_row_needs_due_date`` would refuse the
        write anyway, rendered as *Invalid reference*; the gate says why.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            was = one_off.due_date
            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "due_date": "",
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 400
            assert b"can be moved but not cleared" in resp.data
            assert b"Invalid reference" not in resp.data
            db.session.refresh(one_off)
            assert one_off.due_date == was

    def test_a_recurring_definitions_row_still_refuses_the_field(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: a generated row's date is its definition's, move or clear."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            was = recurring.due_date
            for submitted in ("2026-02-20", ""):
                resp = auth_client.patch(f"/transactions/{recurring.id}", data={
                    "due_date": submitted,
                    "version_id": recurring.version_id,
                })
                assert resp.status_code == 400
                assert b"recurring transaction" in resp.data
                db.session.refresh(recurring)
                assert recurring.due_date == was


class TestTheOverrideFlip:
    """``mutations._apply_field_updates``: the MOVE half keys on ``recurs``."""

    def test_a_period_move_does_not_flip_a_rule_less_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A moved one-off keeps hearing its definition.

        The flag is what keeps the maintain and generate passes off a row the
        owner placed elsewhere; a rule-less definition runs no pass, and the
        flag would only hide the row from ``propagate_to_unruled_definition``
        (the twin's defect **BAL-493** on this table).
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            target = seed_periods_today[1]
            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "pay_period_id": str(target.id),
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(one_off)
            assert one_off.pay_period_id == target.id
            assert one_off.is_override is False

    def test_a_period_move_still_flips_a_recurring_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """THE CONTROL: the flip the regeneration relies on is untouched."""
        with app.app_context():
            recurring = _recurring(seed_user, seed_periods_today)
            target = seed_periods_today[1]
            resp = auth_client.patch(f"/transactions/{recurring.id}", data={
                "pay_period_id": str(target.id),
                "version_id": recurring.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(recurring)
            assert recurring.pay_period_id == target.id
            assert recurring.is_override is True

    def test_a_typed_figure_still_flips_a_rule_less_definitions_row(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Until ``X-bi-7b``'s restate door, a typed figure is the OWNER's.

        Developer 2026-09-13: the figure lands OWN on the row today, and the
        flag is what keeps the definition's next edit from re-declaring the
        row TEMPLATE-priced and silently discarding it.  Kayla's Kindle,
        `$162.25` on the definition, `$170.00` typed.
        """
        with app.app_context():
            one_off = _one_off(seed_user, seed_periods_today)
            companion = _rendered_companion(auth_client, one_off)
            assert companion == "162.25"
            resp = auth_client.patch(f"/transactions/{one_off.id}", data={
                "estimated_amount": "170.00",
                "estimated_amount_as_rendered": companion,
                "pay_period_id": str(one_off.pay_period_id),
                "status_id": str(one_off.status_id),
                "notes": "",
                "version_id": one_off.version_id,
            })
            assert resp.status_code == 200, resp.data
            db.session.refresh(one_off)
            assert str(one_off.estimated_amount) == "170.00"
            assert one_off.amount_source_id is None
            assert one_off.is_override is True
