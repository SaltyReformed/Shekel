"""The transfer EDIT form's lock affordance, computed for THIS edit.

Plan step **R7d-f-5**, rulings **R-R78** (the module split) and **R-R79** (the
affordance).  The create form has shipped two sets of destination ids since
plan steps R7c-b and R7d-f-3 -- every loan of the owner, and the payment-less
subset -- so ``recurrence_form.js`` can lock the "Starts on" and "Ends" rows the
moment such a loan is chosen.  The edit form shipped neither, on the premise
that it "already knows" whether its template is a loan payment; plan step
R7d-f-4 made that premise false, because the UPDATE door now derives the same
bounds for two edits the server cannot see at render -- a cadence added to a
one-time transfer into a loan, and a repeating transfer moved onto one -- so
the form invited a start the save replaces and a stop the save refuses.

**What the edit form emits is decided the way the door decides the same edit**
(:func:`~app.routes._loan_destination.loan_destination_locks_for_edit`), filter
by filter: a definition ruling **R-R76** pins to its loan may not move at all,
so it ships EMPTY sets and a DISABLED destination control carrying the
refusal's sentence; a definition that already repeats leaves its stored
destination out (staying put derives nothing); one that does not repeat yet
keeps every loan in (adding a cadence derives for the loan it pays into).

Rendered HTML can see the attributes, the ``disabled`` flag and the help text;
whether the script actually locks a row when a listed loan is chosen is
``tests/manual/verify_recurrence_form.py``'s, because a rendered page cannot
tell a control a script disabled from one it never touched.  Every negative
control named in a docstring below was executed as a mutation while this module
was written.

Two tests elsewhere had their premise reversed by this step's ruling
(CLAUDE.md rule 5's exception, developer 2026-09-12):
``test_transfers.py::TestTemplateList`` no longer asserts an edit form ships no
sets, and ``test_transfer_create_derived_stop.py``'s help-sentence case asserts
the edit form carries both sentences too.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.transfer_template import TransferTemplate
from app.routes._loan_destination import (
    LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION,
    LoanDestinationLocks,
    loan_destination_locks_for_edit,
)
from app.schemas.validation import SAME_ACCOUNT_TRANSFER_MESSAGE
from app.services.balance_at import BalanceContext
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    freeze_today,
    make_every_period_rule,
    make_loan_payment_template,
    state_template_price,
)

#: Frozen so "today" cannot drift past the schedule ``seed_periods`` builds.
_TODAY = date(2026, 3, 1)

#: Every loan below originates after today, payment day 1.
_ORIGINATION = date(2026, 4, 15)


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today ahead of the loans' origination."""
    freeze_today(monkeypatch, _TODAY)


def _mortgage(seed_user, name):
    """Return a mortgage originating after today, payment day 1."""
    return create_loan_account(
        seed_user, db.session, name=name,
        principal=Decimal("200000.00"), rate=Decimal("0.05000"),
        term=360, origination_date=_ORIGINATION, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


def _savings(seed_user):
    """Return a savings account of the owner's."""
    return create_account_of_type(
        seed_user, db.session, "Savings", "Savings",
        anchor_balance=Decimal("100.00"),
    )


def _template_into(seed_user, account, name, *, repeats):
    """Return a FLUSHED transfer template into *account*, named *name*.

    ``make_transfer_template``'s shape with a free name -- the owner's names
    are unique (``uq_transfer_templates_user_name``) and several cases here
    need two definitions into one loan.  Priced through the one write door;
    given the every-paycheck rule when *repeats*, else left as the "does not
    repeat" definition ``POST /transfers`` creates by default.  The caller
    commits.
    """
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        from_account_id=seed_user["account"].id,
        to_account_id=account.id,
        name=name,
        default_amount=Decimal("200.00"),
    )
    db.session.add(template)
    db.session.flush()
    state_template_price(template)
    if repeats:
        make_every_period_rule(db.session, template)
    return template


def _edit_form(auth_client, template):
    """Return the rendered edit form of *template*."""
    return auth_client.get(f"/transfers/{template.id}/edit").data.decode()


def _attribute(html, name):
    """Return the value of the ``#recurrence-fields`` attribute *name*."""
    container = html.split('id="recurrence-fields"')[1].split(">")[0]
    assert f'{name}="' in container, f"{name} is not on the container"
    return container.split(f'{name}="')[1].split('"')[0]


