# Mobile-First v2 -- Implementation Plan

- Version: 1.0
- Date: 2026-05-22
- Author: prepared for the solo developer (SaltyReformed)
- Source plan: this document supersedes the v1 mobile responsiveness work tracked in
  `docs/implementation_plan_mobile.md` (shipped April 2026, recorded as Appendix A.8 in
  `docs/project_roadmap_v5.md`).
- Prerequisite reading: `docs/implementation_plan_mobile.md` (v1 baseline),
  `docs/project_roadmap_v5.md` Sections 3 and 4 (Smart Features, Notifications),
  `CLAUDE.md` (Rules, Transfer Invariants), `docs/coding-standards.md`,
  `docs/testing-standards.md`.

---

## 0. Context

The v1 mobile pass (11 commits, complete) made the desktop app *functional* on small screens:
table-responsive wrappers, touch targets, bottom sheets, a single-period card grid, a
manifest. The developer reports the result as "ok but clunky" and now uses the phone for the
primary daily workflow.

The primary daily workflow (developer self-report):

1. Check upcoming expenses.
2. Mark expenses paid or add a transaction.
3. Check end balance for the current pay period and the next two to decide affordability.

All three are done in the grid. The grid handles the work correctly; the *mobile rendering*
of the grid forces the user to swipe through three single-period cards to do the affordability
math, opens a popover for a single-tap state transition, and forces the desktop edit form into
a small viewport for new entries. None of these reflect the work the user is actually doing.

This plan reframes mobile from "the desktop app fits on a phone" to "the high-frequency flows
are designed for the phone first." The grid is the highest-leverage starting point because it
is where the user spends almost all their time; once the grid's mobile vocabulary exists, the
same patterns propagate to the rest of the app cheaply.

This plan also lays the PWA infrastructure (service worker, web push subscription plumbing)
that Section 4 (Notifications) will consume. Notifications without mobile push reach are a
desktop feature with a phone icon; pairing the two phases avoids that outcome.

### Consequence of getting this wrong

Two distinct failure modes are worth naming because they shape the rules in Section 1:

1. **A mobile-specific code path that re-implements logic the canonical producers already
   own.** The financial-calculation remediation just landed a single source of truth for
   balances (`balance_resolver`) and loans (`loan_resolver`). A mobile partial that does its
   own balance math, or reads `Account.current_anchor_balance` directly, recreates the bug
   class the audit closed. Every displayed monetary value in this plan flows through the
   canonical producer.
2. **Service worker caching financial data.** A grid that serves yesterday's balances offline
   produces wrong numbers silently. The service worker in Phase B caches static assets only;
   HTML and JSON responses are network-first with no stale fallback. A user offline sees an
   honest connection error, not stale money.

---

## 1. Hard rules for executing this plan

These bind every commit. They restate CLAUDE.md and the testing standards in the context of
this work.

1. **Canonical producers only.** Every displayed monetary value flows through
   `balance_resolver` (`balances_for`, `period_subtotal`, `balance_as_of_date`) or
   `loan_resolver`. No partial or JS file reads `Account.current_anchor_*` or
   `LoanParams.current_principal` directly. The static guard at
   `tests/test_routes/test_grid.py` (commit `842d415`, F-6) is the model -- extend its scope
   to mobile partials as new ones land.
2. **Read the entire file before editing.** Audit citations and line numbers in this plan
   drift. Re-grep before editing.
3. **Firefox parity is a hard requirement.** Every commit's manual verification (Section F)
   runs on Firefox Desktop (Gecko) and Firefox Android (GeckoView). Firefox iOS uses WebKit
   per Apple's App Store rules; commits that touch features WebKit handles differently
   document the limitation explicitly rather than letting the user discover it later.
4. **No JS framework, no new CSS framework.** HTMX + vanilla JS + Bootstrap 5 only, per
   `docs/coding-standards.md`. New JS goes to `app/static/js/`; new CSS extends
   `app/static/css/app.css`.
5. **Touch gestures must have a keyboard equivalent.** Swipe-to-act, swipe-period-nav, and
   any other gesture exposes a non-gesture path (button, menu, keyboard shortcut) so the
   app remains usable on desktop and with assistive technology.
6. **Touch targets minimum 44x44 CSS pixels** (Apple HIG / WCAG 2.5.5 AA). Codified as a
   CSS custom property in Commit 1; reused everywhere.
7. **Service worker caches static assets only.** HTML and JSON are network-first, no stale
   fallback. Static assets (CSS, JS, fonts, images, manifest) are cache-first with version-
   keyed cache busting. No financial data is ever served from cache.
8. **Atomic commits, suite green after each.** Targeted tests per change; the full suite
   (`./scripts/test.sh`, ~65 s at the `pytest.ini` `-n 12` default) only as the final gate
   of each commit and as the plan's final gate. `pylint app/ --fail-on=E,F` after every
   commit, no new warnings.
9. **Decimal only in monetary paths.** JS receives pre-rounded strings from server-rendered
   templates or JSON; JS never performs monetary arithmetic. Reuses
   `app/utils/money.round_money` (E-26) at the server boundary.
10. **Style.** No Unicode dashes anywhere (use `--` or `-`). Pythonic, type-hinted,
    substantive docstrings, specific exceptions.
11. **Scope.** Only the commit's stated work. Out-of-scope issues are reported, not fixed.

---

## 2. Design decisions (made for this plan; confirm at review)

These were resolved during planning and lock the implementation. Any change requires a plan
revision.

- **D-A. Swipe-to-act convention: right-swipe = primary positive action, left-swipe = action
  menu.** Right-swipe on a Projected transaction marks it Done (the most common single-tap
  goal). Left-swipe opens a status menu (Cancelled, Credit, Settled where the state machine
  allows). Rationale: matches iOS Mail (right = primary mark, left = options) so iPhone
  muscle memory transfers; Firefox Android has no platform convention, so iOS convention
  wins by default. Both gestures show a visual peek and commit only on release past a
  threshold so accidental swipes cancel cleanly.
- **D-B. Quick-add supports ad-hoc transactions, not just template-driven.** The quick-add
  bottom sheet has a single form: template field is *optional*. Selecting a template
  pre-populates the other fields with template defaults but they remain editable; leaving
  template blank requires the user to fill the same fields the existing
  `_transaction_full_create.html` would require, reusing its validation schema. This
  prevents two parallel "new transaction" surfaces (DRY).
- **D-C. Sticky affordability strip shows three periods: current + next two.** Matches the
  user's reported affordability workflow. The strip is sticky to the top of the mobile grid
  (below the existing app nav) and uses `position: sticky` with safe-area-insets so it
  cooperates with the URL bar collapse on Firefox Android.
- **D-D. Service worker is registered for every page, not grid-only.** Scoping the SW to a
  single route is structurally fragile (a navigation off the grid would un-register the SW
  on the next visit). Registered from `base.html` once.
- **D-E. Web push subscription is opt-in per user via `/settings`, not on by default.**
  Pushing notifications without explicit consent is poor UX and is required-explicit by
  browser permission models anyway. Subscription is per-device (so the user can be opted
  in on phone and opted out on desktop), stored as one row per `(user_id, endpoint)` in a
  new `push_subscriptions` table.
- **D-F. Push *plumbing* lands in this plan; push *content* lands in Section 4
  (Notifications).** This plan only builds the subscription round-trip and an internal
  helper `send_push(user_id, payload)` that Section 4 will call. No notification triggers,
  no notification UI, no email parity -- those are Section 4's scope.
- **D-G. Companion view does not get a new mobile rewrite.** Commit `2072995` already
  built a mobile-first companion view. Commit 14 in this plan only aligns it with the
  v2 vocabulary (same FAB, same bottom sheet primitive, same touch tokens) so it does not
  visually drift from the rest of the mobile app.

---

## 3. v1 mobile baseline (what this plan inherits)

Live-code verification of what the v1 plan landed (re-grep at edit time; line numbers drift):

- `app/templates/grid/_mobile_grid.html` -- single-period card view, ~250 lines.
- `app/static/js/mobile_grid.js` -- period navigation handlers, ~85 lines.
- `app/static/css/app.css` -- bottom-sheet primitive at `~:838` (`.bottom-sheet-backdrop`);
  touch-target sizing in mobile media queries; 360 px breakpoint.
- `app/static/manifest.json` -- PWA manifest with icons.
- `app/templates/base.html` -- manifest link (`:21`), `theme-color` (`:22`),
  `apple-mobile-web-app-*` meta tags (`:24-26`).
- `app/templates/companion/index.html` + `_transaction_card.html` -- mobile-first companion
  view (commit `2072995`).
- Bottom-sheet conversion of full-edit popover (commit `c1cc309`), card-based mobile grid
  (commit `55eab1d`), salary action dropdown collapse (commit `a3e9467`), scrollable loan
  tabs (commit `ede7014`), touch target sizing (commit `6b75d10`), 360 px breakpoint
  (commit `4db73c2`).

This plan extends, not replaces, that baseline. The single-period card layout from
`_mobile_grid.html` remains the structural pattern; the affordability strip sits above it,
swipe gestures attach to its cells, and the FAB hovers over it.

What v1 did not deliver and this plan does:

- Multi-period balance visibility on mobile (current + next two).
- Single-tap mark-Done from the mobile cell.
- Quick-add reachable without finding the right cell first.
- Period navigation via swipe (currently button-only).
- Service worker (no `sw.js` exists).
- Push subscription plumbing.
- Pattern reuse across `/savings`, `/accounts`, `/loan`, calendar, template list.
- Form-page mobile patterns (sticky save bar, progressive disclosure).

---

## 4. Pattern -> canonical implementation map (the spine of this plan)

Every mobile-first pattern collapses onto one component. This table is the contract the
commits implement. A mobile partial that needs one of these patterns reuses the component;
it never re-implements the pattern.

