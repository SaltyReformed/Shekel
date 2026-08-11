# Implementation Plan: The Pay Calendar

## Where this stands

**C1** (`f9d148fe`), **C2-a** (`3cb3082f`), **C2-b1** (`90f2fbb7`), **C2-d** (`3e6cd4ec`) and
**C3-a** are built; what has reached `main` and production is a MEASUREMENT --
`git log --oneline origin/main..dev`, `docker inspect shekel-prod-app`.

**`C3-b` is the arc's one ready leaf and it gates the rest**: C2-b2 / C2-c / C2-e / C2-f each wait
on `C3` and nothing else, and `C4` waits on both. Run the ready-set query
(`tools/plan_gate/_registry.py`); do not read the table. **C3-b starts with a RULING** -- section 4
names it: whether the writer re-derives the whole payday list or only the preceding end, which
decides whether **N-127** closes.

The arc opened 2026-08-08 out of the recurrence arc's **F-10**, a missing NORMALIZATION rather than
a missing check; `balance:N-128` / `X-l` are the same defect from a third side. `C2` ticks as ONE
step under three names -- `C2` == `balance:X-l` == `recurrence:R-F12` -- and **R-F10** / **R-F12**
tick with **C5a** / **C2**. **P16** was ruled to **C5b** 2026-08-09, **C5 DECOMPOSED** into C5a
(after C4) and C5b (after R5). **A cold session starts at section 4**, whose preamble names four
things a cutover must not assume; findings, steps and rules are the shared registries.

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
| **The forward-only rule (R-PC1)** | **REPLACE `_reject_overlapping_batch`, do not delete it. Ruled 2026-08-10 (developer), amended twice by adversarial review the same day.** A new payday must be at least `MIN_MATERIALISABLE_CADENCE_DAYS` after the latest existing one, and the last paycheck must hold no row dated on or after it on EITHER clock (`due_date` and `settled_on`, transactions and transfers). Deleting the guard outright would open the mid-schedule insert C6 defers. See C3-b |
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

**C3's collision with X-ad is SETTLED IN CODE** (`balance:X-ad-a`): the registration bootstrap
payday is DELETED, and `register_user` now writes a `PaySchedule` row beside the paydays, so C4's
**P8** backfill is no longer reopened by the next signup.

**C3 and the recurrence arc's R7c share a derivation, not a file.**
`recurrence_rules.offset_periods` is a phase modulo `interval_n` computed from the start period's
`period_index` (`recurrence/_resolution.py:896`), so once the index is derived, inserting a payday
BEFORE an existing one re-phases the `Every N Periods` rules row **P26** names. Ledger row **P11**
carries the measurement (zero live rules today); R7c derives the phase from the authored anchor,
which removes it permanently.

---

## 1. Root cause

`budget.pay_periods` stores three values per row and only one of them is a fact.

| column | is it a fact? | what it actually is |
|---|---|---|
| `start_date` | **yes** | the day money arrived |
| `end_date` | no | `lead(start_date) - 1` -- the day before the NEXT payday |
| `period_index` | no | `row_number() - 1` over the user's paydays in date order |

`pay_period_service`'s own module docstring states the derivation it then does not enforce
("end_date = day before next payday"); what the writer computes is `start_date + cadence_days - 1`,
which equals the definition only when the next batch happens to start at `start + cadence`.

**Two derived values stored beside the fact they derive from, with nothing reconciling them.** Every
symptom here is one disagreement: `end_date` BELOW the next `start_date` is a GAP, a day funded by
no paycheck (row P2); at or ABOVE it is an OVERLAP, a day funded by two; and `period_index` order
disagreeing with `start_date` order is the balance resolver walking money out of calendar order.

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

`uq_pay_periods_user_start UNIQUE (user_id, start_date)` is live on production -- the payday model's
exact key, already enforced. The two derived columns are pure redundancy on a correctly-keyed fact
table, which is why C4 is two `DROP COLUMN`s and not a rewrite: row `id` never moves, so all four
inbound FKs (`transactions`, `transfers`, `journal_entries`, `recurrence_rules`) are untouched.