def _sets(html):
    """Return the two lock sets the page emits, as the script reads them."""
    return (
        _attribute(html, "data-loan-account-ids"),
        _attribute(html, "data-loan-account-ids-without-payment"),
    )


def _destination_select_tag(html):
    """Return the opening tag of the "To Account" ``<select>``."""
    return html.split('id="to_account_id"')[1].split(">")[0]


def _starts_on_input_tag(html):
    """Return the opening tag of the "Starts on" ``<input>``."""
    return html.split('id="starts_on"')[1].split(">")[0]


def _pass(seed_user):
    """Return a read pass for the owner, as the route builds one."""
    return BalanceContext.build(seed_user["user"].id)


def _post_what_a_pinned_form_emits(auth_client, template, **fields):
    """POST the edit form of a PINNED loan payment exactly as the browser would.

    Every enabled control and nothing else: the name, the amount, the source
    account, the version pin, and the three cadence controls -- the MONTHLY
    cadence the loan door authors (``make_loan_payment_template``).  NOT
    ``to_account_id`` (the destination select is disabled since plan step
    R7d-f-5), NOT ``starts_on`` and NOT the three "Ends" keys (both rows are
    server-locked for the standing payment).  *fields* overrides what this
    case changes.  Redirect unfollowed so the flash stays readable.
    """
    payload = {
        "name": template.name,
        "default_amount": str(template.default_amount),
        "from_account_id": str(template.from_account_id),
        "version_id": str(template.version_id),
        **cadence_payload(unit=RecurrenceUnitEnum.MONTH, states_a_start=False),
        **fields,
    }
    return auth_client.post(f"/transfers/{template.id}", data=payload)


def _flashes(auth_client):
    """Return the unconsumed flash messages left by an unfollowed redirect."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _reload(template):
    """Return *template*'s row as the request left it."""
    db.session.expire_all()
    return db.session.get(TransferTemplate, template.id)


