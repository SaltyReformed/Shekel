"""
Unit tests for ``app.routes._recurrence_form_helpers`` (F-24, F-26).

Pins helper-internal contracts so future edits to any of the four
helpers surface as unit-test failures rather than as integration
drift in the templates / transfers CRUD route suites.  Test IDs
C2-1 through C2-6 map to the F-24 commit's E section; C3-1 and
C3-2 map to the F-26 commit's E section.  Both commit specs live
in ``remediation_follow_up_F24_F25_F26_plan.md`` Section 7.

The tests use real templates / transfers blueprint endpoints
(``templates.new_template``, ``templates.edit_template``,
``transfers.list_transfer_templates``) for the helper's redirect
target rather than fabricating a test-only endpoint, because the
session-scoped ``app`` fixture is frozen by the time these tests
run (Flask refuses ``app.add_url_rule`` once a request has been
handled).
"""
import logging
from types import SimpleNamespace

import pytest
from flask import Response

from app import ref_cache
from app.enums import (
    PeriodPlacementEnum,
    RecurrencePatternEnum,
    RecurrenceUnitEnum,
)
from app.extensions import db
from app.models.recurrence_rule import RecurrenceRule
from app.routes._commit_helpers import (
    StaleConflictContext,
    handle_stale_conflict,
)
from app.routes._recurrence_form_helpers import (
    STALE_ACTION_MESSAGE,
    STALE_EDITING_MESSAGE,
    RecurrenceFormContext,
    build_recurrence_rule_from_form,
    handle_stale_form_conflict,
    resolve_recurrence_rule_for_update,
    update_recurrence_rule_from_form,
)
from app.routes._redirect_target import RedirectTarget
from app.services.pay_calendar import calendar_for
from app.services.recurrence import (
    RecurrenceResolutionError,
    recurrence_spec,
    resolve,
)
from tests._test_helpers import validated_cadence


