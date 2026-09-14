# The paycheck engine: what one paycheck is worth, stated once

**The arc that owns the paycheck engine** -- `paycheck_calculator`, `income_service`,
`calibration_service`, `payroll_basis`, the `routes/salary/` package and the tax services -- minted
2026-09-03 by the developer (**R-SAL1**) because four arcs edited the engine and none named it. Its
rules are `conventions.md`, its findings are `ledger.md` rows whose `arc` reads `salary`, "done" is
`verification.md`, and the ORDER is `steps.md`.

## Where this stands

**`S3-f-4` shipped 2026-09-13 (`329b663d`): every readiness what-if refusal is the rail at 422 and
retires on edit** (**R-SAL33**, **R-SAL34**), and `S3` -- the engine prices the WHOLE horizon --
ships with it; the whole `S3` span is archived (`historical/salary_s3_as_built_2026-09-13.md`, the
`S3-f` leaves in `historical/salary_s3f_as_built_2026-09-13.md`). The same day `R15-a` (`bdd77055`)
gave a recurrence rule's cadence a per-month CEILING (**R-SAL29**), the vocabulary `R15-b` migrates
every 24 / 12 deduction onto. Also archived: the `R14` span
(`historical/salary_r14_as_built_2026-09-11.md`), `S3-e-2`'s record
(`historical/salary_s3e2_as_built_2026-09-11.md`) and `S2`'s
(`historical/salary_s2_as_built_2026-09-04.md`). Nine steps were re-filed or minted into this arc
when it was created (**R-SAL1**), with their ledger rows and the four `balance:X-au-d` findings that
had no arc to go to.

**What to do next is `steps.md`'s order table; do not re-derive it here.** Section 0 states this
arc's own reasons, which that table resolves against. Which steps are in production is a MEASUREMENT
(`git branch -r --contains <hash>` against `origin/main`), never a stored value.

## The rulings

**This arc's rulings are in `rulings.md`, rows whose `arc` is `salary`**, ids `R-SAL1` onward: that
the engine is an arc, what a deduction is priced from (**R-SAL2**), what a line's cadence IS
(**R-SAL3**), and that a calibration applies forward from its stub's date (**R-SAL4**).

## 0. Why this arc exists, and its own sequencing

**Four arcs edited one engine and none owned it.** `recurrence` carried R14, R15 and R18 because
their findings were found while tracing payroll deposits; `pay_calendar` carried C12 because the
engine's three readers each derived a calendar; `balance` carried X-at and X-av because the amount
model's INPUT side is the salary; and `balance:X-au-d` then measured three defects in the engine
itself and could name no step for any of them (**N-441**, **N-442**, **N-443**), which is
conventions rule 1's retired value spelled as a whole subsystem. The developer ruled on 2026-09-03
that the engine is a sixth arc with its own document, and on 2026-09-03 named it `salary` and moved
C12 in with the others; N-443, the three spellings of the paycheck projection, closes at R14's first
leaf, which memoizes the breakdown map, and C12 collapses the dashboards' readers onto it.

**Why each step sits where `steps.md` puts it, which is that table's to say and not this
document's.** `S2` is the arc's cheapest first act, because a derivation that moves `-$19.28` for no
recorded reason is a baseline nobody can measure `S1` against until the input is named. The
earnings-lines chain follows the bank_import production scope by `bank_import:R-JJ`, because `R18`
is what makes one payroll deposit one app row; `S2` sits ahead of it as the arc's first act. `C12-a`
decided the shape `balance:X-i1` waited on (**R-SAL27**): the basis takes the pass's PRICER.

## 1. Root cause

**The engine's inputs are stated without the dimension that makes them a fact, and its answer is
spelled three times.** Both halves of `CLAUDE.md` rule 14, on one subsystem.