### Live census, `shekel-prod-db`, 2026-08-08, re-verified 2026-08-10

```text
owner user 1 : 61 paydays, FIRST 2026-03-26, LAST 2028-07-13 (the last stored end_date
               is 2028-07-26; quoting that as the range's top would be the very
               conflation this document argues against).
               0 non-contiguous pairs, 0 periods of a length other than 14
               pay_schedule: cadence 14, rolling ON, target 52
user 2       : companion, 0 paydays -- CORRECT by design (it reads the owner's).
recurrence   : 46 rules, interval_n > 1 on ZERO, offset_periods <> 0 on ZERO
derivation   : row_number()-1 and coalesce(lead(start_date)-1, start+cadence-1)
               reproduce the two stored columns on 61 of 61 rows, 0 mismatches.
```

**Production has no hole today**, so this arc is entirely about what the writer PERMITS.

### P2 -- the writer accepts a hole, and only two paths can pass it one

**ONE `PayPeriod` constructor now exists in `app/` and `scripts/`** -- `generate_pay_periods`. *This
paragraph named a second, `auth_service`'s registration bootstrap, until `balance:X-ad-a` deleted
it; re-measured 2026-08-10.* Of its `app/` callers, `extend_pay_periods` cannot gap (it starts at
the last end + 1), `reset_pay_periods` cannot (it wipes first), and `top_up_rolling_window` inherits
extend's safety. **The gap-bearing paths are the two that take a free date from a form**:
`/pay-periods/generate` and `regenerate_pay_periods`. Cost when a hole exists is `-$140.63`, cited
from balance **N-128** rather than measured here: production is contiguous and no gapped clone
exists.

### P3 -- a new owner cannot enter their real first payday

**CLOSED by `balance:X-ad-a` (`2a4eb477`).** Kept as EVIDENCE: under the payday model that input
needed no code at all, so the special case existed only to keep two columns honest.

### P4 / P5 -- BOTH gates inherit the write door's blind spot

`integrity_check`'s BA-03/BA-04 and the suite's `_pp_assert_structure` each police OVERLAP and say
nothing about a HOLE, so neither could have caught P2. BA-04 is additionally off by one. Both rows
carry the predicates and their line cites.

### P8 / P12 / P29 -- the cadence, read circularly and written by accident

Three defects on `budget.pay_schedule.cadence_days`, all sharpened by the payday model because the
column becomes an INPUT to the last period's derived end. **P8**: `resolve_cadence` infers the
cadence from the last period's LENGTH, which after C4 reads back the value it produces (its
write-door half shipped at `balance:X-ad-a`). **P12**: a batch that creates NOTHING still rewrites
the stored cadence. **P29**, found 2026-08-10, is P12 in the mirror: the extend door generates at a
cadence it never persists. C3-b's one rule closes both. All three rows carry the traces.

### P9 -- a legal schedule the CHECK forbids

`ck_pay_periods_date_order CHECK (start_date < end_date)` makes a one-day pay period illegal, and
two paydays one day apart legitimately produce one. An artifact of `end_date` being authored.

### P13 -- `period_index` was the wire key of a destructive form

**CLOSED by `C3-a`.** Kept as EVIDENCE: a user-supplied ORDINAL selected which rows a CASCADE
destroyed, across a browser round trip, and was stable only while nothing renumbers.

### P6 -- SEVEN implementations, not three, which is what C2 is sized against

An AST census found **six**, not the three this row claimed until 2026-08-10, and a review of C2-a
found a SEVENTH the census structurally could not see -- it keyed on the predicate. Row **P6**
carries the site list and the lesson; the six `pay_period_service` readers carry **66** call sites.

### P14 -- the derivation is window-dependent where the stored column was not

Derived over a PARTIAL payday set, the last row falls to `start + cadence - 1` instead of
`lead(start_date) - 1`, so
**the same period reports a different end depending on which window asked** -- a disagreement a
stored column cannot produce. Row **P14** records the mechanism it first named as REFUTED; row
**P26** carries the half that survives on the other column. C2-a shipped the structural answer: a
calendar is built ONLY from a complete set, and a slice is a `PeriodWindow`.

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

