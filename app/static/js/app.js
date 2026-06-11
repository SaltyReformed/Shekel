/**
 * Shekel Budget App -- Client-Side JavaScript
 *
 * Minimal JS: HTMX handles most interactivity server-side.
 * This file provides the theme toggle and small UX helpers.
 */

// --- Service Worker Registration ---
// Registers the static-asset-only service worker shipped at
// /sw.js (served by app/routes/static_pass.py at the root scope
// so the worker can intercept fetches across the whole app, not
// just under /static/).  The worker NEVER caches HTML or JSON --
// see app/static/sw.js header for the financial-correctness
// invariant.  Guarded so older / privacy-restricted browsers
// without serviceWorker support degrade silently to plain network
// fetches; registration is deferred to the load event so the SW
// install never competes with the first paint of the page.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  });
}

// --- Theme Toggle ---
(function() {
  var saved = localStorage.getItem('shekel-theme');
  if (saved) {
    document.documentElement.setAttribute('data-bs-theme', saved);
  }

  document.addEventListener('DOMContentLoaded', function() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var icon = btn.querySelector('i');

    function updateIcon() {
      var current = document.documentElement.getAttribute('data-bs-theme');
      if (icon) {
        icon.className = current === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
      }
    }

    updateIcon();

    btn.addEventListener('click', function() {
      var current = document.documentElement.getAttribute('data-bs-theme');
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-bs-theme', next);
      localStorage.setItem('shekel-theme', next);
      updateIcon();
      document.dispatchEvent(new CustomEvent('shekel:theme-changed', { detail: { theme: next } }));
    });
  });
})();

// Listen for HTMX gridRefresh events to reload the full page.
document.body.addEventListener("gridRefresh", function() {
  window.location.reload();
});

// Reset the Add Transaction form whenever its modal opens.
var addModal = document.getElementById("addTransactionModal");

// Purchase tracking is expense-only, so the "Track individual purchases"
// row is hidden (and unchecked) whenever the modal's Type select is set
// to a non-expense type.  The expense type id is published on the row as
// a data attribute by the template.  Companion visibility applies to all
// types and is never hidden.
function syncAddEnvelopeVisibility() {
  if (!addModal) return;
  var row = addModal.querySelector("[data-adhoc-envelope-row]");
  var typeSel = addModal.querySelector('select[name="transaction_type_id"]');
  if (!row || !typeSel) return;
  var isExpense = typeSel.value === row.getAttribute("data-expense-type-id");
  row.classList.toggle("d-none", !isExpense);
  if (!isExpense) {
    var cb = row.querySelector('input[name="is_envelope"]');
    if (cb) cb.checked = false;
  }
}

if (addModal) {
  addModal.addEventListener("show.bs.modal", function() {
    var form = addModal.querySelector("form");
    if (form) form.reset();
    syncAddEnvelopeVisibility();
  });
  var addTypeSel = addModal.querySelector('select[name="transaction_type_id"]');
  if (addTypeSel) {
    addTypeSel.addEventListener("change", syncAddEnvelopeVisibility);
  }
}

// Inject CSRF token into all HTMX requests.
document.body.addEventListener("htmx:configRequest", function(event) {
  var token = document.querySelector('meta[name="csrf-token"]');
  if (token) {
    event.detail.headers["X-CSRFToken"] = token.getAttribute("content");
  }
});

// Show a loading spinner during HTMX requests (class-based).
document.body.addEventListener("htmx:beforeRequest", function(event) {
  const target = event.detail.elt;
  if (target && target.closest && target.closest("td")) {
    target.closest("td").classList.add("htmx-loading");
  }
});

document.body.addEventListener("htmx:afterRequest", function(event) {
  const target = event.detail.elt;
  if (target && target.closest && target.closest("td")) {
    target.closest("td").classList.remove("htmx-loading");
  }
});

