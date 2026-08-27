"""What has already been DECIDED: the register page and its two doors.

Plan step **bank_import:X-gf-2**, ruling **bank_import:R-GX**.  The review
screen is the exception QUEUE; this is the other half of that split -- the
merchant answers already given, restatable, and the matches already accepted,
each with its undo.

**The route's own subjects, none of which the service tests can see**:
OWNERSHIP (the security response rule's 404 for both "not found" and "not
yours"), the FORM PAYLOAD, the unit of work, and what the screen SAYS.

**Every payload here is what the template actually emits**
(:mod:`tests.test_routes._statement_forms`).  That is a rule this arc
has paid for twice, and the split makes it sharper rather than softer: the
review screen no longer renders a row for a merchant that HAS an answer, so a
hand-written payload restating one would be testing a submission no browser can
make.  Those cases live here, against the form that does emit them.

**One control here MOVES MONEY and it is the undo**: releasing a match removes
the rows that act created (ruling **R-GG**).  Stating a merchant answer moves
none and can move none.
"""

from datetime import timedelta

import pytest

from app.enums import StatusEnum
from app.models.account import Account
from app.models.user import User, UserSettings
from app.models.statement_match import StatementMatch
from app.models.transaction import Transaction
from app.models.transaction_entry import TransactionEntry
from app.routes.accounts import (
    statement_register as statement_register_route,
)
from app.services import auth_service, entry_service
from app.services.statement_match import (
    REGISTER_LIMIT,
    ReviewScope,
    file_new_swipes,
)
from tests.test_routes._statement_forms import (
    match_item,
    one_pass,
    record_line,
    rule_form_controls,
    rule_item,
)
from tests.test_services.test_statement_match._builders import (
    a_bank_line,
    a_merchant,
    a_rule,
    a_transaction,
    an_envelope,
    an_import,
    an_unexplained_outflow,
)


def _register_url(account_id):
    """Return the register page's URL for *account_id*."""
    return f"/accounts/{account_id}/statements/register"


def _review_url(account_id):
    """Return the review page's URL for *account_id*."""
    return f"/accounts/{account_id}/statements/review"


def _merchants_url(account_id):
    """Return the QUEUE's merchant-rule POST URL for *account_id*.

    Where a FIRST answer is given: the review screen is what asks about a
    merchant nobody has answered for.
    """
    return f"/accounts/{account_id}/statements/review/merchants"


def _restate_url(account_id):
    """Return the REGISTER's merchant-rule POST URL for *account_id*.

    Where an answer already given is CHANGED.  Two doors rather than one
    because the two answer with different screens, and the URL is what says
    which surface a submission came from (plan step ``bank_import:X-gf-2``).
    """
    return f"/accounts/{account_id}/statements/register/merchants"


def _attribute(page: str, name: str) -> str:
    """Return the value of the first *name* attribute on *page*.

    **An assertion about a DIALOG has to read the dialog.**  This page renders
    every act's own figure in its amount column, so a body-wide search for a
    figure is satisfied by a cell that means something else entirely -- which
    is how the undo's money clause came to be graded by markup that would
    survive its deletion.

    Args:
        page: The rendered page, as text.
        name: The attribute to read.

    Returns:
        Its value, unescaped enough for a substring assertion.

    Raises:
        AssertionError: When the page carries no such attribute -- an absence
            an ``in`` test would report as a failed assertion about wording.
    """
    marker = f'{name}="'
    assert marker in page, f"the page carries no {name} attribute at all"
    start = page.index(marker) + len(marker)
    return page[start:page.index('"', start)].replace("&#34;", '"')


