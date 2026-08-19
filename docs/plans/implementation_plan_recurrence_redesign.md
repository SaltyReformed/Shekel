# Implementation Plan: Recurrence Rule Redesign

## Where this stands

**Plan of record** for the two-axis recurrence model and the cash-date / installment-date split.
R1-R4 ARCHIVED. Which steps are in PRODUCTION is a measurement, never a stored value:
`git branch -r --contains <hash>` against `origin/main`, and `shekel-prod-app`'s own revision label.

**The closed pattern set is GONE, and that is what this arc was for** (R-R16 / R-R18 / R-R27).
`budget.recurrence_rules` states its cadence ONCE -- `interval_n` / `unit_id` / `placement_id` /
`shift_id` / `starts_on` / `nominal_day` -- so every interval is authorable on every unit. R7c
dropped the column and R9 dropped `ref.recurrence_patterns`, its enum and its accessor; nothing in
the application or the suite speaks the eight names.

**R8 DECOMPOSED at R8-a (R-R23), and the split is a measurement.** Three of the four ruled add-ons
need a generated row's own `occurs_on`, which is **R5**'s: `compute_due_date(rule, period)` never
receives the occurrence, so a weekly row, an nth-weekday row and a shifted row each carry a date the
cadence never named. R8-a shipped what does not, and closed **D20** by measuring its premise false.

