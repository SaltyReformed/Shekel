# Mobile-First v3 -- Follow-up Work

Tracks issues identified during the mobile-first v3 implementation
(`docs/implementation_plan_mobile_v3.md`) that are deliberately out of
scope for the commit that surfaced them. Each entry is self-contained
and can be picked up after the originating commit lands.

Cross-references:

- Plan: `docs/implementation_plan_mobile_v3.md`
- Common rules and work-summary format:
  `docs/audits/financial_calculations/remediation_follow_up_common.md`
  (rule 6 routes out-of-scope items to this file)

---

## F-1. Scope the `#mobile-grid` swipe-to-navigate handler to the Plan tab

- **Surfaced during:** Commit 6
  (`feat(mobile-grid): _mobile_this_period.html partial with arrows`).
- **Status:** open. Trivial to fold into Commit 7
  (`feat(mobile-grid): _mobile_plan.html + inline card action bar`),
  which already rewrites `mobile_grid.js` and the Plan tab markup; if
  Commit 7 implementation does not naturally pick it up, fix it as a
  one-line follow-up.

### Problem

`app/static/js/mobile_grid.js:42-60` attaches the horizontal-swipe
listener to `#mobile-grid` (the outer container that wraps both tabs)
rather than to `#mobile-plan`. The handler calls `navigate(delta)`,
which mutates `currentIndex` and `display` on the Plan tab's
`.mobile-period-panel` elements.

With Commit 6's default-tab flip ("This Period" is now active by
default), a horizontal swipe on the This Period tab silently advances
the Plan tab's `currentIndex`. The user sees nothing change because:

1. The This Period tab has no `.mobile-period-panel` elements (the
   partial uses a plain wrapper div by design -- the absence is what
   keeps the existing Plan navigation's `querySelectorAll` from
   skipping indexes).
2. Bootstrap's tab JS hides the Plan tab-pane via `display: none` on
   the outer `.tab-pane` element, so the inner `display: ''` /
   `display: 'none'` writes the `navigate()` function makes on
   `panels[currentIndex]` have no visual effect.

The bug becomes user-visible when the user switches from This Period
to Plan: the Plan tab is now showing a different period than the one
they last saw, with no explicit action that would have moved it.

### Latent before Commit 6

Commit 5 introduced the tab scaffold with a placeholder in This Period
and the existing flow in Plan. The handler was attached to
`#mobile-grid` then too, but a swipe on the placeholder still
advanced Plan -- the placeholder had no other content to compete for
the user's attention, so the gesture was unlikely to be triggered.
Commit 6 turns the This Period tab into the default-active surface
with real period content and prev/next arrows; the swipe-on-card
intent the user might bring to a real period now surfaces the
cross-tab leak.

### Recommended fix (estimated effort: 5 minutes)

In `app/static/js/mobile_grid.js`, replace

```javascript
var grid = document.getElementById('mobile-grid');
if (grid) {
    // ... touchstart / touchend listeners
}
```

with

```javascript
var planPane = document.getElementById('mobile-plan');
if (planPane) {
    // ... same touchstart / touchend listeners on planPane
}
```

`mobile-plan` is the Plan tab-pane; attaching the listeners there
constrains the gesture to the tab the navigation actually drives.
The This Period tab keeps its URL-driven prev/next arrows (Commit 6)
and -- if the user wants a horizontal-swipe shortcut on This Period
later -- a new gesture handler can be added on `#mobile-this-period`
that posts to the same `?periods=1&offset=N±1#this-period` URL the
arrows already use. Out of scope for F-1.

### Test coverage

No new test needed -- the existing
`tests/test_routes/test_grid.py::TestMobileThisPeriodPartial` covers
the partial's structural invariants, and the gesture behavior is
JS-side (no Python test infrastructure for touch events; the project
explicitly excludes this per `docs/implementation_plan_mobile_v3.md`
Section 14, "Out of scope" item 6). Manual verification at 375x812
in Firefox responsive mode is sufficient.

### Why defer