**The inputs.** An input that describes a MOMENT must carry it, or every derivation over it is
retroactive. The stored salary is an annual scalar for all time, so a raise and a typo correction
are the same edit (**N-237**), and it is stored in a UNIT the owner's stub does not state -- the
stub says `$3,526.00` a paycheck and `$91,675.00` a year, and `26 x $3,526.00` is `$91,676.00`, a
disagreement the app resolves silently (**N-391**). A calibration is one real stub on one real date
stored with no date at all (**N-441**). A deduction's inflation escalation is anchored on the row's
`created_at` (**N-240**). A deduction's cadence is a biweekly COUNT used as a three-valued MODE
(**F-21**), one reader ignores it (**N-395**), and the earnings side has no lines at all, so an
employer allowance with a cadence becomes a separate income template that misfiles (**D59**). Two
active profiles on one template are priced by whichever `ORDER BY id` returns (**N-294**). A
substituted tax year is never shown and a new year's brackets have no door (**N-235**, **N-236**).

**The answer.** `project_salary` is run by three readers over the same calendar --
`income_service.SalaryPricing._net_by_period`, `routes/salary/views.py` and
`routes/salary/cockpit.py` -- two of them keeping the whole breakdown and one keeping only
`net_pay`, so they cannot call each other; the shared leaf would be the BREAKDOWN map and it is out
of reach (**N-443**, **P62**). A fourth reader, the contribution tier, does not run the engine at
all and divides the annual figure by the paycheck count (**D45**). The engine derives its own
calendar where every other read-path consumer takes one (**P63**), and the module sat at pylint's
ceiling until R-F16 took it to 873; it is back to 994 (**P64**), so the room that argument rests on
is effectively gone.

## 2. Evidence

Every figure is dated and rests on the developer's own data; re-measure before citing.

| what was measured | result | when |
|---|---|---|
| the whole raise-blind feed against the engine, over all 63 saved paydays | `$1,646.84` understated (`$898.18` employee + `$748.66` employer); `$562.12` a full year. *Superseded `$137.51`, which was the employer half at ONE raise and did not compound* | 2026-09-03 clone, **D45** |
| one owner-level gross on a two-job owner, R-F16's reverted fix | a 39% swing that flips between renders with no data change | 2026-08-19, **D45** |
| the 2026-08-28 calibration applied to the seven RECEIVED March-June paychecks | `$2,454.10 -> $2,483.19`, `+$29.09` each, `+$203.63` over seven | 2026-09-02 clone, **N-441** |
| the same seven re-derived with the calibration removed, against their generated figure | `$2,473.38 -> $2,454.10`, `-$19.28`; the input is a calibration DELETED 2026-08-28 | 2026-09-04, **N-442** closed |
| all 12 settled paychecks priced under every calibration the data has held | 11 reproduce under the deleted row, the 12th only under the live one; none reproduces both | 2026-09-04, **N-535** |
| payroll deposits against the app's rows, seven of seven | `$0.04`-`$0.06` above the app; pricing at the stub's gross collapses it to `$0.00`-`$0.02` | 2026-08-30, **N-391** |
| live deductions carrying the `24` mode | 11 of 12 | 2026-08-19, **F-21** |
| the Health Insurance Allowance modelled as a 26-of-26 template | a `$100.00` income row on 2026-07-30 the employer does not pay, stopped only by an `end_date` | 2026-09-02, **D59** |

## 3. Target model

**One producer, every input dated, the period as the clock.**

1. **The breakdown map is the shared leaf.** `project_salary(basis, periods)` returns, per period,
   the gross, the taxable income, every deduction line, every earnings line and the net; the amount
   model reads a row's amount from it, the salary page and the cockpit render it, the contribution
   tier prices a deduction from ITS period's gross (**R-SAL2**), and the dashboards' "current
   paycheck" is one entry per PROFILE, summed (C12-b). A second walk is a cache with no column
   (**balance:R-IZ**).
2. **Every time-varying input is effective-dated, and the base is per paycheck.** The stored fact is
   what ONE paycheck pays, dated, with the annual figure derived beside it (**balance:R-HW(b)**,
   X-av); raises already are; a calibration applies forward from its stub's date (**R-SAL4**, S1)
   and calibrations ACCUMULATE, none destroyed (**R-SAL9**); inflation escalation anchors on the
   line's own date, not a row timestamp (X-av).
