/**
 * mobile_grid.js -- Period navigation, swipe gestures, and
 * tap-to-toggle action-bar handling for the mobile card-based budget
 * grid.
 *
 * Tap on a `.mobile-txn-card` no longer opens the bottom sheet
 * directly (Commit 7 of the mobile-first v3 implementation).  It now
 * toggles a sibling `.mobile-card-action-bar` collapse via the
 * Bootstrap Collapse API; the bar exposes `[Mark Paid]`,
 * `[Edit Amount]`, and `[Open Full]` buttons.  The bottom sheet is
 * still reachable, but explicitly via the `[Open Full]` button, which
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
        activateTabFromHash();

        var panels = document.querySelectorAll('.mobile-period-panel');
        if (!panels.length) return;

        var currentIndex = 0;
        var prevBtn = document.getElementById('mobile-prev-btn');
        var nextBtn = document.getElementById('mobile-next-btn');

        function updateLabel() {
            var panel = panels[currentIndex];
            var labelEl = document.getElementById('mobile-period-label');
            var rangeEl = document.getElementById('mobile-period-range');
            if (labelEl) labelEl.textContent = panel.dataset.periodLabel;
            if (rangeEl) rangeEl.textContent = panel.dataset.periodRange;
            if (prevBtn) prevBtn.disabled = (currentIndex === 0);
            if (nextBtn) nextBtn.disabled = (currentIndex === panels.length - 1);
        }

        function navigate(delta) {
            var newIndex = currentIndex + delta;
            if (newIndex < 0 || newIndex >= panels.length) return;
            panels[currentIndex].style.display = 'none';
            currentIndex = newIndex;
            panels[currentIndex].style.display = '';
            updateLabel();
        }

        if (prevBtn) prevBtn.addEventListener('click', function() { navigate(-1); });
        if (nextBtn) nextBtn.addEventListener('click', function() { navigate(1); });

        // Swipe detection on the mobile grid container.
        var grid = document.getElementById('mobile-grid');
        if (grid) {
            var touchStartX = 0;
            var touchStartY = 0;

            grid.addEventListener('touchstart', function(e) {
                touchStartX = e.changedTouches[0].clientX;
                touchStartY = e.changedTouches[0].clientY;
            }, { passive: true });

            grid.addEventListener('touchend', function(e) {
                var dx = e.changedTouches[0].clientX - touchStartX;
                var dy = e.changedTouches[0].clientY - touchStartY;
                // Only trigger on horizontal swipes exceeding 50px threshold.
                if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
                    navigate(dx < 0 ? 1 : -1);
                }
            }, { passive: true });
        }

        updateLabel();
    }

    // Tap-to-toggle action bar: delegated click handler for mobile
    // transaction cards (Commit 7).  Tapping a `.mobile-txn-card`
    // expands the sibling `.mobile-card-action-bar` (the per-card
    // [Mark Paid] / [Edit Amount] / [Open Full] row).  At most one
    // bar is open at a time -- opening one collapses any other.
    //
    // Registered at module scope (not inside `init`) so it survives
    // HTMX swaps that re-render parts of the grid: HTMX never
    // re-runs `DOMContentLoaded`, and re-running the inner-listener
    // setup would double-attach the period-nav button handlers.
    // Delegation on `document` is naturally swap-safe because
    // dynamically-inserted descendants bubble through the same
    // listener.
    //
    // Guards:
    //   - taps that originated inside the action bar itself are
    //     ignored (otherwise a tap on [Mark Paid] would re-toggle
    //     the bar shut as the bubble climbed past the card).
    //   - `data-mobile-txn-id` scopes the selector to real txn
    //     cards so the group-header `<li>` (no data attr in the
    //     owner render path) cannot accidentally trigger.
    //   - missing wrapper / bar / Bootstrap is a hard no-op rather
    //     than a console error -- the action bar's absence on a
    //     server-render path (companion read-only edge cases,
    //     test scaffolding) should not break tap handling
    //     elsewhere on the page.
    document.addEventListener('click', function(e) {
        if (e.target.closest('.mobile-card-action-bar')) return;
        var card = e.target.closest('.mobile-txn-card[data-mobile-txn-id]');
        if (!card) return;

        var wrapper = card.closest('.mobile-card-wrapper');
        if (!wrapper) return;
        var bar = wrapper.querySelector('.mobile-card-action-bar');
        if (!bar) return;
        if (typeof bootstrap === 'undefined' || !bootstrap.Collapse) return;

        document.querySelectorAll('.mobile-card-action-bar.show').forEach(function(other) {
            if (other !== bar) {
                bootstrap.Collapse.getOrCreateInstance(other).hide();
            }
        });

        bootstrap.Collapse.getOrCreateInstance(bar).toggle();
    });

    // Sync the card's `aria-expanded` with its action bar's
    // open/closed state.  The card emits `aria-controls="<bar id>"`
    // (set by `render_row_card`); we resolve it back via that
    // attribute rather than DOM proximity so any future trigger
    // pointing at the same bar would also get its aria-expanded
    // maintained.  Bootstrap fires `shown.bs.collapse` /
    // `hidden.bs.collapse` on the collapsed element after its CSS
    // transition completes, which is the right moment to flip the
    // attribute (matches screen-reader expectations for "the
    // disclosure has finished opening").
    function _syncAriaExpanded(barEl, value) {
        if (!barEl || !barEl.id) return;
        var trigger = document.querySelector('[aria-controls="' + barEl.id + '"]');
        if (trigger) trigger.setAttribute('aria-expanded', value);
    }
    document.addEventListener('shown.bs.collapse', function(e) {
        if (!e.target.classList.contains('mobile-card-action-bar')) return;
        _syncAriaExpanded(e.target, 'true');
    });
    document.addEventListener('hidden.bs.collapse', function(e) {
        if (!e.target.classList.contains('mobile-card-action-bar')) return;
        _syncAriaExpanded(e.target, 'false');
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
