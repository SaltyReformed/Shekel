# Implementation Plan: The Pay Calendar

## Where this stands

**`C2` IS DONE, and with it `balance:X-l` and `recurrence:R-F12`** -- one step under three names,
ticked at `C2-f3e` (`4f134bf4`). Built: **C1**, **C2** whole, **C3**. Section 4 carries each commit;
what reached `main` is a MEASUREMENT (`git log --oneline origin/main..dev`).

**`C4` IS DONE.** Decomposed into seven leaves 2026-08-25 (developer) once its reader census was
re-measured -- the leaves ARE that census, which row **P70**'s query-position count structurally
could not see. All five `C4-a` reader leaves, both `C4-b` leaves, `C4-c` (the drop, `c703e1c7`) and
`C4-d` have SHIPPED. `C4-b` was split in two on 2026-09-01 (developer, **R-PC40**) once its real
prerequisite was measured: `C4-b-1` took the TEST CORPUS off hand-built pay periods, `C4-b-2` added
the key, `C4-c` dropped the columns, and
**`C4-d` took the same defect one tier up -- in the TYPE rather than in the schema** (**R-PC45**): a
calendar HAS a cadence, and an owner with no `budget.pay_schedule` row has no calendar rather than
an empty cadence-less one.

**`C10`-`C12` came OUT of `C2-f3`** on 2026-08-19 for gating C4 on work it does not depend on: the
salary package's clock (**P49**, which `C2-f3a` wrongly closed), the layer predicate (**P56**) and
the current-paycheck merge (**P62** / **P63**). **A cold session starts at section 4**; the shared
registries are `ledger.md`, `steps.md`, `conventions.md` and `verification.md`.

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

| fence | cite |
|---|---|
| `_reject_overlapping_batch` -- one-directional, which IS row P2 | `pay_period_service.py:87-130` |
| `PeriodCalendar.__post_init__` -- the same property at the value boundary | `recurrence/_calendar.py:162-182` |
| `_pp_assert_structure` -- the same property in the test suite | `tests/_test_helpers.py:3367-3395` |
| `integrity_check` BA-03 / BA-04 -- the same property in weekly SQL | `scripts/integrity_check.py:353-376` |
| `uq_pay_periods_user_index` + `ck_pay_periods_date_order` | the schema |

Not one of them would exist under the normalized model, because none of them would have a subject.

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
      in" as `period_containing(date.today())` -- `routes/salary/_helpers.py:175` and `:256`,
      `profiles.py:253`, `views.py:63`, `cockpit.py:284` -- having taken the derivation at
      `C2-f2d-3` and kept the process clock. **`C2-f3a` CLOSED P49 and was wrong to**; its
      adversarial design review caught that before the commit. Five one-line reads, in a step of
      their own because a clock change on money-adjacent screens gets its own review. Closes
      **P49**.
- [ ] **C11 -- the LAYER predicate.** The four service modules that still open their own read pass
      take one instead -- `calendar_service`, `investment_dashboard_service/_context` and
      `/_orchestrator`, `tax_report_service` -- and the gate becomes the layer rule rather than a
      per-render count: no module under `app/services/**` calls `BalanceContext.build`.
      `loan_recurrence_sync` is a WRITER and takes its own by design, so the rule carves it out or
      takes it from its caller. Collapses the +1 `C2-f3a` left on `/analytics/taxes`. Closes
      **P56**, **P69**.
- [x] **C13 -- a transaction's owner is a COLUMN.** `e2c325dc`. The DECOMPOSED parent, ruling
      **R-PC32**, split in two 2026-09-02 and ticked with `C13-b`. Closes **P75**.
- [x] **C13-a -- the KEY.** `8e707c4c`. Migration `d4a92f6b13c8`: `user_id` backfilled from
      `pay_periods.user_id`, then both composite FKs. The `auth.users` key is `ON DELETE RESTRICT`
      and NOT `UserScopedMixin`'s CASCADE (developer, 2026-09-02) -- the only candidate shape that
      changed what a user delete does. **What a later reader must obey**: the backfill reads the PAY
      PERIOD, so `fk_transactions_owner_ACCOUNT` is the key that grades it and the period key grades
      nothing; the migration's own docstring is the record.
- [x] **C13-b -- the READERS.** `e2c325dc`. The nineteen retired per site, ruling **R-PC46**: the
      ELEVEN that walk a row that EXISTS became `X.user_id`, the EIGHT that refetch a SUBMITTED id
      went to the owner's CALENDAR, and the four route copies of `_get_owned_period` are gone.
      Closed **P75**, **N-373**. **What a later step must obey**: the period-set SCOPES in
      `statement_match._candidates` and `reconcile_service._rows` were weighed and REFUSED -- each
      is also what makes its span lookup TOTAL. As-built: `historical/c13b_as_built_2026-09-03.md`.
- [ ] **C12 -- one current-paycheck producer.** The THREE implementations become one, which needs a
      RULING first because it changes what `/savings` and `/retirement` publish. The merged producer
      gives `income_service`'s amount basis a threaded calendar, so `balance:X-i1` and this step
      decide for each other. It also owes `paycheck_calculator.py`'s 1000-line ceiling, which
      `recurrence:R-F16` took from EXACTLY 1000 to 873 on 2026-08-19 -- so the headroom is real now
      and this step's own growth is what would spend it (re-measured at C2-f3e; row **P64** and
      section 0 said zero and were stale). Closes **P62**, **P63**, **P64**'s engine half.
- [x] **C3 -- the writer writes paydays, forward-only.** `7e3fb33b`, as-built in
      `historical/pay_calendar_as_built_2026-08-16.md`. **Must not be undone**: `pay_period_write`
      is the ONE place in `app/` that constructs or deletes a pay period, and R-PC1's coverage half
      is DELETED.
