# Implementation Plan: Recurrence Rule Redesign

## Where this stands

**Plan of record** for the two-axis recurrence model and the cash-date / installment-date split.
R1-R4 ARCHIVED. **R1-R4b-2, R-F1 and R-F8 ARE IN PRODUCTION** (PRs #85 / #86, head `c7f3a9d1e864`).

**R7a-1 IS DONE on `dev`, UNPUSHED**: the Recurrence column is one function over `(interval, unit)`
and D17 is closed. **R7a is TWO leaves** and this was the first; the plan's single entry was also
measured wrong in three ways, all corrected in R7a-2's entry.

**NEXT = R7a-2** (the monthly equivalent and `PAY_PERIODS_PER_YEAR`, section 4a), then R7b. R5 and
R6 wait on the balance arc (section 0). **Also live:** R-F6, R-F7, R-F13.

**A new finding RESPECIFIES R7c: row D28.** R-R13 makes `starts_on` the opening validity BOUND and
drops `month_of_year` onto `starts_on.month`; those cannot both hold, because a calendar rule's
cycle phase is a month RESIDUE and the bound is not in it. Measured:
**18 of the 24 live multi-month rules would fire in the wrong months.** The direction is in the row.
**R7c must RULE it first.**

**Section 4 is the steps; findings, the step index and the rules are the shared registries.**

## Rulings

Taken 2026-08-05 (developer):

| fork | ruling |
|---|---|
| Scope | Full two-axis redesign (not additive patterns, not staged dual-write) |
| Add-ons | ALL FOUR: weekly-by-date, nth-weekday, count-bounded end, business-day shift |
| `loan_params.payment_day` | DELETE; every reader goes through one accessor |
| The three defects | Folded into the redesign, not fixed as separate PRs |
| Sequencing vs the balance arc | **Half A now; Half B folded into X-an.** See section 0 |
| `PAY_PERIODS_PER_YEAR` | Folded into R7a-2 (which already rewrites `amount_to_monthly`); derivation = `round(365.2425 / cadence_days)`, see section 4a |
| **Anchor day vs month-end clamp** | **R-R3's subtype is SUPERSEDED by R-R13: the anchor SPLITS into `starts_on` + `nominal_day`** |
| **A generated row's dates** | **THREE facts, three homes: `occurs_on` (the occurrence), `pay_period_id` (the funding), `due_on` (the installment). `compute_due_date` is DELETED. R-R12, ruled 2026-08-08** |
| **`anchor_date` itself** | **SPLITS before R7c freezes it: `starts_on` (one meaning, every unit) + `nominal_day`. R-R13, ruled 2026-08-08** |
| **Orphaned rules** | **NOT deleted in R2b; they ship with the fix for the leak that makes them. See R-R7** |
| **A pay-period hole (F-10)** | **NORMALIZE, do not check. `budget.pay_periods` stores the PAYDAY; `end_date` and `period_index` are derived and dropped, so a hole and an overlap are both inexpressible. Ruled 2026-08-08; own plan doc, `implementation_plan_pay_calendar.md`** |
| **A failed migration-bearing deploy** | **Back up unconditionally, PRE-FLIGHT whether rollback can work, and REFUSE the rollback that cannot. The app keeps failing loud rather than booting against a schema it cannot describe. R-R14, ruled 2026-08-08** |
| Shipped / superseded decisions | Ten rows archived 2026-08-08 to `historical/recurrence_as_built_2026-08-08.md`: the `Once` retirement, R2 and R4 sequencing, the wrong stored paycheck, bound semantics, the `Monthly First` and period-unit anchors, write-door enforcement, and the two R-R12 superseded |
| **Where the two-axis columns live** | **Computed until R7c, stored from R7c. R-R10, archived -- it binds R7c's backfill** |
| **What a Recurrence cell says** | **The UNIFORM shape: every calendar cadence names its cycle the same way, month and day, which is what a yearly rule already did and a quarterly one did not. The month named is the FIRST OCCURRENCE's, not the authored `month_of_year` -- the same residue class either way, and the only one R7c can still express. R-R15, ruled 2026-08-08** |

---

## 0. Sequencing against the balance-architecture arc

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

**Half A -- R1, R2, R3, R4, R7a-1 through R7c, R8. Runs NOW**, in the block-6/7 "interleaves
anywhere" slot the balance README already defines. It does not touch a file the anchor half is
editing. Delivers every-other-month, every-two-years, weekly, nth-weekday, count-bounded end,
business-day shift, and defects D1, D2, D3.

**Half B -- R5, R6 and the remainder of R9. NOT one unit, and saying so is the correction**
(2026-08-08). "Half B runs WITH X-an" was loose prose over two different gates, and the balance
README states both: **R6 ships with X-an**, because X-an moves the resolver's replay/projection cut
off `period_start` onto `settled_on` while R6 deletes the `payment_day` argument X-an's fallback
path reads -- one loan-date-semantics trace seen from two sides. **R5 waits on X-f4**, which deletes
from `cash_ledger/_events.py`. X-f4 is three steps behind X-an in that arc's block 1, with X-f3
(moves money, own PR) between them, so the two cannot ship together. R-R12 makes the split clean
rather than awkward: `due_on` is created by R5 and READ by R6, so the order is forced anyway.

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

Live dev-clone census, 50 rules (2026-08-05):

```text
pattern            rules  dom duedom  moy  end start stperiod off!=0  n!=1
Annual                20   20      0   20    0     0       20      0     0
Monthly               14   14      0    0    4     2       13      0     0
Every Period           7    0      0    0    1     0        5      0     0
Once                   4    0      0    0    0     0        4      0     0
Semi-Annual            2    2      0    2    0     0        2      0     0
Quarterly              2    2      0    2    0     0        2      0     0
Monthly First          1    0      0    0    0     0        1      0     0
ORPHANED rules            : 5      (recurrence_rule_id is ON DELETE SET NULL
                                    on both templates -- deleting a template
                                    leaks its rule forever)
```

`due_day_of_month` is used by ZERO rules. `interval_n` / `offset_periods` are at their defaults in
ALL 50.

### D2 -- an edit ignores the chosen "First paycheck"

`start_period_id` is consulted only when `effective_from is None` (`recurrence_engine.py:121`); the
update route always passes `effective_from = data.pop("effective_from", date.today())`
(`templates.py:643`). Probe:

```text
PROBE: chosen start period_index = 8
PROBE: after CREATE  earliest_index/count = (8, 2)
PROBE: after EDIT    earliest_index/count = (4, 6)
```

Four transactions materialised in pay periods the user explicitly excluded. The model docstring
already documents the bypass (`recurrence_rule.py:63-68`) and it is unfixed. 45 of 50 live rules
carry a `start_period_id` (47, not the 45 an earlier draft said -- the census table above sums to
47).

### D3 -- the monthly matcher was neither total nor injective (CLOSED at R4a)

Kept because **D18 is its surviving half**: `compute_due_date` is the last reader of the same
endpoint-month scan, so the defect moved from period selection into the row's DATE rather than
dying. The probe (a monthly day-15 rule finding 6 of 12 occurrences at a 90-day cadence, and
returning one period twice) is in `recurrence_as_built_2026-08-08.md`.

### D4 -- cash date and installment date cannot differ for a loan

Traced on the live Van Loan:

```text
loan_params:      payment_day = 22, origination 2023-02-14
recurrence rule:  Monthly, day_of_month = 22, due_day_of_month = NULL
generated:        period 2026-07-16..07-29  ->  due_date 2026-07-22
```

The developer's actual facts: cash leaves 22 Jul; the lender's installment is due 1 Aug. Three
columns cover two facts, and one does double duty:

| fact | true value | column today |
|---|---|---|
| cash date (money moves, drives period placement) | Jul 22 | `recurrence_rules.day_of_month` = 22 |
| contractual installment (drives the amortization ledger) | Aug 1 | `recurrence_rules.due_day_of_month` = NULL **and** `loan_params.payment_day` = 22 |

**An earlier draft of this section called the generated row's `due_date` the CASH date. It is not**,
and the correction is what forces R-R12: `models/transfer.py:169-177` and
`loan_loaders.loan_payment_due_date:576-644` both state that on a loan payment the stored `due_date`
IS the installment, that the genesis walk orders payments by it, and that its strict
`anchor_date < due_date` post-anchor boundary is applied against it -- so moving it moves the POSTED
balance. Migration `c4e91a7b2d38` already rewrote live rows off the pay-period basis onto it.
Measured on production 2026-08-08: 112 loan shadows, Van Loan every one on the 22nd, Mortgage every
one on the 1st, and **2 carry a `due_date` outside their own pay period** (the first Van row is
dated 2026-04-22 in a period opening 2026-04-23), which `utils/dates.attribution_date:205-214`
clamps back in for the calendar and the daily balance line while the ledger reads it unclamped. So
the app currently believes the Van installment is the 22nd, ten days from the truth.

`rate_period_engine.monthly_due_date:313` reads `payment_day` as "the contractual due date".
`loan_recurrence_sync._sync_loan_cadence:164` force-overwrites
`rule.day_of_month = params.payment_day` and documents the overwrite as intentional.
**So for a loan the cash date is pinned to the installment date and they can never differ.**

Cost, measured: **zero dollars.** Loan interest is monthly-nominal --
`round_money(balance * annual_rate / 12)`, `app/utils/money.py` `accrue_monthly_interest` -- with no
day count anywhere in the amortization path. What is wrong is date labels (payoff date, schedule
rows, payment history), the `first_installment_date` bound, and one anchor-boundary comparison where
a true-up dated inside the gap could flip a payment's inclusion.

### R-R3 -- one `anchor_date` cannot hold "the 31st", and that is a REGRESSION

Ruled 2026-08-05, before R2b was built. Section 3's `anchor_date DATE` was to be the sole source of
a calendar rule's day-of-month. Measured against the CURRENT engine, it loses information.

Today the day is the integer `day_of_month`, clamped per month (`recurrence_engine.py:546`,
`min(day_of_month, last_day)`), so `day_of_month = 31` means "the last day of every month". A DATE
cannot hold 31 when the anchor month is shorter, and the clamped value it holds instead propagates
forward:

```text
monthly day=31, first occurrence April 2026
  today  : Apr 30  May 31  Jun 30  Jul 31  Aug 31  Sep 30  Oct 31  Nov 30
  anchor : Apr 30  May 30  Jun 30  Jul 30  Aug 30  Sep 30  Oct 30  Nov 30
           (anchor_date = 2026-04-30)          4 of 8 WRONG

monthly day=30, first occurrence February 2027       7 of 8 WRONG
annual  Feb 29, anchored in a non-leap year          never fires on Feb 29 again
```

5 of 12 possible start months (Feb, Apr, Jun, Sep, Nov) clamp a day-31 rule's anchor.
**Zero live rules are affected** -- the only day-31 rule is annual in March -- so this is about what
the model permits going forward, and it is silent: the user sees a plausible date, never an error.

Its 2026-08-05 ruling -- keep `anchor_date` and repair the lossy 29-31 case with a 0..1
`recurrence_month_anchors` subtype -- is **SUPERSEDED by R-R13 below**, which was taken once R-R8
supplied the half of the evidence R-R3 did not have.

### R-R13 -- `anchor_date` SPLITS, and the repair table goes with it

Ruled 2026-08-08. R-R3 weighed "split `anchor_date` into phase + `starts_on`" and rejected it for
making two columns hold one value in the common case. That was decided against the CLAMPING evidence
alone. R-R8 then established a second, larger loss the rejection never weighed:
**the column means different things per unit** and is never NULL, so nothing signals the shift --
`_occurrence.py:61-70` and `ResolvedRecurrence`'s own docstring (`_resolution.py:200-206`) both
state that for the PERIOD unit the anchor is a BOUND and not the first occurrence. A column whose
meaning depends on another column is the exact defect section 1 names, re-created in the model meant
to delete it, and R7c is about to freeze it NOT NULL.

**Ruling: `starts_on DATE NOT NULL` (the opening validity bound, ONE meaning for every unit) plus
`nominal_day SMALLINT NULL CHECK (nominal_day BETWEEN 1 AND 31)` on the rule itself.**
`budget.recurrence_month_anchors` is DROPPED rather than given its first writer.
`recurrence_weekday_anchors` STAYS: an nth-weekday anchor is two fields with their own domain, a
real subtype rather than a repair for a lossy encoding.

Three things this deletes, all of them apparatus the encoding required:

- the side table whose whole content is "the day I actually meant", present iff the primary encoding
  lost it;
- the two-place read `subtype.nominal_day if present else anchor_date.day`, implemented twice today
  (`_occurrence.py:393`, `_occurrence.py:489`);
- **`_require_generable`'s third refusal** (`_occurrence.py:489-505`), which exists only to check
  that the two representations of one fact agree. With the day stored once the disagreement is
  unconstructible, which is the difference between a fence and a structure.

**Consequence for R8.** The mutual-exclusion problem R-R3 handed R8 -- "DDL cannot say at most one
of two subtype rows" -- disappears with the second subtype. What R8 must still refuse is
`nominal_day` beside a weekday anchor, which is one table and one row, so a CHECK can express it.

**Consequence for R7c.** Its backfill writes `starts_on` and `nominal_day` instead of `anchor_date`,
and its downgrade round-trip is easier, not harder: `nominal_day` is exactly `day_of_month`.

### R-R6 -- the bounds move from PERIODS to OCCURRENCES

Ruled 2026-08-05, **SHIPPED at R4a**; the eight measured shapes and the four that moved are in
`docs/plans/historical/recurrence_as_built_2026-08-08.md`. The rule: `match_periods` bounded
PERIODS, so a row could be generated whose OWN occurrence lay outside the window the user stated
(defect D5). The engine bounds OCCURRENCES instead. Zero live rules were affected.

**Same ruling, second question, and this half is still LIVE because R7c's backfill must reproduce
it: `Monthly First`.** Its anchor is the 1st of the first month whose OWN first paycheck falls on or
after the effective start -- not the 1st of the effective month, which would place the first row in
a paycheck EARLIER than the one the user chose, because the placement rule is "the first period
starting on or after the occurrence". On the developer's schedule a rule starting at the 2026-07-30
paycheck skips July (whose first paycheck, 07-02, precedes the chosen start) and anchors 2026-08-01.
Today's engine puts that row on 07-30, the LAST July paycheck, which the rule's own name says it
should not. Zero live rules affected: the one `Monthly First` rule starts at period index 0.

### R-R7 -- the orphaned rules stay until the leak itself is closed

Ruled 2026-08-05. Two claims in earlier drafts of this document were measured and are false. R2b's
said the 5 orphaned rules have "neither a start period nor a template": ids 4, 41, 43 and 47 all
carry `start_period_id` (1, 1, 3, 2), so they are as derivable as any other rule and R2b backfills
all 50. R9's said changing the template FKs off `ON DELETE SET NULL` stops rules leaking: that FK is
`transaction_templates.recurrence_rule_id -> recurrence_rules.id`, so it fires when a RULE is
deleted. The leak runs the other way -- `templates.hard_delete_template` (`templates.py:904`)
deletes the template and leaves its rule -- so no `ondelete` on that FK can close it.

**Ruling: deleting the 5 rows moves out of R2b and into the commit that closes the hole** (step
R-F6), so the cleanup and its cause are reviewed together and R2b stays purely additive.

### R-R2 -- a signed day offset was proposed and disproved

A `due_offset_days SMALLINT` was the first proposal, and the Van case disproves it: `+10` gives Jul
22 -> Aug 1 correctly, then Sep 22 -> Oct 2 and Feb 22 -> Mar 4, both wrong by the month's own
length. "Due on the 1st of the following month" is a (day, month-offset) pair, not a day count.
**R-R12 settles it a level down**: the installment is a DATE on the generated row (`due_on`), so
nothing has to encode an offset at all, and `due_day_of_month`'s implicit `+1` inference from
`due_dom < dom` (`recurrence_engine.py:699`) dies with `compute_due_date` at R5.

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
section 2's D4 now records as false, and is not buildable as written.

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

- [ ] **R7a-2 -- the monthly equivalent becomes one function over the same pair.**

`savings_goal_service.amount_to_monthly` becomes four lines over `(interval_n, unit)`, which is the
same edit that replaces the `PAY_PERIODS_PER_YEAR` constant with a resolved per-user cadence
(section 4a) and derives `calendar_service._INFREQUENT_PATTERNS` instead of enumerating it. Unlike
R7a-1 this one MOVES MONEY -- `/savings`, the Recurring surface's equivalents and the retirement
projection all read it -- so it carries its own review pass.

**It must land BEFORE R8**, and R7a-1 is what makes that an ordering constraint rather than a
preference: the Recurring row now derives "how often" from `(interval, unit)` while its monthly
equivalent still derives it from `pattern_id`. The two cannot disagree over today's closed set, but
the first cadence R8 authors -- `(2, MONTH)` -- reads "Every 2 months" in the cell beside a BLANK
equivalent, because `amount_to_monthly`'s unmodelled-pattern branch answers `None`.

**Three corrections R7a-1 measured in this step's own former specification.** (1) The counts were 7,
not 8: `Once` left at R2e-3. (2) **`amount_to_monthly` cannot be "fed by `resolve()`"** -- `resolve`
requires the owner's `PeriodCalendar` and `obligations_aggregator.template_monthly_or_none` has
none, nor do its callers in `savings_dashboard_service`. `(interval_n, unit)` is a function of the
pattern ALONE, so this step needs a cadence seam that does not take a schedule; that seam is this
step's design question. (3) The `REC_*` globals do NOT all die here: R7a-1 deleted the two the macro
was the only reader of, and `_recurrence_fields.html:57-61,95` reads the other five until R7b
rewrites the form.

- [ ] **R7b -- bounds and the form.**

The form becomes interval + unit + anchor + optional due row, and `max_occurrences` gains its first
writer. Deletes the vestigial `offset_periods` schema field (D8) and `start_period_id`'s "First
paycheck" affordance, so **D1 and D2 die here**. Still authors through the closed-set columns: it
collects the two-axis vocabulary and the seam maps it, so the schema does not move yet.

- [ ] **R7c -- the cutover.**

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

- [ ] **R8 -- Add-ons.**

WEEK unit, `recurrence_weekday_anchors`, business-day shift, count-bounded end. Note: the shift
applies to the CASH date only -- a bill due Aug 1 paid Friday because Aug 1 is a Sunday still
satisfies the Aug 1 installment, so `due_on` is never shifted.
**R-R13 removed the exclusivity problem R-R3 handed this step**: with one subtype left, "a rule
fires on a day-of-month OR an nth-weekday" is one table and one row, so a CHECK can express it
instead of the authoring seam.

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

- [ ] **R-F2 -- Tighten the ref-seed parity scan's statement boundary** (finding F-2).

`_insert_statement_bodies` (`tests/test_models/test_posting_ref_seed_parity.py`) bounds a statement
at the next upper-case SQL keyword, so the LAST `INSERT` in a migration runs to end-of-file and any
quoted literal below it satisfies the scan. Measured honest today: none of `'none'` / `'prior'` /
`'next'` appears in the 1,304 characters of Python after `e7a4d95c2b18`'s final seed. Left out of
R2a deliberately -- tightening the boundary changes the semantics of a scan four other migrations
already depend on, which does not belong in an additive commit. Add a Python-level boundary (a line
beginning `def `) and pin the fix with a test that the scan REJECTS a value appearing only after the
seed statement -- without that negative test the change proves nothing. Its own commit, because it
changes the semantics of a scan four other migrations' coverage rests on: re-run the whole file and
show the existing assertions unchanged.

- [ ] **R-F3 -- Resolve the ref-table constraint-naming disagreement** (finding F-3).

**Starts with a ruling, not a keystroke.** `.claude/rules/database.md` says "name all constraints"
(`uq_<table>_<cols>`), but every `ref` table in the project uses bare
`sa.PrimaryKeyConstraint("id")` / `sa.UniqueConstraint("name")` and lets PostgreSQL name them
`<table>_pkey` / `<table>_name_key`. All ~23 of them, including `f5037400dc5e`'s posting tables,
which are byte-identical in shape -- so this is the house pattern, not an R2a oversight, and R2a
followed it rather than making its three tables the odd ones out. Recommendation: amend the RULE to
exempt single-column PK/UNIQUE on `ref` lookup tables, because the names are never referenced (no
downgrade drops them by name -- the table goes with them) and renaming 23 tables' constraints is a
large migration that buys nothing. The alternative is a rename migration. Either way the outcome
must land in `.claude/rules/database.md` so the next reader is not told two different things.

- [ ] **R-F10 -- Delete the gap machinery the pay-calendar arc makes unconstructible** (finding
      F-10).

**The ruling is TAKEN (2026-08-08, developer) and it is not "reject a gapped batch".** F-10 is a
missing NORMALIZATION: `budget.pay_periods` stores `end_date` and `period_index`, both derived from
the ordered paydays, and a hole is those two columns disagreeing with the next row. The model and
its **six** leaves are `docs/plans/implementation_plan_pay_calendar.md`, whose **C3** deletes
`_reject_overlapping_batch` and whose **C4** drops the columns. That arc's **C1 has SHIPPED**
(`f9d148fe`): the derivation exists, proven byte-identical against all 61 live rows, and nothing
calls it yet. What is left HERE is the recurrence-side consequence and it ships as that arc's
**C5**: `PlacementOutcome.SCHEDULE_GAP`, `GenerationPlan.gaps`,
`_recurrence_common.report_schedule_gaps` and its two call sites (`recurrence_engine.py:309`,
`transfer_recurrence.py:81`) all describe a state the model can no longer produce.
**The state goes; the LOSS does not** (that arc's row **P16**, 2026-08-09): a hole is ABSORBED, not
eliminated, so a 28-day period appears and `should_skip_period` (`:196-232`) bills one monthly where
two are owed. These deletions are the only thing reporting it -- do not tick before P16 is ruled.
Deletion-only, so the 430-shape baseline must stay byte-identical.
**Tick this box with C5's commit.**

- [ ] **R-F12 -- One `PeriodCalendar`, not three period-containing searches** (finding F-12).

**Still starts with a ruling, not a keystroke**: the third implementation's fallback
(`loan_ledger/_visible.py:117`, "the latest period ENDING before the target") is what the other two
deliberately refuse, so unifying them means ruling whether that fallback is a legitimate second
QUESTION -- an anchor correction needs a home period because `journal_entries.pay_period_id` is NOT
NULL -- or a compensator. If it is a second question it gets its own named method on the one value;
it does not get a second implementation. **It is DELIVERED by the pay-calendar arc's C2**, which
builds that one value out of the paydays and makes it TOTAL, and which ships as ONE commit with the
balance arc's X-l -- all three arcs need the same value, and building it twice is the defect this
step exists to remove. **Tick this box with C2's commit.**

- [ ] **R-F13 -- Close the three holes in this arc's own gate** (finding F-13).

One commit, three assertions, no behaviour change. Construct an `OccurrencePlacement` with a
`PLACED` outcome and no period and require the raise. Assert `PlacementOutcome.SCHEDULE_GAP` and
`BEYOND_THE_SCHEDULE` at the `_occurrence` unit level rather than only through the engine's derived
`gaps` tuple. Make the baseline gate refuse to skip: assert `SHEKEL_UPDATE_RECURRENCE_BASELINE` is
unset unless a marker says the run is a regeneration.
**Each of the three gets a mutation shown to fire**, which is the standard this arc already holds
its other controls to -- a control that is not shown to fire is the thing being fixed.

- [ ] **R-F7 -- Delete two unreachable branches in `_first_of_month_anchor`** (finding D11).

Left by R2c-1, found by a neutral review of R2d, and re-derived independently by a coverage audit
2026-08-08. Both guards in `app/services/recurrence/_resolution.py` are provably dead and one
comments a case that cannot execute. **The proof is archived** to
`historical/recurrence_as_built_2026-08-08.md`: an argument plus a 243,018-pair sweep in which
neither branch was ever taken, 32,006 of those reaching the fallback. Deleting them is
behaviour-identical, so the R1 baseline must stay byte-identical and
`test_recurrence_resolution.py::TestTotality` must stay green unchanged.

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

**Archived to `docs/plans/historical/recurrence_as_built_2026-08-08.md`** under rule 5 on
2026-08-08, to buy the room ruling R-R13 needed. Three were weighed before R1 and none has been
re-opened: adding two enum members, RFC 5545 RRULE strings as the storage model, and materialising
occurrence dates into their own table.

## 7. Document rules (GATED)

**Moved to `conventions.md`**, one copy for every arc. They were near-identical in three documents
and absent from the fourth.

`tools/plan_gate/` grades this document against them through a pre-commit hook scoped to it and the
CI step that runs the custom pylint checkers -- so EDITING THIS FILE is what runs the gate. This
document's own caps live in the gate's constants beside the other arcs'.