class TestTheReleasePost:
    """The undo, on the surface that lists every act it can act on.

    It posted to the review screen's own release door until plan step
    ``bank_import:X-gf-2`` and redirected there; the accepted matches are here
    now, so the door that acts on one is here and brings the owner back here.
    """

    def test_it_releases_and_leaves_the_day(self, auth_client, db, seed_user):
        """What comes back is the QUESTION, not the date."""
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=bank_day,
        )
        txn = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=bank_day + timedelta(days=3),
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=match_item(lines=[line], transactions=[txn]),
        )
        match_id = db.session.query(StatementMatch.id).scalar()

        response = auth_client.post(
            f"{_register_url(seed_user['account'].id)}/release",
            data={"match_id": match_id},
            follow_redirects=True,
        )

        assert b"Match undone" in response.data
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == 0
        assert txn.settled_on == bank_day
        # A match between rows that already existed removes nothing, so the
        # receipt says nothing about removals -- the control for the case
        # below, which does.
        assert b"row(s) that match had created" not in response.data

    def test_it_removes_what_the_act_CREATED_and_says_so(
        self, auth_client, db, seed_user,
    ):
        """Plan step **bank_import:X-f6f**, ruling **R-GG**.

        The create arm's inverse, driven through the two real POSTs: record a
        `-$57.96` swipe as a purchase in a new envelope, then undo it.  Both
        rows go, and the flash NAMES them and the money -- a destructive act
        whose receipt says only "done" leaves the owner unable to tell a no-op
        from a much larger removal than they meant.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        line = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=bank_day,
            description="POINT OF SALE DEBIT L340 WAL-MART",
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=record_line(
                line, destination="new", name="Walmart",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )
        db.session.expire_all()
        assert db.session.query(TransactionEntry).count() == 1, (
            "the recording must really have happened, or the undo below "
            "proves nothing"
        )
        match_id = db.session.query(StatementMatch.id).scalar()

        response = auth_client.post(
            f"{_register_url(seed_user['account'].id)}/release",
            data={"match_id": match_id},
            follow_redirects=True,
        )

        assert b"Match undone" in response.data
        assert b"removed the 2 row(s) that match had created" in response.data
        assert b"-57.96" in response.data
        db.session.expire_all()
        assert db.session.query(TransactionEntry).count() == 0
        assert db.session.query(Transaction).filter(
            Transaction.name == "Walmart",
        ).count() == 0

    def test_the_page_NAMES_what_the_undo_would_remove(
        self, auth_client, db, seed_user,
    ):
        """The Undo control carries the confirmation and the figure.

        ``data-confirm`` is this project's destructive-action pattern.  Every
        press carries one since ruling **bank_import:R-GY**; what this grades
        is the arm that names ROWS and MONEY, which is the one whose wording
        has to come from the door's own derivation.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        line = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=bank_day,
            description="POINT OF SALE DEBIT L340 WAL-MART",
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=record_line(
                line, destination="new", name="Walmart",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        # **The figure is read INSIDE the dialog and inside the row's own
        # warning, never anywhere on the page.**  Every act renders its own
        # `money(group.amount)` in the amount column, and here that figure is
        # the same `-$57.96` -- so a body-wide search for it was satisfied by a
        # cell that says what the BANK showed, and deleting the money clause
        # from both the dialog and the warning left this test green.  Found by
        # adversarial test-quality review of this step, 2026-08-27; it is the
        # figure plan step X-f6f and ruling R-GD exist for.
        dialog = _attribute(page, "data-confirm")
        warning = page[
            page.index("Undo removes"):page.index("</div>", page.index(
                "Undo removes",
            ))
        ]

        assert "it REMOVES the 2 row(s) this match created" in dialog
        # The macro's own spelling: the sign goes BEFORE the dollar symbol.
        assert "worth -$57.96 of money the app currently records" in dialog
        assert "Undo removes 2 row(s) this" in warning
        assert "-$57.96 of recorded" in warning

    def test_the_page_says_REFUSED_where_the_undo_would_be(
        self, auth_client, db, seed_user,
    ):
        """A panel promising a removal the button refuses is the defect.

        The owner has edited the purchase the act created, so the undo stops.
        The row must say THAT rather than go on listing rows it will not
        remove -- the screen and the door read one derivation
        (``planned_removals``), and this is the arm that proves the TEMPLATE
        reads it too.
        """
        statement = an_import(seed_user)
        bank_day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        line = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=bank_day,
            description="POINT OF SALE DEBIT L340 WAL-MART",
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=record_line(
                line, destination="new", name="Walmart",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )
        db.session.expire_all()
        entry = db.session.query(TransactionEntry).one()
        entry_service.update_entry(
            entry.id, seed_user["user"].id, description="Walmart -- hose",
        )
        db.session.commit()

        page = auth_client.get(_register_url(seed_user["account"].id)).data

        assert b"Undo is refused:" in page
        assert b"you have edited that row since" in page
        assert b"Undo removes" not in page
        assert b"data-confirm=" not in page


class TestTheRestatePost:
    """Changing an answer already given, on the surface that shows it.

    Plan step ``bank_import:X-gf-2``, ruling **bank_import:R-GX**.  Every case
    here gives its first answer through the QUEUE's door -- which is how an
    answer is really given, the merchant having had none -- and then reads it
    back from the REGISTER and posts THAT page's own form.  That is the whole
    of what the split changed for these: the review screen renders no row for
    an answered merchant, so a case restating one against it would be grading a
    submission no browser can make.

    **A rule is RESTATED and never UN-STATED** (ruling **R-GS**), which is why
    none of these can empty the register: what they assert is that the answer
    the door stored is the answer the control renders, on every arm.
    """
    def test_a_stated_rule_is_REVOKED_by_answering_ask_me_every_time(
        self, auth_client, db, seed_user,
    ):
        """THE FIRING CONTROL for revocation, over the wire.

        **This case replaced ``test_a_stated_rule_can_be_WITHDRAWN_from_the_
        screen``**, which posted ``unset`` and asserted the row was gone.
        Ruling **R-GS** (developer, 2026-08-25) removed the withdrawal: a rule
        is only ever restated, and *ask me every time* is the answer that
        revokes a destination.  What the case exists for is unchanged -- a rule
        is a statement about today's budget, and when the credit-card arc gives
        Capital One its own account the Checking-side answer stops being right,
        so the owner must be able to take a destination back from this screen.

        The named-arm point the old case also made is kept and moved to the new
        value: ``BaseSchema``'s ``@pre_load`` normalizer drops every ``""`` a
        form submits, so an arm spelled as an absence is an arm that never
        arrives.

        **It is driven through the REGISTER's own rendered form** (plan step
        ``bank_import:X-gf-2``), and that is the point rather than a detour:
        the answer exists, so the review screen renders no row for this
        merchant and a payload restating it there is one no browser can make.
        Every other case in this class posts the page back UNCHANGED, so this
        is the one that shows the register's door RECORDS -- without it the
        page's whole reason to exist has no end-to-end control.  Found by
        adversarial test-quality review of this step, 2026-08-27.
        """
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )

        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        assert db.session.query(MerchantRule).one().template_id == (
            envelope.template_id
        )

        # The register's OWN form, with the one control the owner would move.
        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()
        submitted = rule_form_controls(page)
        assert submitted["rule-0"] == f"t:{envelope.template_id}", (
            "the register must be rendering the stored answer, or changing it "
            "below proves nothing about this door"
        )
        submitted["rule-0"] = "ask"

        response = auth_client.post(
            _restate_url(seed_user["account"].id), data=submitted,
        )

        assert response.status_code == 200
        # The RECEIPT says it changed, which is what tells a restatement from
        # the no-op every other case here submits.
        assert b"Nothing changed" not in response.data
        row = db.session.query(MerchantRule).one()
        assert row.template_id is None
        # ...and NOT the other container-less answer.  A revocation that landed
        # on *never a purchase* would bar every future line from this merchant,
        # which is the opposite of what the owner asked for.
        assert row.never_a_purchase is False
        # ...and the answer the door stored is what the page now renders back.
        assert rule_form_controls(
            auth_client.get(
                _register_url(seed_user["account"].id),
            ).data.decode(),
        )["rule-0"] == "ask"

    def test_EVERY_stored_answer_comes_back_SELECTED(
        self, auth_client, db, seed_user,
    ):
        """One case per arm, because a select with none selected RE-AIMS.

        A browser shows and submits a single-select's FIRST option when none
        carries ``selected``.  **On THIS surface that option is a real
        recurring envelope**, because *I have not said* is rendered only for a
        merchant with no rule (ruling **R-GS**) and every row here has one --
        so losing ``selected`` on any arm silently points the answer at
        whichever envelope sorts first, and the owner's next Save files that
        merchant's money there.

        The wording this docstring carried until 2026-08-27 was the REVIEW
        screen's -- "the first option here is *I have not said*, which the door
        reads as a withdrawal" -- and it decayed twice over on the way here:
        R-GS removed the withdrawal, and the split removed that option from
        this control.  The measurement it cites (adversarial test-quality
        review 2026-08-19, each arm able to lose ``selected`` with the suite
        green) was taken against the review screen and is kept as the reason
        one case exists per arm.
        """
        envelope = an_envelope(seed_user)
        category = seed_user["categories"]["Groceries"]
        for index, merchant in enumerate(("Alpha", "Beta", "Gamma")):
            an_unexplained_outflow(seed_user, merchant=merchant, sequence=index)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=one_pass(
                rule_item(0, a_merchant(seed_user, "Alpha").id, answer=f"t:{envelope.template_id}"),
                rule_item(1, a_merchant(seed_user, "Beta").id, answer="new", name="Beta Fund",
                        category_id=category.id),
                rule_item(2, a_merchant(seed_user, "Gamma").id, answer="never"),
            ),
        )

        body = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        for index, expected in enumerate(
            (f"t:{envelope.template_id}", "new", "never"),
        ):
            marker = body.index(f'name="rule-{index}"')
            control = body[marker:body.index("</select>", marker)]
            assert f'<option value="{expected}" selected>' in control, expected
            # ...and it is the ONLY one, so no browser has to choose.
            assert control.count("selected") == 1, expected

    def test_a_stored_answer_whose_TEMPLATE_was_turned_off_still_shows(
        self, auth_client, db, seed_user,
    ):
        """The stale-answer option, end to end through the screen.

        Deactivating a template does not delete the rule naming it, and
        ``offerable_templates`` stops listing it -- so without an option of its
        own the select falls back to *I have not said* and the next Save
        withdraws an answer the owner never touched.
        """
        from app.models.transaction_template import (  # pylint: disable=import-outside-toplevel
            TransactionTemplate,
        )
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )

        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        db.session.query(TransactionTemplate).filter(
            TransactionTemplate.id == envelope.template_id,
        ).update({"is_active": False})
        db.session.commit()

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()
        marker = page.index('name="rule-0"')
        control = page[marker:page.index("</select>", marker)]
        assert f'<option value="t:{envelope.template_id}" selected>' in control
        assert "no longer offered" in " ".join(control.split())

        # ...and submitting the page back UNCHANGED leaves the answer alone.
        # **The ANSWER, not the row COUNT**: R-GS removed the withdrawal, so no
        # submission can move a count -- what a dropped stale option would
        # really do is RE-AIM the rule at the first offered envelope, which
        # only the stored id can see.  Found by adversarial test-quality
        # review, 2026-08-27.
        auth_client.post(
            _restate_url(seed_user["account"].id),
            data=rule_form_controls(page),
        )
        assert db.session.query(MerchantRule).one().template_id == (
            envelope.template_id
        )

    def test_an_ANSWERED_merchant_is_NOT_offered_I_have_not_said(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GS**: there is no act behind that option once a rule exists.

        It used to be the WITHDRAWAL, and a rule is never un-stated now, so the
        option is not rendered at all -- an option whose submission does
        nothing is a control that says the owner may take an answer back when
        they may not.

        **What a browser would submit is asserted beside its absence**, because
        those are two different failures: dropping the option from the markup
        while leaving the select unselected would make a browser post the FIRST
        option instead, which is a real envelope and would silently re-aim the
        rule.
        """
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()
        marker = page.index('name="rule-0"')
        control = page[marker:page.index("</select>", marker)]

        assert 'value="unset"' not in control
        assert rule_form_controls(page)["rule-0"] == (
            f"t:{envelope.template_id}"
        )

    def test_ASK_ME_EVERY_TIME_is_offered_and_round_trips(
        self, auth_client, db, seed_user,
    ):
        """The fourth answer, end to end through the screen (**R-GS**).

        It is the answer that looks most like the absence of one, so the arm
        that would be missed is the RENDER: a control that stored *ask me every
        time* and then displayed something else would send the owner's next
        Save somewhere they never chose.
        """
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )

        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id, answer="ask",
            ),
        )
        assert db.session.query(MerchantRule).one().never_a_purchase is False

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert "-- ask me every time --" in page
        assert rule_form_controls(page)["rule-0"] == "ask"

        # ...and posting the page straight back changes nothing, which is what
        # says the render and the door agree about which answer this is.
        response = auth_client.post(
            _restate_url(seed_user["account"].id),
            data=rule_form_controls(page),
        )
        assert b"Nothing changed" in response.data
        assert db.session.query(MerchantRule).one().never_a_purchase is False

    def test_a_stored_answer_whose_CATEGORY_was_ARCHIVED_still_round_trips(
        self, auth_client, db, seed_user,
    ):
        """The category select's totality, and the state THIS step created.

        The picker renders active categories only, so an archived one had no
        option carrying the stored value: the select carried no ``selected``
        and a browser posted its first, the EMPTY one. That reaches the door as
        a new-envelope answer with no category and is REFUSED -- so pressing
        Save to answer about one merchant printed "a new envelope needs both a
        name and a category" for another the owner never touched, every pass,
        naming the wrong half.

        **This step is what makes the state reachable.** Before it, deleting a
        category only a rule used hard-deleted it and cascaded the rule away,
        leaving nothing to mis-render; teaching ``category_has_usage`` about
        this table turns that into an ARCHIVE, which is exactly this. Found by
        two adversarial reviews 2026-08-26.

        Driven through the real page and posted back, because the defect is in
        what a BROWSER submits for a control nobody touched -- which is the one
        thing a hand-written payload cannot show.
        """
        from app.models.category import (  # pylint: disable=import-outside-toplevel
            Category,
        )
        from app.models.merchant_rule import (  # pylint: disable=import-outside-toplevel
            MerchantRule,
        )

        an_unexplained_outflow(seed_user)
        category = seed_user["categories"]["Groceries"]
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer="new", name="Amazon Spending",
                category_id=str(category.id),
            ),
        )
        assert db.session.query(MerchantRule).one().category_id == category.id

        db.session.query(Category).filter(
            Category.id == category.id,
        ).update({"is_active": False})
        db.session.commit()

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()
        submitted = rule_form_controls(page)

        # The browser carries the STORED category, not the empty option.
        assert submitted["rule_category-0"] == str(category.id)
        assert "-- archived" in page

        response = auth_client.post(
            _restate_url(seed_user["account"].id), data=submitted,
        )

        # ...so posting the page straight back is a no-op rather than a
        # refusal about a merchant the owner never touched.
        assert response.status_code == 200
        assert b"were not recorded" not in response.data
        assert db.session.query(MerchantRule).one().category_id == category.id


class TestTheRegisterPage:
    """What the page renders, and what it no longer leaves on the queue."""

    @staticmethod
    def _accept_one(auth_client, db, seed_user, ordinal=0, amount="-180.00"):
        """Accept one match through the review screen's own APPLY door.

        Args:
            auth_client: The logged-in client.
            db: The session.
            seed_user: The seeded user bundle.
            ordinal: The line's ordinal, completing its identity.
            amount: The bank line's signed figure.

        Returns:
            The accepted act's ``match_id``.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount=amount, posted_on=day,
            sequence_in_group=ordinal,
        )
        txn = a_transaction(
            seed_user, name=f"Electricity {ordinal}",
            amount=amount.lstrip("-"), status=StatusEnum.DONE, settled_on=day,
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=match_item(lines=[line], transactions=[txn]),
        )
        db.session.expire_all()
        return db.session.query(StatementMatch.id).order_by(
            StatementMatch.id.desc(),
        ).first()[0]

    def test_it_lists_the_answers_and_the_acts(
        self, auth_client, db, seed_user,
    ):
        """Both cards, on one page, from one request.

        The two are what plan step ``bank_import:X-gf-2`` moved off the review
        screen: 442,109 bytes of a 578,523-byte page on the developer's own
        data, and neither of them a decision he was making.
        """
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        self._accept_one(auth_client, db, seed_user, ordinal=1)

        page = auth_client.get(_register_url(seed_user["account"].id))

        assert page.status_code == 200
        assert b"Where your merchants go" in page.data
        assert b'name="rule_merchant-0"' in page.data
        assert b"Accepted matches" in page.data
        assert b"Electricity 1" in page.data

    def test_the_REVIEW_screen_no_longer_carries_either_of_them(
        self, auth_client, db, seed_user,
    ):
        """The other side of the move, which is what makes it a move.

        A panel left on both screens would be two copies of one register, and
        the queue would still be paying to derive it -- the review pass valued
        all 221 of the developer's accepted acts on every render.
        """
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        self._accept_one(auth_client, db, seed_user, ordinal=1)

        page = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert "Accepted matches" not in page
        assert "Electricity 1" not in page
        # ...and the pointer SAYS what went with them, which is finding
        # N-349's signal: the amber "no longer holds" row used to be on this
        # screen and is now one click away, so the link names the reason to
        # follow it rather than leaving the owner to discover one.
        assert "a match that has stopped holding shows up" in page
        # ...the ANSWERED merchant's control is not here either, so no row of
        # it can be submitted from this screen.
        assert 'name="rule_merchant-0"' not in page
        # ...and the way to reach both is rendered rather than remembered.
        assert "/statements/register" in page

    def test_an_UNANSWERED_merchant_stays_on_the_queue(
        self, auth_client, db, seed_user,
    ):
        """The partition's other half, so the assertion above is not vacuous.

        Ruling **R-GJ** turns on this: a merchant a source files as a payment
        to an account the owner holds is parked until they answer, and the
        queue's control is the only place that answer is given.  A split that
        dropped the unanswered rows from BOTH screens would pass every test
        above and hide the one door that lifts a bar.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()

        queue = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()
        register = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert 'name="rule_merchant-0"' in queue
        assert "Merchants you have not answered for" in queue
        assert 'name="rule_merchant-0"' not in register
        assert "not said where any merchant" in register

    def test_it_says_what_can_unmake_a_match_without_touching_it(
        self, auth_client, db, seed_user,
    ):
        """Finding **N-349**'s disclosure, which is what that row is owed.

        Four doors elsewhere in the app remove a row an accepted match names --
        a template's hard delete, the bulk statement archive,
        ``pay_period_write.retire_paydays``' cascade and the recurrence retire
        sweep -- and none of them says a word.  The match is then explaining
        less than it claims, and the owner has no way to learn that is even
        possible.  Enumerating the four into the writer was refused (rule 13:
        two are bulk SQL where a per-row call does not fit), so what is owed is
        the DISCLOSURE, here, where the acts are listed.
        """
        self._accept_one(auth_client, db, seed_user)

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert "A match can stop holding without anybody touching it" in page
        assert "sorted to the top" in page

    def test_ANOTHER_USERS_account_is_a_404(
        self, auth_client, db, seed_user, seed_second_user,
    ):
        """The security response rule, on a page listing money records.

        A register answering for another owner's account would list what they
        matched, what their bank called each line, and offer to undo it.
        """
        response = auth_client.get(
            _register_url(seed_second_user["account"].id),
        )

        assert response.status_code == 404


class TestEveryUndoPressConfirms:
    """Ruling **bank_import:R-GY** (developer, 2026-08-27).

    ``data-confirm`` was attached only where the undo also removed rows the act
    had CREATED, on the argument that a confirmation over a reversible act
    trains the owner to click through the one that is not.  Measured on the
    developer's own data: **0 of his 221 accepted matches were in that state**,
    so all 221 Undo buttons fired on one click with no dialog at all.

    Undoing moves no money and leaves settle days alone.  What it destroys is
    still a record -- which bank lines ARE which of the owner's rows, and
    whether a standing rule filed the act rather than a person (``R-GT``) --
    and re-matching by hand is the only way back.  So the dialog is always
    there and its WORDING is what varies.

    ``TestTheReleasePost`` grades the arm that names rows and money; these
    grade the two the ruling changed.
    """

    def test_an_act_that_removes_NOTHING_still_asks(
        self, auth_client, db, seed_user,
    ):
        """The 221-of-221 case, and the whole of what the ruling changed."""
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        txn = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day,
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=match_item(lines=[line], transactions=[txn]),
        )

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert "data-confirm=" in page
        assert "destroys the record that" in page
        # ...and it says what it does NOT do, because that is what makes the
        # dialog readable rather than alarming.
        assert "No money moves and no settle date changes" in page
        # The removal wording belongs to the other arm and must not appear
        # here: this act created nothing, and a dialog naming rows it will not
        # remove is the promise one derivation exists to prevent.
        assert "it REMOVES the" not in page

    def test_an_act_whose_undo_is_REFUSED_asks_nothing(
        self, auth_client, db, seed_user,
    ):
        """The one press with no dialog is the one that performs nothing.

        A confirmation before a refusal is the dialog-for-nothing the argument
        the ruling overturned was actually about -- and the row already says,
        in the door's own words, why the press will not succeed.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date + timedelta(days=5)
        line = a_bank_line(
            seed_user, statement, amount="-57.96", posted_on=day,
            description="POINT OF SALE DEBIT L340 WAL-MART",
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=record_line(
                line, destination="new", name="Walmart",
                category_id=seed_user["categories"]["Groceries"].id,
            ),
        )
        db.session.expire_all()
        entry = db.session.query(TransactionEntry).one()
        entry_service.update_entry(
            entry.id, seed_user["user"].id, description="Walmart -- hose",
        )
        db.session.commit()

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert "Undo is refused:" in page
        assert "data-confirm=" not in page


