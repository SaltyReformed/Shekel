# First-Run Experience, Onboarding Wizard, and Dependency-Aware Flash Messages

**Date:** 2026-03-19
**Status:** Draft

---

## 1. Dependency Map

Every user-facing action that creates data has prerequisites. This table shows the full dependency chain, what currently provides each prerequisite, and what happens when it is missing.

| Action | Prerequisites | Current Provider | Failure When Missing |
|--------|--------------|-----------------|---------------------|
| **Register / Create user** | Reference tables seeded (AccountType "checking" must exist) | `seed_ref_tables.py` | `sqlalchemy.exc.NoResultFound` crash on `AccountType.filter_by(name="checking").one()` in `auth_service.register_user()` |
| **Log in** | User must exist | `seed_user.py` or registration | "Invalid email or password." flash (correct behavior) |
| **View budget grid** | Baseline scenario | `seed_user.py` → Scenario | Renders `grid/no_setup.html`: "Run the seed script" (tells user to run CLI command) |
| **View budget grid (with data)** | Baseline scenario + pay periods | Scenario from seed, pay periods from user | Renders `grid/no_periods.html` (acceptable) |
| **Generate pay periods** | User must be logged in | Auth | Login redirect (correct) |
| **Create account** | User must be logged in, AccountType ref data | Auth + `seed_ref_tables.py` | No failure (account types always available if ref tables seeded) |
| **Create salary profile** | Baseline scenario + active account + Income:Salary category (auto-created) + pay periods (for transaction generation) | Scenario from seed, account from seed, category auto-created in route | `salary.py:161` -- `"No baseline scenario found. Set up your budget first."` → redirect to salary list. `salary.py:190` -- `"No active account found."` → redirect to salary list. No link to fix either. |
| **Create recurring transaction** | Account + category (for form dropdowns) + baseline scenario (for transaction generation) | Account from seed, categories from seed, scenario from seed | Form renders with empty dropdowns (no accounts or categories). No flash or error -- just an unusable form. |
| **Create transfer template** | 2+ active accounts | Seed creates 1 account; user must create a 2nd | Form renders with only 1 account in both From and To dropdowns. No validation prevents same-account transfer. |
| **Create savings goal** | At least one account | Account from seed | No guard; form will render but has no useful target. |

### Full Dependency Chain (Correct Order)

```
1. Reference tables (AccountType, Status, TransactionType, etc.)
   └── Provided by: seed_ref_tables.py (entrypoint step 4)

2. User account (email, password, display_name)
   └── Currently: seed_user.py  |  Future: in-app registration

3. User settings (grid defaults, inflation rate, etc.)
   └── Currently: seed_user.py  |  Future: auto-provisioned at registration
   └── auth_service.register_user() already creates this

4. Baseline scenario
   └── Currently: seed_user.py  |  Future: auto-provisioned at registration
   └── auth_service.register_user() already creates this

5. Default checking account
   └── Currently: seed_user.py  |  Future: auto-provisioned at registration
   └── auth_service.register_user() already creates this

6. Default categories (22 items across 7 groups)
   └── Currently: seed_user.py only
   └── auth_service.register_user() does NOT create these

7. Pay periods (biweekly schedule)
   └── Requires user input: start date, count, cadence
   └── Currently: onboarding banner step 1

8. Salary profile (optional)
   └── Requires: account + scenario + pay periods
   └── Currently: onboarding banner step 2

9. Recurring transactions (optional)
   └── Requires: account + categories + scenario
   └── Currently: onboarding banner step 3
```

---

## 2. Seed Script Inventory

`scripts/seed_user.py` creates the following resources for a new user:

| Resource | Can Auto-Provision? | Needs User Input? | Notes |
|----------|:------------------:|:-----------------:|-------|
| User (email, password, display_name) | No | Yes | Registration form handles this |
| UserSettings | Yes | No | All fields have model defaults. `auth_service.register_user()` already creates this. |
| Checking account (name="Checking", balance=0) | Yes | Partially | Name and type can be defaulted. User might want to set initial balance. |
| Baseline scenario (name="Baseline", is_baseline=True) | Yes | No | Always needed, always the same. `auth_service.register_user()` already creates this. |
| 22 default categories (7 groups) | Yes | No | Good starting set. User can customize later. |

### What `auth_service.register_user()` Already Creates

The existing `register_user()` function (lines 96-164) already auto-provisions 3 of the 5 seed script items:

