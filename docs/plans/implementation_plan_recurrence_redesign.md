# Implementation Plan: Recurrence Rule Redesign

## Where this stands

**Plan of record** for replacing the closed 8-name recurrence pattern set with a two-axis model, and
for untangling the cash-date / installment-date collision the design review surfaced. Design LOCKED
2026-08-05. R1 through R2d are ARCHIVED (section 4).

**R2e-1 and R2e-2 are SHIPPED; R2e-3 is next.** Retiring `Once` rested on two things that had to be
true first, each a measured defect: clearing a recurrence had to actually clear it (R2e-1), and
every recurrence surface had to speak the vocabulary the app MODELS rather than the rows the `ref`
table holds (R2e-2) -- which is what makes R2e-3's deliberately-surviving `Once` row unreachable
rather than merely unoffered. **Next:** R2e-3 -> R3. **Also live:** R-F1, the only carried finding
whose failure mode is a broken deploy, and R-F6, the recurrence-rule leak.

**Where detail lives:** section 4 is the step list, each step carrying its own specification;
section 5 is the findings ledger, one line per finding, each naming the step that closes it; section
7 is the rules this document is GATED on.
**This section is REPLACED each session, never appended to** -- if a paragraph here outlived the
session, it belongs in a step, a ledger row or section 7.

## Rulings

Taken 2026-08-05 (developer):

| fork | ruling |
|---|---|
| Scope | Full two-axis redesign (not additive patterns, not staged dual-write) |
| Add-ons | ALL FOUR: weekly-by-date, nth-weekday, count-bounded end, business-day shift |
| Due date model | Subtype table with `due_day` + explicit `due_month_offset` (a signed day offset was proposed and DISPROVED, see R-R2) |
| `loan_params.payment_day` | DELETE; every reader goes through one accessor |
| Row columns | `due_date` -> `occurs_on` (rename, no value change) + new `due_on` |
| The three defects | Folded into the redesign, not fixed as separate PRs |
| Sequencing vs the balance arc | **Half A now; Half B folded into X-an.** See section 0 |
| `PAY_PERIODS_PER_YEAR` | Folded into R7a (which already rewrites `amount_to_monthly`); derivation = `round(365.2425 / cadence_days)`, see section 4a |
| **Anchor day vs month-end clamp** | **`anchor_date` + a 0..1 `recurrence_month_anchors` subtype. See R-R3** |
| **`Once` rules** | **Retired at R2e, BEFORE the new engine and the cutover. See R-R4** |
| **R2 sequencing** | **R2a (vocabulary) -> R2b (subtypes) -> R2c-1 (the door) -> R2d (stop storing the derivation). All DONE** |
| **Bound semantics** | **Occurrence-bounded, not period-bounded. Four frozen shapes move at R4. See R-R6** |
| **Orphaned rules** | **NOT deleted in R2b; they ship with the fix for the leak that makes them. See R-R7** |
| **Monthly First anchor** | **The 1st of the first month whose OWN first paycheck clears the bound. See R-R6** |
| **Period-unit anchor** | **The bound DATE itself, not a period boundary. R-R8, archived** |
| **Write-door enforcement** | **Nothing to enforce: the derived half is not stored. R-R10, archived** |
| **Where the two-axis columns live** | **Computed until R7c, stored from R7c. R-R10, archived -- it binds R7c's backfill** |

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

**Half A -- R1, R2, R3, R4, R7a-R7c, R8. Runs NOW**, in the block-6/7 "interleaves anywhere" slot
the balance README already defines. It does not touch a file the anchor half is editing. Delivers
every-other-month, every-two-years, weekly, nth-weekday, count-bounded end, business-day shift, and
defects D1, D2, D3.

**Half B -- R5, R6, `recurrence_due_dates`, and the remainder of R9. Runs WITH X-an**, as its second
half. Not merely a file collision: X-an moves the resolver's replay/projection cut off
`period_start` onto `settled_on`, and R6 deletes the `payment_day` argument X-an's fallback path
depends on. They are one loan-date-semantics trace seen from two sides; splitting them means tracing
it twice. R5 additionally waits on X-f4, which deletes from `cash_ledger/_events.py`.

**Consequence for Half A:** it must leave the `due_date` contract byte-identical so the R1 oracle
stays green. `recurrence_due_dates` therefore moves OUT of R2 and into Half B, and Half A's form
offers no separate due-day field.

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

### D3 -- the monthly matcher is neither total nor injective

`_match_monthly` inspects only the months of a period's two ENDPOINTS (`recurrence_engine.py:539`).
`cadence_days` is user-selectable 1..365 (`schemas/validation/pay_periods.py:19`;
`ck_pay_schedule_cadence_range`). Probe at a 90-day cadence, monthly bill on the 15th:

```text
monthly day-15 occurrences found: 6      (expected 12)
  fired in period 0 ... (period 0 returned TWICE)
```

Half the occurrences vanish silently; the duplicate would violate
`idx_transactions_template_period_scenario` (confirmed live) as an IntegrityError, i.e. a 500.
Latent at the 14-day cadence in use.

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

**Ruling: `anchor_date` stays the first occurrence, and a 0..1 subtype carries the nominal day when
the anchor month clamped it.**

```sql
budget.recurrence_month_anchors   [R2b creates it]
                                  -- 0..1 per rule; present iff the anchor month
                                  --   was too short to hold the nominal day
  recurrence_rule_id  PK FK -> budget.recurrence_rules  ON DELETE CASCADE
  nominal_day         SMALLINT NOT NULL CHECK (nominal_day BETWEEN 29 AND 31)
```

