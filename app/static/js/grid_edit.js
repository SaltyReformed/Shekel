/**
 * grid_edit.js -- Two-tier editing for the Shekel budget grid.
 *
 * Tier 1: Quick edit -- single inline amount input inside the cell.
 * Tier 2: Full edit -- floating popover with all fields, anchored to the cell.
 *
 * Supports both editing existing transactions and creating new ones
 * from empty cells. Create-mode forms have data-mode="create".
 */

var activePopover = null;

/**
 * Position and show the popover below (or above) the given cell.
 *
 * The popover uses position:fixed so coordinates are relative to the
 * viewport.  This allows it to escape the grid-scroll-wrapper's
 * overflow:auto clipping -- the sticky footer cannot obscure the
 * popover's action buttons.
 *
 * Returns the popover element for further setup.
 */
function positionPopover(cell) {
    var popover = document.getElementById('txn-popover');
    if (!popover) return null;

    // Close any existing popover first.
    closeFullEdit();

    // Mobile: bottom sheet -- CSS handles positioning via fixed position.
    if (window.innerWidth < 768) {
        var backdrop = document.createElement('div');
        backdrop.className = 'bottom-sheet-backdrop';
        backdrop.id = 'bottom-sheet-backdrop';
        backdrop.addEventListener('click', closeFullEdit);
        document.body.appendChild(backdrop);

        // Clear any leftover desktop inline positioning
        popover.style.top = '';
        popover.style.left = '';
        return popover;
    }

    // Desktop: existing positioning logic
    // Viewport-relative coordinates from the triggering cell.
    var cellRect = cell.getBoundingClientRect();

    // Estimated popover height for flip-above logic.  The actual height
    // is unknown until content is injected, so use a conservative guess.
    var popoverHeight = 300;
    var topPos = cellRect.bottom;
    var leftPos = cellRect.left;

    // If the popover would extend below the viewport, open above the cell.
    if (cellRect.bottom + popoverHeight > window.innerHeight) {
        topPos = cellRect.top - popoverHeight;
        // Clamp to top of viewport if the cell is very near the top.
        if (topPos < 0) topPos = 0;
    }

    // Clamp horizontal position so the popover does not overflow the
    // right edge of the viewport.
    if (leftPos + 280 > window.innerWidth) {
        leftPos = window.innerWidth - 290;
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

    if (window.innerWidth < 768) {
        document.body.style.overflow = 'hidden';
    }

    // Process any HTMX attributes in the loaded content.
    htmx.process(popover);

    // Focus the first visible input.
    const firstInput = popover.querySelector('input[type="number"], input[type="text"], select');
    if (firstInput) firstInput.focus();

    // Add click-outside listener after a tick to avoid catching the trigger click.
    setTimeout(function() {
        document.addEventListener('click', handleClickOutside);
    }, 0);

    // Close the popover if the grid scrolls -- position:fixed means
    // the popover would float detached from its cell otherwise.
    var wrapper = document.querySelector('.grid-scroll-wrapper');
    if (wrapper) {
        wrapper.addEventListener('scroll', closeFullEdit);
    }
}

/**
 * Open the full edit popover anchored to the cell containing the trigger.
 * Loads the form HTML via fetch and positions the popover below the cell.
 */
function openFullEdit(txnId, triggerEl) {
    const cell = triggerEl.closest('td');
    const popover = positionPopover(cell);
    if (!popover) return;

    // Load the full edit form via fetch.
    fetch('/transactions/' + txnId + '/full-edit', {
        headers: { 'HX-Request': 'true' }
    })
    .then(function(r) { return r.text(); })
    .then(function(html) {
        showPopover(popover, html);
    })
    .catch(function() {
        closeFullEdit();
    });
}

/**
 * Open the full edit popover for a transfer.
 * Same flow as openFullEdit but fetches the transfer full-edit endpoint.
 */
function openTransferFullEdit(xferId, triggerEl) {
    const cell = triggerEl.closest('td');
    const popover = positionPopover(cell);
    if (!popover) return;

    fetch('/transfers/' + xferId + '/full-edit', {
        headers: { 'HX-Request': 'true' }
    })
    .then(function(r) { return r.text(); })
    .then(function(html) {
        showPopover(popover, html);
    })
    .catch(function() {
        closeFullEdit();
    });
}

/**
 * Open the full create popover for an empty cell.
 * Loads the create form via fetch, anchored to the cell.
 */
function openFullCreate(categoryId, periodId, txnTypeId, accountId, triggerEl) {
    const cell = triggerEl.closest('td');
    const popover = positionPopover(cell);
    if (!popover) return;

    // Give the cell a stable id so the popover form can target it.
    if (!cell.id) {
        cell.id = 'empty-cell-' + categoryId + '-' + periodId;
    }

    // Load the full create form via fetch.
    fetch('/transactions/new/full?category_id=' + categoryId +
          '&period_id=' + periodId +
          '&transaction_type_id=' + encodeURIComponent(txnTypeId) +
          '&account_id=' + accountId, {
        headers: { 'HX-Request': 'true' }
    })
    .then(function(r) { return r.text(); })
    .then(function(html) {
        popover.innerHTML = html;
        popover.classList.remove('d-none');
        activePopover = popover;

        if (window.innerWidth < 768) {
            document.body.style.overflow = 'hidden';
        }

        // Override the form's hx-target before HTMX processes it.
        // The popover is outside the td, so "closest td" won't work.
        const form = popover.querySelector('form');
        if (form) {
            form.setAttribute('hx-target', '#' + cell.id);
        }

        // Now process HTMX attributes with the correct target.
        htmx.process(popover);

        // Focus the first number input.
        const firstInput = popover.querySelector('input[type="number"]');
        if (firstInput) firstInput.focus();

        // Add click-outside listener after a tick.
        setTimeout(function() {
            document.addEventListener('click', handleClickOutside);
        }, 0);

        // Close on grid scroll (position:fixed detaches from cell).
        var wrapper = document.querySelector('.grid-scroll-wrapper');
        if (wrapper) {
            wrapper.addEventListener('scroll', closeFullEdit);
        }
    })
    .catch(function() {
        closeFullEdit();
    });
}

/**
 * Close the full edit popover and clean up listeners.
 */
function closeFullEdit() {
    var backdrop = document.getElementById('bottom-sheet-backdrop');
    if (backdrop) backdrop.remove();
    document.body.style.overflow = '';

    var popover = document.getElementById('txn-popover');
    if (popover) {
        popover.classList.add('d-none');
        popover.innerHTML = '';
    }
    activePopover = null;
    document.removeEventListener('click', handleClickOutside);

    // Remove the scroll listener that was added in showPopover /
    // openFullCreate to close the popover when the grid scrolls.
    var wrapper = document.querySelector('.grid-scroll-wrapper');
    if (wrapper) {
        wrapper.removeEventListener('scroll', closeFullEdit);
    }
}

/**
 * Click-outside handler -- closes the popover when clicking anywhere else.
 */
function handleClickOutside(event) {
    const popover = document.getElementById('txn-popover');
    if (popover && !popover.contains(event.target)) {
        closeFullEdit();
    }
}

// --- Keyboard handlers for quick edit/create and full edit ---
document.addEventListener('keydown', function(e) {
    // F2 in quick edit/create → open full edit/create popover.
    if (e.key === 'F2') {
        const quickInput = document.activeElement;
        if (quickInput && quickInput.closest('.txn-quick-edit')) {
            e.preventDefault();
            const quickForm = quickInput.closest('.txn-quick-edit');

            // Transfer expand button takes priority when present.
            const xferBtn = quickForm.querySelector('.xfer-expand-btn');
            if (xferBtn) {
                openTransferFullEdit(parseInt(xferBtn.dataset.xferId), quickInput);
                return;
            }

            // Transaction expand button.
            const expandBtn = quickForm.querySelector('.txn-expand-btn');
            if (quickForm.dataset.mode === 'create') {
                openFullCreate(
                    parseInt(expandBtn.dataset.categoryId),
                    parseInt(expandBtn.dataset.periodId),
                    parseInt(expandBtn.dataset.txnTypeId),
                    parseInt(expandBtn.dataset.accountId),
                    quickInput
                );
            } else {
                openFullEdit(parseInt(expandBtn.dataset.txnId), quickInput);
            }
            return;
        }
    }

    // Escape -- cancel quick edit/create or close full edit popover.
    if (e.key === 'Escape') {
        // Close full edit/create popover if open.
        if (activePopover) {
            e.preventDefault();
            closeFullEdit();
            return;
        }

        // Cancel quick edit or quick create.
        const quickInput = document.activeElement;
        if (quickInput && quickInput.closest('.txn-quick-edit')) {
            e.preventDefault();
            const quickForm = quickInput.closest('.txn-quick-edit');

            // Transfer quick edit: restore the transfer cell display.
            const xferBtn = quickForm.querySelector('.xfer-expand-btn');
            if (xferBtn) {
                const targetDiv = document.getElementById('xfer-cell-' + xferBtn.dataset.xferId);
                if (targetDiv) {
                    htmx.ajax('GET', '/transfers/cell/' + xferBtn.dataset.xferId, {
                        target: targetDiv,
                        swap: 'innerHTML'
                    });
                }
                return;
            }

            // Transaction quick edit/create.
            const expandBtn = quickForm.querySelector('.txn-expand-btn');
            if (quickForm.dataset.mode === 'create') {
                const td = quickForm.closest('td');
                if (td) {
                    htmx.ajax('GET',
                        '/transactions/empty-cell?category_id=' + expandBtn.dataset.categoryId +
                        '&period_id=' + expandBtn.dataset.periodId +
                        '&transaction_type_id=' + encodeURIComponent(expandBtn.dataset.txnTypeId) +
                        '&account_id=' + expandBtn.dataset.accountId,
                        { target: td, swap: 'innerHTML' }
                    );
                }
            } else {
                const targetDiv = document.getElementById('txn-cell-' + expandBtn.dataset.txnId);
                if (targetDiv) {
                    htmx.ajax('GET', '/transactions/' + expandBtn.dataset.txnId + '/cell', {
                        target: targetDiv,
                        swap: 'innerHTML'
                    });
                }
            }
            return;
        }
    }

});

// --- Delegated click handlers (CSP-compliant, replaces inline onclick) ---
document.addEventListener('click', function(e) {
    // Open full edit popover (expand button in quick-edit mode)
    var editBtn = e.target.closest('.txn-expand-btn[data-txn-id]');
    if (editBtn) {
        openFullEdit(parseInt(editBtn.dataset.txnId), editBtn);
        return;
    }

    // Open full edit popover for transfers (expand button in transfer quick-edit)
    var xferEditBtn = e.target.closest('.xfer-expand-btn[data-xfer-id]');
    if (xferEditBtn) {
        openTransferFullEdit(parseInt(xferEditBtn.dataset.xferId), xferEditBtn);
        return;
    }

    // Open full create popover (expand button in quick-create mode)
    var createBtn = e.target.closest('.txn-expand-btn[data-category-id]');
    if (createBtn) {
        openFullCreate(
            parseInt(createBtn.dataset.categoryId),
            parseInt(createBtn.dataset.periodId),
            parseInt(createBtn.dataset.txnTypeId),
            parseInt(createBtn.dataset.accountId),
            createBtn
        );
        return;
    }

    // Close popover (close/cancel buttons)
    if (e.target.closest('[data-action="close-popover"]')) {
        closeFullEdit();
        return;
    }
});

// Auto-select text when quick-edit input receives focus.
document.addEventListener('focus', function(e) {
    if (e.target.matches('.txn-quick-input')) {
        e.target.select();
    }
}, true);
