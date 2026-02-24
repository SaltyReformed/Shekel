/**
 * grid_edit.js — Two-tier editing for the Shekel budget grid.
 *
 * Tier 1: Quick edit — single inline amount input inside the cell.
 * Tier 2: Full edit — floating popover with all fields, anchored to the cell.
 *
 * Supports both editing existing transactions and creating new ones
 * from empty cells. Create-mode forms have data-mode="create".
 */

var activePopover = null;

/**
 * Position and show the popover below (or above) the given cell.
 * Returns the popover element for further setup.
 */
function positionPopover(cell) {
    var popover = document.getElementById('txn-popover');
    var gridWrapper = cell.closest('.grid-scroll-wrapper');
    if (!popover || !gridWrapper) return null;

    // Close any existing popover first.
    closeFullEdit();

    // Calculate position relative to the grid wrapper.
    var cellRect = cell.getBoundingClientRect();
    var wrapperRect = gridWrapper.getBoundingClientRect();
    var topPos = (cellRect.bottom - wrapperRect.top + gridWrapper.scrollTop);
    var leftPos = (cellRect.left - wrapperRect.left + gridWrapper.scrollLeft);

    // If the popover would go below the viewport, position above the cell.
    var viewportBottom = window.innerHeight;
    if (cellRect.bottom + 300 > viewportBottom) {
        topPos = (cellRect.top - wrapperRect.top + gridWrapper.scrollTop) - 300;
        if (topPos < 0) topPos = 0;
    }

    popover.style.top = topPos + 'px';
    popover.style.left = leftPos + 'px';

    return popover;
}

/**
 * Show the popover with loaded HTML content and set up click-outside.
 */
function showPopover(popover, html) {
    popover.innerHTML = html;
    popover.classList.remove('d-none');
    activePopover = popover;

    // Process any HTMX attributes in the loaded content.
    htmx.process(popover);

    // Focus the first visible input.
    var firstInput = popover.querySelector('input[type="number"], input[type="text"], select');
    if (firstInput) firstInput.focus();

    // Add click-outside listener after a tick to avoid catching the trigger click.
    setTimeout(function() {
        document.addEventListener('click', handleClickOutside);
    }, 0);
}

/**
 * Open the full edit popover anchored to the cell containing the trigger.
 * Loads the form HTML via fetch and positions the popover below the cell.
 */
function openFullEdit(txnId, triggerEl) {
    var cell = triggerEl.closest('td');
    var popover = positionPopover(cell);
    if (!popover) return;

    // Load the full edit form via fetch.
    fetch('/transactions/' + txnId + '/full-edit', {
        headers: { 'HX-Request': 'true' }
    })
    .then(function(r) { return r.text(); })
    .then(function(html) {
        showPopover(popover, html);
    });
}

/**
 * Open the full create popover for an empty cell.
 * Loads the create form via fetch, anchored to the cell.
 */
function openFullCreate(categoryId, periodId, txnTypeName, triggerEl) {
    var cell = triggerEl.closest('td');
    var popover = positionPopover(cell);
    if (!popover) return;

    // Give the cell a stable id so the popover form can target it.
    if (!cell.id) {
        cell.id = 'empty-cell-' + categoryId + '-' + periodId;
    }

    // Load the full create form via fetch.
    fetch('/transactions/new/full?category_id=' + categoryId +
          '&period_id=' + periodId +
          '&txn_type_name=' + encodeURIComponent(txnTypeName), {
        headers: { 'HX-Request': 'true' }
    })
    .then(function(r) { return r.text(); })
    .then(function(html) {
        popover.innerHTML = html;
        popover.classList.remove('d-none');
        activePopover = popover;

        // Override the form's hx-target before HTMX processes it.
        // The popover is outside the td, so "closest td" won't work.
        var form = popover.querySelector('form');
        if (form) {
            form.setAttribute('hx-target', '#' + cell.id);
        }

        // Now process HTMX attributes with the correct target.
        htmx.process(popover);

        // Focus the first number input.
        var firstInput = popover.querySelector('input[type="number"]');
        if (firstInput) firstInput.focus();

        // Add click-outside listener after a tick.
        setTimeout(function() {
            document.addEventListener('click', handleClickOutside);
        }, 0);
    });
}

