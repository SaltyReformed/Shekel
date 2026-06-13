/**
 * Companion view -- post-action page reload trigger.
 *
 * The entries endpoints return an HX-Trigger header of "balanceChanged"
 * when entries are created, updated, or deleted.  The shared Mark Paid
 * form targets `#txn-cell-<id>` with hx-swap="outerHTML", but that element
 * exists only in the desktop grid table, which the companion page does not
 * render -- so the swap finds no target and is a no-op.  The visible update
 * comes from the trigger event instead: HTMX processes the HX-Trigger
 * response header regardless of whether the swap target resolved, so the
 * listener below fires and reloads the page so the card reflects refreshed
 * entry totals and progress indicators.
 *
 * This page does NOT register its own "gridRefresh" handler: app.js (loaded
 * on every authenticated page, including this one) already binds a global
 * document.body "gridRefresh" -> reload listener, so a second copy here
 * would only double the reload (JS-11).  "balanceChanged" has no global
 * handler, so it lives here.
 */
document.body.addEventListener("balanceChanged", function () {
    window.location.reload();
});
