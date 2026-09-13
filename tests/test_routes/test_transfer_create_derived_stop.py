"""A stop stated at CREATE for a loan whose payment this would be is refused.

Plan step **R7d-f-3**, ruling **R-R60**.  The generic transfer form offers
every active account as a destination, so a loan's recurring payment can be
created there -- and until this step the form accepted a closing bound for it
that the next loan edit silently overwrote with the payoff, while the composed
door read the stored date as the chokepoints' cache from the start (plan
ledger row **N-512**).  The edit form has refused such a bound since plan step
R7b-4 (``LOAN_PAYMENT_BOUND_IS_DERIVED``); this is the create door's half,
landed beside ``settle_first_occurrence`` (in ``_loan_destination`` since plan
step R7d-f-5) because the route module stands at pylint's line cap.

**What "stated" means at create is a REAL stop -- a date or a count -- and
not the key's presence** (developer ruling 2026-09-12).  The create form's
server render cannot lock the "Ends" control, so it emits the select enabled
with "Never" preselected and an ordinary browser posts ``never`` unless the
script's lock ran; refusing on presence would make the script the control,
which ruling **R-R60** demotes to an affordance.  Two cases here pin that
reading, and their docstrings say what a presence rule would do to them.

Every refusal case asserts that NOTHING was persisted, and every acceptance
case reads the stored bound back: a door that flashed the right sentence and
saved anyway, or saved a bound other than the one posted, would pass a
status-code assertion.  The browser's half -- that the row actually locks --
is ``tests/manual/verify_recurrence_form.py``'s, because rendered HTML cannot
tell a control a script disabled from one it never touched.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.enums import AcctTypeEnum, RecurrenceUnitEnum
from app.extensions import db
from app.models.category import Category
from app.models.transfer import Transfer
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_form_refusals import LOAN_PAYMENT_BOUND_IS_DERIVED
from app.routes._loan_destination import loan_destination_locks
from app.schemas.validation import end_bound_before_start_message
from tests._test_helpers import (
    cadence_payload,
    create_account_of_type,
    create_loan_account,
    freeze_today,
    make_loan_payment_template,
    make_transfer_template,
)

#: The day every request here is made on, frozen so "today" cannot drift past
#: the schedule ``seed_periods`` builds (2026-01-02 through May) and so the
#: loan below has not originated yet when the payment is created -- the
#: shape ``test_loan_recurrence_start_bound`` measures the derived start on.
_TODAY = date(2026, 3, 1)

#: The loan's origination and its derived first installment (payment day 1 of
#: the month after origination, ``rate_period_engine.first_installment_date``).
_ORIGINATION = date(2026, 4, 15)
_FIRST_INSTALLMENT = date(2026, 5, 1)

#: A stop AFTER the derived start, so the inverted-window twin stays quiet
#: and only the rule under test can refuse it.
_LATER_STOP = date(2030, 1, 1)

#: A stop BEFORE the derived start: both refusals apply, and which one speaks
#: is the order under test.
_EARLIER_STOP = date(2026, 1, 1)

#: The name every POST here gives its template, so "nothing was persisted"
#: is one query rather than a count that a fixture's own rows could move.
_NAME = "R7d-f-3 payment"


@pytest.fixture(autouse=True)
def _frozen(monkeypatch):
    """Freeze today ahead of the loan's origination."""
    freeze_today(monkeypatch, _TODAY)


def _mortgage(seed_user, name="Mortgage"):
    """Return a mortgage originating after today, payment day 1."""
    return create_loan_account(
        seed_user, db.session, name=name,
        principal=Decimal("200000.00"), rate=Decimal("0.05000"),
        term=360, origination_date=_ORIGINATION, payment_day=1,
        account_type=AcctTypeEnum.MORTGAGE,
    )


