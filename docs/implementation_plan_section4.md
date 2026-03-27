# Implementation Plan: Section 4 -- UX/Grid Overhaul

**Version:** 2.0
**Date:** March 27, 2026
**Prerequisite:** All Section 3 (Critical Bug Fixes) and Section 3A (Transfer Architecture Rework) changes are implemented, tested, and merged.
**Scope:** Tasks 4.1, 4.3 through 4.10 from `docs/project_roadmap_v4.md`. Task 4.2 has been superseded by Phase 3A-II.

---

## Grid Interaction Inventory

This inventory catalogs every HTMX interaction currently on the budget grid page (`app/templates/grid/grid.html`). Every task in this plan that modifies the grid must be verified against every item in this inventory.

### GI-1: Transaction Cell Click -> Quick Edit

- **Trigger element:** `<div class="txn-cell" hx-get="/transactions/<id>/quick-edit" hx-trigger="click" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_cell.html`, line 10-14)
- **HTTP request:** GET `/transactions/<txn_id>/quick-edit`
- **Server handler:** `transactions.get_quick_edit` in `app/routes/transactions.py:70`
- **Response:** HTMX fragment -- renders `grid/_transaction_quick_edit.html` with a single amount input and an expand button. For shadow transactions (transfer_id is not null), the same quick edit form is used -- the amount input works normally.
- **DOM update:** `hx-target="#txn-cell-<id>"`, `hx-swap="innerHTML"`. Replaces the content inside the `<div id="txn-cell-<id>">` wrapper.
- **Side effects:** None. No HX-Trigger headers.
- **Dependencies:** `_get_owned_transaction()` ownership check. Requires `txn` object as template context.

### GI-2: Quick Edit Submit -> Update Transaction

- **Trigger element:** `<form class="txn-quick-edit" hx-patch="/transactions/<id>" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_quick_edit.html`, line 7-11)
- **HTTP request:** PATCH `/transactions/<txn_id>` with form data `estimated_amount`.
- **Server handler:** `transactions.update_transaction` in `app/routes/transactions.py` (line 89 area). For shadow transactions, this handler detects `txn.transfer_id is not None` and routes the update through `transfer_service.update_transfer()`, which updates the parent transfer and both shadows atomically.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html` with updated amount.
- **DOM update:** `hx-target="#txn-cell-<id>"`, `hx-swap="innerHTML"`. Replaces the cell content with the updated display.
- **Side effects:** Response header `HX-Trigger: balanceChanged`. This triggers the balance row refresh (GI-10).
- **Dependencies:** `_update_schema` (Marshmallow validation), `_get_owned_transaction()`. Transfer service for shadow transactions.

### GI-3: Quick Edit Expand Button -> Full Edit Popover

- **Trigger element:** `<button class="txn-expand-btn" data-txn-id="<id>">` (in `grid/_transaction_quick_edit.html`, line 20-26). Click handled by delegated JS in `grid_edit.js`.
- **HTTP request:** JS `fetch('/transactions/<id>/full-edit')` (not HTMX -- uses vanilla fetch in `grid_edit.js:90`).
- **Server handler:** `transactions.get_full_edit` in `app/routes/transactions.py:80`. For shadow transactions, this handler detects `transfer_id`, loads the parent Transfer, and returns `transfers/_transfer_full_edit.html` instead of the transaction full edit form. The template receives `source_txn_id` so its HTMX target correctly points to `#txn-cell-<id>`.
- **Response:** HTML fragment -- renders `grid/_transaction_full_edit.html` (regular transactions) or `transfers/_transfer_full_edit.html` (shadow transactions). Injected into `#txn-popover` via JS.
- **DOM update:** Popover content set via `popover.innerHTML = html` in `showPopover()`. Popover positioned absolutely relative to `.grid-scroll-wrapper`.
- **Side effects:** `htmx.process(popover)` called to wire up HTMX attributes in the loaded content.
- **Dependencies:** `positionPopover()` needs `.grid-scroll-wrapper` and a `<td>` ancestor. Statuses list needed for dropdown.

### GI-4: Full Edit Form Submit -> Update Transaction

- **Trigger element:** `<form hx-patch="/transactions/<id>" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 16-19). For shadow transactions opened from the grid, the form in `transfers/_transfer_full_edit.html` uses `hx-target="#txn-cell-<source_txn_id>"` (line 19).
- **HTTP request:** PATCH `/transactions/<txn_id>` (regular) or PATCH `/transfers/<xfer_id>` (shadow via transfer form). With form data (estimated_amount, actual_amount, status_id, notes).
- **Server handler:** `transactions.update_transaction` (same as GI-2) or `transfers.update_transfer`. The transfer route's `_resolve_shadow_context()` helper detects the `source_txn_id` form parameter and returns `grid/_transaction_cell.html` when the request originated from a shadow cell.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **DOM update:** Targets `#txn-cell-<id>` with `innerHTML` swap.
- **Side effects:** `HX-Trigger: balanceChanged`. The `htmx:afterSwap` handler in `app.js:78-93` fires `save-flash` animation and closes the popover if the swap target is outside it.
- **Dependencies:** `_update_schema`, `_get_owned_transaction()`, statuses list.

### GI-5: Mark Done Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/mark-done" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 75-82 for expenses, line 112-120 for income as "Received"). For shadow transactions opened from the grid, the transfer full edit form's "Done" button is at `transfers/_transfer_full_edit.html` line 88-95.
- **HTTP request:** POST `/transactions/<txn_id>/mark-done` (regular or shadow via transaction route guard) or POST `/transfers/<xfer_id>/mark-done` (shadow via transfer form).
- **Server handler:** `transactions.mark_done` in `app/routes/transactions.py:189`. For shadow transactions, routes through `transfer_service.update_transfer()`. The transfer route's `mark_done` (line 574) also detects shadow context via `_resolve_shadow_context()`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html` with updated status badge.
- **DOM update:** Targets `#txn-cell-<id>` with `innerHTML` swap.
- **Side effects:** `HX-Trigger: gridRefresh`. This triggers `window.location.reload()` via the `gridRefresh` listener in `app.js:40-43`. Full page reload, not partial.
- **Dependencies:** Status lookup by name (`"done"` or `"received"`). `_get_owned_transaction()`.

### GI-6: Mark Credit Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/mark-credit" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 84-91). This button is hidden for shadow transactions: the template condition is `{% if not txn.transfer_id and txn.is_expense and txn.status.name == 'projected' %}`.
- **HTTP request:** POST `/transactions/<txn_id>/mark-credit`
- **Server handler:** `transactions.mark_credit` in `app/routes/transactions.py:251`. For shadow transactions, returns 400 (blocked -- shadows cannot be credited).
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`. Full page reload. Also calls `credit_workflow.mark_as_credit()` which creates a payback transaction in the next period.
- **Dependencies:** `credit_workflow` service, `_get_owned_transaction()`.

### GI-7: Undo Credit Button (in Full Edit Popover)

- **Trigger element:** `<button hx-delete="/transactions/<id>/unmark-credit" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 92-100). Also hidden for shadow transactions: condition is `{% elif not txn.transfer_id and txn.status.name == 'credit' %}`.
- **HTTP request:** DELETE `/transactions/<txn_id>/unmark-credit`
- **Server handler:** `transactions.unmark_credit` in `app/routes/transactions.py:272`. For shadow transactions, returns 400 (shadows never in credit status).
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`. Full page reload. Deletes the auto-generated payback transaction.
- **Dependencies:** `credit_workflow.unmark_credit()`, `_get_owned_transaction()`.

### GI-8: Cancel Transaction Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/cancel" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 102-110)
- **HTTP request:** POST `/transactions/<txn_id>/cancel`
- **Server handler:** `transactions.cancel_transaction` in `app/routes/transactions.py:293`. For shadow transactions, routes through `transfer_service.update_transfer()`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`. Full page reload.
- **Dependencies:** Status lookup by name `"cancelled"`, `_get_owned_transaction()`.

### GI-9: Empty Cell Click -> Quick Create

- **Trigger element:** `<div class="txn-cell txn-empty-cell" hx-get="/transactions/new/quick?category_id=X&period_id=Y&txn_type_name=Z" hx-trigger="click" hx-target="closest td" hx-swap="innerHTML">` (in `grid/_transaction_empty_cell.html`)
- **HTTP request:** GET `/transactions/new/quick?category_id=<id>&period_id=<id>&txn_type_name=<income|expense>`
- **Server handler:** `transactions.get_quick_create` in `app/routes/transactions.py:327`
- **Response:** HTMX fragment -- renders `grid/_transaction_quick_create.html`.
- **DOM update:** `hx-target="closest td"`, `hx-swap="innerHTML"`. Replaces the entire `<td>` contents.
- **Side effects:** None.
- **Dependencies:** Category and PayPeriod ownership checks. Scenario lookup.

### GI-10: Balance Row Refresh (Triggered by balanceChanged)

- **Trigger element:** `<tfoot id="grid-summary" hx-get="/grid/balance-row?periods=N&offset=O&account_id=A" hx-trigger="balanceChanged from:body" hx-swap="outerHTML">` (in `grid/_balance_row.html`, line 11-16)
- **HTTP request:** GET `/grid/balance-row?periods=N&offset=O[&account_id=A]`
- **Server handler:** `grid.balance_row` in `app/routes/grid.py:201`
- **Response:** HTMX fragment -- renders `grid/_balance_row.html` (the entire `<tfoot>`). The tfoot contains a single `<tr>` row: Projected End Balance. Total Income, Total Expenses, and Net Cash Flow have been moved to inline `<tbody>` subtotal rows and are NOT part of the tfoot.
- **DOM update:** `hx-swap="outerHTML"`. Replaces the entire `<tfoot id="grid-summary">` element with the new one.
- **Side effects:** None. The response is the new tfoot element which itself has the `hx-trigger="balanceChanged from:body"` attribute, so future triggers will work.
- **Dependencies:** `balance_calculator.calculate_balances()`, all periods, all transactions (including shadow transactions from transfers). `account` and `low_balance_threshold` context variables.

### GI-11: Anchor Balance Click -> Edit Form

- **Trigger element:** `<div id="anchor-display" hx-get="/accounts/<id>/anchor-form" hx-trigger="click" hx-swap="outerHTML">` (in `grid/_anchor_edit.html`)
- **HTTP request:** GET `/accounts/<account_id>/anchor-form`
- **Server handler:** `accounts.anchor_form` in `app/routes/accounts.py:508`
- **Response:** HTMX fragment -- renders `grid/_anchor_edit.html` with `editing=True`.
- **DOM update:** `hx-swap="outerHTML"`. Replaces the `<div id="anchor-display">` with the edit `<form>`.
- **Side effects:** None.
- **Dependencies:** Account ownership check.

### GI-12: Anchor Balance Save

- **Trigger element:** `<form hx-patch="/accounts/<id>/true-up" hx-target="this" hx-swap="outerHTML">` (in `grid/_anchor_edit.html`)
- **HTTP request:** PATCH `/accounts/<account_id>/true-up` with form data `anchor_balance`.
- **Server handler:** `accounts.true_up` in `app/routes/accounts.py:451`
- **Response:** HTML -- the anchor display (editing=False) plus an OOB swap for `#anchor-as-of` timestamp.
- **DOM update:** `hx-target="this"`, `hx-swap="outerHTML"`. Replaces the form with the display div. OOB swap updates the "as of" date.
- **Side effects:** `HX-Trigger: balanceChanged`. Triggers GI-10 (balance row refresh).
- **Dependencies:** Account ownership, `_anchor_schema` validation, `pay_period_service.get_current_period()`.

### GI-13: Anchor Balance Cancel

- **Trigger element:** `<button hx-get="/accounts/<id>/anchor-display" hx-target="closest form" hx-swap="outerHTML">` (in `grid/_anchor_edit.html`)
- **HTTP request:** GET `/accounts/<account_id>/anchor-display`
- **Server handler:** `accounts.anchor_display` in `app/routes/accounts.py:523`
- **Response:** HTMX fragment -- renders `grid/_anchor_edit.html` with `editing=False`.
- **DOM update:** `hx-target="closest form"`, `hx-swap="outerHTML"`.
- **Side effects:** None.
- **Dependencies:** Account ownership check.

### GI-14: Carry Forward

