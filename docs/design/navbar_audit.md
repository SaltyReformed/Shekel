# Navbar Audit

Static diagnosis of the global navigation bar (`app/templates/base.html` lines 84-163, styled in
`app/static/css/base.css`) ahead of its Fable 5 rebuild. Same structure as the other per-screen
audits: per surface, what it should do, what the code actually produces, the divergence, and a keep
/ fix / remove verdict. The navbar is chrome, not a screen, so "should show" is judged against its
job: get the operator to any of the 8 destinations in one click, show where they are, and stay out
of the way of the numbers.

Last evaluated: 2026-07-05, against dev at commit 90b1116f, with live screenshots of both themes
(desktop 1440x900).

## Method and scope

- Read in full: the navbar block of `base.html`, `base.css` (navbar skins, offcanvas drawer, theme
  toggle), the navbar notes in `theme-steel-ink.css`, and `command_palette.js` (the keyboard nav
  layer the bar never advertises).
- Live screenshots of `/dashboard` in dark and light via `tests/manual/shoot.py`.
- No code was changed. The rebuild direction is decided at the gate below, not in this document.

## Current anatomy

One `navbar navbar-expand-md navbar-dark sticky-top` bar, ~62px tall, on every authenticated page.
Left to right:

1. Brand: `shekel_logo.png` at 38px (a 643 KB PNG: gold scales-of-justice medallion plus a blue
   "Shekel" wordmark with baked-in colors).
2. Eight owner destinations, every one icon + label at identical weight: Dashboard, Budget,
   Recurring, Accounts, Salary, Retirement, Analytics, Settings. (Companion role sees one item.)
3. Right cluster: theme toggle button, the user's display name as static non-link text, and a Logout
   button.

Below `md` the whole thing collapses into a Bootstrap offcanvas drawer (280px, 44px touch targets).
A hidden `#scenario-selector-slot` placeholder is parked for Phase 7. Styling is stock Bootstrap
`navbar-dark` link colors (white at 55% / 90% / 100% opacity) over a token background:
`--shekel-page-bg` plus a subtle bottom border in dark mode, `--shekel-header-bg` (the header
deliberately stays dark) in light mode.

## Summary table

| # | Surface | Verdict | Severity |
| - | ------- | ------- | -------- |
| 1 | Placement and layout (top horizontal bar) | keep | - |
| 2 | Active-page indicator | fix | high |
| 3 | Visual language (stock Bootstrap, not Steel Ink) | fix | high |
| 4 | Brand mark | fix | medium |
| 5 | Right cluster (theme / identity / logout) | fix | medium |
| 6 | Light-mode username contrast | fix (defect) | high |
| 7 | Item set and information architecture | keep | - |
| 8 | Mobile offcanvas drawer | keep | - |
| 9 | Command palette discoverability | fix (small) | low |

## Surface 1: Placement and layout

- **Should do:** give every destination one-click access without stealing space from the app's real
  content, above all the grid (pay-period columns are the scarce resource there) and the dashboard
  chart (vertical space is the scarce resource there).
- **Actually does:** a single sticky top bar, ~62px. The 8 items fit at `md`+ with room to spare; no
  overflow, no second row.
- **Assessment:** the original reasoning still holds. A labeled sidebar (~220px) costs ~15% of a
  1440px viewport in exactly the dimension the grid needs most; even a 64px icon rail costs a
  visible slice of every grid column while also demanding custom layout CSS that Bootstrap does not
  provide (against "Bootstrap utilities first" and "as simple as possible"). Eight destinations is
  comfortably inside what a horizontal bar handles; sidebars earn their keep at roughly a dozen
  destinations or nested trees, which Shekel deliberately does not have. The blandness the operator
  senses is a craft problem in the bar, not a placement problem.
- **Verdict: keep** the top horizontal placement. (Confirmed against mockups of rail and sidebar
  alternatives at the decision gate.)

## Surface 2: Active-page indicator

- **Should do:** make "where am I" legible at a glance, with more than a color shift (design
  language: color is never the only signal).
- **Actually does:** stock `navbar-dark` treatment: the active label renders at 100% white vs 55%
  white for the rest. No accent, no underline, no shape. `aria-current="page"` is set correctly
  (good), so the failure is purely visual.
- **Divergence (confirmed):** a 55%-to-100% opacity shift on one of eight identical icon+label items
  is the weakest possible signal, and it is opacity-only: the one place the app's signature accent
  should appear as wayfinding, it does not appear at all.
- **Verdict: fix.** Give the active item a real mark (accent underline rail or pill) so location
  reads instantly in both themes.

## Surface 3: Visual language

- **Should do:** read as part of Steel Ink: achromatic base, the accent as the only non-money
  chroma, deliberate hairlines, tabular precision. The bar is the one component visible on every
  screen, so it sets the tone.
- **Actually does:** an unmodified Bootstrap navbar. Default paddings, default rgba-white link
  colors, icons at text size on every item. In dark mode the bar is page-background plus a hairline;
  in light mode it is a strong dark ink band. The two themes present two different identities for
  the same component.
- **Divergence (confirmed):** the only screen-chrome component that has received no Steel Ink pass
  at all. Every card, table, form, and chip has been rebuilt onto tokens and a consistent grammar;
  the navbar still reads "Bootstrap demo."
- **Verdict: fix.** Rebuild the bar's own styling on tokens with one identity across themes (the
  dark ink band already committed for light mode is the natural candidate).

## Surface 4: Brand mark

