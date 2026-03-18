# Phase 8E WU-1: Registration Service and Route

## Context

This is a personal finance application (Shekel) that manages real money
through pay-period budgeting. Bugs in this code have real financial
consequences. There is no QA team. Every line you write must be correct
the first time. Do not take shortcuts. Do not leave edge cases
untested. Do not write placeholder or stub implementations. Every test
must assert something meaningful and must actually exercise the code
path it claims to test.

Read CLAUDE.md before doing anything. Read tests/TEST_PLAN.md before
writing any tests. Follow every convention described in both files.

## What You Are Building

A user registration feature: a service function, two route handlers
(GET and POST), a template, and comprehensive tests. When a new user
registers, the system creates three database records atomically: a User,
a UserSettings row (with model defaults), and a Baseline Scenario. No
other default data is created (no accounts, categories, or pay periods).

## Pre-Existing Infrastructure You Must Understand First

Before writing any code, read these files in full. Do not skim them.

1. `app/services/auth_service.py` -- Study every function signature,
   import, and pattern. Your `register_user()` function goes in this
   file after `change_password()`. Match the existing style exactly.
   Note: `authenticate()` already imports `db` from `app.extensions`.
   Your function follows the same pattern.

2. `app/routes/auth.py` -- Study the login route structure, how flash
   messages are used, how redirects work, how exceptions are caught.
   Your registration routes go after the login route. Match the
   existing style exactly.

3. `app/templates/auth/login.html` -- Your register.html must follow
   this exact visual layout (centered card, Shekel logo, same column
   widths, same Bootstrap classes). Read it carefully.

4. `app/exceptions.py` -- Study the exception hierarchy. You will use
   `ConflictError` for duplicate emails and `ValidationError` for
   invalid input. (Note: Section 2.8 of the implementation plan
   mentions AuthError for duplicates, but the detailed WU-1 spec
   corrects this to ConflictError. Use ConflictError.)

5. `app/models/user.py` -- Study the User model columns.

6. `app/models/user_settings.py` -- Study the UserSettings model and
   its column defaults (inflation_rate, grid_periods,
   low_balance_threshold, safe_withdrawal_rate).

7. `app/models/scenario.py` -- Study the Scenario model. The baseline
   scenario needs `is_baseline=True`.

8. `tests/conftest.py` -- Study the `seed_user` fixture (lines
   ~150-213), `seed_periods` (~216-240), and `auth_client` (~243-253).
   Your tests must use these existing fixtures where appropriate.

9. `tests/test_routes/test_auth.py` -- Study the existing test class
   structure, assertion patterns, and how flash messages are checked.
   Your `TestRegistration` class goes in this file. Match the style.

10. `tests/test_services/test_auth_service.py` -- Study the existing
    tests. Your `TestRegisterUser` class goes in this file.

## Implementation Specification

### 1. Service Function: `register_user()` in `app/services/auth_service.py`

Add after `change_password()`. Signature:

```python
def register_user(email, password, display_name):
```

**Input sanitization (do this FIRST, before any validation):**

- Strip leading/trailing whitespace from `email` and `display_name`.
- Lowercase the email. Email addresses are case-insensitive per RFC 5321. If you do not lowercase, a user who registers as
  `Alice@Example.com` and later tries to log in as
  `alice@example.com` will fail. The existing `authenticate()` function
  looks up by exact email match, so storage must be normalized.
- Do NOT strip or modify the password in any way.

**Validation order (this exact order, so tests can rely on which error
fires first):**

1. Validate email format: use regex `r"^[^@\s]+@[^@\s]+\.[^@\s]+$"`.
   Note the `\s` additions compared to the plan's regex. The plan's
   regex (`r"^[^@]+@[^@]+\.[^@]+$"`) would accept emails with spaces
   like `"user @example.com"`. That is wrong. Reject if invalid with
   `ValidationError("Invalid email format.")`.

2. Validate display_name is not empty after stripping. If empty or
   whitespace-only, raise `ValidationError("Display name is required.")`.

3. Validate password length: `len(password) < 12` raises
   `ValidationError("Password must be at least 12 characters.")`.
   This matches the hardcoded minimum in `change_password()` at line 79.

