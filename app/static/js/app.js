/**
 * Shekel Budget App -- Client-Side JavaScript
 *
 * Minimal JS: HTMX handles most interactivity server-side.
 * This file provides the theme toggle and small UX helpers.
 */

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
if (addModal) {
  addModal.addEventListener("show.bs.modal", function() {
    var form = addModal.querySelector("form");
    if (form) form.reset();
  });
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
        // If the button also has data-raise-reset, reset the raise form to add mode.
        if (toggleBtn.hasAttribute('data-raise-reset')) {
            _resetRaiseForm();
        }
        return;
    }

    // Edit raise: populate the add form with existing data and switch to edit mode.
    var editBtn = e.target.closest('[data-raise-edit]');
    if (editBtn) {
        _populateRaiseForm(editBtn);
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

    var btn = document.getElementById('raise-submit-btn');
    if (btn) btn.innerHTML = '<i class="bi bi-plus"></i>';
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
             !tr.classList.contains('group-header-row');
    });
  }

  /** Return the number of data columns (excluding the sticky label column). */
  function getColCount() {
    var table = getGridTable();
    if (!table) return 0;
    var headerRow = table.querySelector('thead tr');
    if (!headerRow) return 0;
    // Subtract 1 for the sticky label column
    return headerRow.children.length - 1;
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

    // Initialize focus if not set
    if (focusedRow < 0) {
      if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Tab', 'Enter', 'Home', 'End'].indexOf(e.key) !== -1) {
        e.preventDefault();
        setFocus(0, 0);
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
        e.preventDefault();
        var cell = getFocusedCell();
        if (cell) {
          var clickable = cell.querySelector('.txn-cell');
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
        // Space: toggle status (click the cell to open edit form)
        e.preventDefault();
        var spaceCell = getFocusedCell();
        if (spaceCell) {
          var txnCell = spaceCell.querySelector('.txn-cell');
          if (txnCell) txnCell.click();
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
});
