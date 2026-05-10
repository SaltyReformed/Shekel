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

| Module pair | Lines | Nature of duplication |
|---|---|---|
| `app.services.recurrence_engine:78-107` ↔ `app.services.transfer_recurrence:66-86` | ~30 | Recurrence-rule expansion preamble (look up template, validate effective range, return early on inactive). |
| `app.services.recurrence_engine:328-363` ↔ `app.services.transfer_recurrence:207-243` | ~35 | Per-period materialisation with structured logging and ownership scoping. |
| `app.services.recurrence_engine:391-396` ↔ `app.services.transfer_recurrence:262-267` | ~6 | Cross-user blocked-access `log_event(...)` block.  Verbatim. |
| `app.services.budget_variance_service:441-485` ↔ `app.services.calendar_service:200-267` | ~45 | Period-windowed transaction scan with `joinedload(status)` + soft-delete filter + scenario filter. |
| `app.services.budget_variance_service:441-475` ↔ `app.services.spending_trend_service:529-562` | ~35 | Same period-windowed scan, different downstream aggregation. |
| `app.services.budget_variance_service:247-252` ↔ `app.services.dashboard_service:620-625` | ~6 | The five-line `Transaction.account_id == ... && scenario_id == ... && pay_period_id == ... && is_deleted.is_(False)` filter clause. |
| `app.routes.investment` ↔ `app.routes.loan` | many pairs | Transfer-template creation, scenario lookup, recurrence-rule pop-and-set blocks. |
| `app.routes.templates` ↔ `app.routes.transfers` | many pairs | The bulk of the recurrence form-handling, including the `flash("Invalid recurrence pattern.", "danger")` + `pattern_id_str = data.pop("recurrence_pattern", None)` rituals across create / edit / delete handlers. |
| `app.models.transaction` ↔ `app.models.transfer` | several pairs | `is_override = db.Column(...)` and the `account_id = db.Column(...)` block with the standard ondelete/index/comment pattern. |
| `app.models.transaction_template` ↔ `app.models.transfer_template` | several pairs | `__table_args__ = ({"schema": "budget"}, ...)` headers and the `start_period_id`/`is_active` columns. |

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

### Recommended next step

A dedicated commit (call it `refactor(services): extract shared
recurrence + period-window helpers`) that:

1. Lifts the recurrence_engine ↔ transfer_recurrence shared shape
   into `app/services/_recurrence_common.py` (or similar) -- one
   helper for the preamble (template lookup + effective range +
   active check), one for the per-period materialisation (with the
   ownership-scoping log_event), one for the cross-user
   access-denied event.
2. Lifts the period-windowed transaction scan
   (budget_variance/calendar/spending_trend/dashboard) into a single
   `app/services/_period_window.py::query_window_transactions()`
   helper that takes `(user_id, account_id, scenario_id,
   first_day, last_day, *, joinedload_status: bool)` and returns the
   eager-loaded query.
3. For the model pairs, evaluate whether a SQLAlchemy mixin (e.g.
   `BudgetSchemaMixin` for the `__table_args__ = {"schema":
   "budget"}` cases) is appropriate.  Caveat: SQLAlchemy mixins for
   `__table_args__` need careful handling around `__tablename__` so
   the shared schema dict is merged rather than overwriting.
4. The route-level duplication (templates/transfers,
   investment/loan) is harder because the route handlers are
   request-shaped, not data-shaped.  Defer this to a later commit
   pending a design discussion -- a hasty extraction risks pulling
   the request/response cycle into a service-layer helper, which
   would violate the Routes -> Services -> Models layering invariant
   in CLAUDE.md.

Estimated cost: 3-5 hours for the service-layer extractions alone
(items 1 and 2), with no behavior change.  The model-mixin work
(item 3) is another 1-2 hours.  Route-level cleanup (item 4) is a
larger discussion and not recommended until items 1-3 land.

After the cleanup the pylint score should recover to 9.85+ /10 from
the current 9.50 floor.

---

## Issue 2: Test isolation regressions

Two tests pass in isolation but fail in the broader test suite (or
the reverse).  Both reproduce consistently and are NOT caused by
C-38 -- verified by `git stash`-ing my C-38 changes and re-running:
both fail the same way on `8035aff` (the parent of the C-38 commit).

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

**Priority:** medium.  The test PASSES in CI when run as a single
file but FAILS when run with siblings -- this is exactly the kind
of false confidence the testing-standards.md "Zero Tolerance for
Failing Tests" rule tries to prevent.

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

**Recommendation:** Approach B.  The placeholder is more
discoverable than a comment and the failure mode (connection error)
is louder than the silent-leaked-credential failure.

### Priority

Low.  The audit closed F-109 with the standalone-line fix; the URI
forms are a quieter sibling of the same finding.  Fold into the
next opportunistic touch of `.env.example` rather than scheduling a
dedicated commit.

---

## Tracking

| Issue | Severity | Recommended commit type | Estimated effort |
|---|---|---|---|
| 1 (duplicate code, services + models layers) | Medium | `refactor(services): extract shared recurrence + period-window helpers` | 3-5 hours (services) + 1-2 hours (models mixin) |
| 2a (test_429 ordering-dep) | Medium | `test(routes): reset limiter storage between tests` | 30 min |
| 2b (test_test_config_forces_memory_backend isolation) | Medium | `test(integration): bind limiter to app in storage assertion` | 15 min |
| 3 (.env.example URI templates) | Low | `chore(env): replace shekel_pass placeholder in DATABASE_URL examples` | 5 min (folded into next .env.example touch) |
