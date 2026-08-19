> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is `docs/plans/steps.md`; the code as
> committed is the source of truth for what the app does.

# Two completed recurrence findings-steps, archived 2026-08-19

Two completed recurrence steps, condensed to one line each under
`conventions.md` rule 5, archived out of `docs/plans/steps.md` on 2026-08-19
when `recurrence:R-F16` needed the room. Cite this for how either came to be;
never as a plan of record. The code as committed is the source of truth.

Both are the shape the 2026-08-17 span used: each closed a finding on its own
commit and **blocked nothing** -- no `steps.md` row names either as a blocker,
which is what makes removing them safe under rule 13.

| step | commit | what it closed |
|---|---|---|
| `recurrence:R-D33` | `dd2a5a34` | **D33**. Both closing bounds answer "is this still a commitment" from whether the rule still OWES an occurrence, so a DATE bound stops counting at its bound date and two spellings of one schedule cannot leave the obligations total on different days. |
| `recurrence:R9` | `800671a7`, migration `b2e9a47c3f18`, ruling **R-R27** | The closed pattern set's LAST artefacts: `ref.recurrence_patterns` dropped with `RecurrencePatternEnum`, `ref_cache.recurrence_pattern_id` and the seed entry behind them, in ONE release rather than the two R-R11 had reserved; the suite's cadence vocabulary moved onto the two axes. Opened **D41** / **R12**. Its full account -- the rollback measurement, the production census and the clone rehearsal -- is `recurrence_r9_as_built_2026-08-17.md`. |

## Why the obvious neighbours did NOT go with them

Stated so the next session does not re-derive it:

- **`R7c-c`** looks archivable (no blocker names it) and is not: its entry
  carries a LIVE obligation -- "`R5` deletes `compute_due_date`, and the WEEK
  unit `R8-b` frees is where that migration's SQL and its Python twin part" --
  and rule 5 forbids a live sentence depending on an archived one.
- **`R7c`** is `pay_calendar:C6`'s blocker (`after #17 / pay_calendar:C4 /
  recurrence:R7c (shipped)`), so archiving it would orphan a key rule 13
  grades.
- **`R7a-2a`** is `recurrence:R-F17`'s blocker, and archiving it alone would
  leave two of the three `R7a` leaves behind, which is not a span.
- **`R-F1`** stays for the reason its own row states: a `tools/plan_gate`
  control uses its id as the worked example of the PREFIX trap.

`steps.md` stands at 240 lines against a 260 cap after this -- at the 20-line
headroom floor, not under it. The next step to need room in this arc should
expect to archive again.
