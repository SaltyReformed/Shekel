# Architectural Investigation: Account Parameter Architecture Across All Categories

**Date:** 2026-03-29
**Status:** Analysis complete, pending decision

---

## Phase 0: Documentation Summary

### By Category

**Asset:**
- HYSA is the only parameterized asset type (apy, compounding_frequency)
- Checking and Savings have no params -- they use the basic balance calculator
- CD, Money Market, and HSA exist in `AcctTypeEnum` but have no parameter handling
- Interest projection service consumes HYSA params

**Liability:**
- Mortgage and Auto Loan are implemented with per-type parameter tables
- Student Loan and Personal Loan are proposed in Section 5 with columns identical to Auto Loan
- HELOC exists in `AcctTypeEnum` but is unimplemented
- Credit Card exists in `AcctTypeEnum` but is unimplemented
- The amortization engine is explicitly described as "loan-type-agnostic" in both the v3 addendum and Section 5

**Retirement:**
- 401(k), Roth 401(k), Traditional IRA, and Roth IRA all share a single `investment_params` table
- Growth engine consumes params; pension calculator is separate (linked to salary, not accounts)
- Type is determined by `account_type_id` on the account, not by the params table

**Investment:**
- Brokerage shares the same `investment_params` table as retirement types
- Same growth engine, same routes structure
- Contribution limits are nullable (brokerage has none; IRAs/401ks do)

**Cross-cutting:**
- `ref.account_types` has `has_parameters`, `has_amortization`, and `category_id`
- `ref_cache` maps enum members to integer IDs at startup
- The roadmap (v4.1) explicitly states the goal: "a single source of truth for 'which account types support payoff calculations' that does not require maintaining a hardcoded list of type names in the service layer"
- Section 5 acknowledges that StudentLoanParams, PersonalLoanParams, and AutoLoanParams have "identical column structure" but proposes creating separate tables anyway

---

## Phase 1: Code Investigation Summary

### Models

| Model | Table | Domain Columns | Related Tables |
|-------|-------|---------------|----------------|
| HysaParams | budget.hysa_params | apy, compounding_frequency | None |
| AutoLoanParams | budget.auto_loan_params | original_principal, current_principal, interest_rate, term_months, origination_date, payment_day | None |
| MortgageParams | budget.mortgage_params | Same 6 as auto loan + is_arm, arm_first_adjustment_months, arm_adjustment_interval_months | MortgageRateHistory, EscrowComponent |
| InvestmentParams | budget.investment_params | assumed_annual_return, annual_contribution_limit, contribution_limit_year, employer_contribution_type, employer_flat_percentage, employer_match_percentage, employer_match_cap_percentage | None |

### Services

| Service | Hardcoded Types? | Generic-Ready? | Key Dependencies |
|---------|-----------------|----------------|------------------|
| amortization_engine | No | YES | Decimal, date values only |
| interest_projection | No | YES | balance, apy, compounding_frequency string |
| growth_engine | No | YES | period objects with .id/.start_date/.end_date |
| balance_calculator | YES (HYSA, MORTGAGE, AUTO_LOAN) | PARTIAL | Routing hardcoded; individual functions generic |
| pension_calculator | No | YES | Raise objects with standard attributes |
| retirement_gap_calculator | No | YES | Dict-based projections with is_traditional bool |

The balance calculator is the orchestration point where account type routing happens. It contains explicit if/elif chains comparing against `AcctTypeEnum` values and routing to the correct calculation function. All other services are fully generic calculation engines.

### Routes

| File | Routes | Lines | Type-Specific Logic |
|------|--------|-------|---------------------|
| accounts.py | 19 | ~820 | create_account() if/elif chain (6 branches), HYSA detail/update (2 routes) |
| auto_loan.py | 3 | ~175 | 7 patterns duplicated with mortgage.py |
| mortgage.py | 7 | ~475 | Same 7 patterns + escrow (2 routes), rate history (1), payoff calc (1) |
| investment.py | 3 | ~480 | No type validation -- serves all investment/retirement types |
| retirement.py | 8 | ~735 | Pension CRUD, gap analysis, hardcoded TRADITIONAL_TYPE_ENUMS |
| savings.py | 6 | ~630 | 6 type-specific loading blocks, 4 type-specific processing branches |
| settings.py | 2 | ~110 | Account type display only |

### Schemas

