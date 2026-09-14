"""The rhythm's wire: one table, two inverses, and the refusals live in a validator.

Plan step ``pay_calendar:C17-d-3``, ruling **R-PC84**.
:mod:`app.schemas.validation._pay_rhythm` is where a stated rhythm becomes a
:class:`~app.services.pay_rhythm.Rhythm` and where a rhythm becomes the keys
a form posts.  The route cases in
``tests/test_routes/test_pay_cadence_kind_doors.py`` drive the browser's
path; these grade the module's own contracts, which no HTTP case can see
whole: that the two directions are inverses for every kind, that the wire
spelling matches an INDEPENDENT hand spelling, that the loaded payload
carries the value and none of the keys, and that a refusal is raised where
``Schema.validate`` runs it.
"""
import pytest
from marshmallow import ValidationError

from app.enums import BusinessDayShiftEnum
from app.schemas.validation import (
    PayPeriodGenerateSchema,
    PayPeriodRegenerateSchema,
    RegisterSchema,
    cadence_kind_token,
    rhythm_to_wire,
)
from app.schemas.validation._pay_rhythm import (
    RHYTHM_WIRE_KEYS,
    rhythm_from_wire,
)
from app.services.pay_rhythm import FixedDays, Monthly, Rhythm, SemiMonthly
from tests._test_helpers import cadence_form_values, shift_form_value

#: One value of each kind, and the sorted pair stated backwards on purpose,
#: each beside a first payday on its grid.
KINDS = (
    (FixedDays(7), "2026-06-05"),
    (Monthly(31), "2026-05-31"),
    (SemiMonthly((20, 5)), "2026-06-05"),
)


class TestTheTwoDirectionsAreInverses:
    """``rhythm_from_wire(rhythm_to_wire(r)) == r`` for every kind."""

    @pytest.mark.parametrize(
        "cadence,payday", KINDS, ids=[type(c).__name__ for c, _ in KINDS],
    )
    def test_round_trip_through_the_schema(self, app, cadence, payday):
        """Rendered back to the wire and LOADED again, the value is the same.

        Through the real schema rather than the two functions alone, so the
        field types sit in the loop: the token the inverse writes must be one
        ``CadenceKindField`` maps, and the convention's id one
        ``BusinessDayShiftField`` maps.
        """
        with app.app_context():
            rhythm = Rhythm(cadence=cadence, shift=BusinessDayShiftEnum.PRIOR)
            wire = {
                key: str(value) for key, value in rhythm_to_wire(rhythm).items()
            }
            loaded = PayPeriodRegenerateSchema().load({
                "new_start_date": payday, "num_periods": "2", **wire,
            })
            assert loaded["rhythm"] == rhythm

    @pytest.mark.parametrize(
        "cadence", [c for c, _ in KINDS], ids=[type(c).__name__ for c, _ in KINDS],
    )
    def test_the_wire_spelling_matches_the_hand_spelling(self, app, cadence):
        """The application's tokens and keys against an independent oracle.

        :func:`~tests._test_helpers.cadence_form_values` spells the wire by
        hand so that a token or key that drifted in the schema's table would
        disagree here rather than agree with itself through the round trip
        above.  Only the chosen arm's keys are compared: the helper also
        posts the unchosen arms' boxes, as a browser does, and the inverse
        renders only the arm the value belongs to.
        """
        with app.app_context():
            rendered = rhythm_to_wire(
                Rhythm(cadence=cadence, shift=BusinessDayShiftEnum.NEXT),
            )
            by_hand = cadence_form_values(cadence)
            assert rendered["cadence_kind"] == by_hand["cadence_kind"]
            assert rendered["cadence_kind"] == cadence_kind_token(type(cadence))
            arm_keys = set(rendered) - {"cadence_kind", "shift"}
            assert {key: str(rendered[key]) for key in arm_keys} == {
                key: by_hand[key] for key in arm_keys
            }
            assert str(rendered["shift"]) == shift_form_value(
                BusinessDayShiftEnum.NEXT,
            )

    def test_the_wire_keys_are_exactly_the_six_a_form_posts(self):
        """What the ``@post_load`` consumes is what the macro renders."""
        assert RHYTHM_WIRE_KEYS == frozenset(cadence_form_values()) | {"shift"}


