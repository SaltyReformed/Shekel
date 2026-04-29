# Carry-Forward Aftermath -- UX and Display Design

## Purpose of this document

The carry-forward bug fix (commit `33cd21e`, merged via `95da8c5`) ships
the override-sibling data model: when a user clicks "Carry Fwd" on a
past period, an unpaid template-linked row is moved to the target
period, where it now coexists with the recurrence-engine-generated row
for that template. Both rows are real database rows with independent
status, entries, and audit trails.

That fix is correct at the data layer. It introduces a new class of UX
issues at the **display** layer because every consumer of "transactions
in a period" now sees two rows where there used to be one. This document
inventories those issues, explores the design space for resolving them,
and recommends a phased plan of action sized for a mini-sprint rather
than a single commit.

This is a **design exploration** document, not an implementation plan.
The implementation plan it informs lives at
`docs/implementation_plan_envelope_view.md` (created concurrently) and
should be revised against the decisions made from this analysis.

---

## 1. Background

### 1.1 What carry-forward does today

`app/services/carry_forward_service.py:carry_forward_unpaid` is the
canonical entry point. It:

1. Loads all `Projected`, non-deleted transactions in the source
   period scoped to a single scenario.
2. For regular transactions: sets `is_override = True` and updates
   `pay_period_id = target_period_id`. If the row had `template_id`,
   the override flag is what previously caused the unique-index
   violation; the relaxed index `idx_transactions_template_period_scenario`
   now permits the override sibling alongside the rule-generated row.
3. For shadow transfers: routes through `transfer_service.update_transfer`
   to atomically move the parent transfer plus both shadows.
4. Returns the count moved.

The recurrence engine (`app/services/recurrence_engine.py:107-128`)
treats any pre-existing row in a period as a "skip generation" signal
when its status is immutable, when `is_override = True`, or when it is
soft-deleted. This means: once a row is moved into a period via
carry-forward, recurrence will not generate a sibling for that
template-period in subsequent runs.

### 1.2 The data shape after carry-forward

Worked example used throughout this document:

> Spending money template, $100 default, `companion_visible = True`.
> Recurrence has generated rows in periods A, B, C ($100 each, all
> Projected).  Wife records $65 of entries against period A's row and
> $0 elsewhere.  User clicks Carry Fwd on period A.

After carry-forward:

| Field                    | Period A | Period B canonical | Period B carried |
|--------------------------|----------|---------------------|-------------------|
| `id`                     | (moved)  | 1001                | 999               |
| `template_id`            | 5        | 5                   | 5                 |
| `pay_period_id`          | (gone)   | B                   | B                 |
| `is_override`            | --       | False               | True              |
| `status_id`              | --       | Projected           | Projected         |
| `estimated_amount`       | --       | 100.00              | 100.00            |
| `actual_amount`          | --       | NULL                | NULL              |
| Entries on the row       | --       | 0                   | 65                |

Both Period B rows have the same `(template_id, pay_period_id,
scenario_id)`. The relaxed unique index permits this because one has
`is_override = True`. The recurrence engine will skip Period B in
subsequent runs because at least one existing row matches its skip
condition.

### 1.3 The user-visible effect

Every page that renders transactions for a period now potentially shows
two rows where users expect one envelope. The grid cell, the dashboard
upcoming-bills list, the budget variance breakdown, the companion card
view, and the year-end summary all materialize as separate transactions.

The wife's mental model for envelope-style line items (groceries,
spending money, gas) is **one envelope per category per period**. The
data model post-fix is **two rows per envelope per period after
carry-forward**. Reconciling these is what this document is about.

---

## 2. Issues caused by the carry-forward fix

These are the concrete UX surfaces affected. Each subsection cites the
specific file and function that produces the surface, the current
behavior, and the user-visible problem.

### 2.1 Grid cell rendering (desktop and mobile)

- **Where**: `app/templates/grid/grid.html:150-176` (income loop) and
  `:226-249` (expense loop) build a `matched` list per `(period,
  row_key)` and include `_transaction_cell.html` once per matched
  transaction. `app/templates/grid/_mobile_grid.html` duplicates the
  same matching logic (this duplication is pre-existing).
- **Current behavior**: two `<div id="txn-cell-{id}">` elements
  stacked inside one `<td>`. Each has its own progress bar (`12/100`
  and `65/100`), its own status badge, its own pencil icon for the
  `is_override` row, its own click handler.
- **User-visible**: the wife has to mentally sum two progress
  counters to know how much envelope is left. The pencil icon
  disambiguates the carried row but does not make the math easier.
- **Click behavior**: clicking either cell opens that single row's
  quick-edit popover, swapping into `#txn-cell-{id}`. Editing one row
  does not refresh the other.

### 2.2 Dashboard "Upcoming Bills"

- **Where**: `app/services/dashboard_service.py:_get_upcoming_bills`
  (lines 98-163). Filters Projected expenses for current and next
  period, returns one bill dict per transaction.
- **Current behavior**: after carry-forward, both rows for the same
  template show up as separate bills.
- **User-visible**:

  ```
  Upcoming Bills (period 4/23)
    - Spending Money    $100
    - Spending Money    $100
    - Rent             $1500
  ```

  Wife logs into the dashboard expecting one spending-money line and
  sees two. The pencil icon does not appear in this view.

### 2.3 Budget Variance

- **Where**: `app/services/budget_variance_service.py:_build_txn_variance`
  (lines 357-377), called for every matched transaction.
- **Current behavior**: each row gets its own
  `TransactionVariance(estimated, actual, variance)` entry. Group totals
  sum the per-row estimates.
- **User-visible**:

  ```
  Period 4/23 -- Spending Money breakdown:
    row A: estimated $100, actual --, variance $0
    row B: estimated $100, actual --, variance $0
    Group total: estimated $200, actual --, variance $0
  ```

  Wife's mental model is one $135 envelope. Variance shows $200 budgeted.
  The two row entries are visually identical and confusing.