- **Should do:** a small, themeable mark consistent with the palette.
- **Actually does:** a 643 KB raster PNG whose gold/brass medallion is the only non-token chroma in
  the entire app, next to a wordmark whose blue is baked into the image and cannot follow the
  per-theme accent (`#4A9ECC` dark / `#2878A8` light).
- **Divergence (confirmed):** clashes with the achromatic-plus-accent rule; not themeable; 643 KB on
  every cold load for a 38px-tall image.
- **Verdict: fix.** Replace with a text wordmark in the accent token (optionally with the shekel
  currency glyph as the mark). Keep the PNG for the favicon / PWA icons where it already works.

## Surface 5: Right cluster

- **Should do:** identity, session, and theme controls, quiet and compact.
- **Actually does:** three peers of equal prominence: a bordered theme toggle button, the display
  name as inert text (looks like a nav item, does nothing), and Logout as an always-visible
  top-level action given the same weight as the eight destinations.
- **Divergence (confirmed):** the name invites a click it does not honor; Logout, an action the
  operator uses roughly once per session, occupies permanent prime real estate; three unrelated
  controls sit unglued at the bar's most prominent free end.
- **Verdict: fix.** Consolidate into one compact identity affordance (name as a small menu holding
  Logout, or name-as-text plus an icon-only logout), with the theme toggle as a quiet icon button.

## Surface 6: Light-mode username contrast (defect)

- **Should do:** the display name legible in both themes.
- **Actually does:** `<span class="nav-link text-light-emphasis">`. The header stays dark in light
  mode by design, but `text-light-emphasis` flips with the theme: in light mode it resolves to
  Bootstrap's dark-gray light-emphasis value, rendering dark gray on the dark header at roughly 2:1
  contrast. Confirmed visually in the 2026-07-05 light-theme screenshot: the name is barely visible.
- **Verdict: fix (accessibility defect, WCAG AA failure).** Any rebuild must color this from
  `--shekel-header-text` / header-scoped tokens, not from theme-flipping utility classes.

## Surface 7: Item set and information architecture

- **Should do:** every top-level money surface reachable in one click; meta surfaces present but
  subordinate.
- **Actually does:** the 8 items map one-to-one onto the app's real surfaces post-consolidation
  (Settings correctly absorbs categories and pay periods; Accounts correctly covers savings +
  accounts). Active-state predicates are pre-evaluated once via the `nav_link` macro; ordering
  matches use frequency reasonably well.
- **Verdict: keep.** No item earns removal and nothing is missing. (The Phase 7 scenario slot stays
  parked.) The one IA nuance: Settings is meta, not a money surface, and can move into the identity
  cluster if the rebuild wants a cleaner eight-to-seven split of destinations, but that is optional
  polish, not a finding.

## Surface 8: Mobile offcanvas drawer

- **Should do:** full nav below `md` with adequate touch targets.
- **Actually does:** Bootstrap offcanvas at 280px with 44px targets, WCAG 2.5.5-compliant,
  documented rationale in `base.css`.
- **Verdict: keep.** Any rebuild must preserve this pattern (a top-bar direction gets it for free; a
  sidebar direction would have to rebuild it).

## Surface 9: Command palette discoverability

- **Should do:** advertise the keyboard layer where it exists.
- **Actually does:** Ctrl+K opens the fuzzy palette, but only on the grid (`command_palette.js`
  guards on `gridTable()`), and nothing in the chrome hints that it exists.
- **Verdict: fix (small, optional).** If the rebuilt right cluster has room, a quiet Ctrl+K hint on
  the grid is enough. Extending the palette app-wide is out of scope for a navbar rebuild.

## Decision gate

Decided 2026-07-06 by the developer against four rounds of rendered Loop A mockups (both themes,
desktop + mobile; sidebar and rail alternatives rendered and rejected against the same page mock):

- **Placement: top horizontal bar, kept.** The 68px rail and 232px sidebar mockups confirmed the
  width cost on the grid; the developer ruled the rail out as visual clutter on the page they use
  most.
- **Active-page treatment: tinted pill (direction F).** Accent-tinted pill with a visible accent
  border ring; the solid filled pill (E), quiet underline (A), and carved tab (H) were rendered and
  rejected. One ink-band identity (`--shekel-header-bg`) in both themes.
- **Settings demoted:** gear icon in the utility cluster at md+, labelled drawer item below md.
  Rarely used, so it no longer holds a destination slot.
- **Right cluster:** theme toggle and Settings gear as quiet icon buttons, then an identity menu
  (initials avatar + first name) holding Logout. Fixes the inert-name and light-mode-contrast
  defects (surfaces 5 and 6).
- **Brand: tarnished-silver coin (recipe C) + text wordmark.** The coin-and-scales medallion is kept
  for the ancient-currency tie-in the project is named for, recolored from gold to tarnished silver
  (grayscale, brightness 1.02, contrast 1.12, sepia .14) so it joins the achromatic-plus-accent
  palette; historically apt, since the ancient shekel was a silver coin. Baked as the 28 KB
  coin-only `img/shekel_coin.png` (120px, 4x the 30px slot); the steel-blue tint and geometric SVG
  redraw were rendered and rejected. The gold `shekel_logo.png` remains for the pre-login auth
  pages, which keep their logo-gate look by the standing exception.
- **Ctrl+K hint:** shown in the bar on grid pages at md+ only, matching where `command_palette.js`
  actually binds (surface 9).

Built into `base.html` + `base.css` on dev, 2026-07-06.