- **Trigger element:** `<form hx-post="/pay-periods/<id>/carry-forward" hx-swap="none">` (in `grid/grid.html`, line 84-91, within past period headers)
- **HTTP request:** POST `/pay-periods/<period_id>/carry-forward`
- **Server handler:** `transactions.carry_forward` in `app/routes/transactions.py:566`
- **Response:** Empty body with status 200.
- **DOM update:** `hx-swap="none"` -- no DOM update from the response itself.
- **Side effects:** `HX-Trigger: gridRefresh`. Full page reload via `window.location.reload()`.
- **Dependencies:** `carry_forward_service.carry_forward_unpaid()`, period ownership, scenario lookup.

### GI-15: Inline Create Submit

- **Trigger element:** `<form class="txn-quick-edit" data-mode="create" hx-post="/transactions/inline" hx-target="closest td" hx-swap="innerHTML">` (in `grid/_transaction_quick_create.html`, line 12-17)
- **HTTP request:** POST `/transactions/inline` with form data (category_id, pay_period_id, scenario_id, transaction_type_id, account_id, estimated_amount).
- **Server handler:** `transactions.create_inline` in `app/routes/transactions.py:451`
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html` with `wrap_div=True` (wrapped in `<div id="txn-cell-<id>">`).
- **DOM update:** `hx-target="closest td"`, `hx-swap="innerHTML"`.
- **Side effects:** `HX-Trigger: balanceChanged`. Triggers GI-10.
- **Dependencies:** `_inline_create_schema`, category/period ownership, scenario lookup.

### GI-16: Add Transaction Modal Submit

- **Trigger element:** `<form hx-post="/transactions" hx-swap="none" data-modal-auto-close>` (in `grid/grid.html`, line 301-303 area)
- **HTTP request:** POST `/transactions` with form data (name, estimated_amount, transaction_type_id, category_id, pay_period_id, scenario_id).
- **Server handler:** `transactions.create_transaction` in `app/routes/transactions.py:504`
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **DOM update:** `hx-swap="none"` -- no DOM swap. Instead, the `htmx:afterRequest` handler in `app.js` detects `data-modal-auto-close`, hides the modal, and calls `location.reload()`.
- **Side effects:** `HX-Trigger: balanceChanged`. But the page reload makes this moot.
- **Dependencies:** `_create_schema`, period ownership.

### GI-17: Date Range Controls (Navigation)

- **Trigger element:** Arrow buttons and quick-select buttons in the grid header (in `grid/grid.html`). These are standard `<a href="...">` links, NOT HTMX requests.
- **HTTP request:** GET `/?periods=N&offset=O` (full page load).
- **Server handler:** `grid.index` in `app/routes/grid.py:33`
- **Response:** Full page.
- **DOM update:** Full page load -- entire document replaced.
- **Side effects:** None.
- **Dependencies:** All grid context variables.

### GI-18: Keyboard Navigation

- **Trigger element:** Keyboard events on the document (in `app.js:357-553`).
- **HTTP request:** None directly. Arrow keys move the `cell-focused` class. Enter/Space click the focused cell's `.txn-cell` element, which triggers GI-1 or GI-9.
- **Server handler:** N/A (client-side only until a cell click triggers an HTMX request).
- **DOM update:** Adds/removes `cell-focused` class on `<td>` elements. The `getDataRows()` function (line 357-368) filters out rows with classes: `section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`, `subtotal-row`, `net-cash-flow-row`.
- **Side effects:** After HTMX swaps, `htmx:afterSwap` handler restores focus position.
- **Dependencies:** `.grid-table` must exist. Data rows must NOT have the excluded classes.

### GI-19: Transfer Cell Click -> Quick Edit -- RETIRED

> **This interaction was retired by Phase 3A-I of the Transfer Architecture Rework.** The TRANSFERS
> section has been removed from the grid. Transfer-linked transactions (shadow transactions) now
> appear as regular transactions in the INCOME and EXPENSES sections and use GI-1 (Transaction Cell
> Click -> Quick Edit) instead. When a shadow transaction's expand button is clicked (GI-3), the
> server detects `transfer_id` and returns the transfer full edit form (`transfers/_transfer_full_edit.html`)
> with `source_txn_id` for correct HTMX targeting back to the transaction cell.
>
> The `transfers/_transfer_cell.html` template still exists but is only used on the transfer management
> page (`/transfers`), not in the grid.

### GI-20: Quick Edit Escape -> Revert to Display

- **Trigger element:** Escape key in a quick edit input (handled by `grid_edit.js:221-260`).
- **HTTP request:** JS calls `htmx.ajax('GET', '/transactions/<id>/cell', ...)` for transaction edits or `htmx.ajax('GET', '/transactions/empty-cell?...', ...)` for create mode cancellation.
- **Server handler:** `transactions.get_cell` (`app/routes/transactions.py:60`) or `transactions.get_empty_cell` (`app/routes/transactions.py:418`).
- **Response:** HTMX fragment -- the original cell display or empty cell placeholder.
- **DOM update:** Targets `#txn-cell-<id>` (edit cancel) or `closest td` (create cancel).
- **Side effects:** None.
- **Dependencies:** Transaction/category/period ownership checks.

### GI-21: F2 Key -> Expand to Full Edit/Create

- **Trigger element:** F2 key pressed while focused in a quick edit/create input (handled by `grid_edit.js:197-219`).
- **HTTP request:** JS `fetch('/transactions/<id>/full-edit')` or `fetch('/transactions/new/full?...')` (same as GI-3).
- **Server handler:** `transactions.get_full_edit` or `transactions.get_full_create`. For shadow transactions, `get_full_edit` returns the transfer form.
- **Response:** Full edit/create popover HTML.
- **DOM update:** Injected into `#txn-popover`.
- **Side effects:** `htmx.process(popover)` called.
- **Dependencies:** Same as GI-3.

---

## SECTION A: Current State Analysis

### Task 4.1: Grid Layout -- Category/Transaction Name Clarity

**A1. Current behavior.**

The grid template is `app/templates/grid/grid.html`. It renders a `<table>` with class `grid-table` inside a scrollable `<div class="table-responsive grid-wrapper grid-scroll-wrapper">`.

**Table structure:**

- **`<thead class="table-dark">`:** One row. First column is `<th class="row-label-col sticky-col">Item</th>`. Subsequent columns are pay period headers. Each period header contains two `<div>` elements:
  - `<div class="fw-bold">{{ period.start_date.strftime('%m/%d') }}</div>` -- start date in MM/DD format (line 81).
  - `<div class="small text-light-emphasis">{{ period.end_date.strftime('%m/%d/%y') }}</div>` -- end date in MM/DD/YY format (line 82).
  - For past periods (excluding current), a carry forward button is also rendered inside the `<th>`.

- **`<tbody>`:** Contains two major sections separated by spacer rows (the TRANSFERS section was removed by the Section 3A rework):
  1. **INCOME section:** Starts with a banner row `<tr class="section-banner-income">` (line 100). Then rows are grouped by category. Each category group has an optional group header row `<tr class="group-header-row">` showing `category.group_name` with colspan (line 122). Under each group, individual transaction rows are rendered as `<tr>` with:
     - `<th scope="row" class="sticky-col row-label" title="{{ category.display_name }}">{{ category.item_name }}</th>` -- shows only the category item_name (e.g., "Salary") with display_name as hover tooltip.
     - One `<td class="text-end cell">` per period containing matched transactions.
     - Shadow transactions from transfers appear here alongside regular income transactions. They are visually distinguished by a transfer indicator icon (`bi-arrow-left-right`) in the cell.
  2. **Total Income subtotal row** (line 163): `<tr class="subtotal-row subtotal-row-income">` with label "Total Income". Sums projected income (excluding done/received/credit/cancelled).
  3. **Spacer row** (line 179): `<tr class="spacer-row">` separating income from expenses.
  4. **EXPENSES section:** Same structure as income, with banner `<tr class="section-banner-expense">` (line 184). Shadow transactions from transfers appear here alongside regular expenses.
  5. **Total Expenses subtotal row** (line 243): `<tr class="subtotal-row subtotal-row-expense">` with label "Total Expenses".
  6. **Net Cash Flow row** (line 259): `<tr class="net-cash-flow-row">` showing Total Income minus Total Expenses per period. Negative values get `balance-negative` class and a warning icon.

- **`<tfoot id="grid-summary">`:** Included via `{% include "grid/_balance_row.html" %}` (line 284). Contains a single `<tr>` row: **Projected End Balance** (`<tr class="balance-row-summary fw-bold">`). This is the only sticky footer row. The tfoot has `position: sticky; bottom: 0; z-index: 2;` (from `app.css:314-319`).

**Key row-label behavior:** The row label cell shows `category.item_name` (e.g., "Electricity") with `category.display_name` (e.g., "Home: Utilities > Electricity") as the `title` attribute (hover tooltip). The transaction template name (e.g., "AEP Electric") is NOT shown in the row label. It appears only when the user clicks a cell and opens the full edit popover, or as the `title` attribute on the `.txn-cell` div (line 18 of `_transaction_cell.html`).

**Row grouping:** Categories are iterated in `group_name, item_name` order (set by the query in `grid.py:120-125`). The template checks if `category.group_name != ns.current_group` to decide whether to insert a group header row. This means multiple categories under the same group (e.g., "Home") share one group header.

**CSS sizing:** The `.row-label-col` has `min-width: 160px; max-width: 200px` (app.css:237-240). The `.row-label` has `font-size: 0.8rem` (app.css:243). Group header rows have `font-size: 11px` with `text-transform: uppercase`.

**Context variables passed from `grid.index` (line 145-164):** `scenario`, `account`, `periods`, `current_period`, `balances`, `txn_by_period`, `categories`, `statuses`, `transaction_types`, `num_periods`, `start_offset`, `col_size`, `anchor_balance`, `today`, `all_periods`, `low_balance_threshold`, `stale_anchor_warning`. Note: there is NO `xfer_by_period` -- transfer data is included via shadow transactions in `txn_by_period`.

**A2. What is wrong and why it matters.**

When multiple transactions share the same category item name (e.g., two transactions both in the "Home: Utilities" category with item names "Electricity" and "Gas"), they are distinguishable. However, when a single category item has multiple transaction templates (e.g., category "Insurance: Auto" with templates "State Farm - Liability" and "Geico - Comprehensive"), the row label shows only "Auto" for both. The user must hover over each cell to see which template generated it (via the `title` attribute on the `.txn-cell` div). This is an extra step during the payday workflow when the user needs to quickly identify which expense to mark as done.

After the transfer rework, shadow transactions also appear in the INCOME and EXPENSES sections. They have a transfer indicator icon (`bi-arrow-left-right`) for visual distinction, but their row labels still show the category item name (e.g., "Outgoing" for "Transfers: Outgoing"). If multiple transfers share the same category, distinguishing them has the same problem as regular transactions.

**A3. Impact on payday workflow.**

During step 5 (mark cleared expenses as done), the user scans the grid to find specific expenses. If two transactions share the same category item name, the user cannot distinguish them at a glance in the row label column. They must hover over each cell to see the template name in the tooltip, then click the correct one. This now also applies to shadow transaction expenses from transfers (e.g., "Transfer to Savings" and "Transfer to Mortgage" both showing as "Outgoing" in the row label). With approximately 10-15 active line items, this affects maybe 2-4 items that share categories, but those items require extra cognitive effort every payday.

---

### Task 4.2: Footer Condensation -- SUPERSEDED

This task was fully superseded by Phase 3A-II of the Transfer Architecture Rework (Section
3A). Phase 3A-II added inline subtotal rows (Total Income, Total Expenses) and a Net Cash
Flow row to the grid body, then condensed the sticky footer to a single Projected End
Balance row. The original Task 4.2 goal (reduce footer from 4-5 rows to 2 rows) has been
exceeded (footer is now 1 row).

Test specs C-4.2-1 through C-4.2-3 have been superseded by the Phase 3A-II test suite.
Tests covering the current footer structure exist in `tests/test_routes/test_grid.py`:
`TestInlineSubtotalRows` (4 tests), `TestNetCashFlowRow` (4 tests), and
`TestFooterCondensation` (4 tests).

See `docs/transfer_rework_design.md` section 18.2 and
`docs/transfer_rework_implementation.md` Tasks 13-15 for the implementation that replaced
this task.

---

### Task 4.3: Pay Period Date Format Cleanup

**A1. Current behavior.**

Period column headers in `grid/grid.html` (line 81-82):
```html
<div class="fw-bold">{{ period.start_date.strftime('%m/%d') }}</div>
<div class="small text-light-emphasis">{{ period.end_date.strftime('%m/%d/%y') }}</div>
```

