# Implementation Plan: The Pay Calendar

## Where this stands

**C1** (`f9d148fe`) and **C2-a** (`3cb3082f`) are built; which has reached `main` and production is
a MEASUREMENT -- `git log --oneline origin/main..dev`, `docker inspect shekel-prod-app`. The arc
opened 2026-08-08 out of the recurrence arc's **F-10**, once that proved a missing NORMALIZATION
rather than a missing check; **R-F10** / **R-F12** tick with **C5a** / **C2**, and the balance arc's
**N-128** / **X-l** are the same defect from a third side.

**C2 is RULED on all three forks (2026-08-10) and DECOMPOSED; NEXT is any of `C2-b`..`C2-f`, all
five unblocked by C2-a.** `C2` ticks as ONE step under three names -- `C2` == `balance:X-l` ==
`recurrence:R-F12`. **A cold session starts at section 4**, whose preamble names the three things a
cutover must not assume.

**Both earlier forks RULED 2026-08-09.** P3/N-123 to the balance arc's **X-ad**, so that row is
`balance:N-123`. P16 to the occurrence-aware remedy, now **C5b**: **C5 DECOMPOSED the same day**,
C5a the deletion (after C4) and C5b the migration, blocked by recurrence **R5**.

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
| **An entry dated outside the schedule** | **A legitimate SECOND QUESTION, named on the one value -- not a compensator to delete. Ruled 2026-08-10 (developer).** The calendar gains `period_starting_on_or_before`, the missing mirror of the `period_starting_on_or_after` it already carries, and the FILING rule is DERIVED from it rather than scanning again. The alternative (drop the ledger's stored paycheck and derive it from `entry_date`) was measured against `shekel-prod-db` and REFUTED: 14 days carry TWO paychecks for one date, so `entry_date` does not determine it; 35 of 327 entries are dated outside their own paycheck by design; and 4 loan-opening entries predate the first payday by up to seven years, so the clamp is live rather than hypothetical. See section 6 |
| **Past the last stored payday** | **The calendar ANSWERS, projecting forward at the OWNER's cadence with `period_id = None`. Ruled 2026-08-10 (developer)**, which is what makes it the TOTAL function `balance:X-l` asks for. `growth_engine.generate_projection_periods` and `SyntheticPeriod` retire into it (rows **P17**, **P20**). Containment over SAVED periods stays its own named method, because the recurrence engine needs to tell a schedule HOLE from "the schedule has not reached there yet" |
| **C2's shape** | **DECOMPOSED into `C2-a`..`C2-f`, the value first with nothing calling it. Ruled 2026-08-10 (developer).** A single commit over 66 call sites cannot be proven against production BEFORE its consumers depend on it, which is exactly the technique that made C1 safe, cannot be reviewed in focus, and cannot be reverted precisely -- and two of the cutovers move money |

---

## 0. Sequencing against the other two arcs

Three arcs are live. **A step with no code yet has no measured file set**, and the first draft
claimed one for C1-C3 anyway. What IS measured is the surface C4 must cross, by AST over `app/`:

```text
.end_date      35 files / 72 accesses.  33 are PayPeriod; 2 read only RecurrenceRule
               (loan_recurrence_sync, recurrence/_authoring) and are untouched.  At
               least 7 of the 33 read it off a DERIVED value that survives C4
               (SchedulePeriod, TrendPoint, synthetic periods), so 35 OVERSTATES it.
.period_index  21 files / 60 accesses.
```

C2 is not merely adjacent to the balance arc's **X-l**, it IS that step, and it is also recurrence
**R-F12**: three arcs asking for one total calendar. X-l's stated root -- "the pay calendar is a
PARTIAL function... past the last row every consumer improvises and the improvisations disagree"
(`docs/audits/balance_architecture/README.md:604-607`) -- is what this arc's C2 supplies by deriving
the calendar from the paydays. C2 is the third NAME for one value, not a third step.

**C3's collision with the balance arc's X-ad is RULED** (2026-08-09, to X-ad, after no document said
the two arcs answered it oppositely from April onward): row **P3** IS row `balance:N-123`, the
registration bootstrap payday is DELETED rather than kept beside the owner's real one, and it binds
**C4** as well -- P8's write-door invariant is a `PaySchedule` write inside
`auth_service.register_user`, the function X-ad rewrites.

**C3 and the recurrence arc's R7c share a derivation, not a file.**
`recurrence_rules.offset_periods` is a phase modulo `interval_n` computed from the start period's
`period_index` (`recurrence/_resolution.py:896`), so once the index is derived, inserting a payday
BEFORE an existing one re-phases every `Every N Periods` rule. Ledger row **P11** carries the
measurement (zero live rules exposed today); R7c derives the phase from the authored anchor instead,
which removes the exposure permanently.

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
user 2       : companion (linked_owner_id = 1), 0 paydays -- CORRECT.  A companion reads
               the owner's schedule, so "every user row has paydays" is false BY DESIGN.
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
(`:521-524`). `top_up_rolling_window` is a fifth, transitive through `extend_pay_periods` and
gap-free with it. **The gap-bearing paths are the two that take a free date from a form**:
`/pay-periods/generate` (`routes/pay_periods.py:82`) and `regenerate_pay_periods`
(`pay_period_admin.py:387`). Cost when a hole exists is `-$140.63`, cited from balance **N-128**
rather than measured here: production is contiguous and no gapped clone exists.

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
**That evaporation is the evidence the model is right** -- the special case existed only to keep two
columns honest.

### P4 / P5 -- BOTH gates inherit the write door's blind spot

`integrity_check`'s BA-03/BA-04 and the suite's `_pp_assert_structure` each police OVERLAP and say
nothing about a HOLE, so neither the weekly SQL nor the helper called after every pay-period
mutation test could have caught P2. BA-04 is additionally off by one. Both rows carry the predicates
and their line cites.

### P8 / P12 -- the cadence, read circularly and written by accident

Two defects on `budget.pay_schedule.cadence_days`, and the payday model sharpens both because that
column becomes an INPUT to the last period's derived end. **P8**: `resolve_cadence` infers the
cadence from the last period's LENGTH, which after C4 reads back the value it produces -- and
registration writes a payday with no schedule row at all, so a backfill alone is reopened by the
next signup and the fix is a write-door invariant. **P12**: a `num_periods=1` post naming an
existing payday creates nothing, is refused by nothing, and still rewrites the stored cadence --
live today. Both rows carry the traces.

### P9 -- a legal schedule the CHECK forbids

`ck_pay_periods_date_order CHECK (start_date < end_date)` makes a one-day pay period illegal, and
two paydays one day apart legitimately produce one. An artifact of `end_date` being authored.

### P13 -- `period_index` is the wire key of a destructive form

The truncate card renders each period's `period_index` as an `<option value>`
(`settings/_pay_periods_manage.html:99-105`, plus a visible column at `:50`); the schema takes it as
`keep_through_index` (`schemas/validation/pay_periods.py:48`); the route echoes it into a
re-submittable hidden payload on the discard-confirm 422 (`routes/pay_periods.py:152`); and
`pay_period_admin.py:298` DELETES every period whose index exceeds it. **A user-supplied ORDINAL
selects which rows are destroyed, and it survives a round trip through the browser** -- stable only
because nothing renumbers today. Identity is `id`; the form must key on it. Found by adversarial
review 2026-08-08 against this document's own (false) claim that the index is never a wire key.

### P6 -- SIX implementations, not three, which is what C2 is sized against

An AST census over `app/` for the containment predicate, 2026-08-10, plus the two bisects it cannot
see: **six** searches, not the three row P6 claimed until then. Three of them -- the linear scan
over SYNTHETIC periods in `investment_dashboard_service/_chart.py:200`, and the two SQL searches in
`pay_period_service` -- were named by no planning document. Row **P6** carries the site list and
`entry_service.py:816`'s exclusion (it asks MEMBERSHIP, not the search). The six
`pay_period_service` readers carry **66** `app/` call sites between them.

### P14 -- the derivation is window-dependent where the stored column was not

`PeriodCalendar.from_pay_periods(pay_periods, user_id)` accepts ANY list, saved or not
(`recurrence/_calendar.py:185-222`), and so does `_cash_periods._PeriodSpans.of(periods)` (`:296`).
Derive `end_date` over a partial window and its LAST row falls to the `start + cadence - 1` branch
instead of `lead(start_date) - 1`, so
**the same period reports a different end depending on which window asked** -- a disagreement a
stored column cannot produce. Not hypothetical: `loan_ledger/_visible.owner_pay_periods:78-95`
carries a measured `$150,000.00` divergence for this shape and names the grid's six-period window as
the caller that reaches the balance seam with it. The answer is structural: a calendar is built ONLY
from a complete payday set, and a window is a VIEW over it that keeps the real ends.

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
and not part of C3. Row **P10** carries the two halves that must be specified first, with their
traces: what happens to a row whose date `attribution_date` would now CLAMP into the wrong half (the
damage is a silent RENDER on the wrong day, not an orphaned row), and whether the split-off payday
is repopulated (a monthly billed twice in a fortnight) or left empty (income understated for the
whole horizon).

### The three questions, which is what C2 was waiting on

**RULED 2026-08-10** (rulings table; recurrence **F-12** ticks with it). "Which paycheck does this
row live in" is a WRITE question needing a row an FK can point at; "which span does this day fall
in" is a READ question a projection can answer. Two questions, not one with a compensator, so the
one value states BOTH -- and the third, `period_starting_on_or_after`, already existed.

## 4. Step sequence

Each step is a leaf boundary -- one commit, its own tests green, independently revertible -- and
**budgets a neutral adversarial review pass and a fix pass**.

**Three things every C2 cutover must not assume, learned by building C2-a.** `DerivedPeriod` has
`period_id`, NOT `.id`, and every PROJECTED period carries `period_id = None`, so an `{p.id: ...}`
map over a projected axis collapses (row **P21**); `period_index` is the key that stays unique.
`derive_periods` accepts an UNSAVED payday, so "not a projection" is not "saved". And an ON-CADENCE
fixture cannot see a derived-end defect -- `lead(start) - 1` and `start + cadence - 1` coincide
there, which made this step's first P14 test vacuous.

- [x] **C1 -- the derivation exists and is proven equal to what is stored.** `f9d148fe`.
  Byte-identical on 61 of 61 rows of both clones under two controls; opened **P15**, **P16**.
  Condensed under rule 5; the proof of record is that commit and `_derive.py`'s docstring.

- [ ] **C2 -- one calendar value answers every "which period" question** -- the DECOMPOSED parent,
      RULED on three forks 2026-08-10. Ticks with the last of its leaves, and that tick is also
      `balance:X-l` and `recurrence:R-F12`: one step under three names, one commit each.

- [x] **C2-a -- the one calendar VALUE, and nothing calls it.** `3cb3082f`. `PayCalendar` answers
      `period_containing` / `span_containing` / `filing_period`; a window is a `PeriodWindow`, a
      TYPE no constructor accepts. The periods are DERIVED in `__post_init__`, so tiling is
      structural. `recurrence.PeriodCalendar` now DELEGATES three methods to the shared primitives
      -- forced by `duplicate-code`, proven live by a mutation. Suite 8535; 11 mutants killed.
      Opened **P21**-**P25**.

- [ ] **C2-b -- the recurrence cutover.** `PeriodCalendar` / `SchedulePeriod` /
      `RecurrenceScheduleError` retire into the one value; `period_by_id`, `earliest_start_in_month`
      and `period_starting_on_or_after` move across with them.
      **Its stated P14 work is FALSE and row P14 now says so**: both named callers take
      `get_all_periods`, the COMPLETE set. **`SCHEDULE_GAP` goes unsatisfiable here** (row **P25**).
      The 430-shape baseline must stay byte-identical -- and it CANNOT see hole absorption, because
      every shape it builds is contiguous.

- [ ] **C2-c -- the cash-view cutover.** `balance_at/_cash_periods._PeriodSpans` retires. Its three
  call sites keep answering `None` outside the reported window, which is a VIEW question and not
  the calendar's -- the identity R-K rests on reads a period's own span.

- [ ] **C2-d -- the filing cutover.** `loan_ledger.find_period_containing_date` and
  `resolve_anchor_pay_period` DELETE; the two posting writers
  (`loan_posting_service/_anchors.py:223`, `account_posting_service/_anchors.py:270`) call
  `filing_period`, which is proven equivalent over 1,800 (shape, day) pairs. The first is a
  public export whose only `app/` caller is twelve lines below it -- `balance:N-210`'s shape.

- [ ] **C2-e -- the projection axis.** `growth_engine.generate_projection_periods` and
  `SyntheticPeriod` DELETE; their six call sites take `axis()`. Closes **P7**, **P17**, **P20**,
  **P21**, **P22**, **P23** -- read all six before starting; three were found AFTER this leaf
  was written and they change what it owes.

- [ ] **C2-f -- the readers answer from the calendar.** `pay_period_service`'s six `get_*` (`:213`,
  `:237`, `:260`, `:277`, `:317`, `:336`) resolve against the one value across their 66 call
  sites. Closes **P19** with `get_current_period`'s unordered `.first()`.

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