class TestTheRegisterBoundIsWiredToThePage:
    """Ruling **bank_import:R-GX**'s bound, driven through the real route.

    The arithmetic is graded one tier down
    (``test_release.TestTheRegisterBoundsWhatItRenders``) at a parameterised
    limit.  What only the route can show is that the page passes the SHIPPED
    :data:`~app.services.statement_match.REGISTER_LIMIT`, says what it
    withheld, and offers a link that lifts it -- so this stages one act past
    the real boundary and drives both renders.
    """

    def test_it_cuts_at_the_limit_and_offers_the_rest(
        self, auth_client, db, seed_user,
    ):
        """One act more than the bound: the cut, the count, and the link.

        The acts are made in ONE Apply, which is what that door is for -- the
        developer's own pass applies 195 items -- and staging them one request
        at a time cost 9.3 s of a 30 s per-test budget for nothing.
        """
        day = seed_user["bootstrap_period"].start_date
        statement = an_import(seed_user)
        items = []
        for ordinal in range(REGISTER_LIMIT + 1):
            line = a_bank_line(
                seed_user, statement, amount="-10.00", posted_on=day,
                sequence_in_group=ordinal,
            )
            txn = a_transaction(
                seed_user, name=f"Bill {ordinal}", amount="10.00",
                status=StatusEnum.DONE, settled_on=day,
            )
            items.append(
                match_item(index=ordinal, lines=[line], transactions=[txn]),
            )
        db.session.commit()
        applied = auth_client.post(
            _review_url(seed_user["account"].id), data=one_pass(*items),
        )
        db.session.expire_all()
        assert db.session.query(StatementMatch).count() == (
            REGISTER_LIMIT + 1
        ), (
            f"the pass must really have landed, or the bound below is "
            f"graded against nothing: {applied.status_code}"
        )

        bounded = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()
        everything = auth_client.get(
            f"{_register_url(seed_user['account'].id)}?all=1",
        ).data.decode()

        assert bounded.count('name="match_id"') == REGISTER_LIMIT
        assert "Show the other 1 accepted match(es)" in bounded
        assert everything.count('name="match_id"') == REGISTER_LIMIT + 1
        assert "Every accepted match on this account is listed" in everything


