/**
 * mobile_grid.js -- Period navigation, swipe gestures, and
 * tap-to-toggle action-bar handling for the mobile card-based budget
 * grid.
 *
 * Tap on a `.mobile-txn-card` no longer opens the bottom sheet
 * directly (Commit 7 of the mobile-first v3 implementation).  It now
 * toggles a sibling `.mobile-card-expansion` collapse via the
 * Bootstrap Collapse API.  The expansion bundles every per-card
 * detail block: any progress detail, the inline entries list for
 * envelope templates, and the action buttons (`[Mark Paid]`,
 * `[Edit Amount]`, `[Open Full]`).  The bottom sheet is still
 * reachable, but explicitly via the `[Open Full]` button, which
 * carries the `txn-expand-btn` + `data-txn-id` attributes that
 * `grid_edit.js`'s delegated handler picks up.
 */
(function() {
    'use strict';

    // Activate the mobile-grid tab matching the current URL hash.
    // The "This Period" partial's prev/next arrow links carry
    // `#this-period` so a full GET returns to the same tab; the symmetric
    // `#plan` entry lets future links target the Plan tab. Anything else
    // (no hash, an unrelated fragment) leaves the default-active tab
    // alone.
    function activateTabFromHash() {
        var tabIdByHash = {
            '#this-period': 'mobile-tab-this-period',
            '#plan': 'mobile-tab-plan'
        };
        var tabId = tabIdByHash[window.location.hash];
        if (!tabId) return;
        var btn = document.getElementById(tabId);
        if (!btn) return;
        if (typeof bootstrap === 'undefined' || !bootstrap.Tab) return;
        bootstrap.Tab.getOrCreateInstance(btn).show();
    }

    function init() {
        // The Plan tab no longer hosts the panel-swap navigation that
        // used to live here -- it is now a read-only multi-period
        // accordion (Bootstrap `data-bs-parent` handles mutual
        // exclusion declaratively, so no per-card JS is required).
        // Period navigation lives on the This Period tab via URL-
        // driven prev/next arrows + a jump-to <select>, neither of
        // which needs setup here.  This function is reserved for any
        // page-load JS that does need an init hook; today the only
        // such hook is the hash-driven tab activation.
        activateTabFromHash();
    }

    // Tap-to-toggle card expansion: delegated click handler for mobile
    // transaction cards (Commit 7).  Tapping a `.mobile-txn-card`
    // expands the sibling `.mobile-card-expansion` (the per-card
    // panel that bundles progress detail, the envelope entries list,
    // and the [Mark Paid] / [Edit Amount] / [Open Full] action row).
    // At most one expansion is open at a time -- opening one collapses
    // any other.
    //
    // Registered at module scope (not inside `init`) so it survives
    // HTMX swaps that re-render parts of the grid: HTMX never
    // re-runs `DOMContentLoaded`, and re-running the inner-listener
    // setup would double-attach the period-nav button handlers.
    // Delegation on `document` is naturally swap-safe because
    // dynamically-inserted descendants bubble through the same
    // listener.
    //
    // Guards (top-to-bottom, short-circuit on the first hit):
    //   - taps that originated inside the expansion itself are
    //     ignored (otherwise a tap on [Mark Paid] or an entry-list
    //     button would re-toggle the panel shut as the bubble
    //     climbed past the card).
    //   - `data-mobile-txn-id` scopes the selector to real txn
    //     cards so the group-header `<li>` (no data attr in the
    //     owner render path) cannot accidentally trigger; companion
    //     cards omit the attribute so the expansion there stays
    //     collapsed (companions reach Mark Paid through the
    //     companion-specific UI rendered by `companion/index.html`).
    //   - missing wrapper / expansion / Bootstrap is a hard no-op
    //     rather than a console error -- the expansion's absence on
    //     a server-render path (companion read-only edge cases,
    //     test scaffolding) should not break tap handling elsewhere
    //     on the page.
    document.addEventListener('click', function(e) {
        if (e.target.closest('.mobile-card-expansion')) return;

        var card = e.target.closest('.mobile-txn-card[data-mobile-txn-id]');
        if (!card) return;

        var wrapper = card.closest('.mobile-card-wrapper');
        if (!wrapper) return;
        var expansion = wrapper.querySelector('.mobile-card-expansion');
        if (!expansion) return;
        if (typeof bootstrap === 'undefined' || !bootstrap.Collapse) return;

        document.querySelectorAll('.mobile-card-expansion.show').forEach(function(other) {
            if (other !== expansion) {
                bootstrap.Collapse.getOrCreateInstance(other).hide();
            }
        });

        bootstrap.Collapse.getOrCreateInstance(expansion).toggle();
    });

    // Sync the card's `aria-expanded` with its expansion's
    // open/closed state.  The card emits
    // `aria-controls="<expansion id>"` (set by `render_row_card`);
    // we resolve it back via that attribute rather than DOM
    // proximity so any future trigger pointing at the same expansion
    // would also get its aria-expanded maintained.  Bootstrap fires
    // `shown.bs.collapse` / `hidden.bs.collapse` on the collapsed
    // element after its CSS transition completes, which is the right
    // moment to flip the attribute (matches screen-reader
    // expectations for "the disclosure has finished opening").
    function _syncAriaExpanded(expansionEl, value) {
        if (!expansionEl || !expansionEl.id) return;
        var trigger = document.querySelector('[aria-controls="' + expansionEl.id + '"]');
        if (trigger) trigger.setAttribute('aria-expanded', value);
    }
    document.addEventListener('shown.bs.collapse', function(e) {
        if (!e.target.classList.contains('mobile-card-expansion')) return;
        _syncAriaExpanded(e.target, 'true');
    });
    document.addEventListener('hidden.bs.collapse', function(e) {
        if (!e.target.classList.contains('mobile-card-expansion')) return;
        _syncAriaExpanded(e.target, 'false');
    });

    // Jump-to-period <select> submit (Commit 10 of the mobile-first
    // v3 implementation).  The select sits inside the "This Period"
    // tab-pane (`#mobile-this-period`) and lists every visible
    // pay period; picking a non-current option fires `change`, which
    // submits the parent form as a full GET to
    // `/grid?periods=1&offset=N`.  An inline `onchange="this.form.submit()"`
    // handler would work and CSP allows inline event handlers under
    // the current policy, but the delegated listener is the documented
    // project convention per CLAUDE.md "No inline scripts" -- one
    // handler hosted in this module file, registered once at module
    // scope so it survives HTMX swaps that re-render the partial.
    //
    // The `#mobile-this-period` scope is the load-bearing guard:
    // without it any future `select[name="offset"]` elsewhere on the
    // page (e.g. a desktop-only jump-to selector) would trigger the
    // same submit path and double-submit.
    document.addEventListener('change', function(e) {
        if (e.target.matches('select[name="offset"]') &&
                e.target.closest('#mobile-this-period')) {
            e.target.form.submit();
        }
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
