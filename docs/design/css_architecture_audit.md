# CSS Architecture Audit: app.css Split + Theme Selector Feasibility

Findings and recommendations from the 2026-06-11 survey of `app/static/css/app.css` (1,889
lines / 48 KB at audit time), run as part of the Fable 5 UI/UX overhaul
(`overhaul_plan.md`). Produced by a six-agent survey: CSS delivery, theme mechanics, settings
infrastructure, selector usage map, gates/docs assumptions, and a cascade-order audit.

Line numbers cited below are snapshots of the 1,889-line file and WILL drift; re-verify
against current code before acting (CLAUDE.md rule 2).

Status: SPLIT IMPLEMENTED 2026-06-11 (same day). Developer decisions: Scope B (multiple
palettes) is the chosen theme-selector direction, with Steel Ink the app default and the
only palette until more are built -- hence the per-palette token file
`theme-steel-ink.css`; the selector UI/persistence itself is NOT yet built. The 7-file
layout below is live in base.html; the dead selectors were deleted with the split.
Verification: a parser-level lossless check (274 rules carried over exactly, order
constraints asserted) plus 20 before/after Playwright screenshot pairs (grid, dashboard,
analytics, settings, login x desktop/mobile x dark/light), all pixel-identical.
Implementation deltas from the plan as written: `theme.css` became `theme-steel-ink.css`
(Scope B naming); `.input-rem-7` and `.chart-min-h` stayed in `utilities.css` with the
rest of the C-02 block rather than moving to `components.css` (keeps the
order-sensitive block intact); the C-02 `body` font rule and `.logo-img` moved to
`base.css` (document-level, not tie-sensitive).

## 1. Current state (verified facts)

### Delivery

- `app.css` is the only file in `app/static/css/`, loaded as the last of four stylesheets at
  `base.html:38` (after vendored `bootstrap.min.css`, `bootstrap-icons.min.css`,
  `fonts.css`). All assets are vendored; CSP is `style-src 'self'` with no inline styles, no
  nonce (`app/__init__.py`, `_CSP_DIRECTIVES`).
- `base.html` is the ONLY layout. All 46 page templates extend it (verified: auth pages,
  error pages included); every non-extending template is an underscore partial with no
  `<link>` tags. One file controls all stylesheet links.
- No cache busting of any kind (no `?v=`, no hashed filenames, no config version).
  - Bundled deploy mode: nginx serves `/static/` with `expires 7d` + `Cache-Control:
    public, immutable` - an edited stylesheet can be stale for returning browsers for up
    to 7 days.
  - Shared mode (the actual prod host): the shekel vhost has NO `/static/` location;
    everything proxies to Gunicorn and Flask serves static with no-cache + ETag
    revalidation. Always fresh, never cached at nginx. HTTP/2 is on client-to-nginx.
  - The service worker layer is already content-versioned and prefix-keyed:
    `app/routes/static_pass.py` (`_CACHED_STATIC_DIRS` includes `css`) hashes every file
    under `app/static/css/` into the cache name, and `app/static/sw.js` caches by the
    `/static/css/` URL prefix. Any number of CSS files is covered with no change.

### Gates and tests

- NO hook fires on `.css` edits. The per-edit hooks match only `app/`/`scripts/`/`tests/`
  Python, `app/templates/*.html`, `requirements.txt`, and `migrations/versions/*.py`. The
  template hook checks ref-name comparisons, `|float`, `|safe`, state-changing `hx-get`; it
  does not look at `<link>` tags or `style=` attributes.
- No pytest asserts app.css existence, the link tag, or its path. The suite verifies CSS
  EFFECTS only: computed styles (`tests/manual/verify_mobile_grid_commit14.py`), CSP headers
  (`tests/test_integration/test_security_headers.py`), and no inline `style=` in one partial
  (`tests/test_routes/test_grid.py::test_no_inline_style_attr_in_mobile_card_actions`).
  `tests/test_adversarial/test_cache_control.py` existence-locks vendor assets only -- app.css
  is not in its list.
- `tests/manual/shoot.py` (the Playwright loop) is fully split-agnostic.

### Build constraints