- [ ] **C5 -- the gap machinery goes, and a paycheck may owe one template twice.**

**The DECOMPOSED PARENT, split 2026-08-09 (developer).** Its two halves shared only a sentence in
row P16 and are not one commit: one is a pure deletion gated on C4, the other is a migration gated
on an arc this document does not own. It ticks with the last of them.

- [ ] **C5a -- delete what is now unconstructible.**

`GenerationPlan.gaps` (`recurrence_engine.py:153`), `_recurrence_common.report_schedule_gaps` and
its two call sites (`recurrence_engine.py:309`, `transfer_recurrence.py:81`),
`PlacementOutcome.SCHEDULE_GAP` and its **six** further references (`recurrence_engine.py:253`,
`recurrence/_occurrence.py:235,250,656,704`, `recurrence/_reading.py:126`), and
`PeriodCalendar.__post_init__`'s two refusals. Every one exists to describe or police a state the
model can no longer produce. Ticks recurrence **R-F10**. Deletion-only: the recurrence arc's
430-shape baseline must stay byte-identical.
**It deletes no visibility, and that correction is why the split is safe.** P16 argued the gap
machinery was the last thing reporting the under-bill; it is not. `report_schedule_gaps` logs an
occurrence no period CONTAINS, and the cadence-30/31 case has no hole at all -- both occurrences
land inside one period. Nothing reports that today.

