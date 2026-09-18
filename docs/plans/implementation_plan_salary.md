# The paycheck engine: what one paycheck is worth, stated once

**The arc that owns the paycheck engine** -- `paycheck_calculator`, `income_service`,
`calibration_service`, `payroll_basis`, the `routes/salary/` package and the tax services -- minted
2026-09-03 by the developer (**R-SAL1**) because four arcs edited the engine and none named it. Its
rules are `conventions.md`, its findings are `ledger.md` rows whose `arc` reads `salary`, "done" is
`verification.md`, and the ORDER is `steps.md`.

## Where this stands

**`R18-c` shipped 2026-09-16 (`34ad4bda`, ticked 2026-09-18): every payroll line carries a start and
an optional end on its own rule** -- after `R18-a` (`ef0dc831`) renamed the storage to
`salary.paycheck_lines` and `R18-b` (`ad9fed61`) seeded the two EARNING kinds with the engine's one
line pass. `R18-d`, the operator runbook that moves the phone allowance onto a line, is the
developer's act and NOW; `S9` (the blank start stored as blank, **R-SAL39**) waits on
`recurrence:R21`. Before `R18`, `R15` (`77901fe0`) made a deduction's FREQUENCY a recurrence rule on
the row and `S3-f-4` (`329b663d`) shipped `S3`. Archived spans, all under `historical/`: `R15`
(`salary_r15_as_built_2026-09-14.md`), `S3` and `S3-f` (`salary_s3_as_built_2026-09-13.md`,
`salary_s3f_as_built_2026-09-13.md`), `C12` (`salary_c12_as_built_2026-09-18.md`), `R14`
(`salary_r14_as_built_2026-09-11.md`), `S3-e-2` (`salary_s3e2_as_built_2026-09-11.md`) and `S2`
(`salary_s2_as_built_2026-09-04.md`). Nine steps were re-filed or minted into this arc when it was
created (**R-SAL1**), with their ledger rows and the four `balance:X-au-d` findings that had no arc
to go to.

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
        `+$452.42`; closed **D45**, **N-532**. Archived with `R14`.
- [x] **S3** `329b663d` -- the engine prices the WHOLE horizon (**R-SAL10**, **R-SAL11**,
      **R-SAL14**, **R-SAL15**; closed **N-541**); ticked with `S3-f-4`, the last leaf of its last
      leaf. Its fourteen leaves left this document and the index 2026-09-18 (rule 5); the records
      are the four `historical/salary_s3*` files (the span, S3-a..d, S3-f, S3-e-2).
- [x] **R15** `77901fe0` -- what a payroll deduction's own FREQUENCY means: a RECURRENCE RULE on the
      row (**R-SAL3**, **R-SAL29**-**R-SAL32**, **R-SAL35**-**R-SAL37**); ticked with `R15-c`, its
      last leaf. The span as it stood: `historical/salary_r15_as_built_2026-09-14.md`.
- [x] **R15-a** `bdd77055` -- the per-month CEILING as the cadence's third value (**R-SAL29** as
      amended); migration `ef32dfe4cd8e`; NO FIGURE MOVED. Archived with `R15`.
- [x] **R15-b** `4ed9b5b3` -- the third owning arm, one rule per line (migration `542c61e48ee8`),
      byte-identical over 63 paychecks; closed **F-21**, **SAL-549**, **SAL-556**. With `R15`.
- [x] **R15-c** `77901fe0` -- the deduction form's CADENCE controls (**R-SAL31**, **R-SAL36**,
      **R-SAL37**). Opened **SAL-557** (owner `S6`), **SAL-558** (owner `S5`). Archived with `R15`.
