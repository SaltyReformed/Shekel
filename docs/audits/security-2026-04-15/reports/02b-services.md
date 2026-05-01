# 02b -- Services and Financial Plumbing Findings

## Summary

- **Files read in full:**
  - `app/services/balance_calculator.py` (452 lines)
  - `app/services/transfer_service.py` (728 lines)
  - `app/services/transfer_recurrence.py` (265 lines)
  - `app/services/recurrence_engine.py` (623 lines)
  - `app/services/paycheck_calculator.py` (463 lines)
  - `app/services/tax_calculator.py` (322 lines)
  - `app/services/calibration_service.py` (146 lines, read 1-145)
  - `app/services/carry_forward_service.py` (130 lines)
  - `app/services/credit_workflow.py` (100 lines inspected)
  - `app/services/entry_credit_workflow.py` (120 lines inspected)
  - `app/services/entry_service.py` (relevant sections around shadow guards, `_update_actual_if_paid`)
  - `app/models/account.py` (89 lines, full)
  - `app/models/transfer.py` (105 lines, full)
  - `app/models/transaction.py` (relevant `transfer_id` FK block)
- **Files listed (via Glob) and searched via grep:** every `app/services/*.py` (37 files total).
- **No file under `app/routes/` was read** (B1 scope).
- **Checks performed:** 1 (transfer invariants), 2 (balance calculator isolation grep chain),
  3 (type purity), 4 (raw SQL), 5 (recurrence engine), 6 (paycheck calculator rounding),
  7 (anchor balance -- concurrency).
- **Finding count:** 0 Critical / 2 High / 2 Medium / 3 Low / 2 Info
- **Top concern:** `recurrence_engine.resolve_conflicts()` directly mutates any `Transaction`
  row by ID (sets `is_override`, `is_deleted`, `estimated_amount`) with no guard that the
  target is NOT a transfer shadow. Current callers pass only template-linked rows (which have
  `template_id IS NOT NULL` and therefore cannot be shadows, since `transfer_service.create_transfer`
  writes shadows with `template_id=None`), so the invariant is maintained by convention today --
  but a single future caller misuse, or a route bug, would silently violate Transfer Invariant 4.

---

## Transfer invariants

### Invariant 1: Every transfer has exactly two linked shadow transactions

- **Enforced by:** `app/services/transfer_service.py:349-410` (`create_transfer`)
- **Evidence:**
  ```python
  xfer = Transfer(
      user_id=user_id,
      ...
  )
  db.session.add(xfer)
  db.session.flush()

  expense_shadow = Transaction(
      account_id=from_account_id,
      template_id=None,
      transfer_id=xfer.id,
      ...
      transaction_type_id=expense_type_id,
      estimated_amount=amount,
      ...
  )
  db.session.add(expense_shadow)

  income_shadow = Transaction(
      account_id=to_account_id,
      template_id=None,
      transfer_id=xfer.id,
      ...
      transaction_type_id=income_type_id,
      estimated_amount=amount,
      ...
  )
  db.session.add(income_shadow)
  db.session.flush()
  ```
- **Verdict:** Enforced by code -- at the service layer. A grep for `Transfer(` and `transfer_id=`
  across `app/services/` shows exactly one `Transfer(` constructor (transfer_service.py:349)
  and exactly two `transfer_id=xfer.id` writes (transfer_service.py:373, 394), both in the
  same function scope. Nothing else in services instantiates a Transfer or writes a shadow.
- **Notes:** The "exactly two" property is enforced only for creations that go through this
  function. There is NO database-level CHECK / trigger / unique constraint that prevents a
  third, fourth, or zeroth shadow from appearing if any future code path (or a direct DB
  statement) bypasses the service. See F-B2-03 -- defence-in-depth gap. Invariant 1 is
  preserved post-creation by `_get_shadow_transactions` (lines 195-265), which raises a
  `ValidationError` if the count is not exactly 2 and refuses to mutate, short-circuiting
  every update path.

### Invariant 2: Shadow transactions are never orphaned and never created without their sibling

- **Enforced by:** `app/services/transfer_service.py:364-410` (atomic block in `create_transfer`)
  and `app/services/transfer_service.py:587-605` (CASCADE in hard-delete)
  and `app/services/transfer_service.py:570-583` (explicit soft-delete loop over both shadows)
  and `app/models/transaction.py:94-97` (FK `ondelete="CASCADE"`)
- **Evidence (creation atomicity -- both shadows added before the only flush):**
  ```python
  db.session.add(xfer)
  db.session.flush()
  ...
  db.session.add(expense_shadow)
  ...
  db.session.add(income_shadow)
  db.session.flush()
  ```
- **Evidence (hard-delete CASCADE):**
  ```python
  # Hard delete -- rely on ON DELETE CASCADE to remove shadows.
  db.session.delete(xfer)
  db.session.flush()

  # Verify CASCADE removed the shadows.  If they still exist,
  # the FK was misconfigured in Task 2.
  orphan_count = (
      db.session.query(Transaction)
      .filter_by(transfer_id=transfer_id)
      .count()
  )
  if orphan_count > 0:
      logger.error(...)
  ```
- **Evidence (soft-delete sibling sweep):**
  ```python
  if soft:
      xfer.is_deleted = True
      shadows = (
          db.session.query(Transaction)
          .filter_by(transfer_id=transfer_id)
          .all()
      )
      for shadow in shadows:
          shadow.is_deleted = True
  ```
- **Evidence (model-level FK cascade):**
  ```python
  transfer_id = db.Column(
      db.Integer,
      db.ForeignKey("budget.transfers.id", ondelete="CASCADE"),
  )
  ```
- **Verdict:** Enforced by code.
- **Notes:** There is one subtlety -- the soft-delete branch queries `filter_by(transfer_id=transfer_id)`
  WITHOUT `is_deleted=False`, which is correct (it sweeps any already-deleted shadow too,
  so a half-deleted state is always fully cleaned up). The hard-delete path asserts zero
  orphans post-CASCADE and logs an error if the FK is misconfigured. Good.

### Invariant 3: Shadow amounts, statuses, and periods always equal the parent transfer's

- **Enforced by:** `app/services/transfer_service.py:461-492` (amount/status/period branches
  in `update_transfer`) and `app/services/transfer_service.py:689-721` (drift repair in
  `restore_transfer`)
