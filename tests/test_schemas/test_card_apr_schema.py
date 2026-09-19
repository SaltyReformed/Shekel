"""Tests for :class:`~app.schemas.validation.CardAprSchema` (plan step credit_card:CC-3).

The APR form's two controls: the percent is divided to the stored fraction
at the column's five places, the domain's BOUNDS are admitted (0% and 100%;
a bound one step too tight passes every refusal case), one step past each is
refused, and the effective date holds the calendar window every
effective-dated field holds.
"""

from datetime import date
from decimal import Decimal

import pytest
from marshmallow import ValidationError

from app.schemas.validation import CardAprSchema
from app.schemas.validation._helpers import (
    EFFECTIVE_DATE_MAX,
    EFFECTIVE_DATE_MIN,
)

_schema = CardAprSchema()


def _load(**form):
    """Load a form as the browser posts it (strings)."""
    return _schema.load({"effective_date": "2026-03-01", "interest_rate": "24.99", **form})


class TestThePercentIsStoredAsAFraction:
    """E-28: ``24.99`` on the form is ``0.24990`` in the column."""

    def test_a_percent_divides_to_five_places(self):
        """24.99 / 100 = 0.2499, at the column's ``Numeric(7, 5)`` scale."""
        assert _load()["interest_rate"] == Decimal("0.24990")

    def test_a_sixth_place_rounds_at_the_columns_scale(self):
        """24.9995 / 100 = 0.249995 -> five places."""
        assert _load(interest_rate="24.9995")["interest_rate"] == Decimal("0.25000")

    @pytest.mark.parametrize(
        ("percent", "fraction"),
        [("0", Decimal("0.00000")), ("100", Decimal("1.00000"))],
        ids=["zero", "one-hundred"],
    )
    def test_both_bounds_are_admitted(self, percent, fraction):
        """0% and 100% are the CHECK's closed interval, and the schema's."""
        assert _load(interest_rate=percent)["interest_rate"] == fraction

    @pytest.mark.parametrize("percent", ["-0.001", "100.001"], ids=["below", "above"])
    def test_one_step_past_each_bound_is_refused(self, percent):
        """The refusal names the control."""
        with pytest.raises(ValidationError) as exc:
            _load(interest_rate=percent)
        assert "interest_rate" in exc.value.messages

    def test_a_blank_rate_is_required(self):
        """An empty control is dropped and the field's own message names it."""
        with pytest.raises(ValidationError) as exc:
            _load(interest_rate="")
        assert "interest_rate" in exc.value.messages


class TestTheEffectiveDate:
    """A calendar date inside the effective-date window."""

    def test_a_date_loads(self):
        """ISO input becomes a date."""
        assert _load()["effective_date"] == date(2026, 3, 1)

    @pytest.mark.parametrize(
        "bound", [EFFECTIVE_DATE_MIN, EFFECTIVE_DATE_MAX], ids=["min", "max"],
    )
    def test_both_bounds_of_the_window_are_admitted(self, bound):
        """The window is closed on both ends."""
        assert _load(effective_date=bound.isoformat())["effective_date"] == bound

    def test_a_four_digit_year_typo_is_refused(self):
        """``0202-03-01`` is a valid date and outside the window."""
        with pytest.raises(ValidationError) as exc:
            _load(effective_date="0202-03-01")
        assert "effective_date" in exc.value.messages

    def test_a_blank_date_is_required(self):
        """An empty control is dropped and the field's own message names it."""
        with pytest.raises(ValidationError) as exc:
            _load(effective_date="")
        assert "effective_date" in exc.value.messages
