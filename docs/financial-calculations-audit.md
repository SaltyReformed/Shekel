# Financial Calculations Audit

**Date:** 2026-05-05
**Status:** Read-only audit. No code modified.
**Scope:** Every code path in `app/` that computes a "balance," "current
principal," "monthly payment," "transfer impact," "interest," or any
derived monetary value visible to the user.

---

## 1. Executive Summary

Shekel has **at least 6 independent implementations of "checking balance"
or its near-equivalents** (anchor only, anchor + balance_calculator,
anchor + balance_calculator with entries selectinload'd, anchor +
balance_calculator with interest, balance_calculator using the
`_entry_aware_amount` formula vs. raw `effective_amount`, and the static
anchor on the archived/list pages). The most damaging divergence is
between (a) routes that **selectinload Transaction.entries** before
calling `balance_calculator.calculate_balances`, and (b) routes that do
not. The same calculator returns different numbers for the same period
depending solely on whether entries were eager-loaded -- because
`_entry_aware_amount` falls back to `effective_amount` when entries are
absent from `__dict__`. This is the root cause of the $160 vs. $114.29
symptom on checking.

For loans, the **schedule's per-row "Payment" column is computed in the
template** as `row.payment + monthly_escrow + extra_payment` where
`row.payment = principal_portion + interest`. For confirmed payment
rows, `principal_portion` is derived from the actual transferred amount
(after escrow subtraction and biweekly redistribution), so any variation
in transferred amounts shows up as a "fluctuating mortgage payment" in
the Payment column. The displayed dashboard "Monthly Payment" is the
contractual P&I from `summary.monthly_payment`, a different quantity
entirely. This is the root cause of the $1,911.54 / $1,914.34 / $1,912.94
symptom.

The `/accounts` list shows `account.current_anchor_balance` (raw
database column) labelled as "Current Balance," but every other surface
in the app shows a *projected* current-period balance from
`balance_calculator`. They will diverge whenever there are projected
items in the current pay period that are not yet reflected in the
anchor. This is the root cause of the `/accounts` mismatch with
elsewhere.

Distinct implementations counted per major metric (detailed in §2):

- **Checking / liquid-asset current balance:** 6
- **Account "current balance" for any account:** 7
- **Loan current principal:** 5
- **Loan monthly payment (contractual P&I):** 5
- **Loan amortization schedule generation:** 8 separate call sites
- **Transfer impact on source/destination balance:** 1 conceptual path
  (shadow transactions through balance_calculator) but with 6+
  divergent consumers that load it differently
- **Pay-period rollforward of balances:** 1 (the calculator), but with
  3 different load-side patterns (entries-loaded, entries-unloaded,
  with-interest)

---

## 2. Metric Inventory

### 2.1 Checking / Primary Liquid-Asset "Current Balance"

This is the metric the user sees as the headline number on the
dashboard, the grid, and the savings page. Each row shows a different
function or call site that produces a number described as "current
balance" or "checking balance."

| # | Implementation | Inputs | Status filter | Transfer handling | Anchor handling | Where consumed | Notes / divergence |
|---|---|---|---|---|---|---|---|
| 1 | `app/services/balance_calculator.py:calculate_balances` (35) | anchor_balance, anchor_period_id, periods, transactions list (passed in by caller) | Anchor period: only PROJECTED items contribute (via `_sum_remaining`). Post-anchor: only PROJECTED items contribute (via `_sum_all`). Done/Received/Settled/Credit/Cancelled all skipped. | Shadow transactions are normal Transaction rows with `transfer_id IS NOT NULL`; included in `transactions` arg by every caller. Calculator NEVER queries `budget.transfers` (Invariant 5 OK). | Starts from `anchor_balance` at `anchor_period_id`. If anchor not in periods list, returns empty. Pre-anchor periods produce no balance. | Grid (`grid.py:246`), grid balance row HTMX (`grid.py:446`), dashboard (`dashboard_service.py:707`), savings dashboard (`savings_dashboard_service.py:347`), checking detail (`accounts.py:966`), interest detail (`accounts.py:827` via `_with_interest`), investment dashboard (`investment.py:112`). | This is the canonical pure function. Differences arise from how callers prepare the `transactions` argument. |
| 2 | `_entry_aware_amount` (`balance_calculator.py:292`) -- inner helper | A single Transaction; checks `__dict__['entries']` | Only applied to PROJECTED expenses with eager-loaded entries. Cleared debit, uncleared debit, and credit entries partition `estimated_amount`. | Doesn't distinguish shadow vs. regular -- but shadows have no entries, so always falls back to `effective_amount`. | N/A (per-txn). | Indirectly via `_sum_remaining` and `_sum_all`. | CRITICAL: Returns different numbers for the same Transaction depending on whether `txn.entries` was selectinload'd before the calculator was called. Grid loads them; savings_dashboard does not. See §3.1. |
| 3 | `Transaction.effective_amount` property (`models/transaction.py:140`) | A Transaction's `is_deleted`, `status.excludes_from_balance`, `actual_amount`, `estimated_amount` | Soft-deleted -> 0. Cancelled/Credit -> 0. Otherwise actual_amount if `is not None`, else estimated_amount. | Treated like any other transaction. | N/A. | Used inside `_sum_remaining`/`_sum_all` (when entries unloaded), grid subtotal computation (`grid.py:275`), savings dashboard for various sums, dashboard_service spending comparison, and many templates indirectly (e.g. dashboard `txn_to_bill_dict`). | Subtle: includes `actual_amount` for *projected* transactions where the user has filled in actual but not yet marked done. The balance calculator's `_sum_remaining` calls effective_amount on PROJECTED items, so this branch DOES fire. Differs from the description in `balance_calculator.py:8-13` which says "actual if populated, else estimated" -- consistent here, but easy to misread. |
| 4 | `account.current_anchor_balance` raw column (`models/account.py:31`) | The DB column itself. | None -- raw value. | None. | This IS the anchor. | Accounts list `/accounts` (`templates/accounts/list.html:110`, `accounts/_anchor_cell.html:38`), archived accounts on savings page (`templates/savings/dashboard.html:276`), inline anchor editor, dashboard_service fallback when no balances (`dashboard_service.py:349,352`). | Differs from all projected balances by the magnitude of (income to date - expenses to date in current period). This is the source of /accounts divergence (§4.3). |
| 5 | `_get_balance_info` (`dashboard_service.py:333`) | account, current_period, balance_results dict | Uses balance_results from `_compute_balances` (entries are NOT loaded in `_compute_balances` line 696-705). | Inherited from balance_calculator. | Falls back to `account.current_anchor_balance or 0` if no balances. | Dashboard summary card. | Same as #1 but with no entry awareness; matches the savings dashboard treatment (#1 with entries unloaded). |
| 6 | `_compute_balances` in dashboard_service (`dashboard_service.py:681`) | account, periods, scenario | balance_calculator behavior, entries SELECTINLOAD'd here (line 697). | Standard. | Standard. | The dashboard summary card and alerts. | NOTE: Dashboard DOES selectinload entries (line 697), making #6's results agree with the grid. Savings dashboard does NOT (see §3.1). |

