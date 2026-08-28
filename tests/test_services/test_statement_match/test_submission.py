"""What a tick carries back, as values: the token, and what it reconciles.

Plan step ``bank_import:X-f6d-3``, finding **N-336**.  A review has two moments
-- the screen states a correction, the owner presses Apply later -- and nothing
compared them until this step: the screen offered *from ``-178.32`` to
``-178.29``*, the row was edited to ``500.00`` in another tab, and the door
wrote a **``$321.71``** correction under that caption.

**These are pure-value cases and they carry the load the door cases cannot.**
:class:`~app.services.statement_match.ReviewedRow` has to be right about three
independent writers, and staging each of them through the database costs a
fixture that hides which coordinate actually fired.  The door's own case
(``test_accept``) proves the guard is REACHED; these prove it is COMPLETE.
"""

from decimal import Decimal

import pytest

from app.services.statement_match import (
    CandidateRow,
    ReviewedRow,
    RowKind,
    as_reviewed,
)


def _row(**overrides):
    """Return one candidate row, varied one field at a time.

    Only the four fields a reviewed state reads are meaningful here; the rest
    are what a settled transaction carries, so the value is constructible
    rather than contrived.
    """
    fields = {
        "kind": RowKind.TRANSACTION,
        "row_id": 2461,
        "label": "Geico",
        "cash_amount": Decimal("-178.32"),
        "settled_on": None,
        "is_settled": True,
        "states_own_figure": True,
        "version_id": 3,
    }
    fields.update(overrides)
    return CandidateRow(**fields)


class TestTheTokenIsOneFormatReadBothWays:
    """The template writes it and the schema reads it, so it must round-trip.

    **A format spelled twice is this arc's own root cause 1**, and the pair
    here is the worst case of it: a Jinja template and a Marshmallow validator
    have no compile-time relationship at all, so nothing in the tree fails when
    they drift.  Stating it once and grading the round trip is what replaces
    the failure nothing would otherwise raise.
    """

    def test_a_row_survives_the_round_trip(self):
        """The property the whole wire format rests on."""
        row = as_reviewed(_row())

        assert ReviewedRow.from_token(row.token) == row

    def test_both_kinds_and_both_signs_round_trip(self):
        """A purchase is negative cash and a deposit is positive.

        The sign is carried rather than derived, so a reader that dropped it
        would still parse -- and would compare a `$2,473.38` paycheck against
        its own negation and refuse every payroll match.
        """
        for kind in RowKind:
            for figure in ("-178.32", "2473.38", "0.00", "-0.01"):
                row = as_reviewed(
                    _row(kind=kind, cash_amount=Decimal(figure)),
                )
                assert ReviewedRow.from_token(row.token) == row

    def test_a_figure_with_no_exponent_form_is_emitted(self):
        """``str(Decimal)`` can produce ``"1E+3"`` and the reader refuses it.

        Not hypothetical arithmetic-trivia: a producer handing back a
        positive-exponent ``Decimal`` would make its OWN token unreadable, so
        the screen would refuse a match nobody had touched.  ``:f`` has no
        exponent form, which is why the property is emitted with it.
        """
        row = as_reviewed(_row(cash_amount=Decimal("1E+3")))

        assert "E" not in row.token
        assert ReviewedRow.from_token(row.token).cash_amount == Decimal("1000")

    def test_a_token_carrying_a_FIFTH_field_is_refused_BY_NAME(self):
        """The designed refusal, not an incidental unpack.

        ``len(parts) != _TOKEN_FIELDS`` and ``len(parts) < _TOKEN_FIELDS``
        both refuse a five-field token today -- the second by letting the tuple
        unpack raise -- so only the SENTENCE separates them.  Pinning it is
        what keeps a field added to this token later from being silently
        dropped instead of refused: found by a mutation sweep 2026-08-23, where
        relaxing the test to ``<`` survived every other case in this file.
        """
        with pytest.raises(ValueError, match="four fields"):
            ReviewedRow.from_token("transaction:2461:-178.32:3:9")

    @pytest.mark.parametrize("raw", [
        "transaction:2461:-178.32",
        "transaction:2461:-178.32:3:9",
        "ledger:2461:-178.32:3",
        "transaction:2461:NaN:3",
        "transaction:0:-178.32:3",
        "transaction:2461:-178.32:0",
        "",
    ])
    def test_a_token_this_app_did_not_emit_raises_ValueError(self, raw):
        """Total over every ``str``, because a form door has to be.

        ``app/error_handlers.py`` registers no ``ValueError`` arm, so anything
        escaping here is a 500 on the door that applies a whole reviewed pass
        -- which is exactly what ``apply=%C2%B2`` was once
        (``order_token_key``, adversarial security review 2026-08-19).  The schema
        field turns this into a 400; nothing else may see it.

        Args:
            raw: A token shape this application cannot have written.
        """
        with pytest.raises(ValueError):
            ReviewedRow.from_token(raw)


