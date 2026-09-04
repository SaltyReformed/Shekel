# The paycheck engine: what one paycheck is worth, stated once

**The arc that owns the paycheck engine** -- `paycheck_calculator`, `income_service`,
`calibration_service`, `payroll_basis`, the `routes/salary/` package and the tax services -- minted
2026-09-03 by the developer (**R-SAL1**) because four arcs edited the engine and none named it. Its
rules are `conventions.md`, its findings are `ledger.md` rows whose `arc` reads `salary`, "done" is
`verification.md`, and the ORDER is `steps.md`.

## Where this stands

**Minted, nothing shipped.** Eight steps: six re-filed with their ids unchanged (conventions rule
10) -- `recurrence:R14`, `R15`, `R18`, `pay_calendar:C12`, `balance:X-at`, `X-av` -- and `S1`, `S2`
minted here. Their ledger rows came with them, plus the four `balance:X-au-d` measured on 2026-09-02
that had no arc to go to (**N-391**, **N-441**, **N-442**, **N-443**).

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
is what makes one payroll deposit one app row; `S2` sits ahead of it as the arc's first act. `C12`
and `balance:X-i1` decide for each other -- the merged producer is what gives `income_service`'s
basis a threaded calendar -- and whichever lands first decides the shape for both.

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
ceiling until R-F16 took it to 873 (**P64**).

## 2. Evidence

Every figure is dated and rests on the developer's own data; re-measure before citing.

| what was measured | result | when |
|---|---|---|
| the whole raise-blind feed against the engine, over all 63 saved paydays | `$1,646.84` understated (`$898.18` employee + `$748.66` employer); `$562.12` a full year. *Superseded `$137.51`, which was the employer half at ONE raise and did not compound* | 2026-09-03 clone, **D45** |
| one owner-level gross on a two-job owner, R-F16's reverted fix | a 39% swing that flips between renders with no data change | 2026-08-19, **D45** |
| the 2026-08-28 calibration applied to the seven RECEIVED March-June paychecks | `$2,454.10 -> $2,483.19`, `+$29.09` each, `+$203.63` over seven | 2026-09-02 clone, **N-441** |
| the same seven re-derived with the calibration removed, against their generated figure | `$2,473.38 -> $2,454.10`, `-$19.28`, unaccounted for by any audit row | 2026-09-02, **N-442** |
| payroll deposits against the app's rows, seven of seven | `$0.04`-`$0.06` above the app; pricing at the stub's gross collapses it to `$0.00`-`$0.02` | 2026-08-30, **N-391** |
| live deductions carrying the `24` mode | 11 of 12 | 2026-08-19, **F-21** |
| the Health Insurance Allowance modelled as a 26-of-26 template | a `$100.00` income row on 2026-07-30 the employer does not pay, stopped only by an `end_date` | 2026-09-02, **D59** |

## 3. Target model

**One producer, every input dated, the period as the clock.**

1. **The breakdown map is the shared leaf.** `project_salary(basis, periods)` returns, per period,
   the gross, the taxable income, every deduction line, every earnings line and the net; the amount
   model reads a row's amount from it, the salary page and the cockpit render it, the contribution
   tier prices a deduction from ITS period's gross (**R-SAL2**), and the dashboards' "current
   paycheck" is one entry of it (C12). A second walk is a cache with no column (**balance:R-IZ**).
2. **Every time-varying input is effective-dated, and the base is per paycheck.** The stored fact is
   what ONE paycheck pays, dated, with the annual figure derived beside it (**balance:R-HW(b)**,
   X-av); raises already are; a calibration applies forward from its stub's date (**R-SAL4**, S1);
   inflation escalation anchors on the line's own date, not a row timestamp (X-av).
3. **A line's cadence is a recurrence RULE against the pay calendar** (**R-SAL3**): a deduction or
   an earnings line names a rule, `NULL` meaning every paycheck, and the engine that already places
   recurring rows decides which paychecks a line lands on. `_deduction_applies_at` and the 26 / 24 /
   12 mode are deleted; an employer allowance is an earnings line with a cadence, not an income
   template (R15, R18).
