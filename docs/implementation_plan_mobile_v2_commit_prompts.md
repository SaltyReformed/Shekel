# Mobile-First v2 -- Commit Prompts

- Companion to: `docs/implementation_plan_mobile_v2.md`
- Required reading for every prompt:
  `docs/audits/financial_calculations/remediation_follow_up_common.md`
  (generic rules + work summary format) AND
  `docs/implementation_plan_mobile_v2.md` Section 1 (mobile-specific hard rules)
- Purpose: one ready-to-paste session prompt per commit (17 total) so each commit can be
  executed in its own fresh session.
- Audience: future Claude Code sessions (and the developer reading what each session was
  asked to do).

## How to use this document

1. Wait until every prerequisite commit listed under "Prereqs on dev" has been merged to
   `dev` (and `main`, via the PR-gated workflow in CLAUDE.md). Each prompt depends only on
   the state of `dev`, not on any prior session context.
2. Start a fresh Claude Code session at the project root with `dev` checked out.
3. Copy the entire fenced block under the commit's heading. Paste it as the first message
   in the new session. Do not edit it.
4. The session will read the canonical plan section for this commit, re-verify against
   current code, do the work, run the gates, and stop with a structured work summary that
   ends by asking whether to commit and push. **No commit or push happens without your
   explicit go-ahead.**
5. After the commit lands on `dev` and CI is green, open a PR `dev` -> `main`. After
   merge, resync `dev`
   (`git fetch origin && git checkout dev && git merge origin/main && git push origin dev`)
   before starting the next prompt.
6. If a session reports drift between the plan and current code, stop and reconcile (edit
   the plan or adjust the prompt) before continuing. The plan is the floor, not a
   free-floating wish list.

The prompts are ordered to match the plan's commit numbering (Section 8 checklist). Read
`implementation_plan_mobile_v2.md` Section 7 (Commit dependency analysis) once before
starting; the prereqs in each prompt below encode it but the picture is easier to hold
from the DAG.

**Firefox parity note** (mobile plan Section 1 hard rule 3): every commit's manual
verification runs on Firefox Desktop (Gecko) and Firefox Android (GeckoView). Firefox
iOS uses WebKit per Apple's App Store rules; commits that touch features WebKit handles
differently document the limitation explicitly. The Section F verification steps in the
plan list the device-specific checks; the prompts below preserve them verbatim under
"Manual verification."

---

## Phase A -- Foundations and grid (Commits 1-5)

### Commit 1 -- `feat(mobile): touch gestures, bottom sheet primitive, mobile design tokens`

**Prereqs on dev:** none. **Closes:** Phase A foundation; unblocks Commits 2-5, 14, 15.

```text
You are executing Commit 1 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- read each in full BEFORE anything else (use @path so they are
fetched, do not summarize from memory or training):
- @docs/implementation_plan_mobile_v2.md (Sections 0-7 for context; Section 9 "Commit 1"
  for the A-H specification; Section 1 "Hard rules for executing this plan" -- mandatory;
  Section 4 "Pattern -> canonical implementation map" -- the spine)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply rules and
  work summary format -- mandatory; the rules there are the floor for every commit in
  this codebase regardless of plan)
- @docs/coding-standards.md ("JavaScript" and "CSS" sections)
- @app/static/css/app.css (read the v1 bottom-sheet block at :838 in full; this commit
  extracts it to mobile_components.css unchanged)
- @app/static/js/mobile_grid.js (read in full; will import from the new modules in later
  commits)

Objective: foundation commit. Build the reusable swipe-gesture utility
(`touch_gestures.js::attachSwipe` on Pointer Events), the reusable bottom sheet primitive
(`bottom_sheet.js::openSheet` + `closeSheet`), and the shared CSS design tokens
(`--touch-target-min`, `--safe-area-*`, `--sheet-handle-h`, `--fab-size`, etc.) in a new
`mobile_components.css` imported once from `app.css`. Extract the v1 bottom-sheet block
from `app.css:838` to the new file byte-identically (regression safety). No user-visible
behavior change; this commit only stages the primitives that Commits 2-5 will consume.

Files this commit touches:
- app/static/js/touch_gestures.js (new): attachSwipe with options thresholdPx,
  restraintPx, velocityPxPerMs, peekPx; built on Pointer Events; setPointerCapture for
  clean cancellation.
- app/static/js/bottom_sheet.js (new): openSheet(contentEl, options), closeSheet;
  backdrop, inert focus trap, Escape, swipe-down dismiss via attachSwipe; ARIA dialog.
- app/static/css/mobile_components.css (new): design tokens (:root custom props),
  bottom-sheet styles extracted from app.css.
- app/static/css/app.css: add `@import url('./mobile_components.css');` near top; remove
  the extracted bottom-sheet block.
- tests/test_static/test_static_assets.py (new or extend): assert files load and contain
  expected exports / tokens.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md (sections "Apply
these rules (every commit)" and "Work summary format") AND the mobile-specific hard
rules in @docs/implementation_plan_mobile_v2.md Section 1. End the session with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M1-1 through M1-5 per plan Section 9 Commit 1 E table.
- `grep -n "bottom-sheet-backdrop" app/static/css/app.css` returns empty (block was
  extracted).
- `grep -n "@import url('./mobile_components.css')" app/static/css/app.css` returns
  exactly one match.
- `grep -n "export function attachSwipe" app/static/js/touch_gestures.js` returns one
  match.
- `grep -n "export function openSheet" app/static/js/bottom_sheet.js` returns one match.
- Manual verification: Firefox Desktop -- any existing bottom sheet still opens/closes
  on Escape, traps focus. Firefox Android -- the v1 full-edit popover from commit
  c1cc309 still works byte-identically.
- Full suite green (no behavior change expected; this catches regressions in the
  v1 bottom sheet extraction).

If anything is unclear, ASK.
```

