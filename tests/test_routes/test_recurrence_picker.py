"""
Shekel Budget App -- the recurrence picker offers only what can be STORED (R7b-2)

The form used to pick a NAME from the closed pattern set.  It now authors the
two axes the write door already takes -- how often (``interval_n`` plus
``unit``) and which paycheck funds an occurrence (``placement``) -- and
``encode_cadence`` chooses the pattern that stores them.

**The claim this file exists to hold the code to is that the offer set is
DERIVED from the encoder's own table.**  Before plan step R7b-2 the picker
iterated ``RecurrencePatternEnum`` while the encoder read
``PATTERN_DERIVATIONS``, so "nothing offers an unstorable cadence" held only
because the two sets happened to coincide -- protected by no gate.
``authorable_cadences`` inverts the encoder's table and ``_picker`` words it,
so the refusal is unreachable through the form rather than fenced behind it
(developer ruling 2026-08-12).

Four properties, each of which fails differently:

1. every offered ``(interval, unit, placement)`` is storable -- swept over the
   whole rendered offer set, not sampled;
2. a placement belongs to the ``(unit, interval)`` PAIR, not to the unit:
   ``MONTHLY_FIRST`` is ``(1, MONTH, PERIOD_STARTING_ON_OR_AFTER)`` with no
   quarterly twin, so an offer set keyed on the unit alone offers
   ``(3, MONTH, first-paycheck)`` -- measured unstorable;
3. exactly one of the two ``interval_n`` inputs is ever enabled, because a
   disabled control does not submit and two spellings of one field must never
   reach the schema together;
4. an EDIT form starts on the cadence its rule actually means, and a rule whose
   stored pattern the application no longer models renders the controls unset
   above a flashed explanation -- and cannot be destroyed by saving it
   unchanged.

The state property 4 needs is manufactured here: ``ref_cache.init`` requires
every ENUM member to have a ``ref`` row and says nothing about the reverse, so
a surplus row is a state the schema permits.  Plan step R2e-3 created exactly
one on purpose -- the ``Once`` row survives its deleted enum member to R9 so
the auto-rollback image can still boot (ruling R-R11).
"""
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
    TxnTypeEnum,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.models.ref import AccountType, RecurrencePattern
from app.models.transaction_template import TransactionTemplate
from app.models.transfer_template import TransferTemplate
from app.routes._recurrence_form_helpers import (
    UNREPAIRED_CADENCE_CANNOT_BE_CLEARED,
)
from app.services import account_service
from app.services.recurrence import (
    UNAVAILABLE_PATTERN_MESSAGE,
    RecurrenceResolutionError,
    cadence_options,
    decode_pattern,
    is_authorable,
    modelled_placement,
    modelled_unit,
    picker_model,
)
from tests._test_helpers import cadence_payload


# ── Helpers ──────────────────────────────────────────────────────────


