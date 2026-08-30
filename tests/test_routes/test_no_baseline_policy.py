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

from datetime import timedelta
from decimal import Decimal

import pytest

from flask import Flask

import app as shekel_app_package
from app.models.account import Account
from app.models.ref import AccountType
from app.models.scenario import Scenario
from app.models.transfer_template import TransferTemplate
from app.services import account_service
from app.url_converters import register_url_converters
from app.utils.dates import display_today
from tests._test_helpers import (
    cadence_payload,
    create_hysa_account,
    create_loan_account,
    make_appreciating_account,
    make_investment_account,
)
# The world builders and the registry live in ``tests/conftest.py`` beside the
# worker-database plumbing they drive.  Importing them from there is safe and
# is the same module object pytest loaded -- ``tests`` is a package, so pytest
# imports its conftest as ``tests.conftest`` and this resolves out of
# ``sys.modules`` rather than executing it a second time (verified: a second
# execution would re-run ``_bootstrap_worker_database`` and create a second
# database).  Whether the seam should instead be its own module is a placement
# question raised with the developer, not settled here.
from tests.conftest import (
    build_periods_today,
    build_seed_user,
    register_seeded_state,
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
#: (``dashboard.py:540-541`` and ``:568-569``), which is why they take the
#: 204 arm only.  The first
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

#: The converters this fixture can fill.  A rule carrying any OTHER converter
#: is one it cannot reach, and lands in :data:`_UNREACHED_RULES` instead --
#: **including a rule that carries one of these AND something else**.  Deciding
#: that on what REMAINS after these are removed is what keeps such a rule out of
#: the sweep: testing for ``<int:account_id>`` first would emit seven cases
#: whose URL still held a literal ``<int:line_id>``, and a URL like that 404s,
#: which is under 500, which passes.  Six `escrow` rules are that shape today
#: and are POST-only; one GET among them would have been graded by nobody.
_FILLABLE = ("<int:account_id>", "<int:period_id>")


def _build_baseline_less_owner(db):
    """An OWNER holding every account kind, with the baseline removed.

    **Built ONCE per xdist worker and frozen into a snapshot database** (plan
    step balance:X-be-2, finding **N-387**); every test that declares this
    world still gets its own private clone of that snapshot, so per-test
    isolation is exactly what it was.  Once per WORKER, not once per run: the
    236 cases scatter across ``-n 12``, so the world is built up to twelve
    times a session rather than 236 times a worker.

    As a per-test fixture this cost 302 ms of setup for 22 ms of requests,
    236 times over; the file went 82.26 s -> 18.73 s serially.  The full split,
    and what of it this does NOT remove, is stated once in the seeded-start-
    state block comment in ``tests/conftest.py``.

    Every kind is present deliberately: the door that 500'd the property page
    is reachable only through a Property that SECURES a configured loan, and
    X-t5 already paid for a fixture that could not tell -- its Property fixture
    set ``secured_by_account_id``, which is not a field, so SQLAlchemy accepted
    the assignment in silence and the control exercised nothing.  The link here
    is ``collateral_account_id``, and the sweep asserts the property page's
    answer rather than only its status.

    It returns IDS and no ORM objects, which the seam requires: the dict is
    computed once, at build time, and handed to every test that declares the
    world, so an object in it would be bound to a session that closed with the
    build.

    Args:
        db: The Flask-SQLAlchemy extension to write the world through.

    Returns:
        A dict of the row ids each parametrised URL and assertion needs.
    """
    seed_user = build_seed_user(db)
    periods = build_periods_today(db, seed_user)
    user = seed_user["user"]
    anchor = periods[0]

    loan = create_loan_account(
        seed_user, db.session, name="Policy Loan",
        principal=Decimal("20000.00"), term=60,
        origination_date=display_today() - timedelta(days=400),
    )
    mortgage = create_loan_account(
        seed_user, db.session, name="Policy Mortgage",
        principal=Decimal("300000.00"), term=360,
        origination_date=display_today() - timedelta(days=800),
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
        "period": periods[4].id,
        # The transfer-create arm below files against a real category, and a
        # world hands out ids rather than the ORM rows ``seed_user`` returned.
        "rent_category": seed_user["categories"]["Rent"].id,
    }


register_seeded_state("baseline_less_owner", _build_baseline_less_owner)


@pytest.fixture()
def baseline_less_owner(seeded_world):
    """The ids of the world this file's tests declare.

    A thin alias for :data:`seeded_world` so the tests keep naming the state
    they are in rather than the mechanism that delivers it.

    Returns:
        A dict of the row ids each parametrised URL and assertion needs.
    """
    return seeded_world




def _sweep_cases(app):
    """Every ``(endpoint, kind, rule)`` this sweep grades, and what it skips.

    A route taking ``<int:account_id>`` becomes one case per account kind,
    because the kind decides which producer runs -- the loan doors are
    unreachable with a checking id, and the property door needs the Property.

    **Reachability is decided on what is left after :data:`_FILLABLE` is
    removed**, before the per-kind fan-out, so a rule carrying an account id AND
    an id this fixture has no row for is SKIPPED rather than swept with a
    converter still in it.

    It returns the RULE rather than a finished URL because a case has to exist
    before any fixture does -- see :data:`_SWEEP_CASES`.  The owner's real ids
    arrive later, through :func:`_case_url`.

    Args:
        app: any application whose ``url_map`` is the one to grade.

    Returns:
        ``(cases, skipped)`` -- the cases in ``url_map`` order, and the rule
        strings this fixture cannot reach.
    """
    skipped = []
    cases = []
    with app.app_context():
        rules = sorted(app.url_map.iter_rules(), key=lambda r: r.rule)
    for rule in rules:
        if "GET" not in rule.methods or rule.rule.startswith("/static"):
            continue
        residue = rule.rule
        for token in _FILLABLE:
            residue = residue.replace(token, "")
        if "<" in residue:
            skipped.append(rule.rule)
        elif "<int:account_id>" in rule.rule:
            for kind in _ACCOUNT_KINDS:
                cases.append((rule.endpoint, kind, rule.rule))
        else:
            cases.append((rule.endpoint, "", rule.rule))
    return cases, skipped


def _case_url(rule_string, kind, ids):
    """The concrete URL one case requests, with this owner's real ids."""
    url = rule_string.replace("<int:period_id>", str(ids["period"]))
    if kind:
        url = url.replace("<int:account_id>", str(ids[kind]))
    return url


def _route_table_app():
    """A Flask app carrying the route table and NOTHING else.

    **Why not ``create_app``.**  The table this sweep needs is settled by
    blueprint registration, but ``create_app`` under the testing config also
    ensures schemas, seeds ``ref.*`` and populates ``ref_cache``
    (`app/__init__.py:159-162` and the `ref_cache.init` at `:207`) -- 151 SQL
    statements over 3 connections including 5 ``CREATE SCHEMA IF NOT EXISTS``,
    measured 2026-08-29.  That work is orthogonal to ``url_map`` and it would
    run at COLLECTION here, where a database failure is a collection ERROR:
    under xdist a collection that DIFFERS BETWEEN WORKERS aborts the whole run
    rather than failing one test.  The two are not the same event and the
    difference matters: a uniform outage errors this file and lets the rest of
    the suite finish, while an INTERMITTENT one -- the shape two concurrent
    runs produce here -- aborts everything.  Seen once, 2026-08-29, against a
    DRAFT of this design that did build through ``create_app``:
    ``Different tests were collected between gw8 and gw3``, no test run.  It
    also reconfigures logging process-wide.

    **What this design costs in exchange, stated because it is not free.**  At
    HEAD the parametrize list was a constant tuple, so collection COULD NOT
    differ between workers whatever happened.  It is now derived, once per
    worker, from an app built at import: nothing in `_register_blueprints` is
    non-deterministic today, but the failure mode is no longer structurally
    impossible, and its blast radius is the suite rather than this file.

    ``register_url_converters`` is called FIRST because `create_app` documents
    that order as required (`app/__init__.py:136`): a rule naming a converter
    this app has not registered raises ``LookupError`` at import, which is that
    same collection error arriving by another door.

    Measured 2026-08-29, this table and ``create_app("testing")``'s are
    IDENTICAL -- 218 rules, equal on endpoint, rule and methods.  `create_app`
    adds no route of its own (no ``add_url_rule``, no ``@app.route``).

    **The identity is asserted for the GET routes this sweep grades, not
    assumed**: the coverage test compares this table against the one the
    FIXTURE's app serves.  A non-GET route registered outside a blueprint would
    not be covered by that comparison.

    Registering the blueprints here also marks each one registered-once in this
    process before any test runs.  Nothing in `app/` uses ``record_once`` today,
    so this is inert; a blueprint that grew one would have it consumed here.

    Returns:
        A Flask app whose ``url_map`` is the application's.
    """
    app = Flask(shekel_app_package.__name__)
    register_url_converters(app)
    # Pylint: ``protected-access`` -- ``_register_blueprints`` is the app
    # package's own single decider of what the route table holds, and reaching
    # for it is what keeps this enumeration from becoming a second list of
    # blueprints that could drift from it.  A public seam is worth minting;
    # that is a change to ``app/`` and is raised with the developer, not
    # assumed here.
    # pylint: disable=protected-access
    shekel_app_package._register_blueprints(app)
    return app


#: **Every route is its OWN test.**  This is the whole of the wall-clock fix,
#: and it replaces a hand-cut partition rather than resizing one.
#:
#: As ONE test the sweep made 388 requests (194 concrete URLs, each in both
#: request shapes) inside pytest-timeout's 30 s, which covers setup, call and
#: teardown together.  On 2026-08-16 it crossed: `test_no_get_route_returns_5xx`
#: timed out on four of nine CI runs.  Removing the cluster cost that made CI
#: slow (PR #106) bought headroom and did not remove the shape -- the measured
#: call was still 24.68 s against 30 s.
#:
#: **Splitting it into arms did not remove the shape either, whatever the arms
#: were named.**  One test issuing every request is O(the route table) under a
#: budget that is O(1) per test, and an arm only divides the constant: by
#: account KIND (`910065a9`) the largest arm still grew, and re-measured
#: 2026-08-29, eleven days later, the account-less arm was still exactly 54 URLs
#: while every one of the 42 URLs the table had gained sat in the KIND arms,
#: which grew 20 -> 26 each.  A kind axis cannot bound that by construction: one
#: new account route adds a URL to ALL SEVEN kind arms at once.  Finding
#: **N-364** reads the growth the other way round.  (An earlier draft cited a
#: 35 -> 41 census of ``<int:account_id>`` decorators as corroboration.  That
#: was an ARTIFACT: the grep only counted occurrences sharing a line with
#: ``.route(``, and 22 of today's 63 sit on continuation lines -- the true
#: count is 48 -> 63.  The +6 that matters is the GET rules per kind, 20 -> 26,
#: measured directly off ``url_map``.)
#:
#: One case per test makes the per-test cost O(1) in the route table: two
#: requests, whatever the app grows into.  Nothing here has a size to keep in
#: step, and pytest-timeout's 30 s goes back to being the hang detector
#: `pytest.ini` calibrated it as.  Measured CI-shaped (4 cores, ``-n 12``,
#: 2026-08-29): the largest single arm read 2.16 s split by account kind, and
#: no case's CALL reaches a `--durations` report ahead of this file's own
#: setup entries -- a case's two requests are a 0.020 s median.
#:
#: **What it costs, and where that cost actually lives.**  Measured serially
#: 2026-08-29, 252 items in 81.16 s: per-item SETUP is **87%** of that (236
#: sweep items, 0.270 s median, 70.49 s) and the requests the sweep exists to
#: make are **7%** (0.020 s median, 5.39 s).
#:
#: That 0.270 s splits, on matched serial probes of 30 items each the same day:
#: **0.160 s (59%) is this module's own seven-account fixture** and 0.110 s
#: (41%) is the per-test chain it inherits, whose floor is the autouse ``db``
#: fixture dropping and re-cloning the per-worker database.  Two earlier
#: readings of this file blamed the clone for the whole of it; both were
#: confounded -- one divided an xdist wall clock by an item count, charging the
#: fixed worker bootstrap to the items, and one compared against items that
#: turned out to use the same fixture.
#:
#: **The sweep is READ-ONLY -- every request here is a GET -- so it buys none
#: of that isolation.**  Paying once instead of 236 times is finding
#: **N-387**, owned by **X-be-2**, and the split above is what that step is
#: sized against: sharing a database alone recovers the 41%, so the shared
#: state has to carry the seven accounts too.  It is not done here, and the
#: obvious remedy is measured WRONG: a module-scoped clone is re-cloned out
#: from under this module by the next test from ANY other module landing on the
#: same worker, observed on three workers under ``-n 12`` (2026-08-29).
#:
#: Building an app at import is what a per-route test id costs -- ids are fixed
#: before any fixture runs -- but it costs no DATABASE: see
#: :func:`_route_table_app`, which registers the blueprints onto a bare Flask
#: app rather than calling ``create_app``.
_SWEEP_CASES = _sweep_cases(_route_table_app())[0]

#: Readable ids, so a CI failure names the route it found rather than an
#: index.  Keyed on the RULE and not the endpoint: `analytics.retired_tab`
#: carries three rules and `dashboard.page` two, so an endpoint key left
#: pytest to disambiguate five of the 236 by position -- which is the index
#: this line exists to avoid, and it re-points when a route is added.
_SWEEP_IDS = [f"{rule}[{kind}]" if kind else rule
              for _, kind, rule in _SWEEP_CASES]


@pytest.mark.seeded_start_state("baseline_less_owner")
class TestNoRouteCrashesWithoutABaseline:
    """The sweep: no GET route may 5xx for an owner with no baseline."""

    def test_the_sweep_still_covers_the_whole_url_map(self, app,
                                                      baseline_less_owner):
        """The COVERAGE claim, graded apart from the requests.

        Four assertions that are properties of ``url_map`` and the fixture and
        of nothing else, so they belong in one test rather than being restated
        in every case -- where they would also be one chance per case to rot
        into disagreement.

        The skip list is PINNED, not merely described.  The first draft
        asserted ``all("<" in rule for rule in skipped)`` -- true by
        construction, since that is the branch that fills the list, so it could
        never fail and its own docstring's promise ("an uncovered route that
        grows a balance read would pass this suite") was exactly what it
        allowed.  A new route this fixture cannot reach turns this RED, which
        is the only way the sweep's coverage claim stays honest.
        """
        cases, skipped = _sweep_cases(app)

        assert len(cases) > 100, (
            f"the sweep collapsed to {len(cases)} cases -- it is meant to "
            f"cover the whole url_map, so this means the enumeration stopped, "
            f"not that the app shrank"
        )
        assert sorted(skipped) == _UNREACHED_RULES, (
            "the set of routes this sweep cannot reach has changed. Add the "
            "id this fixture needs and grade the route, or pin it here with "
            "the reason it cannot be graded.\n"
            f"  now:      {sorted(skipped)}\n"
            f"  expected: {_UNREACHED_RULES}"
        )

        # **The AXIS control, graded against the FIXTURE and not against
        # `_ACCOUNT_KINDS`.**  Nothing else here reads the kind -- it selects no
        # test and gates no branch -- so a kind dropped from `_ACCOUNT_KINDS`
        # deletes every case for it, and the surfaces N-112 measured 500ing are
        # reached through the loan, mortgage and property kinds.  Comparing the
        # emitted kinds against `_ACCOUNT_KINDS` CANNOT catch that: both sides
        # are built from that tuple, so both shrink together and the assertion
        # stays green.  Measured 2026-08-29 -- cutting the tuple to two kinds
        # deletes 130 of 236 cases, 55%, and every assertion in an earlier draft
        # of this test passed, `len(cases) > 100` clearing 106 by six.
        #
        # So the axis is PINNED as a literal, the way `_UNREACHED_RULES` is and
        # for the same reason -- a pin is the one form that cannot shrink along
        # with what it grades.  It is NOT keyed on the fixture's dict minus a
        # stop-list: a set defined by SUBTRACTION claims members nobody
        # censused, and un-skipping any pinned rule would add a non-kind key
        # (a goal, a template) and turn this red for an unrelated reason, whose
        # natural repair grows the stop-list and decays the control back to
        # blindness. The containment arm below keeps the pin honest against the
        # fixture without inheriting that shape.
        assert _ACCOUNT_KINDS == (
            "checking", "loan", "mortgage", "property", "hysa", "card",
            "invest",
        ), (
            f"an account kind left the sweep's axis: {_ACCOUNT_KINDS}. Deleting "
            f"one deletes every case for it. If that is deliberate, say so "
            f"here and in the fixture; if it is not, this is 26 producers "
            f"going ungraded."
        )
        assert set(_ACCOUNT_KINDS) <= set(baseline_less_owner), (
            f"the sweep names a kind the fixture creates no account for: "
            f"{sorted(set(_ACCOUNT_KINDS) - set(baseline_less_owner))}"
        )
        assert {kind for _, kind, _ in cases} - {""} == set(_ACCOUNT_KINDS), (
            "the fan-out stopped emitting a case for every kind on the axis, "
            "so a family of producers is graded by nobody.\n"
            f"  emitted: {sorted({kind for _, kind, _ in cases} - {''})}\n"
            f"  axis:    {sorted(_ACCOUNT_KINDS)}"
        )

        # The import-time table against the one the requests run on.  The cases
        # are pytest IDS, fixed at collection from an app built before any
        # fixture; if that table and this one ever disagree, some route is
        # graded by no case at all and every case still passes.
        assert cases == _SWEEP_CASES, (
            "the route table read at collection is not the one the requests "
            "run against, so the sweep is grading a stale set of routes.\n"
            f"  at collection: {len(_SWEEP_CASES)} cases\n"
            f"  at run time:   {len(cases)} cases"
        )

    @pytest.mark.parametrize("case", _SWEEP_CASES, ids=_SWEEP_IDS)
    def test_no_get_route_returns_5xx(self, owner_client, baseline_less_owner,
                                      case):
        """ONE GET route answers a baseline-less owner without crashing.

        Both request shapes, because the handler branches on them and a sweep
        of one shape proves nothing about the other: a plain request must get
        the recovery page, an HTMX request must get 204 and leave the DOM
        alone.  Before the X-v1 handler this reported 8 endpoints raising
        ``ValueError`` from ``require_scenario``, 6 of them GETs.

        **Both are issued before either is graded.**  Asserting inside the loop
        would mean a route that 5xx's on the plain GET never has its fragment
        shape exercised at all, so the run would say nothing about the contract
        the 204 branch exists to keep -- on exactly the routes most likely to
        be breaking it.

        **One case per test, which is what bounds this file.**  See
        :data:`_SWEEP_CASES`: as one test, or as a hand-cut arm of one, the
        wall clock grew with the route table under a budget that does not.
        Here it cannot -- a case is two requests however large the app gets --
        and a failure names the single route that failed instead of counting
        them.
        """
        endpoint, kind, rule = case
        url = _case_url(rule, kind, baseline_less_owner)
        # A URL still holding a converter 404s, and 404 is under 500, so it
        # would pass while grading nothing.  `_sweep_cases` skips such a rule
        # rather than emitting it; this refuses one that got through anyway.
        assert "<" not in url, (
            f"{rule} reached the sweep with a converter unfilled ({url}); it "
            f"should have been skipped and pinned in _UNREACHED_RULES"
        )

        seen = []
        for headers in ({}, {"HX-Request": "true"}):
            resp = owner_client.get(url, headers=headers)
            seen.append((bool(headers), resp.status_code,
                         b"Setup Incomplete" in resp.data))

        crashed = [shape for shape in seen if shape[1] >= 500]
        assert not crashed, (
            f"{url} ({endpoint}, kind={kind or 'none'}) returned "
            f"{[s[1] for s in crashed]} for an owner with no baseline "
            f"scenario (htmx={[s[0] for s in crashed]})"
        )
        # A fragment must never receive the full-page card: htmx would swap a
        # setup page into a balance cell.  The sweep used to grade only the
        # 5xx, so THIS regression -- the one the 204 branch exists to prevent
        # -- would have passed it (X-v2's adversarial design review).
        assert not [s for s in seen if s[0] and s[2]], (
            f"{url} ({endpoint}) answered an HTMX request with the full-page "
            f"recovery card instead of 204"
        )

    @pytest.mark.parametrize("label,template", _MEASURED_PAGE_DOORS)
    def test_a_measured_door_answers_with_the_repair(
        self, app, owner_client, baseline_less_owner, label, template,
    ):
        """Each PAGE door that used to 500 now renders the repair card.

        The status code alone would pass against a blank 200, so this asserts
        the card's REPAIR is present -- the create-baseline form action, which
        is the only route back out of this state.
        """
        url = template.format(**baseline_less_owner)
        resp = owner_client.get(url)

        assert resp.status_code == 200, f"{label} ({url})"
        body = resp.data.decode()
        assert "Setup Incomplete" in body, f"{label} did not render the card"
        assert "/create-baseline" in body, (
            f"{label} rendered a card with no repair: the create-baseline form "
            f"is the only way out of this state"
        )

    @pytest.mark.parametrize("label,template", _MEASURED_DOORS)
    def test_a_measured_door_answers_an_htmx_request_with_204(
        self, app, owner_client, baseline_less_owner, label, template,
    ):
        """An HTMX request gets 204 and an EMPTY body, never the card.

        The grid partials' shipped contract, generalised: a fragment poll must
        leave the live DOM untouched.  Swapping a full-page setup card into a
        balance cell would be worse than the 500 it replaced.
        """
        url = template.format(**baseline_less_owner)
        resp = owner_client.get(url, headers={"HX-Request": "true"})

        assert resp.status_code == 204, f"{label} ({url})"
        assert resp.data == b"", (
            f"{label} returned a body with its 204; htmx swaps nothing on 204 "
            f"only because the body is empty"
        )


@pytest.mark.seeded_start_state("baseline_less_owner")
class TestTheHandlerItself:
    """The handler's own contract, exercised directly rather than inferred.

    Every test here needs the baseline-less owner, so the class declares that
    world.  The handler's NEGATIVE control -- the owner who HAS a baseline --
    is a different start state and therefore a different class,
    :class:`TestTheHandlerIsInertForAHealthyOwner` below.  A world is declared
    per class because a marker cannot be taken back off one test.
    """

    def test_it_logs_an_error_event(self, app, owner_client,
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
            owner_client.get(url)

        events = [r for r in caplog.records
                  if getattr(r, "event", None) == "baseline_missing"]
        assert events, (
            "the no-baseline handler answered without emitting its event; "
            f"records seen: {[r.message for r in caplog.records]}"
        )
        assert events[0].levelname == "ERROR"
        assert events[0].category == "error"

    def test_a_mutating_htmx_request_is_answered_not_silenced(
        self, app, owner_client, baseline_less_owner,
    ):
        """An HTMX POST gets the card, never 204.

        204 is right for an idempotent fragment poll and WRONG for a button:
        measured before the fix, ``POST /debt-strategy/calculate`` answered
        ``204`` with an empty body, so a user pressed Calculate and nothing
        happened -- silently, and every time.  Before X-v1 it 500'd, which at
        least said something.  Found by X-v2's adversarial design review and
        confirmed by executing it.
        """
        resp = owner_client.post(
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
        self, app, owner_client, baseline_less_owner, caplog,
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
            owner_client.get(url)

        events = [r for r in caplog.records
                  if getattr(r, "event", None) == "baseline_missing"]
        assert events
        assert events[0].context_user_id == baseline_less_owner["user_id"]

    def test_a_transfer_create_refuses_instead_of_reporting_success(
        self, app, owner_client, db, baseline_less_owner,
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
            "category_id": str(ids["rent_category"]),
        }
        one_time = {**base, "name": "F9 One Time",
                    "start_period_id": ids["period"]}
        recurring = {**base, "name": "F9 Recurring", **cadence_payload()}

        for payload in (one_time, recurring):
            resp = owner_client.post(
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


class TestTheHandlerIsInertForAHealthyOwner:
    """The negative half of the control, on the state every real user is in.

    Its own class because it is the one test in this file that must NOT start
    in the baseline-less world -- the owner it needs is the ordinary seeded
    one, with the baseline intact.  Declaring the world per class is what keeps
    that difference visible instead of hiding it in a fixture list.
    """

    def test_a_user_with_a_baseline_is_untouched(self, app, auth_client,
                                                 seed_user, seed_periods_today):
        """The handler is inert for the state every real user is in.

        A gate that fired for everyone would pass every arm above while
        breaking the application.
        """
        resp = auth_client.get("/grid")

        assert resp.status_code == 200
        assert "Setup Incomplete" not in resp.data.decode()