1. User (from form input)
2. UserSettings (with model defaults)
3. Checking account (name="Checking", balance=0)
4. Baseline scenario (name="Baseline", is_baseline=True)

**Missing from `register_user()`:** Default categories (the 22 items from seed_user.py).

### Gap Analysis

| Item | `seed_user.py` | `auth_service.register_user()` | Gap |
|------|:-:|:-:|-----|
| User | Yes | Yes | None |
| UserSettings | Yes | Yes | None |
| Checking account | Yes | Yes | None |
| Baseline scenario | Yes | Yes | None |
| Default categories | Yes | **No** | Must add to `register_user()` |

---

## 3. First-Run Flow Map

### Current Flow (Broken for Non-Technical Users)

```
User opens browser → /login → no users exist → login fails
                                                ↓
                          User must: edit .env, run seed script, restart container
                                                ↓
                          Login → grid with no_setup.html → "run the seed script"
                          (should not happen if seed ran, but does for fresh register users)
```

### Proposed Flow (Zero CLI)

```
User opens browser
    ↓
/login detects no users exist → shows "Create an account" link
    ↓
/register → user fills form → register_user() creates:
    - User + UserSettings
    - Checking account
    - Baseline scenario
    - 22 default categories     ← NEW
    ↓
Redirect to /login → user logs in
    ↓
Grid renders (baseline scenario exists, so no_setup.html is NOT shown)
    ↓
Onboarding banner shows with dependency-aware steps:
    Step 1: ✓ Account created (auto-provisioned)
    Step 2: Generate pay periods ← link to Settings > Pay Periods
    Step 3: (locked) Set up salary profile -- requires pay periods first
    Step 4: (locked) Set up recurring transactions -- requires pay periods first
    ↓
User generates pay periods → steps 3 & 4 unlock
    ↓
User optionally sets up salary and recurring transactions
    ↓
Banner disappears when all steps complete
```

### Docker Entrypoint Changes

```
BEFORE:                              AFTER:
1. Wait for PostgreSQL               1. Wait for PostgreSQL
2. Create schemas                    2. Create schemas
3. Initialize database               3. Initialize database
4. Seed reference data               4. Seed reference data
5. Create seed user (if SEED_*)      5. Seed tax brackets
6. Seed tax brackets                 6. Start application
7. Copy static files                 7. (seed user step REMOVED)
8. Start application
```

The `SEED_USER_*` environment variables become unnecessary. The entrypoint only handles infrastructure (schemas, migrations, reference data). User creation happens in the browser.

---

## 4. Current Flash Message Audit

Every prerequisite-failure flash message in the codebase, with its file location and what's wrong:

| File:Line | Flash Message | Trigger | What's Wrong |
|-----------|--------------|---------|-------------|
| `salary.py:149` | `"Please correct the highlighted errors and try again."` | Marshmallow validation failure on salary create | Generic. Doesn't say which fields or what's wrong. |
| `salary.py:161` | `"No baseline scenario found. Set up your budget first."` | No baseline scenario when creating salary profile | "Set up your budget" is vague. No link. User doesn't know what a "baseline scenario" is or how to create one. |
| `salary.py:190` | `"No active account found."` | No active account when creating salary profile | No link to `/accounts/new`. Doesn't explain what kind of account is needed. |
| `salary.py:540` | `"No pay periods found."` | Trying to view salary breakdown with no pay periods | No link to pay period generation. |
| `grid.py:50` | Renders `no_setup.html` | No baseline scenario | Page says "Run the seed script" with `<code>python scripts/seed_user.py</code>`. Completely broken for Docker/non-technical users. |
| `grid.py:68` | Renders `no_periods.html` | No pay periods | This template was not examined but presumably asks user to generate pay periods. Should link to the generation page. |
| `templates.py:87-88` | `"Please correct the highlighted errors and try again."` | Marshmallow validation failure on template create | Generic validation error. Same pattern across all routes. |
| `transfers.py:97-98` | `"Please correct the highlighted errors and try again."` | Marshmallow validation failure on transfer create | Same generic pattern. |
| `accounts.py:103-106` | `"Please correct the highlighted errors and try again."` | Marshmallow validation failure on account create | Same generic pattern. |

### Routes with Silent Prerequisite Failures (No Flash, Just Broken UI)

| Route | What Happens | Problem |
|-------|-------------|---------|
| `templates.new_template` | Form renders with empty account and category dropdowns if user has none | User sees a form they can't fill out. No explanation. |
| `transfers.new_transfer_template` | Form renders with too few accounts to make a useful transfer | No warning that 2+ accounts are needed. |

