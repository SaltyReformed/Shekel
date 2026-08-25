/**
 * Shekel Budget App -- the destructive-action confirmation, bound FIRST.
 *
 * This file exists at all because of WHEN it runs, not what it contains.  It
 * carries the one dialog standing between a click and an irreversible act --
 * an Undo that destroys money records, a Delete that takes bank lines with it
 * -- and it used to live at the bottom of app.js, loaded after the whole
 * document (base.html).  Every control it guards was therefore clickable
 * BEFORE the listener that guards it existed: the statement review page is
 * 537 KB of HTML, and finding N-345 records an Undo submitted before the modal
 * bound, so the act ran with no dialog shown (ruling R-GP).  22 controls
 * across 17 templates depend on this listener (counted 2026-08-25 over data-confirm= and
 * hx-confirm= ATTRIBUTES, excluding the two documentation examples in
 * _confirm_modal.html and one occurrence inside a Jinja comment; a first
 * count said 29 and had counted all three).
 *
 * **So it is loaded in <head>, and NOT deferred.**  A blocking script in the
 * head runs before the parser reaches <body>, so no element carrying
 * data-confirm can exist before its guard does.  That is the whole design;
 * everything below is the dialog itself, moved verbatim apart from the two
 * changes the new position requires.
 *
 * TWO kinds of destructive control reach one dialog, because the project has
 * one destructive-action surface and a control should not look different for
 * being wired with htmx:
 *
 *   * a <form data-confirm="message">, intercepted on submit;
 *   * any element with hx-confirm="question", intercepted on htmx:confirm.
 *
 * The htmx half arrived with the grid's Delete control (plan step
 * bank_import:X-gb).  Before it, an hx-confirm fell through to the browser's
 * own confirm() -- so the entries list's "Delete this entry?" and the
 * statement screen's Undo, two destructive controls one click apart, drew two
 * different dialogs and only one of them could carry a disclosure worth
 * reading.  htmx fires htmx:confirm for EVERY request and sets detail.question
 * only where hx-confirm is present, so the guard below is what keeps this
 * listener out of the way of everything else.
 */
(function() {
  var pendingForm = null;
  var pendingRequest = null;

  // Show the dialog, or -- when the styled one cannot be shown -- fall back to
  // the browser's own rather than letting a destructive act through unasked.
  // It OWNS the pending state, so a caller cannot arm a request and then take
  // the fallback path with it still armed.
  //
  // **THREE things must be true for the styled dialog, and the third is new
  // with this file's position** (plan step bank_import:X-gc): the modal
  // element must be in the document, Bootstrap must have loaded, and
  // bootstrap.Modal must be constructible.  This script now runs BEFORE the
  // modal markup and before the Bootstrap bundle, both of which are at the
  // bottom of base.html, so a click landing in that window finds neither.
  // Asking through window.confirm is the fail-closed answer: the act still
  // cannot happen without an answer, which is the property the dialog exists
  // for.  Throwing instead would leave the press doing nothing at all, with
  // the submit already prevented and no way for the user to know why.
  //
  // **Its blast radius was enumerated rather than assumed**: all 22 armed
  // controls were read, and every data-confirm one is a plain method="POST"
  // form with a real action and no hx-post, so form.submit() on this path is
  // an ordinary full-page POST.  An htmx-driven data-confirm form would break
  // here, which is the thing to check before adding one.
  function ask(message, arm, proceedWithoutModal) {
    var modal = document.getElementById('confirmModal');
    var ready = modal
      && typeof bootstrap !== 'undefined'
      && bootstrap
      && typeof bootstrap.Modal === 'function';
    if (!ready) {
      if (window.confirm(message)) proceedWithoutModal();
      return;
    }
    arm();
    document.getElementById('confirmModalBody').textContent = message;
    new bootstrap.Modal(modal).show();
  }

  document.addEventListener('submit', function(e) {
    var form = e.target;
    var message = form.getAttribute('data-confirm');
    if (!message) return;

    e.preventDefault();
    ask(
      message,
      function() { pendingForm = form; },
      function() { form.removeAttribute('data-confirm'); form.submit(); }
    );
  });

  // Bound on `document`, not `document.body`, because there IS no body when
  // this runs.  htmx 2.0.4 dispatches every event with `bubbles: true` and
  // `cancelable: true` (vendor/htmx/htmx.min.js), so the event reaches
  // document and preventDefault here still cancels the request -- which is
  // what makes htmx re-issue it only through the Confirm button below.
  document.addEventListener('htmx:confirm', function(e) {
    var question = e.detail.question;
    if (!question) return;

    // Hold the request: htmx re-issues it with skipConfirmation, so the
    // question is asked exactly once however the element was activated.
    e.preventDefault();
    ask(
      question,
      function() { pendingRequest = e.detail; },
      function() { e.detail.issueRequest(true); }
    );
  });

  document.addEventListener('click', function(e) {
    if (e.target.id !== 'confirmModalYes') return;
    // Guard the instance AND the library: the button lives inside a modal that
    // is normally shown through bootstrap.Modal, but a click reaching it with
    // no instance (a partial render, a hand-driven DOM) would throw on
    // null.hide() -- and since this file may now run before the bundle, the
    // library itself is tested the same way ask() tests it.  One rule about
    // Bootstrap's availability rather than two.
    var instance = typeof bootstrap !== 'undefined' && bootstrap.Modal
      ? bootstrap.Modal.getInstance(document.getElementById('confirmModal'))
      : null;
    if (instance) instance.hide();
    if (pendingForm) {
      // Remove the data-confirm to avoid re-triggering on submit.
      pendingForm.removeAttribute('data-confirm');
      pendingForm.submit();
      pendingForm = null;
    } else if (pendingRequest) {
      pendingRequest.issueRequest(true);
      pendingRequest = null;
    }
  });

  // A dismissed dialog must not leave a request armed for the NEXT dialog's
  // Confirm to fire.  Bootstrap emits hidden.bs.modal for every close path --
  // the Cancel button, the X, Escape and a backdrop click alike.
  document.addEventListener('hidden.bs.modal', function(e) {
    if (e.target && e.target.id === 'confirmModal') {
      pendingForm = null;
      pendingRequest = null;
    }
  });
})();
