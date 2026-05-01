# 15 -- IDOR DAST Probe: Findings

**Session:** S4 (Section 1M)
**Date of probe run:** 2026-04-17 22:51 UTC (dev compose)
**Probe script:** `scripts/audit/idor_probe.py`
**Seeder script:** `scripts/audit/seed_dast_users.py`
**Raw results:** `docs/audits/security-2026-04-15/scans/idor-probe.json`
**Design doc:** `docs/audits/security-2026-04-15/reports/15-idor-dast-design.md`

---

## 1. Executive summary

The probe sent **270 HTTP requests** across three attacker profiles
(unauthenticated, ownerB, companionC) against ownerA's resources on a
freshly-seeded dev Flask instance. **All 270 requests returned a
secure status** (302, 400, 404, or a legitimate 200 for companionC's
own routes). **Zero IDOR failures were found.**

| Attacker | Probes | Pass | Fail | Criticals | Highs | Mediums |
|---|---|---|---|---|---|---|
| unauthenticated | 90 | 90 | 0 | 0 | 0 | 0 |
| ownerB (owner role) | 90 | 90 | 0 | 0 | 0 | 0 |
| companionC (companion, linked to ownerA) | 90 | 90 | 0 | 0 | 0 | 0 |
| **Total** | **270** | **270** | **0** | **0** | **0** | **0** |

**Conclusion: Shekel's cross-user authorization holds under black-box
probing.** No 200, no 500, no 403 was observed on any
cross-user-accessible path. Every probe request was blocked, either at
`@login_required` (unauthenticated), `@require_owner`
(companion-as-owner), or the ownership check inside the handler
(owner-as-other-owner).

One **compliance finding** remains (see Section 3): the codebase
uses a mixed convention for "not found or not yours" -- 128 cross-user
requests returned `404` (canonical per CLAUDE.md), but 51 returned
`302 redirect with flash` to a listing page. Both shapes are secure,
but the inconsistency deviates from CLAUDE.md's stated rule and
should be unified.

---

## 2. Findings

### 2.1 Critical

**None.**

### 2.2 High

**None.**

### 2.3 Medium

**None.**

### 2.4 Low / Compliance

**F-1M-01: Mixed 302/404 response convention for cross-user access.**

CLAUDE.md line 100 states:

> **Security response rule: 404 for both "not found" and "not yours."**

In practice, the codebase implements this rule via two patterns:

- **Pattern A (canonical 404)**. Used by 128 of the 180 cross-user
  probe requests. Example from
  `app/routes/accounts.py:466-473`:

  ```python
  @accounts_bp.route("/accounts/<int:account_id>/inline-anchor", methods=["PATCH"])
  @login_required
  @require_owner
  def inline_anchor_update(account_id):
      """HTMX endpoint: update anchor balance inline from the accounts list."""
      account = db.session.get(Account, account_id)
      if account is None or account.user_id != current_user.id:
          return "Not found", 404
  ```

- **Pattern B (302 redirect with flash)**. Used by 51 of the 180
  cross-user probe requests. Example from
  `app/routes/accounts.py:198-206`:

  ```python
  @accounts_bp.route("/accounts/<int:account_id>/edit", methods=["GET"])
  @login_required
  @require_owner
  def edit_account(account_id):
      """Display the account edit form."""
      account = db.session.get(Account, account_id)
      if account is None or account.user_id != current_user.id:
          flash("Account not found.", "danger")
          return redirect(url_for("accounts.list_accounts"))
  ```

  The redirect target is the user's own list page
  (`/accounts`, `/templates`, `/salary`, `/savings`, etc.), which
  contains no victim data. Information disclosure is bounded -- the
  attacker learns that their request was rejected and is sent to
  their own index. A 302 here does not distinguish "exists"
  from "does not exist", so the CLAUDE.md rationale for 404 (not
  leaking existence) is satisfied in spirit even though the
  response shape differs.

**Security impact.** None. Both patterns deny access and avoid
leaking victim data. The project's own integration tests accept
either shape -- `tests/test_integration/test_access_control.py:28-42`
defines `_assert_blocked()` as:

```python
def _assert_blocked(response, msg=""):
    assert response.status_code in (302, 404), (
        f"Expected 302 or 404 but got {response.status_code}. "
        f"User B may have accessed User A's resource. {msg}"
    )
```

**Compliance impact.** CLAUDE.md says "404 for both". The codebase
has 51 routes that return 302. This is a visible inconsistency that:

1. Makes it harder for future maintainers to know which pattern to
   follow when adding a new route.
2. Means the assertion in `_assert_blocked` and the CLAUDE.md rule
   are partially out of sync (the test helper already accepts both).
3. Creates 2 code paths to audit for every new access-control change.

**Severity:** Low (inconsistency, no exploitable primitive).

**Recommendation.** Pick one and enforce it globally:

- **Option 1: unify on Pattern A (404).** Use the
  `app/utils/auth_helpers.py::get_or_404` helper everywhere. Delete
  the flash redirects. Update `_assert_blocked` to assert exactly
  `404`. This matches CLAUDE.md's stated rule and tightens the
  contract.