def _unmodelled_pattern_id(name="Every Blue Moon"):
    """Insert and commit a ``ref.recurrence_patterns`` row with no enum member.

    Committed rather than flushed because the route under test runs in its own
    request and session.

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


def _assert_unresolvable(pattern_id):
    """Assert the seam raises for *pattern_id*, i.e. the door earns its keep.

    Without this, a test that only asserts "the route refused" cannot tell a
    door that refuses bad input from a door that refuses everything.

    Args:
        pattern_id: The id the surface just refused.
    """
    expected = rf"pattern id {pattern_id} matches no RecurrencePatternEnum"
    with pytest.raises(RecurrenceResolutionError, match=expected):
        decode_pattern(pattern_id, 1)


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


def _tag(html, element_id):
    """Return the opening tag of the element carrying ``id="element_id"``.

    ``field_is_disabled`` keys on ``name=``, which cannot separate the two
    interval controls: both post ``interval_n``, which is the whole point of
    exactly one of them being enabled.

    Args:
        html: The rendered page.
        element_id: The ``id`` attribute to find.

    Returns:
        str: The opening tag, including its attributes.

    Raises:
        AssertionError: The element is absent, so a typo fails loud.
    """
    match = re.search(rf'<(?:input|select)\b[^>]*\bid="{element_id}"[^>]*>', html)
    assert match is not None, f'id="{element_id}" not found in rendered HTML'
    return match.group(0)


def _options(html, select_id):
    """Return ``(value, is_selected)`` for each option of one ``<select>``.

    Args:
        html: The rendered page.
        select_id: The select's ``id``.

    Returns:
        list[tuple[str, bool]]: In document order.
    """
    block = re.search(
        rf'<select\b[^>]*\bid="{select_id}"[^>]*>(.*?)</select>', html, re.DOTALL,
    )
    assert block is not None, f'<select id="{select_id}"> not found'
    return [
        (match.group(1), "selected" in match.group(0))
        for match in re.finditer(
            r'<option\b[^>]*\bvalue="([^"]*)"[^>]*>', block.group(1),
        )
    ]


def _option_labels(html, select_id):
    """Return the visible TEXT of each option of one ``<select>``.

    :func:`_options`' sibling, reading the half a value comparison cannot see.
    Whitespace is collapsed because the template wraps long labels across
    lines.

    Args:
        html: The rendered page.
        select_id: The select's ``id``.

    Returns:
        list[str]: In document order.
    """
    block = re.search(
        rf'<select\b[^>]*\bid="{select_id}"[^>]*>(.*?)</select>', html, re.DOTALL,
    )
    assert block is not None, f'<select id="{select_id}"> not found'
    return [
        " ".join(text.split())
        for text in re.findall(
            r"<option\b[^>]*>(.*?)</option>", block.group(1), re.DOTALL,
        )
    ]


def _selected(html, select_id):
    """Return the single selected option value of one ``<select>``.

    ``None`` when none is selected -- which is never harmless: a ``<select>``
    with nothing selected silently submits its FIRST option, so "no selection"
    and "the first entry" are the same request.

    Raises:
        AssertionError: More than one option carries ``selected``.
    """
    chosen = [value for value, is_selected in _options(html, select_id) if is_selected]
    assert len(chosen) <= 1, f"multiple options selected in {select_id}: {chosen}"
    return chosen[0] if chosen else None


def _expense_type_id():
    """Return the Expense transaction-type id."""
    return ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)


def _rule_on_pattern(seed_user, pattern_id, interval_n=1):
    """Create and flush a rule naming *pattern_id*, bypassing the write door.

    Deliberately constructed rather than authored: ``author_rule`` resolves
    before it writes, so it REFUSES an unmodelled pattern -- which is the guard
    under test.  The row this builds is what a database left behind by plan
    step R2e-3 would hold, and only a direct construction can produce it.
    """
    rule = RecurrenceRule(
        user_id=seed_user["user"].id,
        pattern_id=pattern_id,
        interval_n=interval_n,
        offset_periods=0,
        day_of_month=1,
    )
    db.session.add(rule)
    db.session.flush()
    return rule


def _template_with_pattern(seed_user, pattern_id, interval_n=1):
    """Create a committed transaction template carrying a rule on *pattern_id*."""
    rule = _rule_on_pattern(seed_user, pattern_id, interval_n)
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


# ── The offer set ────────────────────────────────────────────────────


class TestNothingOfferedIsUnstorable:
    """Property 1: every offered triple is one ``encode_cadence`` accepts."""

    def test_every_offered_triple_is_authorable(self, app):
        """Swept over the whole offer set, not sampled.

        The producer and the encoder read ONE table, so this cannot fail
        without the inversion in ``_frequency`` being broken -- which is
        precisely the coupling worth a gate, because the two were separate
        producers that merely agreed until plan step R7b-2.
        """
        with app.app_context():
            options = cadence_options()

            assert options, "the offer set is empty"
            for option in options:
                unit = modelled_unit(option.unit_id)
                placement = modelled_placement(option.placement_id)
                assert unit is not None, option
                assert placement is not None, option
                # ``None`` means ANY positive interval; 1 stands for the class.
                interval_n = (
                    1 if option.interval_n is None else option.interval_n
                )
                assert is_authorable(interval_n, unit, placement), (
                    f"the picker offers {(interval_n, unit, placement)}, which "
                    f"the write door cannot store"
                )

    def test_the_unstorable_pair_is_really_unstorable(self, app):
        """The premise the sweep above rests on, stated as a measurement.

        Without this the sweep passes against an ``is_authorable`` that says
        yes to everything.  ``(3, MONTH, first-paycheck)`` is the exact triple
        an offer set keyed on the UNIT alone would produce.
        """
        with app.app_context():
            assert not is_authorable(
                3,
                RecurrenceUnitEnum.MONTH,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )
            assert is_authorable(
                1,
                RecurrenceUnitEnum.MONTH,
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )

    def test_the_offer_set_carries_whole_triples(self, app):
        """Property 2: the placement is offered per ``(unit, interval)``.

        A flat list of triples is what leaves that dependency in the DATA.
        Read as "which placements does the MONTH unit allow" the answer is
        both, and it is wrong at every interval but 1.
        """
        with app.app_context():
            month_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH)
            first_paycheck_id = ref_cache.period_placement_id(
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )
            month_offers = {
                (option.interval_n, option.placement_id)
                for option in cadence_options()
                if option.unit_id == month_id
            }

            assert (1, first_paycheck_id) in month_offers
            assert (3, first_paycheck_id) not in month_offers
            assert (6, first_paycheck_id) not in month_offers

    def test_a_calendar_option_states_its_month_span(self, app):
        """The facts the form's calendar detail rows are shown FROM.

        The script must not infer them.  An earlier draft read "the interval
        control is free" as "no day of the month" and "the interval exceeds 1"
        as "the cycle skips months" -- the second is wrong for an ANNUAL rule,
        whose interval is 1 and whose cycle is twelve months, so the Month
        control it needs was hidden.
        """
        with app.app_context():
            spans = {
                (option.unit_id, option.interval_n): (
                    option.anchors_day_of_month, option.months_per_unit,
                )
                for option in cadence_options()
            }
            month_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH)
            year_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.YEAR)
            period_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.PERIOD)

            # A year spans twelve months, so 1 * 12 > 1 and the Month control
            # shows -- the case the inference got wrong.
            assert spans[(year_id, 1)] == (True, 12)
            # A month spans one, so only an interval above 1 shows it.
            assert spans[(month_id, 1)][1] == 1
            # A paycheck cadence has no month reading and no day of the month.
            assert spans[(period_id, None)] == (False, None)

    def test_a_first_paycheck_month_cadence_reads_no_day_of_month(self, app):
        """``anchors_day_of_month`` belongs to the PAIR, not to the unit.

        ``MONTHLY_FIRST`` anchors on a month's first PAYCHECK, so
        ``day_of_month`` is never read for it -- which is why the form has
        always hidden that input for it, and why the fact is asked of the
        anchor router rather than of the unit.
        """
        with app.app_context():
            month_id = ref_cache.recurrence_unit_id(RecurrenceUnitEnum.MONTH)
            first_paycheck_id = ref_cache.period_placement_id(
                PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            )
            covering_id = ref_cache.period_placement_id(
                PeriodPlacementEnum.CONTAINING_DATE,
            )
            by_placement = {
                option.placement_id: option.anchors_day_of_month
                for option in cadence_options()
                if option.unit_id == month_id and option.interval_n == 1
            }

            assert by_placement[covering_id] is True
            assert by_placement[first_paycheck_id] is False


# ── The rendered controls ────────────────────────────────────────────


class TestTheScriptCanReadWhatTheServerSerialized:
    """The wire contract between ``picker_model`` and ``recurrence_form.js``.

    **Not a substitute for driving the form in a browser**, and it is here
    because it covers the ONE failure in that gap a Python suite can see: the
    script reads each fact off the JSON by NAME, and a renamed or dropped field
    is ``undefined`` in JavaScript rather than an error.  ``undefined`` is
    falsy, so the Day of Month row would simply stop appearing -- no console
    error, no failing request, and a monthly bill authored with no day.

    The visibility behaviour itself (which control shows when, and that the
    hidden one does not submit) still needs a browser.
    """

    #: The names the script dereferences off one entry of
    #: ``data-cadence-options``.  Every one of these is a property access on an
    #: option object in ``recurrence_form.js``.
    _READ_BY_THE_SCRIPT = frozenset({
        "unit_id", "interval_n", "placement_id",
        "anchors_day_of_month", "months_per_unit",
    })

    @staticmethod
    def _script_property_reads():
        """Return every option property ``recurrence_form.js`` dereferences.

        Read FROM the script rather than restated beside it, which an
        adversarial review of plan step R7b-2 required: a hand-written list
        catches the server dropping a field and is blind to the script starting
        to read a new one -- and that direction is the silent half, because a
        property JavaScript cannot find is ``undefined``, not an error, and
        ``undefined`` is falsy.

        Returns:
            set[str]: The property names, scoped to the identifiers the file
            binds to an option (``o`` inside its filter callbacks, ``chosen``
            for the selected triple, ``opt`` for a DOM option is deliberately
            absent -- that one is an ``<option>`` element, not an offer).
        """
        source = (
            Path(__file__).resolve().parents[2]
            / "app" / "static" / "js" / "recurrence_form.js"
        ).read_text(encoding="utf-8")
        return set(re.findall(r"\b(?:o|chosen)\.([a-z_]+)\b", source))

    def test_the_script_and_the_server_name_the_same_fields(self, app):
        """The wire contract, checked in BOTH directions.

        A field the server stops emitting breaks the script silently; a field
        the script starts reading that the server never emitted breaks it
        equally silently.  Reading one side from the JS source and the other
        from the serialized payload is what makes either direction fail here.
        """
        reads = self._script_property_reads()

        assert reads == self._READ_BY_THE_SCRIPT, (
            f"recurrence_form.js now dereferences {sorted(reads)}; update "
            f"_READ_BY_THE_SCRIPT and picker_model together"
        )

        with app.app_context():
            emitted = json.loads(picker_model().options_json)

            assert emitted, "the serialized offer set is empty"
            for entry in emitted:
                missing = reads - set(entry)
                assert not missing, (
                    f"recurrence_form.js reads {sorted(missing)}, which "
                    f"picker_model does not serialize; JavaScript would read "
                    f"undefined and silently hide a control"
                )

    def test_the_json_carries_every_option_the_form_rendered(self, app):
        """The script filters the SAME set the three controls were built from.

        Not a round-trip of ``json.dumps`` -- an adversarial review pointed out
        the obvious form of this assertion is ``json.loads(json.dumps(x)) == x``
        and cannot fail.  What is checked is that the serialized set covers
        every triple the projections drew from, so no cadence the user can
        select is one the script has no entry for.
        """
        with app.app_context():
            model = picker_model()
            emitted = json.loads(model.options_json)
            serialized_keys = {
                (entry["unit_id"], entry["interval_n"], entry["placement_id"])
                for entry in emitted
            }

            for projection in (model.units, model.fixed_intervals,
                               model.placements):
                for option in projection:
                    assert (
                        option.unit_id, option.interval_n, option.placement_id,
                    ) in serialized_keys

    def test_the_projections_are_subsets_of_the_offer_set(self, app):
        """Each control's list is drawn FROM the offer set, not beside it."""
        with app.app_context():
            model = picker_model()

            for projection in (model.units, model.fixed_intervals,
                               model.placements):
                assert set(projection) <= set(model.options)

    def test_each_projection_holds_one_entry_per_distinct_key(self, app):
        """The de-duplication rule, per control.

        The interval list is what shipped the defect this pins: ``MONTHLY`` and
        ``MONTHLY_FIRST`` are two triples over one ``(1, MONTH)`` interval, so
        an un-projected list rendered "1 month" twice and marked BOTH selected.
        """
        with app.app_context():
            model = picker_model()

            units = [option.unit_id for option in model.units]
            intervals = [
                (option.unit_id, option.interval_n)
                for option in model.fixed_intervals
            ]
            placements = [option.placement_id for option in model.placements]

            assert len(units) == len(set(units))
            assert len(intervals) == len(set(intervals))
            assert len(placements) == len(set(placements))
            assert all(
                option.interval_n is not None
                for option in model.fixed_intervals
            ), "a free-interval unit has no fixed option to render"