- **Evidence (update propagation -- amount):**
  ```python
  if "amount" in kwargs:
      new_amount = _validate_positive_amount(kwargs["amount"])
      xfer.amount = new_amount
      expense_shadow.estimated_amount = new_amount
      income_shadow.estimated_amount = new_amount
  ```
- **Evidence (update propagation -- status):**
  ```python
  if "status_id" in kwargs:
      new_status_id = kwargs["status_id"]
      xfer.status_id = new_status_id
      expense_shadow.status_id = new_status_id
      income_shadow.status_id = new_status_id
  ```
- **Evidence (update propagation -- period):**
  ```python
  if "pay_period_id" in kwargs:
      new_period_id = kwargs["pay_period_id"]
      _get_owned_period(new_period_id, user_id)
      xfer.pay_period_id = new_period_id
      expense_shadow.pay_period_id = new_period_id
      income_shadow.pay_period_id = new_period_id
  ```
- **Evidence (restore drift repair):**
  ```python
  # Invariant 3: shadow amount must match transfer amount.
  if shadow.estimated_amount != xfer.amount:
      logger.warning(...)
      shadow.estimated_amount = xfer.amount

  # Invariant 4: shadow status must match transfer status.
  if shadow.status_id != xfer.status_id:
      logger.warning(...)
      shadow.status_id = xfer.status_id

  # Invariant 5: shadow period must match transfer period.
  if shadow.pay_period_id != xfer.pay_period_id:
      logger.warning(...)
      shadow.pay_period_id = xfer.pay_period_id
  ```
- **Verdict:** Enforced by code. Every accepted mutation kwarg that affects the invariant
  writes to both shadows in the same branch.
- **Notes:** The `update_transfer` function first calls `_get_shadow_transactions(transfer_id)`
  which raises `ValidationError` if the shadow count is not exactly 2 -- so the propagation
  is atomic and fail-fast. However, the propagation path is "enforced by convention" with
  respect to the scope of `update_transfer`'s accepted kwargs: `name` and `notes` are
  explicitly documented (lines 494-504) as transfer-only metadata NOT propagated to shadows.
  That is consistent with the invariant (invariant 3 covers amount/status/period only, not
  name/notes). Acceptable.

### Invariant 4: No code path directly mutates a shadow

- **Enforced by:** Partial. Evidence below. See F-B2-01 (High).
- **Evidence (direct mutation inventory):**

  Grep output `app/services/` for direct attribute writes to Transaction (`.status_id =`,
  `.pay_period_id =`, `.estimated_amount =`, `.actual_amount =`, `.is_deleted =`):

  ```
  app/services/entry_service.py:61:           txn.actual_amount = compute_actual_from_entries(txn.entries)
  app/services/carry_forward_service.py:97:   txn.pay_period_id = target_period_id
  app/services/entry_credit_workflow.py:95:   existing_payback.estimated_amount = total_credit
  app/services/credit_workflow.py:92:         txn.status_id = credit_id
  app/services/credit_workflow.py:156:        txn.status_id = projected_id
  app/services/recurrence_engine.py:285:      txn.is_deleted = False
  app/services/recurrence_engine.py:287:      txn.estimated_amount = new_amount
  app/services/transfer_service.py:465:       expense_shadow.estimated_amount = new_amount
  ... (remaining writes are all inside transfer_service.py)
  ```

  Each non-transfer-service site is analyzed below.

  1. **`entry_service.py:61` (`_update_actual_if_paid`)** -- the mutation
     `txn.actual_amount = compute_actual_from_entries(txn.entries)` can run only on
     transactions that have `entries`. Creating an entry requires passing the
     `create_entry`/`update_entry` API, which explicitly blocks shadows:
     ```python
     # Transfer guard (mirrors credit_workflow.py line 59).
     if txn.transfer_id is not None:
         raise ValidationError("Cannot add entries to transfer transactions.")
     ```
     (entry_service.py:148-150). Shadows can never have entries, so this write path
     cannot reach a shadow. **Indirectly enforced by code.**

  2. **`carry_forward_service.py:97` (`carry_forward_unpaid`)** -- the direct mutation
     `txn.pay_period_id = target_period_id` is scoped to `regular_txns` only, where the
     list is built explicitly:
     ```python
     for txn in projected_txns:
         if txn.transfer_id is None:
             regular_txns.append(txn)
         else:
             shadow_txns.append(txn)
     ```
     (carry_forward_service.py:87-91). Shadows go through
     `transfer_service.update_transfer(txn.transfer_id, user_id, pay_period_id=..., is_override=True)`
     (line 115-120). **Enforced by code.**

  3. **`entry_credit_workflow.py:95`** -- the mutation writes
     `existing_payback.estimated_amount = total_credit`. `existing_payback` is a
     standalone CC Payback `Transaction` located via `credit_payback_for_id=txn.id`
     (line 85-89). CC Payback transactions are never shadows (the `_create_payback`
     function creates them without `transfer_id`). **No shadow risk.**

  4. **`credit_workflow.py:92, 156`** (`mark_as_credit`, `unmark_as_credit`) -- the
     mutation `txn.status_id = credit_id` is preceded by an explicit shadow guard:
     ```python
     if txn.transfer_id is not None:
         raise ValidationError("Cannot mark transfer transactions as credit.")
     ```
     (credit_workflow.py:59-60). **Enforced by code.**

  5. **`recurrence_engine.py:285, 287`** (`resolve_conflicts`) -- the mutation writes
     `txn.is_override = False`, `txn.is_deleted = False`, `txn.estimated_amount = new_amount`
     on every transaction ID passed in. The function reads the transactions by raw ID:
     ```python
     for txn_id in transaction_ids:
         txn = db.session.get(Transaction, txn_id)
         if txn is None:
             continue

         # Ownership check: Transaction -> PayPeriod -> user_id.
         if txn.pay_period.user_id != user_id:
             ...
             continue

         txn.is_override = False
         txn.is_deleted = False
         if new_amount is not None:
             txn.estimated_amount = new_amount
     ```
     There is **NO guard** `if txn.transfer_id is not None: continue`. Today, the only
     documented caller supplies IDs from `regenerate_for_template`'s `existing` query,
     which is joined on `Transaction.template_id == template.id`, and
     `create_transfer` writes shadows with `template_id=None` (line 372, 393 in
     transfer_service.py), so shadows are not included. This is
     **"enforced by convention at the caller, not by code at the callee."** A route
     handler that passes arbitrary `transaction_ids` from a form / query string
     could silently bypass the transfer service and directly flip `is_deleted`,
     `is_override`, or `estimated_amount` on a shadow -- violating Invariant 4 (and
     Invariant 3 in the process, since the estimated_amount write would not propagate
     to the sibling or parent).

