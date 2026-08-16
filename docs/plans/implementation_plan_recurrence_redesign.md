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

**Row D28 is RULED and R7c is DECOMPOSED into three leaves** (R-R16 / R-R18, 2026-08-14).
`starts_on` is the rule's FIRST OCCURRENCE, not its opening validity bound: a bound is not in the
cycle's residue class, and 18 of the 24 live multi-month rules would have fired in the wrong months
under the earlier reading. The leaves are an expand / migrate / contract and `R7c-a` has landed --
the five columns exist, are backfilled and are dual-written, and nothing reads them yet.

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
| **What `starts_on` MEANS** | **The rule's FIRST OCCURRENCE, ONE meaning for every unit: the first date a calendar cadence fires on, and the payday of the first paycheck a pay-period cadence bills in. Its position in the cycle IS the phase, so `day_of_month` and `month_of_year` are DROPPED rather than renamed. R-R16, ruled 2026-08-14; supersedes R-R13's "opening validity bound" reading and closes row D28** |
| **Whether the YEAR unit survives** | **YES, and `(12k, MONTH)` is CANONICALISED to `(k, YEAR)` at the write door once the interval is free, so one cadence has one spelling. Dropping the unit would move the times-twelve encoding into the form, which is the second vocabulary R7b-1 removed. R-R17, ruled 2026-08-14** |
| **How R7c ships** | **THREE leaves, expand / migrate / contract: `R7c-a` adds the columns and dual-writes them while nothing reads them, `R7c-b` moves every reader and the form onto them, `R7c-c` drops the closed set. The destructive DDL is last and no translation shim is written in any leaf. R-R18, ruled 2026-08-14** |
| **What a REGENERATION does to the rows it already generated** | **It MAINTAINS them; it does not destroy and rebuild them. A row the rule still names is UPDATED in place, a period the rule names with no row gets one, and a row the rule no longer names is removed only when it carries nothing of the owner's. A row holding the owner's own records -- purchases, a note, a hand-entered actual -- is RETAINED and reported, in two shapes: the rule stopped naming its period, or the template's ACCOUNT moved, which drags every purchase onto the new account and invalidates the statement link that cleared it. The delete-and-recreate this replaces was safe only while a generated row was a pure projection of `(template, period)`; `transaction_entries` CASCADE from their parent, so it destroyed `$499.82` of recorded purchases on live data. R-R19, ruled 2026-08-15; shipped as R10-a (`5fc13cdb`), closing row N-292** |

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
  -- All five land at R7c-a NULLABLE and are TIGHTENED at R7c-b, which is
  -- the documented three-step: the leaf that adds a column backfills it,
  -- and the leaf whose readers make a NULL matter is the one that refuses
  -- it.
  unit_id          FK ref.recurrence_units RESTRICT   NOT NULL           [R7c-b]
  starts_on        DATE  NOT NULL   -- the rule's FIRST OCCURRENCE.  ONE
                                    -- meaning for every unit, and its
                                    -- position in the cycle IS the phase,
                                    -- so no month or day column survives
                                    -- beside it.  R-R16               [R7c-a]
  nominal_day      SMALLINT NULL  CHECK (nominal_day IS NULL OR (
                     nominal_day BETWEEN 29 AND 31
                     AND nominal_day > EXTRACT(day FROM starts_on)))
                                    -- the day the rule MEANS when
                                    -- starts_on's own month was too short
                                    -- to hold it.  29-31, not 1-31: a day
                                    -- the date already carries would be a
                                    -- second statement of it.  R-R3 [R7c-a]
  placement_id     FK ref.period_placements RESTRICT  NOT NULL           [R7c-b]
  shift_id         FK ref.business_day_shifts RESTRICT NOT NULL          [R7c-b]
  end_date         DATE  NULL   CHECK (end_date IS NULL OR end_date >= starts_on)  [CHECK at R7c-b]
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
| `day_of_month` | `starts_on.day`, plus `nominal_day` on the one shape a short month loses (R-R16) |
| `month_of_year` | `starts_on.month` -- the FIRST OCCURRENCE's, which is in the cycle's residue class where the bound never was (R-R16, row D28) |
| `offset_periods` | `starts_on` (a date survives a schedule rebuild; an index does not) -- kills D1 |
| `start_period_id` (weak, bypassable) | deleted; `starts_on` is the start and is applied unconditionally -- kills D2 |
| `start_date` (strong, loan-sync only) | `starts_on` at R7c-c, which NARROWS D6 rather than closing it: for a loan rule that fires on a day of the month -- both live loans, and what `routes/loan/payment_transfer.py` sets up -- the first contractual installment IS an occurrence, so the fold is exact; for a DAY-LESS loan rule (row D27's unenforced precondition) `starts_on` is the payday of the paycheck that installment falls in, which selects the same paycheck and generates identically but does not keep the installment DATE. Nothing is lost that the app cannot re-derive: `rate_period_engine.first_installment_date(origination_date, payment_day)` answers it from the loan |
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

- [x] **R7a-1 -- the Recurrence cell is one function.** `6fed14af`. Over `(interval, unit)`, so a
      cadence nothing authors yet reads correctly. Closed **D17**.

- [x] **R7a-2a -- the paycheck count is per owner.** `003e3657`. `PAY_PERIODS_PER_YEAR` deleted for
      `PayCadence`'s derivation. Opened **F-16**, **F-17**.

- [x] **R7a-2b -- the monthly equivalent is ONE expression.** `7c417b90`. Over `(interval_n, unit)`.
      All three archived under rule 5 to `historical/recurrence_findings_as_built_2026-08-15.md`.

- [x] **R7b-1** `e7eb3b1a` -- the authored vocabulary becomes the two axes; the closed set becomes a
      storage ENCODING crossed by one inverted table. **All four R7b leaves are ARCHIVED** to
      `historical/recurrence_as_built_2026-08-14.md` and condensed to one line each here
      (`conventions.md` rule 5); each authors through the closed-set columns, so the schema does not
      move until R7c.

- [x] **R7b-2** `ecc4d01b` -- the form authors that vocabulary, its offer set derived from the
      encoder's own table. Closed **D8**; opened **D31**, **D32**.

- [x] **R7b-3** `c8655584` -- one "ends" control for a bound with three shapes, so a rule cannot
      state two. Closed **D23**; took the count-bounded end off **R8**.

- [x] **R7b-4** `67f013c8` -- the opening bound becomes a DATE and the `Every N Periods` phase
      becomes a derivation of it. Closed **D2**, **D30**.

- [ ] **R7c -- THE CUTOVER, the DECOMPOSED parent of three leaves.**

Split with the developer 2026-08-14 (R-R18) as an expand / migrate / contract: `R7c-a` adds the
two-axis columns and dual-writes them while nothing reads them, `R7c-b` moves every reader and the
form onto them, `R7c-c` drops the closed set. The destructive DDL is therefore LAST, and no leaf
writes a translation shim -- the alternative split, which kept the schema still until the end,
needed three (a route composing `starts_on`, a write door decomposing it back, and a read door
recomposing it behind a `calendar` argument the last leaf would remove again).

**What R7b left for the CUTOVER** sits here rather than in its own entry, because a shipped step's
entry is a pointer rather than an account (`conventions.md` rule 5) and every ruling below binds all
three leaves. R7b's four leaves have all shipped, each authoring through the closed-set columns, so
the schema does not move until this step.

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

- [x] **R7c-a -- the two-axis columns land.** `370a30cc`, migration `f2a94c7e1b60`. All five
      NULLABLE, backfilled, dual-written from the same `resolve` call, and nothing reads them.
      Closed **D12**. Archived under rule 5 to
      `historical/recurrence_findings_as_built_2026-08-15.md`; read it before R7c-c.

- [x] **R7c-b -- every reader and the form move onto the new columns.** `900e761a`, migration
      `b6d41f0a9c27`. One `starts_on` replaces three inputs, four columns tighten to NOT NULL, two
      CHECKs land. **D10, D21, D24, D28 and D31 close**; `end_date >= starts_on` is **R7d**'s (D35).
      Archived under rule 5 to `historical/recurrence_findings_as_built_2026-08-15.md`; read it
      before R7c-c.

- [ ] **R7c-c -- the closed set dies.**

DROP `pattern_id` / `day_of_month` / `month_of_year` / `start_period_id` / `offset_periods`, and
DROP the unwritten `budget.recurrence_month_anchors` (R-R13) -- which moves `EXPECTED_TRIGGER_COUNT`
DOWN by one, asserted by the container entrypoint at start. `encode_cadence`, `decode_pattern`,
`PATTERN_DERIVATIONS`, its inverse and `cadence_of` leave together, and `cadence_of`'s two outside
callers (`obligations_aggregator`, `calendar_infrequency`) read the `unit_id` column instead.

**`start_date` is DROPPED HERE TOO, and this document said otherwise** -- it read "`start_date`
survives: R9 owns it" beside a "where the old columns went" row saying it merges into `starts_on`,
which are two statements of one column's fate. R-R16 settles it: R7c-b moves `RecurrenceSpec` onto
one `starts_on`, and every reader goes with it. Census 2026-08-14, corrected by adversarial review
after a first pass named three of seven: the COLUMN is read by
`_reading.recurrence_spec_with_cadence` (the read door), `_recurrence_form_render` (the prefill),
`_recurrence_form_helpers` (the update merge) and `loan_recurrence_sync` (its no-change guard and
log); the SPEC field is read by `_resolution._effective_start` and `_require_authored_domains`, and
collected by the `schemas/validation/_helpers` field. All seven move at R7c-b, so the column reaches
this leaf dead. **Row D6 is re-pointed off R9 onto this leaf** with it, narrowed as the mapping
table above states. `due_day_of_month` still survives; R5 owns it.

**The INTERVAL is freed HERE**, with the three things that wait on it: `interval_n` is re-pointed
from the encoder's `1` to the two-axis interval in this same migration (Quarterly to 3, Semi-Annual
to 6 -- 4 live rules), the form's month interval widens from a SELECT to a free box, and row **D32**
lands with it, because a free box multiplies the `(unit, interval)` pairs that silently drop the
first-paycheck placement.