def _post_monthly_transfer(auth_client, seed_user, to_account, **bound):
    """POST /transfers: a MONTHLY cadence, no ``starts_on``, and *bound*.

    What the create form posts for a loan destination: the "Starts on" control
    is locked and sends no key.  *bound* is the "Ends" control's three keys as
    the browser posts them -- nothing at all for a locked row, ``never`` for
    the untouched default, a mode and its one value for a real stop.

    Args:
        auth_client: The signed-in test client.
        seed_user: The owner fixture.
        to_account: The destination :class:`Account`.
        **bound: ``recurrence_end_mode`` and its value input, if any.

    Returns:
        The Flask response, redirect unfollowed so the flash stays readable.
    """
    category = Category(
        user_id=seed_user["user"].id, group_name="Debt", item_name="Loan",
    )
    db.session.add(category)
    db.session.commit()
    with auth_client.application.app_context():
        monthly = cadence_payload(
            unit=RecurrenceUnitEnum.MONTH, states_a_start=False,
        )
    return auth_client.post("/transfers", data={
        "name": _NAME,
        "from_account_id": str(seed_user["account"].id),
        "to_account_id": str(to_account.id),
        "default_amount": "1200.00",
        "category_id": str(category.id),
        **monthly,
        **bound,
    })


def _flashes(auth_client):
    """Return the unconsumed flash messages left by an unfollowed redirect."""
    with auth_client.session_transaction() as sess:
        return [message for _category, message in sess.get("_flashes", [])]


def _persisted(to_account):
    """Return the template this module's POST created into *to_account*, or None."""
    return (
        db.session.query(TransferTemplate)
        .filter_by(to_account_id=to_account.id, name=_NAME)
        .one_or_none()
    )


def _assert_refused_and_nothing_persisted(auth_client, resp, to_account):
    """The shared shape of every refusal here."""
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/transfers/new")
    assert LOAN_PAYMENT_BOUND_IS_DERIVED in _flashes(auth_client)
    assert _persisted(to_account) is None, (
        "the refusal flashed and the template was saved anyway"
    )
    assert not (
        db.session.query(Transfer)
        .filter(Transfer.to_account_id == to_account.id)
        .all()
    ), "a refused create must generate nothing"