- **A gap** (consecutive dates define adjacent intervals -- no second column to disagree with),
  **an overlap** (intervals from distinct sorted dates never overlap), and
  **index order differing from date order** (it is the definition of the index).
- All five fences in section 1, and with them `PlacementOutcome.SCHEDULE_GAP`, `GenerationPlan.gaps`
  and `_recurrence_common.report_schedule_gaps` (C5, which ticks recurrence R-F10).

### The one question the model opens, and why it is its OWN step

A payday may now be inserted between two existing ones, splitting a period. **Ruled 2026-08-08:
refuse when the split period is locked by `classify_period_lock`, insert and re-derive otherwise.**
Adversarial review then showed "insert and re-derive" is a feature, not a clause, so it is **C6**.
Row **P10** carries the two halves that must be specified first: what happens to a row
`attribution_date` would now CLAMP into the wrong half (a silent RENDER on the wrong day, not an
orphan), and whether the split-off payday is repopulated (a monthly billed twice) or left empty
(income understated for the whole horizon).

## 4. Step sequence

Each step is a leaf boundary -- one commit, its own tests green, independently revertible -- and
**budgets a neutral adversarial review pass and a fix pass**.

**Four things every C2 cutover must not assume**, three learned by building C2-a and the fourth
measured 2026-08-10. `DerivedPeriod` has `period_id`, NOT `.id`, and every PROJECTED period carries
`period_id = None`, so an `{p.id: ...}` map over a projected axis collapses (row **P21**);
`period_index` is the key that stays unique. `derive_periods` accepts an UNSAVED payday, so "not a
projection" is not "saved". An ON-CADENCE fixture cannot see a derived-end defect -- `lead(start)-1`
and `start + cadence - 1` coincide there, which made C2-a's first P14 test vacuous. And a PARTIAL
payday set is re-indexed from 0 in SILENCE, where the stored ordinal used to survive a slice (row
**P26**).

- [x] **C1 -- the derivation, proven equal to what is stored.** `f9d148fe`. Opened **P15**, **P16**.
      Proof: `_derive.py`'s docstring.

- [ ] **C2 -- one calendar value answers every "which period" question** -- the DECOMPOSED parent,
  RULED on three forks 2026-08-10. Ticks with the last of its leaves, and that tick is also
  `balance:X-l` and `recurrence:R-F12`: one step under three names, one commit each.

- [x] **C2-a -- the one calendar VALUE, and nothing calls it.** `3cb3082f`. Opened **P21**-**P25**.
      Proof: `_calendar.py`'s docstring.

- [ ] **C2-b -- the recurrence cutover.** The DECOMPOSED parent, split 2026-08-10 by an instrumented
      full-suite run: the derived calendar differs from the stored rows at
      **55 shapes over 53 tests**, in two classes -- hole absorption (**P25**, **P27**) and the
      SILENT re-index of a partial payday set (**P26**). `period_starting_on_or_after` does NOT move
      -- C2-a shipped it -- so two move, not three. Ticks with its last leaf.

- [x] **C2-b1 -- the last two questions, the cadence rule, and one door.** `90f2fbb7`. Opened
      **P28**. Proof: `_loader.py`'s docstring.

- [ ] **C2-b2 -- the cutover.** `PeriodCalendar` / `SchedulePeriod` / `RecurrenceScheduleError`
      DELETE; 10 `calendar_for` call sites and 8 `app/` modules take the one value.
      **Its stated P14 work is FALSE and row P14 says so**: all three constructors take
      `get_all_periods`, the COMPLETE set. **`SCHEDULE_GAP` goes unsatisfiable here** (**P25**) and
      `__post_init__`'s two refusals go with the class, so C5a's "it deletes no visibility" is true
      only because this leaf already took it (**P27**).
      **Sized by a simulated cutover over the whole suite: 5 failures, not the 53 diverging tests**
      -- P26's phase test plus the four `TestAnOccurrenceInAScheduleGap` tests, which build a state
      this leaf makes unconstructible. The 430-shape baseline stays byte-identical and sees NO
      divergence class: its schedules are contiguous, complete, and read at the cadence they were
      generated with (**P28**).

