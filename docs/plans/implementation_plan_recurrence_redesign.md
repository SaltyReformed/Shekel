# Implementation Plan: Recurrence Rule Redesign

**Status:** design LOCKED 2026-08-05. **R1 DONE. R2a DONE**; R2b next. **Plan of record** for
replacing the closed 8-name recurrence pattern set with a two-axis model, and for untangling the
cash-date / installment-date collision the design review surfaced.

Rulings taken 2026-08-05 (developer):

| fork | ruling |
|---|---|
| Scope | Full two-axis redesign (not additive patterns, not staged dual-write) |
| Add-ons | ALL FOUR: weekly-by-date, nth-weekday, count-bounded end, business-day shift |
| Due date model | Subtype table with `due_day` + explicit `due_month_offset` (a signed day offset was proposed and DISPROVED, see R-R2) |
| `loan_params.payment_day` | DELETE; every reader goes through one accessor |
| Row columns | `due_date` -> `occurs_on` (rename, no value change) + new `due_on` |
| The three defects | Folded into the redesign, not fixed as separate PRs |
| Sequencing vs the balance arc | **Half A now; Half B folded into X-an.** See section 0 |
| `PAY_PERIODS_PER_YEAR` | Folded into R7 (which already rewrites `amount_to_monthly`); derivation = `round(365.2425 / cadence_days)`, see section 4a |
| **Anchor day vs month-end clamp** | **`anchor_date` + a 0..1 `recurrence_month_anchors` subtype. See R-R3** |
| **`Once` rules under NOT NULL** | **Backfilled with inert values; `pattern_id = Once` stays the gate until R9. See R-R4** |
| **R2 sequencing** | **R2a (vocabulary, DONE) -> R2b (columns nullable + backfill) -> R2c (writers + NOT NULL). See R-R5** |

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

**Half A -- R1, R2, R3, R4, R7, R8. Runs NOW**, in the block-6/7 "interleaves anywhere" slot the
balance README already defines. It does not touch a file the anchor half is editing. Delivers
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
  `savings_goal_service.py:427`, plus `templates.py:962`. Transaction templates already model this
  correctly (`recurrence_rule_id IS NULL`); transfers were forced onto `Once` because their form has
  no null option (`_recurrence_fields.html:44`).
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

### D1 -- an amount-only edit re-phases "every N paychecks"

No `offset_periods` input exists in any template under `app/templates/`, but
`update_recurrence_rule_from_form` writes `rule.offset_periods = data.pop("offset_periods", 0)`
(`_recurrence_form_helpers.py:329`). Probe (route-level, real form payload):

```text
created with offset=1 (start period_index=7, interval=2)
after an amount-only edit:
E   AssertionError: assert 0 == 1
```

Every future occurrence shifts by one pay period. Latent in data only because no live rule uses the
pattern.

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
carry a `start_period_id`.

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

### R-R4 -- a `Once` rule gets inert two-axis values, not a deletion

Ruled 2026-08-05. `Once` means "does not recur", so no honest cadence exists for it -- but R2c makes
`unit_id` / `anchor_date` / `placement_id` / `shift_id` NOT NULL, and 2 of the 4 live `Once` rules
are attached to transfer templates (the other 2 are orphans R2b deletes anyway).

Deleting them now was rejected: transaction templates already model "no recurrence" as
`recurrence_rule_id IS NULL`, but the transfer form has NO null option
(`app/templates/transfers/form.html:87` passes `include_none_option=false`; the transaction form at
`templates/form.html:101` passes `true`), so deleting the rules requires the transfer form and every
transfer reader to handle a NULL rule -- R7's work, pulled into a step that promises no behaviour
change.

So R2b backfills each surviving `Once` rule with
`(interval_n=1, unit=period, anchor_date = start_period.start_date, placement=containing_date, shift=none)`
and `pattern_id = Once` REMAINS the thing that suppresses generation, exactly as today. R9 deletes
the rows.

### R-R5 -- R2 is three steps, because NOT NULL forces every writer to move

