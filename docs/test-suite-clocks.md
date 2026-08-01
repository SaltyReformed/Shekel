# The test suite's two clocks and its calendar

How the suite defends against time-dependent failures, what the two gates actually check, and the
pitfalls that cost real debugging time on 2026-08-01. Written after five separate defects whose only
trigger was *when* the suite ran.

The short version of the rules is in `.claude/rules/testing.md`; the standards entry is
`docs/testing-standards.md` ("The ambient clock and the ambient calendar"). **This document is the
depth: why the gates exist, what they cannot see, and how to read a failure from either of them.**

---

## 1. Why this exists

Between 2026-07-31 and 2026-08-01, five defects surfaced. Every one of them was found by CI blocking
a merge, never by a test, and every one had a single trigger -- the moment the suite ran:

| finding | trigger | what it did |
|---|---|---|
| N-131 | last days of a month | six cross-page tests failed, ~12 days a year |
| N-132 | any day | fixtures separated events by HOURS against a rule that reads civil DAYS |
| R8 | a "day before today" default | an offset silently became a duplicate of its sibling |
| 2026-08-01 (two loan tests) | the 1st, and the days before the month's first Monday | a fixture promised a "clean past" the calendar decided |
| 2028-02-29 / 2027-06-01 | a leap day; a hard-coded future date going stale | a `ValueError` crash; a pill that silently stops rendering |

Three more were reported as "flaky" and were nothing of the kind. **A test that passes on some days
and fails on others is not flaky. It is a broken test with a schedule.** Treating one as flaky is
how it survives to block a merge at the worst possible moment.

---

## 2. The two clocks

```text
date.today()        -> the PROCESS timezone  (whatever TZ the interpreter inherited)
display_today()     -> America/New_York      (app/utils/dates.py, DISPLAY_TIMEZONE)
```

The application's civil day is `display_today()`. The process clock agrees with it
**only because the deployment pins the timezone**: `deploy/docker-compose.prod.yml` and
`docker-compose.dev.yml` both set `TZ: America/New_York`, and the prod file says why in its own
comment --

> the default is UTC, which flipped `date.today()` to the next day at 20:00 America/New_York [...]
> The app's date logic is naive process-local time throughout, so the process timezone IS the app
> timezone.

That is the whole hazard in one sentence. **Correctness rests on an environment variable**, and any
environment that does not set it computes different calendar dates. CI is exactly such an
environment.

### Where it bites

Any value a test builds that the application will later compare against *its own* "today":

- an `entry_date` posted to a route -- `entry_service._reject_future_entry_date` refuses anything
  after `display_today()` (ruling R-M), so a process date one day ahead is simply a **400**;
- a pay-period window that must contain the day `get_current_period` looks for;
- an anchor's `observed_on`;
- any assertion on a date the app derived.

### The rule

> **Whenever a test builds a date the application will compare against its own "today", use
> `display_today()`.**

### The pitfall that makes it invisible

The two clocks agree in a dev shell (the host is Eastern) and in production (TZ pinned). They
disagreed in CI **only between 00:00 and 03:59 UTC** -- four hours a night. So the failure was
unreproducible by anyone who did not happen to run at 20:00-23:59 Eastern, which is precisely why
three of them were filed as flakes.

### Anti-fix

**Do not pin CI's timezone to `America/New_York`.** It makes the symptom vanish and leaves the
coupling in place, ready for the next environment that differs. The fix is always to use the app's
clock in the test.

---

## 3. Gate 1 -- the clock gate (per PR, free)

`.github/workflows/ci.yml`'s `lint-and-test` job sets:

```yaml
TZ: Pacific/Kiritimati
```

Kiritimati is UTC+14, so the process date runs a day ahead of the app's for
**eighteen hours out of every twenty-four**. A `date.today()` / `display_today()` mix now fails on
most runs instead of on a four-hour nightly window. No second job, no extra minutes.

Reproduce locally:

```bash
TZ=Pacific/Kiritimati ./scripts/test.sh
```

