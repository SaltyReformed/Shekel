# Bug Investigation: Transfers to Debt Accounts Do Not Affect Amortization Schedule

**Date:** 2026-04-10
**Status:** Root cause identified, fix not yet implemented

## Root Cause

`get_loan_projection()` in `app/services/amortization_engine.py` (lines 789--866) generates
the amortization schedule starting from today's date instead of the loan's origination date,
making all past confirmed payments invisible. Two specific defects:

1. **Missing `origination_date`:** Line 852 calls `generate_schedule()` without passing
   `origination_date`. This causes the schedule loop to start from `date.today()` (line 448),
   so payment records from past months are present in the `amount_by_month` lookup dict but
   never matched by a `month_key` in the loop. Only current-month or future payments can
   match.

2. **Static starting balance:** Line 828 uses `params.current_principal` -- a database field
   set during loan setup and never automatically updated by payment activity -- instead of
   `params.original_principal`. The schedule begins from a stale balance that does not reflect
   any confirmed payments.

The year-end service (`app/services/year_end_summary_service.py`, lines 1291--1301) calls
the same engine correctly: it passes `params.original_principal` as the starting balance,
`params.origination_date` as the origination date, and `params.term_months` as the term
length. This allows the engine to replay ALL payments from origination. The loan dashboard
does not follow this pattern.

---

## Code Path Trace

### 1. Payment discovery -- `app/services/loan_payment_service.py:get_payment_history()` (lines 45--119)

Queries shadow income transactions on the debt account:

- `Transaction.transfer_id.isnot(None)` -- shadow transactions only
- `Transaction.transaction_type_id == INCOME` -- income side of the transfer
- `Transaction.is_deleted.is_(False)` -- excludes soft-deleted
- `Status.excludes_from_balance.is_(False)` -- excludes Cancelled/Credit

Sets `is_confirmed=txn.status.is_settled` (True for Paid/Settled/Received, False for
Projected). Uses `txn.effective_amount` for the amount and `txn.pay_period.start_date` as
the payment date.

**Verdict: Works correctly.** Confirmed payments are discovered and converted to
`PaymentRecord` instances.

### 2. Payment preparation -- `app/services/loan_payment_service.py:prepare_payments_for_engine()` (lines 152--242)

Two correction steps before the engine receives payment data:

1. **Escrow subtraction** (lines 194--208): Removes escrow from payments exceeding the
   contractual P&I amount. Ensures the engine sees only the P&I portion.
2. **Biweekly redistribution** (lines 210--240): Shifts same-month payments to consecutive
   months to restore one-payment-per-month alignment.

Preserves `is_confirmed` flag throughout both steps.

**Verdict: Works correctly.**

### 3. Loan context loading -- `app/routes/loan.py:_load_loan_context()` (lines 329--415)

Loads escrow components, calls `get_payment_history()`, calls `prepare_payments_for_engine()`.
Returns a dict with prepared payments, rate changes, escrow data, and derived values.

**Verdict: Works correctly.** Payments are loaded and prepared.

### 4. Dashboard entry -- `app/routes/loan.py:dashboard()` (lines 433--589)

- Line 447: Calls `_load_loan_context()` to get payments
- Line 455: Calls `get_loan_projection(params, payments=payments, rate_changes=rate_changes)`
- Lines 477--482: Generates `original_schedule` (no payments, no origination_date)
- Lines 497--504: Generates `floor_schedule` (confirmed payments only, no origination_date)
- Line 561: Passes `proj.schedule` to the template as `amortization_schedule`

**Verdict: Payments are correctly passed to `get_loan_projection`. The bug is inside that
function.** The `original_schedule` and `floor_schedule` also omit `origination_date` and
use `params.current_principal`.

### 5. Projection generation -- `app/services/amortization_engine.py:get_loan_projection()` (lines 789--866)

**THE BUG IS HERE.**

