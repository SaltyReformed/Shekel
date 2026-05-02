# `seed_periods` fixture date-coupling issue

**Status:** Open. Action required before **2026-05-22**.
**Severity:** Test infrastructure -- ~30-50 tests will fail loudly when
the deadline passes.
**Discovered:** 2026-05-01 during the C-01 audit follow-up date-coupling
sweep (commits `66082c4`, `4a2b2aa`).

---

## TL;DR

`tests/conftest.py::seed_periods` generates 10 biweekly pay periods
starting at `date(2026, 1, 2)`, spanning Jan 2 to **May 8, 2026**.
Many tests call `pay_period_service.get_current_period(user_id)`, which
returns `None` once `date.today()` is outside any seed_period.  After
**May 22, 2026** (the end of period 9 + a 14-day grace window for the
last period to close), every test that does
`current_period.id` / `.period_index` / etc. raises `AttributeError`.

This is a *loud* failure mode (clear stack trace), not a silent
pass-for-the-wrong-reason.  The audit pass (commits above) already
fixed every silent no-op it found.  The remaining issue is purely a
ticking infrastructure clock.

---

## How the failure presents

When `today` advances past May 22, 2026, tests that look like this
will start failing:

```python
def test_x(self, app, auth_client, seed_user, seed_periods):
    ...
    current_period = pay_period_service.get_current_period(
        seed_user["user"].id,
    )
    # current_period is None once today > May 22, 2026
    past_period = next(
        p for p in seed_periods
        if p.period_index == current_period.period_index - 1  # AttributeError
    )
```

Or:

```python
account = seed_user["account"]
account.current_anchor_period_id = current_period.id  # AttributeError
```

Or routes that call `get_current_period` internally and then index
into the result.

The error is always `AttributeError: 'NoneType' object has no
attribute 'id'` (or similar) -- not a wrong-result silent failure.

---

## Affected tests (incomplete inventory)

A grep for `get_current_period` across `tests/` returns this set
(non-exhaustive; add more as you find them):

- `tests/test_routes/test_grid_regression.py::TestPaydayWorkflowRegression`
  -- 7 tests, all rely on `get_current_period` returning a valid period
  for assertions or for routing transactions to the current period
- `tests/test_routes/test_dashboard.py::TestBillsDisplay::test_dashboard_shows_bills`
  -- uses `get_current_period` with `seed_periods[0]` fallback (the
  fallback is OK -- `seed_periods[0]` always exists)
- `tests/test_routes/test_dashboard.py::TestBillsDisplay::test_dashboard_bills_sorted`
  -- same fallback pattern
- `tests/test_routes/test_dashboard.py::TestBillsDisplay::test_dashboard_hides_paid_bills`
  -- same fallback pattern (already fixed in commit `4a2b2aa`)
- `tests/test_routes/test_carry_forward_preview.py` -- multiple tests
  that POST to `/pay-periods/<id>/carry-forward` or
  `/pay-periods/<id>/carry-forward-preview`; the route resolves the
  target via `get_current_period`
- `tests/test_services/test_companion_service.py::test_period_id_none_returns_current_period`
  -- explicitly calls `get_current_period`; will raise `NotFoundError`
  rather than `AttributeError`
- `tests/test_routes/test_companion_routes.py` -- several tests POST
  to companion routes that use the current period
- `tests/test_routes/test_grid.py` -- several tests rely on
  `get_current_period` to compute a grid offset; uses the
  fallback-or-skip pattern in some places but not all
- `tests/test_routes/test_dashboard_entries.py` -- uses
  `_current_period_for(user_id, seed_periods)` helper that falls back
  to `seed_periods[0]`; OK
- `tests/test_routes/test_mark_paid_entries.py`,
  `tests/test_routes/test_grid_entries.py` -- check for
  `get_current_period` use inside the route handlers they exercise

The 14 silent no-op fixes that already landed are NOT on this list --
they were addressed in the audit.  This document covers only the
remaining loud-failure tests.

