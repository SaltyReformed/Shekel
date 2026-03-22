/**
 * Investment dashboard -- toggle employer contribution fields.
 */
(function() {
  var typeSelect = document.querySelector('select[name="employer_contribution_type"]');
  var container = document.getElementById('employer-fields-container');
  if (!typeSelect || !container) return;

  function toggle() {
    container.style.display = (!typeSelect.value || typeSelect.value === 'none') ? 'none' : '';
  }

  typeSelect.addEventListener('change', toggle);
  toggle();
})();
