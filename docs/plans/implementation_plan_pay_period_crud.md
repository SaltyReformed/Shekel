# Implementation Plan: Pay Period CRUD + Continuous Rolling Window

Status: PLANNED (design approved for a future implementation session). Not yet started.

## Context

Shekel can **generate** pay periods but cannot edit or delete them (`app/routes/pay_periods.py`
exposes only `GET/POST /pay-periods/generate`). If a user mis-generates, their payday shifts, or
they run out of forward periods, there is no recovery path. This adds the missing lifecycle:
**extend** the schedule forward, **truncate** (delete from the end), **regenerate** a wrong tail,
and an opt-in **continuous rolling window** that keeps N periods always generated ahead of today.

Three facts from the codebase shape the whole design and are non-negotiable:

1. **A pay period is only `(start_date, end_date, period_index)` -- cadence is never stored.**
   `cadence_days` is a generation-time argument (`pay_period_service.py:70`). Extend and continuous
   mode have nothing to continue from unless cadence is persisted. We persist it (new table).
2. **`period_index` must equal calendar order.** The balance engine walks periods by
   `period_index` ascending and assumes that is chronological (`balance_resolver.py:879`). Only
   **tail-append** and **tail-truncate** preserve this. Mid-series date-edit/delete silently
   corrupts as-of balances. This is why we do NOT build per-period date editing.
3. **Recurring transactions are eager real rows; new periods are born empty.**
   `generate_pay_periods` creates blank periods and does NOT call the recurrence engine
   (`recurrence_engine.generate_for_template`, `recurrence_engine.py:131`). Extend/regenerate must
   re-run the engine so new periods get their rent/paychecks. Templates survive deletion, so
   template rows are regenerable; hand-entered ad-hoc rows and overrides are NOT.

## Decisions (locked)

- **Edit model:** Regenerate, **no per-period date editing**. "Fix a mistake" = rebuild the
  unlocked future tail from a new start date + cadence; locked/historical periods stay frozen.
- **Continuous trigger:** **On-request top-up** at grid + dashboard entry (no scheduler exists --
  Gunicorn only). Cheap (one count query), idempotent, concurrency-safe via a Postgres advisory
  lock.
- **Rolling window framing:** **Count-based** -- "always keep N periods ahead of the current one."
- **Config storage:** **New normalized `budget.pay_schedule` table** (one row per user), holding
  cadence + rolling on/off + target count + anchor start date.
- **Destructive-op data safety (recommended default, not separately asked):** **block-and-confirm.**
  Truncate/regenerate refuse if the wiped window holds hand-entered (`template_id IS NULL`) or
  override (`is_override=True`) transactions, until the user passes an explicit
  `confirm_discard=true`. Never silent. The settled/historical/anchor lock (below) is hard-blocking
  and cannot be overridden at all.
- **Repopulation scope (recommended default):** baseline scenario only for MVP; include both
  transaction templates AND transfer templates (so new periods do not silently miss recurring
  transfers). Multi-scenario reserved for later.
- **Full reset (first-time setup correction):** a bounded `reset_pay_periods` rebuilds the WHOLE
  schedule including the anchor period (which Regenerate cannot touch), re-anchoring through the
  existing `anchor_service` and preserving the account + its balance. Offered ONLY when the user has
  no settled transactions. This closes the deferred pay-period reset/realign feature (see
  `docs/audits/pylint-cleanup/deep-quality-hunt.md`, DH-#39 / Batch Y, and the 2026-06-10 design
  feedback note in that register).

## New model: `budget.pay_schedule`

New file `app/models/pay_schedule.py`, class `PaySchedule`, `__table_args__ schema="budget"`.
Mirror the constraint/naming style of `app/models/pay_period.py:12-22`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | Integer PK | |
| `user_id` | Integer FK `auth.users.id` CASCADE | `UserScopedMixin`; `uq_pay_schedule_user` UNIQUE -- one schedule per user |
| `cadence_days` | Integer NOT NULL | `ck_pay_schedule_cadence_range`: BETWEEN 1 AND 365 |
| `anchor_start_date` | Date NOT NULL | canonical first payday / schedule origin |
| `rolling_enabled` | Boolean NOT NULL, server_default false | continuous mode on/off |
| `rolling_target_periods` | Integer NOT NULL, server_default `DEFAULT_PAY_PERIOD_HORIZON` (52) | `ck_pay_schedule_positive_target`: > 0 |
| `created_at` | TIMESTAMPTZ | `CreatedAtMixin` |

- Register `("budget", "pay_schedule")` in `app/audit_infrastructure.py` AUDITED_TABLES (database
  rule: every new `budget` table is audited).