class TestTheLoadedPayloadCarriesTheValue:
    """``rhythm`` is on the payload and no wire key is."""

    def test_a_semi_monthly_load_leaves_no_wire_key_behind(self, app):
        """The pair is sorted, the value is a ``Rhythm``, the keys are gone."""
        with app.app_context():
            loaded = PayPeriodGenerateSchema().load({
                "start_date": "2026-06-15",
                **cadence_form_values(SemiMonthly((15, 1))),
                "shift": shift_form_value(),
            })
            assert loaded["rhythm"] == Rhythm(
                cadence=SemiMonthly((1, 15)), shift=BusinessDayShiftEnum.NONE,
            )
            assert not RHYTHM_WIRE_KEYS & set(loaded)

    def test_registration_loads_the_same_value_the_generate_door_does(self, app):
        """The fifth door declares the rhythm ONCE with generate's."""
        with app.app_context():
            loaded = RegisterSchema().load({
                "email": "kind@example.com",
                "display_name": "Kind",
                "password": "securepass123",
                "confirm_password": "securepass123",
                "last_payday": "2026-06-15",
                **cadence_form_values(Monthly(15)),
                "shift": shift_form_value(),
                "history_opens_on": "",
            })
            assert loaded["rhythm"].cadence == Monthly(15)
            assert loaded["history_opens_on"] is None
            assert not RHYTHM_WIRE_KEYS & set(loaded)


class TestTheRefusalsLiveWhereValidateRunsThem:
    """``Schema.validate`` sees every refusal, so the routes' 422 path does.

    The routes call ``validate()`` first and ``load()`` only on a clean
    payload; marshmallow runs NO ``@post_load`` under ``validate()``.  A
    refusal that lived only in the builder's post-load would pass
    ``validate()`` and then raise out of ``load()`` as a 500.  Each case
    below asks ``validate()`` alone.
    """

    def test_a_blank_chosen_arm_is_reported_by_validate(self, app):
        """The missing-input refusal is a validator's, attributed to the box."""
        with app.app_context():
            errors = PayPeriodGenerateSchema().validate({
                "start_date": "2026-06-15",
                **cadence_form_values(Monthly(15)),
                "day_of_month": "",
                "shift": shift_form_value(),
            })
            assert errors == {
                "day_of_month": ["Enter the day of the month you are paid on."],
            }

    def test_the_pair_rule_is_reported_by_validate_on_the_second_box(self, app):
        """The write door's own sentence, on the arm's last control."""
        with app.app_context():
            errors = PayPeriodGenerateSchema().validate({
                "start_date": "2026-06-28",
                **cadence_form_values(SemiMonthly((28, 30))),
                "shift": shift_form_value(),
            })
            assert list(errors) == ["second_day_of_month"]
            assert "must be day 27 or before; got 28 and 30" in (
                errors["second_day_of_month"][0]
            )

    def test_the_grid_question_is_reported_on_the_doors_own_payday_key(self, app):
        """Generate names ``start_date``; regenerate names ``new_start_date``."""
        with app.app_context():
            wire = {**cadence_form_values(Monthly(15)), "shift": shift_form_value()}
            generate = PayPeriodGenerateSchema().validate({
                "start_date": "2026-06-10", **wire,
            })
            regenerate = PayPeriodRegenerateSchema().validate({
                "new_start_date": "2026-06-10", "num_periods": "2", **wire,
            })
            assert list(generate) == ["start_date"]
            assert list(regenerate) == ["new_start_date"]
            assert generate["start_date"] == regenerate["new_start_date"]

    def test_a_field_error_stops_the_cross_field_rules(self, app):
        """An out-of-range day is the field's own refusal and nothing else's.

        Marshmallow skips every schema validator while a field error stands,
        so the builder is never asked to read a day the field refused.
        """
        with app.app_context():
            errors = PayPeriodGenerateSchema().validate({
                "start_date": "2026-06-15",
                **cadence_form_values(Monthly(32)),
                "shift": shift_form_value(),
            })
            assert list(errors) == ["day_of_month"]

    def test_rhythm_from_wire_refuses_a_blank_arm_against_its_control(self):
        """The builder's own refusal, for the validator that asks it."""
        with pytest.raises(ValidationError) as exc:
            rhythm_from_wire({
                "cadence_kind": SemiMonthly, "shift": BusinessDayShiftEnum.NONE,
                "first_day_of_month": 1, "second_day_of_month": None,
            })
        assert exc.value.normalized_messages() == {
            "second_day_of_month": ["Enter the second of your two paydays."],
        }
