# Bug Investigation: First Occurrence Skipped on Transfer Regeneration

**Date:** 2026-04-11
**Reported behavior:** Editing a recurring transfer template (monthly, day 22) with
"Regenerate effective from" = 2026-04-10 produces a first transfer on 5/22/2026 instead of
4/22/2026. Creating a new recurring transfer does not exhibit this bug. Reproducible in
production with a specific account/transfer, but not in development.

---

## Date Comparison Analysis

**Result: All date comparisons are correct. No `>` vs `>=` bug found.**

Every date comparison in the recurrence pipeline uses inclusive operators. The effective_from
filter is:

```python
# recurrence_engine.py:313  (_match_periods)
candidates = [p for p in periods if p.end_date >= effective_from]
```

This correctly includes the pay period that *contains* effective_from when it falls mid-period.
The comment at line 312 confirms this was deliberate: "Use end_date so that the current pay
period is included when effective_from falls mid-period."

The same inclusive filter is used when querying existing transfers to delete:

```python
# transfer_recurrence.py:156  (regenerate_for_template)
PayPeriod.end_date >= effective_from,

# recurrence_engine.py:207    (regenerate_for_template)
PayPeriod.end_date >= effective_from,
```

And `_match_monthly` (recurrence_engine.py:375) uses:

```python
if period.start_date <= target_date <= period.end_date:
```

All inclusive. No exclusive comparisons on effective_from anywhere in the pipeline.

### Mental trace with the reported example

- day_of_month = 22, effective_from = 2026-04-10
- Regardless of pay period boundaries (tested ~10 configurations), the period containing
  2026-04-22 always has end_date >= 2026-04-10 (since end_date >= 2026-04-22 > 2026-04-10).
- `_match_periods` includes this period in candidates.
- `_match_monthly` finds target_date 2026-04-22 within the period's [start, end] range.
- The period is returned as a match.

**The matching logic cannot skip the April occurrence based on date comparisons alone.**

---

## Root Cause: Existing Transfer Blocking Regeneration

The bug is data-dependent, not a date logic error. The most likely root cause is a
**pre-existing soft-deleted (or immutable) transfer** for the April pay period in the
production database.

### How it happens

1. At some prior point, the user soft-deleted (or settled) the transfer for the April 22
   period.

2. When the user edits the template and triggers regeneration with effective_from = 4/10:

   **`regenerate_for_template`** (transfer_recurrence.py:123) queries all existing transfers
   where `PayPeriod.end_date >= effective_from` and categorizes them:

   ```python
   # Lines 165-177
   for xfer in existing:
       if xfer.status and xfer.status.is_immutable:
           continue                          # Left in place, not deleted
       if xfer.is_override:
           overridden_ids.append(xfer.id)    # Left in place, flagged
           continue
       if xfer.is_deleted:
           deleted_ids.append(xfer.id)       # Left in place, flagged
           continue
       to_delete.append(xfer)                # Will be hard-deleted
   ```

   The soft-deleted transfer is added to `deleted_ids` but **not hard-deleted**.
   The immutable transfer is silently skipped.

3. After deleting the safe-to-remove entries, it calls:

   ```python
   # Line 183
   created = generate_for_template(template, periods, scenario_id, effective_from)
   ```

4. **`generate_for_template`** (transfer_recurrence.py:32) calls `_get_existing_map`
   (line 72) which queries **ALL** transfers for the template/scenario/period -- including
   soft-deleted ones:

   ```python
   # _get_existing_map, lines 252-260
   existing = (
       db.session.query(Transfer)
       .filter(
           Transfer.transfer_template_id == template_id,
           Transfer.scenario_id == scenario_id,
           Transfer.pay_period_id.in_(period_ids),
       )
       .all()
   )
   ```

   **No `is_deleted` filter.** The soft-deleted transfer is returned.

5. The generate loop (lines 75-93) unconditionally skips any period with an existing entry:

   ```python
   for xfer in existing_xfers:
       if xfer.status and xfer.status.is_immutable:
           should_skip = True; break
       if xfer.is_override:
           should_skip = True; break
       if xfer.is_deleted:           # ← THIS is the trigger
           should_skip = True; break
       should_skip = True; break     # Already exists
   ```

   **Result: the April period is skipped. No new transfer is created.**

6. `RecurrenceConflict` is raised with the soft-deleted transfer's ID, and the route shows:
   "Note: 0 overridden and 1 deleted entries were kept as-is." -- but the user may not
   realize this means the April transfer was not generated.

### Why this is data-dependent

- **Production:** The specific transfer was previously soft-deleted (or settled/done). The
  soft-deleted row persists in the database and blocks regeneration.
- **Development:** Fresh data, no soft-deleted transfers exist. Regeneration works.
- **New template:** No existing transfers at all. `_get_existing_map` returns empty.
  All matching periods get new transfers.

### Same pattern exists in transaction recurrence

The transaction recurrence engine (recurrence_engine.py) has the identical pattern:

- `_get_existing_map` (line 527-552) does not filter `is_deleted`.
- The generate loop (lines 107-125) skips periods with soft-deleted entries.
- `regenerate_for_template` (line 163) preserves soft-deleted entries as conflicts.

---

## Files and Line Numbers