- New service `app/services/pay_schedule_service.py` (Flask-isolated): `get_schedule(user_id)`,
  `upsert_schedule(user_id, cadence_days, anchor_start_date)` (called on generate/regenerate),
  `set_rolling(user_id, enabled, target_periods)` (called from settings), `resolve_cadence(user_id)`
  (schedule cadence, else fall back to inferring from the last period's length for legacy users).

## The operation set

All new mutation logic in new module `app/services/pay_period_admin.py` (keeps destructive paths out
of the heavily-imported read/generate `pay_period_service.py`). All new routes extend
`pay_periods_bp` (`app/routes/pay_periods.py`), `@login_required @require_owner`, **full-POST +
redirect** (matches the existing `generate()` at `pay_periods.py:61-64`; mutations are infrequent
settings-page actions, not HTMX-worthy). The route commits; services flush only.

### 1. Extend (append) -- `POST /pay-periods/extend`
`extend_pay_periods(user_id, num_periods, cadence_days=None, scenario_ids=None)`:
1. `last = get_all_periods(user_id)[-1]` (`pay_period_service.py:197`). Empty -> `ValidationError`
   ("generate your first schedule before extending").
2. cadence = explicit arg, else `pay_schedule_service.resolve_cadence(user_id)`.
3. `next_start = last.end_date + 1 day`; call `generate_pay_periods(user_id, next_start,
   num_periods, cadence_days)` -- reuses index continuation + `_reject_overlapping_batch`
   (`pay_period_service.py:26-67`) wholesale.
4. **Repopulate the new periods only** (see Template repopulation).

### 2. Truncate (delete tail) -- `POST /pay-periods/truncate`
`truncate_pay_periods(user_id, keep_through_index, confirm_discard=False)`:
1. `to_delete = [p for p in get_all_periods(user_id) if p.period_index > keep_through_index]`.
   Empty -> idempotent no-op.
2. Run the **bulk lock classifier** over `to_delete`. Any hard-lock (historical / settled / account
   anchor / rule anchor) -> raise `PayPeriodLocked(blocking_periods)`; delete nothing.
3. If any to-delete period has hand-entered/override rows and `confirm_discard` is False -> raise
   `PayPeriodDiscardRequired(count)`; delete nothing.
4. `db.session.delete(p)` for each. CASCADE clears transactions + transfers (+ both shadows,
   preserving transfer invariants, `transfer.py:123-131`) + anchor history. The anchor/rule guards
   guarantee the `accounts.current_anchor_period_id` NOT-NULL FK is never hit via this path.

### 3. Regenerate -- `POST /pay-periods/regenerate`
`regenerate_pay_periods(user_id, new_start_date, num_periods, cadence_days, confirm_discard=False)`:
= compute the **mutable boundary** (lowest `period_index` that is both unlocked and `start_date >=
today`) -> `truncate_pay_periods(keep_through_index = boundary - 1, confirm_discard)` ->
`generate_pay_periods(new_start_date, num_periods, cadence_days)` -> repopulate ->
`pay_schedule_service.upsert_schedule(...)` with the new cadence/anchor. `new_start_date` must be >
the last RETAINED period's `end_date` (re-checked by `_reject_overlapping_batch`). If a settled/
historical/anchor period sits inside the requested window, the truncate guard refuses (you cannot
rewrite history under a settled paycheck).

### 4. Continuous rolling top-up -- `top_up_rolling_window(user_id, now=None)`
1. Read `pay_schedule_service.get_schedule(user_id)`. `rolling_enabled` false -> return 0 (zero
   extra queries in the common disabled path).
2. One count query: `future = COUNT(pay_periods WHERE user_id=? AND end_date >= today)`. If
   `future >= rolling_target_periods` -> return 0.
3. Wrap steps 2-3 in `SELECT pg_advisory_xact_lock(<hash(user_id)>)` (transaction-scoped, auto-
   released on commit). **Mandatory:** two concurrent grid loads would otherwise both read the same
   `max_index` and append two periods with the SAME `period_index` (different start_dates, so
   `uq_pay_periods_user_start` does NOT catch it) -- breaking the index->calendar bijection the
   resolver depends on.
4. `extend_pay_periods(user_id, deficit = target - future, cadence)`.
- **Trigger:** call from the top of `grid.index` (`app/routes/grid.py`) and the dashboard route,
  guarded `if current_user.is_authenticated and not companion`. These are the only routes that
  consume future periods.

### 5. Full reset (first-time setup correction) -- `POST /pay-periods/reset`
`reset_pay_periods(user_id, new_start_date, num_periods, cadence_days)`. Closes the deferred reset/
realign feature: it rebuilds the WHOLE schedule, INCLUDING the anchor period, which Regenerate
cannot (the `ACCOUNT_ANCHOR` lock). The obstacle is structural, not the lock: the account FK is NOT
NULL and the app is forward-only, so a corrected schedule cannot coexist with the wrong one -- the
reset must delete then recreate, leaving the anchor briefly pointing at nothing. **Bounded for
safety:** refuse unless the user has ZERO settled transactions anywhere (reuse the classifier's
`is_settled` subquery across all the user's transactions). This is a first-time-setup correction,
NOT a tool for users with real history (they use Regenerate). One transaction:
1. `SET CONSTRAINTS <anchor fk> DEFERRED` (defer the anchor FK to commit -- see Migration 1).
2. Capture each account's `current_anchor_balance` (a real dollar value, preserved across the reset).
3. Delete all the user's pay periods. CASCADE clears transactions/transfers/anchor-history; the
   `recurrence_rules.start_period_id` SET NULL nulls any rule anchors.
4. `generate_pay_periods(user_id, new_start_date, num_periods, cadence_days)`.
5. Re-establish each account's anchor on the new schedule: re-point `current_anchor_period_id` using
   the SAME resolution as account creation (`account_service._resolve_anchor_period_id:48-87` -- the
   new period containing today, else the earliest), restore the preserved `current_anchor_balance`,
   and write a fresh origination `AccountAnchorHistory` row. Reuse
   `anchor_service.apply_anchor_true_up` (`anchor_service.py:166-258`) rather than hand-mutating the
   columns.
6. Re-point any `recurrence_rules.start_period_id` nulled in step 3 to the new first period, then
   repopulate templates via the same `populate_periods_from_active_templates` extend/regenerate use.
7. Commit -> the deferred anchor FK validates (every account now points at a live new period).
The anchor lock stays a HARD block for Truncate/Regenerate; reset is the ONLY path that deliberately
re-anchors, and it does so THROUGH `anchor_service`, never around the lock.

### Per-period date editing: deliberately NOT built
Editing any non-tail period's dates reorders calendar position without reordering `period_index`,
making `balance_resolver` pick the wrong period and silently drop transactions from as-of balances,
and can create overlapping spans (nondeterministic `get_current_period`, `pay_period_service.py:150`).
Regenerate serves the real need (a drifted schedule) coherently. Documented non-feature.

## The lock predicate (single reusable classifier)

In `pay_period_admin.py`. `PeriodLockReason` enum: `HISTORICAL`, `SETTLED_TXN`, `ACCOUNT_ANCHOR`,
`RECURRENCE_ANCHOR`. `classify_period_lock(period, as_of=None) -> PeriodLockReason | None` returns
the first reason or None (mutable). Checks, in order:
- `end_date < today` -> HISTORICAL.
- Holds a settled txn: reuse the exact subquery from `templates.py:696-701` --
  `Status.id WHERE is_settled.is_(True)` scalar-subquery, `Transaction.pay_period_id == period.id`,
  `status_id.in_(...)`, `is_deleted.is_(False)` -> SETTLED_TXN.
- `EXISTS(Account WHERE current_anchor_period_id == period.id)` -> ACCOUNT_ANCHOR (the NOT-NULL FK
  landmine, `account.py:73-86`).
- `EXISTS(RecurrenceRule WHERE start_period_id == period.id)` -> RECURRENCE_ANCHOR (SET NULL would
  silently break the rule's offset math, `recurrence_rule.py:63-67`).

Add `classify_periods_bulk(periods)` for the truncate path (4 set-queries + in-Python date check)
to avoid N+1; keep `classify_period_lock` as the single-period source of truth. PROJECTED and
future ad-hoc rows are intentionally NOT lock reasons -- they are the mutable payload (handled by
the separate block-and-confirm `confirm_discard` gate, which is overridable; locks are not).

## Migrations

**Migration 1 (Phase 0, ships alone, prerequisite):** retarget the
`budget.accounts.current_anchor_period_id` FK from `SET NULL` to **`NO ACTION DEFERRABLE INITIALLY
IMMEDIATE`** (NOT plain `RESTRICT`). **Reuse the C-43 helpers** from `migrations/versions/b4b588a49a0c_*`
(`_drop_and_recreate_fk`, `_assert_fk_ondelete`, `_constraint_exists`), extended to emit the
`DEFERRABLE INITIALLY IMMEDIATE` clause. Verify the live constraint name with `\d+ budget.accounts`
first (likely Alembic-default `accounts_current_anchor_period_id_fkey`). Working `downgrade()`
restores `SET NULL` (pure DDL). `Review: <name>, <date>` docstring (constraint recreate). Update the
now-stale comment at `account.py:73-86`.
Why `NO ACTION DEFERRABLE`, not `RESTRICT`: both defuse the latent NOT-NULL/SET-NULL landmine and
(with the app-level anchor lock) block any accidental delete of an anchor period at statement end.
But `RESTRICT` is checked immediately and cannot be deferred -- which would make the atomic full
reset (operation 5) impossible, since it must delete the old anchor period and re-point the anchor
to a new one inside one transaction. `NO ACTION DEFERRABLE` lets ONLY the reset transaction
`SET CONSTRAINTS ... DEFERRED` so the FK is validated at commit (by which point the anchor points at
a fresh period); every other path keeps fail-fast immediate checking. Equally safe, strictly more
flexible.

**Migration 2 (Phase 1):** create `budget.pay_schedule` (named constraints above) + **backfill one
row per user who already has pay periods** (backfills belong in the migration): `cadence_days` =
last period length `(end_date - start_date) + 1`, `anchor_start_date` = first period `start_date`,
`rolling_enabled` = false, `rolling_target_periods` = 52. Users with no periods get no row.
`downgrade()` drops the table.

## Routes, schemas, settings

- Routes (all in `pay_periods_bp`): `POST /pay-periods/extend`, `/truncate`, `/regenerate`, plus
  `POST /pay-periods/schedule` (rolling config). New Marshmallow schemas in
  `app/schemas/validation/pay_periods.py`: `PayPeriodExtendSchema`, `PayPeriodTruncateSchema`,
  `PayPeriodRegenerateSchema`, `PayScheduleSchema` (rolling_enabled bool, rolling_target_periods
  1-260, cadence_days 1-365). Follow `PayPeriodGenerateSchema` (`pay_periods.py:12`) style and the
  422-on-error route pattern.
- `/pay-periods/schedule` writes via `pay_schedule_service.set_rolling(...)` (NOT the
  `_SIMPLE_SETTINGS_FIELDS` allowlist in `settings.py`, since config now lives in `pay_schedule`,
  not `user_settings`).
- On the existing `generate()` route, also `upsert_schedule(user_id, cadence_days, start_date)` so
  cadence is captured authoritatively at first generation.

## Templates / UI

Extend the existing pay-periods settings section. `app/templates/settings/_pay_periods.html` keeps
the generate form and gains a new manage partial `settings/_pay_periods_manage.html`:
- A period list rendering each period's `label` (`pay_period.py` property) with a lock badge
  (mutable / historical / settled / anchor) from the classifier.
- Extend form (count). Truncate form (pick "keep through" period) with a confirm step if discard
  needed. Regenerate form (new start + cadence + count) with the same confirm gate.
- Rolling-window controls: enable toggle + target-count input -> `POST /pay-periods/schedule`.
The settings route `show()` (`settings.py:69`) passes the period list + schedule for the
`pay-periods` section (currently passes only `errors={}`).

## Template repopulation (extend & regenerate)

New helper `recurrence_engine.populate_periods_from_active_templates(user_id, periods,
scenario_ids=None, effective_from=None) -> int`:
1. Baseline scenario via `scenario_resolver.get_baseline_scenario(user_id)` (matches how
   `templates.py:326` populates).
2. Active `TransactionTemplate`s for the user -> `generate_for_template(template, periods,
   scenario.id, effective_from=periods[0].start_date)` for each (`recurrence_engine.py:131`).
3. Active `TransferTemplate`s -> the transfer recurrence engine's generate (keeps the transfer +
   exactly-2-shadows invariant, CLAUDE.md transfer invariants).
Cannot violate the unique partial index `idx_transactions_template_period_scenario`
(`transaction.py`) because new/just-truncated periods are empty, and the engine's
`should_skip_period` (`recurrence_engine.py:161-172`) skips any already-populated period -- so
retried top-ups create nothing.

## Phased rollout (tight scope, no gold-plating)

- **Phase 0:** Migration 1 (anchor FK -> `NO ACTION DEFERRABLE INITIALLY IMMEDIATE`) + model comment
  fix + FK tests. Ships alone.
- **Phase 1 (CRUD):** Migration 2 (`pay_schedule` + backfill); `PaySchedule` model + audit
  registration; `pay_schedule_service`; `pay_period_admin` (classifier + extend + truncate +
  regenerate with block-and-confirm + full reset bounded to no-settled, deferred-FK atomic swap +
  re-anchor via `anchor_service`); `populate_periods_from_active_templates`; routes + schemas;
  manage UI; tests.
- **Phase 2 (continuous):** `top_up_rolling_window` (advisory lock); rolling settings UI; grid +
  dashboard trigger hooks; concurrency/idempotency tests.
- **Out of scope:** per-period date editing; multi-scenario repopulation; carry-forward of ad-hoc
  rows on regenerate; any scheduler/Celery infra. Each is a documented later-if-needed.

## Test plan

New: `tests/test_services/test_pay_period_admin.py`, `tests/test_services/test_pay_schedule_service.py`,
`tests/test_routes/test_pay_period_admin.py`, `tests/test_models/test_anchor_fk_restrict.py`. Reuse
`bare_user`, `bare_periods`, `bare_auth_client` (`conftest.py:831-901`); Decimal-from-string money;
exact assertions; docstrings.
- **Classifier:** future-empty -> None; historical -> HISTORICAL; Paid txn -> SETTLED_TXN
  (PROJECTED-only NOT locked; soft-deleted settled NOT locked); account anchor -> ACCOUNT_ANCHOR;
  rule anchor -> RECURRENCE_ANCHOR; `classify_periods_bulk` == N single calls.
- **Extend:** indices continue contiguously; `next_start == last.end + 1`; cadence arg > schedule >
  inferred; new periods get one PROJECTED row per active `every_period` template; archived template
  -> none; empty schedule -> ValidationError.
- **Truncate:** deletes only `index > K`; CASCADE removes txns + transfers (+ shadows); each hard-
  lock raises `PayPeriodLocked` and deletes nothing; ad-hoc/override without confirm raises
  `PayPeriodDiscardRequired`, with confirm proceeds; idempotent no-op past max index.
- **Regenerate:** locked prefix retained, mutable tail rebuilt + repopulated; refuses when a settled
  period is inside the window; resolver as-of balances still correct after rebuild.
- **Full reset:** refuses when ANY settled transaction exists; on a no-settled schedule, wipes +
  rebuilds all periods INCLUDING the old anchor period; re-points each account's
  `current_anchor_period_id` to the new current period with the balance preserved; writes a fresh
  origination history row; the deferred anchor FK validates at commit (no orphan, no NOT-NULL
  violation); recurrence-rule anchors re-pointed + templates repopulated; resolver as-of balances
  correct against the new schedule. Assert a brand-new (not-yet-anchored) user can full-reset too.
- **Top-up:** disabled -> 0, no write query; full window -> 0; deficit D -> exactly D, second call
  -> 0; two sequential top-ups against the same short state never produce a duplicate
  `period_index` (`COUNT(*) == COUNT(DISTINCT period_index)`); hitting `/grid` with rolling enabled
  + deficit creates periods.
- **FK migration:** `pg_constraint.confdeltype == 'r'` for the anchor FK (mirror
  `tests/test_models/test_c43_ondelete_and_naming_convention.py`); deleting an anchor period raises
  IntegrityError.
Run via `./scripts/test.sh`; every batch ends `<N> passed`.

## Verification (end-to-end)

1. `python scripts/build_test_template.py` after Migration 2; run targeted then full suite via
   `./scripts/test.sh`.
2. `flask db upgrade` then `flask db downgrade` for BOTH migrations (DoD requires both directions).
3. `pylint app/ --fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale` clean.
4. Manual in the dev container (`docker compose -f docker-compose.dev.yml up -d`): generate a
   schedule; extend by N and confirm new periods show recurring rows in `/grid`; truncate and
   confirm the settled/anchor lock blocks it; regenerate a future tail; enable rolling (target 52),
   reload `/grid`, confirm the window tops up to 52.

## Critical files

- `app/services/pay_period_service.py` (reused: `generate_pay_periods` 70-147, `_reject_overlapping_batch` 26-67, `get_all_periods` 197)
- `app/services/recurrence_engine.py` (reused: `generate_for_template` 131-208)
- `app/services/anchor_service.py` (reused: `apply_anchor_true_up` 166-258), `app/services/account_service.py` (reused: `_resolve_anchor_period_id` 48-87)
- `app/routes/pay_periods.py`, `app/routes/grid.py` (190-224 + trigger), `app/routes/settings.py` (69, 114)
- `app/models/pay_schedule.py` (new), `app/models/account.py` (73-86 FK + comment), `app/audit_infrastructure.py`
- `migrations/versions/b4b588a49a0c_*` (C-43 helpers to reuse), `app/schemas/validation/pay_periods.py`
- `app/templates/settings/_pay_periods.html` (+ new `_pay_periods_manage.html`)