### 2.4 Year-End Summary

- **Where**: `app/services/year_end_summary_service.py:520-557`. Joins
  TransactionEntry to Transaction to TransactionTemplate, sums entries
  by category for settled, tracked transactions only.
- **Current behavior**: aggregates *entries*, not *estimates*. Each
  entry is tied to a specific row, but the year-end groups by category
  and template.
- **User-visible**: actual-spend totals are correct (entries reflect
  real purchases). The estimate side could overcount if any year-end
  display shows "budgeted by category = sum(estimated_amount)" but I do
  not see that in the cited lines. **Likely correct as-is**, but worth
  a focused review.

### 2.5 Companion view

- **Where**: `app/services/companion_service.py:get_visible_transactions`
  returns a list of Transaction objects (not grouped). Templates
  `app/templates/companion/index.html:36-38` and
  `app/templates/companion/_transaction_card.html` render one card per
  transaction.
- **Current behavior**: two cards for the same envelope after
  carry-forward.
- **User-visible**: same as the grid cell problem, one level worse:
  the companion view is the wife's primary interface, and she has to
  decide which card to record entries against without obvious
  guidance.
- **Entry routing**: `app/routes/entries.py:_get_accessible_transaction`
  (lines 33-64) accepts entries for any specific txn_id the wife
  targets. There is no UI today that tells her which one is "the
  envelope" and which one is "leftover from last period."

### 2.6 Period subtotals and balance projections

- **Subtotals**: `app/routes/grid.py:266-282` sum every Projected
  transaction's `effective_amount` per period. Both rows count.
- **Balance**: `app/services/balance_calculator.py` reduces projected
  balance by every non-deleted, non-excluded transaction's
  `effective_amount`. Both rows count.
- **Current behavior**: Period 4/23 subtotal includes $200 of spending
  money obligation; projected balance reduces by $200 across that
  period.
- **User-visible**: this is **financially correct**. Both rows
  represent allocated cash. Free cash projection is unaffected by the
  envelope display question. The only issue arises if the cell display
  changes to show $135 envelope while subtotal still shows $200 -- a
  divergence the user would need to understand.

### 2.7 HTMX partial-swap target IDs

- **Where**: `app/routes/transactions.py:_render_cell_response` (lines
  50-67) returns an updated single-cell partial after every quick-edit
  save, swapping into `#txn-cell-{id}`. Three more handlers in
  `app/routes/transfers.py` (lines 657, 764, 804) do the same for
  transfer cells.
- **Current behavior**: works correctly for one-row-per-cell. Each
  cell `<div>` has a unique DOM id keyed on the txn id.
- **Issue under any combined-view design**: editing one row of an
  envelope re-renders that one row's HTML, leaving the sibling and the
  envelope-level progress display stale until the next full grid load.
  Any aggregation effort must change the DOM id scheme to be keyed at
  the envelope level (e.g. `#envelope-cell-{period}-{template}`) and
  the four route handlers must rebuild the envelope on save.

### 2.8 Recurrence engine skip rule under envelope semantics

- **Where**: `app/services/recurrence_engine.py:107-128` (and the
  parallel `app/services/transfer_recurrence.py:78-91`).
- **Current behavior**: skip the period if any existing row has
  immutable status, `is_override = True`, or `is_deleted = True`.
- **Issue under envelope semantics**: if the user carry-forwards into
  a period **before** the recurrence engine has populated that
  period's canonical row, the carried row blocks canonical generation.
  The wife ends up with a single-member envelope (all carried) where
  she expected a 2-member envelope (canonical + carried). The skip
  rule needs to ignore carried-only overrides; this requires the
  schema column proposed in section 4.

### 2.9 Multi-hop carry-forward

- **Scenario**: wife forgets to carry-forward two periods running.
  User presses Carry Fwd on Period A; later presses Carry Fwd on
  Period B (which now contains both B's canonical and the row carried
  from A).
- **Current behavior**: works at the data level. All Projected rows in
  B get moved to C. C now has `1 canonical + 2 carried`.
- **Display issue**: the row that originally came from A loses its
  trace once it carries to B then to C. Whatever provenance column we
  add, we have to decide: overwrite each hop, or preserve the original
  source. See section 6.

### 2.10 Status workflow on combined views