Every period header always shows two lines: the start date in MM/DD format and the end date in MM/DD/YY format. The year is always shown on the end date, regardless of whether the year is the current year. The `.period-header` class has `font-size: 0.8rem`.

**A2. What is wrong and why it matters.**

Showing "MM/DD/YY" on every period is redundant when most periods are in the current year. The "/YY" suffix adds visual noise and consumes horizontal space. For example, "03/24/26" could be "03/24" when the year is obvious from context.

**A3. Impact on payday workflow.**

Minor impact. During step 1 (open the app), the user glances at the period headers to orient themselves. The current date format is clear but verbose. Cleaning it up improves scan speed slightly. This is a polish item, not a workflow blocker.

---

### Task 4.4: Terminology -- "Done" to "Paid" / "Received"

**A1. Current behavior.**

The `ref.statuses` table has a `name` column (type `VARCHAR(15)`) and an `id` column. Seeded values: `projected`, `done`, `received`, `credit`, `cancelled`, `settled`. There is NO `display_label` column. The Status model in `app/models/ref.py:38-51` has only `id` and `name`.

**Display of status names throughout the application:**

1. **`grid/_transaction_cell.html:33-39`:** Checks `t.status.name in ('done', 'received')` to show a checkmark badge (line 34). The `title` and `aria-label` attributes use `t.status.name|capitalize` (line 35, renders as "Done" or "Received"). Also checks `t.status.name == 'credit'` for CC badge (line 36). Shadow transactions use this same template and display status the same way.
2. **`grid/_transaction_full_edit.html:40-49`:** Status dropdown uses `s.name|capitalize` as option text (line 45). Shows "Projected", "Done", "Received", "Credit", "Cancelled", "Settled".
3. **`grid/_transaction_full_edit.html:74-82`:** "Done" button text: `Done` (hardcoded string, line 80). Only shown for expenses where `txn.is_expense and txn.status.name != 'done'` (line 74).
4. **`grid/_transaction_full_edit.html:112-120`:** "Received" button text: `Received` (hardcoded string, line 118). Only shown for income where `txn.is_income and txn.status.name == 'projected'` (line 112).
5. **`grid/_transaction_full_create.html:50-59`:** Status dropdown uses `s.name|capitalize` (line 55).
6. **`transfers/_transfer_cell.html`:** Checks `xfer.status.name in ('done', 'received')`. This template is used only on the transfer management page, NOT in the grid. Shadow transactions in the grid use `grid/_transaction_cell.html`.
7. **`transfers/_transfer_full_edit.html:87-95`:** "Done" button text: `Done` (hardcoded, line 94). This template IS used for shadow transaction full edits opened from the grid (via the transaction route guard in `transactions.get_full_edit`). Its status dropdown also uses `s.name|capitalize` (line 45).

**Internal usage of status name strings (these must NOT change):**

1. **`app/services/balance_calculator.py:32`:** `SETTLED_STATUSES = frozenset({"done", "received"})` -- used to exclude settled transactions from balance calculation.
2. **`app/services/balance_calculator.py` (various lines):** String checks `if status_name in ("credit", "cancelled", "done", "received")` for filtering.
3. **`app/routes/transactions.py:189`:** `Status.filter_by(name="done")` / `filter_by(name="received")` for mark_done logic.
4. **`app/routes/transfers.py:574`:** `Status.filter_by(name="done")` for transfer mark-done.
5. **`app/routes/transfers.py:603`:** `Status.filter_by(name="cancelled")` for transfer cancel.
6. **`app/services/recurrence_engine.py`:** `IMMUTABLE_STATUSES = frozenset({"done", "received", "credit", "cancelled"})`.
7. **`app/services/transfer_recurrence.py:31`:** Same immutable statuses frozenset.
8. **`app/services/credit_workflow.py`:** Uses status name checks.
9. **`app/services/chart_data_service.py`:** `Status.filter_by(name="done")` for chart calculations.
10. **`app/models/transaction.py`:** `self.status.name in ("done", "received")` for effective_amount property.
11. **`grid/_balance_row.html`:** No longer has status filtering logic (footer is single balance row). Status filtering moved to tbody subtotal/net cash flow rows.
12. **`grid/grid.html:140, 220`:** `txn.status.name != 'cancelled'` filter for income/expense row display. `txn.status.name not in ('credit', 'cancelled', 'done', 'received')` for subtotal calculations (lines 168, 248, 264).
13. **`app/routes/savings.py`:** `txn.status.name in ("done", "received")` for savings calculations.

**Test assertions that reference "done"/"Done":**
- `tests/test_services/test_transfer_recurrence.py`: Multiple tests query and assert on `name="done"`.
- `tests/test_services/test_recurrence_engine.py`: Queries `name="done"`.
- `tests/test_scripts/test_integrity_check.py`: Queries `name="done"`.
- `tests/test_services/test_credit_workflow.py`: Multiple tests query `name="done"`.

**A2. What is wrong and why it matters.**

The term "Done" is generic and does not distinguish between expense settlement and income receipt. For expenses, "Paid" is clearer -- it communicates that money left the account. For income, "Received" already exists and is correct. The issue is that the "Done" button label and status display don't provide contextual meaning.

**A3. Impact on payday workflow.**

During step 5 (mark cleared expenses as done), the button says "Done." The user knows they're marking an expense as paid, but "Paid" would be more intuitive. During step 3 (mark paycheck as received), the button already says "Received" (line 118 of `_transaction_full_edit.html`), which is correct. For shadow transaction expenses opened from the grid, the transfer full edit form (line 94 of `_transfer_full_edit.html`) also shows "Done" and should change to "Paid". The primary UX improvement is for expense marking.

---

### Task 4.5: Deduction Frequency Display

**A1. Current behavior.**

The deduction list is in `app/templates/salary/_deductions_section.html`. It renders a `<table class="table table-hover table-sm">` with columns: Name, Timing, Method, Amount (right-aligned), Per Year, Cap, Target Account, and action buttons (line 14-23).

The "Per Year" column (line 19) has header text "Per Year" and the cell (line 42) simply shows `{{ d.deductions_per_year }}` -- a raw integer like "26", "24", or "12". There is no descriptive label explaining what the number means.

The table has 8 columns total. On desktop widths, the columns are auto-sized. The "Amount" and "Per Year" columns sit adjacent to each other (columns 4 and 5).

**A2. What is wrong and why it matters.**

Showing "26" next to "$500" does not clearly communicate "26 times per year (every paycheck)." The user must mentally decode that 26 = biweekly. The "Per Year" header helps, but the raw number is ambiguous -- does 26 mean "$500 is deducted 26 times" or "$500 is the annual amount divided by 26"? In the add/edit form (line 150-154), the dropdown options do include labels: "26 (every paycheck)", "24 (skip 3rd paycheck)", "12 (monthly)". But the display table strips these labels and shows only the number.

**A3. Impact on payday workflow.**

This does not directly affect the payday reconciliation workflow (which happens on the grid page). It affects the salary configuration workflow, where the user reviews their deductions to ensure accuracy. Misunderstanding the frequency could lead to incorrect paycheck projections.

---

### Task 4.6: Tax Config Page Reorganization

**A1. Current behavior.**

The tax configuration page is `app/templates/salary/tax_config.html`. It renders three cards in this order:

1. **Federal Tax Brackets** (line 23-64): A card showing bracket tables for each tax year / filing status combination. Each bracket set shows min_income, max_income, rate in a `<table>`. This section also displays standard deduction, child credit, and other dependent credit amounts. This is static data seeded by `scripts/seed_tax_brackets.py` and rarely changes.

2. **FICA Configuration** (line 66-142): A card showing SS rate, wage base, Medicare rate, surtax rate, and threshold in a table. Below the table is an "Update FICA" form. FICA parameters change annually but are modified only once per year.

3. **State Tax Configuration** (line 144-211): A card showing state code, type, flat rate, and standard deduction. Below is an "Update/Add State Tax" form. This is a user-adjustable setting that the user might change when moving states.

**A2. What is wrong and why it matters.**

The Federal Tax Brackets card (which is the longest section, potentially showing multiple years of bracket tables) is at the top. This is the section the user changes least often. The State Tax Configuration (which the user might actually need to adjust) is at the bottom, requiring scrolling past the bracket tables. There is no way to collapse or hide sections. Previous tax year brackets (e.g., 2025 when the current year is 2026) are always visible.

**A3. Impact on payday workflow.**

This does not affect the payday workflow. It affects the less frequent salary/tax configuration workflow. The user visits this page when setting up or updating their tax parameters. Having adjustable settings buried below static tables adds unnecessary scrolling.

---

### Task 4.7/4.8: Account Parameter Setup UX / Mortgage Parameter Page Flow

**A1. Current behavior.**

Account creation is in `app/routes/accounts.py`. The creation route (`create_account`, line 99-148) handles the post-creation flow:

1. **Mortgage accounts:** Redirect to `mortgage.dashboard` (line 143-144). This takes the user to a separate page for entering mortgage parameters (interest rate, term, origination date, etc.).
2. **Auto loan accounts:** Redirect to `auto_loan.dashboard` (line 145-146).
3. **All other types (HYSA, investment, retirement, checking, savings):** Redirect to the accounts list (line 148). For HYSA, a default `HysaParams` record is auto-created (line 136-137), but the user must then find the new account card on the savings dashboard and click the small graph icon button to configure APY.
4. **Investment/retirement accounts:** Same redirect to accounts list. The user must find the account card and click the small graph-up icon to configure return rates.

The account creation form (`app/templates/accounts/form.html`) has three fields: Name, Account Type (dropdown), and Current Balance. There are no conditional parameter fields that appear based on the selected account type.

On the savings dashboard (`app/templates/savings/dashboard.html`), each account card has small icon buttons. For HYSA, there's a link to the HYSA detail page with a graph icon. For mortgage, a house icon. For investment/retirement, a graph-up icon. There is no visual indicator that an account needs parameter configuration. No `needs_setup` badge currently exists.

**A2. What is wrong and why it matters.**

The parameter configuration path is not discoverable. After creating a HYSA account, the user sees a success flash message and lands on the accounts list. They must know to navigate to the savings dashboard, find the new account card, and click the small icon button to set the APY. There's no prompt, no badge, no wizard. The mortgage path is better (auto-redirect to the parameter page), but HYSA, investment, and retirement accounts have a discovery gap.

**A3. Impact on payday workflow.**

This does not directly affect the payday workflow. It affects account setup, which happens infrequently. However, if an account's parameters are never configured, the balance projections on the savings dashboard will be inaccurate (e.g., HYSA interest projections will use default 0% APY).

---

### Task 4.9: Chart -- Balance Over Time Contrast

**A1. Current behavior.**

The Balance Over Time chart is rendered by `chart_balance.js`. It creates a Chart.js line chart with datasets from the server. Each dataset gets a color from `ShekelChart.getColor(i)` which indexes into the 8-color palette defined in `chart_theme.js` (line 19-28):

```javascript
var palette = [
  { name: 'Accent',  dark: '#4A9ECC', light: '#2878A8' },
  { name: 'Green',   dark: '#2ECC71', light: '#1A9B50' },
  { name: 'Amber',   dark: '#E67E22', light: '#C96B15' },
  { name: 'Rose',    dark: '#D97BA0', light: '#B05A80' },
  { name: 'Teal',    dark: '#1ABC9C', light: '#148F77' },
  { name: 'Purple',  dark: '#9B59B6', light: '#7D3C98' },
  { name: 'Coral',   dark: '#E74C3C', light: '#C0392B' },
  { name: 'Slate',   dark: '#95A5A6', light: '#707B7C' }
];
```

All datasets use `borderWidth: 2`, `fill: false`, `tension: 0.3` (`chart_balance.js:39-41`). There is no dash pattern differentiation (`borderDash` is not set). When the chart has many lines (checking, savings, HYSA, mortgage, retirement), some colors may be difficult to distinguish, especially for lines that are close together or small in amplitude.

The chart supports dual Y-axis mode (left axis for small balances like checking/savings, right axis for large balances like mortgage/retirement). Axis assignment is determined server-side by account category.

**A2. What is wrong and why it matters.**

The roadmap specifically mentions that the "standard payments" line on the mortgage Balance Over Time chart is difficult to see due to low contrast. With `borderWidth: 2` and no dash patterns, lines can visually merge when they're close or when the scale difference is large. The dual-axis mode helps separate scales, but lines of similar magnitude on the same axis can still be hard to distinguish.

**A3. Impact on payday workflow.**

This does not affect the payday workflow. It affects the account monitoring workflow when the user reviews the charts page.

---