class TestNeitherCoordinateSeesTheOthersWriters:
    """Why a reviewed row carries TWO facts and not one.

    The finding was filed proposing a version counter alone, and that catches
    the case that was REPRODUCED and misses one that is live -- which is the
    shape ``resolve_rows``' own docstring already names: *enumerating sibling
    writes is a guard the next unenumerated writer reopens*.  Measured on a
    production clone 2026-08-23, one probe per writer; these are those three
    probes as values.
    """

    def test_a_retyped_amount_is_seen(self):
        """The reproduced case: `$321.71` written under a `$0.03` caption."""
        reviewed = as_reviewed(_row())

        moved = reviewed.disagrees_with(_row(cash_amount=Decimal("-500.00")))

        assert moved is not None
        assert "-178.32" in moved and "-500.00" in moved

    def test_a_CHILD_write_is_seen_by_the_FIGURE_and_not_the_counter(self):
        """Adding a card entry moved the row `$25.00` with the counter still.

        A transaction's cash is ``gross - off_statement_sum(entries)``, so a
        child INSERT emits no UPDATE against the parent and no counter on it
        can move.  Measured: ``-178.32`` to ``-153.32``, ``version_id`` 3 to 3.
        """
        reviewed = as_reviewed(_row())
        after_child = _row(cash_amount=Decimal("-153.32"), version_id=3)

        assert reviewed.version_id == after_child.version_id
        assert reviewed.disagrees_with(after_child) is not None

    def test_a_DATE_move_is_seen_by_the_COUNTER_and_not_the_figure(self):
        """Moving ``purchased_on`` left the figure alone: 1 to 2, `-121.12`.

        It is not a cosmetic difference: that day decides whether a match
        RE-DATES the purchase, which is the one write releasing a match cannot
        undo.
        """
        reviewed = as_reviewed(_row())
        after_edit = _row(version_id=4)

        assert reviewed.cash_amount == after_edit.cash_amount
        assert reviewed.disagrees_with(after_edit) is not None

    def test_an_unmoved_row_agrees(self):
        """The control that keeps the two above from being tautologies.

        Every ordinary tick takes this arm: 137 of the developer's own
        proposals apply unchanged, and a guard that refused them all would
        pass both cases above while breaking the screen.
        """
        assert as_reviewed(_row()).disagrees_with(_row()) is None

    def test_a_figure_that_differs_only_in_SCALE_agrees(self):
        """``Decimal("178.3") == Decimal("178.30")``, so a re-read cannot flake.

        The comparison is by VALUE rather than by spelling, deliberately: a
        producer that returned a differently-scaled Decimal for the same money
        would otherwise refuse a match nobody had touched.
        """
        reviewed = as_reviewed(_row(cash_amount=Decimal("-178.3")))

        assert reviewed.disagrees_with(_row(cash_amount=Decimal("-178.30"))) is None

    def test_the_sentence_NAMES_the_row(self):
        """Ruling **R-FZ(a)**: a refusal is quoted in the service's own words.

        A batch reports one sentence per refused item, so *something moved* is
        not an answer -- the owner has to be able to tell a stale tab from a
        bug, and that means the row and both figures.
        """
        moved = as_reviewed(_row()).disagrees_with(
            _row(cash_amount=Decimal("-500.00")),
        )

        assert "Geico" in moved
