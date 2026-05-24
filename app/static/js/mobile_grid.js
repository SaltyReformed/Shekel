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

    // Swipe-left to reveal Mark Paid button on a card (Commit 9 of
    // the mobile-first v3 implementation).  Touch listeners are
    // passive per R-8 of the plan -- they cannot preventDefault, so
    // they cannot block vertical scroll.  The `Math.abs(dy) >
    // Math.abs(dx)` guard inside touchmove cancels swipe tracking
    // when the gesture is dominantly vertical so an ordinary
    // page-scroll that happens to begin on a card stays a scroll.
    // The 50 px horizontal threshold matches the existing period-
    // nav swipe at lines 75-87 above -- R-8 alignment, so both
    // gestures feel identical under the finger.
    //
    // Per-card swipe state lives on the card element itself
    // (`card._swipeStartX` / `_swipeStartY`).  A single module-level
    // pair would corrupt the anchor if the user accidentally
    // double-taps two cards in quick succession.
    //
    // After touchend changes a card's `.swiped` class, the browser
    // dispatches a synthetic click on the touched element.  Without
    // intervention that click would land in the tap-to-toggle
    // handler below and either (a) immediately reopen the action bar
    // alongside the just-revealed Paid button or (b) immediately
    // close the swipe via the "tap closes swipe" branch in the
    // click handler.  `_suppressNextCardClick` is the one-shot flag
    // that absorbs that synthetic click; the timeout backstop
    // clears it after 400 ms so a swipe whose synthetic click never
    // fires (cancelled by another touch, no-touch fallback) does
    // not poison a later genuine tap.
    //
    // The suppression is SCOPED to the same card the swipe just
    // changed (`_suppressClickFor`).  Without this scoping, a real
    // outside-tap that arrives inside the 400 ms window would be
    // wrongly consumed -- the spec requires "tap outside un-swipes"
    // to work, so the flag only applies to clicks whose nearest
    // ancestor `.mobile-txn-card` matches the swiped card.  Clicks
    // on body / any other element fall through to the existing
    // "any swiped? close all" branch in the click handler.
    var _suppressNextCardClick = false;
    var _suppressClickFor = null;
    var _suppressTimeoutId = null;

    function _armSwipeClickSuppression(card) {
        _suppressNextCardClick = true;
        _suppressClickFor = card;
        if (_suppressTimeoutId !== null) {
            clearTimeout(_suppressTimeoutId);
        }
        _suppressTimeoutId = setTimeout(function () {
            _suppressNextCardClick = false;
            _suppressClickFor = null;
            _suppressTimeoutId = null;
        }, 400);
    }

    document.addEventListener('touchstart', function (e) {
        var card = e.target.closest('.mobile-txn-card');
        if (!card) return;
        card._swipeStartX = e.touches[0].clientX;
        card._swipeStartY = e.touches[0].clientY;
    }, { passive: true });

    document.addEventListener('touchmove', function (e) {
        var card = e.target.closest('.mobile-txn-card');
        if (!card || card._swipeStartX === undefined) return;
        var dx = e.touches[0].clientX - card._swipeStartX;
        var dy = e.touches[0].clientY - card._swipeStartY;
        // Vertical-dominant motion cancels swipe tracking so a
        // standard page scroll wins the gesture.
        if (Math.abs(dy) > Math.abs(dx)) {
            card._swipeStartX = undefined;
        }
    }, { passive: true });

    document.addEventListener('touchend', function (e) {
        var card = e.target.closest('.mobile-txn-card');
        if (!card || card._swipeStartX === undefined) return;
        var dx = e.changedTouches[0].clientX - card._swipeStartX;
        card._swipeStartX = undefined;

        if (dx < -50) {
            // Swipe-left past threshold -- honour only when the card
            // actually has a swipe-action button sibling.  Settled
            // txns (render_row_card guards on `txn.status.is_settled`)
            // emit no button because mark_done would reject the
            // request with 400; a swipe on those rows stays a no-op
            // rather than revealing an empty 80 px well.
            var wrapper = card.closest('.mobile-card-wrapper');
            if (!wrapper || !wrapper.querySelector('.swipe-action-mark-paid')) {
                return;
            }
            // Close any other open swipe so the user always knows
            // which row a tap on the revealed button targets.
            document.querySelectorAll('.mobile-txn-card.swiped').forEach(function (other) {
                if (other !== card) other.classList.remove('swiped');
            });
            card.classList.add('swiped');
            _armSwipeClickSuppression(card);
        } else if (dx > 50 && card.classList.contains('swiped')) {
            // Swipe-right on a swiped card un-swipes it.
            card.classList.remove('swiped');
            _armSwipeClickSuppression(card);
        }
    }, { passive: true });

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
    // Guards (top-to-bottom, short-circuit on the first hit):
    //   - taps on `.swipe-action-mark-paid` are commit edges; the
    //     button's `hx-post` issues the mark-done request directly
    //     and this handler must not intercept.
    //   - taps that originated inside the action bar itself are
    //     ignored (otherwise a tap on [Mark Paid] would re-toggle
    //     the bar shut as the bubble climbed past the card).
    //   - the synthetic click that follows a swipe touchend is
    //     suppressed (see `_suppressNextCardClick`); without this
    //     guard adding `.swiped` would be undone immediately by
    //     the swiped-card branch below.
    //   - while any card is swiped open the next click anywhere
    //     un-swipes it and short-circuits -- "tap outside to
    //     dismiss" and "tap inside to dismiss" share the same
    //     branch so the user is never simultaneously presented
    //     with a swipe well and an open action bar on the same
    //     card.
    //   - `data-mobile-txn-id` scopes the selector to real txn
    //     cards so the group-header `<li>` (no data attr in the
    //     owner render path) cannot accidentally trigger.
    //   - missing wrapper / bar / Bootstrap is a hard no-op rather
    //     than a console error -- the action bar's absence on a
    //     server-render path (companion read-only edge cases,
    //     test scaffolding) should not break tap handling
    //     elsewhere on the page.
    document.addEventListener('click', function(e) {
        if (e.target.closest('.swipe-action-mark-paid')) return;
        if (e.target.closest('.mobile-card-action-bar')) return;

        if (_suppressNextCardClick
                && e.target.closest('.mobile-txn-card') === _suppressClickFor) {
            _suppressNextCardClick = false;
            _suppressClickFor = null;
            if (_suppressTimeoutId !== null) {
                clearTimeout(_suppressTimeoutId);
                _suppressTimeoutId = null;
            }
            return;
        }

        var anySwiped = document.querySelectorAll('.mobile-txn-card.swiped');
        if (anySwiped.length > 0) {
            anySwiped.forEach(function (c) { c.classList.remove('swiped'); });
            return;
        }

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