| Pattern | Canonical implementation | First introduced | Reused by |
|---|---|---|---|
| Swipe gesture | `app/static/js/touch_gestures.js::attachSwipe` | Commit 1 | grid cells (3), period nav (5), template list (12), calendar (12) |
| Bottom sheet | `app/static/js/bottom_sheet.js` + `.bottom-sheet-*` CSS | Commit 1 (extending the v1 backdrop) | quick-add (4), status menu (3), install prompt (8) |
| Sticky summary strip | `app/templates/partials/_sticky_summary.html` + `.summary-strip` CSS | Commit 2 | grid (2), /savings (9), /accounts (10), /loan (11) |
| Floating action button | `.fab` CSS + a11y attributes; rendered in `base.html` | Commit 4 (grid-scoped), Commit 13 (global) | grid (4), all mobile pages (13) |
| Quick-add form | `app/templates/_quick_add_sheet.html` + reuse of `TransactionCreateSchema` | Commit 4 | grid (4), global FAB (13) |
| Mobile design tokens | CSS custom properties in `app.css` (`--touch-target-min`, `--sheet-handle-h`, `--safe-area-*`) | Commit 1 | every mobile component |
| Service worker | `app/static/sw.js` registered from `base.html` | Commit 6 | app-wide |
| Push subscription | `app/static/js/push_subscription.js` + `/api/push/{subscribe,unsubscribe}` routes + `PushSubscription` model | Commit 7 | Section 4 (Notifications); not consumed in this plan |

The single open question, **Q-M1** (whether the global FAB should be visible on the grid
specifically, given that the grid already gets a grid-scoped FAB in Commit 4), is resolved in
Commit 13: the global FAB suppresses itself on the grid because the grid-scoped FAB is
positionally identical and avoids double-rendering. No user-visible duplicate.

---

## 5. Optional enhancements (listed; not in the default commit set unless promoted)

Each is independently valuable and called out so the developer can opt in. The plan flags
which commit would carry each if promoted.

- **OPT-M1. Pull-to-refresh on mobile grid.** Adds a pull-down gesture that re-fetches the
  current period's data via HTMX. Useful if data is changed on another device. Adds ~50
  lines of touch handling to `mobile_grid.js`. Folded as a Commit 5 extension if promoted.
- **OPT-M2. Haptic feedback on swipe commit.** `navigator.vibrate(20)` on a successful
  swipe-to-act. Works on Firefox Android; silently no-ops on iOS. Folded as a Commit 3
  extension if promoted.
- **OPT-M3. Periodic background sync.** Registers `periodicSync` in the service worker so
  the SW pre-warms the next period's data when the user is online but not on the page.
  Firefox does not yet ship `periodicSync` (as of writing); Chrome/Edge do. Listed only;
  do not build until Firefox ships it.
- **OPT-M4. Offline read-only mode.** Cache the last-fetched grid HTML and serve it with a
  prominent "OFFLINE -- last updated HH:MM" banner. Rejected by D-7 (no stale financial
  data) but listed here so the rejection is documented.
- **OPT-M5. iOS-Firefox "use Safari to install" deep-link.** Detect Firefox iOS and offer a
  one-tap link to open the app in Safari to install. Listed only; small UX nicety with
  uncertain reach (the user must then re-authenticate in Safari).
- **OPT-M6. Skeleton screens during navigation.** Replace HTMX's default loading with
  per-template skeleton screens. Folded as a Commit 5 extension if promoted; ~150 lines of
  additional CSS/partials.

---

## 6. Codebase inventory (files this plan touches)

Re-grep each path at edit time; line numbers below drift.

### New files

- `app/static/js/touch_gestures.js` -- swipe gesture utility, Pointer Events-based.
- `app/static/js/bottom_sheet.js` -- bottom sheet open/close/swipe-to-dismiss.
- `app/static/js/push_subscription.js` -- push subscription opt-in flow.
- `app/static/sw.js` -- service worker.
- `app/static/css/mobile_components.css` -- new file scoped to mobile-first components,
  imported from `app.css` (keeps the diff reviewable).
- `app/templates/partials/_sticky_summary.html` -- reusable summary strip.
- `app/templates/partials/_fab.html` -- reusable FAB.
- `app/templates/partials/_quick_add_sheet.html` -- quick-add bottom sheet.
- `app/models/push_subscription.py` -- `PushSubscription` model.
- `app/routes/push.py` -- subscribe / unsubscribe routes.
- `app/services/push_service.py` -- `send_push(user_id, payload)` helper (Section 4
  consumer).
- `migrations/versions/<auto>_create_push_subscriptions.py` -- table migration.
- Test files mirroring each (`tests/test_services/test_push_service.py`,
  `tests/test_routes/test_push.py`, `tests/test_models/test_push_subscription.py`).

### Modified files

- `app/templates/base.html` -- service worker registration, global FAB, optional install
  prompt slot.
- `app/templates/grid/_mobile_grid.html` -- sticky affordability strip, swipe handlers, FAB,
  swipe period navigation.
- `app/static/js/mobile_grid.js` -- swipe handler wiring, period navigation enhancements.
- `app/static/css/app.css` -- design tokens, extends bottom-sheet primitive.
- `app/templates/savings/index.html` -- sticky summary strip (Commit 9).
- `app/templates/accounts/index.html` (and child partials) -- sticky summary strip
  (Commit 10).
- `app/templates/loan/dashboard.html` -- sticky payoff strip + chart/tab layout
  (Commits 11, 16).
- `app/templates/templates/index.html` (template list) -- swipe-to-act (Commit 12).
- `app/templates/calendar/*.html` -- swipe-to-act (Commit 12).
- `app/templates/companion/index.html` and `_transaction_card.html` -- align with v2
  vocabulary (Commit 14).
- `app/templates/settings/*.html`, `app/templates/salary/*.html`,
  `app/templates/calibration/*.html` -- mobile form patterns (Commit 15).
- `app/services/balance_resolver.py` -- one new helper `balances_for_period_range(account,
  scenario_id, start_period, count)` only if Commit 2 measurement shows three sequential
  `balances_for` calls are too slow; not promised, decided at Commit 2 implementation.
- `app/routes/grid.py`, `app/routes/savings.py`, `app/routes/accounts.py`,
  `app/routes/loan.py` -- thin context additions for the strip data (no new
  business logic).
- `app/audit_infrastructure.py` -- `AUDITED_TABLES` extended to include
  `push_subscriptions` (Commit 7).
- `.env.example` -- VAPID public/private key documentation (Commit 7).

### Templates / static / migrations / docs

- `app/static/manifest.json` -- icons audited for completeness (Commit 8); update if any
  size is missing.
- `docs/coding-standards.md` -- one new sentence under "JavaScript" forbidding direct
  reads of `current_anchor_*` / `current_principal` in mobile partials (closes the rule in
  Section 1.1 above; promoted from convention to standard).
- `docs/project_roadmap_v5.md` -- update Appendix A on completion of this plan.

---

## 7. Commit dependency analysis

```text
Foundations
  1 touch gestures, bottom sheet primitive, design tokens ──┐
                                                            │
Phase A (grid)                                              │
  2 sticky affordability strip on mobile grid ──────────────┤
  3 swipe-to-act on grid cells (uses 1) ─────────────────────┤
  4 quick-add bottom sheet (uses 1) ─────────────────────────┤
  5 swipe period navigation (uses 1) ────────────────────────┤
                                                            │
Phase B (PWA)                                               │
  6 service worker shell ──────────────────────────────────┐ │
  7 push subscription plumbing (uses 6) ──────────────────┤ │
  8 install prompt + Firefox iOS fallback (uses 6) ───────┘ │
                                                            │
Phase C (rollout)                                           │
  9  sticky strip on /savings (uses 2) ───────────────────┐ │
 10  sticky strip on /accounts (uses 2) ─────────────────┤ │
 11  sticky strip on /loan dashboard (uses 2) ───────────┤ │
 12  swipe-to-act on template list + calendar (uses 3) ──┤ │
 13  global FAB (uses 4) ────────────────────────────────┤ │
 14  companion alignment (uses 1, 4) ────────────────────┘ │
                                                            │
Phase D (long tail)                                         │
 15  mobile-first forms (uses 1) ─────────────────────────┐ │
 16  mobile-first loan dashboard chart/tabs (uses 11) ───┘ │
                                                            │
Final gate                                                  │
 17  full suite + Firefox parity verification appendix ───┘
```

Ordering rationale:

- Commit 1 is the only blocker for Phase A (commits 2-5). It must land first.
- Commit 2 (affordability strip) is the highest user-felt value; ship it second.
- Commits 3-5 are independent of each other within Phase A; pick the order that fits
  attention.
- Phase B is independent of Phase A; can be interleaved if the developer wants to ship the
  user-visible Phase A first, then return for SW infrastructure.
- Phase C requires its Phase A parent (3 for swipe, 4 for quick-add, 2 for strip).
- Phase D is decoupled; can slide whenever convenient.
- The full suite (~65 s at `-n 12` default) runs after each commit; `pylint` runs after
  each commit; the Firefox parity check in Section F runs after each commit.

---

## 8. Commit checklist

