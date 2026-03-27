# Implementation Plan: Section 4 -- UX/Grid Overhaul

**Version:** 3.0
**Date:** March 27, 2026
**Prerequisite:** All Section 3 (Critical Bug Fixes) and Section 3A (Transfer Architecture Rework) changes are implemented, tested, and merged.
**Scope:** Tasks 4.1, 4.3 through 4.17 from `docs/project_roadmap_v4-1.md`. Task 4.2 is complete (resolved during transfer architecture rework, Phase 3A-II).

---

## Grid Interaction Inventory

This inventory catalogs every HTMX interaction currently on the budget grid page (`app/templates/grid/grid.html`). Every task in this plan that modifies the grid must be verified against every item in this inventory. This inventory has been verified against the current codebase as of March 27, 2026 (post-transfer-rework).

### GI-1: Transaction Cell Click -> Quick Edit

- **Trigger element:** `<div class="txn-cell" hx-get="/transactions/<id>/quick-edit" hx-trigger="click" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_cell.html`, line 10-14)
- **HTTP request:** GET `/transactions/<txn_id>/quick-edit`
- **Server handler:** `transactions.get_quick_edit` in `app/routes/transactions.py:70`
- **Response:** HTMX fragment -- renders `grid/_transaction_quick_edit.html` with a single amount input and an expand button. For shadow transactions (transfer_id is not null), the same quick edit form is used.
- **DOM update:** `hx-target="#txn-cell-<id>"`, `hx-swap="innerHTML"`. Replaces the content inside the `<div id="txn-cell-<id>">` wrapper.
- **Side effects:** None. No HX-Trigger headers.
- **Dependencies:** `_get_owned_transaction()` ownership check.

### GI-2: Quick Edit Submit -> Update Transaction

- **Trigger element:** `<form class="txn-quick-edit" hx-patch="/transactions/<id>" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_quick_edit.html`, line 7-11)
- **HTTP request:** PATCH `/transactions/<txn_id>` with form data `estimated_amount`.
- **Server handler:** `transactions.update_transaction` in `app/routes/transactions.py` (line 89 area). For shadow transactions, detects `txn.transfer_id is not None` and routes through `transfer_service.update_transfer()`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html` with updated amount.
- **DOM update:** `hx-target="#txn-cell-<id>"`, `hx-swap="innerHTML"`.
- **Side effects:** Response header `HX-Trigger: balanceChanged`. Triggers GI-10.
- **Dependencies:** `_update_schema` (Marshmallow), `_get_owned_transaction()`. Transfer service for shadows.

### GI-3: Quick Edit Expand Button -> Full Edit Popover

- **Trigger element:** `<button class="txn-expand-btn" data-txn-id="<id>">` (in `grid/_transaction_quick_edit.html`, line 20-26). Click handled by delegated JS in `grid_edit.js`.
- **HTTP request:** JS `fetch('/transactions/<id>/full-edit')` (vanilla fetch in `grid_edit.js:90`).
- **Server handler:** `transactions.get_full_edit` in `app/routes/transactions.py:80`. For shadow transactions, detects `transfer_id`, loads the parent Transfer, and returns `transfers/_transfer_full_edit.html` with `source_txn_id`.
- **Response:** HTML fragment. Injected into `#txn-popover` via JS.
- **DOM update:** Popover content set via `popover.innerHTML = html` in `showPopover()`. Popover positioned absolutely relative to `.grid-scroll-wrapper`.
- **Side effects:** `htmx.process(popover)` called to wire up HTMX attributes.
- **Dependencies:** `positionPopover()` needs `.grid-scroll-wrapper` and a `<td>` ancestor. Statuses list needed for dropdown.

### GI-4: Full Edit Form Submit -> Update Transaction

- **Trigger element:** `<form hx-patch="/transactions/<id>" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 16-19). For shadow transactions opened from the grid, `transfers/_transfer_full_edit.html` uses `hx-target="#txn-cell-<source_txn_id>"`.
- **HTTP request:** PATCH `/transactions/<txn_id>` (regular) or PATCH `/transfers/<xfer_id>` (shadow via transfer form).
- **Server handler:** `transactions.update_transaction` or `transfers.update_transfer`. The transfer route's `_resolve_shadow_context()` helper detects `source_txn_id` and returns `grid/_transaction_cell.html`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **DOM update:** Targets `#txn-cell-<id>` with `innerHTML` swap.
- **Side effects:** `HX-Trigger: balanceChanged`. The `htmx:afterSwap` handler in `app.js:78-93` fires `save-flash` animation and closes the popover if the swap target is outside it.
- **Dependencies:** `_update_schema`, `_get_owned_transaction()`, statuses list.

### GI-5: Mark Done Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/mark-done" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 75-82 for expenses, line 112-120 for income "Received"). For shadow transactions, `transfers/_transfer_full_edit.html` line 88-95.
- **HTTP request:** POST `/transactions/<txn_id>/mark-done` or POST `/transfers/<xfer_id>/mark-done`.
- **Server handler:** `transactions.mark_done` in `app/routes/transactions.py:189`. For shadow transactions, routes through `transfer_service.update_transfer()`. Transfer route's `mark_done` (line 574) also uses `_resolve_shadow_context()`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **DOM update:** Targets `#txn-cell-<id>` with `innerHTML` swap.
- **Side effects:** `HX-Trigger: gridRefresh`. Triggers `window.location.reload()` via `app.js:40-43`.
- **Dependencies:** Status lookup by name (`"done"` or `"received"`). `_get_owned_transaction()`.

### GI-6: Mark Credit Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/mark-credit" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 84-91). Hidden for shadow transactions: `{% if not txn.transfer_id and txn.is_expense and txn.status.name == 'projected' %}`.
- **HTTP request:** POST `/transactions/<txn_id>/mark-credit`
- **Server handler:** `transactions.mark_credit` in `app/routes/transactions.py:251`. Returns 400 for shadow transactions.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`. Calls `credit_workflow.mark_as_credit()` which creates a payback transaction.
- **Dependencies:** `credit_workflow` service, `_get_owned_transaction()`.

### GI-7: Undo Credit Button (in Full Edit Popover)

- **Trigger element:** `<button hx-delete="/transactions/<id>/unmark-credit" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 92-100). Hidden for shadow transactions: `{% elif not txn.transfer_id and txn.status.name == 'credit' %}`.
- **HTTP request:** DELETE `/transactions/<txn_id>/unmark-credit`
- **Server handler:** `transactions.unmark_credit` in `app/routes/transactions.py:272`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`. Deletes the auto-generated payback transaction.
- **Dependencies:** `credit_workflow.unmark_credit()`, `_get_owned_transaction()`.

### GI-8: Cancel Transaction Button (in Full Edit Popover)

- **Trigger element:** `<button hx-post="/transactions/<id>/cancel" hx-target="#txn-cell-<id>" hx-swap="innerHTML">` (in `grid/_transaction_full_edit.html`, line 102-110)
- **HTTP request:** POST `/transactions/<txn_id>/cancel`
- **Server handler:** `transactions.cancel_transaction` in `app/routes/transactions.py:293`. For shadow transactions, routes through `transfer_service.update_transfer()`.
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html`.
- **Side effects:** `HX-Trigger: gridRefresh`.
- **Dependencies:** Status lookup by name `"cancelled"`, `_get_owned_transaction()`.

### GI-9: Empty Cell Click -> Quick Create

- **Trigger element:** `<div class="txn-cell txn-empty-cell" hx-get="/transactions/new/quick?category_id=X&period_id=Y&txn_type_name=Z" hx-trigger="click" hx-target="closest td" hx-swap="innerHTML">` (in `grid/_transaction_empty_cell.html`)
- **HTTP request:** GET `/transactions/new/quick?category_id=<id>&period_id=<id>&txn_type_name=<income|expense>`
- **Server handler:** `transactions.get_quick_create` in `app/routes/transactions.py:327`
- **Response:** HTMX fragment -- renders `grid/_transaction_quick_create.html`.
- **DOM update:** `hx-target="closest td"`, `hx-swap="innerHTML"`.
- **Side effects:** None.
- **Dependencies:** Category and PayPeriod ownership checks. Scenario lookup. TransactionType lookup by name via `txn_type_name`.

### GI-10: Balance Row Refresh (Triggered by balanceChanged)

- **Trigger element:** `<tfoot id="grid-summary" hx-get="/grid/balance-row?periods=N&offset=O&account_id=A" hx-trigger="balanceChanged from:body" hx-swap="outerHTML">` (in `grid/_balance_row.html`, line 11-16)
- **HTTP request:** GET `/grid/balance-row?periods=N&offset=O[&account_id=A]`
- **Server handler:** `grid.balance_row` in `app/routes/grid.py:201`
- **Response:** HTMX fragment -- renders `grid/_balance_row.html` (the entire `<tfoot>`). Contains a single `<tr>` row: Projected End Balance. Total Income, Total Expenses, and Net Cash Flow are inline `<tbody>` subtotal rows, NOT in the tfoot.
- **DOM update:** `hx-swap="outerHTML"`. Replaces the entire `<tfoot id="grid-summary">`.
- **Side effects:** None. The new tfoot retains `hx-trigger="balanceChanged from:body"`.
- **Dependencies:** `balance_calculator.calculate_balances()`, all periods, all transactions (including shadow transactions).

### GI-11: Anchor Balance Click -> Edit Form

- **Trigger element:** `<div id="anchor-display" hx-get="/accounts/<id>/anchor-form" hx-trigger="click" hx-swap="outerHTML">` (in `grid/_anchor_edit.html`)
- **HTTP request:** GET `/accounts/<account_id>/anchor-form`
- **Server handler:** `accounts.anchor_form` in `app/routes/accounts.py:508`
- **Response:** HTMX fragment -- renders `grid/_anchor_edit.html` with `editing=True`.
- **DOM update:** `hx-swap="outerHTML"`.
- **Side effects:** None.
- **Dependencies:** Account ownership check.

### GI-12: Anchor Balance Save

- **Trigger element:** `<form hx-patch="/accounts/<id>/true-up" hx-target="this" hx-swap="outerHTML">` (in `grid/_anchor_edit.html`)
- **HTTP request:** PATCH `/accounts/<account_id>/true-up` with form data `anchor_balance`.
- **Server handler:** `accounts.true_up` in `app/routes/accounts.py:451`
- **Response:** Anchor display (editing=False) plus OOB swap for `#anchor-as-of`.
- **DOM update:** `hx-target="this"`, `hx-swap="outerHTML"`.
- **Side effects:** `HX-Trigger: balanceChanged`. Triggers GI-10.
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

- **Trigger element:** `<form hx-post="/pay-periods/<id>/carry-forward" hx-swap="none">` (in `grid/grid.html`, line 84-91)
- **HTTP request:** POST `/pay-periods/<period_id>/carry-forward`
- **Server handler:** `transactions.carry_forward` in `app/routes/transactions.py:566`
- **Response:** Empty body with status 200.
- **DOM update:** `hx-swap="none"`.
- **Side effects:** `HX-Trigger: gridRefresh`. Full page reload.
- **Dependencies:** `carry_forward_service.carry_forward_unpaid()`, period ownership, scenario lookup.

### GI-15: Inline Create Submit

- **Trigger element:** `<form class="txn-quick-edit" data-mode="create" hx-post="/transactions/inline" hx-target="closest td" hx-swap="innerHTML">` (in `grid/_transaction_quick_create.html`, line 12-17)
- **HTTP request:** POST `/transactions/inline` with form data (category_id, pay_period_id, scenario_id, transaction_type_id, account_id, estimated_amount).
- **Server handler:** `transactions.create_inline` in `app/routes/transactions.py:451`
- **Response:** HTMX fragment -- renders `grid/_transaction_cell.html` with `wrap_div=True`.
- **DOM update:** `hx-target="closest td"`, `hx-swap="innerHTML"`.
- **Side effects:** `HX-Trigger: balanceChanged`. Triggers GI-10.
- **Dependencies:** `_inline_create_schema`, category/period ownership.

### GI-16: Add Transaction Modal Submit

- **Trigger element:** `<form hx-post="/transactions" hx-swap="none" data-modal-auto-close>` (in `grid/grid.html`, line 301-303 area)
- **HTTP request:** POST `/transactions` with full form data.
- **Server handler:** `transactions.create_transaction` in `app/routes/transactions.py:504`
- **Response:** HTMX fragment.
- **DOM update:** `hx-swap="none"`. The `htmx:afterRequest` handler detects `data-modal-auto-close`, hides the modal, and calls `location.reload()`.
- **Side effects:** `HX-Trigger: balanceChanged`. Moot due to page reload.
- **Dependencies:** `_create_schema`, period ownership.

### GI-17: Date Range Controls (Navigation)

- **Trigger element:** Arrow buttons and quick-select buttons in the grid header. Standard `<a href="...">` links, NOT HTMX.
- **HTTP request:** GET `/?periods=N&offset=O` (full page load).
- **Server handler:** `grid.index` in `app/routes/grid.py:33`
- **Response:** Full page.
- **DOM update:** Full page load.
- **Side effects:** None.
- **Dependencies:** All grid context variables.

### GI-18: Keyboard Navigation

- **Trigger element:** Keyboard events on the document (in `app.js:357-553`).
- **HTTP request:** None directly. Arrow keys move `cell-focused` class. Enter/Space click the focused cell's `.txn-cell`, triggering GI-1 or GI-9.
- **Server handler:** N/A (client-side only).
- **DOM update:** Adds/removes `cell-focused` class on `<td>` elements. `getDataRows()` filters out rows with classes: `section-banner-income`, `section-banner-expense`, `spacer-row`, `group-header-row`, `subtotal-row`, `net-cash-flow-row`.
- **Side effects:** After HTMX swaps, `htmx:afterSwap` handler restores focus position.
- **Dependencies:** `.grid-table` must exist. Data rows must NOT have excluded classes.

### GI-19: Transfer Cell Click -> Quick Edit -- RETIRED

> Retired by Phase 3A-I. The TRANSFERS section was removed from the grid. Shadow transactions now appear as regular transactions in INCOME/EXPENSES sections and use GI-1 instead. When a shadow transaction's expand button is clicked (GI-3), the server detects `transfer_id` and returns the transfer full edit form.