---

## 5. Solution Options

### 5A. First-Run Experience (Eliminating the Seed Script)

#### Option A1: Registration + Auto-Provisioning (Recommended)

**Description:** Add default categories to the existing `auth_service.register_user()` function. The login page shows a "Create an account" link (already exists). The Docker entrypoint drops the `seed_user.py` step. The `no_setup.html` template is replaced with guidance for non-technical users.

**Changes:**
- `app/services/auth_service.py` -- add default category creation to `register_user()`
- `app/templates/grid/no_setup.html` -- replace "run the seed script" with "Create an account" link to `/register`
- `entrypoint.sh` -- remove the `SEED_USER_*` block (steps 5-6 become just step 5: tax brackets)
- `docker-compose.yml` -- remove `SEED_USER_*` environment variables
- `.env.example` -- remove `SEED_USER_*` variables

**Pros:**
- Smallest change. `register_user()` already creates 4 of 5 items.
- Registration page and route already exist and work.
- Login page already links to registration.
- Forward-compatible with multi-user (every new user gets the same provisioning).
- No new routes, no wizard, no multi-step flow.

**Cons:**
- No guided wizard experience for account naming or initial balance.
- User must discover onboarding steps from the banner.
- Default checking account is always named "Checking" with balance $0.

**Complexity:** Small
**Migration needed:** No
**Docker impact:** Simplifies entrypoint (removes seed user step and env vars)

#### Option A2: First-Run Setup Wizard

**Description:** When no users exist, all routes redirect to `/setup`. A multi-step wizard handles: account creation → account naming/balance → pay period generation → (optional) salary → (optional) recurring transactions.

**Changes:**
- New route: `app/routes/setup.py` (wizard blueprint)
- New templates: `app/templates/setup/step1.html` through `step5.html`
- New service: `app/services/setup_service.py` (orchestrates wizard)
- `app/__init__.py` -- add `before_request` hook to redirect to `/setup` when no users
- `app/routes/auth.py` -- exempt `/setup` routes from login requirement

**Pros:**
- Best first-run UX -- user is guided through everything.
- Eliminates both the seed script AND the onboarding banner for new users.

**Cons:**
- Significant new code (routes, templates, service, tests).
- Wizard state management (what if user closes browser mid-wizard?).
- Partially duplicates existing routes (pay period generation, account creation).
- Does not help users who register later in multi-user mode (they skip the wizard and need the banner anyway).

**Complexity:** Large
**Migration needed:** No
**Docker impact:** Same as A1 (removes seed user step)

#### Option A3: Hybrid (Auto-Provision + Short Wizard)

**Description:** On registration, auto-create everything the user doesn't need to choose (settings, scenario, categories). Then present a 2-step wizard: (1) name your checking account and set starting balance, (2) generate pay periods.

**Changes:**
- Same as A1 for auto-provisioning
- New route: `/onboarding` (2-step wizard)
- New templates: 2 wizard step templates
- Registration route redirects to `/onboarding` instead of `/login`

**Pros:**
- User gets to name their account and set a real balance.
- Simpler than full wizard (2 steps vs. 5).

**Cons:**
- Still requires new routes and templates.
- Marginal improvement over A1 -- user can rename account and set balance later from the accounts page.

**Complexity:** Medium
**Migration needed:** No
**Docker impact:** Same as A1

### 5B. Onboarding Banner Improvements

#### Option B1: Expanded Dependency-Aware Banner (Recommended)

**Description:** Keep the banner pattern but add all steps including account creation. Enforce visual dependency ordering: gray out steps whose prerequisites are not met, show "requires X first" helper text next to locked steps.

**Changes:**
- `app/__init__.py` (`inject_onboarding`) -- add checks for `has_account`, `has_categories`, `has_scenario`
- `app/templates/base.html` -- expand banner to show 5+ steps with dependency locking
- `tests/test_routes/test_onboarding.py` -- add tests for dependency locking

**Context processor additions:**
```python
has_account = db.session.query(exists().where(
    Account.user_id == uid, Account.is_active.is_(True)
)).scalar()
has_categories = db.session.query(exists().where(
    Category.user_id == uid
)).scalar()
```

**Visual design:**
```
Welcome to Shekel! Get started:

  ✓  Account created                    (auto-provisioned)
  ✓  Budget categories set up           (auto-provisioned)
  2. Generate pay periods               → link to Settings > Pay Periods
  🔒 Set up a salary profile            (requires pay periods)
  🔒 Set up recurring transactions      (requires pay periods)
```