### Task 4.10: Chart -- Balance Over Time Time Frame Control

**A1. Current behavior.**

The chart route `charts.balance_over_time` (`app/routes/charts.py:40-65`) accepts optional `start` and `end` query parameters (line 49-50). The `chart_data_service.get_balance_over_time()` function uses a helper `_get_periods()` to determine the period range.

The template `charts/_balance_over_time.html` has account checkboxes (lines 6-17) and a dual Y-axis toggle (lines 18-23) that re-fetch the chart with selected options. There is NO time frame control. No 1Y, 5Y, 10Y, or Full buttons exist. The chart always shows the default period range (all periods from the anchor forward).

The chart data service's `_get_period_range()` helper (lines 133-169) handles ranges for other charts (`current`, `last_3`, `last_6`, `last_12`, `ytd`) but the balance over time chart does not use this helper -- it uses `_get_periods()` with optional date bounds.

**A2. What is wrong and why it matters.**

For short-duration accounts (checking, savings), the default range showing all periods is fine. But for long-duration accounts (30-year mortgage, 25-year retirement), showing all periods means the chart has so many data points that the lines appear nearly flat for the near-term future. The user cannot zoom in to see meaningful detail for the next 1-5 years.

**A3. Impact on payday workflow.**

This does not affect the payday workflow. It affects the long-term financial planning view.

---

## SECTION B: Proposed Solution for Each Task

### Task 4.1: Grid Layout -- Category/Transaction Name Clarity

**B1. Solution description.**

Prototype both options and compare before committing.

**B2. Option A -- Full Row Headers:**

**Before state:** Each unique `category` object produces one `<tr>` row. The row label cell shows `category.item_name`. Multiple transactions sharing a category are distinguished only by hover tooltip on the cell. Shadow transactions from transfers also appear in their category's row (e.g., "Outgoing" for transfers categorized under "Transfers: Outgoing").

**After state:** Each unique transaction template gets its own row. The row label shows the transaction template name (e.g., "AEP Electric") instead of the category item name. Category group headers remain for visual grouping. Shadow transactions get their own row with their template name (e.g., "Transfer to Savings"), keeping the transfer indicator icon visible in cells.

**Implementation approach for Option A:**

The grid currently iterates over `categories` and finds matching transactions per category per period. This would need to change to iterate over transaction templates (or unique transaction names within each category). The key challenge: the grid is driven by `categories`, not by transaction templates. Transactions without templates (ad-hoc) exist too.

A cleaner approach: build a list of "row keys" in the route. Each row key represents a unique (category_id, template_id_or_name) tuple. The route constructs this from the visible transactions. The template iterates over row keys instead of categories.

**Changes required:**
- `app/routes/grid.py`: Build a `row_keys` list from the transaction data, grouped by category group. Each row key contains: `group_name`, `item_name`, `transaction_name` (from template or ad-hoc name), `category_id`, and a filter function.
- `app/templates/grid/grid.html`: Replace the category-based iteration with row_key iteration in the INCOME and EXPENSES sections (lines 110-158 and 198-238). The row label becomes `<th class="sticky-col row-label">{{ row_key.transaction_name }}</th>` with `title="{{ row_key.display_name }}"`. Group header rows remain. Subtotal rows (lines 163-176 and 243-256) and Net Cash Flow row (lines 259-280) remain unchanged.
- `app/static/css/app.css`: `.row-label` may need slightly smaller font or the row-label-col may need a width increase. Target: single-line row label, truncated with ellipsis for long names (already handled by `overflow: hidden; text-overflow: ellipsis; white-space: nowrap` on grid cells).

**Template structure change for Option A:**
```
CURRENT:
<tr class="group-header-row">Home</tr>
<tr>
  <th class="row-label">Electricity</th>  <!-- Two AEP entries in same row -->
  <td>$150</td>
  <td>$155</td>
</tr>

PROPOSED:
<tr class="group-header-row">Home</tr>
<tr>
  <th class="row-label">AEP Electric</th>  <!-- One template per row -->
  <td>$150</td>
  <td>$155</td>
</tr>
<tr>
  <th class="row-label">Duke Energy</th>  <!-- Separate row for second template -->
  <td>$80</td>
  <td>$82</td>
</tr>
```

Shadow transactions would also get their own rows:
```
<tr class="group-header-row">Transfers</tr>
<tr>
  <th class="row-label">Transfer to Savings</th>
  <td>$200 <i class="bi bi-arrow-left-right"></i></td>
  <td>$200 <i class="bi bi-arrow-left-right"></i></td>
</tr>
```

**Option B -- Enhanced Current Layout:**

Keep the grouped-by-category layout but add the transaction name as a visible sub-label in each cell.

**Changes required:**
- `app/templates/grid/_transaction_cell.html`: Add a small transaction name label above or below the amount. Something like `<div class="txn-name-label small text-muted text-truncate" style="font-size: 0.65rem;">{{ t.name }}</div>` before the amount display. Shadow transactions would show both the name label and the transfer indicator icon.
- `app/static/css/app.css`: Add `.txn-name-label` styling for compact display.
- No route changes needed. No data model changes.

**Template structure change for Option B:**
```
CURRENT cell content:
  <span class="font-mono">150</span>
  <span class="badge-done">✓</span>

PROPOSED cell content:
  <div class="txn-name-label">AEP Electric</div>
  <span class="font-mono">150</span>
  <span class="badge-done">✓</span>
```

**Decision criteria (measurable):**
1. **Visual scan count:** For each option, count how many distinct visual locations the user must check to identify a specific transaction (e.g., "Find the AEP Electric bill for the current period"). Option A: scan the row label column (1 location). Option B: scan the row label to find the category group, then scan within the cells to find the name label (2 locations).
2. **Total vertical height with 15 active line items:** Measure the grid height including headers, section banners, group headers, subtotal rows, net cash flow row, and transaction rows. Option A adds more rows (one per template) but removes the ambiguity. Option B keeps the same row count but adds vertical space within each cell.
3. **Empty cell clarity:** In Option A, an empty cell in a template-specific row means that template has no transaction for that period (clear). In Option B, an empty cell in a category row means no transaction for that category in that period, but the name label is absent, so the user can't tell which template is missing.
4. **Horizontal space impact:** Option A increases row label column usage (longer names like "AEP Electric" vs "Electricity"). Option B increases cell height but not row label width.

**Recommendation:** Option A is likely superior for the primary use case (payday reconciliation) because it eliminates all ambiguity without requiring per-cell scanning. The vertical space increase is modest (likely 3-5 additional rows for a typical 10-15 item budget) and may be partially offset by the removal of group header rows if categories with single items no longer need them.

**B5. HTMX regression analysis for Option A:**

- **GI-1 (Cell click -> quick edit):** `hx-target="#txn-cell-<id>"` -- SAFE. Target is the `<div id="txn-cell-<id>">` inside the `<td>`, which is unchanged.
- **GI-2 (Quick edit submit):** Same target -- SAFE. For shadow transactions, the transfer service guard handles the update atomically.
- **GI-3 (Expand to full edit):** JS uses `triggerEl.closest('td')` -- SAFE. The `<td>` structure is unchanged. For shadow transactions, the server returns the transfer full edit form with `source_txn_id` for correct targeting.
- **GI-4 (Full edit submit):** Targets `#txn-cell-<id>` -- SAFE. Transfer full edit form also targets `#txn-cell-<source_txn_id>` when opened from the grid.
- **GI-5 through GI-8 (Status changes):** Target `#txn-cell-<id>` -- SAFE. All use `gridRefresh` which reloads the page. Shadow transactions route through transfer service.
- **GI-9 (Empty cell click):** `hx-target="closest td"` -- SAFE. The `<td>` is still there.
- **GI-10 (Balance row refresh):** Targets `#grid-summary` tfoot (single Projected End Balance row) -- NOT AFFECTED by tbody changes.
- **GI-11/12/13 (Anchor edit):** Outside the grid table -- NOT AFFECTED.
- **GI-14 (Carry forward):** Returns `gridRefresh` -- SAFE. Full page reload.
- **GI-15 (Inline create):** `hx-target="closest td"` -- SAFE.
- **GI-16 (Add transaction modal):** Uses `location.reload()` -- SAFE.
- **GI-17 (Date range controls):** Full page load -- SAFE.
- **GI-18 (Keyboard navigation):** `getDataRows()` (app.js:357-368) filters out rows with classes: `section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`, `subtotal-row`, `net-cash-flow-row`. If Option A removes some group header rows, fewer rows are excluded. If Option A adds new row types, they must NOT have any of the excluded classes (or they must be added to the exclusion list if they should be skipped). **REQUIRES VERIFICATION** that the new row structure does not add any rows with excluded classes that should be navigable, and does not remove the excluded classes from rows that should be skipped.
- **GI-19 (Transfer cell click):** RETIRED. Shadow transactions use GI-1 instead. Not affected.
- **GI-20/21 (Escape/F2):** Target resolution unchanged -- SAFE.

**B5. HTMX regression analysis for Option B:**

All interactions are SAFE because Option B only adds content within the existing `<div class="txn-cell">` inside each `<td>`. No DOM structure changes outside the cell content.

**B6. Edge cases:**
- **Zero transactions:** Grid shows section banners and group headers but no transaction rows. Option A: no template-specific rows appear. Option B: same as current (no cells with name labels). Subtotal rows show zeros.
- **30+ transactions:** Option A adds more rows, requiring more scrolling. The sticky tfoot (single Projected End Balance row) ensures the balance is always visible. The sticky thead ensures headers are visible. The vertical space concern is the primary argument for Option B.
- **Very long transaction name:** Already handled by `overflow: hidden; text-overflow: ellipsis; white-space: nowrap` on `.row-label` and the `title` attribute for full-text hover. Option A: long names truncate in the row label. Option B: long names truncate in the small cell label.
- **Narrow browser window:** Mobile CSS already constrains `.sticky-col` to `min-width: 90-130px` (app.css:759-762). Long names will truncate more aggressively but remain functional.
- **Shadow transactions in row structure:** In Option A, shadow transactions get their own row (since they have unique template names like "Transfer to Savings"). This is correct and consistent -- they are treated identically to regular transactions. In Option B, shadow transactions show the name label in the cell alongside the transfer indicator icon.
- **Transfer-linked transactions sharing a category with regular transactions:** In Option A, each gets its own row (keyed by template name, not just category). No ambiguity. In Option B, they share a category row but the name label in each cell disambiguates.

**B7. Files to create or modify (Option A):**
- `app/routes/grid.py` -- Modified. Build row_keys from transactions for template-name-based rows.
- `app/templates/grid/grid.html` -- Modified. Replace category iteration with row_key iteration for income and expense sections.
- `app/static/css/app.css` -- Modified. Possibly adjust `.row-label` or `.row-label-col` widths.
- `tests/test_routes/test_grid.py` -- Modified. Update assertions for new row structure.

**B7. Files to create or modify (Option B):**
- `app/templates/grid/_transaction_cell.html` -- Modified. Add transaction name label.
- `app/static/css/app.css` -- Modified. Add `.txn-name-label` styling.
- `tests/test_routes/test_grid.py` -- Modified. Assert name label appears in cell.

---

### Task 4.3: Pay Period Date Format Cleanup

**B1. Solution description.**

Change the period header to show a single combined date range. Within the current year, omit the year. When the period spans a year boundary or is in a different year from today, include the year.

**Template change in `grid/grid.html` (lines 81-82):**

BEFORE:
```html
<div class="fw-bold">{{ period.start_date.strftime('%m/%d') }}</div>
<div class="small text-light-emphasis">{{ period.end_date.strftime('%m/%d/%y') }}</div>
```

AFTER:
```html
{% set current_year = today.year %}
{% if period.start_date.year != current_year or period.end_date.year != current_year %}
  <div class="fw-bold">{{ period.start_date.strftime('%m/%d/%y') }}</div>
  <div class="small text-light-emphasis">{{ period.end_date.strftime('%m/%d/%y') }}</div>
{% else %}
  <div class="fw-bold">{{ period.start_date.strftime('%m/%d') }} - {{ period.end_date.strftime('%m/%d') }}</div>
{% endif %}
```

For current-year periods, this combines the dates into a single line "03/24 - 04/06", saving one line of vertical space in the header and improving scannability. For cross-year periods, the full MM/DD/YY format is shown on both dates.

Note: `today` is already passed as a context variable from `grid.index` (line 160 of `grid.py`).

**B5. HTMX regression analysis:**

This task modifies only the `<thead>` period header content. No HTMX interactions target the `<thead>`. All interactions are NOT AFFECTED.