- Line 822: `schedule_start = date.today().replace(day=1)` -- today, not origination
- Line 828: `principal = Decimal(str(params.current_principal))` -- static DB value
- Line 840--850: `calculate_summary(..., origination_date=schedule_start)` -- passes today
  as origination to the summary computation
- Lines 852--858: `generate_schedule(principal, rate, remaining, ...)` -- does NOT pass
  `origination_date`, so the engine defaults to `date.today()` (line 448)

The `remaining` variable (line 824--826) is computed from `params.origination_date` and
`params.term_months` via `calculate_remaining_months()` -- this correctly yields the number
of months remaining from today. But the schedule starts from today and only iterates
`remaining` months forward. Past months are never visited, so past payment records are
never matched.

### 6. Schedule generation -- `app/services/amortization_engine.py:generate_schedule()` (lines 326--588)

- Lines 390--398: `_build_payment_lookups()` includes ALL payments when `origination_date`
  is None (no pre-origination filtering).
- Lines 441--450: Without `origination_date`, the schedule starts from
  `(date.today().year, date.today().month)`.
- Lines 488--497: For each month, checks `month_key = (pay_year, pay_month)` against
  `amount_by_month`. Only current/future months are generated, so past payment records in
  the lookup dict are never matched.
- Lines 567--577: Correctly assigns `is_confirmed=row_confirmed` to each `AmortizationRow`.

**Verdict: The engine is correct.** It faithfully uses whatever starting point and payments
it receives. The problem is the inputs from `get_loan_projection()`.

### 7. Status propagation -- `app/services/transfer_service.py:update_transfer()` (lines 469--473)

When a transfer status changes:

```python
if "status_id" in kwargs:
    new_status_id = kwargs["status_id"]
    xfer.status_id = new_status_id
    expense_shadow.status_id = new_status_id
    income_shadow.status_id = new_status_id
```

`mark_done()` in `app/routes/transactions.py` (lines 232--260) routes shadow status changes
through `transfer_service.update_transfer()`. Both shadows receive the new status atomically.

**Verdict: Status propagation is correct.** Transfer invariant #4 is maintained.

### 8. Template -- `app/templates/loan/_schedule.html` (lines 52, 80--86)

- Line 52: `{% if row.is_confirmed %}table-success{% endif %}` -- green row highlighting
- Lines 81--85: Renders `<span class="badge bg-success">Confirmed</span>` or
  `<span class="badge bg-secondary">Projected</span>` based on `row.is_confirmed`

**Verdict: Template is correct.** It reads `is_confirmed` and renders appropriately.

### 9. Correct reference implementation -- `app/services/year_end_summary_service.py:_generate_debt_schedules()` (lines 1291--1301)

```python
schedule = amortization_engine.generate_schedule(
    current_principal=params.original_principal,   # start from the beginning
    annual_rate=params.interest_rate,
    remaining_months=params.term_months,            # full term
    origination_date=params.origination_date,       # actual origination
    payment_day=params.payment_day,
    original_principal=original_for_engine,
    term_months=params.term_months,
    payments=payments if payments else None,
    rate_changes=rate_changes,
)
```

This generates the full life-of-loan schedule. All confirmed and projected payments are
matched by month. The engine correctly computes the balance at every point.

---

## What Is Missing or Broken

### Primary: `get_loan_projection()` (amortization_engine.py:852--858)

The `generate_schedule()` call omits `origination_date`, so the schedule starts from today.
Past confirmed payments are in `amount_by_month` but the loop never generates their month
keys. Uses `params.current_principal` (static) instead of `params.original_principal`.

### Secondary: `dashboard()` original/floor schedules (loan.py:477--504)

Both `original_schedule` (line 477) and `floor_schedule` (line 497) also omit
`origination_date` and use `principal` (= `params.current_principal`). These chart projections
are similarly static.

### Secondary: Savings dashboard (savings_dashboard_service.py:348)

Calls `get_loan_projection(acct_loan_params)` WITHOUT passing payments. Debt account
projections on the savings overview are purely static -- no payment data at all.

