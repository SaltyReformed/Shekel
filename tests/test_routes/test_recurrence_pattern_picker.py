"""
Shekel Budget App -- The pattern picker offers what the app MODELS (R2e-2)

Four form routes built the recurrence ``<select>`` from
``db.session.query(RecurrencePattern).all()``, both write doors validated a
submitted id with ``db.session.get(RecurrencePattern, ...)``, and the
occurrence preview did the same.  All five read the TABLE.  What the
application can READ BACK is narrower: ``app.services.recurrence.resolve``
raises ``RecurrenceResolutionError`` for a pattern id no
``RecurrencePatternEnum`` member names, and no route catches it -- so a ``ref``
row the enum does not name was offerable, acceptable, and then fatal.

Today the two sets are identical, so the gap costs nothing.  Plan step R2e-3
opens it on purpose: the ``Once`` enum member is deleted while its ``ref`` row
SURVIVES to R9, because deleting the row in the same release would leave the
auto-rollback image unable to boot (``ref_cache.init`` raises for an enum
member with no row; ruling R-R11).  From that release on, a stale form -- an
edit page open across the deploy, a back-button resubmit -- posts an id the
table still has and the enum no longer names.

So these tests manufacture that state now: they insert a ``ref`` row with no
enum member and drive every surface at it.  Each asserts two things, because
one alone would not show the defect:

1. the surface REFUSES it (a flash and a redirect, or the preview's "Unknown
   pattern"), and nothing is written;
2. ``resolve`` genuinely raises for that same id -- so the refusal is what
   stands between the user and a 500, not a coincidence.
"""
import re
from decimal import Decimal

import pytest

from app import ref_cache
from app.enums import RecurrencePatternEnum, TxnTypeEnum
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, RecurrencePattern
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.services.recurrence import (
    UNAVAILABLE_PATTERN_LABEL,
    UNAVAILABLE_PATTERN_MESSAGE,
    RecurrenceResolutionError,
    RecurrenceSpec,
    calendar_for,
    pattern_choices,
    resolve,
)
from tests._test_helpers import select_option_values


# ── Helpers ──────────────────────────────────────────────────────────


def _unmodelled_pattern_id(name="Every Blue Moon"):
    """Insert and commit a ``ref.recurrence_patterns`` row with no enum member.

    Exactly the post-R2e-3 shape: ``ref_cache.init`` requires every ENUM
    member to have a row and says nothing about the reverse, so a surplus row
    is a state the schema permits.  Committed rather than flushed because the
    route under test runs in its own request and session.

    Args:
        name: The row's name; asserted not to collide with an enum value.

    Returns:
        int: The new row's primary key.
    """
    assert name not in {member.value for member in RecurrencePatternEnum}
    row = RecurrencePattern(name=name)
    db.session.add(row)
    db.session.commit()
    return row.id


def _assert_unresolvable(user_id, pattern_id):
    """Assert ``resolve`` raises for *pattern_id*, i.e. the door earns its keep.

    Without this, a test that only asserts "the route redirected" cannot tell
    a door that refuses bad input from a door that refuses everything.

    The match is the membership sentence, not the bare id: ``match`` is a
    ``re.search`` over the whole message, and ``resolve`` raises three other
    errors that can carry the same digits ("recurrence for user 9 cannot be
    resolved", "interval_n must be positive, got 9").

    Args:
        user_id: Owner whose schedule the resolution is measured against.
        pattern_id: The id the surface just refused.
    """
    expected = rf"pattern id {pattern_id} matches no RecurrencePatternEnum"
    with pytest.raises(RecurrenceResolutionError, match=expected):
        resolve(
            RecurrenceSpec(user_id=user_id, pattern_id=pattern_id),
            calendar_for(user_id),
        )


def _savings_account(seed_user):
    """Create a second account so a transfer template has a destination."""
    savings_type = db.session.query(AccountType).filter_by(name="Savings").one()
    acct = account_service.create_account(
        account_service.AccountSpec(
            user_id=seed_user["user"].id,
            account_type_id=savings_type.id,
            name="Savings",
            anchor_balance=Decimal("0"),
        ),
    )
    db.session.add(acct)
    db.session.commit()
    return acct