- [ ] **S4 -- a payroll deduction's `annual_cap` is a DATED figure** (finding **N-540**, re-pointed
      here at `S3-f-3`'s tick, developer ruling 2026-09-12). The column is read raw and never
      escalated, so a statutory limit that rises every year is modelled as fixed and understates
      contributions over a long horizon; `tax_config_service` already prices an unconfigured year
      from the latest configured one and says so, which is the shape a dated cap should take.
      `$0.00` today (no live deduction's cap binds). Needs a RULING first.
- [ ] **S5 -- every readiness region renders its own refusals** (findings **SAL-550**, **SAL-551**,
      both from the `S3-f-4` session 2026-09-13; **SAL-558**, the deduction form's refusal swapping
      a page body into its section). The readiness GET answers every refusal as the assumptions rail
      (**R-SAL33**), so the assumed-return, months and contribution inputs outside the rail, and a
      stale or foreign raise id, still leave the card silently at its previous picture; and the SWR
      refusal says "less than or equal to 1" beside a percent box. Display-only, `$0.00`.
- [ ] **S6 -- the rail's raise set is the projection's, and a stale Save is refused** (findings
      **SAL-552**, **SAL-553**, **SAL-554**, the `S3-f` span's openings filed 2026-09-13 when it
      shipped; **SAL-557**, the cadence-only edit that bumps no `version_id`, the same family). The
      `/retirement` rail lists every ACTIVE profile's raises rather than the set the page projects;
      its raise row carries no `version_id`, so a Save from a stale rail is last-write-wins on one
      column; the regeneration is handed one read pass per profile. Needs a RULING on the race
      first. `$0.00`.
- [ ] **S7 -- `projection_inputs.py` splits by shape** (finding **SAL-555**: 994 of 1000 lines). A
      PURE move graded by AST (**R-PC74**'s shape), its own step because no live step edits the
      file. `$0.00`.
- [ ] **S9 -- a blank start is STORED AS BLANK and means "since my paychecks began"** (finding
      **SAL-562**; ruling **R-SAL39**, 2026-09-18): `starts_on` NULL on the payroll arm alone, the
      template arms still NOT NULL by the CHECK, resolved at read time to the owner's true opening
      (`history_opens_on` when stated, else the recorded one; R-SAL36's unit-zero); a typed start
      pinned; every payroll line becomes a rule, deleting R-SAL29's no-rule rewrite in
      `_settle_line_cadence`, `line_applies_on`'s `cadence is None` arm, `EVERY_PAYCHECK` as the
      no-rule phrase and R18-c's typed-opening carve-out. Its migration gives every rule-less line a
      BLANK rule and blanks the starts `R15-b` derived; the stored columns cannot tell a derived
      opening from a typed one, so the predicate is the step's first fork. Its own step outside
      every outcome (S8's precedent); after `recurrence:R21`. Worked year (biweekly, recorded
      opening 2026-03-26, history stated 2026-01-20, base `$7,500`, a `$100` every-paycheck
      deduction capped `$2,000`, a `$45` monthly taxable allowance): the `$100` is taken
      01-29..10-22 under the ruled reading and 03-26..12-17 under the recorded-opening one, `$2,000`
      either way; 12 allowances (`$540`) in the wage base against 10 (`$450`); SS on 12-31 `$245.52`
      against `$251.10`; December keeps `$205.58` more under the ruled reading. `$0.00` while no
      owner has stated a history.
- [ ] **S10 -- the database's defaults agree with the models** (finding **SAL-563**): one migration
      sets the `server_default` autogenerate reports missing on
      `auth.user_settings.safe_withdrawal_rate`, `salary.salary_profiles.name`,
      `salary.tax_bracket_sets.child_credit_amount` and `.other_dependent_credit_amount`, so
      `flask db check` reads clean; `$0.00`. Upkeep; minted 2026-09-18 at `credit_card:CC-1`'s tick
      from its migration check.
- [ ] **S8 -- the retirement gap reads the priced paycheck** (finding **SAL-561**):
      `compute_gap_net_biweekly` and the take-home-rate chip scale BASE by a net-over-gross ratio
      that mixes two figures since R-SAL38; the final-year net becomes the engine's own. `$0.00`
      until an earning line exists; its own step because R18's leaves were ruled.
- [ ] **R18 -- a paycheck is BASE PAY plus a LIST OF LINES** (finding **D59**; ruling **R-SAL38**,
      six forks, 2026-09-15): the DECOMPOSED parent, R-SAL35's shape, four leaves. A line's kind is
      its position in the waterfall (taxable earning, pre-tax deduction, post-tax deduction,
      after-tax earning); a percentage line is a percentage of BASE PAY, never of gross (worked:
      base `$3,631.74`, +`$45` taxable phone allowance, 6% of base `$217.90` against `$220.60` of
      gross). One deposit becomes one app row, the population `bank_import:X-gj-3a` was built for.
  - [x] **R18-a** `ef0dc831` -- the storage rename (`paycheck_lines`, `paycheck_line_kinds`;
        migration `0a4d2c3e89f8`), byte-identical over the 64 saved paychecks; `$0.00`.
  - [x] **R18-b** `ad9fed61` -- the two earning kinds (migration `6c15d2a97b78`), the engine's one
        line pass (`priced_gross`; a percentage line is % of BASE), the line door, the cockpit
        groups; byte-identical over the 64 saved paychecks; opened **SAL-561**.
  - [x] **R18-c** `34ad4bda` -- every line's start and optional end on its own rule (R-SAL30 /
        R-SAL31 amended; no migration, no engine change: the door was missing); the drive
        `d97ca1b5`; `$0.00`. Surfaced the blank-start question -> **R-SAL39**, **SAL-562**, `S9`.
  - [ ] **R18-d** the OPERATOR runbook: Josh ends the Phone template as of August 2026 and enters
        the `$45.00` taxable line, monthly first paycheck, start 2026-09-01. **MOVES MONEY**.
- [x] **C12** `945651c2` -- one current-paycheck producer (**R-SAL25**-**R-SAL28**); closed **P62**,
      **P63**, **P64**'s engine half. As it stood: `historical/salary_c12_as_built_2026-09-18.md`.
- [x] **C12-a** `26a7b816` -- the engine package (**R-SAL27**, **R-SAL28**); NO FIGURE MOVED.
      Archived with `C12`.
- [x] **C12-b** `945651c2` -- `/savings` through the pass's pricer (**R-SAL25**, **R-SAL26**); MOVED
      MONEY (the figures in R-SAL25). Archived with `C12`.
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
