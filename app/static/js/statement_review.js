// Statement review: the per-class sweep, and the new-envelope reveal.
//
// Plan step bank_import:X-f6a-3c-2.  Both behaviours are conveniences over a
// form that works entirely without them, and that is deliberate: with
// JavaScript disabled every proposal still has its own checkbox, every bank
// line still has its destination select, and the name and category inputs are
// still submitted -- they are merely always visible.  Nothing here decides
// anything, and nothing here is the only way to reach a control.
//
// **NO MONEY IS COMPUTED HERE**, which is the project's coding rule and, since
// plan step bank_import:X-f6d-4, true again.  That step first summed the
// hand-build form's two sides in this file and posted the total back as the
// owner's consent; the server renders it now
// (``accounts/_statement_hand_totals.html``, driven by plain htmx attributes),
// so the figure on screen and the figure the accept door checks are one
// derivation rather than two in two languages with two rounding modes.
//
// Delegated from the document rather than bound per element, because the batch
// POST swaps the whole review body through htmx: handlers bound at load would
// be attached to nodes that no longer exist after the first Apply.

(function () {
  "use strict";

  // ── The per-class sweep ────────────────────────────────────────────────
  //
  // A proposal either CORRECTS an AMOUNT onto the bank's, CONFIRMS a day the
  // app already had, MOVES one it got wrong, or MARKS a row as having happened
  // for the first time.  The four partition (MatchProposal.review_class) --
  // repricing is the fourth and takes precedence, because it is the only one
  // that changes what money was SPENT (plan step bank_import:X-f6d-1).  This
  // function needs no change for it: it reads the class off the markup.
  // Sweeping by class rather than
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

  // ── The rule sweeps ──────────────────────────────────────────────────
  //
  // PER CLASS, like the proposal sweep above and for the same ruled reason
  // (R-FZ(c)): filing into an open budget line, raising what a closed one
  // recorded, and creating one the account did not have are three different
  // acts, and the riskiest may not ride the same click as the safest.  The
  // class is the SERVER's (Placement.sweep_class), not a shape read off the
  // markup.
  //
  // Plan step bank_import:X-f6a-3d.  A stated merchant rule is a SUGGESTION:
  // each line's destination select still opens on "leave this line alone", and
  // this is what turns the suggestions into ticks -- one press, visible, and
  // undoable line by line before Apply.  Prefilling the selects instead would
  // put a default back on a control that writes money, which is what the
  // developer's ruling of 2026-08-19 removed.
  //
  // The value it sets comes from the SERVER (data-placement, which is
  // Placement.select_value), so the control and the write door cannot disagree
  // about which option a rule means.  A row with no data-placement has no
  // act to sweep -- "never a purchase", or a rule that does not reach this
  // line's pay period -- and is passed over.
  function sweepPlaced(root, group, checked) {
    root
      .querySelectorAll(
        '[data-creatable-row][data-placement-class="' + group + '"]',
      )
      .forEach(function (row) {
        const select = row.querySelector("select[data-destination]");
        if (!select) {
          return;
        }
        // Untick returns the line to the do-nothing arm, never to some prior
        // value: the checkbox says "record these", so its opposite is the
        // documented default rather than an undo stack.
        select.value = checked ? row.getAttribute("data-placement") : "";
        // The name and category boxes are NOT set here.  The server already
        // renders them from the rule, so the sweep and a hand-picked "a new
        // envelope" state the same thing -- and one rule about what a created
        // envelope is called lives in one place.
        revealNewEnvelope(select);
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
    if (target.matches("[data-tick-placed]")) {
      const form = target.closest("form");
      if (form) {
        sweepPlaced(
          form, target.getAttribute("data-tick-placed"), target.checked,
        );
      }
      return;
    }
    if (target.matches("select[data-rule]")) {
      revealRuleFields(target);
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
  // The rule control's own reveal: its name and category boxes are
  // parameters of ONE of its options, exactly as the create form's are.
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

  function syncAll() {
    document
      .querySelectorAll("select[data-destination]")
      .forEach(revealNewEnvelope);
    document.querySelectorAll("select[data-rule]").forEach(revealRuleFields);
  }

  document.addEventListener("DOMContentLoaded", syncAll);
  document.body.addEventListener("htmx:afterSwap", syncAll);
})();