class TestBuildRecurrenceRuleFromForm:
    """Helper :func:`build_recurrence_rule_from_form` contract tests."""

    def test_no_pattern_returns_none_and_pops_all_keys(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """C2-1 (template variant): no pattern -> None, all keys popped.

        ``include_due_day_of_month=True`` -- the helper should also
        pop ``due_day_of_month`` so the caller's
        ``TransactionTemplate`` constructor does not receive it as a
        stray kwarg.
        """
        with app.test_request_context():
            # "Does not repeat" is a submitted-empty UNIT, and the placement
            # arrives beside it as an explicit ``None`` -- both fields are
            # ``allow_none``, so ``_normalize_empty_inputs`` keeps the key.
            data = {
                "recurrence_unit": None,
                "recurrence_placement": None,
                "interval_n": 1,
                "day_of_month": 15,
                "due_day_of_month": 5,
                "month_of_year": 3,
                "end_date": None,
                "name": "Should survive",  # non-recurrence key
            }
            result = build_recurrence_rule_from_form(
                data,
                user_id=seed_user["user"].id,
                start_period_id=None,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget("templates.new_template"),
                    include_due_day_of_month=True,
                ),
            )
            assert result is None
            assert data == {"name": "Should survive"}

    def test_no_pattern_transfer_variant_leaves_due_day_of_month_untouched(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """C2-5 (negative): include_due_day_of_month=False keeps the key.

        Transfer-template schemas do not expose ``due_day_of_month``;
        the helper must not probe the key when the caller signals it
        is not a transaction-template payload.
        """
        with app.test_request_context():
            data = {
                "recurrence_unit": None,
                "interval_n": 1,
                "day_of_month": 15,
                "due_day_of_month": 5,  # would never appear in real
                                        # transfer payload
            }
            result = build_recurrence_rule_from_form(
                data,
                user_id=seed_user["user"].id,
                start_period_id=None,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget("transfers.new_transfer_template"),
                    include_due_day_of_month=False,
                ),
            )
            assert result is None
            # ``due_day_of_month`` survives because the helper did not
            # probe for it -- the caller's TransferTemplate
            # constructor would never see this key in production
            # because the schema strips it via EXCLUDE.
            assert data == {"due_day_of_month": 5}

    def test_every_n_periods_auto_offset(
        self, app, auth_client, seed_user, seed_periods_today,
    ):
        """C2-3: EVERY_N_PERIODS + valid start_period -> offset derived.

        Hand-arithmetic: with ``period_index = 1`` (the second
        seeded period) and ``interval_n = 4``,
        ``offset_periods = 1 % 4 = 1``.
        """
        with app.test_request_context():
            every_n_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_N_PERIODS,
            )
            # Find a period with a known period_index for the
            # arithmetic.  seed_periods_today seeds an indexed range
            # around today; pick one with a non-zero index.
            chosen = next(
                (p for p in seed_periods_today if p.period_index == 1),
                None,
            )
            assert chosen is not None, "fixture missing period_index=1"

            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD, interval_n=4,
                ),
                "day_of_month": None,
                "month_of_year": None,
                "due_day_of_month": None,
                "end_date": None,
            }
            result = build_recurrence_rule_from_form(
                data,
                user_id=seed_user["user"].id,
                start_period_id=chosen.id,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget("templates.new_template"),
                    include_due_day_of_month=True,
                ),
            )
            assert isinstance(result, RecurrenceRule)
            # 1 % 4 = 1
            assert result.offset_periods == 1
            assert result.interval_n == 4
            assert result.pattern_id == every_n_id
            # Every recurrence key should have been popped from data.
            assert data == {}
            # Roll back so the test does not pollute the session.
            db.session.rollback()

    def test_every_n_periods_invalid_start_period_returns_redirect(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """C2-4: EVERY_N_PERIODS + bad start_period -> Response + flash.

        Uses the ``templates.edit_template`` endpoint so the
        redirect_endpoint_kwargs={"template_id": 42} branch is
        exercised; the response Location should contain ``/42``.
        """
        with app.test_request_context():
            data = validated_cadence(
                unit=RecurrenceUnitEnum.PERIOD, interval_n=4,
            )
            result = build_recurrence_rule_from_form(
                data,
                user_id=seed_user["user"].id,
                start_period_id=99_999_999,  # nonexistent
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget(
                        "templates.edit_template",
                        {"template_id": 42},
                    ),
                    include_due_day_of_month=True,
                ),
            )
            assert isinstance(result, Response)
            assert result.status_code == 302
            assert "/templates/42" in result.headers["Location"]
            db.session.rollback()

    def test_non_every_n_cross_user_start_period_rejected(
        self, app, seed_user, seed_second_user, seed_second_periods,  # pylint: disable=unused-argument
    ):
        """C2-7: a cross-user start_period is rejected for a recurring
        (non-EVERY_N) pattern, not only EVERY_N_PERIODS.

        deep-quality-hunt #21: the start_period ownership probe used to
        run ONLY inside the EVERY_N_PERIODS branch, so a MONTHLY (or any
        other recurring) pattern persisted a foreign ``start_period_id``
        unchecked, and ``recurrence_engine`` then read that victim
        period's ``start_date`` as the generation boundary.  The probe
        now runs for every pattern: a start_period owned by another user
        yields a redirect Response + flash, exactly like the EVERY_N
        case (C2-4), and nothing is persisted.
        """
        with app.test_request_context():
            foreign_period = seed_second_periods[0]
            data = {
                **validated_cadence(unit=RecurrenceUnitEnum.MONTH),
                "day_of_month": 15,
                "month_of_year": None,
                "due_day_of_month": None,
                "end_date": None,
            }
            result = build_recurrence_rule_from_form(
                data,
                user_id=seed_user["user"].id,
                start_period_id=foreign_period.id,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget("templates.new_template"),
                    include_due_day_of_month=True,
                ),
            )
            assert isinstance(result, Response)
            assert result.status_code == 302
            assert "/templates/new" in result.headers["Location"]
            # No rule was persisted referencing the foreign period.
            assert (
                db.session.query(RecurrenceRule)
                .filter_by(start_period_id=foreign_period.id)
                .first()
            ) is None
            db.session.rollback()

    def test_non_every_n_own_start_period_persisted(
        self, app, seed_user, seed_periods_today,
    ):
        """C2-8: a MONTHLY pattern with the owner's own start_period is
        accepted and persists it, without auto-deriving offset_periods.

        Confirms the hoisted probe (C2-7) does not over-reject the
        legitimate same-user case, and that ``offset_periods`` stays 0 --
        the ``period_index`` auto-offset is EVERY_N_PERIODS-only even
        when the chosen period has a non-zero index.
        """
        with app.test_request_context():
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )
            own_period = next(
                (p for p in seed_periods_today if p.period_index == 1),
                None,
            )
            assert own_period is not None, "fixture missing period_index=1"
            data = {
                **validated_cadence(unit=RecurrenceUnitEnum.MONTH),
                "day_of_month": 15,
                "month_of_year": None,
                "due_day_of_month": None,
                "end_date": None,
            }
            result = build_recurrence_rule_from_form(
                data,
                user_id=seed_user["user"].id,
                start_period_id=own_period.id,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget("templates.new_template"),
                    include_due_day_of_month=True,
                ),
            )
            assert isinstance(result, RecurrenceRule)
            assert result.start_period_id == own_period.id
            # offset is NOT auto-derived for non-EVERY_N patterns.
            assert result.offset_periods == 0
            assert result.pattern_id == monthly_id
            db.session.rollback()

    def test_include_due_day_of_month_true_consumes_key(
        self, app, auth_client, seed_user, seed_periods_today,  # pylint: disable=unused-argument
    ):
        """C2-5 (positive): include=True puts due_day_of_month on the rule.

        Uses EVERY_PERIOD so the every-N phase derivation is skipped and the
        helper exercises the straight RecurrenceRule construction path.  Named
        the ``Once`` pattern for the same reason until plan step R2e-3 retired
        it; EVERY_PERIOD is the surviving member of the same anchor family.
        """
        with app.test_request_context():
            data = {
                **validated_cadence(unit=RecurrenceUnitEnum.PERIOD),
                "day_of_month": None,
                "month_of_year": None,
                "due_day_of_month": 15,
            }
            result = build_recurrence_rule_from_form(
                data,
                user_id=seed_user["user"].id,
                start_period_id=None,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget("templates.new_template"),
                    include_due_day_of_month=True,
                ),
            )
            assert isinstance(result, RecurrenceRule)
            assert result.due_day_of_month == 15
            assert "due_day_of_month" not in data
            db.session.rollback()


