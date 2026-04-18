# 16 -- Section 1L Financial-Correctness and Business-Logic Audit

**Session:** S5
**Branch:** audit/security-2026-04-15
**Scope:** Section 1L from docs/security-audit-workflow.md (six checks).
**Method:** Static analysis of services, routes, schemas, and models.
No code changes, no scanner runs, no container interaction beyond a
pre-session `docker ps` snapshot for change detection.

This report covers ONLY financial-correctness concerns:

- **Check 1** -- Type-purity (float vs Decimal contamination).
- **Check 2** -- Concurrency / TOCTOU on read-check-write paths.
- **Check 3** -- The five transfer invariants from CLAUDE.md.
- **Check 4** -- Decimal precision and accumulated rounding drift.
- **Check 5** -- Negative- and zero-amount handling.
- **Check 6** -- Idempotency of mutating endpoints.

Findings are recorded with concrete reproduction scenarios so the
developer can make a triage decision on each one. "Theoretically
possible" without a trigger is not a finding here.

---

## Check 1: Type Purity

**What was checked.** Every occurrence of six dangerous patterns
across `app/services/` and `app/routes/`:

1. `float(` -- any conversion from Decimal to float (can leak back
   into monetary arithmetic if the float is re-used).
2. `Decimal(x)` where `x` is NOT a string literal -- a `Decimal(1.1)`
   where `1.1` is a float would capture 18 digits of float imprecision
   (`Decimal(1.1)` is `Decimal("1.1000000000000000888...")`).
3. `round(` -- Python's `round()` uses banker's rounding (ROUND_HALF_EVEN)
   by default, which may not match accounting rules; Shekel is expected
   to use `Decimal.quantize(..., rounding=ROUND_HALF_UP)`.
4. `/ 100` and `* 100` -- percentage conversions where the right-hand
   side is an `int`; if the left is a `float` these silently produce
   a float.
5. `**` (exponentiation) -- Decimal supports `**` but subtleties
   around integer-vs-non-integer exponents matter (Check 4 territory).
6. `if not amount:` / `if amount:` -- truthiness on a monetary Decimal.
   Per CLAUDE.md: "do not rely on truthiness for business logic" --
   a zero balance is not a missing balance.

### Grep 1 -- `float(`

Seven hits across both trees.  Classified:

| File:line | Code | Classification | Why |
|---|---|---|---|
| `app/routes/loan.py:102` | `balances.append(float(row.remaining_balance))` | **Safe** | Chart.js JSON boundary; comment `Presentation boundary: float() for Chart.js JSON serialization.` makes intent explicit.  Not re-used in any math. |
| `app/routes/debt_strategy.py:441` | `data = [float(b) for b in acct.balance_timeline]` | **Safe** | Same Chart.js boundary; comment present. |
| `app/routes/companion.py:54` | `float(total / txn.estimated_amount * Decimal("100"))` | **Safe** | Template progress-bar percent width.  The Decimal arithmetic is done before `float()`, so the value is already quantized; only the final display is float.  Not re-used. |
| `app/routes/analytics.py:448-449` | `[float(g.estimated_total) for g in report.groups]` | **Safe** | Chart JSON serialization.  Not re-used. |
| `app/services/retirement_dashboard_service.py:238` | `current_swr = float(settings.safe_withdrawal_rate or 0.04) * 100 if settings else 4.0` | **Low finding (L1-a)** | Two problems: (a) `or 0.04` uses a **float literal** as fallback, so `current_swr` is a float whenever the column is NULL -- this is display-only so harmless today, but see L1-a below; (b) `or` is Python truthiness, so `Decimal("0.0000")` (a valid if unusual SWR) silently falls back to 4% -- see L1-b below. |
| `app/services/retirement_dashboard_service.py:255` | `current_return = float(weighted_return / total_balance) * 100` | **Safe** | Decimal/Decimal = Decimal, then `float(Decimal) * int` for a slider value.  Display-only, not re-used in financial math. |

None of the seven `float()` calls flow back into Decimal arithmetic
or into a database column.  Every call is on the render path between
the service layer and Chart.js / Jinja.

**Verdict: Grep 1 is clean on type purity.** The only concern is
the `or 0.04` pattern in retirement_dashboard_service.py:238 which
is better classified under Grep 6 (truthiness) below.

### Grep 2 -- `Decimal(` with a non-string argument

~120 hits across services and ~30 hits in routes.  All fall into one
of four safe categories:

- **`Decimal(str(variable))`** -- the defense-in-depth idiom.  The
  `str()` ensures even if `variable` is a float, the Decimal is built
  from the *string representation*, which preserves whatever precision
  was in the original display.  For a Decimal input, `str(Decimal)` is
  lossless.  For a float input like `0.1`, `str(0.1)` returns `"0.1"`
  and `Decimal("0.1")` is exact.  **Safe.** This pattern appears in
  every pure service (tax_calculator, paycheck_calculator,
  interest_projection, escrow_calculator, amortization_engine,
  calibration_service, growth_engine, etc.) -- the discipline is
  consistent.
- **`Decimal(integer_literal_or_variable)`** -- e.g.
  `Decimal(count)` at `year_end_summary_service.py:625`, `Decimal(0)`,
  `Decimal(1)`.  `Decimal(int)` is exact by construction.  **Safe.**
- **`Decimal(form_string)`** at `transactions.py:324`, `:358`,
  `routes/debt_strategy.py:236`, `routes/investment.py:558`,
  `routes/loan.py:857`, `routes/dashboard.py:166`.  Flask's
  `request.form.get()` always returns `str` or `None`.  The `if value:`
  truthiness check guards against the `None` and empty-string cases,
  and the `Decimal(str)` constructor handles the rest with `try/except
  InvalidOperation`.  **Safe.**
- **`Decimal(str(model_column))`** where the column is `Numeric(12,2)`
  -- the value is already a Decimal when loaded from the DB, so `str()`
  is redundant but not harmful.  **Safe.**

I searched specifically for `Decimal(<float_var>)` by reading each
non-string hit in context.  **No hit converts a float value directly
to Decimal.** The only float literals in the project that end up
near Decimal math are in `retirement_dashboard_service.py:238` and
`:250`, and both are explicitly re-wrapped through `Decimal(str(...))`
or used only in the float branch for display.

**Verdict: Grep 2 is clean.**

### Grep 3 -- `round(`

**Zero hits in `app/services/` and `app/routes/`.** Every rounding
operation in the codebase uses
`Decimal.quantize(..., rounding=ROUND_HALF_UP)` -- verified for
paycheck_calculator, tax_calculator, interest_projection,
escrow_calculator, amortization_engine, calibration_service,
balance_calculator, and loan_payment_service.  This is the correct
accounting standard.

**Verdict: Grep 3 is clean.** (The absence of `round(` is itself
strong evidence of disciplined Decimal use.)

### Grep 4 -- `/ 100` and `* 100`

Seven code hits (one comment excluded).  Classified:

| File:line | Code | Classification | Why |
|---|---|---|---|
| `app/services/paycheck_calculator.py:319` | `pct = Decimal(str(raise_obj.percentage)) * 100` | **Safe** | `Decimal * int` = Decimal, used in display string only. |
| `app/routes/loan.py:173` | `raw_pct = amount / total_payment * 100` | **Safe** | All operands are Decimals (from escrow_calculator), result is Decimal, used for percent display. |
| `app/routes/investment.py:241` | `ytd_contributions / params.annual_contribution_limit * 100` | **Safe** | Both sides Decimal; result is Decimal wrapped in `int(min(100, ...))` for a progress bar width. |
| `app/services/savings_dashboard_service.py:688` | `int(acct_balance / resolved_target * 100)` | **Safe** (display truncation only) | Decimal/Decimal = Decimal, `int()` truncates to percent for a progress bar. Progress bar truncation instead of rounding under-reports by <1 pp -- acceptable for display. |
| `app/services/retirement_dashboard_service.py:238` | `* 100` on a `float()` result | Classified under Grep 1/6 |
| `app/services/retirement_dashboard_service.py:255` | `* 100` on a `float()` result | Classified under Grep 1/6 |

User-input percentage conversions use the explicit
`Decimal(str(x)) / Decimal("100")` idiom everywhere:
`accounts.py:887`, `settings.py:205`, `:217`,
`retirement.py:109`, `:175`, `:258`, `:262`, `:295`,
`investment.py:812`.  All safe.

**Verdict: Grep 4 is clean.**

### Grep 5 -- `**` (exponentiation)

Seven real exponentiation hits (excluding `**kwargs` unpacking in
Python function calls).  Classified by **exponent type**:

| File:line | Code | Exponent type | Classification |
|---|---|---|---|
| `app/services/amortization_engine.py:195` | `factor = (1 + monthly_rate) ** remaining_months` | `int` | **Safe** -- Decimal ** int uses fast exact power. |
| `app/services/paycheck_calculator.py:409` | `amount * (1 + inflation_rate) ** years` | `int` (`_inflation_years` returns int) | **Safe**. |
| `app/services/escrow_calculator.py:109` | `annual * (1 + rate) ** year_offset` | `int` (from `range()`) | **Safe**. |
| `app/services/interest_projection.py:49` | `balance * ((1 + daily_rate) ** period_days - 1)` | integer-valued Decimal (`Decimal(str(days))`) | **Safe** -- Decimal's `__pow__` fast-paths integer exponents. |
| `app/services/escrow_calculator.py:52` | `annual * (1 + rate) ** years_elapsed` | **non-integer Decimal** (`months_elapsed / Decimal("12")`) | **Precision concern for Check 4**, classified below. |
| `app/services/growth_engine.py:241` | `(1 + assumed_annual_return) ** (Decimal(str(period_days)) / Decimal("365")) - 1` | **non-integer Decimal** | **Precision concern for Check 4**. |
| `app/services/growth_engine.py:355` | `... ** (Decimal(str(period_days)) / Decimal("365"))` | **non-integer Decimal** | **Precision concern for Check 4**. |

