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

from flask import Response

from app import ref_cache
from app.enums import RecurrencePatternEnum, RecurrenceUnitEnum
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
from app.services.recurrence import recurrence_spec, resolve


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
            data = {
                "recurrence_pattern": None,
                "interval_n": 1,
                "offset_periods": 0,
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
                "recurrence_pattern": None,
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
                "recurrence_pattern": every_n_id,
                "interval_n": 4,
                "offset_periods": 0,
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
            every_n_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_N_PERIODS,
            )
            data = {
                "recurrence_pattern": every_n_id,
                "interval_n": 4,
                "offset_periods": 0,
            }
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
            monthly_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.MONTHLY,
            )
            foreign_period = seed_second_periods[0]
            data = {
                "recurrence_pattern": monthly_id,
                "interval_n": 1,
                "offset_periods": 0,
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
                "recurrence_pattern": monthly_id,
                "interval_n": 1,
                "offset_periods": 0,
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
            every_period_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_PERIOD,
            )
            data = {
                "recurrence_pattern": every_period_id,
                "interval_n": 1,
                "offset_periods": 0,
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


class TestUpdateRecurrenceNoAutoOffset:
    """Pin the no-auto-offset-on-update invariant (quality-pass B7).

    ``build_recurrence_rule_from_form`` auto-derives ``offset_periods``
    from the start period for ``EVERY_N_PERIODS`` (C2-3 above:
    ``period_index % interval_n``).  The update path deliberately does
    NOT: the edit form never re-collects ``start_period_id`` (it is fixed
    at creation), so the submitted ``offset_periods`` is taken verbatim.
    The cleanup (8e01099) extracted ``update_recurrence_rule_from_form``
    and the ``resolve_recurrence_rule_for_update`` dispatcher but left
    this asymmetry unpinned; these tests assert the submitted offset
    survives unchanged on both the direct-update and dispatcher paths, so
    a future edit that copies the create-side auto-offset into the update
    side surfaces here.
    """

    def test_update_uses_submitted_offset_verbatim_for_every_n(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """EVERY_N_PERIODS update keeps the submitted offset, not derived.

        A create with this pattern + a start period would overwrite the
        submitted ``offset_periods`` with ``period_index % interval_n``
        (C2-3).  The update path has no start period to derive from, so
        the submitted ``3`` must land on the rule verbatim.  The rule's
        pre-update ``offset_periods`` of 99 also proves the field was
        actually written (not left stale).
        """
        with app.test_request_context():
            every_n_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_N_PERIODS,
            )
            every_period_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_PERIOD,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_period_id,
                interval_n=1,
                offset_periods=99,
            )
            data = {
                "recurrence_pattern": every_n_id,
                "interval_n": 4,
                "offset_periods": 3,
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
            # Verbatim from the payload -- NOT auto-derived (3, not 3 % 4
            # or any period-index computation).
            assert rule.offset_periods == 3
            assert rule.interval_n == 4
            assert rule.pattern_id == every_n_id
            # All recurrence keys popped so the caller's setattr loop
            # never sees a stray kwarg.
            assert data == {}

    def test_resolve_existing_rule_preserves_submitted_offset(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """Dispatcher routes an existing rule to the no-auto-offset updater.

        ``resolve_recurrence_rule_for_update`` takes the in-place update
        branch when the template already owns a rule and a pattern is
        submitted.  Pins that the EVERY_N_PERIODS offset still arrives
        verbatim (5) through the dispatcher -- the real path the
        ``update_template`` / ``update_transfer_template`` routes take.
        """
        with app.test_request_context():
            every_n_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_N_PERIODS,
            )
            every_period_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.EVERY_PERIOD,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=every_period_id,
                interval_n=1,
                offset_periods=0,
            )
            template = SimpleNamespace(
                recurrence_rule=rule,
                user_id=seed_user["user"].id,
                recurrence_rule_id=None,
            )
            data = {
                "recurrence_pattern": every_n_id,
                "interval_n": 7,
                "offset_periods": 5,
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
            assert rule.offset_periods == 5
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
        ``2 % 3 == 2``.  The edit form submits no offset input at all, so the
        payload carries the schema default 0; before R2c-1 that 0 landed on
        the rule and every future occurrence moved a pay period earlier.
        """
        every_n_id = ref_cache.recurrence_pattern_id(
            RecurrencePatternEnum.EVERY_N_PERIODS,
        )
        with app.test_request_context():
            rule = build_recurrence_rule_from_form(
                {
                    "recurrence_pattern": every_n_id,
                    "interval_n": 3,
                    "offset_periods": 0,
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
                    "recurrence_pattern": every_n_id,
                    "interval_n": 3,
                    "offset_periods": 0,
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


class TestTheFormsIntervalCannotChangeACalendarCadence:
    """A hidden input cannot make a Quarterly bill recur monthly.

    **This is a regression guard for a defect an adversarial review caught
    before it shipped** (plan step R2b).  The edit form's ``interval_n`` input
    is hidden with ``d-none`` for every pattern but ``Every N Periods`` -- and
    a hidden input still SUBMITS, rendering the default of 1.

    While plan step R2b gave ``interval_n`` a SECOND meaning (3 on a Quarterly
    rule, 6 on a Semi-Annual one), that submitted 1 reset the cadence on any
    edit at all, including a rename: ``(interval_n=1, unit=month)`` IS a
    monthly rule, so a quarterly bill would project three times its real cost
    and a semi-annual one six times, with nothing left in the row to detect
    the loss by.

    Plan step R2d removed the second meaning rather than guarding it.  The
    column carries only "repeat every N pay PERIODS" again, read by
    the PERIOD-unit occurrence walk; the interval of a
    MONTH- or YEAR-unit recurrence is derived from the PATTERN and stored
    nowhere.  So the assertions below are about the resolved cadence, not the
    column: whatever the form submits, a Quarterly rule recurs every 3 months.
    """

    def _edit(self, app, seed_user, pattern, stored_interval, submitted):
        """Run one update through the helper and return the resulting rule.

        Args:
            app: The Flask app, for a request context.
            seed_user: The seeded user fixture.
            pattern: The pattern the rule carries and the form submits.
            stored_interval: The rule's ``interval_n`` before the edit.
            submitted: The ``interval_n`` the form posts.

        Returns:
            The edited :class:`RecurrenceRule`.
        """
        with app.test_request_context():
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=ref_cache.recurrence_pattern_id(pattern),
                interval_n=stored_interval,
                offset_periods=0,
                day_of_month=21,
            )
            data = {
                "recurrence_pattern": ref_cache.recurrence_pattern_id(
                    pattern,
                ),
                "interval_n": submitted,
                "offset_periods": 0,
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

    def test_a_quarterly_edit_still_recurs_every_three_months(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """Neither a stored 4 nor a submitted 99 can reach the cadence.

        Deliberately hostile values on BOTH sides rather than the form's
        actual default of 1: a 1 -> 1 edit exercises no mismatch at all, so
        the assertion would hold even if the column WERE the cadence.  A
        stored 4 (left by a rule that used to be every-4-paychecks) and a
        submitted 99 are each wrong in a way that would be visible.
        """
        rule = self._edit(
            app, seed_user, RecurrencePatternEnum.QUARTERLY,
            stored_interval=4, submitted=99,
        )

        assert rule.interval_n == 1, (
            "a calendar cadence carries its interval in the pattern's NAME, so "
            "the column must hold the encoder's 1 -- writing the submitted 99 "
            "there would put a value in a column spelled 'every N pay PERIODS' "
            "that nothing can tell from an authored one (plan step R7b)"
        )
        resolved = resolve(
            recurrence_spec(rule), calendar_for(seed_user["user"].id),
        )
        assert resolved.interval_n == 3, (
            "a form input reached a Quarterly rule's cadence; that bill would "
            "generate every 99 months or MONTHLY -- 3x the spend or none"
        )
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_a_semi_annual_edit_still_recurs_every_six_months(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """The same hostile pair against the six-month cadence."""
        rule = self._edit(
            app, seed_user, RecurrencePatternEnum.SEMI_ANNUAL,
            stored_interval=4, submitted=99,
        )

        resolved = resolve(
            recurrence_spec(rule), calendar_for(seed_user["user"].id),
        )
        assert resolved.interval_n == 6, "6x the spend if this regresses"
        assert resolved.unit is RecurrenceUnitEnum.MONTH

    def test_every_n_periods_still_takes_the_submitted_value(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """The pattern that OWNS the field is unaffected by the guard.

        The neighbouring case, and the one a too-broad fix would break: for
        EVERY_N_PERIODS the input is visible, labelled, and the user's choice,
        so the submitted 5 must land on the rule.
        """
        rule = self._edit(
            app, seed_user, RecurrencePatternEnum.EVERY_N_PERIODS,
            stored_interval=2, submitted=5,
        )
        assert rule.interval_n == 5, (
            "the guard swallowed the user's own choice for the one pattern "
            "whose form field is visible"
        )