After pay periods are generated:
```
  ✓  Account created
  ✓  Budget categories set up
  ✓  Pay periods generated
  4. Set up a salary profile            → link
  5. Set up recurring transactions      → link
```

**Pros:**
- Clear dependency order. User never tries to do step 4 before step 2.
- Shows auto-provisioned steps as already complete (builds confidence).
- Works for both first users and subsequent multi-user registrations.
- Minimal code change.

**Cons:**
- Still a passive banner. User could ignore it.
- More context processor queries (5 instead of 3). Marginal performance impact.

**Complexity:** Small
**Migration needed:** No

#### Option B2: Replace Banner with Wizard Link

**Description:** Banner becomes `"You have N setup steps remaining. Continue setup →"` linking to a wizard page.

**Pros:** Clean, simple banner. Wizard handles the details.
**Cons:** Requires building a wizard (couples to Option A2/A3).

**Complexity:** Medium (requires wizard)
**Migration needed:** No

#### Option B3: Progressive Disclosure Banner

**Description:** Show only the next required step, not all steps.

**Pros:** Reduces cognitive load.
**Cons:** User can't see the full picture. No sense of progress.

**Complexity:** Small
**Migration needed:** No

### 5C. Flash Message Improvements

#### Option C1: Contextual Flash Messages with Links (Recommended)

**Description:** Replace every vague prerequisite-failure flash with a specific message naming the missing resource and including an HTML link. Uses `Markup()` for safe HTML in flash messages.

**Changes:**
- `app/routes/salary.py:161` -- `Markup('No baseline scenario found. <a href="...">Create your budget setup</a> first.')`
- `app/routes/salary.py:190` -- `Markup('You need an active account before creating a salary profile. <a href="/accounts/new">Create an account</a>.')`
- `app/routes/salary.py:540` -- `Markup('No pay periods found. <a href="...">Generate pay periods</a> first.')`
- `app/templates/grid/no_setup.html` -- Replace "run the seed script" with registration/login guidance
- `app/templates/base.html` toast rendering -- ensure `| safe` filter is used for flash message HTML

**Pros:**
- Direct improvement. User sees exactly what to do and can click to do it.
- No new routes or templates needed.
- Works with existing toast notification system.

**Cons:**
- Flash messages with HTML need `| safe` filter (minor security consideration, but messages are developer-authored, not user-input).
- Need to audit `base.html` toast rendering to support HTML.

**Complexity:** Small
**Migration needed:** No

#### Option C2: Redirect-to-Prerequisite

**Description:** Instead of flashing and redirecting to the list page, redirect directly to the prerequisite creation page.

**Pros:** Fewest clicks for the user.
**Cons:** Surprising UX -- user tried to go to page A and ended up on page B. May be disorienting.

**Complexity:** Small
**Migration needed:** No

#### Option C3: Inline Prerequisite Warnings on Form Pages

**Description:** On form pages, show an alert banner listing unmet prerequisites with links. Disable submit button until prerequisites are met.

**Pros:** Prevents the submit-then-fail cycle entirely.
**Cons:** Requires adding checks to every form template. More template changes than C1.

**Complexity:** Medium
**Migration needed:** No

---

## 6. Recommendation

### Recommended combination: A1 + B1 + C1

**Auto-provision + Dependency-aware banner + Contextual flash messages with links.**

This is the minimum-viable solution that solves all three problems with the least new code and the lowest risk:

1. **A1 (Registration + Auto-Provisioning):** The smallest change because `register_user()` already creates 4 of 5 items. Adding default categories is ~15 lines of code. The registration page, route, and login link already exist. No wizard needed.

2. **B1 (Expanded Dependency-Aware Banner):** Adds visual dependency ordering to the existing banner pattern. Users see what's already done, what's next, and what's locked. Works for every user (first or subsequent).

3. **C1 (Contextual Flash Messages with Links):** Replaces vague messages with actionable guidance. Each message names the missing resource and links to its creation page. Small, targeted changes to ~5 flash messages.

### Why not the wizard (A2)?

- The existing registration + banner flow already covers the same ground with less code.
- A wizard would partially duplicate existing routes (account creation, pay period generation).
- The wizard only helps the first user. Subsequent users in multi-user mode would still need the banner.
- YAGNI -- the hybrid approach delivers 90% of the UX benefit at 20% of the complexity.

### Alignment with Existing Plans