`nominal_day = subtype.nominal_day if the row is present else anchor_date.day`. A rule whose day is
1-28 can never be clamped and carries no subtype row, so the common case costs nothing. This is the
design's own idiom -- presence is the discriminator, PK+FK enforces the cardinality -- and it is why
the three rejected alternatives lost: a nullable `anchor_day` column on the rule re-adds a column
whose validity depends on the unit (the exact defect section 1 names); splitting `anchor_date` into
phase + `starts_on` makes two columns hold one value in the common case; and shipping section 3 as
drafted accepts the regression above.

**Consequence for R8.** `recurrence_month_anchors` and `recurrence_weekday_anchors` are mutually
exclusive (a rule fires on a day-of-month OR an nth-weekday, never both) and DDL cannot express "at
most one of two subtype rows". R8 enforces it in the authoring seam and pins it with a test;
recorded here so R8 does not discover it.

### R-R4 -- a `Once` rule is retired at R2e, before anything must model it

Ruled 2026-08-05 and AMENDED 2026-08-07. The original ruling deferred the deletion to R9 and gave
`Once` inert two-axis values in the meantime, because NOT NULL was coming and the transfer form has
no null option. R2d removed the NOT NULL pressure, and two neutral reviews then showed the deferral
costs more than it saves: `Once` resolves to EXACTLY the `Every Period` value, so a consumer holding
only a `ResolvedRecurrence` would generate a `Once` rule forever, and `(1, period, containing_date)`
naming two patterns is what would force R7c's downgrade to refuse rather than round-trip.

**Amended ruling: retire it at R2e, before R3.** The new engine then never models "does not recur",
and the resolved value is unambiguous before anything consumes it. Until then `pattern_id = Once`
REMAINS what suppresses generation, exactly as today -- the four guards run before the engine is
consulted, so the ambiguity is contained to a value nothing reads yet.

### R-R6 -- the bounds move from PERIODS to OCCURRENCES, and 4 frozen shapes move

Ruled 2026-08-05, measured while building R2b. `match_periods` bounds PERIODS: `start_date` is
tested against a period's END (`:488`) and `end_date` against its START (`:492`). So a row is
generated whose OWN occurrence date lies outside the window the user stated. The two-axis model
bounds occurrences instead -- `anchor_date` IS the first occurrence -- which drops exactly those
rows. Measured against the R1 baseline's own schedule and shapes:

```text
start.midperiod          old= 31 new= 31 SAME
start.on_period_start    old= 31 new= 31 SAME
start.on_period_end      old= 31 new= 30 MOVES  drops idx=011 occurrence=2024-06-15
end.midperiod            old= 18 new= 17 MOVES  drops idx=037 occurrence=2025-06-15
end.on_period_start      old= 18 new= 17 MOVES  drops idx=037 occurrence=2025-06-15
end.on_period_end        old= 18 new= 18 SAME
window.both              old= 13 new= 12 MOVES  drops idx=037 occurrence=2025-06-15
window.inverted          old=  0 new=  0 SAME
```

Every dropped row is a bill dated outside its own rule's window: a monthly-15th rule ending
2025-06-05 generating a row due 2025-06-15, and a monthly-15th rule starting 2024-06-16 generating
one due 2024-06-15. **Ruling: occurrence-bounded ships**; the current behaviour is defect D5. Zero
live rules are affected -- the only live `end_date` rules are `Every Period` (whose occurrence IS
the period start, so both readings agree) and Monthly rules whose bounds fall outside the horizon.

**This amends R1's binding statement**, which said R4 re-freezes the `long_cadence.*` lines "and no
other line may move". Four `bounds.*` blocks move too, one row each, listed above so R4 diffs
against a prediction rather than a surprise.

**Same ruling, second question: `Monthly First`.** Its anchor is the 1st of the first month whose
OWN first paycheck falls on or after the effective start -- not the 1st of the effective month,
which would place the first row in a paycheck EARLIER than the one the user chose, because the
placement rule is "the first period starting on or after the occurrence". On the developer's
schedule a rule starting at the 2026-07-30 paycheck skips July (whose first paycheck, 07-02,
precedes the chosen start) and anchors 2026-08-01. Today's engine puts that row on 07-30, the LAST
July paycheck, which the rule's own name says it should not. Zero live rules affected: the one
`Monthly First` rule starts at period index 0.

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

### R-R11 -- `Once` cannot be retired in one leaf, and the `ref` row outlives the enum

Ruled 2026-08-07. Building R2e surfaced two conditions that had to hold before the retirement was
even safe, each a measured defect rather than a refactor, so the step became three (see section 4).

**The deploy half is the ruling that changed the design.** Deleting the `ref.recurrence_patterns`
row was proposed first, because the picker is built from that table and a surviving row would still
offer `Once`. Measured: `ref_cache.init` raises `RuntimeError` for an enum member with no row
(`ref_cache.py:290`), the entrypoint runs migrations before the seed, and `shekel-deploy` rolls back
to the PREVIOUS image on an unhealthy deploy -- so the row's deletion would leave both images unable
to boot, on exactly the deploy where the safety net is needed. The developer refused that trade, and
the refusal produced the better design: **drive the picker off the enum instead** (R2e-2). The app's
model of the vocabulary is `RecurrencePatternEnum` -- `_pattern_member` raises for anything else --
so a table-driven picker was always able to author an unresolvable rule. With the picker enum-driven
the row is unreachable rather than merely unused, and expand/contract costs nothing: R9 already
drops the table.

