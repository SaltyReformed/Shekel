"""
Tests -- ``StatedFigure``, a figure a door states and who wrote it

Plan step **balance:X-bi-3e-1**, rulings **R-BAL61** and **R-BAL69**: the value
every door hands a settle verb or a purchase door in place of a bare figure,
so that WHO WROTE the figure travels with it and nothing infers it from the
day beside it.  These are the constructor's own controls; what the doors and
the seam do with the value is ``test_covering_movement``'s subject.
"""

from decimal import Decimal

import pytest

from app.enums import MovementFigureSourceEnum
from app.services.stated_figure import StatedFigure


class TestTheConstructorRefusesWhatNoDoorMayState:
    """A stated figure is never the app's own pricing, and never nothing."""

    def test_resolved_is_refused(self):
        """``resolved`` is the settle's arm: a door handing it over would be
        laundering its own number as the app's inference."""
        with pytest.raises(ValueError, match="cannot claim the 'resolved' source"):
            StatedFigure(
                amount=Decimal("10.00"),
                source=MovementFigureSourceEnum.RESOLVED,
            )

    def test_a_none_figure_is_refused(self):
        """A door with no figure passes ``None`` in place of the whole value."""
        with pytest.raises(ValueError, match="cannot wrap None"):
            StatedFigure(amount=None, source=MovementFigureSourceEnum.TYPED)

    @pytest.mark.parametrize(
        "source",
        [MovementFigureSourceEnum.TYPED, MovementFigureSourceEnum.OBSERVED],
    )
    def test_the_two_writers_construct(self, source):
        """The accepting cases, without which the refusals prove nothing."""
        figure = StatedFigure(amount=Decimal("-28.29"), source=source)
        assert figure.amount == Decimal("-28.29")
        assert figure.source is source

    def test_the_value_is_frozen(self):
        """A door cannot restate who wrote a figure after building it."""
        figure = StatedFigure(
            amount=Decimal("10.00"), source=MovementFigureSourceEnum.TYPED,
        )
        with pytest.raises(AttributeError):
            figure.source = MovementFigureSourceEnum.OBSERVED  # type: ignore[misc]