| File | Function | Line | What it does |
|------|----------|------|--------------|
| `transfer_recurrence.py` | `regenerate_for_template` | 172-174 | Preserves soft-deleted transfers (adds to `deleted_ids`, does not hard-delete) |
| `transfer_recurrence.py` | `_get_existing_map` | 252-260 | Queries existing transfers **without** `is_deleted` filter |
| `transfer_recurrence.py` | `generate_for_template` | 83-85 | Skips period when existing entry has `is_deleted=True` |
| `recurrence_engine.py` | `regenerate_for_template` | 226-228 | Same pattern -- preserves soft-deleted transactions |
| `recurrence_engine.py` | `_get_existing_map` | 527-552 | Same pattern -- no `is_deleted` filter |
| `recurrence_engine.py` | `generate_for_template` | 118-120 | Same pattern -- skips on `is_deleted` |
| `recurrence_engine.py` | `_match_periods` | 313 | `p.end_date >= effective_from` -- correct, inclusive |

---

## Verification Steps

To confirm the root cause in production, run:

```sql
-- Check for soft-deleted or immutable transfers for this template in the April period
SELECT t.id, t.is_deleted, t.is_override, s.name AS status, s.is_immutable,
       pp.start_date, pp.end_date
FROM budget.transfers t
JOIN budget.pay_periods pp ON t.pay_period_id = pp.id
JOIN ref.statuses s ON t.status_id = s.id
WHERE t.transfer_template_id = <TEMPLATE_ID>
  AND pp.start_date <= '2026-04-22'
  AND pp.end_date >= '2026-04-22';
```

If this returns a row with `is_deleted = true` or `is_immutable = true`, that is the blocker.

---

## Fix

Two options, depending on the intended design:

### Option A: Hard-delete soft-deleted entries during regeneration (simplest)

In `regenerate_for_template` (both `transfer_recurrence.py` and `recurrence_engine.py`),
change the soft-deleted handling to hard-delete instead of preserving:

```python
# Current (transfer_recurrence.py:172-174):
if xfer.is_deleted:
    deleted_ids.append(xfer.id)
    continue

# Fixed:
if xfer.is_deleted:
    to_delete.append(xfer)
    continue
```

This means "the user deleted this once, but they're now regenerating the template, so recreate
it." The RecurrenceConflict would no longer include deleted entries. This is the cleanest fix
if the user's intent with "regenerate" is "start fresh from this date."

### Option B: Filter soft-deleted entries in `_get_existing_map`

Add `is_deleted` filter to the existing-entry query:

```python
# _get_existing_map in transfer_recurrence.py:
existing = (
    db.session.query(Transfer)
    .filter(
        Transfer.transfer_template_id == template_id,
        Transfer.scenario_id == scenario_id,
        Transfer.pay_period_id.in_(period_ids),
        Transfer.is_deleted.is_(False),       # ← ADD THIS
    )
    .all()
)
```

This would create a NEW transfer alongside the soft-deleted one. The old soft-deleted row
persists. Less clean -- leaves stale data.

**Recommendation:** Option A is correct. Regeneration should clear soft-deleted entries.
The user's intent is "regenerate everything from this date" -- previously deleted entries
should not block that.

---

## Docstring Inaccuracy (minor)

The `_match_periods` docstring (recurrence_engine.py:306) says:

> effective_from: Only include periods **starting** on or after this date.

But the implementation uses `p.end_date >= effective_from` (includes mid-period). The docstring
should say "ending on or after" or "whose date range overlaps with or follows this date."
Not a functional bug, but could mislead future developers.

---

## Regression Test

```python
def test_regenerate_with_soft_deleted_entry_recreates_transfer(
    self, app, db, seed_user, seed_periods
):
    """Soft-deleted entries must not block regeneration.

    Scenario: transfer is generated, then soft-deleted for one period.
    On regeneration, that period should get a new transfer, not be skipped.
    """
    with app.app_context():
        template = self._make_template_with_rule(
            seed_user, "Every Period"
        )

        created = transfer_recurrence.generate_for_template(
            template, seed_periods, seed_user["scenario"].id,
        )
        db.session.flush()
        original_count = len(created)
        assert original_count == len(seed_periods)

        # Soft-delete the first period's transfer.
        created[0].is_deleted = True
        db.session.flush()

        # Regenerate -- the soft-deleted period should get a new transfer.
        new_created = transfer_recurrence.regenerate_for_template(
            template, seed_periods, seed_user["scenario"].id,
        )
        db.session.flush()

        # All periods should have transfers, including the previously deleted one.
        assert len(new_created) == original_count
```

A parallel test should be written for `recurrence_engine.regenerate_for_template`.

Additionally, test with a mid-period effective_from to cover the scenario described in
the bug report:

```python
def test_regenerate_with_mid_period_effective_from_includes_current_period(
    self, app, db, seed_user, seed_periods
):
    """effective_from mid-period must include that period's occurrence.

    Scenario: monthly on the 22nd, effective_from = day 10 of a period
    that contains the 22nd. The transfer for the 22nd must be generated.
    """
    # ... create monthly template with day_of_month=22 ...
    # ... set effective_from to a date within the period containing the 22nd ...
    # ... assert the period containing the 22nd gets a transfer ...
```
