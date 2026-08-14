# Implementation Plan: Recurrence Rule Redesign

## Where this stands

**Plan of record** for the two-axis recurrence model and the cash-date / installment-date split.
R1-R4 ARCHIVED. **R1-R4b-2, R-F1, R-F8, R7a-1 and R7a-2 are IN PRODUCTION** (PRs #85 / #86 / #87,
then `003e3657` and `7c417b90`); R7a-2 opened **F-16** / **F-17**, both ruled into this arc.
**R7b DECOMPOSED into four leaves** 2026-08-12; its first (`e7eb3b1a`) made the authored vocabulary
the two axes.

**What to do next is `steps.md`'s order table; do not re-derive it here.** One ruling is still owed
and section 0 states its two options: the index sequenced R6 behind R5 without the developer saying
whether that discharges "R6 ships with X-an", which was unsatisfiable.

**A new finding RESPECIFIES R7c: row D28.** R-R13 makes `starts_on` the opening validity BOUND and
drops `month_of_year` onto `starts_on.month`; those cannot both hold, because a calendar rule's
cycle phase is a month RESIDUE and the bound is not in it. Measured:
**18 of the 24 live multi-month rules would fire in the wrong months.** The direction is in the row,
and **R7c must RULE it first.**

**Section 4 is the steps; findings, the step index, the rules and the verification standard are the
shared registries** -- `ledger.md`, `steps.md`, `conventions.md` and `verification.md`.

## Rulings

Taken 2026-08-05 (developer):

| fork | ruling |
|---|---|
| Scope | Full two-axis redesign (not additive patterns, not staged dual-write) |
| Add-ons | ALL FOUR: weekly-by-date, nth-weekday, count-bounded end, business-day shift |
| `loan_params.payment_day` | DELETE; every reader goes through one accessor |
| The three defects | Folded into the redesign, not fixed as separate PRs |
| Sequencing vs the balance arc | The arc SPLITS: the engine core is disjoint from the balance arc's live work, the date work is not. "Half B folded into X-an" is SUPERSEDED -- R6 reads a column R5 creates, and R5 cannot precede the balance step deleting from `cash_ledger/_events.py`. A developer ruling is owed; see section 0. The resulting ORDER is `steps.md`'s |
| `PAY_PERIODS_PER_YEAR` | SHIPPED at R7a-2a (`003e3657`) as `pay_calendar.PayCadence`; derivation = `round(365.2425 / cadence_days)`, see section 4a. It surfaced a SECOND stored paycheck count -- finding **F-16**, ruled into this arc as R-F16 |
| **Anchor day vs month-end clamp** | **R-R3's subtype is SUPERSEDED by R-R13: the anchor SPLITS into `starts_on` + `nominal_day`** |
| **A generated row's dates** | **THREE facts, three homes: `occurs_on` (the occurrence), `pay_period_id` (the funding), `due_on` (the installment). `compute_due_date` is DELETED. R-R12, ruled 2026-08-08** |
| **`anchor_date` itself** | **SPLITS before R7c freezes it: `starts_on` (one meaning, every unit) + `nominal_day`. R-R13, ruled 2026-08-08** |
| **Orphaned rules** | **NOT deleted in R2b; they ship with the fix for the leak that makes them. See R-R7** |
| **A pay-period hole (F-10)** | **NORMALIZE, do not check. `budget.pay_periods` stores the PAYDAY; `end_date` and `period_index` are derived and dropped, so a hole and an overlap are both inexpressible. Ruled 2026-08-08; own plan doc, `implementation_plan_pay_calendar.md`** |
| **A failed migration-bearing deploy** | **Back up unconditionally, PRE-FLIGHT whether rollback can work, and REFUSE the rollback that cannot. The app keeps failing loud rather than booting against a schema it cannot describe. R-R14, ruled 2026-08-08** |
| **What "this commitment has ended" means** | **The rule OWES no occurrence on or after the day asked about -- one reading for BOTH closing bounds. A date bound used to wait for its bound date, so the same schedule written as a count stopped counting sooner. Ruled 2026-08-13; shipped as R-D33, $0.00 on live data** |
| Shipped / superseded decisions | Ten rows archived 2026-08-08 to `historical/recurrence_as_built_2026-08-08.md`: the `Once` retirement, R2 and R4 sequencing, the wrong stored paycheck, bound semantics, the `Monthly First` and period-unit anchors, write-door enforcement, and the two R-R12 superseded |
| **Where the two-axis columns live** | **Computed until R7c, stored from R7c. R-R10, archived -- it binds R7c's backfill** |
| **What a Recurrence cell says** | **The UNIFORM shape: every calendar cadence names its cycle the same way, month and day, which is what a yearly rule already did and a quarterly one did not. The month named is the FIRST OCCURRENCE's, not the authored `month_of_year` -- the same residue class either way, and the only one R7c can still express. R-R15, ruled 2026-08-08** |

---

## 0. Why this arc splits, and the one ruling still owed

**The ORDER is `steps.md`'s and is not restated here.** This section holds the MEASUREMENT the order
rests on and the one question the measurement could not answer.

The recurrence work SPLITS. Measured file overlap against
`docs/audits/balance_architecture/README.md`'s live blocks (2026-08-05):

```text
R1-R4 (engine core)  vs X-an                 : 0 files
R1-R4                vs X-f4 deletion set    : 0 files
R1-R4                vs xx-attempt-1-held-rde: 0 files
R1-R4                vs xd-attempt-1-parked  : 1 file  (_recurrence_common.py)
PAY_PERIODS_PER_YEAR vs X-an / X-f4 / X-d    : 0 files

R5+R6 (dates)        vs X-an                 : 4 files -- ALL FOUR of X-an's surfaces
R5+R6                vs X-f4 deletion set    : 1 file  (cash_ledger/_events.py)
```

**What the measurement says.** The ENGINE CORE touches no file the balance arc's anchor half is
editing, so it constrains nothing there; it delivers every-other-month, every-two-years, weekly,
nth-weekday, count-bounded end, business-day shift and defects D1, D2, D3. The DATE work is a
different story: `R5` and `R6` sit on all four of X-an's surfaces and one file of X-f4's deletion
set, which is why they are a separate half and why the index gates them where it does.