4. Check email uniqueness: `User.query.filter_by(email=email).first()`.
   If exists, raise `ConflictError("An account with this email already exists.")`.
   This check comes AFTER format validation so we do not hit the
   database with garbage input.

**Object creation:**

5. Create user: `User(email=email, password_hash=hash_password(password), display_name=display_name)`.
6. `db.session.add(user)` then `db.session.flush()` to get `user.id`.
7. Create settings: `UserSettings(user_id=user.id)` -- no other args;
   model defaults handle everything.
8. Create baseline scenario:
   `Scenario(user_id=user.id, name="Baseline", is_baseline=True)`.
9. `db.session.add()` both the settings and scenario.
10. Return the user object. Do NOT call `db.session.commit()`. The
    caller (the route) handles the commit. This is critical for
    atomicity: if anything fails after `register_user()` returns but
    before the route commits, the entire registration rolls back.

**Docstring:** Must include Args, Returns, and Raises sections.
Match the docstring style used by `change_password()`.

**Imports:** Add imports for `UserSettings` and `Scenario` at the top
of the file, following the existing import style. Also import
`ConflictError` and `ValidationError` from `app.exceptions`.

### 2. Routes: `register_form()` and `register()` in `app/routes/auth.py`

Add after the login route.

**`register_form()` -- GET /register:**

1. If `current_user.is_authenticated`, redirect to `grid.grid_view`.
2. Render `auth/register.html`.

**`register()` -- POST /register:**

1. If `current_user.is_authenticated`, redirect to `grid.grid_view`.
2. Extract form fields: `email`, `display_name`, `password`,
   `confirm_password` from `request.form`.
3. If `password != confirm_password`: flash
   `"Password and confirmation do not match."` with category `"danger"`,
   redirect to `auth.register_form`.
4. Call `auth_service.register_user(email, password, display_name)`.
5. On success: `db.session.commit()`, log
   `logger.info("action=user_registered email=%s", email)`,
   flash `"Account created. Please sign in."` with category `"success"`,
   redirect to `auth.login`.
6. Catch `ConflictError` as `e`: flash `str(e)` with `"danger"`,
   redirect to `auth.register_form`.
7. Catch `ValidationError` as `e`: flash `str(e)` with `"danger"`,
   redirect to `auth.register_form`.

**Important:** The route must import `auth_service`, `db`, `ConflictError`,
and `ValidationError`. Check the existing imports at the top of
`auth.py` and add only what is missing. Do not duplicate imports.

### 3. Template: `app/templates/auth/register.html`

Create this file. It must:

- Extend `base.html`.
- Use `{% block title %}Register - Shekel{% endblock %}` (use a hyphen,
  not an em dash or en dash).
- Follow the exact same centered card layout as `login.html`:
  `row justify-content-center mt-5` > `col-md-4 col-lg-3` > `card shadow-sm`.
- Include the Shekel logo at the same size as login.
- Include a CSRF token: `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`.
- Fields: email (type="email", required, autocomplete="email"),
  display_name (type="text", required, maxlength="100"),
  password (type="password", required, minlength="12",
  autocomplete="new-password"), confirm_password (same as password).
- Password help text: `Minimum 12 characters.`
- Submit button: `Create Account` with `btn btn-primary w-100`.
- Preserve form input on validation failure using
  `value="{{ request.form.get('email', '') }}"` on email and
  display_name fields. Do NOT preserve password fields.
- Link at the bottom: `Already have an account? Sign in` pointing to
  `{{ url_for('auth.login') }}`.

### 4. Login Template Modification: `app/templates/auth/login.html`

Add a registration link after the `</form>` tag and before the closing
`</div>` of the card body:

```html
<div class="text-center mt-3">
  <a href="{{ url_for('auth.register_form') }}">Create an account</a>
</div>
```

Read the file first to find the exact insertion point. Do not guess.

## Test Requirements

### Testing Standards (from TEST_PLAN.md and CLAUDE.md)

- Every test needs a docstring explaining what is verified and why.
- Inline comments on non-obvious assertions or setup steps.
- Use `Decimal` for all monetary amounts, never `float`.
- Conform to Pylint standards: snake_case, docstrings, no unused imports.
- Run `pytest` after writing all tests. Fix ALL failures before
  reporting done. Do not report done with failing tests.