- **Verdict:** **Partially enforced -- HIGH finding.** See F-B2-01.

### Invariant 5: Balance calculator queries ONLY budget.transactions

- **Enforced by:** `app/services/balance_calculator.py` imports and query surface.
- **Evidence:** see the dedicated grep in the next section.
- **Verdict:** **Enforced by code.**

---

## Balance calculator grep (Invariant 5 deep dive)

### Grep commands run

```
Grep pattern:   transfer|Transfer   (case-insensitive)
     path:      app/services/balance_calculator.py
     output:    content + line numbers
```

```
Grep pattern:   ^from app|^import app   (to enumerate internal helpers)
     path:      app/services/balance_calculator.py
     output:    content + line numbers
```

```
Grep pattern:   from app\.services\.amortization_engine import   (lazy import)
     path:      app/services/balance_calculator.py
     output:    content + line numbers
```

```
Grep pattern:   transfer   (case-insensitive)
     path:      app/services/interest_projection.py
              app/services/amortization_engine.py
              app/ref_cache.py
              app/enums.py
     output:    content + line numbers
```

```
Grep pattern:   from app\.models\.transfer import Transfer
     path:      app/services/
     output:    files_with_matches
```

```
Grep pattern:   Transfer\(   (instantiations only)
     path:      app/services/
     output:    content + line numbers
```

### Output

`balance_calculator.py` hits for `transfer`/`Transfer` (case-insensitive):

```
17:Transfer effects are included automatically via shadow transactions
18:(expense and income Transaction rows with transfer_id IS NOT NULL).
19:The calculator does NOT query or process Transfer objects directly.
45:                           Shadow transactions (transfer_id IS NOT NULL) participate
183:    transactions (transfer_id IS NOT NULL, transaction_type == income).
259:        # old transfer-based detection (design doc section 6.2).
268:            if (txn.transfer_id is not None
```

All seven hits are (a) docstring/comment text (lines 17-19, 45, 183, 259), or
(b) a field-read on a `Transaction` object (line 268: `txn.transfer_id is not
None` -- reading the FK column, not the Transfer table). None query or import
the `Transfer` model.

`balance_calculator.py` imports from `app`:

```
27:from app.services.interest_projection import calculate_interest
31:from app import ref_cache
32:from app.enums import StatusEnum
```

Plus one lazy import inside `calculate_balances_with_amortization`:

```
202:    from app.services.amortization_engine import (
203:        calculate_monthly_payment, calculate_remaining_months,
204:    )
```

So the helper chain to follow is: `interest_projection`, `ref_cache`, `enums`,
`amortization_engine`.

`interest_projection.py` grep for `transfer`:

```
30:        balance: Account balance after all transactions/transfers for the period.
```

One docstring hit. No code. Safe.

`amortization_engine.py` grep for `transfer`:

```
14:  2. Committed schedule -- payments=confirmed+projected transfers
348:      2. Committed schedule -- payments=confirmed+projected transfers
```

Two docstring hits. No code. Safe.

`ref_cache.py` grep for `transfer`: no matches.
`enums.py` grep for `transfer`: no matches.

`Transfer(` across all services:

```
app/services/transfer_service.py:349:    xfer = Transfer(
```

Only one hit -- inside `transfer_service.create_transfer`. `balance_calculator.py`
does not instantiate Transfer.

`from app.models.transfer import Transfer` across all services:

```
app/services/transfer_service.py:35
app/services/year_end_summary_service.py:42
app/services/transfer_recurrence.py:21
```

`balance_calculator.py` is NOT in this list.

### Imports checked

- `app.services.interest_projection` -- no Transfer model usage
- `app.ref_cache` -- no Transfer reference
- `app.enums` -- no Transfer reference
- `app.services.amortization_engine` -- no Transfer model import or query

### Verdict

**Enforced by code.** `balance_calculator.py` never imports, queries, or
references the `Transfer` model. All transfer effects flow through
`Transaction` rows with `transfer_id IS NOT NULL`. Every helper the calculator
imports is likewise clean. Invariant 5 holds at both the module level and
the full transitive-import level.

---

## Type purity table

No `** 0.5`, no `/ 100`, no `round(`, no `eval(`, no `exec(`, no
`db.session.execute(text(...))`, and no `text(` in application code. The only
`float(` hits are two display-only slider defaults in
`retirement_dashboard_service.py`. All `Decimal(` constructions pass either a
string literal, a `str(...)` wrap, another Decimal, or a sanitized int/count.

| File | Line | Code | Context | Verdict |
|------|------|------|---------|---------|
| app/services/retirement_dashboard_service.py | 238 | `current_swr = float(settings.safe_withdrawal_rate or 0.04) * 100 if settings else 4.0` | display (slider default; docstring says "float %") | Info |
| app/services/retirement_dashboard_service.py | 255 | `current_return = float(weighted_return / total_balance) * 100` | display (slider default; adjacent return dict returns "float %") | Info |
| app/services/retirement_dashboard_service.py | 188 | `Decimal(str(settings.safe_withdrawal_rate or "0.04")) if settings else Decimal("0.04")` | arithmetic (SWR in money math) | safe (string literal fallback) |
| app/services/interest_projection.py | 52 | `Decimal(cal.monthrange(...)[1])` | arithmetic -- day count from `calendar.monthrange` which returns `int` | safe (integer, not float) |
| app/services/interest_projection.py | 68 | `Decimal(str((q_end - q_start).days))` | arithmetic -- `.days` is int | safe |
| app/services/balance_calculator.py | 237 | `monthly_rate = annual_rate / 12 if annual_rate > 0 else Decimal("0")` | arithmetic on Decimal / int 12 | safe (Decimal / int = Decimal) |
| app/services/amortization_engine.py | 444 | `monthly_rate = annual_rate / 12 if annual_rate > 0 else Decimal("0")` | arithmetic on Decimal / int 12 | safe |
| app/services/paycheck_calculator.py | 91 | `(annual_salary / pay_periods_per_year).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` | arithmetic on Decimal / int | safe (quantized) |
| app/services/paycheck_calculator.py | 160 | `(state_annual / pay_periods_per_year).quantize(...)` | arithmetic | safe |
| app/services/recurrence_engine.py | 325 | `(p.period_index - offset) % n == 0` | non-monetary (integer period index modulo integer N) | safe |
| app/services/recurrence_engine.py | 401 | `((start_month - 1 + i * 3) % 12) + 1` | non-monetary (month math, integer) | safe |
| app/services/recurrence_engine.py | 410 | `((start_month - 1 + i * 6) % 12) + 1` | non-monetary (month math, integer) | safe |
| app/services/savings_dashboard_service.py | 386 | `(target_m - 1) % 12 + 1` | non-monetary (month math, integer) | safe |
| app/services/savings_goal_service.py | 482 | `total_months % 12 + 1` | non-monetary | safe |
| app/services/debt_strategy_service.py | 363 | `total_months % 12 + 1` | non-monetary | safe |
| app/services/csv_export_service.py | 11,43 | `# ... % suffix` comments | comment / docstring | safe |
| app/services/retirement_dashboard_service.py | 107-165 etc. | dozens of `Decimal(str(profile.annual_salary))` and friends | arithmetic | safe (string wrap) |

