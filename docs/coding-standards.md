# Coding Standards

These standards apply to all code in the Shekel project. They are referenced from CLAUDE.md and
are loaded when working on code. Every rule here exists because its absence caused a real bug
or a real quality problem in this project.

---

## Python

### Type Safety

- **Use `Decimal`, never `float`**, for all monetary amounts.
- **Construct Decimals from strings.** `Decimal("0.1")` is exact. `Decimal(0.1)` introduces
  float imprecision. This applies to hardcoded values, test assertions, seed data, defaults.
- **Type hints on all function signatures.** Annotate all parameters and return types. Use
  `Decimal` not `float` for monetary values. Use `X | None` for nullable parameters. Use
  specific collection types (`list[Transaction]`, `dict[str, Decimal]`), not bare `list`/`dict`.
  Do not use `Any` to satisfy the requirement without providing real type information.
- **Do not rely on truthiness for business logic.** `0` and `None` mean different things in a
  financial app. Write `if amount is None:` not `if not amount:`. A zero balance is not a
  missing balance.

### Code Structure

- **No magic numbers or strings.** Every numeric or string literal representing a business rule
  must be a named constant. Do not write `26` when you mean `PAY_PERIODS_PER_YEAR`. Do not
  write `Decimal("0.062")` when you mean `SOCIAL_SECURITY_RATE`. Mathematical constants (`0`,
  `1`), HTTP status codes, and framework values are exempt.
- **Keep functions focused.** If a function exceeds 50 lines, evaluate decomposition. Functions
  over 100 lines require justification. A 300-line function is a service module incorrectly
  written as a single function.
- **Prefer guard clauses over deep nesting.** Validate preconditions early and return or raise
  immediately. Aim for a maximum nesting depth of 3 levels in business logic.
- **No mutable default arguments.** Never use `[]`, `{}`, or `set()` as a default parameter.
  Use `None` and initialize inside the function body.
- **DRY and SOLID.** Do not duplicate logic -- extract shared behavior. Do not add parameters
  to patch around a function's broken internals. Verify equivalent logic does not already exist
  before writing new code.

### Documentation

- **Docstrings must be substantive.** A docstring restating the function name adds nothing.
  Explain: what it does in business terms, input types/constraints, return type/edge cases,
  side effects. For financial calculations, document the formula or business rule.
- **Comments must explain why, not what.** `# Subtract expenses` is noise. `# Exclude settled
  transactions -- already reflected in the anchor balance` explains a decision.
- **Docstrings on every module, class, and function.** No exceptions.

### Error Handling

- **Catch specific exceptions.** Never `except Exception:`. Identify the specific exceptions
  the `try` block can raise. List them in a tuple if multiple need the same handling.
- **Error messages must be actionable.** Bad: `raise ValueError("Invalid input")`. Good:
  `raise ValueError(f"Expected positive Decimal for amount, got {amount!r}")`.

### Style and Linting

- **Pylint compliance is mandatory.** Run `pylint app/ --fail-on=E,F` after every change. Do
  not decrease the current score.
- **Fix Pylint violations, do not suppress them.** The only acceptable `# pylint: disable=` is
  for genuine false positives. When truly necessary, it must: (a) be scoped to one line,
  (b) name the specific rule, and (c) include a comment explaining why. NEVER use
  `# pylint: disable=all`.
- **snake_case** for all variables, functions, modules, and database columns.
- **No unused imports.** Fix immediately.
- **Import organization.** Three sections separated by blank lines: standard library,
  third-party, local application. Alphabetical within each section.
- **Do not defer essential work as "future improvements."** If a user can hit an edge case with
  the feature as implemented, handle it now. Only genuinely out-of-scope features belong in a
  "future improvements" suggestion.

---

## SQL / Database

### Query Safety

- **All queries must be user-scoped.** Every query touching user data must filter by `user_id`.
  Missing ownership checks are IDOR vulnerabilities.
- **Always filter soft-deleted records.** Every query on a table with `is_deleted` must include
  `.filter(Model.is_deleted.is_(False))` unless deleted records are explicitly needed.
- **Use SQLAlchemy ORM.** No raw SQL strings in application code.
- **Prevent N+1 queries.** Use `joinedload()`, `subqueryload()`, or `selectinload()` when
  loading collections that access related objects. The grid route and balance calculator are
  the highest-traffic paths.

### Schema Design

- **NOT NULL by default.** Every new column should be NOT NULL unless there is a specific reason
  for nullability. Nullable columns must justify their nullability in a code comment.
- **Numeric(12,2) for all monetary columns.** Do not use Float, Integer, or bare Numeric.
- **CHECK constraints on every financial column.** Amounts, rates, counts, and durations must
  have database-level CHECK constraints. If Marshmallow says `min=0`, the column must have
  `CHECK(column >= 0)`.
- **Explicit ondelete on every foreign key.** Never rely on PostgreSQL's implicit default. Use
  `CASCADE` for user_id FKs, `RESTRICT` for ref table FKs, `CASCADE` or `SET NULL` for
  inter-domain FKs.
- **Name all constraints explicitly.** Pattern: `ck_<table>_<description>` for CHECK,
  `uq_<table>_<columns>` for unique, `ix_<table>_<columns>` for indexes.
- **Add indexes for query patterns.** Every column in a frequent WHERE, JOIN, or ORDER BY
  should have an index. Consider partial indexes for filtered queries.

### Validation

- **Marshmallow schema for every state-changing route.** Every POST/PUT/PATCH/DELETE that
  accepts input must validate through Marshmallow before any database operations. No manual
  `request.form.get()` with inline `try/except`.