---

### Commit 2 -- `feat(mobile): sticky affordability strip on mobile grid (D-C)`

**Prereqs on dev:** Commit 1. **Closes:** the user's primary daily-workflow friction
(affordability decision requires swiping through three single-period cards). Unblocks
Commits 9, 10, 11.

```text
You are executing Commit 2 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 2" A-H;
  Section 1 hard rules; Section 2 design decision D-C)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_plan.md (Commit 5 + Commit 11 -- the
  balance_resolver SSOT and the cross-page equality lock that this commit must not
  regress)
- @app/services/balance_resolver.py (read balances_for at :371, period_subtotal at :452,
  balance_as_of_date at :637)
- @app/templates/grid/_mobile_grid.html (read in full, 250 lines)
- @app/routes/grid.py (read the mobile-grid context branch; re-grep for is_mobile or
  the dispatch)
- @app/utils/money.py (round_money helper, E-26)

Objective: add a sticky three-period balance strip at the top of the mobile grid so the
user's daily affordability decision (current + next two periods' end balance) collapses
to a glance. The strip values flow through balance_resolver.balances_for ONLY (hard rule
1 -- canonical producer; HIGH-01 cross-page equality lock must stay green). The partial
is reusable; Commits 9-11 will apply the same component to /savings, /accounts, and
/loan.

Files this commit touches:
- app/templates/partials/_sticky_summary.html (new): reusable strip; cells, note slot.
- app/templates/grid/_mobile_grid.html: include the partial at the top of the mobile
  card view (above period navigation).
- app/routes/grid.py: extend mobile-grid context with `affordability_cells`
  (list of {label, value, tone} dicts derived from balance_resolver).
- app/services/balance_resolver.py: ONLY if measurement shows 3 sequential balances_for
  calls are too slow, add balances_for_period_range(account, scenario_id, start_period,
  count); otherwise skip (per plan -- this helper is conditional, not promised).
- app/static/css/mobile_components.css: .summary-strip styles (sticky top,
  safe-area aware, tonal colors via Bootstrap utilities, backdrop-filter blur).
- tests/test_routes/test_grid.py: assert affordability_cells in context and values match
  balance_resolver.
- tests/test_integration/test_balance_consistency.py (HIGH-01 lock; existing): extend to
  assert strip cell 1 equals grid current-period end balance.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M2-1 through M2-6 per plan Section 9 Commit 2 E table.
- `grep -n "current_anchor_" app/templates/grid/_mobile_grid.html
  app/templates/partials/_sticky_summary.html` returns empty (no direct anchor reads;
  must go through balance_resolver).
- HIGH-01 cross-page invariant still green (run
  `./scripts/test.sh tests/test_integration/test_balance_consistency.py -v`).
- Manual verification: Firefox Android -- mobile grid shows 3-cell strip; sticky during
  scroll; URL bar collapse does not cover strip. Firefox Desktop responsive 375px --
  same. Firefox iOS (if available) -- strip renders; backdrop blur may degrade on older
  WebKit (acceptable; document in work summary section A).
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 3 -- `feat(mobile): swipe-to-act on mobile grid cells (D-A)`

**Prereqs on dev:** Commit 1. **Closes:** the multi-tap mark-Done friction; reduces it to
one gesture. Unblocks Commit 12.

```text
You are executing Commit 3 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 3" A-H;
  Section 1 hard rules; Section 2 design decision D-A -- right-swipe = mark Done,
  left-swipe = action menu)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/services/state_machine.py (read allowed_transitions; this is the canonical state
  machine; the swipe handler must NOT re-implement it client-side)
- @app/static/js/mobile_grid.js (read in full, 85 lines)
- @app/templates/grid/_mobile_grid.html (read in full)
- @app/templates/grid/_transaction_cell.html and _transaction_card.html (the cell
  partials -- re-grep at edit time for current mobile branch)
- @app/static/js/touch_gestures.js (the attachSwipe utility from Commit 1)

Objective: attach swipe-to-act gestures to mobile grid cells. Right-swipe marks a
Projected transaction Done in one gesture (posts to the existing mark-done HTMX endpoint
-- no new route, no new business logic). Left-swipe opens a status action sheet listing
valid next-statuses from state_machine.allowed_transitions(current_status_id) -- the
list is rendered server-side and passed as context, NOT computed in JS. Both gestures
show a visual peek and snap back if released before threshold. Keyboard equivalent
retained: tapping a cell still opens the full bottom sheet with the status selector
(hard rule 5).

Files this commit touches:
- app/static/js/mobile_grid.js: import attachSwipe; attach to .mobile-txn-cell on
  render; right-swipe -> POST mark-done; left-swipe -> openSheet with status action
  partial.
- app/templates/grid/_transaction_card.html (or the mobile cell partial; re-grep): add
  data-swipe-actionable, data-txn-id, data-status-id, and embed allowed_next_status_ids
  as JSON.
- app/templates/partials/_status_action_sheet.html (new): renders the action sheet
  content from the passed list; each button hx-posts to the existing status-transition
  endpoint.
- app/routes/grid.py (or wherever the mobile cell context is built): pass
  allowed_next_status_ids per cell via state_machine.allowed_transitions.
- app/static/css/mobile_components.css: .swipe-peek-right (green / mark Done),
  .swipe-peek-left (gray / menu), accessible icons.
- tests/test_routes/test_grid.py: assert cell context includes allowed_next_status_ids
  matching the state machine.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M3-1 through M3-5 per plan Section 9 Commit 3 E table.
- `grep -nE "Status\.|status\.name ==" app/static/js/mobile_grid.js` returns empty (no
  JS state-machine; hard rule 1 + ID-based-lookups memory).
