"""Shekel IDOR DAST Probe -- black-box HTTP authorization testing.

Walks every user-scoped route in the Shekel application with three
attacker profiles (unauthenticated, ownerB, companionC) against
ownerA's resources. Asserts that each request returns the expected
blocking status code (``404`` for authenticated cross-user attacks,
``302``/``400`` for unauthenticated) and records every
request/response pair as JSON evidence.

Dev-only: refuses to start unless ``BASE_URL`` is exactly
``http://127.0.0.1:5000`` and multiple sentinel checks confirm the
target is the dev Flask dev-server, not the prod Gunicorn or the
end-user ``shekel-app`` container.

See ``docs/audits/security-2026-04-15/reports/15-idor-dast-design.md``
for the coverage rationale, probe matrix, and expected behaviour.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

# Include this script's directory so ``_audit_common`` imports resolve
# when the probe runs from the project root on the host.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# pylint: disable=wrong-import-position
from _audit_common import atomic_write_json

logger = logging.getLogger(__name__)

# ---- Hard URL constraints (the probe will not hit anything else) ----------

EXPECTED_BASE_URL = "http://127.0.0.1:5000"
EXPECTED_SCHEME = "http"
EXPECTED_HOST = "127.0.0.1"
EXPECTED_PORT = 5000
EXPECTED_SERVER_HEADER_PREFIX = "Werkzeug/"

# Substrings that MUST NOT appear in the base URL or its resolved
# components.
DISALLOWED_URL_SUBSTRINGS: tuple[str, ...] = (
    "prod", "tunnel", "cloudflare", "https:",
)

DEV_APP_CONTAINER = "shekel-dev-app"
PROD_APP_CONTAINER = "shekel-prod-app"

# ---- Probe tuning ---------------------------------------------------------

REQUEST_TIMEOUT_SECONDS = 10
MAX_BODY_EXCERPT = 500
MAX_REQUESTS_PER_ATTACKER = 500

# CSRF token name as emitted by Flask-WTF's hidden_tag().
CSRF_INPUT_NAME = "csrf_token"

# ---- Expected status codes per attacker/verb ------------------------------

STATE_CHANGE_METHODS: frozenset[str] = frozenset({"POST", "PATCH", "DELETE", "PUT"})

EXPECTED_UNAUTH_GET: tuple[int, ...] = (302,)
EXPECTED_UNAUTH_WRITE: tuple[int, ...] = (302, 400)
# Cross-user attacks accept both 404 and 302, matching
# ``tests/test_integration/test_access_control.py::_assert_blocked``
# which accepts either shape. CLAUDE.md states 404 as the canonical
# rule -- the mixed convention (half the routes 302, half 404) is
# reported as a compliance finding in the written audit report, not
# as an IDOR failure here.
EXPECTED_CROSS_USER: tuple[int, ...] = (302, 404)

# Routes that companionC legitimately reads (companion's own access
# to their linked owner's data). For these specific (attacker, path)
# pairs the expected status is 200.
LEGITIMATE_COMPANION_READS: frozenset[str] = frozenset({
    "/companion/period",
})


# ---- Data classes ---------------------------------------------------------


@dataclass
class RouteSpec:
    """One (method, path, target_model) tuple with its HTMX flag.

    ``path`` is the concrete URL path with IDs already substituted.
    ``target_model`` names the Python model the path references -- used
    only in the JSON output so a human reading the findings can locate
    the route quickly.
    """

    method: str
    path: str
    target_model: str
    is_htmx: bool


@dataclass
class ProbeRecord:
    """One probe request plus its response details and verdict.

    Serialized directly into the ``requests`` array of the output JSON.
    """

    attacker: str
    method: str
    path: str
    target_model: str
    target_owner: str
    hx_request: bool
    status: int
    location: str
    body_excerpt: str
    expected: list[int]
    verdict: str
    severity: str


@dataclass
class Summary:
    """Aggregated pass/fail counts and severity breakdown."""

    total_requests: int = 0
    passed: int = 0
    failed: int = 0
    by_attacker: dict[str, dict[str, int]] = field(default_factory=dict)
    critical: list[str] = field(default_factory=list)
    high: list[str] = field(default_factory=list)
    medium: list[str] = field(default_factory=list)
    # Counts cross-user attacker requests that returned 404 (canonical
    # per CLAUDE.md) vs 302 (non-canonical but still secure). Split
    # recorded so the findings report can flag the mixed convention
    # without affecting pass/fail.
    canonical_404: int = 0
    non_canonical_302: int = 0
    non_canonical_302_routes: list[str] = field(default_factory=list)


# ---- Safety rails ---------------------------------------------------------


def assert_base_url(base_url: str) -> None:
    """Reject any URL that is not literally the dev dev-server endpoint.

    The check is intentionally strict: literal string compare plus
    parsed-component compare plus substring blacklist. A human must
    change the constants above to target anything else.
    """
    if base_url != EXPECTED_BASE_URL:
        raise RuntimeError(
            f"Refusing to probe: base_url {base_url!r} is not "
            f"{EXPECTED_BASE_URL!r}. This probe targets only the dev "
            "Flask dev-server."
        )
    lowered = base_url.lower()
    for banned in DISALLOWED_URL_SUBSTRINGS:
        if banned in lowered:
            raise RuntimeError(
                f"Refusing to probe: base_url contains banned "
                f"substring {banned!r}."
            )
    parsed = urlparse(base_url)
    if parsed.scheme != EXPECTED_SCHEME:
        raise RuntimeError(
            f"Refusing to probe: scheme is {parsed.scheme!r} "
            f"(must be {EXPECTED_SCHEME!r})."
        )
    if parsed.hostname != EXPECTED_HOST:
        raise RuntimeError(
            f"Refusing to probe: hostname is {parsed.hostname!r} "
            f"(must be {EXPECTED_HOST!r})."
        )
    if parsed.port != EXPECTED_PORT:
        raise RuntimeError(
            f"Refusing to probe: port is {parsed.port!r} "
            f"(must be {EXPECTED_PORT})."
        )


def _docker_running(container_name: str) -> bool:
    """Return True if the named container is running.

    Uses ``docker ps --filter`` so we do not have to parse arbitrary
    docker output. A zero exit with the container name in stdout means
    running; any other outcome is treated as not running.
    """
    try:
        result = subprocess.run(
            [
                "docker", "ps",
                "--filter", f"name={container_name}",
                "--filter", "status=running",
                "--format", "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("docker ps failed: %s", exc)
        return False
    if result.returncode != 0:
        return False
    return container_name in result.stdout


def sentinel_health_check(base_url: str) -> dict[str, Any]:
    """Verify ``GET /health`` returns 200 and the Server header is Werkzeug.

    The Werkzeug banner is unique to the Flask dev-server. Prod runs
    Gunicorn behind Nginx (``Server: nginx/...``) and the end-user
    ``shekel-app`` container also runs Gunicorn. A Werkzeug banner is
    the strongest single indicator that we're talking to the dev
    compose's ``shekel-dev-app``.
    """
    url = base_url + "/health"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
    server = resp.headers.get("Server", "")
    ok_status = resp.status_code == 200
    ok_server = server.startswith(EXPECTED_SERVER_HEADER_PREFIX)
    return {
        "url": url,
        "status": resp.status_code,
        "server_header": server,
        "ok_status": ok_status,
        "ok_server": ok_server,
    }


def assert_safety_rails(base_url: str) -> dict[str, Any]:
    """Run every startup safety check. Raises on any failure.

    Returns a dict of the check results to be embedded in the output
    JSON as the ``sentinel`` block.
    """
    assert_base_url(base_url)

    health = sentinel_health_check(base_url)
    if not health["ok_status"]:
        raise RuntimeError(
            f"Sentinel /health status {health['status']} (need 200). "
            f"Response server header: {health['server_header']!r}."
        )
    if not health["ok_server"]:
        raise RuntimeError(
            f"Sentinel Server header {health['server_header']!r} does "
            f"not start with {EXPECTED_SERVER_HEADER_PREFIX!r}. This "
            "is not the dev Flask dev-server."
        )

    dev_running = _docker_running(DEV_APP_CONTAINER)
    if not dev_running:
        raise RuntimeError(
            f"Container {DEV_APP_CONTAINER!r} is not running. Bring "
            "up the dev compose before running the probe."
        )
    prod_running = _docker_running(PROD_APP_CONTAINER)
    if not prod_running:
        raise RuntimeError(
            f"Container {PROD_APP_CONTAINER!r} is not running. The "
            "audit environment expects prod to be live. Investigate "
            "before running the probe."
        )

    parsed = urlparse(base_url)
    return {
        "url_scheme": parsed.scheme,
        "url_host": parsed.hostname,
        "url_port": parsed.port,
        "health_status": health["status"],
        "server_header": health["server_header"],
        "dev_app_container_running": dev_running,
        "prod_app_container_running": prod_running,
        "verdict": "OK",
    }


# ---- Credentials loading --------------------------------------------------


def load_credentials(path: Path) -> dict[str, Any]:
    """Load the JSON file written by ``seed_dast_users.py``.

    Validates the structure enough to fail loudly on a malformed file.
    """
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    users = data.get("users", {})
    resources = data.get("ownerA_resources", {})
    required_users = {"ownerA", "ownerB", "companionC"}
    missing_users = required_users - set(users.keys())
    if missing_users:
        raise RuntimeError(
            f"Credentials file missing users: {sorted(missing_users)}"
        )
    required_resources = {
        "checking_account_id", "hysa_account_id", "mortgage_account_id",
        "investment_account_id", "escrow_component_id", "pay_period_ids",
        "salary_profile_id", "raise_id", "deduction_id",
        "savings_goal_id", "pension_id", "template_id",
        "transaction_ids", "entry_id", "transfer_template_id",
        "transfer_id", "category_ids",
    }
    missing_resources = required_resources - set(resources.keys())
    if missing_resources:
        raise RuntimeError(
            f"Credentials file missing ownerA resources: "
            f"{sorted(missing_resources)}"
        )
    return data


# ---- HTTP session helpers -------------------------------------------------


def _fetch_csrf_token(session: requests.Session, base_url: str) -> str:
    """Return a CSRF token scraped from the ``/login`` form.

    Flask-WTF's CSRF token is bound to the session's secret key, so a
    token fetched once on a session is valid for any form POST on that
    same session. The probe refreshes this only on login and reuses
    the same token across all subsequent write requests for a given
    attacker.
    """
    resp = session.get(
        base_url + "/login", timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    node = soup.find("input", {"name": CSRF_INPUT_NAME})
    if node is None or not node.get("value"):
        raise RuntimeError(
            "Could not scrape csrf_token from /login. The form may "
            "have changed shape; update the probe's scraper."
        )
    return str(node["value"])


def login(
    base_url: str, email: str, password: str,
) -> tuple[requests.Session, str]:
    """Log in as the given user and return (session, csrf_token).

    Returns a session with the Flask-Login cookie set. Raises on any
    non-redirect response from the POST (indicating auth failure or a
    server problem).
    """
    session = requests.Session()
    csrf_token = _fetch_csrf_token(session, base_url)
    resp = session.post(
        base_url + "/login",
        data={
            "email": email,
            "password": password,
            CSRF_INPUT_NAME: csrf_token,
        },
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if resp.status_code != 302:
        raise RuntimeError(
            f"Login failed for {email}: status {resp.status_code}, "
            f"body {resp.text[:200]!r}"
        )
    return session, csrf_token


# ---- Route catalog --------------------------------------------------------


def _account_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /accounts/* routes (including HYSA interest)."""
    checking = resources["checking_account_id"]
    hysa = resources["hysa_account_id"]
    return [
        RouteSpec("GET", f"/accounts/{checking}/edit", "Account", False),
        RouteSpec("POST", f"/accounts/{checking}", "Account", False),
        RouteSpec("POST", f"/accounts/{checking}/archive", "Account", False),
        RouteSpec("POST", f"/accounts/{checking}/unarchive", "Account", False),
        RouteSpec("POST", f"/accounts/{checking}/hard-delete", "Account", False),
        RouteSpec("PATCH", f"/accounts/{checking}/inline-anchor", "Account", True),
        RouteSpec("GET", f"/accounts/{checking}/inline-anchor-form", "Account", True),
        RouteSpec("GET", f"/accounts/{checking}/inline-anchor-display", "Account", True),
        RouteSpec("PATCH", f"/accounts/{checking}/true-up", "Account", True),
        RouteSpec("GET", f"/accounts/{checking}/anchor-form", "Account", True),
        RouteSpec("GET", f"/accounts/{checking}/anchor-display", "Account", True),
        RouteSpec("GET", f"/accounts/{checking}/checking", "Account", False),
        RouteSpec("GET", f"/accounts/{hysa}/interest", "Account (HYSA)", False),
        RouteSpec("POST", f"/accounts/{hysa}/interest/params", "Account (HYSA)", False),
    ]


