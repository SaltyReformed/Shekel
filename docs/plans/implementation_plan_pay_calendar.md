# Implementation Plan: The Pay Calendar

## Where this stands

**Built:** **C1**, **C2-a**, **C2-b** (both leaves, which also ticked **C5a** and the recurrence
arc's **R-F10**), **C2-c**-**C2-e**, **C2-f1**, **C2-f2a**-**C2-f2c**, **C2-f2d** (all four leaves)
and **C3** -- section 4 carries each one's commit, and what reached `main` is a MEASUREMENT
(`git log --oneline origin/main..dev`).

**Both engines, every forward PROJECTION, the WHOLE balance seam, the GRID, `/investment`,
`/savings`, `/accounts/<id>` and the PAYCHECK ENGINE read the DERIVED calendar.**
**C2-f DECOMPOSED into three leaves 2026-08-14** and **`C2-f2` into five the same day**; `C2-f2e`
(the budget dashboard) and `C2-f3` (everything else, which deletes the last two readers) carry what
is left of the 60 `app/` call sites C2-f opened with, and each entry names its own set.

**A pass is not a clock, and one pass is not one calendar**: three bare `date.today()` reads survive
on `/retirement` (**P55**), modules still open their own pass (**P56**), and `/` opens TWO, so it
derives the calendar twice a render (**P61**). Where a stored column disagrees with the derivation
every consumer believes the derivation -- those shapes are in `recurrence/_occurrence.py` and
section 3, all owned by **C4**, which changes ONE writer (`pay_period_write`) plus its readers.
**A cold session starts at section 4**; the shared registries are `ledger.md`, `steps.md`,
`conventions.md` and `verification.md`.

## Rulings

| fork | ruling |
|---|---|
| **The pay-period model** | **NORMALIZE. `budget.pay_periods` stores the PAYDAY; `end_date` and `period_index` are derived by one producer and dropped from the table. Ruled 2026-08-08 (developer), option "store paydays only"** |
| **A gapped or overlapping batch** | **Neither is refused, because neither is expressible once the derived columns are gone. The "refuse it" and "bridge it with a filler period" options were both weighed and rejected -- see `historical/pay_calendar_evidence_2026-08-11.md`** |
| **A payday inserted BETWEEN two existing ones** | **REFUSE when the period it splits is locked by the existing `pay_period_admin.classify_period_lock` (historical, settled, posted, or a recurrence anchor); otherwise insert and re-derive. Ruled 2026-08-08 (developer). No row may ever be left dated outside its own paycheck** |
| **The last payday's period end** | Projected as `start_date + cadence_days - 1` from `budget.pay_schedule`. It is the ONLY derived end that is not `lead(paid_on) - 1`, and it is a projection stated as one -- `DerivedPeriod.end_is_projected`, ruled 2026-08-08 (developer), because a consumer holding one period out of its calendar cannot recompute it and a window VIEW must keep it |
| **What a derived period carries** | **`period_id`, `int \| None`. Ruled 2026-08-08 (developer)**: `None` IS the marker for a period no foreign key can point at, which is the distinction `C2` must draw between "which paycheck does this row live in" and "which span does this day fall in". One value type for the arc, so C2 MOVES `SchedulePeriod` rather than merging two |
| **Table and column names** | `budget.pay_periods.start_date` KEEPS both names. A period is identified one-to-one by the payday that opens it, `transactions.pay_period_id` reads correctly against it, and a rename is a four-FK migration that buys nothing. See `historical/pay_calendar_evidence_2026-08-11.md` |
| **An entry dated outside the schedule** | **A legitimate SECOND QUESTION, named on the one value -- not a compensator to delete. Ruled 2026-08-10 (developer).** The calendar gains `period_starting_on_or_before`, the missing mirror of the `period_starting_on_or_after` it already carries, and the FILING rule is DERIVED from it rather than scanning again. The alternative (drop the ledger's stored paycheck and derive it from `entry_date`) was measured against `shekel-prod-db` and REFUTED: 14 days carry TWO paychecks for one date, so `entry_date` does not determine it; 35 of 327 entries are dated outside their own paycheck by design; and 4 loan-opening entries predate the first payday by up to seven years, so the clamp is live rather than hypothetical. See `historical/pay_calendar_evidence_2026-08-11.md` |
| **Past the last stored payday** | **The calendar ANSWERS, projecting forward at the OWNER's cadence with `period_id = None`. Ruled 2026-08-10 (developer)**, which is what makes it the TOTAL function `balance:X-l` asks for. `growth_engine.generate_projection_periods` and `SyntheticPeriod` retire into it (rows **P17**, **P20**). Containment over SAVED periods stays its own named method, because the recurrence engine needs to tell a schedule HOLE from "the schedule has not reached there yet" |
| **The forward-only rule (R-PC1)** | **REPLACE `_reject_overlapping_batch`, do not delete it. Ruled 2026-08-10 (developer), SPLIT IN TWO, corrected twice by C3-b's neutral reviews, then HALVED 2026-08-11 (developer).** The rule the plan stated -- "the last paycheck must hold no row dated on or after the new payday" -- was measured wrong in both directions. What survives is a forward-only FLOOR of one full cadence after the latest payday, whose only job is keeping C6 closed and which C6 deletes. **The COVERAGE half is DELETED**: it refused a write taking a day out of every paycheck while a SETTLED row's `settled_on` fell on it, on the claim that stranding such a day reproduces `balance:N-128` -- and the claim was false. `_cash_periods` values each column at its OWN `end_date`, so a day off the top of the window is absent from both sides of R-K and reports as `period_timing`. See C3-b |
| **When the gap machinery dies** | **With `C2-b2`, the leaf that makes its subject unreachable -- not with `C4`. Ruled 2026-08-11 (developer).** `C5a` was gated on C4 and the gate had no code behind it: `PlacementOutcome.SCHEDULE_GAP`, `GenerationPlan.gaps` and `report_schedule_gaps` read no stored column, so all three went dead when the recurrence engine took the derived calendar, and shipping a branch that cannot fire is what rule 1 forbids. The visibility it took is replaced by `integrity_check` **BA-07**, which asks the stored question as a query and dies with the column at C4. Rejected: leaving it dead with a docstring note (ledger row **P25**'s original disposition), which ships code that lies about a state the app cannot hold |
| **How the seam learns WHICH periods to report** | **It READS them; the argument is DELETED. Ruled 2026-08-13 (developer).** All eight callers filled it with one value -- the owner's whole saved set -- so the only thing it could express was a mistake, which is the shape ruling R-Q already removed the override map for. `BalanceContext` carries the calendar, taking the first of `balance:X-i1`'s five inputs early. Rejected: callers slicing the calendar themselves (eight extra loads, and it pushes calendar-loading into routes ahead of `C2-f`), and threading a `PayCalendar` beside the context (a second per-request bundle, which is what the context exists to end) |
| **A window's two invariants** | **ORDER is DERIVED and CONTIGUITY is CHECKED. Ruled 2026-08-13 (developer).** A window's identity is its period SET, so `__post_init__` sorts and a caller cannot state an order wrongly; contiguity is a property of the input no constructor can compute its way out of, so it is refused. That refusal is P32's first disposition, taken because the second ("`containing` stops answering `None` inside its own span") is what a tiling gives for free and so decides nothing |
| **`PayCalendarError` reaching a balance page** | **RECORD it, do not build the handler. Ruled 2026-08-13 (developer).** `C2-c` widens the raise from the recurrence and savings surfaces to `/grid` and `/accounts/<id>`, where `app/error_handlers.py` leaves it on the bare 500. The raise is right (a defaulted cadence misreports every horizon); what is missing is a recovery page, which is a ruling of `BaselineMissingError`'s size. Row **P35** carries it and `C4` deletes the fallback that causes it |
| **The axis below the first payday** | **REFUSE, and clamp in ONE named companion. Ruled 2026-08-14 (developer)**, ledger row **P23**. `axis()` answered a range opening below `opening_bound` with the part above it -- silently, and a short axis is indistinguishable from a complete one, which is the argument `overlapping()` already makes for refusing a CROSSED range. Nothing is projected backwards (the 2026-08-10 ruling), so covering such a range was never an option and refusing is the only answer that is not a half-truth. The owner whose first payday has not happened yet is an ordinary state -- the Generate form asks for "your next (or first) payday" -- so `projection_axis()` sits beside `axis()` as the TOTAL companion every projecting surface calls, exactly as `filing_period` sits beside `period_starting_on_or_before`. Rejected: stating the truncation in the `Returns` block (a value that answers a different question than it was asked, however honestly documented), and projecting backwards (it would attribute money to paychecks that never happened) |
| **What the projection axis HEAD is** | **Each surface keeps its own, and the SEED follows it. Ruled 2026-08-14 (developer)**, ledger row **P22**. The axis covers the range it is given, so a caller passing the read pass's clock gets the period CONTAINING it -- opening up to a cadence in the past. That is correct wherever the seed is read at `axis[0].start_date - 1`, which `retirement_projection._resolve_seed_balances` already did and which `investment_dashboard_service` was corrected to do. The one surface whose seed CANNOT follow -- the Horizon's asset band, seeded from the figure the net-worth hero shows -- left the axis instead: it carries no contributions, so an axis was only chopping the horizon into pieces the compound formula is indifferent to. Rejected: clipping the axis head to the requested day (a partial period is not a period, and the window type exists to say so), and re-seeding the asset band at the axis head (one balance-seam read per account, and index 0 would stop equalling the hero) |
| **C2's shape** | **DECOMPOSED into `C2-a`..`C2-f`, the value first with nothing calling it. Ruled 2026-08-10 (developer).** A single commit over 60 call sites cannot be proven against production BEFORE its consumers depend on it, which is exactly the technique that made C1 safe, cannot be reviewed in focus, and cannot be reverted precisely -- and two of the cutovers move money |
| **`C2-f`'s shape** | **DECOMPOSED again into three leaves, split by READER. Ruled 2026-08-14 (developer)**, on a measurement the first decomposition did not have: 13 functions read two or more of the six readers over 27 of their 60 sites, and 11 pair `get_current_period` with `get_all_periods`. Splitting THAT pair would leave a dozen context objects holding an ORM row in one field and a `DerivedPeriod` in another, so the two travel together in `C2-f3`. Rejected: by SURFACE (no leaf can then delete a reader, so no leaf has a checkable end state) and one commit (the shape this ruling's parent already refused) |
| **`C2-f2`'s shape** | **DECOMPOSED into FIVE leaves, split by PACKAGE. Ruled 2026-08-14 (developer)**, on a measurement the parent decomposition did not have: the **20** call sites at a surface holding a `BalanceContext` (AST census over the seven packages) feed **23** `app/` modules -- those calling one of the three readers or carrying its rows onward -- and **12** templates, being those reading a period's `id`, `start_date`, `end_date`, `period_index` or `label`; every one of the 12 is the grid's. One commit over that is the shape the `C2's shape` ruling already refused. **The by-READER axis its parent used is UNAVAILABLE here, which is why the split is by package** (adversarial design review, 2026-08-14): six of the seven surfaces read `get_current_period` and `get_all_periods` inside ONE function, so a reader split yields exactly two leaves -- `get_periods_in_range` (3 sites, all grid) and everything else (17 sites, 7 packages) -- and the second is the leaf this ruling exists to refuse. Each leaf's end state is a GRADED predicate rather than a grep: `a` closes **P37**, `b` DELETES `get_periods_in_range` whole, `c` closes **P48**, `d` closes **P43**, `e` closes **P36**. The SEAM goes first: it is the only leaf touching money-shaped logic (the annual-limit accumulation order) and the smallest diff, so it gets a review of its own. Rejected: three leaves (the middle one is 17 modules, the same objection one size down) and one commit |
| **How the CONTRIBUTION tier learns its periods** | **A REQUIRED `PayCalendar` parameter on `_asset_fold.resolve`, which derives the window itself. Ruled 2026-08-14 (developer)**, ledger row **P37**; the type was `PeriodWindow` until an adversarial design review of `C2-f2a` refuted it the same day. That function deliberately takes no `BalanceContext` -- a fold assembled at one scenario beside a context carrying another loads the modelled tier against rows the cash tiers never saw -- and a calendar carries neither a scenario nor a clock, so passing one reintroduces nothing. **The CALENDAR and not the window, on three counts the window loses.** The tier's precondition is the owner's WHOLE schedule (the annual limit is a calendar-year accumulation, so a slice restarts the year-to-date total mid-year and uncaps it) and `PeriodWindow` cannot say whether `saved()`, `window()` or `axis()` produced it, so a window put that precondition in a docstring one call from the grid's six-period slice; the calendar keeps the OWNER on the value where a period list drops it; and **C9**, which projects this tier past the horizon on `projection_axis`, becomes a one-line change inside `resolve` rather than a sixth parameter, which `max-args` and `max-positional-arguments` (both 5, and this signature is at 5) would refuse. *The superseded window ruling claimed its two callers held two DIFFERENT windows; they did not -- both were `reported_periods()`, which is the argument ruling "How the seam learns WHICH periods to report" deleted, at a smaller scale.* Rejected: a field on `ContributionInputs` (its `absent()` token would carry an empty window, the silent failure made reachable) and a field on `AssembledCashFold` (an OWNER value copied onto a per-ACCOUNT record, and an argument every cash reader pays for that only the modelled tier reads -- two of its four callers discard everything but `.walk`) |
| **The modelled fold past the horizon** | **PROJECT the contributions; ruling `balance:R-AG` is SUPERSEDED. Ruled 2026-08-14 (developer)**, and the evidence is what changed rather than the argument: R-AG (2026-07-27) let the fold run a half model because no total calendar existed, `C2-e` built one, and three surfaces already project on it -- so the seam is now the only one that does not and it disagrees with the pages built on it. `C9` is the remedy and it MOVES MONEY (row **P7**, `+$5,427.07` at six months out) |
| **How a producer gets its READ PASS** | **The ROUTE builds one per request; every producer below takes `balance_ctx` as a REQUIRED parameter and DROPS its `user_id`. Ruled 2026-08-16 (developer)**, row **P43**. A producer holding an id can manufacture a `BalanceContext`, and three did, so `/retirement` held two passes and `/savings` two plus a third calendar derivation -- two clock reads behind two cards on one screen, `$4.18` of after-tax projected savings and one paycheck of countdown apart across a midnight-into-payday render. Dropping the id makes a mismatched (owner, pass) pair unrepresentable rather than discouraged, and a required parameter fails at the call instead of silently opening a pass. It follows the seam's own convention (`balance_at(acct, ctx, day)`) and the required-`PayCalendar` ruling above. Rejected: `user_id` BESIDE the pass (two spellings, nothing reconciling them); an optional `balance_ctx=None` that builds one when absent, which is the pattern that CAUSED this; and fixing only the one leaf P43 named, which leaves the measured contradiction on screen. **It does not make a second pass unconstructible** -- eight service modules still open their own, and the layer predicate that would (row **P56**) waits on the leaves closing those doors |
| **How a TEST builds a pass over hand-written paydays** | **ONE shared helper, `tests/_test_helpers.read_pass_over_paydays`. Ruled 2026-08-16 (developer)**, row **P54**. The pass derives its calendar into a field its own module declares private; a unit case with no database must seed it, and three sites wanted to. Rejected: a `BalanceContext.for_test(calendar=...)` constructor (a production entry point whose only caller is the suite, `CLAUDE.md` rule 13) and an optional `calendar=` on the real `build` (it hands every production caller a way to supply a calendar the module did not derive, unchecked against the owner). Also rejected, on measurement: making the calendar EAGER like the scenario -- deriving one can RAISE for an owner with no pay schedule, so every render would begin failing over a fact most never read |
| **How the PAYCHECK ENGINE takes a period** | **A `DerivedPeriod`, and every caller supplies one. Ruled 2026-08-16 (developer)**, on a fork `C2-f2d-3` could not cross without it: the engine reads a period's `start_date` and its ID, `/savings` needed to hand it derived periods, and no adapter is honest. Measured first: an AST census found those two attributes and nothing else, so the move is a retyping over 12 call sites in 10 modules rather than a rewrite, and all 13 `app/` sites trace to a MATERIALISED producer -- which is what makes `PeriodInfo.period_id`'s non-nullability structural where three consumers KEY on it. Rejected: one shared `current_breakdown(balance_ctx)` producer for the three surfaces that each build one (it merges what two OTHER pages publish, so it wants its own ruling -- row **P62**); a narrow ORM load kept inside `savings_dashboard_service` (a second pay-period read on a render whose whole point is one); and leaving the engine alone (`/savings` then keeps an ORM reader the arc exists to delete) |
| **`retirement_projection`'s two period parameters** | **DELETED; it reads both off the pass it already takes. Ruled 2026-08-16 (developer).** Both callers filled them from that same pass, so the pair could express only a mismatch -- the shape the "How the seam learns WHICH periods to report" ruling removed at `C2-c`. 6 arguments to 4, which retires the `too-many-arguments` / `too-many-positional-arguments` suppression at its root rather than by raising a threshold, and left `_CurrentPay`'s two period fields with ZERO readers, so they went too. Rejected: converting at the `/savings` boundary (a second pay-period read), and keeping the parameters with a new type (the pass and the arguments can still disagree, and the suppression stays) |
| **`project_balance_horizons`'s two callers** | **Take `accounts/detail.py` onto the pass EARLY, out of `C2-f2e`. Ruled 2026-08-16 (developer).** That helper reads a period's ordinal and its id, and a `PayPeriod` spells the id `.id` where a `DerivedPeriod` spells it `.period_id` -- so its two callers could not hold two types between them for even one commit. Rejected: one module briefly holding both spellings (`accounts/detail.py` would carry a derived `current_period` for this call and an ORM one for its other fifteen reads), and splitting the rule from the lookup so each caller resolves index -> period -> balance itself (the loop written twice) |

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

**C3 and the recurrence arc's R7c shared a derivation, not a file, and R7c-c is what discharged
it.** `recurrence_rules.offset_periods` was a phase modulo `interval_n` computed from the start
period's `period_index`, so once the index was derived, inserting a payday BEFORE an existing one
re-phased the `Every N Periods` rules row **P26** names.
**Ledger row P11 carried that measurement (zero live rules) and CLOSED at `recurrence:R7c-c`**
(migration `d9f5c1a48b73`), which drops the column: the phase is derived from the rule's first
occurrence on every read, so there is no stored ordinal left for an inserted payday to re-phase. C3
inherits nothing here.

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
      **The CENSUS row P6 counts, and it is not final.** Seven implementations of "which pay period
      contains this date" were found and THREE have gone: `recurrence/_calendar.py:287` at `C2-b2`,
      `balance_at/_cash_periods.py:320` (a bisect over the STORED spans, 3 in-module sites) at
      `C2-c`, and `loan_ledger/_visible.py:150` at `C2-d` (`3e6cd4ec`, with
      `resolve_anchor_pay_period` and `owner_pay_periods`). What survives:
      `pay_period_service.get_current_period` (SQL, `.first()` with NO `ORDER BY` -- row **P19**,
      owned by `C2-f3`), and `investment_dashboard_service/_chart._build_chart_markers` (a linear
      containment scan over a `PeriodWindow`, which that type's own `containing` answers -- owned by
      `C2-f2`, row **P48**). `savings_dashboard_service`'s `_period_id_at` went at `C2-e` and
      `get_overlapping_periods` at `C2-f1`; the `_chart` entry MOVED rather than surviving in place.
      `entry_service.py:816` is EXCLUDED: it asks MEMBERSHIP, the primitive the searches are built
      on, not a search. **The lesson, and why the count is still low:** an AST census keyed on the
      containment PREDICATE could not see `_period_id_at` -- it was found by reading the consumers
      of the producer C2-e retires -- and one keyed on containment cannot see an ORDINAL search at
      all. `C2-f1` found three more that way (rows **P45**, **P47**, **P49**) and its reviews a
      fourth, so this list is a floor rather than a census.

- [x] **C2-a -- the one calendar VALUE, and nothing calls it.** `3cb3082f`. Opened **P21**-**P25**.
      Proof: `_calendar.py`'s docstring.

- [x] **C2-b -- the recurrence cutover.** `fe365de1`. The DECOMPOSED parent, ticked with C2-b2, its
      last leaf.

- [x] **C2-b1 -- the last two questions, the cadence rule, and one door.** `90f2fbb7`. Opened
      **P28**. Proof: `_loader.py`'s docstring.

- [x] **C2-b2 -- the cutover.** `fe365de1`. Closed **P2** (= recurrence **F-10**) and **P25**;
      opened **P34**, **P35**. Proof: `recurrence/_occurrence.py`'s docstring, which states the
      THREE shapes where the derivation and the stored columns disagree, and the byte-identical
      430-shape baseline. **P26**, **P27** and **P28** re-pointed to **C4**: each owed a STATEMENT
      here and now owes only the column.

- [x] **C2-c -- the cash-view cutover.** `b8a72f6c`. `_PeriodSpans` is DELETED and the balance
      seam's THIRTEEN per-period entries stopped taking a period list: the domain is
      `BalanceContext.reported_periods()`. Closed **P14**, **P24**, **P32** and `balance:N-128`;
      opened **P36**-**P39**. Proof: `_window.py`'s docstring, and the corrupted-column pin in
      `test_cash_period_view.py` with its firing control.

- [x] **C2-d -- the filing cutover.** `3e6cd4ec`. Closed **N-169**. Proof: `filing_period`'s
      docstring and `tests/manual/verify_filing_cutover.py` (1,654 days, 0 disagreements).

- [x] **C2-e -- the projection axis.** `8143c6fe`. All six call sites run on
      `PayCalendar.projection_axis`; `generate_projection_periods` and `SyntheticPeriod` are
      DELETED. Closed **P17**, **P20**, **P21**, **P22**, **P23**; opened **P40**-**P44**.
      **P7 is RE-POINTED to C2-f, not ticked** (developer 2026-08-14): its projection half shipped
      here, the tier its `+$5,427.07` was measured on did not. Proof: `projection_axis`'s docstring
      and `tests/manual/verify_projection_axis.py` against a production clone.

- [ ] **C2-f -- the readers answer from the calendar** -- the DECOMPOSED parent, split into three
      leaves 2026-08-14 (developer). Its six `get_*` readers have **60** `app/` call sites (AST,
      2026-08-14). **The split is by READER and its ORDER is measured**: 13 functions read two or
      more of the six across 27 of those sites, and 11 of them pair `get_current_period` with
      `get_all_periods`, so those two may never be separated -- splitting them leaves a dozen
      context objects holding an ORM row in one field and a `DerivedPeriod` in another.
      `earliest_recordable_day` is NOT one of the six and stays: it asks no "which period" question,
      only `min(first payday, today)`, and `start_date` is the column C4 keeps.

- [x] **C2-f1 -- the three the calendar already answered.** `792e3b21`. Opened **P45**-**P50**.
      **TWO shapes a later step must not undo.** `period_starting_after` / `_before` filter to
      MATERIALISED periods, which is what makes the credit-payback FK write safe with no guard --
      `filing_period`'s correction, taken again. And a surface that SELECTS its periods by the
      derived span must PLACE its rows by that same one: splitting those cost `$1,234.56` on a
      planted disagreement. Proof: `tests/manual/verify_period_window_cutover.py`'s docstring.

- [ ] **C2-f2 -- the readers at a surface that already holds a read pass** -- the DECOMPOSED parent,
      split into five leaves by PACKAGE 2026-08-14 (developer). Every remaining reader in a package
      that builds or receives a `BalanceContext` takes the calendar off `ctx.calendar()` rather than
      querying, and **`get_current_period` becomes `calendar().period_containing(ctx.as_of)` at
      every one of them** -- which retires the process clock those sites read (ledger row **P49**'s
      half) and puts the whole pass on one clock. Closes **P36** when the last leaf lands.
      **The measurement the split rests on**: 20 call sites, 23 `app/` modules and 12 templates, in
      seven packages -- and all 12 templates are the grid's, so only `C2-f2b` carries any. The `.id`
      -> `.period_id` rename goes into the templates rather than an alias on `DerivedPeriod`, which
      would be row **P21**'s nullable-id trap under the name a reader trusts least.

- [x] **C2-f2a -- the SEAM's own reader.** `dd5c48a5`. Closed **P37**. No module under
      `app/services/balance_at/` IMPORTS `pay_period_service`. Proof: `verify_balance_baseline`
      byte-identical over 9 accounts / 427 grid cells / 5,978 daily points on a production clone,
      with the harness SHOWN firing on a planted wrong axis: taking the calendar collapsed the two
      wiring sites into ONE derivation, so one plant moves the Empower's grid column, its
      `balance_map` and its 2029 scalar together (`-$182.29`, `-$182.29`, `-$190.39`).

- [x] **C2-f2b -- the GRID.** `f4d4abe6`. Carried **P36**'s grid half. All six sites answer from the
      pass's calendar and `get_periods_in_range` is DELETED, taking the six readers from 47 `app/`
      call sites to 39; the COMPANION moved with it (one shared partial), `routes/grid.py` became a
      package first (`29e4fab8`, a pure move off the 1000-line ceiling), and `period_containing` now
      ENFORCES the "SAVED" it claimed. Proof: `verify_grid_cutover`'s docstring -- byte-identical, 0
      mismatches on PRODUCTION, and SHOWN skipping `$5,827.75`.

- [x] **C2-f2c -- `/investment`.** `d4621147`. Closed **P48**; opened **P52**-**P54**. No module in
      that package imports `pay_period_service`; its three public entries take the calendar AND the
      clock off the pass's `BalanceContext`, and the marker scan retired into
      `PeriodWindow.containing_index` -- the OFFSET, because `containing` alone answers the period.
      A contribution carries its PAYDAY (`241b7b40`), which let the period list leave three SHARED
      signatures without moving `/retirement`. Proof: `verify_investment_cutover`'s docstring.

- [x] **C2-f2d -- `/savings` and `/retirement`.** `c95519dd`. The container and its four leaves,
      condensed into `historical/pay_calendar_c2f2d_as_built_2026-08-16.md` under `conventions.md`
      rule 5, with the THREE shapes a later step must not undo. Closed **P43**, **P57**, **P58**,
      **P59**; opened **P55**, **P56**, **P60**-**P63**. Its last leaf took three rulings WIDER than
      its spec (above), so it also carries the paycheck-engine cutover and `C2-f2e`'s
      `/accounts/<id>` half.

- [ ] **C2-f2e -- the budget dashboard.** `dashboard_service` and `dashboard_pulse_service`.
      **`routes/accounts/detail.py` went EARLY at `C2-f2d-3`** on the developer's 2026-08-17 ruling,
      taking `app/utils/period_projections.project_balance_horizons` with it: that helper's two
      callers could not hold two period types between them and the alternative was a second copy.
      What is left here is the PASS. `/` opens TWO -- one in `compute_pulse_section` through
      `dashboard_service._resolve_section_context`, one in `compute_tracks_section` -- so it derives
      the owner's calendar TWICE a render, measured at `calendar_for` 1 -> 2 across C2-f2d-3 while
      every other render stayed at 1. That is row **P61**, and this step closes it: the ROUTE opens
      one pass and hands it to both producers, `_resolve_section_context` takes it rather than
      building one, and `compute_balance_section`'s own route does the same. Ticks `C2-f2`, and with
      it **P36**.

- [ ] **C2-f3 -- the rest, and the module's last two readers.** Every remaining site loads
      `calendar_for` ONCE per producer and threads it; the three write-path reads in
      `pay_period_admin` that hand ORM ROWS to `pay_period_write` move into that writer, which
      already declares itself the one place in `app/` that constructs or deletes a pay period.
      Deletes `get_current_period` and `get_all_periods`, leaving `pay_period_service` holding
      `earliest_recordable_day` alone. Closes **P19**, **P45**, **P49**.

- [x] **C3 -- the writer writes paydays, forward-only.** `7e3fb33b`. Both leaves and the container,
      condensed into `historical/pay_calendar_as_built_2026-08-16.md` under `conventions.md` rule 5.
      `pay_period_write` is the ONE place in `app/` that constructs or deletes a pay period, and
      R-PC1's coverage half is DELETED -- neither may be undone by a later step. Closed **P2**'s
      writer half, **P12**, **P13**, **P29**, **N-127**; opened **P31**, **P32**; found **P33**.

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

- [x] **C5a -- delete what is now unconstructible.** `fe365de1`. Ticked at **C2-b2**, not after C4:
      nothing in the gap chain read a stored column, so all of it went dead the moment that leaf
      pointed the engine at the derived calendar. `PlacementOutcome` went WHOLE -- its last member
      said only what `period is None` says. Ticks recurrence **R-F10**. Deletion-only; the 430-shape
      baseline stayed byte-identical.

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