- AutoLoanParams vs MortgageParams: core fields identical, mortgage adds ARM fields and has wider term_months range
- InvestmentParams: unique fields for employer contributions
- HysaParams: unique fields (apy, compounding_frequency)
- PayoffCalculatorSchema: already generic and shared

### Templates

| Directory | Files | Total Lines | Notes |
|-----------|-------|-------------|-------|
| auto_loan/ | 2 | ~225 | dashboard + setup |
| mortgage/ | 5 | ~582 | dashboard + setup + 3 HTMX partials |
| investment/ | 2 | ~241 | dashboard + growth chart partial |
| retirement/ | 5 | ~370 | dashboard + pension form + 3 HTMX partials |
| accounts/ (HYSA) | 1 | ~147 | hysa_detail only |
| settings/ (acct types) | 1 | ~54 | name-only management |

Auto loan vs mortgage templates: ~65-70% structural overlap.

---

## Phase 2: Analysis by Category

### ASSET Category

**1. Current parameter tables:**

| Table | Columns |
|-------|---------|
| budget.hysa_params | account_id, apy (Numeric 7,5), compounding_frequency (String 10) |

No other asset types have parameter tables.

**2. Column overlap:** N/A -- only one table exists. However, CD and Money Market would need the same `apy` column. CD would additionally need a maturity date. HSA would need `apy` plus contribution limits (similar to investment_params).

**3. Service interface:** `interest_projection.calculate_interest()` takes standalone args: `balance, apy, compounding_frequency, period_start, period_end`. Fully type-agnostic -- any object with `apy` and `compounding_frequency` attributes works.

**4. Route logic:** HYSA has 2 dedicated routes in `accounts.py` (`hysa_detail`, `update_hysa_params`). The pattern is: load account, load HysaParams, call `calculate_balances_with_interest()`, render template. Checking has a parallel route (`checking_detail`) that calls `calculate_balances()` instead. These two routes are ~80% identical in structure.

**5. Template overlap:** `hysa_detail.html` (147 lines) is a standalone template. If CD and Money Market were added, they would need nearly identical templates with minor field differences (maturity date for CD).

**6. Schema overlap:** Only `HysaParamsCreateSchema` / `HysaParamsUpdateSchema` exist. Both are minimal (2 fields).

**7. Unimplemented types:**
- **CD:** Would need apy + compounding_frequency + maturity_date + term_months
- **Money Market:** Would need apy + compounding_frequency (identical to HYSA)
- **HSA:** Would need apy + compounding_frequency + annual_contribution_limit + contribution_limit_year (hybrid of HYSA and investment)
- Under current architecture: each would require a new model, new schema, new route block, new template, new if/elif branch in savings.py, new if/elif branch in chart_data_service.py

**8. DRY assessment: Moderately duplicative potential.** Currently only one type exists, so there is no actual duplication yet. But the architecture guarantees duplication the moment a second interest-bearing asset type is added. Money Market would be a literal copy-paste of HYSA.

**9. Open/Closed assessment:** If a user creates "Money Market" through settings UI:
- `has_parameters` can be set to True -- but then what? No model exists to store params
- No route handles Money Market-specific params
- The savings dashboard has no loading block for it
- chart_data_service.py doesn't know to call `calculate_balances_with_interest()`
- **Result: dead end.** The account appears in lists but has no functional parameter support

---

### LIABILITY Category

**1. Current parameter tables:**

| Column | auto_loan_params | mortgage_params |
|--------|-----------------|-----------------|
| account_id (FK, UNIQUE) | Yes | Yes |
| original_principal (Numeric 12,2) | Yes | Yes |
| current_principal (Numeric 12,2) | Yes | Yes |
| interest_rate (Numeric 7,5) | Yes | Yes |
| term_months (Integer) | Yes (1-120) | Yes (1-600) |
| origination_date (Date) | Yes | Yes |
| payment_day (Integer, 1-31) | Yes | Yes |
| is_arm (Boolean) | -- | Yes |
| arm_first_adjustment_months (Integer) | -- | Yes |
| arm_adjustment_interval_months (Integer) | -- | Yes |
| **Related tables** | None | MortgageRateHistory, EscrowComponent |

Section 5 proposes `student_loan_params` and `personal_loan_params` with the exact same 6 core columns as `auto_loan_params` (different term_months ranges: 1-600 and 1-120 respectively).