- [ ] **C5b -- a paycheck may owe one template more than once.**

`should_skip_period` (`_recurrence_common.py:196-232`) becomes OCCURRENCE-aware instead of returning
True on the first template-linked row; `refuse_unstorable_repeats` (`:235-300`) and
`RecurrenceCadenceUnsupported` retire with the refusal they exist to raise. Closes **P16**.
**BLOCKED BY the recurrence arc's `R5`, and this is the dependency that made the whole arc look
deferred.** The indexes must re-key onto `(template, scenario, occurs_on)` -- recurrence row
**D19**'s own answer -- and `occurs_on` does not exist: `grep occurs_on app/models/` is empty, R5
creates it, and R5 waits on the balance arc's X-f4. So the re-key is R5's and NOT restated here;
this step consumes it. Needs a migration and its own review pass.

- [ ] **C6 -- a payday may be inserted mid-schedule.**

**Starts with the two rulings section 3 names**, neither of which the 2026-08-08 lock ruling
answers: what happens to a row whose date `attribution_date` would now CLAMP into the wrong half,
and whether the newly split-off payday is repopulated (understated income) or not (a doubled bill).
Not required by the normalization and deliberately last. Closes **P10**.

- [ ] **C7 -- the ledger entry derives its paycheck.**

**Starts with a ruling.** `journal_entries.pay_period_id` is a NOT NULL FK stored beside the
`entry_date` it derives from, which is row **P1**'s defect on the ledger's header table -- and
ruling **R-EA** already made the WRITE side derive it. The NOT NULL is also what forces
`resolve_anchor_pay_period` to be TOTAL, so it is the reason row **P6**'s third implementation
cannot simply be deleted once C2 lands. Three options, and the trace decides: DROP and derive, make
it NULLABLE, or KEEP it as a deliberate materialization with the second definition's cost stated.
Sequenced after C4 (developer, 2026-08-09), because a derived paycheck should be derived from the
calendar this arc normalizes rather than from the one it is replacing. Closes **P18**.