- `grep -n "allowed_transitions" app/static/js/mobile_grid.js` returns empty (client
  does not call the state machine; server passes the result).
- Manual verification: Firefox Android -- right-swipe Projected cell marks Done; release
  before threshold snaps back; left-swipe opens action sheet with valid statuses; tap
  fallback still opens full bottom sheet (keyboard / a11y); Done cell suppresses
  right-swipe and shows only valid-from-Done options on left-swipe. Firefox Desktop
  responsive mode -- mouse drag triggers Pointer Events identically.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 4 -- `feat(mobile): quick-add bottom sheet with template-aware and ad-hoc modes (D-B)`

**Prereqs on dev:** Commit 1. **Closes:** no fast-path for adding a new transaction on
mobile. Unblocks Commit 13.

```text
You are executing Commit 4 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 4" A-H;
  Section 1 hard rules; Section 2 design decision D-B -- template field optional,
  ad-hoc supported, single form not two)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/schemas/validation.py (read TransactionCreateSchema in full; this is the SSOT
  for what fields are required for transaction creation)
- @app/routes/transactions.py (read the existing create endpoint; re-grep)
- @app/templates/grid/_transaction_quick_create.html and _transaction_full_create.html
  (the existing create surfaces; reuse what you can, do not duplicate)
- @app/static/js/bottom_sheet.js (the primitive from Commit 1)
- @app/static/css/mobile_components.css (the FAB tokens from Commit 1)

Objective: add a quick-add bottom sheet reachable from a grid-scoped floating action
button. Single form: optional template typeahead (renders a `<select>` with "(custom
transaction)" as the first option), required amount + type + account + category +
period, optional notes. Selecting a template pre-populates the other fields with
template defaults but leaves them editable -- ad-hoc support is the no-template path,
not a separate surface (hard rule: DRY). POSTs through the existing transaction create
endpoint validating via TransactionCreateSchema; no new POST route, no parallel schema.

Files this commit touches:
- app/templates/partials/_fab.html (new): reusable FAB partial; accepts data-action or
  href.
- app/templates/partials/_quick_add_sheet.html (new): the bottom sheet form.
- app/templates/grid/_mobile_grid.html: render _fab.html with
  data-action="open-quick-add"; safe-area-inset-bottom positioning.
- app/static/js/mobile_grid.js: FAB click handler -> bottom_sheet.openSheet fetching the
  partial via HTMX.
- app/routes/transactions.py (re-grep at edit time): add GET /transactions/quick-add
  returning the partial pre-populated for the current period; the existing POST handles
  submit unchanged.
- tests/test_routes/test_transactions.py: assert GET returns partial with expected
  fields; assert POST with no template_id succeeds (ad-hoc path) and the row has
  template_id NULL.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M4-1 through M4-7 per plan Section 9 Commit 4 E table.
- `grep -n "class.*Schema" app/templates/partials/_quick_add_sheet.html
  app/routes/transactions.py` shows no new schema definition (TransactionCreateSchema is
  reused).
- `grep -n "fields = \[" app/templates/partials/_quick_add_sheet.html` shows fields
  mirror TransactionCreateSchema -- if a future field is added to the schema, the form
  does not auto-update (acceptable; plan calls this out explicitly).
- Manual verification: Firefox Android -- tap FAB; sheet opens; select template
  pre-populates fields; submit creates row; sheet closes; grid updates. Repeat with
  "(custom transaction)" left selected; submit creates ad-hoc row (template_id NULL in
  DB). Firefox Desktop responsive -- swipe-down-to-dismiss works via mouse drag.
  Validation error on missing amount renders in-place via HTMX 422 swap.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 5 -- `feat(mobile): swipe period navigation on mobile grid`

**Prereqs on dev:** Commit 1. **Closes:** mobile-grid period nav is button-only.

```text
You are executing Commit 5 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 5" A-H;
  Section 1 hard rules)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/static/js/mobile_grid.js (read in full)
- @app/templates/grid/_mobile_grid.html (read in full; locate the period-card container
  and the existing next/previous button structure)
- @app/static/js/touch_gestures.js (attachSwipe from Commit 1)

Objective: add left/right swipe between periods on the mobile grid's single-period card
view, complementing (not replacing) the existing buttons (keyboard accessibility -- hard
rule 5). Swipe handler attaches to the period card container with touch-action: pan-y so
vertical scroll within the card is preserved. Commits via synthesized click on the
hidden next/prev button (no second navigation code path). First period suppresses
right-swipe; loading-state suppresses both.

Files this commit touches:
- app/static/js/mobile_grid.js: attach swipe; suppress on first period and during
  loading; synthesize click on existing buttons.
- app/templates/grid/_mobile_grid.html: add data-swipe-period and
  data-swipe-allow-prev / -next attributes to the period card container.
- app/static/css/mobile_components.css: optional CSS transition for swipe peek (up to
  60px transform follow).
- tests/test_routes/test_grid.py: assert period card has data-swipe-period attribute
  and the allow-prev / -next attributes match the position in user history.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M5-1 through M5-3 per plan Section 9 Commit 5 E table.
- Existing period-navigation route tests pass unchanged (regression).
- Manual verification: Firefox Android -- swipe left navigates next; swipe right
  navigates previous; on first period right-swipe shows no peek; buttons still work;
  vertical scroll within the card unaffected.
- Full suite green.

If anything is unclear, ASK.
```

---

## Phase B -- PWA infrastructure (Commits 6-8)

### Commit 6 -- `feat(pwa): service worker shell with version-aware update flow`

**Prereqs on dev:** none (independent of Phase A). **Closes:** no service worker exists;
prerequisite for Commits 7-8 and Section 4 push notifications.

```text
You are executing Commit 6 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 6" A-H;
  Section 1 hard rule 7 -- SW caches static only, never financial data)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/templates/base.html (read in full; the PWA manifest link is at :21, theme-color
  at :22, apple-mobile-web-app-* at :24-26)
