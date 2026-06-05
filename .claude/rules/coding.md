---
paths:
  - "app/**/*"
  - "scripts/**/*"
---

# Coding rules (app/ and scripts/)

The must-knows for code in this project. Full standards, with rationale and
examples: `docs/coding-standards.md` -- read it when a point here needs depth.
Several rules are enforced by gates (the per-edit hook, the custom pylint
checkers, CI); fix what a gate flags at the root, never with a bare disable.

## Python

- **`Decimal`, never `float`, for money. Construct from strings:** `Decimal("0.1")`,
  not `Decimal(0.1)` (float imprecision). Applies to defaults, seeds, test
  assertions. Gate: `shekel-decimal-from-float`. `float()` belongs only at a
  serialization boundary (Chart.js JSON), never in a calculation.
- **Type hints on every signature.** `Decimal` (not `float`) for money, `X | None`
  for nullable, specific collection types -- not bare `list`/`dict`, not `Any`.
- **Do not rely on truthiness for business logic.** Write `if amount is None:`,
  not `if not amount:`. A zero balance is not a missing balance.
- **No magic numbers or strings.** Name every business-rule literal
  (`PAY_PERIODS_PER_YEAR`, `SOCIAL_SECURITY_RATE`). Math constants (0, 1), HTTP
  codes, framework values are exempt.
- **Keep functions focused** (evaluate decomposition past 50 lines), **guard
  clauses over deep nesting** (max depth 3), **no mutable default arguments**.
- **DRY/SOLID:** verify equivalent logic does not already exist before writing new
  code; extract shared behavior rather than duplicating it.
- **Catch specific exceptions**, never `except Exception` (gate:
  `broad-exception-caught`). Error messages must be actionable (include the value).
- **Substantive docstrings on every module, class, function**; comments explain
  *why*, not *what*. **snake_case**; organized imports (stdlib / third-party /
  local). Fix pylint findings, do not suppress them.

## Reference tables

IDs and enums drive logic; `.name` strings are display only. Never compare a
`.name` against a string literal in Python or Jinja (gate: `shekel-refname-compare`
in Python; the template hook in Jinja). Enums in `app/enums.py`, cached in
`app/ref_cache.py`.

## HTML / Jinja2

Templates display, never compute -- do financial math in the route/service with
`Decimal` and pass results in. Use IDs, not name-strings, in conditionals. Never
`|safe` on user data. CSRF on every form (`{{ csrf_token() }}`; HTMX via
`htmx:configRequest`). Mutations use POST (`hx-post`). HTMX responses are partial
templates (prefixed `_`) with an explicit `hx-target`. Extend `base.html`.

## JavaScript / CSS

No inline scripts (CSP); all JS in `app/static/js/` via `<script src>`. Pass data
via `data-*`, read with `element.dataset`. HTMX + vanilla JS only -- no frameworks.
JS never computes monetary values. Bootstrap 5 utility classes before custom CSS
(`app/static/css/app.css` as last resort); no `!important`.

## Shell (scripts/)

Validate inputs and fail loud, not with silent defaults. Idempotent (re-running a
seed equals running it once). Never print secrets. Confirm destructive operations
(`--force` for automation) and log them. Match the Python standards: type hints,
docstrings, specific exceptions, pylint-clean.