**`(12k, MONTH)` is CANONICALISED to `(k, YEAR)` at the write door** (R-R17), which is only
reachable once the interval is free: one cadence gets one spelling, so the Recurring surface cannot
word the same rhythm "Every 12 months" on one row and "Annually" on another.

**Its downgrade must REFUSE rather than guess.** With `Once` retired at R2e,
`(interval, unit, placement)` names exactly one closed-set pattern for every shape the app could
author before this leaf; a row carrying a cadence the closed set cannot name (every-other-month, a
WEEK unit, a weekday anchor) is unrepresentable, so `downgrade` re-derives what it can and raises
`RuntimeError` naming the offending rule ids otherwise. `day_of_month` comes back exactly, from
`starts_on.day` and `nominal_day`.

**Do not quietly retain `offset_periods`**: it is a stored derivative of `period_index`, so once
that ordinal is DERIVED an inserted payday re-phases every `Every N Periods` rule. Dropping it IS
the whole remedy for the pay-calendar arc's row **P11** (inert today, `interval_n = 1` on all 46).
**This leaf needs its own review pass.**

- [ ] **R7d -- a loan payment's validity window stops being a stored column.**

`loan_recurrence_sync` becomes a RESOLVER --
`loan_payment_window(account) -> (starts_on, ends_on | EMPTY)` -- and generation applies that window
ON TOP of the rule's own bound, instead of ten chokepoints writing it into `budget.recurrence_rules`
and hoping no reader gets there first.