- @app/static/manifest.json (the existing PWA manifest)
- @app/__init__.py (read in full; locate where to register the /sw.js passthrough
  route)

Objective: add a service worker shell registered globally from base.html (D-D: registered
for every page, not grid-scoped). Cache-first for static assets (CSS, JS, fonts, images,
manifest); network-first for HTML and JSON with NO stale fallback (hard rule 7 -- a
grid showing yesterday's balances offline produces wrong numbers silently). Version-keyed
cache name; on a new SW activating, a toast appears with a "Reload to update" action.
Served from /sw.js (root scope) via a thin passthrough route -- serving from
/static/sw.js would scope the SW to /static/ which is wrong.

Files this commit touches:
- app/static/sw.js (new): install / activate / fetch / message handlers; version
  constant SHEKEL_SW_V = '1.0.0'; cache-first static, network-first non-static.
- app/static/js/sw_register.js (new): registers on load; listens for updatefound;
  shows toast.
- app/templates/base.html: add sw_register.js script tag (defer) near bottom; add
  #sw-update-toast container.
- app/__init__.py (or app/routes/__init__.py; re-grep): @app.route('/sw.js') one-line
  passthrough route serving app/static/sw.js with content-type application/javascript.
- app/static/css/mobile_components.css: .sw-update-toast styles.
- tests/test_routes/test_sw.py (new): GET /sw.js returns 200 + JS content-type + version
  constant; assert SW does NOT cache HTML/JSON (presence of the network-only branch).

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M6-1 through M6-5 per plan Section 9 Commit 6 E table.
- `grep -nE "caches\.(put|match)" app/static/sw.js` shows put/match are guarded by the
  isStatic check; the network-only branch returns before caching for non-static.
- Manual verification: Firefox Desktop -- DevTools -> Application -> Service Workers
  shows shekel SW registered with scope /; modify a static file, restart, reload;
  update toast appears; reload activates. Offline mode -- static assets load from
  cache; HTML/JSON requests fail with network error (intended; no stale money).
  Firefox Android -- install PWA via menu; launch from home screen; SW active;
  updates work. Firefox iOS -- SW registers under WebKit; cached assets load;
  install via Safari (not Firefox iOS) required for meaningful install; document
  in section A.
- Full suite green.

If anything is unclear, ASK. The fetch-event handler MUST return early (no cache touch)
for non-static requests; verify by reading the implementation, not just by trusting the
comment.
```

---

### Commit 7 -- `feat(pwa): web push subscription plumbing (no notifications sent yet)`

**Prereqs on dev:** Commit 6. **Closes:** Section 4 (Notifications) cannot deliver push
without subscription plumbing. **Requires developer approval before authoring** for the
new `pywebpush` dependency.

```text
You are executing Commit 7 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

NEW DEPENDENCY ALERT: this commit adds `pywebpush` to requirements.txt. CLAUDE.md
"Established patterns" requires explicit developer approval for new packages. Confirm
approval is in hand BEFORE editing requirements.txt or app/services/push_service.py.
If approval is not confirmed, stop and ASK.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 7" A-H;
  Section 1 hard rules; Section 2 design decisions D-E and D-F -- opt-in per device,
  plumbing only / no triggers in this commit)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @CLAUDE.md ("Established patterns" -- ownership helpers, 404 for "not yours,"
  log_event; "Reference tables: IDs for logic"; "Audit triggers" -- AUDITED_TABLES
  expansion procedure)
- @docs/coding-standards.md ("Migrations" -- NOT NULL three-step; "Audit Triggers" --
  AUDITED_TABLES procedure)
- @app/audit_infrastructure.py (read AUDITED_TABLES in full; this commit appends
  ('auth', 'push_subscriptions'))
- @app/utils/auth_helpers.py (the ownership-check pattern this route reuses)
- @app/static/sw.js (the SW from Commit 6; push event handler is added in Section 4,
  NOT here)

Objective: ship the push subscription round-trip and a server-side helper
`send_push(user_id, payload) -> delivered_count` that Section 4 will call from
notification triggers. NO notifications are sent in this commit -- this is pure
plumbing. PushSubscription model (one row per (user_id, endpoint) device); subscribe /
unsubscribe / list routes (user-scoped, 404 for "not yours" per CLAUDE.md security
policy); VAPID keypair loaded from .env (fail-loud at startup if push is configured but
keys absent); device-scoped opt-in toggle in /settings. New table audited.

Files this commit touches:
- app/models/push_subscription.py (new): PushSubscription model. Columns: id, user_id
  (FK auth.users ondelete CASCADE NOT NULL), endpoint (TEXT NOT NULL UNIQUE), p256dh_key
  (TEXT NOT NULL), auth_key (TEXT NOT NULL), device_label (VARCHAR(64) nullable),
  created_at, updated_at, last_used_at (TIMESTAMPTZ nullable). Indexes:
  ix_push_subscriptions_user_id; ix_push_subscriptions_user_id_last_used (partial,
  WHERE last_used_at IS NOT NULL).
- migrations/versions/<auto>_create_push_subscriptions.py (new): table + indexes +
  explicit constraint names per coding-standards.md.
- app/audit_infrastructure.py: append ('auth', 'push_subscriptions') to AUDITED_TABLES.
- app/routes/push.py (new): POST /api/push/subscribe (upserts on endpoint),
  DELETE /api/push/unsubscribe/<id> (ownership-checked -- 404 for not yours),
  GET /api/push/subscriptions (user-scoped).
- app/services/push_service.py (new): send_push(user_id, payload, ttl_seconds=3600) ->
  int; uses pywebpush; on 410 Gone or 404 deletes the subscription and continues
  delivering to other subscriptions; never raises.
