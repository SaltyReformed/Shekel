# Recurrence redesign -- as built (through R2b)

Archived under section 7 rule 5 of `docs/plans/implementation_plan_recurrence_redesign.md`: a
completed span leaves the live plan and becomes one line per step -- its id, its commit, and what it
closed. The live document keeps the specification of what REMAINS; this file keeps the record of what
is done, so the 900-line cap binds on unfinished work rather than on history.

Read the commits, not this file. Each hash is the step's own account of itself and the diff is the
truth; a planning document only needs to say which commit to read.

| step | commit | what it did |
|---|---|---|
| **R1** -- Oracle and characterization snapshot | `96a35fe6` | Froze `match_periods` and `compute_due_date` at 8,105 lines over 423 shapes (`tests/oracles/recurrence_baseline.py`), gated by `tests/test_services/test_recurrence_baseline.py` with two control tests that prove the freeze can fail |
| **R2a** -- the vocabulary | `5c13e643` | `ref.recurrence_units` / `ref.period_placements` / `ref.business_day_shifts`, their enums, `ref_cache` accessors and `ref_seeds` entries, dual-seeded by migration `e7a4d95c2b18`. No behaviour change; R1 baseline byte-identical |
| **R2b** -- the columns, NULLABLE, and the backfill | `86b9eaa3` | The five two-axis columns, both anchor subtypes, and all 50 rules derived by migration `c8f2b6a41d93`; every generating rule reproduced the engine's period set exactly |

## What these steps bind on later ones

Carried forward because a later step would otherwise rediscover it the expensive way:

- **Every subtype table carries a surrogate `id`**, not the design's `recurrence_rule_id` primary
  key. `system.audit_trigger_func` assigns `v_row_id := NEW.id`, so an INSERT into an audited table
  without that column dies with `record "new" has no field "id"` -- measured on a probe table, not
  reasoned. `UNIQUE (recurrence_rule_id)` enforces the identical 0-or-1 cardinality.
- **`c8f2b6a41d93`'s upgrade REFUSES a non-default `interval_n`** on any non-`Every N Periods` rule,
  which is what makes its downgrade exact rather than a guess.
- **`end_date >= anchor_date` was deliberately NOT added.** `anchor_date` is derived and inert while
  `end_date` is user-authored and live; 14 live rules carry a future anchor, so the CHECK would turn
  "stop this recurring bill" into an unhandled `CheckViolation` out of `update_template`'s autoflush.
  It belongs to R7, with the Marshmallow validator that can refuse the pair at the door.
- **The three `ref` vocabulary tables are NOT audited** and seed without literal ids -- the `ref`
  schema is excluded from `AUDITED_TABLES` with one multi-tenant exception, and a literal-id seed is
  what leaves an identity sequence behind its data (finding F-1).
- **R1's baseline freezes some WRONG answers on purpose.** The `long_cadence.*` shapes and four
  `bounds.*` blocks record defects D3 and D5 as they behave today, so R4 -- not R3 -- re-freezes
  exactly those lines with `SHEKEL_UPDATE_RECURRENCE_BASELINE=1`.
