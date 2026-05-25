/**
 * swipe.js -- Shared swipe-left-to-reveal helper for mobile transaction
 * cards.
 *
 * Mobile-first v3 plan Commit 13 factors the swipe-action gesture out
 * of ``mobile_grid.js`` (Commit 9, owner-only) into this shared module
 * so the companion view (``companion.js``) can adopt the same gesture
 * without duplicating ~80 lines of touch handling and click-suppression
 * state.
 *
 * Exports one global function:
 *
 *     attachSwipeAction(root, { onLeftSwipe, threshold = 50 })
 *
 * The function delegates touch + click handling on ``root`` (typically
 * ``document``) so cards inserted later via HTMX swaps are handled
 * without re-attachment.  Calling ``attachSwipeAction`` more than once
 * on the same root creates multiple sets of listeners; the host page
 * should call it exactly once at module-init time.
 *
 * Behavior:
 *
 *   - Swipe-left past ``threshold`` px on a ``.mobile-txn-card`` whose
 *     enclosing ``.mobile-card-wrapper`` contains a
 *     ``.swipe-action-mark-paid`` button: the card gains a ``.swiped``
 *     class (CSS translates it -80px in app.css) revealing the button.
 *     Cards without the button (settled rows -- see the
 *     ``_grid_row_macros.html`` ``txn.status.is_settled`` guard) are a
 *     no-op so a swipe on those rows does not reveal an empty well.
 *   - Swipe-right past ``threshold`` on a swiped card: removes ``.swiped``.
 *   - Vertical-dominant motion (``|dy| > |dx|`` during touchmove)
 *     cancels swipe tracking so an ordinary page scroll wins the
 *     gesture (R-8 of the plan).
 *   - Touch listeners are ``{ passive: true }`` -- they cannot
 *     ``preventDefault``, which is the trade-off that lets the swipe
 *     coexist with vertical scrolling.
 *   - The synthetic click that the browser fires after touchend is
 *     suppressed (scoped to the swiped card) so a tap-to-toggle
 *     handler attached elsewhere does not double-act on the gesture.
 *   - Tapping anywhere outside the revealed ``.swipe-action-mark-paid``
 *     button while a card is swiped un-swipes all cards and stops
 *     event propagation so the host page's tap-to-toggle handler does
 *     not also fire.  Tapping the button itself falls through (the
 *     button's own ``hx-post`` handler commits the action).
 *
 * The optional ``onLeftSwipe(card)`` callback fires after the class is
 * applied, giving the host page a hook to wire e.g. haptic feedback
 * (OPT-M2 in the plan).  No companion of this commit uses the
 * callback yet; it is reserved for future enhancements.
 */
(function () {
    'use strict';

    /**
     * Attach swipe-action handling to a delegation root.
     *
     * @param {EventTarget} root - typically ``document``.  The
     *     touch + click listeners are attached here; the gesture
     *     matches descendants of ``root`` that are ``.mobile-txn-card``
     *     inside a ``.mobile-card-wrapper`` with a
     *     ``.swipe-action-mark-paid`` button.
     * @param {Object} options
     * @param {Function} [options.onLeftSwipe] - optional callback,
     *     receives the swiped card element.
     * @param {number} [options.threshold=50] - horizontal swipe
     *     threshold in CSS pixels (matches the period-nav swipe at
     *     ``mobile_grid.js`` init() per R-8 of the plan).
     */
    function attachSwipeAction(root, options) {
        var opts = options || {};
        var threshold = typeof opts.threshold === 'number' ? opts.threshold : 50;
        var onLeftSwipe = typeof opts.onLeftSwipe === 'function' ? opts.onLeftSwipe : null;

        // Per-card suppression state lives on the swipe-arming closure
        // rather than the card so concurrent swipes on different cards
        // do not clobber each other's flags.  ``_suppressClickFor``
        // scopes the suppression to the SAME card the swipe just
        // changed; a tap on a different card during the 400 ms window
        // still falls through to the tap-to-toggle handler.
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

        function _clearSwipeClickSuppression() {
            _suppressNextCardClick = false;
            _suppressClickFor = null;
            if (_suppressTimeoutId !== null) {
                clearTimeout(_suppressTimeoutId);
                _suppressTimeoutId = null;
            }
        }

        root.addEventListener('touchstart', function (e) {
            var card = e.target.closest('.mobile-txn-card');
            if (!card) return;
            card._swipeStartX = e.touches[0].clientX;
            card._swipeStartY = e.touches[0].clientY;
        }, { passive: true });

        root.addEventListener('touchmove', function (e) {
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

        root.addEventListener('touchend', function (e) {
            var card = e.target.closest('.mobile-txn-card');
            if (!card || card._swipeStartX === undefined) return;
            var dx = e.changedTouches[0].clientX - card._swipeStartX;
            card._swipeStartX = undefined;

            if (dx < -threshold) {
                // Swipe-left past threshold -- honour only when the
                // card has a swipe-action button sibling.  Settled
                // rows emit no button; a swipe on those stays a no-op.
                var wrapper = card.closest('.mobile-card-wrapper');
                if (!wrapper || !wrapper.querySelector('.swipe-action-mark-paid')) {
                    return;
                }
                // Close any other open swipe so only one well shows.
                document.querySelectorAll('.mobile-txn-card.swiped').forEach(function (other) {
                    if (other !== card) other.classList.remove('swiped');
                });
                card.classList.add('swiped');
                _armSwipeClickSuppression(card);
                if (onLeftSwipe) onLeftSwipe(card);
            } else if (dx > threshold && card.classList.contains('swiped')) {
                // Swipe-right on a swiped card un-swipes it.
                card.classList.remove('swiped');
                _armSwipeClickSuppression(card);
            }
        }, { passive: true });

        // Capture-phase click handler -- runs BEFORE any bubble-phase
        // tap handlers on descendants.  Two roles:
        //   1. Absorb the synthetic click that follows touchend on
        //      the just-swiped card (so tap-to-toggle does not also
        //      fire on the same gesture).
        //   2. Un-swipe any open card when the tap landed outside
        //      the revealed ``.swipe-action-mark-paid`` button, and
        //      stopImmediatePropagation so the host's bubble-phase
        //      tap-to-toggle handler does not also act on the click.
        // A tap on the swipe-action button itself falls through both
        // branches so the button's ``hx-post`` handler commits the
        // mark-paid request.
        root.addEventListener('click', function (e) {
            if (e.target.closest('.swipe-action-mark-paid')) return;

            if (_suppressNextCardClick
                    && e.target.closest('.mobile-txn-card') === _suppressClickFor) {
                _clearSwipeClickSuppression();
                e.stopImmediatePropagation();
                return;
            }

            var anySwiped = document.querySelectorAll('.mobile-txn-card.swiped');
            if (anySwiped.length > 0) {
                anySwiped.forEach(function (c) { c.classList.remove('swiped'); });
                e.stopImmediatePropagation();
            }
        }, true);
    }

    // Expose on window so the no-build script-tag setup can call it
    // from ``mobile_grid.js`` and ``companion.js`` (no ES module
    // tooling: CLAUDE.md "No JS frameworks. No new CSS framework").
    window.attachSwipeAction = attachSwipeAction;
})();