| # | Commit message | Summary |
|---|----------------|---------|
| 1 | `feat(mobile): touch gestures, bottom sheet primitive, mobile design tokens` | Reusable swipe utility (Pointer Events), bottom sheet open/close/dismiss, CSS custom properties for touch targets + safe-area-insets + sheet sizing |
| 2 | `feat(mobile): sticky affordability strip on mobile grid (D-C)` | Three-period balance strip at top of mobile grid; reuses `balance_resolver`; sticky via `position: sticky` + safe-area-insets |
| 3 | `feat(mobile): swipe-to-act on mobile grid cells (D-A)` | Right-swipe marks Done; left-swipe opens status action sheet; reuses `state_machine`; keyboard equivalent retained |
| 4 | `feat(mobile): quick-add bottom sheet with template-aware and ad-hoc modes (D-B)` | Global quick-add form; template field optional; reuses `TransactionCreateSchema`; renders into grid-scoped FAB |
| 5 | `feat(mobile): swipe period navigation on mobile grid` | Left/right swipe between periods; CSS-only animation; keeps existing button nav |
| 6 | `feat(pwa): service worker shell with version-aware update flow` | Cache-first for static, network-first for HTML/JSON, version-keyed cache, update toast |
| 7 | `feat(pwa): web push subscription plumbing (no notifications sent yet)` | VAPID keys, `PushSubscription` model + migration, subscribe/unsubscribe routes, opt-in toggle in /settings |
| 8 | `feat(pwa): install prompt with Firefox iOS Safari fallback` | `beforeinstallprompt` handling for Firefox Android; one-time Safari install banner for iOS |
| 9 | `feat(mobile): sticky goal-progress strip on /savings` | Apply Commit 2 strip to /savings goals; per-goal summary |
| 10 | `feat(mobile): sticky net-worth strip on /accounts` | Apply Commit 2 strip to /accounts overview |
| 11 | `feat(mobile): sticky payoff-status strip on /loan dashboard` | Apply Commit 2 strip to /loan dashboard, replacing the visually heavy summary block |
| 12 | `feat(mobile): swipe-to-act on template list and bill calendar` | Apply Commit 3 swipe vocabulary outside the grid |
| 13 | `feat(mobile): global FAB for quick-add from any page` | Promote Commit 4's grid FAB to a global mobile-only component in `base.html`; suppresses on grid |
| 14 | `feat(mobile): align companion view with v2 patterns` | Companion adopts same FAB / bottom sheet / design tokens |
| 15 | `feat(mobile): mobile-first form patterns for settings/salary/calibration` | Sticky save bar, progressive disclosure, native input types, larger touch targets |
| 16 | `feat(mobile): mobile-first loan dashboard chart and tab layout` | Last because charts on small screens are the hardest; uses the strip from Commit 11 |
| 17 | `chore(release): mobile v2 full gate + Firefox parity verification appendix` | Full suite, pylint, manifest/icon audit, Firefox Desktop + Android + iOS verification log appended to this plan |

Promotable options not in the default count: OPT-M1 (pull-to-refresh), OPT-M2 (haptic),
OPT-M5 (iOS-Firefox deep link), OPT-M6 (skeleton screens).

---

## 9. Commits (detailed)

Each commit follows the house format: A message, B problem, C files, D implementation, E
tests, F manual verification, G downstream, H rollback. Test IDs are `M<commit>-<n>`.

### Commit 1 -- Touch gestures, bottom sheet primitive, mobile design tokens

**A. Commit message** `feat(mobile): touch gestures, bottom sheet primitive, mobile design tokens`

**B. Problem statement** Phase A commits (2-5) and Phase C commits (9-14) need a reusable
swipe utility, a reusable bottom sheet primitive, and shared CSS tokens (touch target
sizes, safe-area insets, sheet heights). Implementing these inline in each commit would
duplicate the gesture math, fragment the bottom-sheet behavior across files, and let
touch-target sizing drift. This commit consolidates them as one foundation; no user-visible
behavior change.

**C. Files modified**

- `app/static/js/touch_gestures.js` (new): `attachSwipe(el, options) -> () => void` (the
  returned function detaches). Built on Pointer Events (works in Firefox Gecko, GeckoView,
  WebKit). Handles `pointerdown` / `pointermove` / `pointerup` / `pointercancel`. Options:
  `onSwipeLeft`, `onSwipeRight`, `onSwipeUp`, `onSwipeDown`, `thresholdPx` (default 50),
  `restraintPx` (default 100, max perpendicular drift), `velocityPxPerMs` (default 0.3),
  `peekPx` (default 0; if >0, the element follows the pointer during the gesture up to this
  distance). Includes `setPointerCapture` for clean cancellation.
- `app/static/js/bottom_sheet.js` (new): `openSheet(contentEl, options)` and
  `closeSheet()`. Manages backdrop, focus trap (`inert` on background), `Escape` key,
  swipe-down-to-dismiss (reuses `attachSwipe`), and ARIA (`role="dialog"`,
  `aria-modal="true"`). Extends the v1 backdrop styling from `app.css:838` rather than
  duplicating it.
- `app/static/css/mobile_components.css` (new): imported once at the top of `app.css`.
  Holds all v2 mobile-only CSS additions (touch tokens, sheet sizing, FAB, summary strip,
  swipe peek styles).
- `app/static/css/app.css`: add `@import url('./mobile_components.css');` near the top;
  extract the existing v1 bottom-sheet block at `~:838` to `mobile_components.css`
  unchanged so the new file is the single source of bottom-sheet styles.
- `tests/test_static/test_static_assets.py` (new or extend): assert the new JS files load
  without syntax error via a quick Node-less smoke check (read file, eval-guard
  parse-only via `compile()`-equivalent: a simple regex confirming `export` / `function`
  shape is acceptable in lieu of a JS runtime).

**D. Implementation approach** The swipe utility uses Pointer Events exclusively (not
TouchEvents) because Pointer Events have unified Firefox / WebKit / Chrome support and
handle `pointercancel` correctly when the user begins a system gesture (back-swipe on
Android). `touch-action: pan-y` on the swipe-attached element lets vertical scroll through
horizontal swipes (and vice versa for swipe-up/down nav). The peek option performs CSS
transform on `pointermove` so the user sees the element follow the finger; on `pointerup`
the threshold + velocity decide commit or snap-back.

The bottom sheet primitive uses CSS custom properties for sheet height (`--sheet-h:
calc(100dvh - var(--safe-area-top, 0px))`) so it respects the dynamic viewport on Firefox
Android where the URL bar shows and hides. Focus management uses the `inert` attribute on
the page background (now broadly supported including Firefox 112+, GeckoView 121+, and
WebKit 15.4+).

Design tokens added to `mobile_components.css`:

```css
:root {
  --touch-target-min: 44px;           /* WCAG 2.5.5 AA */
  --touch-target-comfortable: 48px;   /* Material default */
  --safe-area-top: env(safe-area-inset-top);
  --safe-area-bottom: env(safe-area-inset-bottom);
  --sheet-handle-h: 32px;
  --sheet-radius: 16px;
  --strip-h: 56px;
  --fab-size: 56px;
  --fab-offset: 24px;
}
```

`env(safe-area-inset-*)` is supported in Firefox 120+ and WebKit; the fallback in
unsupported browsers is 0, which is the correct default.

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M1-1 | test_touch_gestures_js_parses | static asset | read file | non-empty, contains `export function attachSwipe` | New |
| M1-2 | test_bottom_sheet_js_parses | static asset | read file | non-empty, contains `export function openSheet` | New |
| M1-3 | test_mobile_components_css_imported_once | static asset | read `app.css` | exactly one `@import url('./mobile_components.css')` line | New |
| M1-4 | test_v1_bottom_sheet_block_removed_from_app_css | static asset | grep `app.css` | `bottom-sheet-backdrop` does not appear in `app.css` after the migration | New |
| M1-5 | test_mobile_components_css_contains_tokens | static asset | read | `--touch-target-min`, `--safe-area-top`, `--sheet-handle-h`, `--fab-size` all present | New |

**F. Manual verification steps**

1. `./scripts/test.sh tests/test_static -v` (or the targeted file) green.
2. `pylint app/ --fail-on=E,F` clean.
3. Load the app in Firefox Desktop (Gecko) and any existing mobile bottom sheet still
   opens, closes on Escape, traps focus.
4. Load the app in Firefox Android (GeckoView) and confirm the bottom sheet still works
   (the v1 full-edit popover converted at commit `c1cc309`).
5. Open DevTools Console -- no JS errors from the new files (they are imported but no
   visible call site exists yet; tree-shaking is not in scope here, the files are loaded
   lazily by Commits 2-5).

**G. Downstream effects** Commits 2-5 import from these files. No existing behavior
changes. The bottom sheet visual styling is byte-identical before and after the extract.

**H. Rollback notes** Re-inline the bottom-sheet block into `app.css`, delete the new JS
files and CSS file, remove the `@import`. No data, no migration.

---

### Commit 2 -- Sticky affordability strip on mobile grid (D-C)

**A. Commit message** `feat(mobile): sticky affordability strip on mobile grid (D-C)`

**B. Problem statement** The daily affordability check (current + next two periods' end
balance) requires swiping through three single-period cards on mobile. The desktop grid
shows multiple periods inline; the mobile card view does not. Result: the highest-frequency
mobile workflow is the most cumbersome. This commit adds a sticky three-period balance
strip at the top of the mobile grid so the affordability decision collapses to a glance.

**C. Files modified**

- `app/templates/partials/_sticky_summary.html` (new): reusable strip partial. Accepts a
  `cells` list (each cell: `label`, `value` formatted, `tone` -- positive / negative /
  neutral) and a `note` slot.
- `app/templates/grid/_mobile_grid.html`: include the partial at the top of the mobile
  card view (above the period navigation). Pass three cells derived from
  `balance_resolver.balances_for(account, scenario_id, [current, next, next+1])` already
  computed by the route.
- `app/routes/grid.py`: extend the existing mobile-grid context with
  `affordability_cells` -- a list of three `{label, value, tone}` dicts derived from the
  same `balance_resolver` call the grid uses today. Re-grep current route lines; do not
  invent a parallel query.
- `app/services/balance_resolver.py`: *only if measurement at implementation time shows
  three sequential `balances_for` calls are too slow*, add
  `balances_for_period_range(account, scenario_id, start_period, count) -> list[Decimal]`.
  Default expectation: three calls are fine; this helper is conditional, not promised.
- `app/static/css/mobile_components.css`: `.summary-strip` styles -- sticky top, safe-area
  aware, tonal colors via Bootstrap text utilities, swipe gestures NOT attached (the strip
  is read-only).
- `tests/test_routes/test_grid.py`: assert the mobile grid context includes
  `affordability_cells` and the values match `balance_resolver`. Add a Firefox-tolerance
  test that the partial renders without JS (sticky CSS is non-essential).
- `tests/test_integration/test_balance_consistency.py` (existing per HIGH-01): extend to
  assert the strip's values equal the grid's own current-period and projected-period
  end-balance values for the same `(user, scenario, account)` tuple.

