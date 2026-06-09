/**
 * Investment dashboard -- toggle employer contribution fields.
 *
 * The <select> posts the employer-contribution-type ref id (#38), so
 * the show/hide decision reads the selected option's semantic
 * data-name ("none" / "flat_percentage" / "match") rather than the
 * numeric value.
 */
(function() {
  var typeSelect = document.querySelector('select[name="employer_contribution_type_id"]');
  var container = document.getElementById('employer-fields-container');
  if (!typeSelect || !container) return;

  function selectedName() {
    var opt = typeSelect.options[typeSelect.selectedIndex];
    return opt ? opt.dataset.name : '';
  }

  function toggle() {
    var name = selectedName();
    container.style.display = (!name || name === 'none') ? 'none' : '';
  }

  typeSelect.addEventListener('change', toggle);
  toggle();
})();
