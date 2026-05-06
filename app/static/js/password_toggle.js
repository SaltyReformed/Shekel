/**
 * Shekel Budget App -- Password Show/Hide Toggle
 *
 * Closes audit finding F-090 (masked password view toggle missing).
 *
 * The CSP introduced in commit C-02 forbids inline event handlers
 * (``script-src 'self'`` without ``'unsafe-inline'``), so the toggle
 * cannot use ``onclick="..."`` on the button.  Instead, every toggle
 * button carries:
 *
 *   data-action="password-toggle"
 *   data-target="<id-of-input>"   the password <input> to toggle
 *
 * and this module attaches a single delegated click listener on
 * ``document.body`` that flips the input's ``type`` between ``password``
 * and ``text`` and swaps the icon between ``bi-eye-slash`` (hidden,
 * default) and ``bi-eye`` (visible).  The icon swap mirrors common
 * eye-icon password reveals.
 *
 * Accessibility: the button updates ``aria-label`` on each toggle so
 * screen readers announce the new state ("Show password" -> "Hide
 * password").  The button keeps its existing focus, so a keyboard user
 * who tabbed to it and pressed Space stays on the button rather than
 * losing focus to the document body.
 */

(function () {
  "use strict";

  /**
   * Resolve the target password input for a toggle button.
   *
   * @param {Element} button  The toggle button that was clicked.
   * @returns {HTMLInputElement|null} The matching <input> element, or
   *   null if the button is misconfigured.  A null return is treated
   *   as a no-op (rather than throwing) so that a missed data-target
   *   on a single button does not crash the page.
   */
  function resolveTarget(button) {
    var targetId = button.dataset.target;
    if (!targetId) {
      return null;
    }
    var target = document.getElementById(targetId);
    if (!target || target.tagName !== "INPUT") {
      return null;
    }
    return target;
  }

  /**
   * Update the icon and aria-label inside a toggle button to reflect
   * the new visible/hidden state.
   *
   * @param {Element} button  The toggle button.
   * @param {boolean} isVisible  True when the password is now in
   *   plaintext mode (input type=text), false when masked.
   */
  function syncButtonChrome(button, isVisible) {
    var icon = button.querySelector("i.bi");
    if (icon) {
      // Toggle the two Bootstrap Icon classes.  classList.replace is
      // a no-op if the source class is not present, which keeps the
      // function idempotent on a button that was already in the
      // target state.
      if (isVisible) {
        icon.classList.remove("bi-eye-slash");
        icon.classList.add("bi-eye");
      } else {
        icon.classList.remove("bi-eye");
        icon.classList.add("bi-eye-slash");
      }
    }
    button.setAttribute(
      "aria-label",
      isVisible ? "Hide password" : "Show password"
    );
    // aria-pressed mirrors the on/off state for screen-reader users
    // who interact with the button as a toggle rather than with the
    // password input itself.
    button.setAttribute("aria-pressed", isVisible ? "true" : "false");
  }

  document.body.addEventListener("click", function (event) {
    // Find the closest matching button so a click on the icon child
    // still dispatches.  Bail out cleanly if the click was not on a
    // toggle.
    var button = event.target.closest("[data-action='password-toggle']");
    if (!button) {
      return;
    }
    event.preventDefault();
    var target = resolveTarget(button);
    if (!target) {
      return;
    }
    var nowVisible = target.type === "password";
    target.type = nowVisible ? "text" : "password";
    syncButtonChrome(button, nowVisible);
  });
})();
