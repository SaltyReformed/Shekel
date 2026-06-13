# Mobile-First v3 Follow-up -- Implementation Plan

- Version: 1.0
- Date: 2026-05-25
- Author: prepared for the solo developer (SaltyReformed)
- Source: `docs/mobile_follow_up.md` (entries F-1 through F-10, each
  surfaced during the mobile-first v3 plan but deferred out of scope of
  the originating commit).
- Prerequisite reading: `docs/mobile_follow_up.md` (the 10 deferred
  entries this plan addresses), `docs/implementation_plan_mobile_v3.md`
  (the parent plan; Section 11 records the v3 final state),
  `CLAUDE.md` (Rules, Transfer Invariants), `docs/coding-standards.md`,
  `docs/testing-standards.md`,
  `docs/audits/financial_calculations/remediation_follow_up_common.md`
  (common rules + work-summary format), the live state of every file
  cited in Section 6 in full before any code is edited.
- Standards: every commit follows CLAUDE.md and
  `docs/coding-standards.md`; every test pass follows
  `docs/testing-standards.md`. Test IDs use the form `C<commit>-<n>`.
- Anchor: line numbers below were verified 2026-05-25 against `dev`
  HEAD `dfdb941` (the v3 close-out commit). They drift the moment any
  commit lands; every commit's verification step re-greps the cited
  symbols first per Section 1 rule 1.

---

## 0. Context

The mobile-first v3 plan closed on 2026-05-25 with Section 11's verification
appendix (commit `dfdb941`). Ten follow-up entries (`F-1` through `F-10`
in `docs/mobile_follow_up.md`) were deferred out of scope of their
originating commits because each would have either (a) extended the
parent commit's diff into territory the commit was not scoped to
touch, or (b) was a pre-existing issue that the parent commit only
surfaced rather than introduced.

This plan picks up exactly those ten items. Nothing else. There is no
new user-facing feature here -- the work is touch-target polish, CSP
hygiene, dead-code retirement, a behavioural bug fix, a refactor that
the v3 plan acknowledged it could not complete in scope, and a
dev/prod-parity infrastructure commit. The mobile UX delivered by v3
remains the contract; this plan only tightens the implementation
underneath.

### Why these matter

- **Touch-target gaps (F-3, F-7).** The v3 plan's hard rule 7 mandates
  >= 44 x 44 CSS px touch targets per WCAG 2.5.5 AA + Apple HIG. Three
  CSP-inert inline styles in `_mobile_card_actions.html` and the
  missing min-height floor on `.shekel-scroll-pills .nav-link` mean
  three mobile button surfaces (Mark Paid / Edit Amount / Open Full
  on every grid card; the settings / analytics / loan dashboard tab
  pills) currently render at Bootstrap's default 40 px and slip past
  the floor.
- **Behavioural bug (F-1).** The horizontal-swipe listener that drives
  Plan-tab period navigation is bound to `#mobile-grid` (the outer
  container that wraps both tabs) rather than `#mobile-plan`. A swipe
  on the This Period tab silently advances the Plan tab's
  `currentIndex`; the bug is invisible until the user switches to Plan
  and finds the wrong period selected.
- **Orphan code (F-4, F-9).** `companion/_transaction_card.html`
  (94 lines, zero callers) and the `dashboard.mark_paid` route + 7
  `TestMarkPaid` tests + 4 cross-reference comments are dead weight
  that a future author could mistake for live code. Q-1 of the v3
  plan resolved as REMOVE; only the template-side delete shipped.
- **Pylint quality drift (F-5, F-6).** `app/routes/grid.py:18-20`
  trips C0411 + C0412 (third-party / first-party import order);
  `app/routes/grid.py::index` (47-289) trips R0914 (33/15 locals) +
  R0912 (15/12 branches) + R0915 (54/50 statements). The v3 commit 13
  extraction reduced the function but did not clear the thresholds.
- **CLAUDE.md "Reference Tables" violation (F-8).** The
  `recurrence_cell` macro in `templates/list.html:21-50` compares
  against eight `pattern.name` strings, which violates the
  "IDs for logic, strings for display only" rule (CLAUDE.md
  Architecture / coding-standards.md Reference Tables /
  `[[feedback_id_based_lookups]]`).
- **Verification artefact (F-10).** The v3 plan's Section 10 item 7
  asks for a DevTools `shekel-static-v1` cache audit. Commit 28
  elected to defer the capture because the SW cache invariant is
  enforced statically by `app/static/sw.js`'s `STATIC_PREFIXES`
  allow-list + cache-first guard. The audit remains the documented
  regression check.
- **Dev/prod-parity (F-2).** The dev Flask server runs on the bare
  host (`flask run --host 172.32.0.1`); prod runs in a hardened
  Docker container behind Gunicorn behind nginx. Three classes of
  drift (process-model, authorisation, network-trust-boundary) make
  certain prod-only failure modes invisible in dev.

### Consequence of getting this wrong

The same two failure modes the v3 plan named (Section 0) still apply:
canonical-producer bypass and SW-cached financial data. None of the
ten findings introduce new monetary computations or new SW cache
entries, but the F-6 grid.py decomposition touches the route that
sits between the user and the canonical producers
(`balance_resolver`, `loan_resolver`); the decomposition must preserve
the producer wiring exactly. The three static-guard locks shipped by
the financial-calculation audit (commit `842d415`, F-6 of that audit
-- the locks live as test methods on `TestGridPeriodSubtotalCanonical`
and `TestCheckingDetailCanonicalProducer`, not as a standalone
`tests/test_static_guards.py` file as the v3 plan's Sections 6.8 / 10
item 6 mistakenly state) are the gate for that preservation.

---

## 1. Hard rules for executing this plan

These bind every commit. They restate CLAUDE.md, the coding
standards, the testing standards, and the v3 plan's Section 1 in the
context of this work.

1. **Read the entire file before editing it.** Line numbers below are
   verified 2026-05-25 against `dev` HEAD `dfdb941`. They drift the
   moment any commit lands. Every commit's verification step re-greps
   the cited symbols before any edit. Never edit by remembered line
   number.
