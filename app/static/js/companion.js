/**
 * Companion view -- reload page on gridRefresh events.
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
