'use strict';

/**
 * Shekel Budget App -- Escrow card error surfacing (loan detail).
 *
 * The escrow version-drawer forms POST to the escrow routes with
 * hx-target="#escrow-list". When a route rejects a change (a bad effective
 * date, a duplicate name, a frozen / settled-affecting version, a same-date
 * collision) it returns a plain-text message with a 4xx status. HTMX does NOT
 * swap on 4xx by default -- which is what preserves the operator's typed input
 * in the add / edit / schedule forms -- but it also leaves the message
 * invisible. This surfaces that message in the #escrow-error alert at the top of
 * the escrow card so the rejection is never silent.
 *
 * The alert lives INSIDE #escrow-list, so a later SUCCESSFUL mutation (which
 * swaps #escrow-list) re-renders it hidden and the error clears itself -- no
 * afterSwap bookkeeping needed here. Dismiss is a delegated click (CSP-safe, no
 * inline handler).
 */
(function () {
  "use strict";

  var LIST_ID = "escrow-list";
  var ERROR_ID = "escrow-error";

  /**
   * Show the escrow error alert with a message.
   * @param {string} message - The server's rejection text.
   */
  function show(message) {
    var el = document.getElementById(ERROR_ID);
    if (!el) return;
    var text = el.querySelector("[data-escrow-error-text]");
    (text || el).textContent = message;
    el.classList.remove("d-none");
  }

  // Surface a rejected escrow mutation. Filter to requests fired from within the
  // escrow list (the triggering element lives inside #escrow-list); ignore every
  // other 4xx/5xx on the page.
  document.body.addEventListener("htmx:responseError", function (evt) {
    var detail = evt.detail;
    if (!detail || !detail.elt || !detail.elt.closest("#" + LIST_ID)) return;
    var xhr = detail.xhr;
    show(
      xhr && xhr.responseText
        ? xhr.responseText
        : "That escrow change could not be applied."
    );
  });

  // Manual dismiss (delegated so it survives HTMX swaps; no inline onclick).
  document.body.addEventListener("click", function (evt) {
    var closer = evt.target.closest("[data-escrow-error-close]");
    if (!closer) return;
    var el = document.getElementById(ERROR_ID);
    if (el) el.classList.add("d-none");
  });
})();