Ruled 2026-08-05. R2 said "nothing reads the new ones yet", which is true of READS and silent about
WRITES: the moment the four columns are NOT NULL, every INSERT must supply them. There are 5
production writers (`_recurrence_form_helpers.py:270`, `routes/salary/profiles.py:145`,
`routes/investment.py:252`, `routes/loan/payment_transfer.py:171`, plus
`loan_recurrence_sync.py:167-171` mutating existing rules) and ~80 direct `RecurrenceRule(...)`
constructions across 37 test files. The migration and the writers must therefore land together --
which is one very large commit.

Split instead, the standard expand/contract shape:

| step | contents | state |
|---|---|---|
| **R2a** | the three `ref` vocabulary tables + enums + `ref_cache` accessors + `ref_seeds` + migration `e7a4d95c2b18` | **DONE** |
| **R2b** | the new `recurrence_rules` columns added NULLABLE, `recurrence_weekday_anchors` + `recurrence_month_anchors`, the backfill, the orphan deletion, `AUDITED_TABLES`. No writer changes. | next |
| **R2c** | one authoring seam; all 5 writers and the test constructions routed through it; a second migration re-backfills anything created in between and tightens the four columns to NOT NULL. | after R2b |

The intermediate state is safe: nothing READS the columns until R4, so a rule created between R2b
and R2c carrying NULLs is inert, and R2c's re-backfill catches it.

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
ref.recurrence_units        -- PERIOD, WEEK, MONTH, YEAR                    [R2]
ref.period_placements       -- CONTAINING_DATE, PERIOD_STARTING_ON_OR_AFTER [R2]
ref.business_day_shifts     -- NONE, PRIOR, NEXT                            [R2]

budget.recurrence_rules                                                     [R2]
  id               PK
  user_id          FK auth.users CASCADE          NOT NULL
  interval_n       INT   NOT NULL  CHECK (interval_n > 0)
  unit_id          FK ref.recurrence_units RESTRICT   NOT NULL
  anchor_date      DATE  NOT NULL      -- the first occurrence: phase AND day AND month
  placement_id     FK ref.period_placements RESTRICT  NOT NULL
  shift_id         FK ref.business_day_shifts RESTRICT NOT NULL
  end_date         DATE  NULL   CHECK (end_date IS NULL OR end_date >= anchor_date)
  max_occurrences  INT   NULL   CHECK (max_occurrences > 0)
  created_at
  CHECK (end_date IS NULL OR max_occurrences IS NULL)   -- at most one end bound

budget.recurrence_due_dates    [HALF B -- created in R5/R6, NOT in R2]
                               -- 0..1 per rule; present iff installment <> cash
  recurrence_rule_id  PK FK -> budget.recurrence_rules  ON DELETE CASCADE
  due_day             SMALLINT NOT NULL CHECK (due_day BETWEEN 1 AND 31)
  due_month_offset    SMALLINT NOT NULL DEFAULT 0
                      CHECK (due_month_offset BETWEEN -12 AND 12)

budget.recurrence_weekday_anchors    [R2b creates it; R8 is the first writer]
                                     -- 0..1 per rule; nth-weekday-of-month rules
  recurrence_rule_id  PK FK -> budget.recurrence_rules  ON DELETE CASCADE
  nth_week            SMALLINT NOT NULL
                      CHECK (nth_week BETWEEN -1 AND 5 AND nth_week <> 0)  -- -1 = last
  weekday             SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6)

budget.recurrence_month_anchors      [R2b creates it and is its first writer]
                                     -- 0..1 per rule; present iff the anchor
                                     --   month clamped the nominal day (R-R3)
  recurrence_rule_id  PK FK -> budget.recurrence_rules  ON DELETE CASCADE
  nominal_day         SMALLINT NOT NULL CHECK (nominal_day BETWEEN 29 AND 31)