**What to do next is `steps.md`'s order table; do not re-derive it here.** One ruling is owed and
section 0 states its two options. Section 4 is the steps; the findings, the index, the rules and
`verification.md` are the shared registries in `docs/plans/`.

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
| **Whether `day_of_month` may be dropped before its reader** | **YES, because the column is a derived ENCODING and its reader reads the derivation instead. `recurrence_engine.compute_due_date` dates every generated row from it and plan step R5 deletes that function four ranks later; ledger row D37 named two ways out -- hold the column, or swap the ranks -- and this is a third. `recurrence.scheduling_day_of_month` answers the same value from `(unit_id, placement_id, starts_on, nominal_day)`, and the migration grades the equality in SQL before dropping: 0 of 46 live rules disagree. R-R20, ruled 2026-08-16** |
| **What the form does when a cadence admits ONE funding rule** | **RENDER the "Funded from" row anyway, with help text saying there is nothing to choose. Hiding it is how a bill's funding rule came to change with nothing on screen saying so (ledger row D32), and freeing the interval removes the measured instance outright -- the MONTH unit offers both placements at every interval. The two that remain are PERIOD, where the placement is provably inert, and YEAR, whose first-paycheck anchor is R8's. R-R21, ruled 2026-08-16. **Its second example expired the next day**: R-R24 admitted the YEAR unit's deferring reading, so PERIOD is the only cadence left admitting one placement. The RULING is unchanged -- the row renders either way -- and only the count of cadences it applies to moved** |
| **Whether the AUTHORED month needs surfacing before it is dropped** | **NO: ruling R-R15 already decided the display and the two affected rules fire on the same cycle either way -- `3 = 6 (mod 3)` and `3 = 9 (mod 6)` -- so no generated row and no projected balance moves. The typed month was a second spelling of the residue class the first occurrence carries, not a second fact. Closes ledger row D38 with nothing built. R-R22, ruled 2026-08-16** |
| **What GATES the offer set, and how R8 ships** | **The gate is what the app can HONOUR, derived from two live facts, not from an anchor derivation that was deleted. `anchor_family` and its three `FAMILY_*` constants go: ruling R-R16 made the first occurrence AUTHORED and R7c-b deleted all three derivations the router selected between, leaving it refusing two cadences by citing them. Measured over all eight `(unit, placement)` pairs, its one live projection equals `has_day_of_month_coordinate(unit) and placement is CONTAINING_DATE`. And R8 SPLITS on that measurement: only the gate ships now, as `R8-a`, because the other three add-ons each need a generated row's own `occurs_on` and therefore wait on R5. R-R23, ruled 2026-08-16; shipped as R8-a** |
| **Whether a year-scale cadence may defer onto a later paycheck** | **YES. `(k, YEAR, PERIOD_STARTING_ON_OR_AFTER)` fires on its own authored date every `k` years and defers onto the next paycheck, exactly as its MONTH twin already does; the refusal cited `_first_of_month_anchor`, deleted at R7c-b. 0 of 46 live rules read differently, and a yearly cadence cannot repeat a paycheck at any `cadence_days` in 1-365. It also restores `(12, MONTH, deferred)` -> `(1, YEAR, deferred)`, so one rhythm keeps one spelling. R-R24, ruled 2026-08-16; shipped as R8-a** |
| **Where the nth-weekday coordinate LIVES** | **On `budget.recurrence_rules`, as an EXCLUSIVE ARC of columns under one CHECK, and `budget.recurrence_weekday_anchors` is DROPPED unwritten. "A rule fires on a day-of-month OR an nth-weekday" cannot be a CHECK while the two live in different tables -- PostgreSQL CHECKs are single-table -- so the satellite form would need a trigger or a door-only fence for an invariant the column form makes structural. The same move ruling R-R16 made for `nominal_day` and R7c-c made for the unwritten `recurrence_month_anchors`. R-R25, ruled 2026-08-16; owned by R8-c** |
| **What "non-business day" MEANS** | **Weekends plus the eleven US federal holidays, DERIVED as rules rather than seeded as rows -- they are nth-weekday-of-month rules and fixed dates with weekend observation, so there is no per-year seed to keep current and the derivation composes with R8-c's own machinery. R-R26, ruled 2026-08-16; owned by R8-d. Finding F-4, the PAY SCHEDULE's own holiday shift, is a different question and is not settled by this** |
| **Whether the closed set's TABLE and its ENUM may go in ONE release** | **YES, and R-R11's hazard does not generalise to a dropped TABLE at all -- which an adversarial review of R9 established after the step had argued the opposite. R-R11 held the `Once` ROW because `ref_cache.init` raises for an enum member with no row in a table that EXISTS; `_load_rows` CATCHES the `ProgrammingError` a missing table raises, and `init` records it unavailable and completes, pinned by `test_ref_cache.py::test_init_records_unavailable_table_and_keeps_others_usable`. Three independent reasons the auto-rollback image is safe, in the order they fire: it never reaches `ref_cache`, because `entrypoint.sh` step 3 runs `init_database.py` with `init_ref_cache=False` and its Alembic tree cannot resolve the new revision, so `set -eEuo pipefail` aborts (finding F-8); `shekel-deploy:repin_is_safe` refuses the re-pin for the same reason, leaving the pre-deploy dump as recovery; and booted anyway it would degrade rather than die. Precedent measured the same day: R7c-a, R7c-b and R7c-c all reached production in ONE release (PR #102, `41e09dad`), and R7c-c dropped a column the previous image's ORM mapped. R-R27, ruled 2026-08-17; shipped as R9** |
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

**The axis is short one value, and it is NOT the one this paragraph used to name.** It said both
members fund on or AFTER the occurrence, so a bill that must be funded in ADVANCE is inexpressible,
and named "the last paycheck on or before the occurrence" as the missing member (ledger row D20).
Plan step R8-a measured both halves false. `CONTAINING_DATE` funds from the paycheck whose span
COVERS the occurrence, so its payday is on or before that date by construction, and the member D20
named is that same rule under another name on a calendar whose periods tile.
**D20 CLOSED on that measurement.** What is genuinely inexpressible is a LEAD: funding from a
paycheck EARLIER than the containing one, so rent due 1 August is paid from the 17 July paycheck
rather than the 31 July one. Ledger row **D40**, plan step **R11**, which opens with the ruling of
what the lead is measured in.

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

**Any step that changes the recurrence form's controls runs
`tests/manual/verify_recurrence_form.py`.** A standing mandate for this arc, hoisted here at R7c-c
when the R7c entry that carried it was archived -- it binds steps that have not been written yet, so
it may not live inside a shipped step's pointer. Two of R7b-2's six defects were invisible to pytest
by construction: a control hidden by a class, an option hidden by a script, and a style the browser
REFUSED to apply all look identical in rendered HTML. R7c-c added a third kind -- the run found two
of the harness's OWN checks reading a database the app under test never wrote to, so they would have
passed with every refusal accepted.
**When the dev app is pointed at a database COPY, point the harness there too**
(`VERIFY_DEV_DATABASE`); the `ref` ids a `TEMPLATE` copy carries are identical either way, which is
exactly what made the mismatch invisible.

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

**The four R7b leaves and R7c's first two LEFT this list at plan step R8-a** (`conventions.md` rule
5, one line per step: its id, its commit and what it closed). All six shipped; the first four are
accounted for in `historical/recurrence_as_built_2026-08-14.md` and the last two in
`historical/recurrence_findings_as_built_2026-08-15.md`, which R7c-c's own entry says to read first.