**2. Column overlap:** 6 of 6 core columns are identical between auto_loan and mortgage (100% core overlap). Mortgage adds 3 nullable ARM columns. The proposed student_loan and personal_loan tables would add 0 new columns -- they are exact copies of auto_loan with different term_months constraints. **If all four simple installment loans are built as separate tables, there will be 4 identical tables totaling 24 duplicated columns.**

**3. Service interface:** `amortization_engine` functions take raw values: `current_principal, annual_rate, remaining_months, payment_day, term_months, origination_date`. The engine accesses these 6 attributes from any params object. It is explicitly documented as loan-type-agnostic. Escrow calculator is mortgage-specific (takes `EscrowComponent` list). These are separate services -- escrow doesn't touch the core loan params.

**4. Route logic:** `auto_loan.py` (3 routes, ~175 lines) and `mortgage.py` (7 routes, ~475 lines) share 7 major duplicated patterns:
1. Account validation helper (`_load_*_account`)
2. Chart data builder (`_build_chart_data`) -- identical function
3. Dashboard structure (load params, check setup, calc remaining, calc summary, generate schedule, build chart, render)
4. Create params (validate type, check existing, convert percentage, create model, redirect)
5. Update params (load via helper, validate, convert percentage, update fields, redirect)
6. Percentage conversion logic
7. Schema handling pattern

Mortgage adds: rate history tracking (1 route), escrow management (2 routes), payoff calculator (1 route). These are genuinely mortgage-specific features, but the payoff calculator could serve any loan type.

**5. Template overlap:** ~65-70% structural overlap between auto_loan and mortgage templates.
- `setup.html`: Nearly identical (86 vs 95 lines). Mortgage adds ARM checkbox and wider term_months range.
- `dashboard.html`: Auto loan (139 lines) is a simpler version of mortgage's Overview tab (279 lines). Mortgage adds tabbed interface with Escrow, Rate History, and Payoff Calculator tabs.
- Mortgage has 3 additional HTMX partials (escrow list, rate history, payoff results) that auto loan lacks.

**6. Schema overlap:** Create schemas share 6 identical fields. Mortgage adds 3 optional ARM fields. Update schemas share 3 identical fields (current_principal, interest_rate, payment_day). Mortgage adds ARM update fields. The `PayoffCalculatorSchema` is already generic and shared.

**7. Unimplemented types:**
- **Student Loan:** Identical to auto loan. Section 5 proposes a separate table -- pure duplication.
- **Personal Loan:** Identical to auto loan. Section 5 proposes a separate table -- pure duplication.
- **HELOC:** Revolving credit -- needs variable balance, draw period, repayment period, credit limit. Not a simple installment loan.
- **Credit Card:** Revolving credit -- needs balance, APR, minimum payment formula, credit limit. Fundamentally different from installment loans.
- Under current architecture: each installment loan requires a new model file, migration, route file, template directory, schema pair, if/elif branch in accounts.py, loading block in savings.py, and type check in chart_data_service.py.

**8. DRY assessment: Heavily duplicative.** auto_loan.py and mortgage.py already duplicate 7 patterns. Section 5 proposes creating 2 more copies. The amortization engine is already type-agnostic, but the wiring around it forces per-type boilerplate. Evidence:
- `_build_chart_data()` is copy-pasted verbatim between route files
- Account validation helpers are structurally identical
- Create/update flows differ only in model class name and allowed field list
- Schemas differ only in term_months range and ARM fields

**9. Open/Closed assessment:** If a user creates "Business Loan" through settings UI:
- They can set `has_parameters=True` -- but no model exists
- They can set `has_amortization=True` -- but chart_data_service.py doesn't check it; it checks for MORTGAGE and AUTO_LOAN by type ID
- accounts.py `create_account()` doesn't know how to redirect to a setup page
- savings.py has no loading block for it
- **Result: dead end.** The flags exist but nothing reads them generically.

---

### RETIREMENT Category

**1. Current parameter tables:**

| Table | Columns |
|-------|---------|
| budget.investment_params | account_id, assumed_annual_return (Numeric 7,5), annual_contribution_limit (Numeric 12,2 nullable), contribution_limit_year (Integer nullable), employer_contribution_type (String 20), employer_flat_percentage (Numeric 5,4 nullable), employer_match_percentage (Numeric 5,4 nullable), employer_match_cap_percentage (Numeric 5,4 nullable) |

