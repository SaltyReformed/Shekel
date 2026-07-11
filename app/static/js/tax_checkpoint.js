/**
 * Shekel Budget App -- Analytics Taxes tab: YTD checkpoint 422 swap shim.
 *
 * The app-wide htmx responseHandling config (base.html) deliberately
 * leaves 4xx bodies non-swapping because most of them are raw strings
 * or JSON; a DESIGNED error fragment opts back in per surface (the
 * retirement assumptions panel precedent, retirement_controls.js).
 * The checkpoint save route (salary/checkpoint.py) returns the full
 * card partial with field errors at 422, targeted at
 * #ytd-checkpoint-card (outerHTML) -- without this shim a validation
 * failure was silently dropped: no swap, no error, the form just kept
 * the typed values (dead error UI).  beforeSwap fires while the
 * outerHTML target is still attached, so the id check is reliable.
 *
 * The route's handled-500 path (DB-tier failure) also returns a card
 * with a banner, but 5xx stays non-swapping here: an UNHANDLED 500 is
 * a full error document, indistinguishable client-side, and swapping
 * that into the card would be worse than the silent drop.  The 5xx
 * designed-fragment convention is an open app-wide follow-up.
 */

(function () {
  "use strict";

  document.body.addEventListener("htmx:beforeSwap", function (event) {
    var detail = event.detail;
    if (detail && detail.xhr && detail.xhr.status === 422 &&
        detail.target && detail.target.id === "ytd-checkpoint-card") {
      detail.shouldSwap = true;
    }
  });
})();