**The root cause is a CATEGORY ERROR in the table, and R7c-b is where it became visible.** Those two
columns hold two different KINDS of fact: what a USER AUTHORS about a repeating definition, where a
stop before the start is a mistake to report, and what the app DERIVES for a loan payment, where an
EMPTY window is a legitimate and sometimes correct answer. `ck_recurrence_rules_valid_window` was
drafted into R7c-b and HELD BACK on that measurement (developer ruling, 2026-08-15): originate
2026-08-01 with `payment_day` 1, so the first contractual installment is 2026-09-01, then true the
balance to zero on 2026-08-15 -- `recurrence_end_date` answers `as_of`, the window is empty, forward
generation emits nothing, and that is exactly right for a loan that owes nothing. A CHECK turns it
into an unhandled `CheckViolation` out of the true-up, a params edit or any transfer settle.

**Both local repairs are worse than the state, which is why this is a step rather than a patch.**
Clamping the bound up to `max(as_of, starts_on)` admits ONE occurrence, so a paid-off loan keeps a
projected payment whose cash still debits while the fold books the whole amount to Refund. Archiving
the template from inside a sync is a destructive side effect on a path that runs on every settle,
and a corrected true-up would not undo it.

**What the resolver deletes**, and the reason the step pays for itself: the ten
`sync_recurring_payment_bounds` call sites, the read-path/write-path ordering hazard the module's
own docstring records, the "idempotent WITHIN a day" caveat on `recurrence_end_date`, the double
sync in `create_payment_transfer` (once to bound generation, once to record what the generated plan
implies), and `owns_validity_window`'s documented gap -- it is `sync`'s opening-bound precondition
exactly and its closing-bound one only approximately, so an owner with no baseline scenario sees a
locked "Ends" control for a payoff nothing writes. The CHECK lands in the same migration,
unconditionally true because every remaining row is user-authored. Closes **D35**.