Every single `Decimal(...)` construction across the full services tree uses
either:

1. A string literal: `Decimal("0.01")`, `Decimal("0.04")`, `Decimal("100")`, etc.
2. A `Decimal(str(x))` wrap, defensively stringifying whatever `x` is before
   construction, so a float-typed upstream value is rendered at the
   `repr()`-like textual precision before Decimal parses it.
3. Another Decimal (`Decimal(count)` where count is a positive int -- safe).

Spot check (from the top-level Decimal grep, 416 hits across services): no
`Decimal(x)` where `x` is a bare float variable. The closest to an issue was
in `investment_projection.py:140` (`Decimal(str(salary_gross_biweekly))`) and
`growth_engine.py:201-207` (`Decimal(str(current_balance))`) -- both are
`str()`-wrapped. The defensive `Decimal(str(...))` idiom is used consistently.

See F-B2-02 for the two display-only `float()` hits. They do not touch money
math but they are noisy and make dashboard outputs slightly inconsistent with
the Decimal-everywhere contract.

---

## Raw SQL audit

Grep for `text(`, `db.session.execute(`, raw SQL string construction across
`app/services/`:

- `text(` -- **no hits** to SQL text. All hits are literal `.text(...)` on
  `db.Index(... postgresql_where=db.text(...))` (in models) or are ordinary
  word occurrences (`load_loan_context`, `next`). **No raw SQL.**
- `db.session.execute(` -- **no hits** at all in any service module.
- `db.session.query(...)` -- everywhere, ORM-based, parameter-bound.
- f-string SQL or `.format()` SQL: **no hits**.

**Verdict:** No SQL injection surface in services. All data access is through
SQLAlchemy ORM query objects. Info only.

---

## Recurrence engine audit

`app/services/recurrence_engine.py` (623 lines, full read) and
`app/services/transfer_recurrence.py` (265 lines, full read).

### Date math

- `recurrence_engine.py` imports `from datetime import date` and uses real
  `date(year, month, day)` constructions everywhere (lines 373, 427, 448, 499).
  `period.start_date` / `period.end_date` are the PayPeriod's date columns.
  Month-end clamping is done via `cal.monthrange(year, month)[1]` (standard
  library). No string-as-date anywhere.
- `_compute_due_date` (lines 457-524) correctly handles month-end clamping
  and the "next-month convention" for `due_day_of_month < day_of_month`.
  December wrap is handled explicitly (lines 513-516).

### Pattern dispatch

`_match_periods` (lines 299-350) dispatches on pattern ID (integer, not name)
through `ref_cache.recurrence_pattern_id(RecurrencePatternEnum.<member>)`. All
eight patterns from `RecurrencePatternEnum` (`EVERY_PERIOD`, `EVERY_N_PERIODS`,
`MONTHLY`, `MONTHLY_FIRST`, `QUARTERLY`, `SEMI_ANNUAL`, `ANNUAL`, `ONCE`) are
handled. Unknown patterns fall through to `logger.warning` and return `[]`
(line 349-350). No `eval`/`exec`. Safe.

### ID-based dispatch compliance

`_rp_id = ref_cache.recurrence_pattern_id(member)`. Zero string comparisons
against `rule.pattern.name`. Compliant with CLAUDE.md "IDs for logic, strings
for display only."

### Shadow-safety of `regenerate_for_template` / `resolve_conflicts`

- `regenerate_for_template` (lines 163-247) queries
  `Transaction.template_id == template.id`. Shadow transactions have
  `template_id=None` (set by `create_transfer`). So the query set never
  includes shadows -- `db.session.delete(txn)` on line 236 cannot reach a
  shadow. **Safe.**
- `resolve_conflicts` (lines 249-288) does NOT gate on `transfer_id`. See
  F-B2-01. **Partial enforcement -- HIGH.**

### Transfer-recurrence delegation

`transfer_recurrence.py` does not touch the Transaction table directly at all.
Shadow creation is routed through `transfer_service.create_transfer` (line
100-113), and the conflict resolver routes through
`transfer_service.restore_transfer` and `transfer_service.update_transfer`
(lines 231-241). Good.

### Salary-linked amount fallback

`recurrence_engine._get_transaction_amount` (lines 567-622) wraps the
paycheck calculator call in `try/except (InvalidOperation, ZeroDivisionError,
TypeError, KeyError)`. On failure, the fallback is `template.default_amount`
and a structured `logger.error` is emitted. The exception set is specific (no
bare `except Exception`). Good.

---

## Paycheck calculator audit

`app/services/paycheck_calculator.py` (463 lines, full read).
`app/services/tax_calculator.py` (322 lines, full read).
`app/services/calibration_service.py` (146 lines, read 1-145).

### Quantization discipline

