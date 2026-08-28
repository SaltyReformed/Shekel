# Implementation Plan: The Pay Calendar

## Where this stands

**`C2` IS DONE, and with it `balance:X-l` and `recurrence:R-F12`** -- one step under three names,
ticked at `C2-f3e` (`4f134bf4`). Built: **C1**, **C2** whole, **C3**. Section 4 carries each commit;
what reached `main` is a MEASUREMENT (`git log --oneline origin/main..dev`).

**`C4` is IN FLIGHT, is the deepest cut in the arc, and was DECOMPOSED into seven leaves on
2026-08-25** (developer) once its reader census was re-measured: the four ORM-relationship readers
section 3 named were two stale and two moved, and five more were named by no step at all, which row
**P70**'s query-position census structurally could not see. The leaves ARE that census: five take
the readers off the columns one PACKAGE at a time, one makes an owner's recorded cadence a foreign
key, and the last drops the columns. **`C4-a-1` has SHIPPED; `C4-a-2` is next.**

**`C10`-`C12` came OUT of `C2-f3`** on 2026-08-19 for gating C4 on work it does not depend on: the
salary package's clock (**P49**, which `C2-f3a` wrongly closed), the layer predicate (**P56**) and
the current-paycheck merge (**P62** / **P63**). **A cold session starts at section 4**; the shared
registries are `ledger.md`, `steps.md`, `conventions.md` and `verification.md`.

## The rulings

**This arc's rulings are in `rulings.md`, rows whose `arc` is `pay_calendar`.** They moved there at
`balance:X-ao-2a` with `recurrence`'s and `credit_card`'s, finishing what `X-ao-1` began: a ruling
id came from ONE global sequence spelled across five arc documents in THREE grammars, and
`tools/plan_gate` parsed none of them. The key is `(arc, id)`, and NO arc document states a ruling
now. The gate grades that as the SHAPE of a declaration rather than from a list of arcs, and
reconciles the arc map against `docs/plans/` so a SIXTH plan document nobody added to it fails
rather than being passed over. **What it still cannot see is a dated block of decisions with no IDS,
under a heading that does not say `rulings`** -- which is this corpus's own historical shape with
one word changed. The arm that would close it is "an arc document names no ruling id that has no
registry row", and **N-376** is why it cannot be built yet.

**Thirty-two of this arc's thirty-three rulings had no id at all**, which is why the lift was its
own step: only `R-PC1`, the forward-only rule, could be cited, and it is cited from `app/` three
times. The rest are `R-PC2`-`R-PC33`, minted in the order this table recorded them. Two carried no
DATE either -- the gapped-batch rule and the table-and-column-names rule -- and 2026-08-08 is
measured rather than assumed: both landed in `35cdf863`, the commit that created this document,
alongside the rulings that state that date themselves.

**`R-PC2` also answers the `recurrence` arc's `F-10` fork**, a pay-period HOLE, which that arc's own
table carried as a second copy of this ruling. Rule 16 admits one copy, so the second did not
survive the lift and the fork it answered is named in the row that survives.

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

**Both cross-arc collisions this section was written for are settled in CODE**, and each is kept to
one line because the commit is the record. C3 / `balance:X-ad`: `X-ad-a` deleted the registration
bootstrap payday and writes a `PaySchedule` row instead, so C4's **P8** backfill is not reopened by
the next signup. C3 / `recurrence:R7c`: `R7c-c` (`d9f5c1a48b73`) dropped
`recurrence_rules.offset_periods`, so the phase derives from the rule's first occurrence on every
read and no stored ordinal is left for an inserted payday to re-phase (row **P11**, closed).

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
every grid, dashboard and account render. `PayPeriod.label` (`models/pay_period.py:73-85`) is a
model property built from `end_date`. Those callers hold a `Transaction`, not a calendar, so C4 is a
seam signature change for them and C1's oracle proves nothing about it. ***The four sites this
paragraph used to NAME are not the live set and two of them had already migrated*** (AST census
2026-08-25): `calendar_service` takes a `DerivedPeriod` since `C2-f2`, and
`routes/transactions/_helpers.py:156` reads only `.user_id`, which survives C4.
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