- No preprocessors/bundlers allowed (`docs/coding-standards.md` CSS section: "Bootstrap 5
  only. No additional frameworks, preprocessors, or CSS-in-JS"). No Node on this machine.
  No new packages without approval. Therefore: a split means multiple plain `<link>` tags.
  `@import` rejected (serializes downloads; extra Gunicorn round trips in shared mode).
  Build-time concat rejected (machinery 48 KB does not justify; no-gold-plating).

## 2. Selector usage map (the real split boundaries)

Classification of every selector family by grep over templates + JS (2026-06-11):

- **Grid-only (about half the file):** grid table core, column-size variants, month spine,
  status chips (`.txn-chip`, `.paybtn`, `.st-*`), keyboard cursor, envelope progress,
  command palette (`.cmdk-*`), section banners, summary rows, status badges
  (`.badge-done`/`.badge-credit`), `.anchor-balance-display`, transaction cell,
  `.btn-xs`, save-flash, `.period-btn-group`, quick edit, full-edit popover + bottom
  sheet, `.btn-touch-44`, mobile card view, `.modal-fullscreen-sm-down .modal-footer`
  rule, the grid rules inside all three global mobile media blocks, and the C-02
  utilities `.text-muted-shekel`, `.fs-xs`, `.flex-1-min-0`, `.mobile-section-*`,
  `.mobile-group-header`, `.bg-surface-raised`.
- **Analytics-only:** calendar (`.calendar-*` + its own 767.98px block), year-end
  (`.year-end-*`), variance (`.variance-*`), trends (`.trend-*`).
- **Dashboard-only:** `.bill-row`, `.bill-row--paid`, `.balance-display`, `.runway-badge`.
- **Single-page C-02 strays:** `.cursor-pointer` (accounts), `.input-rem-7` (retirement),
  `.chart-min-h` (debt strategy).
- **Components (2-3 screens):** `.category-group-header`, `.btn-close-sm`,
  `.shekel-scroll-pills`, `.password-strength-meter`, `.password-toggle-btn`, `.fs-mini`,
  `.fs-display`, `.progress-h-*`, `.canvas-max-h-*`, `.mw-px-*`, `.w-px-*`, `.w-pct-40`,
  `.min-w-px-60`.
- **Shared (every page via base.html):** Steel Ink token blocks + themed Bootstrap skins,
  `#theme-toggle`, `.font-mono`, body font, `.logo-img`, `.text-accent`, focus-ring rules,
  `.skip-link`, `.htmx-indicator`/`.htmx-loading` (CSP workaround for HTMX's blocked
  injected styles -- must stay on every page), `.toast-container`, `.breadcrumb`, offcanvas
  drawer rules, `.welcome-banner`, pulse-warning animation.

Note: JS does NOT track screens -- base.html loads `app.js`, `grid_edit.js`,
`command_palette.js`, `mobile_grid.js`, `progress_bar.js`, `categories.js`,
`anchor_edit.js` on every page (they no-op without their DOM). Template markup is the
usage boundary, not script loading. Page-scoped chart/auth JS does align with screens.

### Dead selectors (verified zero references; re-verify at deletion time)

| Selector group | app.css lines (snapshot) | Note |
| -------------- | ------------------------ | ---- |
| `.totals-row td` | ~690 | "kept for non-summary usage" comment, but unused |
| `.badge-received` | ~387, ~697 | grouped with `.badge-done`; never emitted |
| `.txn-edit-form` + children | ~778-786 | superseded by quick edit / full-edit popover |
| `.font-sans` | ~832 | redundant with the C-02 `body` font rule |
| `.login-wordmark` | ~874 | login now uses `<img class="logo-img">` |
| `.mark-paid-btn` | ~1473 | consistent with dashboard mark-paid being a removal candidate |
| `.chart-skeleton` + `@keyframes chart-pulse` | ~1406-1417 | whole CHART CARDS section dead |
| `.chart-controls .btn-group .btn.active` | ~1420 | ditto |

### Dynamic-construction whitelist (look dead to grep, are ALIVE -- never delete)

`.grid-wide/.grid-medium/.grid-compact` (Jinja `grid-{{ col_size }}`), `.st-done/.st-credit/
.st-projected` (`st-{{ chip_state }}`), `td.cur` (Jinja conditional), `.cell-focused`,
`.save-flash`, `td.htmx-loading` (app.js runtime), `.bottom-sheet-handle`,
`.bottom-sheet-backdrop`, `.dragging` (grid_edit.js), `.cmdk-row/.selected/.cmdk-ic
.pay/.credit/.anchor/.cmdk-main/.cmdk-meta/.cmdk-empty` (command_palette.js),
`.calendar-day--selected` (calendar.js), `.htmx-request` (HTMX runtime).

## 3. Recommended split

Seven files; `<link>` order in base.html = this order, all loaded unconditionally on every
page (48 KB total does not justify conditional loading; SW + HTTP/2 absorb the request
count).

| # | File | Contents |
| - | ---- | -------- |
| 1 | `theme-steel-ink.css` | ONLY the `[data-bs-theme="dark"]` / `[data-bs-theme="light"]` token blocks. The swappable layer: a theme is one block of variable values, one file per palette. |
| 2 | `base.css` | Themed Bootstrap component skins (navbar, card, table, modal, form, btn, list-group), body font, `.font-mono`, `.logo-img`, focus ring, skip link, HTMX indicator/loading, toast, breadcrumb, offcanvas drawer, `#theme-toggle`, pulse-warning, welcome banner. |
| 3 | `components.css` | Command palette (intended to spread app-wide per overhaul plan), password meter/toggle, `.shekel-scroll-pills`, `.btn-close-sm`, `.category-group-header`, `.input-rem-7`, `.chart-min-h`. |
| 4 | `grid.css` | Everything in the grid-only list above, including the grid rules from the global mobile media blocks (each wrapped in its own `@media`), in original source order. |
| 5 | `dashboard.css` | The dashboard-only rules. Will grow with the dashboard rebuild. |
| 6 | `analytics.css` | Calendar + its media block, year-end, variance, trends. |
| 7 | `utilities.css` | The C-02 inline-style-migration utility block. MUST BE LAST. |

Timing: do the split BEFORE the dashboard rebuild (next overhaul target) so that work lands
in `dashboard.css` instead of growing the monolith. Effort: a careful half-day, pure
mechanical move plus verification. Fold the dead-selector deletion in (with approval),
honoring the whitelist above.

### Cascade-order constraints (from the order audit; violating one changes rendering)

1. `theme.css` after `bootstrap.min.css` (it redeclares `--bs-*` at equal specificity
   against Bootstrap's own selectors) and present on EVERY page: about 50 `var(--shekel-*)`
   consumers and all `color-mix(... var(--shekel-*) ...)` uses have NO fallback; a missing
   token silently collapses the declaration. Token-block order relative to consumers does
   NOT matter (custom properties resolve at computed-value time); presence and the
   after-Bootstrap position are the only requirements.
2. `utilities.css` last. The C-02 single-class utilities replaced 92 inline `style=`
   attributes and now win equal-specificity ties only by source order.
3. Media-query rules live in the same file as their base rules, AFTER them. The three
   global mobile blocks (767.98 / 575.98 / 359.98) interleave grid and shared rules today
   and must be broken apart by owner; the descending breakpoint order must be preserved for
   the chains that redeclare the same selectors (`.sticky-col`, `.row-label-col`,
   `.grid-table`, `.row-label`, `.anchor-balance-display`, `.period-btn-group .btn`).
4. Grid internals keep source order within `grid.css`. Most fragile pair: the
   `.grid-table th, td { border: 0; ... }` reset (~line 208) MUST precede the
   subtotal / net-cash-flow / balance-row / totals `border-top` rules (~630-692), or every
   summary divider vanishes (equal (0,1,1) specificity). Also `td.cur` (~347) before
   `td.cell-focused` background (~444); the mobile cell-padding rule (~1122) ties at
   (0,1,1) with month-band/section-banner/group-header/spacer paddings from sections 2b/3.
5. Within the theme skins: `[data-bs-theme="dark"] .table` before `.table-dark` (both
   (0,2,0), markup carries both classes).
6. The 16px mobile font-size on `.txn-full-edit-popover` form controls exists to prevent
   iOS Safari zoom-on-focus; it wins only by coming after the 13px base rule.
7. `.btn-touch-44` and `.popover-action-footer` are defined ONLY inside the 767.98 block
   (no desktop counterpart) by design -- do not hoist them out.
8. Existing `!important` declarations (28 at audit time, all pre-dating the no-new-
   `!important` rule) are order-immune anchors. The cascade audit flagged
   ~lines 644/661 as redundant and ~247-251 as dead-shadowed -- candidates for a later
   hygiene pass, not this split.

The two `.grid-table td.cell-focused` declarations (~444 background, ~745 outline) have
zero property overlap and may be merged or kept apart freely.

### Files/docs that must change with the split

- `app/templates/base.html:38` -- the single load reference becomes seven links.
- Normative path statements: `docs/coding-standards.md:255`, `.claude/rules/coding.md:62`,
  `docs/design/fable5-design-language.md:59,70`, `.claude/skills/shekel-design/SKILL.md:38,53`,
  `docs/design/visual_loop.md:53`.
- Stale prose (assertions unaffected): the docstring of
  `test_no_inline_style_attr_in_mobile_card_actions` (tests/test_routes/test_grid.py) and
  the line-number citations in `tests/manual/verify_mobile_grid_commit14.py`.
- `docs/phase_8d1_implementation_plan.md` deploy smoke probes `curl .../static/css/app.css`
  -- update the recipe (preferred) or keep a file named app.css.
- Historical plan/audit docs cite app.css extensively; leave them (historical reference).

### Verification plan for the split

Byte-identical rendering is the acceptance bar: `tests/manual/shoot.py` screenshots of the
key screens (grid desktop + mobile, dashboard, analytics tabs, settings, login) in BOTH
themes, before vs after; then the full suite. No financial logic is touched.

## 4. Theme selector feasibility

### Current mechanics (audited 2026-06-11)

- `<html lang="en" data-bs-theme="dark">` hardcoded at `base.html:2`. Server knows nothing
  about themes (zero "theme" hits in app/ Python).
- `app.js` (~lines 24-54, IIFE at end of body): reads localStorage key `shekel-theme`
  (values `dark`/`light`, applied unvalidated), re-applies on every load; click handler on
  `#theme-toggle` (authed navbar only, base.html:147-150) flips the attribute, stores, and
  dispatches `shekel:theme-changed`.
- Consequences: preference is per-browser not per-account; light-mode users get a dark
  first paint every page load (app.js runs after the vendor scripts at end of body; CSP
  forbids the usual inline head script); `prefers-color-scheme` is never consulted;
  `<meta name="theme-color">` (base.html:28) is hardcoded and never tracks the theme;
  logged-out pages apply a saved preference but have no toggle.
- Charts: `chart_theme.js` re-renders tracked charts on the toggle event;
  `getThemeColors()` reads `--shekel-text-primary/-secondary/--shekel-border-subtle` via
  getComputedStyle with hardcoded hex fallbacks; the 8-color chart palette and grid colors
  are hardcoded per-theme hex pairs.

### Scope A -- persist light/dark per account in user settings: SMALL (one focused session)

1. Column + migration on `auth.user_settings` (model `UserSettings`,
   `app/models/user.py:184-285`; lazy-created one-to-one with users). Design fork for the
   developer (CLAUDE.md rule 8):
   - **Ref table (recommended, convention-conformant):** `ref.themes` + `ThemeEnum` in
     `app/enums.py` + ref_cache accessor + `user_settings.theme_id` Integer FK,
     `server_default` = Steel Ink dark id. Exact shape of `users.role_id`; matches the
     deep-hunt #38 normalization that just PROMOTED string-CHECK columns to ref FKs.
   - String column + named CHECK (the `SecurityEventKind` precedent): cheaper, but the
     pattern #38 spent a migration eliminating.
   Migration is additive (static server_default backfills, like aeb04f13caff); test both
   directions; rebuild the test template (`python scripts/build_test_template.py`).
2. Server-render `data-bs-theme` from `current_user.settings` (context processor). Kills
   the FOUC for logged-in users; preference roams across devices.
3. Settings UI: one `render_select` in `settings/_general.html`, one field in
   `UserSettingsSchema` (`app/schemas/validation/settings.py`), one branch in
   `settings.update` (`app/routes/settings.py:114-153`; FK validation mirrors
   `default_grid_account_id`). There is no settings service; route -> schema -> model is
   the established flow.
4. Reconcile the navbar toggle: it must persist its flip (small HTMX POST) so toggle and
   setting never disagree. localStorage shrinks to a pre-login echo for the login page, or
   is dropped (login then always defaults dark).
5. Optional later: a "system" value (follow OS) needs `prefers-color-scheme` JS because the
   server cannot know the OS setting; it reintroduces a small flash for that option only.
   Ship dark/light first.

### Scope B -- multiple palettes beyond Steel Ink: MODERATE + a product gate

Mechanically ready once `theme.css` exists: add a `data-shekel-theme` attribute dimension;
each palette = ~30 token values per mode (~60 lines). Real costs:

- WCAG AA contrast verification per palette (the design language treats token values as the
  contract); money-state colors must keep their semantics on every palette.
- One-time `chart_theme.js` refactor: hardcoded 8-color palette, grid colors, and fallbacks
  do not track new palettes -- everything must read from CSS custom properties.
- Tokenize the strays: `#2A3040` (categories header dark), hardcoded `meta theme-color`,
  scrim/shadow rgba literals, `#fff` button text.
- Every new palette multiplies the "verify in both themes" burden of each screen rebuild.
- PRODUCT TENSION: the design language locked "Steel Ink, app-wide" (2026-06-11) with the
  accent as the only non-money chroma. A palette selector reopens that decision; if taken,
  Steel Ink stays the first-class default and alternates are additive token sets held to
  the same constraints.

Recommendation: build Scope A now; structure the split so Scope B stays cheap; take Scope B
only if alternate palettes are genuinely wanted.

## 5. Findings surfaced by the survey -- ALL FIXED 2026-06-12 (except as noted)

1. **FIXED -- chart function options silently stripped.** Root cause: `mergeThemeDefaults`
   cloned configs with `JSON.parse(JSON.stringify(...))`, dropping every function-valued
   option (tooltip/tick callbacks) before `new Chart()`. Fix: `ShekelChart.create()` now
   takes a config FACTORY (throws TypeError otherwise); the factory's fresh output is
   merged directly with no clone, so functions survive. All six consumer files
   (chart_variance, chart_year_end, payoff_chart, growth_chart, retirement_gap_chart,
   debt_strategy) converted to factory closures. Verified in real Chromium against the
   real files: tooltip label/afterBody and tick callbacks reach the live Chart instance
   and compute correct output.
2. **FIXED -- stale chart dataset colors on theme toggle.** Same root cause and fix:
   colors resolve via `ShekelChart.getColor()` INSIDE the factory, and `rerenderAll()`
   re-invokes the factory on `shekel:theme-changed`, so dataset colors re-resolve.
   Verified: variance chart datasets flip dark->light palette variants on toggle while
   callbacks survive the re-render.
3. **FIXED -- cache-busting gap.** `_register_static_versioning` (app/__init__.py)
   appends a per-file content hash `v=` to every `url_for('static', ...)` URL via
   `static_file_version` (app/routes/static_pass.py, sha256 prefix memoized by mtime,
   safe_join-guarded). URLs are now content-addressed, making the bundled deploy's
   `expires 7d + immutable` correct; in shared mode (actual prod, Flask serves static)
   the after-request hook grants `public, max-age=31536000, immutable` ONLY when the
   `v=` matches the file's current hash (forged/stale values keep no-cache + ETag).
   The wrong "nginx serves /static in production" comment in app/__init__.py and the
   matching test docstring were corrected. The service worker composes cleanly (it
   caches by full URL and its cache name is already content-versioned). Tests:
   TestStaticFileVersion (test_static_pass.py) + 4 cache-header tests
   (test_cache_control.py). RESIDUE (accepted): icon paths inside
   app/static/manifest.json are plain strings the hook cannot version; a changed icon
   can stay stale up to 7 days for non-SW clients in bundled mode.
4. Minor items: `settings/settings.html` orphan DELETED 2026-06-12; the `.cell-focused`
   comment fixed with the split; `meta theme-color` not tracking the theme remains
   DEFERRED to the Scope B selector work (the real fix is server-rendered theme
   awareness); dead CSS deleted with the split (section 2).

## 6. Decision gates -- resolved 2026-06-11

1. 7-file split + dead-selector deletion: APPROVED and implemented (see Status).
2. Theme selector: developer chose SCOPE B (multiple palettes). Steel Ink is the app
   default and sole palette until more are built; the per-user selector (settings UI,
   persistence, `data-shekel-theme` dimension, chart_theme.js token refactor, stray-color
   tokenization -- see section 4 Scope B) is future work, unblocked by the split.
3. Findings 1-3 in section 5: FIXED 2026-06-12 (chart factory API + static URL
   versioning; details and verification in section 5). All app.css path references,
   including the test_grid.py docstring and the grid_edit.js comment, were updated with
   the split. Remaining from this audit: only the section-5 residue notes (manifest icon
   paths; meta theme-color deferred to Scope B).