### Service Tests: `TestRegisterUser` in `tests/test_services/test_auth_service.py`

These tests call `register_user()` directly. They test the service
layer in isolation from the routes. Add this class to the existing file.

**Required tests (every one of these must be implemented):**

```
test_register_user_creates_user
```

Call `register_user("newuser@example.com", "securepass123", "New User")`.
Flush. Assert: User exists in db with correct email (lowercased),
correct display_name, `user.id` is not None. Assert: exactly one
UserSettings row exists for this user_id. Assert: exactly one Scenario
row exists for this user_id with `is_baseline=True` and
`name="Baseline"`.

```
test_register_user_password_is_bcrypt_hashed
```

Register a user. Assert: `user.password_hash` is not equal to the
plaintext password. Assert: `user.password_hash` starts with `$2b$`
(bcrypt identifier). Assert: `verify_password(plaintext, user.password_hash)`
returns True (round-trip verification).

```
test_register_user_settings_have_correct_defaults
```

Register a user. Load the UserSettings for that user. Assert each
default value matches what the model defines. Check: `inflation_rate`,
`grid_periods`, `low_balance_threshold`, `safe_withdrawal_rate`.
Look at the UserSettings model to get the exact default values. Do not
hardcode assumed values; read the model first.

```
test_register_user_email_is_lowercased
```

Call `register_user("UPPER@EXAMPLE.COM", ...)`. Assert the stored
`user.email` is `"upper@example.com"`.

```
test_register_user_email_is_stripped
```

Call `register_user("  spaced@example.com  ", ...)`. Assert stored
email is `"spaced@example.com"`.

```
test_register_user_display_name_is_stripped
```

Call `register_user(email, password, "  Padded Name  ")`. Assert stored
`display_name` is `"Padded Name"`.

```
test_register_user_duplicate_email_raises_conflict
```

Depends on `seed_user` fixture. Call `register_user` with the seed
user's email (`test@shekel.local`). Assert raises `ConflictError`.
Assert the exception message contains "already exists".

```
test_register_user_duplicate_email_case_insensitive
```

Depends on `seed_user` fixture. Call `register_user` with
`"TEST@SHEKEL.LOCAL"` (uppercased version of seed user email). Assert
raises `ConflictError`. This verifies that email lowercasing happens
before the uniqueness check.

```
test_register_user_short_password_raises_validation
```

Call with an 11-character password. Assert raises `ValidationError`.
Assert message contains "at least 12 characters".

```
test_register_user_exactly_12_chars_succeeds
```

Call with a password that is exactly 12 characters. Assert: no
exception raised, user is created successfully.

```
test_register_user_invalid_email_no_at_sign
```

Call with `"notanemail"`. Assert raises `ValidationError` with
"Invalid email format".

```
test_register_user_invalid_email_no_domain
```

Call with `"user@"`. Assert raises `ValidationError`.

```
test_register_user_invalid_email_no_tld
```

Call with `"user@domain"`. Assert raises `ValidationError`.

```
test_register_user_invalid_email_spaces
```

Call with `"user @example.com"`. After stripping, this becomes
`"user @example.com"` which still has an internal space. Assert raises
`ValidationError`.

```
test_register_user_empty_email_raises_validation
```

Call with `""`. Assert raises `ValidationError`.

```
test_register_user_empty_display_name_raises_validation
```

Call with `display_name=""`. Assert raises `ValidationError` with
"Display name is required".

```
test_register_user_whitespace_display_name_raises_validation
```

Call with `display_name="   "`. Assert raises `ValidationError` with
"Display name is required".

```
test_register_user_does_not_commit
```

Call `register_user(...)`. Before the test does its own commit, call
`db.session.rollback()`. Assert: no User exists in the database with
that email. This verifies the function honors the "caller commits"
contract.

```
test_register_user_validation_order_email_before_password
```

Call with BOTH an invalid email AND a short password. Assert raises
`ValidationError` with "email" in the message (not the password error).
This verifies validation runs in the specified order.

### Route Tests: `TestRegistration` in `tests/test_routes/test_auth.py`

These test the full HTTP request/response cycle. Add this class to the
existing test file.

**Required tests (every one of these must be implemented):**

```
test_get_register_renders_form
```

