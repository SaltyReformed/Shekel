"""
Shekel Budget App -- C-30 Analytics Cross-User Ownership Tests

Route-level coverage for commit C-30 of the 2026-04-15 security
remediation plan: ``analytics.calendar_tab`` rejects a cross-user
or non-existent ``account_id`` with 404 (F-039), and
``analytics.variance_tab`` rejects a cross-user or non-existent
``period_id`` with 404 (F-098).

Threat model.  Both gaps shared the same shape: the route lifted a
foreign-key id straight off the query string and handed it to the
service layer, which itself either silently fell back to a user-
scoped default (calendar) or read victim metadata into the
response label and CSV filename (variance).  Neither service raised
a security exception, so the IDOR probe surfaced as a normal-
looking 200 instead of a 404, masking the boundary breach behind
plausible response bodies.

  * F-039 (calendar): ``calendar_service._resolve_account`` checks
    ownership but on failure silently falls through to the user's
    own default checking account -- the requester sees their own
    data with no error.  An attacker probing for valid victim
    account ids cannot distinguish "owned" from "not owned" but
    also gets no security signal.  The route-level 404 closes the
    silent-fallback gap and emits the standard
    ``access_denied_cross_user`` audit event.

  * F-098 (variance): the budget-variance txn filter joins
    ``account_id`` (user-owned) with ``pay_period_id`` and so
    returns no rows on a cross-user period_id, BUT
    ``_build_window_label`` and ``_variance_csv_filename`` both
    read ``PayPeriod.start_date`` without an ownership re-check.
    The victim's start_date leaks through the variance label
    visible in the HTML response and the CSV download filename.

The route-level guard delegates ownership to
:func:`app.utils.auth_helpers.get_or_404` (Pattern A in
``auth_helpers``) so the existing structured logging contract
(INFO ``resource_not_found`` for missing pk, WARNING
``access_denied_cross_user`` for cross-user pk) covers both the
analytics routes and every other route that uses the helper.

Test scope.  Each finding is exercised through HTML (HTMX) and
CSV paths because the C-30 plan's "G" gate (re-run the IDOR probe;
expect zero failures) covers both.  An additional
defense-in-depth class verifies that period_id is validated even
on window types that ignore it downstream -- the service contract
may shift in the future and the boundary check should not depend
on whether the value happens to be consumed.
"""

from datetime import date

import pytest

from app.extensions import db
from app.models.pay_period import PayPeriod
from tests._test_helpers import freeze_today


@pytest.fixture(autouse=True)
def _freeze_today_inside_seed_range(monkeypatch):
    """Freeze today to 2026-03-20 so seed_periods (Jan-May 2026) is current.

    The seeded pay-period range spans 2026-01-02 through roughly
    2026-05-08.  Freezing today inside that window keeps the
    variance defaults (current period lookup, year selector) on a
    real period regardless of the wall-clock date when the test
    runs.  Mirrors the autouse freeze in ``test_analytics.py`` so
    fixture behavior is consistent.
    """
    freeze_today(monkeypatch, date(2026, 3, 20))


# ── analytics.calendar_tab -- F-039 ──────────────────────────────────


