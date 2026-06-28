# Follow-up: kind-correct grid balances + interest line

**Status:** IMPLEMENTED on `dev` 2026-06-28 (scope narrowed to INTEREST -- see below). Not yet PR'd
to `main`. Implementation plan + per-commit log:
`implementation_plan_kind_correct_grid_interest.md`.
**Enabled by:** the Level-1 balance-at seam (`app/services/balance_at.py`); see
`implementation_plan_level1_balance_seam.md`.

**What shipped (4 commits on `dev`):**
- `6f0610b` -- the `balance_at.grid_balance_view` seam (kind-aware view: interest-accrued balance +
  per-period accrual for INTEREST, cash-flow for every other kind).
- `d6eddd8` -- grid desktop + mobile: the read-only "Interest" row + interest-accrued projected
  balance for an INTEREST grid account.
- `65fae78` -- the dashboard obligations panel routes through the same seam view.
- (final) -- docs + full-suite gate (6394 passed, pylint 10.00).

**Scope decision (revised 2026-06-28):** grid accrual is **INTEREST only**. The original brief below
framed PLAIN / INTEREST / INVESTMENT / APPRECIATING as one "cash basis + increment" family; that is
true only for PLAIN and INTEREST. INVESTMENT and APPRECIATING are projection-driven (growth /
appreciation engine, NOT a transaction sum), so an ad-hoc grid row would not move their projected
balance -- the same projection-vs-transaction mismatch that excludes loans. The developer chose to
leave INVESTMENT / APPRECIATING / AMORTIZING on the cash-flow view (their modeled value lives on the
/savings cockpit + detail pages). The implementation plan's known-limitation note records the full
rationale. The historical brief below is preserved as written on 2026-06-27.

**Size:** a multi-surface feature comparable to the whole Level-1 reroute (Commits 4-8), NOT a
behavior-preserving reroute.

## Motivation

After Level-1 Commit 8, the single-account cash-flow surfaces (the budget grid, the obligations
panel, the calendar, the checking/interest detail pages) read an account's balance through the
seam's **cash-flow view** (`balance_at.cash_balance_map` / `cash_balance_at`): a pure transaction
running-balance, with no per-kind dispatch. For a checking account that is exactly correct.

But the grid account is not always checking. `resolve_grid_account` falls back to the user's
`default_grid_account_id` or "first active account of any type," and `resolve_analytics_account`
(calendar) accepts an explicit `account_id`. So a user can point the grid (or calendar) at an
**interest-bearing account** (HYSA / Money Market / CD / HSA). For such an account the cash-flow
view **ignores interest accrual**, so the grid's projected balance understates the real balance,
and the gap compounds across the ~2-year projection horizon.

Worked example a user would see -- a $10,000 HYSA at 4.00% APY (daily compounding) with a recurring
+$200.00/period transfer in, no expenses:

| Period | Cash-flow view (today's grid) | Interest-accrued (real) | Hidden delta |
|---|---|---|---|
| P0 | 10,200.00 | 10,215.66 | +15.66 |
| P1 | 10,400.00 | 10,431.65 | +15.99 |
| P2 | 10,600.00 | 10,647.97 | +16.32 |
| P3 | 10,800.00 | 10,864.63 | +16.66 |

The understatement is ~$15-17/period and growing (~$800 on $10,000 over two years, ~8%). The
interest IS already shown on the dedicated `/accounts/<id>/interest` page (which uses the
kind-correct `balance_map`); the grid simply does not surface it.

## Decision history

During Commit 8 the developer considered routing the grid to the **kind-correct** seam so an HYSA
grid account would accrue interest. That is "Option B'": kind-correct grid balances **plus an
interest line** so the rows still reconcile. The decision was to **ship Option A now** (the
cash-flow view -- behavior-preserving, completes the fence) and **defer Option B' as this
feature**, because B' is a multi-surface feature, not a behavior-preserving reroute.

Worked examples the developer reviewed for the three candidates:

- **Option A (shipped):** grid shows the cash-flow view (no interest). Balance row reconciles with
  the transaction-based subtotal row. Identical to pre-Level-1 behavior.
- **Option B' (this feature):** grid shows the interest-accrued balance AND adds an Interest row so
  the rows reconcile (`Net Cash Flow + Interest = balance delta`).
- A naive "just route to `balance_map`" (rejected): the balance row would accrue interest while the
  subtotal row stayed transaction-based, **breaking the grid's invariant**
  `balances[p] - balances[p-1] == subtotals[p].net` (the +$15.99 at P1 would appear with no row
  explaining it -- it reads as a bug).

## Why this is a feature, not a reroute

1. **Interest is not a transaction; it is computed.** The grid is a *transaction* grid -- every row
   maps to an editable transaction, and mark-paid, carry-forward, entries, mobile cards, and the
   command palette all operate on transactions. An interest line is a new **synthetic, read-only**
   row type none of those interactions apply to.
2. **The grid account can be any kind.** "Kind-correct" really means "explain the per-period balance
   change for every kind the grid can host": interest (HYSA), principal-paydown + interest-charged
   (loan), contributions + growth (investment), appreciation (property). An interest line only fixes
   the interest case; the other kinds break the reconciliation invariant with no explanatory row.
3. **The invariant must hold.** `balances[p] - balances[p-1] == subtotals[p].net` is locked by
   `tests/test_integration/test_cross_page_balance_equality.py::TestSubtotalReconciliation` and the
   grid static guards. If the balance row accrues interest, either the subtotal must grow an interest
   component or a separate Interest row must make the change visible.

## Blast radius (B' done consistently across surfaces)

**Backend**
- `app/services/balance_at.py`: interest-bearing grid accounts read the kind-correct `balance_map`;
  every other kind keeps `cash_balance_map`. Plus `stale_anchor_warning` for interest accounts
  (`balance_map` drops it -- needs an accessor or a richer cash result).
- `app/services/balance_resolver.py`: `PeriodSubtotal` + `_subtotal_from_transactions` +
  `period_subtotals` / `period_subtotal` grow an interest component (the type is grid-only, so this
  is bounded -- but it changes what "Net Cash Flow" means). Only if interest folds into the subtotal;
  the recommended first cut avoids this (see below).
- `app/routes/grid.py`: five functions thread per-period interest (`_build_grid_subtotals`, `index`,
  `balance_row`, `subtotal_rows`, `mobile_this_period_summary`, `_build_plan_view`); the two HTMX
  refresh endpoints must recompute it.
- `app/services/obligations_projection.py`: must accrue interest too -- it is "byte-identical to the
  grid footer" by design.
- `app/services/calendar_service.py`: a HYSA viewed in the calendar should accrue interest at
  month-end for consistency with the grid.

**Templates** (the interest row is conditional on an interest-bearing account): `grid/grid.html`,
`grid/_subtotal_rows.html` (both render modes + the self-refresh), and the mobile set
`grid/_mobile_tp_summary.html`, `grid/_mobile_this_period.html`, `grid/_mobile_plan.html`.

**Tests**: grid routes + partials, obligations, calendar, and the cross-page oracle (which currently
has no interest-bearing surface -- add one).

## Recommended approach (the clean flip the seam enables)

The Level-1 seam already exposes **both** `cash_balance_map` (today's grid) and `balance_map`
(kind-correct, interest-accrued), and the interest figure is available from the fenced
`net_worth_kernel.interest_by_period_for_account`. So the feature is a presentation change plus a
per-kind branch in the grid, NOT a producer hunt:

1. For an interest-bearing grid account, flip the balance source from `cash_balance_map` to
   `balance_map`.
2. Thread `net_worth_kernel.interest_by_period_for_account` for the per-period interest.
3. Add a conditional, read-only Interest row to the grid templates (desktop + mobile).
4. Decide the loan/investment/property grid-account policy (see open questions).
5. Test (grid/obligations/calendar/oracle).

**Lightweight first cut** (bounds the blast radius to interest accounts):
- Interest-accrued balance + one read-only "Interest" row for interest-bearing grid accounts only.
- NO `PeriodSubtotal` change: keep "Net Cash Flow" transaction-based. The user sees Net Cash Flow
  AND Interest as two rows, and the balance delta equals their sum -- every change is explained by a
  visible row without redefining "Net Cash Flow."
- loan/investment/property grid accounts stay on `cash_balance_map` so they do not break.

The seam guarantees the interest-accrued numbers already agree to the cent with the year-end summary,
the savings cockpit, and the net-worth trend (all kind-correct), so this feature cannot reintroduce
a cross-page divergence.

## Open decisions for the planner

1. **Non-cash, non-interest grid accounts (loan / investment / property).** Restrict the grid to
   cash/interest kinds, keep them cash-basis (recommended first cut -- they are degenerate grid
   accounts), or build their synthetic paydown / growth / appreciation rows (much larger)?
2. **Interest placement.** Fold interest into the Net Cash Flow subtotal (changes its meaning) or
   keep it a separate read-only row (recommended -- preserves "Net Cash Flow = the cash flow you
   control")?
3. **Scope.** Interest-bearing accounts only (recommended first cut) vs all kinds.
4. **`stale_anchor_warning` on the interest path.** `balance_map` drops the flag the grid banner
   reads; decide between a kernel accessor for it or reusing `cash_balance_map`'s flag for the
   interest path.