**"R6 ships with X-an" was UNSATISFIABLE, and the contradiction was inside this section** (found
2026-08-09 while building X-an's first leaf). Three statements could not all hold: R6's own
specification derives the installment "over the rule plus `due_on`"; `due_on` is created by R5; and
R5 cannot precede the balance step that deletes from `cash_ledger/_events.py`. The sentence right
after the ordering claim already said so -- "`due_on` is created by R5 and READ by R6, so the order
is forced anyway". `steps.md` recorded only `R6 blocked by balance:X-an`, and nothing reconciled the
two; **that column is now graded, so the same contradiction cannot be re-entered** (`conventions.md`
rule 13).

**What survives is the TRACE, not the ship.** The file overlap is real and unchanged: this arc asks
which date IS the contractual installment while X-an asks which date decides a payment already
HAPPENED, and tracing them apart means tracing the loan half's date semantics twice. X-an-a was
traced with R6's question in view and shipped without it.

**`developer-decision` OWED, and it is the one thing in this section the index cannot settle.** Two
options: re-point R6 behind R5, which is what `steps.md` currently records; or split off the half
that needs no `due_on` -- the single `loan_installment_date` accessor over the rule -- and ship that
beside the remaining X-an leaf.
**The index recording the first option is not the developer choosing it.**

**Consequence for Half A:** it must leave the `due_date` contract byte-identical so the R1 oracle
stays green, so no step before R5 touches the column. The transaction-template form's live "Due Day
of Month" field (`_recurrence_fields.html:104-111`, `routes/templates.py:472,649`) stays exactly as
it is until R5 gives the installment a column of its own.

---

## 1. Root cause

`RecurrencePatternEnum` (`app/enums.py:136`) is a closed set of eight names. Four of them --
Monthly, Quarterly, Semi-Annual, Annual -- are the same idea with a different integer baked into the
*name*: every 1, 3, 6, or 12 months. The integer lives in a column (`interval_n`) only for the
paycheck-space family (`Every N Periods`).

**One cadence family got a knob; the other got hardcoded constants.** That is why "every other
month" and "every two years" have nowhere to live. It is not a missing enum member; adding two would
repeat the exercise at the next gap.

Second-order consequences of the same fusion:

- `budget.recurrence_rules` is a wide sparse table: 8 columns whose validity depends on
  `pattern_id`, with **no constraint tying presence to pattern**. The engine papers over malformed
  rules with `rule.month_of_year or 1` and `rule.day_of_month or 1`
  (`recurrence_engine.py:504,516`), so a broken annual rule silently becomes January instead of
  failing loud.
- `Once` is a row in the recurrence table that means "no recurrence", requiring four separate guards
  to suppress: `recurrence_engine.py:115`, `:257`, `recurring_view.py:236`,
  `savings_goal_service.py:427`, plus `templates.py:931`. Transaction templates already model this
  correctly (`recurrence_rule_id IS NULL`); transfers were forced onto `Once` because their form has
  no null option (`_recurrence_fields.html:49`).
- Generation is a **reverse** mapping ("scan every period, ask if it contains the target day"),
  which needs five near-identical `_match_*` helpers (`recurrence_engine.py:527-628`) and is neither
  total nor injective (see D3).

## 2. Evidence

**Archived to `historical/recurrence_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings above state what was decided and the code states what
was built. Cite the archive for how a decision came to be, never for what is true now.

## 3. Target model

**This is the END state. Which step creates each piece is marked; see R2.**

```sql
ref.recurrence_units        -- PERIOD, WEEK, MONTH, YEAR                    [R2a, DONE]
ref.period_placements       -- CONTAINING_DATE, PERIOD_STARTING_ON_OR_AFTER [R2a, DONE]
ref.business_day_shifts     -- NONE, PRIOR, NEXT                            [R2a, DONE]

budget.recurrence_rules
  id               PK
  user_id          FK auth.users CASCADE          NOT NULL
  interval_n       INT   NOT NULL  CHECK (interval_n > 0)
  -- COMPUTED until R7c (R-R10): resolve() emits them from the closed-set
  -- columns plus the owner's schedule, and R7c makes them authored columns.
  unit_id          FK ref.recurrence_units RESTRICT   NOT NULL             [R7c]
  starts_on        DATE  NOT NULL   -- the opening validity bound.  ONE meaning
                                    -- for every unit; R-R13 split it out of
                                    -- anchor_date, which had two      [R7c]
  nominal_day      SMALLINT NULL  CHECK (nominal_day BETWEEN 1 AND 31)
                                    -- the day the rule MEANS, clamped per
                                    -- month by the walk; NULL for a unit
                                    -- that does not fire on one        [R7c]
  placement_id     FK ref.period_placements RESTRICT  NOT NULL             [R7c]
  shift_id         FK ref.business_day_shifts RESTRICT NOT NULL            [R7c]
  end_date         DATE  NULL   CHECK (end_date IS NULL OR end_date >= starts_on)  [CHECK at R7c]
  max_occurrences  INT   NULL   CHECK (max_occurrences IS NULL OR max_occurrences > 0)  [R2b, DONE]
  created_at
  CHECK (end_date IS NULL OR max_occurrences IS NULL)   -- at most one end bound

-- ONE subtype survives R-R13.  It carries a surrogate ``id`` PK plus
-- ``UNIQUE (recurrence_rule_id)``, NOT ``recurrence_rule_id`` as the PK: it is
-- audited, and ``system.audit_trigger_func`` assigns ``v_row_id := NEW.id`` --
-- on a table without that column every INSERT dies with
-- ``record "new" has no field "id"`` (measured on a probe table, R2b).
-- UNIQUE over a NOT NULL column enforces the identical 0-or-1 cardinality.

budget.recurrence_weekday_anchors    [R2b created it, EMPTY; R8 is the first writer]
                                     -- 0..1 per rule; nth-weekday-of-month rules.
                                     -- A REAL subtype: two fields with their own
                                     -- domain, not a repair for a lossy encoding.
  id                  PK
  recurrence_rule_id  FK -> budget.recurrence_rules ON DELETE CASCADE, UNIQUE
  nth_week            INT NOT NULL
                      CHECK (nth_week BETWEEN -1 AND 5 AND nth_week <> 0)  -- -1 = last
  weekday             INT NOT NULL CHECK (weekday BETWEEN 0 AND 6)  -- date.weekday(), 0=Mon

-- DROPPED by R-R13, unwritten: budget.recurrence_month_anchors.  Its whole
-- content was "the day I actually meant", present iff a DATE anchor had lost
-- it; ``nominal_day`` above holds it once instead.
-- NEVER CREATED (R-R12): budget.recurrence_due_dates.  The installment is a
-- fact about a generated ROW (``transactions.due_on``), where the loan ledger
-- already reads it, not about the rule.

budget.transactions / budget.transfers          [R5, ruling R-R12]
  occurs_on        DATE  NOT NULL   -- the date the CADENCE names
  pay_period_id    FK                -- the funding.  Already exists.
  due_on           DATE  NULL        -- the contractual installment, when it
                                     -- differs.  A POSTING INPUT.
  UNIQUE (template_id, scenario_id, occurs_on) WHERE ...   -- re-keyed off the paycheck
```

**`end_date >= starts_on` lands at R7c, with the column it names.** `end_date` is user-authored and
live; 14 live rules RESOLVE to a bound in the future, so setting an earlier end date -- exactly what
the field invites -- would become a `CheckViolation` out of `update_template`'s autoflush, which
nothing catches: the user could not stop an annual bill and the projection would keep charging it.
R7c adds it together with the Marshmallow validator that refuses the pair at the door.

**Zero conditionally-meaningless columns, and since R-R13 zero conditionally-MEANING-SHIFTING
ones.** `nominal_day` is NULL exactly when the unit does not fire on a day of the month, so absence
is the discriminator and no second table repairs a lossy one.

`placement` is well-defined for all four units, and **INERT under the PERIOD unit** -- a claim this
document retired and R3 restored by measurement. The retirement read `anchor_date` as the emitted
occurrence; R3 does not emit it. A pay-period-space rule emits the qualifying PAYCHECK's own
`start_date`, and both placements carry a period start back to that same period. Emitting the payday
is also what reproduces the current row's DATE: `compute_due_date` returns `period.start_date` for a
day-less rule, so emitting a mid-period bound would move it.

**The axis is short one value.** Both members fund on or AFTER the occurrence, so a bill that must
be funded in ADVANCE -- rent due the 1st, paid from the last paycheck before it -- is inexpressible;
at a 90-day cadence today's `Monthly First` funds February's rent on 31 March. Ledger row D20, R8.

**Only the `budget` tables are audited.** An earlier draft of this section said "all three new
tables go into `AUDITED_TABLES`"; measured against that list's own inclusion criteria
(`app/audit_infrastructure.py:46-64`) that is wrong for the `ref` tables -- the `ref` schema is
excluded with exactly one exception, the multi-tenant `ref.account_types`, and adding read-only seed
catalogues would both drown the trail in seed noise and move `EXPECTED_TRIGGER_COUNT`, which the
container entrypoint asserts at start. So: `ref.recurrence_units` / `ref.period_placements` /
`ref.business_day_shifts` are NOT audited (pinned by `TestNotAudited` in
`tests/test_models/test_recurrence_ref_tables_migration.py`), while
`budget.recurrence_weekday_anchors` and `budget.recurrence_month_anchors` went into `AUDITED_TABLES`
in R2b. **R7c drops the second of those with its table (R-R13), so it leaves the list in the same
migration** -- and `EXPECTED_TRIGGER_COUNT` moves DOWN by one, which the container entrypoint
asserts at start and which R7c must therefore update in the same commit.

### Where the old columns went

| old | new |
|---|---|
| `day_of_month` | `nominal_day` (R-R13; a column, not `anchor_date.day` plus a repair table) |
| `month_of_year` | `starts_on.month` |
| `offset_periods` | `starts_on` (a date survives a schedule rebuild; an index does not) -- kills D1 |
| `start_period_id` (weak, bypassable) | deleted; `starts_on` is the start and is applied unconditionally -- kills D2 |
| `start_date` (strong, loan-sync only) | merged into `starts_on`, which R-R13 makes lossless for the PERIOD unit too -- closes D6 |
| `due_day_of_month` + implicit next-month rule | `transactions.due_on` / `transfers.due_on` on the generated ROW (R-R12) |
| `Once` pattern | deleted; `recurrence_rule_id IS NULL` for both template kinds |

### Pattern mapping (migration derivation)

| today | (interval, unit) | placement | starts_on |
|---|---|---|---|
| Every Period | (1, PERIOD) | CONTAINING | start_period.start_date, else first generated row's period start |
| Every N Periods | (N, PERIOD) | CONTAINING | the period whose `period_index % N == offset_periods` |
| Monthly | (1, MONTH) | CONTAINING | first date with `day = day_of_month` on/after the rule's effective start |
| Monthly First | (1, MONTH) | **STARTING_ON_OR_AFTER** | the 1st of the first month whose OWN first paycheck clears the effective start (R-R6) |
| Quarterly | (3, MONTH) | CONTAINING | (month_of_year, day_of_month) |
| Semi-Annual | (6, MONTH) | CONTAINING | (month_of_year, day_of_month) |
| Annual | (1, YEAR) | CONTAINING | (month_of_year, day_of_month) |
| Once | rule DELETED, FK nulled | -- | -- |
| *impossible today* | **(2, MONTH)** every other month | | |
| *impossible today* | **(2, YEAR)** every two years | | |
| *impossible today* | **(1, WEEK)** / **(2, WEEK)** weekly / biweekly by date | | |

Equivalence is provable for the calendar family: old quarterly fires in months
`{moy, moy+3, moy+6, moy+9}` forever, new fires at `anchor + 3k` months, and
`anchor.month === moy (mod 3)` by construction -- identical sets. Same for semi-annual (mod 6) and
annual (mod 12). Month-end **clamping is preserved** (`recurrence_engine.py:546`), so the live
`Walmart+ Membership` rule (day 31, March) stays 31 March.

### Generation becomes one function

**Built at R3.** The signature below is the shipped one, not the drafted one -- the draft read
`occurrences(rule, window)`, which cannot serve the PERIOD unit at all (finding D9, closed):

```text
occurrences(resolved, calendar, *, through) -> Iterator[date]      # forward, by unit
place(occurrence, calendar, placement) -> SchedulePeriod | None    # bisect, NOT total
occurrence_placements(resolved, calendar, *, through=None) -> tuple[...]
```

Forward generation plus placement is explicit -- a date the schedule cannot host is a stated "no
period" rather than one nobody looked for. This kills D3 structurally, at any cadence, and R4
retires all five `_match_*` helpers.

**It is not TOTAL, and the claim that it is was measured false.** This paragraph read "periods are
contiguous by construction (`pay_period_service.py:190`), so every date has exactly one period".
Contiguity holds WITHIN a generated batch and not across batches: `_reject_overlapping_batch` only
requires a new batch to start after the latest existing `end_date`, so `latest_end + 5 days` is
accepted and leaves a gap -- and registration bootstraps a 14-day period 0 that any later real
schedule starts after. `place()` answers "no period" and the composition REPORTS the unplaced
occurrence; generation LOGS it and skips it (ruled 2026-08-08, built at R4b-2, row D7 closed).
Closing the WRITER that permits a gapped batch is finding F-10.

## 4. Step sequence

Each step is a leaf boundary: one commit, its own tests green, independently revertible.
**Budget a neutral review pass and a fix pass into every one.** R2e-3 shipped at roughly twice its
specification because a review found that retiring a value is not done when nothing reads it -- it
is done when the SHAPE replacing it behaves -- and three further findings were false claims in this
arc's own new prose. **A step's behaviour change is stated by measurement, not by a hedge**: R4a's
draft entry claimed it changed nothing at the developer's cadence, and an adversarial review
disproved it -- the four `bounds.*` shapes that moved are on the BIWEEKLY schedule, and every live
loan-payment rule carries the `end_date` that moved them. Against production it moves nothing: 46
live rules, 866 generated rows, byte-identical under both engines. Half A = R1-R4, R7a-1 through
R7c, R8 (+ the Half-A part of R9); Half B = R5, R6 (see section 0).

- [x] **R1-R3 -- oracle, vocabulary, subtypes, write door, `Once` gone, forward engine.** `4b5c577b`
      and the eight commits before it, archived under rule 5 to
      `docs/plans/historical/recurrence_as_built_2026-08-05.md` with the rulings taken for them
      (R-R4, R-R8, R-R10, R-R11). **Read it before R4b or R7c.**

- [x] **R4a, R4b-1, R4b-2 -- the forward cutover.** `1836a928`, `b4538d25`, `75346625`, archived
      under rule 5 to `docs/plans/historical/recurrence_as_built_2026-08-08.md`.
      **D3, D5, D22, D25 and D7 closed; D2 narrowed to the FIELD; D10 re-pointed to R7c.**
      **Read it before R5 or R7c.**

- [ ] **R5 -- a generated row carries THREE dates, in three places.**

**RESPECIFIED by ruling R-R12** (2026-08-08). The old specification -- rename `due_date` to
`occurs_on` as a pure rename, then read a `recurrence_due_dates` table -- rested on a premise
`ledger.md`'s **D4** now records as false, and is not buildable as written.

`occurs_on NOT NULL` is the date the CADENCE names, written from the `OccurrencePlacement` the
engine already computes and `resolve_generation_plan` already carries to the write loop.
`pay_period_id` is the funding and already exists. `due_on NULL` is the contractual installment,
present only when it differs. **`compute_due_date` is DELETED** -- it is the last reader of the
endpoint-month scan R4a deleted from period selection (row D18) and the last place the disproved
`due_dom < dom` next-month inference lives (R-R2). Two things follow that the old specification did
not have: the write loop stops discarding `PlannedOccurrence.occurrence`
(`recurrence_engine.py:342`, `transfer_recurrence.py:127`, the two producers of one fact), and
`idx_transactions_template_period_scenario` can finally re-key onto
`(template, scenario, occurs_on)` -- which the old plan said needed only D18 and did not: for a
day-less rule `compute_due_date` returns `period.start_date` whatever the month scan does, so three
deferred `Monthly First` occurrences still collided. That re-key retires
`RecurrenceCadenceUnsupported` (`app/exceptions.py:110`) and its error handler, a fence for an index
keyed on the wrong column.

**It is a value-SPLITTING migration, not `alter_column ... new_column_name`**, and it is
destructive: it carries the `Review:` line and a refusing downgrade. The split is per row class -- a
loan payment shadow's stored value moves to `due_on` (it is the installment the ledger reads,
`models/transfer.py:169-177`), every other row's stays as `occurs_on`.
**`due_date` is a POSTING INPUT**, so the migration is followed by
`loan_posting_service.backfill_all_loan_postings()`, the caveat `c4e91a7b2d38` already carries. Own
PR. It also deletes a false claim: `compute_due_date`'s docstring names a "due-date backfill script"
that no longer exists anywhere in `scripts/`. Scope, re-measured 2026-08-08 rather than inherited:
**20 Python files touch the column in code**, 6 more name it only in prose, and 4 templates render
it (two carrying `<input name="due_date">`, so the wire format moves too). The plan's four
"highest-risk readers" were wrong about three of them -- `balance_at/_plan.py`,
`rate_period_engine.py` and `loan_payment_service.py` hold **zero** column references between them
and are R6 surfaces.

- [ ] **R6 -- Delete `payment_day`; one installment accessor.**

`loan_installment_date(...)` becomes the single derivation over the rule plus `due_on`.
**There is no `recurrence_due_dates` table and there will not be**: R-R12 puts the installment on
the ROW, where the ledger already reads it, rather than on the rule. 22 files carry `payment_day` (3
doc-only, 2 the definition surface), and **19 of 22 already read it as the installment** -- exactly
two make it a CASH day, `routes/loan/payment_transfer.py:175` and `loan_recurrence_sync.py:172`, and
those two ARE D4's mechanism. Eight distinct producers of "when is this installment due" collapse
into one; the plan previously counted them as one accessor plus a rule read. Kills D4.
**This step needs its own review pass** -- it is the deepest cut into the ledger.

**R7 is THREE leaves**, ruled 2026-08-07: the cutover is the only irreversible-ish one, so the label
and form work is not carried into it.

- [x] **R7a-1 -- the Recurrence cell is one function over `(interval, unit)`.** `6fed14af`, on
      `dev`. `describe()` words a RESOLVED recurrence, so `(2, MONTH)` and `(1, WEEK)` already read
      correctly; `read_rule` makes the page resolve each rule ONCE; the Archived drawer gets a
      producer. **D17 closed.** Measured on production: 45 cells render, 2 move month (Anchor
      Disposal, Clothes) and 5 unauthored shapes reword -- see the commit.

- [x] **R7a-2a -- the paycheck count is derived per owner, not a constant.** `003e3657`. R7a-2 is
      TWO leaves, split with the developer 2026-08-11: the paycheck count is a fact about the
      SCHEDULE and the two-axis reading is a fact about the RULE, so the first ships alone and
      byte-identical. `PAY_PERIODS_PER_YEAR` is deleted; `pay_calendar.PayCadence` derives
      `round(365.2425 / cadence_days)` (section 4a), reached through two doors and refusing an owner
      who has stated none. **Opened F-16 and F-17.**

- [x] **R7a-2b -- the monthly equivalent is ONE expression.** `7c417b90`. Over `(interval_n, unit)`:
      `amount_to_monthly` is DELETED, `obligations_aggregator` computes
      `amount * units_per_year / (interval_n * 12)`, and the infrequent badge derives from the same
      pair. The seam is `recurrence.cadence_of` on the ONE table `resolve()` reads
      (`_frequency.py`); the `None` arm raises now.

- [x] **R7b-1 -- the authored vocabulary becomes the two axes.** `e7eb3b1a`. A caller states
      `(interval_n, unit, placement)`; the closed set is a STORAGE ENCODING crossed by
      `_frequency.encode_cadence` and `decode_pattern`, the latter INVERTED from
      `PATTERN_DERIVATIONS` at import. The `month_step` column, the `family` column and the second
      statement of which units fire on a day of the month are gone with it. Baseline byte-identical;
      on a production clone all 46 rules read and re-author unmoved.

- [x] **R7b-2 -- the form authors that vocabulary.** `ecc4d01b`. Three linked controls, their offer
      set `authorable_cadences` -- the encoder's table INVERTED -- so an unstorable cadence is
      unofferable rather than fenced. `pattern_choices`, `RecurrencePatternField`, the five `REC_*`
      globals and the `offset_periods` schema field go with it. **D8 closed**; **D31**, **D32**
      opened. Six defects were found CLOSING it, four by two adversarial reviews and two by driving
      the form; the commit enumerates them and `anchor_family` moved to `_frequency` with them.

- [x] **R7b-3 -- one "ends" control, and the CHECKs the door does not mirror.** `c8655584`. The
      closing bound is ONE value with THREE shapes above the columns, so a rule cannot state two,
      and `max_occurrences` has its first writer. **D23 closes** on two remedies, not one:
      `single_end_bound` and `positive_max_occurrences` are properties of the TYPE that no door
      refuses; `due_dom` and `valid_offset` are mirrored beside `dom` / `moy`. Takes the
      count-bounded end off **R8**. Four reviews; the commit enumerates what they found.

- [x] **R7b-4 -- the opening bound becomes a DATE.** `67f013c8`. "First paycheck" (a pay-period FK)
      became "Starts on", folded into `start_date` under a MAXIMUM that writes only the term
      deciding it. **D2 and D30 close.** The `Every N Periods` PHASE became a derivation of that
      bound, deleting `_phased_period_anchor`, `RecurrenceSpec.offset_periods` and the negative-
      offset refusal. 46 rules, 880 placed occurrences, 0 moved; the frozen 430-shape oracle is
      byte-identical. What R7c inherits is under R7c.

- [ ] **R7c -- the cutover.**

**What plan step R7b-4 left for THIS step**, moved here because a shipped step's entry is a pointer
rather than an account (`conventions.md` rule 5) and because every ruling below binds the cutover
rather than the leaf that took it.

**R7b is FOUR leaves**, split with the developer 2026-08-12: the vocabulary swap, the form, the
bounds and the opening bound, of which only the last carried a migration. **All four have shipped.**
Every leaf authors through the closed-set columns, so the schema does not move until R7c.

**Two rulings taken 2026-08-12 on the shape of the cadence controls, which R7c must obey.** They are
LINKED rather than independent: the unit repopulates the interval control and the placement select,
which renders only where a `(unit, interval)` pair offers more than one. And the month interval is a
SELECT of 1 / 3 / 6 rather than a number box, because those are the only month cadences the closed
set stores; R7c widens it to a free box when the authored columns land. Three free controls would
offer combinations `encode_cadence` refuses, which is the refusal this design makes unreachable.

**A placement belongs to the `(unit, interval)` PAIR, not to the unit**, and a design keyed on the
unit alone re-opens exactly what that ruling closes. `MONTHLY_FIRST` is
`(1, MONTH, PERIOD_STARTING_ON_OR_AFTER)` and the closed set has no quarterly or semi-annual twin,
so `(3, MONTH, first-paycheck)` is unstorable -- measured, not argued. The offer set therefore
carries whole triples and every consumer filters them. Row **D32** is the cost still owed on it: a
pair that drops the first-paycheck option rewrites the funding choice with nothing on screen saying
which paycheck now pays.

**Where the anchor-family router lives, ruled 2026-08-13.** `anchor_family` and the `FAMILY_*`
constants sit in `_frequency`, not `_resolution`: WHICH derivation a `(unit, placement)` cadence
uses needs no schedule, which is that module's charter, while WHERE the anchor lands needs the whole
calendar. `fires_on_day_of_month` is its projection, and it is what the form asks rather than
keeping a second list of which cadences have a day-of-month coordinate.

**THE PHASE IS A DERIVATION OF THE OPENING BOUND** (developer ruling 2026-08-14, option C of three),
and R7c inherits it. `_derive_offset_periods` answers
`span_containing(effective).period_index % interval_n` -- the ordinal of the paycheck the bound
falls in -- so nothing authors a phase and `offset_periods` is written but never read back. The
alternative was to keep the stored column, which would have opened a new every-N-paychecks rule up
to N-1 paychecks LATE for its whole life. Three consequences R7c must not undo: the `FAMILY_PERIOD`
anchor IS the bound (`_phased_period_anchor` existed only to reconcile two independent statements of
one cadence); `ck_recurrence_rules_valid_offset` is unviolatable rather than mirrored, retiring one
of **D23**'s two remaining mirrors; and the divergence `_occurrence._period_walk` recorded for R7c
-- the advance falling back to the raw bound and generating nothing -- is unreachable, because the
paycheck the bound falls in is in phase by construction. `span_containing` rather than
`period_containing` is what keeps it TOTAL past the horizon, which R7c's NOT NULL columns need.

**The migration's downgrade restores NOTHING, and that is the ruling rather than a shortcut.** The
old code read `start_period_id` for two things: the opening maximum, which `start_date` now HOLDS,
and the phase, which fell back to the `offset_periods` COLUMN -- and the write door always wrote
that column with the value the FK would have derived. Re-deriving the FK would NOT be neutral: a
rule that always had a `start_date` and never had a start period (every loan payment) would newly
acquire one and re-phase. Exercised in both directions on a production clone.

**Any step that changes the recurrence form's controls runs
`tests/manual/verify_recurrence_form.py`.** Two of R7b-2's six defects were invisible to pytest by
construction -- a control hidden by a class, an option hidden by a script, and a style the browser
REFUSED to apply all look identical in rendered HTML.

**R7b-3's unrun debt was PAID at R7b-4**, which ran the script FIRST against the unchanged form
(green, so R7b-3's control was sound), extended it for its own two controls (`_drive_opening_bound`,
18 checks), and ran it again.
**That second run found a 500 the whole 9,293-test suite was green across**: the "Starts on" box is
hidden when the form says "does not repeat", a hidden input still SUBMITS, and `start_date=""`
reached `TransactionTemplate(**data)`, which has no such keyword. Every hand-written payload omitted
the key because a person writing one includes the fields they are thinking about; a browser posts
every control the page renders. Fixed on both tiers, with a route test written from the wire and
shown FAILING against the un-fixed helper before it was kept.

**The transfer form's pay-period `<select>` SURVIVES under its other job** -- which period a
one-time transfer lands in -- and it means ONE thing now: the JS relabelling is deleted, the control
shows only while "Does not repeat" is selected, and it is DISABLED otherwise because a hidden
control still submits. Its owner-check moved with it, from the kind-agnostic F-24 builder to
`transfers.create_transfer_template`; the transaction schema no longer declares the field at all.

**One finding R7b-3 left was stated BACKWARDS, and the wrong direction was the harmless-reading
one.** It said `is_loan_payment` (`settings is not None`) is BROADER than the set
`loan_recurrence_sync` writes bounds for, and that every live loan payment satisfies both. Measured
2026-08-14: **neither real loan payment carries a `loan_payment_settings` row**, so it is NARROWER
and R7b-3's "Ends" lock never fired on either loan -- a user could type an end date on their
mortgage and the next payoff-affecting edit would silently overwrite it. Both bound locks and both
crafted-POST refusals now ask `loan_recurrence_sync.owns_validity_window`, which is the sync's own
precondition.

**The THIRD caller was fixed in the same commit** (developer ruling 2026-08-14, reversing one taken
before the measurement existed). `LOAN_PAYMENT_CANNOT_BE_ONE_TIME` had been left on
`is_loan_payment` on the reasoning that it is about the standing `extra_principal` -- true, and not
the whole of it: clearing the recurrence nulls `recurrence_rule_id`, which is how
`active_recurring_transfer_template` FINDS a loan's payment, so both of the developer's real loans
could be set to "Does not repeat" and left amortizing with nothing projecting a payment. It asks the
UNION now, which keeps the set the refusal was written for and adds the set the harm is measured on.
Its firing control uses the PRODUCTION shape -- a loan payment with no settings row -- and was shown
failing against the predicate it replaced.

**The other inherited finding is unchanged, and it belongs to `balance:X-ah`** -- the step that
already rules every other input-door spelling. The 58 `Schema.validate(...)` calls each followed by
a `load()` of the same payload run every validator twice, and `validate` is
`_do_load(postprocess=False)`, so a `@post_load` refusal escapes as an unhandled 500. The four sites
in this arc already moved to `load_form_or_redirect`, and that function is the pattern the sweep
should copy.

**Three adversarial reviews ran against this leaf before it shipped and every one earned its keep.**
What they found is in the commit; three things they left are here because a later step must act on
them, and the ledger is at its 20-line headroom (`conventions.md` rule 4).

- **The create form's DEFAULT was the money finding, and it was mine to introduce.** The control
  this step replaced was a `<select>` with no empty option preselecting the CURRENT period, so every
  definition ever created carried an opening bound of "the paycheck I am in". A date box defaulting
  to empty made that "unbounded", and the create routes generate over `GenerationSchedule.for_user`
  with no lower window bound -- measured, a `$2,000.00` rent template created today wrote 5
  backdated rows, `$10,000.00`, into pay periods that had already closed. Fixed in-commit
  (`create_form_default_start_date`), with a route test that drives the form's OWN rendered default
  rather than a date the test chose, shown FAILING against the empty default. **Nothing is owed** --
  it is recorded because the lesson generalises: replacing a control that always submitted with one
  that may not is a DEFAULT change, and the suite could not see it because no test asserted the
  generated ROWS of a create.
- **A create into a configured LOAN still discards a typed "Starts on" silently.**
  `materialize_initial_transfers` calls `bind_rule_to_loan`, which overwrites `start_date` with the
  loan's first contractual installment -- the same "accepted then silently discarded" outcome the
  EDIT path refuses with a message. Financially safe (the loan's bound wins, so nothing generates
  pre-origination) and PRE-EXISTING for the closing bound, which R7b-3 left in the same shape. It is
  R7c's to rule with the rest of the create-form controls: lock on a loan destination, or say so in
  the help text.
- **`PayCalendar.period_by_id` has no `app/` caller left**, this step having removed the last one.
  It is the pay-calendar arc's value, so deleting it from the recurrence arc would be the
  out-of-scope change `RecurrenceRule.start_period` was not; both docstrings now say so rather than
  naming a consumer that no longer exists.

**One claim about the frozen oracle is weaker than it reads, stated so it is not over-relied on.**
The 36 `every_n_periods` shapes are re-parameterised onto `start_date`, and every bound they state
is exactly a payday -- so the byte-identical blob proves the derivation over the payday case and
says nothing about a bound landing MID-period, which is the input the change actually introduced.
That case is covered, by two hand-computed cases in `test_recurrence_resolution.py` and one in
`test_recurrence_engine.py`; the blob is not what covers it.

ONE migration: add `unit_id` / `starts_on` / `nominal_day` / `placement_id` / `shift_id`, backfill,
tighten `starts_on` to NOT NULL by the documented three-step (`.claude/rules/database.md`), add
`CHECK (end_date IS NULL OR end_date >= starts_on)` with its Marshmallow mirror, then DROP
`pattern_id` / `day_of_month` / `month_of_year` / `start_period_id` / `offset_periods` in the same
transaction, and DROP the unwritten `budget.recurrence_month_anchors` (R-R13). `due_day_of_month`
and `start_date` survive -- R6 and R9 own those. **`nominal_day` stays NULLABLE**: it is NULL
exactly for a unit that does not fire on a day of the month, which is absence rather than a missing
value.

**Its downgrade round-trips and must REFUSE rather than guess.** With `Once` retired at R2e,
`(interval, unit, placement)` names exactly one closed-set pattern for every shape the app can
author today; a row carrying a cadence the closed set cannot name (every-other-month, a WEEK unit, a
weekday anchor, `max_occurrences`) is unrepresentable, so `downgrade` re-derives what it can and
raises `RuntimeError` naming the offending rule ids otherwise.
**R-R13 makes one half of that round-trip exact rather than derived**: `nominal_day` IS
`day_of_month`.

**Budget the derivation copy.** Measured against the 46 live rules (2026-08-08, re-measured on
`shekel-prod-db`): `unit_id` / `placement_id` / `shift_id` are a per-pattern CASE, 45 of 46 bounds
need only the `_effective_start` maximum (Postgres `GREATEST` skips NULLs, so it is that maximum
exactly), and one `Monthly First` rule needs a lateral scan. Prove it before it ships the way this
arc has twice: drive the migration's own function against `resolve()` over all 430 oracle shapes,
not the 46 live rows. **D10, D12, D21 and D24 all close here.**
**Do not quietly retain `offset_periods`**: it is a stored derivative of `period_index`, so once
that ordinal is DERIVED an inserted payday re-phases every `Every N Periods` rule. Dropping it here
IS the whole remedy for the pay-calendar arc's row **P11** (inert today, `interval_n = 1` on all
46). **This step needs its own review pass.**

**One finding R7b-3 left for this step**, for the reason the two on R7b-4 are there
(`conventions.md` rule 4, the ledger at its headroom): the Recurring surface resolves a
COUNT-bounded rule TWICE per row. `obligations_aggregator` asks `recurrence.has_ended`, which
resolves and walks it, and `recurring_view._build_section` then calls `read_rule`, which resolves
and walks it again. `$0.00` -- both answers agree, being one pure function -- and no live rule
carries a count yet, but it is the redundant-producer-call shape this project treats as a DRY
violation. This step rewrites what the surface reads from a rule, so threading the reading into the
filter is free here.

- [ ] **R8 -- Add-ons.**

WEEK unit, `recurrence_weekday_anchors`, business-day shift.
**The count-bounded end LEFT this list at R7b-3**, which built the control that authors it:
`max_occurrences` has a writer, the occurrence walk has honoured it since R3, and the display, the
obligations filter and the frozen oracle all cover it. Note: the shift applies to the CASH date only
-- a bill due Aug 1 paid Friday because Aug 1 is a Sunday still satisfies the Aug 1 installment, so
`due_on` is never shifted. **R-R13 removed the exclusivity problem R-R3 handed this step**: with one
subtype left, "a rule fires on a day-of-month OR an nth-weekday" is one table and one row, so a
CHECK can express it instead of the authoring seam.

- [ ] **R9 -- Drop the old columns.**

Drops the `ref.recurrence_patterns` table and `pay_period_admin._repoint_recurrence_rules`
(`:756-795`). The `Once` row leaves at R2e; the orphan cleanup and the FK claim that used to live
here were both wrong and belong to R-F6 (see R-R7).

**Two premises to re-check before dropping anything.** `_repoint_recurrence_rules` was to be retired
on the premise that "a date survives a schedule rebuild" -- and it does not survive
`reset_pay_schedule`, which takes an arbitrary new start and cadence. R2d narrows what that costs:
with the anchor computed there is nothing to strand, so what actually needs re-pointing is
`start_period_id` and the `offset_periods` derived from it, which is exactly what the function now
does. And `start_date` is retired on the premise that `anchor_date >= start_date` holds by
construction; it holds for the calendar family only, because a PERIOD-unit anchor is the effective
bound and a period qualifies on its END. Ledger row D6.

Derived simplifications that fall out and must be taken, not left behind:

- `savings_goal_service.amount_to_monthly` (`:406-450`): 8-branch switch -> 4 lines over
  `(interval, unit)`, automatically correct for intervals not yet invented.
- `calendar_service._INFREQUENT_PATTERNS` (`:74-79`): enumerated -> derived.
- The four `Once` guards: deleted.

### Carried steps -- scheduled here so they are not merely remembered

Section 5's ledger carries findings that no numbered step closes: some this arc surfaced elsewhere
and does not own, and some it left in its OWN code and chose not to fix inside a commit that
promised something else. They get steps here so they are scheduled rather than remembered.
**None blocks R1-R9, and none is blocked by them**; each is a standalone commit that can run in any
gap. Do not fold them into a recurrence migration -- an unrelated fix riding in a schema migration
is unreviewable.

- [x] **R-D33 -- a date bound answers from occurrences.** `dd2a5a34`. Both closing bounds answer
      from whether the rule still OWES an occurrence, so "monthly until 31 December" and "monthly
      for 12 occurrences" cannot leave the obligations total on different days. Both carry the
      HORIZON guard that keeps an un-extended pay schedule from reading as a finished commitment.
      Measured $0.00 on the dev clone: the total is 11,066.16 before and after and no template
      changes inclusion. **D33 closes.**

- [x] **R-F1 -- the lagging `ref` identity sequences are in step (F-1).** `44b25ad3`, migration
      `c7f3a9d1e864`, on `dev`. A census of every serial sequence in all five application schemas
      found exactly the five. **Two corrections to the spec it replaced**: the drafted
      `GREATEST(max(id), 1)` takes the greatest against a literal floor rather than the sequence's
      own position, so it would LOWER a sequence sitting ahead of its data; and the repair cannot
      live in `ref_seeds` -- `setval` needs UPDATE, which the app role measurably lacks.

- [ ] **R-F6 -- Close the recurrence-rule leak, then delete what leaked** (finding F-6).

`templates.hard_delete_template` (`:904`) deletes a `TransactionTemplate` and leaves its
`RecurrenceRule` unreferenced forever; the transfer-template path is the same shape. Three rows have
accumulated on production (ids 4, 44, 47 -- R2e-3 deleted 41 and 43, which were `Once` rules too).
**Starts with a ruling, not a keystroke**: either the deletion path deletes the rule with its
template, or ownership inverts so the rule carries the template id and CASCADEs. The FK change R9
used to propose cannot fix it -- see R-R7. Deleting the 3 existing rows rides in the SAME commit, so
the cleanup and its cause are reviewed together; that makes the migration destructive, so it carries
the `Review:` line and a downgrade that refuses with the literal SQL.

- [x] **R-F8 -- the deploy's safety net stops lying (F-8, F-14, R-R14).** `2e63e4f9`, `8aeae48e`,
      `398c332c`. The pre-flight asks whether an image can resolve the revision the database is
      STAMPED at; "does the release add migrations" was directional and read a DOWNGRADE as safe,
      reproducing F-8. Dump-first, full-decode readback, behavioural gate. The symlink is installed,
      so the repo copy IS the live deploy path; the pre-fix hand-copy is kept at
      `/opt/docker/scripts/shekel-deploy.sh.prefix-2026-08-08.bak`.

- [x] **R-F2 -- the ref-seed parity scan ends a statement where the SQL does (F-2).** `672c18b1`.
      Not another keyword: a statement lives inside a Python STRING LITERAL, so the literal is the
      outer bound and the keyword list stays as the inner one. Census: all 78 `INSERT INTO`
      occurrences in 38 migrations sit inside a string constant, 2 constants carry more than one.
      Controls SHOWN to fire against the old reader -- a literal below the seed read as seeded, and
      a docstring-only INSERT counted as one.

- [x] **R-F3 -- a `ref` table's generated PK/UNIQUE names ARE the rule (F-3).** `e37b736c`. Ruled
      2026-08-14 as recommended: the standard exempts the single-column `PRIMARY KEY (id)` and
      `UNIQUE (name)` on a `ref` lookup table, stated in BOTH places the rule lives. Measured
      against the live schema rather than the plan's estimate -- **24 of 24** carry `<table>_pkey`
      and `<table>_name_key`, none a `uq_` name. Rejected: a rename migration across 24 tables, for
      names nothing references.

- [x] **R-F10 -- delete the gap machinery.** `fe365de1`. The same commit as `pay_calendar:C5a`,
      ticked at that arc's **C2-b2**: a period's end is derived from the next payday, so a hole is
      not a state a reader can see. Closed **F-10**. The LOSS survives the state -- an absorbed hole
      leaves an over-long period, which is that arc's **P16** and its `C5b`. Deletion-only; the
      430-shape baseline stayed byte-identical.

- [ ] **R-F12 -- One `PeriodCalendar`, not three period-containing searches** (finding F-12).

**RULED 2026-08-10, and this step's own count was wrong**: an AST census found **SIX**
implementations, not three. The third's fallback (`loan_ledger/_visible.py:150`) is a legitimate
second QUESTION and gets its own named method on the one value -- proven equivalent over 1,800
(shape, day) pairs to "the latest period starting on or before the day, else the earliest", which is
the exact mirror of the `period_starting_on_or_after` this arc's own `PeriodCalendar` already
carries. **DELIVERED by the pay-calendar arc's C2**, now DECOMPOSED into `C2-a`..`C2-f`; `C2-b` is
the leaf that retires `PeriodCalendar` and `SchedulePeriod` into it, and the arc's 430-shape
baseline must stay byte-identical across it. **Tick this box with C2's LAST leaf.**

- [x] **R-F13 -- a baseline REGENERATION run can no longer report success (F-13).** `b97ec1c3`. TWO
      of its three holes no longer existed and were NOT rebuilt: `PlacementOutcome`, the
      `OccurrencePlacement` invariant and the `SCHEDULE_GAP` / `BEYOND_THE_SCHEDULE` members died at
      `pay_calendar:C2-b2` (`fe365de1`). The third survived: the 430-shape gate SKIPS while
      `SHEKEL_UPDATE_RECURRENCE_BASELINE` is set, and a skip reads as a pass. Shown to fire --
      switch on 1 failed / 7 passed / 1 skipped (was 8 passed, 1 skipped), switch off 9 passed.

- [ ] **R-F16 -- ONE producer for "how often am I paid"** (finding F-16).

**Starts with a RULING, not a keystroke.** `salary.salary_profiles.pay_periods_per_year` is a
12/24/26/52 dropdown (`app/templates/salary/form.html:52-57`) and is the DIVISOR the paycheck engine
turns an annual salary into a paycheck with -- `paycheck_calculator:225`, plus `:898`, `:945`,
`retirement_projection:230`, `investment_projection:88/113/147`, `retirement_dashboard_service:752`
and `routes/salary/profiles.py:322`, every one of them `or 26`. `budget.pay_schedule.cadence_days`
is the rhythm R7a-2a's conversions multiply that paycheck back up by.
**No door validates one against the other**, and while both read 26 the two errors cancelled
exactly.

Measured on the developer's `$91,675` salary, true monthly gross `$7,639.58`:

```text
profile 26 / cadence 14d  (production)          before 7,639.58   after 7,639.58   correct
profile 52 / cadence  7d  (consistent weekly)   before 3,819.79   after 7,639.58   FIXED by R7a-2a
profile 12 / cadence 30d  (consistent monthly)  before 16,552.43  after 7,639.58   FIXED by R7a-2a
profile 26 / cadence  7d  (MISMATCHED)          before 7,639.58   after 15,279.17  now visibly wrong
profile 26 / cadence 30d  (MISMATCHED)          before 7,639.58   after 3,525.96   now visibly wrong
```

The remedy is that the engine divides by `PayCadence.periods_per_year` too and the column goes.
**The ruling owed first: what does SEMI-MONTHLY mean here?** 24 is the 1st and the 15th, which no
fixed `cadence_days` can express -- `round(365.2425 / 15) = 24` gives the right COUNT and the wrong
paydays, and the pay-calendar arc's whole model is a fixed-cadence walk (finding F-4 is its sibling:
`pay_periods` stores NOMINAL paydays). Three options, and the step opens by putting them to the
developer: derive the count and accept nominal paydays; keep the column but REFUSE a pair that
disagrees at both write doors; or give the pay calendar a second cadence KIND.

Destructive if the column is dropped, so it carries the `Review:` line and a refusing downgrade.
**MOVES MONEY** and needs its own review pass.

- [ ] **R-F17 -- the two period-INDEX horizon windows** (finding F-17).

`utils/period_projections.py:16` offers `("1 year", 26)` and `routes/accounts/detail.py`'s
`_ONE_YEAR_PERIODS = 26` bounds the "Interest, next 12 months" chip. Both count PERIODS and label
MONTHS, so at a weekly cadence the chip sums six months of interest under a twelve-month heading and
at a monthly cadence two years. Left by R7a-2a because they are index arithmetic rather than the
money constant that step replaced.

**Its own ruling first**: `round(365.2425 / cadence_days)` gives a whole count for the chip, but
`period_projections`' offsets are a module-level tuple of `(label, offset)` pairs shared by several
surfaces, so making them per-owner means deciding whether the LABEL or the OFFSET is the fixed thing
-- and what a fractional period offset means when neither divides evenly. Small, and no migration.

- [x] **R-F7 -- `_first_of_month_anchor` loses two dead guards (D11).** `5ac7ab4d`. Both were
      re-derived from the code before deleting rather than taken from the archived proof: the scan's
      `earliest is not None` asks about a period's OWN month, so that period is in the minimand
      `pay_calendar/_searches.earliest_start_in_month` reduces; the fallback's re-ask could only be
      taken when the loop had already returned. The R1 baseline stayed byte-identical and
      `TestTotality` stayed green unchanged.

## 4a. `PAY_PERIODS_PER_YEAR` (folded into R7a-2)

`PAY_PERIODS_PER_YEAR = Decimal("26")` (`app/utils/money.py:43`) is a magic number while
`cadence_days` is user-selectable 1..365, so every monthly-equivalent figure on `/obligations`,
`/savings`, and the Recurring surface is wrong for any non-biweekly schedule. It is NOT a separate
task: R7a-2 already rewrites `savings_goal_service.amount_to_monthly` from an 8-branch pattern
switch to four lines over `(interval, unit)`, and changing its input from a module constant to a
resolved per-user cadence is the same edit. Nine files reference the constant; overlap with the
balance arc is one file (`savings_dashboard_service/_metrics.py`, vs the X-x held branch).

**Derivation:** `periods_per_year = round(Decimal("365.2425") / cadence_days)`. Biweekly -> 26,
weekly -> 52, a monthly cadence -> 12. Resolved ONCE per request and threaded, never re-queried per
row.

**Why the rounded integer and not the exact rate.** A `365.2425 / 14 = 26.0888` rate was proposed
first and measured against the developer's own rhythm (anchor `period_index 0` = 2026-03-26):

```text
paydays per CALENDAR year:  2016-2025 all 26,  2026 = 27,  2027-2036 all 26,  2037 = 27
rolling-12-month count, every day of 2026-2027:  26 on 655 of 731 days (89.6%)
                                                 27 on  75 of 731 days (10.3%)
```

The 27-paycheck year is real (26 paychecks span 364 days, so the payday calendar slips ~1.25 days a
year and catches an extra payday every ~11 years; it is NOT a leap-year effect -- 2024 was a leap
year with 26). But it is a calendar-BOUNDARY artifact: 2026 has 27 only because Jan 1 and Dec 31
2026 are both paydays. A forward-looking rolling year -- which is what a monthly equivalent is --
holds 26 about 90% of the time. `26.0888` is asymptotically correct and matches no window the
developer budgets against; it would also shift every displayed figure +0.341% on migration day
(`+$25.91/mo` on the live every-period set) for no gained truth.

The only consumer where the long-run rate genuinely differs is the multi-decade retirement
projection: 0.34% over 30 years. Separable if it ever matters.

**And the calendar count is not even what the developer receives.** The 1 Jan 2026 payday fell on a
holiday and was PAID 31 Dec 2025, so 2025 carried the 27th and 2026 receives 26. The stored
`pay_periods.start_date` values are a NOMINAL 14-day walk; the real paydays shift off holidays and
weekends and the table does not model that. The developer has never observed a 27-paycheck year, and
the rounded integer is what matches lived experience -- ruled 2026-08-05.

Two things this surfaces, both pay-period concerns rather than recurrence ones, and both recorded in
section 5 as F-4 and F-5 (CLAUDE.md rule 6: report out of scope, do not fix):

- `pay_periods` stores nominal paydays. A holiday/weekend shift for the PAY SCHEDULE is the sibling
  of R8's business-day shift for recurrence occurrences, and would need the same holiday source.
  Scoping it is its own task.
- A 27-paycheck year is a real budgeting event (one extra Groceries at
  $500, one extra Data Manager paycheck at $2,473.38 of income). Surfacing it is a feature.

## 5. Findings ledger

**Moved to `ledger.md`**, the one findings table for every arc. This arc's rows are the ones whose
`arc` column reads `recurrence`; a row's owner names a step in `steps.md`, whose specification is
section 4 of this document.

They moved because a finding is not arc-local: `P2` / `F-10`, `P3` / `N-123` and `P6` / `F-12` were
each one defect recorded in two ledgers, kept in step by hand, and one of those pairs went unnoticed
for months. The rules the table is graded against are `conventions.md`.

## 6. Alternatives considered and rejected

**Archived to `historical/recurrence_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings above state what was decided and the code states what
was built. Cite the archive for how a decision came to be, never for what is true now.

## 7. Document rules (GATED)

**Moved to `conventions.md`**, one copy for every arc. They were near-identical in three documents
and absent from the fourth.

`tools/plan_gate/` grades this document against them through a pre-commit hook scoped to it and the
CI step that runs the custom pylint checkers -- so EDITING THIS FILE is what runs the gate. This
document's own caps live in the gate's constants beside the other arcs'.