class TestBothFormsRenderTheSameControls:
    """The one producer serves both template kinds."""

    @pytest.mark.parametrize(
        "url", ["/templates/new", "/transfers/new"],
    )
    def test_the_unit_select_offers_each_offered_unit_once(
        self, app, auth_client, seed_user, seed_periods_today, url,
    ):
        """One ``<option>`` per unit however many readings it has.

        The MONTH unit has four offered readings (1, 3, 6 and the
        first-paycheck one) and must still render a single entry, or the user
        is asked to choose between four things called "months".
        """
        assert seed_periods_today
        with app.app_context():
            expected = []
            for option in cadence_options():
                if str(option.unit_id) not in expected:
                    expected.append(str(option.unit_id))

        resp = auth_client.get(url)
        body = resp.data.decode()

        assert resp.status_code == 200
        # The leading "" is the "Does not repeat" entry, which is the form's
        # own and not a cadence (plan step R2e-3).
        assert [
            value for value, _ in _options(body, "recurrence_unit")
        ] == [""] + expected

    def test_both_forms_offer_identical_cadences(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A user meets one vocabulary whichever kind they are creating."""
        assert seed_periods_today
        with app.app_context():
            transaction = auth_client.get("/templates/new").data.decode()
            transfer = auth_client.get("/transfers/new").data.decode()

        for select_id in ("recurrence_unit", "interval_n_fixed",
                          "recurrence_placement"):
            assert _options(transaction, select_id) == _options(
                transfer, select_id,
            ), select_id

    @pytest.mark.parametrize(
        "url", ["/templates/new", "/transfers/new"],
    )
    def test_neither_interval_control_submits_on_a_create_form(
        self, app, auth_client, seed_user, seed_periods_today, url,
    ):
        """Property 3, at the "Does not repeat" default.

        Both post ``interval_n``; a disabled control does not submit, so the
        schema must never see two spellings of one field.  On a create form no
        cadence is chosen, so neither may submit.
        """
        assert seed_periods_today
        resp = auth_client.get(url)
        body = resp.data.decode()

        assert "disabled" in _tag(body, "interval_n_free")
        assert "disabled" in _tag(body, "interval_n_fixed")

    @pytest.mark.parametrize(
        ("pattern", "enabled", "disabled"),
        [
            # The paycheck cadence takes ANY N, so the free box is live.
            (
                RecurrencePatternEnum.EVERY_N_PERIODS,
                "interval_n_free", "interval_n_fixed",
            ),
            # A month cadence takes 1 / 3 / 6 only, so the select is.
            (
                RecurrencePatternEnum.QUARTERLY,
                "interval_n_fixed", "interval_n_free",
            ),
            (
                RecurrencePatternEnum.ANNUAL,
                "interval_n_fixed", "interval_n_free",
            ),
        ],
    )
    def test_exactly_one_interval_control_submits_on_an_edit_form(
        self, app, auth_client, seed_user, seed_periods_today,
        pattern, enabled, disabled,
    ):
        """Property 3 on the form that starts with a cadence chosen.

        The server renders the state matching the stored rule, so the form is
        correct before any script runs -- which is what a user with a slow
        connection submits.
        """
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_pattern(
                seed_user,
                ref_cache.recurrence_pattern_id(pattern),
                interval_n=3 if pattern is RecurrencePatternEnum.EVERY_N_PERIODS
                else 1,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert "disabled" not in _tag(body, enabled)
        assert "disabled" in _tag(body, disabled)


# ── The edit form's starting state ───────────────────────────────────


class TestAnEditFormStartsOnItsRulesCadence:
    """Property 4: the controls read back what the rule actually means."""

    @pytest.mark.parametrize(
        ("pattern", "unit", "interval", "placement"),
        [
            (
                RecurrencePatternEnum.EVERY_PERIOD, RecurrenceUnitEnum.PERIOD,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                RecurrencePatternEnum.MONTHLY, RecurrenceUnitEnum.MONTH,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                RecurrencePatternEnum.MONTHLY_FIRST, RecurrenceUnitEnum.MONTH,
                1, PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
            ),
            (
                RecurrencePatternEnum.QUARTERLY, RecurrenceUnitEnum.MONTH,
                3, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                RecurrencePatternEnum.SEMI_ANNUAL, RecurrenceUnitEnum.MONTH,
                6, PeriodPlacementEnum.CONTAINING_DATE,
            ),
            (
                RecurrencePatternEnum.ANNUAL, RecurrenceUnitEnum.YEAR,
                1, PeriodPlacementEnum.CONTAINING_DATE,
            ),
        ],
    )
    def test_each_stored_pattern_preselects_its_own_axes(
        self, app, auth_client, seed_user, seed_periods_today,
        pattern, unit, interval, placement,
    ):
        """Every modelled pattern, decoded back onto the three controls.

        Swept rather than sampled because the decoding is per pattern: a
        mapping wrong for ONE of them shows a user the wrong cadence on the
        screen where they are about to re-save it.
        """
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_pattern(
                seed_user, ref_cache.recurrence_pattern_id(pattern),
            ).id
            expected_unit = str(ref_cache.recurrence_unit_id(unit))
            expected_placement = str(ref_cache.period_placement_id(placement))

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert _selected(body, "recurrence_unit") == expected_unit
        assert _selected(body, "recurrence_placement") == expected_placement
        if unit is RecurrenceUnitEnum.PERIOD:
            assert f'value="{interval}"' in _tag(body, "interval_n_free")
        else:
            assert _selected(body, "interval_n_fixed") == str(interval)

    def test_an_every_n_rule_prefills_its_own_interval(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The one cadence whose interval is a COLUMN reads back from it."""
        assert seed_periods_today
        with app.app_context():
            template_id = _template_with_pattern(
                seed_user,
                ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_N_PERIODS,
                ),
                interval_n=4,
            ).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert 'value="4"' in _tag(body, "interval_n_free")


