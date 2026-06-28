# Implementation plan: kind-correct grid balances + accrual row

**Status:** Planned (2026-06-27). Not started.
**Feature brief:** `followup_kind_correct_grid_interest.md` (this is its implementation plan).
**Enabled by:** the Level-1 balance-at seam (`app/services/balance_at.py`), shipped to prod in PR #45.
**Branch:** `feat/kind-correct-grid-accrual` off `dev`.

## What this delivers

When a user's grid account (or obligations default account) is **not** a plain checking
account, the grid currently shows a pure transaction running-balance that ignores the account's
modeled growth -- so a HYSA grid account understates the real balance by ~$15-17/period, compounding
to ~8% over the 2-year horizon. This feature makes the grid footer (and the dashboard obligations
panel) show the **kind-correct** balance for interest-bearing, investment, and property accounts,
and adds one **read-only accrual row** that explains the part of each period's balance change the
transactions do not -- so every row still reconciles.

## Decisions locked with the developer (2026-06-27)

1. **Surfaces:** grid **+** obligations panel. The calendar is **deferred** (it is a scalar
   month-end value over a day-cell grid; interest has no natural row there, and it defaults to
   checking -- a distinct, harder design). See "Out of scope".
2. **Placement:** a **separate read-only accrual row**. "Net Cash Flow" keeps meaning
   exactly what it does today (income minus the expenses you control); the subtotal producer
   (`period_subtotals` / `PeriodSubtotal`) is **not** touched.
3. **Kinds (revised 2026-06-28):** the accrual row is shown for **INTEREST only**. The original
   "all kinds" intent was narrowed after the Commit 1 review established that only INTEREST has a
   *transaction-sum* balance (so a row typed on the editable grid flows into it). **INVESTMENT and
   APPRECIATING are projection-driven** (growth / appreciation engine, not a transaction sum), so
   an ad-hoc grid row would not move their projected balance -- the same projection-vs-transaction
   mismatch that excludes loans. They are therefore **left on the cash-flow view too**, exactly
   like loans (the developer chose this over including them as read-only projection views). PLAIN
   shows no row (its increment is identically zero). See the known-limitation note below.
4. **Loans (AMORTIZING):** **left on the cash-flow view** -- out of scope. A loan's balance is
   schedule-driven (principal paydown) while its grid "transactions" are the payment transfers
   recorded as income; the two have opposite sign and cannot reconcile with a single increment
   row. A correct loan grid needs a Principal-Paid / Interest-Charged decomposition that replaces
   the income/expense subtotals -- a separate, larger feature that overlaps the existing loan
   detail and debt-strategy pages. Grid + obligations both keep showing loans exactly as today
   (so they stay consistent with each other).

## The unified mechanism

The four non-loan kinds share one shape: `balance = cash basis (anchor + your transactions) + a
modeled increment layered on top`. The per-period accrual is therefore the **modeled premium over
the cash basis, deltaed period to period**:

```
premium[p]   = round_money(kind_correct_balance[p]) - round_money(cash_basis_balance[p])
increment[p] = premium[p] - premium[previous present period]   (premium = 0 before the anchor)
```

where `kind_correct_balance` is the seam's `balance_map` (interest-accrued / growth-modeled /
appreciated) and `cash_basis_balance` is the seam's `cash_balance_map` (pure transaction
running-balance).

**Why it reconciles exactly, by construction.** The displayed balance row is
`round_money(kind_correct_balance[p])`. The displayed Net Cash Flow is `period_subtotals[p].net`.
The locked E-25 invariant (`TestSubtotalReconciliation`) guarantees
`round_money(cash_basis[p]) - round_money(cash_basis[q]) == period_subtotals[p].net`. Substituting:

```
displayed_balance[p] - displayed_balance[q]
  = (round(kc[p]) - round(kc[q]))
  = (round(cash[p]) - round(cash[q])) + increment[p]      (definition of increment)
  = period_subtotals[p].net + increment[p].               (E-25)
```

So `balance_delta == NetCashFlow + increment` holds to the cent **whenever the increment is shown**,
regardless of the kernel's internal rounding -- the increment is computed from the same rounded
balances the grid displays. The seam's `grid_balance_view` implements this premium math, but per
Decision 3 the grid **only surfaces it for INTEREST**; every other kind takes the cash-flow walk
(empty `increments`). The table below shows what the premium would be per kind for reference, with
the **wired** column marking what the grid actually shows:

| Kind | cash basis | kind-correct | increment | wired on grid? |
|---|---|---|---|---|
| PLAIN | `balances_for` | `balances_for` (identical) | **0** | cash-flow, no row |
| INTEREST | no-interest txn balance | interest-accrued | **Interest** earned | **YES -- Interest row** |
| INVESTMENT | contribution txns (`investment_base`) | growth-modeled | growth (+ contributions) | no -- cash-flow (projection-driven; see limitation) |
| APPRECIATING | flat anchor (no txns) | appreciated value | appreciation | no -- cash-flow (projection-driven; see limitation) |
| AMORTIZING | (cash-flow view; balance is schedule-driven) | -- | -- | no -- cash-flow |

Worked example -- a $10,000 HYSA at 4.00% APY, +$200/period transfer in, no expenses:

| Period | Total Income | Net Cash Flow | Interest (new row) | Projected Balance | check: dBal = net + interest |
|---|---|---|---|---|---|
| P0 | 200.00 | 200.00 | 15.66 | 10,215.66 | 215.66 = 200 + 15.66 OK |
| P1 | 200.00 | 200.00 | 15.99 | 10,431.65 | 215.99 = 200 + 15.99 OK |

## Architecture

One new seam entry composes the seam's two existing views; nothing below the seam changes.

```python
# app/services/balance_at.py

@dataclass(frozen=True)
class GridBalanceView:
    """Kind-aware cash-flow-surface projection for the grid + obligations panel.

    balances:            period_id -> round_money balance (kind-correct for INTEREST /
                         INVESTMENT / APPRECIATING; the cash-flow running-balance for PLAIN
                         and AMORTIZING -- byte-identical to today for those two).
    stale_anchor_warning: the cash producer's flag (always from the cash walk).
    increments:          period_id -> round_money per-period accrual (the modeled premium
                         delta). EMPTY for PLAIN and AMORTIZING (no accrual row).
    """
    balances: OrderedDict
    stale_anchor_warning: bool
    increments: OrderedDict


def grid_balance_view(account, scenario, periods, *, amount_overrides=None) -> GridBalanceView:
    # _require_scenario(scenario)
    # cash = cash_balance_map(...)               # cash basis + stale flag, ALWAYS
    # kind = classify_account(account)
    # if kind in {INTEREST, INVESTMENT, APPRECIATING}:
    #     kc = balance_map(..., amount_overrides=amount_overrides)
    #     if kc is not None:
    #         build rounded balances + increments (premium delta) using cash as the baseline
    #         return GridBalanceView(rounded_kc, cash.stale_anchor_warning, increments)
    # return GridBalanceView(cash.balances, cash.stale_anchor_warning, OrderedDict())
```

Key properties:

- **No engine, kernel, or calculator change.** `grid_balance_view` only calls the seam's own
  `cash_balance_map` and `balance_map`. The stale flag comes from the cash walk (which we run
  anyway for the increment baseline), so the interest path never needs to surface it.
- **No W9906 fence change.** `grid_balance_view` is a seam entry calling seam entries; consumers
  call only the seam. No producer name is exposed and none is added to `_BALANCE_PRODUCERS`.
- **Cost.** PLAIN and AMORTIZING grid accounts do exactly one walk (unchanged from today). Only an
  INTEREST / INVESTMENT / APPRECIATING grid account does the second (kind-correct) walk -- the rare
  case. `amount_overrides` is threaded to both walks so the live-projected-income map the grid
  builds once is honored, and the cash baseline matches the kind-correct base (so the increment is
  pure modeled growth, not an override mismatch).
- **Dispatch lives once.** The grid's four balance call sites and the obligations panel all read
  the same `grid_balance_view`, so "which view does a cash-flow surface show for which kind" is
  defined in exactly one place (SOLID/DRY).

## Commits

Five atomic commits. Each is independently green and gets an **adversarial code review (the
`code-reviewer` subagent on the staged diff) before committing**; findings are fixed before the
commit lands. Targeted tests per commit; the full suite is the final gate in Commit 5 (per the
"targeted per change, full suite as the final gate" testing rule, and run alone -- the shared test
DB on :5433 flakes under concurrent load).

### Commit 1 -- Seam: `grid_balance_view` + `GridBalanceView`

