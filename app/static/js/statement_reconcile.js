// The Reconcile page's conveniences: the per-class sweeps, the new-envelope
// reveal, the dismissable legend, the OK count and the keyboard moves.
//
// Plan step bank_import:X-gj-1b, rulings bank_import:R-FZ(c), R-HD and R-HR.
//
// **EVERY BEHAVIOUR HERE IS A CONVENIENCE OVER A CONTROL THAT WORKS WITHOUT
// IT**, and that is structural rather than a courtesy.  With scripting off:
// each card's panel still opens (it is a <details>), its four verb tabs still
// switch (they are a radio group, styled by CSS), each card still has its own
// OK checkbox, the destination select and the income arm are still submitted,
// and Apply is still a submit button.  What is lost is the bulk click, the
// hidden new-envelope fields, the legend toggle, the running count and the
// keyboard -- and the MATCH tab's live difference, whose absence leaves the
// accept door refusing an unbalanced group and naming both sums, which is
// what it does today.
//
// **NO MONEY IS COMPUTED HERE**, which is the project's coding rule.  The
// difference a group comes to is rendered by the server
// (`accounts/_statement_reconcile_match.html`, driven by plain htmx
// attributes), so the figure on screen and the figure the accept door checks
// are one derivation rather than two in two languages with two rounding
// modes.
//
// Delegated from the document rather than bound per element, because Apply
// swaps the whole body through htmx: handlers bound at load would be attached
// to nodes that no longer exist after the first press.