### GI-20: Quick Edit Escape -> Revert to Display

- **Trigger element:** Escape key in a quick edit input (handled by `grid_edit.js:221-260`).
- **HTTP request:** JS calls `htmx.ajax('GET', '/transactions/<id>/cell', ...)` or `htmx.ajax('GET', '/transactions/empty-cell?...', ...)`.
- **Server handler:** `transactions.get_cell` or `transactions.get_empty_cell`.
- **Response:** HTMX fragment -- original cell display or empty cell placeholder.
- **DOM update:** Targets `#txn-cell-<id>` (edit cancel) or `closest td` (create cancel).
- **Side effects:** None.
- **Dependencies:** Transaction/category/period ownership checks.

### GI-21: F2 Key -> Expand to Full Edit/Create

- **Trigger element:** F2 key in a quick edit/create input (handled by `grid_edit.js:197-219`).
- **HTTP request:** JS `fetch('/transactions/<id>/full-edit')` or `fetch('/transactions/new/full?...')`.
- **Server handler:** `transactions.get_full_edit` or `transactions.get_full_create`. For shadow transactions, returns transfer form.
- **Response:** Full edit/create popover HTML.
- **DOM update:** Injected into `#txn-popover`.
- **Side effects:** `htmx.process(popover)` called.
- **Dependencies:** Same as GI-3.

---

## Status String Audit

This audit catalogs every location in the codebase where `status.name` (or an equivalent string comparison against a status value) is used in logic. This is the foundation of task 4.4a. Organized by file.

**Reference data (from `scripts/seed_ref_tables.py` and `app/__init__.py`):**

The Status table has these entries (IDs are auto-incremented in insertion order):

| Expected ID | Name      | Purpose                                   |
| ----------- | --------- | ----------------------------------------- |
| 1           | projected | Default status for future transactions    |
| 2           | done      | Expense settled from checking             |
| 3           | received  | Income deposited                          |
| 4           | credit    | Expense charged to credit card            |
| 5           | cancelled | Transaction cancelled                     |
| 6           | settled   | Terminal state (done/received -> settled) |

**Note:** IDs are auto-incremented by PostgreSQL SERIAL. The expected IDs above assume fresh database seeding in the listed order. The implementation MUST define named constants that are verified against the database at application startup, not hardcoded integer literals. See the implementation approach for task 4.4a.

### Python Files -- Services

**`app/services/balance_calculator.py`:**

| Line    | Code                                                                                                       | Purpose                                     | Replacement                                                                                           |
| ------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 32      | `SETTLED_STATUSES = frozenset({"done", "received"})`                                                       | Define set of settled status names          | Replace with ID-based frozenset: `SETTLED_STATUS_IDS = frozenset({StatusID.DONE, StatusID.RECEIVED})` |
| 101-102 | `status_name = txn.status.name if txn.status else "projected"` / `if status_name in SETTLED_STATUSES:`     | Check if txn is settled in anchor period    | Use `txn.status_id` and `SETTLED_STATUS_IDS`                                                          |
| 246-247 | `status_name = txn.status.name ...` / `if status_name in ("cancelled",):`                                  | Skip cancelled in non-anchor                | Use `txn.status_id == StatusID.CANCELLED`                                                             |
| 287-294 | `status_name = ...` / `if status_name in ("credit", "cancelled"):` / `if status_name in SETTLED_STATUSES:` | Balance calculation for checking            | Use status_id comparisons                                                                             |
| 321-324 | `status_name = ...` / `if status_name in ("credit", "cancelled", "done", "received"):`                     | Subtotal calculations -- skip non-projected | Use `txn.status_id in NON_PROJECTED_STATUS_IDS`                                                       |

**`app/services/recurrence_engine.py`:**

| Line    | Code                                                                                   | Purpose                                             | Replacement                                                                                                              |
| ------- | -------------------------------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 40      | `IMMUTABLE_STATUSES = frozenset({"done", "received", "credit", "cancelled"})`          | Statuses the recurrence engine must never overwrite | Replace with `IMMUTABLE_STATUS_IDS = frozenset({StatusID.DONE, StatusID.RECEIVED, StatusID.CREDIT, StatusID.CANCELLED})` |
| 90      | `projected_status = db.session.query(Status).filter_by(name="projected").one()`        | Get projected status for new transactions           | Use `StatusID.PROJECTED` constant                                                                                        |
| 108-111 | `status_name = existing_txn.status.name ...` / `if status_name in IMMUTABLE_STATUSES:` | Skip immutable transactions during regeneration     | Use `existing_txn.status_id in IMMUTABLE_STATUS_IDS`                                                                     |
| 215-218 | Same pattern                                                                           | Skip immutable during cleanup                       | Same replacement                                                                                                         |

**`app/services/transfer_recurrence.py`:**

| Line    | Code                                                                            | Purpose                       | Replacement                                  |
| ------- | ------------------------------------------------------------------------------- | ----------------------------- | -------------------------------------------- |
| 31      | `IMMUTABLE_STATUSES = frozenset({"done", "received", "credit", "cancelled"})`   | Same as recurrence_engine     | Same replacement                             |
| 71      | `projected_status = db.session.query(Status).filter_by(name="projected").one()` | Get projected status          | Use `StatusID.PROJECTED`                     |
| 82-83   | `status_name = xfer.status.name ...` / `if status_name in IMMUTABLE_STATUSES:`  | Skip immutable transfers      | Use `xfer.status_id in IMMUTABLE_STATUS_IDS` |
| 166-168 | Same pattern                                                                    | Skip immutable during cleanup | Same replacement                             |

**`app/services/credit_workflow.py`:**

| Line | Code                                                                            | Purpose                          | Replacement                               |
| ---- | ------------------------------------------------------------------------------- | -------------------------------- | ----------------------------------------- |
| 60   | `if txn.status and txn.status.name == "credit":`                                | Check if already credit          | Use `txn.status_id == StatusID.CREDIT`    |
| 70   | `if txn.status.name != "projected":`                                            | Only projected can become credit | Use `txn.status_id != StatusID.PROJECTED` |
| 77   | `credit_status = db.session.query(Status).filter_by(name="credit").one()`       | Fetch credit status              | Use `StatusID.CREDIT`                     |
| 78   | `projected_status = db.session.query(Status).filter_by(name="projected").one()` | Fetch projected status           | Use `StatusID.PROJECTED`                  |
| 144  | `projected_status = db.session.query(Status).filter_by(name="projected").one()` | Revert to projected              | Use `StatusID.PROJECTED`                  |

**`app/services/carry_forward_service.py`:**

| Line | Code                                                                            | Purpose                                      | Replacement              |
| ---- | ------------------------------------------------------------------------------- | -------------------------------------------- | ------------------------ |
| 65   | `projected_status = db.session.query(Status).filter_by(name="projected").one()` | Find projected transactions to carry forward | Use `StatusID.PROJECTED` |

**`app/services/chart_data_service.py`:**

| Line | Code                                                                       | Purpose                             | Replacement              |
| ---- | -------------------------------------------------------------------------- | ----------------------------------- | ------------------------ |
| 385  | `done = db.session.query(Status).filter_by(name="done").first()`           | Fetch done status for chart filters | Use `StatusID.DONE`      |
| 386  | `projected = db.session.query(Status).filter_by(name="projected").first()` | Fetch projected status              | Use `StatusID.PROJECTED` |

**`app/services/transfer_service.py`:**

| Line          | Code                                        | Purpose                              | Replacement         |
| ------------- | ------------------------------------------- | ------------------------------------ | ------------------- |
| 347 (comment) | `# Initial status (typically 'projected').` | Comment only -- no string comparison | Update comment only |

### Python Files -- Routes

**`app/routes/transactions.py`:**

| Line    | Code                                                                               | Purpose                                                          | Replacement               |
| ------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------- |
| 204     | `status = db.session.query(Status).filter_by(name="received").one()`               | Mark income as received                                          | Use `StatusID.RECEIVED`   |
| 206     | `status = db.session.query(Status).filter_by(name="done").one()`                   | Mark expense as done                                             | Use `StatusID.DONE`       |
| 213     | `done_status = db.session.query(Status).filter_by(name="done").one()`              | Shadow txn -- use done for transfer service                      | Use `StatusID.DONE`       |
| 305     | `cancelled = db.session.query(Status).filter_by(name="cancelled").one()`           | Cancel shadow transaction                                        | Use `StatusID.CANCELLED`  |
| 315     | `status = db.session.query(Status).filter_by(name="cancelled").one()`              | Cancel regular transaction                                       | Use `StatusID.CANCELLED`  |
| 335/351 | `txn_type = db.session.query(TransactionType).filter_by(name=txn_type_name).one()` | Quick create -- lookup transaction type by name from query param | See ref table audit below |
| 381/394 | Same pattern                                                                       | Full create -- same                                              | Same                      |
| 427     | Same pattern                                                                       | Empty cell -- same                                               | Same                      |
| 481     | `projected = db.session.query(Status).filter_by(name="projected").one()`           | Inline create                                                    | Use `StatusID.PROJECTED`  |
| 524     | `projected = db.session.query(Status).filter_by(name="projected").one()`           | Full create                                                      | Use `StatusID.PROJECTED`  |

**`app/routes/transfers.py`:**

| Line | Code                                                                               | Purpose                               | Replacement              |
| ---- | ---------------------------------------------------------------------------------- | ------------------------------------- | ------------------------ |
| 247  | `pattern = db.session.query(RecurrencePattern).filter_by(name=pattern_name).one()` | Lookup recurrence pattern by name     | See ref table audit      |
| 334  | `projected_status = db.session.query(Status).filter_by(name="projected").one()`    | Create transfer with projected status | Use `StatusID.PROJECTED` |
| 374  | `projected_status = db.session.query(Status).filter_by(name="projected").one()`    | Create one-time transfer              | Use `StatusID.PROJECTED` |
| 513  | `projected = db.session.query(Status).filter_by(name="projected").one()`           | Undelete transfer                     | Use `StatusID.PROJECTED` |
| 574  | `done_status = db.session.query(Status).filter_by(name="done").one()`              | Mark transfer done                    | Use `StatusID.DONE`      |
| 603  | `cancelled_status = db.session.query(Status).filter_by(name="cancelled").one()`    | Cancel transfer                       | Use `StatusID.CANCELLED` |

**`app/routes/templates.py`:**

| Line | Code                                                                               | Purpose                           | Replacement              |
| ---- | ---------------------------------------------------------------------------------- | --------------------------------- | ------------------------ |
| 233  | `pattern = db.session.query(RecurrencePattern).filter_by(name=pattern_name).one()` | Lookup recurrence pattern by name | See ref table audit      |
| 318  | `projected_status = db.session.query(Status).filter_by(name="projected").one()`    | Create txn from template          | Use `StatusID.PROJECTED` |
| 347  | `projected_status = db.session.query(Status).filter_by(name="projected").one()`    | Same                              | Same                     |

**`app/routes/savings.py`:**

| Line    | Code                                                                               | Purpose                       | Replacement                               |
| ------- | ---------------------------------------------------------------------------------- | ----------------------------- | ----------------------------------------- |
| 85      | `income_type = db.session.query(TransactionType).filter_by(name="income").first()` | Lookup income type            | See ref table audit                       |
| 100     | `db.session.query(AccountType).filter_by(name="hysa").first()`                     | Lookup HYSA account type      | See ref table audit                       |
| 112-118 | `db.session.query(AccountType).filter_by(name="mortgage").first()` etc.            | Lookup debt account types     | See ref table audit                       |
| 400     | `if txn.is_expense and txn.status and txn.status.name in ("done", "received"):`    | Emergency fund expense filter | Use `txn.status_id in SETTLED_STATUS_IDS` |
| 412     | `db.session.query(AccountType).filter_by(name="savings").first()`                  | Lookup savings type           | See ref table audit                       |

**`app/routes/accounts.py`:**

| Line | Code                                                                  | Purpose                              | Replacement         |
| ---- | --------------------------------------------------------------------- | ------------------------------------ | ------------------- |
| 133  | `if account_type and account_type.name == "hysa":`                    | Post-creation redirect for HYSA      | See ref table audit |
| 143  | `if account_type and account_type.name == "mortgage":`                | Post-creation redirect for mortgage  | See ref table audit |
| 145  | `if account_type and account_type.name == "auto_loan":`               | Post-creation redirect for auto loan | See ref table audit |
| 548  | `if not account.account_type or account.account_type.name != "hysa":` | HYSA detail guard                    | See ref table audit |
| 649  | `if not account.account_type or account.account_type.name != "hysa":` | HYSA update guard                    | See ref table audit |

**`app/routes/auto_loan.py`:**

| Line | Code                                                                       | Purpose                | Replacement         |
| ---- | -------------------------------------------------------------------------- | ---------------------- | ------------------- |
| 37   | `if not account.account_type or account.account_type.name != "auto_loan":` | Auto loan guard        | See ref table audit |
| 111  | Same pattern                                                               | Auto loan update guard | Same                |

**`app/routes/mortgage.py`:**

| Line | Code                                                                      | Purpose               | Replacement         |
| ---- | ------------------------------------------------------------------------- | --------------------- | ------------------- |
| 47   | `if not account.account_type or account.account_type.name != "mortgage":` | Mortgage guard        | See ref table audit |
| 172  | Same pattern                                                              | Mortgage update guard | Same                |

**`app/routes/salary.py`:**

| Line | Code                                                                             | Purpose            | Replacement         |
| ---- | -------------------------------------------------------------------------------- | ------------------ | ------------------- |
| 158  | `income_type = db.session.query(TransactionType).filter_by(name="income").one()` | Lookup income type | See ref table audit |

**`app/routes/retirement.py`:**

| Line | Code                                                                               | Purpose            | Replacement         |
| ---- | ---------------------------------------------------------------------------------- | ------------------ | ------------------- |
| 174  | `income_type = db.session.query(TransactionType).filter_by(name="income").first()` | Lookup income type | See ref table audit |

**`app/routes/investment.py`:**

| Line | Code                                                                               | Purpose            | Replacement         |
| ---- | ---------------------------------------------------------------------------------- | ------------------ | ------------------- |
| 142  | `income_type = db.session.query(TransactionType).filter_by(name="income").first()` | Lookup income type | See ref table audit |
| 361  | Same                                                                               | Same               | Same                |