**Status: DONE (2026-06-28, branch `feat/kind-correct-grid-accrual`, not yet committed).**
`balance_at.GridBalanceView` + `grid_balance_view` + `_accruing_grid_view` added; 8
`TestGridBalanceView` tests (44/44 in the file), `pylint app/` 10.00, `balance_resolver` untouched.
Two adversarial code-review rounds (`code-reviewer`); verdict ship. Deviations from the entry below,
all review-driven:
- **Scope narrowed to INTEREST only** (Decision 3 revised) -- dispatch is
  `classify_account(account) is AccountProjectionKind.INTEREST`, no `_ACCRUING_KINDS` set;
  INVESTMENT / APPRECIATING now take the cash-flow walk (their tests assert that). See the
  known-limitation note for why.
- **Income-basis fix (review M1):** for INTEREST, `amount_overrides=None` is normalized to `{}` so
  both the cash baseline and the interest walk use stored income (the interest producer does not
  auto-build a live map from `None` the way `balances_for` does) -- a pure premium. A caller threads
  a live map for live income (the grid will). Added `test_interest_increment_pure_when_live_differs_from_stored`.
- **Degrade branch (review L2):** the `kc_balance is None` path (anchor-cache-divergence prefix)
  resets the premium baseline; covered by `test_accruing_kc_none_degrades_to_cash` (monkeypatch, the
  no-map state is NOT-NULL-unreachable for a real account).
- Also corrected `balance_map`'s own docstring (it wrongly claimed `None` always builds live
  overrides -- false for the interest sub-path).

**Goal:** add the kind-aware view; no consumer rerouted yet (zero behavior change on any screen).

- `app/services/balance_at.py`: add the frozen `GridBalanceView` dataclass and the
  `grid_balance_view` entry per "Architecture". Reuse the existing `round_money`, `classify_account`,
  `AccountProjectionKind` imports (all already present). Full docstrings; the increment formula and
  its by-construction reconciliation documented on the function.