- app/static/js/push_subscription.js (new): client subscribe / unsubscribe flow;
  reads VAPID public key from meta tag.
- app/templates/base.html: add <meta name="vapid-public-key" content="..."> populated
  from config.
- app/templates/settings/index.html (or wherever settings live; re-grep): "Push
  notifications" section with per-device toggle and subscription list (device_label
  or "Unnamed device" + last-used date).
- scripts/generate_vapid_keys.py (new): one-time helper that PRINTS keypair to stdout
  for copy-paste into .env; does NOT write .env automatically (no secrets-handling
  shortcuts per CLAUDE.md).
- .env.example: document VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY.
- app/__init__.py or config: load VAPID keys from env; fail-loud at startup if push
  is enabled and keys absent.
- requirements.txt: pin pywebpush==<latest stable at time of writing>.
- tests/test_models/test_push_subscription.py (new).
- tests/test_routes/test_push.py (new).
- tests/test_services/test_push_service.py (new): mock pywebpush; test the 410 / 404
  cleanup and the multi-subscription continue-on-failure path.
- Re-run `python scripts/build_test_template.py` after upgrade (new table; new
  AUDITED_TABLES entry per CLAUDE.md "Tests" section).

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim. Section G ("Migrations") MUST include the
upgrade -> downgrade -> upgrade results.

Specific verification gates for this commit:
- M7-1 through M7-16 per plan Section 9 Commit 7 E table.
- Migration round-trips: flask db upgrade -> flask db downgrade -> flask db upgrade
  clean.
- After upgrade: `python scripts/build_test_template.py` succeeds; entrypoint
  trigger-count health check passes (audit trigger attached to new table).
- `grep -n "send_push" app/` shows callers only in tests (Section 4 will add live
  callers later).
- Manual verification: generate VAPID keys via the new script; add to .env; restart;
  /settings shows the Push Notifications section. Firefox Desktop -- enable push;
  permission prompt; subscription appears in list. `flask shell` ->
  `from app.services.push_service import send_push; send_push(user.id, {'title':
  'Test', 'body': 'Hello'})` -- native Firefox notification appears. Firefox Android
  -- same flow; Android system notification appears. Firefox iOS -- subscribe
  attempt fails (WebKit Push requires Safari install); failure reported gracefully
  with "Install via Safari to enable notifications on iOS" message (Commit 8
  surfaces this; Commit 7 only needs to fail without crashing).
- Full suite green.

If anything is unclear, ASK. Do NOT add pywebpush to requirements.txt without
confirmed developer approval.
```

---

### Commit 8 -- `feat(pwa): install prompt with Firefox iOS Safari fallback`

**Prereqs on dev:** Commit 6.

```text
You are executing Commit 8 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 8" A-H;
  Section 1 hard rules)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/templates/base.html (the existing manifest + apple-mobile-web-app meta tags
  at :21-26)
- @app/static/manifest.json (icons inventory)
- @app/static/js/bottom_sheet.js (the primitive from Commit 1)

Objective: add a one-time, mobile-only "Install to Home Screen" prompt. On Firefox
Android, the prompt directs the user to the URL bar menu's "Install" option. On Firefox
iOS, the prompt explains that PWA install requires Safari (Apple platform constraint --
Firefox iOS uses WebKit but cannot install PWAs because the Add to Home Screen flow
is owned by Safari). Dismissable; localStorage flag suppresses re-show. Appears 3 days
after first visit, not on initial interaction.

Files this commit touches:
- app/templates/partials/_install_prompt.html (new): bottom sheet content; platform-
  conditional copy.
- app/static/js/install_prompt.js (new): UA detection (FxiOS for Firefox iOS), 3-day
  delay, localStorage dismiss flag.
- app/templates/base.html: include the prompt partial on mobile only (server-side
  mobile detection); the partial itself is hidden by default and shown via JS.
- app/static/css/mobile_components.css: prompt styles (reuses bottom sheet primitive).

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M8-1 through M8-4 per plan Section 9 Commit 8 E table.
- Manual verification: Firefox Desktop -- no prompt; URL bar install icon works.
  Firefox Android (fresh profile) -- shift system clock +3 days; reload; prompt
  appears with "Install via menu" instructions; install via menu; PWA installs;
  launches from home screen. Firefox iOS (fresh profile) -- same time-shift; prompt
  appears with "Install requires Safari" explanation; URL copy works; document
  Firefox iOS limitation in section A.
- Full suite green.

If anything is unclear, ASK.
```

---

## Phase C -- Pattern rollout (Commits 9-14)

### Commit 9 -- `feat(mobile): sticky goal-progress strip on /savings`

**Prereqs on dev:** Commit 2 (the strip component).

```text
You are executing Commit 9 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 9" A-H;
  Section 1 hard rules; Section 4 pattern map)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_plan.md (Commit 6 -- /savings routed
  through balance_resolver; do not regress this)
- @app/templates/partials/_sticky_summary.html (the component from Commit 2)
- @app/services/savings_dashboard_service.py (read in full; this is the canonical
  producer; the strip cells derive from it, not from a parallel query)
- @app/routes/savings.py (read in full; locate the mobile context branch)
- @app/templates/savings/index.html (read in full)

Objective: apply the Commit 2 sticky summary strip to /savings to surface goal-progress
metrics at a glance. Cells: total saved, on-track count, off-track count. All values
derived from savings_dashboard_service (the canonical producer per the financial calc
remediation Commit 6). No parallel math.

Files this commit touches:
- app/templates/savings/index.html: include _sticky_summary.html at the top with
  summary_cells.
- app/routes/savings.py: extend context with summary_cells (same shape as Commit 2).
- tests/test_routes/test_savings.py: assert cells in context and values match
  savings_dashboard_service.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M9-1, M9-2 per plan Section 9 Commit 9 E table.