### Python Files -- Models

**`app/models/transaction.py`:**

| Line | Code                                                                       | Purpose                                         | Replacement                                                   |
| ---- | -------------------------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------- |
| 116  | `if self.status and self.status.name in ("credit", "cancelled"):`          | effective_amount returns 0 for credit/cancelled | Use `self.status_id in (StatusID.CREDIT, StatusID.CANCELLED)` |
| 118  | `if self.status and self.status.name in ("done", "received"):`             | effective_amount uses actual_amount for settled | Use `self.status_id in (StatusID.DONE, StatusID.RECEIVED)`    |
| 125  | `return self.transaction_type and self.transaction_type.name == "income"`  | is_income property                              | Use `self.transaction_type_id == TxnTypeID.INCOME`            |
| 130  | `return self.transaction_type and self.transaction_type.name == "expense"` | is_expense property                             | Use `self.transaction_type_id == TxnTypeID.EXPENSE`           |

**`app/models/transfer.py`:**

| Line | Code                                                  | Purpose                                  | Replacement                                |
| ---- | ----------------------------------------------------- | ---------------------------------------- | ------------------------------------------ |
| 95   | `if self.status and self.status.name == "cancelled":` | effective_amount returns 0 for cancelled | Use `self.status_id == StatusID.CANCELLED` |

### Template Files

**`app/templates/grid/grid.html`:**

| Line | Code                                                                 | Purpose                               | Action                                                |
| ---- | -------------------------------------------------------------------- | ------------------------------------- | ----------------------------------------------------- |
| 140  | `txn.status.name != 'cancelled'`                                     | Filter cancelled from income display  | Replace with `txn.status_id != STATUS_IDS.cancelled`  |
| 168  | `txn.status.name not in ('credit', 'cancelled', 'done', 'received')` | Income subtotal: only projected       | Replace with `txn.status_id not in NON_PROJECTED_IDS` |
| 220  | `txn.status.name != 'cancelled'`                                     | Filter cancelled from expense display | Same as line 140                                      |
| 248  | `txn.status.name not in ('credit', 'cancelled', 'done', 'received')` | Expense subtotal: only projected      | Same as line 168                                      |
| 264  | `txn.status.name not in ('credit', 'cancelled', 'done', 'received')` | Net Cash Flow: only projected         | Same as line 168                                      |

**`app/templates/grid/_transaction_cell.html`:**

| Line | Code                                    | Purpose                               | Action                                           |
| ---- | --------------------------------------- | ------------------------------------- | ------------------------------------------------ | -------------------------------------------------- |
| 17   | `({{ t.status.name }})` in aria-label   | Display status name to screen readers | Keep name for display, but refactor logic checks |
| 34   | `t.status.name in ('done', 'received')` | Show checkmark badge                  | Replace with `t.status_id in SETTLED_IDS`        |
| 35   | `t.status.name                          | capitalize` in title and aria-label   | Display label                                    | Replace with `t.status.display_label` (after 4.4b) |
| 36   | `t.status.name == 'credit'`             | Show CC badge                         | Replace with `t.status_id == STATUS_IDS.credit`  |

**`app/templates/grid/_transaction_full_edit.html`:**

| Line | Code                             | Purpose                         | Action                                               |
| ---- | -------------------------------- | ------------------------------- | ---------------------------------------------------- |
| 74   | `txn.status.name != 'done'`      | Show Done button for expenses   | Replace with `txn.status_id != STATUS_IDS.done`      |
| 84   | `txn.status.name == 'projected'` | Show Mark Credit button         | Replace with `txn.status_id == STATUS_IDS.projected` |
| 92   | `txn.status.name == 'credit'`    | Show Undo Credit button         | Replace with `txn.status_id == STATUS_IDS.credit`    |
| 102  | `txn.status.name == 'projected'` | Show Cancel button              | Replace with `txn.status_id == STATUS_IDS.projected` |
| 112  | `txn.status.name == 'projected'` | Show Received button for income | Replace with `txn.status_id == STATUS_IDS.projected` |

**`app/templates/grid/_transaction_full_create.html`:**

| Line | Code                    | Purpose                                 | Action                                      |
| ---- | ----------------------- | --------------------------------------- | ------------------------------------------- |
| 54   | `s.name == 'projected'` | Pre-select projected in status dropdown | Replace with `s.id == STATUS_IDS.projected` |

**`app/templates/transfers/_transfer_cell.html`:**

| Line | Code                                       | Purpose              | Action                                       |
| ---- | ------------------------------------------ | -------------------- | -------------------------------------------- | -------------------------- |
| 16   | `({{ xfer.status.name }})` in aria-label   | Display              | Keep for display, but add display_label      |
| 34   | `xfer.status.name in ('done', 'received')` | Show checkmark badge | Replace with `xfer.status_id in SETTLED_IDS` |
| 35   | `xfer.status.name                          | capitalize` in title | Display label                                | Replace with display_label |

**`app/templates/transfers/_transfer_full_edit.html`:**

| Line | Code                              | Purpose          | Action                                                |
| ---- | --------------------------------- | ---------------- | ----------------------------------------------------- |
| 87   | `xfer.status.name == 'projected'` | Show Done button | Replace with `xfer.status_id == STATUS_IDS.projected` |

### Application Initialization

**`app/__init__.py`:**

| Line | Code                                                                          | Purpose          | Action                               |
| ---- | ----------------------------------------------------------------------------- | ---------------- | ------------------------------------ |
| 367  | `Status: ["projected", "done", "received", "credit", "cancelled", "settled"]` | Test/dev seeding | Keep -- this is seed data, not logic |

### Test Files

Test files use `db.session.query(Status).filter_by(name="...")` extensively for fixture setup. A representative count:

- `tests/conftest.py`: Lines 528, 652, 910 -- status lookups by name for fixtures
- `tests/test_audit_fixes.py`: Lines 114, 135, 156, 177, 461, 499, 535, 536
- `tests/test_services/test_balance_calculator.py`: ~30+ occurrences
- `tests/test_services/test_credit_workflow.py`: ~10+ occurrences
- `tests/test_services/test_recurrence_engine.py`: ~15+ occurrences
- `tests/test_services/test_transfer_recurrence.py`: ~10+ occurrences
- `tests/test_services/test_transfer_service.py`: ~15+ occurrences
- `tests/test_routes/test_grid.py`: ~20+ occurrences
- `tests/test_routes/test_transfers.py`: ~20+ occurrences
- `tests/test_routes/test_transaction_guards.py`: ~10+ occurrences

**Test file strategy:** Tests use `filter_by(name=...)` to obtain status objects for fixture setup. After 4.4a, tests should use the same `StatusID` constants as production code. The name-based lookups in test fixtures are acceptable as a transitional pattern (they are setup code, not logic under test), but should be updated to use constants for consistency. Tests that assert on status name strings in response HTML (e.g., checking for "Done" in button text) will be updated in 4.4b.

---

## Reference Table String Audit

This audit extends the status audit to all `ref.*` tables where `.name` is used in logic instead of `.id`.

### TransactionType

**Expected IDs:** 1 = income, 2 = expense (auto-increment insertion order)

| File                               | Line     | Code                                      | Purpose                       | Replacement                                     |
| ---------------------------------- | -------- | ----------------------------------------- | ----------------------------- | ----------------------------------------------- |
| `app/models/transaction.py`        | 125      | `self.transaction_type.name == "income"`  | is_income property            | `self.transaction_type_id == TxnTypeID.INCOME`  |
| `app/models/transaction.py`        | 130      | `self.transaction_type.name == "expense"` | is_expense property           | `self.transaction_type_id == TxnTypeID.EXPENSE` |
| `app/services/transfer_service.py` | 295      | `filter_by(name="expense").one()`         | Shadow expense type           | `TxnTypeID.EXPENSE`                             |
| `app/services/transfer_service.py` | 298      | `filter_by(name="income").one()`          | Shadow income type            | `TxnTypeID.INCOME`                              |
| `app/services/transfer_service.py` | 388, 391 | Same pattern                              | Bulk shadow creation          | Same                                            |
| `app/services/transfer_service.py` | 719, 722 | Same pattern                              | Template shadow creation      | Same                                            |
| `app/services/credit_workflow.py`  | 79       | `filter_by(name="expense").one()`         | Payback type                  | `TxnTypeID.EXPENSE`                             |
| `app/routes/transactions.py`       | 351      | `filter_by(name=txn_type_name).one()`     | Quick create from query param | Discussed below                                 |
| `app/routes/transactions.py`       | 394      | Same                                      | Full create                   | Same                                            |
| `app/routes/transactions.py`       | 427      | Same                                      | Empty cell                    | Same                                            |
| `app/routes/savings.py`            | 85       | `filter_by(name="income").first()`        | Dashboard income filter       | `TxnTypeID.INCOME`                              |
| `app/routes/salary.py`             | 158      | `filter_by(name="income").one()`          | Salary income link            | `TxnTypeID.INCOME`                              |
| `app/routes/retirement.py`         | 174      | `filter_by(name="income").first()`        | Retirement income             | `TxnTypeID.INCOME`                              |
| `app/routes/investment.py`         | 142, 361 | `filter_by(name="income").first()`        | Investment income             | `TxnTypeID.INCOME`                              |

**Special case -- `txn_type_name` query parameter:** The grid passes `txn_type_name=income` or `txn_type_name=expense` as a query parameter in empty cell URLs and quick create forms (`grid/_transaction_empty_cell.html:11`, `grid_edit.js:125`, `grid_edit.js:244`). The route handler uses this string to look up the TransactionType by name. After the refactor, the grid should pass `transaction_type_id` instead of `txn_type_name`. This requires coordinated changes in: the grid template, the JS, the quick create template, and the route handlers.

**Template occurrences (display only -- lower priority):**

| File                  | Line     | Code                                               | Purpose                             | Action                                    |
| --------------------- | -------- | -------------------------------------------------- | ----------------------------------- | ----------------------------------------- |
| `grid/grid.html`      | 153, 233 | `{% set txn_type_name = "income" %}` / `"expense"` | Set for empty cell template         | Change to pass ID                         |
| `grid/grid.html`      | 321      | `tt.name == 'expense'`                             | Default selection in modal dropdown | Change to `tt.id == TXN_TYPE_IDS.expense` |
| `templates/list.html` | 48       | `t.transaction_type.name == 'income'`              | Icon selection                      | Change to ID comparison                   |
| `templates/form.html` | 55       | `tt.name == 'expense'`                             | Default selection in template form  | Change to ID                              |

### AccountType

**Expected IDs:** 1=checking, 2=savings, 3=hysa, ..., 8=mortgage, 9=auto_loan, etc. (insertion order)

| File                                 | Line     | Code                                      | Purpose                  | Replacement                          |
| ------------------------------------ | -------- | ----------------------------------------- | ------------------------ | ------------------------------------ |
| `app/routes/accounts.py`             | 133      | `account_type.name == "hysa"`             | Post-creation redirect   | `account_type_id == AcctTypeID.HYSA` |
| `app/routes/accounts.py`             | 143      | `account_type.name == "mortgage"`         | Post-creation redirect   | `AcctTypeID.MORTGAGE`                |
| `app/routes/accounts.py`             | 145      | `account_type.name == "auto_loan"`        | Post-creation redirect   | `AcctTypeID.AUTO_LOAN`               |
| `app/routes/accounts.py`             | 548      | `account_type.name != "hysa"`             | HYSA guard               | ID comparison                        |
| `app/routes/accounts.py`             | 649      | Same                                      | HYSA update guard        | Same                                 |
| `app/routes/auto_loan.py`            | 37, 111  | `account_type.name != "auto_loan"`        | Auto loan guard          | ID comparison                        |
| `app/routes/mortgage.py`             | 47, 172  | `account_type.name != "mortgage"`         | Mortgage guard           | ID comparison                        |
| `app/routes/savings.py`              | 100      | `filter_by(name="hysa").first()`          | HYSA type lookup         | `AcctTypeID.HYSA`                    |
| `app/routes/savings.py`              | 112-118  | `filter_by(name="mortgage").first()` etc. | Debt type lookups        | ID constants                         |
| `app/routes/savings.py`              | 412      | `filter_by(name="savings").first()`       | Savings type lookup      | `AcctTypeID.SAVINGS`                 |
| `app/services/account_resolver.py`   | 44       | `filter_by(name="checking").first()`      | Default checking account | `AcctTypeID.CHECKING`                |
| `app/services/auth_service.py`       | 391      | `filter_by(name="checking").one()`        | User setup               | `AcctTypeID.CHECKING`                |
| `app/services/chart_data_service.py` | 218, 253 | `account.account_type.name`               | Chart axis assignment    | Use ID or category                   |
| `app/services/chart_data_service.py` | 486      | `a.account_type.name`                     | Account list for charts  | Display use -- keep name for display |

**Template occurrences (display/routing):**

| File                        | Line                    | Code                                          | Purpose                                | Action                                               |
| --------------------------- | ----------------------- | --------------------------------------------- | -------------------------------------- | ---------------------------------------------------- |
| `savings/dashboard.html`    | 50-103 (12 occurrences) | `ad.account.account_type.name == 'hysa'` etc. | Icon and link routing per account type | Replace with ID comparisons using template constants |
| `transfers/form.html`       | 59, 73                  | `acct.account_type.name\|format_account_type` | Display formatted type name            | Keep -- display only, uses custom filter             |
| `accounts/list.html`        | 42                      | `acct.account_type.name\|format_account_type` | Display                                | Keep -- display only                                 |
| `savings/goal_form.html`    | 31                      | Same                                          | Display                                | Keep -- display only                                 |
| `investment/dashboard.html` | 18                      | Same                                          | Display                                | Keep -- display only                                 |

### RecurrencePattern

| File                      | Line | Code                                 | Purpose                        | Replacement                                 |
| ------------------------- | ---- | ------------------------------------ | ------------------------------ | ------------------------------------------- |
| `app/routes/transfers.py` | 247  | `filter_by(name=pattern_name).one()` | Lookup pattern from form input | Accept pattern ID from form instead of name |
| `app/routes/templates.py` | 233  | `filter_by(name=pattern_name).one()` | Same                           | Same                                        |

