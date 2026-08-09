# Implementation Plan: The Pay Calendar

## Where this stands

**C1 IS DONE** (`f9d148fe`, branch `feat/pay-calendar`); nothing else has shipped. This arc was
opened 2026-08-08 out of the recurrence arc's **F-10**, after the ruling below established that F-10
is not a missing check but a missing normalization. That arc's **R-F10** and **R-F12** stay live
there and are ticked by **C5** and **C2** here; the balance arc's **N-128** and **X-l** are the same
defect from the third side.

**NEXT = C2**, which starts with a ruling and not a keystroke (section 3, "What is NOT decided yet")
and **ships as ONE commit with the balance arc's X-l** -- both build the same total calendar, and
building it twice is the defect C2 exists to remove.
**Nothing here blocks the recurrence arc's Half A** (R7a, R7b, R7c, R8), which touches no file this
arc edits.

C1's neutral review took two rulings (in the table below), corrected two of this document's own
claims, and opened **P15** and **P16** -- owned by C3 and C5, the steps that delete the last thing
standing between each and a silent loss.
**C3 also collides with the balance arc's X-ad: section 0.**

**Section 4 is the steps; findings, the step index and the rules are the shared registries.**

## Rulings

| fork | ruling |
|---|---|
| **The pay-period model** | **NORMALIZE. `budget.pay_periods` stores the PAYDAY; `end_date` and `period_index` are derived by one producer and dropped from the table. Ruled 2026-08-08 (developer), option "store paydays only"** |
| **A gapped or overlapping batch** | **Neither is refused, because neither is expressible once the derived columns are gone. The "refuse it" and "bridge it with a filler period" options were both weighed and rejected -- see section 6** |
| **A payday inserted BETWEEN two existing ones** | **REFUSE when the period it splits is locked by the existing `pay_period_admin.classify_period_lock` (historical, settled, posted, or a recurrence anchor); otherwise insert and re-derive. Ruled 2026-08-08 (developer). No row may ever be left dated outside its own paycheck** |
| **The last payday's period end** | Projected as `start_date + cadence_days - 1` from `budget.pay_schedule`. It is the ONLY derived end that is not `lead(paid_on) - 1`, and it is a projection stated as one -- `DerivedPeriod.end_is_projected`, ruled 2026-08-08 (developer), because a consumer holding one period out of its calendar cannot recompute it and a window VIEW must keep it |
| **What a derived period carries** | **`period_id`, `int \| None`. Ruled 2026-08-08 (developer)**: `None` IS the marker for a period no foreign key can point at, which is the distinction `C2` must draw between "which paycheck does this row live in" and "which span does this day fall in". One value type for the arc, so C2 MOVES `SchedulePeriod` rather than merging two |
| **Table and column names** | `budget.pay_periods.start_date` KEEPS both names. A period is identified one-to-one by the payday that opens it, `transactions.pay_period_id` reads correctly against it, and a rename is a four-FK migration that buys nothing. See section 6 |

---

## 0. Sequencing against the other two arcs

Three arcs are live. **A step with no code yet has no measured file set**, and the first draft said
one for C1-C3 anyway under the heading "measured" (found by adversarial review 2026-08-08). What IS
measured is the consumer surface C4 must cross, by AST over `app/`:

```text
.end_date      35 files / 72 accesses.  33 are PayPeriod; 2 read only RecurrenceRule
               (loan_recurrence_sync, recurrence/_authoring) and are untouched.  At
               least 7 of the 33 read it off a DERIVED value that survives C4
               (SchedulePeriod, TrendPoint, synthetic periods), so 35 OVERSTATES it.
.period_index  21 files / 60 accesses.
```

C2 is not merely adjacent to the balance arc's **X-l**, it IS that step, and it is also recurrence
**R-F12**: three arcs asking for one total calendar. X-l's stated root is "the pay calendar is a
PARTIAL function -- `get_all_periods` returns the MATERIALIZED rows and nothing else, so past the
last row every consumer improvises and the improvisations disagree"
(`docs/audits/balance_architecture/README.md:604-607`). That README's own "the two steps must be
SEQUENCED TOGETHER" (`:604`) names **X-l and R-F12**, which predate this document; C2 is the third
name for the same value, not a third step.