- [x] **C4 -- drop the derived columns.** `327a70f2`. The DECOMPOSED parent, split into seven leaves
      2026-08-25 and into EIGHT on 2026-09-01 when `C4-b` split in two (**R-PC40**); it ticks with
      `C4-d`, its last open leaf. Closes **P1**, **P4**, **P5**, **P8**, **P9**.
      **A later leaf obeys the PREDICATE, never a count**: a read of `end_date` or `period_index`
      reaching through a `budget.pay_periods` ROW. Reader census as built:
      `historical/c4_reader_census_2026-09-02.md`.
- [x] **C4-a-1 -- the balance seam's attribution clamp.** `8962e073` + `2895f693`. Closed **P38**.
- [x] **C4-a-2 -- the reconcile panel, and the clamp moves onto the value.** `82bd762c`.
      `utils.dates.attribution_date` DELETED for `DerivedPeriod.attribution_day` (**R-PC31**).
- [x] **C4-a-3 -- the purchase-date warning, and `DerivedPeriod.covers` lands.** `a0fb14ba`.
      `check_purchase_date_in_period` DELETED (**R-PC34**, **R-PC35**). `$0.00` on production.
- [x] **C4-a-4 -- the merchant-destination picker.** `f18a58af`. `destinations_for` takes the
      CALENDAR and no `owner_id` (**R-PC36**-**R-PC38**). `$0.00`.
- [x] **C4-a-5 -- the two LABEL readers, and the model accessor goes.** `95b2dc67` (+ `ce96887a`).
      `PayPeriod.label` DELETED; the COLUMN is `C4-c`'s (**R-PC39**). Opened **N-413**.
- [x] **C4-b -- an owner with paydays HAS a recorded cadence.** `5db9f8a0`. The DECOMPOSED parent,
      ticked with `C4-b-2`; what split it in two was that the key's prerequisite turned out to be
      the TEST CORPUS rather than any reader (**R-PC40**).
- [x] **C4-b-1 -- every test owner's calendar comes from the doors that own it.** `eb6597ae`. The
      hand-built `PayPeriod(...)` sites that built an ORDINARY owner go through `record_paydays` and
      `reset_pay_periods`; `_drop_seed_user_bootstrap`'s 135 lines are DELETED. It REFUTED N-392's
      diagnosis: `POST /pay-periods/reset` does perform that resting state. Closed **N-392**; opened
      **P78**. **What `C4-c` must still obey**: read no COUNT of hand-built constructions out of any
      document -- re-run the grep this file's `C4-c` entry states.
- [x] **C4-b-2 -- the key itself, and the fallback goes.** `5db9f8a0` (+ `2e3c609e`). Migration
      `f1c8b3d5e920`; `fk_pay_periods_schedule` is **`ON DELETE RESTRICT`** (**R-PC41**), the
      inferring arm and its ~25 docstrings are gone, and `PayCalendarError` gains the handler
      **P35** deferred (**R-PC42**). Closed **P8** and **P35**. **What a later step must obey**: the
      backfill reads the PAYDAYS, not the stored span -- restoring `(end - start) + 1` writes a
      2x-wrong cadence (row **P28**); and `MIN_MATERIALISABLE_CADENCE_DAYS` now bounds only stored.
- [x] **C4-d -- the cadence type stops admitting a row that cannot exist.** `327a70f2`. Ruling
      **R-PC45**: a calendar HAS a cadence, and `calendar_for` RAISES for an owner with no
      `budget.pay_schedule` row rather than answering an empty one. Six `int | None` declarations
      and two live `cadence_days is not None` guards deleted.
      **A later leaf wanting an empty calendar takes `bare_user_with_cadence`, never `bare_user`.**
      Opened **P81**, **P82**. As built: `historical/c4_d_as_built_2026-09-02.md`.
- [x] **C4-c -- the drop.** `c703e1c7`. Migration `b7a41e2c9d63`, RE-PARENTED onto `c9a4e7b21d58`;
      63 rows byte-identical across that adjacency, the off-cadence control shown able to DISAGREE
      first. `upgrade()` REPORTS a disagreeing stored pair; the downgrade names its three missing
      promises and a one-day period aborts it whole. **What a later step must obey**: a migration
      test predating this head must rewind first (**P79**). Closed TEN rows -- **P1**, **P4**,
      **P5**, **P9**, **P26**, **P27**, **P28**, **P33**, **P53**, **P70**; opened **P79**, **P80**.
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

**"DERIVABLE FROM `entry_date`" IS FALSE, measured 2026-08-10 against `shekel-prod-db` while ruling
C2's first fork. Do not carry the assumption into this step.** 14 days carry TWO different
`pay_period_id` values for ONE `entry_date` (a single owner), so the date does not determine the
paycheck; 35 of 327 entries (10.7%) are dated outside their own paycheck BY DESIGN; and 4
loan-opening entries predate the first payday by up to seven years, so the clamp is live rather than
hypothetical. **The column has 11 references in 7 `app/` modules**, 2 of them docstrings:
`_posting_reconcile.py:186`, `posting_service.py:145,153`, `pay_period_admin.py:876-879`,
`ledger_report_service/_income_statement.py:165`, `account_posting_service/_walk.py:322`,
`loan_posting_service/_payments.py:163`. The income statement GROUPS by it, so it is a real query
key and not dead weight.

- [ ] **C8 -- the forecast cadence gets ONE control.**

Recording paydays and setting the forward cadence are two operations welded onto one form, on
generate / regenerate / reset. After C4 the column's only job is projecting past the last recorded
payday, so it is a FORECAST SETTING rather than a property of any batch, and the normalized shape is
one control that sets it beside payday forms that only record paydays.
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
reader move.

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