class TestAnUnmodelledRuleCannotBeDestroyedBySavingIt:
    """A stored pattern the app no longer models renders UNSET -- and is SAFE.

    An HTML ``<select>`` whose selected value is absent from its options does
    not fail: the browser silently selects the first, and that option submits.
    Before plan step R7b-2 the picker met that by keeping the stored pattern as
    a trailing option, because the first entry is the empty "Does not repeat"
    one whose save DELETES the rule and sweeps its future rows (plan step
    R2e-1).

    The two-axis controls carry no pattern id, so there is nothing to keep
    selected -- they render unset, which means the unit select's first entry IS
    selected.  ``UNAVAILABLE_PATTERN_MESSAGE`` therefore promises the user that
    "saving it unchanged will be refused", and this class holds the code to
    that promise: the refusal is the SERVER's, made from the two facts it
    already holds (the stored rule is unmodelled, the submission names no
    cadence), not a hidden field a client can drop.
    """

    def test_the_transaction_form_renders_the_controls_unset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """No cadence is preselected, because none of them is this rule's."""
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_pattern_id()
            _assert_unresolvable(surplus_id)
            template_id = _template_with_pattern(seed_user, surplus_id).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        # The empty "Does not repeat" entry is what "unset" means on a
        # <select>, and it is the whole assertion: comparing the surplus
        # PATTERN id against the UNIT ids the control renders would be
        # measuring two unrelated sequences against each other -- vacuously
        # true today and a false failure the day they align.
        assert _selected(body, "recurrence_unit") == ""
        assert _selected(body, "interval_n_fixed") is None

    def test_the_transfer_form_renders_the_controls_unset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same on the transfer form."""
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            surplus_id = _unmodelled_pattern_id()
            template_id = _transfer_template_with_pattern(
                seed_user, savings, surplus_id,
            ).id

        body = auth_client.get(f"/transfers/{template_id}/edit").data.decode()

        assert _selected(body, "recurrence_unit") == ""

    def test_the_user_is_told_the_pattern_is_gone(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A warning names the state and the repair.

        With no odd option left on the control, the sentence is the whole of
        the explanation rather than half of it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_pattern_id()
            template_id = _template_with_pattern(seed_user, surplus_id).id

        body = auth_client.get(f"/templates/{template_id}/edit").data.decode()

        assert UNAVAILABLE_PATTERN_MESSAGE.split(".", maxsplit=1)[0] in body

    def test_saving_it_unchanged_is_refused_and_the_rule_survives(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The load-bearing one: re-submitting the form destroys nothing.

        The user opens the edit form to change the amount and saves.  The unit
        select submits the empty value the form rendered -- which on any other
        template means "does not repeat", and would DELETE the rule and sweep
        its future rows.  It is not a choice here: the form could not offer
        this rule's cadence, so the empty submission carries no intent, and
        the server refuses rather than acting on it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_pattern_id()
            template = _template_with_pattern(seed_user, surplus_id)
            template_id, rule_id = template.id, template.recurrence_rule_id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1300.00",
                # Exactly what the rendered form submits.
                "recurrence_unit": "",
                "recurrence_placement": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            # The REASON is asserted, not just the survival: without it this
            # test passes against a route that refused the POST for an
            # unrelated reason, and the message is the whole of what
            # UNAVAILABLE_PATTERN_MESSAGE promised the user on the way in.
            assert UNREPAIRED_CADENCE_CANNOT_BE_CLEARED.split(
                ",", maxsplit=1,
            )[0].encode() in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule_id == rule_id, (
                "the rule was detached by a save the form promised to refuse"
            )
            assert db.session.get(RecurrenceRule, rule_id) is not None, (
                "the rule row was DELETED by a save the form promised to refuse"
            )
            # The refused edit committed nothing, amount included.
            assert reloaded.default_amount == Decimal("1200.00")

    def test_the_transfer_door_refuses_it_too(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The same refusal on the other kind, because the guard is shared.

        Both routes dispatch through ``resolve_recurrence_rule_for_update``,
        and that function checks TWO refusals in order -- the loan-payment one
        and this -- so an ordering change between them would pass a suite that
        only drove the transaction form.
        """
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            surplus_id = _unmodelled_pattern_id()
            template = _transfer_template_with_pattern(
                seed_user, savings, surplus_id,
            )
            template_id = template.id
            rule_id = template.recurrence_rule_id

            resp = auth_client.post(f"/transfers/{template_id}", data={
                "name": "To Savings",
                "default_amount": "75.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "recurrence_unit": "",
                "recurrence_placement": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransferTemplate, template_id)
            assert reloaded.recurrence_rule_id == rule_id
            assert db.session.get(RecurrenceRule, rule_id) is not None
            assert reloaded.default_amount == Decimal("50.00")

    def test_choosing_a_cadence_repairs_the_rule(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The repair the surface asks for actually works.

        The class above proves the OTHER action is refused; a suite holding
        only that can watch the advertised remedy turn into a 500, which is
        what happened at plan step R7b-1 -- reading the rule's authored state
        on the way to REPLACING its cadence raised on the cadence being
        replaced.  ``recurrence_spec_with_cadence`` is what fixed it.
        """
        assert seed_periods_today
        with app.app_context():
            surplus_id = _unmodelled_pattern_id()
            _assert_unresolvable(surplus_id)
            template = _template_with_pattern(seed_user, surplus_id)
            template_id, rule_id = template.id, template.recurrence_rule_id
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                **cadence_payload(unit=RecurrenceUnitEnum.MONTH),
                "day_of_month": "1",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            # The SAME rule row, re-pointed -- not a new one, so every
            # generated row keeps its lineage.
            assert reloaded.recurrence_rule_id == rule_id
            assert reloaded.recurrence_rule.pattern_id == monthly_id
            assert reloaded.recurrence_rule.day_of_month == 1


class TestTheWriteDoorsRefuseAnUnstorableCadence:
    """A hand-assembled POST is refused with a message, and writes nothing.

    The end-to-end half of ``validate_authorable_cadence``.  Its predecessor
    covered the equivalent for a submitted PATTERN id and was deleted with the
    field; an adversarial review of plan step R7b-2 caught that nothing had
    replaced it, so a route that accepted the triple and 500'd at the flush
    would have passed the whole suite.

    ``(2, MONTH)`` is the case: well defined, walked correctly by the resolver,
    and with no closed-set pattern to be stored as until plan step R7c.  The
    picker offers 1 / 3 / 6 for months, so no click produces it.
    """

    #: The verbatim copy the routes flash.  Pinned because the message names
    #: the two controls the user can change, and the generic
    #: "correct the highlighted errors" prompt names none -- on a redirect that
    #: highlights nothing.
    _REFUSAL = b"That repeat schedule cannot be saved yet"

    def test_a_transaction_template_is_not_created(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates refuses, flashes, and persists no rule.

        Both halves matter: a 200 alone would also be produced by a route that
        created the template and redirected to the list.
        """
        assert seed_periods_today
        with app.app_context():
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/templates", data={
                "name": "Every Other Month Bill",
                "default_amount": "10.00",
                "category_id": str(seed_user["categories"]["Rent"].id),
                "transaction_type_id": str(_expense_type_id()),
                "account_id": str(seed_user["account"].id),
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH, interval_n=2,
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            assert db.session.query(TransactionTemplate).filter_by(
                name="Every Other Month Bill",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_a_transfer_template_is_not_created(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /transfers refuses the same triple the same way.

        Swept across both kinds because ``_form_errors`` exists precisely
        because the two forms once explained the same refusal differently.
        """
        assert seed_periods_today
        with app.app_context():
            savings = _savings_account(seed_user)
            rules_before = db.session.query(RecurrenceRule).count()

            resp = auth_client.post("/transfers", data={
                "name": "Every Other Month Transfer",
                "default_amount": "25.00",
                "from_account_id": str(seed_user["account"].id),
                "to_account_id": str(savings.id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH, interval_n=2,
                ),
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            assert db.session.query(TransferTemplate).filter_by(
                name="Every Other Month Transfer",
            ).first() is None
            assert db.session.query(RecurrenceRule).count() == rules_before

    def test_an_edit_leaves_the_existing_rule_untouched(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """POST /templates/<id> refuses and re-points nothing.

        The edit path re-AUTHORS the rule in place, so a door that accepted the
        triple would overwrite a working cadence on a row the user already
        depends on.
        """
        assert seed_periods_today
        with app.app_context():
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )
            template = _template_with_pattern(seed_user, monthly_id)
            template_id, rule_id = template.id, template.recurrence_rule_id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                **cadence_payload(
                    unit=RecurrenceUnitEnum.MONTH, interval_n=2,
                ),
                "day_of_month": "1",
            }, follow_redirects=True)

            assert resp.status_code == 200
            assert self._REFUSAL in resp.data
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule_id == rule_id
            assert reloaded.recurrence_rule.pattern_id == monthly_id


class TestTheCopyTheUserActuallyReads:
    """The picker's words, pinned verbatim.

    Restored after an adversarial review of plan step R7b-2 found the whole
    label surface unpinned: the migration's successor test compared option
    VALUES between the two forms and never read a label.  The template chooses
    between two of them -- ``unit_label_one if interval_n == 1 else
    unit_label_many`` -- so swapping the pair in ``_picker._UNIT_LABELS``
    renders "1 months", "3 month", "6 month", "1 years" with every other test
    in the suite still green.  ``CadenceOption``'s ``KeyError``-on-missing-copy
    design catches an ABSENT label; nothing caught a wrong one.
    """

    def test_the_unit_options_read_as_plurals(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """The unit select is worded for "Repeats <unit>", so plural."""
        assert seed_periods_today
        body = auth_client.get("/templates/new").data.decode()

        assert _option_labels(body, "recurrence_unit") == [
            "Does not repeat", "paychecks", "months", "years",
        ]

    def test_the_interval_options_agree_with_their_own_counts(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """"1 month", not "1 months" -- the count drives the noun.

        The one place the template picks between the two label forms.
        """
        assert seed_periods_today
        body = auth_client.get("/templates/new").data.decode()

        assert _option_labels(body, "interval_n_fixed") == [
            "1 paycheck", "1 month", "3 months", "6 months", "1 year",
        ]

    def test_the_placement_options_are_worded_over_the_date(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """Generic over the occurrence DATE rather than per unit.

        The old picker's "Monthly (first paycheck of month)" fused a unit, an
        interval, an anchor day and this choice into one name, which is why
        "every other month, funded from the first paycheck" had nowhere to
        live.
        """
        assert seed_periods_today
        body = auth_client.get("/templates/new").data.decode()

        assert _option_labels(body, "recurrence_placement") == [
            "The paycheck that covers the date",
            "The first paycheck starting on or after the date",
        ]

    def test_both_forms_word_every_control_identically(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A user meets one vocabulary whichever kind they are creating."""
        assert seed_periods_today
        transaction = auth_client.get("/templates/new").data.decode()
        transfer = auth_client.get("/transfers/new").data.decode()

        for select_id in ("recurrence_unit", "interval_n_fixed",
                          "recurrence_placement"):
            assert _option_labels(transaction, select_id) == _option_labels(
                transfer, select_id,
            ), select_id


class TestADeliberateClearStillWorks:
    """The negative control for the refusal above.

    Without it, a route that refused EVERY clear would pass every test in the
    class above -- and "does not repeat" is a real choice on both kinds, made
    from a form that offered the template's own cadence.
    """

    def test_a_modelled_rule_can_still_be_cleared(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """A rule the form COULD show is cleared by the empty unit."""
        assert seed_periods_today
        with app.app_context():
            template = _template_with_pattern(
                seed_user,
                ref_cache.recurrence_pattern_id(
                    RecurrencePatternEnum.EVERY_PERIOD,
                ),
            )
            template_id, rule_id = template.id, template.recurrence_rule_id

            resp = auth_client.post(f"/templates/{template_id}", data={
                "name": "Rent",
                "default_amount": "1200.00",
                "recurrence_unit": "",
            }, follow_redirects=True)

            assert resp.status_code == 200
            db.session.expire_all()
            reloaded = db.session.get(TransactionTemplate, template_id)
            assert reloaded.recurrence_rule_id is None
            assert db.session.get(RecurrenceRule, rule_id) is None