- **Scenario**: husband marks the carried row Done at `actual_amount =
  $50` (recording last period's actual) while leaving the canonical
  row Projected.
- **Current behavior**: each row has its own status. Status badges in
  the cell render per-row.
- **Issue under combined view**: the envelope cell needs to summarize
  status. "Show canonical's status" works most of the time but is
  ambiguous when the carried row is settled at a non-zero actual --
  does that affect envelope_spent? See section 5.4.

---

## 3. Display options for the grid cell

Five options that have been considered. Each is evaluated against the
user's stated mental model and against financial correctness.

### 3.1 Option A: Status quo (two stacked cells)

- **Display**: two cells, `12/100` and `65/100`, with the override
  pencil on the carried one.
- **Math**: each row's `entries_sum / estimated_amount`.
- **Schema impact**: none.
- **Code impact**: none.
- **Pros**: zero work, all detail visible, status independence preserved
  in plain sight, audit trail is the rendered UI.
- **Cons**: wife has to mentally sum to know envelope state. Dashboard
  bills and variance reports also show duplicate rows. Pencil icon
  disambiguates one from the other but does not solve the math problem.
- **Acceptable when**: wife rarely uses carry-forward for
  envelope-style items, OR you want to ship the envelope-display work
  in a separate phase.

### 3.2 Option B: Sum-and-sum (`83/200`)

- **Display**: a single cell. `entries_sum_total / estimated_total`.
  For our example: `(0 + 65) / (100 + 100) = 65/200`. After wife
  spends another $18 against either row: `83/200`.
- **Math**: numerator = sum of entries across siblings; denominator =
  sum of `estimated_amount`s.
- **Schema impact**: none.
- **Code impact**: medium -- aggregation helper, cell partial refactor,
  grid/mobile/companion template updates, partial-swap DOM id change.
- **Pros**: financially honest. Numbers tie out to subtotal ($200) and
  balance projections. Wife sees full envelope, full spend. No schema
  column required.
- **Cons**: counts last period's $65 as part of "this period's spend."
  Wife's envelope mental model says "$65 was already spent last
  period; this period I have $135 fresh." Sum-and-sum buries that.
  Wife may also be confused that envelope size is `$200` when she
  thinks of it as "$100/period."

### 3.3 Option C: Single-row denominator (`77/400`)

- **Display**: combine entries but use only the canonical row's
  estimated. `(12 + 65) / 100 = 77/100`. Or with the user's earlier
  example numbers, `77/400`.
- **Math**: numerator = sum of entries; denominator = canonical's
  estimated.
- **Schema impact**: none.
- **Code impact**: same as Option B.
- **Pros**: small denominator matches the wife's "envelope is $100"
  mental model.
- **Cons**: misleading once entries cross $100 -- envelope reads "full
  / over budget" when there is in fact $100 of carried allocation
  unspent. Subtotal disagrees more sharply ($200 vs $100). Doesn't
  match either mental model cleanly.
- **Verdict**: **rejected**. Worst of both worlds.

### 3.4 Option D: Envelope rollover (`18/135`) [recommended]

- **Display**: matches wife's spending-money example exactly. Treat
  the canonical row as "this period's allocation," and treat each
  carried row as "leftover budget from a prior period that rolls
  forward."
- **Math**:

  ```
  envelope_budget = canonical.estimated_amount
                  + Σ max(0, c.estimated_amount - c.entries_sum)
                    for c in carried

  envelope_spent  = canonical.entries_sum
                    (or canonical.actual_amount if the row is settled)
  ```

  For the worked example: `100 + max(0, 100 - 65) = 135`. Initial
  display `0/135`. After wife spends $18 on canonical: `18/135`.
- **Schema impact**: requires a new column to distinguish "carried
  from prior period" from "manually edited in this period." Both set
  `is_override = True` today. See section 4.
- **Code impact**: largest. Schema migration + carry-forward and
  transfer-service writes + recurrence-engine skip-rule update +
  aggregation helper + cell partial refactor + grid/mobile/companion
  template updates + partial-swap DOM id change + dashboard upcoming
  bills + variance grouping + tests. Estimated 900 to 1100 LoC.
- **Pros**: matches the envelope mental model exactly. The badge UX
  for carry-forward rows comes for free as a side-effect (the column
  enables an unambiguous "carried" indicator). Multi-hop works without
  special casing.
- **Cons**: cell display ($135) diverges from period subtotal ($200)
  and balance projections ($200). Need to document the divergence.
- **Verdict**: **best fit for the wife's stated mental model and the
  user's worked example**.

### 3.5 Option E: Settle-source-on-carry-forward

- **Display**: same as Option D, but achieved by changing
  carry-forward semantics rather than adding a display layer.
- **Mechanism**: when the user clicks Carry Fwd, do not move the
  source row. Instead:
  1. Mark the source row Done with `actual_amount = entries_sum`
     (lock in last period's spend).
  2. Create a new "rollover" row in the target period with
     `estimated_amount = source.estimated - source.entries_sum`
     (the unspent leftover), `is_override = True`, `template_id =
     source.template_id`.
- **Result**: target period has two rows in the same shape as Option D
  (1 canonical at $100 + 1 rollover at $35), but with strictly correct
  data model: nothing is "carried" twice; period A is closed at $65
  actual; period B has $135 of allocation total; balance projections
  reduce period A by $65 (actual) and period B by $135 (envelope total).
- **Pros**: cleanest data model. No display-layer math. Subtotal and
  balance reduce by $135 in period B (matches envelope). No schema
  column needed.
- **Cons**: forks carry-forward into "envelope" vs "discrete obligation"
  branches -- envelope items roll, discrete items (rent, utilities)
  carry forward as full obligations. This is the exact DRY/SOLID
  violation rejected during the original carry-forward fix design.
  Also, the carry-forward UI button has been doing one thing for users
  for a while; changing the semantics is a behavioral break for any
  workflow that depended on "carry the row over so I can pay it later"
  (e.g. wife forgot to mark rent paid before period rolled over).
- **Verdict**: **rejected** for the same reasons it was rejected in
  the original carry-forward design discussion. Listed here for
  completeness.

### 3.6 Comparison matrix

| Option | Display | Math | Schema | Code effort | Mental model | Subtotal divergence |
|---|---|---|---|---|---|---|
| A. Status quo | `12/100` + `65/100` | per-row | none | none | wife sums in head | none |
| B. Sum-and-sum | `83/200` | total / total | none | medium | counts prior spend; foreign | matches |
| C. Single-row denom | `77/100` | total / canonical | none | medium | misleads once over | mismatch |
| **D. Envelope rollover** | **`18/135`** | **leftover-aware** | **+1 column** | **large** | **exact match** | **mismatch (documented)** |
| E. Settle-source | `0/135` | data-level rollover | none | large + behavior change | exact match | matches |

---

## 4. The schema column: `carried_from_period_id`

### 4.1 Why is_override alone is insufficient

`is_override = True` is set in two distinct paths:

1. `app/routes/transactions.py:273-274` -- when a user manually edits
   a rule-generated row's amount, period, or status, the route flips
   `is_override = True`. The row stays in its current period; it
   represents *this period's allocation, modified*.
2. `app/services/carry_forward_service.py:107` -- when carry-forward
   moves a row, it sets `is_override = True`. The row's `pay_period_id`
   changes; it represents *a prior period's allocation, rolled
   forward*.

Envelope math needs to treat these cases differently:

- **Manually edited row**: entries on it ARE this period's spend.
  Counts in `envelope_spent`.
- **Carried row**: entries on it represent prior-period spend. Must
  NOT count in `envelope_spent`; the unspent portion contributes to
  `envelope_budget` as leftover.

A heuristic ("two rows for the same template-period means one is
carried") fails on the rare case where a user manually edits a row in
this period AND carry-forward brings another row in. Both rows then
have `is_override = True` with no canonical; envelope math cannot
recover the user's intent.

### 4.2 Proposed column

`carried_from_period_id INT NULL` on `budget.transactions` and
`budget.transfers`.

- FK reference: `budget.pay_periods.id` with `ON DELETE SET NULL`.
- Partial index `idx_<table>_carried_from_period` WHERE
  `carried_from_period_id IS NOT NULL`. (Backward queries are rare;
  partial keeps it small.)
- Set by `carry_forward_service.carry_forward_unpaid` when moving a
  row. Null on rule-generated rows and on manually edited rows.

### 4.3 What it enables

- **Envelope math** -- the canonical/carried split that Option D
  requires.
- **Recurrence skip-rule fix** -- "skip if any non-carried override
  exists in period" rather than "skip if any override exists."
- **Carry-forward badge UX** -- unambiguous icon showing this row was
  rolled forward, with tooltip "Carried from <date range>".
- **Dashboard grouping** -- group bills by envelope using the same
  helper.
- **Variance grouping** -- collapse two rows of $100 into one envelope
  row of $135 (matches grid cell).
- **Audit hint** -- if the user wants to see where a row originated,
  the column makes that visible. Multi-hop overwrites lose the
  original source; if that is unacceptable, see section 6 for the
  alternatives.

### 4.4 Migration shape

```python
def upgrade():
    op.add_column(
        "transactions",
        sa.Column(
            "carried_from_period_id",
            sa.Integer(),
            sa.ForeignKey(
                "budget.pay_periods.id", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        schema="budget",
    )
    op.create_index(
        "idx_transactions_carried_from_period",
        "transactions",
        ["carried_from_period_id"],
        schema="budget",
        postgresql_where=sa.text(
            "carried_from_period_id IS NOT NULL"
        ),
    )
    # ... same for budget.transfers ...

def downgrade():
    op.drop_index(
        "idx_transactions_carried_from_period",
        table_name="transactions",
        schema="budget",
        postgresql_where=sa.text(
            "carried_from_period_id IS NOT NULL"
        ),
    )
    op.drop_column(
        "transactions", "carried_from_period_id", schema="budget",
    )
    # ... same for budget.transfers ...
```

No data backfill. Existing rows leave the column null (none of them
were carried under the prior data model -- `is_override = True` rows
in production today are exclusively from manual edits, since the
unique index forbade carry-forward sibling rows).

---

## 5. Subtotal and balance philosophy

### 5.1 Three numbers that diverge under Option D

For the worked example after carry-forward but before any new spend:

| View | Number | What it answers |
|---|---|---|
| Cell envelope display | $135 | "How much budget is available to spend this period?" |
| Period subtotal | $200 | "How much total cash flow is allocated to this category this period?" |
| Balance projection | account_balance - $200 | "What will my account balance be at end of period if every projected obligation hits?" |

After wife spends $18 on canonical:

| View | Number |
|---|---|
| Cell envelope display | `18/135` ($117 remaining envelope) |
| Period subtotal | $200 unchanged (subtotal is allocations, not spend) |
| Balance projection | account_balance - $200 unchanged |

### 5.2 Why subtotal and balance should stay at $200

The carried row's $100 estimate represents real cash earmarked for
spending money. If the wife does not spend it all, she has not freed it
up -- it remains allocated until somebody acts (wife marks Done at
actual=$X; husband cancels; row gets deleted). Until then,
`balance_calculator` correctly subtracts $100 from the projected
balance.

If we route envelope semantics into `balance_calculator` so it reduces
balance by $135 not $200, we are saying "the carried row is only a
$35 obligation now." That hides the $65 that the wife spent **last
period** but that is now (post-carry-forward) attributed to the new
period's row. The cumulative balance projection across both periods
becomes wrong.

**Proof**: pre-carry-forward, the source row hits balance_calculator's
period A by $100 (full estimated). Wife's $65 entries do not change
this -- entries do not flow into `effective_amount`. Post-carry-forward,
the row's `pay_period_id` moves to B; period A's hit drops to $0,
period B's hit grows to $200. Total cash flow across A and B:
unchanged. If we now reduce period B's hit to $135, total cash flow
drops by $65 -- matching the wife's actual spending in period A but
without recording it anywhere.

### 5.3 The cell vs subtotal divergence is real but narrow

The wife rarely looks at the period subtotal -- it lives at the bottom
of the desktop grid, not on her companion view. The husband, who plans
balance, looks at the subtotal. He cares about full cash-flow allocation,
not envelope-experiential "available to spend." The two views serve
different users.

Mitigation if it proves confusing in practice:

- **Tooltip on the cell**: "Envelope: $135 ($100 this period + $35
  carried). Total period allocation: $200." Costs ~5 lines of Jinja.
- **Optional secondary subtotal row**: alongside "Period total: $X" add
  "Envelope total: $Y." Costs ~30 lines, no balance_calculator change.
  Defer until you have used the cell display for a while and decide
  whether you want it.

### 5.4 Settled-canonical math

When the canonical row's status changes from Projected to Done with a
real `actual_amount`, what does `envelope_spent` use?

**Recommended rule**: same as today's single-cell logic. If the row is
settled (`status.is_settled`), use `actual_amount` (or
`effective_amount` which falls back to `estimated_amount` when actual
is null). Otherwise use `entries_sum`.

This matches the existing `_transaction_cell.html` behavior at lines
33-37 (display crossed-out estimate plus actual when they differ).
Aligned with how the rest of the app handles the Projected to Done
transition.

### 5.5 Settled-carried math

If the wife marks the carried row Done with `actual_amount = $0`,
what is the leftover?

**Recommended rule**: still compute leftover as
`max(0, estimated - max(actual, entries_sum))`. If actual is set, use
actual; otherwise use entries_sum. Reasoning: actual represents the
final settled spend; entries are interim records. Whichever is larger
captures the true outflow.

Edge case: wife marks carried Done at actual = $0 because she means
"this allocation is closed and the money is freed." Under the rule
above, leftover = $100, so envelope grows by $100 -- the wife sees
"$100 of last period rolled forward as savings." That matches strict
envelope budgeting (unspent rolls forward).

If she instead means "release this $100 back to general budget, do not
roll it into this period's envelope," she should cancel the row (which
sets `excludes_from_balance` via the Status flags) rather than mark
Done at $0. The aggregation helper filters out cancelled rows, so the
envelope size shrinks correctly.

---

## 6. Multi-hop carry-forward

### 6.1 Scenario

Wife forgets to carry-forward in periods A and B. User clicks Carry
Fwd on Period A first, then on Period B.

```
Initial:
  Period A: canonical $100, $0 entries, Projected
  Period B: canonical $100, $0 entries, Projected
  Period C: canonical $100, $0 entries, Projected

After Carry Fwd on A:
  Period A: empty
  Period B: canonical $100, $0 entries
            carried-from-A $100, $0 entries (carried_from_period_id=A)
  Period C: canonical $100, $0 entries

Wife spends $30 against B's canonical.

After Carry Fwd on B:
  Period A: empty
  Period B: empty
  Period C: canonical $100, $0 entries (C's own canonical)
            carried-from-B $100, $30 entries (was B's canonical)
              carried_from_period_id = B
            carried-from-? $100, $0 entries (was A's row, then B's carried)
              carried_from_period_id = ???  ← decision point
```

### 6.2 Decision: overwrite vs preserve

**Overwrite**: when a carried row is moved a second time, set
`carried_from_period_id = previous_pay_period_id` (the immediate source).
The row that originated in A now reads `carried_from_period_id = B`,
losing the trace to A.

**Preserve original**: only set `carried_from_period_id` if it is
currently null. Once set, keep it.

### 6.3 Math is identical under both policies

Envelope math does not look at `carried_from_period_id` for sums; it
looks at `IS NULL` versus `IS NOT NULL` to bucket rows into canonical
versus carried.

For period C in the example above:

```
canonical:    $100, $0  entries, carried_from_period_id IS NULL
carried (1):  $100, $30 entries, carried_from_period_id IS NOT NULL
carried (2):  $100, $0  entries, carried_from_period_id IS NOT NULL

envelope_budget = 100 + max(0, 100 - 30) + max(0, 100 - 0)
                = 100 + 70 + 100
                = 270
envelope_spent  = 0 (canonical's entries)
display         = 0/270
```

Same result regardless of whether the second carried row's column
reads `A` or `B`.

### 6.4 What differs is the badge and audit

The deferred carry-forward badge shows "Carried from <period date
range>." Under overwrite, the badge for the doubly-carried row reads
"Carried from B" -- which is true (the user pressed Carry Fwd on B,
moving the row from B to C). Under preserve, the badge reads "Carried
from A" -- which is also true (the row originated in A) but less
intuitive given the user's most recent action was on B.

Recommendation: **overwrite**. Display matches the user's most recent
action. If full lineage is ever needed (auditing a chain of
carry-forwards), the right shape is a separate
`transaction_carry_forward_history` table that logs each move. That is
heavyweight and not warranted by current requirements.

### 6.5 What happens to the `entries` on multi-hop rows

Entries are tied to `transaction_id`, not period. When a row moves from
A to B, its entries move with it (their `transaction_id` does not
change). When the row moves again from B to C, entries still travel.

For the envelope math, this means: a row that originated in A with $20
in entries, then was carried to B (still $20 entries), then carried to
C, contributes `max(0, 100 - 20) = 80` to C's envelope. Correct: $80 of
that allocation was unspent across A and B; $20 was spent during A
(before carry-forward, since post-carry-forward the wife was supposed
to be putting new entries on the new period's canonical).

This is technically right but introduces a subtle gotcha: if the wife
adds a NEW entry against the row after it was carried, the new entry
becomes indistinguishable from the original entries. The envelope helper
would treat it as "prior-period spend" (reducing leftover) when the wife
intended it as "this period's spend" (which should land on canonical).

**Mitigation** (in scope for the implementation plan): the entry
recording flow defaults to the canonical row when opened from a
combined cell. The wife rarely targets the carried row directly. If she
does, that is a deliberate action with no UI guard against it -- the
envelope math will charge the entry against "prior-period leftover"
rather than "current-period spend." Document this as a known edge case;
revisit if it bites.

---

## 7. Recurrence engine skip-rule change

### 7.1 Current behavior

`app/services/recurrence_engine.py:107-128` iterates existing rows in
each candidate period. The first matching skip condition wins:

```python
for existing_txn in existing_txns:
    if existing_txn.status and existing_txn.status.is_immutable:
        should_skip = True
        break
    if existing_txn.is_override:
        should_skip = True
        break
    if existing_txn.is_deleted:
        should_skip = True
        break
    should_skip = True  # any other existing entry blocks generation
    break
```

A carried row in a period blocks canonical generation because
`is_override = True` matches the skip clause.

### 7.2 Required change under envelope semantics

Skip only on **non-carried** overrides:

```python
if existing_txn.is_override and existing_txn.carried_from_period_id is None:
    should_skip = True
    break
```

Effect: a carried row alone does not prevent recurrence from generating
the period's canonical row. Manually edited rows still block (they
represent the user's deliberate choice for this period).

### 7.3 Mirror on transfer recurrence

`app/services/transfer_recurrence.py:78-91` has identical skip logic.
Change in lockstep.

### 7.4 Test surface

- New test: carrying forward into a period that has not yet had
  recurrence run -> running recurrence -> canonical is generated
  alongside the carried row.
- Existing test (`test_respects_is_override_flag`) should be re-read
  to confirm it tests "non-carried override" specifically. May need to
  be split into "manual override blocks regeneration" and "carried
  override does not block regeneration."

---

## 8. HTMX partial-swap target IDs

### 8.1 Today

Cell wrapper `<div id="txn-cell-{txn.id}">` in `_transaction_cell.html`.
Quick-edit `hx-target` swaps `innerHTML` of that div. Four route
handlers return refreshed cell HTML for the same div:

- `app/routes/transactions.py:_render_cell_response` (lines 50-67)
- `app/routes/transfers.py` -- three handlers around lines 657, 764,
  804.

### 8.2 Required change for combined view

The cell IS the envelope under Option D. DOM id moves to the envelope
level:

```
<div id="envelope-cell-{period_id}-{template_id}">
  ... envelope progress, status badges, click handler ...
</div>
```

For ad-hoc rows (no template_id), the id falls back to a
name-or-category-derived key. The grid loop already keys row keys this
way, so the convention exists.

### 8.3 Route handler ripple

Each of the four handlers, after committing the txn change, must:

1. Identify the envelope (period plus template or name).
2. Reload all sibling rows for that envelope.
3. Build a fresh `EnvelopeCell` view-model.
4. Render `_transaction_cell.html` with the envelope.
5. Set the HTMX response header `HX-Reswap` and target if needed, OR
   structure the response so the existing client-side `hx-target`
   continues to point at the envelope div.

This is the single largest source of risk in Option D. The
`_render_cell_response` helper centralizes the rendering; the
sibling-loading logic is the new code.

---

## 9. Click and edit behavior in combined view

### 9.1 Click default

For multi-member envelopes, the cell click opens the canonical row's
quick-edit by default. Rationale: most edits target the current
period's allocation, not the leftover. This matches user intent in the
common case.

### 9.2 Drill-down

The popover shows status badges for both rows and an "Edit member"
affordance per row. The husband can mark the carried row Done if the
wife confirms last period's actual amount; the wife can record entries
against canonical.

This is the primary place where the per-row independence of the data
model surfaces. Users who do not care about the distinction never see
it; users who do can drill in.

### 9.3 When canonical is None

Edge case: carry-forward into a period that has no canonical row
(rare, only if recurrence has not yet generated it AND the skip-rule
fix is not deployed). Click default falls back to the first carried
row's quick-edit. The drill-down UI shows all members.

Once the skip-rule fix in section 7 is in place, this case is
self-healing: subsequent recurrence runs will populate the canonical.

---

## 10. Entry recording in combined view

### 10.1 Current flow

`app/templates/grid/_transaction_entries.html` posts new entries to
`POST /entries/{txn_id}`. The form's hidden `txn_id` is set by the
caller (the cell partial when it renders the popover).

`app/routes/entries.py:_get_accessible_transaction` (lines 33-64)
enforces ownership and (for companion users) `companion_visible`
template requirement.

### 10.2 Combined-view default

For multi-member envelopes, the form's `txn_id` is set to
`envelope.canonical_id`. New entries land on the canonical row; they
count toward `envelope_spent` exactly as the math requires.

### 10.3 Manual targeting (advanced)

A user could navigate directly to a carried row's entries page and add
entries there. Entries on a carried row reduce the leftover but do not
increment envelope_spent. This is technically possible in the current
data model and remains possible after the change.

Mitigation if it matters: gate carried-row entries behind a confirm
("This entry will be applied to the carried-forward portion. Are you
sure?"). Defer until requested.

### 10.4 Companion visibility edge case

If a template is set `companion_visible = False` AFTER carry-forward
already placed a row on the wife's companion view, the carried row
remains visible (the join in `companion_service.get_visible_transactions`
is on the current template state). Routing entries to canonical
preserves the existing per-row access checks; the companion either has
or does not have access to canonical based on current template state.

This was always true for any post-carry-forward visibility change. No
new risk from envelope view.

---

## 11. Mobile and companion considerations

### 11.1 Mobile grid

`app/templates/grid/_mobile_grid.html` duplicates the desktop grid's
matching loop verbatim. Any envelope grouping helper must be wired into
both templates. Refactoring to a shared partial would help DRY but is a
larger refactor than this work needs to do. Pragmatic plan: copy the
envelope rendering into the mobile template, file a follow-up to
deduplicate later.

### 11.2 Companion view

`app/templates/companion/index.html` and `_transaction_card.html`
render one card per transaction. Refactor:

1. `companion_service.get_visible_transactions` returns a list of
   transactions (current behavior, unchanged).
2. The route or template uses the same envelope helper to group by
   `(template_id)` per period.
3. Render one envelope card per group, with combined progress.
4. Drill-down on the card shows individual member status if needed
   (simplest first iteration: skip drill-down on companion; the wife
   does not normally edit per-row status).

### 11.3 Card click vs entry recording

On the card, the wife's primary action is "record an entry." The card
target's `txn_id` is set to canonical's id. New entries land on
canonical. Husband-only actions like "mark Done" drop down through
the desktop grid only.

---

## 12. Downstream service issues

### 12.1 Dashboard upcoming bills

- **Severity**: high. Dashboard is a daily-use surface.
- **Behavior today**: shows duplicate bills for any envelope with
  carried siblings.
- **Fix**: apply the envelope helper to `_get_upcoming_bills` output.
  Group bills by `(period_id, template_id)`, render one bill per
  envelope with combined estimated_amount when desired (or the
  canonical's amount only -- design call). Update
  `dashboard_service.txn_to_bill_dict` to accept an envelope and emit
  one dict.
- **Effort**: small, ~50 to 80 LoC plus tests. Mostly a refactor of
  existing helper.
- **Recommendation**: include in the same sprint as the grid envelope
  view. Same data contract, same helper, similar template change.

### 12.2 Budget variance

- **Severity**: medium. Variance reports are reviewed periodically,
  not daily.
- **Behavior today**: produces one `TransactionVariance` per row, so a
  carry-forward sibling pair shows as two variance entries.
- **Fix**: aggregate variance at the envelope level. The
  `_build_txn_variance` function takes a single Transaction; an
  `_build_envelope_variance` function would take an `EnvelopeCell` and
  produce one combined variance entry.
- **Edge case**: variance only reports settled rows (`actual` only
  meaningful when row is Done). For envelope-style items, the wife
  often leaves rows Projected (entries against Projected canonical
  are treated as actual spend by the envelope helper). Variance may
  need to surface envelope_spent vs envelope_budget too, not just
  actual vs estimated. Define carefully.
- **Effort**: medium, ~150 LoC plus tests.
- **Recommendation**: separate phase. Do after the grid envelope
  ships and you have verified the envelope mental model in practice.

### 12.3 Year-end summary

- **Severity**: low. Already mostly correct because it sums entries,
  which are real spend.
- **Behavior today**: probably fine for actuals; estimate side
  may inflate if any total uses sum(estimated_amount).
- **Fix**: review the year-end report carefully when reviewing actual
  output for a year that has carry-forwards. If totals look right,
  no change. If estimates are inflated, apply envelope grouping.
- **Effort**: small audit, possibly zero code change.
- **Recommendation**: defer. Audit during normal year-end review.

### 12.4 Other consumers (out of scope, listed for awareness)

- `app/services/dashboard_service.py:332-396` -- cash runway and
  alerts. Sums per-txn `effective_amount`. With carried rows, runway
  reduces by the full $200 obligation, which is consistent with
  `balance_calculator`. No change.
- `app/services/csv_export_service.py` -- exports raw rows. No
  envelope grouping needed; CSV consumers want detail.
- `app/services/scenarios_service.py` (if it exists) -- scenario
  comparison. Should reflect the actual data; no envelope grouping
  needed there either.

---

## 13. Sprint planning recommendation

The user's stated framing is correct: this is a mini-sprint or phase,
not a single commit. Recommend breaking the work into the following
phases. Each phase is independently shippable to production and yields
a coherent improvement.

### Phase 1: Schema and data layer

**Scope**: schema column, carry-forward writer, transfer-service kwarg,
recurrence-engine skip-rule fix, model updates.

- Migration: `carried_from_period_id` on transactions and transfers.
- `app/models/transaction.py`, `app/models/transfer.py`.
- `app/services/carry_forward_service.py` -- write the column.
- `app/services/transfer_service.py` -- accept and propagate kwarg.
- `app/services/recurrence_engine.py`, `app/services/transfer_recurrence.py`
  -- skip-rule update.
- Tests: column set on carry, null on rule-generated, skip rule honored.

**Effort**: ~150 LoC including tests. Risk: low (schema change is
nullable, no backfill; skip-rule change is precisely scoped).

**Ships value**: nothing visible to end users yet. Sets up the rest.
This phase is the foundation.

### Phase 2: Grid envelope display (desktop and mobile)

**Scope**: aggregation helper, cell partial refactor, grid and mobile
templates, partial-swap DOM id change in route handlers.

- New module `app/services/grid_aggregation.py` with `EnvelopeCell` and
  `build_envelope_cells`.
- `app/routes/grid.py` -- call helper after `txn_by_period`.
- `app/templates/grid/grid.html`, `_mobile_grid.html` -- per-envelope
  loop.
- `app/templates/grid/_transaction_cell.html` -- combined display
  branch.
- `app/routes/transactions.py:_render_cell_response` and three handlers
  in `app/routes/transfers.py` -- envelope-level rebuild and DOM id.
- Tests: aggregation math, cell rendering, quick-edit re-render.

**Effort**: ~500 LoC including tests. Risk: medium (partial-swap DOM id
change is the largest correctness risk).

**Ships value**: husband and wife both see envelope rollover in the
grid. Largest user-visible improvement.

### Phase 3: Companion view envelope

**Scope**: apply envelope helper to companion route or template; render
one card per envelope.

- `app/routes/companion.py` and/or `app/services/companion_service.py`
  to call the helper.
- `app/templates/companion/index.html`, `_transaction_card.html` --
  per-envelope rendering.
- Tests: companion sees one card per envelope; entries default to
  canonical.

**Effort**: ~150 LoC including tests. Risk: medium (companion view is
wife's primary surface; tested manually as well as automated).

**Ships value**: wife's primary interface gets the same envelope
display the husband sees.

### Phase 4: Dashboard upcoming bills

**Scope**: group bills by envelope in `_get_upcoming_bills`; update
the bill dict shape if needed.

- `app/services/dashboard_service.py` -- group bills.
- `app/templates/dashboard/_bill_row.html` -- adjust if needed.
- Tests: dashboard shows one bill per envelope post-carry-forward.

**Effort**: ~80 LoC including tests. Risk: low.

**Ships value**: dashboard stops showing duplicate bills. High-impact
small change.

### Phase 5: UX badge for carry-forward rows

**Scope**: visual indicator in the cell when the envelope has any
carried members. Tooltip "Carried forward from <period>."

- `_transaction_cell.html` -- icon and tooltip when `has_carried`.
- Optional: same on companion card.
- Tests: rendering only.

**Effort**: ~30 LoC. Risk: trivial.

**Ships value**: explicit visual disambiguation for any envelope that
has rolled-forward content.

### Phase 6 (optional): Budget variance grouping

**Scope**: group variance rows by envelope; possibly extend
TransactionVariance shape to envelope-aware fields.

- `app/services/budget_variance_service.py` -- envelope grouping.
- Tests.

**Effort**: ~150 LoC. Risk: medium (variance has its own view
templates; need to confirm the envelope shape fits).

**Ships value**: variance reports show one row per envelope. Defer
until the grid view is in production and you have decided whether the
variance display matters.

### Phase 7 (optional): Year-end summary audit

**Scope**: review year-end output for years with carry-forwards.
Confirm actuals and estimates are correct. Fix only if needed.

**Effort**: audit only, possibly zero code.

### Phase 8 (optional): Secondary envelope subtotal row

**Scope**: add a "Envelope total" subtotal row alongside the existing
"Period total" row, computed by summing envelope_budget across the
period.

- `app/routes/grid.py` -- aggregate.
- `app/templates/grid/_balance_row.html` (or wherever subtotals render).

**Effort**: ~30 LoC. Risk: trivial.

**Ships value**: closes the cell-vs-subtotal divergence visually.
Defer until you have lived with the divergence and decided it bothers
you.

---

## 14. Risk assessment

| Phase | Effort (LoC) | Risk | Reversibility |
|---|---|---|---|
| 1. Schema and data layer | 150 | Low | High (migration down works, behavior unchanged for users) |
| 2. Grid envelope display | 500 | Medium | Medium (partial-swap DOM id change touches multiple route handlers) |
| 3. Companion view | 150 | Medium | Medium (wife's primary view; manual testing matters) |
| 4. Dashboard bills | 80 | Low | High |
| 5. UX badge | 30 | Trivial | High |
| 6. Variance grouping | 150 | Medium | Medium |
| 7. Year-end audit | 0 to 50 | Low | High |
| 8. Envelope subtotal | 30 | Trivial | High |

Total committed work (phases 1 to 5): ~910 LoC.
Total optional work (phases 6 to 8): ~180 to 230 LoC.

---

## 15. Open questions to resolve before phase 2

These are decisions the implementation plan asserts but the user has
not explicitly confirmed. Worth re-confirming when planning the sprint:

1. **Cell vs subtotal divergence**: Option D (envelope cell at $135,
   subtotal at $200) acceptable? Or do we want the secondary envelope
   subtotal row in the same phase?
2. **Drill-down UI on multi-member envelopes**: simple click-opens-
   canonical's-quick-edit, or richer envelope popover with all members
   listed?
3. **Companion drill-down**: do companion users need to see the
   individual member status, or is one card per envelope enough?
4. **Multi-hop provenance**: overwrite `carried_from_period_id` on
   each hop (recommended)? Or preserve original?
5. **Settled-carried math**: `max(actual, entries_sum)` for the spent
   portion (recommended)? Or stricter "actual when settled, entries
   when projected"?
6. **Manual entry on carried row**: leave as-is and document, or add a
   confirm dialog?
7. **Phase 4 timing**: bundle dashboard fix with phase 2, or ship it
   as a separate phase?

---

## 16. Reference: file-by-file change inventory

For sprint planning, here is the full list of files that any phase
might touch. Use this as a checklist.

### Schema and models
- `migrations/versions/<new>.py` -- add `carried_from_period_id`
- `app/models/transaction.py`
- `app/models/transfer.py`

### Services
- `app/services/carry_forward_service.py`
- `app/services/transfer_service.py`
- `app/services/recurrence_engine.py`
- `app/services/transfer_recurrence.py`
- `app/services/grid_aggregation.py` (new)
- `app/services/dashboard_service.py`
- `app/services/budget_variance_service.py`
- `app/services/year_end_summary_service.py` (audit only)
- `app/services/companion_service.py` (probably no change; helper
  consumed by routes/templates)

### Routes
- `app/routes/grid.py`
- `app/routes/transactions.py` (`_render_cell_response`)
- `app/routes/transfers.py` (3 cell-render handlers)
- `app/routes/companion.py`
- `app/routes/dashboard.py`

### Templates
- `app/templates/grid/grid.html`
- `app/templates/grid/_mobile_grid.html`
- `app/templates/grid/_transaction_cell.html`
- `app/templates/grid/_balance_row.html` (only if phase 8)
- `app/templates/companion/index.html`
- `app/templates/companion/_transaction_card.html`
- `app/templates/dashboard/_bill_row.html` (phase 4)

### Tests
- `tests/test_services/test_carry_forward_service.py`
- `tests/test_services/test_recurrence_engine.py`
- `tests/test_services/test_transfer_service.py`
- `tests/test_services/test_transfer_recurrence.py` (if exists)
- `tests/test_services/test_grid_aggregation.py` (new)
- `tests/test_services/test_companion_service.py`
- `tests/test_services/test_dashboard_service.py`
- `tests/test_services/test_budget_variance_service.py`
- `tests/test_routes/test_grid_regression.py`
- `tests/test_routes/test_transactions.py`
- `tests/test_routes/test_transfers.py`
- `tests/test_routes/test_companion.py` (if exists)
- `tests/test_routes/test_dashboard.py` (if exists)

---

## 17. Glossary

- **Canonical row**: the row representing this period's allocation for
  a given (template, period). `carried_from_period_id IS NULL`. Rule-
  generated by recurrence or manually edited in this period.
- **Carried row**: a row whose `pay_period_id` was changed by
  carry-forward. `carried_from_period_id IS NOT NULL`. Originally
  belonged to a prior period.
- **Override sibling**: any non-canonical row sharing
  `(template_id, pay_period_id, scenario_id)` with a canonical row.
  Today's data model permits override siblings only if `is_override =
  TRUE`.
- **Envelope**: a logical grouping of canonical plus carried rows for
  the same `(category_id, template_id, pay_period_id)`. Single rows
  also constitute single-member envelopes.
- **Envelope budget**: `canonical.estimated + Σ max(0, c.estimated -
  c.entries) for c in carried`.
- **Envelope spent**: entries on canonical (or actual_amount when
  canonical is settled).
- **Leftover**: `max(0, c.estimated - c.entries)` for a carried row;
  the unspent portion that rolls forward.
- **Cash-flow obligation**: `Σ effective_amount` across all rows in
  the envelope. What `balance_calculator` and the period subtotal use.
- **Multi-hop carry-forward**: a row that has been carried more than
  once across periods.
