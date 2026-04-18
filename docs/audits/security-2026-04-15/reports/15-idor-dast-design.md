# 15 -- IDOR DAST Probe: Design

**Session:** S4 (Section 1M)
**Author:** Claude (Opus 4.7, 1M context) under developer supervision
**Status:** Design -- awaiting approval before code is written
**Scope:** `scripts/audit/seed_dast_users.py` plus `scripts/audit/idor_probe.py`,
output to `docs/audits/security-2026-04-15/scans/idor-probe.json`.

---

## 1. Goal

Prove, by black-box HTTP probing of a running dev Flask instance, that every
user-scoped route in the Shekel application returns the correct blocking status
code when an unauthorized attacker tries to access another user's resource.

CLAUDE.md pins the expected behavior: **"404 for both 'not found' and 'not
yours'"**. Static review of `auth_helpers.py` callers in Sessions S1-S3 proved
the code *looks* right. This probe proves the actual running HTTP endpoints
*behave* right.

**Pass criterion:** every request from an unauthorized attacker (unauthenticated,
owner B, or companion C) against a resource owned by owner A returns the
expected blocking status -- no `200`, no `500`, no `403` where `404` is
required. Every deviation is recorded as a finding.

**Fail criterion:** any single deviation, each recorded with the full
request/response pair and a severity label.

---

## 2. Target environment

### Hard constraints on BASE_URL

The probe refuses to start if any of these checks fail:

1. Scheme is `http` (not `https`; HTTPS would mean we somehow resolved to a
   Cloudflare Tunnel hostname or the prod Nginx, which never serve dev).
2. Hostname resolves to `127.0.0.1` or is literally the string `127.0.0.1` (the
   only host that the dev compose binds port 5000 on; prod and the end-user
   `shekel-app` container never bind to host 127.0.0.1:5000).
3. Port is `5000`, matching the `5000:5000` line in `docker-compose.dev.yml`.
4. URL does not contain the substring `prod`, `tunnel`, `cloudflare`, or a
   public hostname.

### Sentinel checks at startup

Before sending any probe request, the probe makes a single `GET /health` and
asserts:

1. Status is `200` with JSON body `{"status": "healthy", ...}`.
2. Response `Server:` header starts with `Werkzeug/`. Production runs behind
   Gunicorn + Nginx (`Server: nginx/1.25.x`), so a Werkzeug banner is uniquely
   the dev Flask dev-server. The GHCR image used by `shekel-prod-app` and the
   `shekel-app` end-user container both run Gunicorn.
3. `docker ps --filter name=shekel-dev-app` returns a running container.
4. `docker ps --filter name=shekel-prod-app` ALSO returns a running container
   (if prod has been accidentally taken down, the audit environment is in an
   unexpected state and we should stop and investigate rather than run the
   probe blindly).

If any of the four checks fails, the probe prints a specific error and exits
with a non-zero status. No `--force` flag overrides this -- if the environment
is off, a human must investigate.

### Authoritative BASE_URL

Single source of truth:

    BASE_URL = "http://127.0.0.1:5000"

Hard-coded as a module constant, validated by the URL assertion at startup,
and accepted on the command line only with this exact value (any other value
causes the probe to refuse).

---

## 3. Seed data

### Users (3 total)

All users are created by `scripts/audit/seed_dast_users.py` using the
application's ORM inside a Flask app context. This bypasses the `/register`
rate limit (3/hour) and `/settings/companions` CSRF handshake, and produces
deterministic IDs and credentials the probe can read from a generated file.

| User | Role | Linked to | Email | Password |
|---|---|---|---|---|
| ownerA | owner | -- | `ownerA@audit.local` | `DastOwnerA!2026` |
| ownerB | owner | -- | `ownerB@audit.local` | `DastOwnerB!2026` |
| companionC | companion | ownerA | `companionC@audit.local` | `DastCompC!2026` |