**D. Implementation approach** Route reads the three periods (current and the next two)
from `balance_resolver.balances_for(...)` (the canonical producer per the financial
remediation; methods at `balance_resolver.py:371,452,637`). Money rounding goes through
`app.utils.money.round_money` (E-26 helper) on the server before serialization. The
template emits the cell content as text and a `data-tone` attribute; CSS picks the tone
color. JS is not involved -- the strip is non-interactive in v2 (interactivity, like
tapping a cell to jump to its period, can be a v3 enhancement).

`position: sticky; top: var(--safe-area-top, 0)` keeps the strip below the URL bar on
Firefox Android during the bar's collapse animation. The strip uses `backdrop-filter:
blur(8px)` for visual separation against the content scrolling under it -- supported in
Firefox 103+ and WebKit 18+.

Re-grep before editing:

- `app/templates/grid/_mobile_grid.html` for the include location.
- `app/routes/grid.py` for the existing mobile-context branch (around the
  `is_mobile_request()` or equivalent dispatch).

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M2-1 | test_grid_mobile_includes_affordability_cells | seeded user, three periods | GET grid as mobile UA | response context has 3-cell list with formatted values | New |
| M2-2 | test_affordability_cells_match_balance_resolver | as above | compute via `balances_for` directly | identical Decimal values | New |
| M2-3 | test_affordability_strip_negative_tone | period 3 projected negative | GET grid | cell 3 `tone == "negative"` | New |
| M2-4 | test_affordability_strip_partial_renders | render `_sticky_summary.html` with 3 cells | -- | DOM has 3 `.summary-strip-cell` elements with `data-tone` | New |
| M2-5 | test_cross_page_strip_equals_grid | symptom-tuple fixture | compare strip cell 1 with grid current-period end balance | equal | New |
| M2-6 | test_affordability_uses_balance_resolver_only | grep | `_mobile_grid.html` does not read `current_anchor_*` | passes | New |

Re-pinned tests: none; this is additive.

**F. Manual verification steps**

1. `./scripts/test.sh tests/test_routes/test_grid.py tests/test_integration/test_balance_consistency.py -v` green.
2. Open the app on Firefox Android. The mobile grid shows three balances at the top,
   sticky during scroll. The URL bar collapse does not cover the strip.
3. Open the app on Firefox Desktop in a mobile viewport (devtools responsive mode, 375px).
   Same behavior.
4. Open on Firefox iOS (if available). The strip renders; backdrop blur may degrade on
   older WebKit -- acceptable.
5. Confirm the strip's current-period value equals the desktop grid's current-period end
   balance for the same data.

**G. Downstream effects** Commits 9-11 reuse `_sticky_summary.html` for /savings,
/accounts, /loan. The strip's positional convention (top, sticky, safe-area aware) is
locked here for visual consistency across pages.

**H. Rollback notes** Remove the include from `_mobile_grid.html`, remove
`affordability_cells` from the route context, delete the partial. Strip CSS can stay (no
consumer left, harmless).

---

### Commit 3 -- Swipe-to-act on mobile grid cells (D-A)

**A. Commit message** `feat(mobile): swipe-to-act on mobile grid cells (D-A)`

**B. Problem statement** Marking a Projected transaction Done on mobile currently requires:
tap cell -> bottom sheet opens -> select Done -> close. Three interactions for a state
transition the user has already decided on. This commit adds swipe-to-act: right-swipe
marks Done, left-swipe opens a status action sheet (Cancelled, Credit, Settled where valid).
Both gestures show a visual peek and cancel cleanly if released before the threshold.

**C. Files modified**

- `app/static/js/mobile_grid.js`: import `attachSwipe` from `touch_gestures.js`, attach to
  each `.mobile-txn-cell` on render. Right-swipe calls a small handler that posts to the
  existing mark-done endpoint via HTMX (reuses `hx-post` URL, no new route). Left-swipe
  opens a bottom sheet rendered from a new partial.
- `app/templates/grid/_transaction_card.html` (or the mobile card partial -- re-grep at
  edit time): add `data-swipe-actionable="true"` and the necessary `data-txn-id` /
  `data-status-id` attributes so JS knows which transaction is which.
- `app/templates/partials/_status_action_sheet.html` (new): the left-swipe action sheet
  content -- a list of valid next-statuses derived from the state machine, each a
  `hx-post` button to the existing status-transition endpoint.
- `app/routes/grid.py` (or wherever the mobile cell context is built): pass the list of
  valid next-statuses per cell so the action sheet does not need a client-side state
  machine. Reuses `app.services.state_machine.allowed_transitions(current_status_id)` --
  the canonical state machine, not a JS re-implementation.
- `app/static/css/mobile_components.css`: `.swipe-peek` styles for the green Done peek
  (right) and the gray menu peek (left); accessible icons.
- `tests/test_routes/test_grid.py`: assert the cell context includes
  `allowed_next_status_ids`; existing mark-done route tests still pass unchanged.
- `tests/test_services/test_state_machine.py`: existing; no changes.

**D. Implementation approach** The swipe handler does not implement any state-machine logic
client-side. It posts to the existing endpoints (the same ones the bottom-sheet edit form
posts to). On success (HTMX swap), the cell re-renders with the new status; the swipe
visual snaps back automatically because the DOM node is replaced.

State machine: `state_machine.allowed_transitions(current_status_id)` returns the list of
allowed `(action, target_status_id)` tuples. Right-swipe picks the "mark Done" action if
present in the list; if not present (e.g., the cell is already Done), the right-swipe is
suppressed and the visual peek does not start. Left-swipe always opens the menu, even if
the menu has only one entry, for predictability.

Keyboard equivalent (hard rule 5): the existing cell tap still opens the full bottom sheet
with the status selector. No regression.

Right-swipe is the primary positive action (D-A). Left-swipe reveals the menu. This matches
iOS Mail; Firefox Android has no platform convention so iOS wins.

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M3-1 | test_grid_mobile_cell_has_allowed_next_statuses | Projected cell | GET mobile grid | cell context includes `allowed_next_status_ids` matching state machine | New |
| M3-2 | test_grid_mobile_done_cell_has_no_done_action | already Done cell | GET mobile grid | `allowed_next_status_ids` excludes Done | New |
| M3-3 | test_mark_done_endpoint_unchanged | -- | POST existing mark-done | 200, status changed (regression check) | Existing |
| M3-4 | test_status_action_sheet_partial_renders | render with 3 actions | -- | 3 buttons with hx-post URLs | New |
| M3-5 | test_swipe_attached_only_to_actionable_cells | mobile card partial | grep | `data-swipe-actionable` only on cells with at least one allowed transition | New |

**F. Manual verification steps**

1. `./scripts/test.sh tests/test_routes/test_grid.py tests/test_services/test_state_machine.py -v` green.
2. Firefox Android: right-swipe a Projected cell. Visual green peek follows the finger;
   release past threshold marks it Done. Release before threshold snaps back.
3. Firefox Android: left-swipe the same cell. Visual gray peek; release opens the action
   sheet with Cancelled / Credit / Settled (filtered by state machine).
4. Firefox Desktop responsive mode: gestures work via mouse drag (Pointer Events handle
   both).
5. Tap a cell (no swipe). The existing bottom sheet still opens with the status selector
   (keyboard equivalent retained).
6. Test on a Done cell: right-swipe does nothing (no peek, no action). Left-swipe opens
   the menu showing only valid transitions from Done.

**G. Downstream effects** Commit 12 reuses the swipe-to-act vocabulary on the template list
and bill calendar. The `_status_action_sheet.html` partial is reusable.

**H. Rollback notes** Remove the swipe attach in `mobile_grid.js`, remove
`data-swipe-actionable` attributes, delete the action sheet partial. The existing tap-to-
edit flow still works.

---

### Commit 4 -- Quick-add bottom sheet with template-aware and ad-hoc modes (D-B)

**A. Commit message** `feat(mobile): quick-add bottom sheet with template-aware and ad-hoc modes (D-B)`

**B. Problem statement** Adding a new transaction on mobile currently requires finding the
right cell to tap "edit" in -- or worse, finding the desktop "add transaction" button on
the mobile layout. There is no fast-path entry for a brand-new transaction. This commit
adds a quick-add bottom sheet, reachable from a grid-scoped FAB, supporting both
template-aware entry (pre-populate from a template) and ad-hoc entry (no template).

**C. Files modified**

- `app/templates/partials/_fab.html` (new): reusable FAB; accepts an `href` or
  `data-action` attribute.
- `app/templates/partials/_quick_add_sheet.html` (new): the bottom sheet form. Single
  form: optional template typeahead (renders a `<select>` with "(custom transaction)" as
  the first option), required amount, required transaction type (expense / income),
  required account, required category, optional notes, default to current period.
  Selecting a template auto-populates fields but leaves them editable.
- `app/templates/grid/_mobile_grid.html`: render `_fab.html` with
  `data-action="open-quick-add"`. The FAB sits bottom-right with `--fab-offset` from the
  safe-area-inset-bottom.
- `app/static/js/mobile_grid.js`: handle FAB click -> `bottom_sheet.openSheet(...)` with
  the quick-add partial fetched via HTMX from a new route.
- `app/routes/transactions.py` (or wherever the create endpoint lives -- re-grep at edit
  time): add `GET /transactions/quick-add` returning the partial with the form
  pre-populated for the current period; the existing POST create endpoint handles the
  submit. No new POST endpoint -- the existing one reuses `TransactionCreateSchema` from
  `app/schemas/validation.py`.
- `tests/test_routes/test_transactions.py`: assert the new GET endpoint returns the
  partial with the expected form fields and that the existing POST handles a submit with
  no template_id (the ad-hoc path).

**D. Implementation approach** The form is a single HTML form. Template field is a `<select>`
populated server-side with the user's active templates; the first `<option value="">` is
"(custom transaction -- no template)". When the user selects a template, a small inline JS
handler (in `mobile_grid.js`) reads the template's defaults from `data-*` attributes on
the `<option>` and populates the other fields. Crucially, the fields remain editable so
the user can override any default. This is one surface, not two.

The POST goes through the existing create endpoint, which validates via
`TransactionCreateSchema`. The schema already accepts an optional `template_id`; the
ad-hoc path simply leaves it null. The schema is the single source of truth for what fields
are required -- the bottom sheet form mirrors them; if the schema grows a field in the
future, the form does not, and the user sees a clear validation error. (This is a
deliberate design choice to keep the bottom sheet minimal; if a new required field is
needed for mobile entry, that is a separate commit.)

On success, the bottom sheet closes and the grid refreshes the current period via HTMX.

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M4-1 | test_quick_add_get_returns_partial | auth client | GET /transactions/quick-add | 200, contains form with template select, amount, type, account, category | New |
| M4-2 | test_quick_add_template_options_include_custom | seeded templates | GET /transactions/quick-add | first option is the custom-transaction placeholder | New |
| M4-3 | test_quick_add_post_with_template | seeded template | POST with template_id + amount | 200/302, transaction created with template's defaults overridden by submitted values | New (extends existing POST tests) |
| M4-4 | test_quick_add_post_ad_hoc | -- | POST without template_id, all required fields | 200/302, transaction created, `template_id is None` | New |
| M4-5 | test_quick_add_post_missing_required | -- | POST without amount | 422, validation error on amount | New |
| M4-6 | test_quick_add_reuses_create_schema | grep | the GET route imports `TransactionCreateSchema`, no parallel schema defined | passes | New |
| M4-7 | test_quick_add_fab_renders_on_mobile_grid | mobile UA | GET grid | FAB partial present with `data-action="open-quick-add"` | New |

**F. Manual verification steps**

1. `./scripts/test.sh tests/test_routes/test_transactions.py tests/test_routes/test_grid.py -v` green.
2. Firefox Android: tap the FAB. Bottom sheet opens with the quick-add form. Select a
   template; other fields populate. Submit; sheet closes; new transaction appears in the
   grid.
3. Firefox Android: tap the FAB again. Leave the template field as "(custom transaction)".
   Fill amount, type, account, category. Submit; ad-hoc transaction appears.
4. Firefox Desktop responsive mode: same behavior; sheet swipe-down-to-dismiss works via
   mouse drag.
5. Submit with missing amount: server returns 422; the bottom sheet shows the validation
   error in-place (HTMX swap with the error variant of the partial).
6. Confirm the new transaction shows up on the desktop grid view as well (single source
   of truth -- the same DB row).

**G. Downstream effects** Commit 13 promotes the FAB from grid-scoped to global; Commit 14
applies the same FAB to the companion view.

**H. Rollback notes** Delete the new partials and route; remove the FAB include and the
JS handler. The existing add-transaction paths still work.

---

### Commit 5 -- Swipe period navigation on mobile grid

**A. Commit message** `feat(mobile): swipe period navigation on mobile grid`

**B. Problem statement** The mobile grid's single-period card view requires tapping the
"next" or "previous" period button to navigate. Swiping between periods is the standard
mobile pattern for paginated card content (calendar months, photo galleries, story carousels).
This commit adds left/right swipe between periods, complementing the existing buttons (which
remain for keyboard accessibility and discoverability).

**C. Files modified**

- `app/static/js/mobile_grid.js`: attach swipe handler to the period card container.
  Left-swipe navigates to next period; right-swipe navigates to previous period. Uses
  `touch-action: pan-y` so vertical scroll within the card is preserved.
- `app/templates/grid/_mobile_grid.html`: add a CSS class to the period card container so
  the JS can find it.
- `app/static/css/mobile_components.css`: optional CSS transition for the swipe peek (the
  card follows the finger up to 60px before snapping or committing).
- No route changes (the existing period-navigation HTMX endpoint serves both button and
  swipe).

**D. Implementation approach** The swipe handler calls the existing HTMX endpoint by
synthesizing a click on the hidden next/previous button (avoids a second code path for
navigation). The peek effect uses CSS transform; on commit, the HTMX swap replaces the card
and the transform resets naturally.

Edge cases:

- First period: right-swipe is suppressed (no previous to go to).
- Last loaded period: left-swipe is suppressed if the route returns a known last-period
  marker, otherwise it falls through to the existing behavior (which may load a new
  period).
- During an HTMX request (loading state): swipe is suppressed.

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M5-1 | test_mobile_grid_period_card_has_swipe_target | mobile UA | GET grid | period card container has `data-swipe-period` | New |
| M5-2 | test_existing_period_nav_unchanged | -- | click next/prev button | unchanged (regression) | Existing |
| M5-3 | test_mobile_grid_first_period_has_no_previous_swipe | first period of user's history | GET grid | container has `data-swipe-allow-prev="false"` | New |

**F. Manual verification steps**

1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. Firefox Android: swipe left on the period card; next period loads. Swipe right; previous
   period loads.
3. On the first period (no previous), right-swipe shows no peek and does not navigate.
4. Buttons still work (regression check).
5. Vertical scroll within the period card is unaffected.

**G. Downstream effects** None outside the grid.

**H. Rollback notes** Remove the swipe attach in `mobile_grid.js`. Buttons remain.

---

### Commit 6 -- Service worker shell with version-aware update flow

**A. Commit message** `feat(pwa): service worker shell with version-aware update flow`

**B. Problem statement** The app has a PWA manifest (commit pre-v1) but no service worker.
A service worker is the prerequisite for: (a) Web Push notifications (Section 4 will
consume), (b) faster repeat-load times via cached static assets, (c) the "Add to Home
Screen" install on Firefox Android feeling like a real app (the install banner is gated
on SW presence in some browsers). This commit adds a minimal SW: cache-first for static
assets, network-first for HTML/JSON, no offline financial data, with a version-aware
update flow that notifies the user when a new SW is ready.

**C. Files modified**

- `app/static/sw.js` (new): the service worker. Listens for `install`, `activate`,
  `fetch`, `message`. Cache name includes a version constant (`SHEKEL_SW_V = '1.0.0'`)
  that this plan increments on Commits 6, 7, 8 and on any future SW change.
- `app/static/js/sw_register.js` (new): registers the SW from `base.html` on `load`,
  listens for `updatefound`, and dispatches a custom event when a new SW is ready.
- `app/templates/base.html`: add `<script type="module" src="{{ url_for('static',
  filename='js/sw_register.js') }}" defer></script>` near the bottom; add a toast
  container `<div id="sw-update-toast" hidden>...</div>` with "Reload to update" action.