Commit 6's stated scope is the new partial, the include wiring, and
the default-tab flip. Modifying the swipe-handler binding is a
behavior change to `mobile_grid.js` outside that scope. Commit 7
already touches `mobile_grid.js` extensively (per the plan, it adds
the inline action-bar tap-to-toggle handler and removes the existing
tap-to-edit handler at lines 65-75); the swipe-scope fix can land
in the same commit at near-zero marginal cost.

---

## F-2. Containerize the dev Flask server to mirror production

- **Surfaced during:** Commit 6 manual-verification wiring
  (`feat(mobile-grid): _mobile_this_period.html partial with arrows`),
  when the dev Flask binding had to be moved from `127.0.0.1:5000`
  to `172.32.0.1:5000` (the shekel-frontend bridge gateway) so the
  shared nginx container could reach it.
- **Status:** open. Pure dev-environment refactor with no impact
  on the application code path; pick up when the mobile work has
  settled and parity-fidelity is the higher value than iteration
  speed.

### Problem

The dev Flask server runs on the bare host via
`flask run --host 172.32.0.1` while the production Shekel app runs
inside a Docker container behind Gunicorn behind the shared nginx,
on the `shekel-frontend` bridge with the audit triggers, two-role
DB policy, and entrypoint health checks that prod ships with.  Three
classes of drift result:

1. **Process-model drift.** Dev uses Werkzeug's single-threaded
   reloader; prod uses Gunicorn with multiple workers, gthread, the
   `pre_request` / `post_request` hooks, and the entrypoint
   `EXPECTED_TRIGGER_COUNT` health gate.  Concurrency bugs that
   only surface under multi-worker scheduling (per-process caches,
   first-request initialisation races, SIGTERM handling) are
   invisible in dev.
2. **Authorisation drift.** Prod runs the app under the
   least-privilege `shekel_app` Postgres role (cannot drop, alter,
   or replace audit triggers per
   `docs/coding-standards.md` "Audit Triggers").  Dev runs as
   `shekel_user`, the superuser-equivalent that owns the schema.
   Code that accidentally relies on schema-modifying privileges
   works in dev and fails in prod.  The pylint guard catches most
   of this, but a runtime path that issues a DDL statement (e.g.
   a poorly-scoped `CREATE TEMPORARY` cousin) would only surface
   in prod.
3. **Network-trust-boundary drift.** Prod's nginx terminates TLS
   and forwards via the `shekel-frontend` bridge to
   `shekel-prod-app:8000`; the app receives CF-Connecting-IP from
   the cloudflared sidecar and trusts the bridge subnet to inject
   it (`set_real_ip_from 172.32.0.0/24` in nginx.conf).  Dev's
   nginx (via `conf.d/shekel-dev.conf`) proxies to
   `172.32.0.1:5000` which is the bridge gateway, not the bridge
   member -- so `X-Real-IP` and `X-Forwarded-For` arrive from a
   different network position than prod.  Code that branches on
   either header (rate-limit zones, audit user attribution, the
   `shekel_app.current_user_id` session-local) behaves slightly
   differently in dev.

### Recommended approach (estimated effort: half a day)

Add a `dev` profile to `deploy/docker-compose.prod.yml` (or a new
`deploy/docker-compose.dev.yml` keyed on `name: shekel-dev` at the
top) that:

- Builds the same image the prod compose uses, but bind-mounts the
  repo at `/home/shekel/app` so code changes hot-reload through
  Gunicorn's `--reload` flag.
- Joins the same `shekel-frontend` external bridge so the shared
  nginx reaches it via Docker DNS (`shekel-dev-app:8000`) the same
  way it reaches prod (`shekel-prod-app:8000`).
- Uses a sibling Postgres container on a dedicated dev DB so the
  prod DB is untouchable from the dev path.
- Carries the same `cap_drop: [ALL]` / `read_only: true` /
  `tmpfs:` / non-root `user:` hardening per CLAUDE.md "Compose
  conventions" so the dev container has the same attack surface
  as prod.
- Defaults to a different `EXPECTED_TRIGGER_COUNT` only if the
  dev DB intentionally differs; otherwise reuses the same value.