```

**Zero conditionally-meaningless columns.** Every column on `recurrence_rules` is meaningful for
every unit; the two facts that apply to only some rules are 0-or-1 subtype rows, where presence is
the discriminator and the PK+FK enforces the cardinality.

`placement` is well-defined for all four units rather than inert for some: under the PERIOD unit the
occurrence date IS a period start, so `CONTAINING_DATE` and `PERIOD_STARTING_ON_OR_AFTER` resolve to
the same period.

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
| Monthly First | (1, MONTH) | **STARTING_ON_OR_AFTER** | the 1st of the first covered month |
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

Forward generation plus placement is **total and injective**: periods are contiguous by construction
(`pay_period_service.py:190`), so every date has exactly one period, and a date past the horizon is
an explicit "beyond horizon" rather than a silent drop. This kills D3 structurally, at any cadence,
and retires all five `_match_*` helpers.

## 4. Step sequence

Each step is a leaf boundary: one commit, its own tests green, independently revertible.
**Steps R1-R4 do not change any user-visible behaviour.** Half A = R1-R4, R7, R8 (+ the Half-A part
of R9); Half B = R5, R6 (see section 0).

**R1 -- Oracle and characterization snapshot. DONE** (no production code changed).
`tests/oracles/recurrence_baseline.py` captures what the CURRENT engine answers from its two public
entry points, `match_periods` and `compute_due_date`; `recurrence_baseline.txt` freezes it at
**8,105 lines over 423 shapes**, and `tests/test_services/test_recurrence_baseline.py` is the gate.

Three things it does that the original sketch did not.
**The shape set is exhaustive on every axis the matcher branches on, not the 50 live shapes** -- 50
live shapes are themselves a sample, and the verification standard this arc borrows forbids one. All
31 monthly days, all 12 cycle months against the 6 clamp-relevant days, every `EVERY_N_PERIODS`
interval 1..8 against every legal phase, the whole due-day axis 1..31, and the validity-window
bounds on both sides of a period boundary.
**Keys are `period_index` and the pattern ENUM, never a row id**, so a rebuilt test template cannot
churn the blob. **The D3 shapes freeze the WRONG answer on purpose**, against a 90-day schedule: R4
is expected to change exactly those lines, which makes the fix visible in a diff instead of asserted
in a message.

Shown to fire, not merely asserted: clamping `_match_monthly` one day short turned the gate red at
`recurrence_baseline.txt line 3780` with the committed and captured lines side by side. Two
monkeypatch controls (`compute_due_date`, `match_periods`) patch the SOURCE module, which is what
proves the harness resolves the engine at call time rather than having bound it at import -- the
"can it SEE the code under test?" failure. Suite **7,875 passed** (7,868 + 7), green under
`TZ=Pacific/Kiritimati`; the capture reads no clock.

**R2 -- New schema, additive.** Old columns retained and still authoritative; nothing reads the new
ones yet. Section 3 is the END state, this is how it gets there. **Three steps, not one** -- see
R-R5 for why NOT NULL drags every writer along with it.

*Exit criteria, all three steps.* Migration tested in BOTH directions.
`python scripts/build_test_template.py` re-run (each adds a migration, so every suite fails against
a stale template until it is). And the R1 baseline **byte-identical** -- R2 changes no behaviour, so
a moved line means the migration touched something it should not have.

**R2a -- the vocabulary. DONE** (migration `e7a4d95c2b18`, no behaviour change).
`ref.recurrence_units`, `ref.period_placements`, `ref.business_day_shifts`, their enums, `ref_cache`
accessors and `ref_seeds` entries. Seeded in the migration AND in `ref_seeds` -- the dual-seed
pattern the posting refs use, so a freshly upgraded DB resolves them before `ref_seeds` re-runs.
That is not a nicety: `entrypoint.sh` runs `scripts/init_database.py` (whose `ref_cache.init` is
strict about a table that exists but has no rows) BEFORE `scripts/seed_ref_tables.py`, so an
unseeded new ref table aborts the deploy. Rows are inserted without literal ids so the identity
sequence stays ahead of the data. Verified executably against the prod-clone dev DB: upgrade -> seed
-> `flask db migrate` produces no diff for these tables -> downgrade drops all three -> re-upgrade
re-seeds identically. Suite **7,893 passed**; R1 baseline byte-identical; `pylint app/` 10.00/10.

**R2b -- the columns, NULLABLE.**

*Creates on `budget.recurrence_rules`, all nullable:* `unit_id` (FK `ref.recurrence_units`
RESTRICT), `anchor_date` DATE, `placement_id` (FK `ref.period_placements` RESTRICT), `shift_id` (FK
`ref.business_day_shifts` RESTRICT), `max_occurrences` INT `CHECK (max_occurrences > 0)`, plus the
two rule-level CHECKs from section 3: `end_date IS NULL OR end_date >= anchor_date` and
`end_date IS NULL OR max_occurrences IS NULL`. `interval_n` and `end_date` already exist. Measured
satisfiable: the 3 non-orphan rules carrying an `end_date` derive anchors 2026-03-26, 2026-04-01 and
2026-04-22 against end dates 2026-06-30, 2048-12-01 and 2029-01-22.

*Creates as tables:* `budget.recurrence_weekday_anchors` (nothing writes it until R8: one migration
for the shape, not two) and `budget.recurrence_month_anchors` (R-R3; R2b's own backfill is its first
writer). Both go into `AUDITED_TABLES` before the migration runs -- they are `budget`-schema
user-controlled state, unlike R2a's ref tables. *Does NOT create* `budget.recurrence_due_dates` --
**that one is Half B** (section 0), because Half A must leave the `due_date` contract byte-identical
for the R1 baseline to stay green.

*The backfill* derives each rule from section 3's mapping table. Four derivations it must not be
left to invent:

1. **The effective start** every calendar anchor is measured from is
   `max(rule.start_date, rule.start_period.start_date)` over whichever are present; failing both,
   the earliest generated row's period start; failing that, the user's earliest period start. The
   MAX is what reproduces today exactly -- `match_periods` applies BOTH filters
   (`recurrence_engine.py:481,488`) -- and it also keeps the loan bound, because
   `anchor_date >= start_date` then holds by construction, so no installment can precede origination
   (step C9a). Measured: 2 live rules carry both (the two loan transfers), and the MAX reproduces
   their current first occurrence in each case.
2. **A calendar rule's anchor** is the first date matching its `(month_of_year, day_of_month)` on or
   after that effective start; a `PERIOD`-unit rule's anchor is the effective start itself.
3. **A surviving `Once` rule** gets the inert tuple R-R4 fixes.
4. **The 5 ORPHANED rules have neither a start period nor a template**, so they are DELETED here
   rather than backfilled with a guess (R9 changes the template FKs off `ON DELETE SET NULL` so they
   cannot recur). Deleting them is destructive, so the migration carries the `Review:` line the
   database rules require.

A `recurrence_month_anchors` row is written **iff** the derived `anchor_date` is the last day of its
month AND the rule's `day_of_month` exceeds it -- i.e. the clamp lost information (R-R3). Measured:
zero live rules qualify, so the backfill writes no rows today; the branch still needs a test, built
from a constructed rule rather than live data.

`placement_id` is backfilled to `containing_date` for every rule except `MONTHLY_FIRST` (which gets
`period_starting_on_or_after`), and `shift_id` to `none` for every rule, so R8 turns behaviour on
rather than adding a column.

**R2c -- the writers, then NOT NULL.** One authoring seam that every rule writer goes through, so
the old->new derivation lives in ONE place rather than five; the 5 production writers and the ~80
test constructions routed through it; then a second migration that re-backfills anything created
between R2b and R2c and tightens `anchor_date` / `unit_id` / `placement_id` / `shift_id` to NOT NULL
using the documented three-step (`.claude/rules/database.md`) -- raising `RuntimeError` with the
diagnostic SELECT if any NULL survives.

**R3 -- New engine, parallel and unread.** `app/services/recurrence/` with `occurrences()` and
`place()`. Pure, no Flask. Nothing reads it yet.

Ships with a parallel-run test that drives the NEW engine through
`tests/oracles/recurrence_baseline.py`'s own shape set and schedules and asserts it reproduces the
committed blob -- all **423 shapes**, not the 50 live ones. Expect exactly one class of legitimate
divergence: the `long_cadence.*` shapes, which R1 froze WRONG on purpose (D3's dropped months and
duplicate period). Those lines are the proof R3 works and must be re-frozen with
`SHEKEL_UPDATE_RECURRENCE_BASELINE=1` **in R4's commit, not R3's** -- R3 changes no reader, so
nothing it does may move the baseline yet.

**R4 -- Cut generation over.** `match_periods` becomes a thin adapter, then callers move to the new
engine. D3 dies here. The R1 oracle is the gate.

**R5 -- Row columns.** `transactions.due_date` / `transfers.due_date` -> `occurs_on` (pure rename,
zero value changes -- the column already holds the cash date), plus a new nullable `due_on`. Then
correct all 28 Python files and 5 templates to the one they actually mean. Highest-risk readers:
`transfer_service.py` (9), `balance_at/_plan.py` (8), `rate_period_engine.py` (6),
`loan_payment_service.py` (6).

**R6 -- Delete `payment_day`; one accessor.** `loan_installment_date(...)` becomes the single
derivation, reading the rule + `recurrence_due_dates`. 22 files, including the balance seam
(`balance_at/_plan.py`, `_loan_interest.py`, `_resolution.py`) and the loan ledger. Kills D4.
**This step needs its own review pass** -- it is the deepest cut into the ledger.

**R7 -- Bounds, form, and labels.** `anchor_date` replaces `start_period_id` + `offset_periods` (D1
and D2 die), `max_occurrences` lands, the form becomes interval + unit + anchor + optional due row,
and ONE label function over `(interval, unit)` replaces the 8-branch `recurrence_cell` macro
(`_recurrence_macros.html:17`), the 8-entry `recurrence_pattern_labels` dict
(`app/__init__.py:321`), and the 8 `REC_*` Jinja globals (`jinja_globals.py:91`).

**R8 -- Add-ons.** WEEK unit, `recurrence_weekday_anchors`, business-day shift, count-bounded end.
Note: the shift applies to the CASH date only -- a bill due Aug 1 paid Friday because Aug 1 is a
Sunday still satisfies the Aug 1 installment, so `recurrence_due_dates` is never shifted.

**R9 -- Drop the old columns**, the `ref.recurrence_patterns` table, the `Once` row, and
`pay_period_admin._repoint_recurrence_rules` (`:756-795`, obsolete once phase is a date). Delete the
5 orphaned rules and change the template FKs off `ON DELETE SET NULL` so rules cannot leak again.

Derived simplifications that fall out and must be taken, not left behind:

- `savings_goal_service.amount_to_monthly` (`:406-450`): 8-branch switch -> 4 lines over
  `(interval, unit)`, automatically correct for intervals not yet invented.
- `calendar_service._INFREQUENT_PATTERNS` (`:74-79`): enumerated -> derived.
- The four `Once` guards: deleted.

### Carried steps -- found while building this arc, NOT part of it

Section 5 records three defects this arc surfaced and does not own. They get steps here so they are
scheduled rather than remembered. **None blocks R1-R9, and none is blocked by them**; each is a
standalone commit that can run in any gap. Do not fold them into a recurrence migration -- an
unrelated fix riding in a schema migration is unreviewable.

**R-F1 -- Re-sync the five lagging `ref` identity sequences** (finding 5.1). **Do this one first**:
it is the only one with a live failure mode, and the failure lands during a DEPLOY.

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

**R-F2 -- Tighten the ref-seed parity scan's statement boundary** (finding 5.2).
`_insert_statement_bodies` (`tests/test_models/test_posting_ref_seed_parity.py`) bounds a statement
at the next upper-case SQL keyword, so the LAST `INSERT` in a migration runs to end-of-file and any
quoted literal below it satisfies the scan. Add a Python-level boundary (a line beginning `def `)
and pin the fix with a test that the scan REJECTS a value appearing only after the seed statement --
without that negative test the change proves nothing. Its own commit, because it changes the
semantics of a scan four other migrations' coverage rests on: re-run the whole file and show the
existing assertions unchanged.

**R-F3 -- Resolve the ref-table constraint-naming disagreement** (finding 5.3).
**Starts with a ruling, not a keystroke.** `.claude/rules/database.md` says name every constraint;
all ~23 `ref` tables use auto-named `<table>_pkey` / `<table>_name_key`. Recommendation: amend the
RULE to exempt single-column PK/UNIQUE on `ref` lookup tables, because the names are never
referenced (no downgrade drops them by name -- the table goes with them) and renaming 23 tables'
constraints is a large migration that buys nothing. The alternative is a rename migration. Either
way the outcome must land in `.claude/rules/database.md` so the next reader is not told two
different things.

## 4a. `PAY_PERIODS_PER_YEAR` (folded into R7)

`PAY_PERIODS_PER_YEAR = Decimal("26")` (`app/utils/money.py:43`) is a magic number while
`cadence_days` is user-selectable 1..365, so every monthly-equivalent figure on `/obligations`,
`/savings`, and the Recurring surface is wrong for any non-biweekly schedule. It is NOT a separate
task: R7 already rewrites `savings_goal_service.amount_to_monthly` from an 8-branch pattern switch
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

Two things this surfaces, RECORDED NOT BUILT (rule 6, both are pay-period concerns rather than
recurrence ones):

- `pay_periods` stores nominal paydays. A holiday/weekend shift for the PAY SCHEDULE is the sibling
  of R8's business-day shift for recurrence occurrences, and would need the same holiday source.
  Scoping it is its own task.
- A 27-paycheck year is a real budgeting event (one extra Groceries at
  $500, one extra Data Manager paycheck at $2,473.38 of income). Surfacing it is a feature.

## 5. Found while building, RECORDED NOT BUILT (rule 6)

Three defects this arc surfaced but does not own. None is caused by the redesign; each is stated
with its measurement so a later session does not have to re-find it. **Each has a scheduled step**
-- 5.1 -> R-F1, 5.2 -> R-F2, 5.3 -> R-F3 in section 4's "Carried steps" block -- so the measurement
below is the evidence and the step is the work.

**5.1 -- Five `ref` identity sequences sit BEHIND their data, on production.** Measured 2026-08-05
against `shekel-prod-db` (and identically on the dev clone): `goal_modes` (max id 2, next value 1),
`income_units` (2/1), `user_roles` (2/1), `compounding_frequencies` (3/1),
`employer_contribution_types` (3/1). Their migrations seeded literal ids
(`INSERT INTO ref.goal_modes (id, name) VALUES (1, 'Fixed'), ...`, e.g. `1dc0e7a1b9e4`), which does
not advance the sequence.

Latent today because `ref_seeds.seed_reference_data` only INSERTs a MISSING row and none is missing.
It bites the first time anyone adds a value to one of those five enums: the id-less INSERT asks for
id 1, collides on the primary key, and `scripts/seed_ref_tables.py` aborts -- **during a deploy**.
Fix is a one-line `setval` per table in a migration. R2a's own three tables seed without ids and are
pinned ahead of their data by `TestIdentitySequenceInStep`.

**5.2 -- The ref-seed parity scan's last `INSERT` body runs to end-of-file.**
`tests/test_models/test_posting_ref_seed_parity.py` bounds each `INSERT INTO <table>` body at the
next upper-case SQL keyword, so the LAST insert in a migration has no closing boundary. Measured
honest today (none of `'none'` / `'prior'` / `'next'` appears in the 1,304 characters of Python
after `e7a4d95c2b18`'s final seed), but a future edit that quotes one of those values below the seed
would satisfy the scan without seeding anything. Left alone deliberately: tightening the boundary
changes the semantics of a scan four other migrations already depend on, which does not belong in an
additive commit.

**5.3 -- `ref` tables use auto-named PK/UNIQUE constraints.** `.claude/rules/database.md` says "name
all constraints" (`uq_<table>_<cols>`), but every `ref` table in the project -- including
`f5037400dc5e`'s posting tables, byte-identical in shape -- uses bare
`sa.PrimaryKeyConstraint("id")` / `sa.UniqueConstraint("name")` and lets PostgreSQL name them
`<table>_pkey` / `<table>_name_key`. R2a follows the house pattern rather than making its three
tables the odd ones out. The rule and the pattern disagree; one of them should be amended,
project-wide, in its own pass.

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