- `app/static/css/mobile_components.css`: `.sw-update-toast` styles.
- `app/routes/__init__.py` (or wherever a passthrough route lives): if needed, ensure
  `app/static/sw.js` is served from the application root so the SW scope is `/` (browsers
  scope SW to the directory containing the SW file by default; serving from
  `/static/sw.js` scopes to `/static/`, which is wrong). Add a one-line passthrough route
  `@app.route('/sw.js')` that streams the file from `app/static/sw.js`. Tested.
- `tests/test_routes/test_sw.py` (new): assert `/sw.js` returns 200, content-type
  `application/javascript`, and contains the version constant.

**D. Implementation approach**

```javascript
// sw.js
const SHEKEL_SW_V = '1.0.0';
const STATIC_CACHE = `shekel-static-${SHEKEL_SW_V}`;
const STATIC_PATTERNS = [/\.(css|js|woff2?|png|jpg|svg|ico)$/, /\/static\/manifest\.json$/];

self.addEventListener('install', (e) => {
  // No precache list; lazy population.
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k.startsWith('shekel-') && k !== STATIC_CACHE)
          .map(k => caches.delete(k))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  const isStatic = STATIC_PATTERNS.some(p => p.test(url.pathname));
  if (e.request.method !== 'GET') return;                  // pass through
  if (!isStatic) return;                                   // network-only for HTML/JSON
  e.respondWith(
    caches.open(STATIC_CACHE).then(async (cache) => {
      const hit = await cache.match(e.request);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok) cache.put(e.request, res.clone());
      return res;
    })
  );
});
```

`sw_register.js`:

```javascript
if ('serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    const reg = await navigator.serviceWorker.register('/sw.js');
    reg.addEventListener('updatefound', () => {
      const sw = reg.installing;
      sw.addEventListener('statechange', () => {
        if (sw.state === 'installed' && navigator.serviceWorker.controller) {
          document.getElementById('sw-update-toast').hidden = false;
        }
      });
    });
  });
}
```

The toast has a "Reload" button that calls `window.location.reload()`. No `skipWaiting`
message round-trip in v2 -- the user explicitly reloads, which both activates the new SW
and refreshes the page.

Hard rule 7: HTML and JSON never enter the cache. Verified by the `if (!isStatic) return;`
fall-through (which passes the fetch event to the network without intervention).

Firefox specifics: Gecko fully supports SW (since Firefox 44, current behavior is mature).
GeckoView supports SW. Firefox iOS = WebKit; SW support is present in WebKit but the
install banner does not fire. Documented as expected behavior in Commit 8.

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M6-1 | test_sw_route_returns_js | -- | GET /sw.js | 200, content-type contains "javascript" | New |
| M6-2 | test_sw_contains_version_constant | -- | GET /sw.js | body contains `SHEKEL_SW_V` | New |
| M6-3 | test_sw_does_not_cache_html | -- | GET /sw.js | body contains "network-only" rationale comment AND a `return;` pre-cache check | New |
| M6-4 | test_sw_register_script_in_base | -- | GET / (any page) | response contains `sw_register.js` script tag | New |
| M6-5 | test_sw_update_toast_in_base | -- | GET / | response contains `#sw-update-toast` element | New |

**F. Manual verification steps**

1. `./scripts/test.sh tests/test_routes/test_sw.py -v` green.
2. Firefox Desktop: load the app, open DevTools -> Application -> Service Workers. The
   `shekel` SW is registered with scope `/`, status `activated and is running`.
3. Modify any static CSS file, restart the server. Reload. DevTools shows
   "waiting to activate." The toast appears. Click Reload. Page refreshes, the new SW is
   activated, and the change is visible.