### The question `C2-f3e` leaves open: how a transaction's owner is proved

Finding **P75**. `budget.transactions` carries no `user_id`; ownership is its pay period's, so every
door that refuses a foreign row states that relationship BY HAND -- **nineteen** of them in `app/`
after `C2-f3e`, in two shapes. Both censuses were re-run 2026-08-20 after that step's first count of
three was refuted by two adversarial reviews.

**ELEVEN walk the RELATIONSHIP** (`grep -rn "\.pay_period\.user_id" app/`, keeping the reads that
REFUSE: ten are the comparison itself and the eleventh binds the owner one line above the two
comparisons that use it): `utils/auth_helpers.py:347` (`get_accessible_transaction`, the canonical
route-boundary door, reached from eight route sites), `routes/transactions/_helpers.py:447`,
`routes/transfers/_helpers.py:137`, `routes/entries.py:241`, `entry_service/_doors.py` `:655` `:814`
`:935` `:994`, `credit_workflow.py` `:114` `:448`, `recurrence_engine/_conflicts.py:77`.

**EIGHT fetch the row by PRIMARY KEY** and compare, P51's literal wording:
`transfer_service/_ownership.py:61` and `routes/transfers/_instances.py:142` directly, plus
`_user_owns(PayPeriod, ...)` at `transfers/mutations.py:185` `:307` and
`transfers/templates.py:250`, and `_resolve_owned_fks` with a `PayPeriod` spec at
`transactions/create.py:120` `:179` and in `_verify_owned_fks_in_update`.

**NOT this finding**: reads that derive an owner to SCOPE or STAMP rather than to refuse
(`posting_service.py` x4, `loan_posting_service/_payments.py` x3,
`transaction_service/_settle.py:936`, `routes/transactions/_helpers.py:156`). Counting one of those
as a check is how the first census reached three.

**The fork.** `C2-f3e` answered its own three doors with the owner's CALENDAR: it holds one owner's
schedule, so a foreign id is ABSENT. The alternative is already in the house at
`statement_match/_candidates.py:716` -- `Transaction.pay_period.has(user_id=owner_id)` -- ONE
indexed query where the calendar is two plus a derivation, also leaving no Python comparison, and
refusing by never RETURNING the row. **Ruling one way while `C2-f3e` has shipped the other leaves
two structural answers to one question on adjacent doors**, which is the denormalisation this arc
exists to remove. So what is owed is not "which is better" but what each is FOR: the calendar where
the caller needs the PERIOD (a window, a label, a placement), the JOIN where it needs only the row
it was already fetching. Under that rule `C2-f3e`'s three fragments are on the right side of it and
the nineteen above are not.

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
- [ ] **C13 -- a transaction's owner is a COLUMN**, per the ruling of that name. Expand / backfill /
      contract: `user_id` NULLABLE, backfilled from `pay_periods.user_id`; `UNIQUE (id, user_id)` on
      `budget.pay_periods`; both composite FKs; `SET NOT NULL`. P75's nineteen comparisons go in a
      SECOND leaf. **Own review pass**: the busiest table in the schema, and a downgrade cannot
      re-add a constraint a cross-owner write invalidated while it was off. Closes **P75**.
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
- [ ] **C4 -- drop the derived columns.** The DECOMPOSED parent, split into SEVEN leaves 2026-08-25
      (developer); it ticks with `C4-c`. Its FIRST commit landed before the split (row **P70**): the
      rolling top-up's remaining-paycheck count moved onto `PayCalendar.current_and_future` and the
      top-up itself left for `pay_period_rolling`, `pay_period_admin` having had nine lines under
      its ceiling (row **P31**). Closes **P1**, **P4**, **P5**, **P8**, **P9**.

