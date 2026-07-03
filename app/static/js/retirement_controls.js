/**
 * Shekel Budget App -- Retirement Page Controls (direction-D rebuild)
 *
 * Client-side glue for the rebuilt /retirement page.  Pure UI wiring --
 * no money math; every figure this page shows is computed server-side.
 *
 *   1. What-if debounce: edits to ``.js-whatif-input`` fields (SWR /
 *      assumed return / merit horizon) dispatch a debounced
 *      ``shekel:readiness-whatif`` event; edits to ``.js-lever-input``
 *      stepper fields dispatch ``shekel:lever-refresh``.  Two hidden
 *      trigger elements in dashboard.html listen (``from:body``) and
 *      fire ONE readiness GET each with the matching hx-include set --
 *      delegation through body events survives HTMX swaps, where an
 *      ``hx-trigger="input from:.selector"`` binding would die with the
 *      swapped element.
 *   2. Name mirroring: the SWR row's save field posts
 *      ``safe_withdrawal_rate`` while the readiness what-if GET expects
 *      ``swr``; inputs with ``data-mirror`` copy their value into the
 *      hidden mirror input before the debounce fires.
 *   3. Steppers: the +/- buttons (``data-step`` / ``data-step-target``)
 *      adjust their number input within its min/max and dispatch an
 *      ``input`` event so the debounce path sees the change (programmatic
 *      value writes fire no events on their own).
 *   4. 422 swap shim: htmx's responseHandling config leaves 4xx bodies
 *      unswapped app-wide, but the assumptions panel's validation
 *      failures are DESIGNED fragments (update_settings re-renders the
 *      panel with field errors at 422), so swaps into
 *      ``#assumptions-region`` are re-enabled for that status.
 *   5. Post-save coherence: a saved assumption changes the verdict, the
 *      chart, the income meter, both levers, the account projections,
 *      AND the pension footer -- every figure on the page.  After a
 *      successful (2xx) assumptions-panel swap the page reloads so all
 *      of them re-derive server-side; the 422 path stays inline (field
 *      errors echo in place, no reload).
 */

(function () {
  "use strict";

  var DEBOUNCE_MS = 500;
  var timers = {};

  /**
   * Dispatch a bubbling custom event on body (the HTMX trigger bus).
   * @param {string} name - Event name.
   */
  function dispatch(name) {
    document.body.dispatchEvent(new CustomEvent(name, { bubbles: true }));
  }

  /**
   * Debounce one named event dispatch.
   * @param {string} name - Event name.
   */
  function debounced(name) {
    clearTimeout(timers[name]);
    timers[name] = setTimeout(function () {
      dispatch(name);
    }, DEBOUNCE_MS);
  }

  // What-if / lever inputs -> debounced refresh events (delegated so
  // HTMX panel swaps cannot orphan the listeners).
  document.body.addEventListener("input", function (event) {
    var el = event.target;
    if (!el || !el.classList) return;
    var mirrorId = el.getAttribute && el.getAttribute("data-mirror");
    var mirror = mirrorId ? document.getElementById(mirrorId) : null;
    if (mirror) mirror.value = el.value;
    if (el.classList.contains("js-whatif-input")) {
      debounced("shekel:readiness-whatif");
    } else if (el.classList.contains("js-lever-input")) {
      debounced("shekel:lever-refresh");
    }
  });

  // Stepper +/- buttons: adjust the target input within its bounds and
  // fire an input event so the debounce path runs.
  document.body.addEventListener("click", function (event) {
    var btn = event.target.closest ? event.target.closest("[data-step]") : null;
    if (!btn) return;
    var input = document.getElementById(btn.getAttribute("data-step-target"));
    if (!input) return;
    var step = parseFloat(btn.getAttribute("data-step"));
    if (!Number.isFinite(step)) return;
    var min = input.min !== "" ? parseFloat(input.min) : -Infinity;
    var max = input.max !== "" ? parseFloat(input.max) : Infinity;
    var value = parseFloat(input.value);
    if (!Number.isFinite(value)) value = 0;
    value = Math.min(max, Math.max(min, value + step));
    // Match the input's declared precision: whole numbers for integer
    // steps (months), two decimals for money steps (contribution).
    input.value = (input.step && input.step.indexOf(".") >= 0)
      ? value.toFixed(2)
      : String(Math.round(value));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });

  // 422 swap shim for the assumptions panel (designed error fragment).
  // The save forms target the stable #assumptions-region wrapper with
  // an innerHTML swap, so beforeSwap/afterSwap fire on an attached node
  // (an outerHTML target would be detached by the time afterSwap runs
  // and its events would never reach body).
  document.body.addEventListener("htmx:beforeSwap", function (event) {
    var detail = event.detail;
    if (detail && detail.xhr && detail.xhr.status === 422 &&
        detail.target && detail.target.id === "assumptions-region") {
      detail.shouldSwap = true;
    }
  });

  // Post-save coherence: reload so every server-computed figure on the
  // page re-derives against the newly stored settings.
  document.body.addEventListener("htmx:afterSwap", function (event) {
    var detail = event.detail || {};
    if (event.target && event.target.id === "assumptions-region" &&
        detail.xhr && detail.xhr.status < 400) {
      window.location.reload();
    }
  });
})();
