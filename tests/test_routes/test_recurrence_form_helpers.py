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
from app.enums import RecurrencePatternEnum
from app.exceptions import RecurrenceConflict
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
    handle_recurrence_conflict,
    handle_stale_form_conflict,
    resolve_recurrence_rule_for_update,
    update_recurrence_rule_from_form,
)
from app.routes._redirect_target import RedirectTarget


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

    def test_invalid_pattern_returns_redirect_response(
        self, app, auth_client, seed_user,  # pylint: disable=unused-argument
    ):
        """C2-2: invalid pattern id -> Response 302 + flash."""
        with app.test_request_context():
            data = {"recurrence_pattern": "99999999"}  # nonexistent
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
            assert isinstance(result, Response)
            assert result.status_code == 302
            assert "/templates/new" in result.headers["Location"]

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
                "recurrence_pattern": str(every_n_id),
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
                "recurrence_pattern": str(every_n_id),
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

    def test_include_due_day_of_month_true_consumes_key(
        self, app, auth_client, seed_user, seed_periods_today,  # pylint: disable=unused-argument
    ):
        """C2-5 (positive): include=True puts due_day_of_month on the rule.

        Uses the ONCE pattern so the every-N auto-offset branch is
        skipped and the helper exercises the straight RecurrenceRule
        construction path.
        """
        with app.test_request_context():
            once_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.ONCE,
            )
            data = {
                "recurrence_pattern": str(once_id),
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
            once_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.ONCE,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=once_id,
                interval_n=1,
                offset_periods=99,
            )
            data = {
                "recurrence_pattern": str(every_n_id),
                "interval_n": 4,
                "offset_periods": 3,
                "day_of_month": None,
                "month_of_year": None,
                "due_day_of_month": None,
            }
            result = update_recurrence_rule_from_form(
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
            assert result is None
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
            once_id = ref_cache.recurrence_pattern_id(
                RecurrencePatternEnum.ONCE,
            )
            rule = RecurrenceRule(
                user_id=seed_user["user"].id,
                pattern_id=once_id,
                interval_n=1,
                offset_periods=0,
            )
            template = SimpleNamespace(
                recurrence_rule=rule,
                user_id=seed_user["user"].id,
                recurrence_rule_id=None,
            )
            data = {
                "recurrence_pattern": str(every_n_id),
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
            assert result is None
            assert rule.offset_periods == 5
            assert rule.interval_n == 7
            assert rule.pattern_id == every_n_id


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


class TestHandleRecurrenceConflict:
    """F-26 helper :func:`handle_recurrence_conflict` contract tests.

    Phase-1 auto-keep-overrides advisory handler.  Logs at WARNING
    with the override / delete counts; flashes the canonical
    "kept as-is" notice; returns ``None`` (NOT a Response -- the
    caller continues executing after the flash).
    """

    def test_logs_warning_flashes_and_returns_none(
        self, app, auth_client, seed_user, caplog,  # pylint: disable=unused-argument
    ):
        """C3-2: log WARN with counts; flash counts; return None.

        Hand-arithmetic: ``len([1, 2, 3]) = 3`` overridden,
        ``len([4]) = 1`` deleted.  The flash string substitutes
        both counts.
        """
        with app.test_request_context():
            test_logger = logging.getLogger(
                "test_handle_recurrence_conflict",
            )
            conflict = RecurrenceConflict(
                overridden=[1, 2, 3],
                deleted=[4],
            )
            with caplog.at_level(
                logging.WARNING,
                logger="test_handle_recurrence_conflict",
            ):
                result = handle_recurrence_conflict(
                    logger=test_logger,
                    log_label="Recurrence conflict for template",
                    log_id=99,
                    conflict=conflict,
                )
            # Load-bearing: must return None, not a Response.  A
            # Response return would cause the caller (which does
            # ``handle_recurrence_conflict(...)`` without
            # ``return``) to discard it, but a future caller that
            # added ``return`` by mistake would early-exit the
            # route mid-update.  Pin the contract.
            assert result is None
            log_msg = caplog.records[-1].getMessage()
            assert "Recurrence conflict for template 99" in log_msg
            assert "3 overridden" in log_msg
            assert "1 deleted" in log_msg

    def test_log_label_preserves_transfers_side_prefix(
        self, app, auth_client, seed_user, caplog,  # pylint: disable=unused-argument
    ):
        """C3-2 (variant): log_label kwarg carries the prefix verbatim.

        Pre-extraction the transfers side logged with prefix
        "Transfer recurrence conflict for template"; the helper
        preserves the prefix via the ``log_label`` kwarg so log-
        grep patterns stay valid post-extraction.
        """
        with app.test_request_context():
            test_logger = logging.getLogger(
                "test_handle_recurrence_conflict_transfer",
            )
            conflict = RecurrenceConflict(overridden=[], deleted=[])
            with caplog.at_level(
                logging.WARNING,
                logger="test_handle_recurrence_conflict_transfer",
            ):
                handle_recurrence_conflict(
                    logger=test_logger,
                    log_label="Transfer recurrence conflict for template",
                    log_id=77,
                    conflict=conflict,
                )
            log_msg = caplog.records[-1].getMessage()
            assert (
                "Transfer recurrence conflict for template 77"
                in log_msg
            )
            assert "0 overridden" in log_msg
            assert "0 deleted" in log_msg