## 5. Findings ledger

**Moved to `ledger.md`** -- this arc's rows are the ones whose `arc` column reads `pay_calendar`. A
finding is not arc-local: `P2` / `F-10`, `P3` / `N-123` and `P6` / `F-12` were each one defect in
two ledgers, and one of those pairs went unnoticed for months.

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

**Delete the out-of-schedule answer and derive the ledger's paycheck from `entry_date`** (C2's fork
1, the option that would have made "which paycheck" ONE question instead of two). Rejected
2026-08-10 on a measurement of `shekel-prod-db`, because its premise is false: `entry_date` does NOT
determine `pay_period_id`. On
**14 days one owner's entries carry TWO different paychecks for one date**;
**35 of 327 entries (10.7%)** are dated outside their own paycheck across five of the seven source
kinds, which is the budget clock and the cash clock legitimately disagreeing; and
**4 `loan_opening` entries** -- a mortgage dated 2018-12-01 and a van loan 2023-02-14 -- precede the
owner's first payday (2026-03-26) by years and rely on the clamp to name any paycheck at all. Two
further measurements sharpen where the real question lives: for transaction-sourced entries the
column equals its SOURCE ROW's paycheck on **174 of 174**, so there it is a copy of stored data
rather than a derivation of the date; for assertions it IS derived, by ruling R-EA.
**That is two relationships in one column, and it is `C7`'s subject, not this step's** -- row
**P18** describes only the second and owns the whole column with it.

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

**Moved to `conventions.md`**, one copy for every arc. `tools/plan_gate/` grades this document
against them through a pre-commit hook scoped to it -- so EDITING THIS FILE is what runs the gate.
This document's own caps live in the gate's constants beside the other arcs'.