| Plan | Overlap | Resolution |
|------|---------|------------|
| **Phase 8E (Multi-User Groundwork)** | 8E plans a registration page and route. | Already done. `register_form` and `register` routes exist in `auth.py`. `register_user()` in `auth_service.py` exists. 8E's registration work item is complete. Our change adds default categories to `register_user()`, which 8E would also need. |
| **UI/UX Remediation Phase 2** | Changes onboarding banner text from "Create transaction templates" to "Set up recurring transactions." | Our expanded banner supersedes the simple text change. Phase 2's banner item becomes a no-op. |
| **UI/UX Remediation Phase 3** | Consolidates settings into a dashboard with sections for categories, pay periods, etc. | Our banner links will point to `url_for('settings.show', section='pay-periods')` etc., which aligns with the consolidated settings dashboard. If Phase 3 runs first, banner links work. If our change runs first, links point to current routes (which Phase 3 will redirect). |
| **Docker Containerization Design** | Plans `SEED_USER_*` env vars and `seed_user.py` in entrypoint. | Our change removes the need for both. The Docker design should be updated: entrypoint drops step 5 (seed user), `.env.example` drops `SEED_USER_*`, `docker-compose.yml` drops those env vars. |

---

## 7. Implementation Sketch

### Files to Modify

#### Service Layer

**`app/services/auth_service.py`** -- Add default category creation to `register_user()`.

After the baseline scenario creation (line 162), add:

```python
# Create default categories.
from app.models.category import Category

DEFAULT_CATEGORIES = [
    ("Income", "Salary"),
    ("Income", "Other Income"),
    ("Home", "Mortgage/Rent"),
    ("Home", "Electricity"),
    ("Home", "Gas"),
    ("Home", "Water"),
    ("Home", "Internet"),
    ("Home", "Phone"),
    ("Home", "Home Insurance"),
    ("Auto", "Car Payment"),
    ("Auto", "Car Insurance"),
    ("Auto", "Fuel"),
    ("Auto", "Maintenance"),
    ("Family", "Groceries"),
    ("Family", "Dining Out"),
    ("Family", "Spending Money"),
    ("Family", "Subscriptions"),
    ("Health", "Medical"),
    ("Health", "Dental"),
    ("Financial", "Savings Transfer"),
    ("Financial", "Extra Debt Payment"),
    ("Credit Card", "Payback"),
]

for sort_idx, (group, item) in enumerate(DEFAULT_CATEGORIES):
    db.session.add(Category(
        user_id=user.id,
        group_name=group,
        item_name=item,
        sort_order=sort_idx,
    ))
```

This list should be a module-level constant shared between `auth_service.py` and `seed_user.py` (DRY).

#### Context Processor

**`app/__init__.py`** -- Expand `inject_onboarding` to check all prerequisites.

```python
from app.models.account import Account
from app.models.category import Category

has_account = db.session.query(
    exists().where(Account.user_id == uid, Account.is_active.is_(True))
).scalar()
has_categories = db.session.query(
    exists().where(Category.user_id == uid)
).scalar()
```

Return expanded onboarding dict:

```python
return {
    "onboarding": {
        "has_account": has_account,
        "has_categories": has_categories,
        "has_periods": has_periods,
        "has_salary": has_salary,
        "has_templates": has_templates,
        "complete": has_periods and has_salary and has_templates,
    }
}
```

#### Templates

**`app/templates/base.html`** -- Expand the welcome banner with dependency-aware steps.

Replace the current 3-item checklist (lines 140-178) with a 5-item list:

1. Account created (check `has_account`)
2. Budget categories set up (check `has_categories`)
3. Generate pay periods (check `has_periods`, link to pay period generation)
4. Set up a salary profile (check `has_salary`, locked if `!has_periods`, link to salary)
5. Set up recurring transactions (check `has_templates`, locked if `!has_periods`, link to templates)

Locked items show grayed-out text with "(generate pay periods first)" helper text.

**`app/templates/grid/no_setup.html`** -- Replace seed script message.

```html
<h3><i class="bi bi-rocket-takeoff text-primary"></i> Welcome to Shekel</h3>
<p class="mt-3">
  Your budget is almost ready. Please
  <a href="{{ url_for('auth.register_form') }}">create an account</a>
  to get started, or <a href="{{ url_for('auth.login') }}">sign in</a>
  if you already have one.
</p>
```

Note: This page should only appear if a user is logged in but has no baseline scenario. After the `register_user()` change, this should never happen for newly registered users. It would only appear for users created through some other path that bypasses `register_user()`. Consider making this a fallback that redirects to registration if no users exist, or shows a "contact your administrator" message if users exist but the current user has no scenario.

