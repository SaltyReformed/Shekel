/**
 * Companion view -- post-action page reload triggers.
 *
 * The mark_done endpoint returns grid cell HTML plus an HX-Trigger
 * header of "gridRefresh"; the entries endpoints return
 * "balanceChanged".  The shared Mark Paid form targets
 * `#txn-cell-<id>` with hx-swap="outerHTML", but that element exists
 * only in the desktop grid table, which the companion page does not
 * render -- so the swap finds no target and is a no-op.  The visible
 * update comes from the trigger events instead: HTMX processes the
 * HX-Trigger response header regardless of whether the swap target
 * resolved, so the listeners below fire and reload the page so the
 * card reflects the updated status (Paid indicator, hidden Mark Paid
 * button) and refreshed entry totals.
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
