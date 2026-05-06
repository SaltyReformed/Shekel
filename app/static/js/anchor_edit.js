/**
 * Shekel Budget App -- Anchor Balance Inline Edit Helpers
 *
 * The accounts list and the budget grid both expose a click-to-edit
 * cell for the account's anchor balance.  Inside the edit form, the
 * user expects Escape to discard their changes and revert to the
 * display row.
 *
 * Pre-C-02 this was an inline ``onkeydown="if(event.key==='Escape')..."``
 * attribute.  The CSP introduced in commit C-02 forbids inline event
 * handlers (``script-src 'self'`` without ``'unsafe-inline'``), so
 * the input now carries ``data-action="anchor-cancel-on-escape"``
 * and a ``data-revert-url`` that names the GET endpoint returning
 * the display partial.
 *
 * Listener is delegated from ``document.body`` so it picks up inputs
 * that arrived via HTMX swap (the inline edit form is itself an HTMX
 * partial response).
 */

(function() {
  "use strict";

  document.body.addEventListener("keydown", function(event) {
    if (event.key !== "Escape") {
      return;
    }
    var target = event.target;
    if (
      !target.matches ||
      !target.matches("[data-action='anchor-cancel-on-escape']")
    ) {
      return;
    }
    var revertUrl = target.dataset.revertUrl;
    if (!revertUrl) {
      // Misconfigured template -- fail loudly so a developer sees it
      // immediately rather than a user wondering why Escape did
      // nothing.
      throw new Error(
        "anchor_edit.js: missing data-revert-url on " + target.tagName
      );
    }
    event.preventDefault();
    // window.htmx is exposed by the vendored htmx bundle.  Reuse it
    // to keep the swap semantics identical to the original inline
    // call (target=closest form, swap=outerHTML).
    var form = target.closest("form");
    if (!form) {
      return;
    }
    window.htmx.ajax("GET", revertUrl, {
      target: form,
      swap: "outerHTML",
    });
  });
})();
