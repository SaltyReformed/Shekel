/**
 * Shekel Budget App -- Retirement Page Controls (direction-D rebuild)
 *
 * Client-side glue for the rebuilt /retirement page.  Pure UI wiring --
 * no money math; every figure this page shows is computed server-side.
 *
 *   1. What-if debounce: edits to ``.js-whatif-input`` fields (SWR /
 *      assumed return / each recurring raise's end-year pair) dispatch a debounced
 *      ``shekel:readiness-whatif`` event; edits to ``.js-lever-input``
 *      stepper fields dispatch ``shekel:lever-refresh``.  ONE hidden
 *      trigger element in dashboard.html listens for both (``from:body``)
 *      and fires a readiness GET carrying both include sets --
 *      delegation through body events survives HTMX swaps, where an
 *      ``hx-trigger="input from:.selector"`` binding would die with the
 *      swapped element.  The two events remain distinct because they are
 *      debounced separately; they stopped needing separate triggers at
 *      plan step C2-f2d-4, when the levers began solving against the
 *      what-if assumptions and therefore needed the same payload.
 *   2. Name mirroring: the SWR row's save field posts
 *      ``safe_withdrawal_rate`` while the readiness what-if GET expects
 *      ``swr``; inputs with ``data-mirror`` copy their value into the
 *      hidden mirror input before the debounce fires.
 *   3. Steppers: the +/- buttons (``data-step`` / ``data-step-target``)
 *      adjust their number input within its min/max and dispatch an
 *      ``input`` event so the debounce path sees the change (programmatic
 *      value writes fire no events on their own).
 *   4. Post-save coherence: a SAVED assumption changes the verdict, the
 *      chart, the income meter, both levers, the account projections,
 *      AND the pension footer -- every figure on the page, including the
 *      accounts table the fragment response does not carry.  After a
 *      successful (2xx) assumptions-panel swap the page reloads so all
 *      of them re-derive server-side; the 422 path stays inline (field
 *      errors echo in place, no reload).
 *   5. A refusal retires when its control is edited, and the request in
 *      flight is aborted on every keystroke (plan step
 *      salary:S3-f-4, ruling R-SAL34).  The rail renders a refusal --
 *      the Save's, or since that step the what-if GET's, which answers
 *      a refused input with the rail itself, retargeted (R-SAL33) -- as
 *      ``is-invalid`` on the control and one ``.invalid-feedback`` per
 *      row.  The GET's SUCCESS never re-renders the rail (a re-render on
 *      every debounced refresh would replace the input the owner is
 *      still typing in), so without this a corrected year would keep
 *      the refusal of the year it replaced beside a card drawn at
 *      itself.  The message describes the value it refused; editing the
 *      value retires it, and the server's next answer -- 200 or 422,
 *      GET or Save -- re-states whatever still holds.
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

  /**
   * Retire the refusal rendered on the rail row that holds a control.
   *
   * The row's controls drop ``is-invalid`` and its feedback line goes; a
   * raise row's two controls share one feedback line, and a refusal of
   * either half is about the pair, so the whole row clears.  A control
   * outside a rail row (the assumed-return what-if) has nothing to clear.
   * @param {Element} el - The control that was edited.
   */
  function retireRefusal(el) {
    var row = el.closest ? el.closest(".retire-assump-row") : null;
    if (!row) return;
    row.querySelectorAll(".is-invalid").forEach(function (control) {
      control.classList.remove("is-invalid");
    });
    row.querySelectorAll(".invalid-feedback").forEach(function (feedback) {
      feedback.remove();
    });
  }

  /**
   * Abort the readiness request in flight, if any: its answer is for an
   * input the owner has since changed.  A refusal re-renders the rail with
   * the request's own values echoed (R-SAL33), so a stale answer landing
   * after a newer keystroke would put the older value back under the
   * caret; a stale 200 landing after a newer one would draw the card at
   * the older input.  The debounce re-issues the request with the latest
   * values, and hx-sync="this:replace" on the trigger covers the case
   * where the next request is issued while one is still in flight.
   */
  function abortInFlightRefresh() {
    var trigger = document.getElementById("readiness-refresh");
    if (trigger && window.htmx) htmx.trigger(trigger, "htmx:abort");
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
      retireRefusal(el);
      abortInFlightRefresh();
      debounced("shekel:readiness-whatif");
    } else if (el.classList.contains("js-lever-input")) {
      abortInFlightRefresh();
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

  // Post-save coherence: reload so every server-computed figure on the
  // page re-derives against the newly stored settings.
  // The 422 error path stays inline WITHOUT a page reload: the route
  // stamps the designed-fragment marker header (update_settings), the
  // global listener in app.js swaps the rail with its field errors, and
  // the status >= 400 guard below skips the reload.  The save forms
  // target the stable #assumptions-region wrapper with an innerHTML
  // swap, so the swap lands on an attached node.  The what-if GET's
  // refusal lands there too, RETARGETED by the route (R-SAL33): htmx
  // then raises afterSwap on this region at 422, which the same guard
  // keeps from reloading the page mid-edit -- and which is where the
  // caret is put back at the end of the value the owner is typing.
  document.body.addEventListener("htmx:afterSwap", function (event) {
    var detail = event.detail || {};
    if (!event.target || event.target.id !== "assumptions-region" || !detail.xhr) {
      return;
    }
    if (detail.xhr.status < 400) {
      window.location.reload();
      return;
    }
    // A refusal re-rendered the rail around the control the owner is
    // typing in.  htmx restored focus to the swapped-in control by id, but
    // a number input has no selection to restore and Chromium's focus()
    // puts the caret at the START -- the next digit would land in front
    // of the value (measured at plan step salary:S3-f-4: a "0" typed after
    // a refused "2025" read "02025").  Re-assigning the value moves the
    // caret to the end, and a programmatic value write fires no input
    // event, so nothing here re-triggers the refresh.
    var active = document.activeElement;
    var value;
    if (active && active.tagName === "INPUT" && event.target.contains(active)) {
      value = active.value;
      active.value = "";
      active.value = value;
    }
  });
})();