| step | commit | what it did, and what it closed |
|---|---|---|
| `R7b-1` | `e7eb3b1a` | The authored vocabulary becomes the two axes; the closed set becomes a storage ENCODING. Closed nothing |
| `R7b-2` | `ecc4d01b` | The form authors that vocabulary, its offer set derived from the encoder's own table. Closed **D8**; opened **D31**, **D32** |
| `R7b-3` | `c8655584` | One "Ends" control for a bound with three shapes, so a rule cannot state two. Closed **D23**; took the count-bounded end off **R8** |
| `R7b-4` | `67f013c8` | The opening bound becomes a DATE and the `Every N Periods` phase a derivation of it. Closed **D2**, **D30** |
| `R7c-a` | `370a30cc` (migration `f2a94c7e1b60`) | The two-axis columns land NULLABLE, backfilled and dual-written, read by nobody. Closed **D12** |
| `R7c-b` | `900e761a` (migration `b6d41f0a9c27`) | Every reader and the form move onto them; four columns tighten to NOT NULL. Closed **D10**, **D21**, **D24**, **D28**, **D31** |

- [x] **R7a-1 -- the Recurrence cell is one function.** `6fed14af`. Over `(interval, unit)`, so a
      cadence nothing authors yet reads correctly. Closed **D17**.

- [x] **R7a-2a -- the paycheck count is per owner.** `003e3657`. `PAY_PERIODS_PER_YEAR` deleted for
      `PayCadence`'s derivation. Opened **F-16**, **F-17**.

- [x] **R7a-2b -- the monthly equivalent is ONE expression.** `7c417b90`. Over `(interval_n, unit)`.
      All three archived under rule 5 to `historical/recurrence_findings_as_built_2026-08-15.md`.

- [x] **R7c -- THE CUTOVER, the DECOMPOSED parent of three leaves.** `ee35bca7`, ticked with
      `R7c-c`, its last leaf. Split 2026-08-14 (**R-R18**) as an expand / migrate / contract, so the
      destructive DDL came LAST and no leaf wrote a translation shim. Its account, and the five
      rulings that bound all three leaves, are archived under rule 5 to
      `historical/recurrence_r7cc_as_built_2026-08-16.md`.