/**
 * Close the full edit popover and clean up listeners.
 */
function closeFullEdit() {
    var popover = document.getElementById('txn-popover');
    if (popover) {
        popover.classList.add('d-none');
        popover.innerHTML = '';
    }
    activePopover = null;
    document.removeEventListener('click', handleClickOutside);
}

/**
 * Click-outside handler — closes the popover when clicking anywhere else.
 */
function handleClickOutside(event) {
    var popover = document.getElementById('txn-popover');
    if (popover && !popover.contains(event.target)) {
        closeFullEdit();
    }
}

// --- Keyboard handlers for quick edit/create and full edit ---
document.addEventListener('keydown', function(e) {
    // F2 in quick edit/create → open full edit/create popover.
    if (e.key === 'F2') {
        var quickInput = document.activeElement;
        if (quickInput && quickInput.closest('.txn-quick-edit')) {
            e.preventDefault();
            var quickForm = quickInput.closest('.txn-quick-edit');
            var expandBtn = quickForm.querySelector('.txn-expand-btn');

            if (quickForm.dataset.mode === 'create') {
                // Create mode — open full create popover.
                var categoryId = expandBtn.dataset.categoryId;
                var periodId = expandBtn.dataset.periodId;
                var txnTypeName = expandBtn.dataset.txnTypeName;
                openFullCreate(
                    parseInt(categoryId),
                    parseInt(periodId),
                    txnTypeName,
                    quickInput
                );
            } else {
                // Edit mode — open full edit popover.
                var txnId = expandBtn.dataset.txnId;
                openFullEdit(parseInt(txnId), quickInput);
            }
            return;
        }
    }

    // Escape — cancel quick edit/create or close full edit popover.
    if (e.key === 'Escape') {
        // Close full edit/create popover if open.
        if (activePopover) {
            e.preventDefault();
            closeFullEdit();
            return;
        }

        // Cancel quick edit or quick create.
        var quickInput = document.activeElement;
        if (quickInput && quickInput.closest('.txn-quick-edit')) {
            e.preventDefault();
            var quickForm = quickInput.closest('.txn-quick-edit');

            if (quickForm.dataset.mode === 'create') {
                // Create mode — revert to empty cell via server.
                var expandBtn = quickForm.querySelector('.txn-expand-btn');
                var categoryId = expandBtn.dataset.categoryId;
                var periodId = expandBtn.dataset.periodId;
                var txnTypeName = expandBtn.dataset.txnTypeName;
                var td = quickForm.closest('td');
                if (td) {
                    htmx.ajax('GET',
                        '/transactions/empty-cell?category_id=' + categoryId +
                        '&period_id=' + periodId +
                        '&txn_type_name=' + encodeURIComponent(txnTypeName),
                        { target: td, swap: 'innerHTML' }
                    );
                }
            } else {
                // Edit mode — revert cell to display mode.
                var expandBtn = quickForm.querySelector('.txn-expand-btn');
                var txnId = expandBtn.dataset.txnId;
                var targetDiv = document.getElementById('txn-cell-' + txnId);
                if (targetDiv) {
                    htmx.ajax('GET', '/transactions/' + txnId + '/cell', {
                        target: targetDiv,
                        swap: 'innerHTML'
                    });
                }
            }
            return;
        }
    }

});

// Close popover after a successful HTMX swap targeting a cell.
document.addEventListener('htmx:afterSwap', function(e) {
    if (activePopover && !activePopover.contains(e.detail.elt)) {
        closeFullEdit();
    }
});
