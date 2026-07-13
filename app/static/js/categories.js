/**
 * Shekel Budget App -- Categories Settings UI
 *
 * Handles the inline display ↔ edit toggle on each category row plus
 * the "create new group" UX in the group dropdown.  Replaces the
 * inline ``onclick=`` / ``onchange=`` / ``oninput=`` attributes that
 * the C-02 CSP (``script-src 'self'`` without ``'unsafe-inline'``)
 * blocks at load time.
 *
 * All listeners are delegated from ``document.body`` so handlers
 * still attach to category rows that arrive after page load via an
 * HTMX swap (the create_category route returns a fresh
 * ``_category_row.html`` partial under HX-Request).
 *
 * Behaviour matches the original inline handlers exactly -- this is
 * a wiring change, not a feature change.  Each ``data-action`` value
 * names one of four discrete behaviours:
 *
 *   data-action="cat-edit-show"     -- "Edit" button: show the edit
 *                                      drawer, hide the display row.
 *   data-action="cat-edit-cancel"   -- "Cancel" button: hide the edit
 *                                      drawer, show the display row.
 *   data-action="cat-group-change"  -- group <select>: switch the
 *                                      "create new" UX on / off.
 *   data-action="cat-group-name-input" -- new-group text input:
 *                                      mirror value to hidden field.
 *
 * The edit drawer (``data-edit-id``) is a wrapper <div>, not the edit
 * <form> itself: the labeled Archive / Delete actions moved into the
 * drawer as sibling forms (polish audit P-ST6 / S11), and forms cannot
 * nest, so show/cancel toggle the wrapper.
 *
 * Each element carries the IDs of its peer elements as data-* hints
 * so a row's handler operates only on that row's pieces (no
 * cross-row leakage when many rows render at once).
 */

(function() {
  "use strict";

  /**
   * Resolve a peer element by ID, throwing a clear error if the
   * data-* attribute is missing or points at a non-existent ID.  A
   * malformed template is a developer bug; failing loudly beats a
   * silent no-op that confuses a user.
   */
  function getPeer(el, dataKey) {
    var id = el.dataset[dataKey];
    if (!id) {
      throw new Error(
        "categories.js: missing data-" + dataKey + " on " + el.tagName
      );
    }
    var peer = document.getElementById(id);
    if (!peer) {
      throw new Error(
        "categories.js: no element with id=" + id +
        " (referenced from data-" + dataKey + ")"
      );
    }
    return peer;
  }

  // --- Click handler (delegated) -----------------------------------------
  document.body.addEventListener("click", function(event) {
    var trigger = event.target.closest("[data-action]");
    if (!trigger) return;

    var action = trigger.dataset.action;

    if (action === "cat-edit-show") {
      // "Edit" button.  Show the edit drawer, hide the display row.
      const editDrawer = getPeer(trigger, "editId");
      const displayRow = getPeer(trigger, "displayId");
      editDrawer.classList.remove("d-none");
      displayRow.classList.add("d-none");
      return;
    }

    if (action === "cat-edit-cancel") {
      // "Cancel" button.  Hide the edit drawer, restore the display
      // row.  The drawer is resolved by id, not closest(<form>): the
      // Cancel button sits outside the edit form so the drawer's
      // sibling Archive / Delete forms hide with it.
      const drawer = getPeer(trigger, "editId");
      drawer.classList.add("d-none");
      const display = getPeer(trigger, "displayId");
      display.classList.remove("d-none");
      return;
    }
  });

  // --- Change handler (delegated) ----------------------------------------
  document.body.addEventListener("change", function(event) {
    var target = event.target;
    if (!target.matches || !target.matches("[data-action='cat-group-change']")) {
      return;
    }
    // Group <select> changed.  When "__new__" is chosen, surface a
    // text input for the new group name and clear the hidden field
    // (the hidden field is the one that submits with the form);
    // otherwise hide the text input and copy the chosen group into
    // the hidden field.
    var customDiv = getPeer(target, "customId");
    var hiddenInput = getPeer(target, "hiddenId");
    if (target.value === "__new__") {
      customDiv.classList.remove("d-none");
      hiddenInput.value = "";
      const customInput = customDiv.querySelector("input");
      if (customInput) {
        customInput.focus();
      }
    } else {
      customDiv.classList.add("d-none");
      hiddenInput.value = target.value;
    }
  });

  // --- Input handler (delegated) -----------------------------------------
  document.body.addEventListener("input", function(event) {
    var target = event.target;
    if (
      !target.matches ||
      !target.matches("[data-action='cat-group-name-input']")
    ) {
      return;
    }
    // Custom group-name input.  Mirror its value into the hidden
    // input that actually submits with the form.
    var hiddenInput = getPeer(target, "hiddenId");
    hiddenInput.value = target.value;
  });
})();
