// Statement review: the per-class sweep, and the new-envelope reveal.
//
// Plan step bank_import:X-f6a-3c-2.  Both behaviours are conveniences over a
// form that works entirely without them, and that is deliberate: with
// JavaScript disabled every proposal still has its own checkbox, every bank
// line still has its destination select, and the name and category inputs are
// still submitted -- they are merely always visible.  Nothing here decides
// anything, and nothing here is the only way to reach a control.
//
// Delegated from the document rather than bound per element, because the batch
// POST swaps the whole review body through htmx: handlers bound at load would
// be attached to nodes that no longer exist after the first Apply.

(function () {
  "use strict";

  // ── The per-class sweep ────────────────────────────────────────────────
  //
  // A proposal either CONFIRMS a day the app already had, MOVES one it got
  // wrong, or MARKS a row as having happened for the first time.  The three
  // partition (MatchProposal.review_class), and sweeping by class rather than
  // by one "tick all" is what keeps R-FP's "reviewed before it commits"
  // meaningful at 124 proposals: the review happens once per class, and the
  // riskiest class is never swept by the same click as the safest.
  function sweep(root, group, checked) {
    root
      .querySelectorAll('input[type="checkbox"][data-proposal-class="' + group + '"]')
      .forEach(function (box) {
        box.checked = checked;
      });
  }

  // ── The new-envelope reveal ────────────────────────────────────────────
  //
  // The name and category inputs are PARAMETERS OF ONE OPTION of the
  // destination select, not a destination of their own -- reading them as one
  // is the defect that made the existing-envelope arm unreachable from a
  // browser at plan step X-f6a-3b.  Hiding them until that option is chosen
  // says so on screen; the service still refuses a new envelope stated by
  // halves, so the rule does not live here.
  function revealNewEnvelope(select) {
    const row = select.closest("[data-creatable-row]");
    if (!row) {
      return;
    }
    const wanted = select.value === "new";
    row.querySelectorAll("[data-new-envelope-field]").forEach(function (field) {
      field.classList.toggle("d-none", !wanted);
    });
  }

  document.addEventListener("change", function (event) {
    const target = event.target;
    if (!target || !target.matches) {
      return;
    }
    if (target.matches("[data-tick-all]")) {
      const form = target.closest("form");
      if (form) {
        sweep(form, target.getAttribute("data-tick-all"), target.checked);
      }
      return;
    }
    if (target.matches("select[data-destination]")) {
      revealNewEnvelope(target);
    }
  });

  // After an htmx swap the replacement markup carries its own state: every
  // sweep box is unticked and every destination select is back on "leave this
  // line alone", so the fields start hidden and nothing needs re-syncing.  The
  // one case that does is a browser restoring a select's value on a back /
  // reload, which fires no change event.
  function syncAll() {
    document
      .querySelectorAll("select[data-destination]")
      .forEach(revealNewEnvelope);
  }

  document.addEventListener("DOMContentLoaded", syncAll);
  document.body.addEventListener("htmx:afterSwap", syncAll);
})();