class TestTheDoorAnswersWithTheSurfaceItWasPostedFrom:
    """One rule door per surface, and the URL is what says which.

    Plan step ``bank_import:X-gf-2``.  Both controls submit the identical
    payload and both re-render through htmx, so the one thing that can go
    wrong silently is a door answering with the OTHER screen: htmx would swap a
    fragment whose id its target never named, and the owner would press Save
    and see nothing at all -- the exact failure the review body's own
    ``show:top`` comment records being reported as "the button does nothing".

    That is why the surface is the URL rather than a hidden field: a field
    would be a client-chosen template with an allowlist to keep, and these two
    cases would be grading the allowlist instead of the routing table.
    """

    def _an_answered_merchant(self, auth_client, db, seed_user):
        """State one answer, so both controls have something to render.

        Args:
            auth_client: The logged-in client.
            db: The session.
            seed_user: The seeded user bundle.

        Returns:
            The envelope the answer names.
        """
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        an_unexplained_outflow(seed_user, merchant="Walmart", sequence=1)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        return envelope

    def test_the_REGISTER_door_answers_with_the_register_body(
        self, auth_client, db, seed_user,
    ):
        """...and the body it returns is the one this page's form targets."""
        self._an_answered_merchant(auth_client, db, seed_user)
        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        response = auth_client.post(
            _restate_url(seed_user["account"].id),
            data=rule_form_controls(page),
        )

        assert response.status_code == 200
        target = 'hx-target="#statement-register-body"'
        assert target in page, "the register's form names no target of its own"
        assert b'id="statement-register-body"' in response.data, (
            "the answer must be the register, or htmx swaps a fragment that "
            "is not the one the request targeted"
        )
        assert b'id="statement-review-body"' not in response.data

    def test_the_QUEUE_door_still_answers_with_the_review_body(
        self, auth_client, db, seed_user,
    ):
        """The other side, so the assertion above is a boundary not a hole.

        The queue's answer has to be the WHOLE review body rather than its own
        control: a new answer moves that merchant's lines between the creatable
        and parked lists and re-places every other line it reaches, so a swap
        of the control alone would leave the screen below it stale.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()

        response = auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id, answer="ask",
            ),
        )

        queue = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert response.status_code == 200
        assert 'hx-target="#statement-review-body"' in queue, (
            "the queue's form names no target of its own"
        )
        assert b'id="statement-review-body"' in response.data
        assert b'id="statement-register-body"' not in response.data


class TestTheSaveKeepsTheSurfaceItWasPressedOn:
    """Three defects this step's own build had, and their controls.

    Each is the same shape from a different side: a door that answers with a
    surface has to answer with the surface the owner was ON, derived at the
    moment that makes it true, and saying only what it has.
    """

    @staticmethod
    def _an_act(auth_client, db, seed_user, ordinal=0):
        """Accept one match, so the register has something to render.

        Args:
            auth_client: The logged-in client.
            db: The session.
            seed_user: The seeded user bundle.
            ordinal: The line's ordinal, completing its identity.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-10.00", posted_on=day,
            sequence_in_group=ordinal,
        )
        txn = a_transaction(
            seed_user, name=f"Bill {ordinal}", amount="10.00",
            status=StatusEnum.DONE, settled_on=day,
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=match_item(lines=[line], transactions=[txn]),
        )

    def test_saving_while_showing_EVERYTHING_answers_with_everything(
        self, auth_client, db, seed_user,
    ):
        """The bound is part of the surface, so the save carries it.

        Without it a Save pressed while showing the whole record answers with
        the bounded list, and the record collapses under the owner mid-read --
        which is the same class of defect as answering with the other screen.
        """
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        self._an_act(auth_client, db, seed_user, ordinal=1)

        page = auth_client.get(
            f"{_register_url(seed_user['account'].id)}?all=1",
        ).data.decode()
        # The RENDERED action, which is what a browser would post.
        assert "/statements/register/merchants?all=1" in page

        response = auth_client.post(
            f"{_restate_url(seed_user['account'].id)}?all=1",
            data=rule_form_controls(page),
        )

        assert response.status_code == 200
        assert b"Every accepted match on this account is listed" in (
            response.data
        )

    def test_a_REFUSED_save_reads_the_database_once(
        self, auth_client, db, seed_user, monkeypatch,
    ):
        """A refusal may not re-derive, and the reason is the database arm.

        The connection that produced the first error very likely produces a
        second, which escapes as an unhandled 500 -- and htmx does not swap a
        500, so the owner presses Save and sees nothing at all.  The sibling
        route states this and reuses its own derivation; this door must too.

        The refusal driven here is a designed one (a merchant id this account
        has never seen), because what is under test is the SHAPE -- one
        derivation, reused -- and that shape is what makes the database arm
        safe.
        """
        an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        calls = []
        real = statement_register_route.register_set
        monkeypatch.setattr(
            statement_register_route, "register_set",
            lambda *args, **kwargs: (
                calls.append(1) or real(*args, **kwargs)
            ),
        )

        response = auth_client.post(
            _restate_url(seed_user["account"].id),
            data=rule_item(0, 987_654, answer="never"),
        )

        assert response.status_code == 400
        assert response.headers.get("Shekel-Designed-Fragment") == "1"
        assert b'id="statement-register-body"' in response.data
        assert len(calls) == 1, (
            f"the refusal path derived the register {len(calls)} times: a "
            f"second read is the one this arm exists to avoid"
        )

    def test_an_EMPTY_register_does_not_explain_rows_it_has_none_of(
        self, auth_client, db, seed_user,
    ):
        """The disclosure describes acts, so it renders where there are some.

        Its positive twin is
        ``TestTheRegisterPage.test_it_says_what_can_unmake_a_match_without_
        touching_it``; without this side, a disclosure that had become
        unconditional would read the same.
        """
        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert "Nothing accepted yet" in page
        assert "A match can stop holding without anybody touching it" not in (
            page
        )