**C3 COLLIDES with the balance arc's X-ad, and no document said so until 2026-08-09.** That step's
**N-123** -- "the writer refuses every payday from `today+1` to `today+13`" -- IS row **P3** here.
The two arcs answer it OPPOSITELY: balance ruling **R-DB** DELETES the registration bootstrap
payday; C3 KEEPS it and lets the owner's real payday sit beside it, which the payday model makes
legal with no code at all. Whichever ships first decides for both. It binds **C4** too, whose P8
write-door invariant is a `PaySchedule` write inside `auth_service.register_user` -- the function
X-ad rewrites. Both that step and row N-123 now point back here.

**C3 and the recurrence arc's R7c share a derivation, not a file.**
`recurrence_rules.offset_periods` is a phase modulo `interval_n` computed from the start period's
`period_index` (`recurrence/_resolution.py:896`). Once `period_index` is derived rather than stored,
inserting a payday BEFORE an existing one shifts every later index and silently re-phases every
`Every N Periods` rule. Ledger row **P11**. Zero live rules are exposed today (all 46 carry
`interval_n = 1`, where the phase is inert -- measured on `shekel-prod-db` 2026-08-08), and R7c
derives the phase from the authored anchor instead, which removes the exposure permanently.

---

## 1. Root cause

`budget.pay_periods` stores three values per row and only one of them is a fact.

| column | is it a fact? | what it actually is |
|---|---|---|
| `start_date` | **yes** | the day money arrived |
| `end_date` | no | `lead(start_date) - 1` -- the day before the NEXT payday |
| `period_index` | no | `row_number() - 1` over the user's paydays in date order |

The module's own docstring states the derivation it then does not enforce: "Each period is defined
by a start_date (payday) and end_date (day before next payday)" (`pay_period_service.py:4-6`). What
the writer actually computes is `start_date + cadence_days - 1` (`:190`), which equals the
definition only when the next batch happens to start at `start + cadence`.

**Two derived values stored beside the fact they derive from, with nothing reconciling them.** Every
symptom in this area is one disagreement:

- `end_date` **below** the next `start_date` is a GAP -- a day funded by no paycheck (row P2).
- `end_date` **at or above** the next `start_date` is an OVERLAP -- a day funded by two.
- `period_index` order disagreeing with `start_date` order is the balance resolver walking a user's
  money out of calendar order.

Because the schema cannot make them agree, the application grew
**five separate runtime fences that all police the same functional dependency**:

| fence | cite |
|---|---|
| `_reject_overlapping_batch` -- one-directional, which IS row P2 | `pay_period_service.py:87-130` |
| `PeriodCalendar.__post_init__` -- the same property at the value boundary | `recurrence/_calendar.py:162-182` |
| `_pp_assert_structure` -- the same property in the test suite | `tests/_test_helpers.py:3367-3395` |
| `integrity_check` BA-03 / BA-04 -- the same property in weekly SQL | `scripts/integrity_check.py:353-376` |
| `uq_pay_periods_user_index` + `ck_pay_periods_date_order` | the schema |

Not one of them would exist under the normalized model, because none of them would have a subject.

## 2. Evidence

### The table is ALREADY the paydays table

`uq_pay_periods_user_start UNIQUE (user_id, start_date)` is live on production. That is the payday
model's exact key, already enforced. The two derived columns are pure redundancy bolted onto a
correctly-keyed fact table, which is why C4 is two `DROP COLUMN`s and not a rewrite: row `id` never
moves, so all four inbound foreign keys (`transactions`, `transfers`, `journal_entries`,
`recurrence_rules`) are untouched.

### Live census, `shekel-prod-db`, 2026-08-08

