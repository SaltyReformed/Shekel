"""Loan route tests: a loan balance true-up, and the balance hero's click-to-edit.

Split from ``tests/test_routes/test_loan.py`` by ``tools/split`` (ruling R-BAL301).
"""

from datetime import date
from decimal import Decimal

from app.models.ref import AccountType
from app.services import account_service

from ._helpers import _create_auto_loan, _create_other_loan, _create_fresh_mortgage


class TestLoanBalanceTrueUp:
    """Tests for the dated balance true-up route (loan.true_up_balance).

    The route mirrors the checking-account anchor true-up UX
    (:func:`app.routes.accounts.true_up`) for loan accounts.  POSTing
    ``(anchor_date, anchor_balance)`` appends a ``user_trueup``
    :class:`LoanAnchorEvent` and redirects back to the dashboard.

    Test IDs follow the Commit-16 plan checklist (C16-1 .. C16-7).
    """

    # C16-1
    def test_trueup_appends_event(self, auth_client, seed_user, db, seed_periods):
        """POST trueup creates a new LoanAnchorEvent; no prior row mutated.

        Hand-check: the ``_create_auto_loan`` fixture writes ONE stored
        anchor event (the balance stated at setup, a tracking_start at
        $25,000; the origination is synthesized from params, not stored --
        as production's create_params does since the read switch).  After
        POSTing today / $24,000:
          * 302 redirect to /accounts/<id>/loan.
          * Two anchor events on disk (the stated balance + new trueup).
          * The new event has source_id == USER_TRUEUP id, balance
            $24,000, anchor_date == 2026-03-20 (the frozen "today"
            for this test file).
          * The prior event is byte-identical (no UPDATE).
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        from app import ref_cache  # pylint: disable=import-outside-toplevel
        from app.enums import LoanAnchorSourceEnum  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        before_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        before_snapshot = [
            (e.id, e.anchor_date, e.anchor_balance, e.source_id, e.created_at)
            for e in before_events
        ]
        assert len(before_snapshot) == 1, (
            "Fixture is expected to seed one stored event (the balance stated "
            "at setup; origination is synthesized, not stored); if this assertion "
            "fails the helper has drifted and the rest of this test "
            "is meaningless."
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "24000.00",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/accounts/{acct.id}/loan")

        db.session.expire_all()
        after_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        assert len(after_events) == 2

        after_by_id = {e.id: e for e in after_events}
        for snap in before_snapshot:
            e_id, e_date, e_balance, e_source, e_created = snap
            after = after_by_id[e_id]
            assert (
                (after.id, after.anchor_date, after.anchor_balance,
                 after.source_id, after.created_at)
                == snap
            ), (
                f"Prior event id={e_id} must not be mutated by a "
                f"trueup (LoanAnchorEvent is append-only)."
            )

        new_event = next(
            e for e in after_events
            if e.id not in {s[0] for s in before_snapshot}
        )
        user_trueup_id = ref_cache.loan_anchor_source_id(
            LoanAnchorSourceEnum.USER_TRUEUP,
        )
        assert new_event.source_id == user_trueup_id
        assert new_event.anchor_balance == Decimal("24000.00")
        assert new_event.anchor_date == date(2026, 3, 20)

    # C16-2
    def test_trueup_changes_loan_card_balance(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """After a trueup, the loan dashboard renders the new balance.

        The resolver replays from the latest event by
        ``(anchor_date, created_at)`` DESC, so a trueup at a future
        anchor_date than the existing seed trueup is selected.  This
        verifies the resolver consumes the freshly-written event.

        Hand-check: the balance stated at setup is dated ``_TRACKED_FROM``
        (2026-01-01) at $25,000.  The new trueup is dated 2026-03-20
        (today) at $23,500, which is strictly later, so the resolver picks
        it -- meaning the loan card's displayed balance becomes $23,500.00.
        """
        acct = _create_auto_loan(seed_user, db.session)

        # Sanity check pre-trueup balance == $25,000.00 (the seed
        # trueup amount).
        resp_pre = auth_client.get(f"/accounts/{acct.id}/loan")
        assert b"$25,000.00" in resp_pre.data

        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "23500.00",
            },
        )
        resp_post = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp_post.status_code == 200
        assert b"$23,500.00" in resp_post.data

    # C16-3
    def test_trueup_rejects_future_date(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_date strictly in the future -> rejected, no event written.

        Schema validation rejects future dates as a validation error;
        the route flashes "correct the highlighted errors" and
        redirects without writing.  Asserts via the event count that
        nothing was appended.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        future = date(2026, 3, 21).isoformat()  # one day past frozen today
        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={"anchor_date": future, "anchor_balance": "20000.00"},
        )
        # Validation failure: redirect (302) with flash.
        assert resp.status_code == 302

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before, (
            "Future anchor_date must NOT append an event."
        )

    # C16-4
    def test_trueup_rejects_pre_origination(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_date before origination -> rejected with explanatory flash.

        Hand-check: ``_create_auto_loan`` uses ``origination_date =
        date(2025, 1, 1)``.  Submitting 2024-12-31 must be rejected
        and no event appended.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2024, 12, 31).isoformat(),
                "anchor_balance": "20000.00",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"origination" in resp.data.lower() or b"Origination" in resp.data

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before, (
            "Pre-origination anchor_date must NOT append an event."
        )

    # C16-5
    def test_trueup_rejects_negative_balance(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """POST anchor_balance < 0 -> rejected, no event written.

        Schema-tier ``Range(min=0)`` plus the CHECK
        ``ck_loan_anchor_events_balance_nonneg`` at the storage tier.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "-100.00",
            },
        )
        assert resp.status_code == 302

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .count()
        )
        assert after == before

    # C16-6
    def test_trueup_is_append_only(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Two distinct trueups produce two new rows; prior rows untouched.

        Hand-check: starting from the seeded ONE event (the true-up;
        origination is synthesized, not stored), post two different
        trueups (different dates).  Final state must have three events;
        all earlier rows byte-identical.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        snapshot_before = [
            (e.id, e.anchor_date, e.anchor_balance, e.source_id, e.created_at)
            for e in (
                db.session.query(_LAE)
                .filter_by(account_id=acct.id)
                .order_by(_LAE.id)
                .all()
            )
        ]
        assert len(snapshot_before) == 1

        # First trueup -- different date than any seed event.
        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 2, 1).isoformat(),
                "anchor_balance": "24500.00",
            },
        )
        # Second trueup -- yet another distinct date.
        auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 1).isoformat(),
                "anchor_balance": "24000.00",
            },
        )

        db.session.expire_all()
        all_events = (
            db.session.query(_LAE)
            .filter_by(account_id=acct.id)
            .order_by(_LAE.id)
            .all()
        )
        assert len(all_events) == 3

        events_by_id = {e.id: e for e in all_events}
        for snap in snapshot_before:
            e_id, e_date, e_balance, e_source, e_created = snap
            after = events_by_id[e_id]
            assert (
                (after.id, after.anchor_date, after.anchor_balance,
                 after.source_id, after.created_at)
                == snap
            ), f"Append-only invariant violated: event id={e_id} mutated."

    # C16-7
    def test_trueup_form_includes_csrf_token(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The Record Loan Balance form renders the csrf_token hidden input.

        ``TestConfig`` disables CSRF enforcement so a runtime token
        check is not exercised in tests, but the form HTML must still
        carry the ``{{ csrf_token() }}`` placeholder so production
        (where CSRF is enabled) accepts the submission.  Asserting on
        the rendered form is the proxy for "CSRF is wired correctly."
        """
        acct = _create_auto_loan(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200

        # Locate the Record Loan Balance form and confirm both the
        # csrf_token field and the trueup action are present.
        html = resp.data.decode()
        trueup_url = f"/accounts/{acct.id}/loan/trueup"
        assert trueup_url in html
        # The csrf_token() helper renders as
        # <input type="hidden" name="csrf_token" value="..."> in
        # production; in TestConfig (CSRF disabled) it renders as
        # an empty string.  Assert the rendered form references
        # csrf_token to prove the template includes the call.
        assert b'name="csrf_token"' in resp.data or 'csrf_token' in html

    def test_trueup_idor_returns_404(
        self, auth_client, second_user, db, seed_periods,
    ):
        """POST trueup against another user's loan returns 404 (security).

        Cross-owner trueup must not write a row and must not leak the
        loan's existence; mirrors the rest of the loan-route IDOR
        contract.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        other = _create_other_loan(second_user, db.session)
        before = (
            db.session.query(_LAE)
            .filter_by(account_id=other.id)
            .count()
        )

        resp = auth_client.post(
            f"/accounts/{other.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "100.00",
            },
        )
        assert resp.status_code == 404

        db.session.expire_all()
        after = (
            db.session.query(_LAE)
            .filter_by(account_id=other.id)
            .count()
        )
        assert after == before

    def test_trueup_resubmit_is_idempotent(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """Submitting the same (date, balance) twice is idempotent.

        The second submit asserts what the first made governing, so
        :func:`apply_loan_anchor_true_up` writes nothing, returns
        ``UNCHANGED``, and the route flashes an informational message.
        Exactly one new event row exists at the (date, balance) tuple after
        both calls.  **It was a unique-index rejection until plan step
        X-f1c4b** (ruling R-EQ); the route behaviour is unchanged.
        """
        from app.models.loan_anchor_event import LoanAnchorEvent as _LAE  # pylint: disable=import-outside-toplevel
        acct = _create_auto_loan(seed_user, db.session)

        first = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "22500.00",
            },
        )
        assert first.status_code == 302

        second = auth_client.post(
            f"/accounts/{acct.id}/loan/trueup",
            data={
                "anchor_date": date(2026, 3, 20).isoformat(),
                "anchor_balance": "22500.00",
            },
        )
        assert second.status_code == 302

        db.session.expire_all()
        matching = (
            db.session.query(_LAE)
            .filter_by(
                account_id=acct.id,
                anchor_date=date(2026, 3, 20),
                anchor_balance=Decimal("22500.00"),
            )
            .all()
        )
        assert len(matching) == 1, (
            "A resubmit of the governing (date, balance) must write nothing, "
            "so exactly one row survives."
        )