**Note:** The recurrence pattern lookup is used during template creation/editing. The form dropdown sends the pattern name. Changing to ID requires updating the form `<select>` option values from names to IDs and updating the route handler to use `filter_by(id=pattern_id)`.

### FilingStatus (display only -- no logic use)

| File                        | Line | Code                                             | Purpose               | Action               |
| --------------------------- | ---- | ------------------------------------------------ | --------------------- | -------------------- |
| `salary/list.html`          | 45   | `p.filing_status.name\|replace('_', ' ')\|title` | Display filing status | Keep -- display only |
| `salary/tax_config.html`    | 33   | Same                                             | Display               | Keep -- display only |
| `settings/_tax_config.html` | 13   | Same                                             | Display               | Keep -- display only |

---

## Commit Plan

### Ordering Rationale

1. **Task 4.4a (Status ID refactor) MUST come first.** It touches the most files in the codebase. Every subsequent task that adds new code uses the corrected ID-based pattern from day one.
2. **Task 4.4c (Ref table audit) immediately follows 4.4a.** Same type of work, same mindset.
3. **Task 4.4b (Rename done to paid) follows 4.4a.** Once logic uses IDs, the name change is safe.
4. **Task 4.13 (Emergency fund fix) is a correctness fix** and is prioritized over cosmetic changes.
5. **Task 4.1 (Grid layout) comes before 4.12 (Tooltips).** The layout decision affects what tooltips need to show.
6. **Tasks 4.7/4.8 (Account parameter setup) are adjacent.**
7. **Task 4.15 (Auto loan fixes) follows 4.7/4.8.**

### Commit Sequence

| Commit | Task    | Type     | Commit Message                                                                       |
| ------ | ------- | -------- | ------------------------------------------------------------------------------------ |
| #0     | Prereq  | test     | `test(grid): add payday workflow end-to-end regression test suite`                   |
| #1     | 4.4a    | refactor | `refactor(status): replace status name lookups with ID constants`                    |
| #2     | 4.4c    | refactor | `refactor(ref): replace all ref table name lookups with ID constants`                |
| #3     | 4.4b    | feat     | `feat(status): rename "Done" to "Paid" for expense status display`                   |
| #4     | 4.13    | fix      | `fix(savings): include transfer expenses in emergency fund coverage`                 |
| #5     | 4.5     | feat     | `feat(salary): improve deduction frequency display with descriptive labels`          |
| #6     | 4.6     | feat     | `feat(salary): reorganize tax config page -- adjustable settings first`              |
| #7     | 4.11    | feat     | `feat(salary): move View Breakdown and View Projection buttons higher`               |
| #8     | 4.7/4.8 | feat     | `feat(accounts): redirect to parameter config after creating parameterized accounts` |
| #9     | 4.15    | fix      | `fix(auto_loan): add editable term field and pre-populate principal from balance`    |
| #10    | 4.14    | feat     | `feat(accounts): add balance projection to checking account detail page`             |
| #11    | 4.16    | fix      | `fix(retirement): preserve form data and highlight fields on validation errors`      |
| #12    | 4.17    | feat     | `feat(retirement): clarify return rate slider behavior with explanatory text`        |
| #13    | 4.9     | feat     | `feat(charts): add dash patterns and varied line weights to balance chart`           |
| #14    | 4.10    | feat     | `feat(charts): add time frame controls to balance over time chart`                   |
| #15    | 4.3     | feat     | `feat(grid): condense pay period date headers -- omit year for current year`         |
| #16    | 4.1     | feat     | `feat(grid): show transaction names in row headers for clarity`                      |
| #17    | 4.12    | feat     | `feat(grid): enhance tooltips with full amount and faster display`                   |

---

## Per-Task Sections

### Commit #0: Payday Workflow Regression Test Suite

**A. Commit message:** `test(grid): add payday workflow end-to-end regression test suite`

**B. Problem statement.** Before any Section 4 changes, a regression test suite must exist that verifies the complete payday workflow (true-up, mark received, carry forward, mark done, mark credit, balance refresh). This safety net protects against breakage during the DOM restructuring and status refactoring that follows.

**C. Files modified.**

- `tests/test_routes/test_grid_regression.py` -- New. Contains 7 regression tests covering the payday workflow interaction sequence.

**D. Implementation approach.**

Create a new test file `tests/test_routes/test_grid_regression.py` with a `TestPaydayWorkflowRegression` class. Each test exercises a distinct step of the payday workflow as documented in the requirements (v2 Section 3). Tests use existing fixtures (`seed_user`, `auth_client`) and create necessary data (pay periods, transactions, accounts) inline. Every test verifies both the HTTP response and the database state after the operation.

The test file must import:

- `seed_user`, `auth_client` fixtures from conftest
- `Status`, `TransactionType`, `AccountType` from models
- `Transaction`, `PayPeriod`, `Account`, `Scenario` from budget models

Each test follows the pattern: create fixtures -> perform action -> assert response -> assert database state.

**E. Test cases.**

**C-0-1:** `test_trueup_anchor_balance`

- Setup: `seed_user`, `auth_client`, checking account with existing anchor balance, current pay period.
- Action: PATCH `/accounts/<id>/true-up` with `anchor_balance=5000.00`.
- Expected: 200 response. `HX-Trigger` header contains `balanceChanged`. Database `anchor_balance` updated. `anchor_period_id` set to current period.
- New test.

**C-0-2:** `test_mark_paycheck_received`

- Setup: `seed_user`, `auth_client`, income transaction with status projected.
- Action: POST `/transactions/<id>/mark-done`.
- Expected: 200 response. Transaction status becomes "received" (because is_income). `HX-Trigger` contains `gridRefresh`. Checkmark badge in response HTML.
- New test.

**C-0-3:** `test_carry_forward_unpaid`

- Setup: `seed_user`, `auth_client`, past period with 2 projected + 1 done transaction, current period.
- Action: POST `/pay-periods/<past_period_id>/carry-forward`.
- Expected: 200 response. `HX-Trigger` contains `gridRefresh`. 2 projected transactions moved to current period. Done transaction stays in past period.
- New test.

**C-0-4:** `test_mark_expense_done`

- Setup: `seed_user`, `auth_client`, expense transaction with status projected.
- Action: POST `/transactions/<id>/mark-done`.
- Expected: 200 response. Transaction status becomes "done". `HX-Trigger` contains `gridRefresh`.
- New test.

**C-0-5:** `test_mark_credit_creates_payback`

- Setup: `seed_user`, `auth_client`, expense transaction with status projected, next pay period exists.
- Action: POST `/transactions/<id>/mark-credit`.
- Expected: 200 response. Transaction status becomes "credit". A payback transaction exists in the next period with `credit_payback_for_id` pointing to the original. `HX-Trigger` contains `gridRefresh`.
- New test.

**C-0-6:** `test_balance_row_refresh`

- Setup: `seed_user`, `auth_client`, periods with transactions, checking account.
- Action: GET `/grid/balance-row?periods=6&offset=0`.
- Expected: 200 response. Response contains `<tfoot id="grid-summary">`. Contains exactly 1 `<tr>` in the tfoot. Contains `hx-trigger="balanceChanged from:body"`.
- New test.

**C-0-7:** `test_full_payday_sequence`

- Setup: `seed_user`, `auth_client`, 3 periods (past, current, future) with income + expenses, checking account with anchor.
- Action: Execute steps 1-5 in sequence: true-up -> mark paycheck -> carry forward -> mark expense done -> mark one credit.
- Expected: All 5 requests return 200. Final GET `/grid/balance-row` returns correct calculated balances matching hand-computed values.
- New test (integration).

**F. Manual verification steps.** N/A -- test-only commit.

**G. Downstream effects.** None. This commit only adds tests.

**H. Rollback notes.** No dependencies. Can be reverted independently.

---

### Commit #1: Task 4.4a -- Refactor Status Lookups to Use ID Constants

**A. Commit message:** `refactor(status): replace status name lookups with ID constants`

**B. Problem statement.** The codebase uses `Status.filter_by(name="done")` and `txn.status.name == "done"` throughout routes, services, models, and templates for logic decisions. This makes the `name` column load-bearing: renaming "done" to "paid" would break every comparison. The `ref.statuses` table has integer primary keys specifically to allow display names to change without affecting logic. The status audit above documents 60+ occurrences across 15+ files that must be converted.

**C. Files modified.**

- `app/constants.py` -- New. Define `StatusID` and `TxnTypeID` named constant classes.
- `app/services/balance_calculator.py` -- Modified. Replace all status name comparisons with ID comparisons.
- `app/services/recurrence_engine.py` -- Modified. Replace IMMUTABLE_STATUSES and projected lookup.
- `app/services/transfer_recurrence.py` -- Modified. Same.
- `app/services/credit_workflow.py` -- Modified. Replace all status name checks and lookups.
- `app/services/carry_forward_service.py` -- Modified. Replace projected lookup.
- `app/services/chart_data_service.py` -- Modified. Replace done/projected lookups.
- `app/routes/transactions.py` -- Modified. Replace all status name lookups.
- `app/routes/transfers.py` -- Modified. Replace all status name lookups.
- `app/routes/templates.py` -- Modified. Replace projected lookups.
- `app/routes/savings.py` -- Modified. Replace status check in emergency fund calc.
- `app/models/transaction.py` -- Modified. Replace status.name checks in effective_amount, is_income, is_expense.
- `app/models/transfer.py` -- Modified. Replace status.name check in effective_amount.
- `app/templates/grid/grid.html` -- Modified. Replace status.name comparisons with status_id comparisons.
- `app/templates/grid/_transaction_cell.html` -- Modified. Replace status.name checks.
- `app/templates/grid/_transaction_full_edit.html` -- Modified. Replace status.name checks.
- `app/templates/grid/_transaction_full_create.html` -- Modified. Replace name == 'projected' check.
- `app/templates/transfers/_transfer_cell.html` -- Modified. Replace status.name checks.
- `app/templates/transfers/_transfer_full_edit.html` -- Modified. Replace status.name check.
- `tests/` (multiple files) -- Modified. Update to use constants where practical.

**D. Implementation approach.**

**Step 1: Create `app/constants.py`.**

Define a constants module with status IDs and transaction type IDs. These IDs are verified at application startup by the `_seed_ref_tables()` function in `app/__init__.py`, which creates the ref table entries in a known order. The constants module uses a simple class with class attributes:

```python
"""
Shekel Budget App -- Reference Table ID Constants

These constants map to the auto-incremented IDs in ref-schema tables.
They are seeded in a fixed order by scripts/seed_ref_tables.py and
app/__init__.py._seed_ref_tables().  If the seeding order ever changes,
these constants must be updated to match.

Using ID constants instead of name strings allows display names to change
(e.g., "done" -> "paid") without breaking logic.
"""


class StatusID:
    """Integer IDs for ref.statuses rows."""

    PROJECTED = 1
    DONE = 2
    RECEIVED = 3
    CREDIT = 4
    CANCELLED = 5
    SETTLED = 6

    # Frequently used sets.
    SETTLED_SET = frozenset({DONE, RECEIVED})
    IMMUTABLE_SET = frozenset({DONE, RECEIVED, CREDIT, CANCELLED})
    NON_PROJECTED_SET = frozenset({DONE, RECEIVED, CREDIT, CANCELLED})


class TxnTypeID:
    """Integer IDs for ref.transaction_types rows."""

    INCOME = 1
    EXPENSE = 2
```

**Important safety measure:** Add a verification function to `app/__init__.py` that runs at application startup (inside `create_app()`) and confirms that the constants match the actual database IDs. If any mismatch is found, the application logs a CRITICAL error and refuses to start. This prevents silent data corruption if the database is re-seeded in a different order.

```python
def _verify_ref_constants(app):
    """Verify that ID constants match actual database values."""
    with app.app_context():
        from app.constants import StatusID, TxnTypeID
        from app.models.ref import Status, TransactionType

        for name, expected_id in [
            ("projected", StatusID.PROJECTED),
            ("done", StatusID.DONE),
            ("received", StatusID.RECEIVED),
            ("credit", StatusID.CREDIT),
            ("cancelled", StatusID.CANCELLED),
            ("settled", StatusID.SETTLED),
        ]:
            row = db.session.query(Status).filter_by(name=name).first()
            if row and row.id != expected_id:
                raise RuntimeError(
                    f"StatusID constant mismatch: {name} expected id={expected_id}, "
                    f"got id={row.id}. Update app/constants.py."
                )

        for name, expected_id in [
            ("income", TxnTypeID.INCOME),
            ("expense", TxnTypeID.EXPENSE),
        ]:
            row = db.session.query(TransactionType).filter_by(name=name).first()
            if row and row.id != expected_id:
                raise RuntimeError(
                    f"TxnTypeID constant mismatch: {name} expected id={expected_id}, "
                    f"got id={row.id}. Update app/constants.py."
                )
```

**Step 2: Replace all status name comparisons in services.**

Work through each file listed in the status audit above. For each occurrence, replace the `filter_by(name="...")` call with a direct ID reference. For example:

```python
# BEFORE
projected_status = db.session.query(Status).filter_by(name="projected").one()
txn.status_id = projected_status.id

# AFTER
from app.constants import StatusID
txn.status_id = StatusID.PROJECTED
```

For frozenset-based checks:

```python
# BEFORE
SETTLED_STATUSES = frozenset({"done", "received"})
status_name = txn.status.name if txn.status else "projected"
if status_name in SETTLED_STATUSES:

# AFTER
from app.constants import StatusID
if txn.status_id in StatusID.SETTLED_SET:
```

This eliminates the need to query the Status table at all in these code paths, improving both correctness and performance.

**Step 3: Replace status name comparisons in models.**

In `app/models/transaction.py`, the `effective_amount`, `is_income`, and `is_expense` properties use `.name` comparisons. Replace with `_id` comparisons:

```python
# BEFORE
if self.status and self.status.name in ("credit", "cancelled"):
    return Decimal("0")

# AFTER
from app.constants import StatusID
if self.status_id in (StatusID.CREDIT, StatusID.CANCELLED):
    return Decimal("0")
```

For `is_income` and `is_expense`, these use `transaction_type.name`. Replace with `transaction_type_id`:

```python
# BEFORE
return self.transaction_type and self.transaction_type.name == "income"

# AFTER
from app.constants import TxnTypeID
return self.transaction_type_id == TxnTypeID.INCOME
```

