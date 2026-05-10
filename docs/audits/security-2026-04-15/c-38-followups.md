# C-38 follow-ups: out-of-scope issues observed during implementation

**Origin:** Surfaced while implementing commit C-38 (Docker secrets
migration + env file cleanup + DevConfig pragma + placeholder
rejection).  Documented here for later, separate follow-ups -- none
of them were fixed in C-38 itself per the in-scope rule (CLAUDE.md
rule 6).

**Severity ordering:** Issue 2 (test isolation) is the most likely
to mask real bugs over time -- a test that passes in isolation but
fails in the suite (or vice versa) erodes confidence in the suite as
a regression detector.  Issue 1 (duplicate code) is the largest
cleanup but the lowest blast radius.  Issue 3 (env.example URI
templates) is the smallest but the most security-adjacent.

**Re-verified on 2026-05-10.**  Verification notes are inlined per
issue below; the original observations are preserved so the audit
trail remains coherent.  Two issues' recommendations were narrowed
or replaced after the second pass:

- Issue 1's per-period materialisation extraction was found to
  violate transfer invariant #4 ("All mutations go through the
  transfer service") and has been removed from the recommended
  scope.  The `budget_variance ↔ calendar_service` period-scan
  pairing was also over-claimed -- period-id and date-range scoping
  don't share a clean helper shape.
- Issue 3's Approach B was replaced with a stronger Approach C
  (sentinel token + app-level rejection in `_runtime_database_uri()`).
  Neither DevConfig nor ProdConfig currently validates DATABASE_URL
  for placeholder values, so the original Approach B would have
  surfaced as a generic Postgres "authentication failed" rather than
  an app-level error.

Issue 2a was re-verified but did not reproduce on HEAD; the
architectural concern remains and the recommended fix stands as
proactive hardening.  See each issue's "Verification (2026-05-10)"
inline note for details.

---

## Issue 1: Pylint R0801 duplicate-code warnings (~80 instances)

### Symptom