- `grep -n "current_anchor_\|amount_to_monthly" app/templates/savings/index.html`
  returns empty in the new strip include (no parallel paths).
- HIGH-01 cross-page invariant still green.
- Manual verification: Firefox Android -- /savings shows sticky strip with 3 metrics;
  values match the per-goal section below.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 10 -- `feat(mobile): sticky net-worth strip on /accounts`

**Prereqs on dev:** Commit 2.

```text
You are executing Commit 10 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 10" A-H;
  Section 1 hard rules)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_plan.md (Commit 7 -- /accounts
  routed through balance_resolver; Commit 13 + 15 -- loan_resolver SSOT; do not
  regress)
- @app/templates/partials/_sticky_summary.html (the component from Commit 2)
- @app/services/balance_resolver.py and @app/services/loan_resolver.py (canonical
  producers)
- @app/routes/accounts.py (read in full; locate the mobile context branch)
- @app/templates/accounts/index.html (read in full)

Objective: apply the Commit 2 sticky summary strip to /accounts to surface net-worth at
a glance. Cells: total net worth, liquid total, debt total. All values flow through
balance_resolver / loan_resolver (hard rule 1; HIGH-01 lock).

Files this commit touches:
- app/templates/accounts/index.html: include _sticky_summary.html.
- app/routes/accounts.py: extend context with summary_cells.
- tests/test_routes/test_accounts.py: assert cells match canonical producers.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M10-1, M10-2 per plan Section 9 Commit 10 E table.
- `grep -n "current_anchor_\|current_principal" app/templates/accounts/index.html`
  returns empty in the new strip include.
- HIGH-01 cross-page invariant still green.
- Manual verification: Firefox Android -- /accounts shows sticky strip; values match
  the grid's view of the same accounts.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 11 -- `feat(mobile): sticky payoff-status strip on /loan dashboard`

**Prereqs on dev:** Commit 2. **Unblocks:** Commit 16.

```text
You are executing Commit 11 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 11" A-H;
  Section 1 hard rules)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_plan.md (Commit 13 -- LoanState SSOT)
- @docs/plans/2026-05-21-amortization-engine-split-replay-projection.md (the recent
  loan resolver consolidation; LoanState is the source of truth for current_balance,
  monthly_payment, projected_payoff_date)
- @app/services/loan_resolver.py (read LoanState in full)
- @app/routes/loan.py (read the dashboard route in full; locate the mobile branch and
  the existing summary block)
- @app/templates/loan/dashboard.html (read in full)
- @app/templates/partials/_sticky_summary.html (the component from Commit 2)

Objective: replace the visually heavy /loan dashboard mobile summary block with the
standard sticky strip. Cells: current balance, monthly payment, projected payoff date.
All values derived from loan_resolver.LoanState (hard rule 1). Desktop layout unchanged.

Files this commit touches:
- app/templates/loan/dashboard.html: include _sticky_summary.html on mobile branch;
  remove the heavy summary block from the mobile branch only.
- app/routes/loan.py: extend mobile dashboard context with summary_cells from LoanState.
- tests/test_routes/test_loan.py: assert cells match LoanState.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M11-1, M11-2 per plan Section 9 Commit 11 E table.
- `grep -n "current_principal\|interest_rate" app/templates/loan/dashboard.html`
  shows no new direct reads in the mobile branch (LoanState only).
- F-21 / unified loan figures tests still green
  (`./scripts/test.sh tests/test_integration/test_loan_unified_figures.py -v`).
- Manual verification: Firefox Android -- /loan dashboard shows the strip; chart and
  tabs fit under it without horizontal page scroll (Commit 16 will polish the chart
  further -- this commit only verifies the strip lands cleanly).
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 12 -- `feat(mobile): swipe-to-act on template list and bill calendar`

**Prereqs on dev:** Commit 3 (the swipe vocabulary).

```text
You are executing Commit 12 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 12" A-H;
  Section 1 hard rules; Section 2 D-A swipe convention)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/static/js/touch_gestures.js (attachSwipe from Commit 1)
- @app/templates/partials/_status_action_sheet.html (the menu partial from Commit 3;
  reusable where applicable)
- @app/templates/templates/index.html (the template list; re-grep current path)
- @app/templates/calendar/*.html (the bill calendar surfaces; re-grep)
- @app/routes/templates.py and @app/routes/calendar.py (the existing endpoints; do not
  add new ones)
- @app/services/state_machine.py (allowed_transitions for the bill calendar case)

Objective: apply the Commit 3 swipe-to-act vocabulary outside the grid so gesture
behavior is consistent across the app. Template list: right-swipe = archive (active) /
unarchive (archived); left-swipe = action menu (delete, duplicate, edit). Bill calendar:
right-swipe = mark paid (if pending); left-swipe = action menu (snooze, edit, delete
instance). All actions post to existing endpoints; no new routes.

Files this commit touches:
- app/templates/templates/index.html: data-swipe-actionable on rows;
  data-swipe-allow-archive / -unarchive based on row state.
- app/templates/calendar/*.html (re-grep): data-swipe-actionable on event entries.
- app/static/js/swipe_list.js (new) OR extend mobile_grid.js (decide at edit time;
  prefer the new file for separation if more than one consumer): the swipe wiring.
- Per-surface action sheet partials only if the existing _status_action_sheet.html
  does not fit (e.g., template list needs delete + duplicate, not status transitions).
  Reuse where applicable; create new partial named _template_action_sheet.html or
  _calendar_action_sheet.html if needed.
- tests/test_routes/test_templates.py and tests/test_routes/test_calendar.py: assert
  data-swipe-actionable presence; existing endpoint tests unchanged (regression).

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M12-1 through M12-4 per plan Section 9 Commit 12 E table.
- `grep -n "Status\." app/static/js/swipe_list.js` returns empty (no JS state machine;
  server-passed allowed actions, same pattern as Commit 3).
- Manual verification: Firefox Android -- right-swipe on template row archives; visual
  indicator follows finger; release commits or snaps back. Same on calendar event for
  mark paid. Left-swipe opens the appropriate action menu per surface.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 13 -- `feat(mobile): global FAB for quick-add from any page`

**Prereqs on dev:** Commit 4 (the quick-add bottom sheet).

```text
You are executing Commit 13 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 13" A-H;
  Section 1 hard rules; Section 4 "single FAB" resolution of Q-M1)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/templates/base.html (read in full; identify where to include the global FAB)