3. **A line's cadence is a recurrence RULE against the pay calendar** (**R-SAL3**): a deduction or
   an earnings line names a rule, `NULL` meaning every paycheck, and the engine that already places
   recurring rows decides which paychecks a line lands on. `_deduction_applies_at` and the 26 / 24 /
   12 mode are deleted; an employer allowance is an earnings line with a cadence, not an income
   template (R15, R18).
4. **The period is the clock.** A projected paycheck is priced against its own period's inputs;
   nothing in the engine reads the process clock (`pay_calendar:C10` moves the five salary-route
   reads; the engine itself has read the owner's calendar since `balance:X-bh-1`).
5. **A substituted tax year says so, and a year can be completed** (X-at).
6. **The engine is a package**, one private leaf per verb, the answer **P64** recorded at C12-a.

**What becomes impossible rather than checked**: a calibration restating a paycheck received before
its stub; a deduction priced off a profile not its own; a 24-of-26 benefit modelled as 26-of-26; two
readers of one paycheck disagreeing. Each is a state the model cannot express.

## 4. Step sequence

- [x] **S2** `08638f61` -- the `-$19.28` was a DELETED calibration, not the engine; no single
      calibration reproduces the record. Closed **N-442**, opened **N-535**, ruled **R-SAL9**.
      As-built: `historical/salary_s2_as_built_2026-09-04.md`.
- [ ] **S1 -- a calibration is a DATED OBSERVATION and is never destroyed** (**R-SAL9**, amending
      **R-SAL4**; findings **N-441**, **N-535**, and **N-530**'s calibration kind).
      `salary.calibration_overrides` carries effective rates derived from ONE stub on ONE date and
      stores no date, so entering a stub restates every paycheck the owner ever had: `+$29.09` on
      each of seven RECEIVED paychecks, visible since `balance:X-au-d` made a settled row's plan a
      derivation. **And the write door REPLACES**, so the stub that priced the eleven earlier
      paychecks is already gone (**N-535**, measured at S2). Three parts, ruled 2026-09-04: an
      effective date on the row, backfilled at its stub's date; the calibration deleted 2026-08-28
      RESTORED from `system.audit_log` id 4212's `old_data` as a second dated row; and the door made
      to ADD rather than replace. The engine then resolves the calibration in force for each period
      as it resolves a raise, and all 12 settled paychecks re-derive to their generated figure.
      **Dating the survivor ALONE was rejected**: it resolves no calibration before 2026-08-27 and
      costs `-$334.32` on the 2026 net total the projection page and the cockpit show.
      **It can never move a balance** (a settled row is worth what it recorded); what moves is the
      EXPECTED figure and the variance beside it. The row's "derived effective rates" that nothing
      recomputes (**N-530**) are decided here: the rates derive from the stub's stated figures at
      read, or the stub's figures are the stored fact and the rates go (**balance:R-IY**). A
      migration; own review pass.
- [x] **R14** `e0f0c05f` -- the DECOMPOSED parent of a deduction's gross (**R-SAL6**); closed
      **D45**. Archived: `historical/salary_r14_as_built_2026-09-11.md`.
  - [x] **R14-a** `9e81d9e7` -- an employer contribution NAMES its funding profile (**R-SAL5**);
        closed **N-443**, **N-533**, **N-534**. Archived with `R14`.
  - [x] **R14-b** `e0f0c05f` -- the contribution tier CONSUMES the engine's breakdown (**R-SAL2**),
        `+$452.42`; closed **D45**, **N-532**. Its interim hold went at `S3-e-2`. Archived with
        `R14`.
- [x] **S3** `329b663d` -- the engine prices the WHOLE horizon (**R-SAL10**, **R-SAL11**,
      **R-SAL14**, **R-SAL15**; closed **N-541**); ticked with `S3-f-4`, the last leaf of its last
      leaf. The span as it stood: `historical/salary_s3_as_built_2026-09-13.md`; its argument and
      the `S3-a`..`S3-d` records: `historical/salary_s3_leaves_as_built_2026-09-11.md`.
- [x] **S3-a** `e4491ee6` -- the merit horizon is a per-raise TERMINATION. Archived with `S3`.
- [x] **S3-b** `8a8dd51e` -- `terminal_year` and three CHECKs (migration `c9a4e17b53d8`). Archived
      with `S3`.
- [x] **S3-c** `62567d87` -- THE CUTOVER (**R-SAL12**, **R-SAL13**); its downgrade is STATE-LOSSY.
      Archived with `S3`.
- [x] **S3-d** `62612c9a` -- the producer became a FUNCTION of the payday (**R-SAL14**). Archived
      with `S3`.
- [x] **S3-f** `329b663d` -- the PER-RAISE probe and its Save on the `/retirement` rail
      (**R-SAL20**-**R-SAL24**, **R-SAL33**, **R-SAL34**); ticked with `S3-f-4`. Every leaf's
      record: `historical/salary_s3f_as_built_2026-09-13.md`.
- [x] **S3-f-1** `c463dfbc` -- the engine seam (**R-SAL20**). Archived with `S3-f`.
- [x] **S3-f-2** `587c20d5` -- the plan point and the probe, split at the money line. Archived with
      `S3-f`.
- [x] **S3-f-2a** `f3032c87` -- the calibrated current paycheck (**R-SAL21**); MOVED `+$41,562.00`.
      Archived with `S3-f`.
- [x] **S3-f-2b** `587c20d5` -- the point believes each raise's end year; the rail probes it.
      Archived with `S3-f`.
- [x] **S3-f-3** `a5ef1bdf` -- the rail SAVES it (**R-SAL22**); the regeneration is a service
      (**R-SAL24**). Archived with `S3-f`.
- [x] **S3-f-4** `329b663d` -- the refused what-if RENDERED (**R-SAL33**, **R-SAL34**): the rail at
      422 via `HX-Retarget`, a row's refusal retiring on edit; 28 browser checks. Closed
      **SAL-548**; opened **SAL-550**, **SAL-551** (owner `S5`). Archived with `S3-f`.
- [x] **S3-e** `a6af5b3c` -- the hold is DELETED (**R-SAL15**, **R-SAL16**). Archived with `S3`.
- [x] **S3-e-1** `b8ee429a` -- the two window-only questions re-homed and deleted (**R-SAL17**,
      **R-SAL18**). Archived with `S3`.
- [x] **S3-e-2** `a6af5b3c` -- the feed prices a payday ON DEMAND (**R-SAL15**, **R-SAL19**); MOVED
      `+$194,321.85` on `/investment` and `-$4,909.81` on `/retirement`. As built:
      `historical/salary_s3e2_as_built_2026-09-11.md`.
- [ ] **R15 -- what a payroll deduction's own FREQUENCY means** (**R-SAL3**; findings **F-21**,
      **SAL-549**; **N-395** left at R15-a's tick, its fix having shipped at `R14-b` `e0f0c05f`).
      `deductions_per_year` stores 26 / 24 / 12 as a three-valued MODE the engine only compares
      against; 11 of the developer's 12 live deductions carry 24, a cadence the recurrence
      vocabulary could not say. The DECOMPOSED parent, split into THREE leaves 2026-09-13 once its
      four forks were ruled (**R-SAL29**-**R-SAL32**); ships with `R15-c`.
- [x] **R15-a** `bdd77055` -- the per-month CEILING as the cadence's third value (**R-SAL29** as
      amended: a paycheck cadence counts the month's paydays on the OWNER'S calendar; a week cadence
      its own first N): `budget.recurrence_rules.max_per_month` + CHECK (migration `ef32dfe4cd8e`),
      `_ceilinged` between the walk and the bound, `monthly_equivalent` off the aggregator, the
      template form end to end; three pure moves for the line ceiling. NO FIGURE MOVED; the browser
      drive was run by the developer.
- [ ] **R15-b** -- the third owning arm `paycheck_deduction_id` (an exactly-one-of-three CHECK),
      `PayrollBasis` resolving each line's rule ONCE and the engine asking it whether a payday is an
      admitted occurrence, and the migration writing one rule per 24 / 12 line (`starts_on` = the
      owner's opening payday, **R-SAL30**) then DROPPING `deductions_per_year` and
      `_deduction_applies_at`; graded byte-identical over the 63 saved paychecks. **How a NEW
      deduction states its cadence between this leaf and `R15-c` -- or whether the two ship in one
      PR -- is a question for the developer before it is built.** Closes **F-21**, **SAL-549**;
      carries **SAL-556** (ex N-399). A migration; own review.
- [ ] **R15-c** -- the deduction form takes `_recurrence_fields.html` (**R-SAL31**), the end-bound
      and due-day rows off by flag and the ceiling on, replacing the 26 / 24 / 12 select and
      `app.js`'s prefill.
- [ ] **S4 -- a payroll deduction's `annual_cap` is a DATED figure** (finding **N-540**, re-pointed
      here at `S3-f-3`'s tick, developer ruling 2026-09-12). The column is read raw and never
      escalated, so a statutory limit that rises every year is modelled as fixed and understates
      contributions over a long horizon; `tax_config_service` already prices an unconfigured year
      from the latest configured one and says so, which is the shape a dated cap should take.
      `$0.00` today (no live deduction's cap binds). Needs a RULING first.
- [ ] **S5 -- every readiness region renders its own refusals** (findings **SAL-550**, **SAL-551**,
      both from the `S3-f-4` session 2026-09-13). The readiness GET answers every refusal as the
      assumptions rail (**R-SAL33**), so the assumed-return, months and contribution inputs outside
      the rail, and a stale or foreign raise id, still leave the card silently at its previous
      picture; and the SWR refusal says "less than or equal to 1" beside a percent box.
      Display-only, `$0.00`.
- [ ] **S6 -- the rail's raise set is the projection's, and a stale Save is refused** (findings
      **SAL-552**, **SAL-553**, **SAL-554**, the `S3-f` span's openings filed 2026-09-13 when it
      shipped). The `/retirement` rail lists every ACTIVE profile's raises rather than the set the
      page projects; its raise row carries no `version_id`, so a Save from a stale rail is
      last-write-wins on one column; the regeneration is handed one read pass per profile. Needs a
      RULING on the race first. `$0.00`.
- [ ] **S7 -- `projection_inputs.py` splits by shape** (finding **SAL-555**: 994 of 1000 lines). A
      PURE move graded by AST (**R-PC74**'s shape), its own step because no live step edits the
      file. `$0.00`.
- [ ] **R18 -- a paycheck's EARNINGS side gets LINES, as its deductions side already has** (finding
      **D59**). `paycheck_calculator.Earnings` is four scalars and `net_pay` only ever SUBTRACTS, so
      there is no way to add a dollar to a paycheck that is not an annual-salary raise; a negative
      deduction is refused by `ck_paycheck_deductions_positive_amount`.
      **Measured on the developer's own data 2026-09-01**: his Health Insurance Allowance is paid on
      24 of 26 paychecks and his Phone Allowance on the first payday of each month -- exactly the
      two cadences the deduction side implements, on the wrong side of the paycheck -- so both were
      modelled as income templates, one of which would have generated a `$100.00` row the employer
      does not pay and the other of which misfiled 2 rows of 6. Earnings lines take the same cadence
      rule R15 gives deductions. **What it deletes**: one deposit becomes one app row, so the exact
      tier explains the developer's payroll deposits with no group and no residue -- the population
      `bank_import:X-gj-3a` was built for -- and it does NOT delete `DifferenceLanding`, which a
      genuine multi-row deposit still needs. **Its own ruling first**: whether an allowance is
      taxable, and what becomes of the two live income templates and their rows. **MOVES MONEY** (it
      changes `net_pay`); migration; own review.
- [x] **C12** `945651c2` -- one current-paycheck producer (the DECOMPOSED parent, split 2026-09-12
      at the money line, **R-SAL28**, once **R-SAL25**-**R-SAL27** ruled the design asked for from
      scratch; findings **P62**, **P63**, **P64**'s engine half). Both leaves shipped; the container
      ships with the last.
- [x] **C12-a** `26a7b816` -- the engine package (**R-SAL28**), the basis over the pass's pricer
      (**R-SAL27**; the twelve pass-less sites are **balance:BAL-491**), the four byte-identical
      direct-engine sites; NO FIGURE MOVED. Closed **P63**, **P64**.
- [x] **C12-b** `945651c2` -- `/savings` through the pass's pricer, calibrated (**R-SAL25**), summed
      over active profiles (**R-SAL26**). MOVED MONEY (the figures in R-SAL25; the budget
      dashboard's savings track shares the producer and moved with it). Closed **P62**.
- [ ] **X-av -- the pay rate is a dated per-paycheck gross** (**balance:R-HW(b)**; findings
      **N-237**, **N-240**, **N-294**, **N-391**). The stored fact becomes what ONE paycheck pays,
      effective-dated, with `annual_salary` derived as `gross x periods_per_year` and shown beside
      the entry, never a second stored figure -- so the app can tell a raise from a correction, and
      the owner can enter the `$3,526.00` the stub actually pays instead of an annual figure that
      divides to four cents less. **It does not presume the gross is the culprit**: a `$0.04` error
      in any of the twelve hand-entered deductions reproduces the same net, so the step opens with
      the operator re-reading one stub (N-391's operator half, committed 2026-09-03). It also
      anchors a deduction's inflation escalation on the line's own date rather than
      `profile.created_at` (N-240), rules which of two active profiles on one template prices it or
      refuses the second at the door (N-294), and reads the salary template's price from the amount
      series once `balance:X-bp` deletes `default_amount`, which two salary routes wrote as two
      different quantities (**N-446**). A value-splitting migration; own PR.
- [ ] **X-at -- a substituted tax year says so, and a new year can be entered** (findings **N-235**,
      **N-236**). `tax_config_service.resolve_tax_year` answers an unconfigured year with the latest
      configured year's rules -- the only available answer -- and every surface renders the result
      as a plain figure: `/analytics/taxes?year=YYYY` accepts any year in `[2000, 2100]` and a 2019
      request renders against another year's law with nothing on the page saying which. Carry the
      resolved year out of the resolver and render it. Its second half is the door that is missing
      entirely: nothing in `app/` creates a `TaxBracketSet` outside the signup seed, so the settings
      screen can write a year's state and FICA rows and never its brackets. Either a bracket-set
      write door, or a ruled statement that brackets are seed-only and the screen says so.

## 5. Findings ledger

**Rows in `ledger.md` whose `arc` reads `salary`.** A finding is not arc-local; the rows that moved
here on 2026-09-03 keep their bare ids (**D45**, **F-21**, **N-395**, **D59**, **P62**, **P63**,
**P64**, **N-235**, **N-236**, **N-237**, **N-240**, **N-294**, **N-391**, **N-441**, **N-442**,
**N-443**) because commit messages already cite them, one was minted here from `balance:N-243`'s
dissolved census (**N-530**), and `recurrence:D43` re-homed here as **SAL-549** (**R-SAL32**).

## 6. Alternatives considered and rejected

**Leaving the engine unowned**, each arc naming the step nearest its own finding: rejected
2026-09-03, because it is how three measured defects came to have no owner.
**Filing it under `pay_calendar`**: rejected, the calendar being one INPUT of the engine.
**A two-letter ruling prefix `R-SA`**: rejected, because the corpus-wide two-letter sequence would
eventually reach it, and a prefix that sequence cannot produce cannot collide with it.

## 7. Document rules (GATED)

**`conventions.md`, one copy for every arc**, graded by `tools/plan_gate/` through a pre-commit hook
scoped to this file, so EDITING IT runs the gate; its caps (260 lines, a 20-line signpost) were set
by the developer on 2026-09-03 and live in the gate's constants beside the other arcs'.