To get a precise count, run after May 22:

```bash
pytest tests/test_routes/ tests/test_services/ tests/test_integration/ \
    --tb=line -q 2>&1 | grep "AttributeError\|NotFoundError"
```

---

## Root cause

`seed_periods` is a deliberate fixture: tests that need *specific
calendar dates* (e.g. due_dates in January 2026, year-end summaries
for tax_year=2026, loan origination alignment) want stable dates so
their assertions stay deterministic.  Tests that need *the current
period to exist* want a date relative to today.

Both classes of tests currently share one fixture, which makes the
trade-off impossible to satisfy at the same time.  As long as today
falls within seed_periods range (Jan 2 - May 22, 2026), both
semantics happen to coincide.  Past the deadline they diverge.

---

## Remediation options

### Option A: Make `seed_periods` today-relative

```python
@pytest.fixture()
def seed_periods(app, db, seed_user):
    today = date.today()
    start = today - timedelta(days=4 * 14 + today.weekday())
    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=start,
        num_periods=10,
        cadence_days=14,
    )
    ...
```

**Pros:** All `get_current_period`-dependent tests start working
indefinitely; zero per-test migration.

**Cons:** Tests that hardcode `date(2026, 1, X)` as `due_date` or that
depend on `seed_periods[7]` being April 2026 (e.g. loan tests with
fixed Jan 1, 2026 origination -- the schedule's third month aligns
with seed_periods[7] only when seed_periods[7] is April) silently
start asserting on different calendar months.  This is the
*silent-failure* mode -- worse than the loud failure we're trying to
fix.  Audit work would be needed to catch every place this breaks.

### Option B: Dual fixture (recommended)

Keep `seed_periods` exactly as-is.  Add a sibling fixture for the
`get_current_period`-dependent tests:

```python
@pytest.fixture()
def seed_periods_today(app, db, seed_user):
    """Generate 10 biweekly pay periods so today falls in period 4.

    Use this fixture when the test exercises a code path that calls
    ``pay_period_service.get_current_period()`` (directly or through a
    route).  Use the regular ``seed_periods`` fixture when the test
    asserts on specific calendar dates (due_date filters, year-end
    summaries for tax_year=2026, loan origination alignment).

    Tests should pick one or the other, not both.
    """
    today = date.today()
    # 4 periods past, period 4 contains today, 5 periods future.
    start = today - timedelta(days=today.weekday() + 4 * 14)
    periods = pay_period_service.generate_pay_periods(
        user_id=seed_user["user"].id,
        start_date=start,
        num_periods=10,
        cadence_days=14,
    )
    db.session.flush()
    seed_user["account"].current_anchor_period_id = periods[0].id
    db.session.commit()
    return periods
```

Migrate each affected test from `seed_periods` to `seed_periods_today`.

**Pros:** Cleanly separates the two semantic contracts.  Each test
self-documents which one it needs.  Calendar-date tests are
unaffected.  No silent failures.

**Cons:** Migration cost: ~30-50 test signatures and bodies need a
1-line change each, plus a code review per test to confirm no
calendar-date dependency exists.  Realistic effort: 4-8 hours.

### Option C: Per-test `@patch` of `date.today()`

For each affected test, patch `date.today()` to a value inside
seed_periods range (the pattern already used in
`tests/test_integration/test_data_isolation.py::_freeze_today_to_period_5`,
which freezes today to `date(2026, 3, 20)`):

```python
@patch("app.services.pay_period_service.date")
def test_x(self, mock_date, ...):
    mock_date.today.return_value = date(2026, 3, 20)
    mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
    ...
```

**Pros:** Localized; the test explicitly states "I need today inside
seed_periods range."  No fixture proliferation.  The pattern is
already documented and used in the codebase.

**Cons:** Each test that *transitively* uses today via several
services may need multiple `@patch` decorators (one per import site).
Patch targets are fragile to refactors that change where `date` is
imported.

### Option D: Smart `seed_periods` that snaps forward