class TestAStopStatedForANewLoanPayment:
    """The door: which "Ends" submissions a payment-less loan destination refuses."""

    @pytest.mark.parametrize("bound", [
        pytest.param(
            {"recurrence_end_mode": "on_date",
             "end_date": _LATER_STOP.isoformat()},
            id="on a date",
        ),
        pytest.param(
            {"recurrence_end_mode": "after_occurrences", "max_occurrences": "12"},
            id="after a count",
        ),
    ])
    def test_a_real_stop_is_refused_where_the_loan_holds_no_payment(
        self, auth_client, seed_user, seed_periods, bound,  # pylint: disable=unused-argument
    ):
        """Both stop shapes, because the rule is about the stop and not its form.

        The loan holds no active recurring transfer, so the definition being
        created is its payment the moment it exists, and a loan payment
        carries no authored stop (ruling **R-R59**).

        NEGATIVE CONTROL: delete the ``_refuse_stop_on_a_new_loan_payment``
        call from ``settle_first_occurrence`` and both cases save -- the
        route redirects to the list and the template holds the posted bound.
        """
        loan = _mortgage(seed_user)
        db.session.commit()

        resp = _post_monthly_transfer(auth_client, seed_user, loan, **bound)

        _assert_refused_and_nothing_persisted(auth_client, resp, loan)

    def test_never_is_accepted_as_the_unbounded_rule(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The form's own default is not a stop the owner stated.

        The server renders the "Ends" select with "Never" preselected, so a
        browser whose script did not lock the row posts ``never`` for a stop
        it never chose -- and ``never`` authors exactly the unbounded rule
        the loan's payment carries.  A presence rule would refuse this create
        outright (developer ruling 2026-09-12, the value reading).

        NEGATIVE CONTROL: change ``bound == NEVER_ENDS`` to ``False`` in
        ``_refuse_stop_on_a_new_loan_payment`` and this is refused.
        """
        loan = _mortgage(seed_user)
        db.session.commit()

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan, recurrence_end_mode="never",
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        template = _persisted(loan)
        assert template is not None, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.end_date is None and rule.max_occurrences is None
        # The start was derived beside it: the same door, both bounds.
        assert rule.starts_on == _FIRST_INSTALLMENT

    def test_an_absent_bound_is_accepted(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """What the locked row posts: nothing, which the create reads as never.

        The affordance disables the control, and a disabled control sends no
        key at all.  This is the submission the browser produces when the
        script ran, and it must be the one the door was written for.
        """
        loan = _mortgage(seed_user)
        db.session.commit()

        resp = _post_monthly_transfer(auth_client, seed_user, loan)

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        template = _persisted(loan)
        assert template is not None, _flashes(auth_client)
        rule = template.recurrence_rule
        assert rule.end_date is None and rule.max_occurrences is None

    def test_a_second_transfer_into_a_paid_loan_keeps_its_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A loan that already holds a payment makes this the owner's stop.

        The standing payment is the loan's OLDEST active recurring transfer,
        so a second one is not the payment and its authored stop binds beside
        the derived one (ruling **R-R56**'s second-transfer clause).  The
        posted date is read back off the stored rule, not inferred from the
        redirect.

        NEGATIVE CONTROL: delete the ``_loan_holds_no_active_payment`` test
        from ``_refuse_stop_on_a_new_loan_payment`` and this is refused --
        the rule would fire for every loan destination.
        """
        loan = _mortgage(seed_user)
        make_loan_payment_template(db.session, seed_user, loan)
        db.session.commit()

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan,
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        template = _persisted(loan)
        assert template is not None, _flashes(auth_client)
        assert template.recurrence_rule.end_date == _LATER_STOP

    def test_an_archived_payment_does_not_count(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Archiving is the door to stop a payment early; the next one is the payment.

        The identity reads ACTIVE transfers, so a loan whose only payment is
        archived holds none, and the definition created next is its payment
        -- with no stop of its own.  A door that counted archived rows would
        let a stop through here that the next loan edit overwrites.
        """
        loan = _mortgage(seed_user)
        archived = make_loan_payment_template(db.session, seed_user, loan)
        archived.is_active = False
        db.session.commit()

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan,
            recurrence_end_mode="on_date", end_date=_LATER_STOP.isoformat(),
        )

        _assert_refused_and_nothing_persisted(auth_client, resp, loan)

    def test_the_derived_rule_speaks_before_the_inverted_window(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A stop both before the start and not the owner's to state: one message.

        Both refusals apply to a date before the derived first installment on
        a payment-less loan.  The one that speaks is the more fundamental --
        the owner cannot state a stop here at all -- because "this ends before
        it starts" would send them to move a date they cannot state, which is
        the order the edit door asks the same two rules in.

        NEGATIVE CONTROL: swap the two calls in ``settle_first_occurrence``'s
        loan branch and the inverted-window sentence is flashed instead.
        """
        loan = _mortgage(seed_user)
        db.session.commit()

        resp = _post_monthly_transfer(
            auth_client, seed_user, loan,
            recurrence_end_mode="on_date", end_date=_EARLIER_STOP.isoformat(),
        )

        _assert_refused_and_nothing_persisted(auth_client, resp, loan)
        assert end_bound_before_start_message(
            _EARLIER_STOP, _FIRST_INSTALLMENT,
        ) not in _flashes(auth_client)

    def test_a_non_loan_destination_keeps_its_stop(
        self, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The rule is scoped to loans; a savings transfer's stop is its owner's.

        CONTROL for the refusal's reach: ``_loan_holds_no_active_payment``
        answers ``True`` of a savings account too, and only the loan branch
        of ``settle_first_occurrence`` asks it.  A savings destination states
        its own start (nothing derives one), so the payload carries one.
        """
        savings = create_account_of_type(
            seed_user, db.session, "Savings", "Sav",
            anchor_balance=Decimal("100.00"),
        )
        db.session.commit()
        with auth_client.application.app_context():
            monthly = cadence_payload(
                unit=RecurrenceUnitEnum.MONTH, starts_on=_TODAY,
            )
        category = Category(
            user_id=seed_user["user"].id, group_name="Savings", item_name="Sav",
        )
        db.session.add(category)
        db.session.commit()

        resp = auth_client.post("/transfers", data={
            "name": _NAME,
            "from_account_id": str(seed_user["account"].id),
            "to_account_id": str(savings.id),
            "default_amount": "50.00",
            "category_id": str(category.id),
            **monthly,
            "recurrence_end_mode": "on_date",
            "end_date": _LATER_STOP.isoformat(),
        })

        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/transfers")
        template = _persisted(savings)
        assert template is not None, _flashes(auth_client)
        assert template.recurrence_rule.end_date == _LATER_STOP


class TestTheCreateFormNamesTheDestinationsThatDeriveAStop:
    """The affordance's data: which loans lock the "Ends" row, from the server."""

    def test_the_two_sets_the_form_emits(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Every loan derives the start; only a payment-less loan the stop too.

        Two loans: one already holding a payment, one not.  Both are in
        ``data-loan-account-ids`` (a second transfer into a paid loan still
        has its start derived, plan ledger row **D50**) and only the
        payment-less one is in ``data-loan-account-ids-without-payment``.

        NEGATIVE CONTROL: drop the ``_loan_holds_no_active_payment`` filter
        from ``loan_destination_locks`` and the paid loan appears in the
        second set, so the script would lock a stop the door honours.
        """
        with app.app_context():
            paid = _mortgage(seed_user, name="Paid")
            make_loan_payment_template(db.session, seed_user, paid)
            unpaid = _mortgage(seed_user, name="Unpaid")
            db.session.commit()

            html = auth_client.get("/transfers/new").data.decode()

            start_set = html.split('data-loan-account-ids="')[1].split('"')[0]
            stop_set = html.split(
                'data-loan-account-ids-without-payment="',
            )[1].split('"')[0]
            assert start_set == f"{paid.id},{unpaid.id}"
            assert stop_set == str(unpaid.id)

    def test_the_locks_producer(self, app, seed_user, seed_periods):  # pylint: disable=unused-argument
        """The value the route emits, read directly: ascending, and a subset.

        An owner with no loan gets two empty sets, so the script listens for
        nothing.  An archived payment does not keep a loan out of the second
        set, for the reason the door gives.
        """
        with app.app_context():
            user_id = seed_user["user"].id
            empty = loan_destination_locks(user_id)
            assert empty.start_derived_for == ()
            assert empty.stop_derived_for == ()

            paid = _mortgage(seed_user, name="Paid")
            make_loan_payment_template(db.session, seed_user, paid)
            unpaid = _mortgage(seed_user, name="Unpaid")
            was_paid = _mortgage(seed_user, name="Was Paid")
            archived = make_loan_payment_template(db.session, seed_user, was_paid)
            archived.is_active = False
            db.session.commit()

            locks = loan_destination_locks(user_id)
            assert locks.start_derived_for == (paid.id, unpaid.id, was_paid.id)
            assert locks.stop_derived_for == (unpaid.id, was_paid.id)

    def test_the_ends_help_carries_both_sentences_on_the_transfer_forms_alone(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The copy the script swaps rides on the row only where the row can lock.

        The transfer form's destination is chosen in the form on a create and
        can be changed on an edit, so its "Ends" help carries the locked and
        the open sentence for the script to choose between -- on BOTH renders
        since plan step R7d-f-5.  **REVERSED for the edit form by ruling**
        (developer 2026-09-12, ruling **R-R79**; CLAUDE.md rule 5's
        exception): until then the edit render passed
        ``swappable=(template is none)`` and this case asserted it carried
        neither, on the premise that an edit form "already knows" its
        destination -- false once the update door derives bounds for a
        destination MOVE (plan step R7d-f-4).  The transaction form still
        carries neither: it has no destination and can never be a loan
        payment.  The locked sentence holds the word "updated", which
        ``test_template_flags`` asserts no transaction page renders without a
        save behind it.
        """
        with app.app_context():
            savings = create_account_of_type(
                seed_user, db.session, "Savings", "Sav",
                anchor_balance=Decimal("100.00"),
            )
            stored = make_transfer_template(
                db.session, seed_user, to_account=savings,
            )
            db.session.commit()

            create_form = auth_client.get("/transfers/new").data.decode()
            edit_form = auth_client.get(
                f"/transfers/{stored.id}/edit",
            ).data.decode()
            transaction_form = auth_client.get("/templates/new").data.decode()

        def ends_help_tag(html):
            return html.split('id="end-bound-help"')[1].split(">")[0]

        for page in (create_form, edit_form):
            assert "data-locked-text=" in ends_help_tag(page)
            assert "data-open-text=" in ends_help_tag(page)
        assert "data-locked-text=" not in ends_help_tag(transaction_form)
        assert "data-open-text=" not in ends_help_tag(transaction_form)
