/**
 * Shekel Budget App — Client-Side JavaScript
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

// Configure HTMX to include CSRF token if we add it later.
document.body.addEventListener("htmx:configRequest", function(event) {
  // Future: event.detail.headers["X-CSRFToken"] = getCsrfToken();
});

// Show a brief loading indicator during HTMX requests.
document.body.addEventListener("htmx:beforeRequest", function(event) {
  const target = event.detail.elt;
  if (target && target.closest && target.closest("td")) {
    target.closest("td").style.opacity = "0.6";
  }
});

document.body.addEventListener("htmx:afterRequest", function(event) {
  const target = event.detail.elt;
  if (target && target.closest && target.closest("td")) {
    target.closest("td").style.opacity = "1";
  }
});

// Consolidated htmx:afterSwap handler — save flash, popover close, focus restore.
document.body.addEventListener("htmx:afterSwap", function(event) {
  const el = event.detail.elt;

  // Save flash animation — only for transaction cell saves.
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