The helper writes these credentials to
`scripts/audit/.dast-credentials.json` (gitignored). The probe reads from
that file. The file is recreated on every seeder run.

### ownerA's resources (the attack target)

The seeder creates these for ownerA so every user-scoped model has at least
one ID to probe:

| Model | Count | Notes |
|---|---|---|
| Account (Checking) | 1 | Auto-created by `register_user()` |
| Account (HYSA) | 1 | Needed for `/accounts/<id>/interest` routes |
| Account (Mortgage) | 1 | Needed for `/accounts/<id>/loan/*` routes |
| Account (401k) | 1 | Needed for `/accounts/<id>/investment/*` routes |
| Category | 24 | Auto-created by `register_user()` |
| Scenario | 1 | Auto-created baseline |
| PayPeriod | 3 | Needed for `/pay-periods/<id>/carry-forward` and transaction FKs |
| SalaryProfile | 1 | Needed for `/salary/<id>/*` routes |
| SalaryRaise | 1 | Needed for `/salary/raises/<id>/*` routes |
| PaycheckDeduction | 1 | Needed for `/salary/deductions/<id>/*` routes |
| SavingsGoal | 1 | Needed for `/savings/goals/<id>/*` routes |
| PensionProfile | 1 | Needed for `/retirement/pension/<id>/*` routes |
| TransactionTemplate | 1 | Needed for `/templates/<id>/*` routes |
| Transaction | 3 | One projected, one done, one with entries. Needed for `/transactions/<id>/*` |
| TransferTemplate | 1 | Needed for `/transfers/<template_id>/*` template routes |
| Transfer (instance) | 1 | Needed for `/transfers/instance/<xfer_id>/*` routes |
| TransactionEntry | 1 | Needed for `/transactions/<txn_id>/entries/<entry_id>` routes |
| LoanParams | 1 | Pre-populated so `/loan/refinance` / `/loan/payoff` don't 500 on missing params |
| InterestParams | 1 | Pre-populated for HYSA interest routes |
| InvestmentParams | 1 | Pre-populated for 401k routes |
| EscrowComponent | 1 | Needed for `/loan/escrow/<component_id>/delete` |

### ownerB and companionC's resources

ownerB gets the default `register_user()` set (1 checking, 24 categories, 1
baseline scenario, tax config) -- enough to log in and navigate. The probe
does not attack ownerB's resources, only uses ownerB as an attacker identity.

companionC gets no additional resources. Companions are read-only over their
linked owner's data via `/companion/` and have no owner-scoped resources of
their own.

---

## 4. Probe matrix -- expected status per (attacker, verb)

### Attacker: unauthenticated (no session cookie, no CSRF token)

For every owner-only route, the probe sends the HTTP request with no cookies
and no CSRF token. Expected outcomes:

| Verb | Expected status | Why |
|---|---|---|
| GET | `302` to `/login?next=...` | `@login_required` redirects anonymous users to the login page |
| POST / PATCH / DELETE | `400` (CSRF error) OR `302` (login redirect) | Flask-WTF's `CSRFProtect` rejects unsafe methods without a token; if CSRF doesn't fire first, `@login_required` redirects to login |

The probe accepts either `302` or `400` as a PASS for state-changing verbs.
Any other status (especially `200`, `403`, `404`, or `500`) is a FAIL.

### Attacker: ownerB (authenticated, attacking ownerA's resources)

