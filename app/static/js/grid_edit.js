/**
 * grid_edit.js — Two-tier editing for the Shekel budget grid.
 *
 * Tier 1: Quick edit — single inline amount input inside the cell.
 * Tier 2: Full edit — floating popover with all fields, anchored to the cell.
 */

var activePopover = null;

/**
 * Open the full edit popover anchored to the cell containing the trigger.
 * Loads the form HTML via fetch and positions the popover below the cell.
 */
function openFullEdit(txnId, triggerEl) {
    var cell = triggerEl.closest('td');
    var popover = document.getElementById('txn-popover');
    var gridWrapper = cell.closest('.grid-scroll-wrapper');

    if (!popover || !gridWrapper) return;

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

    // Load the full edit form via fetch.
    fetch('/transactions/' + txnId + '/full-edit', {
        headers: { 'HX-Request': 'true' }
    })
    .then(function(r) { return r.text(); })
    .then(function(html) {
        popover.innerHTML = html;
        popover.classList.remove('d-none');
        activePopover = popover;

        // Process any HTMX attributes in the loaded content.
        htmx.process(popover);

        // Focus the first input.
        var firstInput = popover.querySelector('input, select');
        if (firstInput) firstInput.focus();
    });

    // Add click-outside listener after a tick to avoid catching the trigger click.
    setTimeout(function() {
        document.addEventListener('click', handleClickOutside);
    }, 0);
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

// --- Keyboard handlers for quick edit and full edit ---
document.addEventListener('keydown', function(e) {
    // F2 in quick edit → open full edit popover.
    if (e.key === 'F2') {
        var quickInput = document.activeElement;
        if (quickInput && quickInput.closest('.txn-quick-edit')) {
            e.preventDefault();
            var expandBtn = quickInput.closest('.txn-quick-edit')
                           .querySelector('.txn-expand-btn');
            var txnId = expandBtn.dataset.txnId;
            openFullEdit(parseInt(txnId), quickInput);
            return;
        }
    }

    // Escape — cancel quick edit or close full edit popover.
    if (e.key === 'Escape') {
        // Close full edit popover if open.
        if (activePopover) {
            e.preventDefault();
            closeFullEdit();
            return;
        }

        // Cancel quick edit — revert cell to display mode.
        var quickInput = document.activeElement;
        if (quickInput && quickInput.closest('.txn-quick-edit')) {
            e.preventDefault();
            var expandBtn = quickInput.closest('.txn-quick-edit')
                           .querySelector('.txn-expand-btn');
            var txnId = expandBtn.dataset.txnId;
            var targetDiv = document.getElementById('txn-cell-' + txnId);
            if (targetDiv) {
                htmx.ajax('GET', '/transactions/' + txnId + '/cell', {
                    target: targetDiv,
                    swap: 'innerHTML'
                });
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
