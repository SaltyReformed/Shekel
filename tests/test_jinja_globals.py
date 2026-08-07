"""Tests for the shared ID-derived Jinja-globals registration helper.

Locks the F-7 / Commit 6 invariant: the single source of truth for
the Jinja globals lives in :mod:`app.jinja_globals`, both
``create_app()`` and the conftest's per-test re-seat route through
it, and every previously-missing constant is now present so any
template that references one no longer raises ``UndefinedError``
at request time.
"""

# pylint: disable=import-outside-toplevel

from app import ref_cache
from app.enums import (
    CalcMethodEnum, DeductionTimingEnum, GoalModeEnum, IncomeUnitEnum,
    RecurrencePatternEnum,
)
from app.jinja_globals import _REF_ID_GLOBALS, register_ref_id_globals


def test_register_ref_id_globals_populates_previously_missing_entries(app):
    """All eight C-28-era missing constants are present after registration.

    The conftest list pre-Commit-6 omitted these; templates that
    referenced any of them at test time raised UndefinedError.
    """
    with app.app_context():
        register_ref_id_globals(app)

        assert app.jinja_env.globals["TIMING_PRE_TAX"] == (
            ref_cache.deduction_timing_id(DeductionTimingEnum.PRE_TAX)
        )
        assert app.jinja_env.globals["TIMING_POST_TAX"] == (
            ref_cache.deduction_timing_id(DeductionTimingEnum.POST_TAX)
        )
        assert app.jinja_env.globals["CALC_PERCENTAGE"] == (
            ref_cache.calc_method_id(CalcMethodEnum.PERCENTAGE)
        )
        assert app.jinja_env.globals["CALC_FLAT"] == (
            ref_cache.calc_method_id(CalcMethodEnum.FLAT)
        )
        assert app.jinja_env.globals["GOAL_MODE_FIXED"] == (
            ref_cache.goal_mode_id(GoalModeEnum.FIXED)
        )
        assert app.jinja_env.globals["GOAL_MODE_INCOME_RELATIVE"] == (
            ref_cache.goal_mode_id(GoalModeEnum.INCOME_RELATIVE)
        )
        assert app.jinja_env.globals["INCOME_UNIT_PAYCHECKS"] == (
            ref_cache.income_unit_id(IncomeUnitEnum.PAYCHECKS)
        )
        assert app.jinja_env.globals["INCOME_UNIT_MONTHS"] == (
            ref_cache.income_unit_id(IncomeUnitEnum.MONTHS)
        )


def test_register_ref_id_globals_is_idempotent(app):
    """Calling the helper twice in succession is a no-op (same values).

    Each per-test ref-cache reseat calls the helper; subsequent
    invocations during a session must produce identical Jinja
    globals dicts so templates rendered between calls see a
    consistent view.
    """
    with app.app_context():
        register_ref_id_globals(app)
        first = dict(app.jinja_env.globals)

        register_ref_id_globals(app)
        second = dict(app.jinja_env.globals)

        # Every key the registration table declares, DERIVED from the table
        # rather than mirrored by hand.  The hand-written copy this replaced
        # had drifted from the module it was checking: it listed 45 names and
        # asserted that count, while ``_REF_ID_GLOBALS`` registered 49 at the
        # time -- ``REC_EVERY_PERIOD`` and all three ``EMPLOYER_TYPE_*`` were
        # absent, and nothing could notice, because the loop below only
        # asserts that each LISTED key is present.  A second copy of a
        # single-source-of-truth list is the exact defect the F-7 extraction
        # removed from ``create_app`` and the conftest; re-introducing it here
        # as a test fixture made it a place a constant could hide unverified.
        registered_keys = {
            name
            for _accessor, members in _REF_ID_GLOBALS
            for name in members
        }
        assert len(registered_keys) == sum(
            len(members) for _accessor, members in _REF_ID_GLOBALS
        ), "a global name is declared twice under different accessors"

        # Deriving the names makes them self-maintaining but costs the one
        # thing the hand-written list DID have: a derived set shrinks along
        # with the module, so on its own it cannot notice a global that was
        # DELETED.  Two pins restore that, and neither is hand-maintained:
        #
        #   * the total, which changes only on a deliberate edit here;
        #   * per-enum coverage for the recurrence patterns, the one group a
        #     step has actually narrowed (plan step R2e-3 removed ``ONCE``
        #     from both the enum and the table).  Jinja is not configured
        #     with ``StrictUndefined``, so a dropped ``REC_*`` would make
        #     ``rr.pattern_id == REC_ANNUAL`` in ``_recurrence_macros.html``
        #     evaluate silently False and a yearly bill fall through to the
        #     name-string fallback.
        assert len(registered_keys) == 48, (
            "the ID-derived globals changed count -- update this number "
            "deliberately, and check the template that reads the new or "
            "removed constant"
        )
        assert {
            f"REC_{member.name}" for member in RecurrencePatternEnum
        } <= registered_keys, "a recurrence pattern has no REC_* global"

        for key in registered_keys:
            assert key in first, f"first pass missing {key}"
            assert first[key] == second[key], (
                f"value drifted across idempotent calls for {key}"
            )
        # Each is a resolved ``ref`` row id, not an Undefined or a name string.
        for key in registered_keys:
            assert isinstance(first[key], int), (
                f"{key} is {first[key]!r}, not a ref-row id"
            )


def test_goal_form_renders_with_fixed_mode_id_constant(auth_client, app):
    """GET /savings/goals/new embeds the GOAL_MODE_FIXED id in the form.

    ``app/templates/savings/goal_form.html`` consumes the
    ``GOAL_MODE_FIXED`` global at two sites: the
    ``data-fixed-mode-id="..."`` attribute on the mode selector and
    a ``selected``-by-default branch.  Before Commit 6 the
    conftest's reseat helper omitted this constant; the route
    response would have rendered an empty ``data-fixed-mode-id=""``
    attribute (Jinja Undefined coerces to empty string in
    expression context with the default environment) and the
    JavaScript that gates the income-relative fields on the id
    comparison would have silently broken.
    """
    with app.app_context():
        expected_fixed_id = ref_cache.goal_mode_id(GoalModeEnum.FIXED)

    resp = auth_client.get("/savings/goals/new")
    assert resp.status_code == 200
    html = resp.data.decode()
    # The id must be embedded as an integer literal in the attribute.
    assert f'data-fixed-mode-id="{expected_fixed_id}"' in html