4. **The period is the clock.** A projected paycheck is priced against its own period's inputs;
   nothing in the engine reads the process clock (`pay_calendar:C10` moves the five salary-route
   reads; the engine itself has read the owner's calendar since `balance:X-bh-1`).
5. **A substituted tax year says so, and a year can be completed** (X-at).
6. **The engine is a package**, one private leaf per verb, the answer **P64** recorded (C12).

**What becomes impossible rather than checked**: a calibration restating a paycheck received before
its stub; a deduction priced off a profile not its own; a 24-of-26 benefit modelled as 26-of-26; two
readers of one paycheck disagreeing. Each is a state the model cannot express.

## 4. Step sequence

- [ ] **S2 -- name the input that moved a past paycheck `-$19.28`** (finding **N-442**).
      Production's seven March-June 2026 paychecks were generated and settled at `$2,473.38` and
      re-derive at `$2,454.10` with the calibration removed; the `salary` schema's whole audit trail
      since generation is one calibration row and one tax edit, and reverting the tax edit moves the
      answer `$0.00`. What is left is a paycheck-ENGINE change since 2026-03 that no audit trail
      records, and `balance:X-aw`'s deletion of the biweekly rounding residue (**N-239**) is the
      named, UNCONFIRMED candidate. **A trace step, not a build**: bisect `paycheck_calculator`
      against a fixed profile and period on a production clone, name the input, and record whether
      it is a correction the settled rows should keep as a variance or a regression. It ships as an
      as-built record and a ledger disposition; the reason it is first is that S1 changes what a
      past paycheck is EXPECTED to be, and that change is unmeasurable against an unexplained
      baseline.
- [ ] **S1 -- a calibration applies FORWARD from its stub's date** (**R-SAL4**; finding **N-441**,
      and **N-530**'s calibration kind). `salary.calibration_overrides` carries effective tax rates
      derived from ONE stub on ONE date and stores no date, so entering a stub restates every
      paycheck the owner ever had: `+$29.09` on each of seven RECEIVED paychecks, visible since
      `balance:X-au-d` made a settled row's plan a derivation. The remedy the developer ruled on
      2026-09-02: an effective date on the row, a migration backfilling the existing row at its
      stub's date, and the engine resolving the calibration per period as it resolves a raise --
      applied from its date forward and never before. **It can never move a balance** (a settled row
      is worth what it recorded); what it moves is the EXPECTED figure and the variance shown beside
      it. The row's "derived effective rates" that nothing recomputes (**N-530**) are decided here:
      the rates derive from the stub's stated figures at read, or the stub's figures are the stored
      fact and the rates go (**balance:R-IY**). A migration; own review pass.
- [ ] **R14 -- what a payroll deduction's gross is priced from** -- the DECOMPOSED parent, split
      2026-09-03 (**R-SAL6**) into the EXPAND and the MONEY, because an additive migration is graded
      by its backfill's branches and a money cutover by what it moves against a named input set.
      Carries **D45**.
  - [x] **R14-a** `9e81d9e7` -- an employer contribution NAMES its funding profile (**R-SAL5**) and
        the calendar-wide projection became SINGLE (**N-443**), closing **N-533** / **N-534** with
        it. **A later step must obey**: `investment_params.salary_profile_id` is nullable and
        UNREAD; `R14-b` is its reader and owns what a NULL means at the door.
  - [ ] **R14-b -- what a deduction's gross is priced from** (**R-SAL2**; **D45**, **N-532**).
        `investment_projection._compute_deduction_per_period` divides a profile's stored
        `annual_salary` by the paycheck count, where every sibling surface routes through the
        engine; it sets both the employee contribution and the employer-match basis.
        **R-SAL2 answers the three questions together** (D45 carries what R-F16's revert measured):
        the deduction's OWN profile; THAT period's gross from the engine's per-period breakdown,
        raises applied as-of the period; the period as the clock, never `date.today()`. The tier
        CONSUMES the breakdown map -- the first surface moved onto the shared leaf C12 later
        collapses the rest onto. **Consuming the breakdown deletes more than the gross**:
        `DeductionLine` already carries `target_account_id`, and the engine already applies the
        calendar-year cap per line, so `_annual_cap_averaged` and `_period_capped_total` are a third
        and fourth spelling beside the raise-blind gross (**D45**) and the ignored inflation
        escalation (**N-532**). The two cap semantics differ (even spread against front-loaded):
        state which survives and grade it. **It runs after `S2`** (developer, 2026-09-04) -- that
        `-$19.28` is in the engine this step makes the feed's single source, and the feed is dormant
        today, so shipping first leaves no baseline that could show the difference. **MOVES MONEY**;
        own review pass, own harness.
- [ ] **R15 -- what a payroll deduction's own FREQUENCY means** (**R-SAL3**; findings **F-21**,
      **N-395**). `salary.paycheck_deductions.deductions_per_year` server-defaults to 26 and the
      form offers 26 / 24 / 12; it is never multiplied or divided, only compared, so it is a
      three-valued MODE wearing a biweekly count, and a third reader ignores it altogether -- the
      investment contribution timeline pays every deduction on every payday, 26 times against the 24
      the engine takes (**N-395**). At a weekly cadence "every paycheck" is 52 and "skip the 3rd
      paycheck" names nothing; 11 of the developer's 12 live deductions carry 24. **The ruling**: a
      line's cadence is a RECURRENCE RULE against the pay calendar, `NULL` meaning every paycheck,
      placed by the engine that already places recurring rows -- one cadence engine in the app, and
      `_deduction_applies_at` is deleted. **Its first act is a trace**: how
      `resolve_generation_plan` couples to a template, since a paycheck line has none, and what the
      migration re-expresses the three live values as. The count column moves no figure; APPLYING
      the frequency to the timeline does, at 2/26 of a 24-per-year deduction's annual total, latent
      until one names an investment account. A migration and a form change; own review pass.
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
- [ ] **C12 -- one current-paycheck producer** (findings **P62**, **P63**, **P64**'s engine half;
      **N-443** closes at R14's first leaf, which this step builds on). The paycheck projection is
      spelled THREE times over one calendar -- `income_service.SalaryPricing._net_by_period`,
      `routes/salary/views.py` and `routes/salary/cockpit.py` each load the tax configs and run
      `project_salary` -- and the two route sites keep the whole breakdown where the derivation
      keeps only `net_pay`, so the shared leaf has to be the BREAKDOWN map, moved where every reader
      can reach it (**balance:R-IZ**: where a layer puts the shared leaf out of reach, move the
      leaf). The dashboards' "current paycheck" (`savings_dashboard_service/_metrics`,
      `retirement_dashboard_service`) is one entry of that map. **It needs a RULING first** because
      it changes what `/savings` and `/retirement` publish, and the merged producer gives
      `income_service`'s basis a threaded calendar, so
      **`balance:X-i1` and this step decide for each other**. It also owes the engine's package
      split (**P64**): the module was at exactly 1000 lines until `recurrence:R-F16` took it to 873,
      and this step's own growth is what would spend that room.
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
**N-443**) because commit messages already cite them, and one was minted here from `balance:N-243`'s
dissolved census (**N-530**).

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
