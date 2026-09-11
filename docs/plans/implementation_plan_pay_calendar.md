# Implementation Plan: The Pay Calendar

## Where this stands

**JUST LANDED: `C17-a` (`6caf56bc`, PR #310) and `C17-b-1` (`1ae0cd02`, PR #311)**, both `$0.00`. A
pay schedule is a SEQUENCE OF ERAS: `budget.pay_eras` holds one row per *how I have been paid since*
and `effective_from` IS the grid's phase (**R-PC66**); `_derive.py` gave up its forward continuation
to `_projection.py`. **NEXT: `C17-b-2`**, the leaf that MOVES MONEY -- every reader asks the era
covering its own day and anchors on that era's phase. It takes its own review pass and its own PR;
`steps.md` carries the order of the leaves behind it.

**BUILT AND TICKED**: `C1`; `C2` whole, which is one step under three names (`balance:X-l`,
`recurrence:R-F12`), ticked at `C2-f3e`; `C3`; `C4` whole; `C10` and `C11`, which came out of
`C2-f3`; `C13-a`, `C13-b`; `C14` whole, archived 2026-09-11; `C17-a` and `C17-b-1`. Section 4
carries each commit, and `steps.md` alone carries the ORDER. **A cold session starts at section 4**;
the shared registries are `ledger.md`, `steps.md`, `conventions.md` and `verification.md`.

## The rulings

**This arc's rulings are in `rulings.md`, rows whose `arc` is `pay_calendar`.** The key is
`(arc, id)` and no arc document states a ruling; cite one as `pay_calendar:R-PCnn` wherever the bare
id could be another arc's (`conventions.md` rules 9 and 10).
**`R-PC2` also answers the `recurrence` arc's `F-10` fork**, a pay-period HOLE, and is recorded once
(rule 16).

---

## 0. Sequencing against the other two arcs

Three arcs are live. **A step with no code yet has no measured file set**, and the first draft
claimed one for C1-C3 anyway. The surface `C4` had to cross was measured by AST over `app/` on
2026-08-08 and is a HISTORICAL figure: `.end_date` 35 files / 72 accesses (33 of them `PayPeriod`),
`.period_index` 21 files / 60 accesses. **`C4-c` crossed it**; the same census over `app/` today
returns ZERO reads of either name reached through a `budget.pay_periods` row, and every surviving
`.end_date` is a `DerivedPeriod`, `TrendPoint`, `SchedulePeriod` or `RecurrenceRule`.

C2 is not merely adjacent to the balance arc's **X-l**, it IS that step, and it is also recurrence
**R-F12**: three arcs asking for one total calendar. X-l's stated root -- "the pay calendar is a
PARTIAL function... past the last row every consumer improvises and the improvisations disagree"
(`docs/audits/balance_architecture/README.md:604-607`) -- is what this arc's C2 supplies by deriving
the calendar from the paydays. C2 is the third NAME for one value, not a third step.

**Both cross-arc collisions this section was written for are settled in CODE**, and each is kept to
one line because the commit is the record. C3 / `balance:X-ad`: `X-ad-a` deleted the registration
bootstrap payday and writes a `PaySchedule` row instead, so C4's **P8** backfill is not reopened by
the next signup. C3 / `recurrence:R7c`: `R7c-c` (`d9f5c1a48b73`) dropped
`recurrence_rules.offset_periods`, so the phase derives from the rule's first occurrence on every
read and no stored ordinal is left for an inserted payday to re-phase (row **P11**, closed).

---

## 1. Root cause

`budget.pay_periods` STORED three values per row and only one of them was a fact. **`C4-c` dropped
the other two on 2026-09-01 (`c703e1c7`), so this section is the arc's ROOT CAUSE as it stood, kept
because everything below is argued from it.** The table holds `start_date` and nothing else now.

| column | is it a fact? | what it actually was |
|---|---|---|
| `start_date` | **yes** | the day money arrived |
| `end_date` | no | `lead(start_date) - 1` -- the day before the NEXT payday |
| `period_index` | no | `row_number() - 1` over the user's paydays in date order |

`pay_period_service`'s own module docstring stated the derivation it then did not enforce
("end_date = day before next payday"); what the writer computed was `start_date + cadence_days - 1`,
which equals the definition only when the next batch happens to start at `start + cadence`.

**Two derived values stored beside the fact they derive from, with nothing reconciling them.** Every
symptom here is one disagreement: `end_date` BELOW the next `start_date` is a GAP, a day funded by
no paycheck (row P2); at or ABOVE it is an OVERLAP, a day funded by two; and `period_index` order
disagreeing with `start_date` order is the balance resolver walking money out of calendar order.

Because the schema cannot make them agree, the application grew
**five separate runtime fences that all police the same functional dependency**:

**FOUR OF THE FIVE ARE ALREADY GONE, and `C4-c` (`c703e1c7`) is what took them** -- which is this
section's own argument arriving, not a correction to it. Re-derive rather than re-read: the table
below once carried five line-range cites and every one of them had rotted by 2026-09-11.

| fence | where it stood | now |
|---|---|---|
| `_reject_overlapping_batch` -- one-directional, which IS row P2 | `pay_period_service.py` | DELETED; the file names it in prose only |
| `PeriodCalendar.__post_init__` -- the same property at the value boundary | `recurrence/_calendar.py` | the module MOVED to `pay_calendar/_calendar.py`; the guard survives |
| `_pp_assert_structure` -- the same property in the test suite | `tests/_test_helpers.py` | DELETED |
| `integrity_check` BA-03 / BA-04 -- the same property in weekly SQL | `scripts/integrity_check.py` | DELETED at `C4-c` and NOT replaced, which that file says at its own `:331` |
| `uq_pay_periods_user_index` + `ck_pay_periods_date_order` | the schema | both DROPPED; `uq_pay_periods_user_start` is what stands |

Regenerate with:

```bash
grep -rn '_reject_overlapping_batch\|_pp_assert_structure\|BA-03\|uq_pay_periods_user' \
     app/ tests/ scripts/
```

Not one of them would exist under the normalized model, because none would have a subject -- and the
four that have gone went exactly that way, by their subject being removed rather than by being
argued with.

## 2. Evidence

**Archived to `historical/pay_calendar_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings above state what was decided and the code states what
was built. Cite the archive for how a decision came to be, never for what is true now.

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

budget.pay_schedule            -- the OWNER's facts, one row per owner with a payday:
  user_id       UNIQUE          -- rolling_enabled, rolling_target_periods, history_opens_on
  -- DROPPED [C17-a]: cadence_days, shift_id, nominal_anchor -- the RHYTHM is an era's

budget.pay_eras                -- [C17-a] one row per "how I have been paid since"
  user_id       FK budget.pay_schedule.user_id RESTRICT  (fk_pay_eras_schedule)
  effective_from DATE NOT NULL  -- the era's first NOMINAL payday, and so the grid's PHASE
  kind_id       FK ref.pay_cadence_kinds          -- fixed_days; monthly, semi_monthly [C17-d]
  cadence_days  INT NOT NULL    -- ck_pay_eras_cadence_range
  shift_id      FK ref.business_day_shifts        -- the convention (R-PC47)
  UNIQUE (user_id, effective_from)                -- uq_pay_eras_user_effective_from
```

An era governs from its `effective_from` to the next era's; the EARLIEST also runs backward below
the record, bounded by `history_opens_on` (**R-PC66**). Everything else is derived, once, by one
producer:

```text
period_index = row_number() over (partition by user_id order by start_date) - 1
end_date     = coalesce(lead(start_date) over (...) - 1,   -- the definition.  NOT
                                                           -- "- INTERVAL '1 day'", which
                                                           -- returns a timestamp
                        projected_payday(start_date, era, 1) - 1)  -- the open last one:
                                                           -- the day before the NEXT nominal
                                                           -- payday, displaced (C14-c, C14-e-3)
```

**The value type exists**: `PayCalendar` derives `(period_id, period_index, start_date, end_date)`
per period from the paydays alone. **But "consumers do not change shape" is FALSE for one class of
reader and saying so is the correction** (adversarial review, 2026-08-08): the hot path reads the
bounds off the ORM RELATIONSHIP, not off a calendar --
`period = txn.pay_period; attribution_date(txn.due_date, period.start_date, period.end_date)` on
every grid, dashboard and account render. ***That spelling no longer exists***: `C4-a-2` deleted
`utils.dates.attribution_date` for `DerivedPeriod.attribution_day`, so the shape a remaining reader
has is `period.end_date` off the relationship rather than a three-argument call. `PayPeriod.label`
(`models/pay_period.py:73-85`) is a model property built from `end_date`. Those callers hold a
`Transaction`, not a calendar, so C4 is a seam signature change for them and C1's oracle proves
nothing about it. ***The four sites this paragraph used to NAME are not the live set and two of them
had already migrated*** (AST census 2026-08-25): `calendar_service` takes a `DerivedPeriod` since
`C2-f2`, and `routes/transactions/_helpers.py:156` reads only `.user_id`, which survives C4.
**Section 4 carries the measured census** rather than a list here that goes stale between the two.
**Two constraints on the producer, both structural:**

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

### How a transaction's owner is proved -- RULED, BUILT, and closed

Finding **P75**, CLOSED at `C13-b`. **`budget.transactions` HAS a `user_id` since `C13-a`**, held
equal to both its account's owner and its paycheck's by a composite key each, and every door reads
it since `C13-b`. *This section opened with the sentence "`budget.transactions` carries no
`user_id`" until 2026-09-02 -- the same decay `C13-a` corrected in P75's own ledger cell.*

**What the ruling was.** `C2-f3e` left the question open and offered two answers: the owner's
CALENDAR (it holds one owner's schedule, so a foreign id is ABSENT) against a `user_id`-filtered
JOIN (one indexed query, refusing by never RETURNING the row).
**Ruling `R-PC32`, 2026-08-27, took NEITHER.** Both are ways of ASKING, and ruling for either would
have left two structural answers to one question on adjacent doors -- the denormalisation this arc
exists to remove. The third answer makes the question unanswerable by making the state
unconstructible. *The JOIN arm cited a live spelling, `Transaction.pay_period.has(user_id=owner_id)`
in `statement_match/_candidates.py`; that call no longer exists anywhere in `app/`, which is a
second reason the fork as recorded could not have been decided from this text.*

**All nineteen are RETIRED at `C13-b` (`e2c325dc`), and the PREDICATES are what say so.** A list of
line numbers in a planning document cannot survive the code -- these had already drifted twice -- so
what this section keeps is the two greps that REGENERATE the census, run last on 2026-09-03 and
returning prose only:

- **The RELATIONSHIP walk** -- `grep -rn "\.pay_period\.user_id" app/`, keeping the reads that
  REFUSE. Eleven after `C2-f3e`; ZERO live sites now, each one equality on `X.user_id`.
- **The PRIMARY-KEY refetch**, P51's literal wording -- a `PayPeriod` fetched by id and compared.
  Eight after `C2-f3e`; ZERO now. They went to the owner's CALENDAR rather than to the composite
  key, per ruling **R-PC46**: the key answers what may be STORED and a submitted id is a question
  about INPUT, which it does not answer.

**The SCOPES were weighed and REFUSED** -- `statement_match._candidates`' two period-set clauses and
`reconcile_service._rows`', which two comments had predicted this step would move. Each is also what
makes its span lookup TOTAL, so `Transaction.user_id` cannot replace it.

**The STAMP reads moved anyway** (`posting_service`, `loan_posting_service/_payments`,
`transaction_service/_settle`, `routes/transactions/_helpers`, `routes/entries`). They were never
P75's -- counting one as a check is how the first census reached three -- but a row's owner has ONE
home, and leaving them would have kept a hydration for a value the row carries.

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

**Ten of the `C1`-`C2` span's entries are ARCHIVED under rule 5** (2026-08-25) to
`historical/pay_calendar_c1_c2_index_2026-08-25.md`, which carries them one line each, what the span
CLOSED, the three below that stayed and why, and the live `starts` cell that had to move for the
archive to be legal. They had been condensed the same day and that was not enough room; the COMMIT
is the record for every one of them.
**`C1` and `C2-f1` are held for the GATE rather than for the arc** -- three of its controls derive
their only live specimen from them, which both `_staging` docstrings predict and call temporary.

- [x] **C1 -- the derivation, proven equal to what is stored.** `f9d148fe`.
- [x] **C2 -- one calendar value answers every "which period" question.** `4f134bf4`. The DECOMPOSED
      parent, ticked at `C2-f3e`; that tick is also `balance:X-l` and `recurrence:R-F12`.
- [x] **C2-f1 -- the three the calendar already answered.** `792e3b21`.
- [ ] **C10 -- the salary package reads the OWNER's day.** Five sites answer "which paycheck am I
      in" as `period_containing(date.today())` (census 5 code lines `period_containing` in
      `app/routes/salary/**/*.py`), FOUR of whose line numbers this row carried had drifted by
      2026-09-11 -- having taken the derivation at `C2-f2d-3` and kept the process clock.
      **`C2-f3a` CLOSED P49 and was wrong to**; its adversarial design review caught that before the
      commit. Five one-line reads, in a step of their own because a clock change on money-adjacent
      screens gets its own review. **It grows the INSTRUMENT** (`balance:N-138`, re-keyed here
      2026-09-03): a pylint checker forbidding the process clock -- `date.today()`,
      `datetime.now()` -- outside one clock module, so the five reads stay moved. Closes **P49**,
      **N-138**.
- [ ] **C11 -- the LAYER predicate.** The four service modules that still open their own read pass
      take one instead -- `calendar_service`, `investment_dashboard_service/_context` and
      `/_orchestrator`, `tax_report_service` -- and the gate becomes the layer rule rather than a
      per-render count: no module under `app/services/**` calls `BalanceContext.build`.
      `loan_recurrence_sync` is a WRITER and takes its own by design, so the rule carves it out or
      takes it from its caller. Collapses the +1 `C2-f3a` left on `/analytics/taxes`. Closes
      **P56**, **P69**.
**The `C14` span is ARCHIVED under rule 5** (2026-09-11) to
`historical/pay_calendar_c14_as_built_2026-09-11.md`, one line each below; the COMMIT is the record.
- [x] **C14 -- the pay schedule carries its shift convention.** `5d14e4d4`. The DECOMPOSED parent,
      ticked with `C14-f` (**R-PC47**, **R-PC54**-**R-PC57**, **R-PC59**-**R-PC61**, **R-PC63**).
- [x] **C14-a -- the shared business-day module.** `088339f5`. `app/utils/business_days.py`; its
      displacement may answer OUTSIDE the calendar bounds (the CALLER bounds it) and REFUSES a
      non-member convention rather than defaulting one.
- [x] **C14-b -- the convention column.** `229f0e23`. Opened **N-493**, **N-494**.
- [x] **C14-c -- ONE end rule.** `659260c0`. Opened **N-495**, **N-496**.
- [x] **C14-d -- the floor asks `projected_payday`.** `c1ce08b5`. Opened **PC-497**.
- [x] **C14-e-1 -- the rhythm is ONE value.** `f32c9d7a`.
- [x] **C14-e-2 -- the grid carries its own phase.** `ab5b26bc`, migration `a1c7e5d20f43`. Closed
      **PC-497** fault 2.
- [x] **C14-e-3 -- the shift goes live.** `ed267298`. **MOVED MONEY.** Closed **N-398** and
      **PC-497** fault 1 (the row was carried open through two re-points in error).
- [x] **C14-f -- the generate door asks one job's questions.** `5d14e4d4`. **P80** re-pointed at
      `C17` as an era question; **N-493**, **N-494** narrowed.
- [ ] **C18 -- a payday may be recorded BEFORE the schedule's earliest, and a period below the books
      generates nothing** (ruling **R-PC62**; closes **PC-499**, **PC-500**).
      `_reject_backward_payday` bounds a batch after the LATEST payday, where its own docstring says
      the only thing left to refuse is one INSIDE a paycheck (**C6**'s); it narrows to
      *strictly inside `[min(paydays), the last paycheck's end)`*, tested on EVERY new payday, since
      the narrowed rule is not monotone. And what it admits must not GENERATE: a prepend wrote a
      `$531.94` Van Payment moving 173 figures. The bound is PER ACCOUNT (openings stagger 03-26 to
      06-26); the date lives on `budget.transfers.occurs_on`.
- [ ] **C17 -- a pay schedule is a SEQUENCE OF ERAS** (rulings **R-PC58**, **R-PC66**; split
      2026-09-11 into four leaves, **R-PC69**). `budget.pay_eras` holds one row per
      *how I have been paid since* -- `effective_from`, `kind_id`, `cadence_days`, `shift_id` --
      beside a `budget.pay_schedule` that keeps only the owner's facts, and
      **`effective_from` IS the grid's phase**: R-PC61's anchor is absorbed into it, not carried
      beside it. An era governs from its `effective_from` to the next era's; the EARLIEST also runs
      backward below the record, bounded by `history_opens_on`. The DECOMPOSED parent, ticking with
      `C17-d`.
- [x] **C17-a -- the relation.** `6caf56bc`, migration `6fc77e86d76f`. One era per owner backfilled,
      phased on the record's opening; the three columns dropped; every reader takes the LATEST era's
      rhythm where it took the row's (`$0.00`). A batch mints an era only where it STATES a rhythm
      the covering era does not hold, and retires every era past the surviving record. Narrowed
      **N-494** to the one top-up that restates an era; closed N-492's write half. Left
      `pay_period_write.py` at 1,000 of 1,000 (**PC-507**).
- [x] **C17-b-1 -- the forward continuation leaves `_derive.py`.** `1ae0cd02`. A pure move of
      `project_period_after` and `covering_projection` into `pay_calendar/_projection.py`, between
      `_derive` and `_searches`. Closed **PC-498**.
- [ ] **C17-b-2 -- readers anchor on the era's phase.** **MOVES MONEY, OWN PR.** Every reader asks
      `pay_rhythm.era_covering(eras, day)` for its OWN day and anchors on that era's
      `effective_from` rather than on a recorded cash payday: `derive_periods`' last end,
      `_projection.project_period_after`, `_backdated_paydays`, and `_reject_backward_payday`'s
      floor. That restores `C14-c`'s premise that the probe window's estimate and its candidates
      share ONE anchor (**N-495**). Worked: a last recorded payday of 2030-11-27 -- the nominal
      11-28 Thanksgiving displaced under `prior` -- projects a next payday of 12-11 today where the
      era's grid says 12-12, one paycheck and every boundary after it a day early; `$0.00` on
      production, whose one era has convention `none`. Closes **N-495**, **PC-502**, **N-492**;
      carries **N-496**, **PC-505**.
- [ ] **C17-c -- the doors ask for the ERA.** The DECOMPOSED parent, split 2026-09-11 (**R-PC71**)
      into the pure-move split of `pay_period_write.py` and the door rewrite it makes room for; it
      ticks with `C17-c-2`.
- [ ] **C17-c-1 -- `pay_period_write.py` leaves the ceiling.** A PURE move of part of the writer
      (1,000 of pylint's 1,000 lines after `C17-a`, **PC-507**) into a sibling module, its own
      commit and PR, `$0.00`. The CUT is a fork this leaf's session presents and the developer rules
      before it lands (**R-PC60**, **R-PC69** precedents); PC-507's row names the candidate seam
      (the batch shape against the doors) without deciding it. Closes **PC-507**.
- [ ] **C17-c-2 -- the doors** (**R-PC64**, **R-PC67**, **R-PC70**). Registration and first-time
      generate take the NOMINAL first payday with the cadence and convention; `regenerate` is the
      era-mint door, its rebuilt tail the new era's grid, REFUSING a first payday that skips a whole
      paycheck of the old era -- `reject_unconfirmed_gap`, `PayPeriodGapRequired`, `confirm_gap` and
      the banner are DELETED, since a hole is unrepresentable; `reset` wipes eras with the periods.
      That door is also the repair for an era a moved holiday set makes illegal (**R-PC70**: the
      read path is loud, no sweep), so the recovery page offers it; and the top-up of an owner
      truncated below their latest era's day, which restates that era and is judged, stops meeting
      an unhandled `ValidationError` (**N-494**'s one surviving path). Closes **PC-504**, **P80**,
      **N-493**, **N-494**.
- [ ] **C17-d -- the day-of-month cadence KIND** (**R-PC68**; one commit with `recurrence:R13`).
      `monthly` and `semi_monthly` join `ref.pay_cadence_kinds`; `_grid.nominal_payday`,
      `cadence_steps_to` and `PayCadence.periods_per_year` branch on the era's kind, so a
      semi-monthly owner's paydays land on the 1st and 15th and their year holds 24 rather than
      `365.2425 / 15 = 24.35`. **MOVES MONEY** for such an owner; `$0.00` on production. P78's eight
      fixtures go through the door. Closes **P78**; carries **N-399**, closing it only where the
      kind ends `_month_ordinal`'s walk per prior payday (measured, not asserted).
- [ ] **C15 -- the retire-later solve runs only when an assumption moved** (ruling **R-PC52**;
      closes **P60**). The readiness card re-solves the retire-later binary search -- about nine
      projection walks of pure compute no query cost covers -- on every refresh, so a slider-only
      change went `159 -> 426` ms while its queries fell `100 -> 97`, the PRICE of P59's fix. The
      card is split so the solve refreshes only when an assumption it reads has changed and a
      stepper move costs one probe; the developer rejected accepting the cost though it sits inside
      the 500 ms input debounce.
- [ ] **C13 -- a transaction's owner is a COLUMN.** RE-OPENED 2026-09-03 for a third leaf, `C13-c`
      (**R-PC48**); its first two shipped at `e2c325dc`. The DECOMPOSED parent, ruling **R-PC32**,
      split in two 2026-09-02 and ticked with `C13-b`. Closes **P75**.
- [x] **C13-a -- the KEY.** `8e707c4c`. Migration `d4a92f6b13c8`: `user_id` backfilled from
      `pay_periods.user_id`, then both composite FKs, the `auth.users` one `ON DELETE RESTRICT`
      rather than CASCADE (developer, 2026-09-02); its docstring is the record.
      **What a later reader must obey**: the backfill reads the PAY PERIOD, so
      `fk_transactions_owner_ACCOUNT` grades it and the period key grades nothing.
- [x] **C13-b -- the READERS.** `e2c325dc`. The nineteen retired per site (**R-PC46**): eleven walk
      a row that EXISTS and became `X.user_id`, eight refetch a SUBMITTED id and went to the owner's
      CALENDAR, and four route copies of `_get_owned_period` are gone. Closed **P75**, **N-373**.
      **What a later step must obey**: the period-set SCOPES in `statement_match._candidates` and
      `reconcile_service._rows` were weighed and REFUSED -- each is also what makes its span lookup
      TOTAL. As-built: `historical/c13b_as_built_2026-09-03.md`.
- [ ] **C13-c -- eight keys hold six more tables' rows to ONE owner** (ruling **R-PC48**; finding
      **P83**). `budget.transfers` carries `user_id`, `from_account_id`, `to_account_id` and
      `pay_period_id` with no key holding them to one owner, and the same shape stands on
      `transaction_templates`, `ledger_accounts`, `savings_goals`, `statement_imports` and
      `journal_entries` -- **P75**'s defect on six more tables, 0 mismatched rows on production
      across all seven column pairs, expressible-not-present. `fk_statement_matches_owner` and
      `fk_account_external_identities_owner` are already this construction, so each is a superkey
      plus a composite foreign key -- three on `transfers`, one on each other table, eight keys --
      in ONE migration, and it DELETES `integrity_check.py`'s DC-09, the hand-written fence for
      exactly this one table over, rather than joining it (`CLAUDE.md` rule 14). Filed here as
      `C13`'s third leaf though the tables span three arcs: a migration does not care which arc a
      table's steps live in.

*`C12` (one current-paycheck producer) moved to the `salary` arc on 2026-09-03 (**R-SAL1**) with its
rows **P62**, **P63** and **P64**'s engine half; its specification is
`implementation_plan_salary.md`, section 4, under its unchanged id.*

- [x] **C3 -- the writer writes paydays, forward-only.** `7e3fb33b`, as-built in
      `historical/pay_calendar_as_built_2026-08-16.md`. **Must not be undone**: `pay_period_write`
      is the ONE place in `app/` that constructs or deletes a pay period, and R-PC1's coverage half
      is DELETED.
- [x] **C4 -- drop the derived columns.** `327a70f2`. The DECOMPOSED parent, eight leaves; ticked
      with `C4-d`. Closed **P1**, **P4**, **P5**, **P8**, **P9**.
- [x] **C4-a-1** `8962e073` + `2895f693` -- the balance seam's clamp. Closed **P38**.
- [x] **C4-a-2** `82bd762c` -- reconcile panel; `utils.dates.attribution_date` DELETED (**R-PC31**).
- [x] **C4-a-3** `a0fb14ba` -- purchase-date warning (**R-PC34**, **R-PC35**). `$0.00`.
- [x] **C4-a-4** `f18a58af` -- destination picker (**R-PC36**-**R-PC38**). `$0.00`.
- [x] **C4-a-5** `95b2dc67` (+ `ce96887a`) -- the two LABEL readers (**R-PC39**). Opened **N-413**.
- [x] **C4-b** `5db9f8a0` -- an owner with paydays HAS a recorded cadence; the decomposed parent
      (**R-PC40**).
- [x] **C4-b-1** `eb6597ae` -- every test owner's calendar comes from the doors that own it. Closed
      **N-392**; opened **P78**.
- [x] **C4-b-2** `5db9f8a0` (+ `2e3c609e`), migration `f1c8b3d5e920` -- the key,
      `ON DELETE RESTRICT` (**R-PC41**, **R-PC42**). Closed **P8**, **P35**.
      **A LATER STEP MUST OBEY**: its backfill reads the PAYDAYS, not the stored span -- restoring
      `(end - start) + 1` writes a 2x-wrong cadence (**P28**).
- [x] **C4-d** `327a70f2` -- a calendar HAS a cadence (**R-PC45**). Opened **P81**, **P82**.
      **A LATER LEAF WANTING AN EMPTY CALENDAR TAKES `bare_user_with_cadence`, never `bare_user`.**
- [x] **C4-c -- the drop.** `c703e1c7`, migration `b7a41e2c9d63`. Closed **P1**, **P4**, **P5**,
      **P9**, **P26**, **P27**, **P28**, **P33**, **P53**, **P70**; opened **P79**, **P80**. As
      built, with the whole `C4` span: `historical/c4_as_built_2026-09-06.md`.
- [x] **C5 -- the gap machinery goes, and a paycheck may owe one template twice.** `4e8b40b3`. The
      decomposed parent, ticked with `C5b`. This span is COMPLETE and condensed under rule 5, to buy
      the room `C4-b`'s decomposition needed; the commits are the record and `steps.md` carries each
      row's own sentence.
- [x] **C5a -- delete what is now unconstructible.** `fe365de1`. Ticked at `C2-b2`; ticks
      `recurrence:R-F10`.
- [x] **C5b -- a paycheck may owe one template more than once.** `4e8b40b3`. One commit under two
      arc names with `recurrence:R17`; closed **P16**.
- [ ] **C6 -- a payday may be inserted mid-schedule.**

**Starts with the two rulings section 3 names**, neither of which the 2026-08-08 lock ruling
answers: what happens to a row whose date `attribution_date` would now CLAMP into the wrong half,
and whether the newly split-off payday is repopulated (understated income) or not (a doubled bill).
Not required by the normalization and deliberately last. **It also takes P46** (**R-PC49**,
2026-09-03): the period-move control offers every paycheck the owner holds and
`classify_period_lock`, which this step already consults, is the one thing that refuses a move.
Closes **P10**, **P46**.

- [x] **C7 -- the ledger entry KEEPS its paycheck.** `a9e04106`, `f7cf292e`, `c725b814`. Ruling
      **R-PC53** holds the argument and every measurement.
      **P18's premise fits NEITHER half of the column**; NULLABLE refused.
      **What a later step must obey**: the argument lives in `rulings.md`, NOT in docstrings -- two
      adversarial reviews measured this step's own first prose false in eleven places, which is why
      `c725b814` stripped it. Closed **P18**, **N-490**; opened **N-491**, owned by **C16**.

- [ ] **C16 -- the ledger's two filing rules stop being one column to its reader.**

**Starts with a ruling**, as C7 did. R-PC53 established that the column has two halves; this step
decides what to do about one READER being unable to tell them apart.
`ledger_report_service/_income_statement._pay_period_statement_nets` filters the single column and
adds both populations: measured 2026-09-04, **`$61,633.27`** of Income+Expense on the budget clock
against **`$10,653.91`** of Income+Unrealized on the cash-date clock. Three remedies and the trace
decides: SPLIT the column so the two rules are separately addressable; DISAMBIGUATE at the reader,
which may mean partitioning by source kind or stating the basis; or ACCEPT and document, on the
argument that a pay-period income statement legitimately mixes them.
**The statement's own structure already segregates part of it** -- an `account_trueup`'s counter leg
books to a per-account Change in Value row, which `net_income` cannot count and
`comprehensive_income` states -- so the exposure is narrower than the raw `$10,653.91`, and
**this step owes a measurement of the Income-only slice before the fork is put**.
**MOVES NO MONEY**: it changes what a report SAYS, not any balance. Closes **N-491**.

- [ ] **C8 -- the forecast cadence gets ONE control.**

Recording paydays and setting the forward cadence are two operations welded onto one form, on
generate / regenerate / reset. After C4 the column's only job is projecting past the last recorded
payday, so it is a FORECAST SETTING rather than a property of any batch, and the normalized shape is
one control that sets it beside payday forms that only record paydays. **It also takes P47**
(**R-PC50**, 2026-09-03): the four date-range registers stand, and the reconcile panel and the
dashboard pulse -- one hand-rendered range written twice -- read `period.range_label`.
**C3-b took the extend door's half by DELETING its input** (finding **P29**) and the developer ruled
2026-08-11 that the remaining three are a UX step rather than a writer step, sequenced after C4 so
the control lands on a column whose job has already narrowed. Closes **P30**.

- [ ] **C9 -- the modelled fold projects contributions past the horizon.**

**It SUPERSEDES the balance arc's ruling R-AG** (2026-07-27: "past the pay-period horizon, let the
fold answer and RECORD the half-model rather than capping it"), re-ruled 2026-08-14 by the developer
on evidence that ruling did not have. Past the last saved payday an investment account keeps
ACCRUING while its CONTRIBUTION tier stops, because `_asset_contributions.contribution_events` walks
the SAVED periods -- `+$2,501.92` on Empower and `+$5,427.07` on Property at six months out, which
is row **P7**'s price and the half `C2-e` did not close. R-AG was ruled before a TOTAL calendar
existed; `C2-e` built one, and `/retirement`, `/savings` and `/investment` already project
contributions on `PayCalendar.projection_axis`. **So the balance seam is now the ONE surface that
does not, and the seam disagrees with the three pages built on it** -- which is a stronger reason to
close it than the reason R-AG had to leave it open. **MOVES MONEY, OWN PR**, and its measurement is
a production clone rather than an argument. Closes **P7**; carries **P42**, **P44** and **P50** --
the /savings and /retirement seeding defects `C2-e`'s reviews opened, and the savings-goal divisor
that counts only saved periods -- because every one is the projection's own reach rather than a
reader move. **The CASH tier joins the same projection** (`balance:N-394`, re-keyed here
2026-09-03): the analytics month card froze at the last saved payday because `cash_balance_at` stops
there -- every month to 2030-12 read one identical `$9,539.92` -- and one projection past the
horizon for every tier is this step's rule.

## 5. Findings ledger

**Moved to `ledger.md`** -- this arc's rows are the ones whose `arc` column reads `pay_calendar`. A
finding is not arc-local: `P2` / `F-10`, `P3` / `N-123` and `P6` / `F-12` were each one defect in
two ledgers, and one of those pairs went unnoticed for months.

## 6. Alternatives considered and rejected

**Archived to `historical/pay_calendar_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings above state what was decided and the code states what
was built. Cite the archive for how a decision came to be, never for what is true now.

## 7. Document rules (GATED)

**Moved to `conventions.md`**, one copy for every arc. `tools/plan_gate/` grades this document
against them through a pre-commit hook scoped to it -- so EDITING THIS FILE is what runs the gate.
This document's own caps live in the gate's constants beside the other arcs'.