### Minor: `_compute_real_principal()` in debt_strategy.py (lines 173--182)

Uses `current_principal` (not `original_principal`) and `remaining` (not `term_months`) as
starting point. It does pass `origination_date=params.origination_date`, so past payments
CAN match, but the starting balance may be incorrect if `current_principal` differs from the
actual balance at origination.

### Minor: Refinance calculator (loan.py:1012--1014)

Inherits all `get_loan_projection()` issues. The `current_real_principal` derivation (lines
1031--1035) may find no confirmed rows because the schedule starts from today.

---

## Proposed Fix Approach

### Primary fix: Modify `get_loan_projection()` to match the year-end service pattern

Change the function to start from origination and replay the full payment history:

- Use `params.original_principal` instead of `params.current_principal` as the starting balance
- Pass `params.origination_date` as `origination_date` to both `generate_schedule()` and
  `calculate_summary()`
- Use `params.term_months` instead of the computed `remaining` as the schedule length

This allows the engine to start from origination, iterate through every month of the loan
term, match all confirmed and projected payments, and compute correct balances.

### Secondary fixes

1. **`dashboard()` original/floor schedules** (loan.py:477--504): Apply the same parameter
   changes (origination_date, original_principal, term_months).
2. **`savings_dashboard_service.py`** (line 348): Load payment history and pass it to
   `get_loan_projection()`.
3. **`debt_strategy.py:_compute_real_principal()`** (lines 173--182): Use `original_principal`
   and `term_months` instead of `current_principal` and `remaining`.

### Display consideration

The full schedule from origination could include hundreds of past rows. The template or route
may need to filter displayed rows -- for example, showing from 12 months before today forward,
or collapsing past confirmed months into a summary. The engine must generate from origination
for correct balance computation, but the display can be trimmed.

---

## Files That Would Need to Change

1. `app/services/amortization_engine.py` -- `get_loan_projection()`: origination_date,
   original_principal, term_months
2. `app/routes/loan.py` -- `dashboard()`: original_schedule and floor_schedule generation
3. `app/services/savings_dashboard_service.py` -- Pass payments to `get_loan_projection()`
4. `app/routes/debt_strategy.py` -- `_compute_real_principal()`: use original_principal and
   term_months
5. `app/templates/loan/_schedule.html` -- Possibly add row filtering for display

---

## Tests That Should Verify the Fix

1. **Unit: `generate_schedule()` with origination in the past and confirmed payments** --
   verify past-month payments produce `is_confirmed=True` rows with correct
   remaining_balance.
2. **Unit: `get_loan_projection()` with confirmed payments** -- verify schedule rows reflect
   actual payment amounts and confirmed status.
3. **Unit: `get_loan_projection()` balance accuracy** -- verify the balance after the last
   confirmed row equals the expected principal from replaying actual payments.
4. **Integration: Loan dashboard with transfer marked as Paid** -- verify the schedule table
   shows "Confirmed" badge and green highlighting for the paid month.
5. **Integration: Savings dashboard debt projections** -- verify projected balances
   incorporate payment data.
6. **Regression: Year-end schedule consistency** -- verify loan dashboard and year-end
   service produce identical schedules for the same account.

---

## Cross-Cutting Note: Other Account Types

**Savings and interest-bearing accounts are NOT affected by this root cause.** They use
`balance_calculator.calculate_balances()` or `calculate_balances_with_interest()`, which
process ALL transactions (including shadow transactions from transfers) period-by-period
from the anchor balance. Transfer status changes are correctly reflected via
`effective_amount`.

**Investment/retirement accounts are NOT affected.** They use the growth engine with
contribution data derived from shadow transactions and the balance calculator's output,
both of which correctly incorporate transfer shadows.

**Debt accounts on the savings dashboard ARE affected** -- `savings_dashboard_service.py:348`
calls `get_loan_projection()` without payments, making debt projections on the savings
overview completely static. This is the same root cause (missing payment data in
`get_loan_projection`), not a distinct issue.