### 2.2 Account Balances by Account Type (Beyond Checking)

| # | Implementation | Inputs | Status filter | Transfer handling | Anchor handling | Where consumed | Notes / divergence |
|---|---|---|---|---|---|---|---|
| 1 | `balance_calculator.calculate_balances_with_interest` (`balance_calculator.py:112`) | anchor, periods, transactions, interest_params | Same as `calculate_balances`. Layers per-period interest from `interest_projection.calculate_interest` on top. | Same. | Same. | Interest detail page (`accounts.py:827`), savings dashboard for interest-bearing accounts (`savings_dashboard_service.py:339`). | Calls `calculate_balances` first, then re-walks adding interest. |
| 2 | `balance_calculator.calculate_balances_with_amortization` (`balance_calculator.py:176`) | anchor, periods, transactions, account_id, loan_params | Same projection rules; payments derived from shadow income transactions where `txn.is_income and transfer_id is not None`. | Detects payments from shadow income, reduces principal by computed principal portion. | Starts running_principal from anchor at anchor period. | **NOT CALLED ANYWHERE**. Grep `calculate_balances_with_amortization` finds zero call sites in routes/services other than its own definition and tests. The savings dashboard uses `get_loan_projection` (#3) instead. Dead-or-near-dead code. | An ENTIRELY THIRD loan-balance pathway that conflicts with `get_loan_projection`. Out-of-scope finding (§6). |
| 3 | `amortization_engine.get_loan_projection().current_balance` (`amortization_engine.py:864`) | LoanParams, payments, rate_changes | ARM: returns `current_principal` (anchor). Fixed-rate: walks schedule backward to last `is_confirmed` row, returns `remaining_balance`. Falls back to `current_principal`. | Payments fed in from `loan_payment_service.get_payment_history`. | ARM uses `anchor_balance=current_principal, anchor_date=today`. Fixed: starts from `original_principal` at origination, replays payments. | Loan dashboard `/accounts/<id>/loan` (`loan.py:426`), payoff calc target_date mode (`loan.py:953`), refinance calc (`loan.py:1027`), savings dashboard `_compute_account_projections` (`savings_dashboard_service.py:366`). | The "real" current balance for loan accounts. |
| 4 | `account.current_anchor_balance` raw column | DB column. | None. | None. | This IS the anchor. | Archived accounts on `/accounts` and `/savings`, anchor editor cells. | For loan accounts, this is `current_principal` set at LoanParams creation/edit but stored separately on the Account row -- NOTE: not on Account, on `LoanParams.current_principal`. The Account.current_anchor_balance is rarely set for loans. |
| 5 | `LoanParams.current_principal` (`models/loan_params.py:54`) | DB column. | None. | None. | This IS the loan's "anchor". | Loan setup form, loan params edit form, ARM `get_loan_projection` anchor source, debt strategy `_compute_real_principal` (ARM branch returns this directly). | For ARM loans, all forward projections are pinned to this value. For fixed-rate, this value is treated as a stale fallback and the actual balance is derived from the schedule walk in `current_balance`. This split is the source of inconsistency #1 below. |
| 6 | `growth_engine.project_balance` (`growth_engine.py:164`) | current_balance, return rate, periods, contributions | Per-period growth on prior balance, then add contribution. ContributionRecord.is_confirmed used for status display only. Cancelled/credit shadow contributions are filtered upstream by `excludes_from_balance.is_(False)` in queries. | Contributions sourced from shadow income transactions on the investment account (`build_contribution_timeline`). | Starts from `current_balance` arg, which itself comes from `balance_calculator.calculate_balances` for the investment account. | Investment detail (`investment.py:214`), savings dashboard for investment/retirement (`savings_dashboard_service.py:549`), retirement dashboard. | Stacks on top of #1. Calls `calculate_balances` to seed `current_balance`, then projects with growth. |
| 7 | `_compute_real_principal` (`debt_strategy.py:145`) | LoanParams, scenario_id, principal, rate | For ARM: returns the passed-in `principal` (= `LoanParams.current_principal`). For fixed: replays from origination using `generate_schedule(payments=...)` and walks back to last `is_confirmed` row. | Confirmed-only payments from `get_payment_history`. | None for ARM. Origination-forward for fixed. | Debt strategy dashboard. | Almost identical to #3's fallback path but reimplemented inline. Differs from #3 in that it replays only "confirmed" payments rather than all payments, and that it does not load escrow components or rate_changes. This is its own copy. |

### 2.3 Mortgage and Other Loan Calculations

#### 2.3.1 "Current Principal" of a Loan

| # | Implementation | Method | Where consumed |
|---|---|---|---|
| 1 | `LoanParams.current_principal` raw | DB column, user-edited | Setup form, params edit form, payment-creation transfer amount (`loan.py:1183`), `compute_contractual_pi` (ARM branch), refinance form default, ARM `get_loan_projection` anchor, ARM `_compute_real_principal` |
| 2 | `get_loan_projection().current_balance` for ARM | Returns `current_principal` directly | Loan dashboard "current balance" displays, savings dashboard "current_balance" for the loan card |
| 3 | `get_loan_projection().current_balance` for fixed-rate | Walks schedule reverse, finds last `is_confirmed` row, returns its `remaining_balance` | Same consumers as #2 |
| 4 | `_compute_real_principal` (`debt_strategy.py:145`) | Reimplements #3 inline (fixed) or returns input (ARM) | Debt strategy dashboard |
| 5 | `_check_loan_paid_off` (`savings_dashboard_service.py:445`) | Independently regenerates schedule from origination with confirmed payments only, walks confirmed rows, checks last == 0 | Savings dashboard `is_paid_off` flag |

#### 2.3.2 "Monthly Payment" (Contractual P&I)

| # | Implementation | Method | Where consumed |
|---|---|---|---|
| 1 | `compute_contractual_pi` (`loan_payment_service.py:233`) | ARM: `calculate_monthly_payment(current_principal, rate, remaining)`. Fixed: `calculate_monthly_payment(original_principal, rate, term_months)`. | Used by `prepare_payments_for_engine` for the escrow-cap, and by `loan.py:970` payoff target_date display. |
| 2 | `get_loan_projection().summary.monthly_payment` | Same logic as #1 (ARM vs fixed branch in `get_loan_projection`, lines 950-959). | Loan dashboard headline (`templates/loan/dashboard.html:59,129`), refinance comparison "current_monthly". |
| 3 | `calculate_summary().monthly_payment` (engine `:692-699`) | If `original_principal is not None` and no rate_changes: uses original_principal/term. Else: uses current_principal/remaining. | Payoff calculator extra_payment mode (`loan.py:858`). |
| 4 | Inline logic in `loan.py:create_payment_transfer` (`loan.py:1178-1192`) | Reimplements ARM/fixed branch yet again to default the recurring transfer amount. | Recurring payment-transfer creation. |
| 5 | Inline logic in `balance_calculator.calculate_balances_with_amortization` (`balance_calculator.py:217-235`) | Same ARM/fixed branch, again. | (Function is unused; see §2.2 #2.) |

These five all *should* return the same value but differ in subtle ways:

- #1, #2 use `calculate_remaining_months(origination_date, term_months)`
  via `as_of=date.today()`. They depend on the system clock.
- #3 (calculate_summary) takes `remaining_months` as a parameter; the
  caller decides what to pass.
- #4 (create_payment_transfer) uses
  `calculate_remaining_months(origination_date, term_months)` only in
  the ARM branch.
- #5 uses `calculate_remaining_months` -- but the function is dead.

If `term_months` and `original_principal` are stable, fixed-rate loans
agree across all five. For ARM loans, all five depend on
`current_principal` -- so changing it via the loan params edit page
re-derives a new monthly payment everywhere except in the *committed*
schedule where individual rows reflect actual transferred amounts.
**This is part of the $1,911.54 / $1,914.34 fluctuation symptom.**

#### 2.3.3 Amortization Schedule Generation (`generate_schedule` Call Sites)

| # | Caller | Args worth noting |
|---|---|---|
| 1 | `get_loan_projection` (engine `:932`) | orig_principal, rate, term_months, payments, rate_changes, ARM anchor. Used by 4 routes. |
| 2 | `calculate_summary` standard (engine `:702`) | extra_monthly=0; passes through original_principal, payments, rate_changes, anchor. |
| 3 | `calculate_summary` accelerated (engine `:720`) | extra_monthly=user value; same passthroughs. |
| 4 | `calculate_payoff_by_date` standard (engine `:779`) | NO payments, NO anchor; binary search uses `:824`. |
| 5 | `calculate_payoff_by_date` mid-search (engine `:824`) | Standard contractual schedule with extra_monthly trial value; NO payments. |
| 6 | `_check_loan_paid_off` (`savings_dashboard_service.py:492`) | Confirmed-only payments, NO rate_changes, NO anchor. |
| 7 | `_compute_real_principal` (`debt_strategy.py:179`) | Confirmed-only payments, NO rate_changes, NO anchor (fixed-rate only). |
| 8 | `_generate_debt_schedules` (`year_end_summary_service.py:1470`) | All payments, rate_changes, ARM anchor. |
| 9 | `loan.py:dashboard` original_schedule (`loan.py:450`) | NO payments, NO rate_changes, NO anchor (intentional baseline). |
| 10 | `loan.py:dashboard` floor_schedule (`loan.py:477`) | Confirmed payments only, rate_changes, ARM anchor. |
| 11 | `loan.py:payoff_calculate` original (`loan.py:875`) | NO payments, NO rate_changes, NO anchor. |
| 12 | `loan.py:payoff_calculate` committed (`loan.py:883`) | All payments, rate_changes, ARM anchor. |
| 13 | `loan.py:payoff_calculate` accelerated (`loan.py:895`) | All payments + extra_monthly, rate_changes, ARM anchor. |
| 14 | `loan.py:refinance_calculate` refi schedule (`loan.py:1066`) | NEW principal/rate/term from form; **`origination_date=date.today().replace(day=1)`** (different from elsewhere). |

That's **14 call sites** of `generate_schedule` across the codebase
(plus the `calculate_summary` ones). Some pass payments, some don't;
some pass rate_changes, some don't; some pass an ARM anchor, some
don't. The behavior of `payoff_calculate` target_date mode at
`loan.py:963` even passes `origination_date=date.today().replace(day=1)`
to `calculate_payoff_by_date`, which is inconsistent with the rest of
the route and with the refinance calc. The combinations of
{payments, rate_changes, anchor, origination override} produce
materially different schedules.

#### 2.3.4 Escrow

| # | Implementation | Method |
|---|---|---|
| 1 | `escrow_calculator.calculate_monthly_escrow` (`escrow_calculator.py:14`) | Sums active component annual_amounts/12, optionally with inflation from created_at. |
| 2 | `escrow_calculator.calculate_total_payment` | Wrapper: `calculate_monthly_escrow(...) + monthly_pi`. |
| 3 | `escrow_calculator.project_annual_escrow` | Per-year projection with per-component inflation. |
| 4 | Inline subtraction inside `prepare_payments_for_engine` (`loan_payment_service.py:305-319`) | "If payment > contractual_pi, subtract min(monthly_escrow, excess)." This is consistent only when actual payments exceed P&I; if a user transfers exactly P&I (no escrow), escrow is silently NOT subtracted. |

### 2.4 Transfers Between Accounts

The transfer model itself has 1 service (`transfer_service.py`) that
maintains shadow invariants. Balance impact, however, is computed
indirectly through whatever balance-calculator path each route uses.

| # | Aspect | Implementation | Notes |
|---|---|---|---|
| 1 | Shadow creation | `transfer_service.create_transfer` (`:271`) | Creates Transfer + 2 Transactions (expense on from_account, income on to_account) atomically. |
| 2 | Shadow propagation on edit | `transfer_service.update_transfer` (`:421`) | Propagates amount/status/period/category/etc to both shadows. |
| 3 | Balance impact on source | Standard balance_calculator pulls in the expense shadow as a normal expense Transaction. | If account_id of source is filtered (e.g., grid scoped to checking), only that side is included. |
| 4 | Balance impact on destination | Standard balance_calculator pulls in the income shadow as a normal income Transaction. | The income shadow has `category_id` inherited from the transfer (e.g. "Car Insurance" if user picked that as the category). On the destination's grid the shadow shows up under that group, named "Transfer from <source>". This is the bug-investigation-#4 root cause. |
| 5 | Loan-payment detection | `loan_payment_service.get_payment_history` (`:156`) | Queries shadow income transactions on the loan account, filters out `excludes_from_balance` (Cancelled/Credit), uses `effective_amount`. Returns PaymentRecord list. |
| 6 | Investment-contribution detection | `investment_projection.build_contribution_timeline` (`:201`) -- and inline duplicates in `investment.py:165` and `savings_dashboard_service.py:107` | Shadow income on the investment account. Uses `effective_amount`, filters `excludes_from_balance`. NOTE: three near-duplicate query patterns. |
| 7 | Carry-forward of unpaid transfers | `carry_forward_service._build_transfer_plan` (`:703`) | Routes through `transfer_service.update_transfer` to maintain Invariant 4. |
| 8 | Recurrence-driven generation | `transfer_recurrence.generate_for_template` | Loops through periods, calls `create_transfer` per period. |

The transfer architecture itself is sound (per the `adversarial_audit.md`
section 2.12 confirmation of Invariants 1-5). The divergence is not in
the transfer service but in the consumers: `get_payment_history` and
`build_contribution_timeline` and inline shadow-income queries each have
slightly different filtering, projection, and conversion logic.

### 2.5 Pay Period Calculations

| # | Function | Purpose |
|---|---|---|
| 1 | `pay_period_service.generate_pay_periods` (`:19`) | Bulk creation of biweekly periods. |
| 2 | `pay_period_service.get_current_period` (`:87`) | Returns the period containing `as_of` (default today). Uses `start_date <= as_of <= end_date`. |
| 3 | `pay_period_service.get_periods_in_range` (`:111`) | Slice by period_index. |
| 4 | `pay_period_service.get_all_periods` (`:134`) | All periods for a user, ordered by period_index. |
| 5 | `pay_period_service.get_next_period` (`:151`) | Next period by index. |

Pay period code is NOT a divergence source. The balance-calculation
divergence comes from how each route fetches periods then loads (or
doesn't load) entries before calling the calculator.

The status workflow is: `projected -> done|credit|cancelled`,
`done|received -> settled`. The balance calculator only counts
`projected`. Once an item is `done`, the user is expected to true up
the anchor balance to absorb it. **This is the design** -- but the
"stale anchor warning" is the only safeguard, and as documented in
`balance_calculator.py:88-107` it is informational only. If a user
marks a transaction `done` but does not true-up the anchor, the
projected balance becomes wrong (under-counts the spend that already
hit the bank).

### 2.6 Paycheck Calculations

| # | Function | Purpose |
|---|---|---|
| 1 | `paycheck_calculator.calculate_paycheck` | Full breakdown: gross, deductions, taxes, net. |
| 2 | `paycheck_calculator.project_salary` | Multi-period projection for charts. |
| 3 | `_get_net_biweekly_pay` (`savings_dashboard_service.py:572`) | Wrapper: instantiates calculator, returns `breakdown.net_pay`. |
| 4 | `_get_net_pay_for_period` (`dashboard_service.py:451`) | Same wrapper, different file. |
| 5 | `recurrence_engine._get_transaction_amount` paycheck branch (`:543-549`) | Calls calculator; on ANY exception silently falls back to `template.default_amount`. (See `adversarial_audit.md` C-01 -- still present.) |

Paychecks feed into the calculator only via shadow income (when paid
via direct deposit transfer template) or via direct income transactions
created by the recurrence engine. The C-01 silent fallback would
silently corrupt every projected period's income figure.

### 2.7 Other Discoveries

- `interest_projection.calculate_interest` (`:20`): per-period interest;
  used only by `calculate_balances_with_interest`. One implementation.
- `pension_calculator`, `tax_calculator`: contribute to paycheck
  breakdown only; not direct balance contributors.
- `growth_engine.reverse_project_balance` (`:297`): not used by any
  balance display I can find. Used in tests.

---

## 3. Divergence Analysis

### 3.1 Checking "Current Balance": $160 (grid) vs $114.29 (savings)

**Two layers of divergence exist.**

**Layer A -- entries selectinload disagreement.**

The grid loads transactions WITH entries:
```python
# grid.py:230-237
all_transactions = (
    db.session.query(Transaction)
    .options(
        selectinload(Transaction.entries),
        selectinload(Transaction.template),
    )
    .filter(*txn_filters)
    .all()
)
```

The savings dashboard does NOT load entries:
```python
# savings_dashboard_service.py:96-104
all_transactions = (
    db.session.query(Transaction)
    .filter(
        Transaction.pay_period_id.in_(period_ids),
        Transaction.scenario_id == scenario.id,
        Transaction.is_deleted.is_(False),
    )
    .all()
)
```

Inside `_entry_aware_amount` (`balance_calculator.py:349-358`):
```python
if 'entries' not in txn.__dict__:
    return txn.effective_amount

entries = txn.__dict__['entries']
if not entries:
    return txn.effective_amount
```

So when the savings dashboard calls `calculate_balances`, every
expense transaction returns `effective_amount` -- which equals
`estimated_amount` for a projected expense regardless of any entry
records. When the grid calls `calculate_balances`, every expense
transaction with entries instead returns:

```
max(estimated - cleared_debit - sum_credit, uncleared_debit)
```

This means: a projected expense of \$500 estimated where the user has
recorded three cleared debit purchases of \$462.34 against it, AND the
anchor was trued up after those purchases:
- Grid calculates the holdback as `max(500 - 462.34, 0) = 37.66`,
  reducing the period's expense total by \$462.34.
- Savings dashboard counts the full \$500 against the projection.

If the difference between projected end balance and the savings
dashboard's "current balance" is \~\$45 (grid \$160 minus savings
\$114.29 is \$45.71), and a single grocery transaction with cleared
entries totalling \$45.71 (or some sum thereof) is the explanation,
that fits the observed delta exactly.

**Layer B -- format precision.**

The grid renders end balance as `"{:,.0f}".format(bal)` (whole
dollars) at `_balance_row.html:26`. Savings renders as `"{:,.2f}"` at
`dashboard.html:186`. So even if the underlying numbers were
identical, the grid would round to the nearest dollar while savings
shows cents. \$159.71 grid would display as "$160" while savings would
display as "$159.71". This is a separate issue from Layer A but
contributes to the perception of inconsistency.

**Net root cause:** The `_entry_aware_amount` function is called from
inside the calculator but its behavior depends on a load-side detail
the calculator's signature does not expose. Two callers cannot use the
calculator and get the same answer unless they both selectinload
entries.

### 3.2 Mortgage Payment Fluctuation: $1,911.54 / $1,914.34 / $1,912.94

**The displayed dashboard `summary.monthly_payment` and the
schedule's per-row Payment column are different quantities.**

`summary.monthly_payment` (in the dashboard headline) comes from
`get_loan_projection`:
- ARM branch (`amortization_engine.py:951-954`):
  `calculate_monthly_payment(current_principal, rate, remaining)`.
- Fixed branch (`:957-959`):
  `calculate_monthly_payment(original_principal, rate, term_months)`.

This is a single number derived from the params and the system clock
(via `calculate_remaining_months`). For ARM it changes every time
`current_principal` changes. The user reports that editing the
principal recalculates this to \$1,910.95, which matches: ARM,
re-amortized at the current rate over remaining months.

The schedule's per-row Payment column is computed in
`templates/loan/_schedule.html:55`:

```jinja
${{ "{:,.2f}".format((row.payment|float) + (monthly_escrow|float) + (row.extra_payment|float)) }}
```

`row.payment` for confirmed rows is set in `generate_schedule` line
545-549:
```python
actual_payment = principal_portion + interest
```
where `principal_portion = total_payment - interest` and `total_payment`
is the *actual transfer amount after escrow subtraction and biweekly
redistribution*. For projected (non-confirmed) rows, `row.payment` is
the contractual payment.

So the schedule shows different P&I per row whenever the user
transferred a different amount than the contractual P&I (very common
when the user transfers a round number like \$2,000 or transfers the
P&I-plus-escrow total in a single transfer). The escrow subtraction
adds a second layer of variance: `prepare_payments_for_engine`
(`loan_payment_service.py:305-319`) only subtracts escrow from amounts
exceeding contractual_pi, and only `min(monthly_escrow, excess)`. If a
transfer amount is slightly above contractual_pi but below
contractual_pi + monthly_escrow, only part of the escrow is subtracted,
leading to a non-standard `total_payment` going into the engine.

For an ARM loan: each confirmed row uses `interest = balance *
monthly_rate` where `balance` is the running balance computed by the
engine before the anchor reset fires (anchor only kicks in for the
first month after `today`). So pre-anchor confirmed rows have
"approximate" interest splits because the engine assumes the current
rate applied historically (per the `get_loan_projection` docstring at
`:880-881`). Combined with variable transferred amounts, this produces
the per-month variation \$1,911.54 / \$1,914.34 / \$1,912.94.

The "drifting principal" hypothesis from the prompt is partially
correct: the principal *displayed in the LoanParams form* and the
principal *the engine computes for any given pre-anchor month* differ
because the engine's running balance is approximate. The user trues
that up by editing `current_principal`, which then cascades to a
single recomputed `summary.monthly_payment` (\$1,910.95) but does NOT
retroactively change historical row Payments -- those still reflect
actual transferred amounts.

**Root cause:** The Payment column header and the dashboard "Monthly
Payment" use the same word but compute different things. There is no
single source of truth for "what is the P&I I owe each month."

### 3.3 /accounts vs Elsewhere

`templates/accounts/list.html:110` and
`templates/accounts/_anchor_cell.html:38` render
`acct.current_anchor_balance` -- the raw anchor stored on the Account
row (or `LoanParams.current_principal`'s mirror, depending on
account type). For checking, this is the last value the user typed
when they trued up. For loans, this is the user-typed
`current_principal`.

Every other page renders a *projected current-period* balance:
- Grid: `balances.get(current_period.id)` from `balance_calculator`.
- Savings: same.
- Dashboard summary: same.

Whenever there is at least one PROJECTED transaction in the current
period (very common: the next paycheck, the next mortgage transfer,
the next credit card payment), the projected end balance differs from
the anchor by exactly the net of those projected items.

For loans, the divergence is more dramatic: the Account row's
`current_anchor_balance` is rarely set (the Account model permits NULL),
so `/accounts` displays "$0.00" for a mortgage account whose actual
balance is six figures, while `/savings` displays the engine's
`current_balance` which is the anchor or the schedule walk.

**Root cause:** `/accounts` is showing a literal column value labelled
as "Current Balance" without any projection logic. Every other page
runs the projection.

### 3.4 Loan "Current Principal"

There are five mechanisms (§2.3.1). They produce different numbers in
different scenarios:

- ARM with no edits to `current_principal` since some confirmed
  payments: #1 returns the user-set value; #3 also returns the
  user-set value (ARM branch); #4 returns the user-set value; #5
  returns false (not paid off). Consistent.
- Fixed with confirmed payments not yet trued up: #1 returns the
  stored (stale) value; #3 returns the schedule-walk-back value
  (different); #4 returns the schedule-walk-back value (matches #3);
  #5 may return true if confirmed schedule reaches zero.
  **Inconsistent**: #1 (loan params edit form) and #3 (every other
  consumer of `get_loan_projection`) disagree.

The loan params form on `/accounts/<id>/loan` shows `current_principal`
from the DB column (#1) but the dashboard headline reads from
`get_loan_projection().current_balance` (#3). After the user edits
`current_principal` via the form, #1 and #3 agree until new confirmed
payments arrive.

For ARM specifically: the schedule has `is_confirmed` rows showing
balances that depend on the engine's approximate pre-anchor split,
which can differ from the stored `current_principal` by several
percent. The dashboard then displays both: form shows
`current_principal`, schedule's last confirmed row shows the engine's
balance just before the anchor reset. Different numbers, both labelled
"balance."

---

## 4. Specific Symptom Diagnoses

### 4.1 Grid end balance ($160) vs /savings checking balance ($114.29)

**Hypothesis:** The grid loads transactions with
`selectinload(Transaction.entries)` (`grid.py:232`), causing
`balance_calculator._entry_aware_amount` to apply the cleared-entry
holdback formula. The savings dashboard does NOT eager-load entries
(`savings_dashboard_service.py:96-104`), causing the same function to
return the full `effective_amount`. When at least one projected
checking expense has cleared entries totalling roughly the difference
(\$45.71), the two routes produce different "current period" balances.

**Evidence:**
- `balance_calculator.py:349-358` -- explicit `__dict__` check.
- `grid.py:232` -- `selectinload(Transaction.entries)`.
- `savings_dashboard_service.py:96-104` -- no `selectinload`.
- The grid renders `"{:,.0f}"` (rounds to nearest dollar), savings
  renders `"{:,.2f}"`. A grid display of \$160 corresponds to an
  underlying value in [\$159.50, \$160.49].

**Confirmation steps for the developer:** Open
`/accounts/<checking_id>/checking`. That route uses
`balance_calculator.calculate_balances` WITHOUT entries
(`accounts.py:949`), so it should match savings (\$114.29). If it does,
hypothesis confirmed. If checking_detail shows \$160, the hypothesis
is wrong and we need to look at scenario or account_id filter
divergence (less likely given both savings and grid share the same
filters).

### 4.2 Mortgage payment $1,911.54 -> $1,914.34 -> $1,912.94

**Hypothesis:** The schedule's Payment column shows each row's
`row.payment + monthly_escrow + extra_payment`. For confirmed payment
rows, `row.payment = principal_portion + interest` where
`principal_portion` is derived from the actual transferred amount
after escrow subtraction and biweekly redistribution. Because actual
transfer amounts vary slightly from the contractual P&I (the user
transferred whole-dollar amounts, or amounts that included partial
escrow), the per-row P&I varies. The dashboard's "Monthly Payment"
(\$1,910.95 after editing principal) is the contractual ARM payment
re-amortized from `current_principal` and is a different quantity.

**Evidence:**
- `templates/loan/_schedule.html:55` -- the addition is in the
  template using `|float`.
- `amortization_engine.py:545-549` -- confirmed-row `actual_payment =
  principal_portion + interest` where `principal_portion = total_payment
  - interest`.
- `loan_payment_service.py:305-319` -- escrow subtraction is
  conditional on payment > contractual_pi, and subtracts only the
  excess capped at monthly_escrow. A transfer of \$P that is between
  contractual_pi and contractual_pi + monthly_escrow produces a
  partial escrow strip-off.
- ARM-specific: `get_loan_projection` (`amortization_engine.py:880-881`)
  documents that pre-anchor rows have approximate P&I splits because
  the engine assumes current rate applied historically.

**Confirmation steps:** Look at the actual Transfer amounts on the
mortgage account in the rows that show \$1,911.54, \$1,914.34,
\$1,912.94. Each row should correspond to a transfer whose total
(post-escrow strip) is consistent with the displayed Payment.
Specifically, `row.payment + monthly_escrow + row.extra_payment ==
the actual transferred amount` (they should round-trip exactly except
for re-quantization).

### 4.3 /accounts balances differ from elsewhere

**Hypothesis:** `/accounts` renders `acct.current_anchor_balance`
literally. Every other surface renders a projected current-period
balance from `balance_calculator`. They are different quantities by
design but both labelled "Current Balance" in the UI.

**Evidence:**
- `templates/accounts/list.html:35` header: "Current Balance".
- `templates/accounts/_anchor_cell.html:38` value:
  `acct.current_anchor_balance|float`.
- `templates/savings/dashboard.html:183` header: "Current Balance".
- `templates/savings/dashboard.html:186` value:
  `ad.current_balance|float` where `current_balance` comes from
  `balance_calculator.get(current_period.id)`.

The two values agree only when the current pay period has no
projected transactions affecting the account. For loans, the
divergence is amplified because Account.current_anchor_balance is
typically NULL for loan accounts (the principal lives in
LoanParams.current_principal), so /accounts shows \$0 or "--" while
/savings shows the real principal.

---

## 5. Template-Side Computation

The coding standards forbid arithmetic in templates. Findings:

| File:Line | Computation | Severity |
|---|---|---|
| `templates/loan/_schedule.html:55` | `(row.payment\|float) + (monthly_escrow\|float) + (row.extra_payment\|float)` -- the displayed Payment column. **Float arithmetic on monetary values, in the user-visible total**. | HIGH. Direct violation. Implicated in the mortgage-payment fluctuation symptom (§4.2) because it forces the "Payment" interpretation onto whatever combination of confirmed and projected sources happen to populate the row. |
| `templates/loan/_schedule.html:10` | `set col_count = 7 + (1 if monthly_escrow\|float > 0 else 0) + ...` -- arithmetic but only over column counts (integers), not money. | LOW. Cosmetic. |
| `templates/loan/dashboard.html:227` (and several other escrow displays) | `${{ "{:,.2f}".format(monthly_escrow|float) }}/mo` -- format only, no arithmetic. | None. |
| Many places use `|float > 0` for class/sign decisions. | Not arithmetic on displayed monetary values. | LOW (still violates the Decimal-everywhere principle). |

Audit confirms the `adversarial_audit.md` H-05 finding about grid
subtotals using float was fixed (subtotals are now computed in
`grid.py:268-282` as Decimal and rendered without arithmetic). One
remaining material violation is `_schedule.html:55`.

---

## 6. Other Findings

These were noticed during the read and per CLAUDE.md rule 4 must be
reported:

1. **`balance_calculator.calculate_balances_with_amortization` is
   unused.** Defined at `balance_calculator.py:176-289`, ~115 lines
   including its own ARM/fixed branch and payment detection. No
   non-test caller. It duplicates logic from `get_loan_projection` and
   `_compute_account_projections` and would produce different numbers
   if it ever WERE called. Recommend deletion.

2. **`payoff_calculate` target_date mode passes
   `origination_date=date.today().replace(day=1)` to
   `calculate_payoff_by_date`** (`loan.py:963`). This conflicts with
   the rest of the route which uses `params.origination_date`. The
   binary search in `calculate_payoff_by_date` (engine `:822-845`)
   regenerates schedules using this fake origination date and a
   `current_principal` derived from the committed projection. If the
   loan has confirmed payments, those payments dated before
   `date.today().replace(day=1)` will be filtered out by
   `_build_payment_lookups` (engine `:241`) -- but `calculate_payoff_by_date`
   doesn't pass payments anyway, so the issue is just inconsistent
   semantics. Still likely wrong for ARM since `calculate_payoff_by_date`
   doesn't accept anchor parameters at all.

3. **`refinance_calculate` uses
   `origination_date=date.today().replace(day=1)`** for the refi
   schedule (`loan.py:1065-1070`). This is intentional (a new loan
   starts today) and probably correct, but it's a fourth distinct
   "origination_date" treatment in the same file.

4. **`recurrence_engine._get_transaction_amount` still has the C-01
   silent fallback.** From the prior `adversarial_audit.md`; appears
   unfixed. Any exception in `paycheck_calculator.calculate_paycheck`
   silently substitutes `template.default_amount` for every projected
   income transaction. Direct impact on the projected balance.

5. **`_compute_real_principal` (debt_strategy.py:145) is a third copy
   of "current real principal" logic** (after #1
   `LoanParams.current_principal`, #3
   `get_loan_projection().current_balance`). All three should converge.

6. **`_check_loan_paid_off` (savings_dashboard_service.py:445) is yet
   another schedule-replay** but with confirmed-only payments and
   without rate_changes. The `bug_investigation_01_amortization.md`
   doc flagged this as remaining issue #1 ("uses old pattern without
   origination params") but the current code DOES use origination
   params -- it just omits rate_changes for ARM. Still inconsistent
   with the other replay sites.

7. **Three near-duplicate "shadow income on this account" queries**
   (`savings_dashboard_service.py:107-120`,
   `investment.py:165-176`, `loan_payment_service.py:194-214`). Each
   has slightly different filtering (one joins Status, one
   joinedloads, one uses Status filter directly) and different
   ordering. They should be one helper.

8. **`Transaction.account` is `lazy="joined"`** (`models/transaction.py:118`)
   while `Transaction.entries` is `lazy="select"` (line 137). Every
   transaction load joins Account whether or not it's needed; entries
   are conditionally selectinload'd. This contributes to the entries
   divergence problem because there's no consistent default.

9. **`Account.current_anchor_balance` column is nullable** (`models/account.py:31`).
   This forces every consumer to write `or Decimal("0.00")` defensively.
   For loan accounts, it is typically NULL (the principal lives in
   LoanParams). Treating it as the "Current Balance" on /accounts
   produces "--" for every loan. The Account model is being asked to
   represent two different things: the anchor for liquid accounts and
   nothing-much for loans.

10. **Dashboard uses the same balance calculator with entries
    selectinload'd** (`dashboard_service.py:697`) -- so the dashboard
    summary card and the grid agree, but disagree with savings dashboard
    and checking_detail. Three-way inconsistency: {dashboard, grid} vs.
    {savings, checking_detail, interest_detail, investment} vs.
    {/accounts}.

11. **Grid's `subtotals` (`grid.py:267-282`) computes "income/expense"
    using `txn.effective_amount` directly**, not `_entry_aware_amount`.
    This means the subtotal can differ from the contribution to the
    end balance for the same period -- if entries are loaded, the
    balance reflects the holdback formula, but the subtotals reflect
    the full estimated. Subtle internal inconsistency within the grid
    itself.

12. **`_get_debt_summary` in dashboard_service** (`dashboard_service.py:532`)
    invokes the entire `savings_dashboard_service.compute_dashboard_data`
    just to extract debt_summary, with a broad
    `except (ValueError, KeyError, AttributeError)` that swallows
    exceptions. Performance + silent-failure issue.

13. **`logger = logging.getLogger(__name__)` is positioned mid-file in
    `balance_calculator.py:29`** -- between two imports. Stylistic only.

---

## 7. Recommended Consolidation Plan (Sketch)

These are *suggestions* for the developer to convert into an actual
remediation plan. They are ordered by user-visible impact.

### Tier 1 -- Eliminate the entry-load divergence

Single source of truth for "expense impact on a balance" should not
depend on how the caller eager-loaded relationships. Two options:

- **Option A (preferred):** Move the entry partitioning out of the
  calculator. Compute the per-transaction holdback in the route or in
  a new pure helper that explicitly receives entries. The calculator
  takes a list of (transaction, contribution_amount) pairs precomputed
  by the caller. Eliminates the silent `__dict__` check.
- **Option B:** Make the calculator perform the entry load itself, so
  every caller gets the same answer at the cost of a query. Slower
  but simpler.

Either way, the goal: any two callers of "give me the projected
balance for account X at period Y" must get the same answer.

### Tier 2 -- One canonical "current balance per account" function

Create `account_balance_service.get_current_balance(account_id, user_id)
-> Decimal` that:

1. Loads the account, scenario, periods, transactions, and entries
   correctly.
2. Dispatches by account_type metadata (`has_amortization`,
   `has_interest`, `has_parameters`).
3. For loans, delegates to `get_loan_projection` and returns
   `current_balance`.
4. For interest-bearing, calls `calculate_balances_with_interest`.
5. For investment/retirement, calls `growth_engine.project_balance`
   seeded by `calculate_balances`.
6. For checking/cash/credit-card, calls `calculate_balances`.

Then update `/accounts`, `/savings`, dashboard, and detail pages to
call this single function. Remove the raw
`acct.current_anchor_balance` template references.

### Tier 3 -- Single "monthly P&I payment" function

Replace the five copies of "ARM uses current_principal/remaining,
fixed uses original_principal/term" with one helper -- in fact
`compute_contractual_pi` (`loan_payment_service.py:233`) already exists
and does this correctly. Replace the inline duplicates in:

- `loan.py:create_payment_transfer:1178-1192`
- `balance_calculator.calculate_balances_with_amortization:217-235` (or
  delete the whole function per Other-Findings #1)
- The branches inside `get_loan_projection` and `calculate_summary`
  that re-derive the same quantity.

### Tier 4 -- Move the schedule's Payment column computation off the template

Add a `payment_with_escrow` field to `AmortizationRow` (or compute it
in the route and pass a list of dicts). The template should never
add `|float`'d Decimals.

This also forces clarity: the "Payment" displayed is
`row.payment + monthly_escrow + row.extra_payment`. If the user wants
a single static "Monthly Payment" displayed in the schedule header, it
should be `summary.monthly_payment + monthly_escrow`. Right now the
header and the schedule rows display materially different concepts
both labelled "Monthly Payment" / "Payment".

### Tier 5 -- One canonical loan-context loader

Most of this is already done by `load_loan_context`
(`loan_payment_service.py:78`), but several call sites re-implement
parts of it (`_compute_real_principal`, `_check_loan_paid_off`,
`payoff_calculate target_date`). Push the remaining call sites
through the shared loader, then push schedule generation through a
single helper that takes a `LoanContext` plus a "scenario flag"
(original / committed / floor / accelerated / refinance) and returns
the schedule.

### Tier 6 -- Address the C-01 silent paycheck fallback (still open)

Per `adversarial_audit.md` C-01: narrow the `except Exception` in
`recurrence_engine._get_transaction_amount:543-549`. Until this is
done, balance projections silently use gross instead of net pay
whenever any tax config glitch occurs.

### Tier 7 -- Delete dead code

`balance_calculator.calculate_balances_with_amortization` is unused
and conflicts with `get_loan_projection`. Removing it eliminates a
trap door for future divergence.

---

## Appendix: Files Read

Services (full): `account_resolver.py`, `amortization_engine.py`,
`balance_calculator.py`, `dashboard_service.py`, `escrow_calculator.py`,
`growth_engine.py`, `interest_projection.py`, `investment_projection.py`,
`loan_payment_service.py`, `pay_period_service.py`,
`savings_dashboard_service.py`, `transfer_service.py` (full).

Services (skimmed for divergence): `calibration_service.py`,
`carry_forward_service.py` (function list and key signatures),
`debt_strategy_service.py`, `paycheck_calculator.py`,
`recurrence_engine.py`, `transfer_recurrence.py`,
`year_end_summary_service.py` (debt_schedules section).

Routes (full): `accounts.py`, `grid.py`, `loan.py`, `savings.py`.
Routes (relevant sections): `dashboard.py`, `debt_strategy.py`,
`investment.py`, `transactions.py`, `transfers.py`.

Models (full): `account.py`, `loan_params.py`, `transaction.py`.
Models (skimmed): `transfer.py`, `transaction_entry.py`,
`pay_period.py`, `interest_params.py`, `investment_params.py`,
`loan_features.py`, `savings_goal.py`.

Templates: `accounts/list.html`, `accounts/_anchor_cell.html`,
`grid/grid.html`, `grid/_balance_row.html`, `loan/dashboard.html`,
`loan/_schedule.html`, `savings/dashboard.html`. Grep for arithmetic
across all `app/templates/`.

Prior investigation docs read: `bug_investigation_01_amortization.md`,
`bug_investigation_03_transfer_rename.md`, `bug_investigation_report.md`,
`implementation_plan_arm_anchor_refactor.md`, `adversarial_audit.md`.
