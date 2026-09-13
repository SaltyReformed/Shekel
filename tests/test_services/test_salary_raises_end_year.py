"""
Shekel Budget App -- The ONE end-year rule (salary:S3-f-2b, ruling R-SAL22)

:func:`app.services.salary_raises.end_year_of` is the single statement of what
a valid answer to "how long is this recurring raise believed?" is.  Three doors
ask it -- the salary form (``RaiseCreateSchema``, against the payload's
effective year), the ``/retirement`` rail's per-raise probe
(``RetirementInputs.plan_with``, against the ROW's) and, at plan step S3-f-3,
the rail's Save -- so the rule is graded once, here, on plain inputs, and each
door's tests grade only that it CALLS the rule.
"""

import pytest

from app.services.salary_raises import (
    RAISE_END_MODE_NONE,
    RAISE_END_MODE_YEAR,
    RAISE_END_MODES,
    RAISE_YEAR_MAX,
    RAISE_YEAR_MIN,
    EndYearError,
    end_year_of,
)


class TestEndYearOf:
    """Every arm of the rule, with the half of the answer each one blames."""

    def test_the_vocabulary_is_the_two_answers(self):
        """Exactly ``year`` and ``none`` -- the salary form's two radios."""
        assert RAISE_END_MODES == ("year", "none")
        assert RAISE_END_MODE_YEAR == "year"
        assert RAISE_END_MODE_NONE == "none"

    def test_no_end_year_resolves_to_none_whatever_the_box_holds(self):
        """The mode is AUTHORITATIVE (R-SAL13): a stale year box is ignored."""
        assert end_year_of("none", None, 2027) is None
        assert end_year_of("none", 2031, 2027) is None
        # Even a year the ordering rule would refuse: under "none" there is
        # no value left to contradict anything.
        assert end_year_of("none", 2020, 2027) is None

    def test_an_end_year_on_or_after_the_effective_year_is_the_answer(self):
        """The tightest year the CHECK permits is the effective year itself."""
        assert end_year_of("year", 2027, 2027) == 2027
        assert end_year_of("year", 2031, 2027) == 2031
        # The ceiling is inclusive, as ``ck_salary_raises_valid_terminal_year``'s is.
        assert end_year_of("year", 2100, 2027) == 2100

    def test_a_year_past_the_ceiling_is_refused_on_the_year(self):
        """R-SAL22's third clause -- *and not past 2100* -- is the rule's own.

        The schemas refuse it earlier on the control through a ``Range`` built
        from the same constant; this is the clause a service door resolving
        through the rule alone relies on (``ck_salary_raises_valid_terminal_year``
        would otherwise be the first thing to say no, as a 500).
        """
        assert (RAISE_YEAR_MIN, RAISE_YEAR_MAX) == (2000, 2100)
        with pytest.raises(EndYearError) as info:
            end_year_of("year", 2101, 2027)
        assert info.value.field == "year"
        assert info.value.message == "A raise cannot be believed past 2100."

    def test_an_unanswered_mode_is_refused_on_the_mode(self):
        """An unanswered end year would mean *forever*; it is REFUSED instead."""
        for mode in (None, "", "forever", "YEAR"):
            with pytest.raises(EndYearError) as info:
                end_year_of(mode, 2031, 2027)
            assert info.value.field == "mode"
            assert info.value.message == (
                "Say how long this recurring raise is believed: pick an end "
                "year, or say it has none."
            )

    def test_a_missing_year_under_the_year_mode_is_refused_on_the_year(self):
        """"Ends after" with an empty box answers nothing."""
        with pytest.raises(EndYearError) as info:
            end_year_of("year", None, 2027)
        assert info.value.field == "year"
        assert info.value.message == (
            "Enter the last year this raise is believed to happen."
        )

    def test_a_year_before_the_effective_year_is_refused_on_the_year(self):
        """``ck_salary_raises_terminal_year_not_before_effective``'s mirror."""
        with pytest.raises(EndYearError) as info:
            end_year_of("year", 2026, 2027)
        assert info.value.field == "year"
        assert info.value.message == (
            "A raise cannot end before it starts: it takes effect in 2027."
        )
        # The exception is a ValueError whose str is the owner's sentence.
        assert str(info.value) == info.value.message
        assert isinstance(info.value, ValueError)