class TestWhatTheEditFormEmits:
    """The rendered attributes, one edit shape at a time."""

    def test_a_repeating_savings_transfer_names_every_loan(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A repeating transfer into SAVINGS: every loan derives the start.

        Its stored destination is not a loan, so the exclusion below has
        nothing to remove; a move onto either loan derives the start, onto
        the payment-less one the stop too (the door's rule 2 and rule 3).
        """
        with app.app_context():
            paid = _mortgage(seed_user, "Paid")
            make_loan_payment_template(db.session, seed_user, paid)
            unpaid = _mortgage(seed_user, "Unpaid")
            template = _template_into(
                seed_user, _savings(seed_user), "To Savings", repeats=True,
            )
            db.session.commit()

            start_set, stop_set = _sets(_edit_form(auth_client, template))

            assert start_set == f"{paid.id},{unpaid.id}"
            assert stop_set == str(unpaid.id)

    def test_a_repeating_transfer_leaves_its_stored_loan_out(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A repeating SECOND transfer into a paid loan: staying put derives nothing.

        The loan's standing payment is the OLDER definition (the loan door's
        own, with a settings row), so this one is neither standing nor
        settings-carrying -- not pinned -- and its stored start is its own
        (plan ledger row **D50**).  The door's early return for "a rule, and
        not moving" is what this mirrors: its stored loan leaves both sets and
        the other loan stays.

        NEGATIVE CONTROL: drop the ``excluding`` filter from
        ``_loan_destination_lock_sets`` and the stored loan appears in the
        start set, so the script would lock a start the door leaves alone.
        """
        with app.app_context():
            paid = _mortgage(seed_user, "Paid")
            make_loan_payment_template(db.session, seed_user, paid)
            unpaid = _mortgage(seed_user, "Unpaid")
            second = _template_into(seed_user, paid, "Second into Paid", repeats=True)
            db.session.commit()

            html = _edit_form(auth_client, second)

            assert _sets(html) == (str(unpaid.id), str(unpaid.id))
            assert "disabled" not in _destination_select_tag(html)

    def test_a_one_time_transfer_into_a_loan_keeps_that_loan_in(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A transfer that does not repeat yet: adding a cadence derives for its own loan.

        No rule, so nothing is excluded: the door's authoring shape derives
        for whatever loan the edit LEAVES the definition paying into, the
        stored one included -- and this loan holds no payment (a one-time
        transfer is not a recurring one), so the stop derives too.

        NEGATIVE CONTROL: exclude the stored destination whatever the rule
        says and the stored loan leaves both sets, so the owner types a start
        the save replaces -- the exact gap this leaf closes.
        """
        with app.app_context():
            unpaid = _mortgage(seed_user, "Unpaid")
            paid = _mortgage(seed_user, "Paid")
            make_loan_payment_template(db.session, seed_user, paid)
            one_off = _template_into(seed_user, unpaid, "One-off", repeats=False)
            db.session.commit()

            html = _edit_form(auth_client, one_off)

            assert _sets(html) == (f"{unpaid.id},{paid.id}", str(unpaid.id))
            assert "disabled" not in _destination_select_tag(html)

    def test_the_standing_payment_is_pinned(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The loan's standing payment: empty sets, a disabled destination, the sentence.

        Its two bound rows are ALREADY server-locked (plan step R7d-f); what
        this step adds is the destination control, disabled with ruling
        **R-R76**'s own sentence as its help, and sets the script has nothing
        to apply from.  A second loan exists so the empty set is a decision
        and not an owner with nothing to list.
        """
        with app.app_context():
            paid = _mortgage(seed_user, "Paid")
            _mortgage(seed_user, "Unpaid")
            standing = _template_into(seed_user, paid, "Standing", repeats=True)
            db.session.commit()

            html = _edit_form(auth_client, standing)

        assert _sets(html) == ("", "")
        assert "disabled" in _destination_select_tag(html)
        assert LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION in html
        assert "disabled" in _starts_on_input_tag(html), (
            "the standing payment's Starts on row is server-locked"
        )

    def test_a_settings_carrying_second_payment_is_pinned_too(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The R-R76 union's other arm: pinned, though its rows are OPEN.

        A generic recurring transfer is the loan's standing payment (older
        id); the loan door's own second, DERIVE-mode payment carries a
        settings row and is not standing, so the server locks neither of its
        bound rows -- and still may not let it move, because its amount is
        priced off the loan it pays into.  The pin and the row locks are two
        different facts, and this case holds them apart.

        NEGATIVE CONTROL: drop ``is_loan_payment`` from
        ``is_loan_payment_or_standing`` and this form ships the other loan in
        both sets with the destination enabled -- promising a derivation the
        save refuses.
        """
        with app.app_context():
            loan = _mortgage(seed_user, "Paid")
            _mortgage(seed_user, "Unpaid")
            standing = _template_into(seed_user, loan, "Standing", repeats=True)
            second = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            assert second.id > standing.id, "precondition: the generic one is standing"

            html = _edit_form(auth_client, second)

        assert _sets(html) == ("", "")
        assert "disabled" in _destination_select_tag(html)
        assert LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION in html
        assert "disabled" not in _starts_on_input_tag(html), (
            "a second payment's Starts on row is the owner's: the pin is not the lock"
        )

    def test_an_unpinned_form_shows_no_pin_sentence(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """CONTROL: the sentence and the help element appear only when pinned."""
        with app.app_context():
            template = _template_into(
                seed_user, _savings(seed_user), "To Savings", repeats=True,
            )
            db.session.commit()

            html = _edit_form(auth_client, template)

        assert LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION not in html
        assert 'id="to-account-help"' not in html
        assert "disabled" not in _destination_select_tag(html)

    def test_both_help_rows_carry_both_sentences(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The edit form's two help rows are swappable, because its rows can lock without a reload.

        The script swaps each row's help between the two sentences the
        template carries as data attributes; until this step the edit render
        passed ``swappable=False`` and carried neither.  The "Ends" half is
        also pinned where the create form's case lives
        (``test_transfer_create_derived_stop.py``); this pins the "Starts on"
        half.
        """
        with app.app_context():
            template = _template_into(
                seed_user, _savings(seed_user), "To Savings", repeats=True,
            )
            db.session.commit()

            html = _edit_form(auth_client, template)

        for help_id in ("starts-on-help", "end-bound-help"):
            tag = html.split(f'id="{help_id}"')[1].split(">")[0]
            assert "data-locked-text=" in tag, help_id
            assert "data-open-text=" in tag, help_id

    def test_the_transaction_form_ships_no_sets(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """NEGATIVE CONTROL: the attributes belong to the transfer form alone.

        A transaction template has no destination, so its form emits no set to
        test membership against and the script's lock paths stay inert there.
        """
        with app.app_context():
            _mortgage(seed_user, "Unpaid")
            html = auth_client.get("/templates/new").data.decode()

        assert "data-loan-account-ids" not in html


class TestWhatThePinnedFormPosts:
    """The route, fed the payload the pinned form REALLY emits: no destination key.

    Every other update test posts ``to_account_id``, because every form did
    until this step; a control this step disables posts nothing, and the
    project's own lesson is that a route test must post what the template
    emits.  Two cases: the ordinary rename saves with the destination left
    alone, and the one submission the absent key disarmed in the schema --
    the stored destination chosen as the SOURCE -- is refused with the
    schema's own sentence rather than reaching the CHECK constraint (the
    step's adversarial review found the first cut surfacing that as the
    name-collision flash).
    """

    def test_a_rename_without_a_destination_key_saves_and_moves_nothing(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """An absent ``to_account_id`` is "not moving": the save proceeds, the column stays."""
        with app.app_context():
            loan = _mortgage(seed_user, "Paid")
            # The loan door's own payment: a MONTHLY rule bound to the first
            # installment, so the regeneration the save runs generates nothing
            # before origination (ruling R-C) and only the door is under test.
            standing = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            stored_start = standing.recurrence_rule.starts_on

            resp = _post_what_a_pinned_form_emits(
                auth_client, standing, name="Standing, renamed",
            )

            assert resp.status_code == 302, _flashes(auth_client)
            assert resp.headers["Location"].endswith("/transfers"), _flashes(auth_client)
            saved = _reload(standing)
            assert saved.name == "Standing, renamed"
            assert saved.to_account_id == loan.id
            assert saved.recurrence_rule.starts_on == stored_start

    @pytest.mark.parametrize("repeats", [
        pytest.param(True, id="the pinned standing payment (no destination key at all)"),
        pytest.param(False, id="a partial update of a one-time transfer (destination key omitted)"),
    ])
    def test_the_stored_destination_chosen_as_the_source_is_refused(
        self, app, auth_client, seed_user, seed_periods, repeats,  # pylint: disable=unused-argument
    ):
        """Source set to the stored destination, no destination key: refused with the schema's sentence.

        The schema's ``_reject_same_account_transfer`` grades only a submission
        carrying BOTH keys, so this pair reached the write and the CHECK
        constraint, whose ``IntegrityError`` the name-collision handler
        translated into "a recurring transfer with that name already exists".
        The route grades the pair the write would LEAVE
        (``_first_template_fk_refusal``) and refuses it ahead of every write:
        nothing changes, the redirect is the edit form, and the flash is the
        same sentence the create form shows.

        Both shapes, because the rule is about partial updates and not about
        pinned forms: the pinned standing payment's form cannot post the key;
        an API client editing a one-time transfer may simply omit it.

        NEGATIVE CONTROL: drop the effective-pair rule from
        ``_first_template_fk_refusal`` and the flash reads "already exists".
        """
        with app.app_context():
            loan = _mortgage(seed_user, "Paid")
            template = (
                make_loan_payment_template(db.session, seed_user, loan) if repeats
                else _template_into(seed_user, loan, "Into the loan", repeats=False)
            )
            db.session.commit()
            source_before = template.from_account_id
            version_before = template.version_id

            if repeats:
                resp = _post_what_a_pinned_form_emits(
                    auth_client, template, from_account_id=str(loan.id),
                )
            else:
                resp = auth_client.post(f"/transfers/{template.id}", data={
                    "name": template.name,
                    "from_account_id": str(loan.id),
                    "version_id": str(template.version_id),
                })

            assert resp.status_code == 302
            assert resp.headers["Location"].endswith(f"/transfers/{template.id}/edit")
            flashes = _flashes(auth_client)
            assert SAME_ACCOUNT_TRANSFER_MESSAGE in flashes, flashes
            assert not any("already exists" in message for message in flashes), flashes
            saved = _reload(template)
            assert saved.from_account_id == source_before
            assert saved.to_account_id == loan.id
            assert saved.version_id == version_before, "a refusal writes nothing"


class TestTheEditProducer:
    """``loan_destination_locks_for_edit`` read directly, off a pass."""

    def test_the_three_shapes(self, app, seed_user, seed_periods):  # pylint: disable=unused-argument
        """Savings excludes nothing; a repeating loan transfer excludes its loan; a one-off keeps it.

        Ascending by account id in every set, and the stop set a subset of the
        start set (which the value itself refuses to violate).
        """
        with app.app_context():
            paid = _mortgage(seed_user, "Paid")
            make_loan_payment_template(db.session, seed_user, paid)
            unpaid = _mortgage(seed_user, "Unpaid")
            savings = _template_into(
                seed_user, _savings(seed_user), "To Savings", repeats=True,
            )
            second = _template_into(seed_user, paid, "Second into Paid", repeats=True)
            one_off = _template_into(seed_user, unpaid, "One-off", repeats=False)
            db.session.commit()
            ctx = _pass(seed_user)

            assert loan_destination_locks_for_edit(savings, ctx) == LoanDestinationLocks(
                start_derived_for=(paid.id, unpaid.id),
                stop_derived_for=(unpaid.id,),
            )
            assert loan_destination_locks_for_edit(second, ctx) == LoanDestinationLocks(
                start_derived_for=(unpaid.id,),
                stop_derived_for=(unpaid.id,),
            )
            assert loan_destination_locks_for_edit(one_off, ctx) == LoanDestinationLocks(
                start_derived_for=(paid.id, unpaid.id),
                stop_derived_for=(unpaid.id,),
            )

    def test_a_pinned_definition_by_either_identity(
        self, app, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Both arms of the R-R76 union answer the pinned value, sets empty."""
        with app.app_context():
            loan = _mortgage(seed_user, "Paid")
            _mortgage(seed_user, "Unpaid")
            standing = _template_into(seed_user, loan, "Standing", repeats=True)
            settings_carrying = make_loan_payment_template(db.session, seed_user, loan)
            db.session.commit()
            ctx = _pass(seed_user)

            pinned = LoanDestinationLocks(
                start_derived_for=(), stop_derived_for=(),
                pinned_reason=LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION,
            )
            assert loan_destination_locks_for_edit(standing, ctx) == pinned
            assert loan_destination_locks_for_edit(settings_carrying, ctx) == pinned

    def test_an_owner_with_no_loan(self, app, seed_user, seed_periods):  # pylint: disable=unused-argument
        """Two empty sets and no pin: the script listens for nothing."""
        with app.app_context():
            template = _template_into(
                seed_user, _savings(seed_user), "To Savings", repeats=True,
            )
            db.session.commit()

            assert loan_destination_locks_for_edit(
                template, _pass(seed_user),
            ) == LoanDestinationLocks(start_derived_for=(), stop_derived_for=())


class TestTheValueRefusesToDisagreeWithItself:
    """``LoanDestinationLocks.__post_init__``: the two invariants fire."""

    @pytest.mark.parametrize("start, stop", [
        pytest.param((7,), (), id="pinned yet derives a start"),
        pytest.param((7,), (7,), id="pinned yet derives both"),
    ])
    def test_a_pinned_value_carries_no_set(self, start, stop):
        """A definition that cannot move has no destination choice to grade."""
        with pytest.raises(ValueError, match="pinned"):
            LoanDestinationLocks(
                start_derived_for=start, stop_derived_for=stop,
                pinned_reason=LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION,
            )

    def test_the_stop_set_is_a_subset_of_the_start_set(self):
        """Every loan that derives the stop is a loan, and every loan derives the start."""
        with pytest.raises(ValueError, match="without deriving the start"):
            LoanDestinationLocks(start_derived_for=(7,), stop_derived_for=(7, 9))

    def test_the_two_shapes_that_construct(self):
        """CONTROL: an unpinned pair and a pinned empty value both stand."""
        LoanDestinationLocks(start_derived_for=(7, 9), stop_derived_for=(9,))
        LoanDestinationLocks(
            start_derived_for=(), stop_derived_for=(),
            pinned_reason=LOAN_PAYMENT_CANNOT_CHANGE_DESTINATION,
        )