class TestAnEditCannotRePhaseAPhaselessRule:
    """Defect **D8**, closed at plan step R7b-2, at the surface it survived on.

    **The subject of this class changed with the step, and the change is the
    point.**  It used to pin that the update path took the SUBMITTED
    ``offset_periods`` verbatim, which was the honest reading while the schemas
    declared the field: an edit form never re-collects ``start_period_id``, so
    for a rule naming no start period the payload was the only statement of
    phase there was.

    It was also the last hole in D1.  No template under ``app/templates/`` ever
    rendered an offset input, so "the submitted value" was ALWAYS the schema's
    default of 0 -- an amount-only edit of an every-N-paychecks rule with no
    start period silently re-phased every future occurrence to 0.  Plan step
    R7b-2 deleted the schema field rather than guarding it, so there is no
    submitted value left to take verbatim and the rule's own stored phase rides
    through :func:`~app.services.recurrence.recurrence_spec_with_cadence`
    untouched.  These tests pin THAT, on both the direct-update and dispatcher
    paths.

    The sibling class below covers the 45-of-50 case: a rule that DOES name a
    start period, where the phase is re-derived from it on every write.
    """

    def test_the_stored_phase_survives_a_cadence_edit(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """A rule with no start period keeps its phase across an edit.

        The rule is phased at 3 within a four-paycheck cycle and names no
        start period, so nothing can re-derive that 3 -- it is the only
        statement of the phase the row has.  The edit changes the cadence and
        must leave it alone; writing the old schema default here moved every
        future occurrence three paychecks earlier.
        """
        with app.test_request_context():
            every_n_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_N_PERIODS,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_n_id,
                interval_n=4,
                offset_periods=3,
            )
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD, interval_n=6,
                ),
                "day_of_month": None,
                "month_of_year": None,
                "due_day_of_month": None,
            }
            update_recurrence_rule_from_form(
                rule,
                data,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                    include_due_day_of_month=True,
                ),
            )
            assert rule.offset_periods == 3, (
                "the edit re-phased a rule whose phase nothing else states"
            )
            assert rule.interval_n == 6
            assert rule.pattern_id == every_n_id
            # All recurrence keys popped so the caller's setattr loop
            # never sees a stray kwarg.
            assert data == {}

    def test_an_offset_in_the_payload_is_not_read_at_all(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """A hand-crafted ``offset_periods`` cannot reach the rule.

        The schemas no longer DECLARE the field, so marshmallow's ``EXCLUDE``
        drops it and no such key can arrive through a route -- this drives the
        helper directly to prove the second half: even handed one, the helper
        does not read it.  The key is deliberately left unpopped, which is what
        says it is not a recurrence key any more rather than one this helper
        happens to ignore.
        """
        with app.test_request_context():
            every_n_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_N_PERIODS,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_n_id,
                interval_n=4,
                offset_periods=3,
            )
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD, interval_n=4,
                ),
                "offset_periods": 0,
                "day_of_month": None,
                "month_of_year": None,
                "due_day_of_month": None,
            }
            update_recurrence_rule_from_form(
                rule,
                data,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                    include_due_day_of_month=True,
                ),
            )
            assert rule.offset_periods == 3
            assert data == {"offset_periods": 0}

    def test_the_stored_phase_survives_through_the_dispatcher(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """The same, through the door the update routes actually take.

        ``resolve_recurrence_rule_for_update`` takes the in-place update
        branch when the template already owns a rule and a cadence is
        submitted, which is the real path
        ``update_template`` / ``update_transfer_template`` follow.
        """
        with app.test_request_context():
            every_n_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_N_PERIODS,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_n_id,
                interval_n=4,
                offset_periods=2,
            )
            template = SimpleNamespace(
                recurrence_rule=rule,
                user_id=seed_user["user"].id,
                recurrence_rule_id=None,
            )
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.PERIOD, interval_n=7,
                ),
                "day_of_month": None,
                "month_of_year": None,
                "due_day_of_month": None,
            }
            result = resolve_recurrence_rule_for_update(
                template,
                data,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                    include_due_day_of_month=True,
                ),
            )
            assert result is None
            assert rule.offset_periods == 2
            assert rule.interval_n == 7
            assert rule.pattern_id == every_n_id