All 4 retirement types (401k, Roth 401k, Traditional IRA, Roth IRA) share this single table.

**2. Column overlap:** 100% -- all types use the same table. Type-specific behavior (contribution limits, employer eligibility) is driven by the account's type, not by different columns. IRAs don't use employer fields (they stay NULL/none). Brokerage doesn't use contribution limits (stays NULL).

**3. Service interface:** `growth_engine.project_balance()` takes: `current_balance, assumed_annual_return, periods, periodic_contribution, employer_params (dict), annual_contribution_limit, ytd_contributions_start`. Fully type-agnostic. `pension_calculator` is separate -- linked to salary profiles, not to investment_params.

**4. Route logic:** `retirement.py` (8 routes, ~735 lines) handles the unified retirement dashboard, pension CRUD, gap analysis, and settings. `investment.py` (3 routes, ~480 lines) handles per-account dashboards and params updates. Investment routes serve both retirement and investment category accounts with no type-specific branching -- the same dashboard renders for 401(k) and Brokerage.

**5. Template overlap:** Investment dashboard (227 lines) renders the same for all 5 types. Retirement dashboard (165 lines) aggregates all retirement+investment accounts. Templates use conditional display (e.g., contribution limit section only shows if limit is set) rather than type-specific blocks.

**6. Schema overlap:** Single schema pair (`InvestmentParamsCreateSchema`, `InvestmentParamsUpdateSchema`) serves all 5 types. No duplication.

**7. Unimplemented types:** `529 Plan` exists in `AcctTypeEnum`. It would use the same `investment_params` table (assumed return, contribution limit). Adding it requires: adding the type ID to the set of investment types in `create_account()` and the retirement gap calculator's `TRADITIONAL_TYPE_ENUMS` set (or not, since 529 is neither). That's 1-2 lines of code.

**8. DRY assessment: DRY-compliant.** One table, one model, one schema pair, one set of routes, one dashboard template. The only duplication is the hardcoded set of type IDs in `create_account()` (5 enum members listed) and similar sets in savings.py and retirement.py.

**9. Open/Closed assessment:** If a user creates "SEP IRA" through settings UI:
- `has_parameters=True` -- but `create_account()` only auto-creates InvestmentParams for 5 hardcoded type IDs
- The investment dashboard would work if InvestmentParams existed
- Gap analysis would miss it because it queries by hardcoded category IDs and type ID sets
- **Result: partially broken.** The shared table is the right design, but the wiring uses hardcoded type ID lists instead of metadata-driven queries.

---

### INVESTMENT Category

**1-6:** Same as Retirement -- shares the same table, model, schema, routes, and templates. Brokerage is the only current investment type. It uses `investment_params` with employer fields set to none/NULL and no contribution limit.

**7. Unimplemented types:** No additional investment types exist in `AcctTypeEnum` beyond Brokerage.

**8. DRY assessment: DRY-compliant** (inherits the retirement/investment shared design).

**9. Open/Closed assessment:** Same issues as retirement. A user-created "Crypto" investment type would need InvestmentParams auto-created, which only happens for 5 hardcoded type IDs.

---

### Cross-cutting Analysis

**10. Balance calculator coupling:**

The balance calculator functions themselves (`calculate_balances`, `calculate_balances_with_interest`, `calculate_balances_with_amortization`) are generic. The coupling is in `chart_data_service.py` which routes to the correct function:

```python
if acct_type_id == ref_cache.acct_type_id(AcctTypeEnum.HYSA) and account.hysa_params:
    -> calculate_balances_with_interest()
elif acct_type_id in (MORTGAGE, AUTO_LOAN):
    -> calculate_balances_with_amortization()
else:
    -> calculate_balances()
```

This is a hardcoded if/elif chain on specific type IDs. It does NOT use `has_parameters`, `has_amortization`, or `category_id`. Adding student loan support requires modifying this chain. Adding a user-created loan type is impossible without code changes.

**11. Account creation flow:**

`create_account()` in accounts.py has this redirect chain:

```
if HYSA -> auto-create HysaParams, redirect to hysa_detail?setup=1
elif K401/ROTH_401K/TRAD_IRA/ROTH_IRA/BROKERAGE -> auto-create InvestmentParams, redirect to investment.dashboard?setup=1
elif MORTGAGE -> redirect to mortgage.dashboard?setup=1
elif AUTO_LOAN -> redirect to auto_loan.dashboard?setup=1
elif has_parameters=True -> log warning "no setup route", redirect to accounts list
else -> redirect to accounts list
```