**It is not 100%.** For the remaining six hours the two dates coincide and the property is not
exercised. No single timezone can disagree with another all day; this is the best a free, single-run
gate can do. Treat a green run as strong evidence, not proof.

---

## 4. Gate 2 -- the calendar sweep (weekly)

`.github/workflows/calendar-sweep.yml` runs the whole suite as if today were each of five awkward
positions, `fail-fast: false` so one red date does not hide the others:

| date | why |
|---|---|
| 2028-02-29 | leap day -- `date.replace(year=+1)` raises here |
| 2026-12-31 | last day of a year |
| 2027-01-01 | first day of a year -- a 13-month window fails to reach the next year from here |
| 2026-11-30 | month end (finding N-131's shape) |
| 2026-09-01 | first of a month -- an installment falls on today |

Locally, one date at a time:

```bash
SHEKEL_FAKE_TODAY=2028-02-29 ./scripts/test.sh
```

The mechanism is a session-scoped autouse fixture in `tests/conftest.py` (`_calendar_sweep`), a
**no-op unless the variable is set**, wrapping the session in `time_machine.travel(..., tick=True)`.

- `tick=True`, not frozen: a frozen clock makes every `created_at` in a session identical, and the
  fold's assertion ordering (ruling R-DH -- two assertions sharing a civil day apply in *recording*
  order) then has no order to read.
- The instant is built at **midday in `DISPLAY_TIMEZONE`**, so the faked civil day is unambiguous
  and cannot straddle midnight in either direction.

### Two fixture shapes that shipped, and their fixes

- **Never `date.replace(year=...)`.** On 29 February it raises
  `ValueError: day 29 must be in range 1..28 for month 2 in year 2029`. Use
  `app.utils.dates.add_months(d, 12)`, which clamps to 2029-02-28. (`app/` has never used
  `.replace(year=`; this was test-only.)
- **Never hard-code a date because it is "in the future".** `target_date=date(2027, 6, 1)` stops
  being in the future on 2027-06-01, and the test depending on it starts failing that morning.
  Derive it: `add_months(display_today(), 10)`.

---

## 5. What the sweep CANNOT see -- the two-clock artifact

**`time-machine` moves Python's clock. It cannot move PostgreSQL's.**

```text
created_at / updated_at   server_default=db.func.now()   app/models/mixins.py
paid_at                   db.func.now()                  app/services/status_seam.py
entry_date                server_default CURRENT_DATE    app/models/transaction_entry.py
```

All three are evaluated **in the database**, which knows nothing about the fake. So under a faked
date a server-stamped row carries the **real** instant, and any test comparing it against a
Python-derived date fails by the offset between them.

Measured directly, under `SHEKEL_FAKE_TODAY=2026-09-02`:

```text
opening created_at  (POSTGRES clock) = 2026-08-01 12:39 UTC
opening observed_on (PYTHON clock)   = 2026-09-01
```

An impossible production state, produced entirely by the instrument.

### The marker

Those tests carry `@pytest.mark.server_clock` (registered in `pytest.ini`), and the sweep job
deselects them with `-m "not docker and not server_clock"`. Two of them assert the database's clock
**on purpose** -- the `CURRENT_DATE` server default and the audit trigger's `executed_at` -- which
no amount of Python faking can satisfy.

> **The marker is a statement about the instrument, never a way to quiet a failure.** Every marked
> test still runs in ordinary CI. A test earns the marker only after its failure has been traced to
> the two clocks.

### The trap -- read this before adding a marker

**"It fails at some dates but not others" is NOT sufficient evidence that a failure is real.**

`test_two_same_day_trueups_reconcile` fails at 2026-09-07 and passes at 2026-09-01, which looks
exactly like a genuine calendar bomb. It is not. Its fixture computes `origin + 30 days` from the
**Postgres-stamped** `created_at` while the opening's business date comes from the **faked Python**
clock; the two only diverge once the faked date moves far enough, so the failure is
*date-dependent and still an artifact*. This cost real time, and the pattern-based shortcut is what
caused it.