- **Option 2: loosen CLAUDE.md** to say "404 or redirect to a safe
  index page". Update the file and leave the code alone.

Option 1 is preferable because it reduces attacker-visible surface
area and gives a single, auditable contract.

**Non-canonical 302 routes (51 total).** See
`idor-probe.json["summary"]["cross_user_non_canonical_302_routes"]`
for the full list. Grouped by blueprint:

- `accounts.py`: 14 routes (edit, update, archive, unarchive,
  hard-delete, interest detail, interest params, loan dashboard,
  loan setup, loan params, loan create-transfer, investment
  dashboard, investment params, investment create-contribution-
  transfer).
- `templates.py`: 5 routes (edit, update, archive, unarchive,
  hard-delete).
- `transfers.py`: 5 routes (template edit, update, archive,
  unarchive, hard-delete). Note: the `/transfers/instance/<id>/*`
  and `/transfers/cell|quick-edit|full-edit/<id>` routes correctly
  use Pattern A (404).
- `salary.py`: 16 routes (profile edit/update/delete,
  raises-add/delete/edit, deductions-add/delete/edit, breakdown x 2,
  projection, calibrate x 4).
- `savings.py`: 3 routes (edit, update, delete).
- `categories.py`: 4 routes (edit, archive, unarchive, delete).
- `retirement.py`: 3 routes (pension edit, update, delete).
- `companion.py`: 1 route (ownerB GET `/companion/period/<id>`
  redirects owner users back to `/grid` via `_companion_or_redirect()`
  -- that's the intended owner-landing behaviour, not a compliance
  deviation).

---

## 3. Evidence -- raw probe results

The probe wrote a 200 KB JSON document with one record per probe
request at `docs/audits/security-2026-04-15/scans/idor-probe.json`.
Each record has the form:

```jsonc
{
  "attacker": "ownerB",
  "method": "GET",
  "path": "/accounts/7/edit",
  "target_model": "Account",
  "target_owner": "ownerA",
  "hx_request": false,
  "status": 302,
  "location": "/accounts",
  "body_excerpt": "<!doctype html>...",
  "expected": [302, 404],
  "verdict": "PASS",
  "severity": "PASS"
}
```

### 3.1 Pass-shape distribution

| Outcome | Count | Meaning |
|---|---|---|
| `302` to `/login?next=...` | 90 | Unauthenticated GET blocked by `@login_required` |
| `302` to `/login?next=...` (no CSRF) | 0 | Flask-WTF lets unauth POSTs through to login_required first |
| `400` CSRF reject | 0 | Not observed -- login_required fires first |
| `404` direct (canonical) | 128 | Pattern A ownership check |
| `302` redirect with flash (non-canonical) | 51 | Pattern B ownership check (F-1M-01) |
| `200` legitimate | 1 | companionC `GET /companion/period/<ownerA period>` -- the companion's legitimate read of their linked owner's period |

Total across all attackers = 270.

### 3.2 Sanity check: ownerA's own requests