class TestItRefusesAnotherUsersAccount:
    """Firing controls against an IDOR on the door that DESTROYS records.

    Plan step ``bank_import:X-gf-2`` moved the release door off the review
    screen, and moving it is exactly how such a control goes quiet: the old
    case posted to ``/statements/review/release``, a URL no rule matches any
    more, so its 404 came from Flask's URL MAP and not from the ownership gate.
    It passed, said nothing, and would have gone on passing with
    ``load_cash_account_or_404`` deleted.  These post to the two URLs that
    exist, and both are asserted to still exist -- a control against an IDOR
    must fail if the route it names is gone rather than pass louder.
    """

    @pytest.fixture()
    def other_users_account(self, db, seed_user):
        """Return an account id belonging to a DIFFERENT user.

        Args:
            db: The session.
            seed_user: The seeded user bundle, for the account type.

        Returns:
            The stranger's account id.
        """
        stranger = User(
            email="registerstranger@shekel.local",
            password_hash=auth_service.hash_password("otherpass"),
            display_name="Stranger",
        )
        db.session.add(stranger)
        db.session.flush()
        db.session.add(UserSettings(user_id=stranger.id))
        db.session.flush()
        account = Account(
            user_id=stranger.id,
            account_type_id=seed_user["account"].account_type_id,
            name="Stranger Checking",
        )
        db.session.add(account)
        # COMMITTED, not flushed (plan step balance:X-i3): a query request
        # opens a transaction of its OWN, so a row this fixture only flushed is
        # one the request cannot see.  The 404 must be the OWNERSHIP gate
        # refusing a real account of someone else's, never a missing row.
        db.session.commit()
        return account.id

    @pytest.mark.parametrize("path", [
        "/accounts/{}/statements/register",
        "/accounts/{}/statements/register/merchants",
        "/accounts/{}/statements/register/release",
        "/accounts/{}/statements/release",
    ])
    def test_the_url_exists_for_the_callers_OWN_account(
        self, auth_client, seed_user, path,
    ):
        """The control below is worthless if its URL matches no rule.

        Each of these answers 200, 400 or 405 on the owner's own account --
        anything but the 404 the case below asserts for a stranger's.  That is
        what makes the stranger's 404 the ownership gate's answer.
        """
        url = path.format(seed_user["account"].id)
        answer = (
            auth_client.get(url) if url.endswith("register")
            else auth_client.post(url, data={})
        )

        assert answer.status_code != 404, f"{url} matches no route"

    @pytest.mark.parametrize("path", [
        "/accounts/{}/statements/register",
        "/accounts/{}/statements/register/merchants",
        "/accounts/{}/statements/register/release",
        "/accounts/{}/statements/release",
    ])
    def test_a_STRANGERS_account_is_a_404_on_every_door(
        self, auth_client, db, other_users_account, path,
    ):
        """A 403 would confirm the account exists.

        The two release doors are the ones that matter most: releasing a match
        removes the rows that act created (ruling **R-GG**), so a door
        answering for another owner's account would let one user destroy
        another's records from their own screen.
        """
        url = path.format(other_users_account)
        answer = (
            auth_client.get(url) if url.endswith("register")
            else auth_client.post(url, data={"match_id": 1})
        )

        assert answer.status_code == 404
        assert db.session.query(StatementMatch).count() == 0