**Trace every sweep failure to the two clocks before classifying it.** The tell is a date offset
equal to the distance between the faked date and the real one.

---

## 6. Operational pitfalls

### The test template goes stale across branches

`scripts/build_test_template.py` builds `shekel_test_template` at the **current branch's** migration
head. Switching between branches that differ by a migration and running the suite produces a flood
of errors that look nothing like a migration problem:

```text
psycopg2.errors.UndefinedColumn: column "observed_on" of relation "account_anchor_history"
    does not exist
```

**Rebuild after every branch switch that crosses a migration**, and be suspicious of any run with
thousands of errors:

```bash
TEST_DATABASE_URL="postgresql://shekel_user:shekel_pass@127.0.0.1:5433/shekel_test" \
TEST_ADMIN_DATABASE_URL="postgresql://shekel_user:shekel_pass@127.0.0.1:5433/postgres" \
    .venv/bin/python scripts/build_test_template.py
```

This nearly produced a wrong conclusion: a sweep run reporting *4,979 errors* was read as "the
Postgres-clock gap makes the instrument unusable", when it was a stale template and the real number
was 20.

`TEST_TEMPLATE_DATABASE=<name>` builds a second template under a different name -- useful for
comparing a branch against `origin/main` in a worktree without destroying the branch's own template.

### Comparing against `main`

Use `git worktree add`, never `git checkout` -- a checkout reverts the working tree and discards
uncommitted work. The worktree needs its own template (see `TEST_TEMPLATE_DATABASE` above) and
explicit `TEST_DATABASE_URL` / `TEST_ADMIN_DATABASE_URL`, because `scripts/test.sh` derives the
admin DSN from the test DSN and neither is inherited.

### Environment

- `IDLE_TIMEOUT_MINUTES=720` when a concurrent session has left `.env` at `10080`; otherwise
  `test_stale_activity_rejected` fails suite-wide.
- Do not run the full suite concurrently with anything else against the same cluster -- the
  per-worker DB names collide (see `docs/testing-standards.md`).

---

## 7. How to read a failure

```text
Test fails in CI but not locally
  └─ Is the CI job's TZ skewed?  (yes -- Pacific/Kiritimati, by design)
     └─ Does the test build a date the app compares against display_today()?
        └─ YES  -> real defect. Use display_today() in the test.
        └─ NO   -> keep going; it is not the clock gate.

Test fails in the calendar sweep
  └─ Does it compare a server-stamped timestamp against a Python-derived date?
     └─ YES  -> instrument artifact. Trace it, then @pytest.mark.server_clock.
     └─ NO   -> real calendar coupling. Make the fixture construct the property
                it needs on every calendar day, rather than deriving it from today.

Thousands of errors, not failures
  └─ Almost certainly a stale test template. Rebuild it before reading anything else.
```

---

## 8. Current state

- Full suite: **7,687 passed / 0 failed**, on the normal clock and under `TZ=Pacific/Kiritimati`.
- Calendar sweep: **7,662 passed / 0 failed** at 2028-02-29, 2027-01-01 and 2026-11-30 (25
  `server_clock` tests deselected, all of which still run in ordinary CI).
- `app/` carries no `.replace(year=` and was never exposed to the leap-day crash.

### Known gaps, stated so they are not mistaken for covered

1. **The clock gate covers ~18 hours of every 24**, not all of it (section 3).
2. **Only the forward skew is exercised.** `TZ=Pacific/Kiritimati` puts the process date *ahead* of
   the app's. The reverse -- process date *behind* -- has never been run, and a coupling that only
   breaks in that direction would not be caught.
3. **The sweep cannot fake PostgreSQL** (section 5). Closing that would need `libfaketime` inside
   the test-db container, which is invasive; the marker is the accepted alternative.
4. **Five calendar positions, not all of them.** A coupling keyed to something else -- a specific
   weekday, a DST transition -- would need its date added to the matrix.