# ── Click-to-Edit Balance Hero (D14 port, polish audit P-DT8) ────────


class TestLoanBalanceHeroClickToEdit:
    """S8 / D14: the loan detail hero doubles as the dated true-up control.

    ``loan.balance_hero`` renders the click-to-edit display cell (the
    Cancel / Escape revert target); ``loan.anchor_form`` swaps in the
    inline as-of-date + balance editor, whose form posts the EXISTING
    ``loan.true_up_balance`` redirect flow (already covered by
    TestLoanTrueUpBalance) -- so these tests pin the two fragments and
    the page wiring, not the write path.  Today is frozen to 2026-03-20
    by ``_freeze_today_inside_seed_range``.
    """

    def test_dashboard_hero_is_click_to_edit(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """The dashboard hero carries the editor opener AND keeps the form.

        The developer's S8 ruling keeps BOTH recording surfaces: the
        click-to-edit hero and the parameters card's "Record balance"
        form.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="loan-balance-hero"' in html
        assert f'hx-get="/accounts/{acct.id}/loan/anchor-form"' in html
        # The keep-both ruling: the guidance-carrying form card stays.
        assert "Record balance" in html

    def test_balance_hero_renders_display_cell(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET (HX) returns the resolver balance and the editor opener.

        A fresh $250,000 mortgage with zero confirmed payments resolves
        to exactly its original principal: 250,000.00.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/loan/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "250,000.00" in html
        assert 'id="loan-balance-hero"' in html
        assert f'hx-get="/accounts/{acct.id}/loan/anchor-form"' in html

    def test_balance_hero_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET without HX-Request redirects to the loan dashboard page."""
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/balance-hero")
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

    def test_balance_hero_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """GET another user's loan hero returns 404 and leaks nothing."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other.id}/loan/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other Loan" not in resp.data

    def test_balance_hero_unconfigured_loan_404s(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """A params-less loan has no dashboard hero: the fragment 404s.

        The full page renders setup.html for an unconfigured loan, so no
        hero fragment of it exists to serve (deliberately a 404, not the
        POST flows' flash-and-redirect, which would swap a whole page
        into the HTMX hero slot).
        """
        loan_type = (
            db.session.query(AccountType).filter_by(name="Mortgage").one()
        )
        account = account_service.create_account(
            account_service.AccountSpec(
                user_id=seed_user["user"].id,
                account_type_id=loan_type.id,
                name="Unconfigured Loan",
                anchor_balance=Decimal("0.00"),
            ),
        )
        db.session.add(account)
        db.session.commit()
        resp = auth_client.get(
            f"/accounts/{account.id}/loan/balance-hero",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404

    def test_anchor_form_renders_dated_editor(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET (HX) returns the inline editor with correct bounds + prefill.

        The date input is floored at origination (2026-02-01 for the
        fresh-mortgage default: first of the month before the frozen
        2026-03-20 today) and capped at today; the balance prefills the
        resolver figure (250000.00, zero payments confirmed); the form
        posts the existing dated true-up route; Cancel reverts through
        loan.balance_hero.
        """
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(
            f"/accounts/{acct.id}/loan/anchor-form",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        html = resp.data.decode()
        assert f'action="/accounts/{acct.id}/loan/trueup"' in html
        assert 'min="2026-02-01"' in html
        assert 'max="2026-03-20"' in html
        assert 'value="2026-03-20"' in html
        assert 'value="250000.00"' in html
        assert f'hx-get="/accounts/{acct.id}/loan/balance-hero"' in html
        assert 'name="csrf_token"' in html

    def test_anchor_form_redirects_without_htmx(
        self, auth_client, seed_user, db, seed_periods,
    ):
        """GET without HX-Request redirects to the loan dashboard page."""
        acct = _create_fresh_mortgage(seed_user, db.session)
        resp = auth_client.get(f"/accounts/{acct.id}/loan/anchor-form")
        assert resp.status_code == 302
        assert f"/accounts/{acct.id}/loan" in resp.headers.get("Location", "")

    def test_anchor_form_idor(
        self, auth_client, second_user, db, seed_periods,
    ):
        """GET another user's loan editor returns 404 and leaks nothing."""
        other = _create_other_loan(second_user, db.session)
        resp = auth_client.get(
            f"/accounts/{other.id}/loan/anchor-form",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 404
        assert b"Other Loan" not in resp.data