- `tests/test_services/test_balance_at.py`: a `TestGridBalanceView` class.
  - PLAIN: `balances == cash_balance_map(...).balances`; `increments == {}`; stale flag passes
    through.
  - AMORTIZING (loan): same as PLAIN -- cash-flow view, `increments == {}` (status quo lock).
  - INTEREST: `balances == {pid: round_money(balance_map(...)[pid])}`; for every adjacent present
    pair `round(kc[p]) - round(kc[q]) == (round(cash[p]) - round(cash[q])) + increments[p]`
    (the by-construction reconciliation); and a magnitude band -- the summed increment over the
    horizon is positive and within a couple cents of
    `sum(net_worth_kernel.interest_by_period_for_account(...))` (proves it is real interest, not a
    residual absorbing a bug). Use a seeded $10k HYSA @ 4% with a recurring contribution; assert the
    P0/P1 numbers from the worked example with a hand-computed comment.
  - INVESTMENT and APPRECIATING: `balances == round(balance_map)`; reconciliation holds; increment
    is non-zero where growth/appreciation occurs.
  - `scenario=None` -> `ValueError` (the seam's fail-loud contract).
- **Adversarial review focus:** the anchor-period edge (premium baseline = 0 before the anchor, so
  `increments[anchor] == round(kc[anchor]) - round(cash[anchor])`); `balance_map` returning `None`
  falls through to the cash view; rounding is applied to the displayed balances *before* the
  increment subtraction (else the displayed rows would not reconcile); `amount_overrides` threaded to
  both walks; Decimal-only, no float; Flask-free.
- **Gates:** `pylint app/ --fail-on=...` clean; `./scripts/test.sh tests/test_services/test_balance_at.py -v` green.

### Commit 2 -- Grid desktop + mobile: kind-correct balance + accrual row

**Status: DONE (2026-06-28, dev) -- MERGED with Commit 3 (mobile).** Desktop and mobile could not
be split: `index` computes ONE `balances` map that grid.html renders for both the desktop table and
the mobile cards, so making it interest-accrued affects both at once -- a desktop-only commit would
leave the mobile Plan tab showing accrued balances with cash-only subtotals and no Interest line (a
reconciliation gap). So this commit does both surfaces. Deviations from the entry below, all sound:
- **No `accrual_label` helper / per-kind label.** Scope is INTEREST only (Decision 3), so the row
  label is the static string "Interest", hardcoded in the templates like the sibling "Net Cash
  Flow" / "Projected End Balance" labels -- no route-side label computation.
- **`_build_grid_balances` returns `(grid_view, anchor_balance)`**, not a 4-tuple: threading the
  cohesive `GridBalanceView` (balances + increments + stale) keeps `_build_plan_view` at <= 5 args
  (the bundle-a-cohesive-concept rule) and lets both maps be sliced symmetrically inside it.
- **Income-basis threading:** `balance_row` and `mobile_this_period_summary` build + thread the live
  override map via a new shared `_grid_amount_overrides` helper, so an interest account's refreshed
  figures use live income (matching the full render) rather than the stored estimate
  `grid_balance_view` falls back to on a bare None.
- **Mobile:** the Interest bar is added to `_mobile_tp_summary.html` (covers the This-Period tab via
  its include + the refresh endpoint) and the Plan recap gains an "Interest" figure via
  `plan_increments`. 5 route tests (desktop render, balance-row refresh, mobile summary refresh,
  PLAIN-absent x2); 207 in test_grid.py, pylint 10.00.

**Goal:** the desktop grid footer shows the kind-correct balance and the accrual row for a
non-loan, non-plain grid account.

- `app/routes/grid.py`:
  - `_build_grid_balances` -> call `balance_at.grid_balance_view`; return
    `(balances, stale_anchor_warning, anchor_balance, increments)`. Update its `index` caller.
  - `index` -> pass `increments` and an `accrual_label` to the template.
  - `balance_row` (HTMX) -> `grid_balance_view`; render the tfoot with the accrual row.
  - New `_accrual_row_label(account) -> str | None`: maps the account kind to a display label using
    the `account_type` boolean columns (ID-driven, never a name string) --
    `has_interest -> "Interest"`, `has_appreciation -> "Appreciation"`, investment ->
    `"Investment Growth"`, else `None`. (The route owns the display string; the service stays
    string-free.)
  - `subtotal_rows` -> **unchanged** (subtotals stay transaction-based; the accrual row lives in the
    tfoot, not the subtotal tbodies).
- `app/templates/grid/_balance_row.html`: add a conditional read-only accrual row as the **first
  tfoot row** (directly under Net Cash Flow, directly above Projected End Balance, keeping the
  reconciliation chain visually adjacent). Rendered only when `increments` is non-empty; styled
  muted/read-only; whole-dollar via the `money(val, cents=false)` macro like the sibling rows; label
  from `accrual_label`. Display only -- no computation in the template.
- `app/templates/grid/grid.html`: thread `increments` / `accrual_label` into the `_balance_row.html`
  include.
- `tests/test_routes/test_grid.py`:
  - HYSA grid account (use the shared `create_hysa_account` + `set_default_grid_account` helpers):
    the balance row shows interest-accrued balances; the accrual row is present, labeled "Interest",
    with per-period values; the rendered three rows reconcile.
  - PLAIN grid account: no accrual row; balances byte-identical to before (regression lock).
  - Loan grid account: cash-flow balances, no accrual row (status-quo lock).
- **Adversarial review focus:** the tfoot HTML-parser constraint (the existing `<template>` OOB
  wrapper for the stale-anchor banner must stay first; the accrual `<tr>` must sit inside `<tfoot>`
  so htmx's table-aware swap keeps the refresh cycle alive -- the load-bearing comment at the top of
  `_balance_row.html`); the label is ID-driven; CSRF/`hx-*` untouched; no `|safe` on any value.
- **Gates:** pylint clean; `./scripts/test.sh tests/test_routes/test_grid.py -v` green.

### Commit 3 -- Grid mobile + Plan tab: accrual line

**Goal:** parity for the mobile "This Period" summary and the mobile "Plan" tab.

- `app/routes/grid.py`:
  - `mobile_this_period_summary` (HTMX) -> `grid_balance_view`; pass `increments` + `accrual_label`.
  - `_build_plan_view` -> slice `increments` for the plan periods (alongside the existing
    `plan_balances` slice); pass through.
- `app/templates/grid/_mobile_tp_summary.html`, `_mobile_this_period.html`, `_mobile_plan.html`:
  add the read-only accrual line near the Net Cash Flow / Projected Balance block, guarded so the
  companion view (which passes no `increments`) renders nothing -- mirroring the existing
  `{% if subtotals is defined %}` / `{% if balances is defined %}` guards.
- `tests/test_routes/test_grid.py`: the mobile this-period summary and the plan window show the
  accrual line with correct values for a HYSA grid account; companion view unaffected.
- **Adversarial review focus:** the `is defined` guards so the companion/owner split is preserved;
  the plan slice uses the same full-map source as `plan_balances`.
- **Gates:** pylint clean; targeted `test_grid.py` green.

### Commit 4 -- Obligations panel: kind-aware balances

**Status: DONE (2026-06-28, dev).** `project_cash_flow` reads
`balance_at.grid_balance_view(...).balances`; an INTEREST default grid account's markers now accrue
interest (matching the grid footer), every other kind is byte-identical (PLAIN routes through the
cash path -- the existing flat-anchor / growing / negative-count tests still pass unchanged).
`now_balance` stays `resolve_anchor`. Income-basis note (documented in the code): with no override
map the interest path uses STORED income; for this markers-only summary panel that is acceptable (it
differs from the grid's live figure only for salary direct-deposited into a HYSA-as-default-grid with
a stale estimate -- the grid footer is the precise surface). 1 new test (HYSA markers accrue,
cross-checked vs the seam); 6 in test_obligations_projection.py, pylint 10.00.

**Goal:** the dashboard obligations panel reconciles with the grid footer for a non-loan default
grid account.

- `app/services/obligations_projection.py`: `project_cash_flow` -> read balances from
  `balance_at.grid_balance_view(...).balances` instead of `cash_balance_map(...).balances`. The
  panel shows markers only (no accrual row, no reconciliation), so it consumes `.balances` and
  ignores `.increments` / `.stale_anchor_warning`. `now_balance` stays `resolve_anchor` (the real
  current balance is the kind-independent starting point).
- `tests/test_services/test_obligations_projection.py` (or the existing obligations test module):
  HYSA default grid account -> the 12-month / end markers are interest-accrued (assert against the
  kind-correct `balance_map` values); PLAIN and loan default accounts -> markers unchanged
  (regression lock).
- **Adversarial review focus:** the marker selection (`_summarize_forward`) still reads from the
  returned map keys; `negative_period_count` semantics unchanged; loan default account stays
  byte-identical.
- **Gates:** pylint clean; targeted obligations test green.

### Commit 5 -- Cross-page reconciliation lock, full suite, manual verify, docs

**Goal:** lock the new three-way reconciliation in the cross-page oracle, prove the whole feature on
real data, and record it.

- `tests/test_integration/test_cross_page_balance_equality.py`: add a reconciliation test that for
  an INTEREST grid surface,
  `round(balance[p]) - round(balance[p-1]) == period_subtotals[p].net + increment[p]` for every
  period -- the kind-correct analogue of `TestSubtotalReconciliation`. Extend to INVESTMENT and
  APPRECIATING if the per-kind fixtures make it tractable; otherwise note the seam-level coverage in
  Commit 1 carries those and leave a one-line scope note (no silent gap).
- **Full suite** (`./scripts/test.sh`, run alone) -> expect `<N> passed`, show the count.
- `pylint app/` -> 10.00, all custom checkers, zero W9906.
- **Manual verification** (service-level against the prod-clone dev DB, 2FA disabled -- the pattern
  the Level-1 work used): with the Money Market (HYSA) as the grid account, confirm the grid footer
  and obligations markers show the interest-accrued figure and the accrual row reconciles; confirm a
  PLAIN checking grid account is byte-identical; confirm a loan grid account is unchanged. Both
  themes. Leave the dev DB pristine.
- Docs: flip `followup_kind_correct_grid_interest.md` to "Implemented" with the commit list; update
  the `project_grid_interest_kind_correct_feature` and `project_balance_at_seam_level1` memories.
- **Adversarial review focus:** the oracle test is non-tautological (it computes `increment`
  independently of the producer under test, or asserts against the engine interest figure);
  no fixture leakage between per-kind cases.
- **Gates:** full suite green (count shown); pylint 10.00; both-theme manual check done.

## Testing strategy

- **Service layer (Commit 1):** the seam's per-kind contract, including the worked-example numbers
  with hand-computed comments, the by-construction reconciliation, and the "increment is real
  interest" magnitude band.
- **Route layer (Commits 2-3):** assert rendered content -- the accrual row present/absent per kind,
  its label, its values, and that the displayed rows reconcile; PLAIN and loan regression locks.
- **Integration (Commit 5):** the cross-page three-way reconciliation oracle.
- Use existing fixtures/helpers (`create_hysa_account`, `set_default_grid_account`,
  `seed_periods_today` for current-period tests). Decimals from strings in every assertion.

## Known limitation surfaced in Commit 1 review: investment & property are projection-driven

The Commit 1 adversarial review (and a failing test) corrected a framing error in the
scope decision above. The unified-mechanism table called PLAIN / INTEREST / INVESTMENT /
APPRECIATING a single "cash basis + increment" family. That is true only for **PLAIN and
INTEREST**, whose balance is a *transaction sum* (plus interest) -- so a row the user types on
the grid flows into the projected balance.

**INVESTMENT and APPRECIATING are projection-driven, like loans.** Their kind-correct balance
comes from the growth / appreciation engine (anchor + modeled contributions, compounded), NOT
from summing the account's transactions: `_build_investment_balance_map` takes the growth
projection for post-anchor periods (`_merge_balance_sources`), and the appreciation map
compounds the anchor value. So an ad-hoc income / expense row the user enters on an investment
or property grid account lands in the cash basis but **not** in the kind-correct balance. The
seam's identity still reconciles algebraically (`balance delta == net + increment`, the
increment absorbing the offset), but:

- the **projected balance row is materially wrong** for that ad-hoc row (it ignores it), and
- the "Investment Growth" / "Appreciation" row reads oddly (it nets the ignored row out).

This is structurally the **same reason AMORTIZING (loans) is excluded** from grid accrual:
projection-driven balance vs transaction-driven grid rows. It is pre-existing `balance_map`
behavior (the `/savings` cockpit shows the same modeled value), not a seam defect -- the seam
composes `balance_map` faithfully and there is no live caller in Commit 1. INTEREST is
unaffected (its balance is a transaction sum, so grid edits flow in correctly).

**Decision (2026-06-28):** the developer chose **(a) -- exclude INVESTMENT and APPRECIATING from
grid accrual, like loans**. They stay on the cash-flow view (the grid shows their cash-basis
transaction running-balance, no accrual row), and their modeled growth / appreciation lives on the
/savings cockpit and detail pages where the projection is read-only. So grid accrual is **INTEREST
only**. Considered and rejected: (b) include them as read-only projection views with the caveat;
(c) fold ad-hoc rows into the projection (a large growth-engine change, out of scope). The seam
`grid_balance_view` dispatches on `classify_account(account) is INTEREST`; no `_ACCRUING_KINDS` set
is needed.

## Out of scope (explicit, with pointers)

