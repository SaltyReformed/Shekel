/**
 * Goal mode toggle -- show/hide fields based on goal type selection.
 *
 * The fixed-mode ID is read from a data attribute on the select element
 * (set server-side from the Jinja global) so this file never hardcodes
 * database IDs.
 */
(function() {
  var modeSelect = document.getElementById('goal_mode_id');
  if (!modeSelect) return;

  var fixedModeId = modeSelect.getAttribute('data-fixed-mode-id');
  var fixedFields = document.getElementById('fixed-fields');
  var incomeFields = document.getElementById('income-fields');

  function toggleMode() {
    var isFixed = modeSelect.value === fixedModeId;
    fixedFields.style.display = isFixed ? '' : 'none';
    incomeFields.style.display = isFixed ? 'none' : '';
  }

  modeSelect.addEventListener('change', toggleMode);

  // Set initial state on page load (for edit mode).
  toggleMode();
})();
