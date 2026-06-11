---
name: shekel-design
description: >
  Load and enforce the Shekel design language for any UI/UX work on the budget app.
  Use when redesigning or building a screen, reviewing a screen against the design
  language, generating visual directions, or working through the Fable 5 UI/UX
  overhaul. Reads docs/design/fable5-design-language.md and the relevant per-screen
  audit, then holds the rebuild to the design principles and hard constraints.
---

# Shekel Design Skill

This skill keeps every screen of the Shekel UI/UX overhaul coherent by reloading the same
written design language and the same hard constraints in every session. It does not invent a new
look each time. It applies the committed one.

## Conventions

- Use snake_case for file names.
- Do not use em dashes or en dashes anywhere in the output.
- Money math never goes in a template; figures are computed in the service or route with `Decimal`
  and passed in.
- Format dates as YYYY-MM-DD in any document you write.
- Wrap Markdown lines at a reasonable length.

---

## Step 1: Load the design language

Read these in order before touching any UI:

1. `docs/design/fable5-design-language.md` -- purpose, audience, tone, principles, hard
   constraints, and the design-token reference. This is the source of truth.
2. The per-screen audit for the screen you are working on, if one exists. For the dashboard that is
   `docs/design/dashboard_card_audit.md`. If no audit exists for the screen, produce one first
   using the same structure: per surface, what it should show, what the code actually produces, the
   divergence, and a keep / fix / remove verdict.
3. `app/static/css/app.css` -- confirm the current design-token names so you reference variables,
   never raw hex.

Do not proceed to design work until you can state the screen's job and its one-question-per-card
breakdown.

---

## Step 2: Hold the hard constraints

These are not stylistic preferences. A direction that breaks one is out of bounds. Verify each
before and after you build:

- **Stack:** Bootstrap 5 + design tokens + HTMX + vanilla JS. No framework, no SPA, unless the
  stack ROI gate has explicitly decided otherwise.
- **CSP:** no inline `<style>` and no inline `<script>`. CSS goes in `app/static/css/app.css`; JS
  goes under `app/static/js/` loaded with `<script src>`; data passes via `data-*` read with
  `element.dataset`. (`app/__init__.py` sets `script-src 'self'`, `style-src 'self'`.)
- **Templates display, never compute.** `float()` only at a Chart.js serialization boundary.
- **Reference tables by id or enum, never by `.name` string** in Python or Jinja.
- **Forms and security:** CSRF on every form, HTMX mutations use POST, HTMX responses are `_`
  partials with an explicit `hx-target`, never `|safe` on user data, 404 for both not-found and
  not-yours.
- **CSS hygiene:** Bootstrap utilities first, custom CSS only as a last resort, no `!important` in
  new rules.
- **Both themes:** every screen renders correctly in light and dark via `data-bs-theme`, using
  tokens so a theme is a single block of variable values.

---

## Step 3: Apply the design principles

From the brief, in priority order: the number is the hero; a figure and its caption never disagree;
as simple as possible without losing functionality; every call to action goes somewhere useful;
tabular money; density with breathing room; consistent components across screens.

Map color to money state consistently: positive or settled uses `--shekel-done`, negative or
over-budget uses `--shekel-danger`, credit uses `--shekel-credit`. Never let color be the only
signal; pair it with an icon or text.

---

## Step 4: Model and workflow discipline

- Use Fable 5 (`/model fable`) for visual, template, and CSS work. **Switch to Opus 4.8 for any
  edit to `app/services/`, `app/routes/`, or test assertions** -- those are financial-logic changes,
  not skin changes.
- Never edit a passing test to make a redesign pass. If a data path was wrong, fix the code and add
  assertions only after hand-confirming the corrected value (CLAUDE.md rule 5).
- Stay in scope. A visual rebuild does not silently change what a number means. If the audit says a
  card shows the wrong figure, that is a separate, explicit data fix, decided at the per-card gate.

---

## Step 5: Verify before done

- Render the screen in both themes and check it against the principles and hard constraints in
  Step 2 and Step 3.
- Confirm no inline `<style>` or `<script>` was introduced (grep the changed templates).
- Run the targeted route and service tests for the screen, then the full suite, and show the
  pass/fail summary. A UI rebuild is not done until the suite is green.
- Report any gap between the built screen and the design language explicitly rather than silently
  accepting it.