def _expected_option_values(app):
    """Return the picker's expected ``<option>`` values, as rendered strings."""
    with app.app_context():
        return [str(choice.pattern_id) for choice in pattern_choices()]


def _option_labels(html):
    """Return the visible TEXT of each pattern ``<option>``, in document order.

    The sibling of ``select_option_values``, which reads only the ``value``
    attributes.  Needed because the label is the half that used to come from a
    context-processor lookup and now comes from the producer.
    """
    block = re.search(
        r'<select[^>]*\bname="recurrence_pattern"[^>]*>(.*?)</select>',
        html, re.DOTALL,
    )
    return [
        text.strip()
        for text in re.findall(
            r"<option\b[^>]*>(.*?)</option>", block.group(1), re.DOTALL,
        )
    ]


def _selected_option_value(html):
    """Return the ``value`` of the pattern option carrying ``selected``.

    ``None`` when no option does -- which is the H1 failure state, not a
    harmless one: a ``<select>`` with nothing selected silently submits its
    FIRST option, so "no selection" and "the first cadence" are the same
    request.

    Raises:
        AssertionError: When more than one option is selected.
    """
    block = re.search(
        r'<select[^>]*\bname="recurrence_pattern"[^>]*>(.*?)</select>',
        html, re.DOTALL,
    )
    selected = re.findall(
        r'<option\b[^>]*\bvalue="([^"]*)"[^>]*\bselected', block.group(1),
    )
    assert len(selected) <= 1, f"multiple options selected: {selected}"
    return selected[0] if selected else None


# ── The picker ───────────────────────────────────────────────────────


class TestThePickerRendersTheModelledSet:
    """Both forms offer the enum's patterns, in the enum's order."""

    def test_the_transaction_form_offers_the_modelled_patterns_in_order(
        self, app, auth_client,
    ):
        """GET /templates/new renders the enum's ids, plus the null option.

        The order is asserted as a sequence: the unordered ``SELECT`` this
        replaced could reorder the dropdown between deploys with no code
        change.  The leading ``""`` is "Does not repeat", which this form has
        always offered in some wording.
        """
        expected = _expected_option_values(app)

        resp = auth_client.get("/templates/new")

        assert resp.status_code == 200
        assert select_option_values(
            resp.data.decode(), "recurrence_pattern",
        ) == [""] + expected

    def test_the_transfer_form_offers_the_modelled_patterns_in_order(
        self, app, auth_client,
    ):
        """GET /transfers/new renders the same ids, and the null option first.

        Identical to the transaction form since plan step R2e-3.  Before it,
        this form offered NO null option because its one-time case was the
        ``Once`` PATTERN; retiring that made "does not repeat" the empty value
        on both kinds, so the two pickers are now the same list.
        """
        expected = _expected_option_values(app)

        resp = auth_client.get("/transfers/new")

        assert resp.status_code == 200
        assert select_option_values(
            resp.data.decode(), "recurrence_pattern",
        ) == [""] + expected

    def test_a_ref_row_the_enum_does_not_name_is_never_offered(
        self, app, auth_client,
    ):
        """Neither form renders an option for an unmodelled ``ref`` row.

        The defect in one assertion: before R2e-2 the picker was the table, so
        this row would have rendered as a selectable option on both forms and
        selecting it would have 500'd on save.
        """
        with app.app_context():
            surplus = str(_unmodelled_pattern_id())

        for url in ("/templates/new", "/transfers/new"):
            resp = auth_client.get(url)
            assert resp.status_code == 200
            assert surplus not in select_option_values(
                resp.data.decode(), "recurrence_pattern",
            )

    def test_the_option_labels_are_the_human_copy_in_the_producers_order(
        self, auth_client,
    ):
        """The full label sequence is pinned, not just a substring.

        The macro used to look each name up in a context-processor dict and
        fall back to a title-cased ``pattern.name``.  The label now arrives
        beside its id, so this asserts the WHOLE list in order -- a substring
        check would have passed against the old lookup too, and a
        whitespace-exact negative ("the raw ref name is absent") would be
        disarmed by a reindent.
        """
        resp = auth_client.get("/templates/new")

        assert _option_labels(resp.data.decode()) == [
            "Does not repeat",
            "Every paycheck",
            "Every N paychecks",
            "Monthly (specific day)",
            "Monthly (first paycheck of month)",
            "Quarterly",
            "Every 6 months",
            "Yearly",
        ]

    def test_both_forms_offer_the_identical_option_labels(self, auth_client):
        """The transfer picker renders the same labels as the transaction one.

        The two diverged only because a one-time transfer carried a ``Once``
        RULE while a one-time transaction carried none; plan step R2e-3
        removed the divergence and the ``include_none_option`` macro flag that
        expressed it.  Asserting EQUALITY rather than re-listing the copy is
        what keeps a future edit from moving one form's wording alone.
        """
        transaction = _option_labels(auth_client.get("/templates/new").data.decode())
        transfer = _option_labels(auth_client.get("/transfers/new").data.decode())

        assert transfer == transaction
        assert transfer[0] == "Does not repeat"