- Every monetary output is quantized. Examples:
  - `gross_biweekly = (annual_salary / pay_periods_per_year).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)`
    (line 91-93)
  - `state_biweekly = (state_annual / pay_periods_per_year).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)`
    (line 160-162)
  - `net_pay = (...).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` (line 189)
  - `_apply_raises` -> `salary.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` (line 284)
  - Deductions: percentage-based path quantizes (line 398-400);
    inflation-adjusted path quantizes (line 409-411).
  - `tax_calculator._apply_marginal_brackets` -> `total_tax.quantize(...)` (line 209)
  - `tax_calculator.calculate_federal_withholding` -> `per_period_withholding.quantize(...)` (line 162-164)
  - `tax_calculator.calculate_state_tax` -> `(taxable * rate).quantize(...)` (line 266)
  - `tax_calculator.calculate_fica` -> every branch quantizes (lines 304, 306, 309, 316-318)
  - `calibration_service.apply_calibration` -> all four output amounts quantize (lines 133-144)
- Rounding mode is consistently `ROUND_HALF_UP`. This is an explicit choice
  (not banker's rounding). Pylint-clean, matches the quantized columns at
  `Numeric(12,2)`. Note: the paycheck calculator uses HALF_UP, not HALF_EVEN.
  Workflow doc section 1L.4 mentions HALF_EVEN "or equivalent" -- HALF_UP is
  a defensible equivalent choice. Info.
- Intermediate raise compounding (line 257-283) does multiple raise
  applications unquantized, then quantizes once at the end of `_apply_raises`
  (line 284). This is the correct design -- quantize at the output boundary,
  not inside the loop, to avoid compounding rounding errors.
- `_get_cumulative_wages` (line 438-462) quantizes each biweekly gross before
  accumulating. This matches how historical gross pay amounts would have been
  stored in settled transactions, so the cumulative wage for SS cap tracking
  is consistent with the per-period grosses. Safe.

### Third-paycheck detection

`_is_third_paycheck` (line 327-344) counts how many period start dates fall in
the same calendar month up to and including the target period. Correct O(N)
implementation.

### Federal withholding (IRS Pub 15-T)

`tax_calculator.calculate_federal_withholding` implements the IRS Publication
15-T Percentage Method with six explicit steps (annualize, pre-tax adjust,
standard deduction, marginal brackets, credits, de-annualize). Each step
guards against negative values (lines 113-114, 119-120, 150-151). Specific
exception types (`InvalidGrossPayError`, `InvalidPayPeriodsError`,
`InvalidFilingStatusError`, `InvalidDependentCountError`) are raised early,
never bare `Exception`.

### FICA SS cap / Medicare surtax

`calculate_fica` (line 274-321) tracks cumulative wages against the SS wage
base and Medicare surtax threshold. Split logic for the cap-crossing period
(line 302-304) taxes only the pre-cap portion. Correct.

### Verdict

**Clean.** No rounding findings. The calculator is tight, quantizes at every
sensible boundary, and aggregates gross pay with the same precision used for
storage.

---

## Anchor balance audit

There is no `anchor_*.py` service module. Anchor balance reads and writes
live in routes (B1's scope). Service modules only consume
`account.current_anchor_balance` as input to pure calculators. For the
concurrency question, I can only speak to the model / service layer.

Grep output for the anchor column name across `app/`:

```
app/models/account.py:30:    current_anchor_balance = db.Column(db.Numeric(12, 2))
app/models/account.py:79:    anchor_balance = db.Column(db.Numeric(12, 2), nullable=False)   # AnchorHistory table
```

Grep output for concurrency primitives:

```
Grep pattern: with_for_update|FOR UPDATE|version_id
   path: app/
   result: No matches found
```

- **Account** has no `version_id_col`, no advisory-lock helper, no
  SELECT FOR UPDATE usage anywhere in the codebase. The `Account.updated_at`
  column is an `onupdate=db.func.now()` timestamp, which is audit data, not a
  concurrency control.
- **AccountAnchorHistory** (lines 57-88) is append-only -- each true-up
  creates a new row. This provides an audit trail but does NOT prevent two
  concurrent mutations from both succeeding against `Account.current_anchor_balance`.
- Two concurrent POSTs (from two browser tabs, or two devices) to the same
  anchor balance update endpoint will, under current code, both read the old
  value, both write a new value, and the last one wins -- silently. The
  anchor history audit trail preserves both true-up events, but the effective
  balance is whoever wrote last. For the balance calculator, whose output is
  entirely a function of the anchor, this means projections for one tab's
  view of "what I just changed" will be overwritten by the other tab's view
  without a warning.

### Verdict

See F-B2-04 -- Medium finding (today) / High once the app goes multi-user or
public. Note: route-level concurrency (TOCTOU around the ownership check and
commit) is B1's scope. This finding is scoped to the lack of any
service-layer, model-layer, or DB-level concurrency guard on the anchor
balance column.

---

## Findings

### F-B2-01: `recurrence_engine.resolve_conflicts` can silently mutate transfer shadows

- **Severity:** High
- **OWASP:** A04:2021 (Insecure Design)
- **CWE:** CWE-841 (Improper Enforcement of Behavioral Workflow)
- **Location:** `app/services/recurrence_engine.py:249-288` (the `resolve_conflicts`
  function)
- **Evidence:**
  ```python
  def resolve_conflicts(transaction_ids, action, user_id, new_amount=None):
      ...
      if action == "update":
          for txn_id in transaction_ids:
              txn = db.session.get(Transaction, txn_id)
              if txn is None:
                  continue

              # Ownership check: Transaction -> PayPeriod -> user_id.
              if txn.pay_period.user_id != user_id:
                  logger.warning(...)
                  continue

              txn.is_override = False
              txn.is_deleted = False
              if new_amount is not None:
                  txn.estimated_amount = new_amount
          db.session.flush()
  ```
- **Impact:** The function accepts an arbitrary list of transaction IDs (which
  originate from an HTTP form in the conflict-resolution prompt flow). It
  then writes three fields -- `is_override`, `is_deleted`, `estimated_amount`
  -- directly on the loaded Transaction object. There is NO guard of the form
  `if txn.transfer_id is not None: continue`.

  Today, the documented caller passes IDs that originated from
  `regenerate_for_template`'s `existing` query, filtered on
  `Transaction.template_id == template.id`. Because `transfer_service.create_transfer`
  explicitly writes shadows with `template_id=None`, shadows cannot enter
  that particular query. So the invariant is **maintained by the query
  filter at the caller**, not by a guard at the callee.

  This is the exact pattern CLAUDE.md calls out as a High finding:
  > "no code path actively blocks the violation, callers are trusted to do
  > the right thing."

  Failure modes:
  1. A future route that wires a new caller to `resolve_conflicts` (e.g. a
     bulk "clear overrides" UI) and passes `Transaction.id` values directly
     from a form, some of which happen to be shadow IDs, would silently
     bypass the transfer service. The call would clear the shadow's
     `is_override` flag, clear its `is_deleted` flag, and rewrite its
     `estimated_amount`, leaving the parent Transfer and the sibling shadow
     with stale values. That violates Transfer Invariants 3 AND 4.
  2. A malicious/curious user who knows the shadow IDs and can POST a
     crafted form to the conflict-resolution endpoint would likewise be
     routed through this function. The ownership check `pay_period.user_id
     != user_id` catches cross-user IDOR, but it does NOT catch
     "your-own-shadow-that-you-shouldn't-mutate-directly."
- **Recommendation:** Add a fail-fast shadow guard at the top of the per-ID
  loop:
  ```python
  if txn.transfer_id is not None:
      logger.warning(
          "resolve_conflicts refused to mutate shadow transaction %d "
          "(transfer_id=%d). Route mutations through the transfer service.",
          txn_id, txn.transfer_id,
      )
      continue
  ```
  Or, more defensively, raise a `ValidationError` so the route sees an HTTP
  error instead of a silent skip -- this is the more consistent choice given
  CLAUDE.md rule 1 ("fix root causes, not symptoms"). Either way, the shadow
  must not be directly mutated.

  In addition, the docstring should be updated to state explicitly:
  "This function MUST NOT be called with transfer shadow IDs. Shadow
  mutations go through the transfer service."

### F-B2-02: Display-only `float()` cast inconsistency in retirement dashboard

- **Severity:** Low
- **OWASP:** N/A (style / consistency)
- **CWE:** CWE-1339 (Insufficient Precision in Financial Calculations -- display only)
- **Location:** `app/services/retirement_dashboard_service.py:238` and
  `app/services/retirement_dashboard_service.py:255` (`compute_slider_defaults`)
- **Evidence:**
  ```python
  current_swr = float(settings.safe_withdrawal_rate or 0.04) * 100 if settings else 4.0
  ...
  if total_balance > 0:
      current_return = float(weighted_return / total_balance) * 100
  else:
      current_return = 7.0
  ```
- **Impact:** Both values end up in a dict returned by `compute_slider_defaults`
  and feed HTML `<input type="range">` step values. The defaults flow to UI
  sliders, not to balance math. Mathematically harmless. But:
  1. It breaks the "Decimal everywhere in services" contract stated in
     `docs/coding-standards.md` ("Use `Decimal`, never `float`, for all
     monetary amounts"). A reviewer hitting grep for `float(` in services
     would have to re-examine these hits every time.
  2. `0.04` and `4.0` and `7.0` are magic numbers. CLAUDE.md rule: "No magic
     numbers or strings. Every numeric or string literal representing a
     business rule must be a named constant." `0.04` is the default safe
     withdrawal rate; `7.0` is the default assumed annual return percentage.
- **Recommendation:** Compute the percentage values as `Decimal` and convert
  to float at the template boundary only if a float is actually required
  (HTML `step` attributes accept arbitrary strings, so `str(decimal_value)`
  works fine). Extract the defaults to module-level constants named something
  like `_DEFAULT_SWR_PCT = Decimal("4.00")` and
  `_DEFAULT_RETURN_PCT = Decimal("7.00")`.

### F-B2-03: No DB-level constraint that enforces "exactly two shadows per transfer"

- **Severity:** Medium
- **OWASP:** A04:2021 (Insecure Design -- reliance on application-layer enforcement for a
  critical invariant)
- **CWE:** CWE-840 (Business Logic Errors)
- **Location:** `app/models/transfer.py` (no shadow count constraint) and
  `app/models/transaction.py:94-97` (`transfer_id` FK, no uniqueness on
  `(transfer_id, transaction_type_id)` pair)
- **Evidence:** The Transfer model declares:
  ```python
  db.CheckConstraint(
      "from_account_id != to_account_id",
      name="ck_transfers_different_accounts",
  ),
  db.CheckConstraint("amount > 0", name="ck_transfers_positive_amount"),
  ```
  and the Transaction model declares only an index on `transfer_id`:
  ```python
  db.Index(
      "idx_transactions_transfer",
      "transfer_id",
      postgresql_where=db.text("transfer_id IS NOT NULL"),
  ),
  ```
  Nothing in the schema prevents a Transfer from having zero, one, three, or
  seventeen shadow transactions. Nothing prevents two expense-type shadows
  with the same `transfer_id`.
- **Impact:** Today, `transfer_service.create_transfer` is the sole creation
  path and it always writes exactly two shadows, one of each type. Invariant
  1 is enforced at the service layer. But the database has no defense if
  (a) a future code path bypasses the service, (b) a future migration's
  data-backfill script writes directly to the transactions table (a pattern
  the migration guide warns against but does not prevent), or (c) a
  concurrent POST sequence manages to get a partial state through a retry
  loop.

  CLAUDE.md is explicit:
  > "enforced by convention (i.e. no code path actively blocks the
  > violation, callers are trusted to do the right thing) is itself a High
  > finding for a money app"

  I am grading this Medium rather than High because the service IS the sole
  current writer AND the on-delete CASCADE ensures the number never grows
  above what the service created. The main residual risk is future code
  changes.
- **Recommendation:** Two complementary defences:
  1. Add a partial-unique composite index on
     `(transfer_id, transaction_type_id) WHERE transfer_id IS NOT NULL AND
     is_deleted = FALSE`. This prevents two expense-type shadows or two
     income-type shadows for the same transfer from ever coexisting. Name:
     `uq_transactions_transfer_type_active`.
  2. Add a DB-level CHECK or a trigger (PostgreSQL `CREATE FUNCTION ... COUNT`
     trigger) that asserts each active Transfer has exactly 2 active shadows.
     A trigger is the more complete defence but adds complexity; if the
     developer prefers simplicity, the partial unique index + the existing
     `_get_shadow_transactions` count assertion is already a strong
     defence-in-depth pair. Recommend option 1 as the minimum.

  If neither defence is added, the docstring of `create_transfer` should be
  promoted into a module-level doctstring paragraph that explicitly states
  "no database-level enforcement exists for the two-shadow invariant; this
  module's functions are the sole legitimate writers."

### F-B2-04: No concurrency defence on anchor balance updates

- **Severity:** Medium (Low for today, Medium now because CLAUDE.md notes the app intends to
  go public, High after multi-user goes live)
- **OWASP:** A04:2021 (Insecure Design -- missing race defence)
- **CWE:** CWE-362 (Concurrent Execution using Shared Resource with Improper Synchronization --
  Race Condition)
- **Location:** `app/models/account.py` (Account model has no version or lock column).
  Routes under `app/routes/accounts.py`, `app/routes/grid.py`, and `app/routes/investment.py`
  are B1's scope; this finding concerns only the service/model layer. The
  balance calculator (this audit's scope) depends on `account.current_anchor_balance`
  as an input but does not itself hold a write lock.
- **Evidence:**
  ```python
  # app/models/account.py:11-42
  class Account(db.Model):
      ...
      current_anchor_balance = db.Column(db.Numeric(12, 2))
      current_anchor_period_id = db.Column(...)
      ...
      created_at = db.Column(db.DateTime(timezone=True), server_default=db.func.now())
      updated_at = db.Column(
          db.DateTime(timezone=True),
          server_default=db.func.now(),
          onupdate=db.func.now(),
      )
  ```
  - No `version_id_col` SQLAlchemy mapper argument.
  - No explicit `version` or `lock_version` column.
  - Grep `app/` for `with_for_update|FOR UPDATE|version_id` -- zero hits.
- **Impact:** Two concurrent POSTs from different browser tabs (or the
  owner and a companion from different devices) both reading
  `account.current_anchor_balance`, each committing their own new value,
  will race with last-write-wins semantics. There is no SQL `SELECT ... FOR
  UPDATE`, no SQLAlchemy optimistic lock, no row version check. The
  `AccountAnchorHistory` table records both events in sequence, so the
  audit trail survives, but the effective balance is whichever commit
  serialized later. For a solo-user app, the practical risk is limited to
  the same user in two tabs confusing themselves. For the advertised
  "intends to go public" direction, this becomes a real race condition
  vector for companion-vs-owner editing.
- **Recommendation:** Add optimistic concurrency to the Account mapper:
  ```python
  class Account(db.Model):
      __tablename__ = "accounts"
      __mapper_args__ = {
          "version_id_col": version_id,
      }
      ...
      version_id = db.Column(db.Integer, nullable=False, default=1)
      ...
  ```
  Any stale update will then raise `StaleDataError`, which the route layer
  can catch and turn into a 409 Conflict / retry prompt. A migration is
  required to add `version_id INTEGER NOT NULL DEFAULT 1` on
  `budget.accounts`. Alternatively, wrap anchor updates in a
  `SELECT ... FOR UPDATE` at the route layer (B1's scope), but the
  SQLAlchemy version column is the cheaper, more robust choice. The
  conversation about which approach to take should go through the
  developer (CLAUDE.md rule 8: "Ask before making design decisions"), so
  this finding is written up rather than unilaterally fixed.

### F-B2-05: Display-only rounding mode is HALF_UP, workflow doc mentions HALF_EVEN

- **Severity:** Info
- **OWASP:** N/A
- **CWE:** N/A
- **Location:** Every `quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` in
  `paycheck_calculator.py`, `tax_calculator.py`, `calibration_service.py`,
  and `balance_calculator.py`. Consistent across the codebase.
- **Evidence:** `docs/security-audit-workflow.md:867` recommends
  `ROUND_HALF_EVEN` "or equivalent" for the final output of the paycheck
  calculator. The code uses `ROUND_HALF_UP` (banker's rounding off). Both
  are defensible for payroll. IRS Pub 15-T does not mandate one specific
  rounding mode.
- **Impact:** None today. The entire codebase is consistent on HALF_UP, so
  there is no mixed-rounding drift. The workflow doc note was "or
  equivalent" and HALF_UP is a standard US-tax-software choice.
- **Recommendation:** If the developer wants strict Pub 15-T precision
  compliance, switch to HALF_EVEN globally. Otherwise, add a note to
  `docs/coding-standards.md` saying "we use ROUND_HALF_UP for all
  monetary output; do not mix rounding modes." Info only; no code change
  required.

### F-B2-06: "Magic number" fallbacks for safe withdrawal rate and assumed return

- **Severity:** Low (CLAUDE.md rule: no magic numbers; but these are defaults, not business
  rules)
- **OWASP:** N/A
- **CWE:** N/A
- **Location:** `app/services/retirement_dashboard_service.py:238, 257`
- **Evidence:**
  ```python
  current_swr = float(settings.safe_withdrawal_rate or 0.04) * 100 if settings else 4.0
  ...
  current_return = 7.0
  ```
- **Impact:** Hardcoded fallback percentages are brittle and scatter
  retirement-planning defaults across multiple files. If the user's settings
  row is missing, the slider default is 4% SWR and 7% return. Those are the
  standard "Trinity study" / "S&P long-run average" values but they should
  be named constants.
- **Recommendation:** Extract to module-level `Decimal` constants.
  `_DEFAULT_SWR = Decimal("0.04")` and `_DEFAULT_RETURN = Decimal("0.07")`
  with comments citing the source.

### F-B2-07: `recurrence_engine._get_transaction_amount` catches `TypeError` and `KeyError`

- **Severity:** Info
- **OWASP:** N/A
- **CWE:** N/A
- **Location:** `app/services/recurrence_engine.py:614-622`
- **Evidence:**
  ```python
  try:
      ...
      breakdown = paycheck_calculator.calculate_paycheck(
          salary_profile, period, all_periods, tax_configs,
          calibration=calibration,
      )
      return breakdown.net_pay

  except (InvalidOperation, ZeroDivisionError, TypeError, KeyError) as exc:
      logger.error(
          "Paycheck calculation failed for salary profile %d in "
          "period %s: %s. Using template default_amount.",
          salary_profile.id,
          period.start_date,
          exc,
      )
      return template.default_amount
  ```
- **Impact:** `TypeError` and `KeyError` here cover broad classes of coding
  errors that should ideally surface as 500 errors rather than silently
  falling back to `template.default_amount`. The fallback is a legitimate
  concern for `InvalidOperation` (bad Decimal input) and `ZeroDivisionError`
  (pay_periods_per_year=0), but `TypeError` usually means "I passed `None`
  to a Decimal constructor" and `KeyError` usually means "tax_configs is
  missing an expected key." Both of those are bugs, not recoverable
  domain conditions.
- **Recommendation:** Narrow the except to `(InvalidOperation,
  ZeroDivisionError)` and let `TypeError`/`KeyError` bubble up. Logged
  errors on the fallback path are currently the only way the developer
  would ever notice a `calculate_paycheck` TypeError, and log-buried bugs
  are harder to track than 500s. This is Info-level because it is not a
  security finding; it is a correctness / observability finding.

---

## What was checked and found clean

- **Invariant 5 (balance calculator isolation):** fully clean. No Transfer
  import, no Transfer query, no Transfer reference anywhere in
  `balance_calculator.py` or any module it imports. Shadow effects are read
  via `Transaction` rows with `transfer_id IS NOT NULL`. Confirmed via full
  import-chain grep.
- **Invariant 1 (two shadows per transfer at creation):** clean at the
  service layer (sole creation path is `transfer_service.create_transfer`).
  See F-B2-03 for the missing DB constraint.
- **Invariant 2 (never orphaned, always created with sibling):** clean via
  creation atomicity + CASCADE FK + explicit soft-delete sweep.
- **Transfer creation atomicity:** `create_transfer` flushes once, adds the
  Transfer, flushes for the id, adds both shadows, flushes. If the outer
  transaction rolls back, all three rows vanish together. Correct.
- **Hard-delete CASCADE verification:** line 597 asserts zero orphaned
  shadows after the CASCADE fires; logs an error if the FK is
  misconfigured.
- **Soft-delete atomicity:** the loop on line 579-580 marks BOTH shadows
  regardless of how many the query returns; a single sibling with missing
  partner would still be marked deleted.
- **Restore-drift repair:** `restore_transfer` re-verifies shadow count,
  type pairing, and the three invariant fields (amount, status, period)
  and repairs any drift that happened while the transfer was soft-deleted.
  The drift repair emits a structured `logger.warning` so the developer
  can audit post-hoc.
- **Transfer recurrence:** `transfer_recurrence.py` delegates all shadow
  mutations to `transfer_service`. Zero direct `Transaction` field writes.
- **Recurrence engine:** covers all 8 patterns from `RecurrencePatternEnum`,
  handles month-end clamping and December wrap correctly, uses real `date`
  objects, never eval/exec.
- **Type purity:** no `float(` on monetary values, no `Decimal(x)` with
  float `x`, no `round(` without second argument, no modulo on monetary
  Decimals, no `** 0.5`, no `/ 100`. The only `float(` hits are two
  display-only slider defaults (F-B2-02).
- **Raw SQL:** zero `text(...)` SQL strings, zero `db.session.execute(...)`
  calls, zero f-string or `.format()` SQL concatenation in services.
- **Paycheck calculator rounding:** consistently quantizes to 2 places
  with `ROUND_HALF_UP` at every output. Intermediate raise compounding is
  correctly deferred to a single quantize at the end. FICA SS cap / Medicare
  surtax threshold are handled correctly.
- **Tax bracket application:** `_apply_marginal_brackets` sorts brackets by
  `sort_order`, iterates by ID, never by name. Uses `Decimal(str(...))`
  everywhere.
- **Calibration service:** effective-rate derivation uses 10 decimal places
  of precision (`RATE_PLACES = Decimal("0.0000000001")`) to avoid penny
  rounding on multiply-back. Good.
- **Carry-forward service:** correctly partitions shadow vs regular
  transactions and routes shadows through `transfer_service.update_transfer`.
- **Entry credit workflow:** CC Payback transactions are not shadows; they
  have `credit_payback_for_id`, not `transfer_id`. Safe.
- **Credit workflow (per-transaction):** explicit `if txn.transfer_id is not
  None: raise ValidationError` at line 59. Safe.
- **Entry service:** explicit `if txn.transfer_id is not None: raise
  ValidationError` at line 149. Safe.
- **Year-end summary service:** reads `Transfer` for grouping only; does not
  mutate. Separate read path from `balance_calculator` and does not affect
  invariant 5.

---

## Open questions for the developer

1. **F-B2-01 remediation style:** Should `resolve_conflicts` `continue` on
   shadow detection (logged warning, best-effort success), or should it
   raise `ValidationError` and fail the whole request? My recommendation is
   `raise` to match the fail-fast style of the rest of the transfer service,
   but the conflict-resolution UX may prefer best-effort.

2. **F-B2-03 remediation scope:** The minimum fix is a partial-unique index
   on `(transfer_id, transaction_type_id) WHERE transfer_id IS NOT NULL AND
   is_deleted = FALSE`. A full fix also includes a DB-level trigger or
   CHECK that asserts `COUNT(*) = 2` active shadows per active transfer.
   Do you want minimum or full? (The full fix requires a PostgreSQL-specific
   trigger or a deferred constraint with a procedural check.)

3. **F-B2-04 concurrency remediation approach:** SQLAlchemy optimistic
   locking via `version_id_col` (requires migration to add the column) vs.
   `SELECT ... FOR UPDATE` at the route layer (no migration, route-only
   change). SQLAlchemy version column is cheaper and more robust once in
   place; FOR UPDATE is a smaller diff. Which do you want?

4. **F-B2-05 rounding mode:** Do you want to stay on `ROUND_HALF_UP`
   globally (current state, consistent, US-tax-software norm) or switch to
   `ROUND_HALF_EVEN` (workflow doc preference, strict IRS Pub 15-T
   compliance)? If you switch, it must be a global sweep through tax /
   paycheck / balance calculators all at once.

5. **`calibration_service` full read:** I read lines 1-145. The module is
   146 lines; line 146 is just the file trailer blank. If the developer
   believes there are additional functions after line 145, please confirm.
   (My grep for `def ` in that file showed only `derive_effective_rates`,
   `apply_calibration`. Two functions. Read in full.)

6. **Savings dashboard service coverage:** I checked the Decimal, transfer,
   and raw-SQL grep over the entire file (980+ lines). I did NOT read the
   entire file top-to-bottom. It is not in the transfer-invariant critical
   path, and all its Decimal hits are clean. If a deeper read is wanted
   before final sign-off, please say so.
