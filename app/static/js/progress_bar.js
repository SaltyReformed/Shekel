/**
 * Shekel Budget App -- Dynamic Progress-Bar Width Applier
 *
 * The CSP forbids inline ``style="..."`` attributes (audit F-036).
 * Progress-bar widths are computed server-side from financial data and
 * cannot be expressed via a finite set of utility classes.  This module
 * is the bridge: templates render ``data-progress-pct="<float>"`` on
 * each progress-bar element, and this script applies the percentage as
 * an inline width via ``element.style.width = '<n>%'`` -- a CSSOM
 * property setter, which CSP3 governs under ``script-src`` (allowed
 * because this script loaded from 'self'), NOT ``style-src``.
 *
 * Apply timing:
 *   - At ``DOMContentLoaded`` for the initial page render.
 *   - After every ``htmx:afterSwap`` for HTMX-replaced fragments.
 *
 * Defensive: tolerates missing or non-numeric data attributes by
 * skipping the element rather than throwing.  A malformed value is a
 * template bug and should be caught in test, not crash the UI.
 */

(function() {
  "use strict";

  /**
   * Apply each ``data-progress-pct`` value as an inline width to its
   * element.  Idempotent -- re-applying yields the same result.
   *
   * @param {Element|Document|null} root  Subtree to search.  Falsy or
   *                                      malformed roots become no-ops.
   */
  function applyProgressWidths(root) {
    if (!root || typeof root.querySelectorAll !== "function") {
      return;
    }
    var nodes = root.querySelectorAll("[data-progress-pct]");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var raw = el.getAttribute("data-progress-pct");
      var pct = parseFloat(raw);
      if (isFinite(pct)) {
        // Clamp to [0, 100] so a malformed server value cannot push the
        // progress bar off-axis.  The server should already clamp, but
        // defending here protects against template-time arithmetic
        // bugs and HTMX swaps that pre-render with stale data.
        if (pct < 0) { pct = 0; }
        if (pct > 100) { pct = 100; }
        el.style.width = pct + "%";
      }
    }
  }

  // Initial render.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function() {
      applyProgressWidths(document);
    });
  } else {
    applyProgressWidths(document);
  }

  // HTMX-replaced fragments.  htmx fires one ``htmx:afterSwap`` per
  // settled element -- including out-of-band fragments -- but the
  // event's ``detail.target`` always points at the request's PRIMARY
  // swap target.  ``event.target`` is the element each dispatch
  // actually fired on, so it covers the primary target AND every OOB
  // fragment (e.g. the entries-CRUD cell re-render, whose envelope
  // progress bar would otherwise never get its width applied).
  document.body.addEventListener("htmx:afterSwap", function(event) {
    var detail = event && event.detail ? event.detail : {};
    applyProgressWidths(event.target || detail.target || detail.elt || document);
  });
})();