# ── The edit form's pre-selected value ───────────────────────────────


class TestAnEditFormNeverSilentlyChangesTheStoredCadence:
    """A stored pattern the app no longer models stays SELECTED and loud.

    An HTML ``<select>`` whose selected value is absent from its options does
    not fail -- the browser silently selects the first option, and that option
    submits.  Measured on the transaction edit form before the fix: no option
    carried ``selected``, and the first is the empty "Does not repeat" entry,
    whose save DELETES the rule and sweeps its future rows (plan step R2e-1).
    On the transfer form, which had no null option before plan step R2e-3, the
    first was "Every paycheck", which re-authors the cadence instead.

    Before R2e-2 the picker was the table, so the row WAS rendered and
    pre-selected and a save raised loudly.  Without this class the step would
    have traded a 500 for a silent wrong write on the one screen where it is
    least likely to be noticed.
    """

    def test_the_transaction_form_keeps_the_stored_pattern_selected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The unmodelled id is selected, so the browser cannot pick "".

        Asserting the selected value is the point; asserting merely that the
        id APPEARS would pass on the broken render too, because the option
        list is not what was wrong.
        """
        with app.app_context():
            assert seed_periods_today
            surplus_id = _unmodelled_pattern_id()
            template_id = _template_with_pattern(seed_user, surplus_id).id

        resp = auth_client.get(f"/templates/{template_id}/edit")

        assert resp.status_code == 200
        assert _selected_option_value(resp.data.decode()) == str(surplus_id)

    def test_the_transfer_form_keeps_the_stored_pattern_selected(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Same on the transfer form, whose silent default is a real cadence.

        This form offers no null option, so the browser's fallback is "Every
        paycheck" -- a transfer generated into every pay period rather than a
        deleted rule.  Different damage, same cause.
        """
        with app.app_context():
            assert seed_periods_today
            savings = _savings_account(seed_user)
            surplus_id = _unmodelled_pattern_id()
            template_id = _transfer_template_with_pattern(
                seed_user, savings, surplus_id,
            ).id

        resp = auth_client.get(f"/transfers/{template_id}/edit")

        assert resp.status_code == 200
        assert _selected_option_value(resp.data.decode()) == str(surplus_id)

    def test_the_user_is_told_the_pattern_is_gone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A warning names the problem, so the odd option is not a mystery.

        The selected option reads "Unavailable -- pick a new pattern", which
        says WHAT but not what to do about a save; the flash does.
        """
        with app.app_context():
            assert seed_periods_today
            surplus_id = _unmodelled_pattern_id()
            template_id = _template_with_pattern(seed_user, surplus_id).id

        resp = auth_client.get(f"/templates/{template_id}/edit")
        body = resp.data.decode()

        assert UNAVAILABLE_PATTERN_MESSAGE.split(".", maxsplit=1)[0] in body
        assert UNAVAILABLE_PATTERN_LABEL in body

    def test_saving_it_unchanged_is_refused_and_the_rule_survives(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The load-bearing one: re-submitting the form destroys nothing.

        This is the whole point of keeping the id selected. The user opens the
        edit form, changes the amount, saves -- the pattern field submits the
        value the form showed, the write door refuses it, and the rule the
        template depends on is still there.  On the broken render the same
        action deleted it.
        """
        with app.app_context():
            assert seed_periods_today
            surplus_id = _unmodelled_pattern_id()
            template = _template_with_pattern(seed_user, surplus_id)
            template_id, rule_id = template.id, template.recurrence_rule_id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1300.00",
                # Exactly what the rendered form submits.
                "recurrence_pattern": str(surplus_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid recurrence pattern" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule_id == rule_id
            assert db.session.get(RecurrenceRule, rule_id) is not None
            # The refused edit committed nothing, amount included.
            assert reloaded.default_amount == Decimal("1200.00")

    def test_a_modelled_pattern_gets_no_extra_option(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Negative control: an ordinary rule's picker is the modelled set.

        Without this, a producer that appended an "Unavailable" entry to EVERY
        edit form would pass every test above.
        """
        expected = _expected_option_values(app)
        with app.app_context():
            assert seed_periods_today
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )
            template_id = _template_with_pattern(seed_user, monthly_id).id

        resp = auth_client.get(f"/templates/{template_id}/edit")
        body = resp.data.decode()

        assert select_option_values(body, "recurrence_pattern") == [""] + expected
        assert _selected_option_value(body) == str(monthly_id)
        assert UNAVAILABLE_PATTERN_LABEL not in body


# ── The write doors ──────────────────────────────────────────────────


class TestTheCreateDoorRefusesAnUnmodelledPattern:
    """A create POST naming an unmodelled ``ref`` row writes nothing."""

    def test_a_transaction_template_is_not_created(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates redirects with a flash and persists no rule.

        Both halves matter: a 302 alone would also be produced by a route that
        created the template and redirected to the list.
        """
        with app.app_context():
            # The ROUTE needs a schedule: without one it refuses before it
            # reaches the pattern at all ("No pay periods generated yet" on the
            # preview; no period for a template's rows).  ``resolve`` itself
            # does not -- it calls ``_pattern_member`` (``_resolution.py:803``)
            # before ``_effective_start`` (``:808``), so the membership error
            # fires on an empty calendar too.
            assert seed_periods_today
            surplus_id = _unmodelled_pattern_id()
            user_id = seed_user["user"].id
            _assert_unresolvable(user_id, surplus_id)
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/templates", data={
                "name": "Blue Moon Bill",
                "default_amount": "10.00",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(_expense_type_id()),
                "account_id": str(seed_user["account"].id),
                "recurrence_pattern": str(surplus_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid recurrence pattern" in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="Blue Moon Bill",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_a_transfer_template_is_not_created(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers refuses the same id the same way."""
        with app.app_context():
            # The ROUTE needs a schedule: without one it refuses before it
            # reaches the pattern at all ("No pay periods generated yet" on the
            # preview; no period for a template's rows).  ``resolve`` itself
            # does not -- it calls ``_pattern_member`` (``_resolution.py:803``)
            # before ``_effective_start`` (``:808``), so the membership error
            # fires on an empty calendar too.
            assert seed_periods_today
            savings = _savings_account(seed_user)
            surplus_id = _unmodelled_pattern_id()
            user_id = seed_user["user"].id
            _assert_unresolvable(user_id, surplus_id)
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/transfers", data={
                "name": "Blue Moon Transfer",
                "default_amount": "25.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_pattern": str(surplus_id),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid recurrence pattern" in resp.data
            assert db.session.query(TransferTemplate).filter_by(
                name="Blue Moon Transfer",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before


class TestTheEditDoorRefusesAnUnmodelledPattern:
    """An edit POST naming an unmodelled row leaves the rule untouched."""

    def test_the_existing_rule_keeps_its_pattern(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates/<id> refuses and re-points nothing.

        The edit path re-AUTHORS the rule in place, so a door that accepted
        the id would overwrite a working cadence with an unresolvable one --
        on a row the user already depends on, not on a new one.
        """
        with app.app_context():
            # The ROUTE needs a schedule: without one it refuses before it
            # reaches the pattern at all ("No pay periods generated yet" on the
            # preview; no period for a template's rows).  ``resolve`` itself
            # does not -- it calls ``_pattern_member`` (``_resolution.py:803``)
            # before ``_effective_start`` (``:808``), so the membership error
            # fires on an empty calendar too.
            assert seed_periods_today
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )
            template = _template_with_pattern(seed_user, monthly_id)
            template_id, rule_id = template.id, template.recurrence_rule_id
            surplus_id = _unmodelled_pattern_id()
            _assert_unresolvable(seed_user["user"].id, surplus_id)

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                "recurrence_pattern": str(surplus_id),
                "day_of_month": "1",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert b"Invalid recurrence pattern" in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule_id == rule_id
            assert reloaded.recurrence_rule.pattern_id == monthly_id


# ── The preview ──────────────────────────────────────────────────────


class TestThePreviewRefusesAnUnmodelledPattern:
    """The live preview answers rather than raising."""

    def test_it_reports_unknown_instead_of_raising(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """GET the preview with an unmodelled id -> 200 "Unknown pattern".

        The preview builds a TRANSIENT rule through the same authoring seam a
        save goes through, so the table-driven probe it used to run let the
        row through to ``resolve`` -- which raises, uncaught, on a fragment
        the form fetches on every keystroke of the pattern select.
        """
        with app.app_context():
            # The ROUTE needs a schedule: without one it refuses before it
            # reaches the pattern at all ("No pay periods generated yet" on the
            # preview; no period for a template's rows).  ``resolve`` itself
            # does not -- it calls ``_pattern_member`` (``_resolution.py:803``)
            # before ``_effective_start`` (``:808``), so the membership error
            # fires on an empty calendar too.
            assert seed_periods_today
            surplus_id = _unmodelled_pattern_id()
            _assert_unresolvable(seed_user["user"].id, surplus_id)

        resp = auth_client.get(
            f"/templates/preview-recurrence?recurrence_pattern={surplus_id}",
        )

        assert resp.status_code == 200
        assert b"Unknown pattern" in resp.data

    def test_a_modelled_pattern_still_previews(
        self, app, auth_client, seed_periods_today,
    ):
        """The refusal did not over-reject: a real pattern still lists dates.

        Negative complement of the test above -- without it, a door that
        refused everything would pass.
        """
        with app.app_context():
            # Ten periods, so the preview has occurrences to list at all.
            assert seed_periods_today
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )

        resp = auth_client.get(
            "/templates/preview-recurrence"
            f"?recurrence_pattern={monthly_id}&day_of_month=15",
        )

        assert resp.status_code == 200
        assert b"occurrences" in resp.data


# ── Local fixtures for the helpers above ─────────────────────────────


def _expense_type_id():
    """Return the Expense transaction-type id."""
    return ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _rule_on_pattern(seed_user, pattern_id):
    """Create and flush a rule naming *pattern_id*, bypassing the write door.

    Deliberately constructed rather than authored: ``author_rule`` resolves
    before it writes, so it REFUSES an unmodelled pattern -- which is the guard
    under test.  The row this builds is what a database left behind by plan
    step R2e-3 would hold, and only a direct construction can produce it.
    """
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=pattern_id,
        interval_n=1,
        offset_periods=0,
        day_of_month=1,
    )
    db.session.add(rule)
    db.session.flush()
    return rule


def _transfer_template_with_pattern(seed_user, destination, pattern_id):
    """Create a committed transfer template carrying a rule on *pattern_id*."""
    rule = _rule_on_pattern(seed_user, pattern_id)
    template = TransferTemplate(
        user_id=seed_user["user"].id,
        name="To Savings",
        default_amount=Decimal("50.00"),
        from_account_id=seed_user["account"].id,
        to_account_id=destination.id,
        category_id=seed_user["categories"]["Rent"].id,
        recurrence_rule_id=rule.id,
    )
    db.session.add(template)
    db.session.commit()
    return template


def _template_with_pattern(seed_user, pattern_id):
    """Create a committed transaction template carrying a rule on *pattern_id*."""
    rule = _rule_on_pattern(seed_user, pattern_id)
    template = TransactionTemplate(
        user_id=seed_user["user"].id,
        name="Rent",
        default_amount=Decimal("1200.00"),
        category_id=seed_user["categories"]["Rent"].id,
        transaction_type_id=_expense_type_id(),
        account_id=seed_user["account"].id,
        recurrence_rule_id=rule.id,
    )
    db.session.add(template)
    db.session.commit()
    return template