// Consolidated htmx:afterSwap handler -- save flash, popover close, focus restore.
document.body.addEventListener("htmx:afterSwap", function(event) {
  const el = event.detail.elt;

  // Save flash animation -- only for transaction cell saves.
  if (el && el.closest && el.closest('td.cell')) {
    el.classList.add("save-flash");
    el.addEventListener("animationend", function() {
      el.classList.remove("save-flash");
    }, { once: true });
  }

  // Close the full-edit popover if the swap target is outside it.
  if (typeof activePopover !== 'undefined' && activePopover && !activePopover.contains(el)) {
    closeFullEdit();
  }

  // Re-initialize Bootstrap popovers/tooltips in swapped content
  var target = event.detail.target || event.detail.elt;
  if (target) {
    target.querySelectorAll('[data-bs-toggle="popover"]').forEach(function(popEl) {
      if (!bootstrap.Popover.getInstance(popEl)) {
        new bootstrap.Popover(popEl);
      }
    });
    target.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(tipEl) {
      if (!bootstrap.Tooltip.getInstance(tipEl)) {
        new bootstrap.Tooltip(tipEl);
      }
    });

    // Auto-show modal partials swapped into the DOM.  Used by
    // server-rendered confirmation/preview modals (e.g. the
    // carry-forward preview at
    // app/templates/grid/_carry_forward_preview_modal.html).  Marked
    // by ``data-modal-auto-show`` on the .modal element.  After
    // hide, leave the markup in place so a subsequent hx-get swap
    // replaces it cleanly without orphaning Bootstrap state.
    target.querySelectorAll('[data-modal-auto-show]').forEach(function(modalEl) {
      bootstrap.Modal.getOrCreateInstance(modalEl).show();
    });
  }
});

// Carry-forward modal: surface confirm-time validation failures.
// The Confirm button posts to /pay-periods/<id>/carry-forward; on a
// race-condition validation failure the route returns 4xx with a
// human-readable body.  HTMX defaults to NOT swapping on 4xx, so we
// project the response text into the modal's alert box ourselves.
// On success the route returns 200 + HX-Trigger=gridRefresh which
// reloads the page (handled above), tearing down the modal in the
// process.
document.body.addEventListener("htmx:responseError", function(event) {
  var elt = event.detail.elt;
  if (!elt || !elt.matches || !elt.matches("[data-carry-forward-confirm]")) {
    return;
  }
  var modal = elt.closest(".modal");
  if (!modal) return;
  var alertBox = modal.querySelector("[data-carry-forward-error]");
  if (!alertBox) return;
  alertBox.textContent =
    (event.detail.xhr && event.detail.xhr.responseText)
      ? event.detail.xhr.responseText
      : "An error occurred while carrying forward.";
  alertBox.classList.remove("d-none");
});

// Close Add Transaction modal and reload after successful creation.
document.body.addEventListener("htmx:afterRequest", function(event) {
    if (!event.detail.successful) return;
    var form = event.detail.elt;
    if (form && form.hasAttribute('data-modal-auto-close')) {
        var modal = bootstrap.Modal.getInstance(
            document.getElementById('addTransactionModal')
        );
        if (modal) modal.hide();
        location.reload();
    }
});

// Delegated handlers for salary pages (CSP-compliant, replaces inline onclick/onchange).
document.addEventListener('click', function(e) {
    // Toggle target element (e.g. deduction form collapse)
    var toggleBtn = e.target.closest('[data-toggle-target]');
    if (toggleBtn) {
        var target = document.getElementById(toggleBtn.dataset.toggleTarget);
        if (target) target.classList.toggle('show');
        // If the button also has a reset attribute, reset the corresponding form.
        if (toggleBtn.hasAttribute('data-raise-reset')) {
            _resetRaiseForm();
        }
        if (toggleBtn.hasAttribute('data-ded-reset')) {
            _resetDeductionForm();
        }
        return;
    }

    // Edit raise: populate the add form with existing data and switch to edit mode.
    var editBtn = e.target.closest('[data-raise-edit]');
    if (editBtn) {
        _populateRaiseForm(editBtn);
        return;
    }

    // Edit deduction: populate the add form with existing data and switch to edit mode.
    var dedEditBtn = e.target.closest('[data-ded-edit]');
    if (dedEditBtn) {
        _populateDeductionForm(dedEditBtn);
        return;
    }

    // Period select navigation (breakdown page)
    var navBtn = e.target.closest('[data-action="period-navigate"]');
    if (navBtn) {
        var sel = document.getElementById(navBtn.dataset.selectId);
        if (sel) window.location.href = sel.value;
        return;
    }
});