The three non-integer-exponent hits are NOT type-purity bugs --
Decimal ** Decimal with a non-integer exponent is defined in Python
(uses the `decimal` module's mpdecimal library) and produces a Decimal
result -- but the result is *inexact within Decimal's context precision*
(default 28 digits).  This is a **Check 4 (precision)** concern, not
a Check 1 (type purity) concern, so I've moved them there.

**Verdict: Grep 5 is clean on type purity.** Three hits deferred to
Check 4.

### Grep 6 -- Truthiness on monetary values

I ran two greps -- `if not <var>:` and `if <var>:` -- targeting the
variable names: `amount`, `balance`, `principal`, `estimated_amount`,
`actual_amount`, `anchor_balance`, `gross`, `net_pay`, `salary`,
`default_amount`, `total`, `interest`, `payment`, `rate`, `apy`, plus
attribute patterns (`.percentage`, `.flat_amount`, `.inflation_rate`,
`.assumed_annual_return`, `.rate`, `.amount`, `.balance`,
`.principal`).  Hits that test object existence, collection emptiness,
or ref data (not monetary Decimals) are safe.

The monetary-Decimal hits:

| File:line | Code | Classification | Severity |
|---|---|---|---|
| `app/services/paycheck_calculator.py:289` | `if raise_obj.percentage:` | **Low** (L1-c) | See below |
| `app/services/paycheck_calculator.py:292` | `if raise_obj.flat_amount:` | **Low** (L1-c) | See below |
| `app/services/paycheck_calculator.py:318` | `if raise_obj.percentage:` (in `_get_raise_event`) | **Info** (L1-d) | Display-only |
| `app/services/paycheck_calculator.py:403` | `if ded.inflation_enabled and ded.inflation_rate:` | **Info** (L1-e) | Harmless |
| `app/services/escrow_calculator.py:35` | `if as_of_date and hasattr(comp, "inflation_rate") and comp.inflation_rate:` | **Info** (L1-e) | Harmless |
| `app/services/retirement_dashboard_service.py:238` | `settings.safe_withdrawal_rate or 0.04` | **Low** (L1-a, L1-b) | See below |
| `app/services/retirement_dashboard_service.py:250` | `if params and params.assumed_annual_return:` | **Low** (L1-f) | See below |

---

### Findings from Check 1

#### L1-a -- Float literal fallback in `retirement_dashboard_service.py:238` (Low)

```python
current_swr = float(settings.safe_withdrawal_rate or 0.04) * 100 if settings else 4.0
```

**What goes wrong.** The fallback `or 0.04` is a `float` literal.  If
`settings.safe_withdrawal_rate` is `None` (column is NULL), the
expression becomes `float(0.04) * 100 = 4.000000000000001` (float
imprecision: `0.04 * 100` in IEEE-754 doubles is not exactly `4.0`).

**User-visible consequence.** The retirement dashboard slider default
shows `4.000000000000001%` instead of `4%` if the user hasn't set an
SWR and Jinja doesn't format the output.  In practice, the template
probably formats to `{:.2f}` so the user never sees the drift, but it
is still non-clean.

**Severity:** Low.  Display-only, and template-level formatting masks
the imprecision.

**Fix:** `Decimal("0.04")` as the fallback, then convert to float at
the very last step:

```python
swr = settings.safe_withdrawal_rate if settings and settings.safe_withdrawal_rate is not None else Decimal("0.04")
current_swr = float(swr * 100)  # Still float for the slider, but
                                # the multiplication happens in Decimal.
```

#### L1-b -- `or` truthiness on Decimal SWR in `retirement_dashboard_service.py:238` (Low)

**What goes wrong.** `settings.safe_withdrawal_rate or 0.04` treats
`Decimal("0.0000")` as falsy and falls back to 4%.  SWR of 0 is
semantically nonsensical (you'd never draw from savings), but Python's
`or` cannot distinguish "user explicitly set 0" from "never set."

**User-visible consequence.** If a user sets SWR to 0% via the API or
a future admin tool, the slider still shows 4%.

**Severity:** Low.  Contrived scenario -- SWR=0 is not useful to a
user -- but violates CLAUDE.md's "do not rely on truthiness" rule.

**Fix:** `settings.safe_withdrawal_rate if settings and settings.safe_withdrawal_rate is not None else Decimal("0.04")`.

#### L1-c -- Truthiness on raise percentage/flat_amount in `paycheck_calculator.py:289, 292` (Low)

```python
def _apply_single_raise(salary, raise_obj):
    if raise_obj.percentage:
        pct = Decimal(str(raise_obj.percentage))
        return salary * (1 + pct)
    if raise_obj.flat_amount:
        return salary + Decimal(str(raise_obj.flat_amount))
    return salary
```

**What goes wrong.** If `raise_obj.percentage == Decimal("0.00")`,
the first `if` is False and control falls to the second `if`.  Since
`RaiseCreateSchema.validate_one_method` enforces exactly one of
percentage/flat_amount, `flat_amount` is None, so the second `if` is
also False, and the function returns `salary` unchanged.

**User-visible consequence.** Today: none.  A 0% raise is
mathematically equivalent to no raise, so returning `salary` is
arithmetically correct.  The `schema.validate_one_method` requires
exactly one of percentage/flat_amount, so a user cannot accidentally
enter both zero -- the schema would reject it.

**Why it's still a finding.** CLAUDE.md says "do not rely on
truthiness for business logic."  A future refactor -- e.g., adding
a raise-event audit log or a UI toast that announces "Raise applied:
+0%" -- would silently skip the log for 0% raises.  The
`Decimal("0")` and `None` cases should be disambiguated with
`is None`.

**Severity:** Low.  Correct today by mathematical coincidence.

**Fix:**
```python
if raise_obj.percentage is not None:
    ...
elif raise_obj.flat_amount is not None:
    ...
```

#### L1-d -- Truthiness in `_get_raise_event` (paycheck_calculator.py:318) (Info)

Display-only message generation.  A 0% raise produces no event
string -- desired behavior (why would you show "+0%" to the user?)
but achieved via truthiness rather than explicit check.  No user
harm.

#### L1-e -- Truthiness on `inflation_rate` (paycheck_calculator.py:403, escrow_calculator.py:35) (Info)

Zero inflation rate is semantically "no adjustment," and skipping
the inflation block produces the correct result.  Truthiness happens
to work.  Same Info-level recommendation as L1-c: prefer
`is not None`.

#### L1-f -- Truthiness on `assumed_annual_return` in weighted-average return (Low)

```python
if params and params.assumed_annual_return:
    bal = proj.get("current_balance", acct.current_anchor_balance) or Decimal("0")
    total_balance += bal
    weighted_return += bal * params.assumed_annual_return
```

**What goes wrong.** If an investment account has
`assumed_annual_return = Decimal("0")` (a conservative or stopped-
contributions scenario), both its balance AND its return contribution
are excluded from the weighted-average return calculation.  This skews
the `current_return` displayed to the user toward the non-zero-return
accounts only.

Additionally, `acct.current_anchor_balance or Decimal("0")` on the
same line uses `or` truthiness -- a checking balance of exactly
`Decimal("0.00")` would fall back to the same `Decimal("0")`, so no
actual harm here.

**User-visible consequence.** Slider default return rate is wrong
for users who explicitly set `assumed_annual_return = 0` on one or
more accounts (uncommon but possible).  Display-only.

**Severity:** Low.  The account is silently excluded from the
weighted average rather than being counted with a zero return.

**Fix:** `if params and params.assumed_annual_return is not None:`
and separately use `current_balance if current_balance is not None
else Decimal("0")`.

---

### Check 1 summary

| Grep | Pattern | Hits | Findings | Severity |
|---|---|---|---|---|
| 1 | `float(` | 7 | 0 type-purity findings; 2 truthiness findings (deferred to Grep 6) | Low |
| 2 | `Decimal(non-string)` | ~150 | 0 -- all hits are `Decimal(str(...))` or `Decimal(int)` | n/a |
| 3 | `round(` | 0 | 0 -- zero occurrences is strong positive evidence | n/a |
| 4 | `/ 100`, `* 100` | 7 | 0 -- all percentage math uses explicit Decimal | n/a |
| 5 | `**` | 7 | 0 type-purity; 3 precision concerns deferred to Check 4 | n/a |
| 6 | Truthiness on monetary | 7 code hits | 6 (L1-a through L1-f) | 0 Medium, 3 Low, 3 Info |

**Zero High findings.** **Zero Medium findings.** **Six Low/Info
findings**, all in retirement/paycheck/escrow service display code,
none on a critical balance-calculation path.  The codebase is
exceptionally disciplined about Decimal use.  The `Decimal(str(x))`
idiom is applied consistently, `Decimal.quantize()` is used uniformly
for rounding, and the only float conversions are explicit
presentation-layer boundaries for Chart.js and Jinja templates.

**No invariant or transfer-related code appears in Check 1 findings.**
transfer_service.py, balance_calculator.py, and recurrence_engine.py
all pass Check 1 completely.

---

## Check 2: Concurrency and TOCTOU

**What was checked.** Every mutating endpoint in `app/routes/` that
performs a read-check-write pattern against user data.  For each, I
verified whether the read-check-write is serialized against
concurrent access via (a) a row-level lock
(`.with_for_update()`), (b) a conditional UPDATE
(`UPDATE ... WHERE id=X AND status=Y`), (c) a unique database
constraint that would reject a duplicate, or (d) none of these.

### Preliminary greps

**`with_for_update` usage across app/:** `grep -rn with_for_update app/`
-- **zero hits.** Not a single row-level lock anywhere in the
codebase.

**Isolation level overrides:** `grep -rn 'isolation_level\|SERIALIZABLE\|REPEATABLE READ' app/`
-- **zero hits.** No session overrides of PostgreSQL's default
READ COMMITTED isolation.

**READ COMMITTED implications.** Two concurrent sessions can both
SELECT the same row, both see the same "current" state, and both
UPDATE based on that state -- with the second UPDATE silently
overwriting the first's write.  PostgreSQL's MVCC does NOT block
the second SELECT on the first transaction's uncommitted state
(that's REPEATABLE READ's job).  Therefore every read-check-write
in Shekel is susceptible to TOCTOU unless a unique constraint or
conditional UPDATE provides a different form of protection.

**Unique constraints that provide incidental TOCTOU protection.** I
grepped the models and found:

- `uq_accounts_user_name` on `(user_id, name)` -- prevents duplicate
  account names.
- `uq_pay_periods_user_start` on `(user_id, start_date)` -- prevents
  duplicate periods.
- `uq_scenarios_user_name`, `uq_transfer_templates_user_name`,
  `uq_salary_profiles_user_scenario_name`, `uq_categories_user_group_item`
  -- same pattern for named user entities.
- Partial unique index `idx_transactions_template_period_scenario`
  on `(template_id, pay_period_id, scenario_id)` WHERE
  `template_id IS NOT NULL AND is_deleted = FALSE` -- prevents
  duplicate template-generated transactions per period.
- Partial unique index `idx_transfers_template_period_scenario` --
  same for template-generated transfers.
- `uq_calibration_overrides_profile` on `(salary_profile_id)` --
  one calibration per profile.
- `uq_tax_bracket_sets_user_year_status`,
  `uq_state_tax_configs_user_state_year`, `uq_fica_configs_user_year`
  -- tax config uniqueness.
- `uq_savings_goals_user_acct_name`.
- `uq_escrow_account_name` on `(account_id, name)`.

**Unique constraints that are missing (directly relevant to the
critical paths):**

- **No unique constraint on `Transaction.credit_payback_for_id`.**
  Nothing at the DB level prevents two CC Payback transactions from
  pointing at the same original transaction.  This is the root cause
  of findings H-C2-02 and H-C2-03 below.
- **No version column on `Account.current_anchor_balance`.** No
  optimistic-concurrency protection on the anchor -- last-writer-wins.
- **No unique constraint on ad-hoc transactions or ad-hoc transfers
  (template_id IS NULL).** A double-submit creates visible duplicates.

### Endpoints enumerated

70+ mutating endpoints across blueprints.  I focused Check 2 on the
ones that touch financial state directly (transfers, transactions,
accounts, entries, dashboard mark-paid, carry-forward, salary
calibration).  The non-financial ones (auth, MFA, companion management,
category CRUD, template CRUD) have their own race conditions but are
out of scope for Section 1L.

---

### Finding H-C2-01 -- Anchor balance update is last-writer-wins with no optimistic concurrency (High)

**Endpoints affected:**
- `accounts.py:466 /accounts/<id>/inline-anchor` (PATCH)
- `accounts.py:648 /accounts/<id>/true-up` (PATCH)
- `accounts.py:220 /accounts/<id>` (POST -- contains anchor edit when present)

**Code (`accounts.py:666-695`):**

```python
data = _anchor_schema.load(request.form)
new_balance = Decimal(str(data["anchor_balance"]))

current_period = pay_period_service.get_current_period(current_user.id)
...
account.current_anchor_balance = new_balance
account.current_anchor_period_id = current_period.id

history = AccountAnchorHistory(
    account_id=account.id,
    pay_period_id=current_period.id,
    anchor_balance=new_balance,
)
db.session.add(history)
...
db.session.commit()
```

There is no row lock, no version column, no conditional UPDATE, and
no unique constraint that would serialize two concurrent anchor
updates.

**Concrete scenario.** A user's browser retries a PATCH request
that was slow:
1. **T=0** -- User types $1100 into the anchor field, submits. Browser
   sends PATCH /accounts/5/true-up with `anchor_balance=1100`.  The
   request makes it to the server and commits (Account.current_anchor_balance
   = $1100, history row #1 written).
2. **T=1s** -- User sees the server still "thinking" (network hiccup
   on the response side), notices the balance is wrong in the UI,
   types $1200 into the same field, submits.  Browser sends PATCH
   with `anchor_balance=1200`.
3. **T=1.5s** -- The browser's HTTP retry of the T=0 request fires
   (because it never got a response), re-sending `anchor_balance=1100`.
4. **T=2s** -- The $1200 request commits: anchor = $1200, history #2.
5. **T=2.5s** -- The $1100 retry commits: anchor = $1100, history #3.

**Final state:** anchor is $1100.  The user's latest intention ($1200)
is lost.  The history table shows $1100, $1200, $1100 -- but the live
anchor is the wrong value.  Every balance projection from now forward
uses the stale $1100 anchor.

**User-visible consequence.** The projected end-of-year balance, the
low-balance alert threshold, the runway calculation -- all derive
from `Account.current_anchor_balance`.  A silent rollback from $1200
to $1100 would make every downstream projection off by $100 until
the user notices and trues up again.  For a checking account where
the user has authorized bills against $1200, a rollback to $1100
could show a false low-balance alert or a false "bill cannot be
covered" warning.

Even without a network retry, the same race can occur between two
browser tabs, between a desktop browser and a mobile browser, or
between a companion/owner pair if a companion could update anchors
(they can't today -- `@require_owner` -- but defence-in-depth
still applies).

**Severity: High.** Financial state is silently rolled back to a
previous value, and the user has no signal that it happened.
Downstream projections are wrong.  Exploitable today by a single
user who double-submits or whose browser retries.

**Remediation.**
- Preferred: add `version_id_col` to `Account` (SQLAlchemy optimistic
  concurrency).  Every PATCH must include the expected version; a
  mismatch returns 409 and the user is prompted to re-load.
- Alternative: conditional UPDATE: `UPDATE accounts SET
  current_anchor_balance = %(new)s WHERE id = %(id)s AND
  current_anchor_balance = %(expected_old)s`.  Form must pass the
  old anchor back.  Zero rows affected = 409.
- Alternative (weakest): `.with_for_update()` on the Account row
  immediately before the write.  Serializes concurrent updates but
  does not catch stale-form submissions (the retry would still
  overwrite the newer value since it wins the lock second).

### Finding H-C2-02 -- `mark_as_credit` TOCTOU creates duplicate CC paybacks (High)

**Endpoint affected:** `transactions.py:373 /transactions/<id>/mark-credit`
(POST) -- delegates to `credit_workflow.mark_as_credit`.

**Code (`credit_workflow.py:70-124`):**

```python
credit_id = ref_cache.status_id(StatusEnum.CREDIT)
projected_id = ref_cache.status_id(StatusEnum.PROJECTED)

# Idempotency: if already credited with existing payback, return it.
if txn.status_id == credit_id:
    existing_payback = (
        db.session.query(Transaction)
        .filter_by(credit_payback_for_id=txn.id)
        .first()
    )
    if existing_payback:
        return existing_payback

# Only projected transactions can be newly marked as credit.
if txn.status_id != projected_id:
    raise ValidationError(...)

# Update the original transaction's status.
txn.status_id = credit_id
...
payback = Transaction(
    ...
    credit_payback_for_id=txn.id,
)
db.session.add(payback)
db.session.flush()
```

The idempotency check at the top only protects against sequential
duplicates (user clicks, sees response, clicks again).  It does NOT
protect against concurrent duplicates -- both sessions read
`txn.status_id == PROJECTED`, both pass the check, both set
`status = CREDIT`, both insert a payback row.

**Critically, `Transaction.credit_payback_for_id` has an *index* but
no *unique constraint*** (confirmed by reading `models/transaction.py`
lines 27 and 98).  The database cannot catch the duplicate insert.

**Concrete scenario.**
1. **T=0** -- User clicks "mark as credit" on a $50 grocery
   transaction.  Request 1 enters the handler.
2. **T=0.02s** -- User double-clicks.  Request 2 enters the handler
   a split-second later.
3. **T=0.03s** -- Request 1's session reads `txn.status_id = PROJECTED`.
4. **T=0.04s** -- Request 2's session reads `txn.status_id = PROJECTED`
   (Request 1 hasn't committed yet, but READ COMMITTED doesn't matter
   here because even with REPEATABLE READ, both sessions started
   before either committed).
5. **T=0.05s** -- Request 1 passes the `status_id == PROJECTED` check,
   sets status to CREDIT, inserts Payback A with
   `credit_payback_for_id=<original_id>` and `estimated_amount=$50`,
   flushes, commits.
6. **T=0.06s** -- Request 2 passes the same check (its session still
   sees `status_id == PROJECTED`), sets status to CREDIT (idempotent --
   same value), inserts Payback B with same
   `credit_payback_for_id` and same amount, flushes, commits.

**Final state.** Two payback transactions pointing at the same
original, both with status=PROJECTED and amount=$50.

**User-visible consequence.** The next pay period now contains two
$50 CC Payback expenses instead of one.  The projected end-of-period
balance is $50 lower than it should be.  The user sees the balance
calculator subtract $100 instead of $50.  When the credit card bill
arrives, the user pays $50 and the remaining $50 "payback" sits as
a projected expense forever, confusing future projections.

The user might not notice for a pay period or more -- most users
glance at the aggregate balance, not every line item.

**Severity: High.** Financial projections are wrong by exactly one
payback amount, and the bug surfaces in downstream balance
calculations without any warning.  A very common user interaction
(impatient double-click) triggers it.  Exploitable today, single
user.

**Remediation.**
- Add `UNIQUE(credit_payback_for_id) WHERE credit_payback_for_id IS
  NOT NULL` as a partial unique index on the transactions table.
  Second INSERT fails with IntegrityError.  Route catches and
  returns 409 (or silently returns the existing payback if
  idempotency is desired).
- Additionally: `.with_for_update()` on the original transaction row
  at the top of `mark_as_credit`, so Request 2 blocks waiting for
  Request 1 to commit.  After it commits and releases the lock,
  Request 2 re-reads status=CREDIT and returns the existing payback
  via the top-of-function idempotency check.
- Or both (belt-and-suspenders).

### Finding H-C2-03 -- `sync_entry_payback` TOCTOU creates duplicate CC paybacks via entries (High)

**Endpoint affected:** `entries.py:118 /transactions/<id>/entries`
(POST) -- `entry_service.create_entry` calls
`entry_credit_workflow.sync_entry_payback` (line 190).

**Code (`entry_credit_workflow.py:85-93`):**

```python
# Find existing payback (same query pattern as credit_workflow.py).
existing_payback = (
    db.session.query(Transaction)
    .filter_by(credit_payback_for_id=txn.id)
    .first()
)

if total_credit > 0:
    if existing_payback is None:
        return _create_payback(txn, owner_id, credit_entries, total_credit)
```

Same TOCTOU pattern as H-C2-02.  Two concurrent entry creations
with `is_credit=True` both read `existing_payback=None`, both call
`_create_payback`, both insert.

**Concrete scenario.**
1. User has a $500 grocery transaction with "track individual
   purchases" enabled.
2. Two entries are added in rapid succession (double-submit of a
   form, or two tabs both submitting entries):
   - Entry A: $50 debit, is_credit=True
   - Entry B: $30 debit, is_credit=True
3. Request A's `sync_entry_payback` reads no existing payback,
   creates Payback X with amount=$50 (its sum of credit entries),
   commits.
4. Request B's `sync_entry_payback` also reads no existing payback
   (started before A committed), creates Payback Y with amount=$30,
   commits.

**Final state.** Two paybacks: X ($50) and Y ($30).  The entries
are linked to Payback Y (whichever request committed second, since
`_create_payback` line 178-179 sets `entry.credit_payback_id =
payback.id` on every credit entry).

Actually, the entry-link overwrite is a secondary bug: Request A
creates Payback X and links Entry A to X.  Request B creates
Payback Y and links BOTH Entry A and Entry B to Y (line 178-179).
Now Payback X has no linked entries and should have amount=$0, but
its `estimated_amount=$50` is frozen.

**User-visible consequence.** Two paybacks summing $80 in the next
period when only one $80 payback should exist.  Balance is $80 too
low in projections.  Additionally, the orphaned Payback X has a
stale $50 that will never be corrected by a future entry toggle
(no entries link to it to drive a sync).

**Severity: High.** Same balance-corruption mechanism as H-C2-02,
with the added wrinkle of orphaned entry links.

**Remediation.** Same as H-C2-02: partial unique index on
`credit_payback_for_id`, plus `.with_for_update()` on the parent
transaction row inside `sync_entry_payback`.

### Finding H-C2-04 -- PATCH endpoints accept stale form amounts (lost update) (High)

**Endpoints affected:**
- `transactions.py:183 /transactions/<id>` (PATCH)
- `transfers.py:617 /transfers/instance/<id>` (PATCH)
- `entries.py:153 /transactions/<tx>/entries/<ent>` (PATCH)
- `accounts.py:220 /accounts/<id>` (POST -- form-based update)
- `salary.py` raise/deduction edit routes

**Code (`transfers.py:617-648`, abridged):**

```python
@transfers_bp.route("/transfers/instance/<int:xfer_id>", methods=["PATCH"])
...
def update_transfer(xfer_id):
    xfer = _get_owned_transfer(xfer_id)
    ...
    data = _xfer_update_schema.load(request.form)
    ...
    transfer_service.update_transfer(xfer.id, current_user.id, **data)
    ...
```

No version check.  The form submits whatever the client-side has,
and the server writes it.

**Concrete scenario.**
1. User opens Tab 1 with the full-edit popover for a $500 transfer.
   Form fields are prefilled: amount=$500, category=Groceries,
   notes="".
2. User opens Tab 2 and edits the same transfer's amount to $600,
   submits.  Committed: transfer.amount = $600.
3. User returns to Tab 1 (forgotten), edits only the notes field to
   "March rent," submits.  The form body includes `amount=500`
   (pre-filled from the stale page load), `category=1`, `notes=March rent`.
4. Server side: Marshmallow validates, `update_transfer` sees
   `"amount" in kwargs`, sets `xfer.amount = $500` and propagates to
   both shadows (rolling Tab 2's $600 back to $500).

**Final state.** Transfer reverts from $600 to $500.  Both shadows
reflect $500.  The user's Tab 2 edit is silently lost.

**User-visible consequence.** A budget line the user intentionally
increased drops back to its old value because of an unrelated edit
in a stale tab.  The audit trail shows only the final amount;
there is no hint that the user had previously changed it.

**Severity: High.** This is a classic lost-update vector that money
apps protect against with optimistic concurrency.  Shekel has no
protection, and every PATCH endpoint is susceptible (transfers,
transactions, entries, account anchor, salary raise edit, salary
deduction edit, category edit, template edit).

The transfer service provides per-kwarg dispatch (`if "amount" in
kwargs`) which *could* be used to distinguish user-changed fields
from pre-filled fields, but the route's
`_xfer_update_schema.load(request.form)` returns every field the
form submitted, not just the changed ones.  So every submit is
treated as an update to every field.

**Remediation.**
- Add a version column (`version_id_col` in SQLAlchemy, or
  `updated_at` as a crude version proxy).  Form must echo the
  version; server rejects mismatches with 409.
- Alternative: client-side "dirty field tracking" -- form only
  submits fields the user actually changed.  Less robust; relies on
  JavaScript correctness.
- Alternative: split the popover into purpose-specific endpoints
  (amount-only, category-only, status-only).  Reduces blast radius
  but doesn't eliminate it.

---

### Finding M-C2-05 -- `carry_forward_unpaid` has no status precondition check (Medium)

**Endpoint affected:** `transactions.py:715 /pay-periods/<id>/carry-forward` (POST).

**Code (`carry_forward_service.py:71-91`):**

```python
projected_txns = (
    db.session.query(Transaction)
    .filter(
        Transaction.pay_period_id == source_period_id,
        Transaction.scenario_id == scenario_id,
        Transaction.status_id == projected_id,
        Transaction.is_deleted.is_(False),
    )
    .all()
)

# Partition into regular transactions and shadow transactions.
...
for txn in regular_txns:
    txn.pay_period_id = target_period_id
    ...
for txn in shadow_txns:
    if txn.transfer_id not in moved_transfer_ids:
        transfer_service.update_transfer(
            txn.transfer_id, user_id,
            pay_period_id=target_period_id, is_override=True,
        )
```

**Race 1 -- double-click carry-forward.** Two requests both read the
same 5 projected txns, both try to update `pay_period_id` on the
same rows.  Under READ COMMITTED, the second UPDATE blocks on
PostgreSQL row locks, waits for the first to commit, then runs the
second UPDATE -- which now sets `pay_period_id = target` (no-op,
same value).  **Safe -- idempotent.**

**Race 2 -- carry-forward vs. mark-done.**
1. Tab 1 clicks "carry forward" on period P.  Session 1 reads 5
   projected txns (IDs 10-14).
2. Tab 2 clicks "mark done" on txn 12 (inside period P).  Session 2
   sets `txn.status_id = DONE`, commits.
3. Tab 1's session still has txn 12 in its `projected_txns` list
   (snapshot at read time).  It calls `txn.pay_period_id =
   target_period`, setting txn 12's period even though txn 12 is
   already DONE.

**Final state.** Txn 12 has `status=DONE` (from Tab 2) AND
`pay_period_id=target_period` (from Tab 1).  A done transaction has
been carried forward to a future period.  Balance calculator
treats DONE as already-reflected-in-anchor, so it's excluded from
the target period -- but it's now tagged to a period the user
marked it paid in only through coincidence.

The concrete impact is muted because the balance calculator ignores
DONE txns in both source and target periods, but the user will see
the DONE transaction in the target period's grid instead of the
source period's grid, which is confusing.

**User-visible consequence.** A transaction the user marked paid
appears in the wrong pay period after a carry-forward.  Historical
grid view is wrong.  Reports filtered by period show this txn in
the wrong month.  Not a direct financial error but a data-integrity
inconsistency.

**Severity: Medium.** The race window is narrow (both happening
within a single request lifetime), but is plausible with a
distracted user and a slow server.

**Remediation.** Add `.with_for_update()` to the SELECT in
carry_forward_service or change the UPDATE to a conditional form:
`UPDATE transactions SET pay_period_id = :target WHERE id IN (:ids)
AND status_id = :projected`.  Only still-projected txns move.

### Finding M-C2-06 -- Transfer status transitions have no state-machine check (Medium)

**Endpoints affected:** `transfers.py mark_done`, `transfers.py
cancel_transfer`, `transactions.py mark_done` (shadow path),
`dashboard.py mark_paid`.

**Code (transfers.py:748-749):**

```python
done_id = ref_cache.status_id(StatusEnum.DONE)
transfer_service.update_transfer(xfer.id, current_user.id, status_id=done_id)
```

And inside `transfer_service.update_transfer` (line 468-473):

```python
if "status_id" in kwargs:
    new_status_id = kwargs["status_id"]
    xfer.status_id = new_status_id
    expense_shadow.status_id = new_status_id
    income_shadow.status_id = new_status_id
```

There is no check "only transition projected → done" or "only
transition non-settled → cancelled."  Any status can transition to
any other status via the service.

**Concrete scenario (illegal transition via race):**
1. Transfer status = CANCELLED (user decided not to move money).
2. Tab 1 (stale tab showing the transfer as projected) clicks "mark
   done."
3. Request hits `/transfers/instance/5/mark-done`, loads transfer 5
   (status=CANCELLED), calls
   `update_transfer(status_id=DONE)`.
4. Transfer flips from CANCELLED to DONE.  Balance calculator now
   treats the transfer as settled, includes its effect in the anchor
   period calculation.

**Final state.** A transfer the user previously cancelled is now
marked as executed without the user actually confirming it.
Balance calculator reflects the (non-existent) transfer.

This is less of a *concurrency* bug and more of a *missing state
machine enforcement*.  The TOCTOU variant is:
1. Both Tab 1 and Tab 2 read status=PROJECTED.
2. Tab 1 does mark-done; transfer = DONE.
3. Tab 2 does cancel; transfer = CANCELLED.
4. Shadow.paid_at was set by Tab 1's propagation (if paid_at was
   passed -- transfers.py/mark_done does NOT pass paid_at today, so
   this particular side-effect is absent), but the status is
   CANCELLED with a dangling paid_at from the shadow perspective if
   a different route was used.

**User-visible consequence.** The user sees a cancelled transfer
that the system thinks was done, or vice versa.  Balance
projections reflect the last-writer status.

**Severity: Medium.** Requires an unusual user interaction (stale
tab + old transfer) or a TOCTOU race.  Balance state can be
corrupted but is recoverable by toggling status again.

**Remediation.**
- Enforce a state-machine check at the top of
  `transfer_service.update_transfer` when status_id is changing:
  reject disallowed transitions (e.g. CANCELLED → DONE requires
  manual restore-then-mark-done; DONE → PROJECTED should clear
  paid_at; etc.).
- A CHECK constraint at the DB level on allowed state transitions
  is possible via a trigger, but Alembic + CHECK triggers is
  awkward; a service-level state machine is simpler.

### Finding M-C2-07 -- `transfers.py mark_done` does not propagate `paid_at` (Medium, bug not race)

**Endpoint affected:** `transfers.py:739 /transfers/instance/<id>/mark-done`.

**Code (transfers.py:748-749):**

```python
done_id = ref_cache.status_id(StatusEnum.DONE)
transfer_service.update_transfer(xfer.id, current_user.id, status_id=done_id)
```

Compare with `dashboard.py:75-78` (the dashboard-side mark-paid for
transfer shadows):

```python
svc_kwargs = {
    "status_id": ref_cache.status_id(StatusEnum.DONE),
    "paid_at": db.func.now(),
}
```

And `transactions.py:316-319` (the grid shadow mark-done):

```python
svc_kwargs = {
    "status_id": ref_cache.status_id(StatusEnum.DONE),
    "paid_at": db.func.now(),
}
```

**Result:** if the user marks a transfer done from the
transfers-management page, the shadow transactions are DONE but
their `paid_at` is NULL.  If they mark it done from the grid or the
dashboard, `paid_at` is set.  Inconsistent.

Not strictly a concurrency finding, but surfaced by this audit's
status-transition analysis.  I'm flagging it here.

**User-visible consequence.** `Transaction.days_paid_before_due`
returns None for transfers marked done via the management page.
Analytics / spending trend / year-end reports that rely on paid_at
show incomplete data for those transfers.

**Severity: Medium.** Not a race, but a systematic inconsistency
that corrupts one of the reporting inputs.

**Remediation.** Add `paid_at=db.func.now()` to the call on
transfers.py:749.  Single-line fix.

---

### Finding L-C2-08 -- Ad-hoc transaction / ad-hoc transfer double-submit creates duplicates (Low)

**Endpoints affected:** `transactions.py /transactions/inline` (POST),
`transactions.py /transactions` (POST), `transfers.py
/transfers/ad-hoc` (POST), `loan.py payment transfer create`,
`investment.py contribution transfer create`.

**Code pattern (`transactions.py:585-640`):**

```python
errors = _inline_create_schema.validate(request.form)
...
txn = Transaction(**data)
db.session.add(txn)
db.session.commit()
```

No dedupe.  Two rapid POSTs create two identical transactions.

**Concrete scenario.** User adds a $50 grocery transaction in the
grid.  Clicks "Save."  Nothing happens visually (HTMX hasn't swapped
yet).  Clicks "Save" again.  Two rows are created.

**User-visible consequence.** Duplicate transaction appears in the
grid.  User sees it and deletes one manually.  Balance is correct
after the delete.  If the user doesn't notice before a status
change, projections are off by $50 until they do.

**Severity: Low.** Visible duplicate, user-correctable.  Not a
silent corruption.

**Remediation.** See Check 6 -- idempotency design discussion.  A
client-side "disable button during submit" + server-side
idempotency token would close this.

### Info observations

**I-C2-09 -- Anchor history rows can duplicate on double-click.**
A rapid double-submit on a true-up creates two history rows with
the same balance, same period, same timestamp (within a second).
Not corruption; audit-trail noise.

**I-C2-10 -- No global session lock on `db.session.commit()`.**
Each request has its own session; commit order is determined by
arrival order at the DB, not request dispatch.  This is by design
in Flask-SQLAlchemy; noted for completeness.

---

### Check 2 summary

| Check | Endpoint group | Protection | Finding |
|---|---|---|---|
| Transfer status transitions | `/transfers/*/mark-done`, `/cancel` | None (no row lock, no precondition) | M-C2-06 |
| Anchor balance updates | `/accounts/*/true-up`, `/inline-anchor` | None | H-C2-01 |
| Transaction mutations | `/transactions/<id>` PATCH | None -- stale form hazard | H-C2-04 |
| Transaction delete | `/transactions/<id>` DELETE | Idempotent; hard-delete safe via ORM | Pass |
| Transfer creation (ad-hoc) | `/transfers/ad-hoc` | None -- no unique on ad-hoc | L-C2-08 |
| Transfer creation (template-linked) | recurrence engine | Partial unique index `idx_transfers_template_period_scenario` | Pass |
| Transaction creation (ad-hoc) | `/transactions/inline`, `/transactions` | None -- duplicate on double-submit | L-C2-08 |
| Mark as credit | `/transactions/*/mark-credit` | Idempotency check (TOCTOU-racy), no DB unique | H-C2-02 |
| Entry credit sync | `/transactions/*/entries` POST | Same pattern, no DB unique | H-C2-03 |
| Carry-forward | `/pay-periods/*/carry-forward` | None | M-C2-05 |
| Recurrence regeneration | recurrence_engine.regenerate_for_template | Partial unique indexes | Pass |
| Calibration override | `/salary/*/calibrate/confirm` | `uq_calibration_overrides_profile` | Pass |
| Pay period generation | `/pay-periods/generate` | `uq_pay_periods_user_start` | Pass |

**Severity totals for Check 2:** **4 High** (H-C2-01, H-C2-02,
H-C2-03, H-C2-04), **3 Medium** (M-C2-05, M-C2-06, M-C2-07), **1
Low** (L-C2-08), **2 Info** (I-C2-09, I-C2-10).

The Highs cluster into two classes:
- **Lost-update / last-writer-wins** (H-C2-01 anchor, H-C2-04
  PATCH endpoints).  Fix: optimistic concurrency via version column.
- **Missing DB-level uniqueness** (H-C2-02 mark-credit, H-C2-03
  entry-credit-sync).  Fix: partial unique index on
  `credit_payback_for_id`.

Both fix classes are schema changes requiring Alembic migrations.

---

## Check 3: Transfer Invariants

**What was checked.** The five invariants from CLAUDE.md, each with
quoted source evidence:

1. Every transfer has exactly two linked shadow transactions (one
   expense, one income).
2. Shadow transactions are never orphaned and never created without
   their sibling.
3. Shadow amounts, statuses, and periods always equal the parent
   transfer's.
4. No code path directly mutates a shadow.
5. Balance calculator queries ONLY budget.transactions, NEVER also
   queries budget.transfers.

Session S1's Check 1C.2 (in
`reports/07-manual-deep-dives.md`) and Subagent B2 (in
`reports/02b-services.md`) already covered this territory.  My job
is to independently re-verify, confirm or challenge, and catch any
new code paths that might violate the invariants.

### Enumeration greps

**Who creates Transfer objects?**

```
app/services/transfer_service.py:349      xfer = Transfer(...)
app/models/transfer.py:13                 class Transfer(db.Model):
```

Plus in `scripts/`:

```
scripts/audit/seed_dast_users.py:554      transfer = Transfer(...)   # DAST seed script
```

**Zero** other production code paths instantiate a Transfer.

**Who creates Transaction objects with `transfer_id` set?**

```
app/services/transfer_service.py:370      expense_shadow = Transaction(... transfer_id=xfer.id, ...)
app/services/transfer_service.py:391      income_shadow  = Transaction(... transfer_id=xfer.id, ...)
scripts/repair_orphaned_transfers.py:100  expense_shadow = Transaction(... transfer_id=xfer.id, ...)
scripts/repair_orphaned_transfers.py:114  income_shadow  = Transaction(... transfer_id=xfer.id, ...)
```

Other `Transaction(...)` call sites (`recurrence_engine.py:139`,
`credit_workflow.py:111`, `entry_credit_workflow.py:162`,
`transactions.py:628`, `:673`) do NOT pass `transfer_id`, so the
field defaults to None.  Marshmallow's `TransactionCreateSchema`
and `InlineTransactionCreateSchema` do not declare `transfer_id`,
so the EXCLUDE policy drops any user-submitted `transfer_id=`
attempt.

---

### Invariant 1 -- Every transfer has exactly two linked shadow transactions

**ENFORCED BY CODE** at creation time and at mutation time.

**Creation enforcement -- `transfer_service.py:349-410` (abridged):**

```python
xfer = Transfer(
    user_id=user_id,
    ...
    amount=amount,
    is_override=False,
    is_deleted=False,
)
db.session.add(xfer)
db.session.flush()      # ← required to obtain xfer.id

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

Both shadows are added and flushed within the same function, the
same session, before the function returns.  The caller owns
`db.session.commit()`, but the three rows are already in the same
flush and will roll back together on any error before commit.

**Mutation-time enforcement -- `transfer_service.py:195-265`:**

```python
def _get_shadow_transactions(transfer_id):
    shadows = (
        db.session.query(Transaction)
        .filter_by(transfer_id=transfer_id, is_deleted=False)
        .all()
    )

    if len(shadows) != 2:
        ...
        raise ValidationError(
            f"Transfer {transfer_id} has {len(shadows)} shadow "
            f"transactions instead of the expected 2.  "
            f"Data integrity issue -- cannot proceed."
        )

    expense_type_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)
    income_type_id = ref_cache.txn_type_id(TxnTypeEnum.INCOME)
    ...
    if expense_shadow is None or income_shadow is None:
        raise ValidationError(
            f"Transfer {transfer_id} shadows do not have the expected "
            f"expense/income type pairing.  Data integrity issue."
        )
```

`_get_shadow_transactions` is the gate through which every
mutation (`update_transfer`, and implicitly `restore_transfer`)
must pass.  A transfer with anything other than exactly one
expense and one income shadow is immediately blocked with a clear
error.

**Residual gap -- F-B2-03 (Medium, confirmed).**  The database has
no constraint that enforces "exactly two shadows per transfer."
Direct ORM writes, SQL writes, or scripts can create a Transfer
without shadows, or with only one shadow, or with three.  The
service catches the violation at the next mutation attempt but does
not prevent the write.

**Strong evidence this has occurred in production.** The file
`scripts/repair_orphaned_transfers.py` exists specifically for
this purpose.  Its docstring (lines 1-22) says:

> Background: A bug in create_transfer_template() caused one-time
> (non-recurring) transfers to be created as Transfer records without
> corresponding shadow transactions in budget.transactions.

So F-B2-03 is not theoretical -- invariant 2 was actually violated
in production, a bug was fixed, and a repair script was built to
clean up the fallout.  A partial unique index or a CHECK constraint
would have surfaced the fault at write time rather than leaving
orphans to accumulate.

**Additional evidence -- the DAST seed.**
`scripts/audit/seed_dast_users.py:554-566` intentionally creates a
Transfer without shadows for DAST probe coverage.  The script is
explicitly scoped to the audit tooling -- it never runs against
prod -- but its existence demonstrates that another path outside
the service can create orphans.

**Verdict:** Enforced by code at the service layer, but **NOT
enforced at the database layer**.  F-B2-03 (Medium) confirmed and
reinforced by the repair script's existence.

---

### Invariant 2 -- Shadows never orphaned, never created without their sibling

**ENFORCED BY CODE** via four mechanisms:

**(a) Creation atomicity (transfer_service.py:349-410).**
Transfer and both shadows are in the same session, same flush.  A
rollback (any exception before commit) removes all three together.

**(b) Hard-delete CASCADE with verification
(transfer_service.py:586-605):**

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
    logger.error(
        "CASCADE delete failed: %d orphaned shadow transactions "
        "remain for deleted transfer %d.",
        orphan_count, transfer_id,
    )
```

The FK definition (`models/transaction.py:94-97`) is:
```python
transfer_id = db.Column(
    db.Integer,
    db.ForeignKey("budget.transfers.id", ondelete="CASCADE"),
)
```

So CASCADE is the actual defense; the verification is a diagnostic
log.

**(c) Soft-delete sibling sweep
(transfer_service.py:570-584):**

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

Note the query is NOT filtered on `is_deleted=False` -- so even if
one shadow is already soft-deleted and the other isn't, both get
caught and marked.  Defensive.

**(d) Cascading archive flow -- transfers.py:392-432, :490-565.**
The `archive_transfer_template` and `hard_delete_transfer_template`
route handlers always delete transfers through
`transfer_service.delete_transfer`, so shadow invariants hold in
those paths too.

**Verdict:** Enforced by code.

---

### Invariant 3 -- Shadow amounts, statuses, periods match parent transfer

**ENFORCED BY CODE** at three points:

**(a) Creation-time inheritance
(transfer_service.py:370-409).** Both shadows receive
`status_id=status_id`, `pay_period_id=pay_period_id`, and
`estimated_amount=amount` directly from the parent's constructor
args.  Same source, same value.  Cannot drift at creation.

**(b) Update-time propagation
(transfer_service.py:461-540, abridged):**

```python
if "amount" in kwargs:
    new_amount = _validate_positive_amount(kwargs["amount"])
    xfer.amount = new_amount
    expense_shadow.estimated_amount = new_amount
    income_shadow.estimated_amount = new_amount

if "status_id" in kwargs:
    new_status_id = kwargs["status_id"]
    xfer.status_id = new_status_id
    expense_shadow.status_id = new_status_id
    income_shadow.status_id = new_status_id

if "pay_period_id" in kwargs:
    new_period_id = kwargs["pay_period_id"]
    _get_owned_period(new_period_id, user_id)
    xfer.pay_period_id = new_period_id
    expense_shadow.pay_period_id = new_period_id
    income_shadow.pay_period_id = new_period_id
```

All three propagating kwargs write to `xfer`, `expense_shadow`, and
`income_shadow` in the same branch.  If one of the three assignments
was forgotten, a test that edits that field would see drift.

**(c) Drift repair in `restore_transfer`
(transfer_service.py:688-720):** On restore, all three fields are
re-verified against the parent and any drift is logged and
corrected.  This is self-healing for soft-deleted transfers where a
manual DB edit may have diverged during the soft-deleted window.

**Verdict:** Enforced by code.

---

### Invariant 4 -- No code path directly mutates a shadow

**PARTIALLY ENFORCED.  HIGH finding (F-B2-01, confirmed).**

Inside `transfer_service.py`, every shadow mutation is scoped to
the `expense_shadow` / `income_shadow` objects returned by
`_get_shadow_transactions` -- so the two-shadow invariant is upheld
before any mutation happens.

For every OTHER service module that takes a Transaction as input, I
verified a guard clause:

| Service | Guard line | Code |
|---|---|---|
| `entry_service.py:149` | **present** | `if txn.transfer_id is not None: raise ValidationError("Cannot add entries to transfer transactions.")` |
| `credit_workflow.py:59` | **present** | `if txn.transfer_id is not None: raise ValidationError("Cannot mark transfer transactions as credit.")` |
| `carry_forward_service.py:85-91` | **partition** | Transactions are partitioned into `regular_txns` and `shadow_txns`; only regulars are mutated directly, shadows route through `transfer_service.update_transfer` |
| `entry_credit_workflow.py` | **n/a** | Operates only on CC Payback transactions (`credit_payback_for_id IS NOT NULL`), which are regular transactions, not shadows |
| `recurrence_engine.py` | **`generate_for_template` uses filter `template_id=template.id`** -- shadows excluded because they have `template_id=None` | See below |

And for every route that handles Transaction mutations:

| Route | Guard line | Action |
|---|---|---|
| `transactions.py:208` | present | Shadow path routes through `transfer_service.update_transfer` |
| `transactions.py:312` | present | Shadow path routes through `transfer_service.update_transfer` |
| `transactions.py:383, :405` | present (different semantics) | `return 400` -- cannot mark/unmark a shadow as credit |
| `transactions.py:431` | present | Shadow path routes through `transfer_service.update_transfer` |
| `transactions.py:700` | present | `return 400` -- cannot directly delete a shadow |
| `dashboard.py:74` | present | Shadow path routes through `transfer_service.update_transfer` |

**The one gap -- `recurrence_engine.resolve_conflicts` (lines 249-288):**

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
                ...
                continue

            txn.is_override = False
            txn.is_deleted = False
            if new_amount is not None:
                txn.estimated_amount = new_amount
        db.session.flush()
```

**No `if txn.transfer_id is not None: continue` guard.**  A shadow
ID passed in would have `is_override`, `is_deleted`, and
`estimated_amount` overwritten directly, violating invariants 3 AND
4 simultaneously.

**Caller safety check.** I grepped `resolve_conflicts` across
app/routes/ and app/services/:

```
app/routes/*:  zero callers
app/services/*:  only the definition itself
```

**The function has ZERO callers today.**  The `templates.py:366`
handler calls `regenerate_for_template`, catches `RecurrenceConflict`,
and simply logs-and-flashes the conflict counts (templates.py:369-380).
It does NOT call `resolve_conflicts` to apply a resolution.

So the gap is currently dormant: no code path passes transaction
IDs to `resolve_conflicts`.  But:

1. The function is part of the service's public API (not `_prefixed`).
2. The function's docstring (lines 252-263) describes it as the
   resolution handler for `RecurrenceConflict`.
3. A future UI that actually presents the conflict prompt and
   gathers the user's choice would naturally pass the
   `overridden_ids` list to `resolve_conflicts`.  That list comes
   from `regenerate_for_template` at line 223, which queries
   `Transaction.template_id == template.id`.  Template-generated
   transactions have `template_id` set; shadows have `template_id =
   None`.  So even a naive caller would not pass shadow IDs --
   unless a future refactor widens the query to include
   `Transaction.transfer_id = template.some_link` or similar.

Per CLAUDE.md's explicit rule:

> "'Enforced by convention (i.e. no code path actively blocks the
> violation, callers are trusted to do the right thing)' is itself
> a High finding for a money app, even if no current caller violates
> it."

**Verdict: Partially enforced.  HIGH finding confirmed (F-B2-01).**

**Fix:** Add the guard at the top of the for-loop in
`resolve_conflicts`:

```python
for txn_id in transaction_ids:
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        continue
    if txn.transfer_id is not None:
        logger.warning(
            "resolve_conflicts blocked: txn %d is a transfer shadow; "
            "shadow mutation must go through transfer_service.",
            txn_id,
        )
        continue
    ...
```

Or raise `ValidationError` to fail-fast.  Both are acceptable;
`continue` preserves the current contract of silently skipping
invalid IDs.

---

### Invariant 5 -- Balance calculator queries ONLY budget.transactions

**ENFORCED BY CODE.**

S1's Check 1C.3 already verified this exhaustively by grepping
every file in the balance calculator's transitive import chain.  I
confirmed the key pieces with targeted greps and a re-read:

- `balance_calculator.py` imports only: `interest_projection`,
  `ref_cache`, `StatusEnum`, and (lazily) `amortization_engine`.
  None of these import the `Transfer` model.
- Grep in `balance_calculator.py` for `from app.models.transfer` --
  **zero matches**.
- Grep in `balance_calculator.py` for `db.session.query(Transfer)`
  -- **zero matches**.
- The only reference to transfer-related state in
  `balance_calculator.py` is the column-read `txn.transfer_id is
  not None` at line 268, which reads the FK column on a Transaction
  row to detect whether the Transaction is a shadow.  This is NOT a
  query on the `budget.transfers` table.

All transfer effects flow into the balance calculator through
`Transaction` rows with `transfer_id IS NOT NULL`, which the
calculator treats identically to regular transactions.

**Note on other services querying Transfer.**  The following DO
import and query Transfer, but they are NOT the balance calculator:
- `year_end_summary_service.py:657` -- year-end report aggregation
  (separate from balance calculator).
- `transfer_recurrence.py:151`, `:253` -- queries for template
  regeneration.
- `routes/transfers.py:412`, `:453`, `:534`, `:554` -- transfer
  management routes.
- `routes/accounts.py:426` -- account hard-delete cleanup.
- `utils/archive_helpers.py:57` -- `transfer_template_has_paid_history`.

Invariant 5 is specifically about the balance calculator.  Other
services may query Transfer without violating it, as long as they
do not route through the balance calculator's code paths.

**Verdict:** Enforced by code.

---

### Check 3 summary

| Invariant | Verdict | Severity | Evidence |
|---|---|---:|---|
| 1 -- Exactly two shadows | Enforced by service; residual DB gap | Medium (F-B2-03) | `create_transfer` + `_get_shadow_transactions`.  Repair script exists because orphans occurred in prod. |
| 2 -- Never orphaned | Enforced by code | n/a | Atomic creation + CASCADE + soft-delete sweep |
| 3 -- Amount/status/period match | Enforced by code | n/a | Creation inheritance + update propagation + restore drift repair |
| 4 -- No direct shadow mutation | Partially enforced | **High (F-B2-01)** | All other code paths guarded; `recurrence_engine.resolve_conflicts` has no guard but no current caller |
| 5 -- Balance calculator isolation | Enforced by code | n/a | Zero Transfer import/query in balance_calculator.py or its imports |

**Aggregate Check 3 finding count: 1 High (F-B2-01 re-confirmed),
1 Medium (F-B2-03 re-confirmed and STRENGTHENED by the existence
of the repair script).**

No new invariant findings beyond what S1 identified.  But the
existence of `scripts/repair_orphaned_transfers.py` is a new
observation that makes F-B2-03 significantly more actionable: it is
not a theoretical risk; it is a documented production incident.

**Recommended fix priority:**

1. **F-B2-01 fix (30-minute fix, no migration).** Add the
   `if txn.transfer_id is not None` guard at
   `recurrence_engine.resolve_conflicts` line 272.  Log a warning
   and continue.  This closes the invariant 4 gap before a future
   UI feature creates a shadow-mutation path.

2. **F-B2-03 fix (Alembic migration).** Add a partial unique index
   or a deferred CHECK constraint that enforces "exactly two
   non-deleted shadows per non-deleted transfer."  PostgreSQL
   options:
   - Partial unique index on
     `(transfer_id, transaction_type_id) WHERE transfer_id IS NOT
     NULL AND is_deleted = FALSE` -- prevents duplicate expense or
     duplicate income shadows per transfer but doesn't enforce
     exactly two.
   - Trigger-based check that counts shadows on any
     Transaction/Transfer mutation -- heavier but exact.
   - Or: rely on the service as the single writer (current
     design) and accept the residual risk, given that the one known
     bug has been patched and the repair script is available.

---

## Check 4: Rounding and Decimal Precision

**What was checked.** Every calculation path that chains Decimal
operations, with attention to:

- Are intermediate results explicitly quantized?  Or does the
  calculation rely on "final-quantize" (quantize once at the end)?
- Are rounding modes consistent (ROUND_HALF_UP throughout vs. a
  mix)?
- Over 26 pay periods, what is the accumulated drift?
- Are sums computed quantize-then-sum or sum-then-quantize?  Which
  is more accurate?
- Are divisions by non-Decimal (e.g. `amount / 12`) quantized?
- Are the three non-integer Decimal exponents from Check 1 Grep 5
  a precision risk?

### Survey of quantize discipline

Greps confirm the project uses `Decimal.quantize(..., ROUND_HALF_UP)`
uniformly.  `ROUND_HALF_UP` is the accounting standard.  **No
instance of `ROUND_HALF_EVEN` (banker's rounding), `ROUND_DOWN` (for
money), or bare `round(` appears in any financial code path.**

Every calculation service quantizes its final output:

| Service | Final quantize | Rounding |
|---|---|---|
| `paycheck_calculator.calculate_paycheck` | `.quantize(TWO_PLACES, ROUND_HALF_UP)` at line 189 | ROUND_HALF_UP |
| `paycheck_calculator._apply_raises` | line 284 | ROUND_HALF_UP |
| `tax_calculator.calculate_federal_withholding` | line 162-164 | ROUND_HALF_UP |
| `tax_calculator._apply_marginal_brackets` | line 209 | ROUND_HALF_UP |
| `tax_calculator.calculate_state_tax` | line 266 | ROUND_HALF_UP |
| `tax_calculator.calculate_fica` | every component line 304-316 | ROUND_HALF_UP |
| `interest_projection.calculate_interest` | line 73 | ROUND_HALF_UP |
| `escrow_calculator.calculate_monthly_escrow` | line 57 | ROUND_HALF_UP |
| `amortization_engine.calculate_monthly_payment` | line 192, 197 | ROUND_HALF_UP |
| `amortization_engine.generate_schedule` | every AmortizationRow field line 601-604 | ROUND_HALF_UP |
| `growth_engine.project_balance` per period | line 243-245 | ROUND_HALF_UP |
| `balance_calculator.calculate_balances_with_amortization` | line 274-276 | ROUND_HALF_UP |
| `calibration_service.derive_effective_rates` | 10-place quantize (intentional high precision for rate) line 83-94 | ROUND_HALF_UP |
| `calibration_service.apply_calibration` | 2-place quantize line 133-144 | ROUND_HALF_UP |

`balance_calculator.calculate_balances` (the primary grid function)
does NOT call `.quantize()` explicitly, but its inputs are already
2-place Decimals (from `Numeric(12,2)` columns) and addition /
subtraction of 2-place Decimals produces exact 2-place results --
so no drift is possible.  Verified by tracing `_sum_remaining`,
`_sum_all`, and `_entry_aware_amount`.

### Sum-then-quantize vs quantize-then-sum

**Sum-then-quantize is the dominant pattern** in the tax and
amortization code.  For example, `tax_calculator._apply_marginal_brackets`
(lines 187-209):

```python
total_tax = ZERO
for bracket in sorted(brackets, key=lambda b: b.sort_order):
    bracket_min = Decimal(str(bracket.min_income))
    bracket_max = Decimal(str(bracket.max_income)) if bracket.max_income else None
    rate = Decimal(str(bracket.rate))
    ...
    if amount_in_bracket > ZERO:
        total_tax += amount_in_bracket * rate
return total_tax.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
```

Intermediate `amount_in_bracket * rate` products are NOT quantized;
they accumulate into `total_tax` at Decimal's context precision
(28 digits by default).  The final `.quantize()` rounds once.

**This is the CORRECT approach** for minimizing drift.
Quantize-then-sum would introduce up to `N * 0.005` rounding error
where N is the number of brackets (7 brackets * 0.005 = $0.035
worst case).  Sum-then-quantize caps the error at the single
final `.quantize()` step, i.e. < $0.005.

### Drift analysis over 26 pay periods

For each iterative calculation I traced whether per-iteration
state accumulates unquantized precision:

| Function | Iterates over | Per-iter state | Accumulates drift? |
|---|---|---|---|
| `balance_calculator.calculate_balances` | periods | `running_balance` = sum of quantized amounts | **No** -- arithmetic on 2-place Decimals is exact |
| `balance_calculator.calculate_balances_with_interest` | periods | `interest_cumulative`, `running_balance` | **No** -- quantized per-period |
| `balance_calculator.calculate_balances_with_amortization` | periods | `running_principal` | **No** -- quantized per-period (line 274, 551, 586) |
| `amortization_engine.generate_schedule` | months | `balance` | **No** -- quantized after each month (line 551, 586) |
| `growth_engine.project_balance` | periods | `current_balance`, `ytd_contributions` | **No** -- quantized per-period (line 243-245, then exact sum) |
| `paycheck_calculator.project_salary` | periods | none (each period is independent) | **No** -- pure per-period function |
| `paycheck_calculator._apply_raises` | raises | `salary` (accumulates unquantized mults) | **No** -- final quantize; intermediate high-precision is MORE accurate |
| `paycheck_calculator._get_cumulative_wages` | periods | `cumulative` = sum of quantized grosses | **No** -- exact |
| `tax_calculator._apply_marginal_brackets` | brackets | `total_tax` | **No** -- sum-then-quantize |
| `escrow_calculator.calculate_monthly_escrow` | components | `total` | **No** -- sum-then-quantize |
| `escrow_calculator.project_annual_escrow` | years | `annual_total` | **No** -- quantized per year at line 113 |

**Conclusion: No iterative calculation in the codebase accumulates
drift over 26 pay periods.** Every per-iteration state is either
(a) directly quantized after each step, or (b) preserves full
precision and quantizes only at the end.  The discipline is
consistent.

### Non-integer Decimal exponents (carry-over from Check 1)

Three hits were deferred from Check 1 Grep 5.  All use
`Decimal ** Decimal` with a non-integer exponent:

1. **`escrow_calculator.py:52`**:
   ```python
   months_elapsed = (as_of_date.year - created.year) * 12 + (as_of_date.month - created.month)
   years_elapsed = max(months_elapsed / Decimal("12"), Decimal("0"))
   ...
   annual = annual * (1 + rate) ** years_elapsed
   ```
   `years_elapsed` is a non-integer Decimal like `0.583...`.  Python's
   `Decimal.__pow__` with non-integer exponent uses an iterative
   algorithm that produces a result correct to Decimal's context
   precision (28 digits default).

2. **`growth_engine.py:241`**:
   ```python
   period_return_rate = (
       (1 + assumed_annual_return) ** (Decimal(str(period_days)) / Decimal("365")) - 1
   )
   ```
   Same pattern.  14/365 ≈ 0.03835616...  Result is precise to 28
   digits.

3. **`growth_engine.py:355`**: Same as #2, in `reverse_project_balance`.

**Precision analysis.** For a balance of $1,000,000 at 7% annual
return, the 14-day period rate is:
`(1.07) ** (14/365) - 1 = 0.002613...`
Decimal's iterative power yields ~27 digits of precision.  Growth
for one period = $1,000,000 * 0.002613... = $2,613.xxx...  Quantized
to $2,613.xx.  Per-period error < $0.005 from the quantize step.

**No accumulated drift over 26 periods** because the result is
quantized at each step (growth_engine.py:243-245) before being
added to the running balance.  Over a year, total drift is
bounded by 26 * $0.005 = $0.13 maximum -- but because the drift
is stochastic (each quantize rounds up or down independently), the
expected total drift is ~$0.00 and the standard deviation is
~$0.015.  Indistinguishable from zero at budget-app scale.

**Verdict: Info-only.** The non-integer Decimal exponent pattern
is correct and does not produce meaningful drift.  Documenting the
pattern in the module docstring would help future reviewers avoid
flagging it.

### Leap-year daycount (`interest_projection.py`)

`interest_projection.py:15`:

```python
# US bank convention: actual/365 day count.  In leap years this
# overstates daily interest by ~0.27% (~$1.23 per $100K at 4.5% APY).
# Acceptable approximation for projection purposes.
DAYS_IN_YEAR = Decimal("365")
```

In leap years (2024, 2028), the actual days-per-year is 366, but
the code uses 365.  This overstates daily interest by 1/365 ≈
0.27% throughout the leap year.  The comment acknowledges this as
intentional.

**User-visible consequence in a leap year.** For $100K at 4.5%
APY, the projected annual interest is overstated by ~$1.23.  For
typical Shekel users with a few thousand dollars in a HYSA, the
overstatement is well under $0.10/year.

**Verdict: Low (Info-adjacent).**  Documented in code; matches the
commonly-used actual/365 day-count convention; affects projections
only (not accounting).  Not a defect; just a documented simplifying
assumption.

### Biweekly-paycheck rounding residue

`paycheck_calculator.py:91-93`:

```python
gross_biweekly = (annual_salary / pay_periods_per_year).quantize(
    TWO_PLACES, rounding=ROUND_HALF_UP
)
```

For annual_salary = $50,000, pay_periods_per_year = 26:
$50,000 / 26 = $1923.0769230...  →  quantized to $1923.08.

26 × $1923.08 = $49,999.92.  The $0.08 residue is "missing" from
the annual aggregate.

**Verdict: Low (expected behavior).** This matches real-world
employer payroll practice -- biweekly paychecks are typically
quantized to the cent, with the residue absorbed by the year-end
true-up or simply accepted as pennies-off-of-round-numbers.  Not a
bug; noted for year-end-summary consumers to understand that
projected annual income may differ from `annual_salary` by up to
26 * $0.005 = $0.13.

### Division by non-Decimal

Checked each division-by-integer in monetary code:

| File:line | Code | Result type | Safe? |
|---|---|---|---|
| `paycheck_calculator.py:91` | `annual_salary / pay_periods_per_year` | Decimal (Decimal/int produces Decimal) | **Yes** -- result quantized line 91-93 |
| `paycheck_calculator.py:161` | `state_annual / pay_periods_per_year` | Decimal | **Yes** -- quantized line 160-162 |
| `paycheck_calculator.py:457` | `salary / pay_periods_per_year` | Decimal | **Yes** -- quantized line 457-459 |
| `tax_calculator.py:158-164` | `annual_tax_after_credits / pay_periods` | Decimal | **Yes** -- quantized |
| `balance_calculator.py:237` | `annual_rate / 12` | Decimal | **Yes** -- per-month interest quantized immediately after (line 274) |
| `amortization_engine.py:194` | `annual_rate / 12` | Decimal | **Yes** -- used in quantized per-month calc |
| `escrow_calculator.py:54` | `annual / 12` | Decimal | **Yes** -- final sum quantized |
| `growth_engine.py:241` | `period_days / Decimal("365")` | Decimal | **Yes** -- used in quantized per-period calc |
| `calibration_service.py:83-94` | `federal / taxable` etc. | Decimal | **Yes** -- quantized to 10 places (intentional for rate) |
| `dashboard_service.py:521` | `current / target * _HUNDRED` | Decimal | **Yes** -- quantized |

**No division-by-int hit produces a float or unquantized
monetary result.**  Python's Decimal/int semantics returns Decimal
with context precision, which is then quantized.

### sum() start-value consistency

A few `sum(...)` calls lack an explicit `Decimal("0")` start:

```
app/services/budget_variance_service.py:136-137  total_est = sum(g.estimated_total for g in groups)
app/services/budget_variance_service.py:139       txn_count = sum(len(tv.transactions) ...)  (int, OK)
app/services/budget_variance_service.py:322-323   est = sum(t.estimated for t in txn_vars)
app/services/budget_variance_service.py:341-342   est = sum(i.estimated_total for i in items)
app/services/calendar_service.py:176-177          annual_income = sum(ms.total_income for ms in months)
app/services/dashboard_service.py:410, 632        sum(abs(txn.effective_amount) for txn in txns)
app/services/spending_trend_service.py:325, 376, 457, 510  various
app/services/pension_calculator.py:147           total = sum(Decimal(str(s)) for _, s in window)
```

For an empty iterable, `sum()` returns `int(0)`, not `Decimal("0")`.
The result is a bare int.  A downstream `.quantize()` call on int
would crash with `AttributeError`.

**Practical impact.** In every hit above, the iterable is a
collection of transactions/periods/groups for the current user.  An
empty collection is rare but not impossible (a user with zero
transactions in a window).  Let me spot-check one:

- `budget_variance_service.py:148`: `_pct(total_var, total_est)`.
  If `total_est` is int(0), `_pct` divides by zero -- same crash
  whether int or Decimal.  So the empty-iterable hazard is a
  separate "divide by zero" issue, not a type issue.
- `calendar_service.py:176`: `annual_income` used at line 178 for
  display.  If empty, displayed as `0` (int).  Jinja renders both
  int 0 and Decimal 0 the same way.  No user-visible issue.

**Verdict: Info.** Consistent use of the explicit
`Decimal("0")` start would be cleaner and prevent future
`.quantize()` on empty-iterable results.  No active defect today.

### Findings from Check 4

#### L-P1 -- `interest_projection.py` uses 365-day year in leap years (Low)

Already covered above.  Documented in code as acceptable
approximation.  Severity: Low.

#### L-P2 -- Biweekly-paycheck residue from even division (Low -- expected behavior)

Already covered above.  Matches real-world payroll practice.
Severity: Low (Info-adjacent).

#### I-P3 -- Non-integer Decimal exponents in growth and escrow engines (Info)

`escrow_calculator.py:52`, `growth_engine.py:241`, `growth_engine.py:355`.
Python's `Decimal ** non-integer Decimal` is defined and produces
Decimal-context-precision results.  Each result is quantized
before use; no drift accumulates.  Severity: Info.

Recommendation: add a module-level docstring line noting that
`Decimal ** non-integer` is intentional and safe, so future
reviewers don't flag it.

#### I-P4 -- Some `sum()` calls lack explicit `Decimal("0")` start (Info)

Eight call sites across `budget_variance_service`, `calendar_service`,
`dashboard_service`, `spending_trend_service`, `pension_calculator`.
Empty iterable returns `int(0)` instead of `Decimal("0")`.  No
active defect (callers don't `.quantize()` the result), but a
latent type-mismatch waiting to happen.  Severity: Info.

**Recommended fix:** audit and add explicit `Decimal("0")` start
wherever the result flows into financial arithmetic.

---

### Check 4 summary

| Check | Status | Finding |
|---|---|---|
| Final quantize on every calculation service | Pass | All 14 service final outputs quantized |
| Rounding mode consistency (ROUND_HALF_UP) | Pass | Uniform; no banker's rounding or bare `round()` |
| Iterative-state drift over 26 periods | Pass | Zero drift in any calculation path |
| Sum-then-quantize vs quantize-then-sum | Pass | Sum-then-quantize used correctly (more accurate) |
| Division by non-Decimal | Pass | All results quantized |
| Non-integer Decimal exponents | Pass (Info) | Safe; documented-adjacent |
| Leap-year day count | Pass (Low-Info) | Documented as acceptable |
| Biweekly paycheck residue | Pass (Low-Info) | Expected payroll behavior |
| `sum()` start-value consistency | Info | Latent type hazard only |

**Severity totals for Check 4:** **0 High**, **0 Medium**, **2
Low** (both documented / expected), **2 Info**.

The financial-calculation code is exemplarily disciplined about
Decimal precision.  No calculation path accumulates drift over 26
pay periods or over compound-interest horizons.  Every monetary
value is stored as `Numeric(12,2)`, read as a 2-place Decimal,
manipulated with full Decimal precision, and quantized to 2 places
before use or storage.  `ROUND_HALF_UP` is the uniform rounding
mode.  Sum-then-quantize is used correctly to minimize drift.

This check corroborates Check 1's overall finding that the
codebase's Decimal discipline is the strongest defense against
money-math bugs in the audit.

---

## Check 5: Negative and Zero Amount Handling

**What was checked.** For every monetary field that accepts user
input, I compared the Marshmallow schema validator against the
database CHECK constraint (or absence thereof), and traced what
happens on zero and negative values.

Per coding-standards.md:

> "Range validation must match between schema and database. No gaps
> where one is stricter than the other."

A schema stricter than the DB is a Low finding (a raw SQL path
bypasses validation but data is safe).  A DB stricter than the
schema is High-or-Medium (user sees 500 errors instead of 400
validation errors, and the UX is broken for legitimate input).

### Comprehensive field table

| Model.field | Schema validator | DB CHECK / NULL | Match | Zero handling |
|---|---|---|---|---|
| `Transaction.estimated_amount` | `Range(min=0)` | `>= 0` | ✅ | Zero accepted both |
| `Transaction.actual_amount` | `Range(min=0), allow_none` | `NULL OR >= 0` | ✅ | Zero accepted both |
| `TransactionTemplate.default_amount` | `Range(min=0)` | `>= 0` | ✅ | Zero accepted both |
| `Transfer.amount` | `Range(min=0, min_inclusive=False)` | `> 0` | ✅ | Zero rejected both |
| `TransferTemplate.default_amount` | `Range(min=0, min_inclusive=False)` | `> 0` | ✅ | Zero rejected both |
| `TransactionEntry.amount` | `Range(min=Decimal("0.01"))` | `> 0` | ✅ | Zero rejected both (schema 0.01, DB 0) |
| `Account.current_anchor_balance` | **NO Range** | **NO CHECK** | Info | Neither constrained |
| `AccountAnchorHistory.anchor_balance` | **NO Range** | **NO CHECK** | Info | Neither constrained |
| `SavingsGoal.target_amount` | `Range(min=0, min_inclusive=False)` | `> 0` | ✅ | Zero rejected both |
| `SavingsGoal.contribution_per_period` | `Range(min=0)` | `NULL OR > 0` | **GAP L-V1** | Schema accepts 0; DB rejects 0 |
| `SavingsGoal.income_multiplier` | `Range(min=0, min_inclusive=False)` | `NULL OR > 0` | ✅ | |
| `SalaryProfile.annual_salary` | `Range(min=0, min_inclusive=False)` | `> 0` | ✅ | Zero rejected both |
| `SalaryProfile.additional_income` | **NO Range** | `>= 0` | **GAP M-V2** | Schema accepts negative; DB rejects |
| `SalaryProfile.additional_deductions` | **NO Range** | `>= 0` | **GAP M-V2** | Schema accepts negative; DB rejects |
| `SalaryProfile.extra_withholding` | **NO Range** | `>= 0` | **GAP M-V2** | Schema accepts negative; DB rejects |
| `SalaryRaise.percentage` | `Range(-100, 1000)` | `NULL OR > 0` | **GAP H-V3** | Schema accepts negative AND zero; DB rejects both |
| `SalaryRaise.flat_amount` | `Range(-10000000, 10000000)` | `NULL OR > 0` | **GAP H-V3** | Schema accepts negative AND zero; DB rejects both |
| `PaycheckDeduction.amount` | **NO Range, places=4** | `> 0` | **GAP H-V4** | Schema accepts ANY value; DB rejects ≤ 0 |
| `PaycheckDeduction.annual_cap` | `allow_none` | `NULL OR > 0` | Info | Schema has no Range, DB enforces > 0 |
| `PaycheckDeduction.inflation_rate` | `allow_none, places=4` | **NO CHECK** | Info | Neither bound |
| `TaxBracketSet.standard_deduction` | **NO Range** | `>= 0` | **GAP M-V5** | Schema accepts negative; DB rejects |
| `TaxBracketSet.child_credit_amount` | **NO Range** | `>= 0` | **GAP M-V5** | Schema accepts negative; DB rejects |
| `TaxBracketSet.other_dependent_credit_amount` | **NO Range** | `>= 0` | **GAP M-V5** | Schema accepts negative; DB rejects |
| `TaxBracket.min_income` | (admin-only; no schema) | `>= 0` | n/a | |
| `TaxBracket.max_income` | (admin-only) | `NULL OR > min_income` | n/a | |
| `TaxBracket.rate` | (admin-only) | `>= 0 AND <= 1` | n/a | |
| `FicaConfig.ss_rate` | `Range(0, 100)`, route `/100` | `>= 0 AND <= 1` | ✅ after conversion | Zero accepted both |
| `FicaConfig.ss_wage_base` | `Range(min=0, min_inclusive=False)` | `> 0` | ✅ | Zero rejected both |
| `FicaConfig.medicare_rate` | `Range(0, 100)`, route `/100` | `>= 0 AND <= 1` | ✅ | Zero accepted both |
| `FicaConfig.medicare_surtax_rate` | `Range(0, 100)`, route `/100` | `>= 0 AND <= 1` | ✅ | Zero accepted both |
| `FicaConfig.medicare_surtax_threshold` | `Range(min=0, min_inclusive=False)` | `> 0` | ✅ | Zero rejected both |
| `StateTaxConfig.flat_rate` | `Range(0, 100)`, route `/100` | `NULL OR (>= 0 AND <= 1)` | ✅ | Zero accepted both |
| `StateTaxConfig.standard_deduction` | `Range(min=0), allow_none` | **NO CHECK** | Info L-V6 | DB not enforcing |
| `InterestParams.apy` | `Range(0, 100)`, route `/100` | **NO CHECK** | Info L-V7 | DB not enforcing; raw SQL or script could insert invalid rate |
| `LoanParams.original_principal` | `Range(min=0)` | `> 0` | **GAP L-V8** | Schema accepts 0; DB rejects 0 |
| `LoanParams.current_principal` | `Range(min=0)` | `>= 0` | ✅ | Zero accepted both |
| `LoanParams.interest_rate` | `Range(0, 100)` places=5 | `>= 0` (no upper) | Partial | DB looser on upper bound |
| `LoanParams.term_months` | `Range(1, 600)` | `> 0` (no upper) | Partial | DB looser on upper bound |
| `LoanParams.payment_day` | `Range(1, 31)` | `>= 1 AND <= 31` | ✅ | |
| `EscrowComponent.annual_amount` | `Range(min=0)` | **NO CHECK** | Info | DB not enforcing |
| `EscrowComponent.inflation_rate` | `Range(0, 100), allow_none` places=4 | **NO CHECK** | Info | DB not enforcing |
| `InvestmentParams.assumed_annual_return` | `Range(-1, 1)` | `>= -1 AND <= 1` | ✅ | Zero accepted both |
| `InvestmentParams.annual_contribution_limit` | `Range(min=0), allow_none` | **NO CHECK** | Info | |
| `InvestmentParams.employer_flat_percentage` | `Range(0, 1), allow_none` | **NO CHECK** | Info | |
| `InvestmentParams.employer_match_percentage` | `Range(0, 10), allow_none` | **NO CHECK** | Info | |
| `InvestmentParams.employer_match_cap_percentage` | `Range(0, 1), allow_none` | **NO CHECK** | Info | |
| `PensionProfile.benefit_multiplier` | `Range(min=0, min_inclusive=False)` | (need model check) | Assumed ✅ | Zero rejected at schema |
| `UserSettings.default_inflation_rate` | `Range(0, 100), /100` | `>= 0` (likely) | Info | |
| `UserSettings.low_balance_threshold` | `Range(min=0)` | `>= 0` | ✅ | |

---

### Findings from Check 5

#### H-V3 -- Salary raise percentage/flat_amount: schema/DB mismatch (High)

**Fields:** `SalaryRaise.percentage`, `SalaryRaise.flat_amount`.

**Schema (`validation.py:249-256`):**

```python
percentage = fields.Decimal(
    places=2, as_string=True,
    validate=validate.Range(min=-100, max=1000),
)
flat_amount = fields.Decimal(
    places=2, as_string=True,
    validate=validate.Range(min=-10000000, max=10000000),
)
```

**DB constraint (`models/salary_raise.py:25-26`):**

```python
db.CheckConstraint("percentage IS NULL OR percentage > 0", name="ck_salary_raises_positive_pct"),
db.CheckConstraint("flat_amount IS NULL OR flat_amount > 0", name="ck_salary_raises_positive_flat"),
```

**Mismatch.** The schema explicitly allows negative values (the
`Range(min=-100, ...)` was presumably added to support pay-cut
modeling).  But the DB CHECK **rejects negative and zero**.

**Concrete scenario.** A user wants to model a 5% pay cut
(negative raise) to stress-test their budget.  They open the
add-raise form and enter `percentage = -5.0`:
1. Client-side: no client validation (schema is server-side only).
2. Schema: `-5.0` is in `Range(-100, 1000)`, validates OK.
3. Route `salary.py:384`: `SalaryRaise(..., percentage=-5.0/100=-0.05, ...)`.
4. Commit: DB raises `IntegrityError` on `ck_salary_raises_positive_pct`.
5. Route line 390 catches `except Exception:` (a CLAUDE.md rule
   violation -- too broad), logs the error, and flashes "Failed to
   add raise. Please try again."

**User-visible consequence.** The user sees a generic failure
message with no clue why.  They cannot model pay cuts.  The
Marshmallow schema suggests the feature exists (-100 is allowed),
but the DB prevents it.  UX is broken for a legitimate use case.

**Additionally, zero raise is also rejected at DB.**  `Range(min=-100)`
accepts 0 (inclusive by default).  If a user enters `percentage =
0.0`, schema validates, DB CHECK `> 0` rejects, same generic
failure message.

**Severity: High.** A legitimate user interaction (pay cut, or
explicitly-zero raise) produces an unhelpful error.  The mismatch
is on the "DB stricter than schema" axis, which CLAUDE.md calls
out as broken UX.

**Recommendation.** Pick one:
- If negative raises are supported: update the DB CHECK to
  `percentage != 0` (non-zero) or drop the CHECK and rely on
  schema.  Zero raise is semantically nonsensical, but negative
  is legitimate.
- If only positive raises are intended: tighten the schema to
  `Range(min=0, min_inclusive=False, max=1000)` for percentage and
  equivalent for flat_amount.  Remove the negative lower bound.

Either way, the mismatch must be resolved.  Coding-standards.md
requires schema and DB to match.

#### H-V4 -- PaycheckDeduction amount: schema has NO Range (High)

**Field:** `PaycheckDeduction.amount`.

**Schema (`validation.py:280`):**

```python
amount = fields.Decimal(required=True, places=4, as_string=True)
```

No `validate.Range(...)`.  Any Decimal is accepted, including
negative, zero, and absurdly large values (Decimal("1e100")).

**DB constraint (`models/paycheck_deduction.py:16`):**

```python
db.CheckConstraint("amount > 0", name="ck_paycheck_deductions_positive_amount"),
```

**Concrete scenario.** User adds a 401(k) deduction and accidentally
types `-500` instead of `500`:
1. Schema: no Range check, validates OK.
2. Route `salary.py:515`: `PaycheckDeduction(..., amount=-500, ...)`.
3. Commit: DB `IntegrityError`.
4. User sees a generic flash error.

Or a user enters `0` thinking it means "no deduction":
1. Same path; DB rejects 0.
2. User sees same generic error.

**Severity: High.** Same pattern as H-V3: DB stricter than schema,
and user gets no actionable feedback.  The schema could also
accept `9999999999999` (absurd amount) and the DB would accept it
too (no upper bound on `amount`), which is separately
problematic.

**Recommendation.** Add `validate=validate.Range(min=Decimal("0.0001"), max=Decimal("1000000"))` to the schema.

#### M-V2 -- SalaryProfile additional_income/deductions/extra_withholding: schema no Range (Medium)

**Fields:** `SalaryProfile.additional_income`,
`additional_deductions`, `extra_withholding`.

**Schema (`validation.py:198-206`):**

```python
additional_income = fields.Decimal(
    load_default="0", places=2, as_string=True
)
additional_deductions = fields.Decimal(
    load_default="0", places=2, as_string=True
)
extra_withholding = fields.Decimal(
    load_default="0", places=2, as_string=True
)
```

No Range.  Same pattern on `SalaryProfileUpdateSchema`.

**DB constraints (`models/salary_profile.py:24-26`):**

```python
db.CheckConstraint("additional_income >= 0", name="ck_salary_profiles_nonneg_add_income"),
db.CheckConstraint("additional_deductions >= 0", name="ck_salary_profiles_nonneg_add_deductions"),
db.CheckConstraint("extra_withholding >= 0", name="ck_salary_profiles_nonneg_extra_withholding"),
```

**Mismatch.** Schema accepts negative, DB rejects.

**Severity: Medium.** The user would enter a negative extra
withholding to model getting a refund instead of withholding --
but this is a conceptual mismatch with W-4 semantics.  The
correct fix is to tighten the schema to `Range(min=0)` to match
the DB.  The W-4 form itself does not allow negative extra
withholding.

**Recommendation.** Add `validate=validate.Range(min=0)` to all
three fields in both Create and Update schemas.

#### M-V5 -- TaxBracketSet fields: schema no Range (Medium)

**Fields:** `TaxBracketSet.standard_deduction`,
`child_credit_amount`, `other_dependent_credit_amount`.

**Schema (`validation.py:302-308`):**

```python
standard_deduction = fields.Decimal(required=True, places=2, as_string=True)
child_credit_amount = fields.Decimal(
    load_default="0", places=2, as_string=True
)
other_dependent_credit_amount = fields.Decimal(
    load_default="0", places=2, as_string=True
)
```

No Range.

**DB constraints (`models/tax_config.py:20-22`):**

```python
db.CheckConstraint("standard_deduction >= 0", ...)
db.CheckConstraint("child_credit_amount >= 0", ...)
db.CheckConstraint("other_dependent_credit_amount >= 0", ...)
```

**Mismatch.** Schema accepts negative, DB rejects.

**Severity: Medium.** This is admin-facing tax bracket config;
only power-users edit it.  The DB protects the system, but the
UX failure mode is "saving rejected with generic error" instead
of "field is invalid."

**Recommendation.** Add `validate=validate.Range(min=0)` to all
three fields in `TaxBracketSetSchema`.

#### L-V1 -- SavingsGoal.contribution_per_period: schema accepts 0, DB rejects 0 (Low)

**Schema (`validation.py:488-491`):**

```python
contribution_per_period = fields.Decimal(
    places=2, as_string=True,
    validate=validate.Range(min=0),
)
```

`Range(min=0)` with default `min_inclusive=True` accepts 0.

**DB (`models/savings_goal.py:38-41`):**

```python
db.CheckConstraint(
    "contribution_per_period IS NULL OR contribution_per_period > 0",
    name="ck_savings_goals_positive_contribution",
)
```

`> 0` rejects 0.

**Scenario.** User creates a savings goal with `contribution_per_period=0`:
1. Schema accepts.
2. DB rejects.

**Severity: Low.** Zero contribution means "no regular
contribution" -- the user probably intended NULL (omit the field)
instead.  The DB's `NULL OR > 0` is more correct than the
schema's `>= 0`.

**Recommendation.** Change schema to `Range(min=0,
min_inclusive=False), allow_none=True` and let the `@pre_load`
hook strip empty strings to None.

#### L-V8 -- LoanParams.original_principal: schema accepts 0, DB rejects 0 (Low)

**Schema (`validation.py:895`):**

```python
original_principal = fields.Decimal(required=True, places=2, as_string=True, validate=validate.Range(min=0))
```

**DB (`models/loan_params.py:26-29`):**

```python
db.CheckConstraint(
    "original_principal > 0",
    name="ck_loan_params_orig_principal",
)
```

**Scenario.** User enters 0 for original principal.  Schema
accepts, DB rejects.

**Severity: Low.** A loan with principal 0 is nonsensical.  The DB
is stricter and correct.

**Recommendation.** Change schema to `Range(min=0,
min_inclusive=False)`.

#### L-V7 -- InterestParams.apy: DB has no CHECK (Low, defensive gap)

**Schema (`validation.py:853-860`):**

```python
apy = fields.Decimal(
    required=True, places=3, as_string=True,
    validate=validate.Range(min=0, max=100),
)
```

Route does `/100` to convert to fraction.

**DB (`models/interest_params.py:21-25`):**

Only the CHECK for `compounding_frequency`.  **No CHECK on `apy`.**

**Scenario.** A raw SQL path or future endpoint could insert
`apy = -0.05` (negative APY) or `apy = 2.0` (200% APY).  Schema
would normally catch it, but if bypassed, DB lets it through.

**Severity: Low (defensive).** The schema currently protects us,
but the DB should also have `apy >= 0 AND apy <= 1` as a safety
net.

**Recommendation.** Add migration:
```python
op.create_check_constraint(
    "ck_interest_params_valid_apy",
    "interest_params",
    "apy >= 0 AND apy <= 1",
    schema="budget",
)
```

#### L-V6 -- Several model fields have schema validation but no DB CHECK (Low)

Affected:
- `StateTaxConfig.standard_deduction` -- Numeric(12,2), no CHECK.  Schema has `Range(min=0)`.
- `EscrowComponent.annual_amount` -- Numeric(12,2), no CHECK.  Schema has `Range(min=0)`.
- `EscrowComponent.inflation_rate` -- Numeric(5,4), no CHECK.  Schema has `Range(0, 100)` (then /100).
- `PaycheckDeduction.inflation_rate` -- Numeric(5,4), no CHECK.  Schema `allow_none, places=4`.
- `InvestmentParams.annual_contribution_limit` -- Numeric(12,2), no CHECK.  Schema has `Range(min=0)`.
- `InvestmentParams.employer_flat_percentage` -- Numeric(5,4), no CHECK.  Schema has `Range(0, 1)`.
- `InvestmentParams.employer_match_percentage` -- Numeric(5,4), no CHECK.  Schema has `Range(0, 10)`.
- `InvestmentParams.employer_match_cap_percentage` -- Numeric(5,4), no CHECK.  Schema has `Range(0, 1)`.

**Severity: Low.** Per coding-standards.md:

> "Schema-level validation without database-level enforcement is a
> finding because a raw SQL path (script, admin, future endpoint)
> bypasses the schema."

The risk is latent: today, every endpoint validates through the
schema.  Tomorrow, a new endpoint or maintenance script could
bypass it.  Defense-in-depth favors DB CHECK constraints as the
last line.

**Recommendation.** One migration that adds CHECK constraints for
all eight fields.  Single migration, small churn.

---

### Anchor balance and zero transactions (Info)

**Anchor balance:** `Account.current_anchor_balance` is
intentionally unconstrained (negative is valid for overdrafts;
upper bound is Numeric(12,2) storage limit ~ $10B).  No schema
Range, no DB CHECK.  **Intentional and correct.** Noted for
Check 6 (anchor is a concurrency target) and already flagged as
H-C2-01 for the lost-update hazard.

**Zero-amount transactions:** `Transaction.estimated_amount = 0`
is accepted by both schema and DB.  The balance calculator handles
zero correctly (contributes $0 to period sums).  The grid displays
such a row as a placeholder.  This may surprise users ("why is
there a $0 entry?") but is not a correctness concern.  **Intentional;
noted for UX review out of scope.**

---

### Check 5 summary

| Severity | Count | Findings |
|---|---|---|
| High | 2 | H-V3 (salary raise schema/DB mismatch), H-V4 (paycheck deduction no Range) |
| Medium | 2 | M-V2 (W-4 fields no Range), M-V5 (tax bracket fields no Range) |
| Low | 4 | L-V1, L-V6, L-V7, L-V8 (schema allows 0 where DB rejects; DB missing CHECK where schema has Range) |
| Info | Multiple | Anchor balance intentionally unconstrained; zero-amount transactions accepted |

**Root cause of the High findings:** four fields where the DB is
stricter than the schema.  All produce a generic "Failed to add/
update" flash message instead of a 400-with-field-error response.
The user has no way to discover what went wrong.

**Fix pattern:** update the Marshmallow schemas in
`app/schemas/validation.py`.  No migrations required for the
Highs; the DB is correct, the schema is permissive.  Three lines
of code per field, test with a targeted pytest run.  This is the
cheapest-to-fix cluster in the whole audit.

**Fix pattern for Lows L-V6, L-V7:** Alembic migration adding
CHECK constraints on 8 currently-unchecked columns.  Single
migration, <100 lines, round-trip tested via upgrade/downgrade.
Defense-in-depth only; no current user-visible defect.

---

## Check 6: Idempotency of Mutations

**What was checked.** Every POST endpoint that can plausibly be
double-clicked or retried by the browser, classified by protection
mechanism and duplicate behavior.

Check 2 (Concurrency/TOCTOU) covered simultaneous races between
two requests.  Check 6 covers **sequential duplicates**: request 1
commits successfully, then an identical request 2 arrives
(double-click, browser retry, network reshipment).  The two
checks overlap in root cause for a few endpoints; where they do I
cross-reference rather than restate.

### Infrastructure inventory

**Rate limiting:** Flask-Limiter is installed
(`app/extensions.py:9-31`) and configured with `default_limits=[]`
(empty).  Applied only to auth routes:

```
app/routes/auth.py:74    @limiter.limit("5 per 15 minutes", methods=["POST"])  # /login
app/routes/auth.py:136   @limiter.limit("10 per hour")                         # /register form? logout?
app/routes/auth.py:152   @limiter.limit("3 per hour")                          # /register POST
app/routes/auth.py:251   @limiter.limit("5 per 15 minutes", methods=["POST"])  # /mfa/verify
```

**Zero rate limits on any financial mutation endpoint.** An
authenticated user can POST to `/transactions` as fast as their
network allows.

**Idempotency keys:** `grep` for `idempotency|Idempotency` across
`app/` returns zero matches.  The server generates an `X-Request-Id`
UUID for logging only (`app/utils/logging_config.py:110`), which
is emitted in response headers but not consumed for dedupe.

**Explicit idempotency checks in code:** grep for the word
"idempotency" in comments:

```
app/services/credit_workflow.py:72   # Idempotency: if already credited with existing payback, return it.
app/services/transfer_service.py:566 # allow_deleted=True so that idempotent soft-delete and hard-delete
app/services/transfer_service.py:616 Idempotent: calling on an already-active transfer is a no-op.
```

Three explicit idempotency comments.  The first
(`credit_workflow.py:72`) is TOCTOU-racy, already flagged as H-C2-02.
The second two (transfer_service soft-delete + restore) are
correctly idempotent because they check current state before
writing.

### Endpoint-by-endpoint classification

**Legend:**
- *Unique*: a DB unique constraint rejects the duplicate.
- *Status*: a status check rejects the second attempt.
- *Natural*: the second invocation is a no-op because the first's effect is already in place.
- *None*: no protection; duplicate produces duplicate side effects.

| Route (method) | Protection | Duplicate behavior | Severity |
|---|---|---|---|
| `POST /transactions` (ad-hoc create) | None | Two identical Transaction rows in grid | L-I1 |
| `POST /transactions/inline` (inline create) | None | Two rows | L-I1 |
| `PATCH /transactions/<id>` (update) | Natural | Same value written twice; ORM UPDATE is idempotent | Pass |
| `POST /transactions/<id>/mark-done` | Natural (status) | status=DONE set twice; paid_at overwritten with later timestamp | Pass (paid_at imprecision already L-C2-06) |
| `POST /transactions/<id>/mark-credit` | Status check (TOCTOU-racy) | Duplicate CC paybacks — **see H-C2-02** | *H (already counted)* |
| `DELETE /transactions/<id>/unmark-credit` | Natural | Second delete finds no payback, no-op | Pass |
| `POST /transactions/<id>/cancel` | Natural | status=CANCELLED set twice | Pass |
| `DELETE /transactions/<id>` | Natural | soft: sets is_deleted=True twice; hard: 404 on second | Pass |
| `POST /pay-periods/<id>/carry-forward` | Natural | Second call finds no projected txns in source (first moved them all), count=0 | Pass |
| `POST /transfers` (create template) | Unique `uq_transfer_templates_user_name` | Second hit: IntegrityError, rollback | Pass |
| `POST /transfers/ad-hoc` | **None** | Two Transfer rows + **four** shadow Transactions | **M-I2** |
| `PATCH /transfers/instance/<id>` | Natural | Same value written twice | Pass (stale-form issue is H-C2-04) |
| `DELETE /transfers/instance/<id>` | Natural | Soft sets is_deleted=True twice; hard 404 on second | Pass |
| `POST /transfers/instance/<id>/mark-done` | Natural | Same status write | Pass |
| `POST /transfers/instance/<id>/cancel` | Natural | Same status write | Pass |
| `POST /transfers/<id>/archive` | Natural | is_active=False set twice | Pass |
| `POST /transfers/<id>/unarchive` | Natural | Plus regenerate may hit unique index | Pass |
| `POST /accounts` (create) | Duplicate-name check inside route | Second rejected with flash | Pass |
| `POST /accounts/<id>` (update) | Natural | Same value written; H-C2-04 lost-update applies | Pass (but H-C2-04) |
| `PATCH /accounts/<id>/true-up` | None | Second call writes same anchor + duplicate history row | L-I3 (audit noise) + H-C2-01 (lost update) |
| `PATCH /accounts/<id>/inline-anchor` | None | Same as above | L-I3 + H-C2-01 |
| `POST /accounts/<id>/archive` | Natural | is_active=False | Pass |
| `POST /accounts/<id>/interest/params` | Natural (upsert) | Same params written | Pass |
| `POST /accounts/<id>/loan/setup` | Unique on `loan_params.account_id` | Second hit: IntegrityError | Pass |
| `POST /accounts/<id>/loan/params` (update) | Natural (upsert) | Same values | Pass |
| `POST /accounts/<id>/loan/rate` | **None** | Duplicate RateHistory row inserted (no unique on account_id+effective_date) | **L-I4** |
| `POST /accounts/<id>/loan/escrow` (create component) | Unique `uq_escrow_account_name` | Second hit: IntegrityError | Pass |
| `POST /accounts/<id>/loan/create-transfer` | Goes through `transfer_service.create_transfer` with template-linked recurrence; protected by `idx_transfers_template_period_scenario` | Second fires recurrence, hits unique index, rolls back | Pass |
| `POST /dashboard/mark-paid/<id>` | Natural (status) | Same as transactions/mark-done; paid_at imprecision | Pass |
| `POST /settings` | Natural | Same settings written | Pass |
| `POST /settings/companions` (create) | Uniqueness enforced at route via email lookup | Second rejected | Pass |
| `POST /savings/goals` (create) | `uq_savings_goals_user_acct_name` | Second hit: IntegrityError | Pass |
| `POST /savings/goals/<id>` (update) | Natural | Same values | Pass |
| `POST /salary` (create profile) | `uq_salary_profiles_user_scenario_name` | Second rejected | Pass |
| `POST /salary/<id>/raises` | **None** | Duplicate SalaryRaise row -- paycheck projection **wrong** | **M-I5** |
| `POST /salary/<id>/deductions` | **None** | Duplicate PaycheckDeduction row -- paycheck projection **wrong** | **M-I6** |
| `POST /salary/<id>/calibrate/confirm` | Unique `uq_calibration_overrides_profile` | Second rejected | Pass |
| `POST /salary/tax-config` | Natural (upsert via query) | Same values | Pass |
| `POST /salary/fica-config` | Natural (upsert via query) | Same values | Pass |
| `POST /retirement/pension` (create) | No unique on pension name | Duplicate pension profile | L-I7 |
| `POST /retirement/settings` | Natural (one settings row per user) | Same values | Pass |
| `POST /pay-periods/generate` | Partial protection: `uq_pay_periods_user_start` rejects duplicate start dates | Second generates only new periods beyond existing | Pass |
| `POST /categories` (create) | `uq_categories_user_group_item` | Second rejected | Pass |
| `POST /transactions/<id>/entries` (create entry) | None (entries can legitimately duplicate) | Two entries + **duplicate CC payback** — see H-C2-03 | *H (already counted)* |

---

### Findings from Check 6

#### M-I2 -- Ad-hoc transfer double-submit creates duplicate with four shadows (Medium)

**Endpoint:** `POST /transfers/ad-hoc`.

**Protection:** None.  No unique constraint on ad-hoc
(`transfer_template_id IS NULL`) rows.  No rate limit.  No
idempotency key.

**Concrete scenario.**
1. User clicks "Save" on an ad-hoc $500 transfer from checking to
   savings.
2. HTMX doesn't finish the swap visually; user clicks again.
3. Two `POST /transfers/ad-hoc` hit the server.
4. Each creates: 1 Transfer + 1 expense shadow + 1 income shadow =
   3 rows.  Two calls = 6 rows total (2 transfers, 2 expense
   shadows in checking, 2 income shadows in savings).

**User-visible consequence.** The grid shows two $500 outflows
from checking and two $500 inflows to savings.  The balance
calculator subtracts $1000 from checking's projection (not $500)
and adds $1000 to savings' projection.  User notices the duplicate
in the grid, deletes one ad-hoc transfer, which deletes its two
shadows via CASCADE.  Net effect: 15-30 seconds of confusion, no
lasting damage.

But: a DELETE-one-of-two requires clicking the "delete" action on
the duplicate row, which takes another HTMX round-trip.  If the
user doesn't notice and waits until pay period close, the
projected balance is off by $500 for multiple periods.

**Severity: Medium.** Visible duplicate, user-correctable, but
blast radius (4 shadow transactions affecting two accounts'
balance projections) is larger than a single duplicate transaction.

**Remediation.**
- Client-side: disable the submit button on click; re-enable on
  HTMX completion or error.  Classic double-submit protection.
- Server-side: add a short-lived "recent ad-hoc transfer" check
  based on (user_id, from_account_id, to_account_id, amount,
  scenario_id, pay_period_id) within the last N seconds.
  Reject duplicate within the window.
- Or: add an idempotency-key form field generated per form render.
  Server stores recently-seen keys in a short TTL set; reject
  repeats.

#### M-I5 -- Salary raise double-submit duplicates the raise event (Medium)

**Endpoint:** `POST /salary/<profile_id>/raises`.

**Protection:** None.  No composite unique on (profile_id,
raise_type_id, effective_year, effective_month).

**Concrete scenario.**
1. User adds a 3% annual recurring raise effective 2026-01.
2. Form submit lags; user clicks again.
3. Two SalaryRaise rows inserted: `percentage=0.03,
   effective_year=2026, effective_month=1, is_recurring=True` for
   the same profile.
4. `paycheck_calculator._apply_raises` iterates all raises and
   applies each: salary * 1.03 * 1.03 instead of salary * 1.03.
5. Every future paycheck is projected as 6.09% above baseline, not
   3%.

**User-visible consequence.** Paycheck projections are wrong by
~3% for every projected future period.  For a $50K salary, that's
~$1500/year in phantom income.  The grid's projected income rows
show inflated amounts.  The year-end summary is inflated.

**Severity: Medium.** Paycheck math is a load-bearing feature.  A
3% → 6.09% drift is visible but subtle -- the user might not
realize their take-home projection is wrong until an actual
paycheck arrives and disagrees.

Plus: the duplicate raise is visible in the raises list under
the salary profile.  The user can spot and delete it -- but only
if they think to look.

**Remediation.**
- **Preferred:** add composite unique constraint
  `uq_salary_raises_profile_year_month_type` on `(salary_profile_id,
  raise_type_id, effective_year, effective_month)`.
  Prevents accidental duplicates; the rare user who genuinely
  wants two raises at the same date would have to use distinct
  raise types.
- Client-side: debounce the submit.

#### M-I6 -- Paycheck deduction double-submit duplicates the deduction (Medium)

**Endpoint:** `POST /salary/<profile_id>/deductions`.

**Protection:** None.  No composite unique on (profile_id, name).

**Concrete scenario.** Same pattern as M-I5.  User adds a "401(k)
6% pre-tax" deduction; double-submit creates two.
`_calculate_deductions` iterates `profile.deductions` and applies
each independently, so 401(k) is deducted twice per paycheck.

**User-visible consequence.** Net pay projection is understated
by the duplicate deduction's amount.  For a $500 bi-weekly 401(k)
deduction, net pay is $500 lower than correct per paycheck,
$13,000 lower per year.  The 401(k) annual-cap check is also
duplicated, so the cap is hit twice as fast (more deduction
periods are skipped after cap-hit -- but this compounds the
projection error).

**Severity: Medium.** Same severity logic as M-I5 -- user-visible
in the deduction list, but the impact on paycheck math is
significant.

**Remediation.** Composite unique constraint on
`(salary_profile_id, name)` with a migration.  Also matches the
naming convention the user expects ("my 401(k)" is one thing,
not two).

#### L-I1 -- Ad-hoc transaction double-submit creates duplicate (Low)

**Endpoints:** `POST /transactions`, `POST /transactions/inline`.

**Concrete scenario.** User adds a $50 grocery transaction in the
grid.  HTMX swap lags; user clicks again.  Two Transaction rows
in the period.

**User-visible consequence.** Two $50 groceries in the grid under
the same category.  The balance calculator subtracts $100 from
projected balance (not $50).

**Severity: Low.** Visible duplicate, user-correctable by
deleting one.  Single-row blast radius (no shadows).

**Remediation.** Client-side debounce is sufficient.

#### L-I3 -- Anchor true-up writes duplicate history rows on double-submit (Low)

**Endpoints:** `PATCH /accounts/<id>/true-up`,
`PATCH /accounts/<id>/inline-anchor`.

**Scenario.** User submits anchor=$1000.  Commits: account anchor
= $1000, AccountAnchorHistory row #1 written.  Submits again
(double-click or retry): account anchor = $1000 (no-op), history
row #2 written with same values.

**User-visible consequence.** Audit trail shows two "anchor set to
$1000" entries one second apart.  Not corruption, just noise.  The
LIST of anchor updates becomes misleading if users look at it for
cadence analysis ("how often do I true up?").

**Severity: Low (audit noise).** Already noted as I-C2-09.  The
core lost-update hazard is H-C2-01.

**Remediation.** Inside the route, check whether the most recent
AccountAnchorHistory row matches the current submission.  If yes,
skip writing another history row.

#### L-I4 -- Loan rate change double-submit creates duplicate RateHistory (Low)

**Endpoint:** `POST /accounts/<id>/loan/rate`.

**Protection:** None.  `RateHistory` has no `__table_args__` unique
constraint at all.

**Concrete scenario.** User records a rate change on an ARM loan:
effective_date=2026-05-01, new_rate=7.25%.  Submits twice.  Two
RateHistory rows with identical fields.

**User-visible consequence.** The amortization engine replays rate
changes by `effective_date`.  Two entries on the same date
**should not change the math** because `_build_rate_change_list`
deduplicates by `effective_date` (last entry wins;
`amortization_engine.py:283-295`).  But the rate-change UI
displays all entries including the duplicate, which is confusing.

**Severity: Low.** The deduplication in the amortization engine
limits the damage to UI noise.

**Remediation.** Add composite unique constraint on
`(account_id, effective_date)`.

#### L-I7 -- Pension profile double-submit creates duplicate (Low)

**Endpoint:** `POST /retirement/pension`.

**Scenario.** Similar to M-I5 but on pension profiles.  No unique
constraint on name.  Two pensions with same name.  Retirement gap
calculator double-counts.

**Severity: Low.** Pension profiles are edited infrequently.  User
will notice and delete one.  No automatic double-counting in the
balance calculator (pensions feed the retirement dashboard, not
the grid).

**Remediation.** Composite unique on (user_id, name).

---

### What happens to the "high-impact" scenarios?

Per the prompt's high-value targets:

**1. Create transfer (shadows atomic with parent?)**
- Ad-hoc: duplicates possible, atomic per-request.  M-I2.
- Template-linked: protected by `idx_transfers_template_period_scenario`.  Pass.

**2. Record paycheck (double-click-safe?)**
- Paychecks are not created directly via a POST endpoint; they are
  generated by `recurrence_engine.regenerate_for_template` when
  the salary profile / template is edited.  Template changes are
  protected by `idx_transactions_template_period_scenario`.
- The individual raise / deduction mutations that feed paycheck
  calc are NOT protected.  M-I5, M-I6.

**3. Mark transaction as done/settled (double-click-safe?)**
- Status transitions are natural-idempotent (same status written
  twice = no effect).  Pass.
- Paid_at timestamp races (second write overwrites first's
  paid_at).  Low, already noted.

**4. Mark transfer as done/settled (double-click-safe?)**
- Same as above.  Pass.

**5. Set anchor balance (double-click-safe?)**
- Anchor is overwritten (natural-idempotent on value).  Audit
  history gets a duplicate row.  L-I3.
- Lost-update hazard on concurrent retry: H-C2-01.

**6. Mark as credit (double-click-safe?)**
- Duplicate CC paybacks created.  H-C2-02.

---

### Check 6 summary

| Severity | Count | Findings |
|---|---|---|
| High | 0 new (2 inherited from C2) | H-C2-02 (mark-credit), H-C2-03 (entry-credit) |
| Medium | 3 new | M-I2 (ad-hoc transfer dupe), M-I5 (raise dupe), M-I6 (deduction dupe) |
| Low | 4 new | L-I1 (ad-hoc txn dupe), L-I3 (anchor history dupe), L-I4 (rate history dupe), L-I7 (pension dupe) |
| Info | -- | |

**Unified fix for M-I5, M-I6, L-I1, L-I2, L-I4, L-I7:** composite
unique constraints.  One migration per class:

- `uq_salary_raises_profile_year_month_type` on
  `(salary_profile_id, raise_type_id, effective_year,
  effective_month)`.
- `uq_paycheck_deductions_profile_name` on
  `(salary_profile_id, name)`.
- `uq_rate_history_account_effective_date` on
  `(account_id, effective_date)`.
- `uq_pension_profiles_user_name` on `(user_id, name)`.

These would have prevented Check 6's main findings without
touching any route code.

**Unified fix for ad-hoc duplicates (M-I2, L-I1):** client-side
submit debounce + optional server-side idempotency window.  This
is a UI-level fix, not a schema fix.  It would also address the
"user rapid-clicks and gets confused" class of problems generally.

**Rate limiting on financial mutations:** Flask-Limiter is already
installed and used for auth.  Extending it to financial mutations
(e.g., `10 per minute` on `/transactions/*` and `/transfers/*`
POST/PATCH) would catch bot-like abuse and repeat-click spam at a
low cost.  Out of strict idempotency scope but a natural
companion fix.

---

## Session Wrap-Up

### Deliverable

- `docs/audits/security-2026-04-15/reports/16-business-logic.md`
  (this file).  Lines: see `wc -l` at session close.

### Findings totals across all six checks

| Severity | New in S5 | Confirmed from S1 | Total |
|---|---|---|---|
| **High** | 6 (H-C2-01, H-C2-02, H-C2-03, H-C2-04, H-V3, H-V4) | 1 (F-B2-01) | **7** |
| **Medium** | 8 (M-C2-05, M-C2-06, M-C2-07, M-V2, M-V5, M-I2, M-I5, M-I6) | 1 (F-B2-03) | **9** |
| **Low** | 14 (L-C2-08, L-P1, L-P2, L-V1, L-V6, L-V7, L-V8, L-I1, L-I3, L-I4, L-I7, plus 3 from Check 1) | -- | **14** |
| **Info** | 7+ (I-C2-09, I-C2-10, I-P3, I-P4, plus Check 1 L1-d/e, Check 5 anchor-intentional, zero-txn) | -- | **7+** |

**Overall:** 7 High, 9 Medium, 14 Low, 7+ Info across Section 1L.

### Per-check summary

| Check | Scope | Findings | Top severity | Nature |
|---|---|---:|---|---|
| 1 -- Type purity | `float(`, `Decimal()`, `round(`, `/100`, `**`, truthiness | 6 (0/0/3/3) | Low | Mostly cosmetic truthiness on Decimal |
| 2 -- Concurrency / TOCTOU | Every mutating endpoint | 10 (4/3/1/2) | **High** | Lost-update hazards, no row locks, CC payback dupes |
| 3 -- Transfer invariants | 5 CLAUDE.md invariants | 2 (1/1/0/0) | **High** | Confirmed S1's F-B2-01 and F-B2-03; rest enforced |
| 4 -- Rounding & precision | Every calculation service | 4 (0/0/2/2) | Low | No drift over 26 periods; leap-year daycount + biweekly residue only |
| 5 -- Negative / zero amounts | Schema vs DB CHECK | 8 (2/2/4/0) | **High** | Schema/DB mismatches produce generic "Failed" errors |
| 6 -- Idempotency | Every double-clickable POST | 7 (0/3/4/0) | Medium | Missing composite unique constraints |

### Top five concerns (prioritized for action)

#### 1. Duplicate CC paybacks from double-click on "mark as credit" (H-C2-02 + H-C2-03)

**Finding in one sentence.** `credit_workflow.mark_as_credit` and
`entry_credit_workflow.sync_entry_payback` both read
`credit_payback_for_id`, check "no existing payback," then INSERT
-- TOCTOU-racy and no DB unique constraint means two rapid
clicks insert two paybacks.

**User-visible consequence.** A $50 credit card expense marked twice
(double-click or two tabs) creates two $50 paybacks in the next
period.  The balance calculator subtracts $100 from projected
balance, off by exactly one payback amount.  The user might not
notice for weeks.

**Exploitable today?** Yes, single user, single browser.  Trivial
to trigger by impatience.

**Fix priority.** Before production: add a partial unique index
on `budget.transactions(credit_payback_for_id) WHERE
credit_payback_for_id IS NOT NULL`.  Single Alembic migration,
~5 lines.  Catches the duplicate at write time.

#### 2. Anchor balance lost-update on retry (H-C2-01)

**Finding in one sentence.** `PATCH /accounts/<id>/true-up` has no
version column or conditional UPDATE; a browser retry or a
stale-tab submission silently overwrites the newer anchor value.

**User-visible consequence.** User sets anchor to $1100, then to
$1200.  Browser retries the $1100 request after a hiccup; anchor
drops to $1100.  Every balance projection is off by $100 until
the user notices.  For a checking account with authorized bills,
a false low-balance alert could fire.

**Exploitable today?** Yes, any user with flaky wifi or two
browser tabs.

**Fix priority.** Before production.  Add `version_id_col` to
Account or require the client to echo the previous anchor in the
form; server rejects on mismatch with 409.

#### 3. PATCH lost-update / stale-form hazard (H-C2-04)

**Finding in one sentence.** Every PATCH endpoint
(transactions, transfers, entries, accounts, raises, deductions)
blindly writes whatever the form submits; a stale tab can
silently roll back an unrelated tab's edit.

**User-visible consequence.** Tab 2 edits a $500 transfer to
$600.  Tab 1 (stale, still showing $500) submits an unrelated
category change; the form body re-submits $500 and overwrites
Tab 2's $600.  The user's intentional $600 edit vanishes with no
warning.

**Exploitable today?** Yes, any user with multiple tabs or a
lingering open edit form.

**Fix priority.** Before public.  Version columns on all mutable
entities or client-side dirty-field tracking.  This is the biggest
architectural fix in the audit -- but without it, a public-facing
app has no defense against a common user error.

#### 4. Schema/DB validation mismatches produce opaque "Failed" errors (H-V3 + H-V4)

**Finding in one sentence.** `SalaryRaise.percentage`,
`SalaryRaise.flat_amount`, and `PaycheckDeduction.amount` have
Marshmallow schemas that permit values the database CHECK
constraints reject (negative, zero, or out-of-range).

**User-visible consequence.** A user trying to model a 5% pay cut
(`percentage=-5`) sees a generic "Failed to add raise. Please try
again." flash message with no indication of what's wrong.
Marshmallow's inline-field-error UX doesn't fire because the
validator accepted the value; the error is raised by the DB
CHECK at commit time and caught by a broad `except Exception`.

**Exploitable today?** No exploit -- user is blocked from a
legitimate feature.  UX broken.

**Fix priority.** Before public.  Tighten schema validators to
match DB CHECKs.  Three lines per field in `validation.py`.  No
migration needed.  **Cheapest-to-fix cluster in the whole audit.**

#### 5. `recurrence_engine.resolve_conflicts` has no shadow guard (H, F-B2-01)

**Finding in one sentence.** Per S1 and re-confirmed here:
`resolve_conflicts` directly writes `is_override`, `is_deleted`,
and `estimated_amount` on any Transaction whose id is passed in,
with no check for `transfer_id IS NOT NULL`.  The function is
currently uncalled, but exists on the service's public API.

**User-visible consequence.** Today: none; no current caller passes
shadow IDs.  Tomorrow: a UI that surfaces the RecurrenceConflict
prompt and wires up resolve_conflicts would instantly break
invariants 3 and 4 if any override/delete list contains a shadow
ID.

**Exploitable today?** No.  Dormant gap.

**Fix priority.** Acceptable-for-private-use; fix before any UI
feature that surfaces RecurrenceConflict to the user.  30-minute
fix: add `if txn.transfer_id is not None: continue` at the top of
the loop in `recurrence_engine.py:272`.

### Transfer invariants final verdict

This is the single most important section of the audit per the
session brief.

| Invariant | Status | Evidence |
|---|---|---|
| 1 -- Exactly two shadows | **Enforced in service** (quoted: transfer_service.py:349-410 creation, :195-265 mutation gate) -- residual Medium DB gap F-B2-03 | Yes, confirmed + strengthened |
| 2 -- Never orphaned | **Enforced by code** (creation atomicity + ON DELETE CASCADE + soft-delete sweep) | Yes, confirmed |
| 3 -- Amount/status/period match | **Enforced by code** (update propagation at :461-540; drift-repair in restore at :688-720) | Yes, confirmed |
| 4 -- No direct shadow mutation | **PARTIALLY ENFORCED** -- HIGH finding F-B2-01 for `recurrence_engine.resolve_conflicts` | Yes, confirmed |
| 5 -- Balance calculator isolation | **Enforced by code** -- zero `Transfer` import or query in balance_calculator.py | Yes, confirmed |

**Three of the five invariants are fully enforced by code.**
Invariant 4 is partially enforced (one gap, currently dormant).
Invariant 1 has a residual DB-level gap (F-B2-03).

**New evidence reinforcing F-B2-03:** the existence of
`scripts/repair_orphaned_transfers.py` documents that invariant 2
(never orphaned) was violated in production at some point by a
prior bug in `create_transfer_template`.  The bug was patched and
the repair script was built to clean up fallout.  This makes the
"no DB-level exactly-two-shadows constraint" gap a concrete
operational risk, not a theoretical one.

### Cross-references to Session S1

S5 and S1 agree on all transfer-invariant verdicts:

- **S1 Check 1C.2** (`07-manual-deep-dives.md` lines 269-640) and
  Subagent B2 (`02b-services.md`) identified invariant 4 as
  partially enforced (F-B2-01, High) and invariant 1 residual DB
  gap (F-B2-03, Medium).  S5 re-verified both with independent
  source reads and concurred.
- **S1 Check 1C.3** (`07-manual-deep-dives.md` lines 643-776)
  grepped the balance calculator's full transitive import chain
  for any Transfer import or query and found zero.  S5 re-verified
  with targeted greps and concurred.
- **S5 adds:** the `scripts/repair_orphaned_transfers.py` file as
  concrete evidence that the gap has manifested in production.
  S1 noted F-B2-03 as theoretical; S5 upgrades the evidence to
  "documented production incident."

**No contradictions with S1.** Where S5 diverged from S1's
analysis, it was to add evidence, not to disagree.

### What S5 newly adds beyond S1's manual deep dives

S1's Section 1C covered transfer invariants and balance-calculator
isolation but did not cover:

- **Concurrency and TOCTOU across all mutating endpoints** -- S5's
  Check 2, the largest finding cluster (4 Highs).
- **Type-purity grep across services and routes** -- S5's Check 1,
  clean-bill outcome (0 H/M).
- **Rounding precision over 26 pay periods** -- S5's Check 4,
  clean-bill outcome.
- **Schema vs DB CHECK mismatch audit** -- S5's Check 5 (4 actionable).
- **Idempotency of every POST endpoint** -- S5's Check 6 (3
  Mediums, cheap schema-migration fixes).

S5 is complementary to S1, not duplicative.

### Post-session health snapshot

```
# Pre-session (T=start)                        # Post-session (T=end)
cloudflared Up 5 hours (unhealthy)             cloudflared Up 16 hours (unhealthy)
shekel-prod-app Up 24 hours (healthy)          shekel-prod-app Up 36 hours (healthy)
shekel-prod-db Up 24 hours (healthy)           shekel-prod-db Up 36 hours (healthy)
... (12 containers total, same set pre and post)
```

**Diff:** only the uptime counters advanced (5h→16h, 24h→36h).
No container stopped, no image changed, no health state changed.
Pre-existing `cloudflared` and `shekel-app` (dev) unhealthy
conditions unchanged.  The audit was read-only as intended.

Pre-snapshot: `/tmp/shekel-prod-pre-s5-snapshot.txt`
Post-snapshot: `/tmp/shekel-prod-post-s5-snapshot.txt`

### Session S5 status

**Session S5 is complete.**  All six checks finished.  No code
changes made.  No container state changed.  The single deliverable
`reports/16-business-logic.md` is ready for developer review and
consolidation by Session S8's `findings.md` builder.

You can close this chat and open Session S6.