- @app/templates/partials/_fab.html and @app/templates/partials/_quick_add_sheet.html
  (the partials from Commit 4)
- @app/templates/grid/_mobile_grid.html (remove the grid-scoped FAB include in this
  commit; the global FAB will cover the grid case too)
- @app/static/js/mobile_grid.js (the grid-scoped quick-add handler; extract or move
  to a top-level module so other pages can use it)

Objective: promote the Commit 4 grid-scoped FAB to a global mobile-only component in
base.html so quick-add is reachable from any page (Q-M1 resolution: the global FAB
replaces the grid FAB; no double-render). Mobile-only via server-side detection AND
Bootstrap d-md-none utility (belt + suspenders).

Files this commit touches:
- app/templates/base.html: include _fab.html inside a <div class="d-md-none"> wrapper
  on mobile.
- app/templates/grid/_mobile_grid.html: REMOVE the grid-scoped FAB include.
- app/static/js/quick_add.js (new, extracted from mobile_grid.js): top-level module
  for the FAB click -> openSheet handler so any page can use it.
- app/static/js/mobile_grid.js: remove the inlined FAB handler if present (now lives
  in quick_add.js).
- tests/test_routes: assert FAB renders on every mobile page (sample: /savings,
  /accounts, /loan, /calendar) and not on desktop.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M13-1 through M13-4 per plan Section 9 Commit 13 E table.
- M13-3 specifically: exactly one FAB element on the mobile grid (no duplicate).
- Manual verification: Firefox Android -- FAB visible on grid, /savings, /accounts,
  /calendar, /loan; tap opens quick-add; submit creates a transaction; transaction
  appears in grid. Firefox Desktop -- no FAB visible. Inspect mobile grid DOM -- one
  .fab element only.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 14 -- `feat(mobile): align companion view with v2 patterns`

**Prereqs on dev:** Commits 1, 4 (touch tokens, FAB, bottom sheet primitives).

```text
You are executing Commit 14 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 14" A-H;
  Section 1 hard rules; Section 2 D-G -- align, do not rewrite)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/templates/companion/index.html and @app/templates/companion/_transaction_card.html
  (the existing mobile-first companion view from commit 2072995; read both in full)
- @app/static/css/mobile_components.css (the design tokens from Commit 1)
- @app/static/js/touch_gestures.js (attachSwipe from Commit 1)
- The existing companion JS (re-grep paths)

Objective: align the companion view (commit 2072995) with the v2 vocabulary. Adopt the
v2 design tokens (--touch-target-min, --safe-area-*, --fab-size), the attachSwipe
utility for any inline swipe code present, and the bottom-sheet primitive for any inline
sheet code. NO new functional behavior -- this commit is strictly visual and structural
consistency. Existing companion tests must pass unchanged.

Files this commit touches:
- app/templates/companion/index.html and _transaction_card.html: adopt v2 tokens; swap
  any inline swipe wiring for attachSwipe imports.
- app/static/css/mobile_components.css: companion-specific selectors only if needed.
- The companion JS file(s): swap inline gestures for the shared utility.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M14-1, M14-2 per plan Section 9 Commit 14 E table.
- Existing companion test suite passes unchanged
  (`./scripts/test.sh tests/test_routes/test_companion.py -v`).
- `grep -nE "addEventListener\(['\"]touchstart" app/templates/companion/` returns empty
  (any inline touch handling has been replaced by the shared utility).
- Manual verification: Firefox Android -- companion view looks visually consistent with
  the rest of the mobile app; swipe gestures match.
- Full suite green.

If anything is unclear, ASK.
```

---

## Phase D -- Long-tail polish (Commits 15-16)

### Commit 15 -- `feat(mobile): mobile-first form patterns for settings/salary/calibration`

**Prereqs on dev:** Commit 1 (design tokens).

```text
You are executing Commit 15 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 15" A-H;
  Section 1 hard rules)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/templates/settings/*.html (read each in full)
- @app/templates/salary/*.html (read each in full; salary/tax_config.html is a thin
  wrapper per the v1 plan D-4 note -- mind the indirection)
- @app/templates/calibration/*.html (read each in full)
- @app/static/css/mobile_components.css (the design tokens from Commit 1)

Objective: apply four mobile form patterns to long forms (settings, salary config,
calibration): sticky save bar (always-visible primary save + cancel at bottom);
progressive disclosure (collapsible <details> for advanced sections); native input types
(inputmode="decimal" for money, "numeric" for integers, "email" for email, "tel" for
TOTP backup codes); larger touch targets on form controls. <details> / <summary> is
native HTML so basic open/close needs no JS.

Files this commit touches:
- app/templates/settings/*.html: per-file edits (sticky save, progressive sections,
  input modes).
- app/templates/salary/*.html: same.
- app/templates/calibration/*.html: same.
- app/templates/partials/_sticky_save_bar.html (new): reusable sticky save bar.
- app/static/css/mobile_components.css: .sticky-save-bar, .progressive-section styles.
- app/static/js/forms.js (new or extend): collapse/expand handling for any custom
  progressive sections not using <details>; enable/disable sticky save based on form
  dirty state.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M15-1 through M15-3 per plan Section 9 Commit 15 E table.
- `grep -nE 'inputmode=\"(decimal|numeric|email|tel)\"'
  app/templates/settings app/templates/salary app/templates/calibration`
  shows the appropriate inputmodes are applied.
- Existing form tests pass unchanged (regression).
- Manual verification: Firefox Android -- each form is comfortable to fill; save button
  always visible at bottom (safe-area-aware); advanced sections collapsed by default;
  money fields trigger the decimal keypad.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 16 -- `feat(mobile): mobile-first loan dashboard chart and tab layout`

**Prereqs on dev:** Commit 11 (the strip frees the top of the viewport).

```text
You are executing Commit 16 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-7; Section 9 "Commit 16" A-H;
  Section 1 hard rules)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @app/templates/loan/dashboard.html (read in full)