4. Firefox Desktop: navigate offline (DevTools -> Network -> Offline). Existing static
   assets load from cache. HTML/JSON requests fail with a network error (intended -- no
   stale financial data per hard rule 7).
5. Firefox Android: same checks; install the PWA via Add to Home Screen; launch from home
   screen; SW is active and updates work.
6. Firefox iOS: SW registers under WebKit; cached assets load; install via Safari (not
   Firefox iOS) is required for any meaningful PWA install.

**G. Downstream effects** Commits 7 and 8 build on the SW registration. Section 4 (Notifications)
consumes the SW for push delivery.

**H. Rollback notes** Remove the SW registration script from `base.html`; users with the SW
still installed will continue to use cached assets until their browser unregisters it (can
be forced by serving a no-op SW at `/sw.js` that calls `self.registration.unregister()`).
Document this rollback procedure in the commit message.

---

### Commit 7 -- Web push subscription plumbing (no notifications sent yet)

**A. Commit message** `feat(pwa): web push subscription plumbing (no notifications sent yet)`

**B. Problem statement** Section 4 (Notifications) needs server-side push capability. This
commit lands the subscription round-trip and a server-side `send_push(user_id, payload)`
helper, but does not yet trigger any notifications (those land with Section 4's
notification types). Building the plumbing now means Section 4 only writes notification
content, not infrastructure.

**C. Files modified**

- `app/models/push_subscription.py` (new): `PushSubscription` model. Columns:
  `id`, `user_id (FK auth.users, ondelete='CASCADE', NOT NULL)`, `endpoint (TEXT NOT NULL
  UNIQUE)`, `p256dh_key (TEXT NOT NULL)`, `auth_key (TEXT NOT NULL)`, `device_label
  (VARCHAR(64) nullable, user-set)`, `created_at`, `updated_at`,
  `last_used_at (TIMESTAMPTZ nullable)`. Indexes: `ix_push_subscriptions_user_id`,
  `ix_push_subscriptions_endpoint` (already covered by UNIQUE), `ix_push_subscriptions_user_id_last_used` (partial, where last_used_at is not null).
- `migrations/versions/<auto>_create_push_subscriptions.py` (new): creates the table with
  explicit constraint names, adds to `system.audit_log` triggers via the existing
  infrastructure.
- `app/audit_infrastructure.py`: add `('budget', 'push_subscriptions')` -- wait, this is
  an auth-related concern but storing per-user device tokens. The right schema is `auth`
  (auth-related, user-scoped device identity). Re-confirm with the audit policy in
  CLAUDE.md -- audited tables in `auth`, `budget`, `salary`. Add `('auth',
  'push_subscriptions')` to `AUDITED_TABLES`.
- `app/routes/push.py` (new): `POST /api/push/subscribe`,
  `DELETE /api/push/unsubscribe/<sub_id>`, `GET /api/push/subscriptions` (user-scoped
  list). All routes ownership-checked via the existing `app/utils/auth_helpers.py`
  pattern; 404 for "not yours."
- `app/services/push_service.py` (new): `send_push(user_id: int, payload: dict, ttl_seconds:
  int = 3600) -> int` returning the number of subscriptions delivered to. Uses the
  `pywebpush` library (new dependency -- approval required, see G below). On 410 Gone or
  404 from the push endpoint, deletes the subscription (the user has uninstalled the PWA
  or revoked permissions).
- `app/static/js/push_subscription.js` (new): client-side subscribe/unsubscribe flow.
  Reads `VAPID_PUBLIC_KEY` from a `<meta name="vapid-public-key">` tag in `base.html`.
  Uses `navigator.serviceWorker.ready` then `reg.pushManager.subscribe({ userVisibleOnly:
  true, applicationServerKey: ... })`.
- `app/templates/base.html`: add `<meta name="vapid-public-key" content="{{
  config.VAPID_PUBLIC_KEY }}">`.
- `app/templates/settings/index.html` (or wherever settings live -- re-grep): add a
  "Push notifications" section with a per-device toggle. List existing subscriptions for
  this user with their `device_label` (or "Unnamed device" + last-used date).
- `scripts/generate_vapid_keys.py` (new): one-time helper to generate VAPID keypair and
  print to stdout for copy-paste into `.env`. Does NOT write to `.env` automatically (no
  secrets handling shortcuts).
- `.env.example`: document `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` requirements.
- `app/__init__.py` or config: load VAPID keys from env; fail loudly at startup if push
  is configured but keys are absent (rather than silently fail at push-send time).
- `requirements.txt`: pin `pywebpush==X.Y.Z` (latest stable at time of implementation).
- Tests: `tests/test_models/test_push_subscription.py`, `tests/test_routes/test_push.py`,
  `tests/test_services/test_push_service.py`.
- `python scripts/build_test_template.py` rerun is required (new table; CLAUDE.md
  testing section).