**Step 4: Replace status name comparisons in templates.**

Templates need access to the ID constants. Pass them via the template context. Add to `grid.index()` in `app/routes/grid.py`:

```python
from app.constants import StatusID, TxnTypeID

# In the render_template call:
STATUS_IDS=StatusID,
TXN_TYPE_IDS=TxnTypeID,
```

Alternatively, register them as Jinja globals in `app/__init__.py` so they are available in all templates without explicit passing:

```python
app.jinja_env.globals['StatusID'] = StatusID
app.jinja_env.globals['TxnTypeID'] = TxnTypeID
```

This is the preferred approach -- it avoids updating every route handler.

Then in templates:

```html
<!-- BEFORE -->
{% if txn.status.name != 'cancelled' %}

<!-- AFTER -->
{% if txn.status_id != StatusID.CANCELLED %}
```

**Step 5: Update test files.**

Update test fixture setup to use constants where the test creates transactions with specific statuses. Tests that use `filter_by(name="projected")` for fixture setup can continue to do so (the name column still exists and is queryable), but tests that assert on logic behavior should use the constants for consistency. Add a test that verifies the constants match the database:

```python
def test_status_id_constants_match_database(self, db_session):
    """Verify StatusID constants match actual database IDs."""
    from app.constants import StatusID
    for name, expected in [
        ("projected", StatusID.PROJECTED),
        ("done", StatusID.DONE),
        # ... etc
    ]:
        status = db.session.query(Status).filter_by(name=name).one()
        assert status.id == expected, f"{name}: expected {expected}, got {status.id}"
```

**E. Test cases.**

**C-4.4a-1:** `test_status_id_constants_match_database`

- Setup: Standard test database with seeded ref tables.
- Action: Query each status by name, compare ID to constant.
- Expected: All 6 status IDs match their constants.
- New test.

**C-4.4a-2:** `test_txn_type_id_constants_match_database`

- Setup: Standard test database.
- Action: Query income and expense by name, compare to constants.
- Expected: Both match.
- New test.

**C-4.4a-3:** `test_effective_amount_uses_id_not_name`

- Setup: Transaction with `status_id=StatusID.CREDIT`, no status relationship loaded.
- Action: Access `txn.effective_amount`.
- Expected: Returns Decimal("0") without needing the status name.
- New test. Verifies the model property works with ID-only.

**C-4.4a-4:** `test_balance_calculator_with_id_constants`

- Setup: Existing balance calculator test data.
- Action: Run `calculate_balances()`.
- Expected: Same results as before the refactor. This is a regression test.
- Modification of existing tests in `test_balance_calculator.py`.

**C-4.4a-5:** `test_grid_renders_with_id_based_status_checks`

- Setup: `seed_user`, `auth_client`, seeded transactions with various statuses.
- Action: GET `/`.
- Expected: Grid renders correctly. Cancelled transactions hidden. Done/received show checkmarks. Subtotals correct.
- Modification of existing grid tests.

**C-4.4a-6:** `test_mark_done_uses_constant_id`

- Setup: `seed_user`, `auth_client`, projected expense.
- Action: POST `/transactions/<id>/mark-done`.
- Expected: Transaction `status_id` equals `StatusID.DONE`. No name-based lookup failure.
- Modification of existing test.

**C-4.4a-7:** `test_startup_verification_catches_mismatch`

- Setup: Mock a status row with wrong ID.
- Action: Call `_verify_ref_constants()`.
- Expected: RuntimeError raised with descriptive message.
- New test.

**F. Manual verification steps.**

1. Start the app. Confirm it boots without constant mismatch errors.
2. Open the grid. Verify all transaction statuses display correctly.
3. Mark an expense as done. Verify the status badge appears.
4. Mark an expense as credit. Verify the payback is created.
5. Carry forward unpaid items. Verify only projected items move.
6. Check the balance row. Verify projected end balance is correct.

**G. Downstream effects.**

- Every file in the codebase that imports `Status` for name-based lookup is affected.
- The `app/constants.py` module becomes a new dependency for services, routes, models, and templates.
- Seed scripts (`seed_ref_tables.py`, `__init__.py._seed_ref_tables`) are NOT changed -- they still seed by name, which is correct for data population.
- The Jinja global registration makes constants available to all templates.

**H. Rollback notes.** This is a pure refactor with no database migration. Reverting restores name-based lookups. No data changes. However, if any subsequent commit (4.4b, 4.4c) has been applied, they depend on this commit and must also be reverted.

---

### Commit #2: Task 4.4c -- Audit All Reference Tables for String-Based Lookups

**A. Commit message:** `refactor(ref): replace all ref table name lookups with ID constants`

**B. Problem statement.** The pattern of using `.name` for logic exists beyond just statuses. AccountType, TransactionType, and RecurrencePattern names are also used in logic comparisons. The reference table audit above documents 40+ additional occurrences across routes, services, and templates.

**C. Files modified.**

- `app/constants.py` -- Modified. Add `AcctTypeID` and `RecurrencePatternID` constants.
- `app/routes/accounts.py` -- Modified. Replace account type name comparisons.
- `app/routes/auto_loan.py` -- Modified. Replace type guard checks.
- `app/routes/mortgage.py` -- Modified. Replace type guard checks.
- `app/routes/savings.py` -- Modified. Replace account type lookups.
- `app/routes/transactions.py` -- Modified. Replace `txn_type_name` lookups with ID-based.
- `app/routes/transfers.py` -- Modified. Replace recurrence pattern name lookup.
- `app/routes/templates.py` -- Modified. Replace recurrence pattern name lookup.
- `app/routes/investment.py` -- Modified. Replace income type lookup.
- `app/routes/retirement.py` -- Modified. Replace income type lookup.
- `app/routes/salary.py` -- Modified. Replace income type lookup.
- `app/services/transfer_service.py` -- Modified. Replace transaction type lookups.
- `app/services/credit_workflow.py` -- Modified. Replace expense type lookup.
- `app/services/account_resolver.py` -- Modified. Replace checking type lookup.
- `app/services/auth_service.py` -- Modified. Replace checking type lookup.
- `app/services/chart_data_service.py` -- Modified. Replace account type name usage.
- `app/templates/grid/grid.html` -- Modified. Pass transaction_type_id instead of txn_type_name.
- `app/templates/grid/_transaction_empty_cell.html` -- Modified. Use ID parameter.
- `app/templates/grid/_transaction_quick_create.html` -- Modified. Use ID parameter.
- `app/templates/savings/dashboard.html` -- Modified. Replace account type name checks with ID checks.
- `app/static/js/grid_edit.js` -- Modified. Pass `transaction_type_id` instead of `txn_type_name`.
- Tests (multiple files) -- Modified. Use constants.

**D. Implementation approach.**

**Step 1: Add constants to `app/constants.py`.**

```python
class AcctTypeID:
    """Integer IDs for ref.account_types rows."""
    CHECKING = 1
    SAVINGS = 2
    HYSA = 3
    MONEY_MARKET = 4
    CD = 5
    HSA = 6
    CREDIT_CARD = 7
    MORTGAGE = 8
    AUTO_LOAN = 9
    STUDENT_LOAN = 10
    PERSONAL_LOAN = 11
    HELOC = 12
    K401 = 13
    ROTH_401K = 14
    TRADITIONAL_IRA = 15
    ROTH_IRA = 16
    BROKERAGE = 17
    PLAN_529 = 18

    # Useful sets.
    DEBT_SET = frozenset({MORTGAGE, AUTO_LOAN, STUDENT_LOAN, PERSONAL_LOAN, HELOC})
    SAVINGS_SET = frozenset({SAVINGS, HYSA})
    RETIREMENT_SET = frozenset({K401, ROTH_401K, TRADITIONAL_IRA, ROTH_IRA})
    INVESTMENT_SET = frozenset({BROKERAGE, PLAN_529})
    PARAMETERIZED_SET = frozenset({
        HYSA, MORTGAGE, AUTO_LOAN, STUDENT_LOAN, PERSONAL_LOAN,
        K401, ROTH_401K, TRADITIONAL_IRA, ROTH_IRA, BROKERAGE,
    })


class RecurrencePatternID:
    """Integer IDs for ref.recurrence_patterns rows."""
    EVERY_PERIOD = 1
    EVERY_N_PERIODS = 2
    MONTHLY = 3
    MONTHLY_FIRST = 4
    QUARTERLY = 5
    SEMI_ANNUAL = 6
    ANNUAL = 7
    ONCE = 8
```

Add these to the startup verification function and register as Jinja globals.

**Step 2: Replace all AccountType name comparisons.** Follow the audit table above.

**Step 3: Refactor `txn_type_name` to `transaction_type_id`.** This is the most complex change because it spans template, JS, and route. The grid currently passes `txn_type_name=income` or `txn_type_name=expense` as a string. Change to pass `transaction_type_id=1` or `transaction_type_id=2`.

Changes:

- `grid/grid.html:153` -- Change `{% set txn_type_name = "income" %}` to `{% set txn_type_id = TxnTypeID.INCOME %}`
- `grid/grid.html:233` -- Same for expense
- `grid/_transaction_empty_cell.html:11` -- Change `txn_type_name=txn_type_name` to `transaction_type_id=txn_type_id`
- `grid_edit.js:125, 244` -- Change `txn_type_name` to `transaction_type_id` in URL construction
- `grid/_transaction_quick_create.html:37` -- Change `data-txn-type-name` to `data-txn-type-id`
- `transactions.py:335, 381, 427` -- Accept `transaction_type_id` parameter, use `filter_by(id=...)` instead of `filter_by(name=...)`

**Step 4: Replace RecurrencePattern name lookups.** Update the transfer template and transaction template forms to send pattern ID instead of name.

**E. Test cases.**

**C-4.4c-1:** `test_acct_type_id_constants_match_database`

- Setup: Standard test database.
- Action: Query each account type by name, compare to constant.
- Expected: All 18 account type IDs match.
- New test.

**C-4.4c-2:** `test_recurrence_pattern_id_constants_match_database`

- Setup: Standard test database.
- Action: Query each pattern by name, compare to constant.
- Expected: All 8 match.
- New test.

**C-4.4c-3:** `test_empty_cell_uses_transaction_type_id`

- Setup: `seed_user`, `auth_client`, seeded grid data.
- Action: GET `/`. Check that empty cells pass `transaction_type_id` in their `hx-get` URLs.
- Expected: URL contains `transaction_type_id=2` (expense) or `transaction_type_id=1` (income).
- New test.

**C-4.4c-4:** `test_quick_create_with_type_id`

- Setup: `seed_user`, `auth_client`, category, period.
- Action: GET `/transactions/new/quick?category_id=X&period_id=Y&transaction_type_id=2`.
- Expected: 200 response. Quick create form rendered for expense.
- Modification of existing test.

**C-4.4c-5:** `test_account_creation_redirect_uses_type_id`

- Setup: `seed_user`, `auth_client`.
- Action: POST `/accounts` with HYSA type.
- Expected: Redirect uses account type ID logic, not name comparison.
- Modification of existing test.

**F. Manual verification steps.**

1. Open the grid. Click an empty cell. Verify the quick create form appears.
2. Submit a new transaction via inline create. Verify it saves correctly.
3. Open the Add Transaction modal. Verify the type dropdown works.
4. Create a new HYSA account. Verify the post-creation redirect works.
5. Visit the savings dashboard. Verify account type icons and links are correct.

**G. Downstream effects.** The `txn_type_name` -> `transaction_type_id` change affects the grid template, JS, and 3 route handlers. All must be updated atomically.

**H. Rollback notes.** This commit depends on commit #1 (the constants module). Reverting requires also reverting #1 if constants are removed. However, the constants can remain as unused imports without harm.

---

### Commit #3: Task 4.4b -- Rename "Done" to "Paid"

**A. Commit message:** `feat(status): rename "Done" to "Paid" for expense status display`