**What R2e-1 had to fix first.** Two defects the retirement would otherwise have shipped onto a
second surface: **D14** (the null option was a silent no-op that then regenerated) and, through it,
**D15** -- a loan payment could be made "one-time", which nulls the column
`recurring_transfer_query` finds it by and silently drops the standing overpayment the balance seam
threads (measured 250.00 -> 0.00 with the `loan_payment_settings` row still asserting 250.00).

### R-R2 -- a signed day offset was proposed and disproved

A `due_offset_days SMALLINT` was the first proposal. The Van case disproves it:

```text
due_offset_days = +10
  Jul 22 -> Aug 1   correct
  Sep 22 -> Oct 2   WRONG (Sept has 30 days; should be Oct 1)
  Feb 22 -> Mar 4   WRONG (Feb has 28 days; should be Mar 1)
```

"Due on the 1st of the following month" is a (day-of-month, month-offset) pair, not a day count.
`due_day_of_month` already encodes it -- but infers the `+1` from `due_dom < dom`
(`recurrence_engine.py:691`) instead of storing it, which is why it reads as a surprise and why
"cash the 5th, due the 25th of next month" is inexpressible.

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
  -- The four below are COMPUTED, not stored, until R7c (ruling R-R10):
  -- app.services.recurrence.resolve emits them from the closed-set columns
  -- plus the owner's schedule.  They become columns at R7c, authored by the
  -- form, in the transaction that drops the closed set they came from.
  unit_id          FK ref.recurrence_units RESTRICT   NOT NULL             [R7c]
  anchor_date      DATE  NOT NULL      -- the first occurrence: phase AND day AND month  [R7c]
  placement_id     FK ref.period_placements RESTRICT  NOT NULL             [R7c]
  shift_id         FK ref.business_day_shifts RESTRICT NOT NULL            [R7c]
  end_date         DATE  NULL   CHECK (end_date IS NULL OR end_date >= anchor_date)  [CHECK at R7c]
  max_occurrences  INT   NULL   CHECK (max_occurrences IS NULL OR max_occurrences > 0)  [R2b, DONE]
  created_at
  CHECK (end_date IS NULL OR max_occurrences IS NULL)   -- at most one end bound

-- Every subtype below carries a surrogate ``id`` PK plus
-- ``UNIQUE (recurrence_rule_id)``, NOT ``recurrence_rule_id`` as the PK.  All
-- three are audited, and ``system.audit_trigger_func`` assigns
-- ``v_row_id := NEW.id`` -- on a table without that column every INSERT dies
-- with ``record "new" has no field "id"`` (measured on a probe table, R2b).
-- UNIQUE over a NOT NULL column enforces the identical 0-or-1 cardinality.
-- Day/week columns are INTEGER, matching ``day_of_month`` on the parent and
-- every other table in the project; the CHECKs bound the domain.

budget.recurrence_due_dates    [HALF B -- created in R5/R6, NOT in R2]
                               -- 0..1 per rule; present iff installment <> cash
  id                  PK
  recurrence_rule_id  FK -> budget.recurrence_rules ON DELETE CASCADE, UNIQUE
  due_day             INT NOT NULL CHECK (due_day BETWEEN 1 AND 31)
  due_month_offset    INT NOT NULL DEFAULT 0
                      CHECK (due_month_offset BETWEEN -12 AND 12)

budget.recurrence_weekday_anchors    [R2b created it, EMPTY; R8 is the first writer]
                                     -- 0..1 per rule; nth-weekday-of-month rules
  id                  PK
  recurrence_rule_id  FK -> budget.recurrence_rules ON DELETE CASCADE, UNIQUE
  nth_week            INT NOT NULL
                      CHECK (nth_week BETWEEN -1 AND 5 AND nth_week <> 0)  -- -1 = last
  weekday             INT NOT NULL CHECK (weekday BETWEEN 0 AND 6)  -- date.weekday(), 0=Mon

budget.recurrence_month_anchors      [R2b created it, EMPTY; R7c is the first
                                     --  writer -- there is no stored anchor
                                     --  to clamp before then (R-R10)]
                                     -- 0..1 per rule; present iff the anchor
                                     --   month clamped the nominal day (R-R3)
  id                  PK
  recurrence_rule_id  FK -> budget.recurrence_rules ON DELETE CASCADE, UNIQUE
  nominal_day         INT NOT NULL CHECK (nominal_day BETWEEN 29 AND 31)