**D. Implementation approach** VAPID (Voluntary Application Server Identification, RFC 8292)
authenticates the server to the push service. The server holds the private key; the public
key is sent to the browser at subscribe time. The browser returns a `PushSubscription`
object containing the endpoint URL (a unique URL on the browser's push service), a public
ECDH key (`p256dh`), and an auth secret (`auth`). The server stores all three; to send a
push, the server encrypts the payload using the subscription's keys and POSTs to the
endpoint.

`pywebpush` handles the encryption. The dependency is widely used (Mozilla Foundation
maintains the spec), MIT-licensed, and pure Python (no compiled deps to manage in the
Docker image).

`send_push` returns delivered-count rather than raising. Section 4 will call it from
notification triggers and may have many subscriptions per user (one per device); a single
subscription failure (e.g., the user uninstalled on one device) should not abort delivery
to other devices.

Subscriptions are device-scoped: one user can have many. `endpoint` is UNIQUE to prevent
duplicate subscriptions if the user re-subscribes. The route's POST upserts on endpoint
(updates the `p256dh_key` / `auth_key` if the user re-subscribes with a new browser
session).

`device_label` is user-set in the settings UI ("Josh's iPhone", "Desktop Firefox"); if
not set, the UI shows "Unnamed device" plus the last-used date.

Hard rule 7: this commit does NOT cache or store any financial data. Push payloads are
opaque encrypted blobs to the browser's push service; only the user's device decrypts.

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M7-1 | test_push_subscription_model_constraints | -- | create with NULL endpoint | IntegrityError | New |
| M7-2 | test_push_subscription_endpoint_unique | -- | create two with same endpoint | IntegrityError | New |
| M7-3 | test_push_subscription_user_cascade | -- | delete user | subscriptions cascade-deleted | New |
| M7-4 | test_push_subscription_audited | -- | insert | row appears in system.audit_log | New |
| M7-5 | test_subscribe_route_creates_subscription | auth client | POST /api/push/subscribe with valid payload | 201, row in DB | New |
| M7-6 | test_subscribe_route_upserts_on_duplicate_endpoint | existing subscription | POST same endpoint with new keys | 200, keys updated, no duplicate | New |
| M7-7 | test_unsubscribe_route_ownership | second user's subscription | DELETE /api/push/unsubscribe/<id> | 404 (not 403, per CLAUDE.md security policy) | New |
| M7-8 | test_unsubscribe_route_deletes | own subscription | DELETE | 204, row gone | New |
| M7-9 | test_subscriptions_list_user_scoped | two users with subscriptions | GET /api/push/subscriptions | only own | New |
| M7-10 | test_send_push_zero_subscriptions | user with none | `send_push(user.id, {})` | returns 0, no error | New |
| M7-11 | test_send_push_deletes_410 | mock pywebpush to return 410 Gone | `send_push` | subscription deleted, returns 0 | New |
| M7-12 | test_send_push_deletes_404 | mock pywebpush to return 404 | `send_push` | subscription deleted | New |
| M7-13 | test_send_push_continues_on_single_failure | two subs, mock first to fail | `send_push` | returns 1, second succeeded | New |
| M7-14 | test_vapid_required_at_startup | missing env var, push enabled | startup | raises RuntimeError with actionable message | New |
| M7-15 | test_settings_page_lists_subscriptions | auth client | GET /settings | response contains the subscription list section | New |
| M7-16 | test_pywebpush_pinned | -- | read requirements.txt | `pywebpush==` present with explicit version | New |

**F. Manual verification steps**

1. `./scripts/test.sh tests/test_models/test_push_subscription.py tests/test_routes/test_push.py tests/test_services/test_push_service.py -v` green.
2. `python scripts/build_test_template.py` succeeds (new table).
3. `python scripts/generate_vapid_keys.py` -> add output to `.env`.
4. Restart server. /settings shows the Push Notifications section.
5. Firefox Desktop: enable push for this device. Permission prompt appears; accept. The
   subscription appears in the list with "Desktop Firefox" as a default label (or
   "Unnamed").
6. Run `flask shell` and call `from app.services.push_service import send_push;
   send_push(current_user.id, {'title': 'Test', 'body': 'Hello'})`. A native Firefox
   notification appears. (No Section 4 trigger wired in this commit -- this is the
   manual smoke.)
7. Firefox Android: same flow; Android system notification appears.
8. Firefox iOS: subscribe fails (WebKit Push requires Safari install). The toggle is
   present; the failure is reported gracefully with a "Install via Safari to enable
   notifications on iOS" message. Documented in Commit 8.

**G. Downstream effects** Section 4 (Notifications) consumes `send_push` from its
notification triggers. The `pywebpush` dependency is the only new pinned package -- this
requires developer approval per CLAUDE.md ("Dependencies pinned in requirements.txt -- no
new packages without approval"). Get explicit approval before merging.

**H. Rollback notes** `flask db downgrade -1` drops the table. Remove the route, model,
service, JS, settings UI. Unsubscribe all clients manually (their stored subscriptions
become orphans but cannot be used without server-side keys).

---

### Commit 8 -- Install prompt with Firefox iOS Safari fallback

**A. Commit message** `feat(pwa): install prompt with Firefox iOS Safari fallback`

**B. Problem statement** The PWA is installable on Firefox Android (via the URL bar menu)
and on Firefox Desktop (via the URL bar install icon when SW is registered). Neither
platform fires `beforeinstallprompt` reliably, but Firefox Android exposes an in-page
install API via the menu. Firefox iOS cannot install the PWA at all -- the user must
switch to Safari. This commit adds a small, one-time "Install to Home Screen" prompt that
appears on mobile only and routes Firefox iOS users to Safari with clear instructions.

**C. Files modified**

- `app/templates/partials/_install_prompt.html` (new): the bottom sheet content. Detects
  platform via user agent (UA sniffing is a smell but is justified here -- no feature
  detection distinguishes Firefox iOS from Safari iOS for install eligibility).
- `app/static/js/install_prompt.js` (new): one-time logic. Stores `install_prompt_seen=1`
  in `localStorage` on dismiss or install. Detects Firefox iOS via UA contains `FxiOS`.
- `app/templates/base.html`: include the prompt partial on mobile only (server-side
  detection acceptable since the partial is rendered only when needed).
- `app/static/css/mobile_components.css`: prompt styles (reuses the bottom sheet primitive).
- No route changes.

**D. Implementation approach** The install prompt appears once per device, three days after
first visit (no point prompting on the user's first interaction with the app). On Firefox
Android, the prompt directs to the URL bar menu's "Install" option with a small inline
diagram. On Firefox Desktop, no prompt (install is too discoverable in the URL bar to
warrant a popup). On Firefox iOS, the prompt explains that PWA install requires Safari and
provides a one-tap link that opens the current URL in Safari (`x-web-search://` or just a
plain `<a>` -- iOS does not have a deep link to "open in Safari," but the user can
copy-paste the URL or use the share sheet).

The prompt is dismissable; dismissal stores `install_prompt_seen=1`. Re-enabling requires
manually clearing localStorage (documented in the prompt itself for the user).

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M8-1 | test_install_prompt_partial_renders | -- | render `_install_prompt.html` | DOM contains the prompt structure | New |
| M8-2 | test_install_prompt_not_in_desktop_base | desktop UA | GET / | response does NOT contain the prompt partial | New |
| M8-3 | test_install_prompt_in_mobile_base | mobile UA | GET / | response contains the prompt partial | New |
| M8-4 | test_install_prompt_js_detects_firefox_ios | mock UA `FxiOS` | JS function call | returns 'firefox-ios' | New (jest-less; pattern test in `tests/test_static`) |

**F. Manual verification steps**

1. `./scripts/test.sh tests/test_routes -k install -v` green.
2. Firefox Desktop: load the app. No install prompt. URL bar install icon works.
3. Firefox Android (fresh profile): visit the app. Set system clock forward 3 days. Reload.
   Install prompt appears with "Install via menu" instructions. Tap install in the menu.
   PWA installed; app launches from home screen.
4. Firefox iOS (fresh profile): same time-shift. Install prompt appears with "Install
   requires Safari" explanation. URL copy works.
5. Dismiss prompt. Reload. Prompt does not reappear.

**G. Downstream effects** None.

**H. Rollback notes** Remove the partial include and JS file.

---

### Commit 9 -- Sticky goal-progress strip on /savings

**A. Commit message** `feat(mobile): sticky goal-progress strip on /savings`

**B. Problem statement** /savings is the second-highest-traffic page for the user's daily
workflow (after /grid). On mobile, the goal progress is buried below the per-account
breakdown, requiring scroll. Apply the Commit 2 strip to surface goal progress at a glance.

**C. Files modified**

- `app/templates/savings/index.html`: include `_sticky_summary.html` at the top with cells
  derived from `savings_dashboard_service` (existing producer, routed through
  `balance_resolver` per the financial calc remediation).
- `app/routes/savings.py`: extend the context with `summary_cells` -- the same shape
  Commit 2 introduced.
- `tests/test_routes/test_savings.py`: assert the cells exist and match the canonical
  producer.

**D. Implementation approach** Cells: total saved, on-track count, off-track count. All
derived from the existing service. No new business logic.

**E. Test cases** (abbreviated; mirror Commit 2's structure)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M9-1 | test_savings_mobile_has_summary_cells | seeded goals | GET /savings | 3 cells in context | New |
| M9-2 | test_savings_summary_matches_canonical_producer | -- | compare to `savings_dashboard_service` | equal | New |

**F. Manual verification steps** Firefox Android: /savings shows sticky strip with 3
metrics. Tap each cell to expand details (deferred to Phase D if requested).

**G. Downstream effects** None.

**H. Rollback notes** Remove the include and the context key.

---

### Commit 10 -- Sticky net-worth strip on /accounts

**A. Commit message** `feat(mobile): sticky net-worth strip on /accounts`

**B. Problem statement** /accounts shows per-account balances but the user's daily question
is "what is my total net worth and is it trending up?" Buried below the account list on
mobile. Apply the Commit 2 strip.

**C. Files modified**

- `app/templates/accounts/index.html`: include the strip with cells: total net worth,
  liquid total, debt total.
- `app/routes/accounts.py`: extend context. All values routed through `balance_resolver`
  / `loan_resolver` (no parallel paths -- hard rule 1).
- `tests/test_routes/test_accounts.py`: assert cells.

**D. Implementation approach** Cells reuse the canonical producers. No new math.

**E. Test cases** (abbreviated)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M10-1 | test_accounts_mobile_has_summary_cells | seeded accounts | GET /accounts | 3 cells | New |
| M10-2 | test_accounts_summary_matches_canonical | -- | compare | equal | New |

**F. Manual verification steps** Firefox Android: /accounts shows sticky strip; values
match grid's view of the same accounts.

**G. Downstream effects** None.

**H. Rollback notes** Remove the include and context key.

---

### Commit 11 -- Sticky payoff-status strip on /loan dashboard

**A. Commit message** `feat(mobile): sticky payoff-status strip on /loan dashboard`

**B. Problem statement** The /loan dashboard has a visually heavy summary block at the top
that takes the full viewport on mobile. Replace with the standard summary strip. Cells:
current balance, monthly payment, projected payoff date.

**C. Files modified**

- `app/templates/loan/dashboard.html`: replace the existing mobile-rendered summary block
  with the strip include. Desktop layout unchanged.
- `app/routes/loan.py`: extend context with the strip cells, all derived from
  `loan_resolver.LoanState`.
- `tests/test_routes/test_loan.py`: assert cells.

**D. Implementation approach** Cells reuse `LoanState.current_balance`,
`LoanState.monthly_payment`, `LoanState.projected_payoff_date`. The visually heavy summary
block survives on desktop; mobile branch routes through the strip.

**E. Test cases** (abbreviated)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M11-1 | test_loan_dashboard_mobile_strip | seeded loan | GET /accounts/<id>/loan as mobile | strip cells present | New |
| M11-2 | test_loan_strip_matches_loan_state | -- | compare to `loan_resolver` | equal | New |

**F. Manual verification steps** Firefox Android: /loan dashboard shows the strip; the chart
fits the viewport without horizontal scroll.

**G. Downstream effects** Commit 16 builds on the freed-up vertical space for the chart and
tab layout.

**H. Rollback notes** Restore the previous summary block.

---

### Commit 12 -- Swipe-to-act on template list and bill calendar

**A. Commit message** `feat(mobile): swipe-to-act on template list and bill calendar`

**B. Problem statement** Templates and bill calendar items also need quick actions on
mobile (archive a template, mark a bill paid, snooze, etc.). Apply the Commit 3 swipe
vocabulary so the gesture set is consistent across the app.

**C. Files modified**

- `app/templates/templates/index.html` (or wherever the template list lives -- re-grep):
  add `data-swipe-actionable` to template rows.
- `app/templates/calendar/*.html`: same for calendar event entries.
- `app/static/js/templates.js` or new `app/static/js/swipe_list.js` (decide at edit time):
  the swipe wiring. Reuses `attachSwipe` from Commit 1.
- Action sheet partials per surface (templates and calendar may have different action sets;
  re-use `_status_action_sheet.html` where applicable, new partial per surface where not).
- Tests for each surface.

**D. Implementation approach** Same vocabulary as Commit 3. Per-surface actions:
- Template list: right-swipe = mark archived (if active) or unarchive (if archived);
  left-swipe = action menu (delete, duplicate, edit).
- Calendar: right-swipe = mark paid (if pending); left-swipe = action menu (snooze, edit,
  delete instance).

**E. Test cases** (per-surface tables; abbreviated)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M12-1 | test_template_list_has_swipe_target | mobile UA | GET /templates | rows have `data-swipe-actionable` | New |
| M12-2 | test_calendar_has_swipe_target | mobile UA | GET /calendar | events have `data-swipe-actionable` | New |
| M12-3 | test_template_archive_endpoint_unchanged | -- | POST archive | regression | Existing |
| M12-4 | test_calendar_mark_paid_endpoint_unchanged | -- | POST mark paid | regression | Existing |

**F. Manual verification steps** Firefox Android: swipe on template row -- archive
indicator follows finger; release commits or snaps back. Same on calendar.

**G. Downstream effects** None.

**H. Rollback notes** Remove swipe attaches per surface.

---

### Commit 13 -- Global FAB for quick-add from any page

**A. Commit message** `feat(mobile): global FAB for quick-add from any page`

**B. Problem statement** Quick-add (Commit 4) is currently grid-scoped. The user often
wants to add a transaction from /savings, /accounts, /calendar, etc. Promote the FAB to a
global mobile-only component in `base.html` so quick-add is reachable from anywhere.

**C. Files modified**

- `app/templates/base.html`: include `_fab.html` in a `<div class="d-md-none">` wrapper
  (or equivalent server-side mobile detection). The FAB has `data-action="open-quick-add"`.
- `app/templates/grid/_mobile_grid.html`: remove the grid-scoped FAB include (resolved by
  Q-M1 -- the global FAB replaces it on the grid too, no duplicate).
- `app/static/js/quick_add.js` (new, or extracted from `mobile_grid.js`): the FAB handler
  promoted to a top-level module that any page can use. Reuses `bottom_sheet.openSheet`.
- Tests: assert the FAB renders on every mobile page and not on desktop.

**D. Implementation approach** Server-side mobile detection (the same helper that toggles
`_mobile_grid.html` vs the desktop grid). The FAB is hidden on `>=md` viewports via
Bootstrap utility classes regardless, so a misdetection still hides it on desktop.

**E. Test cases**

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M13-1 | test_global_fab_renders_on_mobile | mobile UA | GET / | FAB present | New |
| M13-2 | test_global_fab_absent_on_desktop | desktop UA | GET / | FAB absent | New |
| M13-3 | test_no_duplicate_fab_on_grid | mobile UA | GET /grid | exactly one FAB element | New |
| M13-4 | test_fab_open_quick_add | mock JS | trigger fab click | bottom sheet opens with quick-add partial | New (pattern test in tests/test_static) |

**F. Manual verification steps** Firefox Android: FAB visible on every page (grid,
/savings, /accounts, /calendar, /loan). Tap opens quick-add. Submit; transaction appears.

**G. Downstream effects** None.

**H. Rollback notes** Remove global FAB; restore grid-scoped FAB.

---

### Commit 14 -- Align companion view with v2 patterns

**A. Commit message** `feat(mobile): align companion view with v2 patterns`

**B. Problem statement** The companion view (commit `2072995`) is already mobile-first but
predates the v2 vocabulary. Align it: same FAB, same bottom sheet primitive, same design
tokens, same swipe-to-act gestures on transaction cards. No functional change beyond
visual consistency.

**C. Files modified**

- `app/templates/companion/index.html` and `_transaction_card.html`: adopt the v2 tokens
  (`--touch-target-min`, etc.) and the `attachSwipe` utility (replace any inline swipe
  code if present).
- `app/static/css/mobile_components.css`: companion-specific selectors only if needed.
- Tests: existing companion tests pass unchanged.

**D. Implementation approach** Strictly visual / structural alignment. No business logic
changes.

**E. Test cases** (regression check)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M14-1 | test_companion_existing_tests_pass | -- | run companion test suite | all green | Existing |
| M14-2 | test_companion_uses_v2_swipe_utility | -- | grep companion JS | imports `attachSwipe` from `touch_gestures` | New |

**F. Manual verification steps** Firefox Android: companion view looks visually consistent
with the rest of the mobile app; swipe gestures match.

**G. Downstream effects** None.

**H. Rollback notes** Revert template / JS edits.

---

### Commit 15 -- Mobile-first form patterns for settings/salary/calibration

**A. Commit message** `feat(mobile): mobile-first form patterns for settings/salary/calibration`

**B. Problem statement** Long, multi-section forms (settings, salary config, calibration)
are painful on mobile: the save button is at the bottom of a scroll, fields lack
appropriate input modes, advanced sections that 99% of users never touch consume vertical
space. This commit applies four patterns: sticky save bar, progressive disclosure
(collapsible advanced sections), native input types (`inputmode="decimal"` etc.), and
larger touch targets on form controls.

**C. Files modified**

- `app/templates/settings/*.html`, `app/templates/salary/*.html`,
  `app/templates/calibration/*.html`: per-file edits.
- `app/templates/partials/_sticky_save_bar.html` (new): reusable sticky save bar with
  primary save + cancel actions.
- `app/static/css/mobile_components.css`: `.sticky-save-bar`, `.progressive-section`
  styles.
- `app/static/js/forms.js` (new or extend): collapse/expand handling for progressive
  sections, sticky save bar enable/disable based on form dirty state.
- Tests: existing form tests pass unchanged.

**D. Implementation approach** Standard mobile form patterns. Sticky save bar is `position:
fixed` + `bottom: 0` + safe-area-inset-bottom. Progressive sections use `<details>` /
`<summary>` (native HTML, no JS required for basic open/close). Input types:
`inputmode="decimal"` for money, `inputmode="numeric"` for integers, `type="email"` for
email, `type="tel"` for phone-like (TOTP backup codes).

**E. Test cases** (abbreviated; mostly regression)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M15-1 | test_settings_form_has_inputmode | mobile UA | GET /settings | money fields have `inputmode="decimal"` | New |
| M15-2 | test_salary_form_has_sticky_save | mobile UA | GET /salary | sticky save bar present | New |
| M15-3 | test_calibration_form_has_progressive_sections | mobile UA | GET /calibration | advanced sections in `<details>` | New |

**F. Manual verification steps** Firefox Android: settings form is comfortable to fill;
save button always visible; advanced sections collapsed by default.

**G. Downstream effects** None.

**H. Rollback notes** Per-file revert.

---

### Commit 16 -- Mobile-first loan dashboard chart and tab layout

**A. Commit message** `feat(mobile): mobile-first loan dashboard chart and tab layout`

**B. Problem statement** The /loan dashboard's chart and tab layout were the hardest mobile
problem in v1 (commit `ede7014` made tabs scrollable; the chart still feels cramped).
With Commit 11's strip freeing the top of the viewport, this commit redesigns the chart
sizing and tab layout for mobile.

**C. Files modified**

- `app/templates/loan/dashboard.html`: mobile-specific chart sizing via CSS container
  queries (chart aspect ratio adjusts to viewport).
- `app/static/css/mobile_components.css`: chart container styles, tab visual updates.
- `app/static/js/loan_chart.js` (or wherever the chart is initialized): pass a
  device-pixel-aware width on initial render.
- Tests: regression on existing chart tests.

**D. Implementation approach** Container queries (Firefox 110+, WebKit 16+) let the chart
respond to its container's width independently of the viewport. Tabs collapse to a
horizontally scrollable pill list (v1 already did this for one tab set; extend to all).

**E. Test cases** (abbreviated)

| ID | Test name | Setup | Action | Expected | New/Mod |
|----|-----------|-------|--------|----------|---------|
| M16-1 | test_loan_chart_renders_on_mobile | mobile UA | GET /loan | chart container has aspect-ratio CSS | New |
| M16-2 | test_loan_tabs_scrollable_on_mobile | mobile UA | GET /loan | tab list has overflow-x scroll | New |
| M16-3 | test_loan_chart_data_unchanged | -- | regression | unchanged | Existing |

**F. Manual verification steps** Firefox Android: chart fits viewport; tabs scroll
horizontally without consuming vertical space.

**G. Downstream effects** None.

**H. Rollback notes** Revert template / CSS edits.

---

### Commit 17 -- Mobile v2 full gate and Firefox parity verification appendix

**A. Commit message** `chore(release): mobile v2 full gate + Firefox parity verification appendix`

**B. Problem statement** Plan-final gate: full suite green, pylint clean, manifest/icon
audit, and an appendix documenting Firefox parity verification across desktop, Android,
and iOS. The appendix is appended to this plan (`docs/implementation_plan_mobile_v2.md`)
under "Appendix A -- Verification log."

**C. Files modified**

- `docs/implementation_plan_mobile_v2.md` (this file): append Appendix A.
- `docs/project_roadmap_v5.md`: append "A.X Mobile-First v2" under Appendix A with the
  commit range.

**D. Implementation approach** Run the full gate; record results in the appendix. No code
changes.

**E. Test cases** Full suite invocation only.

**F. Manual verification steps**

1. `./scripts/test.sh` -- all 5,500+ tests green.
2. `pylint app/ --fail-on=E,F` clean.
3. `flask db upgrade` then `flask db downgrade -1` then `flask db upgrade` round-trips
   cleanly across the migrations introduced in this plan (Commit 7).
4. `python scripts/build_test_template.py` succeeds.
5. Manifest icon audit: every size listed in `manifest.json` exists in `app/static/icons/`
   and renders.
6. Firefox Desktop verification: every Phase A/C/D commit's manual verification re-run.
7. Firefox Android verification: same.
8. Firefox iOS verification: same, with documented WebKit limitations.
9. Append the verification log to this document.

**G. Downstream effects** Section 4 (Notifications) can now consume the push plumbing.

**H. Rollback notes** Doc-only; no code rollback.

---

## 10. Verification

End-to-end after Commit 5 (Phase A complete) -- the user-visible mobile-first grid is live:

1. Open the app on Firefox Android. The mobile grid shows the affordability strip at the
   top; current + next two periods' end balances are visible at a glance.
2. Right-swipe a Projected transaction. It marks Done in one gesture; the strip updates.
3. Left-swipe the same transaction. Action menu opens with valid next-statuses from the
   state machine.
4. Tap the FAB. Quick-add bottom sheet opens. Add an ad-hoc transaction (no template).
   It appears in the grid; the strip updates.
5. Swipe left/right between periods. Period navigation is single-gesture.

End-to-end after Commit 8 (Phase B complete) -- PWA infrastructure is live:

6. Install the PWA from Firefox Android via the URL bar menu. App launches from home
   screen.
7. Modify a static asset; reload; the SW update toast appears; reload again applies the
   update.
8. Enable push in /settings; trigger a test push via `flask shell`; notification appears
   on the device.

End-to-end after Commit 14 (Phase C complete) -- patterns are universal:

9. Sticky strips on /savings, /accounts, /loan dashboard; values match canonical producers.
10. Swipe-to-act on template list and calendar.
11. FAB visible and functional on every mobile page.

End-to-end after Commit 16 (Phase D complete) -- long-tail polish:

12. Settings, salary, calibration forms comfortable on mobile.
13. Loan dashboard chart and tabs fit the viewport without horizontal page scroll.

End-to-end after Commit 17 (final gate):

14. `./scripts/test.sh` full suite green.
15. `pylint app/ --fail-on=E,F` clean.
16. Firefox parity verified across Desktop, Android, iOS.

---

## Appendix A -- Verification log (filled at Commit 17)

To be appended at the close of the plan. Template:

```text
## Firefox Desktop (Gecko <version>)
- Commit 1: [pass/fail with notes]
- ...

## Firefox Android (GeckoView <version>, device <model>)
- Commit 1: [pass/fail with notes]
- ...

## Firefox iOS (WebKit <iOS version>, device <model>)
- Commit 1: [pass/fail with notes; document expected WebKit limitations]
- ...
```
