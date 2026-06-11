# Shekel Design Language

The written design commitment for the Fable 5 UI/UX overhaul. Every screen rebuild reads this
first and is checked against it afterward. The companion `shekel-design` skill
(`.claude/skills/shekel-design/`) loads this document so the constraints reload in every design
session and screen 1 stays coherent with screen 50.

Last evaluated: 2026-06-10.

## Purpose

Shekel is a personal budget app organized around pay periods, not calendar months. Every
transaction maps to a specific paycheck, with roughly two years of forward projection. The product
exists so one person can answer, at a glance and with confidence: do I have enough money, what is
due before my next paycheck, and where is my projection heading. The design language serves that
question. Clarity about money beats decoration every time.

## Audience and tone

- **Audience:** a single financially literate operator managing their own household budget. Not a
  team, not a client. They open the app often and know their own data.
- **Tone:** calm, precise, trustworthy. The interface should feel like a well-kept ledger, not a
  consumer fintech dashboard competing for attention. No celebratory confetti, no growth-hacking
  nudges, no manufactured urgency. Urgency is reserved for real financial signals (a projected
  negative balance), and it is proportionate.
- **Voice in copy:** plain, short, specific. "Projected balance goes negative on May 14" beats
  "Uh oh, trouble ahead." Numbers are the headline; words are the caption.

## Design principles

1. **The number is the hero.** Every card answers one money question. The figure that answers it is
   the largest, highest-contrast element on the card. Everything else supports it.
2. **A figure and its caption never disagree.** If a number is a projection, say so; if it is "as
   of" a date, the number must be the value on that date. (This principle exists because the current
   balance card violates it; see `dashboard_card_audit.md`.)
3. **As simple as possible without losing functionality.** Consolidate redundant surfaces, remove
   cards that are not helpful, and reduce clicks, but never drop a number the user relies on. Removal
   is a product decision, made explicitly, not a side effect of a redesign.
4. **Every call to action goes somewhere useful.** A link or button resolves the thing it sits next
   to. No placeholder links to the home page.
5. **Tabular money.** Monetary values use tabular numerals and right alignment so columns of figures
   scan cleanly. The existing `font-mono` / tabular-nums treatment is the baseline.
6. **Density with breathing room.** This is a data-dense app for a power user; prefer compact,
   information-rich layouts over large empty hero space, but keep enough whitespace that figures do
   not collide.
7. **Consistent across screens.** A card, a progress bar, a money figure, an alert, and an empty
   state look and behave the same everywhere. The token layer and shared component patterns are how
   that consistency is enforced, not by convention alone.

## Hard constraints (non-negotiable)

These come from the codebase and CLAUDE.md, not from taste. A direction that violates one of these
is out of bounds regardless of how it looks.

- **Stack:** Bootstrap 5 + the design-token layer + HTMX + vanilla JS. No frontend framework, no
  SPA, unless the stack ROI gate explicitly decides otherwise.
- **Content Security Policy** (`app/__init__.py`, `_CSP_DIRECTIVES`): `script-src 'self'`,
  `style-src 'self'`. No inline `<style>` and no inline `<script>`. All CSS lives in
  `app/static/css/app.css` (or another linked stylesheet); all JS lives under `app/static/js/` and
  is loaded with `<script src>`. Pass data to JS via `data-*` attributes read with
  `element.dataset`.
- **Templates display, never compute.** All money math happens in the service or route with
  `Decimal` and is passed in. `float()` appears only at a serialization boundary (Chart.js JSON),
  never in a calculation.
- **Reference tables by id or enum, never by name string.** No comparing a `.name` against a string
  literal in Python or Jinja (gates `shekel-refname-compare`, plus the template hook).
- **Security and forms:** CSRF token on every form; HTMX mutations use POST; HTMX responses are
  partial templates (prefixed `_`) with an explicit `hx-target`; never `|safe` on user data; 404 for
  both "not found" and "not yours."
- **CSS hygiene:** Bootstrap utility classes first, custom CSS in `app.css` only as a last resort,
  no `!important` in new rules.
- **Both themes.** Every screen must render correctly in light and dark, driven by `data-bs-theme`.
  Use tokens, never hardcoded hex, so a theme is a single block of variable values.

## Design tokens

The token vocabulary already exists in `app/static/css/app.css` as CSS custom properties defined
per `[data-bs-theme="dark"]` and `[data-bs-theme="light"]` block. New work references these
variables; it does not introduce new raw hex. The Step 2a refactor consolidates the remaining
inlined hex onto these names.

| Token | Role |
| ----- | ---- |
| `--bs-primary` / `--shekel-accent` | brand accent (Steel Blue, `#4A9ECC`) |
| `--shekel-accent-hover` | accent pressed / hover (`#2878A8`) |
| `--shekel-accent-light` | accent highlight (`#6BB8E0`) |
| `--shekel-surface` | card / panel background |
| `--shekel-surface-raised` | raised surface (card header, raised cell) |
| `--shekel-text-primary` | primary body text |
| `--shekel-text-secondary` | secondary text |
| `--shekel-text-muted` | muted / caption text |
| `--shekel-border-strong` / `--shekel-border-subtle` | strong vs subtle borders |
| `--shekel-row-hover` | table row hover background |
| `--shekel-sticky-bg` | sticky column / header background |
| `--shekel-header-bg` | grid header background |
| `--shekel-summary-bg` | grid summary / total row background |
| `--shekel-done` (`#2ECC71`) | settled / done / positive |
| `--shekel-credit` (`#E67E22`) | credit state |
| `--shekel-danger` (`#E74C3C`) | danger / negative |
| `--shekel-section-income-bg` / `--shekel-section-income-text` | income section banners |
| `--shekel-section-expense-bg` / `--shekel-section-expense-text` | expense section banners |

Semantic mapping for money state: positive / settled uses `--shekel-done`, negative / over-budget
uses `--shekel-danger`, credit uses `--shekel-credit`. These already match the grid; the rebuild
keeps them consistent so a color means the same thing on every screen.

## Accessibility

- Color is never the only signal. Pair every color-coded state with an icon or text (the bill row
  already pairs over-budget red with bold weight; keep that pattern).
- Maintain WCAG AA contrast in both themes. The token values are the contract; do not introduce
  one-off colors that have not been contrast-checked.
- Interactive elements are real controls with roles and keyboard access (the balance card already
  uses `role="button" tabindex="0"`); keep that for any click-to-edit affordance.
- Every progress bar and alert keeps its ARIA attributes and screen-reader text.

## Differentiation

Shekel is not a generic SaaS dashboard and should not look like one. Its distinguishing idea is the
**pay-period ledger**: money is organized by paycheck, projected forward, and presented with the
precision of a spreadsheet that grew a real interface. The visual language leans into that, a
quiet, dense, trustworthy ledger, rather than chasing the rounded-card, big-illustration look of
consumer budgeting apps. The Steel Blue palette and tabular figures are the signature; the rebuild
sharpens them rather than replacing them.

## How a screen uses this document

1. Read this brief and the relevant per-screen audit (for the dashboard,
   `dashboard_card_audit.md`).
2. Confirm the screen's job and its one-question-per-card breakdown.
3. Build with tokens, Bootstrap utilities, and the constraints above.
4. Verify against the principles and constraints, in both themes, before calling the screen done.