```

**`end_date >= anchor_date` lands at R7c, with the column it names.** `end_date` is user-authored
and live; 14 live rules RESOLVE to an anchor in the future, so setting an earlier end date --
exactly what the field invites -- would become a `CheckViolation` out of `update_template`'s
autoflush, which nothing catches: the user could not stop an annual bill and the projection would
keep charging it. R7c adds it together with the Marshmallow validator that refuses the pair at the
door.

**Zero conditionally-meaningless columns.** Every column on `recurrence_rules` is meaningful for
every unit; the two facts that apply to only some rules are 0-or-1 subtype rows, where presence is
the discriminator and the PK+FK enforces the cardinality.

`placement` is well-defined for all four units rather than inert for some.
**The claim that it is also INERT under the PERIOD unit no longer holds** and was deleted here: it
rested on the occurrence date being a period start, which ruling R-R8 reversed -- a mid-period bound
places differently under the two placements. Every rule the app writes today is `CONTAINING_DATE`,
so nothing depends on the retired claim.

**Only the `budget` tables are audited.** An earlier draft of this section said "all three new
tables go into `AUDITED_TABLES`"; measured against that list's own inclusion criteria
(`app/audit_infrastructure.py:46-64`) that is wrong for the `ref` tables -- the `ref` schema is
excluded with exactly one exception, the multi-tenant `ref.account_types`, and adding read-only seed
catalogues would both drown the trail in seed noise and move `EXPECTED_TRIGGER_COUNT`, which the
container entrypoint asserts at start. So: `ref.recurrence_units` / `ref.period_placements` /
`ref.business_day_shifts` are NOT audited (pinned by `TestNotAudited` in
`tests/test_models/test_recurrence_ref_tables_migration.py`), while
`budget.recurrence_weekday_anchors` and `budget.recurrence_month_anchors` go into `AUDITED_TABLES`
in R2b, and `budget.recurrence_due_dates` in Half B.

### Where the old columns went

| old | new |
|---|---|
| `day_of_month` | `anchor_date.day` |
| `month_of_year` | `anchor_date.month` |
| `offset_periods` | `anchor_date` (a date survives a schedule rebuild; an index does not) -- kills D1 |
| `start_period_id` (weak, bypassable) | deleted; `anchor_date` is the start and is applied unconditionally -- kills D2 |
| `start_date` (strong, loan-sync only) | merged into `anchor_date` |
| `due_day_of_month` + implicit next-month rule | `recurrence_due_dates` with an EXPLICIT `due_month_offset` |
| `Once` pattern | deleted; `recurrence_rule_id IS NULL` for both template kinds |

### Pattern mapping (migration derivation)

| today | (interval, unit) | placement | anchor_date |
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

```text
occurrences(rule, window) -> Iterator[date]        # forward, by unit
place(occurrence_date, periods) -> PayPeriod       # binary search, total
```

Forward generation plus placement is FORWARD and explicit -- a date past the horizon is an explicit
"beyond horizon" rather than a silent drop. This kills D3 structurally, at any cadence, and retires
all five `_match_*` helpers.

**It is not TOTAL, and the claim that it is was measured false.** This paragraph read "periods are
contiguous by construction (`pay_period_service.py:190`), so every date has exactly one period".
Contiguity holds WITHIN a generated batch and not across batches: `_reject_overlapping_batch` only
requires a new batch to start after the latest existing `end_date`, so `latest_end + 5 days` is
accepted and leaves a gap -- and registration bootstraps a 14-day period 0 that any later real
schedule starts after. R3 owns the gap case (ledger row D7); `place()` must answer "no period"
rather than assume one.

## 4. Step sequence

Each step is a leaf boundary: one commit, its own tests green, independently revertible.
**Steps R1-R4 do not change any user-visible behaviour.** Half A = R1-R4, R7a-R7c, R8 (+ the Half-A
part of R9); Half B = R5, R6 (see section 0).

**R1, R2a, R2b, R2c-1 and R2d are ARCHIVED** to
`docs/plans/historical/recurrence_as_built_2026-08-05.md` (rule 5) -- one line per step with its
commit, plus the constraints they bind on later steps. Read that file before R7c or R4.

### R2 -- New schema, additive (R2a, R2b, R2c-1, R2d). COMPLETE

Old columns retained and still authoritative. **R2c-2 and R2c-3 were DELETED, not deferred**: they
existed to fence and then tighten a stored derivation, and R-R10 removed the derivation instead.
Their work now lands at R7c, where the same values arrive AUTHORED.

- [x] **R2d -- the derived half stops being stored.** `1e5e3430` -- the four two-axis columns
      removed and computed on demand by `resolve()`, with `c8f2b6a41d93` amended in place. Ruling
      **R-R10**, archived with it; read the commit before R7c.

### R2e -- retire `Once`. THREE leaves (ruling R-R11)

Why it runs here rather than at R9 is ruling **R-R4**, stated once there. Transaction templates
already model "does not recur" correctly as `recurrence_rule_id IS NULL`; transfers were forced onto
`Once` because their form has no null option (`transfers/form.html:87` passes
`include_none_option=false`, `templates/form.html:101` passes `true`). Drains part of R9.

- [x] **R2e-1 -- clearing a recurrence clears it.** `4d99c9d4` -- the null option both edit forms
      already offered was a silent no-op that then REGENERATED from the rule it was asked to stop
      (**D14**), and through it a loan payment could be made one-time and silently lose the standing
      overpayment the balance seam threads (**D15**). The clear branch now deletes the rule rather
      than orphaning it; a loan payment is refused at the door.

- [x] **R2e-2 -- the pattern picker offers what the app MODELS.** `a465e9fa` -- picker, both write
      doors and the preview read `RecurrencePatternEnum` through one producer, so the `ref` row
      R2e-3 leaves behind is unreachable, not merely unused. Binds R2e-3: it needs no further guard,
      and `pattern_choices_for` already keeps an unmodelled STORED pattern selected -- without which
      an edit form silently defaults to the destructive clear (measured, review-found).

- [ ] **R2e-3 -- retire `Once`.**

The enum member, the four suppression guards (`recurrence_engine.py:115,257`,
`recurring_view.py:236`, `savings_goal_service.py:427`), the preview guard (`templates.py:931`),
`_PATTERN_DERIVATIONS`'s entry, `calendar_service._INFREQUENT_PATTERNS`, `REC_ONCE` and its label,
the macro branch, `data-once` and the JS, the transfer form's null option, and the create route's
`rule is None` branch (defect **D13**). One migration nulls the 2 transfer-template FKs and deletes
the 4 `Once` rules -- which takes ids 41 and 43 out of R-F6's orphan set, leaving it 4, 44 and 47.

**EXPAND/CONTRACT: the `ref.recurrence_patterns` row and its `ref_seeds` entry STAY**, and R9 drops
them with the table. `ref_cache.init` raises when an enum member has no row (`ref_cache.py:290`), so
deleting the row in the release that deletes the member breaks the PREVIOUS image -- and
`shekel-deploy`'s auto-rollback runs that image, turning a failed deploy into "rollback container
also unhealthy; manual intervention required". Ruled 2026-08-07 (developer). R2e-2 is what makes the
surviving row harmless: nothing references it and no picker offers it.

- [ ] **R3 -- New engine, parallel and unread.**

`app/services/recurrence/` with `occurrences()` and `place()`. Pure, no Flask. Nothing reads it yet.

Ships with a parallel-run test that drives the NEW engine through
`tests/oracles/recurrence_baseline.py`'s own shape set and schedules and asserts it reproduces the
committed blob -- all **423 shapes**, not the 50 live ones. Expect exactly one class of legitimate
divergence: the `long_cadence.*` shapes, which R1 froze WRONG on purpose (D3's dropped months and
duplicate period). Those lines are the proof R3 works and must be re-frozen with
`SHEKEL_UPDATE_RECURRENCE_BASELINE=1` **in R4's commit, not R3's** -- R3 changes no reader, so
nothing it does may move the baseline yet.

- [ ] **R4 -- Cut generation over.**

`match_periods` becomes a thin adapter, then callers move to the new engine. D3 dies here. The R1
oracle is the gate.

- [ ] **R5 -- Row columns.**

`transactions.due_date` / `transfers.due_date` -> `occurs_on` (pure rename, zero value changes --
the column already holds the cash date), plus a new nullable `due_on`. Then correct all 28 Python
files and 5 templates to the one they actually mean. Highest-risk readers: `transfer_service.py`
(9), `balance_at/_plan.py` (8), `rate_period_engine.py` (6), `loan_payment_service.py` (6).

- [ ] **R6 -- Delete `payment_day`; one accessor.**

`loan_installment_date(...)` becomes the single derivation, reading the rule +
`recurrence_due_dates`. 22 files, including the balance seam (`balance_at/_plan.py`,
`_loan_interest.py`, `_resolution.py`) and the loan ledger. Kills D4.
**This step needs its own review pass** -- it is the deepest cut into the ledger.

**R7 is THREE leaves**, ruled 2026-08-07: the cutover is the only irreversible-ish one, so the label
and form work is not carried into it.

- [ ] **R7a -- the derived-value surfaces become functions over `(interval, unit)`.**

Two surfaces, one shape of change, both fed by `resolve()` and neither touching the schema. The
LABEL: one function replaces the 8-branch `recurrence_cell` macro (`_recurrence_macros.html:17`),
the 8-entry `recurrence_pattern_labels` dict (`app/__init__.py:321`) and the 8 `REC_*` Jinja globals
(`jinja_globals.py:91`) -- killing the last `.name`-for-display coupling on this table. The MONTHLY
EQUIVALENT: `savings_goal_service.amount_to_monthly` becomes four lines over the same pair, which is
the same edit that replaces the `PAY_PERIODS_PER_YEAR` constant with a resolved per-user cadence
(section 4a) and derives `calendar_service._INFREQUENT_PATTERNS` instead of enumerating it.

- [ ] **R7b -- bounds and the form.**

The form becomes interval + unit + anchor + optional due row, and `max_occurrences` gains its first
writer. Deletes the vestigial `offset_periods` schema field (D8) and `start_period_id`'s "First
paycheck" affordance, so **D1 and D2 die here**. Still authors through the closed-set columns: it
collects the two-axis vocabulary and the seam maps it, so the schema does not move yet.

- [ ] **R7c -- the cutover.**

ONE migration: add `unit_id` / `anchor_date` / `placement_id` / `shift_id`, backfill, tighten to NOT
NULL by the documented three-step (`.claude/rules/database.md`), add
`CHECK (end_date IS NULL OR end_date >= anchor_date)` with its Marshmallow mirror, then DROP
`pattern_id` / `day_of_month` / `month_of_year` / `start_period_id` / `offset_periods` in the same
transaction. `due_day_of_month` and `start_date` survive -- Half B and R9 own those.

**Its downgrade round-trips and must REFUSE rather than guess.** With `Once` retired at R2e,
`(interval, unit, placement)` names exactly one closed-set pattern for every shape the app can
author today; a row carrying a cadence the closed set cannot name (every-other-month, a WEEK unit, a
weekday anchor, `max_occurrences`) is unrepresentable, so `downgrade` re-derives what it can and
raises `RuntimeError` naming the offending rule ids otherwise.

**Budget the derivation copy.** Measured against the 50 live rules: `unit_id` / `placement_id` /
`shift_id` are a per-pattern CASE, 49 of 50 anchors need only the `_effective_start` maximum
(Postgres `GREATEST` skips NULLs, so it is that maximum exactly), and one `Monthly First` rule needs
a lateral scan. Prove it before it ships the way this arc has twice: drive the migration's own
function against `resolve()` over all 423 oracle shapes, not the 50 live rows.
**This step needs its own review pass.**

- [ ] **R8 -- Add-ons.**

WEEK unit, `recurrence_weekday_anchors`, business-day shift, count-bounded end. Note: the shift
applies to the CASH date only -- a bill due Aug 1 paid Friday because Aug 1 is a Sunday still
satisfies the Aug 1 installment, so `recurrence_due_dates` is never shifted.

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

- [ ] **R-F1 -- Re-sync the five lagging `ref` identity sequences** (finding F-1).

**Do this one first**: it is the only carried finding with a live failure mode, and the failure
lands during a DEPLOY. Measured 2026-08-05 against `shekel-prod-db` (and identically on the dev
clone): `goal_modes` (max id 2, next value 1), `income_units` (2/1), `user_roles` (2/1),
`compounding_frequencies` (3/1), `employer_contribution_types` (3/1). Their migrations seeded
literal ids (`INSERT INTO ref.goal_modes (id, name) VALUES (1, 'Fixed'), ...`, e.g. `1dc0e7a1b9e4`),
which does not advance the sequence. Latent today because `ref_seeds.seed_reference_data` only
INSERTs a MISSING row and none is missing; it bites the first time anyone adds a value to one of
those five enums, when the id-less INSERT asks for id 1, collides on the primary key, and
`scripts/seed_ref_tables.py` aborts mid-deploy.

One migration, one statement per table, over `goal_modes`, `income_units`, `user_roles`,
`compounding_frequencies`, `employer_contribution_types`:

```sql
SELECT setval(pg_get_serial_sequence('ref.goal_modes', 'id'),
              GREATEST((SELECT max(id) FROM ref.goal_modes), 1));
