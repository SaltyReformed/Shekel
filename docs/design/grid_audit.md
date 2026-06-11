# Grid Audit

Static plus workflow diagnosis of the pay-period grid ahead of the Fable 5 rebuild. The grid is the
first rebuild target. This is the shekel-design Step-1 artifact for the grid: it pairs a full code
map with the developer's lived daily workflow, since the grid's numbers are trusted and the real
issues are interaction speed and scannability, not data correctness.

Last evaluated: 2026-06-10.

## Method and scope

- Code mapped in full: `app/templates/grid/` (desktop table, mobile This-Period / Plan tabs, the
  three edit tiers, the popover, mobile cards), `app/routes/grid.py` plus the HTMX partial
  endpoints, the balance producer (`balance_resolver`) and pure calculator (`balance_calculator`),
  `grid_view_service` (row keys / cell matching), the status mutation routes, and the three JS
  modules (`grid_edit.js`, `mobile_grid.js`, `anchor_edit.js`).
- Workflow captured from the developer, the primary daily user. The grid is the app's core, modeled
  on a spreadsheet used for years; the developer's spouse uses mobile exclusively as a companion
  account; mobile was recently improved (mobile v3), so desktop is now the more painful side.
- This is a static plus workflow audit. Items marked "needs live confirmation" require driving the
  running app (the visual loop). No code is changed here; fixes happen during the rebuild, decided
  per item.

## The grid's job

Let the user, at a glance across roughly six months of pay periods, see where projected end balances
are heading, and quickly run the recurring loop: update the anchor to match real checking, mark
items paid, log individual purchases, and occasionally add or adjust a transaction. It is a
spreadsheet that grew a real interface; the forward, period-by-period view is the entire point.

## North star (developer priorities, in order)

1. Make the core loop fast, above all desktop mark-paid. Today it is three clicks through a popover
   (click cell, click the small triple-dot, click Paid); mobile is two taps. It is the action done
   almost every session.
2. Make a dense, wide grid (a six-month view is roughly 13 period columns) easy for the eyes to
   track across rows and columns, without adding clutter. The old spreadsheet used zebra striping;
   the developer is wary that striping reads as clutter.
3. Preserve the at-a-glance six-month forward view and the trust in the numbers. Faster editing,
   especially on desktop.

## Findings

### A. Core-loop speed (top priority)

- **A1. Mark paid, desktop, three clicks. (fix, high.)** Clicking a filled cell opens the inline
  quick-edit (amount only); the Paid action lives only in the full popover reached by the small
  triple-dot button. So marking paid is cell -> triple-dot -> Paid, on small targets, for the most
  repeated action. The `mark-done` endpoint already exists and mobile already does it in two taps
  (expand card -> Mark Paid). Target: one-click mark-paid directly on the desktop cell, no popover.
- **A2. Three-tier edit system buries frequent actions. (fix, high.)** Empty cell -> quick form ->
  full popover. The actions done constantly (mark paid, add purchase) sit in the deepest tier, only
  discoverable via the triple-dot. The rebuild should pull frequent actions to one interaction and
  reserve the full popover for the rare detailed edit.
- **A3. Add a purchase (entries). (fix, medium.)** Reached inside the popover or the mobile card
  expansion. Make it fast and discoverable for envelope-tracked rows.
- **A4. Anchor update. (keep / polish.)** Click anchor display -> inline form -> save, with
  optimistic locking. Already direct; keep it prominent since it is the developer's first action.
- **A5. Quick-create has no name field. (fix, low.)** A new ad-hoc cell takes an amount only; naming
  it requires expanding to the full form, so the Tier-1 entry point is incomplete for ad-hoc rows.

### B. Scannability and density (priority two)

- **B1. No row/column tracking aid across a wide grid. (fix, high; design-direction call.)** Sticky
  row labels and a sticky balance row exist, but nothing helps the eye follow a single row or column
  across roughly 13 columns. Candidate tools, to be judged visually from the directions: subtle row
  banding, a hover crosshair that lights the row and column under the cursor, stronger current-period
  column emphasis, cleaner typographic grouping. The clutter-versus-clarity tradeoff is exactly what
  the Fable directions exist to show.
- **B2. Per-cell clutter, no hierarchy. (fix, medium.)** A cell can stack amount, status badge,
  transfer icon, due-date line, envelope progress, and an override pencil with no clear visual
  hierarchy. Establish a hierarchy so a cell scans in a glance.
- **B3. Two balance concepts shown apart. (review, low.)** The anchor balance (top) and the
  projected end balance (footer / mobile summary) are separate surfaces; their relationship is not
  visually obvious.

### C. Desktop/mobile parity and correctness (priority three)

- **C1. Desktop lags mobile on edit speed. (fix; ties to A.)** Mobile got the recent investment;
  the rebuild brings desktop editing speed up to at least mobile parity.