class TestUpdateKeepsTheStartPeriodsPhase:
    """Defect **D1**, closed at plan step R2c-1, at its own surface.

    The sibling class above pins the update path's treatment of a rule that
    names NO start period: the submitted offset is all the phase information
    there is, so it arrives verbatim.  This class covers the case D1 was
    MEASURED on and that 45 of the 50 live rules are in -- a rule that DOES
    name a start period.  There the phase is a derived fact, and the pre-seam
    update path overwrote it with the payload's default, shifting every future
    occurrence by one pay period on an edit that changed only the amount.
    """

    def test_an_edit_does_not_re_phase_a_rule_with_a_start_period(
        self, app, auth_client, seed_user, db, seed_periods,  # pylint: disable=unused-argument
    ):
        """The phase stays ``period_index % interval_n`` across an edit.

        Start period index 2 with an interval of 3 phases the rule at
        ``2 % 3 == 2``.  Before R2c-1 the edit wrote the payload's offset
        default of 0 onto the rule and every future occurrence moved a pay
        period earlier; since plan step R7b-2 there is no such key in the
        payload at all (defect **D8**), and the phase is re-derived from the
        start period the rule still names.
        """
        with app.test_request_context():
            rule = build_recurrence_rule_from_form(
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.PERIOD, interval_n=3,
                    ),
                    "day_of_month": None,
                    "month_of_year": None,
                    "due_day_of_month": None,
                },
                user_id=seed_user["user"].id,
                start_period_id=seed_periods[2].id,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                    include_due_day_of_month=True,
                ),
            )
            assert rule.offset_periods == 2

            update_recurrence_rule_from_form(
                rule,
                {
                    **validated_cadence(
                        unit=RecurrenceUnitEnum.PERIOD, interval_n=3,
                    ),
                    "day_of_month": None,
                    "month_of_year": None,
                    "due_day_of_month": None,
                },
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                    include_due_day_of_month=True,
                ),
            )

            assert rule.offset_periods == 2
            assert rule.start_period_id == seed_periods[2].id