6 type-specific branches. The `has_parameters=True` fallback explicitly logs a warning that no setup route exists -- this is the dead end for user-created types.

**12. Savings dashboard loading:**

savings.py has 6 type-specific loading blocks:
1. HYSA params query (by type ID)
2. Mortgage type ID lookup
3. Auto Loan type ID lookup
4. Investment params query (by 5 hardcoded type IDs)
5. Paycheck deductions for investment accounts
6. Loan params aggregation (mortgage + auto loan into combined map)

Then the per-account processing loop has 4 type-specific branches:
- HYSA -> `calculate_balances_with_interest()`
- Loan (mortgage/auto) -> amortization engine projections
- Investment -> growth engine projections
- Generic -> basic balance projections

**13. Settings UI gap:**

Current settings UI for account types allows:
- **Create:** Name only. `category_id` is hardcoded to ASSET. `has_parameters` is accepted by the schema but the template doesn't have a checkbox for it. `has_amortization` is not exposed.
- **Update:** Name only.
- **Delete:** Only if not in use.

**User-created "CD" (Asset category) trace:**
1. User creates "CD" in settings -> row added to ref.account_types with category=ASSET, has_parameters=False
2. User creates account with type "CD" -> no params auto-created, redirects to accounts list
3. CD appears in savings dashboard as generic account with basic balance tracking
4. No interest projection, no maturity tracking, no APY -- it's just a named balance
5. **Dead end at step 2.** No setup flow, no params storage, no service integration.

**User-created "Credit Card" (would need Liability category) trace:**
1. User creates "Credit Card" in settings -> category hardcoded to ASSET (wrong)
2. Even if category were correct, no has_amortization flag exposed
3. Account creation has no redirect for this type
4. **Dead end at step 1.** Can't even set the correct category.

**User-created "Crypto Wallet" (Investment-like) trace:**
1. User creates "Crypto Wallet" -> category hardcoded to ASSET (wrong)
2. Even if category were Investment, create_account() only auto-creates InvestmentParams for 5 specific type IDs
3. **Dead end at step 1.** Wrong category, no params.

---

## Phase 3: Proposed Options

### Liability Category (needs the most work)

#### Option A: Unified `loan_params` table (full unification)

One table replaces `auto_loan_params`, `mortgage_params`, and the proposed `student_loan_params`/`personal_loan_params`:

```
budget.loan_params:
  id, account_id (FK UNIQUE), original_principal, current_principal,
  interest_rate, term_months, origination_date, payment_day,
  is_arm (default false), arm_first_adjustment_months (nullable),
  arm_adjustment_interval_months (nullable),
  created_at, updated_at
```

- `MortgageRateHistory` and `EscrowComponent` remain as separate tables FK'd to `account_id` (not to loan_params) -- they already are.
- One model class: `LoanParams`.
- One generic route file: `loans.py` with conditional rendering of mortgage-specific features based on `account_type_id` or `has_amortization` + additional metadata.
- One set of templates with conditional blocks (escrow tab for mortgage, ARM fields for mortgage).
- One schema pair with validators parameterized by account type (term_months range varies).

**Files created:** `app/models/loan_params.py`, `app/routes/loans.py`, `app/templates/loans/` (dashboard, setup, partials)
**Files deleted:** `app/models/auto_loan_params.py`, `app/routes/auto_loan.py`, `app/templates/auto_loan/`
**Files modified:** `app/models/mortgage_params.py` -> renamed to `loan_params.py`, `app/routes/mortgage.py` -> merged into `loans.py`, migration to rename table and consolidate, `accounts.py` create flow, `savings.py` loading, `chart_data_service.py` routing, all mortgage/auto_loan test files
**Breaks:** All existing mortgage and auto loan routes, templates, and tests. Must rebuild and re-verify.
**Effort:** High (but avoids growing debt -- every new loan type is now zero-code)
**DRY:** Excellent -- one table, one model, one route file, one template set
**Open/Closed:** A user-created "Business Loan" with `has_amortization=True` gets full amortization support with zero code changes (params stored in `loan_params`, amortization engine called via `has_amortization` flag)