**`app/templates/base.html`** -- Update toast rendering to support HTML in flash messages.

Change line 127 from:
```html
<div class="toast-body">{{ message }}</div>
```
To:
```html
<div class="toast-body">{{ message | safe }}</div>
```

This is safe because flash messages are developer-authored (never user input).

#### Route Flash Messages

**`app/routes/salary.py`**

Line 161:
```python
from markupsafe import Markup
flash(Markup(
    'No baseline scenario found. This usually means your account setup is incomplete. '
    'Please <a href="/settings">check your settings</a> or contact support.'
), "danger")
```

Line 190:
```python
flash(Markup(
    'You need an active checking account before creating a salary profile. '
    f'<a href="{url_for("accounts.new_account")}">Create an account</a>.'
), "danger")
```

Line 540:
```python
flash(Markup(
    'No pay periods found. '
    f'<a href="{url_for("pay_periods.generate_form")}">Generate pay periods</a> first.'
), "warning")
```

**`app/templates/grid/no_periods.html`** -- Verify it links to pay period generation. If not, add a link.

#### Docker

**`entrypoint.sh`** -- Remove the seed user block (lines 27-30).

Before:
```bash
# ── 5. Create seed user (first run only) ───────────────────────
if [ -n "$SEED_USER_EMAIL" ]; then
    echo "Checking for seed user..."
    python scripts/seed_user.py
fi
```

After: (removed entirely)

**`docker-compose.yml`** -- Remove `SEED_USER_*` environment variables.

**`.env.example`** -- Remove the `SEED_USER_*` section. Add a note:
```
# User accounts are created through the app's registration page.
# No seed user variables are needed.
```

#### Tests

**New tests to add:**

1. `tests/test_services/test_auth_service.py`:
   - `test_register_user_creates_default_categories` -- verify 22 categories are created
   - `test_register_user_categories_have_correct_groups` -- verify group names

2. `tests/test_routes/test_onboarding.py`:
   - `test_banner_shows_locked_steps_when_no_periods` -- salary and template steps show locked state
   - `test_banner_unlocks_steps_when_periods_exist` -- salary and template steps show links after pay periods created
   - `test_banner_shows_account_and_categories_as_complete` -- auto-provisioned items show checkmarks

3. `tests/test_routes/test_auth.py`:
   - `test_register_creates_categories` -- end-to-end registration creates categories
   - `test_register_user_sees_banner_not_no_setup` -- after registration, grid shows banner not no_setup.html

4. `tests/test_routes/test_grid.py`:
   - `test_no_setup_page_not_shown_for_registered_user` -- registered user sees grid (or no_periods), never no_setup

5. `tests/test_routes/test_salary.py`:
   - `test_create_profile_missing_scenario_flash_has_link` -- verify flash message contains `<a href`
   - `test_create_profile_missing_account_flash_has_link` -- verify flash message contains link to create account

---

## 8. Migration Path

### For Existing Deployments (Users Who Already Have Data)

1. **No database migration needed.** All changes are in application code, templates, and the Docker entrypoint.

2. **Existing users are unaffected.** The `register_user()` changes only apply when a new user registers. Existing users already have their seed script data.

3. **Onboarding banner change is backward-compatible.** The expanded banner adds new checks (`has_account`, `has_categories`) but existing users will have both, so those items will show as completed. The banner's `complete` condition is unchanged (`has_periods and has_salary and has_templates`).

4. **Flash message changes are backward-compatible.** The new messages are more helpful but do not change any behavior.

5. **Docker entrypoint change is backward-compatible.** Removing the seed user step is safe because:
   - If a user already ran the seed script, their data exists and is untouched.
   - If a user is upgrading and has `SEED_USER_*` variables in `.env`, they simply become unused (no error).
   - New users will register through the app instead.

### Rollout Steps

1. Deploy the code changes (auth_service, context processor, templates, routes).
2. Update the Docker entrypoint to remove the seed user step.
3. Update `.env.example` and `docker-compose.yml` to remove `SEED_USER_*`.
4. Update documentation (README, Docker design doc) to reflect in-app registration.
5. `scripts/seed_user.py` is kept but documented as a development/testing tool only, not a production requirement.

### Deprecation of `seed_user.py`

The script remains useful for:
- Development environments (quick test user creation)
- CI/CD test fixtures
- Emergency user creation if the app is inaccessible

It should be updated with a docstring noting it is no longer needed for production first-run setup.