// Populate the raise form fields from the edit button's data attributes
// and switch the form action/hx-post to the update endpoint.
function _populateRaiseForm(editBtn) {
    var form = document.getElementById('raise-form');
    if (!form) return;

    var editUrl = editBtn.dataset.raiseEditUrl;
    form.action = editUrl;
    form.setAttribute('hx-post', editUrl);
    // Re-process hx-post after changing it dynamically.
    if (window.htmx) htmx.process(form);

    // Populate fields.
    var sel = form.querySelector('[name=raise_type_id]');
    if (sel) sel.value = editBtn.dataset.raiseTypeId;

    var month = form.querySelector('[name=effective_month]');
    if (month) month.value = editBtn.dataset.raiseMonth;

    var year = form.querySelector('[name=effective_year]');
    if (year) year.value = editBtn.dataset.raiseYear;

    var pct = form.querySelector('[name=percentage]');
    var flat = form.querySelector('[name=flat_amount]');
    if (pct) pct.value = editBtn.dataset.raisePercentage || '';
    if (flat) flat.value = editBtn.dataset.raiseFlat || '';

    var recur = form.querySelector('[name=is_recurring]');
    if (recur) recur.checked = editBtn.dataset.raiseRecurring === 'true';

    // Optimistic-locking pin (commit C-18 / F-010): submit the
    // raise's version_id so the route handler can detect a stale
    // form before applying the update.  Cleared by _resetRaiseForm
    // when the form returns to add mode.
    var versionInput = form.querySelector('[name=version_id]');
    if (versionInput) versionInput.value = editBtn.dataset.raiseVersionId || '';

    // Change submit button icon to a check mark.
    var btn = document.getElementById('raise-submit-btn');
    if (btn) btn.innerHTML = '<i class="bi bi-check-lg"></i>';

    // Expand the form if collapsed.
    var formDiv = document.getElementById('add-raise-form');
    if (formDiv && !formDiv.classList.contains('show')) {
        formDiv.classList.add('show');
    }
}

// Reset the raise form to add mode (clear fields, restore action).
function _resetRaiseForm() {
    var form = document.getElementById('raise-form');
    if (!form) return;

    var addUrl = form.dataset.addAction;
    if (addUrl) {
        form.action = addUrl;
        form.setAttribute('hx-post', addUrl);
        if (window.htmx) htmx.process(form);
    }

    form.reset();

    // Clear the optimistic-lock pin so a subsequent add submission
    // does not carry a stale version from a prior edit cycle.
    var versionInput = form.querySelector('[name=version_id]');
    if (versionInput) versionInput.value = '';

    var btn = document.getElementById('raise-submit-btn');
    if (btn) btn.innerHTML = '<i class="bi bi-plus"></i>';
}

// Populate the deduction form fields from the edit button's data attributes
// and switch the form action/hx-post to the update endpoint.
function _populateDeductionForm(editBtn) {
    var form = document.getElementById('deduction-form');
    if (!form) return;

    var editUrl = editBtn.dataset.dedEditUrl;
    form.action = editUrl;
    form.setAttribute('hx-post', editUrl);
    if (window.htmx) htmx.process(form);

    // Populate fields.
    var name = form.querySelector('[name=name]');
    if (name) name.value = editBtn.dataset.dedName;

    var timing = form.querySelector('[name=deduction_timing_id]');
    if (timing) timing.value = editBtn.dataset.dedTimingId;

    var method = form.querySelector('[name=calc_method_id]');
    if (method) {
        method.value = editBtn.dataset.dedMethodId;
        // Trigger change event so the label updater runs.
        method.dispatchEvent(new Event('change', { bubbles: true }));
    }

    var amount = form.querySelector('[name=amount]');
    if (amount) amount.value = editBtn.dataset.dedAmount || '';

    var perYear = form.querySelector('[name=deductions_per_year]');
    if (perYear) perYear.value = editBtn.dataset.dedPerYear || '26';

    var cap = form.querySelector('[name=annual_cap]');
    if (cap) cap.value = editBtn.dataset.dedCap || '';

    var target = form.querySelector('[name=target_account_id]');
    if (target) target.value = editBtn.dataset.dedTargetAccount || '';

    var inflEnabled = form.querySelector('[name=inflation_enabled]');
    if (inflEnabled) inflEnabled.checked = editBtn.dataset.dedInflationEnabled === 'true';

    var inflRate = form.querySelector('[name=inflation_rate]');
    if (inflRate) inflRate.value = editBtn.dataset.dedInflationRate || '';

    var inflMonth = form.querySelector('[name=inflation_effective_month]');
    if (inflMonth) inflMonth.value = editBtn.dataset.dedInflationMonth || '';

    // Optimistic-locking pin (commit C-18 / F-010): submit the
    // deduction's version_id so the route handler can detect a
    // stale form before applying the update.  Cleared by
    // _resetDeductionForm when the form returns to add mode.
    var versionInput = form.querySelector('[name=version_id]');
    if (versionInput) versionInput.value = editBtn.dataset.dedVersionId || '';

    // Change submit button text.
    var btn = document.getElementById('ded-submit-btn');
    if (btn) btn.innerHTML = '<i class="bi bi-check-lg"></i> Update';

    // Expand the form if collapsed.
    var formDiv = document.getElementById('add-deduction-form');
    if (formDiv && !formDiv.classList.contains('show')) {
        formDiv.classList.add('show');
    }
}