(function () {
  "use strict";

  const LEGEND_KEY = "shekel.reconcile.legend";

  // Everything that only works when THIS FILE RUNS carries `data-rec-scripted`
  // and is rendered `hidden` by the server, so an affordance that could not
  // succeed is never printed -- the same rule this package applies to a verb
  // whose door does not exist.  **One MARKER rather than a list of selectors**:
  // a list here is a second place the set is written down, and a control added
  // to the markup but not to the list ships visible and dead.  The set IS the
  // markup.  Today its members are the footer's keyboard hints and the panel's
  // Close (a disclosure element is closed by its own summary with nothing
  // running, so the control that works without this file is the summary).
  const SCRIPTED_ONLY = "[data-rec-scripted]";

  // ── The per-class sweep ────────────────────────────────────────────────
  //
  // PER CLASS and never one "tick all" (R-FZ(c)): filing into a budget line
  // still open is absorbed by what it reserved, raising what a CLOSED one
  // recorded changes a figure the owner had finished with, and minting an
  // envelope the account did not have is the one act an undo cannot fully
  // reverse.
  //
  // **THE REACH IS THE SERVER'S PREDICATE, not a shape read off the markup**
  // (R-HD).  A card carries `data-sweep-class` only when
  // `LineCard.sweep_class` answered -- a working verb, no withheld sentence
  // and no money at risk -- and the caption's count is derived over exactly
  // that predicate.  So the click cannot reach a card the same screen has
  // just questioned, and the number in the caption is the number it sets.
  function sweep(form, group, checked) {
    form
      .querySelectorAll('[data-rec-card][data-sweep-class="' + group + '"]')
      .forEach(function (card) {
        const ok = card.querySelector("[data-rec-ok]");
        if (ok) {
          ok.checked = checked;
        }
      });
    countOk(form);
  }

  // ── The new-envelope reveal ────────────────────────────────────────────
  //
  // The name and category inputs are PARAMETERS OF ONE OPTION of the
  // destination select, not a destination of their own -- reading them as one
  // is the defect that made the existing-envelope arm unreachable from a
  // browser at plan step bank_import:X-f6a-3b.  Hiding them until that option
  // is chosen says so on screen; the service still refuses a new envelope
  // stated by halves, so the rule does not live here.
  function revealNewEnvelope(select) {
    const arm = select.closest("[data-rec-add]");
    if (!arm) {
      return;
    }
    const wanted = select.value === "new";
    arm.querySelectorAll("[data-new-envelope-field]").forEach(function (field) {
      field.hidden = !wanted;
    });
  }

  // ── The Apply button's count ───────────────────────────────────────────
  //
  // It counts TICKED OK BOXES, which is exactly what the door will read: the
  // button may not promise a number the submission does not carry.
  function countOk(form) {
    const slot = form.querySelector("[data-rec-ok-count]");
    if (!slot) {
      return;
    }
    // The server renders this EMPTY so the button reads "Apply the cards you
    // OK'd" when nothing runs; a count is only printed once something can
    // keep it true.  Zero prints nothing rather than "0", so the label never
    // contradicts a page with no ticks.
    const ticked = form.querySelectorAll("[data-rec-ok]:checked").length;
    slot.textContent = ticked ? String(ticked) + " of" : "";
  }

  // ── The legend ─────────────────────────────────────────────────────────
  //
  // Remembered per browser, which is what the locked direction asks for.  A
  // browser that refuses storage (a private window, blocked site data) throws
  // on access, so both halves are guarded and the legend simply starts shown.
  function legendDismissed() {
    try {
      return window.localStorage.getItem(LEGEND_KEY) === "1";
    } catch (err) {
      return false;
    }
  }

  function rememberLegend(dismissed) {
    try {
      window.localStorage.setItem(LEGEND_KEY, dismissed ? "1" : "0");
    } catch (err) {
      // Nothing to do: the legend still works for this page view.
    }
  }

  function showLegend(shown) {
    document.querySelectorAll("[data-rec-legend]").forEach(function (legend) {
      legend.hidden = !shown;
    });
  }

  // ── The keyboard ───────────────────────────────────────────────────────
  //
  // The footer's hints are rendered `hidden` by the server and revealed here,
  // so a hint for a key nothing binds is never printed -- which is the same
  // rule this package applies to a control whose door does not exist.
  function cards() {
    return Array.prototype.slice.call(
      document.querySelectorAll("[data-rec-card]"),
    );
  }

  function currentIndex(all) {
    for (let i = 0; i < all.length; i += 1) {
      if (all[i].classList.contains("rec-current")) {
        return i;
      }
    }
    return -1;
  }

  function moveTo(all, index) {
    all.forEach(function (card) {
      card.classList.remove("rec-current");
    });
    const card = all[Math.max(0, Math.min(index, all.length - 1))];
    if (!card) {
      return null;
    }
    card.classList.add("rec-current");
    card.scrollIntoView({ block: "nearest" });
    return card;
  }

  function typing(target) {
    if (!target || !target.tagName) {
      return false;
    }
    const tag = target.tagName.toLowerCase();
    return tag === "input" || tag === "select" || tag === "textarea";
  }

  document.addEventListener("change", function (event) {
    const target = event.target;
    if (!target || !target.matches) {
      return;
    }
    if (target.matches("[data-rec-sweep]")) {
      const form = target.closest("[data-rec-form]");
      if (form) {
        sweep(form, target.getAttribute("data-rec-sweep"), target.checked);
      }
      return;
    }
    if (target.matches("select[data-destination]")) {
      revealNewEnvelope(target);
      return;
    }
    if (target.matches("[data-rec-ok]")) {
      const form = target.closest("[data-rec-form]");
      if (form) {
        countOk(form);
      }
    }
  });

  document.addEventListener("click", function (event) {
    const target = event.target;
    if (!target || !target.closest) {
      return;
    }
    if (target.closest("[data-rec-legend-toggle]")) {
      const legend = document.querySelector("[data-rec-legend]");
      const shown = legend ? legend.hidden : false;
      showLegend(shown);
      rememberLegend(!shown);
      return;
    }
    if (target.closest("[data-rec-legend-close]")) {
      showLegend(false);
      rememberLegend(true);
      return;
    }
    // Closing the PANEL is closing the card: the panel is the disclosure's
    // content, so there is nothing else to collapse.
    //
    // **FOCUS MOVES TO THE SUMMARY, and that is not a courtesy.**  The Close
    // button is `document.activeElement` when a keyboard reaches it, and it
    // lives INSIDE the element being collapsed -- so once the card closes the
    // browser drops focus to <body> and the next Tab restarts from the top of
    // a page this arc measures at 248 cards and 537 KB.  The summary is where
    // the reader was and is the control that reopens the card.
    if (target.closest("[data-rec-close]")) {
      const card = target.closest("[data-rec-card]");
      if (card) {
        const summary = card.querySelector("summary");
        card.open = false;
        if (summary) {
          summary.focus();
        }
      }
    }
  });

  document.addEventListener("keydown", function (event) {
    if (typing(event.target) || event.metaKey || event.ctrlKey || event.altKey) {
      return;
    }
    const all = cards();
    if (!all.length) {
      return;
    }
    const at = currentIndex(all);
    if (event.key === "j") {
      moveTo(all, at + 1);
      event.preventDefault();
    } else if (event.key === "k") {
      moveTo(all, at < 0 ? 0 : at - 1);
      event.preventDefault();
    } else if (event.key === "o" && at >= 0) {
      all[at].open = !all[at].open;
      event.preventDefault();
    } else if (event.key === "Enter" && at >= 0) {
      const ok = all[at].querySelector("[data-rec-ok]");
      if (ok) {
        ok.checked = !ok.checked;
        const form = all[at].closest("[data-rec-form]");
        if (form) {
          countOk(form);
        }
        event.preventDefault();
      }
    }
  });

  function sync() {
    document
      .querySelectorAll("select[data-destination]")
      .forEach(revealNewEnvelope);
    document.querySelectorAll("[data-rec-form]").forEach(countOk);
    document.querySelectorAll(SCRIPTED_ONLY).forEach(function (control) {
      control.hidden = false;
    });
    showLegend(!legendDismissed());
  }

  document.addEventListener("DOMContentLoaded", sync);
  document.body.addEventListener("htmx:afterSwap", sync);
})();