- **C2. Possible dead "Edit Amount" button on mobile. (verify; likely bug.)** The code map flags a
  mobile action button whose target is a desktop-only element id (`#txn-cell-<id>`) that does not
  exist on mobile. Confirm on the running app; if dead, fix or remove it.
- **C3. Full-grid reload on some edits. (fix / perf.)** Mark-credit, cancel, and balance-affecting
  full edits fire `gridRefresh`, a full `GET /grid` that recomputes row keys, balances, and
  subtotals, rather than a targeted swap. Part of why editing feels heavy; supports the faster-edit
  goal.

### D. Lower priority (noted, not prioritized)

- **D1.** Period navigation has three mechanisms (offset arrows, preset-count buttons, and the
  mobile jump-select) that could be simplified.
- **D2.** Invalid status transitions surface as raw 400s with no pre-hint about what is allowed.

## Prioritized fix list for the rebuild

1. Desktop one-click mark-paid on the cell (A1 / A2 / C1).
2. Scannability treatment without clutter, chosen from the Fable directions (B1 / B2).
3. Streamline the common edits; reserve the full popover for rare detail (A2 / A3 / A5).
4. Targeted swaps instead of full-grid reloads (C3).
5. Verify and fix the mobile Edit-Amount button (C2).
6. Polish: anchor prominence, balance-concept clarity, period-nav simplification (A4 / B3 / D1).

## Open questions (decided via the directions or a per-item gate)

All three were settled by the Loop A direction rounds; see "Rebuild decisions" below.

- Scannability technique: subtle banding versus hover crosshair versus structured zones, judged
  visually from the directions, not in the abstract.
- Desktop mark-paid affordance: hover-revealed action versus an always-visible status control versus
  a dedicated status gutter column.
- Does the full popover remain the "advanced" surface, or do we flatten editing further?

## Rebuild decisions (2026-06-11, Loop A complete)

Four mockup rounds (directions A-K, theme rounds T1-T4 / U1-U3 / M1-M2) ran on scratch canvases
in /tmp per the visual loop; the mockups are disposable and this section is the durable record.

1. **Grid direction: C3 "Month Spine."** Two-row header (month band over period dates), strong
   month boundary rules, per-period hairlines inside each month. Filled pill chips for paid
   (`--shekel-done` tint) and credit (`--shekel-credit` tint); unpaid amounts stay plain text.
   Hovering a cell previews the chip: dashed outline plus a ghost check positioned where the
   paid check will sit, so clicking reads as completing the chip. One click marks paid (fixes
   A1). Hover affects only the hovered cell, with a mild full-row tint; no crosshair, no
   banding, no always-visible per-cell controls.
2. **Editing model: one anchored action card replaces the three-tier popover system**
   (A2 / A3 / A5). Anchored positioning fixes the popover misplacement bugs. Single-cell state
   carries status actions, inline amount edit, add-purchase, and recent entries for envelope
   rows; the full-edit surface remains only for rare deep edits.
3. **Keyboard cursor** (vanilla JS module): arrow keys move a cell cursor; Space marks paid,
   C marks credit, Enter edits.
4. **Command layer (Ctrl+K):** fuzzy palette over a server-built action index (mark paid or
   credit, add purchase, jump to row or period, anchor update). It executes the same POST
   endpoints as the buttons; the only new surface is a read-only action-index route (Opus
   scope per the model discipline).
5. **Theme: M1 "Steel Ink"** - achromatic carbon base, Steel Blue as the only non-money
   chroma, vivid state trio, dark mode first-class. Token values are recorded in
   `fable5-design-language.md` and apply app-wide.
6. **Stack: stay on Bootstrap 5 + HTMX + vanilla JS.** The stack ROI gate ran (React, Vue,
   Svelte, Solid, Angular, Qwik, Astro, and Tailwind considered). A Svelte 5 island for the
   desktop grid (direction I "Live Sheet": range selection, bulk mark-paid, type-in-cell,
   optimistic ripple) is the recorded upgrade path, to be re-gated only if daily use of the
   rebuilt grid shows the need. Nothing in the chosen build is thrown away by that upgrade.
7. **Noted for later screens:** direction E2's horizon strip plus alert line is the leading
   candidate for the deferred dashboard rebuild. Rejected for the grid: hover crosshair (A),
   always-visible status rings (B), row banding, transposed period journal (D), unified
   workspace drawer (J), chart-first grid (G), four-period window plus balance rail (H).

## Hard constraints (unchanged, from the design language)

Bootstrap 5 plus the design tokens plus HTMX plus vanilla JS; CSP forbids inline `<style>` and
`<script>` in app templates (CSS in `app.css`, JS under `static/js/`, data via `data-*`); templates
display, money is computed in services with `Decimal`; reference tables by id or enum, never by name
string; CSRF on every form, POST mutations, `_` partials with an explicit `hx-target`; both themes
via `data-bs-theme`, tokens not raw hex; tabular numerals for money.