Note: The `<thead>` also contains the carry forward button (GI-14). This button is inside the `<th>` but below the date display. The date format change does not affect the button's `hx-post` or `hx-swap` attributes. SAFE.

**B6. Edge cases:**
- **Year boundary:** A period starting 12/30/25 and ending 01/12/26 -- both dates show year suffix. Correct.
- **All periods in current year:** All periods show the compact "MM/DD - MM/DD" format. Correct.
- **2-year view (52 periods):** Some far-future periods will be in the next year and show year suffix. Near-term periods won't. This natural transition is clear.
- **Mobile/narrow:** The combined "MM/DD - MM/DD" string is slightly wider than the current "MM/DD" alone but narrower than having two lines. Net horizontal impact: neutral to slightly wider. The `.period-header` `font-size: 0.8rem` and compact mode `font-size: 0.75rem` still apply.

**B7. Files to modify:**
- `app/templates/grid/grid.html` -- Modified. Update period header date format logic.
- `tests/test_routes/test_grid.py` -- Modified. Update assertions for new date format in response HTML.

---

### Task 4.4: Terminology -- "Done" to "Paid" / "Received"

**B1. Solution description.**

Add a `display_label` column to the `ref.statuses` table. The internal `name` column values ("done", "received", etc.) remain UNCHANGED. All code that queries by `Status.name` continues to work. Only the user-facing display text changes.

**B3. Full ripple effect:**