GET `/register`. Assert 200. Assert response contains "Create Account"
(the heading). Assert contains `name="email"`. Assert contains
`name="display_name"`. Assert contains `name="password"`. Assert
contains `name="confirm_password"`. Assert contains `csrf_token`.

```
test_get_register_has_login_link
```

GET `/register`. Assert response contains "Already have an account?"
and a link to `/login`.

```
test_get_login_has_register_link
```

GET `/login`. Assert response contains "Create an account" and a link
to `/register`.

```
test_register_success_creates_all_records
```

POST `/register` with valid data (email, display_name, password,
confirm_password all valid). Assert: redirects to `/login` (check
`response.status_code == 302` and `Location` header). Follow the
redirect; assert the success flash message "Account created" appears.
Then verify the database: User exists with correct email (lowercased),
UserSettings exists for this user, Scenario with `is_baseline=True`
exists for this user.

```
test_register_success_user_can_login
```

POST `/register` to create a user. Then POST `/login` with the same
credentials. Assert: login succeeds (redirect to grid, not back to
login).

```
test_register_success_new_user_sees_empty_grid
```

Register a user, log in, then GET the grid page. Assert 200.
Assert the response does NOT contain any transaction data from the
seed user (e.g., "Rent Payment" should not appear). This verifies
complete data isolation from the start.

```
test_register_duplicate_email_shows_error
```

Depends on `seed_user`. POST `/register` with the seed user's email.
Assert: redirects back to registration. Follow the redirect; assert
the flash message contains "already exists".

```
test_register_duplicate_email_preserves_form_input
```

Depends on `seed_user`. POST `/register` with the seed user's email
and a display_name. Follow the redirect. Assert the response body
contains the submitted email and display_name in the form field values,
so the user does not have to re-type everything.

```
test_register_short_password_shows_error
```

POST with an 11-character password. Assert redirects. Assert flash
message contains "at least 12 characters".

```
test_register_password_mismatch_shows_error
```

POST with `password="validpassword1"` and
`confirm_password="validpassword2"`. Assert redirects. Assert flash
contains "do not match".

```
test_register_invalid_email_shows_error
```

POST with `email="notvalid"`. Assert redirects. Assert flash contains
"Invalid email format".

```
test_register_empty_display_name_shows_error
```

POST with `display_name=""`. Assert redirects. Assert flash contains
"Display name is required".

```
test_register_get_redirects_when_authenticated
```

Use the `auth_client` fixture (already logged in). GET `/register`.
Assert: redirect (302) to the grid page.

```
test_register_post_redirects_when_authenticated
```

Use the `auth_client` fixture. POST `/register` with valid data.
Assert: redirect (302) to the grid page. Assert: no new user was
created (check User count has not increased).

```
test_register_success_has_baseline_scenario
```

Register a new user. Query the database for their Scenario rows.
Assert: exactly one exists. Assert: `is_baseline` is True. Assert:
`name` is "Baseline". Assert: `user_id` matches the new user.

## Execution Checklist

After implementing everything, run these checks in order:

1. `pylint app/services/auth_service.py` -- fix all issues.
2. `pylint app/routes/auth.py` -- fix all issues.
3. `pytest tests/test_services/test_auth_service.py -v` -- all must pass.
4. `pytest tests/test_routes/test_auth.py -v` -- all must pass.
5. `pytest` -- the FULL suite. All ~900+ existing tests plus your new
   tests must pass. Zero failures. Zero errors.

If ANY test fails at step 5, diagnose and fix before reporting done.
The most likely cause of existing test breakage is an import error or
a changed function signature. If you modified any existing function
signature (you should not need to for WU-1), update every call site.

## Things You Must NOT Do

- Do not create any migration file. No schema changes are needed.
- Do not modify any existing function signatures.
- Do not modify `conftest.py` (that is WU-3's job).
- Do not create any files other than `app/templates/auth/register.html`
  and the tests added to existing test files.
- Do not add any new Python packages to requirements.txt.
- Do not write `pass` or `TODO` in any function or test body.
- Do not use `assert True` or any assertion that always passes.
- Do not mock the database. Tests use the real PostgreSQL test database.
- Do not use `float` for any numeric value. Use `Decimal` when needed.
- Do not use em dashes or en dashes anywhere (use hyphens instead).
