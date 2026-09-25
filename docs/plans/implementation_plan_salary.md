# The paycheck engine: what one paycheck is worth, stated once

**The arc that owns the paycheck engine** -- `paycheck_calculator`, `income_service`,
`calibration_service`, `payroll_basis`, the `routes/salary/` package and the tax services -- minted
2026-09-03 by the developer (**R-SAL1**) because four arcs edited the engine and none named it. Its
rules are `conventions.md`, its findings are `ledger.md` rows whose `arc` reads `salary`, "done" is
`verification.md`, and the ORDER is `steps.md`.

## Where this stands

**`X-at-4` (`5d5f5bc1`, 2026-09-25) made a forgotten tax year loud**, `$0.00`; `S15`, the 2027 law,
is due before 2026-12-01 and `S11-c-2` MOVES MONEY. Archived spans: `historical/salary_*`.

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
C12 in with the others; N-443, the three spellings of the paycheck projection, closed at R14's first
leaf, which memoized the breakdown map, and C12 collapsed the dashboards' readers onto it.

**Why each step sits where `steps.md` puts it, which is that table's to say and not this
document's.** The earnings-lines chain follows the bank_import production scope by
`bank_import:R-JJ`, because `R18` is what makes one payroll deposit one app row. `C12-a` decided the
shape `balance:X-i1` waited on (**R-SAL27**): the basis takes the pass's PRICER.

## 1. Root cause

**The engine's inputs are stated without the dimension that makes them a fact, and its answer is
spelled three times.** Both halves of `CLAUDE.md` rule 14, on one subsystem.

**The inputs.** An input that describes a MOMENT must carry it, or every derivation over it is
retroactive. The stored salary is an annual scalar for all time, so a raise and a typo correction
are the same edit (**N-237**), and it is stored in a UNIT the owner's stub does not state -- the
stub says `$3,526.00` a paycheck and `$91,675.00` a year, and `26 x $3,526.00` is `$91,676.00`, a
disagreement the app resolves silently (**N-391**). A calibration is one real stub on one real date;
its row stores that date (`pay_stub_date`) and only displays it; no pricing reader consults it
(**N-441**). A deduction's inflation escalation is anchored on the row's `created_at` (**N-240**). A
deduction's cadence is a biweekly COUNT used as a three-valued MODE (**F-21**), one reader ignores
it (**N-395**), and the earnings side has no lines at all, so an employer allowance with a cadence
becomes a separate income template that misfiles (**D59**). Two profiles on one template in one
scenario were priced by whichever `ORDER BY id` returned, until `X-av-1` made that state unstorable
(**N-294**, closed). A substituted tax year is never shown (**N-235**); a new year's brackets had no
door in the app; since `X-at-1` a release adds each year (**N-236**, closed).

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
   and calibrations ACCUMULATE, none destroyed (**R-SAL9**); a line's amount is dated too, its
   inflation compounding from its latest entry (S12).
3. **A line's cadence is a recurrence RULE against the pay calendar** (**R-SAL3**): a deduction or
   an earnings line names a rule, `NULL` meaning every paycheck, and the engine that already places
   recurring rows decides which paychecks a line lands on. `_deduction_applies_at` and the 26 / 24 /
   12 mode are deleted; an employer allowance is an earnings line with a cadence, not an income
   template (R15, R18).