#### Option B: Keep mortgage separate, unify simple installment loans

Mortgage keeps its own table (justified by ARM, escrow, rate history complexity). A new `installment_loan_params` table serves auto loan, student loan, personal loan, and any future simple fixed-rate loans.

```
budget.installment_loan_params:
  id, account_id (FK UNIQUE), original_principal, current_principal,
  interest_rate, term_months, origination_date, payment_day,
  created_at, updated_at
```

- Mortgage retains `mortgage_params` with ARM columns
- New generic `installment_loans.py` route file handles auto/student/personal
- Mortgage keeps its own routes

**Files created:** `app/models/installment_loan_params.py`, `app/routes/installment_loans.py`, `app/templates/installment_loans/`
**Files deleted:** `app/models/auto_loan_params.py`, `app/routes/auto_loan.py`, `app/templates/auto_loan/`
**Files modified:** `accounts.py`, `savings.py`, `chart_data_service.py`, auto loan tests
**Breaks:** Auto loan routes and tests (mortgage untouched)
**Effort:** Medium
**DRY:** Good for simple loans. Mortgage remains isolated. But now there are TWO loan patterns to maintain, and the payoff calculator is duplicated or must be shared.
**Open/Closed:** User-created simple installment loans work. But adding a new loan type with ARM-like features requires knowing to use mortgage_params instead.

#### Option C: Strategy/registry pattern on current tables (no schema change)

Keep per-type tables but extract shared logic:
- Base model mixin with shared columns
- Generic route helper functions (load_loan_account, build_chart_data, etc.)
- Shared templates with includes
- Registry dict mapping type_id -> (model_class, schema_class, template_dir)

**Files created:** `app/models/mixins.py`, `app/utils/loan_helpers.py`
**Files modified:** All loan route files, all loan model files
**Breaks:** Nothing -- refactor only
**Effort:** Low-medium
**DRY:** Moderate improvement. Reduces code duplication in routes but database schema still has 4 identical tables.
**Open/Closed:** Still requires new model + migration + route file + template dir for each new loan type. Does not solve the user-created type problem at all.

#### Assessment

Option C does not solve the stated problem. Option B creates an unnecessary split -- if mortgage can have nullable ARM columns in a shared table, so can any future loan with unique features. **Option A is the correct approach.** The ARM columns are 3 nullable fields. Escrow and rate history are already separate tables FK'd to account_id. There is no technical reason mortgage needs its own params table.

---

### Asset Category

#### Option A: Unified `interest_bearing_params` table

```
budget.interest_bearing_params:
  id, account_id (FK UNIQUE), apy (Numeric 7,5),
  compounding_frequency (String 10, default 'daily'),
  maturity_date (Date, nullable),                       -- CD only
  term_months (Integer, nullable),                       -- CD only
  annual_contribution_limit (Numeric 12,2, nullable),    -- HSA only
  contribution_limit_year (Integer, nullable),            -- HSA only
  created_at, updated_at
```

- One model, one route, one template with conditional fields
- Interest projection service already takes standalone args -- no changes needed
- HYSA, CD, Money Market, HSA all stored here
- HSA's contribution limit fields mirror investment_params (but HSA earns interest, not growth)

**Effort:** Medium (HYSA migration + new route/template generalization)
**DRY:** Eliminates future duplication before it starts
**Open/Closed:** User-created "Christmas Club Savings" with `has_parameters=True` and category=Asset gets interest tracking with zero code changes

#### Option B: Keep HYSA as-is, add types individually

Current architecture. Each new interest-bearing asset gets its own table.

**Effort:** Low per-type (but grows linearly)
**DRY:** Increasingly duplicative with each type
**Open/Closed:** Does not solve user-created types

#### Assessment

Option A is correct. The column set is small, the nullable additions are minimal, and it prevents the same mistake the liability category already demonstrates.

---

### Retirement/Investment Category

**Current architecture assessment:** This category is already well-designed. One table serves 5 types. Routes are shared. Templates are shared. The growth engine is type-agnostic.

**Issues found (minor):**
1. `create_account()` hardcodes 5 type IDs for InvestmentParams auto-creation instead of using `category_id` + `has_parameters`
2. `savings.py` hardcodes the same 5 type IDs for params loading
3. `retirement.py` hardcodes `TRADITIONAL_TYPE_ENUMS = {K401, TRADITIONAL_IRA}` for tax treatment
4. A user-created "SEP IRA" would not get InvestmentParams auto-created