// Reset the deduction form to add mode.
function _resetDeductionForm() {
    var form = document.getElementById('deduction-form');
    if (!form) return;

    var addUrl = form.dataset.addAction;
    if (addUrl) {
        form.action = addUrl;
        form.setAttribute('hx-post', addUrl);
        if (window.htmx) htmx.process(form);
    }

    form.reset();

    // Clear the optimistic-lock pin so a subsequent add submission
    // does not carry a stale version from a prior edit cycle.
    var versionInput = form.querySelector('[name=version_id]');
    if (versionInput) versionInput.value = '';

    var btn = document.getElementById('ded-submit-btn');
    if (btn) btn.innerHTML = '<i class="bi bi-plus"></i> Add';
}

// Update deduction form labels when calc method changes.
document.addEventListener('change', function(e) {
    if (!e.target.matches('[data-action="update-deduction-labels"]')) return;
    var form = e.target.closest('form');
    if (!form) return;
    var amountInput = form.querySelector('[name=amount]');
    var label = form.querySelector('.amount-label');
    if (e.target.selectedOptions[0].textContent.trim().toLowerCase() === 'percentage') {
        if (amountInput) { amountInput.placeholder = 'e.g. 6 for 6%'; amountInput.step = '0.01'; }
        if (label) label.textContent = 'Amount (%)';
    } else {
        if (amountInput) { amountInput.placeholder = 'e.g. 500'; amountInput.step = '0.01'; }
        if (label) label.textContent = 'Amount ($)';
    }
});

// --- Confirmation Modal (replaces browser confirm()) ---
// Forms with data-confirm="message" show a Bootstrap modal instead of confirm().
(function() {
  var pendingForm = null;

  document.addEventListener('submit', function(e) {
    var form = e.target;
    var message = form.getAttribute('data-confirm');
    if (!message) return;

    e.preventDefault();
    pendingForm = form;

    var modal = document.getElementById('confirmModal');
    if (!modal) { if (confirm(message)) form.submit(); return; }

    document.getElementById('confirmModalBody').textContent = message;
    new bootstrap.Modal(modal).show();
  });

  document.addEventListener('click', function(e) {
    if (e.target.id === 'confirmModalYes' && pendingForm) {
      bootstrap.Modal.getInstance(document.getElementById('confirmModal')).hide();
      // Remove the data-confirm to avoid re-triggering on submit.
      pendingForm.removeAttribute('data-confirm');
      pendingForm.submit();
      pendingForm = null;
    }
  });
})();

// --- Keyboard Help Modal (? key) ---
document.addEventListener('keydown', function(e) {
  if (e.key === '?' && !e.ctrlKey && !e.metaKey) {
    var tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (document.querySelector('.modal.show')) return;
    var modal = document.getElementById('keyboardHelpModal');
    if (modal) {
      e.preventDefault();
      new bootstrap.Modal(modal).show();
    }
  }
});