Running `pylint app/ --fail-on=E,F` reports a sustained 9.50/10
score, the deficit driven entirely by R0801 ("similar lines in N
files") warnings against many module pairs.  The score has been
stable at 9.50 across recent commits including C-34, C-35, C-36,
C-37, C-38 -- each commit has been confirming "no new warnings" but
the floor itself never recovers.

### Where it lives

A representative sample of the highest-volume duplications, captured
from `pylint app/ --fail-on=E,F` after commit C-38:

**Status tags below were added on 2026-05-10 after a line-by-line
re-read of each cited range.**  `VERBATIM` = identical except for
trivial substitutions (model name, log message text); `NEAR-VERBATIM`
= same structure with cosmetic differences; `PARTIAL` = only a subset
of the cited range duplicates; `PARTIALLY FALSE` / `FALSE` =
re-verification disagreed with the original claim.

| Module pair | Lines | Status + Nature of duplication |
|---|---|---|
| `app.services.recurrence_engine:78-107` ↔ `app.services.transfer_recurrence:66-86` | ~30 | **[NEAR-VERBATIM]** Recurrence-rule expansion preamble (look up template, validate effective range, return early on inactive).  Only log message text differs. |
| `app.services.recurrence_engine:328-363` ↔ `app.services.transfer_recurrence:207-243` | ~35 | **[PARTIALLY FALSE]** Per-period materialisation with structured logging and ownership scoping.  *Verification:* transfer side delegates to `transfer_service.create_transfer()` / `restore_transfer()` to materialise shadow pairs atomically; transaction side mutates Transaction rows directly.  Naive merge would route shadow creation outside the transfer service, violating CLAUDE.md transfer invariant #4. |
| `app.services.recurrence_engine:391-396` ↔ `app.services.transfer_recurrence:262-267` | ~6 | **[VERBATIM]** Cross-user blocked-access `log_event(...)` block.  Confirmed by direct read: only `model="Transaction"` vs `"Transfer"` and the owner_id lookup path (`txn.pay_period.user_id` vs `xfer.user_id`) differ. |
| `app.services.budget_variance_service:441-485` ↔ `app.services.calendar_service:200-267` | ~45 | **[FALSE]** Period-windowed transaction scan with `joinedload(status)` + soft-delete filter + scenario filter.  *Verification:* period-id scoping (budget_variance) vs date-range scoping (calendar) isn't interchangeable; a shared helper would force polymorphic dispatch (`date.between(...) OR pay_period_id IN (...)`) that obscures both call sites. |
| `app.services.budget_variance_service:441-475` ↔ `app.services.spending_trend_service:529-562` | ~35 | **[PARTIAL]** Same period-windowed scan, different downstream aggregation.  *Verification:* only the ~8-line preamble truly duplicates; the variance side groups by category while the trend side fits linear regression per category. |
| `app.services.budget_variance_service:247-252` ↔ `app.services.dashboard_service:620-625` | ~6 | **[VERBATIM]** The five-line `Transaction.account_id == ... && scenario_id == ... && pay_period_id == ... && is_deleted.is_(False)` filter clause.  Pure filter duplication. |
| `app.routes.investment` ↔ `app.routes.loan` | many pairs | **[VERIFIED (sampled)]** Transfer-template creation, scenario lookup, recurrence-rule pop-and-set blocks.  ~80-100 lines copy-pasted across the contribution/payment transfer paths; loan-specific concerns (escrow, amortization, ARM re-amortization) and investment-specific concerns are embedded, which is exactly why item 4 below recommends deferral. |
| `app.routes.templates` ↔ `app.routes.transfers` | many pairs | **[VERIFIED (sampled)]** The bulk of the recurrence form-handling, including the `flash("Invalid recurrence pattern.", "danger")` + `pattern_id_str = data.pop("recurrence_pattern", None)` rituals across create / edit / delete handlers. |
| `app.models.transaction` ↔ `app.models.transfer` | several pairs | **[VERBATIM]** `is_override = db.Column(...)`, `is_deleted = db.Column(...)`, and the `account_id = db.Column(...)` block with the standard ondelete/index/comment pattern. |
| `app.models.transaction_template` ↔ `app.models.transfer_template` | several pairs | **[VERIFIED]** `__table_args__ = ({"schema": "budget"}, ...)` headers and the `start_period_id`/`is_active` columns. |

The full list is roughly 80 R0801 entries, mostly in:
- `app/services/recurrence_engine.py` ↔ `app/services/transfer_recurrence.py`
- `app/services/budget_variance_service.py` ↔ `app/services/{calendar,spending_trend,dashboard}_service.py`
- `app/routes/investment.py` ↔ `app/routes/loan.py`
- `app/routes/templates.py` ↔ `app/routes/transfers.py`
- The `app/models/` cluster around transaction-shaped tables.

### Why it matters

DRY (CLAUDE.md coding-standards.md "DRY and SOLID") is a project
requirement, not a guideline.  Verbatim-or-near-verbatim
duplication carries three concrete risks:

1. **Drift.**  A bug fix in one copy is not propagated to the
   sibling.  The recurrence engine vs. transfer recurrence pair is
   especially load-bearing -- the cross-user `log_event(...)` block
   on each side must stay synchronized for the security audit's
   evidence trail to remain coherent.  A fix to one side that
   forgets the other shows up as a missing audit-log entry, which is
   a Phase 6 audit-finding-class regression.
2. **Review burden.**  Reading two near-identical 35-line blocks
   doubles the mental work for every reviewer of either side, every
   time either side changes.
3. **Testability.**  A shared helper can be unit-tested once.  Two
   copies need two tests, and the tests themselves duplicate the
   setup.

### Recommended next step (revised 2026-05-10)

The original recommendation suggested extracting the per-period
materialisation pair, which the re-verification flagged as a
transfer-invariant risk.  The narrowed scope below preserves the
DRY win without touching the load-bearing shadow-creation path.

A dedicated commit (call it `refactor(services): extract cross-user
defense helper + budget-schema mixin + soft-delete column mixin`)
that:

1. **Lifts ONLY the cross-user blocked-access `log_event(...)` block**
   (the `[VERBATIM]` row above: `recurrence_engine:391-396` ↔
   `transfer_recurrence:262-267`) into
   `app/services/_recurrence_common.py` as a single helper, e.g.
   `log_cross_user_blocked(logger, *, user_id, model, pk, owner_id)`.

   **Do NOT also extract the per-period materialisation** (the
   `[PARTIALLY FALSE]` row above).  The transfer side delegates to
   `transfer_service.create_transfer()` / `restore_transfer()` to
   keep parent + shadow pair creation atomic, which is load-bearing
   per CLAUDE.md transfer invariant #4 ("All mutations go through
   the transfer service").  A naive merge would route shadow
   creation outside the service boundary.

   Likewise leave the `[NEAR-VERBATIM]` preamble (`78-107` ↔
   `66-86`) in place: the ~30-line saving is not worth the
   cognitive overhead of a helper whose body would have to thread
   `Transfer` vs `Transaction` model selection through every call
   site, and the log_event message text differs in a way that
   matters for the audit-evidence trail.

2. **Lifts the genuinely shared bits of the period-windowed scan
   only.**  The `budget_variance ↔ calendar_service` row was
   over-claimed (period-id vs date-range scoping is not
   interchangeable -- see the `[FALSE]` tag above).  Two cleaner
   targets remain:

   - The ~8-line preamble shared between `budget_variance` and
     `spending_trend` (the `[PARTIAL]` row).
   - The 6-line filter clause shared between `budget_variance` and
     `dashboard_service` (the second `[VERBATIM]` row).

   An `app/services/_period_window.py` module hosting two small
   helpers -- one for the period-scoped query preamble, one for
   the "settled expenses in a period" filter -- is the right
   scope.  Do NOT try to unify the calendar-service date-range
   path under the same helper; the polymorphism would obscure
   both call sites.

3. **For the model pairs**, add two SQLAlchemy mixins:
   `BudgetSchemaMixin` exposing `__table_args__ = ({"schema":
   "budget"},)` and `SoftDeleteOverridableMixin` exposing the
   verbatim `is_override` and `is_deleted` columns (plus their
   `server_default=db.text("false")` and `nullable=False`
   declarations).

   Caveat: each table still has its own indexes and CHECK
   constraints, so the mixin's `__table_args__` must concatenate
   (not overwrite) the per-table tuple.  The right pattern is a
   `@declared_attr` on the mixin that returns a tuple-flattened
   result; SQLAlchemy then merges the mixin's args with the
   subclass's per-table args.  Verify the merge with a smoke test
   on `Transaction.__table_args__` before and after the mixin
   landing.

4. **Route-level duplication** (templates/transfers, investment/
   loan) remains deferred.  The route handlers are request-shaped,
   not data-shaped -- a hasty extraction risks pulling the
   request/response cycle into a service-layer helper and
   violating the Routes -> Services -> Models layering invariant.
   Loan-specific concerns (escrow, amortization, ARM
   re-amortization) and investment-specific concerns are embedded
   in their respective route handlers; merging them would
   over-parameterize a shared helper that has to know about both.

Estimated cost (revised): ~1.5-2 hours for the narrowed
service-layer extractions (items 1 and 2); another 1-2 hours for
the model mixin work (item 3); route-level cleanup (item 4)
remains deferred.

After the cleanup the pylint score should recover to ~9.65-9.75
/10 from the current 9.50 floor.  (The original estimate of 9.85+
assumed the per-period materialisation merge, which the
re-verification ruled out as unsafe.)

---

## Issue 2: Test isolation regressions

Two tests pass in isolation but fail in the broader test suite (or
the reverse).  Both reproduced consistently when first observed
and are NOT caused by C-38 -- verified at the time by `git stash`-ing
my C-38 changes and re-running: both failed the same way on
`8035aff` (the parent of the C-38 commit).  See each issue's
"Verification (2026-05-10)" note for the second-pass observation
-- 2a no longer reproduces on HEAD, 2b still does.

### Issue 2a: `test_429_includes_retry_after_header` fails in the route batch

**File:** `tests/test_routes/test_errors.py:64`
**Class:** `TestErrorPages`
**Reproduces:**

```bash
# Passes in isolation:
pytest tests/test_routes/test_errors.py::TestErrorPages::test_429_includes_retry_after_header -v
# 1 passed

# Fails inside the broader d-i route batch:
pytest tests/test_routes/test_d* tests/test_routes/test_e* tests/test_routes/test_g* tests/test_routes/test_h* tests/test_routes/test_i* --tb=short -q
# 391 passed, 1 failed (the 429 test)
```

**Verification (2026-05-10, HEAD = `257625d`):** the failure no
longer reproduces in the cited batch.  Only one code commit (C-38,
`dc70ce0`) sits between `8035aff` and HEAD, so the divergence
likely reflects environment/order variance rather than an
intentional fix.  The architectural fragility the doc identifies
remains: `app/config.py:339-345` self-documents that `test_errors`
is one of "the few tests that flip `RATELIMIT_ENABLED` back on,"
which is the brittle pattern.  The recommended `limiter.reset()`
hardening should still land as a pre-emptive measure -- a future
test reordering (e.g. a new test added to `test_d*` that flips
`limiter.enabled = True` and forgets to reset) would reintroduce
the same false-confidence failure mode.

**Symptom:** the test expects `response.status_code == 429` after 6
POSTs to `/login`, but in the suite it gets a different status code
(presumably 200 or 302 because the rate-limit counter for `/login`
is already past the threshold from a sibling test, so the loop
short-circuits before the 6th attempt registers).

**Root cause hypothesis:** `app.extensions.limiter` is a session-
scoped Flask-Limiter instance.  Its in-memory counters are NOT
reset between tests despite `RATELIMIT_ENABLED=False` in TestConfig.
When the test toggles `limiter.enabled = True` mid-suite, it
inherits whatever counter state the previous test left behind.  The
test's own cleanup block (`limiter.enabled = False` at line 91)
disables the limiter but does not clear the storage.

**Recommended fix:** add a `limiter.reset()` call to the test's
setup AND teardown.  Also worth investigating whether the
session-scoped limiter is the right fixture scope -- a function-
scoped limiter via a fixture that re-initialises the storage on
every test would close the underlying gap.

**Priority:** low (revised 2026-05-10, was "medium").  Reframed as
proactive hardening: the cited failure does not currently
reproduce on HEAD, but the underlying pattern (session-scoped
limiter + tests that flip `limiter.enabled = True` without
resetting storage) is the false-confidence failure mode the
testing-standards.md "Zero Tolerance for Failing Tests" rule is
designed to prevent.  The fix is cheap (~30 minutes) and lands
ahead of the next test reordering that would reintroduce the
failure.

### Issue 2b: `test_test_config_forces_memory_backend` fails in isolation

**File:** `tests/test_integration/test_rate_limiter.py:446`
**Class:** `TestStorageBackendIsConfigDriven`
**Reproduces:**

```bash
# Fails in isolation:
pytest tests/test_integration/test_rate_limiter.py::TestStorageBackendIsConfigDriven::test_test_config_forces_memory_backend -v
# 1 failed (limiter._storage is NoneType)

# Passes inside the integration suite:
pytest tests/test_integration/ --tb=short -q
# 220 passed
```

**Verification (2026-05-10):** still fails in isolation with
`limiter._storage` `NoneType`; still passes in the integration
suite.  Recommended fix (add the `app` fixture parameter and call
`limiter.init_app(app)` inside the test body) is unchanged and
correct.

**Symptom:** `limiter._storage` is `None` when the test imports it
directly.  The test's intent is to assert that `TestConfig.
RATELIMIT_STORAGE_URI = "memory://"` produces a `MemoryStorage`
instance.  In isolation, `app.extensions.limiter` has not yet been
attached to any Flask app, so `_storage` is unset.

**Root cause hypothesis:** the limiter's `_storage` is materialised
only after the limiter is bound to a Flask app via `init_app()`.
Earlier tests in the integration suite trigger a Flask app
construction that happens to call `limiter.init_app(app)` -- by the
time the storage-test runs, the storage attribute is populated.  In
isolation, the integration suite's `conftest` may set up a session
fixture but the limiter never sees it before the test body runs.

**Recommended fix:** the test should explicitly bind the limiter to
the test app within its body, e.g.:

```python
def test_test_config_forces_memory_backend(self, app):
    from app.extensions import limiter
    limiter.init_app(app)  # ensure storage is materialised
    assert isinstance(limiter._storage, MemoryStorage), ...
```

The `app` fixture is already session-scoped and uses `TestConfig`
so this would be a 1-line behavior fix.

**Priority:** medium.  Order-dependent tests are a maintenance
hazard -- a future restructure of `tests/test_integration/conftest.py`
or test ordering could flip this from passes-in-suite to fails-in-
suite without anyone noticing.

---

## Issue 3: `.env.example` URI examples still embed `shekel_pass`

### Symptom

`.env.example` lines 25 and 28:

```bash
DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5432/shekel
TEST_DATABASE_URL=postgresql://shekel_user:shekel_pass@localhost:5433/shekel_test
```

Both URIs embed `shekel_pass` as the example password.  An operator
who copies `.env.example` to `.env` and edits only the standalone
`POSTGRES_PASSWORD=` line (which C-38 emptied) inherits an active
`DATABASE_URL` that targets `shekel_pass` -- the publicly-known
historical default that audit finding F-109 was specifically opened
to remove.

### Why this is out of scope for C-38

Audit finding F-109 specifies `.env.example:28` as the line carrying
`POSTGRES_PASSWORD=shekel_pass` (the line numbers shifted
post-C-34's documentation expansion, but the audit's intent is the
standalone assignment).  The URI examples that EMBED `shekel_pass`
inside connection URIs were not called out in F-109 and addressing
them was outside the C-38 commit's scope (CLAUDE.md rule 6).

### Why it matters anyway

The standalone `POSTGRES_PASSWORD=shekel_pass` is the easy hit --
F-109 closed it.  The URI form is the harder hit because:

1. An operator who reads only the comments above the standalone
   line ("REQUIRED in production... empty by default") may believe
   they have closed the placeholder by setting POSTGRES_PASSWORD
   to a real value, not realising that DATABASE_URL still embeds
   `shekel_pass`.
2. The shared-mode prod compose file overrides `DATABASE_URL` to
   reconstruct from `${POSTGRES_PASSWORD}`, so the embedded
   `shekel_pass` is bypassed in shared-mode prod.  But the
   bundled-mode quickstart (`docker-compose.yml` alone) and the
   local-dev `flask run` path read DATABASE_URL directly from the
   operator's `.env`.
3. The audit's threat model treats `shekel_pass` as a leaked
   credential.  A copy-paste of `.env.example` to `.env` that
   touches only the standalone line still ships a leaked credential
   to whatever PostgreSQL the developer points at.

### Recommended fix

Two viable approaches:

**Approach A: comment out the URI examples by default.**  The
DevConfig already falls back to peer-auth `postgresql:///shekel` if
`DATABASE_URL` is unset.  Commenting the examples out preserves
them as documentation but prevents accidental activation:

```bash
# DATABASE_URL=postgresql://shekel_user:<your-postgres-password>@localhost:5432/shekel
# TEST_DATABASE_URL=postgresql://shekel_user:<your-postgres-password>@localhost:5433/shekel_test
```

Pro: the operator must actively opt in to the URI override.
Con: a developer who relies on password-auth must remember to
uncomment.

**Approach B: replace `shekel_pass` with a placeholder marker
inside the URI.**  E.g.:

```bash
DATABASE_URL=postgresql://shekel_user:<your-postgres-password>@localhost:5432/shekel
TEST_DATABASE_URL=postgresql://shekel_user:<your-postgres-password>@localhost:5433/shekel_test
```

Pro: the URI is still active by default, so peer-auth-skipping
operators don't need to uncomment anything.  The `<your-postgres-
password>` form fails connection cleanly with a clear error pointing
at the placeholder.
Con: a copy-paste-without-edit operator gets a connection failure
instead of a working dev DB; they have to read the docs to figure
out the substitution.  This is the better failure mode but not the
nicer onboarding experience.

**Verification (2026-05-10) -- Approach B is weaker than originally
stated.**  I read `ProdConfig.__init__` (`app/config.py:449-490`):
it validates `SECRET_KEY` against `_KNOWN_DEFAULT_SECRETS` and
length, but the only DATABASE_URL check is non-emptiness
(line 482-483).  DevConfig and TestConfig perform no URI validation
at all.  So `<your-postgres-password>` would silently pass every
config layer and surface as a generic Postgres "password
authentication failed" at connect time -- not the app-level
"placeholder detected" message Approach B's pro column implies.
This re-verification motivates Approach C below.

**Approach C: sentinel token + app-level rejection (recommended,
added 2026-05-10).**  Replace `shekel_pass` in the URI examples
with a sentinel value (`REPLACE-ME-WITH-YOUR-POSTGRES-PASSWORD`)
AND add an explicit rejection in the URI-resolution path so the
failure surfaces as a clear app-level `ValueError` rather than a
generic Postgres "password authentication failed" at connect time.

```bash
DATABASE_URL=postgresql://shekel_user:REPLACE-ME-WITH-YOUR-POSTGRES-PASSWORD@localhost:5432/shekel
TEST_DATABASE_URL=postgresql://shekel_user:REPLACE-ME-WITH-YOUR-POSTGRES-PASSWORD@localhost:5433/shekel_test
```

Implementation note: `app/config.py` resolves URIs in two places.
`_runtime_database_uri()` (lines 250-284) is used by DevConfig
(line 323) and -- indirectly via the `DATABASE_URL` env var --
ProdConfig.  `TestConfig.SQLALCHEMY_DATABASE_URI` (lines 332-334)
calls `os.getenv("TEST_DATABASE_URL", ...)` directly.  The
sentinel-rejection helper must be called from BOTH paths, or the
test URI form will not be validated.

The cleanest pattern is a `_reject_sentinel(uri, *, var_name)`
helper that follows the existing `SECRET_KEY` placeholder
rejection at lines 468-476 and raises `ValueError` with a message
naming the sentinel and pointing at `.env.example`.  Two
candidate invocation points:

- Wrap `_runtime_database_uri()`'s return value with a sentinel
  check, so DevConfig/ProdConfig get coverage on class
  definition.
- Either wrap the TestConfig URI assignment in the same helper
  (cleanest) or move the assignment into a `TestConfig.__init__`
  that calls the helper.

Test: add a config-layer test that monkey-patches
`os.environ["DATABASE_URL"]` to a sentinel-bearing URI and
asserts `ValueError` with a message mentioning the sentinel and
`.env.example`.  Add the symmetric test for `TEST_DATABASE_URL`.

Pro: clear, app-level error with named remediation.  Operator
never gets a generic Postgres "authentication failed" -- the
message points at the sentinel by name and tells them to edit
`.env.example`.  Loud, fail-fast, follows the existing C-38
placeholder-rejection pattern in `ProdConfig.__init__`.
Con: small config-layer change plus a test, not a one-line
`.env.example` edit.  The added cost is justified by the
clearer failure mode -- the prior posture (silent acceptance of
a placeholder) is exactly the kind of false-confidence path the
audit is designed to close.

**Recommendation (revised 2026-05-10):** Approach C.  The
sentinel-rejection helper makes the failure mode app-level (loud)
rather than database-level (silent until connect), follows the
existing C-38 placeholder-rejection pattern, and closes the
F-109 URI form completely.  Approach A (commenting out) is kept
above as a fallback for operators who object to the config-layer
change.

### Related cleanup: `scripts/verify_backup.sh` (added 2026-05-10)

Out of scope of the original F-109 finding, but on the same
`shekel_pass`-hardcoded-fallback thread that Approach C closes:
`scripts/verify_backup.sh:236, 243` still embed `shekel_pass` as
fallback values.

- Line 236:
  `db_password=$(docker exec "${DB_CONTAINER}" printenv POSTGRES_PASSWORD 2>/dev/null || echo "shekel_pass")`
  -- the `|| echo "shekel_pass"` branch fires when the live DB
  container does not expose `POSTGRES_PASSWORD` (e.g. the
  container is down or the secret was rotated out of the env
  channel).  In that branch, the script silently constructs a URL
  using the known leaked credential.
- Line 243:
  `local verify_url="postgresql://${PGUSER}:shekel_pass@localhost:5432/${VERIFY_DB}"`
  -- this line ignores `${db_password}` entirely and unconditionally
  uses the leaked credential.  Almost certainly a copy-paste from
  an earlier draft that pre-dates the `db_password` variable.

Both should be replaced as part of the Approach C commit so the
F-109 thread closes cleanly:

- Line 236's fallback should `exit 1` (or call a `die` helper) with
  a clear "POSTGRES_PASSWORD must be discoverable from the live DB
  container; ensure the container is running and the env channel
  is populated" message.
- Line 243 should reference `${db_password}` rather than re-hard
  -coding `shekel_pass`.

Add a shell test (or extend the existing `tests/test_scripts/`
coverage) that asserts the script errors out cleanly when
`POSTGRES_PASSWORD` is not discoverable, rather than silently
constructing a leaked-credential URL.

### Priority (revised 2026-05-10)

Medium (was "Low").  The original tag assumed Approach B (a
one-line `.env.example` edit, fold into the next opportunistic
touch).  Approach C requires a `_reject_sentinel()` helper in
`app/config.py` plus a test, plus the `scripts/verify_backup.sh`
cleanup, plus the `.env.example` edit -- a small but dedicated
commit, not opportunistic.  The F-109 thread closes cleanly after
this.

---

## Tracking (rebuilt 2026-05-10)

| Issue | Status (2026-05-10) | Severity | Recommended commit type | Estimated effort |
|---|---|---|---|---|
| 1a (cross-user defense helper + budget-schema mixin + soft-delete column mixin) | Verified safe to extract | Medium | `refactor(services): extract cross-user defense helper + budget-schema mixin + soft-delete column mixin` | ~1.5-2 hr (services) + 1-2 hr (models mixin) |
| 1b (per-period materialisation merge, calendar/period-window unification) | **Do NOT extract** without a separate design discussion -- transfer invariant risk + period-vs-date-range divergence | -- | (deferred) | -- |
| 2a (`test_429_includes_retry_after_header`) | Not reproducing on HEAD; architectural concern remains | Low (proactive) | `test(routes): reset limiter storage between tests` | 30 min |
| 2b (`test_test_config_forces_memory_backend`) | Verified, fails in isolation | Medium | `test(integration): bind limiter to app in storage assertion` | 15 min |
| 3 (.env.example URI + `verify_backup.sh` fallback) | Approach C recommended (was "Approach B, Low") | Medium | `chore(config): sentinel-token rejection in DATABASE_URL and TEST_DATABASE_URL` | 30-45 min (sentinel helper + DevConfig/TestConfig hookups + sentinel-rejection tests + `.env.example` edit + `verify_backup.sh` edit) |