```text
owner user 1 : 61 paydays, FIRST 2026-03-26, LAST 2028-07-13 (the last stored end_date
               is 2028-07-26; quoting that as the range's top would be the very
               conflation this document argues against).
               0 non-contiguous pairs, 0 periods of a length other than 14
               pay_schedule: cadence 14, rolling ON, target 52
user 2       : companion (role `companion`, linked_owner_id = 1), 0 paydays -- CORRECT.
               A companion reads the owner's schedule and has none of its own, so
               "every user row has paydays" is false by design and no step may assume it.
recurrence   : 46 rules, interval_n > 1 on ZERO, offset_periods <> 0 on ZERO
derivation   : row_number()-1 and coalesce(lead(start_date)-1, start+cadence-1)
               reproduce the two stored columns on 61 of 61 rows, 0 mismatches.
```

**Production has no hole today**, so this arc is entirely about what the writer PERMITS.

### P2 -- the writer accepts a hole, and only two paths can pass it one

Two `PayPeriod` constructors exist in `app/` and `scripts/`: `pay_period_service.py:191` and the
registration bootstrap at `auth_service.py:704`. (Repo-wide there are ~58 more, all in `tests/` --
"in the repository" was the first draft's overclaim.) Of the four `app/` callers of
`generate_pay_periods`, two cannot gap by construction -- `extend_pay_periods` derives its start as
`last.end_date + 1` (`pay_period_admin.py:235`) and `reset_pay_periods` wipes every row first
(`:521-524`), so its `existing_periods` read returns nothing. `top_up_rolling_window` is a fifth,
transitive caller through `extend_pay_periods` and is gap-free with it. The gap-bearing paths are
the two that take a free date from a form: `/pay-periods/generate` (`routes/pay_periods.py:82`) and
`regenerate_pay_periods` (`pay_period_admin.py:387`).

Cost when a hole exists, **cited from the balance arc rather than measured here** (no gapped clone
exists; production is contiguous): `-$140.63`, where `_cash_sums` and `_assertion_sums` drop a fact
whose day no period can place while `_period_balances` keeps it, so the reconciliation identity
breaks (`README.md:615`, balance ledger **N-128**).

### P3 -- a new owner cannot enter their real first payday

Registration writes a placeholder period of `[signup_day, signup_day + 13]`
(`auth_service.py:704-709`); the Generate form then asks for "your next (or first) payday"
(`settings/_pay_periods_form.html:14-21`). `_reject_overlapping_batch` refuses any start on or
before the placeholder's end. Traced over every candidate start `T..T+14`:
**`T+1` through `T+13` are all refused**, 13 of the 14 possible next-payday offsets for a biweekly
owner. Only `T` (which de-duplicates to a first created start of `T+14`) and `T+14` are accepted.
Regenerate is no escape -- `_regenerate_keep_through_index` needs `start_date > today` to find a
rebuildable tail (`pay_period_admin.py:631-660`) and the placeholder starts TODAY, so the truncate
is a no-op and the generate hits the same refusal. "Reset entire schedule" is the only path.

Under the payday model the same input needs no code at all: insert `08-14` beside the placeholder
`08-08` and the periods derive as `[08-08, 08-13], [08-14, 08-27], ...`.
**That evaporation is the evidence the model is right** -- the special case had to exist only
because two columns had to be kept honest.

### P4 -- the weekly check has the same blind spot, plus an off-by-one

`integrity_check` BA-03 checks `period_index` gaps and BA-04 checks date overlaps;
**no check exists for a date GAP**. BA-04 is also off by one: its predicate is
`p2.start_date < p1.end_date` (`scripts/integrity_check.py:375`), so `[Jan 2, Jan 15]` beside
`[Jan 15, Jan 28]` both cover Jan 15 and the check passes.

### P5 -- so does the test gate

`_pp_assert_structure` asserts `cur.start_date > prev.end_date` (`tests/_test_helpers.py:3392`) --
no overlap, and nothing about a hole. The helper is called after EVERY pay-period mutation test and
is documented as "the single source of truth for this user's period structure is sound", so the
suite could not have caught a gapped write either.

### P8 -- one derivation that becomes circular

`pay_schedule_service.resolve_cadence` falls back to inferring the cadence from the LAST PERIOD'S
LENGTH for a user with periods but no schedule row (the code is `:169-177`, not the `:151-155`
docstring the first draft cited). Once the last period's end is projected FROM the cadence, that
inference reads back the value it is producing. It is only UNRESOLVABLE for a one-payday user --
with two paydays `last_start - prev_start` gives the same integer non-circularly -- and a backfill
does not close it, because **registration writes a payday and NO schedule row at all**: there is no
`PaySchedule` write anywhere in `auth_service.py`, so every signup after C4 reopens the state. The
fix is a write-path invariant, not a data repair.

### P12 -- a no-op generate silently rewrites the cadence

`routes/pay_periods.py:96-98` calls `upsert_schedule` WITHOUT CONSULTING WHAT THE BATCH DID, after a
`generate_pay_periods` that returns `[]` whenever every requested start already exists
(`pay_period_service.py:178-185`, then `_reject_overlapping_batch`'s empty-batch early return at
`:120-121`). "Unconditionally" was C1's review correction: the route DOES return 422 first on a
schema error (`:76-78`) and on the overlap guard (`:82-95`), so the reachable path is a
`num_periods=1` post naming an existing payday -- `new_starts == []`, nothing is refused, nothing is
written, the stored cadence changes, it commits and flashes "Generated 0 pay periods." A live defect
today (the next extend continues at the wrong cadence); under the target model it is worse, because
the cadence is an INPUT to the last period's derived end.

### P9 -- a legal schedule the CHECK forbids

`ck_pay_periods_date_order CHECK (start_date < end_date)` makes a one-day pay period illegal. Two
paydays one day apart are two facts, and the derived period between them is one day long. The
constraint is an artifact of `end_date` being authored rather than derived.

### P13 -- `period_index` is the wire key of a destructive form

The truncate card renders each period's `period_index` as an `<option value>`
(`settings/_pay_periods_manage.html:99-105`, plus a visible column at `:50`); the schema takes it as
`keep_through_index` (`schemas/validation/pay_periods.py:48`); the route echoes it back into a
re-submittable hidden payload on the discard-confirm 422 (`routes/pay_periods.py:152`); and
`pay_period_admin.py:298` DELETES every period whose index exceeds it. So a user-supplied ORDINAL
selects which rows are destroyed, and it survives a round trip through the browser. That is stable
only because nothing renumbers today.
**Identity is `id`; `period_index` is an ordinal, and the form must key on the former.** Found by
adversarial review 2026-08-08, against this document's own (false) claim that the index is never a
wire key. `period_index` is also persisted outside the table in `system.audit_log`'s whole-row jsonb
-- 9 rows on production carry it.

### P14 -- the derivation is window-dependent where the stored column was not

`PeriodCalendar.from_pay_periods(pay_periods, user_id)` accepts ANY list, saved or not
(`recurrence/_calendar.py:185-222`), and so does `_cash_periods._PeriodSpans.of(periods)` (`:296`).
Derive `end_date` over a partial window and the LAST row of that window falls to the
`start + cadence - 1` branch instead of `lead(start_date) - 1`, so
**the same period reports a different end depending on which window asked** -- a disagreement a
stored column cannot produce. This is not hypothetical:
`loan_ledger/_visible.owner_pay_periods:78-95` already carries a measured `$150,000.00` divergence
for exactly this shape, and names the grid's six-period window as the caller that reaches the
balance seam with it. The answer is structural: a calendar is constructed ONLY from an owner's
complete payday set, and a window is a VIEW over it that keeps the real ends.

## 3. Target model

**This is the END state. Which step creates each piece is marked.**

```sql
budget.pay_periods
  id            PK                              -- UNCHANGED: every inbound FK survives
  user_id       FK auth.users CASCADE  NOT NULL
  start_date    DATE NOT NULL                   -- THE PAYDAY.  the only fact in the row
  created_at
  UNIQUE (user_id, start_date)                  -- already live; the payday model's exact key

-- DROPPED [C4]: end_date, period_index
-- DROPPED [C4] with them: THREE constraints -- ck_pay_periods_date_order,
--                         ck_pay_periods_positive_index, uq_pay_periods_user_index

budget.pay_schedule            -- every OWNER with a payday has one, enforced at the
  cadence_days  INT NOT NULL   -- WRITE DOOR (registration + generate), not backfilled once
```

Everything else is derived, once, by one producer:

```text
period_index = row_number() over (partition by user_id order by start_date) - 1
end_date     = coalesce(lead(start_date) over (...) - 1,   -- the definition.  NOT
                                                           -- "- INTERVAL '1 day'", which
                                                           -- returns a timestamp
                        start_date + cadence_days - 1)     -- the open last one
```

**The value type mostly exists.** `SchedulePeriod` (`recurrence/_calendar.py:97-100`) is exactly
`(period_id, period_index, start_date, end_date)` and `PeriodCalendar` is the ordered, owner-checked
tuple of them. **But "consumers do not change shape" is FALSE for one class of reader and saying so
is the correction** (adversarial review, 2026-08-08): the hot path reads the bounds off the ORM
RELATIONSHIP, not off a calendar -- `balance_at/_cash_fold.py:513-518` does
`period = txn.pay_period; attribution_date(txn.due_date, period.start_date, period.end_date)` on
every grid, dashboard and account render, and `calendar_service.py:809-812`,
`routes/transactions/_helpers.py:168` and `routes/_recurrence_conflict_chooser.py:140` have the same
shape. `PayPeriod.label` (`models/pay_period.py:44-52`) is a model property built from `end_date`.
Those callers hold a `Transaction`, not a calendar, so C4 is a seam signature change for them and
C1's oracle proves nothing about it. **Two constraints on the producer, both structural:**

1. **It is constructed ONLY from an owner's COMPLETE payday set.** A partial list makes the last
   row's end window-dependent (row P14). The partial-list constructor does not survive C2.
2. **A window is a VIEW over that calendar**, carrying the real ends, never a calendar rebuilt from
   a slice.

### What becomes impossible rather than checked

- **A gap.** Consecutive dates define adjacent intervals; there is no second column to disagree
  with.
- **An overlap.** Intervals derived from a set of distinct sorted dates never overlap.
- **Index order differing from date order.** It is the definition of the index.
- All five fences in section 1, and with them `PlacementOutcome.SCHEDULE_GAP`, `GenerationPlan.gaps`
  and `_recurrence_common.report_schedule_gaps` (C5, which ticks recurrence R-F10).

### The one question the model opens, and why it is its OWN step

A payday may now be inserted between two existing ones, splitting a period. **Ruled 2026-08-08:
refuse when the split period is locked by `classify_period_lock`, insert and re-derive otherwise.**
Adversarial review then showed "insert and re-derive" is a feature, not a clause, so it is **C6**
and not part of C3 -- the normalization does not need it, and shipping it underspecified inside the
writer step would be the gold-plating this project's rules forbid. Two halves must be specified
first, both in row P10:

- **The stated hazard is already prevented, by a mechanism this document did not name.** A row is
  never left dated outside its own period: `utils/dates.attribution_date:228-233` CLAMPS it. So the
  real damage is different and worse -- a $2,000 charge the user dated Aug 10 renders on Aug 7 on
  both surfaces built to agree (`balance_at/_cash_fold.py:513-518`, `calendar_service.py:809-812`),
  silently. `classify_period_lock` (`pay_period_admin.py:119-127`) has four reasons and none is
  about a transaction's DATE, so an unlocked future period full of Projected rows splits happily.
- **The new period has no paycheck.** Leave it empty and every forward balance is understated by a
  paycheck for the rest of the horizon; repopulate it and `should_skip_period`
  (`_recurrence_common.py:196-226`) skips only periods already holding a template-linked row, so a
  monthly bill lands in the new half while the old half keeps its copy -- billed twice in a
  fortnight.

### What is NOT decided yet

**C2 needs its own ruling before a keystroke.** A projected period past the horizon has no `id`, so
it cannot be an FK target: "which paycheck does this row live in" (a WRITE question, must be
materialized) and "which span does this day fall in" (a READ question, may be projected) are two
questions, and `loan_ledger/_visible.py:117`'s fallback -- the latest period ENDING before the
target -- is a third that the other two implementations deliberately refuse. Whether that is a
legitimate named question on the one value or a compensator is recurrence finding **F-12**'s open
ruling, and C2 inherits it.

## 4. Step sequence

Each step is a leaf boundary: one commit, its own tests green, independently revertible.
**Budget a neutral adversarial review pass and a fix pass into every one.**

- [x] **C1 -- the derivation exists and is proven equal to what is stored.** `f9d148fe`,
      `app/services/pay_calendar`. Byte-identical on 61 of 61 rows of both clones, with TWO
      controls: a moved payday (31 shifted indices, 2 shifted ends) and a PROBE CADENCE, which the
      first cut lacked -- a derivation projecting EVERY end reproduced the clone exactly and exited
      0. Ten hand-computed irregular shapes; nothing in `app/` calls it. Opened **P15** and **P16**.

- [ ] **C2 -- one calendar value answers every "which period" question.**

**Starts with a ruling, not a keystroke** (see "What is NOT decided yet"). `PeriodCalendar` becomes
the one producer and grows the two named questions; `pay_period_service`'s **six** `get_*` readers
(`:213`, `:237`, `:260`, `:277`, `:317`, `:336`) and the three period-containing searches answer
from it. **Its constructor stops accepting a partial list** (row P14), and the two callers that pass
one -- `routes/_recurrence_preview.py:252`, `generation_schedule.py:192` -- move to a window VIEW.
Closes **P6**, **P7**, **P14**. **Ships as ONE commit with the balance arc's X-l**, which is the
same step under another name; that README's "SEQUENCED TOGETHER" (`:604`) names X-l and R-F12, both
of which predate this document. Ticks recurrence **R-F12**.

- [ ] **C3 -- the writer writes paydays, forward-only.**

`generate_pay_periods` stops computing `end_date` and `period_index` from cadence arithmetic and
materializes them from the derivation over the WHOLE payday list, which re-closes the preceding
period's end as a matter of course. `_reject_overlapping_batch` is deleted: a hole and an overlap
are both already unexpressible at this point, one step before the columns go. Re-keys the truncate
form onto `id` (row **P13**) -- an ordinal must not select rows for deletion once anything can
renumber -- and stops `upsert_schedule` firing on a batch that created nothing (row **P12**).
**Mid-schedule insert is NOT here; it is C6.** Closes **P2**, **P3**, **P12**, **P13**. The columns
still exist and still agree, so C1's harness must stay byte-identical across this step -- that is
the proof the cutover is behaviour-preserving.

- [ ] **C4 -- drop the derived columns.**

First the WRITE-DOOR invariant row P8 actually needs -- `auth_service.register_user` writes a
`PaySchedule` beside its bootstrap payday, so a backfill is not reopened by the next signup -- and
the ORM-relationship readers named in section 3 get the bounds from the calendar instead of from
`txn.pay_period`. Then ONE migration: backfill the schedule row for every owner with paydays,
`DROP COLUMN end_date`, `DROP COLUMN period_index`, and drop **three** constraints with them --
`ck_pay_periods_date_order`, `ck_pay_periods_positive_index` (`models/pay_period.py:30`, which the
first draft omitted) and `uq_pay_periods_user_index`. Destructive, so it carries the `Review:` line.
**The downgrade is NOT unconditionally lossless and must say so**: re-adding
`CHECK (start_date < end_date)` fails outright on any one-day period C4 legalises (row P9), and the
LAST row's rebuilt end is a projection off `cadence_days` as it reads at downgrade time. Deletes the
four surviving fences of section 1, including BA-03/BA-04 and `_pp_assert_structure`'s invariants 1,
2, 3a and 3b. Closes **P1**, **P4**, **P5**, **P8**, **P9**.
**This step needs its own review pass**; it is the deepest cut into the spine.

- [ ] **C5 -- delete what is now unconstructible.**

`GenerationPlan.gaps` (`recurrence_engine.py:153`), `_recurrence_common.report_schedule_gaps` and
its two call sites (`recurrence_engine.py:309`, `transfer_recurrence.py:81`),
`PlacementOutcome.SCHEDULE_GAP` and its **six** further references (`recurrence_engine.py:253`,
`recurrence/_occurrence.py:235,250,656,704`, `recurrence/_reading.py:126`), and
`PeriodCalendar.__post_init__`'s two refusals. Every one exists to describe or police a state the
model can no longer produce. Ticks recurrence **R-F10**. Deletion-only: the recurrence arc's
430-shape baseline must stay byte-identical.

- [ ] **C6 -- a payday may be inserted mid-schedule.**

**Starts with the two rulings section 3 names**, neither of which the 2026-08-08 lock ruling
answers: what happens to a row whose date `attribution_date` would now CLAMP into the wrong half,
and whether the newly split-off payday is repopulated (understated income) or not (a doubled bill).
Not required by the normalization and deliberately last. Closes **P10**.

## 5. Findings ledger

**Moved to `ledger.md`**, the one findings table for every arc. This arc's rows are the ones whose
`arc` column reads `pay_calendar`; a row's owner names a step in `steps.md`, whose specification is
section 4 of this document.

They moved because a finding is not arc-local: `P2` / `F-10`, `P3` / `N-123` and `P6` / `F-12` were
each one defect recorded in two ledgers, kept in step by hand, and one of those pairs went unnoticed
for months. The rules the table is graded against are `conventions.md`.

## 6. Alternatives considered and rejected

**Refuse a gapped batch at the write door.** The first option weighed, and the one finding F-10 was
written as. It is a SIXTH fence around the same functional dependency, and C3 would delete it. It
also makes two live features unusable: `regenerate_pay_periods`' "Corrected first payday" field
could only ever accept the single day after the retained coverage ends, so it could correct a
cadence and never a payday; and P3's blockage gets worse rather than better.

**Bridge a hole with a filler period.** Insert `[latest_end + 1, new_start - 1]` as its own period.
It fabricates a paycheck that never happened: the templates generate a full set of recurring bills
into it, the paycheck calculator writes it an income row, and `growth_engine.period_return_rate`
prorates a return into it (`:288`). A model that invents money events to preserve a stored column is
worse than the hole.

**Rename the table to `budget.paydays`.** Correct about what the row holds, and rejected on cost
with no correctness gain: four inbound foreign keys would move,
`transactions.pay_period_id -> paydays.id` reads worse than what it replaces, and a pay period
genuinely IS identified one-to-one by the payday that opens it. The name `start_date` is kept for
the same reason.

**Keep `period_index` stored and derive only `end_date`.** Half the normalization, and it keeps the
half that needs the advisory lock and the uniqueness constraint.
**The first draft rejected this on a claim adversarial review REFUTED** -- that the index is never a
persisted reference or a wire key. It is both (row P13), it appears at a third grid site
(`routes/grid.py:530`) and four in `routes/accounts/detail.py:194,195,198,225`, and it is serialized
to the browser as a deep-link offset (`dashboard_pulse_service.py:514`). The rejection SURVIVES on
better grounds: the index's only stable referent is its position in the payday order, so storing it
is storing the same functional dependency P1 describes -- and every one of those call sites is an
ORDINAL use that a derived value serves identically. What P13 proves is that the one IDENTITY use,
the truncate form, was wrong before this arc and must be re-keyed onto `id` regardless.

## 7. Document rules (GATED)

**Moved to `conventions.md`**, one copy for every arc. They were near-identical in three documents
and absent from the fourth.

`tools/plan_gate/` grades this document against them through a pre-commit hook scoped to it and the
CI step that runs the custom pylint checkers -- so EDITING THIS FILE is what runs the gate. This
document's own caps live in the gate's constants beside the other arcs'.