```

`GREATEST` so the statement can never move a sequence BACKWARDS -- it must be safe on a database
where the sequence is already correct (every environment is at a different point). `downgrade`
raises `NotImplementedError`: reverting means re-introducing the defect, and the rules require the
refusal to carry both the reason and the literal SQL to do it by hand anyway.

The test is the generalisation, not a copy: widen `TestIdentitySequenceInStep`
(`tests/test_models/test_recurrence_ref_tables_migration.py`) from the three R2a tables to **every**
table in the `ref` schema, discovered by query. That covers the five, and covers ref tables not yet
written. It belongs in its own file once it stops being about recurrence.

- [ ] **R-F6 -- Close the recurrence-rule leak, then delete what leaked** (finding F-6).

`templates.hard_delete_template` (`:904`) deletes a `TransactionTemplate` and leaves its
`RecurrenceRule` unreferenced forever; the transfer-template path is the same shape. Five rows have
accumulated on production (ids 4, 41, 43, 44, 47). **Starts with a ruling, not a keystroke**: either
the deletion path deletes the rule with its template, or ownership inverts so the rule carries the
template id and CASCADEs. The FK change R9 used to propose cannot fix it -- see R-R7. Deleting the 5
existing rows rides in the SAME commit, so the cleanup and its cause are reviewed together; that
makes the migration destructive, so it carries the `Review:` line and a downgrade that refuses with
the literal SQL.

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

- [ ] **R-F7 -- Delete two unreachable branches in `_first_of_month_anchor`** (finding D11).

Left by R2c-1 and found by a neutral review of R2d. Both guards in
`app/services/recurrence/_resolution.py` are provably dead, and one carries a comment describing a
case that cannot execute -- which is worse than the dead code, because it tells the next reader the
function handles something it does not.

*The in-loop `earliest is not None`.* `earliest_start_in_month(y, m)` is called with the year and
month OF A PERIOD ALREADY IN `calendar.periods`, so that period is itself in the minimand and the
result is never `None`.

*The fallback's `if earliest is not None and earliest >= effective`.* The fallback runs only when
the loop returned nothing. If that month's earliest payday were `>= effective`, the period whose
start IS that payday would have passed the loop's own `start_date < effective` guard, and its
month's earliest is the same value -- so the loop would have returned. The branch is therefore
unreachable and the function always falls through to `_next_month_first(effective)`.

Proven both ways before it was written down: the argument above, plus a brute-force sweep of 243,018
`(schedule shape, effective date)` pairs across cadences 1-365, gapped and degenerate schedules, and
bounds inside and past the horizon -- 32,006 of which reached the fallback.
**Neither guard was ever taken.** Deleting them is provably behaviour-identical, so the R1 baseline
must stay byte-identical and `tests/test_services/test_recurrence_resolution.py::TestTotality` must
stay green unchanged.

## 4a. `PAY_PERIODS_PER_YEAR` (folded into R7a)

`PAY_PERIODS_PER_YEAR = Decimal("26")` (`app/utils/money.py:43`) is a magic number while
`cadence_days` is user-selectable 1..365, so every monthly-equivalent figure on `/obligations`,
`/savings`, and the Recurring surface is wrong for any non-biweekly schedule. It is NOT a separate
task: R7a already rewrites `savings_goal_service.amount_to_monthly` from an 8-branch pattern switch
to four lines over `(interval, unit)`, and changing its input from a module constant to a resolved
per-user cadence is the same edit. Nine files reference the constant; overlap with the balance arc
is one file (`savings_dashboard_service/_metrics.py`, vs the X-x held branch).

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

Every defect this arc has measured, one line each, and the step that closes it. **The last column is
the rule this document is gated on (section 7 rule 1): it names a LIVE step, and a step that ships
re-points every row that named it.** The measurement lives where the work is -- D1-D4 in section 2,
F-1 to F-3 in their R-F step entries -- so a row is a pointer, never a second copy of a fact.

D2-D7 are defects in the code the redesign replaces; D8-D12 are findings about the arc's OWN work;
F-1 to F-3 and F-6 were found while building it and are NOT part of it, which is why their steps sit
in the carried block at the end of section 4. F-4 and F-5 are pay-period features section 4a
surfaced -- they have no step because they need a ruling first, which is what `operator` means here.
**D1 left this table at R2c-1, and D14/D15 at R2e-1**; each measurement lives with the step that
closed it -- the historical archive for D1, commit `4d99c9d4` for D14/D15, which R-R11 still names.

**The ledger stands at 21 rows.**

| id | finding (one line) | worst measured | status | owned by |
|---|---|---|---|---|
| D2 | an edit ignores the chosen "First paycheck": `effective_from` overrides `start_period_id` | 4 rows materialised in excluded periods | OPEN | R7b (the form rewrite that retires `start_period_id`) |
| D3 | `_match_monthly` reads only a period's two ENDPOINT months, so it is neither total nor injective | 6 of 12 occurrences dropped; a duplicate period would 500 | OPEN | R4 |
| D4 | a loan's cash date and contractual installment date cannot differ | $0 -- labels only (payoff date, schedule rows, history) | OPEN | R6 |
| D5 | `match_periods` bounds PERIODS, not occurrences, so a row is generated dated OUTSIDE its own rule's window | a bill dated 10 days past the end date the user set | OPEN | R4 |
| D6 | folding `start_date` into `anchor_date` is lossy for PERIOD-unit rules | the loan origination bound stops being an exact date | OPEN, NARROWED TWICE -- R-R8 made an anchor a date, and R2d made it COMPUTED, so the "a rebuild strands the anchor" half is dead: there is no stored anchor to strand, and `_repoint_recurrence_rules` narrowed back to the rules the wipe actually nulled | R9 (re-check before dropping `start_date`) |
| D7 | pay periods are NOT contiguous by construction, so `place()` is not total -- section 3's totality argument rests on a false premise | a date in a schedule GAP belongs to no period; reachable because `_reject_overlapping_batch` rejects overlaps, not gaps, and registration bootstraps a period the user's real schedule then starts after | OPEN | R3 |
| F-1 | five `ref` identity sequences sit behind their data, on production | the next value added to those enums aborts a DEPLOY | OPEN | R-F1 |
| F-2 | the ref-seed parity scan's last `INSERT` body runs to end-of-file | none today; a literal quoted below the seed would pass | OPEN | R-F2 |
| F-3 | `ref` tables use auto-named constraints while the database rule says name them | none -- the names are never referenced | OPEN | R-F3 (developer ruling first) |
| F-6 | a hard-deleted template leaves its recurrence rule behind forever | 5 orphaned rules on production today | OPEN | R-F6 |
| D8 | `offset_periods` is a declared schema field (`templates.py:67`, `transfers.py:65`) that NO template renders, so only a crafted POST can author a phase | none -- it is the one derived value still stored, re-derived on EVERY write since R2c-1, and the anchor carries whatever phase it states; vestigial, not wrong | OPEN | R7b (the form rewrite that deletes it) |
| D9 | the plan's stated `occurrences(rule, window)` signature cannot serve the PERIOD unit: those occurrences are pay periods, so they are not derivable from the rule alone | R3 would discover mid-build that the seam needs the schedule threaded through it | OPEN | R3 |
| D10 | a `Monthly First` anchor is horizon-dependent: re-authoring an unchanged rule after the schedule extends can move it a month earlier | inherent to a pattern defined in terms of paydays, and equally true of the scan R2b shipped -- but no surface says so | OPEN | R4 (freeze the semantics with the baseline) |
| D11 | two branches of `_first_of_month_anchor` are unreachable, and one comments a case that cannot execute | none -- both are dead; proven by argument and by a 243,018-case sweep in which neither was ever taken | OPEN | R-F7 |
| D12 | `ref_cache.recurrence_unit_id` / `period_placement_id` / `business_day_shift_id` have ZERO callers in `app/` since R2d made `resolve()` return enum members | none -- they are correct and tested; the risk is a dead-code sweep DELETING them before their first consumer exists | OPEN | R7c (its backfill maps enums back to ids) |
| D13 | `create_transfer_template` dereferences `rule.id` with no null branch, and its own comment claims the schema prevents it -- `recurrence_pattern` is NOT `required` on `TransferTemplateCreateSchema` | 500 (`AttributeError`) on any POST omitting the pattern; becomes the DEFAULT path once the form offers the null option | OPEN | R2e-3 |
| F-7 | `ref_seeds`' pattern list restates `[m.value for m in RecurrencePatternEnum]` verbatim, so the set the app models is stated twice | none -- probed byte-identical, and `ref_cache.init` already refuses a mismatch | OPEN | operator (derive every `ref` seed from its enum, or keep the literals? it is the shape of all ~23 seed lists, not just this one) |
| D17 | `RecurrenceRule.pattern` is `lazy="joined"`, so every rule load eager-joins `ref.recurrence_patterns` for a relationship whose only reader is the `recurrence_cell` macro's else-branch | none -- one join per rule load | OPEN | R7a (deletes that branch, the labels dict and `pattern_labels_by_name` together, so the join goes with them) |
| D16 | renaming a `Once` transfer template DESTROYS its Transfer: regeneration sweeps the row, then the `Once` guard generates nothing back | the transfer and both shadows deleted on a rename; the 2 live `Once` transfer templates are exposed the moment their row is projected rather than Paid | OPEN | R2e-3 (a rule-less template is skipped by the sweep gate) |
| F-4 | `pay_periods` stores NOMINAL paydays; a holiday/weekend shift for the pay SCHEDULE is unmodelled | Josh's 1 Jan 2026 payday was really paid 31 Dec 2025 | OPEN | operator (scope it as its own task, or rule it out?) |
| F-5 | a 27-paycheck year is a real budgeting event and no surface names one | one extra $500 Groceries + one extra $2,473.38 paycheck | OPEN | operator (build the surfacing, or leave it?) |

## 6. Alternatives considered and rejected

**Add `EVERY_N_MONTHS` + `EVERY_N_YEARS` to the enum.** ~1 day, touches the ~12 pattern-aware
surfaces, fills the two named gaps and nothing else. Leaves D1-D4, the sparse table, and every
N-branch switch. The next gap repeats the work.

**RFC 5545 RRULE strings + `python-dateutil`.** Maximum expressiveness, no custom matcher. Rejected
as the storage model: a new dependency; an opaque string cannot be CHECK-constrained or queried
("what is due in March"); RFC 5545 sec 3.3.10 specifies invalid dates are IGNORED, not clamped, so a
monthly "31st" bill would silently lose 5 occurrences a year where Shekel clamps
(`recurrence_engine.py:546`) -- a behaviour change on live data; and RRULE has no notion of pay
periods, so the placement layer stays hand-written either way. The vocabulary (FREQ / INTERVAL) is
worth borrowing; the storage is not.

**Materialize occurrence dates into a table.** Rejected: `transactions` / `transfers` already ARE
the materialization. A second one is a second source of truth for the same fact -- the defect class
the balance-architecture arc exists to eliminate.

## 7. Document rules (GATED)

`tools/plan_gate/test_recurrence_plan_ledger_integrity.py` grades this file through a pre-commit
hook scoped to this document and the same CI step that runs the custom pylint checkers -- so EDITING
THIS FILE is what runs the gate. The machinery is shared with
`docs/audits/balance_architecture/README.md`, which adopted these rules first after three
hand-passes in two days each found the same class of rot.

**Rules 1-4, 6's cap and 7 are PREDICATES. Rule 5 and 6's "replaced, never appended" half are
disciplines** -- nothing distinguishes an archive from a trim, or a rewrite from an append. Saying
so is the point: this arc's own standard is that a safety which is not a predicate is not a safety,
and labelling a discipline as one is the failure being guarded against.

1. **Every section 5 row names a LIVE owner.** The last column is a ` / `-separated list, each entry
   an unticked section 4 step id (optionally annotated in parentheses), or `operator` with the
   question stated, or `developer-decision` with the date the fork was taken. There is deliberately
   no value meaning "someone will get to it". A row with an empty owner, an owner naming no
   checkbox, or an owner naming a TICKED step is a failure.
2. **A step that ships re-points every row that named it.** Ticking a box is the same edit as
   re-pointing its findings; the gate refuses the commit that does one without the other.
3. **Section 5 states its own size, and the number is checked**
   (`**The ledger stands at N rows.**`). The sentence is optional to the parser and mandatory here:
   the balance ledger's read 38 against a 40-row table because a step that closed four rows and
   opened three updated the rows and not the prose about them.
4. **The whole file is capped at 900 lines**, and the cap is a FORCING FUNCTION, not a ceiling sized
   to fit the work. The arithmetic says so plainly: the document stands at 747, eight steps remain
   unspecified (R2e, R3-R9), and specifying each at the ~45 lines R2b's took would add ~293 --
   landing at ~1,021, well over. **That gap is deliberate and rule 7 is what closes it.** A step
   that ships surrenders its specification: R1 and R2a occupied 45 lines between them before rule 7
   condensed them to 11. At roughly 35 lines returned per ship against ~45 spent per specification,
   the file breathes rather than grows, and the cap binds only when the archive move is genuinely
   overdue. **Raising it is not the answer when it binds.**
5. **The only legal way back under the cap is to archive a COMPLETED span** to
   `docs/plans/historical/recurrence_as_built_<date>.md`, condensed to one line per step: its id,
   its commit and what it closed. Never trim a live step's specification to fit. Shrink the record
   of what is done, never the specification of what remains. (Discipline, not a predicate -- see
   above.)
6. **"Where this stands" is capped at 20 lines and is REPLACED, never appended to.** It is the
   signpost the next session reads first. On the balance README the same section became an
   append-only log of 1,019 lines, so a reader had to scroll a month of history to find the branch
   they were on. When it overflows, the remedy is relocation, not deletion: a constraint on a step
   belongs in that step, a defect belongs in a section 5 row with an owner, a standing rule belongs
   here.
7. **A SHIPPED step's entry is a POINTER: it OPENS with its commit hash, and is at most 6 lines.**
   Write it as `- [x] **<step> -- what it did.**` followed by the sha in backticks and one or two
   sentences. The hash's POSITION is the predicate, not its presence: an Alembic revision id is hex
   too, and this document cites one beside the commit that added it. Prose nobody re-verifies is
   worse than a hash anyone can check -- the balance arc carried an invented provenance line, a
   drifted count and a citation to a deleted producer into records of shipped work. A LIVE step is a
   specification and is never trimmed; only the record of what is DONE shrinks.
   **This rule was enforced by the gate the day it was installed and was missing from this list**,
   while rules 3 and 4 both cited it by number -- found 2026-08-07, at the first tick after the gate
   shipped.
