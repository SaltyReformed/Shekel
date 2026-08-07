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
| **R2b** -- the anchor subtypes and the count bound | `86b9eaa3`, amended by `1e5e3430` | Both anchor subtype tables, `max_occurrences` and its two bound CHECKs, by migration `c8f2b6a41d93`. **It also added four two-axis columns and backfilled all 50 rules; R2d withdrew both**, so the migration as it stands writes no data at all |
| **R2c-1** -- the write door | `2fca91bc` | `app/services/recurrence/`: a caller states what it AUTHORS, one pure function resolves it, one writer assigns the columns. Five construction sites and four in-place writers routed through it. Closed defect **D1** -- an amount-only edit no longer re-phases "every N paychecks" |
| **R2d** -- the derived half stops being stored | `1e5e3430` | The four two-axis columns removed and computed on demand instead; steps R2c-2 and R2c-3 deleted with them. See ruling **R-R10** |

## What these steps bind on later ones

Carried forward because a later step would otherwise rediscover it the expensive way:

- **Every subtype table carries a surrogate `id`**, not the design's `recurrence_rule_id` primary
  key. `system.audit_trigger_func` assigns `v_row_id := NEW.id`, so an INSERT into an audited table
  without that column dies with `record "new" has no field "id"` -- measured on a probe table, not
  reasoned. `UNIQUE (recurrence_rule_id)` enforces the identical 0-or-1 cardinality.
- **`c8f2b6a41d93` was amended in place**, which was safe only because it was the chain head and had
  never left `dev`. Its docstring carries the literal SQL for the one case that needs a hand: a
  backup restored from between `86b9eaa3` and `1e5e3430` already reads that revision, so
  `flask db upgrade` is a no-op and the withdrawn columns survive with nothing able to remove them.
- **`end_date >= anchor_date` was deliberately NOT added**, and there is no `anchor_date` column for
  it to name. It lands with the column at R7c, with the Marshmallow validator that can refuse the
  pair at the door -- `end_date` is user-authored and live, and 14 live rules resolve to a future
  anchor, so the CHECK alone would turn "stop this recurring bill" into an unhandled
  `CheckViolation` out of `update_template`'s autoflush.
- **A rule is written whole, through `app/services/recurrence` and nowhere else.** `RecurrenceSpec`
  is pinned by test to be exactly the table's non-DB-assigned columns, so a column added without it
  becomes unauthorable loudly rather than in silence.
- **The three `ref` vocabulary tables are NOT audited** and seed without literal ids -- the `ref`
  schema is excluded from `AUDITED_TABLES` with one multi-tenant exception, and a literal-id seed is
  what leaves an identity sequence behind its data (finding F-1).
- **R7c inherits R2's whole blast radius** (ruling R-R5, now spent). The moment the two-axis columns
  are NOT NULL every INSERT must supply them: 5 production writers plus **83 direct
  `RecurrenceRule(...)` constructions across 39 test files**. R2 was split three ways to avoid one
  enormous commit; R2d dissolved the problem instead, so none of those 83 was touched -- a partial
  construction is still a complete rule. R7c is where that bill finally comes due.
- **R1's baseline freezes some WRONG answers on purpose.** The `long_cadence.*` shapes and four
  `bounds.*` blocks record defects D3 and D5 as they behave today, so R4 -- not R3 -- re-freezes
  exactly those lines with `SHEKEL_UPDATE_RECURRENCE_BASELINE=1`.

## Closed defects, with the measurement that found them

Kept here rather than in the live plan: a defect whose step has shipped is history, and the live
document's line cap is for work that has not happened yet (section 7 rules 4 and 5).

**D1 -- an amount-only edit re-phased "every N paychecks"** (closed by `2fca91bc`). No
`offset_periods` input exists in any template under `app/templates/`, but
`update_recurrence_rule_from_form` wrote `rule.offset_periods = data.pop("offset_periods", 0)`, so
the schema default landed on every edit. Probe, route-level with a real form payload:

```text
created with offset=1 (start period_index=7, interval=2)
after an amount-only edit:
E   AssertionError: assert 0 == 1
```

Every future occurrence shifted by one pay period. Latent in data only because no live rule uses the
pattern. Closed by deriving the phase from the rule's own start period on EVERY write, not only on
create.