**B. Problem statement.** The status "Done" is generic. For expenses, "Paid" is clearer -- it communicates that money left the account. Now that all logic uses ID constants (commit #1), the `name` column is display-only and can be safely changed.

**C. Files modified.**

- `migrations/versions/<new>.py` -- New. Alembic migration: `UPDATE ref.statuses SET name = 'paid' WHERE name = 'done'`.
- `scripts/seed_ref_tables.py` -- Modified. Change "done" to "paid" in seed list.
- `app/__init__.py` -- Modified. Change "done" to "paid" in test seed list.
- `app/templates/grid/_transaction_full_edit.html` -- Modified. Change "Done" button text to "Paid".
- `app/templates/transfers/_transfer_full_edit.html` -- Modified. Change "Done" button text to "Paid".
- `tests/conftest.py` -- Modified. Update seed list from "done" to "paid".
- Tests (multiple files) -- Modified. Update any remaining `filter_by(name="done")` to `filter_by(name="paid")` and assertions that check for "Done" text to "Paid".

**D. Implementation approach.**

Since commit #1 converted all logic to use `StatusID.DONE` (integer 2), the `name` column value is now purely cosmetic. The rename is safe.

**Step 1: Create Alembic migration.**

```python
def upgrade():
    op.execute("UPDATE ref.statuses SET name = 'paid' WHERE name = 'done'")

def downgrade():
    op.execute("UPDATE ref.statuses SET name = 'done' WHERE name = 'paid'")
```

**Step 2: Update seed scripts.** Change `"done"` to `"paid"` in the status seed lists in `scripts/seed_ref_tables.py`, `app/__init__.py`, and `tests/conftest.py`.

**Step 3: Update button text.** In `grid/_transaction_full_edit.html` line 80, change the hardcoded "Done" button text to "Paid". In `transfers/_transfer_full_edit.html` line 94, same change. The status dropdown already uses `s.name|capitalize`, which will now render as "Paid" automatically.

**Step 4: Update status display in cell templates.** In `grid/_transaction_cell.html` line 35, the `title` and `aria-label` use `t.status.name|capitalize`. After the rename, this automatically shows "Paid" for done expenses. No template change needed for this.

**Step 5: Update tests.** Any test that uses `filter_by(name="done")` to get the status object must change to `filter_by(name="paid")`. Any test that asserts on "Done" in response HTML must change to "Paid". Tests that use `StatusID.DONE` constant need no change.

**E. Test cases.**

**C-4.4b-1:** `test_status_name_is_paid_not_done`

- Setup: Database with migration applied.
- Action: Query `Status` with ID 2.
- Expected: `name == "paid"`.
- New test.

**C-4.4b-2:** `test_expense_button_says_paid`

- Setup: `seed_user`, `auth_client`, projected expense.
- Action: GET `/transactions/<id>/full-edit`.
- Expected: Response HTML contains "Paid" button (not "Done").
- Modification of existing test.

**C-4.4b-3:** `test_status_dropdown_shows_paid`

- Setup: `seed_user`, `auth_client`, transaction.
- Action: GET `/transactions/<id>/full-edit`.
- Expected: Status dropdown `<option>` contains "Paid" for the done status.
- Modification of existing test.

**C-4.4b-4:** `test_cell_badge_tooltip_says_paid`

- Setup: `seed_user`, `auth_client`, expense with status "paid" (ID 2).
- Action: GET `/`.
- Expected: Cell badge `title` attribute contains "Paid".
- New test.

**C-4.4b-5:** `test_transfer_full_edit_button_says_paid`

- Setup: `seed_user`, `auth_client`, shadow transaction from transfer, projected status.
- Action: GET `/transactions/<shadow_id>/full-edit`.
- Expected: Transfer form "Paid" button (not "Done").
- Modification of existing test.

**F. Manual verification steps.**

1. Open the grid. Click a projected expense. Verify the full edit shows "Paid" button.
2. Click the status dropdown. Verify it shows "Paid" option (not "Done").
3. Mark the expense. Verify the cell badge tooltip says "Paid".
4. Click a shadow transaction. Verify the transfer form also shows "Paid" button.
5. Income transactions should still show "Received" -- verify unchanged.

**G. Downstream effects.**

- The `name` column in `ref.statuses` changes value. Any external tool querying this column by name must be updated.
- The `StatusID.DONE` constant is unchanged (still integer 2). No logic is affected.
- The seed scripts must stay in sync with the migration.

**H. Rollback notes.** Requires running the downgrade migration. Seed scripts must be reverted. Depends on commit #1 (ID constants must be in place for logic to work with either name).

---

### Commit #4: Task 4.13 -- Emergency Fund Coverage Calculation Fix

**A. Commit message:** `fix(savings): include transfer expenses in emergency fund coverage`

**B. Problem statement.** The emergency fund coverage calculation in `app/routes/savings.py:376-428` averages actual expenses from the last 6 pay periods. It counts transactions where `is_expense` is True and status is "done" or "received." After the transfer rework, shadow expense transactions from transfers (mortgage payments, savings transfers) have `is_expense=True` and thus ARE included in the query when their status is "done"/"received" (now "paid"). However, the calculation only considers historical actuals -- transfers that are still "projected" (not yet settled) are not counted. This means a recently created recurring transfer will take several pay periods before it fully appears in the expense average. More critically, the roadmap identifies that transfers out of checking should be explicitly included as committed monthly expenses regardless of their settlement history.

**C. Files modified.**

- `app/routes/savings.py` -- Modified. Enhance the emergency fund expense calculation to include recurring transfer template amounts.
- `app/services/savings_goal_service.py` -- Modified. Add a helper function for computing committed monthly expenses.
- `tests/test_routes/test_savings.py` -- Modified. Add tests for the corrected calculation.

**D. Implementation approach.**

The fix has two parts:

1. **Keep the historical actuals approach** for settled transactions (it correctly captures variable expenses).
2. **Add a floor based on recurring templates** to ensure committed outflows are always counted even when settlement history is sparse.

In `app/routes/savings.py`, after computing `avg_monthly_expenses` from historical actuals, also compute a `committed_monthly` figure from active recurring expense templates and transfer templates that debit checking. Use the higher of the two figures as the baseline for emergency fund coverage.

```python
# After the existing avg_monthly_expenses calculation (line 407):

# Also compute committed monthly from active recurring templates.
from app.models.budget import TransactionTemplate, TransferTemplate

# Active expense templates for checking account.
expense_templates = (
    db.session.query(TransactionTemplate)
    .filter(
        TransactionTemplate.user_id == user_id,
        TransactionTemplate.is_active.is_(True),
        TransactionTemplate.account_id == checking_account_id,
        TransactionTemplate.transaction_type_id == TxnTypeID.EXPENSE,
    )
    .all()
)

# Active transfer templates FROM checking.
transfer_templates = (
    db.session.query(TransferTemplate)
    .filter(
        TransferTemplate.user_id == user_id,
        TransferTemplate.is_active.is_(True),
        TransferTemplate.from_account_id == checking_account_id,
    )
    .all()
)

# Sum to monthly: each template's default_amount * occurrences_per_month.
committed_per_period = sum(t.default_amount for t in expense_templates) + \
                       sum(t.amount for t in transfer_templates)
committed_monthly = committed_per_period * Decimal("26") / Decimal("12")

# Use the higher of historical actual average or committed baseline.
avg_monthly_expenses = max(avg_monthly_expenses, committed_monthly)
```

**Note:** This simplified approach assumes all templates recur every period. A more accurate version would account for each template's recurrence pattern (monthly templates contribute 1x/month, biweekly contribute 26/12 per month, etc.). The implementation should calculate per-template monthly contribution based on the recurrence pattern. This can be extracted into a helper in `savings_goal_service.py`.

**E. Test cases.**

**C-4.13-1:** `test_emergency_fund_includes_transfer_templates`

- Setup: `seed_user`, `auth_client`, checking account, active transfer template from checking to mortgage ($1500/month).
- Action: GET savings dashboard.
- Expected: `avg_monthly_expenses` includes the $1500 transfer amount.
- New test.

**C-4.13-2:** `test_emergency_fund_uses_higher_of_actual_or_committed`

- Setup: `seed_user`, `auth_client`, checking account. Historical actuals total $3000/month. Committed templates total $3500/month.
- Action: GET savings dashboard.
- Expected: Emergency fund uses $3500 (the higher figure).
- New test.

**C-4.13-3:** `test_emergency_fund_with_no_history_uses_committed`

- Setup: `seed_user`, `auth_client`, checking account. No historical settled transactions. Active expense templates and transfer templates totaling $2000/month.
- Action: GET savings dashboard.
- Expected: `avg_monthly_expenses` is $2000 (committed baseline, not $0).
- New test.

**C-4.13-4:** `test_emergency_fund_no_templates_no_history`

- Setup: `seed_user`, `auth_client`, checking account. No templates, no history.
- Action: GET savings dashboard.
- Expected: `avg_monthly_expenses` is $0. Coverage shows "N/A" or infinity.
- New test (edge case).

**F. Manual verification steps.**

1. Open the savings dashboard. Note the emergency fund coverage (months covered).
2. Create a recurring transfer from checking to a savings account ($500/month).
3. Refresh the dashboard. Verify the coverage decreases to reflect the new committed outflow.
4. Delete the transfer template. Verify coverage returns to the previous value.

**G. Downstream effects.**

- The `savings_goal_service.calculate_savings_metrics()` function is unchanged -- it receives the corrected `avg_monthly_expenses` value.
- No grid changes.
- The emergency fund display template is unchanged.

**H. Rollback notes.** Pure route/service logic change. No migration. Revertable independently.

---

### Commit #5: Task 4.5 -- Deduction Frequency Display

**A. Commit message:** `feat(salary): improve deduction frequency display with descriptive labels`

**B. Problem statement.** The deductions table in `app/templates/salary/_deductions_section.html` shows raw integer values like "26", "24", "12" in the "Per Year" column. These numbers are ambiguous without context.

**C. Files modified.**

- `app/templates/salary/_deductions_section.html` -- Modified. Update the Per Year column to show descriptive labels.
- `tests/test_routes/test_salary.py` -- Modified. Add test for frequency display.

**D. Implementation approach.**

In `salary/_deductions_section.html`, change the column header from "Per Year" to "Frequency" and update the cell display:

```html
<td>
  {% if d.deductions_per_year == 26 %} 26x/yr
  <small class="text-muted">(every paycheck)</small> {% elif
  d.deductions_per_year == 24 %} 24x/yr
  <small class="text-muted">(skip 3rd)</small> {% elif d.deductions_per_year ==
  12 %} 12x/yr <small class="text-muted">(monthly)</small>
  {% else %} {{ d.deductions_per_year }}x/yr {% endif %}
</td>
```

**E. Test cases.**

**C-4.5-1:** `test_deduction_frequency_shows_descriptive_label`

- Setup: `seed_user`, `auth_client`, salary profile with deductions at 26x, 24x, 12x.
- Action: GET salary profile page.
- Expected: Response contains "26x/yr" with "(every paycheck)", "24x/yr" with "(skip 3rd)", "12x/yr" with "(monthly)".
- New test.

**F. Manual verification steps.** Open the salary page. Verify deductions show descriptive frequency labels.

**G. Downstream effects.** None. Single template change on a non-grid page.

**H. Rollback notes.** Template-only. Revertable independently.

---

### Commit #6: Task 4.6 -- Tax Config Page Reorganization

**A. Commit message:** `feat(salary): reorganize tax config page -- adjustable settings first`

**B. Problem statement.** The tax config page (`app/templates/salary/tax_config.html`) puts static federal bracket tables at the top and user-adjustable settings at the bottom. Users must scroll past rarely-changed data to reach frequently-changed settings.

**C. Files modified.**

- `app/templates/salary/tax_config.html` -- Modified. Reorder sections, add collapse.
- `tests/test_routes/test_salary.py` -- Modified. Add ordering and collapse tests.

**D. Implementation approach.**

1. Move the State Tax Configuration card to position 1.
2. Move the FICA Configuration card to position 2.
3. Move the Federal Tax Brackets card to position 3.
4. Wrap the Federal Tax Brackets card body in a Bootstrap 5 collapse component, defaulting to collapsed.
5. Group bracket tables by tax year with individual collapses. Most recent year expanded, previous years collapsed.

**E. Test cases.**

**C-4.6-1:** `test_tax_config_state_tax_appears_first`

- Setup: `seed_user`, `auth_client`, seeded tax data.
- Action: GET `/salary/tax-config`.
- Expected: In response HTML, "State Tax" card appears before "FICA" card, which appears before "Federal Tax Brackets" card.
- New test.

**C-4.6-2:** `test_federal_brackets_collapsed_by_default`

- Setup: Same.
- Action: GET `/salary/tax-config`.
- Expected: Federal brackets container has `class="collapse"` (not `class="collapse show"`).
- New test.

**F. Manual verification steps.** Open tax config. Verify order. Click expand on Federal brackets. Verify they expand.

**G. Downstream effects.** None. Non-grid page.

**H. Rollback notes.** Template-only.

---

### Commit #7: Task 4.11 -- Salary Profile Button Placement

**A. Commit message:** `feat(salary): move View Breakdown and View Projection buttons higher`

**B. Problem statement.** The View Breakdown and View Projection buttons are in the salary profile list (`app/templates/salary/list.html`, lines 72-79) within each profile's table row. On the profile detail page, these buttons are at the bottom and hard to find.

**C. Files modified.**

- `app/templates/salary/list.html` -- Modified. Add prominent button group above the profile details.
- `tests/test_routes/test_salary.py` -- Modified. Assert button placement.

**D. Implementation approach.**

Add a prominent action bar immediately below the profile name/summary section on the salary list page. The existing buttons in the table row actions column remain as secondary access points.

```html
<!-- Add below the profile summary card -->
<div class="d-flex gap-2 mb-3">
  <a
    href="{{ url_for('salary.breakdown_current', profile_id=p.id) }}"
    class="btn btn-outline-primary btn-sm"
  >
    <i class="bi bi-receipt"></i> View Breakdown
  </a>
  <a
    href="{{ url_for('salary.projection', profile_id=p.id) }}"
    class="btn btn-outline-primary btn-sm"
  >
    <i class="bi bi-graph-up"></i> View Projection
  </a>
</div>
```

**E. Test cases.**

**C-4.11-1:** `test_salary_buttons_visible_above_fold`

- Setup: `seed_user`, `auth_client`, salary profile.
- Action: GET salary page.
- Expected: Response contains "View Breakdown" and "View Projection" buttons (check for the URL patterns) early in the HTML (before the deductions table).
- New test.

**F. Manual verification steps.** Open salary page. Verify buttons are visible without scrolling.

**G. Downstream effects.** None.

**H. Rollback notes.** Template-only.

---

### Commit #8: Task 4.7/4.8 -- Account Parameter Setup UX

**A. Commit message:** `feat(accounts): redirect to parameter config after creating parameterized accounts`

**B. Problem statement.** After creating accounts that require parameters (HYSA, investment, retirement), the app redirects to the generic accounts list. The user must then discover the small icon button on the account card to configure parameters. Mortgage and auto loan already redirect to their parameter pages, but HYSA, investment, and retirement do not.

**C. Files modified.**

- `app/routes/accounts.py` -- Modified. Extend post-creation redirect for all parameterized types.
- `app/routes/savings.py` -- Modified. Add `needs_setup` flag to account data.
- `app/templates/savings/dashboard.html` -- Modified. Add "Setup Required" badge.
- Various parameter page templates -- Modified. Add wizard banner when `setup=1`.
- `tests/test_routes/test_accounts.py` -- Modified. Test redirects.
- `tests/test_routes/test_savings.py` -- Modified. Test badge.

**D. Implementation approach.**

In `accounts.py:create_account()`, extend the post-creation redirect logic to cover all parameterized account types using the new `AcctTypeID.PARAMETERIZED_SET`:

```python
from app.constants import AcctTypeID

# After account creation:
if account.account_type_id == AcctTypeID.HYSA:
    # Auto-create default HysaParams if not exists (already done at line 136)
    return redirect(url_for("accounts.hysa_detail", account_id=account.id, setup=1))
elif account.account_type_id == AcctTypeID.MORTGAGE:
    return redirect(url_for("mortgage.dashboard", account_id=account.id, setup=1))
elif account.account_type_id == AcctTypeID.AUTO_LOAN:
    return redirect(url_for("auto_loan.dashboard", account_id=account.id, setup=1))
elif account.account_type_id in AcctTypeID.RETIREMENT_SET | AcctTypeID.INVESTMENT_SET:
    # Auto-create default InvestmentParams.
    from app.models.budget import InvestmentParams
    existing = db.session.query(InvestmentParams).filter_by(account_id=account.id).first()
    if not existing:
        db.session.add(InvestmentParams(account_id=account.id))
        db.session.commit()
    return redirect(url_for("investment.dashboard", account_id=account.id, setup=1))
```

For the savings dashboard badge, add a `needs_setup` flag based on whether parameters are at default values (APY=0 for HYSA, return_rate=0 for investment/retirement).

**E. Test cases.**

**C-4.7-1:** `test_hysa_creation_redirects_to_params`

- Setup: `seed_user`, `auth_client`.
- Action: POST `/accounts` with HYSA type.
- Expected: 302 redirect to HYSA detail with `setup=1`.
- New test.

**C-4.7-2:** `test_investment_creation_redirects_to_params`

- Setup: `seed_user`, `auth_client`.
- Action: POST `/accounts` with investment type.
- Expected: 302 redirect to investment dashboard with `setup=1`.
- New test.

**C-4.7-3:** `test_checking_creation_redirects_to_list`

- Setup: `seed_user`, `auth_client`.
- Action: POST `/accounts` with checking type.
- Expected: Redirect to accounts list (no parameter page).
- New test.

**C-4.7-4:** `test_setup_badge_shown_for_unconfigured_hysa`

- Setup: `seed_user`, `auth_client`, HYSA with default params (APY=0).
- Action: GET savings dashboard.
- Expected: "Setup Required" badge visible.
- New test.

**C-4.7-5:** `test_setup_badge_hidden_for_configured_hysa`

- Setup: `seed_user`, `auth_client`, HYSA with APY=4.5%.
- Action: GET savings dashboard.
- Expected: No "Setup Required" badge.
- New test.

**F. Manual verification steps.** Create a new HYSA. Verify redirect to detail with banner. Create checking. Verify redirect to list. Check dashboard for badge.

**G. Downstream effects.** Investment/retirement account creation now auto-creates InvestmentParams records.

**H. Rollback notes.** Route/template changes. No migration.

---

### Commit #9: Task 4.15 -- Auto Loan Parameter Page Fixes

**A. Commit message:** `fix(auto_loan): add editable term field and pre-populate principal from balance`

**B. Problem statement.** The auto loan parameter page (`app/templates/auto_loan/dashboard.html`) allows editing current_principal, interest_rate, and payment_day, but not term_months. Additionally, the principal field is not pre-populated from the account balance entered during creation.

**C. Files modified.**

- `app/templates/auto_loan/dashboard.html` -- Modified. Add term_months input field.
- `app/routes/auto_loan.py` -- Modified. Accept and save term_months. Pre-populate principal from account balance on first visit.
- `app/routes/accounts.py` -- Modified. Pass initial balance to auto loan params on creation.
- `tests/test_routes/test_auto_loan.py` -- Modified. Test term field and pre-population.

**D. Implementation approach.**

1. Add `term_months` to the parameter form in the template. Use a number input with min=1, max=360.
2. Update the `auto_loan.update_params()` handler to accept and save `term_months`.
3. In `accounts.create_account()`, when creating an auto loan, copy the initial balance to `AutoLoanParams.current_principal`.
4. Verify that personal_loan and student_loan don't have the same issues. Based on the agent research, personal_loan and student_loan do NOT have dedicated parameter pages, so this fix applies only to auto_loan.

**E. Test cases.**

**C-4.15-1:** `test_auto_loan_term_field_editable`

- Setup: `seed_user`, `auth_client`, auto loan account with params.
- Action: GET auto loan dashboard.
- Expected: Response contains term_months input field.
- New test.

**C-4.15-2:** `test_auto_loan_term_update`

- Setup: `seed_user`, `auth_client`, auto loan with term_months=60.
- Action: POST update with term_months=48.
- Expected: Saved successfully. Database shows term_months=48.
- New test.

**C-4.15-3:** `test_auto_loan_principal_prepopulated_from_balance`

- Setup: `seed_user`, `auth_client`.
- Action: Create auto loan with balance=$15000, then GET param page.
- Expected: current_principal field shows $15000.
- New test.

**F. Manual verification steps.** Create auto loan with $15000 balance. Verify principal pre-populated. Verify term field is editable.

**G. Downstream effects.** None beyond auto loan pages.

**H. Rollback notes.** Route/template changes. No migration needed if term_months column already exists on the model (it does).

---

### Commit #10: Task 4.14 -- Checking Account Balance Projection

**A. Commit message:** `feat(accounts): add balance projection to checking account detail page`

**B. Problem statement.** The checking account has no dedicated detail page. Savings/HYSA accounts show balance projections at 3, 6, and 12 months. The checking account, despite being the most important account for the payday workflow, has no forward-looking view outside the grid.

**C. Files modified.**

- `app/templates/accounts/checking_detail.html` -- New. Checking account detail page with balance projection.
- `app/routes/accounts.py` -- Modified. Add checking account detail route.
- `app/templates/savings/dashboard.html` -- Modified. Add link from checking account card to detail page.
- `tests/test_routes/test_accounts.py` -- Modified. Test checking detail page.

**D. Implementation approach.**

Create a new route `accounts.checking_detail(account_id)` that:

1. Loads the checking account and verifies it's a checking type.
2. Calls `balance_calculator.calculate_balances()` to get projected end balances per period (same logic the grid uses).
3. Renders a simple projection table/list showing projected balance at 3, 6, and 12 month intervals.
4. Optionally, renders a small Chart.js line chart of projected checking balance over time.

The template reuses the projection display pattern from `accounts/hysa_detail.html` but without interest calculations (checking APY is negligible per the user's confirmation).

**E. Test cases.**

**C-4.14-1:** `test_checking_detail_page_renders`

- Setup: `seed_user`, `auth_client`, checking account with anchor balance, periods, transactions.
- Action: GET `/accounts/<checking_id>/detail`.
- Expected: 200 response. Page contains account name, current balance, projected balances.
- New test.

**C-4.14-2:** `test_checking_detail_projection_values`

- Setup: `seed_user`, `auth_client`, checking with known transactions.
- Action: GET `/accounts/<checking_id>/detail`.
- Expected: Projected balance at 3 months matches hand-computed value.
- New test.

**C-4.14-3:** `test_checking_detail_rejects_non_checking`

- Setup: `seed_user`, `auth_client`, savings account.
- Action: GET `/accounts/<savings_id>/detail`.
- Expected: 404 (not a checking account).
- New test.

**F. Manual verification steps.** Visit checking account detail. Verify balance projection displays. Compare values with grid balance row.

**G. Downstream effects.** A new route and template are added. The savings dashboard gets a new link to the checking detail page.

**H. Rollback notes.** New route and template. Revertable independently.

---

### Commit #11: Task 4.16 -- Retirement Date Validation UX

**A. Commit message:** `fix(retirement): preserve form data and highlight fields on validation errors`

**B. Problem statement.** When an invalid date is entered on the retirement/pension forms, the toast says "correct the highlighted error" but no fields are highlighted. The form clears all data on validation failure, forcing re-entry.

**C. Files modified.**

- `app/routes/retirement.py` -- Modified. Return form data on validation failure instead of redirecting.
- `app/templates/retirement/pension_form.html` (or equivalent) -- Modified. Add field-level error indicators and pre-populate fields from submitted data.
- `app/templates/settings/_retirement.html` -- Modified. Same pattern for settings form.
- `tests/test_routes/test_retirement.py` -- Modified. Test error display and data preservation.

**D. Implementation approach.**

The current pattern (`flash()` + `redirect()`) loses form data because redirects create a new GET request with no form context. The fix: render the form template directly on validation failure (POST returns the form with errors) instead of redirecting.

```python
# BEFORE (in retirement.py pension update handler):
if errors:
    for err in errors:
        flash(err, "danger")
    return redirect(url_for("retirement.edit_pension", pension_id=pension_id))

# AFTER:
if errors:
    return render_template(
        "retirement/pension_form.html",
        pension=pension,
        form_data=form_data,  # Pass submitted values back
        field_errors=field_errors,  # Dict mapping field name -> error message
    ), 422
```

In the template, add Bootstrap `is-invalid` class and error text:

```html
<input
  type="date"
  name="planned_retirement_date"
  class="form-control {% if field_errors and 'planned_retirement_date' in field_errors %}is-invalid{% endif %}"
  value="{{ form_data.planned_retirement_date if form_data else pension.planned_retirement_date }}"
/>
{% if field_errors and 'planned_retirement_date' in field_errors %}
<div class="invalid-feedback">
  {{ field_errors['planned_retirement_date'] }}
</div>
{% endif %}
```

**E. Test cases.**

**C-4.16-1:** `test_pension_validation_preserves_form_data`

- Setup: `seed_user`, `auth_client`, pension profile.
- Action: POST pension update with invalid planned_retirement_date (in the past).
- Expected: 422 response. Form re-rendered with submitted values pre-populated. Error message visible near the invalid field.
- New test.

**C-4.16-2:** `test_pension_validation_highlights_invalid_field`

- Setup: `seed_user`, `auth_client`, pension profile.
- Action: POST with planned_retirement_date before hire_date.
- Expected: Response contains `is-invalid` class on the planned_retirement_date input. Contains `invalid-feedback` div with error text.
- New test.

**C-4.16-3:** `test_settings_validation_preserves_form_data`

- Setup: `seed_user`, `auth_client`.
- Action: POST retirement settings with invalid safe_withdrawal_rate.
- Expected: Form re-rendered with submitted values. Error highlighted.
- New test.

**F. Manual verification steps.** Open pension form. Enter an invalid date. Submit. Verify the field is highlighted, error message visible, and other field values preserved.

**G. Downstream effects.** Changes the response pattern from redirect-on-error to render-on-error. The template must handle both initial load (pension object) and error re-render (form_data dict).

**H. Rollback notes.** Route/template changes. Revertable independently.

---

### Commit #12: Task 4.17 -- Retirement Dashboard Return Rate Clarity

**A. Commit message:** `feat(retirement): clarify return rate slider behavior with explanatory text`

**B. Problem statement.** The Assumed Annual Return slider on the retirement dashboard is unclear. The user cannot determine what it controls, whether it overrides per-account rates, or what fields it recalculates.

**C. Files modified.**

- `app/templates/retirement/dashboard.html` -- Modified. Add explanatory text near the slider and visual feedback on what changes.
- `tests/test_routes/test_retirement.py` -- Modified. Test explanatory text presence.

**D. Implementation approach.**

1. Add a `<small class="text-muted">` paragraph below the slider explaining its purpose: "This rate is used to project your aggregate retirement savings. Individual account rates are set on each account's parameter page."
2. Ensure that when the slider value changes and the HTMX request fires, all affected numbers on the dashboard update visibly (the `gap-analysis-container` div already updates via HTMX).
3. If individual account projections on the dashboard do NOT use the slider rate (they use per-account rates), add a note: "Account projections below use their individual configured rates."

**E. Test cases.**

**C-4.17-1:** `test_retirement_slider_has_explanatory_text`

- Setup: `seed_user`, `auth_client`, retirement dashboard data.
- Action: GET retirement dashboard.
- Expected: Response contains explanatory text about the slider's purpose.
- New test.

**F. Manual verification steps.** Open retirement dashboard. Read the slider label. Verify the explanation is clear. Move the slider. Verify the gap analysis updates.

**G. Downstream effects.** None.

**H. Rollback notes.** Template-only.

---

### Commit #13: Task 4.9 -- Chart Balance Over Time Contrast

**A. Commit message:** `feat(charts): add dash patterns and varied line weights to balance chart`

**B. Problem statement.** The Balance Over Time chart on the mortgage account detail page (and main Charts page) uses uniform 2px solid lines with similar colors. Lines can be hard to distinguish, especially the "standard payments" line on the mortgage chart.

**C. Files modified.**

- `app/static/js/chart_balance.js` -- Modified. Add lineStyles array with dash patterns.

**D. Implementation approach.**

Add a `lineStyles` array that cycles through visual differentiation patterns:

```javascript
var lineStyles = [
  { borderWidth: 2.5, borderDash: [] },
  { borderWidth: 2, borderDash: [8, 4] },
  { borderWidth: 2, borderDash: [2, 3] },
  { borderWidth: 2.5, borderDash: [12, 4, 2, 4] },
  { borderWidth: 2, borderDash: [] },
  { borderWidth: 2, borderDash: [6, 3] },
  { borderWidth: 2, borderDash: [3, 2] },
  { borderWidth: 2.5, borderDash: [8, 3, 2, 3] },
];
```

Apply in the `datasets.map()` call alongside existing color assignment.

**E. Test cases.**

**C-4.9-1:** `test_balance_chart_renders_after_style_changes`

- Setup: `seed_user`, `auth_client`, multiple accounts.
- Action: GET `/charts/balance-over-time` (HTMX).
- Expected: 200 response. Chart canvas present. This is a smoke test.
- Modification of existing test if one exists, otherwise new.

**F. Manual verification steps.** Open Charts page. Verify lines have distinct styles. Open mortgage detail. Verify "standard payments" line is distinguishable.

**G. Downstream effects.** None. JS-only change.

**H. Rollback notes.** JS-only. Revertable.

---

### Commit #14: Task 4.10 -- Chart Time Frame Controls

**A. Commit message:** `feat(charts): add time frame controls to balance over time chart`

**B. Problem statement.** The Balance Over Time chart has no time frame controls. Long-duration accounts (30-year mortgage, retirement) show flat lines when all periods are plotted because the near-term detail is compressed.

**C. Files modified.**

- `app/templates/charts/_balance_over_time.html` -- Modified. Add time frame button group.
- `app/routes/charts.py` -- Modified. Accept `range` parameter.
- `app/services/chart_data_service.py` -- Modified. Filter periods by range.
- `tests/test_routes/test_charts.py` -- Modified. Test range parameter.

**D. Implementation approach.**

Add 1Y/5Y/10Y/Full buttons as a Bootstrap button group in the chart card header. Each button triggers an HTMX request with the `range` parameter. The route translates the range to a period count (1Y=26, 5Y=130, 10Y=260, Full=all) and passes it to the chart data service.

Default: intelligent based on selected accounts. If any mortgage or retirement account is checked, default to `full`. If only checking/savings, default to `1y`.

**E. Test cases.**

**C-4.10-1:** `test_balance_chart_accepts_range_parameter`

- Setup: `seed_user`, `auth_client`, periods and accounts.
- Action: GET `/charts/balance-over-time?range=1y`.
- Expected: 200. Chart data contains ~26 labels.
- New test.

**C-4.10-2:** `test_balance_chart_full_range`

- Setup: `seed_user`, `auth_client`, 2 years of periods.
- Action: GET `/charts/balance-over-time?range=full`.
- Expected: Chart data contains all available periods.
- New test.

**C-4.10-3:** `test_balance_chart_range_buttons_rendered`

- Setup: `seed_user`, `auth_client`.
- Action: GET `/charts/balance-over-time`.
- Expected: Response contains buttons "1Y", "5Y", "10Y", "Full".
- New test.

**F. Manual verification steps.** Open Charts. Click each time frame button. Verify chart updates.

**G. Downstream effects.** None beyond charts page.

**H. Rollback notes.** Route/service/template changes. Revertable.

---

### Commit #15: Task 4.3 -- Pay Period Date Format Cleanup

**A. Commit message:** `feat(grid): condense pay period date headers -- omit year for current year`

**B. Problem statement.** Pay period column headers always show both MM/DD and MM/DD/YY on two lines. The year suffix is redundant for current-year periods and wastes horizontal space.

**C. Files modified.**

- `app/templates/grid/grid.html` -- Modified. Update period header date format logic (lines 81-82).
- `tests/test_routes/test_grid.py` -- Modified. Update date format assertions.

**D. Implementation approach.**

Replace the current two-line header with a conditional format:

```html
{% set current_year = today.year %} {% if period.start_date.year != current_year
or period.end_date.year != current_year %}
<div class="fw-bold">{{ period.start_date.strftime('%m/%d/%y') }}</div>
<div class="small text-light-emphasis">
  {{ period.end_date.strftime('%m/%d/%y') }}
</div>
{% else %}
<div class="fw-bold">
  {{ period.start_date.strftime('%m/%d') }} - {{
  period.end_date.strftime('%m/%d') }}
</div>
{% endif %}
```

`today` is already in the template context from `grid.index()`.

**HTMX regression analysis:** Only modifies `<thead>` content. No HTMX interactions target thead (the carry forward button is inside the `<th>` but below the date display, unaffected). All interactions SAFE.

**E. Test cases.**

**C-4.3-1:** `test_period_header_omits_year_for_current_year`

- Setup: `seed_user`, `auth_client`, periods all within current year.
- Action: GET `/`.
- Expected: Headers show "MM/DD - MM/DD" format (no "/YY").
- New test.

**C-4.3-2:** `test_period_header_includes_year_for_different_year`

- Setup: `seed_user`, `auth_client`, periods spanning into next year.
- Action: GET `/`.
- Expected: Cross-year periods show "MM/DD/YY" on both dates.
- New test.

**F. Manual verification steps.** Open grid. Verify current-year periods show compact format. Scroll right to find cross-year periods and verify year is shown.

**G. Downstream effects.** None. Template-only change to thead.

**H. Rollback notes.** Template-only. Revertable.

---

### Commit #16: Task 4.1 -- Grid Layout: Transaction Name Row Headers

**A. Commit message:** `feat(grid): show transaction names in row headers for clarity`

**B. Problem statement.** The grid shows only the category item name (e.g., "Electricity") in row headers. When multiple transactions share a category item, or when shadow transactions share a "Transfers: Outgoing" category, the user must hover over cells to identify which transaction is which. This adds cognitive overhead during the payday reconciliation workflow.

**C. Files modified.**

- `app/routes/grid.py` -- Modified. Build `row_keys` from transaction data for template-name-based rows.
- `app/templates/grid/grid.html` -- Modified. Replace category-based iteration with row_key iteration for income and expense sections.
- `app/static/css/app.css` -- Modified. Adjust `.row-label-col` width if needed.
- `tests/test_routes/test_grid.py` -- Modified. Update row structure assertions.

**D. Implementation approach.**

**Recommendation: Option A (Full Row Headers).** Each transaction template gets its own row with the transaction name in the row header. This eliminates all ambiguity during the payday workflow.

**Step 1: Build row_keys in the route.**

In `grid.py:index()`, after querying transactions, build a list of "row keys" that represent unique rows in the grid. Each row key is a (category_id, template_id or ad-hoc name, transaction_type) tuple.

```python
def _build_row_keys(transactions, categories, transaction_type_name):
    """Build ordered row keys for the grid, one per unique transaction identity."""
    row_keys = []
    seen = set()

    # Sort by category group, then item, then transaction name.
    for cat in categories:
        if cat.transaction_type != transaction_type_name:
            continue
        # Find all distinct transaction identities for this category.
        for txn in transactions:
            if txn.category_id != cat.id:
                continue
            # Key: use template_id if available, else transaction name.
            key = (cat.id, txn.template_id or txn.name)
            if key not in seen:
                seen.add(key)
                row_keys.append({
                    'category': cat,
                    'group_name': cat.group_name,
                    'item_name': cat.item_name,
                    'display_name': txn.name,  # Transaction name for row label
                    'category_id': cat.id,
                    'template_id': txn.template_id,
                    'txn_name': txn.name,
                    'filter_key': key,
                })
    return row_keys
```

Pass `income_row_keys` and `expense_row_keys` to the template.

**Step 2: Update the grid template.**

Replace the category-based iteration:

```html
<!-- BEFORE: iterate over categories -->
{% for category in income_categories %}
<tr>
  <th class="sticky-col row-label" title="{{ category.display_name }}">
    {{ category.item_name }}
  </th>
  {% for period in periods %}
  <td>{% for txn in txn_by_period[period.id] %}...{% endfor %}</td>
  {% endfor %}
</tr>
{% endfor %}

<!-- AFTER: iterate over row_keys -->
{% for row_key in income_row_keys %} {% if row_key.group_name !=
ns.current_group %} {% set ns.current_group = row_key.group_name %}
<tr class="group-header-row">
  <td colspan="...">{{ row_key.group_name }}</td>
</tr>
{% endif %}
<tr>
  <th class="sticky-col row-label" title="{{ row_key.category.display_name }}">
    {{ row_key.display_name }}
  </th>
  {% for period in periods %}
  <td class="text-end cell">
    {% for txn in txn_by_period.get(period.id, []) %} {% if txn.category_id ==
    row_key.category_id and (txn.template_id == row_key.template_id or txn.name
    == row_key.txn_name) and txn.is_income and not txn.is_deleted and
    txn.status_id != StatusID.CANCELLED %} {% include
    "grid/_transaction_cell.html" %} {% endif %} {% endfor %} {% if not found %}
    {% include "grid/_transaction_empty_cell.html" %} {% endif %}
  </td>
  {% endfor %}
</tr>
{% endfor %}
```

**Step 3: Verify subtotal calculations.** The inline subtotal rows (Total Income, Total Expenses) and Net Cash Flow row iterate over ALL transactions in a period, not over row keys. They are unaffected by the row key restructure. Verify this.

**HTMX regression analysis:**

- GI-1 through GI-8: Target `#txn-cell-<id>` -- SAFE (cell ID unchanged).
- GI-9: Uses `category_id` and `period_id` -- category_id still valid per row key.
- GI-10: Targets `#grid-summary` tfoot -- NOT AFFECTED by tbody changes.
- GI-14: Returns `gridRefresh` -- SAFE (full page reload).
- GI-18: `getDataRows()` excludes `group-header-row`, `subtotal-row`, `net-cash-flow-row` -- SAFE if new rows don't have excluded classes.
- All others: SAFE.

**E. Test cases.**

**C-4.1-1:** `test_grid_renders_transaction_names_in_row_labels`

- Setup: `seed_user`, `auth_client`, two transaction templates in the same category (e.g., "Insurance: Auto" with "State Farm" and "Geico").
- Action: GET `/`.
- Expected: Two separate `<tr>` rows with `<th class="sticky-col row-label">` containing "State Farm" and "Geico" respectively.
- New test.

**C-4.1-2:** `test_grid_shadow_transactions_get_own_rows`

- Setup: `seed_user`, `auth_client`, transfer with shadow transactions.
- Action: GET `/`.
- Expected: Shadow transaction appears in its own row with template name (e.g., "Transfer to Savings") in the row label.
- New test.

**C-4.1-3:** `test_grid_inline_edit_after_layout_change`

- Setup: `seed_user`, `auth_client`, transaction.
- Action: GET `/transactions/<id>/quick-edit`, then PATCH with new amount.
- Expected: Quick edit returns 200. Patch returns 200 with `HX-Trigger: balanceChanged`.
- New test.

**C-4.1-4:** `test_grid_empty_state_after_layout_change`

- Setup: `seed_user`, `auth_client`, periods but no templates.
- Action: GET `/`.
- Expected: Grid renders without errors. Section banners present. Subtotals show zeros.
- New test.

**C-4.1-5:** `test_keyboard_navigation_after_layout_change`

- Setup: `seed_user`, `auth_client`, transactions.
- Action: GET `/`. Inspect row CSS classes.
- Expected: Transaction rows do NOT have excluded classes. Banner/spacer/subtotal/net-cash-flow rows DO have excluded classes.
- New test.

**C-4.1-6:** `test_payday_workflow_complete_after_layout_change`

- Setup: Full payday workflow data.
- Action: Execute C-0-7 sequence (true-up, mark received, carry forward, mark done, mark credit).
- Expected: All steps pass. Balances correct.
- Regression test.

**F. Manual verification steps.**

1. Open the grid. Verify each transaction has its own row with its name in the row header.
2. Verify category group headers still appear above grouped rows.
3. Count rows -- should be one per transaction template plus group headers, subtotals, and banners.
4. Perform the complete payday workflow. Verify every step works.
5. Test keyboard navigation (arrow keys). Verify it skips banner/subtotal rows.
6. Test with a narrow browser window. Verify row labels truncate gracefully.

**G. Downstream effects.**

- The `categories` context variable changes to `income_row_keys` / `expense_row_keys`. Any code that depends on `categories` being passed must be updated.
- The empty cell interaction (GI-9) passes `category_id` which is still present in the row key.
- The Add Transaction modal (GI-16) is unaffected -- it creates transactions with full form data.

**H. Rollback notes.** Route and template changes. Revertable. No migration.

---

### Commit #17: Task 4.12 -- Grid Tooltip Enhancement

**A. Commit message:** `feat(grid): enhance tooltips with full amount and faster display`

**B. Problem statement.** Grid tooltips are slow and show only the transaction name. The tooltip does not show the full dollar amount when the grid displays rounded values.

**C. Files modified.**

- `app/templates/grid/_transaction_cell.html` -- Modified. Update `title` attribute content.
- `app/static/js/app.js` or `grid_edit.js` -- Modified. Reduce Bootstrap tooltip delay.
- `app/static/css/app.css` -- Modified if tooltip styling needs adjustment.
- `tests/test_routes/test_grid.py` -- Modified. Assert tooltip content.

**D. Implementation approach.**

**Content fix:** Update the `title` attribute on `.txn-cell` to include the full formatted amount:

```html
<!-- BEFORE -->
title="{{ t.name }}"

<!-- AFTER (since 4.1 puts the name in the row header, focus tooltip on amount + metadata) -->
title="${{ '{:,.2f}'.format(t.effective_amount) }}{% if t.actual_amount %}
(actual: ${{ '{:,.2f}'.format(t.actual_amount) }}){% endif %}{% if t.status %}
-- {{ t.status.name|capitalize }}{% endif %}"
```

If task 4.1 was implemented with Option A (transaction names in row headers), the tooltip no longer needs to show the name -- it can focus entirely on the full dollar amount, actual vs. estimated, and status.

**Speed fix:** If Bootstrap tooltips are used, initialize with `delay: { show: 200, hide: 0 }` instead of the default 0/0. If the delay is coming from HTMX fetch, pre-render the tooltip content in the `title` attribute (already done) rather than fetching it server-side.

**E. Test cases.**

**C-4.12-1:** `test_tooltip_contains_full_amount`

- Setup: `seed_user`, `auth_client`, transaction with estimated_amount=1234.56.
- Action: GET `/`.
- Expected: Cell `title` attribute contains "$1,234.56".
- New test.

**C-4.12-2:** `test_tooltip_shows_actual_when_set`

- Setup: `seed_user`, `auth_client`, done transaction with actual_amount=1200.00.
- Action: GET `/`.
- Expected: Cell `title` contains "actual: $1,200.00".
- New test.

**F. Manual verification steps.** Hover over a grid cell. Verify tooltip appears quickly and shows full dollar amount.

**G. Downstream effects.** None.

**H. Rollback notes.** Template/JS changes. Revertable.

---

## Final Gate

After all commits are merged, run the full test suite:

```bash
timeout 660 pytest -v --tb=short
```

All tests must pass before reporting the phase as complete.

Additionally, perform a complete manual payday workflow walkthrough:

1. Open the app. Verify the grid loads correctly with transaction names in row headers.
2. True-up the checking balance. Verify projections recalculate.
3. Mark paycheck as received. Verify the status updates and balance reflects actual amount.
4. Carry forward unpaid items from a past period. Verify items move and balances recalculate.
5. Mark cleared expenses as paid (not "done"). Verify status badge shows "Paid" and balance updates.
6. Mark one expense as credit. Verify payback transaction appears in next period.
7. Scan projected balances across future periods. Verify they are reasonable.
8. Visit the accounts dashboard. Verify emergency fund coverage reflects transfers. Verify "Setup Required" badges on unconfigured accounts.
9. Visit the checking account detail page. Verify balance projection is displayed.
10. Visit the salary profile page. Verify View Breakdown and View Projection buttons are accessible near the top.
11. Visit the tax config page. Verify State Tax is first, Federal brackets are collapsed.
12. Visit the retirement dashboard. Verify the return rate slider has explanatory text. Test date validation error display.
13. Open the Charts page. Verify Balance Over Time chart has distinct line styles and time frame controls.
14. Create a new HYSA account. Verify redirect to parameter page with setup banner.
15. Test keyboard navigation on the grid (arrow keys, Enter, Escape, F2).