**Proposed fix (minor improvements, not rework):**
- Replace hardcoded type ID sets with queries against `ref.account_types` metadata: `WHERE category_id IN (retirement_cat_id, investment_cat_id) AND has_parameters = TRUE`
- Add a `is_tax_deferred` boolean to `ref.account_types` (or use the existing category to infer: traditional = tax-deferred, Roth = not). This replaces the hardcoded `TRADITIONAL_TYPE_ENUMS` set.
- These are small, targeted changes that make the already-good architecture fully extensible

**Effort:** Low
**DRY:** Already compliant
**Open/Closed:** Fixes the remaining hardcoded type lists

---

### Cross-cutting Infrastructure

**Balance calculator / chart_data_service routing:**

Current:
```python
if type == HYSA: -> interest
elif type in (MORTGAGE, AUTO_LOAN): -> amortization
else: -> basic
```

Proposed:
```python
account_type = account.account_type  # eager-loaded relationship
if account_type.has_amortization and loan_params:
    -> amortization
elif account_type.has_parameters and interest_params:
    -> interest
elif account_type.has_parameters and investment_params:
    -> growth
else:
    -> basic
```

This uses the metadata flags on `ref.account_types` instead of hardcoded type IDs. Adding a new type means setting the right flags -- no code changes.

However, this still needs to know which params table to query. With unified tables (loan_params, interest_bearing_params, investment_params), there are only 3 params relationships to check, and the flags tell you which one applies.

**Account creation flow:**

Replace the if/elif chain with metadata-driven routing:

```python
acct_type = db.session.get(AccountType, acct_type_id)
if acct_type.has_amortization:
    -> redirect to loans.dashboard?setup=1
elif acct_type.category_id == asset_cat_id and acct_type.has_parameters:
    -> auto-create interest_bearing_params, redirect to interest_bearing.dashboard?setup=1
elif acct_type.category_id in (retirement_cat_id, investment_cat_id) and acct_type.has_parameters:
    -> auto-create investment_params, redirect to investment.dashboard?setup=1
else:
    -> redirect to accounts list
```

3 branches based on metadata vs. 6+ branches based on specific type IDs.

**Savings dashboard:**

Replace 6 type-specific loading blocks with 3 category-aware queries:
1. Load all loan_params for accounts where `account_type.has_amortization = True`
2. Load all interest_bearing_params for asset accounts where `account_type.has_parameters = True`
3. Load all investment_params for retirement/investment accounts where `account_type.has_parameters = True`

**Settings UI:**

Must be extended to expose:
- `category_id` dropdown (Asset, Liability, Retirement, Investment)
- `has_parameters` checkbox
- `has_amortization` checkbox (only when category=Liability)
- `is_tax_deferred` checkbox (only when category=Retirement)

This turns the settings UI from a dead-end name editor into a functional account type configuration tool.

---

## Phase 4: Recommendation

### Per-category Verdict

| Category | Verdict | Rationale |
|----------|---------|-----------|
| Asset | Significant rework | Unify to `interest_bearing_params`. Current single-type table will duplicate the moment CD or Money Market is added. |
| Liability | Significant rework | Unify to `loan_params` (Option A). 6 identical columns, 7 duplicated route patterns, ~65-70% template overlap. Section 5's proposal to create 2 more identical tables would triple the duplication. |
| Retirement | Minor improvements | Replace hardcoded type ID lists with metadata-driven queries. Add `is_tax_deferred` flag. Shared-table design is already correct. |
| Investment | Minor improvements | Same as retirement -- fix hardcoded type ID lists. |
| Cross-cutting | Required infrastructure | Update chart_data_service.py, create_account(), savings.py, and settings UI to use metadata flags instead of hardcoded type IDs. |

### Sequencing

**Phase 1: Cross-cutting infrastructure (do first)**
1. Add `is_tax_deferred` column to `ref.account_types` (migration)
2. Update settings UI to expose `category_id`, `has_parameters`, `has_amortization`, `is_tax_deferred`
3. Replace hardcoded type ID lists in retirement.py with metadata queries
4. Replace hardcoded type ID sets in create_account() with metadata-driven routing

This provides immediate value and de-risks the later phases by establishing the metadata-driven pattern.