4. **The period is the clock.** A projected paycheck is priced against its own period's inputs;
   nothing in the engine reads the process clock (`pay_calendar:C10` moves the five salary-route
   reads; the engine itself has read the owner's calendar since `balance:X-bh-1`).
5. **The tax law has one home, in the code, and a substituted tax year says so** (X-at).
6. **The engine is a package**, one private leaf per verb, the answer **P64** recorded at C12-a.

**What becomes impossible rather than checked**: a calibration restating a paycheck received before
its stub; a deduction priced off a profile not its own; a 24-of-26 benefit modelled as 26-of-26; two
readers of one paycheck disagreeing. Each is a state the model cannot express.

## 4. Step sequence

- [ ] **S11 -- a calibration is the STUB TRANSCRIBED, line by line, dated** (**R-SAL41** and
      **R-SAL9** as amended by **R-SAL42**; **SAL-564**; absorbs `S1`'s **N-441**, **N-535**,
      **N-530**): the DECOMPOSED parent of six leaves (2026-09-23), ticking with its last. Today one
      dated stub's four EFFECTIVE rates price every paycheck; the row stores that date
      (`pay_stub_date`) and only displays it; no pricing reader consults it; both calibration doors
      destroy a row. A paycheck copies the latest same-lines stub's four taxes and the app's
      formulas, run on both sides, price the difference, superseding the spec's "federal bracket's
      marginal rate" (a fixed rate withholds tax his credits cancel). **S1's clauses, corrected:**
      nothing is RESTORED (an old row holds five figures and no line; the one entered 2026-08-28,
      deleted 2026-09-19, mis-read the 2026-08-27 stub), the history is TRANSCRIBED, and "all 12
      settled paychecks re-derive" becomes fork 8c's grade against each RECORD.
  - [x] **S11-b** `1d3a2574` -- the door: the payday picked first (**R-SAL50**), one the app holds
        up to the next (**R-SAL48**, **R-SAL49**; a kept date unchecked, **R-SAL53**), a new stub on
        a payday already holding one refused (**R-SAL52**), the printed net checked, one-off clashes
        refused (**R-SAL45**, **R-SAL51**), the comparison and the switch; `delete_line` refuses a
        named line. `$0.00`; opened **SAL-567**, **SAL-568**.
  - [ ] **S11-c -- the engine's calibrated path**: the DECOMPOSED parent of two leaves (the salary
        lane's decomposition, accepted by the coordinator 2026-09-23), ticking with its last.
  - [x] **S11-c-1** `dff66c5e` -- a stub line records the kind it is printed under (**R-SAL58**,
        retiring **R-SAL56**; migration `9b64df71cc34`), and the one-off clash asks only what a save
        adds (**R-SAL57**). `$0.00`; closed **SAL-567**.
  - [ ] **S11-c-2 -- the engine prices from the stubs**: the latest switched-on stub on or before
        the payday with the SAME LINES supplies the four taxes and the formulas the difference
        (`PricedLine`'s line identity shipped at `S11-b`); with no such stub, the latest switched-on
        stub on or before it, of ANY lines, and with none at all the formulas alone (**R-SAL54**,
        settling fork 8b); the stub's side priced by the kinds it records, never its lines' current
        ones (**R-SAL58**), on its own payday's tax year (**R-SAL77**); every priced tax floored at
        `$0.00` (**R-SAL55**); every reader switched, the rates path and `calibrate_*` deleted.
        **MOVES MONEY**, graded on a clone holding the stubs: fork 8c, the projected diff,
        `tests/manual/measure_r18d_phone_line.py` before and after (within a cent of the pair), each
        stub beside the old calibrations of its date (fork 9). Closes **SAL-565**.
  - [ ] **S11-d -- the old table goes**: a migration drops `salary.calibration_overrides` and every
        reference, REFUSING while any calibration the data has held (live, or deleted per the audit
        log) has no stub on its date, and printing any differing figures (fork 9).
  - [ ] **S11-e -- the FICA treatment** (fork 7): a pre-tax line says if it reduces FICA wages, stub
        or no stub; the developer sets the backfill from his stubs. Closes **SAL-566**.
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
- [x] **R18** `0345fbae` -- a paycheck is BASE PAY plus a LIST OF LINES (**R-SAL38**, six forks);
      closed **D59**. Its leaves `R18-a`..`R18-d` left this document and the index 2026-09-24 (rule
      5); the span as it stood: `historical/salary_r18_as_built_2026-09-23.md`.
- [ ] **X-av -- the pay rate is a dated per-paycheck gross** (**balance:R-HW(b)**, **R-SAL59**;
      findings **N-237**, **N-294**, **N-391**, **SAL-569**): the DECOMPOSED parent of four leaves
      (**R-SAL67**), ticking with its last; built beside `S11-c`, neither waiting (**R-SAL62**,
      amending **R-SAL43**'s order). The stub transcription (**R-SAL40**) subsumes the one-stub
      re-read that opened it; **N-240** moved to `S12`; **N-446** stays `balance:X-bp`'s.
  - [x] **X-av-2** `89a56168` -- one engine walk for base pay at each payday's own rhythm, the
        priced paycheck carrying it (**R-SAL66**, **R-SAL70**). `$0.00` stored money; the Recurring
        salary row moved, with the four totals it feeds (Net committed hero, Income chip, Expenses %
        of income, Income subtotal); closed **SAL-569**.
  - [ ] **X-av-3 -- the pay list** (**R-SAL59**-**R-SAL61**, **R-SAL65**, **R-SAL68**; closes
        **N-237**, **N-391**'s app half, **SAL-572**): dated per-paycheck gross entries, one per
        profile and payday, audited; `annual_salary` DROPPED by a migration writing each profile one
        entry (yearly over paychecks a year, to the cent) dated on or before every raise's first
        landing and the first priced payday, else refusing; its downgrade refuses a profile with two
        entries. A payday's base is the latest entry on or before it (else the first), each forecast
        raise landing after that entry's date rounding to the cent; a flat raise adds its yearly
        dollars over paychecks a year. Doors: Record, Fix, Remove (never the only entry); the yearly
        figure shown beside each entry; the GROSS `default_amount` writes go. A yearly edit
        re-prices EVERY projected paycheck since `balance:X-au-d`, past-dated ones too.
        **MOVES MONEY** by cents, projected only; own PR and release. The developer's pay history is
        entered after it, an operator act.
  - [ ] **X-av-4 -- the stub screen offers its base as the pay** (**R-SAL61**): when a stub's base
        differs from the pay that payday prices at, "Use this as my pay from this payday" adds an
        entry through `X-av-3`'s Record door, one function both call. `$0.00` until used.
- [ ] **S12 -- a paycheck line's amount is dated too** (**R-SAL64**; finding **N-240**): a line's
      amount gets the pay's dated list, its inflation forecast compounding from its latest entry, in
      place of `paycheck_calculator/_lines._inflation_years` counting from `profile.created_at`. A
      step of its own after `X-av`; `$0.00` (no line inflates).
- [ ] **S13 -- the salary template's archive and delete doors** (**R-SAL81**; findings **SAL-579**,
      **SAL-580**): the archive door and `hard_delete_template`'s archive fallback archive the
      template under an ACTIVE salary profile, which the Archived drawer shows at its stored
      paycheck, and the no-history arm deletes it outright; no door may leave that profile behind,
      the archive refusing or archiving both together. `$0.00` today.
- [ ] **S14 -- two conversions across a recorded rhythm change** (**R-SAL72**; findings **SAL-571**,
      **SAL-573**): with the developer, decide what "per paycheck" means in the retirement page's
      contribution headroom (`retirement_levers._headroom_per_period`), per year or per current
      paycheck; convert each period the savings dashboard's monthly expense average
      (`_metrics._compute_avg_monthly_expenses`) averages at the rhythm it was paid at
      (`pay_calendar.cadence_on`). `$0.00` on production.
- [ ] **X-at -- the tax law has ONE home, and a year the app lacks is loud** (**R-SAL74**): the
      DECOMPOSED parent of seven leaves, ticking with its last; a new year's law waits on none.
  - [x] **X-at-1** `42bb425d` -- the law's one home, `app/tax_law/`, each year citing its sources,
        read with no query and written by no app door (**R-SAL74**; the tests' law, **R-SAL80**).
        `$0.00`, byte-identical on a production clone; closed **N-236**, **SAL-574**.
  - [ ] **X-at-2 -- the five tables go**: a migration drops `tax_bracket_sets`, `tax_brackets`,
        `state_tax_configs`, `fica_configs`, `state_child_deductions`, their models and audit
        triggers, REFUSING while a row differs from the law, its downgrade recreating them; the
        docstrings still naming those classes go too. `$0.00`; its own PR.
  - [ ] **X-at-3 -- the supported states** (**R-SAL78**): the law lists each one, a no-income-tax
        state as an explicit `$0.00` entry checked against a primary source (the developer names
        which); the profile form offers only those and refuses another, and the engine refuses a
        state the law lacks or a flat state with no rate. R-SAL91's one-sentence wording was ruled
        for ONE state; its two-state form ('... and NC tax uses 2026's and SC tax uses 2026's, ...',
        and the script's '..., except NC on 2026's and SC on 2026's.') is extrapolated and pinned by
        tests. X-at-3 puts it to the developer before a second state lands. In that form a state
        priced on the newest year is named nowhere. Closes **SAL-575**.
  - [x] **X-at-4** `5d5f5bc1` -- the alarms (**R-SAL74**, **R-SAL86**-**R-SAL88**), `$0.00`; where
        the newest year lacks a state, a missing year's one sentence names each part's year
        (**R-SAL91**); the publish job outputs the refusal's start instant and `build-and-push`'s
        first step refuses once it has passed, so a re-run reusing a November check still refuses.
  - [ ] **X-at-5 -- the Taxes tab says which year's rules** (**R-SAL75**'s first half, **R-SAL76**):
        the resolver returns each part's own year (derived, not stored); the liability and report
        carry it to one tab line, the year row and state named. Closes **N-235**'s report half.
  - [ ] **X-at-6 -- the salary pages say which rules or which stub** (**R-SAL75**'s second half),
        after `S11-c-2` rewrites pricing: the priced paycheck carries its basis, and the cockpit,
        the breakdown and the projection print it. Closes **N-235**.
  - [ ] **X-at-7 -- two law figures corrected to their sources** (**SAL-576**, **SAL-577**): 2025's
        standard deduction (P.L. 119-21) and 2026's head-of-household 24%/32% boundary (Rev. Proc.
        2025-32). **MOVES MONEY** (the 2025 Taxes tab); whether 2025 withholding keeps the old
        deduction is a fork for the developer, with worked examples, first.
- [ ] **S15 -- the 2027 tax law** (**R-SAL74**): `_year_2027.py` in `LAW`, each figure citing its
      source (the 2027 IRS Revenue Procedure's brackets, standard deductions and child credits, the
      SSA's 2027 wage base, NC's 2027 rate), once published (late October to November 2026);
      released before 2026-12-01, when `X-at-4`'s refusal begins, the banner showing from 2026-11-01
      until it lands. **MOVES MONEY** (every projected 2027 paycheck).

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
scoped to this file, so EDITING IT runs the gate; its caps (320 lines since the developer raised it
on 2026-09-05, a 20-line signpost) live in the gate's constants beside the other arcs'.
