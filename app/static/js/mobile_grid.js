/**
 * mobile_grid.js -- Period navigation, swipe gestures, and tap-to-edit
 * for the mobile card-based budget grid.
 *
 * Depends on grid_edit.js (openFullEdit, openTransferFullEdit) being
 * loaded first.
 */
(function() {
    'use strict';

    function init() {
        var panels = document.querySelectorAll('.mobile-period-panel');
        if (!panels.length) return;

        var currentIndex = 0;
        var prevBtn = document.getElementById('mobile-prev-btn');
        var nextBtn = document.getElementById('mobile-next-btn');

        function updateLabel() {
            var panel = panels[currentIndex];
            var labelEl = document.getElementById('mobile-period-label');
            var rangeEl = document.getElementById('mobile-period-range');
            if (labelEl) labelEl.textContent = panel.dataset.periodLabel;
            if (rangeEl) rangeEl.textContent = panel.dataset.periodRange;
            if (prevBtn) prevBtn.disabled = (currentIndex === 0);
            if (nextBtn) nextBtn.disabled = (currentIndex === panels.length - 1);
        }

        function navigate(delta) {
            var newIndex = currentIndex + delta;
            if (newIndex < 0 || newIndex >= panels.length) return;
            panels[currentIndex].style.display = 'none';
            currentIndex = newIndex;
            panels[currentIndex].style.display = '';
            updateLabel();
        }

        if (prevBtn) prevBtn.addEventListener('click', function() { navigate(-1); });
        if (nextBtn) nextBtn.addEventListener('click', function() { navigate(1); });

        // Swipe detection on the mobile grid container.
        var grid = document.getElementById('mobile-grid');
        if (grid) {
            var touchStartX = 0;
            var touchStartY = 0;

            grid.addEventListener('touchstart', function(e) {
                touchStartX = e.changedTouches[0].clientX;
                touchStartY = e.changedTouches[0].clientY;
            }, { passive: true });

            grid.addEventListener('touchend', function(e) {
                var dx = e.changedTouches[0].clientX - touchStartX;
                var dy = e.changedTouches[0].clientY - touchStartY;
                // Only trigger on horizontal swipes exceeding 50px threshold.
                if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
                    navigate(dx < 0 ? 1 : -1);
                }
            }, { passive: true });
        }

        // Tap-to-edit: delegated click handler for mobile transaction cards.
        // Transfer shadows open the transfer full-edit; others open the
        // transaction full-edit.  Both use the Commit #9 bottom sheet.
        document.addEventListener('click', function(e) {
            var card = e.target.closest('.mobile-txn-card[data-mobile-txn-id]');
            if (!card) return;

            var xferId = card.dataset.mobileXferId;
            if (xferId && typeof openTransferFullEdit === 'function') {
                openTransferFullEdit(parseInt(xferId), card);
            } else if (typeof openFullEdit === 'function') {
                openFullEdit(parseInt(card.dataset.mobileTxnId), card);
            }
        });

        updateLabel();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