- [ ] **C2-c -- the cash-view cutover.** `_cash_periods._PeriodSpans` retires. Its three call sites
  keep answering `None` outside the reported window, a VIEW question and not the calendar's --
  the identity R-K rests on reads a period's own span.

- [x] **C2-d -- the filing cutover.** `3e6cd4ec`. Closed **N-169**. Proof: `filing_period`'s
      docstring and `tests/manual/verify_filing_cutover.py` (1,654 days, 0 disagreements).

- [ ] **C2-e -- the projection axis.** `growth_engine.generate_projection_periods` and
  `SyntheticPeriod` DELETE; their six call sites take `axis()`. Closes **P7**, **P17**, **P20**,
  **P21**, **P22**, **P23** -- read all six before starting; three were found AFTER this leaf
  was written and they change what it owes.

- [ ] **C2-f -- the readers answer from the calendar.** `pay_period_service`'s six `get_*` (`:213`,
  `:237`, `:260`, `:277`, `:317`, `:336`) resolve against the one value across their 66 call
  sites. Closes **P19** with `get_current_period`'s unordered `.first()`.

- [ ] **C3 -- the writer writes paydays, forward-only** -- the DECOMPOSED parent, split 2026-08-10
      (developer). Two commits because only one takes user input and only one can renumber. Ticks
      with its last leaf.

- [x] **C3-a -- the destructive form stops keying on an ordinal.** `5f1e2bd6`.
      `keep_through_period_id`, a `RowId` resolved against the owner's own periods; anything else is
      `PayPeriodUnresolved`, with both F-144 branches logged. The tail is selected by PAYDAY, so no
      part of the operation reads a column C4 drops. The lock classifier moved to `pay_period_locks`
      (developer ruling: the 1000-line ceiling reported a read-predicate and four destructive
      writers sharing one module). Closed **P13**; opened **P29**, **P30**.

- [ ] **C3-b -- the writer materialises the derivation.**

`generate_pay_periods` stops computing `end_date` and `period_index` from cadence arithmetic and
materializes them from the derivation over the WHOLE payday list, which re-closes the preceding
period's end as a matter of course. Closes **P2**, **P12**, **N-127**, and it is the second of
**P27**'s two ends. The columns still exist and still agree, so C1's harness must stay
byte-identical -- **which is true only if TRUNCATE re-materialises too**: it deletes rows and
re-derives nothing, so paydays `[J, J+14, J+40]` truncated through `J+14` leave a stored end of
`J+39` where the derivation says `J+27`. An on-cadence fixture cannot see that (section 4's own
warning), and it falsifies the byte-identity claim if left out.

**`_reject_overlapping_batch` is REPLACED, not deleted, and the first draft of this step had that
wrong.** Deleting it opens the mid-schedule insert C6 defers behind two unruled questions. Its
successor is ruling **R-PC1**, and an adversarial review corrected the rule twice before it was
written:

- The batch's first payday must be at least `MIN_MATERIALISABLE_CADENCE_DAYS` after the latest
  existing payday, not merely after it. At `L+1` the derivation gives `L` an end of `L`, and
  `ck_pay_periods_date_order` fires as an unhandled 500 until C4 drops it.
- The last paycheck must hold no row dated on or after the new payday **on EITHER clock**:
  `max(due_date, settled_on)`, over transactions AND transfers. `_cash_periods` groups the same rows
  twice -- the budget leg on the stored FK (`:380-387`), the cash leg on
  `spans.containing(fact.settled_on)` (`:428`) against the stored end -- so moving an end re-buckets
  a settled row's cash leg while its budget leg stays. That is `balance:N-128`'s shape. A NULL
  `due_date` needs no branch: `attribution_date` lands it at `period_start`.
- **It closes only the SHORTENING half of P15.** Appending LATER than the cadence lengthens the last
  period and moves a clamped row's render forward; the predicate cannot see that, and the step must
  say which half it closes rather than claim the row.