// --- Keyboard Navigation ---
// Tracks the focused cell by [row, col] in the grid table.
// Supports arrow keys, Tab, Enter, Escape, Space, Ctrl+Left/Right, Home, End.
(function() {
  var focusedRow = -1;
  var focusedCol = -1;
  var gridTable = null;

  function getGridTable() {
    if (!gridTable) gridTable = document.querySelector('.grid-table');
    return gridTable;
  }

  /** Return all data rows (tbody tr that are not banners/spacers/group headers). */
  function getDataRows() {
    var table = getGridTable();
    if (!table) return [];
    return Array.from(table.querySelectorAll('tbody tr')).filter(function(tr) {
      return !tr.classList.contains('section-banner-income') &&
             !tr.classList.contains('section-banner-expense') &&
             !tr.classList.contains('spacer-row') &&
             !tr.classList.contains('group-header-row') &&
             !tr.classList.contains('subtotal-row') &&
             !tr.classList.contains('net-cash-flow-row');
    });
  }

  /** Return the number of data columns (excluding the sticky label column).
      The period header is the LAST thead row -- the C3 month band sits
      above it with one th per month, so counting the first row would
      undercount the columns. */
  function getColCount() {
    var table = getGridTable();
    if (!table) return 0;
    var headerRow = table.querySelector('thead tr:last-child');
    if (!headerRow) return 0;
    // Subtract 1 for the sticky label column
    return headerRow.children.length - 1;
  }

  /** Column index of the current pay period (0 = first data column),
      so keyboard navigation starts where the daily loop happens. */
  function currentPeriodCol() {
    var table = getGridTable();
    if (!table) return 0;
    var headerRow = table.querySelector('thead tr:last-child');
    if (!headerRow) return 0;
    var cur = headerRow.querySelector('th.current-period');
    if (!cur) return 0;
    var idx = Array.prototype.indexOf.call(headerRow.children, cur) - 1;
    return idx >= 0 ? idx : 0;
  }

  function clearFocus() {
    var prev = document.querySelector('.grid-table td.cell-focused');
    if (prev) prev.classList.remove('cell-focused');
  }

  function setFocus(row, col) {
    var rows = getDataRows();
    var colCount = getColCount();
    if (rows.length === 0 || colCount === 0) return;

    // Clamp
    row = Math.max(0, Math.min(row, rows.length - 1));
    col = Math.max(0, Math.min(col, colCount - 1));

    clearFocus();
    focusedRow = row;
    focusedCol = col;

    // col+1 because first td is the sticky label
    var td = rows[row].children[col + 1];
    if (td) {
      td.classList.add('cell-focused');
      td.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
  }

  function getFocusedCell() {
    var rows = getDataRows();
    if (focusedRow < 0 || focusedRow >= rows.length) return null;
    return rows[focusedRow].children[focusedCol + 1] || null;
  }

  document.addEventListener('keydown', function(e) {
    var table = getGridTable();
    if (!table) return;

    // Don't intercept when typing in form fields.
    // Quick edit and full edit keyboard handling is in grid_edit.js.
    var tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
      return;
    }

    // Don't interfere with modal or popover interactions
    if (document.querySelector('.modal.show')) return;
    if (typeof activePopover !== 'undefined' && activePopover) return;

    var rows = getDataRows();
    var colCount = getColCount();
    if (rows.length === 0 || colCount === 0) return;

    // Initialize focus if not set -- start in the current period's
    // column, where the daily loop happens.
    if (focusedRow < 0) {
      if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Tab', 'Enter', 'Home', 'End'].indexOf(e.key) !== -1) {
        e.preventDefault();
        setFocus(0, currentPeriodCol());
        return;
      }
      return;
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setFocus(focusedRow + 1, focusedCol);
        break;
      case 'ArrowUp':
        e.preventDefault();
        setFocus(focusedRow - 1, focusedCol);
        break;
      case 'ArrowRight':
        if (e.ctrlKey || e.metaKey) {
          // Ctrl+Right: shift period window forward
          e.preventDefault();
          var rightArrow = document.querySelector('a[title="Later"]');
          if (rightArrow) rightArrow.click();
        } else {
          e.preventDefault();
          setFocus(focusedRow, focusedCol + 1);
        }
        break;
      case 'ArrowLeft':
        if (e.ctrlKey || e.metaKey) {
          // Ctrl+Left: shift period window backward
          e.preventDefault();
          var leftArrow = document.querySelector('a[title="Earlier"]');
          if (leftArrow) leftArrow.click();
        } else {
          e.preventDefault();
          setFocus(focusedRow, focusedCol - 1);
        }
        break;
      case 'Tab':
        e.preventDefault();
        if (e.shiftKey) {
          // Move backward
          if (focusedCol > 0) {
            setFocus(focusedRow, focusedCol - 1);
          } else if (focusedRow > 0) {
            setFocus(focusedRow - 1, colCount - 1);
          }
        } else {
          // Move forward
          if (focusedCol < colCount - 1) {
            setFocus(focusedRow, focusedCol + 1);
          } else if (focusedRow < rows.length - 1) {
            setFocus(focusedRow + 1, 0);
          }
        }
        break;
      case 'Enter':
        // Enter opens the cell's edit surface: the anchored action
        // card for a transaction (.txn-open), or quick-create on an
        // empty cell (rebuild decision 3, docs/design/grid_audit.md).
        e.preventDefault();
        var cell = getFocusedCell();
        if (cell) {
          var clickable = cell.querySelector(
            '.txn-open[data-txn-id], .txn-empty-cell');
          if (clickable) clickable.click();
        }
        break;
      case 'Escape':
        e.preventDefault();
        clearFocus();
        focusedRow = -1;
        focusedCol = -1;
        break;
      case ' ':
        // Space marks the focused cell paid via its one-click check
        // button -- the button only renders on projected cells, the
        // exact precondition of the projected -> done transition, so
        // Space on any other cell is a deliberate no-op.
        e.preventDefault();
        var spaceCell = getFocusedCell();
        if (spaceCell) {
          var payBtn = spaceCell.querySelector('.paybtn');
          if (payBtn) payBtn.click();
        }
        break;
      case 'c':
      case 'C':
        // C marks the focused cell credit.  The template stamps
        // data-can-credit with the same predicate as the card's
        // Credit button (expense, projected, not a transfer shadow,
        // not an envelope), so this cannot fire where the button
        // would not render.
        var creditCell = getFocusedCell();
        var creditable = creditCell
          && creditCell.querySelector('.txn-open[data-can-credit]');
        if (creditable) {
          e.preventDefault();
          htmx.ajax('POST',
            '/transactions/' + creditable.dataset.txnId + '/mark-credit',
            {
              target: '#txn-cell-' + creditable.dataset.txnId,
              swap: 'innerHTML',
            });
        }
        break;
      case 'Home':
        e.preventDefault();
        setFocus(focusedRow, 0);
        break;
      case 'End':
        e.preventDefault();
        setFocus(focusedRow, colCount - 1);
        break;
    }
  });

  // Restore focus after HTMX swaps
  document.body.addEventListener('htmx:afterSwap', function() {
    if (focusedRow >= 0 && focusedCol >= 0) {
      // Re-apply focus after a short delay to allow DOM to settle
      setTimeout(function() {
        setFocus(focusedRow, focusedCol);
      }, 50);
    }
  });

  // Allow clicking a cell to set focus
  document.addEventListener('click', function(e) {
    var table = getGridTable();
    if (!table) return;
    var td = e.target.closest('td.cell');
    if (td) {
      var tr = td.parentElement;
      var rows = getDataRows();
      var rowIdx = rows.indexOf(tr);
      if (rowIdx >= 0) {
        // Find column index (subtract 1 for sticky col)
        var colIdx = Array.from(tr.children).indexOf(td) - 1;
        if (colIdx >= 0) {
          focusedRow = rowIdx;
          focusedCol = colIdx;
          clearFocus();
          td.classList.add('cell-focused');
        }
      }
    }
  });
})();

// --- Toast Notifications ---
// Initialize Bootstrap toasts on page load.
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.toast').forEach(function(el) {
    new bootstrap.Toast(el).show();
  });

  // Initialize Bootstrap popovers (retirement info icons)
  document.querySelectorAll('[data-bs-toggle="popover"]').forEach(function(el) {
    new bootstrap.Popover(el);
  });

  // Initialize Bootstrap tooltips (transaction status badges)
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function(el) {
    new bootstrap.Tooltip(el);
  });
});