ownerA was NOT an attacker in this run (the happy-path "ownerA
reads ownerA" case is covered by the regular test suite). The
probe only uses ownerA as the victim whose IDs are targeted.

### 3.3 Safety rail record

From `idor-probe.json["sentinel"]`:

```json
{
  "url_scheme": "http",
  "url_host": "127.0.0.1",
  "url_port": 5000,
  "health_status": 200,
  "server_header": "Werkzeug/3.1.8 Python/3.14.3",
  "dev_app_container_running": true,
  "prod_app_container_running": true,
  "verdict": "OK"
}
```

- URL scheme, host, and port match the literal dev compose target.
- Health endpoint 200 with Werkzeug banner confirms the dev Flask
  dev-server (not Gunicorn prod, not the end-user `shekel-app`
  container).
- `shekel-dev-app` was running; `shekel-prod-app` was also running
  (confirming the audit environment was not in a broken state).

---

## 4. Coverage

### 4.1 Routes exercised

82 user-scoped routes across 13 blueprints. The full list lives in
`idor-probe.json["requests"]` with one record per probe request. The
design doc
(`docs/audits/security-2026-04-15/reports/15-idor-dast-design.md`
Section 5) enumerates the coverage table with one row per
(blueprint, route, method, target model).

### 4.2 Routes NOT exercised

The probe explicitly does not attempt the following routes, each
for a concrete reason. These are **not findings**; they are
documented coverage gaps.

- **`/transactions/inline` (POST)**, `/transactions` (POST),
  `/transactions/new/full`, `/transactions/new/quick`,
  `/transactions/empty-cell`, `/transfers/new`, `/transfers/ad-hoc`,
  `/transfers` (POST), `/templates` (POST), `/accounts/new`,
  `/accounts` (POST), `/salary/new`, `/salary` (POST),
  `/savings/goals/new`, `/savings/goals` (POST),
  `/retirement/pension` (POST), `/categories` (POST),
  `/pay-periods/generate` (GET/POST), `/accounts/types` (POST),
  `/salary/tax-config` (GET/POST), `/salary/fica-config` (POST),
  `/retirement/settings` (POST). These are create/list endpoints
  that do NOT take a user-scoped ID path parameter. They filter by
  `current_user.id` automatically -- there is no victim ID to pass,
  so they are not IDOR targets. (They are still worth testing for
  row-injection via hidden fields, but that is Section 7J /
  business-logic territory, not 1M.)
- **`/auth/*`**. Auth endpoints either require no session (login,
  register) or operate on the current session (logout,
  change-password). Neither is a cross-user IDOR path.
- **`/settings/*` top-level routes** (`GET /settings`,
  `POST /settings`). These are list + update of the current user's
  own settings row; no victim ID.
- **`/dashboard/*`** except `mark-paid/<txn_id>` (which IS probed).
  The rest take no ID.
- **`/obligations`, `/charts`, `/analytics`, `/debt-strategy`,
  `/grid`** -- these are whole-account dashboards scoped to
  `current_user`. No ID path parameter.
- **`/settings/companions` (POST)**. Companion creation. Tested
  indirectly -- if ownerB could create a companion linked to
  ownerA, that would be a role-escalation bug, but that is a
  different attack shape from IDOR on an existing resource and is
  out of scope for 1M.

### 4.3 Data coverage

ownerA was seeded with:

- 4 accounts (Checking + HYSA + Mortgage + 401k), each with any
  required params (InterestParams, LoanParams + EscrowComponent,
  InvestmentParams).
- 24 categories (register\_user default set).
- 1 baseline scenario.
- 3 pay periods.
- 1 salary profile + 1 raise + 1 deduction.
- 1 savings goal.
- 1 pension profile.
- 1 transaction template + 3 transactions + 1 transaction entry.
- 1 transfer template + 1 transfer instance.

Every user-scoped model this probe needs had at least one row in
the dev DB at probe time.

---

## 5. Methodology notes

### 5.1 Expected-status derivation

- **Unauthenticated GET:** `[302]`. Flask-Login redirects anonymous
  users to `/login?next=<path>`.
- **Unauthenticated POST/PATCH/DELETE:** `[302, 400]`. Either
  Flask-WTF CSRF (400) or Flask-Login (302) fires first. Either
  is a block.
- **Cross-user (ownerB or companionC vs ownerA's resource):**
  `[302, 404]`. Matches
  `tests/test_integration/test_access_control.py::_assert_blocked`.
  404 is the canonical rule per CLAUDE.md; 302 is accepted as a
  secondary secure shape (see F-1M-01).
- **companionC on `/companion/period/<ownerA period>`:** `[200]`.
  The companion is linked to ownerA and has documented read
  access to ownerA's companion-visible data.

### 5.2 What would have been a FAIL

- **200** on any cross-user path -> Critical.
- **500** anywhere -> High (probable unhandled exception leaking
  state or stack).
- **403** where 404 was expected -> Medium (info leak: 403 tells
  the attacker the resource exists).
- **302 to a page containing victim data** -> Critical.

None of these were observed.

### 5.3 CSRF and sessions

Dev config has CSRF enabled (no `WTF_CSRF_ENABLED=False` override
in `DevConfig`). The probe fetches a CSRF token from `GET /login`
and includes it in POST/PATCH/DELETE requests for the two
authenticated attackers (ownerB, companionC). The unauth attacker
sends no token, which correctly makes the unauth write cases
indistinguishable between "blocked by CSRF" and "blocked by
login_required" -- both shapes are accepted as PASS.

### 5.4 Rate limits

Dev has `@limiter.limit("5 per 15 minutes")` on `POST /login` and
`"3 per hour"` on `POST /register`. The probe uses **2** login
POSTs (one each for ownerB and companionC). The seeder bypasses
`/register` entirely by going through the ORM, so the register
limit was never touched.

### 5.5 HTMX coverage

Routes that return HTMX partials are marked `hx_request: true` in
the catalog and probed with `Hx-Request: true`. The probe records
`body_excerpt` for every response so a human can verify that an
HTMX partial did not leak victim data (none did).

---

## 6. Re-run instructions (for Phase 3 regression)

After every access-control fix, re-run the probe to confirm no
regression:

```
docker compose -f docker-compose.dev.yml down -v
docker compose -f docker-compose.dev.yml up -d
# wait ~15s for the app
curl -sS http://127.0.0.1:5000/health  # expect 200
docker exec shekel-dev-app python \
    /home/shekel/app/scripts/audit/seed_dast_users.py \
    --credentials-out \
    /home/shekel/app/scripts/audit/.dast-credentials.json
.audit-venv/bin/python scripts/audit/idor_probe.py \
    --base-url http://127.0.0.1:5000 \
    --credentials scripts/audit/.dast-credentials.json \
    --output docs/audits/security-2026-04-15/scans/idor-probe.json
docker compose -f docker-compose.dev.yml down
```

Expected exit code: `0`. Expected summary: 270 total, 270 pass, 0
fail. Any regression (a new `fail > 0`) blocks the PR.

If F-1M-01 gets fixed (all routes unified to 404), the
`cross_user_non_canonical_302_count` in the output should drop to
`0` and `cross_user_canonical_404_count` should rise to `179`.