ownerB has a valid session cookie (from `/login`). The probe includes a valid
CSRF token (fetched from a GET of any form page on ownerB's session). For
every route that takes one of ownerA's IDs, expected:

| Verb | Expected status | Why |
|---|---|---|
| GET | `404` | `get_or_404()` / `get_owned_via_parent()` return `None`, route calls `abort(404)` |
| POST / PATCH / DELETE | `404` | Same helper fires before any mutation |

`403` is a **medium-severity FAIL** -- per CLAUDE.md, `403` would leak the
existence of the resource. `200` is a **critical FAIL** (direct IDOR). `500`
is a **high FAIL** (unhandled exception could leak state). `302` to a page
that actually shows victim data is a critical FAIL; `302` to the dashboard or
login is a PASS if it matches a documented redirect path.

### Attacker: companionC (authenticated companion linked to ownerA)

companionC has a valid session cookie. For every owner-only route (which has
`@require_owner` on top of ownership checks) targeting ownerA's IDs:

| Verb | Expected status | Why |
|---|---|---|
| GET (owner-only route) | `404` | `@require_owner` fires, calls `abort(404)` |
| POST / PATCH / DELETE (owner-only route) | `404` | Same |
| GET `/companion/` | `200` | Companion's own landing page, legitimate |
| GET `/companion/period/<invalid_id>` | `404` | Period doesn't belong to companion's linked owner |

Note: companionC is *legitimately* linked to ownerA, so the `/companion/`
blueprint exposes read-only views of ownerA's companion-visible transactions.
That's expected. The IDOR test is about *owner-only* routes being denied.

---

## 5. Coverage list -- every user-scoped route

Every route under `app/routes/` that takes a user-scoped integer ID is
enumerated below. Routes that do not take an ID (list pages, form pages,
create endpoints without path params) are out of scope for IDOR because they
filter by `current_user.id` automatically -- there is no victim ID to pass.
Those routes are tested for "does it render without 500" but not for IDOR.

### accounts (`app/routes/accounts.py`)

| Route | Methods | target model | Notes |
|---|---|---|---|
| `/accounts/<int:account_id>/edit` | GET | Account | |
| `/accounts/<int:account_id>` | POST | Account | Update |
| `/accounts/<int:account_id>/archive` | POST | Account | |
| `/accounts/<int:account_id>/unarchive` | POST | Account | |
| `/accounts/<int:account_id>/hard-delete` | POST | Account | |
| `/accounts/<int:account_id>/inline-anchor` | PATCH | Account | HTMX |
| `/accounts/<int:account_id>/inline-anchor-form` | GET | Account | HTMX |
| `/accounts/<int:account_id>/inline-anchor-display` | GET | Account | HTMX |
| `/accounts/<int:account_id>/true-up` | PATCH | Account | HTMX |
| `/accounts/<int:account_id>/anchor-form` | GET | Account | HTMX |
| `/accounts/<int:account_id>/anchor-display` | GET | Account | HTMX |
| `/accounts/<int:account_id>/interest` | GET | Account (HYSA) | |
| `/accounts/<int:account_id>/interest/params` | POST | Account (HYSA) | |
| `/accounts/<int:account_id>/checking` | GET | Account (Checking) | |
| `/accounts/types/<int:type_id>` | POST | AccountType | Ref table; not user-scoped but included for completeness (only owners can modify) |
| `/accounts/types/<int:type_id>/delete` | POST | AccountType | Same |

### loan (`app/routes/loan.py`)

| Route | Methods | target model |
|---|---|---|
| `/accounts/<int:account_id>/loan` | GET | Account |
| `/accounts/<int:account_id>/loan/setup` | POST | Account |
| `/accounts/<int:account_id>/loan/params` | POST | Account |
| `/accounts/<int:account_id>/loan/rate` | POST | Account |
| `/accounts/<int:account_id>/loan/escrow` | POST | Account |
| `/accounts/<int:account_id>/loan/escrow/<int:component_id>/delete` | POST | Account + EscrowComponent |
| `/accounts/<int:account_id>/loan/payoff` | POST | Account |
| `/accounts/<int:account_id>/loan/refinance` | POST | Account |
| `/accounts/<int:account_id>/loan/create-transfer` | POST | Account |

### investment (`app/routes/investment.py`)

| Route | Methods | target model |
|---|---|---|
| `/accounts/<int:account_id>/investment` | GET | Account |
| `/accounts/<int:account_id>/investment/growth-chart` | GET | Account |
| `/accounts/<int:account_id>/investment/params` | POST | Account |
| `/accounts/<int:account_id>/investment/create-contribution-transfer` | POST | Account |

### templates (`app/routes/templates.py`)

| Route | Methods | target model |
|---|---|---|
| `/templates/<int:template_id>/edit` | GET | TransactionTemplate |
| `/templates/<int:template_id>` | POST | TransactionTemplate |
| `/templates/<int:template_id>/archive` | POST | TransactionTemplate |
| `/templates/<int:template_id>/unarchive` | POST | TransactionTemplate |
| `/templates/<int:template_id>/hard-delete` | POST | TransactionTemplate |

### transfers (`app/routes/transfers.py`)

| Route | Methods | target model |
|---|---|---|
| `/transfers/<int:template_id>/edit` | GET | TransferTemplate |
| `/transfers/<int:template_id>` | POST | TransferTemplate |
| `/transfers/<int:template_id>/archive` | POST | TransferTemplate |
| `/transfers/<int:template_id>/unarchive` | POST | TransferTemplate |
| `/transfers/<int:template_id>/hard-delete` | POST | TransferTemplate |
| `/transfers/cell/<int:xfer_id>` | GET | Transfer |
| `/transfers/quick-edit/<int:xfer_id>` | GET | Transfer |
| `/transfers/<int:xfer_id>/full-edit` | GET | Transfer |
| `/transfers/instance/<int:xfer_id>` | PATCH | Transfer |
| `/transfers/instance/<int:xfer_id>` | DELETE | Transfer |
| `/transfers/instance/<int:xfer_id>/mark-done` | POST | Transfer |
| `/transfers/instance/<int:xfer_id>/cancel` | POST | Transfer |

### transactions (`app/routes/transactions.py`)

| Route | Methods | target model |
|---|---|---|
| `/transactions/<int:txn_id>/cell` | GET | Transaction |
| `/transactions/<int:txn_id>/quick-edit` | GET | Transaction |
| `/transactions/<int:txn_id>/full-edit` | GET | Transaction |
| `/transactions/<int:txn_id>` | PATCH | Transaction |
| `/transactions/<int:txn_id>/mark-done` | POST | Transaction |
| `/transactions/<int:txn_id>/mark-credit` | POST | Transaction |
| `/transactions/<int:txn_id>/unmark-credit` | DELETE | Transaction |
| `/transactions/<int:txn_id>/cancel` | POST | Transaction |
| `/transactions/<int:txn_id>` | DELETE | Transaction |
| `/pay-periods/<int:period_id>/carry-forward` | POST | PayPeriod |

### entries (`app/routes/entries.py`)

| Route | Methods | target model |
|---|---|---|
| `/transactions/<int:txn_id>/entries` | GET | Transaction (entry list) |
| `/transactions/<int:txn_id>/entries` | POST | TransactionEntry |
| `/transactions/<int:txn_id>/entries/<int:entry_id>` | PATCH | TransactionEntry |
| `/transactions/<int:txn_id>/entries/<int:entry_id>/cleared` | PATCH | TransactionEntry |
| `/transactions/<int:txn_id>/entries/<int:entry_id>` | DELETE | TransactionEntry |

### salary (`app/routes/salary.py`)

| Route | Methods | target model |
|---|---|---|
| `/salary/<int:profile_id>/edit` | GET | SalaryProfile |
| `/salary/<int:profile_id>` | POST | SalaryProfile |
| `/salary/<int:profile_id>/delete` | POST | SalaryProfile |
| `/salary/<int:profile_id>/raises` | POST | SalaryProfile + SalaryRaise |
| `/salary/raises/<int:raise_id>/delete` | POST | SalaryRaise |
| `/salary/raises/<int:raise_id>/edit` | POST | SalaryRaise |
| `/salary/<int:profile_id>/deductions` | POST | SalaryProfile + PaycheckDeduction |
| `/salary/deductions/<int:ded_id>/delete` | POST | PaycheckDeduction |
| `/salary/deductions/<int:ded_id>/edit` | POST | PaycheckDeduction |
| `/salary/<int:profile_id>/breakdown/<int:period_id>` | GET | SalaryProfile + PayPeriod |
| `/salary/<int:profile_id>/breakdown` | GET | SalaryProfile |
| `/salary/<int:profile_id>/projection` | GET | SalaryProfile |
| `/salary/<int:profile_id>/calibrate` | GET | SalaryProfile |
| `/salary/<int:profile_id>/calibrate` | POST | SalaryProfile |
| `/salary/<int:profile_id>/calibrate/confirm` | POST | SalaryProfile |
| `/salary/<int:profile_id>/calibrate/delete` | POST | SalaryProfile |

### savings (`app/routes/savings.py`)

| Route | Methods | target model |
|---|---|---|
| `/savings/goals/<int:goal_id>/edit` | GET | SavingsGoal |
| `/savings/goals/<int:goal_id>` | POST | SavingsGoal |
| `/savings/goals/<int:goal_id>/delete` | POST | SavingsGoal |

### categories (`app/routes/categories.py`)

| Route | Methods | target model |
|---|---|---|
| `/categories/<int:category_id>/edit` | POST | Category |
| `/categories/<int:category_id>/archive` | POST | Category |
| `/categories/<int:category_id>/unarchive` | POST | Category |
| `/categories/<int:category_id>/delete` | POST | Category |

### retirement (`app/routes/retirement.py`)

| Route | Methods | target model |
|---|---|---|
| `/retirement/pension/<int:pension_id>/edit` | GET | PensionProfile |
| `/retirement/pension/<int:pension_id>` | POST | PensionProfile |
| `/retirement/pension/<int:pension_id>/delete` | POST | PensionProfile |

### dashboard (`app/routes/dashboard.py`)

| Route | Methods | target model |
|---|---|---|
| `/dashboard/mark-paid/<int:txn_id>` | POST | Transaction |

### companion (`app/routes/companion.py`)

| Route | Methods | target model |
|---|---|---|
| `/companion/period/<int:period_id>` | GET | PayPeriod (via linked owner) |

### settings (`app/routes/settings.py`) -- companion management, owner-only

| Route | Methods | target model |
|---|---|---|
| `/settings/companions/<int:companion_id>/edit` | POST | User (companion) |
| `/settings/companions/<int:companion_id>/deactivate` | POST | User (companion) |
| `/settings/companions/<int:companion_id>/reactivate` | POST | User (companion) |

**Coverage total:** 79 user-scoped (attacker, route, verb) tuples. Multiplied
by 3 attacker profiles = ~237 probe requests per run. Nonexistent-ID
(`999999`) sentinels add ~10 more.

---

## 6. HTMX handling

Any route that returns an HTMX partial needs to be probed with the
`HX-Request: true` header so the server returns the partial, not the full
page. For IDOR the distinction matters because:

1. A full-page 302 to login looks like a pass, but an HTMX partial that
   accidentally includes victim data is a critical leak that the probe would
   miss without the header.
2. Some HTMX endpoints return 204/200 with an empty body on success -- if
   they return empty on failure too, the probe could misread it as a pass.
   The probe records the full response body excerpt (first 500 bytes) so a
   human can verify.

The probe probes each route once without `HX-Request` and once with it, and
records both results. Routes that are purely HTMX (e.g.
`/accounts/<id>/inline-anchor-form`) are tagged `hx_required: true` in the
output.

---

## 7. Output format

### File: `docs/audits/security-2026-04-15/scans/idor-probe.json`

Single JSON document. Written atomically -- the probe writes to a `.tmp`
file, fsyncs, and renames on successful completion. An aborted run does not
leave a half-written file.

```jsonc
{
  "run_at": "2026-04-17T21:45:00+00:00",
  "base_url": "http://127.0.0.1:5000",
  "sentinel": {
    "url_scheme": "http",
    "url_host": "127.0.0.1",
    "url_port": 5000,
    "health_status": 200,
    "server_header": "Werkzeug/3.0.1 Python/3.12.7",
    "dev_app_container": "shekel-dev-app",
    "prod_app_container_present": true,
    "verdict": "OK"
  },
  "seed_summary": {
    "ownerA": {
      "user_id": 3,
      "email": "ownerA@audit.local",
      "account_ids": [4, 5, 6, 7],
      "category_ids": [25, 26, 27, ...],
      "period_ids": [1, 2, 3],
      "template_id": 1,
      "transaction_ids": [1, 2, 3],
      "transfer_template_id": 2,
      "transfer_instance_id": 1,
      "salary_profile_id": 1,
      "raise_id": 1,
      "deduction_id": 1,
      "savings_goal_id": 1,
      "pension_id": 1,
      "entry_id": 1,
      "escrow_component_id": 1
    },
    "ownerB": {"user_id": 4, "email": "ownerB@audit.local"},
    "companionC": {
      "user_id": 5,
      "email": "companionC@audit.local",
      "linked_owner_id": 3
    }
  },
  "requests": [
    {
      "attacker": "unauth",
      "method": "GET",
      "path": "/accounts/4/edit",
      "target_model": "Account",
      "target_owner": "ownerA",
      "hx_request": false,
      "status": 302,
      "location": "/login?next=%2Faccounts%2F4%2Fedit",
      "body_excerpt": "",
      "expected": [302, 400],
      "verdict": "PASS"
    },
    {
      "attacker": "ownerB",
      "method": "GET",
      "path": "/accounts/4/edit",
      "target_model": "Account",
      "target_owner": "ownerA",
      "hx_request": false,
      "status": 404,
      "body_excerpt": "<h1>Not Found</h1>...",
      "expected": [404],
      "verdict": "PASS"
    }
  ],
  "summary": {
    "total_requests": 237,
    "pass": 237,
    "fail": 0,
    "by_attacker": {
      "unauth": {"pass": 79, "fail": 0},
      "ownerB": {"pass": 79, "fail": 0},
      "companionC": {"pass": 79, "fail": 0}
    },
    "critical_findings": [],
    "high_findings": [],
    "medium_findings": []
  }
}
```

### Findings severity mapping

| Observation | Severity |
|---|---|
| Attacker got `200` on GET/POST/PATCH/DELETE of victim's resource | Critical |
| Attacker got `302` to a page that rendered victim data | Critical |
| Attacker got `500` (unhandled exception, potential info leak) | High |
| Attacker got `403` where `404` was required (info leak: resource exists) | Medium |
| Probe could not exercise a route (e.g. 500 for ownerA too, dependency missing) | Info (coverage gap) |

---

## 8. Safety rails (hard asserts before any probe request)

All implemented in the probe's startup sequence. The probe will not send its
first real HTTP request until every one of these passes.

1. `BASE_URL == "http://127.0.0.1:5000"` exactly (literal string compare).
2. `--base-url` CLI argument, if supplied, must match the literal value
   above. The probe does not accept `--base-url` overrides that point
   anywhere else; the flag exists only so a human can confirm the target.
3. `GET /health` returns `200` AND the response `Server:` header starts
   with `Werkzeug/`.
4. `docker ps --filter name=shekel-dev-app --filter status=running` lists
   exactly one container.
5. `docker ps --filter name=shekel-prod-app --filter status=running` lists
   exactly one container. (Sanity check: we are not in a state where prod is
   down.)
6. `docker ps --filter name=shekel-prod-` and `--filter publish=5000` must
   NOT match any container. (Sanity check: no prod container is somehow
   bound to the dev port.)
7. Request timeout is `10` seconds. Long hangs are treated as failures, not
   waited out.
8. Per-attacker request count is capped at `500` to prevent runaway loops
   accidentally creating test data in bulk.
9. The seeder creates only the three well-known audit email addresses
   (`ownerA@audit.local`, `ownerB@audit.local`, `companionC@audit.local`).
   If any of those already exist in the DB, the seeder deletes them and
   their owned data before creating fresh ones. It does NOT touch any other
   user.

If any rail trips, the probe prints a labelled error, writes a sentinel-only
JSON output (no `requests` entries), and exits with status 2.

---

## 9. Running and re-running

### First run (after dev compose is clean)

```
docker compose -f docker-compose.dev.yml down -v       # wipe existing dev-db
docker compose -f docker-compose.dev.yml up -d         # start fresh
sleep 20                                               # app boot + seed
docker exec shekel-dev-app flask db upgrade            # ensure latest schema
.audit-venv/bin/python scripts/audit/seed_dast_users.py --credentials-out scripts/audit/.dast-credentials.json
.audit-venv/bin/python scripts/audit/idor_probe.py --base-url http://127.0.0.1:5000 --credentials scripts/audit/.dast-credentials.json --output docs/audits/security-2026-04-15/scans/idor-probe.json
docker compose -f docker-compose.dev.yml down          # tear down (keeps volume for next round if we want)
```

### Re-runs (Phase 3 regression)

Phase 3 of the audit re-runs the probe after every access-control fix. The
same command sequence applies, but with a fresh `down -v` each time so the
rate limiter (in-memory per Flask process) is reset and the DB is clean.

The probe itself is idempotent: a re-run with the same seeder output
produces the same set of probe requests, in the same order, against the
same IDs.

---

## 10. Open questions for the developer

1. Confirm: for the "unauth" attacker, accepting both `302` and `400` as a
   PASS on state-changing verbs is correct. The probe does not send a CSRF
   token as unauthenticated (we have no session to get one from). Either
   Flask-WTF's CSRF check (→ 400) or Flask-Login's `@login_required` (→ 302)
   fires first. Both outcomes prove access was denied, so either is a PASS.

2. Confirm: for the "companionC" attacker against `/companion/` itself,
   `200` is expected (that's companionC's legitimate route). The probe
   records this as an informational note, not a finding.

3. Confirm: the probe does not need to drive the two-step `calibrate` flow
   (`POST /salary/<id>/calibrate` followed by `POST /salary/<id>/calibrate/confirm`).
   Each step is probed independently for the 404 response -- no need to chain
   them as a real user would.

4. Confirm: `/accounts/types/<int:type_id>` and `/accounts/types/<int:type_id>/delete`
   target `AccountType`, which is a reference-table row (not a user-owned
   resource). These are not strictly IDOR but they should still be protected
   (only logged-in owners can modify ref data, if at all). The probe exercises
   them and expects `403`/`404`/`302` for non-owner attackers. If they turn
   out to accept modifications from any authenticated user, that's a separate
   finding class (role boundary).

5. Confirm: the probe should NOT attack resources owned by companionC (who
   owns no user-scoped resources anyway) or ownerB (who is only an attacker
   identity in this design). If you want mutual owner-vs-owner coverage
   (A attacks B, B attacks A), say so -- currently the probe is A-only as
   the victim, matching the existing `test_access_control.py` shape.

6. Confirm: `/dashboard/mark-paid/<int:txn_id>` is an owner-only action that
   mutates a transaction. It should return 404 to ownerB. The probe tests
   this; if it returns 200, that's a Critical IDOR.

---

## 11. What happens next

On your approval of this design:

1. I write `scripts/audit/seed_dast_users.py` first. I show you the full
   file in the chat before running it.
2. I write `scripts/audit/idor_probe.py` second. Full file shown before any
   execution.
3. I run `pylint` on both and fix any E/F findings.
4. I stop before running either script and wait for your "run it."
5. Only after you approve the code do I bring up the dev compose, seed, and
   probe.

No source in `app/`, no tests, no seed scripts, no files outside the
allowlist.