2. **Canonical producers only for monetary values.** No partial,
   route, or JS file reads `Account.current_anchor_balance`,
   `Account.current_anchor_period_id`, `LoanParams.current_principal`,
   or `LoanParams.interest_rate` directly. The three static guards
   (named in Section 0 above; not a standalone
   `tests/test_static_guards.py` file -- the v3 plan's path is stale)
   stay green by construction; F-6's decomposition must preserve
   `balance_resolver.balances_for` as the producer and must NOT
   re-introduce `balance_calculator.calculate_balances(` in
   `app/routes/grid.py`.
3. **Decimal-only on the server. JS is display-only for money.**
   None of the ten findings introduce new monetary arithmetic.
4. **No new financial logic.** All math, status transitions, balance
   projections stay in `app/services/`. F-6 moves orchestration code
   from `grid.py::index` into private `_helpers` in the same module
   without changing any branch condition or producer call.
5. **HTMX patterns.** 200 for swap, 422 for validation, 204 for
   no-content. F-9's route deletion drops the `mark_paid` POST
   endpoint entirely; no new routes added.
6. **CSRF.** Mobile forms use `{{ csrf_token() }}` or rely on the
   existing `htmx:configRequest` handler in `app/static/js/app.js`.
   F-3's class-swap preserves the existing CSRF wiring on the
   Mark Paid form.
7. **Touch targets >= 44 x 44 CSS px** (WCAG 2.5.5 AA, Apple HIG).
   F-3 + F-7 close two gaps where this floor was not enforced. New
   components do NOT bypass it.
8. **`inputmode="decimal"` on every monetary input.** Already swept
   by v3 commit 11; none of the ten findings add a new monetary
   input.
9. **Firefox parity is a hard requirement.** Every commit's
   verification step runs on Firefox Desktop (Gecko at 1920x1080 and
   responsive design mode at 375x812 + 430x932). F-1 (swipe-handler
   scope) must verify on real iPhone XS / 16 Plus in Firefox iOS
   (WebKit) because the gesture passive-listener semantics differ.
10. **No JS framework. No new CSS framework.** HTMX + vanilla JS +
    Bootstrap 5 only. F-3's class addition lives in
    `app/static/css/app.css` inside the existing
    `@media (max-width: 767.98px)` block; no new media queries.
11. **Atomic commits, suite green after each.** Targeted tests
    (`./scripts/test.sh tests/path/test_file.py -v`) during edits;
    the full suite (`./scripts/test.sh`, ~65 s at the `pytest.ini`
    `-n 12` default) as the final per-commit gate. `pylint app/
    --fail-on=E,F` clean after every commit, no new warnings -- and
    F-5 and F-6 EXPLICITLY remove existing warnings without
    regressing the score.
12. **Stay in scope.** Out-of-scope issues spotted during
    verification go to `J. OUT OF SCOPE -- flagged, not fixed` in
    the work summary with `file:line` + reason. If the issue is in a
    file this plan touches in a later commit, fold it in there;
    otherwise leave it and append a new `F-N` entry to
    `docs/mobile_follow_up.md`.
13. **Do not push.** After green, present the work summary and ASK
    whether to commit and push to `dev` (this triggers CI;
    PR-to-`main` is the promotion path per CLAUDE.md "Git
    Workflow").
14. **Style.** No Unicode em/en dashes (use `--` or `-`). Pythonic,
    type-hinted, substantive docstrings, specific exceptions, no
    broad `except Exception`. All Jinja files end at LF, no
    trailing whitespace.
15. **Work-summary format.** Every session ends with the
    `remediation_follow_up_common.md` A-M labels verbatim. G is
    "n/a" for every commit in this plan (no migrations).

---

## 2. Design decisions (made at plan time; confirm at review)

These resolve the small product / engineering choices the
implementation cannot make on its own. Each is explicitly flagged so
a reviewer can redirect before the commit lands.

- **D-A. F-3 introduces a `btn-touch-44` utility class.** The three
  inline `style="min-height: 44px;"` attributes in
  `_mobile_card_actions.html:66, :75, :85` become
  `class="... btn-touch-44 ..."`. The class is added to
  `app/static/css/app.css` inside the existing
  `@media (max-width: 767.98px)` block, mirroring the convention used
  by `.mw-px-*` / `.w-px-*` / `.fs-*` utility classes already
  established by the C-02 inline-style migration. **Not** scoped to a
  selector like `.mobile-card-action-bar .btn`: a stand-alone utility
  class is reusable for the next button that needs the floor.

- **D-B. F-7 widens the existing mobile media query.** The new
  `.shekel-scroll-pills .nav-link` rule lands inside the
  `@media (max-width: 767.98px)` block at `app/static/css/app.css`
  (the same block F-3 adds to). The desktop pill row stays at
  Bootstrap default heights (where touch is not the primary input);
  the floor applies only at `<= 767.98 px`.

- **D-C. F-1 binds the swipe listener to `#mobile-plan`.** The
  alternative -- scope by `event.target.closest('#mobile-plan')`
  inside the existing `#mobile-grid` handler -- adds branch logic to
  a handler whose only job is the gesture. The binding-site change
  is the lower-friction fix.

- **D-D. F-4's deletion is `git rm`.** No deprecation shim, no
  leave-it-on-disk-for-history. The companion view at
  `app/templates/companion/index.html` has been on the shared
  `_mobile_this_period.html` since v3 commit 13 (2026-05-24); one
  release cycle has elapsed. Git history preserves the file's
  contents if anyone ever needs to compare.

- **D-E. F-9's deletion does NOT cascade beyond the named cross-refs.**
  Four comment-only back-references (in `transfer_service.py:507`,
  `transfers.py:1101`, `validation.py:394`, `test_transfers.py:905`)
  use `dashboard.mark_paid` as a *comparison point* with
  `transactions.mark_done`. The rewrite re-anchors them on
  `transactions.mark_done` directly; the comparison's intent
  (documenting two routes that share a settlement contract) survives
  with the surviving route's name. The state-machine sweep at
  `tests/test_routes/test_c21_state_machine_broad_rollout.py:6` is a
  module docstring; the route name is removed without renaming the
  file.

- **D-F. F-8 introduces ONE injected context variable, not eight.**
  Rather than thread eight individual `RECURRENCE_PATTERN_*_ID`
  constants into every template render, the route layer injects a
  single dict `recurrence_pattern_ids = {RecurrencePatternEnum.<MEMBER>:
  ref_cache.recurrence_pattern_id(...)}` (already keyed by enum
  member). The macro reads
  `rr.pattern_id == recurrence_pattern_ids[RecurrencePatternEnum.EVERY_PERIOD]`.
  This is the established convention from
  `app/__init__.py:285` (`recurrence_pattern_labels` is already
  injected the same way) -- the dict is the single source of truth,
  the macro is the single consumer.

- **D-G. F-6 keeps the helpers PRIVATE to `app/routes/grid.py`.**
  The five extracted functions are `_resolve_grid_context`,
  `_load_grid_transactions`, `_build_grid_balances`,
  `_build_grid_subtotals`, `_build_grid_row_data`. They live in
  `grid.py` (not in `app/services/`) because they are
  view-orchestration helpers that bind request-args to producer
  calls. Pushing them into a service would either drag
  request-context into the service (violates the
  "Services are isolated from Flask" architecture rule) or invent a
  DTO layer the helpers do not need. Pylint quietens; the service
  layer stays clean.

- **D-H. F-10 is a manual capture, not an automated test.** The SW
  cache invariant is enforced statically by `app/static/sw.js`'s
  `STATIC_PREFIXES` allow-list (lines 21-28) + `if (!isStatic)
  return;` cache-first guard (line 73). A Playwright test that mocks
  a service-worker registration would test the test-double, not the
  real cache; the DevTools capture is the only direct evidence path.
  Screenshot lands under `tests/manual/screenshots/`.

- **D-I. F-2 is a separate plan-candidate.** Half-day scope,
  zero overlap with the application code path, dev/prod-parity
  rather than user-visible product. The plan lists it as Commit 10
  for completeness; the user may elect to promote it to its own
  PR-to-`main` cycle or defer it as the prior `F-2` entry already
  permits. Commit 10's implementation step (Section 9) explicitly
  asks before authoring.

---

## 3. Discovered refinements beyond the F-N text (folded into scope)

Live-code verification of every F-N entry on 2026-05-25 against `dev`
HEAD `dfdb941` confirmed every finding's premise and surfaced ten
small corrections. These are folded into the relevant commits, not
left as plan-vs-code gaps.

- **R-1 (F-1 line drift).** The finding cites
  `app/static/js/mobile_grid.js:42-60` for the
  `#mobile-grid`-bound touchstart / touchend listeners. Actual is
  lines 70-88 in the current file (the `init()` function expanded
  during v3 commits 6-10 with the activate-tab-from-hash branch and
  the per-tab panel scan). Bug shape unchanged: `var grid =
  document.getElementById('mobile-grid');` is still the binding
  site, the handler still calls `navigate(delta)` which still
  mutates Plan-tab state, the swipe still fires regardless of which
  tab is visible.

- **R-2 (F-7 line drift).** The finding cites
  `app/static/css/app.css:954-956` for the `.shekel-scroll-pills
  .nav-link` rule. Actual is lines 961-975 (the comment header
  spans 955-957 plus the `.shekel-scroll-pills` parent rule at
  961-967). The `.shekel-scroll-pills .nav-link` rule at line 973
  sets `white-space: nowrap` only -- no min-height floor.

- **R-3 (F-3 verified exactly).** The three inline
  `style="min-height: 44px;"` attributes are at
  `app/templates/grid/_mobile_card_actions.html:66, :75, :85` -- the
  finding's line citations match. CSP `style-src 'self'` confirmed
  at `app/__init__.py:725` with the comment "Inline `style="..."`
  attributes are blocked." The styles are inert today; the buttons
  render at Bootstrap's default 38-40 px button height (small `btn-sm`
  with default padding).

- **R-4 (F-5 verified exactly).** Pylint output against
  `dev` HEAD `dfdb941` reports both warnings:

  ```
  app/routes/grid.py:18:0: C0411: third party import "sqlalchemy.orm.selectinload"
    should be placed before first party import "app.utils.auth_helpers.require_owner"
    (wrong-import-order)
  app/routes/grid.py:20:0: C0412: Imports from package app are not grouped
    (ungrouped-imports)
  ```

  Source confirmed at lines 17-20: the `from app.utils.auth_helpers`
  line at `:17` sits between `flask_login` (`:15`) and the
  third-party `from sqlalchemy.orm` line at `:18`, then
  `from app.extensions` resumes at `:20`.

- **R-5 (F-6 verified exactly).** Pylint output reports the three
  R-warnings the finding cites:

  ```
  app/routes/grid.py:47:0: R0914: Too many local variables (33/15)
  app/routes/grid.py:47:0: R0912: Too many branches (15/12)
  app/routes/grid.py:47:0: R0915: Too many statements (54/50)
  ```

  `index()` spans lines 47 to 289 (next `def` at line 294). The
  function is post-v3-commit-13 (the
  `grid_view_service.RowKey` import at `:28` and the
  `build_entry_sums_dict` import at `:27` are the v3 commit 13
  extraction's leave-behind).

- **R-6 (F-9 verified across all 11 citations).** Confirmed by
  direct grep:
  - Route + `_MARK_PAID_SCHEMA` singleton at
    `app/routes/dashboard.py:33, :54, :57`.
  - Schema at `app/schemas/validation.py:391-394` (the
    `MarkPaidSchema` class).
  - `TestMarkPaid` class at `tests/test_routes/test_dashboard.py:218`
    with 7 tests (`test_mark_paid`, `test_mark_paid_with_actual`,
    `test_mark_paid_returns_paid_row`,
    `test_mark_paid_htmx_trigger`, `test_mark_paid_sets_paid_at`,
    `test_mark_paid_wrong_user`,
    `test_mark_paid_requires_auth`). Count matches the finding.
  - `test_mark_paid_tracked_returns_paid_row_without_progress` at
    `tests/test_routes/test_dashboard_entries.py:540` confirmed
    (singleton; no siblings).
  - C27 sweep entries at
    `tests/test_routes/test_c27_input_validation_sweep.py:16,
    :331, :335` confirmed.
  - C21 module docstring at
    `tests/test_routes/test_c21_state_machine_broad_rollout.py:6`
    confirmed.
  - Four comment cross-refs at `transfer_service.py:507`,
    `transfers.py:1101`, `test_transfers.py:905`,
    `validation.py:394` confirmed.
  - Vacuous assertion at `tests/test_routes/test_dashboard.py:194`
    confirmed: `assert "Already Paid" not in html or
    "mark-paid-btn" not in html`. With the button removed, the
    right operand is always true; the OR self-satisfies.
  - Orphan comment at `app/templates/dashboard/_bill_row.html:53`
    confirmed: "The `dashboard.mark_paid` route and its tests remain
    (orphaned ...".

- **R-7 (F-4 verified by grep).** `app/templates/companion/_transaction_card.html`
  exists at 94 lines. `grep -rn "_transaction_card" app/ tests/`
  returns two matches, both comment-only:
  `app/templates/grid/_grid_row_macros.html:189` (a docstring
  reference describing the legacy companion card design) and
  `tests/test_routes/test_companion_routes.py:763` (a comment
  explaining the test moved off the legacy markup). Neither is a
  caller; the file is truly orphaned.

- **R-8 (F-8 verified exactly + infrastructure ready).** Macro at
  `app/templates/templates/list.html:21-50` matches the finding.
  Required infrastructure already present:
  - `RecurrencePatternEnum` at `app/enums.py:115`.
  - `recurrence_pattern_id(member)` resolver at
    `app/ref_cache.py:511-523`.
  - `_recurrence_pattern_map` cache at `app/ref_cache.py:50`
    (member -> int).
  - Two existing callers in
    `app/routes/templates.py:652, :692` use the resolver in the
    pattern this plan's commit will mirror.
  - `recurrence_pattern_labels` (the existing string-label dict)
    injected at `app/__init__.py:285` as a Jinja-context global.

  The `else`-branch fallback in the macro already routes through
  `recurrence_pattern_labels.get(pname, ...)`, so the entire elif
  chain *can* collapse to a single dict lookup once the comparisons
  drive off `pattern_id`. F-8's recommended fix preserves this.

- **R-9 (F-10 verified -- SW invariant enforced by construction).**
  `app/static/sw.js` source confirmed:
  - `STATIC_PREFIXES` allow-list at lines 21-28: `/static/vendor/`,
    `/static/css/`, `/static/js/`, `/static/img/`, `/static/fonts/`,
    `/static/manifest.json`.
  - Cache-first guard at line 73: `if (!isStatic) return;`
    -- no `respondWith` for non-static URLs.
  - `if (response.ok) cache.put(...)` at line 83 -- only successful
    static responses persist.

  The DevTools capture is a regression check, not a discovery
  exercise. The audit's value is documenting that the invariant
  holds at runtime in addition to the source.

- **R-10 (F-2 verified -- no `docker-compose.dev.yml`).** `deploy/`
  contains `docker-compose.prod.yml`, `nginx-bundled/`,
  `nginx-shared/`, `postgres/`, `README.md`. No
  `docker-compose.dev.yml` exists. No `.flaskenv` file in repo root.
  The dev `flask run --host 172.32.0.1` workflow is operational
  state, not tracked config. The finding's framing of
  "dev runs on bare host" is accurate.

---

## 4. Pattern -> canonical implementation map (the spine of this plan)

Every multi-path pattern collapses onto one component. This table is
the contract the commits implement.

| Pattern | Canonical implementation | First introduced | Reused by |
|---|---|---|---|
| Touch-target floor utility | `.btn-touch-44` class in `app/static/css/app.css` mobile media block | Commit 1 (F-3) | future commits that need a stand-alone 44 px button floor |
| Scroll-pill touch-target floor | `.shekel-scroll-pills .nav-link { min-height: 44px; ... }` inside `@media (max-width: 767.98px)` | Commit 2 (F-7) | analytics tabs (`analytics/analytics.html:16`), loan dashboard tabs (`loan/dashboard.html:21, :323`), settings sidebar (`settings/dashboard.html:22`) |
| Swipe-handler scope | `document.getElementById('mobile-plan')` (not `'mobile-grid'`) | Commit 4 (F-1) | the period-nav gesture; the per-card swipe-action uses `swipe.js::attachSwipeAction` already |
| Recurrence-pattern lookup | `recurrence_pattern_ids[RecurrencePatternEnum.<MEMBER>]` injected from `app/__init__.py` Jinja-context globals | Commit 7 (F-8) | `templates/list.html::recurrence_cell` macro; any future template needing pattern IDs |
| Grid-route orchestration helpers | five private `_helpers` in `app/routes/grid.py` (`_resolve_grid_context`, `_load_grid_transactions`, `_build_grid_balances`, `_build_grid_subtotals`, `_build_grid_row_data`) | Commit 8 (F-6) | `grid_bp::index` only; not exported |

---

## 5. Optional enhancements (listed; not in the default commit set unless promoted)

Each is independently valuable, low-risk, and called out so the
developer can opt in. The plan flags which commit would carry each if
promoted.

- **OPT-F1. Collapse the recurrence_cell elif chain entirely.**
  After F-8 drives comparisons off `pattern_id`, the entire elif
  chain in the macro becomes a candidate for collapse into one
  `{{ recurrence_pattern_labels[rr.pattern_id] }}` lookup -- the
  `else`-branch fallback already routes through this dict. Folded
  as a Commit 7 extension if promoted. Trade-off: the `Monthly (day
  N)` / `Quarterly (starting MMM)` / `Yearly (MMM D)` branches
  produce dynamic labels that the static `recurrence_pattern_labels`
  dict cannot express; collapsing would lose these. Recommend
  promoting only after auditing the label-quality difference.

- **OPT-F2. Static lock that prevents new direct anchor reads in
  routes / templates.** A grep-style guard test that scans `app/`
  for `current_anchor_balance`, `current_anchor_period_id`,
  `current_principal`, `interest_rate` outside the services tree
  would lock the v3 plan's Section 1 rule 2 and the
  Commit 28 work-summary observation that no new reads were
  introduced. Models the existing `TestGridPeriodSubtotalCanonical`
  + `TestCheckingDetailCanonicalProducer` static guards. Folded as
  a standalone commit between Commits 8 and 9 if promoted. Listed
  but not in the default set because the existing two locks already
  cover the highest-traffic paths.

- **OPT-F3. Render `_bill_row.html` orphan-comment cleanup.** After
  F-9 retires the route, the orphan comment at
  `_bill_row.html:53-57` can be deleted. Trivial; folds into
  Commit 6 as a one-line follow-up.

- **OPT-F4. Migrate `app/templates/companion/_transaction_card.html`
  to git history only.** Already the F-4 plan; OPT-F4 is the
  inverse "keep on disk but mark deprecated" option, listed only
  to document the trade-off. Rejected via D-D.

- **OPT-F5. `_helpers` module split.** F-6 keeps the five helpers
  private to `app/routes/grid.py`. The alternative is a new
  `app/routes/grid_helpers.py` module. Considered during planning;
  rejected via D-G (a separate module would invite future code to
  push view-orchestration into the service layer or to import
  helpers from outside the grid route, both of which the private
  underscore convention guards against).

- **OPT-F6. Single-source-of-truth `recurrence_pattern_ids` constant
  module.** Inject the dict from a single `app/constants.py` rather
  than computing it in the route. Trade-off: would shadow
  `ref_cache.recurrence_pattern_id` -- the cache is per-process and
  built at app-init; a constants module would either duplicate the
  build logic or be the build site itself. Reject: the
  ref_cache-as-build-site convention from v3 / E-15 is the
  established pattern; do not split it.

---

## 6. Codebase inventory (files this plan touches)

Re-grep each path at edit time; line numbers below are verified
2026-05-25 against `dev` HEAD `dfdb941` and will drift the moment a
commit lands.

### 6.1 New files

- `tests/manual/screenshots/commit28_sw_cache_audit.png` (or
  `commit9_followup_sw_cache_audit.png` depending on Commit 9
  numbering) -- Commit 9. DevTools panel screenshot, manual capture.

### 6.2 Modified routes

- `app/routes/grid.py` (lines 1-`tail`, currently 294+). Commit 3
  (F-5) reorders imports at lines 17-29; Commit 8 (F-6) decomposes
  `index()` (lines 47-289) into the thin orchestrator plus five
  private helpers in the same file.
- `app/routes/dashboard.py` (lines 33, 54-`tail of mark_paid`).
  Commit 6 (F-9) removes the `mark_paid` route handler and the
  `_MARK_PAID_SCHEMA` singleton.
- `app/__init__.py` (line 285 area -- the Jinja-context-global
  block). Commit 7 (F-8) extends `template_globals` with the
  `recurrence_pattern_ids` dict alongside the existing
  `recurrence_pattern_labels`.

### 6.3 Modified templates

- `app/templates/grid/_mobile_card_actions.html` (lines 66, 75, 85).
  Commit 1 (F-3): three `style="min-height: 44px;"` attributes
  replaced with class additions.
- `app/templates/templates/list.html` (lines 21-50 -- the
  `recurrence_cell` macro). Commit 7 (F-8): elif chain rewired from
  `pname == 'Every Period'` to `rr.pattern_id ==
  recurrence_pattern_ids[RecurrencePatternEnum.EVERY_PERIOD]`.

### 6.4 Deleted files

- `app/templates/companion/_transaction_card.html` -- Commit 5
  (F-4). 94 lines, zero callers.

### 6.5 Modified schemas

- `app/schemas/validation.py` (lines 391-394 -- the
  `MarkPaidSchema` class). Commit 6 (F-9): class removed; the
  preceding-block comment at `:394` is rewritten to anchor on
  `transactions.mark_done`.

### 6.6 Modified tests

- `tests/test_routes/test_dashboard.py` (line 218 area --
  `TestMarkPaid` class with 7 tests; line 194 -- vacuous
  assertion). Commit 6 (F-9): `TestMarkPaid` deleted; the
  `:194` assertion rewritten to a direct check that the paid bill
  is excluded from the upcoming-bills section.
- `tests/test_routes/test_dashboard_entries.py` (line 540 --
  `test_mark_paid_tracked_returns_paid_row_without_progress`).
  Commit 6: test removed (test for code being removed).
- `tests/test_routes/test_c27_input_validation_sweep.py` (lines
  16, 331-335). Commit 6: parametrize entry + section comment
  removed.
- `tests/test_routes/test_c21_state_machine_broad_rollout.py`
  (line 6 -- module docstring). Commit 6: route name removed from
  the listed scope.
- `tests/test_routes/test_transfers.py` (line 905 -- comment
  cross-ref). Commit 6: comment rewritten to anchor on
  `transactions.mark_done`.
- `tests/test_routes/test_grid.py` (line 178 -- existing test
  suite). Commit 8 (F-6): targeted run must stay green by
  construction. No new tests required; optionally add helper-level
  unit tests per OPT-F5 (rejected; not promoted).
- `tests/test_routes/test_templates.py` (59 tests). Commit 7 (F-8):
  targeted run must stay green; one new lock test asserting absent
  `.name ==` / `pname ==` comparisons.

### 6.7 Modified comment cross-refs

- `app/services/transfer_service.py:507` -- Commit 6 rewrites the
  `dashboard.mark_paid` cross-reference.
- `app/routes/transfers.py:1101` -- same.
- `app/schemas/validation.py:394` -- same (block comment above
  `MarkPaidSchema`; the comment survives the schema removal as a
  general note on the shared parse contract).
- `app/templates/dashboard/_bill_row.html:53-57` -- Commit 6:
  orphan comment removed (the route it points to no longer exists).

### 6.8 Modified CSS

- `app/static/css/app.css` (lines 754-`end of @media (max-width:
  767.98px)` block, currently `~1255`). Commit 1 (F-3): add
  `.btn-touch-44 { min-height: 44px; }` rule. Commit 2 (F-7): add
  `.shekel-scroll-pills .nav-link { min-height: 44px; display:
  flex; align-items: center; justify-content: center; }` rule. Both
  rules sit inside the existing mobile media query block; no new
  breakpoints.

### 6.9 New verification harness

- `tests/manual/verify_followup_commit1.py` (etc., per commit
  -- optional, one per commit that has browser-visible changes).
  The harness uses the same Playwright pattern as
  `verify_mobile_grid_commit*.py` and `verify_mobile_nav_commit23.py`
  shipped during v3. Commits with no browser-visible change (F-5,
  F-6, F-9 backend deletion, F-2 infra) need no harness.

### 6.10 Modified docs

- `docs/mobile_follow_up.md` -- mark each `F-N` "**Status:**" line
  as `closed` (with the commit SHA that closed it) as each commit
  lands. Out-of-scope items surfaced during execution append new
  `F-11`, `F-12`, ... entries per CLAUDE.md rule 6 + common-rules
  rule 6.

---

## 7. Commit dependency analysis

```text
Phase 1 -- Touch-target / CSP polish (independent of all others)
  1 F-3 btn-touch-44 class + replace 3 inline styles ───┐
  2 F-7 shekel-scroll-pills 44 px floor ─────────────────┤
                                                        │
Phase 2 -- Pylint cleanups (independent of P1)         │
  3 F-5 grid.py import order ─────────────────────────┐ │
                                                       │ │
Phase 3 -- Behavioural bug fix (independent of P1, P2) │ │
  4 F-1 swipe-handler -> #mobile-plan ───────────────┐ │ │
                                                     │ │ │
Phase 4 -- Orphan deletion (independent of P1-P3)   │ │ │
  5 F-4 delete companion/_transaction_card.html ────┤ │ │
  6 F-9 retire dashboard.mark_paid + tests + refs ──┤ │ │
                                                     │ │ │
Phase 5 -- Refactors                                 │ │ │
  7 F-8 recurrence_cell -> ref_cache IDs ───────────┤ │ │
  8 F-6 decompose grid.py::index ───────────────────┘ │ │
        |                                              │ │
        +-- depends on Commit 3 (F-5) -- same file --- │ │
            -- the import reorder lands first so the   │ │
               decomposition diff stays focused        │ │
                                                       │ │
Phase 6 -- Verification + infra                        │ │
  9 F-10 SW cache DevTools capture --------------------┤ │
 10 F-2 containerize dev Flask (ask first) ────────────┘ │
                                                         │
        +-- 10 is largely independent of all the rest;   │
            ordered last because it's half-day scope and │
            the user may defer it to its own PR.         │
```

Ordering rationale:

- **Phase 1 (Commits 1-2) is the smallest, fastest set.** Two CSS /
  template diffs, each under 10 lines. Either can land first. The
  plan orders F-3 before F-7 because F-3 introduces the
  `.btn-touch-44` utility class pattern that F-7 might choose to
  reuse (it does not -- F-7 names a specific selector -- but the
  ordering documents the precedence).
- **Phase 2 (Commit 3) is the smallest Python diff.** Pylint
  warnings disappear; no behaviour change. Lands before Commit 8
  because Commit 8 touches the same file and a clean import order
  makes the decomposition diff easier to read.
- **Phase 3 (Commit 4) is the only behavioural bug fix.** Independent
  of every other commit; could land first if the user wants the
  user-visible fix shipped before the cleanups. The plan orders it
  fourth because the touch-target floor (Commits 1-2) is the higher
  daily-friction fix and pylint cleanups (Commit 3) are zero-risk.
- **Phase 4 (Commits 5-6) is pure deletion.** Commit 5 is one
  `git rm`; Commit 6 is the larger of the two (a route handler, a
  schema, 7 tests, 4 comment refs, the vacuous-assertion rewrite).
  Both leave the suite green by construction (the tests deleted are
  tests for code being deleted; no other test depends on either).
- **Phase 5 (Commits 7-8) is the refactor block.** Commit 7 (F-8)
  is independent of Commit 8 (F-6) but both are CLAUDE.md-quality
  fixes rather than feature work; grouping them communicates
  intent. Commit 8 depends on Commit 3 (same file, F-5's import
  reorder lands first to keep diffs clean).
- **Phase 6 (Commits 9-10) is verification + infra.** Commit 9 is a
  manual capture, zero code change. Commit 10 is the half-day
  dev/prod-parity commit that the F-2 finding flags as "pure
  dev-environment refactor with no impact on the application code
  path" -- the user may elect to defer it to its own plan.

Every commit leaves the suite green. Phase 4-5 commits change code
behaviour along their stated axis; their Section E test plans assert
the change.

---

## 8. Commit checklist

| # | Commit message | Summary | F-N |
|---|---|---|---|
| 1 | `style(mobile): btn-touch-44 utility + replace 3 inline styles in _mobile_card_actions` | Inline `style="min-height: 44px;"` -> `.btn-touch-44` class; CSP-blocked styles become active | F-3 |
| 2 | `style(mobile): 44 px floor on .shekel-scroll-pills .nav-link` | Mobile-media-query rule applies to analytics / loan / settings tab rows | F-7 |
| 3 | `style(grid): reorder third-party / first-party imports` | `sqlalchemy.orm.selectinload` moves into the third-party block; `app.utils.auth_helpers` re-groups; pylint C0411 + C0412 cleared | F-5 |
| 4 | `fix(mobile-grid): scope period-nav swipe handler to #mobile-plan` | Listener moves from `#mobile-grid` to `#mobile-plan` so This Period tab swipes no longer silently advance Plan | F-1 |
| 5 | `chore(companion): delete orphan _transaction_card.html` | `git rm` the 94-line file; zero callers as of v3 commit 13 | F-4 |
| 6 | `refactor(dashboard): retire dashboard.mark_paid route + tests + cross-refs` | Route + `MarkPaidSchema` + `TestMarkPaid` (7 tests) + 4 comment refs deleted; vacuous assertion at test_dashboard.py:194 rewritten to non-vacuous | F-9 |
| 7 | `refactor(templates): recurrence_cell uses ref_cache pattern IDs` | macro `pname ==` chain replaced with `pattern_id ==` lookups via injected `recurrence_pattern_ids` dict; new lock test | F-8 |
| 8 | `refactor(grid): decompose index() into five private helpers` | `_resolve_grid_context`, `_load_grid_transactions`, `_build_grid_balances`, `_build_grid_subtotals`, `_build_grid_row_data`; pylint R0914/R0912/R0915 cleared; static guards stay green | F-6 |
| 9 | `chore(pwa): capture SW cache DevTools audit screenshot` | Manual capture under `tests/manual/screenshots/`; documents Section 10 item 7 of the v3 plan | F-10 |
| 10 | `feat(deploy): dev Flask in Docker behind shared nginx (ASK)` | Half-day; new `deploy/docker-compose.dev.yml`; nginx dev conf swap; retires the bare-host `flask run --host 172.32.0.1` workflow | F-2 |

---

## 9. Commits (detailed)

Each commit follows the house format: **A.** commit message,
**B.** problem statement, **C.** files modified, **D.** implementation
approach, **E.** test cases, **F.** manual verification steps,
**G.** downstream effects, **H.** rollback notes. Test IDs are
`C<commit>-<n>`. "Re-pinned tests" is "none" on every commit because
no financial assertion changes; if execution surfaces one, name the
finding ID + hand-arithmetic per CLAUDE.md rule 5.

### Commit 1 -- btn-touch-44 utility + replace 3 inline styles

**A. Commit message** `style(mobile): btn-touch-44 utility + replace 3 inline styles in _mobile_card_actions`

**B. Problem statement** `app/templates/grid/_mobile_card_actions.html:66,
:75, :85` each carry `style="min-height: 44px;"` on the Mark Paid /
Edit Amount / Open Full buttons. The project's CSP at
`app/__init__.py:725` (`style-src 'self'`) blocks inline `style="..."`
attributes; the buttons render at Bootstrap's default `btn-sm` height
(~38-40 px), missing the v3 hard-rule-7 / WCAG 2.5.5 / Apple HIG 44 px
touch-target floor. Per `docs/mobile_follow_up.md` F-3.

**C. Files modified**
- `app/static/css/app.css` -- add `.btn-touch-44 { min-height: 44px; }`
  inside the existing `@media (max-width: 767.98px)` block at
  `line 755+`.
- `app/templates/grid/_mobile_card_actions.html` -- three buttons at
  `:66, :75, :85`: drop `style="min-height: 44px;"`, add
  `btn-touch-44` to each `class="..."`.

**D. Implementation approach** Verify the three line numbers via
`grep -n 'min-height: 44px' app/templates/grid/_mobile_card_actions.html`.
The class addition mirrors the existing `.mw-px-*` / `.w-px-*` /
`.fs-*` utility-class convention from the C-02 inline-style migration
(see `app/static/css/app.css:1296-1305` comment block). The class is
NOT scoped to `.mobile-card-action-bar .btn` because a free-standing
utility is reusable for future buttons that need the floor.

**E. Test cases**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C1-1 | `test_no_inline_style_attr_in_mobile_card_actions` | new lock | grep on file | `style=` absent |
| C1-2 | full pytest suite | -- | -- | unchanged pass count |

**F. Manual verification**
1. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.
2. `pylint app/ --fail-on=E,F` clean -- no new warnings.
3. Firefox responsive 375x812: tap a mobile card to open the action
   bar. DevTools "Inspect" each of the 3 buttons: computed height
   `>= 44 px`. No CSP violation in the console.
4. Real iPhone XS / 16 Plus in Firefox iOS: confirm tap area on each
   button feels like a normal touch target.

**G. Downstream effects** Pure CSP / touch-target win. Any future
button needing the floor adds `btn-touch-44` -- no new inline style.

**H. Rollback notes** Revert is `git revert <sha>`; the three inline
styles return (CSP-inert) and the utility class is unused.

---

### Commit 2 -- 44 px floor on .shekel-scroll-pills .nav-link

**A. Commit message** `style(mobile): 44 px floor on .shekel-scroll-pills .nav-link`

**B. Problem statement** `app/static/css/app.css:973-975` defines
`.shekel-scroll-pills .nav-link { white-space: nowrap; }` only.
Bootstrap's default `.nav-link` padding renders at ~40 px height
(`8 + 24 + 8` per the F-7 finding's math) -- 4 px short of the v3
hard-rule-7 floor. Three consumers inherit the gap: analytics tabs
(`app/templates/analytics/analytics.html:16`), loan dashboard tabs
(`app/templates/loan/dashboard.html:21, :323`), settings sidebar
(`app/templates/settings/dashboard.html:22`). Per F-7.

**C. Files modified**
- `app/static/css/app.css` -- add new rule inside the existing
  `@media (max-width: 767.98px)` block at `line 755+`:

  ```css
  .shekel-scroll-pills .nav-link {
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  ```

  Mirrors the existing `#mobile-grid .nav-pills .nav-link` rule at
  `:825-830`.

**D. Implementation approach** Verify the `#mobile-grid .nav-pills
.nav-link` rule shape at `:825-830` exists, then add the new rule
adjacent (after the navbar offcanvas block at `:841-849`, to group
the touch-floor rules visually). The desktop pill row stays at
Bootstrap defaults; the rule applies only at `<= 767.98 px`.

**E. Test cases**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C2-1 | full pytest | -- | -- | unchanged pass count |

(No new pytest -- CSS is browser-rendering, not server-rendering. The
existing route tests assert the HTML; the new rule is style-only.)

**F. Manual verification**
1. `pylint app/ --fail-on=E,F` clean.
2. Firefox responsive 375x812 at `/analytics`, any
   `/savings/loan/<id>`, `/settings`: every pill in the scroll row
   computes `height >= 44 px` in DevTools.
3. Firefox Desktop 1920x1080 at the same URLs: pills unchanged
   (Bootstrap default).
4. Real iPhone XS / 16 Plus in Firefox iOS: tap each tab pill;
   target feels normal.

**G. Downstream effects** Closes the touch-target gap on every
existing `shekel-scroll-pills` consumer. Future consumers inherit
the floor automatically.

**H. Rollback notes** Revert removes the rule; pills shrink back to
~40 px.

---

### Commit 3 -- reorder grid.py imports

**A. Commit message** `style(grid): reorder third-party / first-party imports`

**B. Problem statement** `app/routes/grid.py:17-20` interleaves a
third-party import between first-party ones:

```python
17: from app.utils.auth_helpers import require_owner
18: from sqlalchemy.orm import selectinload
20: from app.extensions import db
```

Pylint reports `C0411` (wrong-import-order) at `:18` and `C0412`
(ungrouped-imports) at `:20`. Per F-5 and CLAUDE.md / coding-
standards.md Python import organisation rule.

**C. Files modified**
- `app/routes/grid.py` (lines 14-29 -- the import block).

**D. Implementation approach** Re-grep the import block with
`sed -n '14,29p' app/routes/grid.py`. Reorder to:

```python
from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.services import balance_resolver, grid_view_service, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import build_entry_sums_dict
from app.services.grid_view_service import RowKey
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import require_owner
```

`app.utils.auth_helpers` moves to its alphabetical position at the
end of the local block.

**E. Test cases**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C3-1 | `pylint app/routes/grid.py` | -- | run | C0411 + C0412 absent; score nudges up |
| C3-2 | full pytest | -- | -- | unchanged pass count |

**F. Manual verification**
1. `pylint app/routes/grid.py --fail-on=E,F` no C0411 / C0412.
2. `pylint app/ --fail-on=E,F` overall score unchanged or higher.
3. `./scripts/test.sh tests/test_routes/test_grid.py -v` green.

**G. Downstream effects** None. Pure import-order fix; runtime
identical.

**H. Rollback notes** Revert restores the warnings (cosmetic).

---

### Commit 4 -- scope period-nav swipe handler to #mobile-plan

**A. Commit message** `fix(mobile-grid): scope period-nav swipe handler to #mobile-plan`

**B. Problem statement** `app/static/js/mobile_grid.js:70-88` attaches
the horizontal-swipe touchstart / touchend listeners to `#mobile-grid`
(the outer tab-container that wraps both tabs). The listeners call
`navigate(delta)`, which mutates `currentIndex` and `display` on the
Plan tab's `.mobile-period-panel` elements. A swipe on the This
Period tab (active by default since v3 commit 6) silently advances
the Plan tab's `currentIndex`; the bug is invisible until the user
switches tabs and finds Plan on the wrong period. Per F-1 (and the
F-1 cross-tab-leak analysis).

**C. Files modified**
- `app/static/js/mobile_grid.js` (lines 70-88).

**D. Implementation approach** Re-grep the binding site with
`sed -n '69,89p' app/static/js/mobile_grid.js`. Replace:

```javascript
var grid = document.getElementById('mobile-grid');
if (grid) {
    var touchStartX = 0;
    var touchStartY = 0;
    grid.addEventListener('touchstart', ..., { passive: true });
    grid.addEventListener('touchend', ..., { passive: true });
}
```

with:

```javascript
var planPane = document.getElementById('mobile-plan');
if (planPane) {
    var touchStartX = 0;
    var touchStartY = 0;
    planPane.addEventListener('touchstart', ..., { passive: true });
    planPane.addEventListener('touchend', ..., { passive: true });
}
```

`#mobile-plan` is the Plan tab-pane; attaching the listeners there
constrains the gesture to the tab the navigation actually drives.
The This Period tab keeps its URL-driven prev/next arrows.
`passive: true` preserved (R-8 alignment from v3 plan).

**E. Test cases**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C4-1 | full pytest | -- | -- | unchanged pass count (gesture is JS-side; no Python infrastructure for touch events) |

**F. Manual verification**
1. `pylint app/ --fail-on=E,F` clean.
2. Firefox responsive 375x812:
   a. Default tab is This Period; swipe left horizontally on a
      mobile card. Nothing happens (no period change in This
      Period; Plan-tab `currentIndex` does not advance).
   b. Switch to Plan tab; swipe left -- period advances. Swipe
      right -- period retreats.
   c. Switch back to This Period; URL is unchanged (still at
      `?periods=1&offset=0` or whatever the arrow set).
3. Real iPhone XS / 16 Plus in Firefox iOS (WebKit): same matrix.
   Firefox iOS uses the right edge for back-navigation gesture --
   confirm that's not affected (the swipe-left is still the Plan
   navigation; right-edge swipe still goes back).
4. Optionally ship `tests/manual/verify_followup_commit4.py` to
   automate the responsive-mode steps as a regression lock.

**G. Downstream effects** This Period swipe-on-card now does
nothing (no period advance). The per-card swipe-action handler
shipped in v3 commit 9 (`swipe.js::attachSwipeAction`) is
unaffected -- it binds at `document` level and matches on
`.mobile-txn-card`, not on the tab container.

**H. Rollback notes** Revert restores the cross-tab leak. The bug
becomes user-visible again on the next This-Period-then-Plan tab
switch.

---

### Commit 5 -- delete orphan companion/_transaction_card.html

**A. Commit message** `chore(companion): delete orphan _transaction_card.html`

**B. Problem statement**
`app/templates/companion/_transaction_card.html` (94 lines) is no
longer reached by any route after v3 commit 13 (`f884fd0`).
Companion's `index.html` includes
`grid/_mobile_this_period.html` which calls `render_row_card`; the
legacy card's markup is now produced entirely by the shared macro.
`grep -rn "_transaction_card" app/ tests/` returns two matches, both
comment-only (R-7). The file is dead weight.

**C. Files modified**
- `app/templates/companion/_transaction_card.html` -- `git rm`.

**D. Implementation approach**

```bash
git rm app/templates/companion/_transaction_card.html
```

Then re-run `grep -rn "_transaction_card" app/ tests/` -- the two
comment references in
`app/templates/grid/_grid_row_macros.html:189` and
`tests/test_routes/test_companion_routes.py:763` remain (they
describe the *legacy* design as a historical reference; deleting
the file does not invalidate the comments). The full pytest suite
must pass without reaching the file at any point.

**E. Test cases**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C5-1 | `./scripts/test.sh tests/test_routes/test_companion_routes.py -v` | -- | run | green |
| C5-2 | `./scripts/test.sh tests/test_routes/test_companion_guards.py -v` | -- | run | green |
| C5-3 | full pytest | -- | -- | unchanged pass count |

**F. Manual verification**
1. `./scripts/test.sh tests/test_routes/test_companion_routes.py
   tests/test_routes/test_companion_guards.py -v` green.
2. Visit `/companion` as the seeded companion user; cards render
   identically to before the deletion.
3. `pylint app/ --fail-on=E,F` clean.

**G. Downstream effects** None. The file was unreachable.

**H. Rollback notes** `git revert` restores the 94-line file. No
runtime change; the file becomes unreachable dead code again.

---

### Commit 6 -- retire dashboard.mark_paid route + tests + cross-refs

**A. Commit message** `refactor(dashboard): retire dashboard.mark_paid route + tests + cross-refs`

**B. Problem statement** Q-1 of the v3 plan resolved as REMOVE
(option (c) at commit 22 / `e079a4e`). The mark-paid `<button>` was
deleted from `app/templates/dashboard/_bill_row.html`; the route,
schema, 7 `TestMarkPaid` tests, the `test_mark_paid_tracked_returns_paid_row_without_progress`
test, the C27 parametrize entry, the C21 docstring reference, and
four comment cross-refs remain as orphan dead code. The vacuous
assertion at `tests/test_routes/test_dashboard.py:194` (the
disjunction whose right operand is always true since the button
was removed) is degraded-not-broken. Per F-9.

**C. Files modified**
- `app/routes/dashboard.py` -- drop the `_MARK_PAID_SCHEMA`
  singleton at `:33`, the `mark_paid` route handler at `:54-end of
  function`.
- `app/schemas/validation.py` -- drop `MarkPaidSchema` at `:391-end
  of class`; the surrounding comment block at `:394` is rewritten
  to drop the `dashboard.mark_paid` reference but keep the
  shared-parse-contract note.
- `app/templates/dashboard/_bill_row.html` -- drop the orphan
  comment at `:53-57` that describes the now-deleted route.
- `tests/test_routes/test_dashboard.py` -- delete `TestMarkPaid`
  class at `:218` (7 tests); rewrite assertion at `:194` to a
  direct check that the paid bill is excluded from the
  upcoming-bills section.
- `tests/test_routes/test_dashboard_entries.py` -- delete
  `test_mark_paid_tracked_returns_paid_row_without_progress` at
  `:540`.
- `tests/test_routes/test_c27_input_validation_sweep.py` -- drop
  the `dashboard.mark_paid` row from the parametrize list at
  `:331-335`; rewrite the module-docstring bullet at `:16`.
- `tests/test_routes/test_c21_state_machine_broad_rollout.py` --
  drop `dashboard.mark_paid` from the module-docstring scope list
  at `:6`.
- `app/services/transfer_service.py:507`,
  `app/routes/transfers.py:1101`,
  `tests/test_routes/test_transfers.py:905` -- comment
  cross-refs rewritten to anchor on `transactions.mark_done`.

**D. Implementation approach** Re-grep every cited line first.
Delete in this order so each step leaves a deletable state:

1. Drop the template comment + the route handler + the
   `_MARK_PAID_SCHEMA` singleton. Full suite at this point still
   passes (the schema is unused; the route's tests still pass
   because the route still exists at this micro-step -- delete the
   route last, not first).

   Wait, actually the route must go BEFORE the tests for the tests
   to fail honestly. Re-sequence:

   1a. Delete `TestMarkPaid` + the single tracked test +
       C27 entry + C21 docstring entry. Suite green.
   1b. Delete the route handler + `_MARK_PAID_SCHEMA` singleton.
       Suite green (no test calls the route any more).
   1c. Delete `MarkPaidSchema`. Suite green.
   1d. Rewrite the four comment cross-refs.
   1e. Delete the orphan template comment.
   1f. Rewrite the vacuous assertion at test_dashboard.py:194 to
       a direct check.

2. The rewritten assertion at `:194` should match the route logic
   at `app/routes/dashboard.py::_upcoming_bills` (verify current
   line; the producer is single-source). New form:

   ```python
   # The paid bill MUST NOT appear in the upcoming-bills section --
   # _upcoming_bills filters by status_id != STATUS_PAID and
   # status_id != STATUS_SETTLED via the canonical settled-status
   # predicate.  Confirm the bill name is absent from that section's
   # rendered HTML rather than asserting on button presence (the
   # mark-paid button was removed in v3 commit 22, so a button-
   # absence check is vacuous).
   bills_section = html.split('<section data-section="bills">')[1].split('</section>')[0]
   assert paid_bill.name not in bills_section
   ```

   (The exact HTML extraction depends on the section's identifying
   attribute -- re-grep `app/templates/dashboard/dashboard.html`
   first.)

**E. Test cases**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C6-1 | `grep -rn 'dashboard.mark_paid\|MarkPaidSchema\|TestMarkPaid' app/ tests/` | -- | post-commit | empty result except for any deliberately-preserved historical references in commit messages or migration docs |
| C6-2 | `./scripts/test.sh tests/test_routes/test_dashboard.py -v` | -- | run | green; pass count `prior - 7` (TestMarkPaid removed) |
| C6-3 | `./scripts/test.sh tests/test_routes/test_dashboard_entries.py -v` | -- | run | green; pass count `prior - 1` |
| C6-4 | `./scripts/test.sh tests/test_routes/test_c27_input_validation_sweep.py -v` | -- | run | green; parametrize entry removed |
| C6-5 | `./scripts/test.sh tests/test_routes/test_dashboard.py::TestDashboardBills::test_paid_bills_not_in_upcoming -v` | -- | run | new assertion form passes |
| C6-6 | full pytest | -- | -- | green; pass count drops by 8 |

**F. Manual verification**
1. `pylint app/ --fail-on=E,F` clean.
2. Visit `/dashboard` as the owner user; paid bills are absent
   from the Bills Due section (the route's existing filter).
3. POST `/dashboard/mark-paid/<int:txn_id>` returns 404 (the route
   no longer exists).

**G. Downstream effects** Eight tests removed from the count (7
TestMarkPaid + 1 tracked-progress). The orphan route is gone; any
future "where is mark-paid?" search lands on
`transactions.mark_done` directly.

**H. Rollback notes** Revert restores the route + schema + tests
+ orphan comments. The vacuous assertion at `:194` becomes vacuous
again.

---

### Commit 7 -- recurrence_cell uses ref_cache pattern IDs

**A. Commit message** `refactor(templates): recurrence_cell uses ref_cache pattern IDs`

**B. Problem statement** `app/templates/templates/list.html:21-50`
defines a `recurrence_cell` macro whose body sets
`{% set pname = rr.pattern.name %}` (line 24) and then compares
`pname == 'Every Period'` / `'Every N Periods'` / `'Monthly'` /
`'Monthly First'` / `'Quarterly'` / `'Semi-Annual'` / `'Annual'` /
`'Once'`. Comparing against string `name` columns violates
CLAUDE.md / coding-standards.md "Reference Tables -- IDs for logic,
strings for display only" and the
`memory/feedback_id_based_lookups.md` user-feedback note. Per F-8.

**C. Files modified**
- `app/__init__.py` (line 285 area -- the Jinja-context-global
  block where `recurrence_pattern_labels` already lives). Inject a
  new `recurrence_pattern_ids` dict alongside it.
- `app/templates/templates/list.html` (lines 21-50). Replace the
  `pname == 'string'` chain with
  `rr.pattern_id == recurrence_pattern_ids[RecurrencePatternEnum.<MEMBER>]`
  lookups.
- `tests/test_routes/test_templates.py` -- add one lock test
  asserting absence of `.name ==` and `pname ==` in the file.

**D. Implementation approach**

1. **Inject the ID dict from `app/__init__.py`.** Re-grep the
   existing block at `:285`:

   ```python
   "recurrence_pattern_labels": {
       member: ref_cache.recurrence_pattern_label(member)
       for member in RecurrencePatternEnum
   }
   ```

   Add adjacent:

   ```python
   "recurrence_pattern_ids": {
       member: ref_cache.recurrence_pattern_id(member)
       for member in RecurrencePatternEnum
   }
   ```

   Both dicts are built once per request (or once per app-init if
   ref_cache memoises -- verify `app/ref_cache.py:511-523` for the
   resolver's cache semantics).

2. **Rewrite the macro.** Each elif comparison takes the form
   `{% elif rr.pattern_id == recurrence_pattern_ids[RecurrencePatternEnum.EVERY_PERIOD] %}`.
   Eight branches; each maps to the corresponding enum member.

3. **New lock test** in
   `tests/test_routes/test_templates.py`:

   ```python
   def test_no_string_name_comparisons_in_recurrence_cell(self):
       """Lock: templates/list.html must not compare against
       pattern.name strings (CLAUDE.md Reference Tables rule)."""
       src = pathlib.Path(
           "app/templates/templates/list.html"
       ).read_text()
       assert ".name ==" not in src
       assert "pname ==" not in src
   ```

**E. Test cases**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C7-1 | `./scripts/test.sh tests/test_routes/test_templates.py -v` | -- | run | green; 59 tests + 1 new lock |
| C7-2 | `./scripts/test.sh tests/test_routes/test_templates.py::TestRecurrenceCellLock::test_no_string_name_comparisons_in_recurrence_cell -v` | -- | run | green |
| C7-3 | full pytest | -- | -- | unchanged pass count + 1 |

**F. Manual verification**
1. `pylint app/ --fail-on=E,F` clean.
2. Visit `/templates` (or wherever active_templates render) as the
   owner; recurrence labels render identically to before.
3. Spot-check each branch: a template with "Every Period" pattern
   renders "Every paycheck"; "Quarterly" with month renders
   "Quarterly (starting MMM)"; etc.

**G. Downstream effects** Future templates wanting a
pattern-driven branch take `recurrence_pattern_ids` from the
Jinja context. The macro is the single consumer today.

**H. Rollback notes** Revert restores the `pname == 'string'`
chain; the lock test fails first, then revert removes the test too.

---

### Commit 8 -- decompose grid.py::index into five private helpers

**A. Commit message** `refactor(grid): decompose index() into five private helpers`

**B. Problem statement** `app/routes/grid.py::index` (lines 47-289)
trips three pylint R-warnings: R0914 (33/15 locals), R0912 (15/12
branches), R0915 (54/50 statements). The function orchestrates:
scenario resolve, account resolve, period range computation,
transaction query, anchor/balances resolve, subtotals build,
category load + filter, row-source scoping, row-key + match-dict
build, status / transaction-type lookups, column-size derivation,
low-balance-threshold lookup, and finally `render_template`. Per
F-6 and CLAUDE.md / coding-standards.md "Keep functions focused"
rule.

**C. Files modified**
- `app/routes/grid.py` (lines 47-289 -- the `index()` function).
  Decompose into the thin orchestrator plus five private helpers
  in the same module:
  - `_resolve_grid_context(user_id, request_args, settings) -> tuple`
    returning `(scenario, account, num_periods, start_offset,
    current_period, periods, all_periods)` or one of the
    empty-state template responses.
  - `_load_grid_transactions(account, scenario, all_periods) ->
    list[Transaction]`.
  - `_build_grid_balances(account, scenario, all_periods) -> tuple`
    returning `(balances, stale_anchor_warning, anchor_balance)`.
  - `_build_grid_subtotals(account, scenario, periods) -> dict`.
  - `_build_grid_row_data(transactions, periods, show_all,
    all_categories) -> tuple` returning `(income_row_keys,
    expense_row_keys, matched_by_row_period, entry_sums)`.

**D. Implementation approach** This is a 30-60 minute refactor.

1. **Re-read `index()` in full** before any edit (CLAUDE.md rule
   10). Map each block of straight-line code to one of the five
   helpers.
2. **Extract one helper at a time.** Each extraction is one
   commit-shaped step:
   - Move the lines into a new function above `index()`.
   - The helper takes the locals it reads as parameters; returns
     the locals it writes.
   - Replace the original block in `index()` with a single call.
   - Run `./scripts/test.sh tests/test_routes/test_grid.py -v` --
     must stay green at each micro-step.
3. **Order helpers by dependency.** `_resolve_grid_context` first
   (no other helper depends on locals from later helpers).
   `_load_grid_transactions` second. `_build_grid_balances` third.
   `_build_grid_subtotals` fourth. `_build_grid_row_data` last.
4. **Static guards stay green.** The three static-guard locks
   (`TestGridPeriodSubtotalCanonical::test_grid_inline_subtotal_loop_removed`,
   `::test_grid_balance_computation_routed_through_resolver`,
   `TestCheckingDetailCanonicalProducer::test_accounts_checking_balance_routed_through_resolver`)
   must stay green; the decomposition preserves
   `balance_resolver.balances_for` as the producer and does NOT
   re-introduce `balance_calculator.calculate_balances(`. Run the
   guards after each micro-step.
5. **Pylint** after the full decomposition: R0914 / R0912 /
   R0915 absent on `index()`. Each helper may itself trip a smaller
   threshold; if any does, decompose further.

**E. Test cases**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C8-1 | full pytest | -- | -- | unchanged pass count |
| C8-2 | targeted: `./scripts/test.sh tests/test_routes/test_grid.py -v` | -- | run | green; 178 tests unchanged |
| C8-3 | static guards: `./scripts/test.sh tests/test_routes/test_grid.py::TestGridPeriodSubtotalCanonical tests/test_routes/test_accounts.py::TestCheckingDetailCanonicalProducer -v` | -- | run | green |
| C8-4 | pylint: `pylint app/routes/grid.py --fail-on=E,F` | -- | run | R0914 / R0912 / R0915 absent on `index()` |

**F. Manual verification**
1. `pylint app/ --fail-on=E,F` clean.
2. Visit `/grid` as the owner; rendered HTML byte-equivalent to
   pre-commit (the refactor is straight-line code moved into
   helpers; no branch condition changed).
3. Diff the response HTML against the pre-commit baseline:

   ```bash
   git stash && curl -s -b cookies.txt http://172.32.0.1:5000/grid > /tmp/grid_pre.html
   git stash pop && curl -s -b cookies.txt http://172.32.0.1:5000/grid > /tmp/grid_post.html
   diff /tmp/grid_pre.html /tmp/grid_post.html
   ```

   Diff must be empty.

**G. Downstream effects** `index()` becomes a thin orchestrator
(~30-40 lines). Each helper is testable in isolation; future
unit tests can target them directly (deferred per D-G / OPT-F5).

**H. Rollback notes** Revert restores the 240-line `index()` and
the pylint R-warnings.

---

### Commit 9 -- capture SW cache DevTools audit screenshot

**A. Commit message** `chore(pwa): capture SW cache DevTools audit screenshot`

**B. Problem statement** `docs/implementation_plan_mobile_v3.md`
Section 10 item 7 asks for a post-Commit-25 DevTools capture
(Application -> Cache Storage -> `shekel-static-v1`) confirming
every cached entry begins with `/static/`. The capture was deferred
at Commit 28; F-10 of `docs/mobile_follow_up.md` tracks it.

**C. Files modified**
- `tests/manual/screenshots/commit9_followup_sw_cache_audit.png`
  (NEW). Screenshot of the DevTools panel.

**D. Implementation approach** This is a manual capture; no code
changes.

1. Open Firefox (Desktop) at `http://172.32.0.1:5000` (or the dev
   origin). Log in. Hard-reload (Ctrl+Shift+R) to register the SW.
2. Open DevTools (F12) -> "Storage" tab (Firefox) or
   "Application" -> "Cache Storage" -> `shekel-static-v1`.
3. Inspect every entry; confirm each `Request URL` starts with
   `/static/`. Take a full-pane screenshot.
4. Save under
   `tests/manual/screenshots/commit9_followup_sw_cache_audit.png`.
5. Repeat on a real iPhone XS (or 16 Plus) in Firefox iOS / Safari
   "Add to Home Screen" install context. Save second screenshot
   under `tests/manual/screenshots/commit9_followup_sw_cache_audit_ios.png`.
6. If any entry is NOT `/static/`-prefixed, treat as a D-I
   regression: open a new follow-up entry, do NOT proceed.

**E. Test cases** None. This is a manual evidence capture.

**F. Manual verification**
1. Both screenshots saved.
2. The plan's Section 11 of `docs/implementation_plan_mobile_v3.md`
   already references this audit -- update it (or this plan's own
   Section 11) at close-out to mark F-10 closed with this commit's
   SHA.
3. Update `docs/mobile_follow_up.md` F-10 `Status:` from `open`
   to `closed (commit <sha>)`.

**G. Downstream effects** Documents the SW invariant holds at
runtime. Future commits touching `app/static/sw.js` re-run the
capture before merging.

**H. Rollback notes** `git revert` removes the screenshots; the
capture is no longer evidence-on-disk but the v3 plan's reference
still stands.

---

### Commit 10 -- (ASK first) dev Flask in Docker behind shared nginx

**A. Commit message** `feat(deploy): dev Flask in Docker behind shared nginx`

**B. Problem statement** The dev Flask server runs on the bare host
via `flask run --host 172.32.0.1`; prod runs in a hardened Docker
container behind Gunicorn behind nginx. Three classes of drift
(process-model, authorisation, network-trust-boundary) make
prod-only failure modes invisible in dev. Per F-2.

**Per D-I: ASK the user before authoring.** This is half-day scope,
largely orthogonal to the application code path. The user may
prefer to defer it to its own focused PR or its own plan.

**C. Files modified (if approved)**
- `deploy/docker-compose.dev.yml` (NEW). Mirrors the prod compose
  with bind-mounted source for hot-reload through Gunicorn's
  `--reload`. Joins the same `shekel-frontend` external bridge.
- `deploy/postgres/` (extension). Sibling Postgres container on a
  dedicated dev DB; pgdata named volume; same `cap_drop: [ALL]`
  hardening as prod.
- `conf.d/shekel-dev.conf` (on the host nginx -- NOT in this repo;
  the change is operational). Proxies to `shekel-dev-app:8000`
  instead of `172.32.0.1:5000`.
- `tests/test_deploy/test_dev_compose.py` (NEW). Asserts the dev
  compose has the same hardening keys as prod.

**D. Implementation approach (if approved)**

1. **ASK the user first.** Confirm: (a) bundle in this plan or
   defer to its own plan; (b) what the development DB should look
   like (fresh template clone? sibling of prod with sanitised
   data?); (c) whether the existing `flask run --host
   172.32.0.1` workflow is documented anywhere this commit needs
   to update.
2. **Pattern after the v3 prod compose** at
   `deploy/docker-compose.prod.yml`. Reuse the same image, the
   same hardening (`security_opt: [no-new-privileges:true]`,
   `cap_drop: [ALL]`, `read_only: true`, non-root `user:`,
   `tmpfs:` for write paths). Add `--reload` to the Gunicorn
   command for dev hot-reload.
3. **Bind-mount the repo at `/home/shekel/app`** so code edits land
   inside the container without rebuilding.
4. **External `shekel-frontend` bridge**: same network as prod so
   the shared nginx reaches `shekel-dev-app:8000` the same way it
   reaches `shekel-prod-app:8000`.
5. **Dev DB**: dedicated Postgres container on a private bridge
   (not the shared one) so the prod DB is untouchable. Reuse
   `shekel_user` / `shekel_app` two-role policy from prod.
6. **New `tests/test_deploy/test_dev_compose.py`** asserts:
   - Compose file parses (`yaml.safe_load`).
   - `security_opt`, `cap_drop`, `read_only`, `user:` all set on
     every service.
   - Dev compose service names + port mappings match the prod
     pattern.

**E. Test cases (if approved)**

| ID | Test | Setup | Action | Expected |
|---|---|---|---|---|
| C10-1 | `python -m yaml deploy/docker-compose.dev.yml` (or `docker compose -f deploy/docker-compose.dev.yml config`) | -- | run | exit 0, no errors |
| C10-2 | `./scripts/test.sh tests/test_deploy/test_dev_compose.py -v` | -- | run | green |
| C10-3 | full pytest | -- | -- | unchanged pass count + dev-compose tests |
| C10-4 | `docker compose -f deploy/docker-compose.dev.yml up` | host | run | container starts; healthcheck passes |
| C10-5 | `curl http://shekel-dev.saltyreformed.com/grid` (or whatever the dev hostname is) | host | run | 302 to login, 200 after auth |

**F. Manual verification (if approved)**
1. `pylint app/ --fail-on=E,F` clean.
2. `docker compose -f deploy/docker-compose.dev.yml up` -- container
   starts, `entrypoint.sh` runs, Gunicorn binds.
3. The shared nginx config reload (`nginx -s reload` on the host)
   picks up the new upstream.
4. Visit the dev URL; the v3 mobile UX works identically.
5. The bare-host `flask run --host 172.32.0.1` workflow is now
   redundant; remove from any developer-onboarding docs.

**G. Downstream effects (if approved)** Future dev workflow is
`docker compose -f deploy/docker-compose.dev.yml up`. The
process-model, authorisation, and network-trust drift from F-2 all
close. Manual verification harnesses
(`tests/manual/verify_*.py`) target the dev container instead of
the bare-host Flask.

**H. Rollback notes (if approved)** `git revert` restores the
bare-host workflow. The shared nginx config reverts to proxy to
`172.32.0.1:5000`. The `shekel-frontend` bridge has one fewer
member; otherwise unchanged.

---

## 10. Cross-cutting verification

Beyond per-commit Section F, the plan's whole-project gates:

1. **CI gate per phase.** Each phase's last commit is the per-phase
   final commit; CI must be green before merging `dev -> main`. CI
   runs `pylint app/ --fail-on=E,F` plus the full pytest suite per
   `.github/workflows/ci.yml`.
2. **Desktop regression.** Firefox 1920x1080. Walk the touched pages
   per commit, confirm zero visible change vs. pre-commit unless the
   commit explicitly changes desktop output (only Commit 6's
   dashboard does; the others are mobile-only or test-only).
3. **Mobile viewport coverage.** Firefox responsive design mode at
   375x812 (iPhone XS) and 430x932 (iPhone 16 Plus). Per the v3
   plan's hard-rule 9 these are the two reference viewports.
4. **Real iPhone testing for behavioural changes.** Commits 1, 2, 4
   ship browser-visible behaviour changes; verify on the real
   iPhone XS + 16 Plus in Firefox iOS before merging to `main`.
   Commits 3, 5, 6 (backend), 7 (template-equivalent output), 8
   (HTML byte-equivalent), 9 (manual evidence), 10 (infra) do not
   require real-device verification beyond their per-commit Section F.
5. **Static-guard preservation.** Commits 6 and 8 touch
   route-handler code. The three static-guard locks
   (`TestGridPeriodSubtotalCanonical::*`,
   `TestCheckingDetailCanonicalProducer::*`) must stay green; ditto
   the no-new-direct-anchor-reads grep from the v3 Commit 28
   appendix.
6. **`docs/mobile_follow_up.md` close-out.** Each `F-N` entry's
   `Status:` line moves from `open` to `closed (commit <sha>)` as
   its commit lands. The plan's Section 11 (this plan's own
   verification appendix, populated at the close-out commit) lists
   the SHA per finding.

---

## 11. Verification appendix (filled in at the close-out commit)

This section is appended at the close-out commit's implementation
time and records the final state of the work: which commits landed,
which OPT-F items were promoted (if any), which screenshots /
DevTools captures were taken, which open questions resolved or
carried forward.

(Section currently empty -- populated at the final commit.)

---

## 12. Open questions carried forward

- **Q-F1. Should `_helpers` in F-6 be relocated to a sibling
  module (`grid_helpers.py`) or stay private inside `grid.py`?**
  Resolved via D-G as "stay private". OPT-F5 carries the relocate
  option forward if usage data suggests a second consumer.
- **Q-F2. Should the recurrence_cell elif chain collapse entirely
  after F-8?** OPT-F1; not promoted by default because the dynamic-
  label branches (`Monthly (day N)`, `Quarterly (starting MMM)`)
  cannot collapse without losing label fidelity.
- **Q-F3. Is the dev/prod-parity work (F-2 / Commit 10) in scope of
  this plan or its own plan?** Asked at Commit 10 implementation
  time per D-I; either is acceptable.

---

## 13. Notes on executing this plan

- Run commits in order within each phase; phases can be reordered
  freely (Phase 1, 2, 3, 4, 5, 6 are all independent except for
  Commit 8 depending on Commit 3 -- same file).
- Every commit: re-grep cited lines first (line numbers drift);
  targeted tests during edits
  (`./scripts/test.sh tests/path/test_file.py -v`); `pylint app/
  --fail-on=E,F` after edits; full suite via `./scripts/test.sh`
  as the per-commit final gate. `SKIP_DB_RESTART=1` on follow-up
  invocations in the same session.
- The test template does NOT need rebuilding by this plan (no
  schema changes, no `app/ref_seeds.py` or
  `app/audit_infrastructure.py` edits, no migrations).
- Never silently re-pin a test. The plan calls out "Re-pinned
  tests: none" on every commit; if execution surfaces a test that
  needs re-pinning, name the finding and the hand arithmetic in a
  comment per CLAUDE.md rule 5.
- Every session ends with a work summary using the
  `remediation_follow_up_common.md` A-M labels verbatim. Section J
  records out-of-scope items spotted during execution + new
  `F-N` entries appended to `docs/mobile_follow_up.md`.
- This is an implementation plan only. No code is changed by
  producing this document. Execution happens in separate sessions;
  one commit per session, suite green before moving on.

---

## 14. Out of scope

- New user-facing mobile UX features. The v3 plan delivered those;
  this plan only tightens the implementation underneath.
- New financial calculations or business logic. All math, status
  transitions, balance projections stay in the existing
  `app/services/` modules.
- New static-guard locks beyond OPT-F2 (which is itself out of the
  default set; promote if desired).
- Service-worker dynamic-caching or offline-editing-queue work.
  Re-rejected by D-I from the v3 plan; not revisited here.
- Bottom-tab-bar navigation (OPT-M8 from v3 Section 5). Q-3 from
  v3 Section 12 still names it; not promoted here.
- Web push notifications. Q-4 from v3 Section 12; v4 candidate.
- Native iOS app / Capacitor / React Native. Tier-3 rejected per
  v1's `mobile_friendliness_assessment.md`; unchanged.
- New testing framework for touch events. v3 Section 14 item 6;
  unchanged. Manual verification at the listed viewports + real
  devices remains the chosen approach.
- Dashboard redesign beyond the F-9 cleanup. v3 plan's "v4
  candidate" framing unchanged.
- Re-running the v3 plan's Commit 22 disposition (Q-1 REMOVE).
  Already shipped; F-9 only closes the orphan tail.
