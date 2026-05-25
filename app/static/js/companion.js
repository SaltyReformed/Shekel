/**
 * Companion view -- post-action reload and swipe-action wiring.
 *
 * The mark_done endpoint returns grid cell HTML and an HX-Trigger
 * header with "gridRefresh".  The companion card uses hx-swap="none"
 * to discard the response body, but HTMX still processes the trigger
 * header and fires a "gridRefresh" event on the document body.
 *
 * This listener catches that event and reloads the page so the card
 * reflects the updated status (Paid indicator, hidden Mark as Paid
 * button, etc.).
 */
document.body.addEventListener("gridRefresh", function () {
    window.location.reload();
});

/**
 * Also reload on balanceChanged events, which fire when entries
 * are created, updated, or deleted.  This keeps the companion
 * card progress indicators up to date.
 */
document.body.addEventListener("balanceChanged", function () {
    window.location.reload();
});

/**
 * Attach the shared swipe-left-reveal handler so companion cards
 * support the same Mark Paid gesture as the owner mobile grid.
 * The helper is defined in ``app/static/js/swipe.js`` and registered
 * on ``window`` (no ES module tooling per CLAUDE.md "No JS
 * frameworks").  Threshold 50 px matches the owner mobile grid call
 * in ``mobile_grid.js`` so both surfaces feel identical under the
 * finger (mobile-first v3 plan R-8).  The host page tap-to-toggle
 * handler that lives in ``mobile_grid.js`` is also loaded on
 * companion pages via ``base.html``; for companion cards the
 * ``[data-mobile-txn-id]`` selector inside that handler does NOT
 * match (the macro omits the attribute under ``can_edit=False``),
 * so the action bar stays collapsed and the swipe-action button
 * remains the sole Mark Paid affordance for companion users.
 */
if (typeof window.attachSwipeAction === "function") {
    window.attachSwipeAction(document, { threshold: 50 });
}