`conf.d/shekel-dev.conf` then proxies to `shekel-dev-app:8000`
instead of `172.32.0.1:5000`, mirroring `shekel.conf`'s shape
exactly.  The `--host 172.32.0.1` workaround on the bare-host
`flask run` is retired; the dev workflow becomes
`docker compose -f deploy/docker-compose.dev.yml up`.

### Test coverage

The full pytest suite already runs against a per-worker template
clone of the prod schema (per `docs/testing-standards.md`).  The
containerised dev environment does not change the test path; it
only changes the manual-on-device verification path.  A new
`tests/test_deploy/test_dev_compose.py` could assert the dev
compose file has the same hardening keys as prod
(`security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`,
`read_only: true`), matching the existing `test_deploy/` audit
pattern.

### Why defer

This is dev/prod parity work, not user-visible product work.  The
mobile-first v3 plan has nineteen commits left after Commit 6; doing
the containerisation now would block them on an unrelated refactor.
After the v3 plan closes (Commit 28), the containerisation makes a
clean self-contained PR with no in-flight grid coupling.

If a mobile commit later in the plan needs Gunicorn-style behaviour
to verify (e.g. cookie binding under workers, SIGTERM behaviour
during the service-worker activate event), promote this item early
rather than papering over the divergence in the dev script.

---

## F-3. Remove `style="min-height: 44px;"` from `_mobile_card_actions.html`

- **Surfaced during:** Commit 9
  (`feat(mobile-grid): swipe-left reveals Mark Paid button on cards`).
- **Status:** open. Pre-existing from Commit 7
  (`feat(mobile-grid): _mobile_plan.html + inline card action bar`).
  Trivial to fold into any future commit that touches
  `_mobile_card_actions.html` (or as a one-line follow-up commit).

### Problem

`app/templates/grid/_mobile_card_actions.html:66`, `:75`, and `:85`
each carry an inline `style="min-height: 44px;"` attribute on the
Mark Paid / Edit Amount / Open Full buttons. The project's CSP
header is `style-src 'self'` (no `'unsafe-inline'`) per
`app/__init__.py:723` and the C-02 inline-style migration noted
in `app.css:1296-1305` removed 92 such attributes precisely so
the policy could stay strict. These three slipped back in.

Browsers honour CSP per Level 3: when `style-src-attr` is unset, it
falls back to `style-src`. So `style-src 'self'` blocks inline
`style="..."` attributes on Firefox and Chromium; the styles fail
to apply and the buttons rely on whatever sizing the surrounding
Bootstrap classes provide. The tests in
`tests/test_integration/test_security_headers.py` lock the header
shape; nothing currently locks "no inline style attrs in any
template", so the regression is silent until someone DOM-inspects
a mobile card and notices the missing min-height.

### Recommended fix (estimated effort: 5 minutes)

Replace the three `style="min-height: 44px;"` attributes with a
single CSS class (e.g. `.btn-touch-44`) added to
`app/static/css/app.css` inside the existing mobile media query
block:

```css
@media (max-width: 767.98px) {
  .btn-touch-44 {
    min-height: 44px;
  }
}
```

and replace each button's `style="..."` with
`class="... btn-touch-44 ..."`. Mirror the convention used by
`.mw-px-*` / `.w-px-*` / `.fs-*` utility classes already
established by the C-02 migration in `app.css`.

### Test coverage

The existing CSP tests in
`tests/test_integration/test_security_headers.py` already lock the
header shape. A new test asserting `style=` does not appear in
`_mobile_card_actions.html` would lock the regression; matches the
existing `test_no_inline_style_on_swipe_action` in
`TestMobileSwipeAction` (Commit 9). One-line pattern:

```python
def test_no_inline_style_on_mobile_card_actions(self):
    src = pathlib.Path(
        "app/templates/grid/_mobile_card_actions.html"
    ).read_text()
    assert "style=" not in src
```

### Why defer

Commit 9's stated scope is the swipe-left gesture: CSS for the
revealed button, JS for the touch handlers, macro change to emit
the button. Refactoring three pre-existing inline style attributes
in a sibling partial is a separate refactor with its own review
surface. The inline styles are currently inert (blocked by CSP) so
the 44 px touch target is not currently enforced on the action-bar
buttons -- a visible regression, but not a new one introduced by
this commit.