def _loan_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /accounts/<id>/loan/* routes."""
    mortgage = resources["mortgage_account_id"]
    escrow = resources["escrow_component_id"]
    return [
        RouteSpec("GET", f"/accounts/{mortgage}/loan", "Account (Loan)", False),
        RouteSpec("POST", f"/accounts/{mortgage}/loan/setup", "Account (Loan)", False),
        RouteSpec("POST", f"/accounts/{mortgage}/loan/params", "Account (Loan)", False),
        RouteSpec("POST", f"/accounts/{mortgage}/loan/rate", "Account (Loan)", False),
        RouteSpec("POST", f"/accounts/{mortgage}/loan/escrow", "Account (Loan)", False),
        RouteSpec(
            "POST",
            f"/accounts/{mortgage}/loan/escrow/{escrow}/delete",
            "Account + EscrowComponent",
            True,
        ),
        RouteSpec("POST", f"/accounts/{mortgage}/loan/payoff", "Account (Loan)", False),
        RouteSpec("POST", f"/accounts/{mortgage}/loan/refinance", "Account (Loan)", False),
        RouteSpec(
            "POST", f"/accounts/{mortgage}/loan/create-transfer",
            "Account (Loan)", False,
        ),
    ]


def _investment_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /accounts/<id>/investment/* routes."""
    investment = resources["investment_account_id"]
    return [
        RouteSpec("GET", f"/accounts/{investment}/investment", "Account (401k)", False),
        RouteSpec(
            "GET", f"/accounts/{investment}/investment/growth-chart",
            "Account (401k)", True,
        ),
        RouteSpec(
            "POST", f"/accounts/{investment}/investment/params",
            "Account (401k)", False,
        ),
        RouteSpec(
            "POST",
            f"/accounts/{investment}/investment/create-contribution-transfer",
            "Account (401k)", False,
        ),
    ]


def _template_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /templates/<id>/* routes."""
    template = resources["template_id"]
    return [
        RouteSpec("GET", f"/templates/{template}/edit", "TransactionTemplate", False),
        RouteSpec("POST", f"/templates/{template}", "TransactionTemplate", False),
        RouteSpec("POST", f"/templates/{template}/archive", "TransactionTemplate", False),
        RouteSpec("POST", f"/templates/{template}/unarchive", "TransactionTemplate", False),
        RouteSpec("POST", f"/templates/{template}/hard-delete", "TransactionTemplate", False),
    ]


def _transfer_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /transfers/* routes (template + instance)."""
    tpl = resources["transfer_template_id"]
    xfer = resources["transfer_id"]
    return [
        RouteSpec("GET", f"/transfers/{tpl}/edit", "TransferTemplate", False),
        RouteSpec("POST", f"/transfers/{tpl}", "TransferTemplate", False),
        RouteSpec("POST", f"/transfers/{tpl}/archive", "TransferTemplate", False),
        RouteSpec("POST", f"/transfers/{tpl}/unarchive", "TransferTemplate", False),
        RouteSpec("POST", f"/transfers/{tpl}/hard-delete", "TransferTemplate", False),
        RouteSpec("GET", f"/transfers/cell/{xfer}", "Transfer", True),
        RouteSpec("GET", f"/transfers/quick-edit/{xfer}", "Transfer", True),
        RouteSpec("GET", f"/transfers/{xfer}/full-edit", "Transfer", False),
        RouteSpec("PATCH", f"/transfers/instance/{xfer}", "Transfer", True),
        RouteSpec("DELETE", f"/transfers/instance/{xfer}", "Transfer", True),
        RouteSpec("POST", f"/transfers/instance/{xfer}/mark-done", "Transfer", True),
        RouteSpec("POST", f"/transfers/instance/{xfer}/cancel", "Transfer", True),
    ]


def _transaction_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /transactions/<id>/* and /pay-periods/<id>/carry-forward."""
    txn = resources["transaction_ids"][0]
    period = resources["pay_period_ids"][0]
    return [
        RouteSpec("GET", f"/transactions/{txn}/cell", "Transaction", True),
        RouteSpec("GET", f"/transactions/{txn}/quick-edit", "Transaction", True),
        RouteSpec("GET", f"/transactions/{txn}/full-edit", "Transaction", False),
        RouteSpec("PATCH", f"/transactions/{txn}", "Transaction", True),
        RouteSpec("POST", f"/transactions/{txn}/mark-done", "Transaction", True),
        RouteSpec("POST", f"/transactions/{txn}/mark-credit", "Transaction", True),
        RouteSpec("DELETE", f"/transactions/{txn}/unmark-credit", "Transaction", True),
        RouteSpec("POST", f"/transactions/{txn}/cancel", "Transaction", True),
        RouteSpec("DELETE", f"/transactions/{txn}", "Transaction", True),
        RouteSpec("POST", f"/pay-periods/{period}/carry-forward", "PayPeriod", False),
    ]


def _entry_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /transactions/<id>/entries/* routes."""
    txn = resources["transaction_ids"][0]
    entry = resources["entry_id"]
    return [
        RouteSpec("GET", f"/transactions/{txn}/entries", "Transaction", True),
        RouteSpec("POST", f"/transactions/{txn}/entries", "TransactionEntry", True),
        RouteSpec(
            "PATCH", f"/transactions/{txn}/entries/{entry}",
            "TransactionEntry", True,
        ),
        RouteSpec(
            "PATCH", f"/transactions/{txn}/entries/{entry}/cleared",
            "TransactionEntry", True,
        ),
        RouteSpec(
            "DELETE", f"/transactions/{txn}/entries/{entry}",
            "TransactionEntry", True,
        ),
    ]


def _salary_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /salary/* routes (profile, raises, deductions, calibrate)."""
    profile = resources["salary_profile_id"]
    raise_id = resources["raise_id"]
    deduction = resources["deduction_id"]
    period = resources["pay_period_ids"][0]
    return [
        RouteSpec("GET", f"/salary/{profile}/edit", "SalaryProfile", False),
        RouteSpec("POST", f"/salary/{profile}", "SalaryProfile", False),
        RouteSpec("POST", f"/salary/{profile}/delete", "SalaryProfile", False),
        RouteSpec("POST", f"/salary/{profile}/raises", "SalaryRaise", False),
        RouteSpec("POST", f"/salary/raises/{raise_id}/delete", "SalaryRaise", False),
        RouteSpec("POST", f"/salary/raises/{raise_id}/edit", "SalaryRaise", False),
        RouteSpec("POST", f"/salary/{profile}/deductions", "PaycheckDeduction", False),
        RouteSpec(
            "POST", f"/salary/deductions/{deduction}/delete",
            "PaycheckDeduction", False,
        ),
        RouteSpec(
            "POST", f"/salary/deductions/{deduction}/edit",
            "PaycheckDeduction", False,
        ),
        RouteSpec(
            "GET", f"/salary/{profile}/breakdown/{period}",
            "SalaryProfile + PayPeriod", False,
        ),
        RouteSpec("GET", f"/salary/{profile}/breakdown", "SalaryProfile", False),
        RouteSpec("GET", f"/salary/{profile}/projection", "SalaryProfile", False),
        RouteSpec("GET", f"/salary/{profile}/calibrate", "SalaryProfile", False),
        RouteSpec("POST", f"/salary/{profile}/calibrate", "SalaryProfile", False),
        RouteSpec(
            "POST", f"/salary/{profile}/calibrate/confirm",
            "SalaryProfile", False,
        ),
        RouteSpec(
            "POST", f"/salary/{profile}/calibrate/delete",
            "SalaryProfile", False,
        ),
    ]


def _savings_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /savings/goals/<id>/* routes."""
    goal = resources["savings_goal_id"]
    return [
        RouteSpec("GET", f"/savings/goals/{goal}/edit", "SavingsGoal", False),
        RouteSpec("POST", f"/savings/goals/{goal}", "SavingsGoal", False),
        RouteSpec("POST", f"/savings/goals/{goal}/delete", "SavingsGoal", False),
    ]


def _category_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /categories/<id>/* routes."""
    category = resources["category_ids"][0]
    return [
        RouteSpec("POST", f"/categories/{category}/edit", "Category", False),
        RouteSpec("POST", f"/categories/{category}/archive", "Category", False),
        RouteSpec("POST", f"/categories/{category}/unarchive", "Category", False),
        RouteSpec("POST", f"/categories/{category}/delete", "Category", False),
    ]


def _retirement_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /retirement/pension/<id>/* routes."""
    pension = resources["pension_id"]
    return [
        RouteSpec("GET", f"/retirement/pension/{pension}/edit", "PensionProfile", False),
        RouteSpec("POST", f"/retirement/pension/{pension}", "PensionProfile", False),
        RouteSpec(
            "POST", f"/retirement/pension/{pension}/delete",
            "PensionProfile", False,
        ),
    ]


def _dashboard_and_companion_routes(resources: dict[str, Any]) -> list[RouteSpec]:
    """RouteSpecs for /dashboard/mark-paid/<id> and /companion/period/<id>."""
    txn = resources["transaction_ids"][0]
    period = resources["pay_period_ids"][0]
    return [
        RouteSpec("POST", f"/dashboard/mark-paid/{txn}", "Transaction", True),
        RouteSpec(
            "GET", f"/companion/period/{period}",
            "PayPeriod (companion view)", False,
        ),
    ]


def build_route_catalog(resources: dict[str, Any]) -> list[RouteSpec]:
    """Return every user-scoped route the probe will exercise.

    Assembled by concatenating each blueprint's RouteSpec list. The
    per-blueprint helpers keep this function short and make each
    coverage slice reviewable on its own.
    """
    catalog: list[RouteSpec] = []
    for builder in (
        _account_routes,
        _loan_routes,
        _investment_routes,
        _template_routes,
        _transfer_routes,
        _transaction_routes,
        _entry_routes,
        _salary_routes,
        _savings_routes,
        _category_routes,
        _retirement_routes,
        _dashboard_and_companion_routes,
    ):
        catalog.extend(builder(resources))
    return catalog


def build_companion_settings_routes(
    companion_user_id: int,
) -> list[RouteSpec]:
    """Return the ``/settings/companions/<id>/*`` owner-only routes.

    These target companionC's user_id (who is linked to ownerA). When
    attacked by ownerB or an unauthenticated client, they must 404.
    When attacked by ownerB specifically the expected behavior is
    still 404 because ownerB does not own companionC.
    """
    return [
        RouteSpec(
            "POST",
            f"/settings/companions/{companion_user_id}/edit",
            "User (companion)",
            False,
        ),
        RouteSpec(
            "POST",
            f"/settings/companions/{companion_user_id}/deactivate",
            "User (companion)",
            False,
        ),
        RouteSpec(
            "POST",
            f"/settings/companions/{companion_user_id}/reactivate",
            "User (companion)",
            False,
        ),
    ]


# ---- Probe execution ------------------------------------------------------


def _classify_fail_severity(status: int) -> str:
    """Map an unexpected status code to a severity label."""
    if status == 200:
        return "Critical"
    if status == 500:
        return "High"
    if status == 403:
        return "Medium"
    return "Medium"


def _expected_for(
    attacker: str, method: str, path: str = "",
) -> tuple[int, ...]:
    """Return the tuple of acceptable statuses for an (attacker, method, path).

    The ``path`` argument is used to special-case companionC's
    legitimate read paths (e.g. ``/companion/period/<linked-owner's
    period>``) where a 200 is the correct response.
    """
    if attacker == "unauth":
        if method in STATE_CHANGE_METHODS:
            return EXPECTED_UNAUTH_WRITE
        return EXPECTED_UNAUTH_GET
    if attacker == "companionC" and any(
        path.startswith(p) for p in LEGITIMATE_COMPANION_READS
    ):
        return (200,)
    return EXPECTED_CROSS_USER


def _truncate_body(text: str) -> str:
    """Return the first ``MAX_BODY_EXCERPT`` characters of the response body."""
    if len(text) <= MAX_BODY_EXCERPT:
        return text
    return text[:MAX_BODY_EXCERPT]


def _error_record(
    attacker: str, spec: RouteSpec, body_excerpt: str,
) -> ProbeRecord:
    """Build a ProbeRecord for a transport-level failure (timeout, conn err)."""
    return ProbeRecord(
        attacker=attacker,
        method=spec.method,
        path=spec.path,
        target_model=spec.target_model,
        target_owner="ownerA",
        hx_request=spec.is_htmx,
        status=-1,
        location="",
        body_excerpt=body_excerpt,
        expected=list(_expected_for(attacker, spec.method, spec.path)),
        verdict="FAIL",
        severity="High",
    )


def _send(
    session: requests.Session | None,
    csrf_token: str | None,
    base_url: str,
    spec: RouteSpec,
) -> requests.Response:
    """Send one HTTP request per the spec and return the Response."""
    headers: dict[str, str] = {}
    if spec.is_htmx:
        headers["Hx-Request"] = "true"
    data: dict[str, str] | None = None
    if spec.method in STATE_CHANGE_METHODS and csrf_token is not None:
        data = {CSRF_INPUT_NAME: csrf_token}
    http = session if session is not None else requests
    return http.request(
        spec.method,
        base_url + spec.path,
        data=data,
        headers=headers,
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )


def probe_one(
    session: requests.Session | None,
    csrf_token: str | None,
    base_url: str,
    attacker: str,
    spec: RouteSpec,
) -> ProbeRecord:
    """Execute one (attacker, route) probe and return the record.

    ``session`` is None for the unauth attacker. ``csrf_token`` is
    threaded through for state-changing verbs; unauth probes include no
    token and we accept either a CSRF 400 or a login 302 as PASS.
    """
    try:
        resp = _send(session, csrf_token, base_url, spec)
    except requests.Timeout:
        return _error_record(attacker, spec, "TIMEOUT")
    except requests.ConnectionError as exc:
        return _error_record(attacker, spec, f"CONNECTION_ERROR: {exc}")

    expected = _expected_for(attacker, spec.method, spec.path)
    verdict = "PASS" if resp.status_code in expected else "FAIL"
    if verdict == "PASS":
        severity = "PASS"
    else:
        severity = _classify_fail_severity(resp.status_code)
    return ProbeRecord(
        attacker=attacker,
        method=spec.method,
        path=spec.path,
        target_model=spec.target_model,
        target_owner="ownerA",
        hx_request=spec.is_htmx,
        status=resp.status_code,
        location=resp.headers.get("Location", ""),
        body_excerpt=_truncate_body(resp.text),
        expected=list(expected),
        verdict=verdict,
        severity=severity,
    )


@dataclass
class ProbeContext:
    """Mutable state shared across attacker runs.

    Holds the fixed config (``base_url``, ``catalog``) and the
    accumulating outputs (``records``, ``summary``) so per-attacker
    helpers take a single context argument instead of a long positional
    list.
    """

    base_url: str
    catalog: list[RouteSpec]
    records: list[ProbeRecord] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)


def _tally_record(record: ProbeRecord, summary: Summary) -> None:
    """Update the aggregate summary with one probe record."""
    summary.total_requests += 1
    bucket = summary.by_attacker.setdefault(
        record.attacker, {"pass": 0, "fail": 0},
    )
    if record.verdict == "PASS":
        summary.passed += 1
        bucket["pass"] += 1
        # For cross-user passes, track 302 vs 404 so the report can
        # surface the mixed-convention compliance finding.
        if record.attacker in {"ownerB", "companionC"}:
            if record.status == 404:
                summary.canonical_404 += 1
            elif record.status == 302:
                summary.non_canonical_302 += 1
                summary.non_canonical_302_routes.append(
                    f"{record.attacker} {record.method} {record.path}"
                )
        return
    summary.failed += 1
    bucket["fail"] += 1
    tag = (
        f"{record.attacker} {record.method} {record.path} "
        f"-> {record.status} (expected {record.expected})"
    )
    if record.severity == "Critical":
        summary.critical.append(tag)
    elif record.severity == "High":
        summary.high.append(tag)
    else:
        summary.medium.append(tag)


def _run_attacker(
    ctx: ProbeContext,
    attacker: str,
    session: requests.Session | None,
    csrf_token: str | None,
) -> None:
    """Run every route in the catalog with one attacker identity."""
    ctx.summary.by_attacker.setdefault(attacker, {"pass": 0, "fail": 0})
    for spec in ctx.catalog:
        record = probe_one(session, csrf_token, ctx.base_url, attacker, spec)
        ctx.records.append(record)
        _tally_record(record, ctx.summary)


def run_probe(
    base_url: str,
    credentials: dict[str, Any],
) -> tuple[list[ProbeRecord], Summary]:
    """Execute all probe requests for all attacker profiles.

    Returns the full list of records plus the aggregated summary.
    """
    catalog = build_route_catalog(credentials["ownerA_resources"])
    catalog.extend(
        build_companion_settings_routes(
            credentials["users"]["companionC"]["user_id"],
        ),
    )
    if len(catalog) > MAX_REQUESTS_PER_ATTACKER:
        raise RuntimeError(
            f"Route catalog size {len(catalog)} exceeds per-attacker "
            f"cap {MAX_REQUESTS_PER_ATTACKER}."
        )

    owner_b_session, owner_b_csrf = login(
        base_url,
        credentials["users"]["ownerB"]["email"],
        credentials["users"]["ownerB"]["password"],
    )
    companion_session, companion_csrf = login(
        base_url,
        credentials["users"]["companionC"]["email"],
        credentials["users"]["companionC"]["password"],
    )

    ctx = ProbeContext(base_url=base_url, catalog=catalog)
    _run_attacker(ctx, "unauth", None, None)
    _run_attacker(ctx, "ownerB", owner_b_session, owner_b_csrf)
    _run_attacker(ctx, "companionC", companion_session, companion_csrf)
    return ctx.records, ctx.summary


# ---- Output ---------------------------------------------------------------


def _records_to_json(records: list[ProbeRecord]) -> list[dict[str, Any]]:
    """Convert the record dataclasses into JSON-safe dicts."""
    return [asdict(r) for r in records]


def _summary_to_json(summary: Summary) -> dict[str, Any]:
    """Convert the summary dataclass into a JSON-safe dict."""
    return {
        "total_requests": summary.total_requests,
        "pass": summary.passed,
        "fail": summary.failed,
        "by_attacker": summary.by_attacker,
        "critical_findings": summary.critical,
        "high_findings": summary.high,
        "medium_findings": summary.medium,
        "cross_user_canonical_404_count": summary.canonical_404,
        "cross_user_non_canonical_302_count": summary.non_canonical_302,
        "cross_user_non_canonical_302_routes": (
            summary.non_canonical_302_routes
        ),
    }


def _seed_summary_for_output(
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """Build the seed_summary block in the output JSON from credentials."""
    users = credentials["users"]
    resources = credentials["ownerA_resources"]
    return {
        "ownerA": {
            "user_id": users["ownerA"]["user_id"],
            "email": users["ownerA"]["email"],
            "resources": resources,
        },
        "ownerB": {
            "user_id": users["ownerB"]["user_id"],
            "email": users["ownerB"]["email"],
        },
        "companionC": {
            "user_id": users["companionC"]["user_id"],
            "email": users["companionC"]["email"],
            "linked_owner_id": users["companionC"]["linked_owner_id"],
        },
    }


# ---- CLI ------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Build and parse the CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "IDOR DAST probe for the Shekel dev Flask instance. "
            f"Hard-coded to {EXPECTED_BASE_URL}; any other URL is "
            "refused."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=EXPECTED_BASE_URL,
        help=(
            "Target URL; must be literally "
            f"{EXPECTED_BASE_URL} (defaulted). The argument exists "
            "so a human can confirm the target; other values are "
            "rejected at startup."
        ),
    )
    parser.add_argument(
        "--credentials", type=Path, required=True,
        help=(
            "Path to the JSON credentials file produced by "
            "seed_dast_users.py."
        ),
    )
    parser.add_argument(
        "--output", type=Path, required=True,
        help="Path for the output JSON (idor-probe.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Script entry point. Returns the process exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    sentinel_block: dict[str, Any]
    try:
        sentinel_block = assert_safety_rails(args.base_url)
    except (RuntimeError, requests.ConnectionError, requests.Timeout) as exc:
        logger.error("Safety rails tripped: %s", exc)
        atomic_write_json(args.output, {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "sentinel": {"verdict": "ABORT", "error": str(exc)},
            "requests": [],
            "summary": {
                "total_requests": 0, "pass": 0, "fail": 0,
                "by_attacker": {}, "critical_findings": [],
                "high_findings": [], "medium_findings": [],
            },
        })
        return 2

    try:
        credentials = load_credentials(args.credentials)
    except (json.JSONDecodeError, OSError, RuntimeError) as exc:
        logger.error("Could not load credentials: %s", exc)
        return 2

    try:
        records, summary = run_probe(args.base_url, credentials)
    except (requests.ConnectionError, requests.Timeout, RuntimeError) as exc:
        logger.error("Probe execution failed: %s", exc)
        return 2

    payload: dict[str, Any] = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "sentinel": sentinel_block,
        "seed_summary": _seed_summary_for_output(credentials),
        "requests": _records_to_json(records),
        "summary": _summary_to_json(summary),
    }
    atomic_write_json(args.output, payload)
    logger.info(
        "Probe finished: %d requests, %d pass, %d fail. Output: %s",
        summary.total_requests, summary.passed, summary.failed,
        args.output,
    )
    # Non-zero exit on any failure so callers (e.g. Phase 3 regression)
    # can gate merges on a clean probe run.
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
