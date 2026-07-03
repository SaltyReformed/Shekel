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
    for (let i = 0; i < nodes.length; i++) {
      const el = nodes[i];
      const raw = el.getAttribute("data-progress-pct");
      let pct = parseFloat(raw);
      if (Number.isFinite(pct)) {
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

  /**
   * Re-apply widths inside whichever element(s) an htmx swap replaced.
   *
   * ``event.target`` is the node htmx DISPATCHED on -- for
   * ``htmx:oobAfterSwap`` that is the request's primary element, NOT
   * the out-of-band fragment (probed live: an OOB income-panel swap
   * fires with event.target #readiness-region and detail.target
   * #income-panel).  ``detail.target`` is the actual swap target for
   * both event kinds, so BOTH subtrees are re-applied; the applier is
   * idempotent, so the overlap on plain afterSwap is free.
   *
   * @param {Event} event  An ``htmx:afterSwap`` or ``htmx:oobAfterSwap``
   *                       dispatch.
   */
  function reapplyAfterSwap(event) {
    var detail = event && event.detail ? event.detail : {};
    applyProgressWidths(event.target || document);
    if (detail.target && detail.target !== event.target) {
      applyProgressWidths(detail.target);
    }
  }

  // HTMX-replaced fragments.  htmx 2.x fires ``htmx:afterSwap`` ONLY
  // for the request's primary swap target; out-of-band fragments get
  // their own ``htmx:oobAfterSwap`` (carrying the OOB element in
  // detail.target).  Both must be hooked: the retirement readiness
  // refresh re-sends the income meter as an hx-swap-oob sibling (and
  // the entries-CRUD cell re-render OOB-swaps envelope bars the same
  // way), so listening to afterSwap alone left every OOB progress bar
  // width un-applied -- the element arrived with a fresh
  // ``data-progress-pct`` but an empty ``style.width`` (found live
  // during the retirement acceptance-drive CSP trace).
  document.body.addEventListener("htmx:afterSwap", reapplyAfterSwap);
  document.body.addEventListener("htmx:oobAfterSwap", reapplyAfterSwap);
})();