**THE READER CENSUS is now the leaf list**, re-measured by AST over `app/` on 2026-08-25: the four
sites section 3 named were two stale and two moved, and a census keyed on QUERY position cannot see
a RELATIONSHIP read. 118 accesses over 52 files, of which every one but the leaves below survives
untouched; each survivor is an `AttributeError` or a `ProgrammingError` the day the columns go.
**Three sites a reader would expect in it are NOT, checked one at a time**: `calendar_service`
(`:939`) and `pay_period_locks` (`:215`) read `DerivedPeriod` values, and
`settings/_pay_periods_manage.html` has since `C2-f3b`.

- [x] **C4-a-1 -- the balance seam's attribution clamp.** `8962e073` + `2895f693`. Closed **P38**.
      *Its specification here described a build `balance:X-i4` had already made impossible -- five
      public entries each taking a `PayCalendar` -- and the shipped shape changes no public
      signature at all. The commits are the record.*
- [ ] **C4-a-2 -- the reconcile panel, and the clamp moves onto the value.** `_rows.attributed_on`
      reads `txn.pay_period` (`:271-273`) and `_assemble._block_headings` SELECTS
      `PayPeriod.end_date` (`:106`); `lands_on_or_before` and `outstanding_rows` thread between
      them, and the panel holds no read pass, so its calendar comes from the route.
      **This leaf is where `utils.dates.attribution_date` is DELETED** and
      `DerivedPeriod.attribution_day` becomes the rule: its last mis-pairable caller leaves here,
      and shipping a state where every caller holds a `DerivedPeriod` while the three-argument
      signature survives is a signature with no remaining reason. Blocked by `C4-a-1`, which moves
      the other caller.
- [ ] **C4-a-3 -- the purchase-date warning.** `entry_service/_sums` (`:344-345`) plus four route
      call sites, and the containment test becomes `DerivedPeriod.covers`, which also retires the
      open-coded pair at `recurrence_engine/_plan.py:317` and `pay_calendar/_searches.py:77`. This
      leaf owes its own decision: `grid/page._build_grid_row_data` is already at pylint's
      five-argument ceiling, so the calendar needs either a rationale-carrying disable or the
      `entry_lists` build hoisted into its two callers.
- [ ] **C4-a-4 -- the merchant-destination picker.** `statement_match/_candidates.destinations_for`
      (`:923-924`) is the one relationship read left in that package, and `_scope` is its only
      caller. `transaction_candidate` in the same module already resolves through
      `calendar.period_by_id`, so this leaf makes one module answer one way.
- [ ] **C4-a-5 -- the two LABEL readers, and the model accessor goes.**
      `routes/_recurrence_conflict_chooser` (`:193`) renders `period.label` off the ORM row and
      `grid/_transaction_full_edit.html` (`:42`) renders `txn.pay_period.label`. Both move onto
      `DerivedPeriod.label`, which is the SAME rule through `utils.dates.pay_period_label`, and the
      leaf ENDS by deleting `PayPeriod.label` -- so one paycheck can no longer be labelled two ways
      on two screens.
- [ ] **C4-b -- an owner with paydays HAS a recorded cadence.** One additive migration: backfill a
      `budget.pay_schedule` row for every owner holding paydays (0 rows on both databases, measured
      2026-08-25), then
      `FOREIGN KEY (user_id) REFERENCES budget.pay_schedule (user_id) ON DELETE CASCADE` on
      `budget.pay_periods`. `resolve_cadence`'s inferring fallback is then DELETED rather than left
      unreachable, and `MIN_MATERIALISABLE_CADENCE_DAYS`'s subject narrows to the stored end alone.
      Closes **P8** and **P35**. **The double CASCADE is the thing to verify**:
      `pay_periods.user_id` and `pay_schedule.user_id` both cascade from `auth.users`, so deleting
      an owner must not deadlock or fail on ordering, and the migration test drives a real user
      delete rather than arguing from the DDL.