class TestWhatOnlyTheRENDEREDPageCanShow:
    """Four surfaces this step built or moved that no assertion reached.

    Every one was found by adversarial test-quality review of this step
    (2026-08-27), and they share a shape: the change is real, the service tier
    grades the VALUE behind it, and nothing grades that any markup renders it
    -- so deleting the markup left the suite green.
    """

    def test_an_act_that_NO_LONGER_HOLDS_is_MARKED_on_the_page(
        self, auth_client, db, seed_user,
    ):
        """Finding **N-349**'s other half: the order is not the disclosure.

        The service tier grades that a non-agreeing act sorts to the top
        (``test_release.TestTheRegisterBoundsWhatItRenders``).  What only the
        page can show is that the owner can TELL: the row wears
        ``table-warning``, says so in words, and names the day the row now
        carries against the one the bank stated.  Every other register case
        stages acts that agree, so all three were rendered by nothing.
        """
        statement = an_import(seed_user)
        day = seed_user["bootstrap_period"].start_date
        line = a_bank_line(
            seed_user, statement, amount="-180.00", posted_on=day,
        )
        txn = a_transaction(
            seed_user, name="Electricity", amount="180.00",
            status=StatusEnum.DONE, settled_on=day,
        )
        db.session.commit()
        auth_client.post(
            _review_url(seed_user["account"].id),
            data=match_item(lines=[line], transactions=[txn]),
        )
        db.session.expire_all()
        # ...and then something ELSE moves the row's day, which is what the
        # four doors of N-349 do to a member without saying a word.
        moved = day + timedelta(days=4)
        db.session.get(Transaction, txn.id).settled_on = moved
        db.session.commit()

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert "table-warning" in page
        assert "This match no longer holds -- re-review it." in page
        assert f"now dated {moved}, not the bank's day" in page

    def test_an_act_a_RULE_filed_is_badged_as_one(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GT**'s whole argument for storing a column at all.

        ``applied_by_rule`` is the one fact about an act that is NOT derivable
        from what it names, and the register is now its only home -- so a badge
        nothing renders would make the column a fact written and never seen,
        which is the shape this arc keeps finding.  Graded by nothing on any
        surface before this step.
        """
        envelope = an_envelope(seed_user)
        a_rule(seed_user, "Coffee", template_id=envelope.template_id)
        db.session.commit()
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-25.00",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="POINT OF SALE DEBIT L340 COFFEE HOUSE (Coffee)",
            merchant="Coffee",
        )
        db.session.commit()
        filed = file_new_swipes(
            ReviewScope.build(
                seed_user["user"].id, seed_user["account"].id,
            ),
            import_id=statement.id,
        )
        db.session.commit()
        assert filed.outcome.recorded_count == 1, (
            "the rule must really have filed something, or the badge below is "
            "graded against an act nobody performed"
        )

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert "filed by your rule" in page

    def test_a_merchant_the_bank_files_as_an_ACCOUNT_PAYMENT_says_so_here(
        self, auth_client, db, seed_user,
    ):
        """Ruling **R-GJ** on the surface where an answer is CHANGED.

        `register_set` reads the bars for this and nothing rendered them: two
        of this row's four options are refused by the door, and a control that
        did not say so is the *chooser whose submission can never succeed*
        shape this package has closed four times.  The service tier grades the
        FLAG (``test_bars``); this grades that the register prints it.
        """
        statement = an_import(seed_user)
        a_bank_line(
            seed_user, statement, amount="-793.23",
            posted_on=seed_user["bootstrap_period"].start_date,
            description="ACH DEBIT CAPITAL ONE MOBILE PMT",
            merchant="Capital One Credit Card",
            source_category="Financial Services/Credit Card Payment",
        )
        a_rule(seed_user, "Capital One Credit Card")
        db.session.commit()

        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()

        assert "payment to an account you hold" in page
        assert "<em>Never a purchase</em> is the" in page

    def test_the_page_LOADS_the_script_its_new_envelope_arm_needs(
        self, auth_client, db, seed_user,
    ):
        """The reveal is shared by two pages, so each must actually load it.

        Without it the name and category boxes stay ``d-none`` on this page:
        an owner switching a row to *a new envelope* sees no inputs, Saves, and
        is told "a new envelope needs both a name and a category" with nothing
        on screen to fix.  The file is new at this step and both includes were
        graded by nothing.
        """
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )

        register = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()
        queue = auth_client.get(
            _review_url(seed_user["account"].id),
        ).data.decode()

        assert "js/statement_rules.js" in register
        assert "data-rule-new-field" in register
        # ...and the queue keeps it too, its own control asking the same thing.
        assert "js/statement_rules.js" in queue


class TestTheFlagIsAPresenceTest:
    """`?all=1` lifts the bound, and so does every other spelling of it.

    :func:`~app.routes.accounts.statement_register._asked_for_everything`
    states the contract -- *a PRESENCE test and not a value one ... no value to
    refuse* -- and only one literal was ever exercised.  Narrowing it to
    ``request.args.get("all", type=int) == 1``, which is what ``grid/page.py``
    does and is a plausible consistency edit, would keep the suite green while
    ``?all=`` and ``?all=yes`` silently stopped working.  Found by adversarial
    test-quality review, 2026-08-27.

    The end-to-end arm (``TestTheRegisterBoundIsWiredToThePage``) drives one
    spelling against a real 51-act account, because the bound cannot be seen
    below it; this grades the predicate itself, where every spelling is cheap.
    """

    @pytest.mark.parametrize("query", ["all=1", "all=0", "all=", "all=yes"])
    def test_ANY_spelling_of_the_flag_lifts_the_bound(self, app, query):
        """Whatever the link carries, carrying it is the whole test."""
        with app.test_request_context(f"/?{query}"):
            # Pylint: protected-access -- a test for a module reaches into
            # it, which is the allowance every sibling here takes.
            assert statement_register_route._asked_for_everything() is (  # pylint: disable=protected-access
                True
            )

    @pytest.mark.parametrize("query", ["", "everything=1", "al=1"])
    def test_ANYTHING_ELSE_leaves_it_bounded(self, app, query):
        """The firing control: a predicate that is always true is not one."""
        with app.test_request_context(f"/?{query}"):
            assert statement_register_route._asked_for_everything() is (  # pylint: disable=protected-access
                False
            )


class TestASaveReDerivesOnlyWhatItCouldHaveChanged:
    """A rule pass writes one table, so it may re-read one half.

    Plan step ``bank_import:X-gf-2``; found by adversarial code review
    2026-08-27.  ``state_rules`` adds or updates ``budget.merchant_rules`` rows
    and writes nothing else -- no transaction, no purchase, no act -- so the
    accepted list is provably identical either side of it.  A door that
    re-derived the WHOLE register for its answer would re-fold every act on the
    account to show a changed sentence, which is the 139-statement fold this
    step just took off the review screen.
    """

    def test_the_ACCEPTED_fold_runs_once_across_a_save(
        self, auth_client, db, seed_user, monkeypatch,
    ):
        """Once for the pre-write derivation, and not again for the answer.

        Counted at ``accepted_register``, which is the expensive half, rather
        than at ``register_set`` -- what must not repeat is the FOLD, and the
        answers half is three indexed reads that must.
        """
        envelope = an_envelope(seed_user)
        an_unexplained_outflow(seed_user)
        db.session.commit()
        auth_client.post(
            _merchants_url(seed_user["account"].id),
            data=rule_item(
                0, a_merchant(seed_user, "Amazon").id,
                answer=f"t:{envelope.template_id}",
            ),
        )
        page = auth_client.get(
            _register_url(seed_user["account"].id),
        ).data.decode()
        submitted = rule_form_controls(page)
        submitted["rule-0"] = "ask"

        folds, answers = [], []
        real_fold = statement_register_route.register_set
        real_answers = statement_register_route.merchant_register
        monkeypatch.setattr(
            statement_register_route, "register_set",
            lambda *a, **k: (folds.append(1) or real_fold(*a, **k)),
        )
        monkeypatch.setattr(
            statement_register_route, "merchant_register",
            lambda *a, **k: (answers.append(1) or real_answers(*a, **k)),
        )
        response = auth_client.post(
            _restate_url(seed_user["account"].id), data=submitted,
        )

        assert response.status_code == 200
        assert len(folds) == 1, (
            f"the accepted acts were folded {len(folds)} times for a pass "
            f"that cannot have changed one"
        )
        assert len(answers) == 1, (
            "the answers must be re-read, or the screen shows what the save "
            "replaced"
        )
        # ...and what came back really is the NEW answer, so reusing the
        # pre-write half did not reuse the wrong half.
        assert rule_form_controls(response.data.decode())["rule-0"] == "ask"
