/**
 * swipe.js -- Transitional no-op stub.
 *
 * The swipe-to-mark-paid affordance was removed (commit 07047c3,
 * 2026-05-26) after Firefox iOS leaked the reveal button at rest
 * and WebKit browsers layered it behind the amount text mid-swipe.
 * The macro and CSS were updated, but the deployed service worker
 * cached the pre-removal mobile_grid.js as `shekel-static-v1`, and
 * that cached file calls window.attachSwipeAction(document, ...)
 * at module-init.  Without this stub the call throws a TypeError
 * (function undefined), halting script execution before the
 * tap-to-toggle action-bar click handler registers -- which the
 * user observed as "nothing happens when I tap an item in the
 * grid".
 *
 * The stub defines window.attachSwipeAction as a no-op so the
 * legacy call site is harmless.  The SW cache name was
 * simultaneously bumped to `shekel-static-v2` so the SW activate
 * handler evicts the old cache on the next page visit; once that
 * propagates and all clients fetch the post-removal mobile_grid.js
 * (which no longer calls attachSwipeAction at all), this stub is
 * dead weight.
 *
 * TODO: delete this file (and its script tag in base.html) once
 * the SW cache bump has propagated -- safe ~2 weeks after deploy.
 */
(function () {
    'use strict';
    window.attachSwipeAction = function () {};
})();