**Database migration:**
- Add column `display_label VARCHAR(20)` to `ref.statuses` with `nullable=True` (so existing rows don't break).
- Data migration: Set `display_label` values:
  - `projected` -> `"Projected"`
  - `done` -> `"Paid"` (this is the key change)
  - `received` -> `"Received"`
  - `credit` -> `"Credit"`
  - `cancelled` -> `"Cancelled"`
  - `settled` -> `"Settled"`

**Model change:**
- `app/models/ref.py`: Add `display_label = db.Column(db.String(20))` to the Status class. Add a property `label` that returns `self.display_label or self.name.capitalize()` for backwards compatibility.

**Template changes (files that display status names to users):**

1. **`app/templates/grid/_transaction_cell.html:35`:**
   - BEFORE: `title="{{ t.status.name|capitalize }}" aria-label="{{ t.status.name|capitalize }}"`
   - AFTER: `title="{{ t.status.display_label or t.status.name|capitalize }}" aria-label="{{ t.status.display_label or t.status.name|capitalize }}"`

2. **`app/templates/grid/_transaction_full_edit.html:40-49`:** (Status dropdown options)
   - BEFORE: `{{ s.name|capitalize }}`
   - AFTER: `{{ s.display_label or s.name|capitalize }}`

3. **`app/templates/grid/_transaction_full_edit.html:80`:** (Button text)
   - BEFORE: `Done`
   - AFTER: `Paid`

4. **`app/templates/grid/_transaction_full_create.html:50-59`:** (Status dropdown)
   - BEFORE: `{{ s.name|capitalize }}`
   - AFTER: `{{ s.display_label or s.name|capitalize }}`

5. **`app/templates/transfers/_transfer_cell.html`:** (Transfer management page only)
   - BEFORE: `title="{{ xfer.status.name|capitalize }}"`
   - AFTER: `title="{{ xfer.status.display_label or xfer.status.name|capitalize }}"`

6. **`app/templates/transfers/_transfer_full_edit.html:94`:** (Button text -- this template IS used for shadow transaction full edits from the grid)
   - BEFORE: `Done`
   - AFTER: `Paid`
   - Also update the status dropdown (line 45) to use `display_label or name|capitalize`.

**Files that must NOT change (internal status name references):**
- `app/services/balance_calculator.py` -- Uses `status.name` for logic. KEEP UNCHANGED.
- `app/routes/transactions.py` -- Uses `Status.filter_by(name="done")`. KEEP UNCHANGED.
- `app/routes/transfers.py` -- Uses `Status.filter_by(name="done")` (line 574) and `filter_by(name="cancelled")` (line 603). KEEP UNCHANGED.
- `app/services/recurrence_engine.py` -- Uses `IMMUTABLE_STATUSES` frozenset. KEEP UNCHANGED.
- `app/services/transfer_recurrence.py` -- Same (line 31). KEEP UNCHANGED.
- `app/services/credit_workflow.py` -- Uses name-based logic. KEEP UNCHANGED.
- `app/services/chart_data_service.py` -- Uses `Status.filter_by(name="done")`. KEEP UNCHANGED.
- `app/models/transaction.py` -- Uses `self.status.name in ("done", "received")`. KEEP UNCHANGED.
- `app/templates/grid/grid.html` -- Uses `txn.status.name != 'cancelled'` and `txn.status.name not in (...)` for filtering logic, not display. KEEP UNCHANGED.
- `scripts/seed_ref_tables.py` -- Seeds by name. KEEP UNCHANGED. The migration handles display_label.
- `app/__init__.py` -- Seeds by name in test mode. KEEP UNCHANGED.

**Seed script update:**
- `scripts/seed_ref_tables.py` -- Optionally update to set `display_label` values during seeding. Or handle via migration only.

**Test changes:**
- Tests that assert on the user-facing display of status names (e.g., checking response HTML for "Done" text in buttons) need updating to assert "Paid" instead.
- Tests that query by `Status.filter_by(name="done")` do NOT change.

**B5. HTMX regression analysis:**

This task does not modify any DOM structure, HTMX attributes, or swap targets. It changes only the text content within existing elements. All grid HTMX interactions are NOT AFFECTED.

**B6. Edge cases:**
- **Status with no display_label:** The `{{ s.display_label or s.name|capitalize }}` pattern falls back gracefully.
- **Custom or extended statuses:** If new statuses are added in the future without display_label, the fallback to `name|capitalize` ensures they display correctly.
- **Migration rollback safety:** If the migration runs but the code is rolled back, the old code uses `s.name|capitalize` and ignores `display_label`. The column exists but is unused. SAFE.

**B7. Files to modify:**
- `app/models/ref.py` -- Modified. Add `display_label` column to Status.
- `migrations/versions/<new>.py` -- New. Alembic migration to add display_label column and set values.
- `app/templates/grid/_transaction_cell.html` -- Modified. Use display_label for title/aria.
- `app/templates/grid/_transaction_full_edit.html` -- Modified. Use display_label for dropdown and button text.
- `app/templates/grid/_transaction_full_create.html` -- Modified. Use display_label for dropdown.
- `app/templates/transfers/_transfer_cell.html` -- Modified. Use display_label for title.
- `app/templates/transfers/_transfer_full_edit.html` -- Modified. Use display_label for dropdown and button text.
- `scripts/seed_ref_tables.py` -- Modified. Add display_label values during seed.
- `tests/test_routes/test_grid.py` -- Modified. Update display text assertions.
- `tests/test_routes/test_transactions.py` -- Modified. Update button text assertions.
- `tests/test_routes/test_transfers.py` -- Modified. Update button text assertions.

---

### Task 4.5: Deduction Frequency Display

**B1. Solution description.**

Change the "Per Year" column in the deductions table to show descriptive labels instead of raw numbers.

**Template change in `salary/_deductions_section.html`:**

Line 42, change from:
```html
<td>{{ d.deductions_per_year }}</td>
```

To:
```html
<td>
  {% if d.deductions_per_year == 26 %}
    26x/yr <small class="text-muted">(every paycheck)</small>
  {% elif d.deductions_per_year == 24 %}
    24x/yr <small class="text-muted">(skip 3rd)</small>
  {% elif d.deductions_per_year == 12 %}
    12x/yr <small class="text-muted">(monthly)</small>
  {% else %}
    {{ d.deductions_per_year }}x/yr
  {% endif %}
</td>
```

Also update the column header from "Per Year" to "Frequency" for clarity.

**B5. HTMX regression analysis:**

This task does not modify the grid page. No grid HTMX interactions are affected.

**B6. Edge cases:**
- **Unusual frequency value:** If a deduction has a frequency not in {26, 24, 12}, the fallback `{{ d.deductions_per_year }}x/yr` handles it.
- **Column width:** The descriptive labels are slightly wider. The deduction table is on the salary page (not the grid) and has sufficient horizontal space.

**B7. Files to modify:**
- `app/templates/salary/_deductions_section.html` -- Modified. Update Per Year column display.

---

### Task 4.6: Tax Config Page Reorganization

**B1. Solution description.**

Reorder sections on the tax config page and add collapsibility:

1. **Move State Tax Configuration to the top** -- this is the most frequently changed setting.
2. **Move FICA Configuration second** -- changed annually.
3. **Move Federal Tax Brackets to the bottom** -- rarely changed, static reference data.
4. **Make Federal Tax Brackets collapsible** -- use Bootstrap 5 collapse component with a toggle button. Default to collapsed.
5. **Group bracket tables by tax year with individual collapse** -- if multiple years exist, each year gets its own collapse section. Most recent year expanded by default, previous years collapsed.

**Template changes in `salary/tax_config.html`:**

Reorder the three `<div class="card mb-4">` blocks. Wrap the Federal Tax Brackets card body in a Bootstrap collapse:

```html
<div class="card mb-4">
  <div class="card-header d-flex justify-content-between align-items-center">
    <h6 class="mb-0"><i class="bi bi-building"></i> Federal Tax Brackets</h6>
    <button class="btn btn-sm btn-outline-secondary" type="button"
            data-bs-toggle="collapse" data-bs-target="#federal-brackets-body"
            aria-expanded="false" aria-controls="federal-brackets-body">
      <i class="bi bi-chevron-down"></i> Show
    </button>
  </div>
  <div class="collapse" id="federal-brackets-body">
    <div class="card-body">
      {# existing bracket tables #}
    </div>
  </div>
</div>
```

**B5. HTMX regression analysis:**

This task does not modify the grid page. No grid HTMX interactions are affected.

**B6. Edge cases:**
- **No bracket data:** The "No federal brackets configured" message still shows, even in collapsed state (the collapse wraps the card-body which contains the message).
- **Multiple tax years:** Each year gets its own collapsible section within the Federal Tax Brackets card.

**B7. Files to modify:**
- `app/templates/salary/tax_config.html` -- Modified. Reorder sections, add collapse.

---

### Task 4.7/4.8: Account Parameter Setup UX

**B4. Evaluation of three approaches:**

**Approach 1: Inline parameter fields in the account creation form.**

Show/hide parameter fields based on the selected account type. When "HYSA" is selected, APY and compounding frequency fields appear. When "Mortgage" is selected, interest rate, term, origination date, and payment day fields appear. Etc.

**Pros:**
- Single-page workflow. User fills everything out once.
- No separate navigation required.
- Discoverable -- the fields appear automatically.

**Cons:**
- The creation form becomes complex with many conditional sections.
- Different account types need very different parameters (HYSA: 2 fields; mortgage: 5+ fields; investment: 3 fields; retirement: 4+ fields). The form would be unwieldy.
- Validation complexity increases -- form must handle partial validation for type-specific fields.
- Breaks the existing pattern where params are stored in separate tables (HysaParams, MortgageParams, etc.) with separate schemas.

**Approach 2: Redirect to parameter page after creation with wizard banner.**

After creating the account, redirect to the appropriate parameter configuration page with a banner message: "Account created. Configure parameters below to enable projections."

**Pros:**
- Reuses existing parameter pages (HYSA detail, mortgage dashboard, investment dashboard).
- No changes to the creation form.
- Clear workflow: create -> configure.
- Works for all account types uniformly.

**Cons:**
- User navigates away from the accounts list. Less jarring for mortgage/auto (already does this) but new behavior for HYSA/investment/retirement.
- Requires creating the params record before redirecting (for HYSA, already done; for investment/retirement, may need to be added).

**Approach 3: "Setup required" badge on account card.**

Show a badge (e.g., a yellow "Setup" or "Configure" badge) on the account card in the savings dashboard when parameters are not configured.

**Pros:**
- Minimal code changes.
- Non-intrusive -- user sees the badge and can configure at their leisure.
- Works with existing navigation patterns.

**Cons:**
- Does not solve the discoverability problem for the parameter *page* -- the user still has to know what to click.
- Passive -- the user might ignore the badge.
- Does not provide a clear "next step" workflow.

**Recommendation: Approach 2 (redirect with wizard banner), combined with Approach 3 (badge for accounts that remain unconfigured).**

This provides both an immediate prompt (the redirect) and a persistent reminder (the badge). The implementation:

1. After creating any account type that requires parameters, auto-create a default params record (if one doesn't already exist) and redirect to the parameter page. This extends the existing mortgage/auto_loan pattern to HYSA, investment, and retirement accounts.
2. On the savings dashboard, add a "Setup Required" badge to account cards where params are at default values (e.g., APY = 0 for HYSA, return rate = 0 for investment).
3. The parameter page shows a dismissible alert banner when reached via the post-creation redirect: "Account created successfully. Configure the settings below to enable accurate projections."

**Changes to `app/routes/accounts.py`:**

In `create_account()` (line 99-148), extend the redirect logic:

```python
# Current: only mortgage and auto_loan redirect.
# Proposed: all parameterized types redirect.
if account_type and account_type.name == "mortgage":
    return redirect(url_for("mortgage.dashboard", account_id=account.id, setup=1))
if account_type and account_type.name == "auto_loan":
    return redirect(url_for("auto_loan.dashboard", account_id=account.id, setup=1))
if account_type and account_type.name == "hysa":
    return redirect(url_for("accounts.hysa_detail", account_id=account.id, setup=1))
if account_type and account_type.category in ("retirement", "investment"):
    # Auto-create investment params if not exists.
    from app.models.investment_params import InvestmentParams
    params = InvestmentParams(account_id=account.id)
    db.session.add(params)
    db.session.commit()
    return redirect(url_for("investment.dashboard", account_id=account.id, setup=1))
```

The `setup=1` query parameter tells the target page to show the wizard banner.

**Changes to savings dashboard template:**

Add a badge check in `savings/dashboard.html` for each account card. The route already passes account data including params. Add a helper check:

```html
{% if ad.needs_setup %}
  <span class="badge bg-warning text-dark">Setup Required</span>
{% endif %}
```

The `needs_setup` flag would be computed in the savings dashboard route based on whether params are at default values.

**B5. HTMX regression analysis:**

This task does not modify the grid page. No grid HTMX interactions are affected.

**B6. Edge cases:**
- **Checking/savings accounts:** No parameters needed. No redirect, no badge.
- **Account type changed after creation:** Not applicable -- account type is set at creation and not typically changed.
- **Params already configured:** If the user creates an account, configures params, then visits the dashboard, the badge should not appear.

**B7. Files to modify:**
- `app/routes/accounts.py` -- Modified. Extend post-creation redirect logic.
- `app/routes/savings.py` -- Modified. Add `needs_setup` flag to account data.
- `app/templates/savings/dashboard.html` -- Modified. Add "Setup Required" badge.
- `app/templates/accounts/hysa_detail.html` -- Modified. Add wizard banner when `setup=1`.
- Possibly `app/templates/mortgage/dashboard.html`, `app/templates/investment/dashboard.html` -- Modified. Add wizard banner.
- `tests/test_routes/test_accounts.py` -- Modified. Test redirect behavior for all account types.
- `tests/test_routes/test_savings.py` -- Modified. Test badge appearance.

---

### Task 4.9: Chart -- Balance Over Time Contrast

**B1. Solution description.**

Differentiate chart lines by varying line width and dash pattern in addition to color.

**Changes to `chart_balance.js`:**

Add a `lineStyles` array that cycles through visual differentiation:
```javascript
var lineStyles = [
  { borderWidth: 2.5, borderDash: [] },        // solid thick
  { borderWidth: 2, borderDash: [8, 4] },       // dashed
  { borderWidth: 2, borderDash: [2, 3] },       // dotted
  { borderWidth: 2.5, borderDash: [12, 4, 2, 4] }, // dash-dot
  { borderWidth: 2, borderDash: [] },            // solid normal
  { borderWidth: 2, borderDash: [6, 3] },       // short dash
  { borderWidth: 2, borderDash: [3, 2] },       // dense dot
  { borderWidth: 2.5, borderDash: [8, 3, 2, 3] }, // dash-dot-dot
];
```

Apply in the `datasets.map()` call:
```javascript
var style = lineStyles[i % lineStyles.length];
return {
  // ... existing fields ...
  borderWidth: style.borderWidth,
  borderDash: style.borderDash,
};
```

This ensures that even when colors are similar, the dash patterns provide visual differentiation.

**B5. HTMX regression analysis:**

This task does not modify the grid page. No grid HTMX interactions are affected.

**B6. Edge cases:**
- **Single account:** Only one line, solid thick. Clear.
- **8+ accounts:** Styles cycle. With 8 colors and 8 dash patterns, up to 64 unique combinations (though only 8 are used before cycling).
- **Legend readability:** Chart.js legends display line samples with dash patterns automatically.

**B7. Files to modify:**
- `app/static/js/chart_balance.js` -- Modified. Add dash patterns and varied line widths.

---

### Task 4.10: Chart -- Balance Over Time Time Frame Control

**B1. Solution description.**

Add time frame control buttons to the Balance Over Time chart. The controls appear in the chart card header as a button group.

**Options:** 1Y (1 year, ~26 periods), 5Y (5 years, ~130 periods), 10Y (10 years, ~260 periods), Full (all periods).

**Template changes in `charts/_balance_over_time.html`:**

Add a button group above the chart (after the account checkboxes):
```html
<div class="btn-group btn-group-sm ms-auto">
  {% for label, value in [('1Y', '1y'), ('5Y', '5y'), ('10Y', '10y'), ('Full', 'full')] %}
  <button class="btn {{ 'btn-primary active' if selected_range == value else 'btn-outline-secondary' }}"
          hx-get="{{ url_for('charts.balance_over_time', range=value) }}"
          hx-include="[name='account_id']:checked, [name='dual_axis']"
          hx-target="closest .card-body"
          hx-swap="innerHTML">
    {{ label }}
  </button>
  {% endfor %}
</div>
```

**Route changes in `app/routes/charts.py`:**

Pass `range` query parameter to the service. The service translates:
- `1y` -> `start=today, end=today+365`
- `5y` -> `start=today, end=today+1825`
- `10y` -> `start=today, end=today+3650`
- `full` -> no start/end filter (all periods)

**Service changes in `app/services/chart_data_service.py`:**

Update `_get_periods()` or `get_balance_over_time()` to accept a named range parameter and compute the appropriate date bounds. The `range` parameter translates to a number of periods to include:
- `1y` -> 26 periods
- `5y` -> 130 periods
- `10y` -> 260 periods
- `full` -> all periods

**Default:** The route should determine a sensible default based on the account types being displayed. If any mortgage or retirement account is selected, default to `full` or `10y`. If only checking/savings, default to `1y`.

**B5. HTMX regression analysis:**

This task does not modify the grid page. No grid HTMX interactions are affected.

**B6. Edge cases:**
- **Fewer periods than requested:** If the user has only 2 years of periods and selects 10Y, show all available periods. The chart simply shows less data.
- **No periods:** The empty state message already exists ("No balance data available").
- **Account type mix:** When a mix of short and long-duration accounts are selected, the time frame applies to all. The dual-axis mode helps separate scales.

**B7. Files to modify:**
- `app/templates/charts/_balance_over_time.html` -- Modified. Add time frame buttons.
- `app/routes/charts.py` -- Modified. Pass range parameter.
- `app/services/chart_data_service.py` -- Modified. Handle range parameter in period query.
- `tests/test_routes/test_charts.py` -- Modified. Test range parameter.

---

## SECTION C: Required Tests

### Prerequisite: Payday Workflow End-to-End Regression Tests

**These tests must be written and committed FIRST, before any Section 4 changes.** They establish the safety net.

#### C-0-1: `test_payday_workflow_trueup_anchor_balance`

- **Category:** Route (HTMX response)
- **Setup:** `seed_user`, `auth_client`, seeded pay periods, seeded account with anchor balance.
- **Action:** PATCH `/accounts/<id>/true-up` with `anchor_balance=5000.00`.
- **Assertion:** Response status 200. Response contains updated balance display. `HX-Trigger` header contains `balanceChanged`. Database shows updated anchor_balance and anchor_period_id.
- **Why:** Guards against anchor balance editing regression -- the foundation of all balance calculations.

#### C-0-2: `test_payday_workflow_mark_paycheck_received`

- **Category:** Route (HTMX response)
- **Setup:** `seed_user`, `auth_client`, seeded income transaction with status "projected".
- **Action:** POST `/transactions/<id>/mark-done` with `actual_amount=2500.00`.
- **Assertion:** Response status 200. Transaction status changed to "received" (because it's income). `actual_amount` set to 2500.00. `HX-Trigger` contains `gridRefresh`. Response HTML contains the checkmark badge.
- **Why:** Guards against the income status transition workflow.

#### C-0-3: `test_payday_workflow_carry_forward_unpaid`

- **Category:** Route (HTMX response)
- **Setup:** `seed_user`, `auth_client`, seeded past period with projected transactions, seeded current period.
- **Action:** POST `/pay-periods/<past_period_id>/carry-forward`.
- **Assertion:** Response status 200. `HX-Trigger` contains `gridRefresh`. Projected transactions moved to current period. Done/received/credit/cancelled transactions NOT moved.
- **Why:** Guards against carry forward logic regression.

#### C-0-4: `test_payday_workflow_mark_expense_done`

- **Category:** Route (HTMX response)
- **Setup:** `seed_user`, `auth_client`, seeded expense transaction with status "projected".
- **Action:** POST `/transactions/<id>/mark-done`.
- **Assertion:** Response status 200. Transaction status changed to "done". `HX-Trigger` contains `gridRefresh`. Response HTML contains the checkmark badge.
- **Why:** Guards against expense status transition.

#### C-0-5: `test_payday_workflow_mark_credit`

- **Category:** Route (HTMX response)
- **Setup:** `seed_user`, `auth_client`, seeded expense transaction with status "projected", seeded next pay period.
- **Action:** POST `/transactions/<id>/mark-credit`.
- **Assertion:** Response status 200. Transaction status changed to "credit". A payback transaction created in the next period. `HX-Trigger` contains `gridRefresh`.
- **Why:** Guards against credit card workflow and auto-payback generation.

#### C-0-6: `test_payday_workflow_balance_row_refresh`

- **Category:** Route (HTMX response)
- **Setup:** `seed_user`, `auth_client`, seeded periods, seeded transactions and account.
- **Action:** GET `/grid/balance-row?periods=6&offset=0`.
- **Assertion:** Response status 200. Response contains `<tfoot id="grid-summary">`. Response contains exactly 1 `<tr>` element (Projected End Balance only -- Total Income, Total Expenses, and Net Cash Flow are in the `<tbody>`, not the `<tfoot>`). Response contains balance amounts. Response contains the `hx-trigger="balanceChanged from:body"` attribute.
- **Why:** Guards against balance row HTMX partial rendering.

#### C-0-7: `test_payday_workflow_full_sequence`

- **Category:** Integration (multi-step)
- **Setup:** `seed_user`, `auth_client`, seeded periods with transactions (income + expenses), seeded account with anchor balance.
- **Action:** Perform steps 1-5 in sequence:
  1. PATCH `/accounts/<id>/true-up` with new balance.
  2. POST `/transactions/<income_id>/mark-done` to mark paycheck received.
  3. POST `/pay-periods/<past_id>/carry-forward` to carry forward.
  4. POST `/transactions/<expense_id>/mark-done` to mark expense done.
  5. POST `/transactions/<expense2_id>/mark-credit` to mark one as credit.
- **Assertion:** After all steps, GET `/grid/balance-row` returns correct balances. The `<tfoot>` has a single row (Projected End Balance). The balance calculation matches hand-computed expected values.
- **Why:** Guards against interactions between workflow steps.

---

### Per-Task Tests

#### Task 4.1 Tests

**C-4.1-1:** `test_grid_renders_transaction_names_in_row_labels` (if Option A chosen)
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded transaction templates with distinct names, including at least one shadow transaction from a transfer.
- **Action:** GET `/`.
- **Assertion:** Response HTML contains the transaction template name (e.g., "AEP Electric") visible in a `<th class="sticky-col row-label">` element. Shadow transaction template names (e.g., "Transfer to Savings") also appear in row labels.
- **Why:** Verifies the layout change renders template names, not just category item names.

**C-4.1-2:** `test_grid_renders_transaction_name_label_in_cell` (if Option B chosen)
- **Category:** Route
- **Setup:** Same as above.
- **Action:** GET `/`.
- **Assertion:** Response HTML contains a `.txn-name-label` element with the transaction name inside a `<td class="text-end cell">`. Shadow transaction cells show both the name label and the transfer indicator icon.
- **Why:** Verifies the name label appears in cells.

**C-4.1-3:** `test_grid_inline_edit_after_layout_change`
- **Category:** Route (HTMX regression)
- **Setup:** `seed_user`, `auth_client`, seeded transaction.
- **Action:** GET `/transactions/<id>/quick-edit`. Then PATCH `/transactions/<id>` with new amount.
- **Assertion:** Quick edit returns 200 with input form. Patch returns 200 with updated cell, `HX-Trigger: balanceChanged`.
- **Why:** Verifies inline editing still works after DOM restructure.

**C-4.1-4:** `test_grid_empty_state_after_layout_change`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded periods but no transaction templates.
- **Action:** GET `/`.
- **Assertion:** Grid renders without errors. Section banners appear. No transaction rows. Subtotal rows show zeros.
- **Why:** Verifies empty grid doesn't crash with new row structure.

**C-4.1-5:** `test_keyboard_navigation_after_layout_change`
- **Category:** Template/Visual
- **Setup:** `seed_user`, `auth_client`, seeded transactions.
- **Action:** GET `/`. Inspect that data rows do NOT have excluded CSS classes (`section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`, `subtotal-row`, `net-cash-flow-row`).
- **Assertion:** Transaction rows are navigable by keyboard (no excluded classes). Banner/spacer/group/subtotal/net-cash-flow rows have the correct excluded classes.
- **Why:** Keyboard navigation depends on CSS class filtering. The exclusion list was updated by Phase 3A-II to include `subtotal-row` and `net-cash-flow-row`.

#### Task 4.3 Tests

**C-4.3-1:** `test_period_header_omits_year_for_current_year`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded periods all within current year.
- **Action:** GET `/`.
- **Assertion:** Period headers show "MM/DD - MM/DD" format (single line, no "/YY" suffix).
- **Why:** Verifies the compact format for current-year periods.

**C-4.3-2:** `test_period_header_includes_year_for_different_year`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded periods spanning into next year.
- **Action:** GET `/`.
- **Assertion:** For periods with start_date or end_date in a different year from today, the header shows "MM/DD/YY" format.
- **Why:** Verifies the year is shown when needed.

#### Task 4.4 Tests

**C-4.4-1:** `test_status_model_has_display_label`
- **Category:** Migration
- **Setup:** Database with migration applied.
- **Action:** Query `Status` model.
- **Assertion:** All statuses have `display_label` values. `done` status has `display_label = "Paid"`. `received` status has `display_label = "Received"`.
- **Why:** Verifies migration correctness.

**C-4.4-2:** `test_expense_done_button_says_paid`
- **Category:** Route (template rendering)
- **Setup:** `seed_user`, `auth_client`, seeded expense transaction.
- **Action:** GET `/transactions/<id>/full-edit`.
- **Assertion:** Response HTML contains button text "Paid" (not "Done").
- **Why:** Verifies user-facing terminology change.

**C-4.4-3:** `test_status_dropdown_shows_display_labels`
- **Category:** Route (template rendering)
- **Setup:** `seed_user`, `auth_client`, seeded transaction.
- **Action:** GET `/transactions/<id>/full-edit`.
- **Assertion:** Response HTML contains `<option>` elements with "Paid" (not "Done") for the done status.
- **Why:** Verifies dropdown uses display_label.

**C-4.4-4:** `test_internal_status_name_unchanged`
- **Category:** Migration/Model
- **Setup:** Database with migration applied.
- **Action:** Query `Status.filter_by(name="done")`.
- **Assertion:** Returns exactly one result. The `name` column is still "done".
- **Why:** Verifies internal name is unchanged -- protects all service logic.

**C-4.4-5:** `test_balance_calculator_still_uses_name`
- **Category:** Service
- **Setup:** Transactions with status "done".
- **Action:** Call `balance_calculator.calculate_balances()`.
- **Assertion:** Done transactions are excluded from balance calculations (same behavior as before).
- **Why:** Verifies the balance calculator is unaffected by the display_label addition.

#### Task 4.5 Tests

**C-4.5-1:** `test_deduction_frequency_shows_descriptive_label`
- **Category:** Route (template rendering)
- **Setup:** `seed_user`, `auth_client`, salary profile with deductions (26x, 24x, 12x per year).
- **Action:** GET salary profile page.
- **Assertion:** Response HTML contains "26x/yr" with "every paycheck" label, "24x/yr" with "skip 3rd" label, "12x/yr" with "monthly" label.
- **Why:** Verifies the frequency display improvement.

#### Task 4.6 Tests

**C-4.6-1:** `test_tax_config_state_tax_appears_first`
- **Category:** Route (template rendering)
- **Setup:** `seed_user`, `auth_client`, seeded tax config data.
- **Action:** GET `/salary/tax-config`.
- **Assertion:** In the response HTML, the "State Tax Configuration" card appears before the "FICA Configuration" card, which appears before the "Federal Tax Brackets" card.
- **Why:** Verifies section reordering.

**C-4.6-2:** `test_federal_brackets_collapsed_by_default`
- **Category:** Route (template rendering)
- **Setup:** Same.
- **Action:** GET `/salary/tax-config`.
- **Assertion:** The Federal Tax Brackets card body has a `collapse` class (not `collapse show`), indicating it is collapsed by default.
- **Why:** Verifies collapsibility.

#### Task 4.7/4.8 Tests

**C-4.7-1:** `test_hysa_creation_redirects_to_params`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, HYSA account type.
- **Action:** POST `/accounts` with HYSA type.
- **Assertion:** Response is a redirect (302) to the HYSA detail page with `setup=1` query parameter.
- **Why:** Verifies the new redirect behavior for HYSA.

**C-4.7-2:** `test_investment_creation_redirects_to_params`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, investment account type.
- **Action:** POST `/accounts` with investment type.
- **Assertion:** Redirect to investment dashboard with `setup=1`.
- **Why:** Verifies redirect for investment accounts.

**C-4.7-3:** `test_checking_creation_redirects_to_list`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, checking account type.
- **Action:** POST `/accounts` with checking type.
- **Assertion:** Redirect to accounts list (no parameter page).
- **Why:** Verifies checking accounts don't redirect to a param page.

**C-4.7-4:** `test_setup_badge_shown_for_unconfigured_hysa`
- **Category:** Route (template rendering)
- **Setup:** `seed_user`, `auth_client`, HYSA account with default params (APY=0).
- **Action:** GET savings dashboard.
- **Assertion:** Response HTML contains "Setup Required" badge for the HYSA account.
- **Why:** Verifies the badge appears for unconfigured accounts.

**C-4.7-5:** `test_setup_badge_hidden_for_configured_hysa`
- **Category:** Route (template rendering)
- **Setup:** `seed_user`, `auth_client`, HYSA account with APY=4.5%.
- **Action:** GET savings dashboard.
- **Assertion:** Response HTML does NOT contain "Setup Required" badge for the HYSA account.
- **Why:** Verifies the badge disappears after configuration.

#### Task 4.9 Tests

**C-4.9-1:** `test_balance_chart_datasets_have_dash_patterns`
- **Category:** Route (template rendering)
- **Setup:** `seed_user`, `auth_client`, multiple accounts.
- **Action:** GET `/charts/balance-over-time` (HTMX request).
- **Assertion:** Response renders the chart canvas with datasets. This is a smoke test -- the JS logic for dash patterns can only be tested via browser testing, but the route test verifies the chart data is served correctly.
- **Why:** Verifies the chart still renders after JS changes.

#### Task 4.10 Tests

**C-4.10-1:** `test_balance_chart_accepts_range_parameter`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded periods and accounts.
- **Action:** GET `/charts/balance-over-time?range=1y` (HTMX request).
- **Assertion:** Response status 200. Chart data contains approximately 26 labels (1 year of biweekly periods).
- **Why:** Verifies range parameter works.

**C-4.10-2:** `test_balance_chart_full_range`
- **Category:** Route
- **Setup:** `seed_user`, `auth_client`, seeded periods (2 years worth).
- **Action:** GET `/charts/balance-over-time?range=full`.
- **Assertion:** Chart data contains all available periods.
- **Why:** Verifies full range option.

**C-4.10-3:** `test_balance_chart_range_buttons_rendered`
- **Category:** Route (template rendering)
- **Setup:** `seed_user`, `auth_client`, seeded data.
- **Action:** GET `/charts/balance-over-time`.
- **Assertion:** Response HTML contains buttons with labels "1Y", "5Y", "10Y", "Full".
- **Why:** Verifies time frame controls are rendered.

---

## SECTION D: Risk and Regression Assessment

### Task 4.1

**D1. What could break:**
- The keyboard navigation in `app.js:357-368` (`getDataRows()`) filters rows by CSS class. If Option A changes the class names on rows, keyboard navigation could skip or include wrong rows. The exclusion list is: `section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`, `subtotal-row`, `net-cash-flow-row`.
- The grid route `grid.index` currently passes `categories` and `txn_by_period` to the template. Option A requires a different data structure (row_keys). If the route changes break the template's iteration logic, the grid fails to render.
- The empty cell interaction (GI-9) uses `category.id` and `period.id` in the `hx-get` URL. If Option A changes how categories map to rows, empty cells could pass wrong category IDs.

**D2. Mitigation:**
- C-0-1 through C-0-7 (payday workflow regression tests) catch any interaction breakage.
- C-4.1-3 (inline edit test) specifically tests the editing flow.
- C-4.1-5 (keyboard navigation test) checks CSS classes, including the `subtotal-row` and `net-cash-flow-row` classes added by Phase 3A-II.

**D3. Data migration risk:** None. No database changes.

**D4. Rollback plan:** Pure template/route change. Revert the commits. No migration to reverse.

**D5. Cross-task interaction:**
- **4.1 + subtotal/net-cash-flow rows (from Phase 3A-II):** Task 4.1 restructures the transaction rows in the tbody. The subtotal rows and Net Cash Flow row sit between/after the transaction rows. If Option A changes the iteration structure, the subtotal calculation logic in the template must still correctly sum across the new row structure. Verify subtotal values are unchanged after 4.1.
- **4.1 + 4.3:** 4.3 reduces header height (one line instead of two for current-year periods). This frees space to offset 4.1's row increase. No structural conflict.
- **4.1 + 4.4:** If 4.4 changes button labels at the same time 4.1 changes row structure, testing both simultaneously increases confidence that neither breaks the other. No structural conflict -- 4.4 changes text content, 4.1 changes DOM structure.

### Task 4.3

**D1. What could break:**
- Tests that assert on specific date format strings in the grid response.
- If the Jinja2 conditional logic has a bug (e.g., wrong year comparison), some headers could show incorrect formats.

**D2. Mitigation:** C-4.3-1 and C-4.3-2 test both code paths.

**D3. Data migration risk:** None.

**D4. Rollback plan:** Template-only change. Revert the commit.

### Task 4.4

**D1. What could break:**
- If any code path reads `status.display_label` before the migration sets values, it gets `None`. The `{{ s.display_label or s.name|capitalize }}` pattern handles this.
- If a test creates statuses without display_label (e.g., in conftest.py seeds), template rendering that assumes display_label exists could fail. The `or` fallback prevents this.
- The migration adds a nullable column. If the migration's data update fails partway through, some statuses might have `display_label` and others might not. The `or` fallback handles this.

**D2. Mitigation:** C-4.4-1 through C-4.4-5 cover migration, display, and internal logic.

**D3. Data migration risk:** The migration adds a column and updates existing rows. If the migration runs but the code is rolled back, the old code ignores the column (it uses `s.name|capitalize` not `s.display_label`). SAFE. If the code deploys but the migration hasn't run, `status.display_label` would be `None`, and the `or` fallback kicks in. SAFE.

**D4. Rollback plan:** Revert code commit. The display_label column remains but is unused. Optionally, run a down migration to remove the column, but it's not harmful to leave it.

### Task 4.5

**D1. What could break:** Minimal risk. Single template file change on a non-grid page.

**D2. Mitigation:** C-4.5-1.

**D3. Data migration risk:** None.

**D4. Rollback plan:** Template-only change.

### Task 4.6

**D1. What could break:** If the Bootstrap collapse is misconfigured (wrong ID, missing data-bs-toggle), the section might not expand/collapse. The data is still there, just hidden.

**D2. Mitigation:** C-4.6-1 and C-4.6-2.

**D3. Data migration risk:** None.

**D4. Rollback plan:** Template-only change.

### Task 4.7/4.8

**D1. What could break:**
- The redirect after HYSA creation could break if the HYSA detail page URL changes.
- Auto-creating InvestmentParams for retirement/investment accounts at creation time could fail if the model requires fields that aren't provided.
- The `needs_setup` flag logic could incorrectly mark configured accounts as needing setup.

**D2. Mitigation:** C-4.7-1 through C-4.7-5.

**D3. Data migration risk:** None (params records are created in route logic, not via migration).

**D4. Rollback plan:** Route and template changes. Revertable.

### Task 4.9

**D1. What could break:** Chart lines could render with unexpected visual appearance if the dash pattern array is malformed.

**D2. Mitigation:** C-4.9-1 (smoke test). Manual visual verification.

**D3. Data migration risk:** None.

**D4. Rollback plan:** JS-only change.

### Task 4.10

**D1. What could break:**
- The range parameter could be incorrectly translated to period counts, showing too many or too few periods.
- The time frame buttons could target the wrong HTMX element, causing the chart to not update.

**D2. Mitigation:** C-4.10-1 through C-4.10-3.

**D3. Data migration risk:** None.

**D4. Rollback plan:** Route, service, and template changes. Revertable.

---

## SECTION E: Difficulty, Time Estimates, and Implementation Order

### E1. Difficulty Ratings

| Task | Difficulty | Time Range | Justification |
|------|-----------|------------|---------------|
| 4.1 (Grid Layout) | Complex | 8-14 hours | DOM restructure of the primary view. Two prototypes. Route data structure change. HTMX regression verification. Highest risk task. |
| 4.3 (Date Format) | Simple | 30-60 min | Single template conditional. Two test cases. |
| 4.4 (Terminology) | Moderate | 3-5 hours | Migration, model change, 7 template files (including transfer templates used for shadow edits), test updates across the codebase. Wide ripple effect but each change is small. |
| 4.5 (Deduction Frequency) | Trivial | 15-30 min | Single template change, one test. |
| 4.6 (Tax Config Reorder) | Simple | 30-60 min | Reorder HTML blocks, add Bootstrap collapse. |
| 4.7/4.8 (Account Setup UX) | Moderate | 3-5 hours | Route logic changes for multiple account types, badge logic, template changes across multiple files. |
| 4.9 (Chart Contrast) | Simple | 30-60 min | JS-only change, add dash patterns. |
| 4.10 (Chart Time Frame) | Moderate | 2-4 hours | New UI controls, route parameter, service logic change. |

### E2. Implementation Order

1. **Commit #0: Payday workflow regression test suite** -- MUST be first. Establishes safety net. Note: `tests/test_routes/test_grid_regression.py` does not currently exist and must be created.

2. **Task 4.5 (Deduction Frequency)** -- Trivial, non-grid. Gets a quick win out of the way. No risk to grid.

3. **Task 4.6 (Tax Config Reorder)** -- Simple, non-grid. Another quick win.

4. **Task 4.9 (Chart Contrast)** -- Simple, non-grid. JS-only change to chart page.

5. **Task 4.10 (Chart Time Frame)** -- Moderate, non-grid. More complex chart change but isolated from the grid.

6. **Task 4.7/4.8 (Account Setup UX)** -- Moderate, non-grid. Route and template changes to account creation flow.

7. **Task 4.4 (Terminology: Done to Paid)** -- Moderate, touches grid templates but only text content, not structure. Wide ripple but low structural risk. Must be done before 4.1 to avoid needing to update both old and new template structures.

8. **Task 4.3 (Date Format)** -- Simple, touches grid `<thead>` only. Low risk. Done after 4.4 so the grid template is in its final non-structural state.

9. **Task 4.1 (Grid Layout)** -- Complex, highest risk. Done last among grid-touching tasks because it restructures the DOM. By this point, all non-structural changes (4.4, 4.3) are already applied and tested, and the regression suite is in place.

**Justification:**
- Safety-first: Non-grid tasks (4.5, 4.6, 4.9, 4.10, 4.7/4.8) come first while the grid is untouched.
- The regression test suite (commit #0) protects all subsequent changes.
- Text-only grid changes (4.4, 4.3) come before structural changes (4.1).
- 4.1 is the riskiest and comes after all simpler changes are proven.
- Task 4.1 is the final commit since Task 4.2 (Footer Condensation) has been superseded by Phase 3A-II.

### E3. Total Estimated Time Range

**Minimum:** 17.75 hours (sum of low estimates + regression test suite ~2h)
**Maximum:** 30.5 hours (sum of high estimates + regression test suite ~4h)
**Expected:** ~22 hours

---

## SECTION F: Atomic Commit Plan

### Commit #0 (PREREQUISITE -- must be first)

**F1.** `test(grid): add payday workflow end-to-end regression test suite`

**F2.**
- `tests/test_routes/test_grid_regression.py` -- New. Contains C-0-1 through C-0-7. Note: this file does not currently exist. The transfer rework added grid structure tests (TestTransfersSectionRemoved, TestInlineSubtotalRows, TestNetCashFlowRow, TestFooterCondensation) to `test_grid.py`, but those test the grid structure, not the payday workflow interaction sequence. The regression tests in this commit specifically test the multi-step HTMX interaction patterns.

**F3.** All 7 new regression tests must pass. Full test suite must pass.

**F4.** N/A (test-only commit).

**F5.** N/A.

---

### Commit #1: Task 4.5

**F1.** `feat(salary): improve deduction frequency display with descriptive labels`

**F2.**
- `app/templates/salary/_deductions_section.html` -- Modified. Update Per Year column.
- `tests/test_routes/test_salary.py` -- Modified. Add C-4.5-1.

**F3.** C-4.5-1 + existing salary tests must pass.

**F4.** Open the salary page. Verify deductions show "26x/yr (every paycheck)" etc.

**F5.** N/A (non-grid change).

---

### Commit #2: Task 4.6

**F1.** `feat(salary): reorganize tax config page -- adjustable settings first, brackets collapsed`

**F2.**
- `app/templates/salary/tax_config.html` -- Modified. Reorder sections, add collapse.
- `tests/test_routes/test_salary.py` -- Modified. Add C-4.6-1, C-4.6-2.

**F3.** C-4.6-1, C-4.6-2 + existing salary tests must pass.

**F4.** Open the tax config page. Verify State Tax is first, FICA second, Federal brackets collapsed at bottom. Click the "Show" button to expand brackets.

**F5.** N/A.

---

### Commit #3: Task 4.9

**F1.** `feat(charts): add dash patterns and varied line weights to balance chart`

**F2.**
- `app/static/js/chart_balance.js` -- Modified. Add lineStyles array.
- `tests/test_routes/test_charts.py` -- Modified. Add C-4.9-1 if not already covered.

**F3.** Existing chart tests must pass.

**F4.** Open the Charts page. Verify the Balance Over Time chart shows distinct line styles (solid, dashed, dotted) for different accounts.

**F5.** Screenshot before/after to compare line visibility.

---

### Commit #4: Task 4.10

**F1.** `feat(charts): add time frame controls to balance over time chart`

**F2.**
- `app/templates/charts/_balance_over_time.html` -- Modified. Add time frame buttons.
- `app/routes/charts.py` -- Modified. Handle `range` parameter.
- `app/services/chart_data_service.py` -- Modified. Filter periods by range.
- `tests/test_routes/test_charts.py` -- Modified. Add C-4.10-1, C-4.10-2, C-4.10-3.

**F3.** C-4.10-1, C-4.10-2, C-4.10-3 + existing chart tests must pass.

**F4.** Open the Charts page. Click 1Y, 5Y, 10Y, Full buttons. Verify the chart updates with different period ranges.

**F5.** N/A.

---

### Commit #5: Task 4.7/4.8

**F1.** `feat(accounts): redirect to parameter config after creating parameterized accounts`

**F2.**
- `app/routes/accounts.py` -- Modified. Extend post-creation redirects.
- `app/routes/savings.py` -- Modified. Add needs_setup flag.
- `app/templates/savings/dashboard.html` -- Modified. Add Setup Required badge.
- `app/templates/accounts/hysa_detail.html` -- Modified. Add wizard banner.
- `tests/test_routes/test_accounts.py` -- Modified. Add C-4.7-1, C-4.7-2, C-4.7-3.
- `tests/test_routes/test_savings.py` -- Modified. Add C-4.7-4, C-4.7-5.

**F3.** All C-4.7-x tests + existing account/savings tests must pass.

**F4.** Create a new HYSA account. Verify redirect to HYSA detail with setup banner. Create checking account, verify redirect to accounts list. Visit savings dashboard, verify badge on unconfigured accounts.

**F5.** N/A.

---

### Commit #6: Task 4.4

**F1.** `feat(status): add display_label column -- rename "Done" to "Paid" for expenses`

**F2.**
- `app/models/ref.py` -- Modified. Add display_label column.
- `migrations/versions/<new>.py` -- New. Alembic migration.
- `app/templates/grid/_transaction_cell.html` -- Modified. Use display_label.
- `app/templates/grid/_transaction_full_edit.html` -- Modified. Use display_label.
- `app/templates/grid/_transaction_full_create.html` -- Modified. Use display_label.
- `app/templates/transfers/_transfer_cell.html` -- Modified. Use display_label.
- `app/templates/transfers/_transfer_full_edit.html` -- Modified. Use display_label for dropdown and "Paid" for button text.
- `scripts/seed_ref_tables.py` -- Modified. Add display_label values.
- `tests/test_routes/test_grid_regression.py` -- Modified. Update assertions if they check for "Done" text.
- Various test files -- Modified. Update assertions for "Paid" text.

**F3.** C-4.4-1 through C-4.4-5 + full payday workflow regression suite + full test suite.

**F4.** Open the grid. Click a projected expense cell. Verify the full edit popover shows "Paid" button (not "Done"). Verify the status dropdown shows "Paid" option. Mark the expense, verify the badge tooltip says "Paid". Open the grid fresh -- verify income still shows "Received" button. Also: click a shadow transaction cell, expand to full edit -- verify the transfer form also shows "Paid" button (not "Done").

**F5.** Screenshot before/after showing the button label change and status badge tooltip change.

---

### Commit #7: Task 4.3

**F1.** `feat(grid): condense pay period date headers -- omit year for current year`

**F2.**
- `app/templates/grid/grid.html` -- Modified. Update date format logic.
- `tests/test_routes/test_grid.py` -- Modified. Add C-4.3-1, C-4.3-2.

**F3.** C-4.3-1, C-4.3-2 + payday workflow regression suite.

**F4.** Open the grid. Verify current-year periods show "MM/DD - MM/DD" format. If periods span into next year, verify those show "MM/DD/YY" format.

**F5.** Screenshot before/after showing the date format change.

---

### Commit #8a: Task 4.1 (Option A prototype)

**F1.** `feat(grid): prototype Option A -- transaction-name row headers`

**F2.**
- `app/routes/grid.py` -- Modified. Build row_keys from transaction data.
- `app/templates/grid/grid.html` -- Modified. Replace category iteration with row_key iteration.
- `app/static/css/app.css` -- Modified if row-label sizing needs adjustment.
- `tests/test_routes/test_grid.py` -- Modified. Update row structure assertions.

**F3.** C-4.1-1, C-4.1-3, C-4.1-4, C-4.1-5 + full payday workflow regression suite.

**F4.** Open the app. Perform the complete payday workflow (true-up balance, mark paycheck received, carry forward unpaid, mark expenses done, mark one credit, check projections). Verify every step completes without errors and the final balance is correct. Verify shadow transaction rows appear with their template names and transfer indicator icons.

**F5.** Screenshot of the grid BEFORE this commit. Screenshot AFTER. Compare row labels, vertical spacing, and overall layout.

---

### Commit #8b: Task 4.1 (Option B prototype) -- ALTERNATIVE

**F1.** `feat(grid): prototype Option B -- transaction name labels in cells`

**F2.**
- `app/templates/grid/_transaction_cell.html` -- Modified. Add name label.
- `app/static/css/app.css` -- Modified. Add `.txn-name-label` styling.
- `tests/test_routes/test_grid.py` -- Modified. Assert name label in cells.

**F3.** C-4.1-2, C-4.1-3, C-4.1-4 + full payday workflow regression suite.

**F4.** Same as Commit #8a.

**F5.** Same as Commit #8a.

**Decision point:** After implementing both prototypes (one on a branch), evaluate using the decision criteria in B2. Choose one and proceed. Delete the rejected prototype branch.

---

### Final Gate

**F1.** After all commits, run the full test suite:
```bash
timeout 660 pytest -v --tb=short
```

All tests must pass before reporting the phase as complete.