**Phase 2: Liability unification (highest duplication, most types)**
1. Create `loan_params` table via migration (add columns for ARM, copy data from auto_loan_params and mortgage_params)
2. Create unified `LoanParams` model
3. Create generic `loans.py` routes with conditional mortgage features
4. Create unified `loans/` template directory
5. Update chart_data_service.py to use `has_amortization` flag
6. Update savings.py to load loan_params generically
7. Migrate existing auto_loan and mortgage tests
8. Remove old per-type models, routes, templates
9. Verify student loan and personal loan work with zero additional code (just ref table rows)

**Phase 3: Asset unification (smaller scope, same pattern)**
1. Create `interest_bearing_params` table via migration (copy data from hysa_params)
2. Create unified model and generic routes
3. Update chart_data_service.py to use `has_parameters` + category for interest routing
4. Verify CD and Money Market work with zero additional code

**Phase 4: Retirement/Investment cleanup (minor)**
1. Replace hardcoded type ID sets with metadata queries in investment.py and savings.py
2. Verify user-created retirement/investment types work end-to-end

### Incremental Migration Strategy

Each phase can be done incrementally:
- Build the new unified system alongside the old one
- Migrate one existing type to prove it works (e.g., auto loan -> loan_params first)
- Run full test suite to verify equivalence
- Then migrate remaining types
- Delete old code only after all types are migrated and tests pass

### Shared Infrastructure Benefits

The cross-cutting phase (Phase 1) benefits all categories:
- Settings UI changes enable user-created types in every category
- Metadata-driven routing in create_account() works for all categories
- chart_data_service.py flag-based routing works for all categories
- savings.py generic loading works for all categories

### Impact on Section 5

Section 5's implementation plan should be revised. Instead of creating `StudentLoanParams` and `PersonalLoanParams` as separate tables:
- Build the unified `loan_params` table first (Phase 2 above)
- Student Loan and Personal Loan become ref table rows with `has_amortization=True` -- zero new models, zero new routes, zero new templates
- Section 5's other work (payment linkage, savings goals, payoff calculator) proceeds as planned but against the unified infrastructure

### Test Coverage Impact

The unified approach makes testing easier:
- One set of model tests covers all loan types (parameterized by type)
- One set of route tests covers the generic loan flow (parameterized by type)
- Type-specific features (ARM, escrow) get targeted tests
- Adding a new loan type requires zero new test files -- just adding the type to the parameterized test matrix

### Revolving Credit Consideration

The proposed `loan_params` table handles installment loans. Revolving credit (credit cards, HELOCs used as lines of credit) would need different fields: credit_limit, minimum_payment_formula, current_balance (no fixed term, no amortization schedule). Two approaches:

1. **Separate `revolving_credit_params` table** -- Clean separation between installment and revolving. `has_amortization=False` on the account type distinguishes them.
2. **Extend `loan_params` with nullable revolving fields** -- credit_limit, min_payment_percentage. Installment loans ignore these; revolving credit ignores term_months/origination_date.

Recommendation: approach 1 (separate table) because the data models are genuinely different -- installment loans amortize to zero over a fixed term while revolving credit has no natural endpoint. Mixing them creates confusion about which fields apply. The `has_amortization` flag on `ref.account_types` cleanly separates them: `True` -> loan_params + amortization engine, `False` -> revolving_credit_params + minimum payment calculator.

This can be deferred until revolving credit support is actually needed. The architecture supports it without any changes to what we build now.

---

### Summary

| Category | Tables Today | Tables After | Route Files Today | Route Files After |
|----------|-------------|-------------|-------------------|-------------------|
| Asset | 1 (hysa_params) | 1 (interest_bearing_params) | inline in accounts.py | 1 (interest_bearing.py) |
| Liability | 2 (auto_loan, mortgage) | 1 (loan_params) | 2 (auto_loan.py, mortgage.py) | 1 (loans.py) |
| Retirement | 1 (investment_params) | 1 (investment_params) | 1 (retirement.py) | 1 (retirement.py) |
| Investment | shared with retirement | shared with retirement | 1 (investment.py) | 1 (investment.py) |

**Net result:** 4 param tables -> 3 param tables. 5 route files -> 4 route files. But more importantly: adding a new account type goes from "create model + migration + route file + template dir + schema + 5 if/elif updates + tests" to "insert a row in ref.account_types with the right flags."