- **Validate FK existence before commit.** Verify referenced rows exist and belong to the user.
  Unvalidated FKs produce IntegrityError (500) instead of clean validation errors (400).
- **Range validation must match between schema and database.** No gaps where one is stricter
  than the other.

### Migrations

- **Always use Alembic.** Never modify schema by hand. Never use `db.create_all()` outside
  tests. Every change must have a migration with a descriptive message.
- **Destructive migrations require explicit approval.** Drops, renames, type changes, and
  constraint removals must be discussed with the developer first.
- **Every migration must have a working downgrade.** Do not write `pass`. If downgrade is
  impossible, raise `NotImplementedError` with a comment explaining why.
- **Consider existing data.** Adding NOT NULL to a populated table requires `server_default`.
- **Review auto-generated migrations.** Verify intended changes, no phantom diffs, named
  constraints, and correct downgrade.

### Audit Triggers

The `system.audit_log` infrastructure (table, trigger function, per-table row-level triggers)
is the project's only tamper-resistant forensic record of financial state changes. It is
materialised by the rebuild migration (`migrations/versions/a5be2a99ea14_rebuild_audit_infrastructure.py`)
and the canonical table list lives in `app/audit_infrastructure.py:AUDITED_TABLES`.

- **Every new table in `auth`, `budget`, or `salary` MUST be added to `AUDITED_TABLES`.**
  Adding a table without auditing it leaves a gap in the forensic trail. Reference tables
  in the `ref` schema are the only schema-level exception (read-only seed data managed by
  `scripts/seed_ref_tables.py`).
- **Add the table to `AUDITED_TABLES`, then re-run `flask db upgrade`.** The rebuild
  migration's idempotent `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER` pair attaches the
  audit trigger on the next upgrade. The entrypoint trigger-count health check
  (`entrypoint.sh`) refuses to start Gunicorn if the count is short of
  `EXPECTED_TRIGGER_COUNT`.
- **Never write directly to `system.audit_log`.** All rows must come through
  `system.audit_trigger_func`, which captures `app.current_user_id`, `db_user`, and
  `executed_at` via session-local state. Direct INSERTs from application code would
  bypass the user-id capture and produce orphaned forensic rows.
- **The runtime app role (`shekel_app`) cannot drop, alter, or replace audit triggers.**
  An attacker who pivots into the Gunicorn process retains DML on financial tables but
  cannot remove the audit trail behind their actions; this is the load-bearing
  invariant the two-role policy provides.

### Reference Tables

- **IDs for logic, strings for display only.** Enums in `app/enums.py`. Cache in
  `app/ref_cache.py`. NEVER compare against string `name` columns. Use boolean columns for
  grouping logic. Use FK references for category groupings, not bare strings.

---

## HTML / Jinja2 Templates

### Logic Boundaries

- **Templates are for display, not computation.** Do not perform financial calculations in
  Jinja. Compute in the route or service using Decimal, pass results to the template.
- **Use IDs, not strings, in template conditionals.** Write
  `{% if txn.status_id == PROJECTED_ID %}`, not `{% if status.name == "Projected" %}`.
- **No lazy-loaded queries in templates.** Confirm relationships were eager-loaded in the route
  before accessing them in a loop.

### Security

- **Never use `|safe` on user-provided data.** Only on HTML the application itself generated.
- **All forms must include CSRF protection.** `{{ csrf_token() }}` for non-HTMX forms. HTMX
  gets CSRF via `htmx:configRequest` in the base template.
- **State-changing actions must use POST.** Use `hx-post`, not `hx-get`, for mutations.

### HTMX Patterns

- **Partial templates (prefixed `_`) for HTMX responses.** Do not return full page HTML.
- **Include `hx-target` explicitly.** Do not rely on HTMX defaults.
- **Appropriate status codes.** 2xx for swap, 422 for validation errors, 204 for no-content.

### Structure

- **Extend `base.html` for all pages.** No standalone HTML files duplicating head/nav/footer.
- **Bootstrap utility classes before custom CSS.** Check Bootstrap docs first.

---

## JavaScript

- **No inline scripts.** CSP prohibits inline JS. All JS in external files via `<script src>`.
- **Pass data via `data-*` attributes.** Read with `element.dataset` in external JS. Do not
  generate JS in Jinja templates.
- **No JS frameworks.** No React, Vue, Alpine, jQuery. HTMX + vanilla JS only.
- **Monetary values in JS are display-only.** Financial arithmetic happens server-side in
  Python. JS never computes monetary values.
- **All JS in `app/static/js/`.** No JS elsewhere.

---

## CSS

- **Bootstrap 5 only.** No additional frameworks, preprocessors, or CSS-in-JS.
- **Utility classes before custom CSS.** Custom styles in `app/static/css/app.css` as a last
  resort.
- **Descriptive class names.** `.pay-period-header` not `.red-text`.
- **No `!important`.** Fix selector specificity instead.
- **Maintain responsive behavior.** Test at Bootstrap `md` and `sm` breakpoints.

---

## Shell Scripts

Scripts in `scripts/` run in the Docker container or on the Arch Linux host.

- **Validate inputs.** Check arguments, ranges, and environment variables. Fail with a clear
  message, not with silent defaults that could corrupt data.
- **Idempotent.** Running a seed script twice must produce the same result as once. Use upsert
  patterns or existence checks.
- **Never print secrets.** Print `Password: [set via environment variable]`, not the value.
- **Confirm destructive operations.** Prompt before deleting data. Support `--force` for
  automation.
- **Log destructive actions.** Write audit entries for credential resets, data deletions.
- **Match project Python standards.** Type hints, docstrings, specific exceptions, Pylint
  compliance. Scripts are production code.