- [ ] **R7e -- the recurrence form's three-state fields become ONE typed submission.**

A recurrence field has three meanings -- *stated*, *cleared*, *not mentioned* -- and HTML form data
has two. The schema's `@post_load` emits a `RecurrenceSubmission` whose fields are each either an
`UNSET` sentinel or a value, and `update_recurrence_rule_from_form` applies it uniformly
(`replace(current, **submission.applied_to(current))`) instead of reading key PRESENCE field by
field.

**Today the third state is a coincidence of the schema declaration.** `_normalize_empty_inputs`
keeps an empty string as a present `None` for an `allow_none` field and drops the key for the rest,
so "cleared" and "not stated" are told apart by whether a field happens to be nullable -- and every
field that needs the distinction grows its own read at the route: `states_a_start = KEY in data` for
`starts_on`, `ctx.end_bound is not None` for the closing bound, and for `due_day_of_month` the read
was simply MISSING. R8's business-day shift is the fourth.

**Both halves of that gap were live at R7c-b.** An amount-only PATCH silently erased a stored
`due_day_of_month`; and the Due Day row is hidden for a cadence anchoring on a paycheck but was
never DISABLED, so a value typed under "every 1 month" still posted after the switch --
`posted=['25']`, caught by `tests/manual/verify_recurrence_form.py` and invisible to the whole
pytest suite. R7c-b made the three fields AGREE (absent keeps, empty clears) and left the reads in
place; this step removes them. Closes **D36**.

- [ ] **R7f -- what a PROGRAMMATICALLY created recurrence starts on.**

`investment.create_contribution_transfer` and `salary/profiles.py`'s profile-template builder both
seat their rule at `calendar.opening_bound()`, so a new contribution or salary profile fans rows
across every pay period the owner has, closed ones included. **MOVES MONEY**, so it is ruled before
it is built.

That is what both routes have always done -- an absent opening bound resolved to the same value
before R7c-b made `starts_on` required -- and R7c-b STATED it rather than changing it, because
changing it was outside that step. The FORM took the other answer for a measured reason:
`create_form_default_starts_on` defaults to today, and the empty state it replaced wrote 5 backdated
rows worth `$10,000.00` into pay periods that had already closed.

The same two lines pass a `date | None` into a `date` field, whose only disposition is
`RecurrenceSpec`'s "states no starts_on" refusal -- a message written for a form, reached by a route
that has none. Whatever this step rules, it states the value honestly at both sites. Closes **D34**.

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

**R10 is the regeneration's own defect, found while X-f3b measured the ledger.** Its two leaves are
below.

- [x] **R10-a -- a regeneration MAINTAINS its rows.** `5fc13cdb`, ruling **R-R19**, closed **N-292**
      (`$499.82` of purchases destroyed by a rename, measured; 0 after).
      **Two things a later step must obey.** The repeat refusal takes a NARROWER blocking set on the
      maintain path, because a maintain pass rewrites the rule's own row rather than adding beside
      it. And `recurrence_engine` is a PACKAGE from here, with `DerivedRowFields` the ONE statement
      of a generated row's derived columns -- a new one belongs there, not in a write path.

- [ ] **R10-b -- the transfer engine onto the same shape.**

`transfer_recurrence.regenerate_for_template` shares `partition_regeneration_rows` and still
hard-deletes and recreates, taking each transfer's shadow pair with it via
`transactions.transfer_id`'s CASCADE. No money RECORDS are at risk -- `entry_service.create_entry`
refuses a transfer row, so a transfer holds no purchases -- which is why it was not in R10-a, and
why it is not a ledger finding. What it shares is the identity churn and the divergence: two engines
answering one question two ways is the drift `_recurrence_common` exists to prevent.

