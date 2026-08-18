"""The no-baseline policy, graded across EVERY route the app registers.

Plan step X-v1, rulings R-BW and R-CB.  Every balance the app produces is
scoped to a baseline scenario, so a user without one has no balance any surface
can answer.  The seam raises one named exception
(:class:`app.exceptions.BaselineMissingError`) and one application-level handler
answers it: the setup-recovery page for a full request, ``204 No Content`` for
an HTMX request, an ERROR log event either way.

**Why this suite enumerates ``url_map`` instead of listing routes.**  The
defect it exists to catch was found that way and could not have been found any
other way.  Finding N-112 counted the sites that STATE the no-baseline rule --
13 of them, in six modules.  The three surfaces that 500'd
(``routes/loan/_helpers._load_route_context``,
``home_equity_service.resolve_home_equity``,
``debt_strategy._load_debt_accounts``, reached by 8 endpoints between them)
state it nowhere at all, so no search for a predicate could reach them.  A
route added a year from now is graded here without its author knowing this rule
exists, which is the property the rejected ``@require_baseline`` decorator
could not have had.

**What it does NOT prove**, stated because a gate that reads as proving more
than it does is this arc's most expensive recurring lesson:

* it cannot see a route that answers with a **fabricated figure**.  A ``$0.00``
  hero over balances that are all ``None`` returns 200 and passes every arm
  here, and so did a balance sheet asserting ``in_balance`` over a ledger it
  could not read -- which is exactly how X-v1's first draft of this file
  graded that page green.  Both were deleted at plan step X-v2 rather than
  gated, because there is no mechanical predicate for "this number is
  invented";
* it grades **GET** routes.  Two of the eight endpoints the census measured are
  POSTs, and they stay outside the sweep (ruling R-CB); the mutating-request
  contract is pinned by :class:`TestTheHandlerItself` instead;
* it proves nothing about surfaces that resolve the baseline WITHOUT the
  balance seam.  Fifteen such sites remain in ``app/`` -- forms, template
  generation, posting sync -- and they are finding **N-117**, owned by plan
  step **X-y**.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from tests._test_helpers import (
    cadence_payload,
    create_hysa_account,
    create_loan_account,
    make_appreciating_account,
    make_investment_account,
)


#: The GET rules this fixture cannot reach, PINNED so the sweep's coverage
#: claim cannot rot.  Each takes an id for a row the fixture does not create
#: (a salary profile, a transaction, a transfer, a goal, a template, a
#: pension).  None of them reads a balance today; a NEW rule landing here turns
#: the sweep red rather than silently shrinking its coverage, which is the only
#: way "every GET route is graded" stays a true sentence.
_UNREACHED_RULES = [
    "/retirement/pension/<int:pension_id>/edit",
    "/salary/<int:profile_id>/anatomy/<int:period_id>",
    "/salary/<int:profile_id>/breakdown",
    "/salary/<int:profile_id>/breakdown/<int:period_id>",
    "/salary/<int:profile_id>/calibrate",
    "/salary/<int:profile_id>/edit",
    "/salary/<int:profile_id>/projection",
    "/savings/goals/<int:goal_id>/edit",
    "/templates/<int:template_id>/edit",
    "/transactions/<int:txn_id>/cell",
    "/transactions/<int:txn_id>/entries",
    "/transactions/<int:txn_id>/full-edit",
    "/transactions/<int:txn_id>/quick-edit",
    "/transfers/<int:template_id>/edit",
    "/transfers/<int:xfer_id>/full-edit",
    "/transfers/cell/<int:xfer_id>",
    "/transfers/quick-edit/<int:xfer_id>",
]

#: The PAGE endpoints measured to raise before plan step X-v1 shipped its
#: handler, with the door each reaches the seam through.  Named individually
#: because the sweep below proves only "nothing 5xx"; these prove the ANSWER is
#: the repair card, which is the part a future refactor could quietly turn back
#: into a 404 or an empty page while the sweep stayed green.
_MEASURED_PAGE_DOORS = [
    # routes/loan/_helpers._load_route_context -> _require_figures -> loan_figures
    ("loan dashboard", "/accounts/{loan}/loan"),
    ("mortgage dashboard", "/accounts/{mortgage}/loan"),
    # home_equity_service.resolve_home_equity -> loan_figures
    ("property detail", "/accounts/{property}/property"),
    # debt_strategy._load_debt_accounts -> balance_at
    ("debt strategy", "/debt-strategy"),
]

#: The two FRAGMENT endpoints on the same loan door.  They are listed apart
#: because a plain GET never reaches the seam through them at all: both redirect
#: a non-HTMX request to the loan page before resolving anything
#: (``dashboard.py:513``), which is why they take the 204 arm only.  The first
#: draft of this gate asserted the card for all six and FAILED on exactly these
#: two -- kept as a comment rather than deleted, because it is the reason the
#: split exists.
_MEASURED_FRAGMENT_DOORS = [
    ("loan anchor form", "/accounts/{loan}/loan/anchor-form"),
    ("loan balance hero", "/accounts/{loan}/loan/balance-hero"),
]

#: Every measured door, for the arm that grades the HTMX answer -- which is the
#: same for a page endpoint and a fragment endpoint.
_MEASURED_DOORS = _MEASURED_PAGE_DOORS + _MEASURED_FRAGMENT_DOORS

#: The account kinds every ``<int:account_id>`` route is swept against.  The
#: kind decides which PRODUCER runs, which is why one id would not do: the loan
#: doors are unreachable with a checking id and the property door needs the
#: Property.
_ACCOUNT_KINDS = (
    "checking", "loan", "mortgage", "property", "hysa", "card", "invest",
)

#: The sweep's arms: one per account kind, plus ``""`` for the routes that take
#: no account id.  **This is the axis the sweep is SPLIT along, and the split is
#: about a wall-clock budget rather than about coverage** -- the same requests
#: are made either way.
#:
#: As one test the sweep made 388 requests (194 concrete URLs, each in both
#: request shapes) inside pytest-timeout's 30 s, which covers setup, call and
#: teardown together.  It grows with the route table, and on 2026-08-16 it
#: crossed: `test_no_get_route_returns_5xx` timed out on four of nine CI runs.
#: Removing the cluster cost that made CI slow (PR #106) bought headroom and
#: did not remove the shape -- the measured call was still 24.68 s against 30 s,
#: a margin under five seconds that the next dozen routes would spend.
#:
#: Split this way the largest arm is the 54 account-less routes (108 requests)
#: and each kind arm is 40, so no single arm's runtime grows with anything but
#: its own share of the route table.
_SWEEP_ARMS = ("",) + _ACCOUNT_KINDS

#: Ids for the COVERAGE arm, which never issues a request.  The skip list and
#: the URL count are properties of ``url_map`` alone -- substitution cannot
#: fail differently for a real id than for a placeholder -- so that arm is
#: spared the seven-account fixture the request arms need.
_PLACEHOLDER_IDS = dict.fromkeys(_ACCOUNT_KINDS + ("period",), 1)


@pytest.fixture()
def baseline_less_owner(app, db, seed_user, seed_periods_today):
    """An OWNER holding every account kind, with the baseline removed.

    Every kind is present deliberately: the door that 500'd the property page
    is reachable only through a Property that SECURES a configured loan, and
    X-t5 already paid for a fixture that could not tell -- its Property fixture
    set ``secured_by_account_id``, which is not a field, so SQLAlchemy accepted
    the assignment in silence and the control exercised nothing.  The link here
    is ``collateral_account_id``, and the sweep asserts the property page's
    answer rather than only its status.

    Returns:
        A dict of the account ids each parametrised URL needs.
    """
    with app.app_context():
        user = seed_user["user"]
        anchor = seed_periods_today[0]

        loan = create_loan_account(
            seed_user, db.session, name="Policy Loan",
            principal=Decimal("20000.00"), term=60,
            origination_date=date.today() - timedelta(days=400),
        )
        mortgage = create_loan_account(
            seed_user, db.session, name="Policy Mortgage",
            principal=Decimal("300000.00"), term=360,
            origination_date=date.today() - timedelta(days=800),
        )
        prop = make_appreciating_account(
            seed_user, db.session, anchor, Decimal("400000.00"),
            Decimal("0.03000"),
        )
        # ``collateral_account_id`` is the real column; assigning any other
        # name would leave the loan unsecured and the property door unexercised.
        db.session.get(Account, mortgage.id).collateral_account_id = prop.id

        hysa = create_hysa_account(
            seed_user, db.session, anchor, Decimal("5000.00"),
        )
        card_type = (
            db.session.query(AccountType).filter_by(name="Credit Card").one()
        )
        card = account_service.create_account(
            account_service.AccountSpec(
                user_id=user.id, account_type_id=card_type.id,
                name="Policy Card", anchor_balance=Decimal("-500.00"),
            ),
        )
        invest = make_investment_account(
            seed_user, db.session, anchor, Decimal("30000.00"),
            name="Policy 401k",
        )

        scenario = db.session.get(Scenario, seed_user["scenario"].id)
        scenario.is_baseline = False
        db.session.commit()

        return {
            "user_id": user.id,
            "checking": seed_user["account"].id, "loan": loan.id,
            "mortgage": mortgage.id, "property": prop.id, "hysa": hysa.id,
            "card": card.id, "invest": invest.id,
            "period": seed_periods_today[4].id,
        }


def _concrete_urls(app, ids):
    """Yield every GET route as concrete URLs, one per account kind.

    A route taking ``<int:account_id>`` is requested once per account kind,
    because the kind decides which producer runs -- the loan doors are
    unreachable with a checking id, and the property door needs the Property.
    Routes whose remaining converters this fixture cannot supply are skipped
    and reported by the count arm, so a silent drop cannot read as coverage.
    """
    skipped = []
    urls = []
    with app.app_context():
        rules = sorted(app.url_map.iter_rules(), key=lambda r: r.rule)
    for rule in rules:
        if "GET" not in rule.methods or rule.rule.startswith("/static"):
            continue
        url = (rule.rule
               .replace("<int:period_id>", str(ids["period"]))
               .replace("<int:year>", str(date.today().year))
               .replace("<int:month>", str(date.today().month)))
        if "<int:account_id>" in url:
            for kind in _ACCOUNT_KINDS:
                urls.append((rule.endpoint, kind,
                             url.replace("<int:account_id>", str(ids[kind]))))
        elif "<" in url:
            skipped.append(rule.rule)
        else:
            urls.append((rule.endpoint, "", url))
    return urls, skipped


class TestNoRouteCrashesWithoutABaseline:
    """The sweep: no GET route may 5xx for an owner with no baseline."""

    def test_the_sweep_still_covers_the_whole_url_map(self, app):
        """The COVERAGE claim, graded apart from the requests.

        Two assertions that are properties of ``url_map`` and of nothing else,
        so they belong in one cheap arm rather than being repeated in each of
        the eight request arms -- where they would also be eight chances to
        rot into disagreement.

        The skip list is PINNED, not merely described.  The first draft
        asserted ``all("<" in rule for rule in skipped)`` -- true by
        construction, since that is the branch that fills the list, so it could
        never fail and its own docstring's promise ("an uncovered route that
        grows a balance read would pass this suite") was exactly what it
        allowed.  A new route this fixture cannot reach turns this RED, which
        is the only way the sweep's coverage claim stays honest.
        """
        urls, skipped = _concrete_urls(app, _PLACEHOLDER_IDS)

        assert len(urls) > 100, (
            f"the sweep collapsed to {len(urls)} urls -- it is meant to cover "
            f"the whole url_map, so this means the substitution stopped, not "
            f"that the app shrank"
        )
        assert sorted(skipped) == _UNREACHED_RULES, (
            "the set of routes this sweep cannot reach has changed. Add the "
            "id this fixture needs and grade the route, or pin it here with "
            "the reason it cannot be graded.\n"
            f"  now:      {sorted(skipped)}\n"
            f"  expected: {_UNREACHED_RULES}"
        )

        # **The hole the SPLIT opened, closed here.**  The request arms select
        # by kind, so a URL tagged with a kind no arm names would be requested
        # by nobody -- silently, with every arm still green, which is the exact
        # "a silent drop reads as coverage" failure this file was built to
        # refuse.  As one test that could not happen; the union WAS the loop.
        assert {row[1] for row in urls} == set(_SWEEP_ARMS), (
            "the sweep's arms no longer partition its URLs, so some route is "
            "graded by no arm at all. Add the new kind to _ACCOUNT_KINDS.\n"
            f"  tagged:   {sorted({row[1] for row in urls})}\n"
            f"  arms:     {sorted(_SWEEP_ARMS)}"
        )

    @pytest.mark.parametrize("arm", _SWEEP_ARMS)
    def test_no_get_route_returns_5xx(self, app, auth_client,
                                      baseline_less_owner, arm):
        """Every GET route answers a baseline-less owner without crashing.

        Both request shapes, because the handler branches on them and a sweep
        of one shape proves nothing about the other: a plain request must get
        the recovery page, an HTMX request must get 204 and leave the DOM
        alone.  Before the X-v1 handler this arm reported 8 endpoints raising
        ``ValueError`` from ``require_scenario``, 6 of them GETs.

        **One arm per account kind, plus one for the account-less routes.**
        The union of the arms is the same 388 requests the single test made;
        see :data:`_SWEEP_ARMS` for the wall-clock budget that forced the
        split.  Filtering on the kind ``_concrete_urls`` already tags each URL
        with is what keeps the two halves from drifting: there is still exactly
        one place that decides which URLs exist.
        """
        urls, _ = _concrete_urls(app, baseline_less_owner)
        arm_urls = [row for row in urls if row[1] == arm]
        assert arm_urls, (
            f"the {arm or 'account-less'!r} arm is empty -- every arm of "
            f"{_SWEEP_ARMS} must select URLs, so this means the kind tag or "
            f"the fixture ids stopped substituting, not that the app shrank"
        )
        crashed = []
        card_in_fragment = []
        for endpoint, kind, url in arm_urls:
            for headers in ({}, {"HX-Request": "true"}):
                resp = auth_client.get(url, headers=headers)
                if resp.status_code >= 500:
                    crashed.append((url, endpoint, kind, headers,
                                    resp.status_code))
                if headers and b"Setup Incomplete" in resp.data:
                    card_in_fragment.append((url, endpoint, kind))
        assert not crashed, (
            f"{len(crashed)} route/kind pairs 5xx for an owner with no "
            f"baseline scenario: {crashed}"
        )
        # A fragment must never receive the full-page card: htmx would swap a
        # setup page into a balance cell.  The sweep used to grade only the
        # 5xx, so THIS regression -- the one the 204 branch exists to prevent --
        # would have passed it (X-v2's adversarial design review).
        assert not card_in_fragment, (
            f"{len(card_in_fragment)} HTMX requests received the full-page "
            f"recovery card instead of 204: {card_in_fragment}"
        )

    @pytest.mark.parametrize("label,template", _MEASURED_PAGE_DOORS)
    def test_a_measured_door_answers_with_the_repair(
        self, app, auth_client, baseline_less_owner, label, template,
    ):
        """Each PAGE door that used to 500 now renders the repair card.

        The status code alone would pass against a blank 200, so this asserts
        the card's REPAIR is present -- the create-baseline form action, which
        is the only route back out of this state.
        """
        url = template.format(**baseline_less_owner)
        resp = auth_client.get(url)

        assert resp.status_code == 200, f"{label} ({url})"
        body = resp.data.decode()
        assert "Setup Incomplete" in body, f"{label} did not render the card"
        assert "/create-baseline" in body, (
            f"{label} rendered a card with no repair: the create-baseline form "
            f"is the only way out of this state"
        )

    @pytest.mark.parametrize("label,template", _MEASURED_DOORS)
    def test_a_measured_door_answers_an_htmx_request_with_204(
        self, app, auth_client, baseline_less_owner, label, template,
    ):
        """An HTMX request gets 204 and an EMPTY body, never the card.

        The grid partials' shipped contract, generalised: a fragment poll must
        leave the live DOM untouched.  Swapping a full-page setup card into a
        balance cell would be worse than the 500 it replaced.
        """
        url = template.format(**baseline_less_owner)
        resp = auth_client.get(url, headers={"HX-Request": "true"})

        assert resp.status_code == 204, f"{label} ({url})"
        assert resp.data == b"", (
            f"{label} returned a body with its 204; htmx swaps nothing on 204 "
            f"only because the body is empty"
        )


class TestTheHandlerItself:
    """The handler's own contract, exercised directly rather than inferred."""

    def test_it_logs_an_error_event(self, app, auth_client,
                                    baseline_less_owner, caplog):
        """The quiet screen is a LOUD log line.

        No code path produces a baseline-less owner (registration writes one,
        nothing deletes or un-baselines one, no path promotes a companion), so
        an occurrence is either data changed outside the app or a caller
        resolving the wrong user.  Both want an alert; a handler that degraded
        in silence would hide exactly the bug it is standing in front of.
        """
        url = f"/accounts/{baseline_less_owner['loan']}/loan"
        with caplog.at_level("ERROR"):
            auth_client.get(url)

        events = [r for r in caplog.records
                  if getattr(r, "event", None) == "baseline_missing"]
        assert events, (
            "the no-baseline handler answered without emitting its event; "
            f"records seen: {[r.message for r in caplog.records]}"
        )
        assert events[0].levelname == "ERROR"
        assert events[0].category == "error"

    def test_a_mutating_htmx_request_is_answered_not_silenced(
        self, app, auth_client, baseline_less_owner,
    ):
        """An HTMX POST gets the card, never 204.

        204 is right for an idempotent fragment poll and WRONG for a button:
        measured before the fix, ``POST /debt-strategy/calculate`` answered
        ``204`` with an empty body, so a user pressed Calculate and nothing
        happened -- silently, and every time.  Before X-v1 it 500'd, which at
        least said something.  Found by X-v2's adversarial design review and
        confirmed by executing it.
        """
        resp = auth_client.post(
            "/debt-strategy/calculate",
            data={"strategy": "avalanche", "extra_monthly": "0"},
            headers={"HX-Request": "true"},
        )

        assert resp.status_code == 200
        body = resp.data.decode()
        assert "Setup Incomplete" in body, (
            "a mutating HTMX request was answered with nothing; the user "
            "pressed a button and the app said neither what happened nor why"
        )
        assert "/create-baseline" in body

    def test_the_event_carries_the_user_the_raise_was_resolved_for(
        self, app, auth_client, baseline_less_owner, caplog,
    ):
        """The ERROR event logs the CONTEXT's user, not only the requester.

        The two ids differ only when a caller built a context for the wrong
        user -- which is one of the two reasons this handler exists, and the
        one the requesting id alone cannot show.  Logging only the requester
        would leave the event blind in exactly the failure it is meant to
        diagnose (X-v2's adversarial design review).
        """
        url = f"/accounts/{baseline_less_owner['loan']}/loan"
        with caplog.at_level("ERROR"):
            auth_client.get(url)

        events = [r for r in caplog.records
                  if getattr(r, "event", None) == "baseline_missing"]
        assert events
        assert events[0].context_user_id == baseline_less_owner["user_id"]

    def test_a_transfer_create_refuses_instead_of_reporting_success(
        self, app, auth_client, db, seed_user, baseline_less_owner,
    ):
        """A CREATE that materialises nothing may not say it created something.

        Ledger row **F-9**, closed by routing both transfer create paths onto
        ``require_baseline_scenario``.  They resolved the NULLABLE form and
        treated absence as a defined answer -- ``_materialize_one_time_transfer``
        returned ``None``, which is its caller's signal for "created, go ahead
        and commit", and ``generate_transfers_for_all_periods`` simply did not
        generate.  Either way the template was COMMITTED and the user was told a
        transfer existed that did not.

        Both branches are exercised because ``rule is None`` is what tells them
        apart: a one-time transfer takes the first path, a cadence the second.
        The assertion is on the DATABASE as well as the page, because the page
        alone passed before the fix on the recurring branch (it redirected to a
        cheerful "created" flash).
        """
        ids = baseline_less_owner
        base = {
            "default_amount": "150.00",
            "from_account_id": ids["checking"],
            "to_account_id": ids["hysa"],
            "category_id": str(seed_user["categories"]["Rent"].id),
        }
        one_time = {**base, "name": "F9 One Time",
                    "start_period_id": ids["period"]}
        recurring = {**base, "name": "F9 Recurring", **cadence_payload()}

        for payload in (one_time, recurring):
            resp = auth_client.post(
                "/transfers", data=payload, follow_redirects=True,
            )

            assert resp.status_code == 200
            assert "Setup Incomplete" in resp.data.decode(), (
                f"the create door answered {payload['name']!r} with something "
                f"other than the repair page"
            )
            with app.app_context():
                assert db.session.query(TransferTemplate).filter_by(
                    user_id=ids["user_id"], name=payload["name"],
                ).count() == 0, (
                    f"{payload['name']!r} was committed for an owner with no "
                    f"baseline scenario, so a definition exists that "
                    f"generated nothing"
                )

    def test_a_user_with_a_baseline_is_untouched(self, app, auth_client,
                                                 seed_user, seed_periods_today):
        """The handler is inert for the state every real user is in.

        The negative half of the control: a gate that fires for everyone would
        pass every arm above while breaking the application.
        """
        resp = auth_client.get("/grid")

        assert resp.status_code == 200
        assert "Setup Incomplete" not in resp.data.decode()
