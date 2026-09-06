"""Tests for ``app.routes._authored_figure`` -- did a HUMAN type this figure?

The three arms of ruling **R-JR**'s question, graded directly rather than only
through the doors that ask it.  The doors' own suites grade what the answer
CAUSES (a leg taken, a flag raised); these grade the answer itself, including
the one arm no door can reach on purpose -- a figure arriving with no companion.

**The fail-closed arm is the reason this file exists.**  It is chosen behaviour,
not a fallback, and it is the arm a crafted request or a stale cached form lands
on.  Nothing else in the suite states which direction it fails in, so a later
reader flipping it to "an unaccompanied figure is an echo" would find every
other test still green while silently discarding numbers users had typed.
"""

from decimal import Decimal

from app.routes._authored_figure import as_rendered_field, figure_was_authored
from app.schemas.validation.transactions import TransactionUpdateSchema
from app.schemas.validation.transfers import TransferUpdateSchema


class TestTheCompanionFieldsName:
    """The companion's name is derived in ONE place."""

    def test_it_suffixes_the_field_it_accompanies(self):
        """The name a form must post is the field's name plus the suffix.

        Stated as a test because the name is spelled in four templates and two
        schemas, and a template that spells it differently does not fail --
        it silently lands every save on the fail-closed arm below.
        """
        assert as_rendered_field("amount") == "amount_as_rendered"
        assert as_rendered_field("estimated_amount") == (
            "estimated_amount_as_rendered"
        )


class TestWhetherAHumanAuthoredTheFigure:
    """Ruling **R-JR**'s three arms."""

    def test_no_figure_at_all_authors_nothing(self):
        """A payload stating no amount states no authorship.

        The notes-only save on a row whose amount box was disabled or absent:
        there is no figure, so there is nothing for a human to have typed.
        """
        assert figure_was_authored({"notes": "hello"}, "amount") is False

    def test_an_echoed_figure_is_not_authored(self):
        """The figure that came back equals the one we showed, so nobody typed.

        This is the arm that carries the whole defect class.  Both doors used
        to answer YES here -- the transfer door because the stored column
        happened to differ, the transaction door because it never compared at
        all -- and a row that answers YES stops tracking its definition.
        """
        data = {"amount": Decimal("250.00"), "amount_as_rendered": Decimal("250.00")}
        assert figure_was_authored(data, "amount") is False

    def test_an_echo_is_numeric_not_textual(self):
        """``250.0`` echoed against a rendered ``250.00`` is still an echo.

        The comparison is between DECIMALS, which the schema has already
        coerced.  A string comparison would read a browser's own formatting as
        a re-price -- and the browsers differ, so the defect would appear for
        some users and not others.
        """
        data = {"amount": Decimal("250.0"), "amount_as_rendered": Decimal("250.00")}
        assert figure_was_authored(data, "amount") is False

    def test_a_changed_figure_is_authored(self):
        """A figure differing from the rendered one is a human's.

        The retype, which must still reach the row: the whole point of keeping
        the box editable is that an owner can re-price one instance.
        """
        data = {"amount": Decimal("400.00"), "amount_as_rendered": Decimal("250.00")}
        assert figure_was_authored(data, "amount") is True

    def test_a_figure_with_NO_companion_claims_no_authorship(self):
        """The residual, and it is unreachable through any door.

        **This case pinned the opposite answer until an adversarial review.**
        The first implementation treated an unaccompanied figure as AUTHORED, on
        the argument that wrongly taking an echo is undone by the conflict
        resolver while a discarded re-price is not.  Both halves were measured
        false: ``is_override`` is in no schema and the chooser is suppressed for
        salary-linked templates, so a wrongly-taken salary row -- N-248's own
        population -- has no in-app hand-back; while a discarded figure
        re-renders the row's cell immediately in front of the person who typed
        it.

        The developer then ruled the question away rather than reversing it: a
        payload stating a figure without stating what was shown is MALFORMED and
        the schemas refuse it, which is graded by
        ``TestTheSchemasRefuseAFigureWithNoCompanion`` below.  What is left here
        is the residual for a caller that reaches this function directly, and it
        declines to claim an authorship it cannot evidence.
        """
        assert figure_was_authored({"amount": Decimal("400.00")}, "amount") is False

    def test_the_two_doors_ask_about_their_OWN_field(self):
        """One producer, two field names, no shared mutable state.

        The transaction door asks about ``estimated_amount`` and the transfer
        door about ``amount``; a payload carrying one says nothing about the
        other.  Graded because the doors' field names differ while their rule
        does not, which is exactly the shape a single producer exists to keep
        from drifting.
        """
        data = {
            "estimated_amount": Decimal("400.00"),
            "estimated_amount_as_rendered": Decimal("400.00"),
        }
        assert figure_was_authored(data, "estimated_amount") is False
        assert figure_was_authored(data, "amount") is False


class TestTheSchemasRefuseAFigureWithNoCompanion:
    """The refusal that makes the residual above unreachable (**R-JR**).

    Graded at the SCHEMA rather than only at a route, because the schema is
    where the rule lives and because both doors must answer identically: a
    reader asking "what happens when a form forgets the companion" should find
    one answer, not two that happen to agree today.
    """

    def test_the_transaction_schema_refuses_it(self):
        """An ``estimated_amount`` with no companion is refused, not guessed."""
        errors = TransactionUpdateSchema().validate({"estimated_amount": "400.00"})
        assert "estimated_amount_as_rendered" in errors, errors

    def test_the_transfer_schema_refuses_it(self):
        """An ``amount`` with no companion is refused, not guessed."""
        errors = TransferUpdateSchema().validate({"amount": "400.00"})
        assert "amount_as_rendered" in errors, errors

    def test_a_payload_with_NO_figure_is_not_refused(self):
        """The refusal is about the PAIR, not about demanding a companion.

        A notes-only save on a row whose amount box is disabled posts neither,
        and must still succeed -- otherwise the rule would break the documented
        unlock path on every finalised row, which is the trap ruling R-EG
        removed for the settle day.
        """
        assert TransactionUpdateSchema().validate({"notes": "hello"}) == {}
        assert TransferUpdateSchema().validate({"notes": "hello"}) == {}

    def test_the_pair_together_is_accepted(self):
        """The ordinary case: both present, so the door can judge."""
        assert TransactionUpdateSchema().validate({
            "estimated_amount": "400.00",
            "estimated_amount_as_rendered": "250.00",
        }) == {}
        assert TransferUpdateSchema().validate({
            "amount": "400.00",
            "amount_as_rendered": "250.00",
        }) == {}