- @app/static/js/<loan chart init> (re-grep for the chart initialization)
- @app/static/css/mobile_components.css (the design tokens from Commit 1)

Objective: redesign the /loan dashboard chart and tab layout for mobile. With Commit 11
freeing the top of the viewport, this commit makes the chart fit the viewport without
horizontal scroll using CSS container queries (Firefox 110+, WebKit 16+), and extends
the v1 commit ede7014's tab scrolling pattern to all tab sets on the page if more than
one exists.

Files this commit touches:
- app/templates/loan/dashboard.html: mobile-specific chart container with aspect-ratio
  CSS via container queries; tab visual updates.
- app/static/css/mobile_components.css: chart container styles; tab pill / scroll
  styles.
- app/static/js/<loan chart>: pass a device-pixel-aware width on init if required by
  the chart library.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- M16-1 through M16-3 per plan Section 9 Commit 16 E table.
- Existing loan chart tests pass unchanged (M16-3 is the regression check).
- Manual verification: Firefox Android -- /loan dashboard chart fits viewport width
  with no horizontal page scroll; tabs scroll horizontally within their container
  without consuming vertical space; container query updates as the viewport rotates.
  Firefox iOS -- same; document any WebKit container-query degradation.
- Full suite green.

If anything is unclear, ASK.
```

---

## Phase E -- Final gate

### Commit 17 -- `chore(release): mobile v2 full gate + Firefox parity verification appendix`

**Prereqs on dev:** 1 through 16. **Closes:** the mobile v2 plan; appends the verification
log to `implementation_plan_mobile_v2.md`.

```text
You are executing Commit 17 of the Shekel mobile-first v2 implementation in a fresh
session. Work in the project root on the dev branch. This is the final gate.

Required reading -- in full:
- @docs/implementation_plan_mobile_v2.md (Sections 0-9 -- the entire plan; Section 9
  "Commit 17" A-H; Section 10 "Verification" -- the end-to-end walkthroughs to run)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/project_roadmap_v5.md (Appendix A -- you will append "Mobile-First v2" with the
  commit range)

Objective: bookkeeping commit. Confirms every Phase A-D commit landed cleanly, runs the
full acceptance gate, verifies the Firefox parity matrix across Desktop / Android /
iOS, and appends the verification log to the plan document under Appendix A. No code
changes.

Files this commit touches:
- docs/implementation_plan_mobile_v2.md: append Appendix A "Verification log" filled in
  per the template in Section 10.
- docs/project_roadmap_v5.md: append a new "A.X Mobile-First v2" entry under Appendix A
  with the commit range and completion date.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md AND the mobile
hard rules in @docs/implementation_plan_mobile_v2.md Section 1. End with the work
summary using labels A through M verbatim. Section E (test summaries) must include the
final `N passed` line from the full suite verbatim.

Gate checklist (every step must pass before committing):
1. `python scripts/build_test_template.py` (Commit 7's migration changed the schema;
   rebuild required if not already rebuilt on this branch).
2. `./scripts/test.sh` -- ends in `N passed`, zero failed/errors/xfailed. Capture the
   final summary line; include in the commit body AND section E of the work summary.
3. `pylint app/ --fail-on=E,F` -- clean, no new warnings vs baseline.
4. `flask db upgrade` -> `flask db downgrade -1` -> `flask db upgrade` for the
   Commit-7 migration -- clean both directions.
5. Manifest icon audit: every size listed in app/static/manifest.json exists in
   app/static/icons/ and renders without 404.
6. The HIGH-01 cross-page balance-equality invariant
   (tests/test_integration/test_balance_consistency.py) green -- the mobile strips
   did not introduce a parallel calc path.
7. F-21 unified loan figures invariant green (Commit 11 did not regress loan_resolver
   SSOT).
8. Walk Section 10 of the plan: Phase A end-to-end (steps 1-5), Phase B (steps 6-8),
   Phase C (steps 9-11), Phase D (steps 12-13) on Firefox Desktop, Firefox Android,
   and Firefox iOS. Record results per the Appendix A template.
9. `git status` shows only the docs files changed.

Specific verification gates for this commit:
- Firefox Desktop (Gecko) -- every Phase A/C/D commit's Section F manual verification
  re-run; record pass/fail with notes.
- Firefox Android (GeckoView) -- same; this is the developer's primary target device
  (his wife uses Firefox Android exclusively per the plan Section 0 context).
- Firefox iOS (WebKit) -- same; document expected WebKit limitations explicitly
  (push subscription requires Safari-installed PWA; backdrop blur may degrade; install
  prompt routes to Safari).
- After this commit lands, Section 4 (Notifications) of project_roadmap_v5.md can run
  against fully-laid PWA + push infrastructure.

If anything is unclear, ASK. Do not edit anything other than the two docs files in
section 7 -- this commit is gate + bookkeeping, no code.
```