```python
@pytest.fixture()
def seed_periods(app, db, seed_user):
    today = date.today()
    fixed_start = date(2026, 1, 2)
    fixed_end = fixed_start + timedelta(days=10 * 14 - 1)

    if today <= fixed_end:
        start = fixed_start  # back-compat
    else:
        # Snap forward in 14-day increments so today is in period 5.
        shift_periods = ((today - fixed_end).days + 14) // 14 + 5
        start = fixed_start + timedelta(days=shift_periods * 14)
    ...
```

**Pros:** Zero per-test migration; identical behavior until May 22.

**Cons:** Same fundamental issue as Option A -- once the snap fires,
calendar-date assumptions silently shift.  Just defers the silent
failure rather than preventing it.  **Not recommended** for a
financial app.

---

## Recommendation

**Option B (dual fixture)** is the right long-term answer.  It cleanly
separates the two distinct contracts and prevents silent failures.

If the deadline is tighter than 4-8 hours of focused work allows,
**Option C (per-test patch)** is acceptable as an interim measure --
the pattern is already in the codebase and each individual change is
mechanical.

**Do not pick A or D.**  Both make the failure silent rather than
loud, which is unacceptable in a financial app where a wrong-but-
green test is worse than a missing test.

---

## Action plan (Option B)

1. Add `seed_periods_today` fixture to `tests/conftest.py` (snippet
   above).  Add an analogous `seed_full_user_data_today` mirror if
   any affected tests use the rich fixture.

2. Run the full suite *after* May 22, 2026, captured into a file:

   ```bash
   pytest tests/ --tb=line -q 2>&1 \
       | tee /tmp/seed_periods_failures.log
   grep -E "AttributeError|NotFoundError" /tmp/seed_periods_failures.log \
       | sort -u
   ```

   The unique stack traces give the exact migration list.

3. For each failing test, change the fixture parameter from
   `seed_periods` to `seed_periods_today`.  Verify the test passes,
   commit per-test or per-class.

4. Audit each migrated test for *new* calendar-date assumptions that
   the today-relative fixture would invalidate (rare; most affected
   tests assert on relative period indices, not absolute dates).

5. Once all failures resolve, run the full directory-split suite gate
   (`tests/test_services`, `tests/test_routes`, `tests/test_models`,
   `tests/test_integration`, `tests/test_adversarial`,
   `tests/test_scripts`) to confirm no regressions in
   non-affected tests.

6. Delete this document.

---

## Action plan (Option C, interim)

1. Run the suite after the failures start; capture the test names.

2. For each failing test, prepend:

   ```python
   @patch("app.services.pay_period_service.date")
   def test_x(self, mock_date, ...):
       mock_date.today.return_value = date(2026, 3, 20)
       mock_date.side_effect = lambda *args, **kw: date(*args, **kw)
       ...
   ```

3. If the test fails again because the production code under test
   imports `date` from a different module, add a second
   `@patch` for that module too.  Stack `@patch` decorators
   bottom-up; the parameters appear in the same order as the
   decorators, top-most-decorator's param last.

4. Commit when the suite passes.

---

## What was already fixed (for reference)

The audit landed 14 fixes for *silent* date-coupling bugs in commits
`66082c4` (C-01) and `4a2b2aa`.  Those tests passed today but would
have either failed silently (passing for the wrong reason) or failed
loudly within 1-3 months.  The list is in those commit messages.

This document covers only the *loud-failure* class that remains.

---

## Why not fix it now?

The audit deliberately scoped to silent failures because:

1. Loud failures are self-documenting -- when they fire, the stack
   trace points exactly at the fix needed.
2. Pre-emptively migrating ~30-50 tests adds risk of breaking
   currently-passing tests, with no immediate payoff.
3. The migration is mechanical once a test fails -- a per-test code
   review is cheap to do reactively.

The document exists so the deadline isn't forgotten.  Set a calendar
reminder for **2026-05-15** (one week before the deadline) to start
work, or schedule a remote agent via `/schedule` to remind you.