class TestCalendarTabAccountIdOwnership:
    """``analytics.calendar_tab`` rejects a cross-user ``account_id``.

    The service-layer ``_resolve_account`` silently falls back to
    the requester's default checking account when ``account_id``
    fails the ownership check, which masks the IDOR probe behind a
    successful 200.  The route-boundary check elevates the response
    to 404 so the security boundary is observable to monitoring
    and to integration tests.
    """

    def test_own_account_id_html_succeeds(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A's HTMX calendar request with their own ``account_id`` returns 200.

        Establishes the success baseline so a regression that
        over-rejects (404 on every account_id) is visible.
        """
        with app.app_context():
            own_account_id = seed_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?account_id={own_account_id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200, (
                f"Owner's own account_id must return 200, got "
                f"{resp.status_code}"
            )

    def test_own_account_id_csv_succeeds(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A's CSV calendar download with their own ``account_id`` returns 200.

        The CSV path shares the same query-arg parsing as the HTML
        path; this confirms the validation does not block the
        legitimate CSV flow.
        """
        with app.app_context():
            own_account_id = seed_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?format=csv&view=month&year=2026"
                f"&month=1&account_id={own_account_id}",
            )
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_cross_user_account_id_html_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user,
    ):
        """A's HTMX calendar request with B's ``account_id`` returns 404.

        F-039 baseline: without the route-level guard this would
        return 200 with the requester's default-account data
        (silent service-layer fallback in
        ``calendar_service._resolve_account``).  The 404 follows
        the project security response rule.
        """
        with app.app_context():
            attacker_target = seed_second_user["account"].id
            assert attacker_target != seed_user["account"].id, (
                "fixture sanity: the two seeded users must own "
                "distinct accounts for the cross-user probe"
            )
            resp = auth_client.get(
                f"/analytics/calendar?account_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404, (
                "cross-user account_id must return 404 (security "
                "response rule: 404 for both 'not found' and "
                "'not yours'); got "
                f"{resp.status_code}.  This indicates the silent "
                "service-layer fallback re-emerged as the response."
            )

    def test_cross_user_account_id_csv_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user,
    ):
        """A's CSV calendar download with B's ``account_id`` returns 404.

        F-039 paired with the CSV bypass note in the plan: the
        CSV path runs before the HX-Request guard so a non-HTMX
        IDOR probe still triggers the silent fallback.  The 404
        must fire on this path too.
        """
        with app.app_context():
            attacker_target = seed_second_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?format=csv&view=month&year=2026"
                f"&month=1&account_id={attacker_target}",
            )
            assert resp.status_code == 404
            assert "text/csv" not in resp.headers.get("Content-Type", ""), (
                "404 response must not carry a CSV content-type "
                "header -- the body is the standard 404 page, not "
                "an empty CSV that would still trigger a download "
                "in the browser"
            )

    def test_cross_user_account_id_year_view_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user,
    ):
        """A's calendar year view with B's ``account_id`` returns 404.

        ``view=year`` calls ``calendar_service.get_year_overview``
        rather than ``get_month_detail``; both paths share the same
        ``_resolve_account`` fallback and the route-boundary check
        must cover both.
        """
        with app.app_context():
            attacker_target = seed_second_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?view=year&year=2026"
                f"&account_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_nonexistent_account_id_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A non-existent ``account_id`` returns 404 (treats not found same as not yours).

        ``9_999_999`` is a deliberately out-of-range integer that
        no user has ever owned, exercising the
        ``record is None`` branch of ``get_or_404``.  The same 404
        response keeps the client unable to distinguish "no such
        row" from "not yours" by status or body shape.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?account_id=9999999",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_no_account_id_param_uses_default(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A calendar request without ``account_id`` falls back to the user's default.

        Confirms the validation helper bypasses the check when the
        query arg is absent, so the legitimate "no filter --> default
        checking account" service-layer path is preserved.  A
        regression here would 404 every calendar load.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_malformed_account_id_uses_default(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A non-integer ``account_id`` is treated as absent.

        ``request.args.get("account_id", None, type=int)`` returns
        None on parse failure rather than raising; the validator
        treats None as "no filter" and the route renders against
        the user's default account.  This guards against a
        regression that would 404 on malformed input rather than
        falling back -- malformed input is a UX bug, not a
        security incident, so the "not supplied" semantics apply.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/calendar?account_id=notanumber",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200


# ── analytics.variance_tab -- F-098 ──────────────────────────────────


class TestVarianceTabPeriodIdOwnership:
    """``analytics.variance_tab`` rejects a cross-user ``period_id``.

    The variance txn filter joins ``account_id`` (user-owned) with
    ``pay_period_id``, so a cross-user ``period_id`` returns no
    rows -- but the metadata path (``_build_window_label`` and
    ``_variance_csv_filename``) reads ``PayPeriod.start_date``
    without an ownership re-check.  The route-boundary 404 closes
    the metadata leak and aligns with the security response rule.
    """

    def test_own_period_id_html_succeeds(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A's HTMX variance request with their own ``period_id`` returns 200.

        Baseline: a legitimate same-user ``period_id`` must
        continue to render successfully.
        """
        with app.app_context():
            own_period_id = seed_periods[0].id
            resp = auth_client.get(
                f"/analytics/variance?window=pay_period"
                f"&period_id={own_period_id}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_own_period_id_csv_succeeds(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A's CSV variance download with their own ``period_id`` returns 200.

        Baseline for the CSV path -- confirms the legitimate
        same-user CSV download is not blocked by the validator.
        """
        with app.app_context():
            own_period_id = seed_periods[0].id
            resp = auth_client.get(
                f"/analytics/variance?format=csv&window=pay_period"
                f"&period_id={own_period_id}",
            )
            assert resp.status_code == 200
            assert "text/csv" in resp.headers["Content-Type"]

    def test_cross_user_period_id_html_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_periods,
    ):
        """A's HTMX variance request with B's ``period_id`` returns 404.

        F-098 baseline: without the route-level guard the response
        body would carry ``_build_window_label``'s text containing
        the victim's start_date (e.g. ``"Pay period 2026-01-02 to
        2026-01-15"``).  The 404 prevents the leak.
        """
        with app.app_context():
            attacker_target = seed_second_periods[0].id
            assert attacker_target != seed_periods[0].id, (
                "fixture sanity: the two seeded users must have "
                "distinct period ids for the probe"
            )
            resp = auth_client.get(
                f"/analytics/variance?window=pay_period"
                f"&period_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_cross_user_period_id_csv_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_periods,
    ):
        """A's CSV variance download with B's ``period_id`` returns 404.

        F-098 specific: ``_variance_csv_filename`` builds a name
        like ``variance_period_2026-01-02.csv`` from the period's
        ``start_date``.  Without the guard the filename would leak
        the victim's date into the Content-Disposition header.
        """
        with app.app_context():
            attacker_target = seed_second_periods[0].id
            resp = auth_client.get(
                f"/analytics/variance?format=csv&window=pay_period"
                f"&period_id={attacker_target}",
            )
            assert resp.status_code == 404

    def test_csv_filename_does_not_leak_victim_start_date(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_periods,
    ):
        """A 404 response must not embed B's ``start_date`` anywhere.

        F-098's leak vector is the CSV filename built from
        ``period.start_date``.  After C-30 the route 404s before
        the helper runs, so the response must not contain the
        Content-Disposition header at all (and certainly not the
        victim's date).  This test asserts on the absence of the
        leaked value rather than just the status code so a
        regression that 404s but still emits the filename is
        caught.
        """
        with app.app_context():
            victim_period = db.session.get(
                PayPeriod, seed_second_periods[0].id,
            )
            victim_start = victim_period.start_date.isoformat()

            resp = auth_client.get(
                f"/analytics/variance?format=csv&window=pay_period"
                f"&period_id={victim_period.id}",
            )
            assert resp.status_code == 404

            # The 404 response must not carry a CSV
            # Content-Disposition with the victim's start_date.
            cd_header = resp.headers.get("Content-Disposition", "")
            assert victim_start not in cd_header, (
                f"Content-Disposition header leaked victim's "
                f"start_date {victim_start!r}: {cd_header!r}"
            )
            # Belt-and-suspenders: the body must not contain it
            # either (a future change that wraps the 404 in a
            # custom template should not regress this).
            body = resp.data.decode(errors="replace")
            assert victim_start not in body, (
                f"Response body leaked victim's start_date "
                f"{victim_start!r}"
            )

    def test_nonexistent_period_id_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """A non-existent ``period_id`` returns 404.

        Exercises the ``record is None`` branch of ``get_or_404``;
        the same 404 keeps "not found" and "not yours"
        indistinguishable from the client's perspective.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance?window=pay_period&period_id=9999999",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_no_period_id_param_uses_default(
        self, app, auth_client, seed_user, seed_periods_today,  # pylint: disable=unused-argument
    ):
        """A variance request without ``period_id`` falls back to the user's current period.

        Uses ``seed_periods_today`` so
        ``pay_period_service.get_current_period`` returns a real
        period.  Confirms the validation helper bypasses the
        check when the query arg is absent, preserving the
        "no period_id --> current period" service-default path.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/variance?window=pay_period",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200


# ── Defense in depth: validate regardless of window_type ─────────────


class TestVarianceTabPeriodIdDefenseInDepth:
    """The route boundary validates ``period_id`` regardless of ``window_type``.

    Today the variance service ignores ``period_id`` when
    ``window_type != "pay_period"`` and the CSV filename helper
    falls through to the year/month branches, so a cross-user
    period_id with ``window=year`` does not technically leak.
    The validation still runs at the boundary because:

      * The boundary must not depend on whether downstream code
        happens to consume the value.  A future refactor that
        starts using period_id under any window type would silently
        re-introduce the leak.
      * The IDOR probe model treats every user-supplied FK as
        equally privileged, regardless of conditional usage.

    This class locks in the always-validate posture.
    """

    def test_cross_user_period_id_with_month_window_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_periods,
    ):
        """A cross-user ``period_id`` paired with ``window=month`` still 404s.

        In normal flow ``window=month`` ignores ``period_id``, but
        the route boundary must still reject a cross-user FK so
        the always-validate posture is preserved.
        """
        with app.app_context():
            attacker_target = seed_second_periods[0].id
            resp = auth_client.get(
                f"/analytics/variance?window=month&month=1&year=2026"
                f"&period_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

    def test_cross_user_period_id_with_year_window_returns_404(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_periods,
    ):
        """A cross-user ``period_id`` paired with ``window=year`` still 404s.

        ``window=year`` is the broadest window and the most likely
        target of a "scan all victim period ids while looking
        innocuous" probe.  The validation must fire here too.
        """
        with app.app_context():
            attacker_target = seed_second_periods[0].id
            resp = auth_client.get(
                f"/analytics/variance?window=year&year=2026"
                f"&period_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404


# ── Sibling routes -- confirm they are NOT affected ─────────────────


class TestUntouchedRoutes:
    """``year_end_tab`` and ``trends_tab`` accept no FK query args.

    Locks in the audit conclusion that F-039 / F-098 are the only
    analytics ownership gaps.  If a future change adds an
    ``account_id`` or ``period_id`` query arg to year_end or
    trends, this class breaks and forces a fresh ownership audit.
    """

    def test_year_end_tab_baseline_succeeds(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """``year_end_tab`` accepts only ``year`` (no FK) and must keep working.

        Regression check: the C-30 changes are scoped to
        ``calendar_tab`` and ``variance_tab``; a stray helper call
        on this route would break this test.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/year-end?year=2026",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200

    def test_trends_tab_baseline_succeeds(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
    ):
        """``trends_tab`` accepts no query-arg FKs and must keep working.

        Regression check, same reasoning as the year-end test.
        """
        with app.app_context():
            resp = auth_client.get(
                "/analytics/trends",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 200


# ── Audit emission -- verify the cross-user log event fires ─────────


class TestCrossUserAuditEvent:
    """Cross-user analytics probes emit ``access_denied_cross_user``.

    The route-boundary helper delegates to ``get_or_404``, which
    is contractually responsible for the structured audit event.
    These tests are smoke checks that confirm the log call still
    fires in the analytics path -- a regression where the helper
    was bypassed (e.g. raw ``db.session.get`` + manual abort)
    would silently drop the SOC alert that depends on this event
    being emitted from every cross-user code path.

    The structured event name is carried in ``LogRecord.event``
    (set via the ``extra`` kwarg in :func:`log_event`), not in
    the message text -- so the assertions read the attribute
    rather than scanning the message string.
    """

    def test_cross_user_account_id_emits_audit_event(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user, caplog,
    ):
        """A cross-user account_id probe writes ``access_denied_cross_user`` at WARNING.

        Asserts on the structured ``event`` attribute rather than
        a mock so the test survives implementation refactors as
        long as the contractual event name is preserved.
        ``caplog`` is configured at WARNING -- the lower INFO
        ``resource_not_found`` event for the missing-pk branch is
        covered by the ``get_or_404`` unit tests, not duplicated
        here.
        """
        import logging  # pylint: disable=import-outside-toplevel

        caplog.set_level(logging.WARNING, logger="app.utils.auth_helpers")
        with app.app_context():
            attacker_target = seed_second_user["account"].id
            resp = auth_client.get(
                f"/analytics/calendar?account_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

            event_records = [
                r for r in caplog.records
                if getattr(r, "event", None) == "access_denied_cross_user"
            ]
            observed = [
                (r.levelname, r.getMessage(), getattr(r, "event", None))
                for r in caplog.records
            ]
            assert event_records, (
                "cross-user account_id must emit "
                "'access_denied_cross_user' at WARNING; "
                f"observed {observed}"
            )
            # And the event must record both user ids so SOC tooling
            # can correlate the probe to the target.
            ev = event_records[-1]
            assert getattr(ev, "model", None) == "Account"
            assert getattr(ev, "user_id", None) == seed_user["user"].id
            assert (
                getattr(ev, "owner_id", None)
                == seed_second_user["user"].id
            )

    def test_cross_user_period_id_emits_audit_event(
        self, app, auth_client, seed_user, seed_periods,  # pylint: disable=unused-argument
        seed_second_user, seed_second_periods, caplog,
    ):
        """A cross-user period_id probe writes ``access_denied_cross_user`` at WARNING.

        Same contract as the account_id case.  Both probes need
        the same event name so SOC dashboards can correlate IDOR
        attempts across analytics endpoints under a single rule.
        """
        import logging  # pylint: disable=import-outside-toplevel

        caplog.set_level(logging.WARNING, logger="app.utils.auth_helpers")
        with app.app_context():
            attacker_target = seed_second_periods[0].id
            resp = auth_client.get(
                f"/analytics/variance?window=pay_period"
                f"&period_id={attacker_target}",
                headers={"HX-Request": "true"},
            )
            assert resp.status_code == 404

            event_records = [
                r for r in caplog.records
                if getattr(r, "event", None) == "access_denied_cross_user"
            ]
            observed = [
                (r.levelname, r.getMessage(), getattr(r, "event", None))
                for r in caplog.records
            ]
            assert event_records, (
                "cross-user period_id must emit "
                "'access_denied_cross_user' at WARNING; "
                f"observed {observed}"
            )
            ev = event_records[-1]
            assert getattr(ev, "model", None) == "PayPeriod"
            assert getattr(ev, "user_id", None) == seed_user["user"].id
            assert (
                getattr(ev, "owner_id", None)
                == seed_second_user["user"].id
            )
