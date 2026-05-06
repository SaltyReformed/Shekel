/**
 * Shekel Budget App -- Password Strength Meter (zxcvbn)
 *
 * Closes audit finding F-089 (password strength meter not implemented).
 *
 * Renders a Bootstrap progress bar plus a textual strength label that
 * updates on every keystroke in a password <input>.  Strength is
 * scored 0..4 by the vendored zxcvbn library
 * (``app/static/vendor/zxcvbn/zxcvbn.js``).  Score thresholds and the
 * resulting bar/text appearance:
 *
 *    Score | Label       | Bar class           | Bar width
 *    ------|-------------|---------------------|----------
 *      0   | Very weak   | bg-danger           | 20%
 *      1   | Weak        | bg-danger           | 40%
 *      2   | Fair        | bg-warning          | 60%
 *      3   | Good        | bg-info             | 80%
 *      4   | Strong      | bg-success          | 100%
 *
 * Wiring uses data attributes so the CSP forbids no inline JS and
 * templates do not need to re-import zxcvbn:
 *
 *    <input id="password" data-password-input ...>
 *    <div data-password-meter-for="password">
 *      <div class="progress" style="height: 6px;">
 *        <div class="progress-bar" data-password-meter-bar></div>
 *      </div>
 *      <small data-password-meter-text class="form-text"></small>
 *    </div>
 *
 * Behavioural notes:
 *
 *   - The strength bar is intentionally NOT a hard gate.  Server-side
 *     enforcement (12-character minimum, HIBP breach check via
 *     ``auth_service._check_pwned_password``) is authoritative; the
 *     meter is a UX cue, not a control.
 *
 *   - When zxcvbn is not loaded (e.g. the vendored asset failed to
 *     download), the module attaches no listener and the meter
 *     elements simply remain empty.  Failing closed (blocking the
 *     form) would be worse for users than failing silent because the
 *     server still validates the password.
 *
 *   - The ``crackTimesDisplay`` and ``feedback`` outputs from zxcvbn
 *     are intentionally NOT shown.  ``crackTimesDisplay`` ("less than
 *     a second") is alarming without being actionable, and
 *     ``feedback.warning`` strings are inconsistent across zxcvbn
 *     versions.  A simple Very-weak..Strong scale matches user
 *     expectations and keeps the UI compact on the registration form.
 */

(function () {
  "use strict";

  /** @type {Array<{label: string, barClass: string, widthPct: number}>} */
  var SCORE_TO_DISPLAY = [
    { label: "Very weak", barClass: "bg-danger", widthPct: 20 },
    { label: "Weak", barClass: "bg-danger", widthPct: 40 },
    { label: "Fair", barClass: "bg-warning", widthPct: 60 },
    { label: "Good", barClass: "bg-info", widthPct: 80 },
    { label: "Strong", barClass: "bg-success", widthPct: 100 },
  ];

  var ALL_BAR_CLASSES = ["bg-danger", "bg-warning", "bg-info", "bg-success"];

  /**
   * Locate the meter container element associated with a password
   * input by its id.  Returns null if no matching container exists,
   * which happens on pages that include this script but only need
   * the show/hide toggle.
   *
   * @param {string} inputId
   * @returns {Element|null}
   */
  function findMeterFor(inputId) {
    return document.querySelector(
      "[data-password-meter-for=\"" + inputId + "\"]"
    );
  }

  /**
   * Reset the meter to a "no input yet" state: zero-width bar, empty
   * label.  Used when the input is cleared and on initial setup so
   * the meter never shows a stale score from a previous render.
   *
   * @param {Element} container
   */
  function clearMeter(container) {
    var bar = container.querySelector("[data-password-meter-bar]");
    var text = container.querySelector("[data-password-meter-text]");
    if (bar) {
      bar.style.width = "0%";
      for (var i = 0; i < ALL_BAR_CLASSES.length; i++) {
        bar.classList.remove(ALL_BAR_CLASSES[i]);
      }
      bar.setAttribute("aria-valuenow", "0");
    }
    if (text) {
      text.textContent = "";
    }
  }

  /**
   * Apply a zxcvbn-derived score (0..4) to the meter container.
   *
   * @param {Element} container
   * @param {number} score  Integer 0..4 from ``zxcvbn(password).score``.
   */
  function renderScore(container, score) {
    var clamped = Math.max(0, Math.min(4, score));
    var display = SCORE_TO_DISPLAY[clamped];
    var bar = container.querySelector("[data-password-meter-bar]");
    var text = container.querySelector("[data-password-meter-text]");
    if (bar) {
      bar.style.width = display.widthPct + "%";
      for (var i = 0; i < ALL_BAR_CLASSES.length; i++) {
        bar.classList.remove(ALL_BAR_CLASSES[i]);
      }
      bar.classList.add(display.barClass);
      bar.setAttribute("aria-valuenow", String(display.widthPct));
    }
    if (text) {
      text.textContent = "Strength: " + display.label;
    }
  }

  /**
   * Wire a single password input to its meter.  Attaches an ``input``
   * listener so the meter updates as the user types.
   *
   * @param {HTMLInputElement} input
   */
  function bind(input) {
    var container = findMeterFor(input.id);
    if (!container) {
      return;
    }
    if (typeof window.zxcvbn !== "function") {
      // Vendored asset failed to load.  Leave the meter blank so the
      // form does not show a stuck "Very weak" indicator.  Server-
      // side validation still enforces minimums.
      clearMeter(container);
      return;
    }
    clearMeter(container);
    input.addEventListener("input", function () {
      var value = input.value || "";
      if (value.length === 0) {
        clearMeter(container);
        return;
      }
      // zxcvbn is CPU-bound but cheap on modern machines (under 10ms
      // for typical input).  No debounce needed for keystroke
      // updates -- the scoring is fast enough that the meter feels
      // instantaneous even on long inputs.
      var result = window.zxcvbn(value);
      renderScore(container, result.score);
    });
  }

  function init() {
    var inputs = document.querySelectorAll("[data-password-input]");
    for (var i = 0; i < inputs.length; i++) {
      bind(inputs[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