**What deleting that fallback stops raising is row P35's blast radius, and it belongs to `C4-b`
rather than to a ledger cell** (`conventions.md` rule 4). `resolve_cadence` infers an owner's
cadence from their last period's LENGTH when they have no `budget.pay_schedule` row,
`derive_periods` refuses anything outside 1..365, and `app/error_handlers.py` leaves the raise on a
bare 500. Four steps joined renders to it after C2-c widened it to every balance page:

- **`C2-f2b`** -- `/grid` derives the calendar BEFORE it looks at an account, so the zero-ACCOUNT
  render, which used to reach `empty_grid_view()` without one, raises with the rest.
- **`C2-f2e`** -- `/` and both its fragments answer "which period is current" from the derivation,
  so a legacy owner whose stored span no longer covers today reaches the calendar where the pulse
  producer's `None` used to give them the "No pay period covers today" CTA. That page's ZERO-ACCOUNT
  render is deliberately still safe: the account guard runs before the derivation, which is the
  opposite of `/grid`'s order and is stated at the site.
- **`C2-f3b`** -- extend, truncate, regenerate and the settings section derive it too, and a bad
  stored cadence stops being the `ValidationError` those routes flash.
  **RESET does NOT derive one**: it reads its ids through `pay_period_write.owner_period_ids`, so
  the door that rebuilds from a SUBMITTED cadence still repairs this owner -- it SUCCEEDED at the
  merge base, and that regression stood until the step's review measured it.
- **`C2-f3e`** -- the grid's three empty-cell fragments (`/transactions/new/quick`, `/new/full`,
  `/empty-cell`) derive it to prove the submitted `period_id` belongs to the requester, where they
  used to read the row by primary key. Same ordering argument as `C2-f2e`'s and no stronger: a
  fragment is only ever swapped into a `/grid` that derived one to render at all, so an owner who
  can reach these doors has a calendar that derives -- and because htmx does not swap a 500, the
  failure there is a click that silently does nothing rather than a visible error page.

Zero affected owners on either database (re-measured 2026-08-25: one owner with paydays, a schedule
row, cadence 14 on 62 of 62 rows), so this is a state the fallback can produce rather than one it
does.

- [ ] **C4-c -- the drop.**

`pay_period_write._write_derivation` stops authoring the two columns and `PayPeriodOverlapStored`
goes with the stored-versus-derived comparison it raises from; `MIN_MATERIALISABLE_CADENCE_DAYS` and
`PayPeriod.__repr__`'s ordinal go with them. Then ONE destructive migration: `DROP COLUMN end_date`,
`DROP COLUMN period_index`, and **three** constraints with them -- `ck_pay_periods_date_order`,
`ck_pay_periods_positive_index` and `uq_pay_periods_user_index`. It carries the `Review:` line.
**The downgrade is NOT unconditionally lossless and must say so**: re-adding
`CHECK (start_date < end_date)` fails outright on any one-day period this legalises (row **P9**),
and the LAST row's rebuilt end is a projection off `cadence_days` as it reads at downgrade time.
Deletes the four surviving fences of section 1, including `integrity_check` BA-03 / BA-04 / BA-07
and `_pp_assert_structure`'s invariants 1, 2, 3a and 3b, and re-bases the 63 `PayPeriod(...)`
constructions in 35 test files that pass a dropped column. **This leaf needs its own review pass**;
it is the deepest cut into the spine.

- [ ] **C5 -- the gap machinery goes, and a paycheck may owe one template twice.**

**The DECOMPOSED PARENT, split 2026-08-09 (developer).** Its two halves shared only a sentence in
row P16 and are not one commit: one is a pure deletion gated on C4, the other is a migration gated
on an arc this document does not own. It ticks with the last of them.

- [x] **C5a -- delete what is now unconstructible.** `fe365de1`. Ticked at **C2-b2** rather than
      after C4: nothing in the gap chain read a stored column, so it went dead when that leaf
      pointed the engine at the derivation.

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