class TestHandleStaleConflict:
    """Helper :func:`handle_stale_conflict` contract tests."""

    def test_logs_flashes_and_redirects(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """C2-6: rollback + log + flash + 302 redirect.

        Pins the canonical handler shape so a regression that drops
        any of the four side effects (rollback, log, flash, redirect)
        surfaces here.
        """
        with app.test_request_context():
            test_logger = logging.getLogger("test_handle_stale_conflict")
            # The helper expects to be invoked from inside an
            # ``except`` block where a commit just raised.  No
            # commit happened here, so rollback is a no-op -- the
            # assertion focuses on the redirect contract.
            response = handle_stale_conflict(StaleConflictContext(
                logger=test_logger,
                log_label="test_route",
                log_id=123,
                flash_message=STALE_EDITING_MESSAGE.format(
                    noun="test object",
                ),
                redirect=RedirectTarget(
                    "templates.edit_template",
                    {"template_id": 123},
                ),
            ))
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/123" in response.headers["Location"]

    def test_stale_message_templates_render_expected_strings(self):
        """C2-6 (variant): the two flash templates render expected copy.

        Pins the user-facing wording so a copy-edit that breaks the
        ``{noun}`` substitution or rewords the canonical line
        surfaces as a unit-test failure.
        """
        editing = STALE_EDITING_MESSAGE.format(noun="recurring transaction")
        assert "while you were editing" in editing
        assert "recurring transaction" in editing
        assert "Please reload and try again." in editing

        action = STALE_ACTION_MESSAGE.format(noun="recurring transfer")
        assert "while you were editing" not in action
        assert "recurring transfer" in action
        assert "Please reload and try again." in action


class TestHandleStaleFormConflict:
    """F-26 helper :func:`handle_stale_form_conflict` contract tests.

    Pre-flush optimistic-locking mirror of
    :func:`handle_stale_conflict`; logs both submitted and current
    version counters so post-mortem analysis can reconstruct the
    race.  Does NOT roll back (no DB write attempted at the call
    site).
    """

    def test_logs_both_counters_flashes_and_redirects(
        self, app, auth_client, seed_user, caplog,  # pylint: disable=unused-argument
    ):
        """C3-1: log at INFO with submitted + current; flash; 302.

        Pins the canonical handler shape so a regression that drops
        either counter from the log (or rewords the canonical line)
        surfaces here.
        """
        with app.test_request_context():
            test_logger = logging.getLogger(
                "test_handle_stale_form_conflict",
            )
            with caplog.at_level(
                logging.INFO,
                logger="test_handle_stale_form_conflict",
            ):
                response = handle_stale_form_conflict(
                    StaleConflictContext(
                        logger=test_logger,
                        log_label="update_template",
                        log_id=42,
                        flash_message=STALE_EDITING_MESSAGE.format(
                            noun="recurring transaction",
                        ),
                        redirect=RedirectTarget(
                            "templates.edit_template",
                            {"template_id": 42},
                        ),
                    ),
                    submitted=7,
                    current=9,
                )
            assert isinstance(response, Response)
            assert response.status_code == 302
            assert "/templates/42" in response.headers["Location"]
            # The log record must carry BOTH the submitted and the
            # current counters -- the post-mortem-reconstruction
            # rationale fails if either is missing.
            log_msg = caplog.records[-1].getMessage()
            assert "update_template" in log_msg
            assert "id=42" in log_msg
            assert "submitted=7" in log_msg
            assert "current=9" in log_msg


class TestTheColumnIsNotTheCadence:
    """A calendar cadence's interval lives in the pattern NAME, not the column.

    **This class began as a regression guard for a defect an adversarial
    review caught before it shipped** (plan step R2b): the edit form's
    ``interval_n`` input was hidden with ``d-none`` for every pattern but
    ``Every N Periods``, a hidden input still SUBMITS, and plan step R2b had
    given the column a SECOND meaning (3 on a Quarterly rule, 6 on a
    Semi-Annual one).  The submitted 1 therefore reset the cadence on any edit
    at all, including a rename -- a quarterly bill would project three times
    its real cost, with nothing left in the row to detect the loss by.

    Plan step R2d removed the second meaning; plan step R7b-2 removed the
    MISMATCH that made it dangerous.  The form no longer submits a pattern
    NAME beside an interval that can contradict it -- it states
    ``(interval_n, unit, placement)`` and ``encode_cadence`` chooses the
    pattern -- so "Quarterly with an interval of 99" is not a payload that can
    be spelled.  What is left to pin is the ENCODING, and it is where the
    money is: an authored 3 months must come back out as 3 months while the
    column holds the encoder's 1, because that column is spelled "every N pay
    PERIODS" and the occurrence walk reads it as one.
    """

    def _edit(
        self, app, seed_user, *, unit, interval, stored_pattern,
        stored_interval,
    ):
        """Re-author one stored rule through the helper and return it.

        Args:
            app: The Flask app, for a request context.
            seed_user: The seeded user fixture.
            unit: The cadence unit the form states.
            interval: The interval the form states.
            stored_pattern: The pattern the rule carries BEFORE the edit.
            stored_interval: The rule's ``interval_n`` before the edit.

        Returns:
            The edited :class:`RecurrenceRule`.
        """
        with app.test_request_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=ref_cache.recurrence_pattern_id(stored_pattern),
                interval_n=stored_interval,
                offset_periods=0,
                day_of_month=21,
            )
            data = {
                **validated_cadence(unit=unit, interval_n=interval),
                "day_of_month": 21,
                "month_of_year": 4,
                "due_day_of_month": None,
            }
            update_recurrence_rule_from_form(
                rule, data,
                ctx=RecurrenceFormContext(
                    end_date_value=None,
                    redirect=RedirectTarget(
                        "templates.edit_template", {"template_id": 1},
                    ),
                    include_due_day_of_month=True,
                ),
            )
            assert "interval_n" not in data, (
                "interval_n must be popped whether or not it is written, so "
                "the caller's setattr loop never sees a stray kwarg"
            )
            return rule

    def test_an_authored_three_months_stores_quarterly_with_a_column_of_one(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """3 months in, Quarterly stored, 3 months back out, column at 1.

        The stored 4 is deliberately hostile -- it is what a rule that used to
        be every-4-paychecks leaves behind -- so the assertion cannot pass
        merely because the column was never touched.
        """
        rule = self._edit(
            app, seed_user,
            unit=RecurrenceUnitEnum.MONTH, interval=3,
            stored_pattern=RecurrencePatternEnum.EVERY_N_PERIODS,
            stored_interval=4,
        )

        assert rule.pattern_id == ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.QUARTERLY,
        )
        assert rule.interval_n == 1, (
            "a calendar cadence carries its interval in the pattern's NAME, so "
            "the column must hold the encoder's 1 -- leaving the stored 4 "
            "there would put a value in a column spelled 'every N pay PERIODS' "
            "that nothing can tell from an authored one (plan step R7b)"
        )
        resolved = resolve(
            recurrence_spec(rule), calendar_for(seed_user["user"].id),
        )
        assert resolved.interval_n == 3, (
            "the authored interval did not survive the round trip; that bill "
            "would generate every month -- 3x the spend"
        )
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_an_authored_six_months_stores_semi_annual(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The same round trip against the six-month cadence."""
        rule = self._edit(
            app, seed_user,
            unit=RecurrenceUnitEnum.MONTH, interval=6,
            stored_pattern=RecurrencePatternEnum.EVERY_N_PERIODS,
            stored_interval=4,
        )

        assert rule.interval_n == 1
        resolved = resolve(
            recurrence_spec(rule), calendar_for(seed_user["user"].id),
        )
        assert resolved.interval_n == 6, "6x the spend if this regresses"
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_a_paycheck_cadence_keeps_its_interval_in_the_column(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """The one cadence whose interval IS the column, and it is unaffected.

        The neighbouring case a too-broad rule would break: ``Every N
        Periods`` is the single pattern that names no interval, so the
        authored 5 must land on the column verbatim.
        """
        rule = self._edit(
            app, seed_user,
            unit=RecurrenceUnitEnum.PERIOD, interval=5,
            stored_pattern=RecurrencePatternEnum.EVERY_N_PERIODS,
            stored_interval=2,
        )
        assert rule.interval_n == 5, (
            "the encoding swallowed the user's own choice for the one pattern "
            "whose interval lives in a column"
        )

    def test_a_month_interval_the_closed_set_cannot_name_is_refused(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """An unstorable cadence raises at the door rather than being coerced.

        ``(99, MONTH)`` is a well-defined cadence the resolver walks correctly
        and the closed pattern set has no NAME for, so until plan step R7c it
        cannot be written.  **The disposition matters more than the refusal**:
        coercing it to the nearest storable month cadence would silently
        re-price a bill.  Nothing offers this combination -- the picker's
        options come from the same table -- and
        ``validate_authorable_cadence`` turns a hand-crafted POST into a field
        error before any of this runs; this is the last line.
        """
        with app.test_request_context():
            quarterly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.QUARTERLY,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=quarterly_id,
                interval_n=1,
                offset_periods=0,
                day_of_month=21,
            )
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.MONTH, interval_n=99,
                ),
                "day_of_month": 21,
                "month_of_year": 4,
                "due_day_of_month": None,
            }
            with pytest.raises(RecurrenceResolutionError) as excinfo:
                update_recurrence_rule_from_form(
                    rule, data,
                    ctx=RecurrenceFormContext(
                        end_date_value=None,
                        redirect=RedirectTarget(
                            "templates.edit_template", {"template_id": 1},
                        ),
                        include_due_day_of_month=True,
                    ),
                )
            assert "99" in str(excinfo.value), (
                "the refusal must name the offending interval"
            )
            assert rule.pattern_id == quarterly_id, (
                "the row was re-pointed before the refusal"
            )

    def test_the_placement_a_three_month_interval_cannot_take_is_refused(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """The PAIR dependency at the door: 3 months has no first-paycheck twin.

        ``MONTHLY_FIRST`` is ``(1, MONTH, PERIOD_STARTING_ON_OR_AFTER)`` and
        the closed set has no quarterly or semi-annual twin, so a placement
        list keyed on the UNIT alone would offer this.  It is the case the
        picker's whole-triple offer set exists to make unreachable, pinned
        here at the layer below it.
        """
        with app.test_request_context():
            quarterly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.QUARTERLY,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=quarterly_id,
                interval_n=1,
                offset_periods=0,
                day_of_month=21,
            )
            data = {
                **validated_cadence(
                    unit=RecurrenceUnitEnum.MONTH,
                    interval_n=3,
                    placement=PeriodPlacementEnum.PERIOD_STARTING_ON_OR_AFTER,
                ),
                "day_of_month": 21,
                "month_of_year": 4,
                "due_day_of_month": None,
            }
            with pytest.raises(RecurrenceResolutionError):
                update_recurrence_rule_from_form(
                    rule, data,
                    ctx=RecurrenceFormContext(
                        end_date_value=None,
                        redirect=RedirectTarget(
                            "templates.edit_template", {"template_id": 1},
                        ),
                        include_due_day_of_month=True,
                    ),
                )
            assert rule.pattern_id == quarterly_id
