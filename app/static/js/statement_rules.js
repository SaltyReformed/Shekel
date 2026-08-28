// The merchant-rule control's new-envelope reveal, for both its surfaces.
//
// Plan step bank_import:X-gf-2.  The control is rendered on the review QUEUE
// (a merchant nobody has answered for) and on the REGISTER (an answer already
// given, being changed), so the one behaviour it needs is its own file rather
// than a passenger in the review screen's.
//
// It is a CONVENIENCE over a form that works entirely without it: with
// JavaScript disabled the name and category inputs are still submitted, they
// are merely always visible.  Nothing here decides anything, and nothing here
// is the only way to reach a control.
//
// NO MONEY IS COMPUTED HERE, which is the project's coding rule.  Stating a
// rule moves no money at all -- it is read to SUGGEST a destination, and only
// a destination submitted for one specific line records a purchase.
//
// Delegated from the document rather than bound per element, because both
// doors swap their whole body through htmx: handlers bound at load would be
// attached to nodes that no longer exist after the first Save.

(function () {
  "use strict";

  // The name and category inputs are PARAMETERS OF ONE OPTION of the answer
  // select, not an answer of their own -- reading them as one is the defect
  // that made the existing-envelope destination unreachable from a browser at
  // plan step X-f6a-3b.  Hiding them until that option is chosen says so on
  // screen; the service still refuses a new envelope stated by halves, so the
  // rule does not live here.
  function revealRuleFields(select) {
    const row = select.closest("[data-rule-row]");
    if (!row) {
      return;
    }
    const wanted = select.value === "new";
    row.querySelectorAll("[data-rule-new-field]").forEach(function (field) {
      field.classList.toggle("d-none", !wanted);
    });
  }

  document.addEventListener("change", function (event) {
    const target = event.target;
    if (target && target.matches && target.matches("select[data-rule]")) {
      revealRuleFields(target);
    }
  });

  // After an htmx swap the replacement markup carries its own state, rendered
  // from what was stored.  The case that needs re-syncing is a browser
  // restoring a select's value on a back / reload, which fires no change event.
  function syncAll() {
    document.querySelectorAll("select[data-rule]").forEach(revealRuleFields);
  }

  document.addEventListener("DOMContentLoaded", syncAll);
  document.body.addEventListener("htmx:afterSwap", syncAll);
})();