- [x] **R7c-c -- the closed set dies.** `ee35bca7`, migration `d9f5c1a48b73`. Seven statements of
      six facts dropped, `interval_n` re-pointed off the encoding, the offer set re-based on the
      producer that refuses. Closed **D6**, **D32**, **D37**, **D38**, pay_calendar **P11**; opened
      **D39**. Four rulings 2026-08-16 (**R-R20**, **R-R21**, **R-R22**, D6's). **Still owed**: `R5`
      deletes `compute_due_date`, and the WEEK unit `R8-b` frees is where that migration's SQL and
      its Python twin part. Account: `historical/recurrence_r7cc_as_built_2026-08-16.md`.

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

- [ ] **R8 -- the DECOMPOSED parent of the ruled add-ons.** Split 2026-08-16 (**R-R23**) on a
      measurement, not on size: three of the four add-ons cannot deliver what they promise before
      **R5**. `recurrence_engine.compute_due_date(rule, period)` never receives the occurrence
      (ledger row **D26**), so a generated row is dated from the rule's day of the month or from its
      PAY PERIOD and from nothing else -- and a weekly row, an nth-weekday row and a shifted row
      each name a date neither source can carry. The count-bounded end left at **R7b-3**.

- [x] **R8-a -- the offer set stops being gated on a derivation that was deleted.** `87e2c5b9`.
      `anchor_family` and its three `FAMILY_*` constants go; `authorable_cadences` derives from
      `has_row_date_coordinate` and `emits_period_starts`, so the ONE reading withheld is the `WEEK`
      unit and it drops out of a derivation. Closed **D20**, opened **D40**. Four rulings
      (**R-R23**-**R-R26**) and every measurement are archived to
      `historical/recurrence_r8a_as_built_2026-08-16.md`; read it before R8-b, R8-c, R8-d or R11.

- [ ] **R8-b -- the WEEK unit.** Blocked by **R5**, and the blocker is measured rather than
      inherited: with the router's refusal lifted a `(2, WEEK)` rule already resolves, walks, places
      and words itself correctly -- and every row it generates carries the funding PAYDAY, because
      `scheduling_day_of_month` answers `None` for a unit with no day of the month and
      `compute_due_date` reads `None` as "date this from the period". The step is therefore the
      DELETION of `has_row_date_coordinate` and its raising twin, which R5's `occurs_on` makes
      unnecessary rather than merely satisfied. A second measurement bounds the value: at the
      developer's 14-day cadence `(1, WEEK)` puts TWO occurrences in one paycheck, which
      `idx_transactions_template_period_scenario` cannot hold and
      `_recurrence_common.refuse_unstorable_repeats` refuses -- so weekly-by-date needs R5's re-key
      as well, and only `(2, WEEK)` and coarser are storable before it.

- [ ] **R8-c -- the nth-weekday coordinate.** Blocked by **R5** for the same reason: a "third
      Tuesday" rule has unit MONTH, so `scheduling_day_of_month` answers `starts_on.day` -- the
      anchor's incidental day -- and every generated row is dated on the 17th of its month rather
      than on that month's third Tuesday. That is ledger row **D29**'s display defect in the DATE.
      **RULED 2026-08-16 (R-R25)**: the two fields go on `budget.recurrence_rules` as an EXCLUSIVE
      ARC under one CHECK, and `budget.recurrence_weekday_anchors` is DROPPED unwritten. The plan
      said this invariant becomes "a CHECK against `recurrence_rules.nominal_day`", which is not
      buildable -- a PostgreSQL CHECK cannot reference another table -- and the column form is what
      ruling **R-R16** already did for `nominal_day` and R7c-c already did to the unwritten
      `recurrence_month_anchors`. `_describe._coordinate` must then dispatch on the coordinate KIND
      rather than on "WEEK or else".

- [ ] **R8-d -- the business-day shift.** Blocked by **R5**, and this one cannot even be OBSERVED
      before it: the shift moves an OCCURRENCE and the write loop discards it (**D26**), so no
      stored row's date would move at all -- only which paycheck the occurrence places into.
      **RULED 2026-08-16 (R-R26)**: "non-business day" is weekends plus the eleven US federal
      holidays DERIVED as rules rather than seeded as rows, which needs no per-year migration and
      composes with the nth-weekday machinery R8-c builds. The shift applies to the CASH date only
      -- a bill due Aug 1 paid Friday because Aug 1 is a Sunday still satisfies the Aug 1
      installment, so `due_on` is never shifted. `RecurrenceSpec` carries no `shift` field today and
      `resolve` hardcodes `NONE`; 46 of 46 live rules carry `none`. Finding **F-4** (the PAY
      SCHEDULE's own holiday shift) is a different question and stays separate.

- [x] **R9 -- the closed pattern set's last artefacts die.** `800671a7`, ruling **R-R27**.
      `ref.recurrence_patterns` is DROPPED (`b2e9a47c3f18`) with `RecurrencePatternEnum`,
      `ref_cache.recurrence_pattern_id` and the seed entry behind them, in ONE release rather than
      the two R-R11 had reserved; the test suite's cadence vocabulary moved with them onto the two
      axes. The rollback measurement, the production census and the clone rehearsal are in
      `historical/recurrence_r9_as_built_2026-08-17.md`.

### R10 -- the regeneration's own defect

Found while X-f3b measured the ledger. Its two leaves are below.

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

- [ ] **R11 -- the LEAD placement: fund an occurrence from an EARLIER paycheck.**

**Opened at plan step R8-a, out of what closing ledger row D20 left behind.** D20 said the placement
axis has no "the LAST paycheck on or before the occurrence", so a bill funded IN ADVANCE is
inexpressible, and both halves were measured false: `CONTAINING_DATE` funds from the paycheck whose
span COVERS the occurrence, so its payday is on or before that date by construction -- 0 of 305
seated occurrences across five pay cadences funded after theirs -- and the remedy it named is not a
third rule, because `PayCalendar.period_starting_on_or_before` disagreed with `period_containing` on
0 of 8,460 days of a tiling calendar and past the horizon answers the LAST saved paycheck, which
would seat every future occurrence of every rule in one.

What is genuinely inexpressible is a LEAD: funding from a paycheck EARLIER than the one containing
the occurrence, so rent due 1 August is paid from the 17 July paycheck rather than the 31 July one.
**MOVES MONEY**, so it opens with a ruling and not a keystroke, and the ruling is what the lead is
MEASURED IN: one paycheck back (`PayCalendar.period_starting_before` over the containing period's
own payday, which already exists and needs no new search), or a lead in DAYS placed at
`occurrence - lead_days`, or a lead in days that then places by the CONTAINING rule. The first adds
no authored value and cannot express "three days early"; the last two add an integer to
`budget.recurrence_rules` and need their own domain.

Whatever wins, three things follow that R8-a's code already names: `_describe._placement_note` must
word the new member or RAISE (it is total over the enum), `_parenthetical`'s day-1 collapse is
guarded on the deferring placement precisely so a LEAD cannot silently inherit it, and
`fires_on_day_of_month` stays `False` for it -- so its rows are dated from the funding payday, the
same deliberate state the deferring placement carries under **D26**. Closes **D40**.

- [ ] **R12 -- the deploy script's refusals get a test.**
**Opened at plan step R9, by two independent adversarial reviewers of it.** R-F8 built
`deploy/shekel-deploy.sh`'s two predicates -- `preflight_migrations`, which refuses a TARGET image
that cannot resolve the database's stamp, and `repin_is_safe`, which after a failure decides whether
re-pinning the previous image recovers or kills. Ruling **R-R27** rests R9's one-release drop of
`ref.recurrence_patterns` on the second of those, and R9 deleted `TestDeliberateRefSeedSurplus`,
which was the last EXECUTABLE statement of the hazard that refusal now covers. An executable guard
was replaced by an unexercised one.

**Nothing in the repository drives the script.** `.pre-commit-config.yaml` scopes to `^app/`,
`^scripts/` and `^tools/`; CI lints `app/` and `scripts/`; `pytest` collects `tests/` and
`tools/plan_gate`. `shellcheck` reads it but says nothing about behaviour.

The step DECIDES first where a shell harness runs -- a `tests/` module shelling out to `bash`, a
`bats`-style suite with its own runner, or a Python port of the two predicates with the shell
calling it -- and then covers at minimum: a stamp the target cannot resolve is refused; a stamp the
OLD image cannot resolve refuses the re-pin; an unchanged stamp after a container that never started
still re-pins; and an unreadable `alembic_version` is treated as unsafe. Each arm shown FIRING,
which is the standard the arc's own verification file sets. Closes **D41**.

**Ruling R-R28 (2026-08-19): semi-monthly pay keeps the NOMINAL 15-day walk.** No fixed
`cadence_days` expresses the 1st and the 15th, `round(365.2425 / 15)` gives the right COUNT and
drifting paydays, and a monthly cadence already carries exactly that limitation; a day-of-month
schedule KIND is scheduled as **R13** rather than blocking R-F16 on it. Chosen from four options.

### Carried steps -- scheduled here so they are not merely remembered

Section 5's ledger carries findings that no numbered step closes: some this arc surfaced elsewhere
and does not own, and some it left in its OWN code and chose not to fix inside a commit that
promised something else. They get steps here so they are scheduled rather than remembered.
**None blocks R1-R9, and none is blocked by them**; each is a standalone commit that can run in any
gap. Do not fold them into a recurrence migration -- an unrelated fix riding in a schema migration
is unreviewable.

**R-F2, R-F3 and R-F8 left this list on 2026-08-17** with their `steps.md` rows, archived as one
completed span to `historical/recurrence_findings_span_as_built_2026-08-17.md` (rule 5) when
`bank_import:X-f6a-2` needed the room. Each closed one `F-` finding on its own commit and blocked
nothing; that record names all three hashes, and says why `R-F1` stayed.

- [x] **R-D33 -- a date bound answers from occurrences.** `dd2a5a34`. Both closing bounds answer
      from whether the rule still OWES one, so two spellings of one schedule cannot leave the
      obligations total on different days. Closed **D33**.

- [x] **R-F1 -- the lagging `ref` identity sequences are in step.** `44b25ad3`, migration
      `c7f3a9d1e864`. Closed **F-1**. Account archived to
      `historical/recurrence_as_built_2026-08-15.md`.
      **Kept in the index when its three siblings were archived** (2026-08-17): a plan-gate control
      uses this id as its worked example of the PREFIX trap, and archiving it emptied that trap out
      of the corpus. Finding **D42**.

- [ ] **R-F6 -- Close the recurrence-rule leak, then delete what leaked** (finding F-6).

`templates.hard_delete_template` (`:904`) deletes a `TransactionTemplate` and leaves its
`RecurrenceRule` unreferenced forever; the transfer-template path is the same shape. Three rows have
accumulated on production (ids 4, 44, 47 -- R2e-3 deleted 41 and 43, which were `Once` rules too).
**Starts with a ruling, not a keystroke**: either the deletion path deletes the rule with its
template, or ownership inverts so the rule carries the template id and CASCADEs. The FK change R9
used to propose cannot fix it -- see R-R7. Deleting the 3 existing rows rides in the SAME commit, so
the cleanup and its cause are reviewed together; that makes the migration destructive, so it carries
the `Review:` line and a downgrade that refuses with the literal SQL.

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

- [x] **R-F16 -- ONE producer for "how often am I paid".** `4258ce28`, migration `f2b7c40d918e`.
      `salary_profiles.pay_periods_per_year` dropped; the engine takes a `PayrollBasis` binding a
      profile to its owner's cadence, so a mismatched pair is unrepresentable. Cross-validating the
      two columns was measured impossible: 5 of 365 legal cadences have a dropdown value that can
      agree. The investment path's employer-match basis stopped being raise-BLIND. Closed **F-16**;
      opened **D43**, **D44**.

- [ ] **R13 -- a DAY-OF-MONTH pay schedule** (ruling **R-R28**).

`budget.pay_schedule` holds one fact, `cadence_days`, and every payday is a fixed-length walk from
the anchor. Semi-monthly pay is not: it is the 1st and the 15th (or the 15th and the last day), and
`round(365.2425 / 15) = 24` gives an owner the right COUNT with paydays that drift through the month
-- Jan 1, Jan 16, Jan 31, Feb 15. **Monthly already carries the identical limitation** (a 30-day
walk is not "the 1st"), and pay-calendar finding **F-4** records that `pay_periods` stores NOMINAL
paydays generally, so this is one shape rather than a semi-monthly special case.

The step gives the schedule a cadence KIND -- fixed-days, or one/two days of the month -- and
branches THREE producers on it: `pay_period_write.record_paydays` (which spaces a batch),
`pay_calendar._derive.derive_periods` (whose last period's end is cadence-projected), and
`PayCadence.periods_per_year` (which must answer 24 without dividing). It is ranked last in this arc
deliberately: it edits the pay-calendar package's core, which `pay_calendar:C2`'s remaining leaves
are still moving, and nothing in either arc depends on it.

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