**The cadence rule (developer, 2026-08-10) applies to ALL FOUR writer doors** -- a batch that
records at least one payday sets `budget.pay_schedule.cadence_days`, one that records none leaves it
alone. Closes **P12** and **P29**. Two corrections ride with it: the column does NOT only project
forward (it sets the last SAVED, id-bearing period's end via `_loader` -> `_derive`, which is what
**P28** measured), and "at least one payday" is satisfiable by a `num_periods=1` batch that records
no SPACING at all -- so the cadence follows a batch of at least TWO paydays, or an owner's first.
**P30** then has to be answered rather than deferred again: this rule makes extend PERSIST the value
that sets the derived horizon, through a form rendering no control for it.

**UNRULED FORK, and it decides whether N-127 closes.** Does the writer rewrite the WHOLE payday list
or only the preceding period's end? Under "preceding only" an interior hole is never repaired by a
forward append, so N-127 stays open. Under "rewrite every row" it is -- but the UPDATE lands on
periods `classify_period_lock` hard-locks (historical, settled, posted), moving `attribution_date`'s
clamp and `_PeriodSpans.containing` for every settled day in them, and the generate path consults no
classifier. **Ruling needed before this leaf is written.** Note R-PC1 forbids the direct repair
regardless: the hole day is not after the latest payday.

- [ ] **C4 -- drop the derived columns.**

**P8's write-door half is DONE** (`balance:X-ad-a`), so the backfill below is no longer reopened by
the next signup. What remains: the ORM-relationship readers of section 3 take their bounds from the
calendar rather than from `txn.pay_period`, and `models/pay_period`'s
`MIN_MATERIALISABLE_CADENCE_DAYS` drops -- its whole subject is the authored `end_date` this step
deletes. Then ONE migration: backfill the schedule row for every owner with paydays,
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
its two call sites (`recurrence_engine.py:309`, `transfer_recurrence.py:81`), and
`PlacementOutcome.SCHEDULE_GAP` with its **six** further references (`recurrence_engine.py:253`,
`recurrence/_occurrence.py:235,250,656,704`, `recurrence/_reading.py:126`). Every one exists to
describe or police a state the model can no longer produce. Ticks recurrence **R-F10**.
Deletion-only: the recurrence arc's 430-shape baseline must stay byte-identical.
**`PeriodCalendar.__post_init__`'s two refusals were on this list and are NOT any more** -- C2-b2
deletes the class that holds them, three leaves earlier, which is row **P25**'s shape a second time.
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
`filing_period` to be TOTAL -- to CLAMP rather than answer `None` -- so it is the reason the filing
question survives as a second question at all rather than collapsing into containment. *It forced
`resolve_anchor_pay_period` before C2-d, which deleted that chain; the obligation moved, it did not
go.* Three options, and the trace decides: DROP and derive, make it NULLABLE, or KEEP it as a
deliberate materialization with the second definition's cost stated. Sequenced after C4 (developer,
2026-08-09), because a derived paycheck should be derived from the calendar this arc normalizes
rather than from the one it is replacing. Closes **P18**.

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
1). Rejected 2026-08-10 on `shekel-prod-db`, because its premise is false:
**14 days carry TWO paychecks for one date** and **35 of 327 entries** are dated outside their own
paycheck. The measurement of record is `filing_period`'s docstring and row **P18**, which also shows
the column holds TWO relationships -- a COPY on 174 of 174 transaction-sourced entries, a derivation
only for assertions. That is `C7`'s subject, not this step's.

**Keep `period_index` stored and derive only `end_date`.** Half the normalization, keeping the half
that needs the advisory lock and the uniqueness constraint. *The first draft rejected it on a claim
adversarial review REFUTED -- that the index is never a persisted reference or a wire key; it was
both.* It SURVIVES on better grounds: the index's only stable referent is its position in payday
order, so storing it stores the functional dependency **P1** describes. Its one IDENTITY use, the
truncate form, was wrong before this arc and C3-a re-keyed it onto `id` regardless.

## 7. Document rules (GATED)

**Moved to `conventions.md`**, one copy for every arc. `tools/plan_gate/` grades this document
against them through a pre-commit hook scoped to it -- so EDITING THIS FILE is what runs the gate.
This document's own caps live in the gate's constants beside the other arcs'.