The update door already exists and is the reason this leaf is small:
`transfer_service.update_transfer` takes `amount`, `category_id`, `name`, `due_date`,
`pay_period_id` and `is_override`, and propagates to both shadows atomically (Transfer Invariants
3-5). The leaf is to give the transfer engine its own `DerivedRowFields` twin, route the maintain
pass through that door, and lift whatever of R10-a's classifier is genuinely model-agnostic into
`_recurrence_common` rather than copying it.

### Carried steps -- scheduled here so they are not merely remembered

Section 5's ledger carries findings that no numbered step closes: some this arc surfaced elsewhere
and does not own, and some it left in its OWN code and chose not to fix inside a commit that
promised something else. They get steps here so they are scheduled rather than remembered.
**None blocks R1-R9, and none is blocked by them**; each is a standalone commit that can run in any
gap. Do not fold them into a recurrence migration -- an unrelated fix riding in a schema migration
is unreviewable.

- [x] **R-D33 -- a date bound answers from occurrences.** `dd2a5a34`. Both closing bounds answer
      from whether the rule still OWES one, so two spellings of one schedule cannot leave the
      obligations total on different days. Closed **D33**.

- [x] **R-F1 -- the lagging `ref` identity sequences are in step.** `44b25ad3`, migration
      `c7f3a9d1e864`. Closed **F-1**. Account archived to
      `historical/recurrence_as_built_2026-08-15.md`.

- [ ] **R-F6 -- Close the recurrence-rule leak, then delete what leaked** (finding F-6).

`templates.hard_delete_template` (`:904`) deletes a `TransactionTemplate` and leaves its
`RecurrenceRule` unreferenced forever; the transfer-template path is the same shape. Three rows have
accumulated on production (ids 4, 44, 47 -- R2e-3 deleted 41 and 43, which were `Once` rules too).
**Starts with a ruling, not a keystroke**: either the deletion path deletes the rule with its
template, or ownership inverts so the rule carries the template id and CASCADEs. The FK change R9
used to propose cannot fix it -- see R-R7. Deleting the 3 existing rows rides in the SAME commit, so
the cleanup and its cause are reviewed together; that makes the migration destructive, so it carries
the `Review:` line and a downgrade that refuses with the literal SQL.

- [x] **R-F8 -- the deploy's safety net stops lying.** `2e63e4f9`, `8aeae48e`, `398c332c`. The
      pre-flight asks whether an image can resolve the revision the database is STAMPED at. Closed
      **F-8**, **F-14**.

- [x] **R-F2 -- the ref-seed parity scan ends a statement where the SQL does.** `672c18b1`. Closed
      **F-2**. Account archived to `historical/recurrence_as_built_2026-08-15.md`.

- [x] **R-F3 -- a `ref` table's generated PK/UNIQUE names ARE the rule.** `e37b736c`. Closed
      **F-3**. Account archived to `historical/recurrence_as_built_2026-08-15.md`.

- [x] **R-F10 -- delete the gap machinery.** `fe365de1`. Closed **F-10**; the LOSS survives as
      `pay_calendar:P16`. Account archived to `historical/recurrence_as_built_2026-08-15.md`.

- [ ] **R-F12 -- One `PeriodCalendar`, not three period-containing searches** (finding F-12).

**RULED 2026-08-10, and this step's own count was wrong**: an AST census found **SIX**
implementations, not three. The third's fallback (`loan_ledger/_visible.py:150`) is a legitimate
second QUESTION and gets its own named method on the one value -- proven equivalent over 1,800
(shape, day) pairs to "the latest period starting on or before the day, else the earliest", which is
the exact mirror of the `period_starting_on_or_after` this arc's own `PeriodCalendar` already
carries. **DELIVERED by the pay-calendar arc's C2**, now DECOMPOSED into `C2-a`..`C2-f`; `C2-b` is
the leaf that retires `PeriodCalendar` and `SchedulePeriod` into it, and the arc's 430-shape
baseline must stay byte-identical across it. **Tick this box with C2's LAST leaf.**

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