- **Calendar interest.** The calendar's month-end balance is a scalar (`cash_balance_at`) over a
  day-cell grid with no natural accrual row, and it defaults to checking. A separate follow-up.
- **Loan grid decomposition.** A Principal-Paid / Interest-Charged grid for a loan account
  (replacing the income/expense subtotals) is a distinct, larger feature overlapping the loan
  detail and debt-strategy pages. Loans stay on the cash-flow view here.
- **Investment increment label precision.** The "Investment Growth" row lumps market growth +
  payroll-deduction contributions + employer match (the modeled change beyond visible transfers). A
  future refinement could split contributions from growth (needs the growth engine to expose the
  split).
- **`interest_detail` DRY cleanup.** The interest detail route still calls
  `net_worth_kernel.interest_by_period_for_account` directly; it is already seam-routed for
  balances, so this is a non-blocking tidy, not part of this feature.

## Risks

- **Rounding.** Mitigated by computing the increment from the same `round_money` balances the grid
  displays, so the cent-level reconciliation is exact by construction. The whole-dollar *display*
  (cents=false) may round a row by a dollar exactly as the grid already does today for balances vs
  Net Cash Flow -- accepted, pre-existing behavior.
- **Investment override consistency.** The increment is pure modeled growth only if the cash
  baseline uses the same live-override map as the kind-correct base. The grid builds
  `amount_overrides` via the same `live_amount_overrides(account, scenario, txns)` the producers
  build internally, so the maps are identical; Commit 1's INVESTMENT test guards this.
- **htmx tfoot swap.** The accrual row must live inside the `<tfoot>` and after the `<template>`
  OOB wrapper, or the refresh cycle dies (the load-bearing parser constraint already documented in
  `_balance_row.html`). Commit 2's review and the existing pinned-shape test guard it.
