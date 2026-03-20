/**
 * Recurrence form — show/hide fields based on pattern selection.
 * Used by both recurring-transaction and recurring-transfer forms.
 */
(function() {
  var patternSelect = document.getElementById('recurrence_pattern');
  if (!patternSelect) return;

  var container = document.getElementById('recurrence-fields');
  var interval = document.getElementById('field-interval');
  var dom = document.getElementById('field-dom');
  var moy = document.getElementById('field-moy');
  var startPeriod = document.getElementById('field-start-period');
  var preview = document.getElementById('recurrence-preview');

  function toggleFields(pattern) {
    if (!pattern) {
      container.classList.add('d-none');
      return;
    }
    container.classList.remove('d-none');

    interval.classList.toggle('d-none', pattern !== 'every_n_periods');
    dom.classList.toggle('d-none', ['monthly', 'quarterly', 'semi_annual', 'annual'].indexOf(pattern) === -1);
    moy.classList.toggle('d-none', ['quarterly', 'semi_annual', 'annual'].indexOf(pattern) === -1);

    if (startPeriod) {
      startPeriod.classList.toggle('d-none', pattern === 'once');
    }

    fetchPreview();
  }

  function fetchPreview() {
    if (!preview) return;

    var pattern = patternSelect.value;
    if (!pattern || pattern === 'once') {
      preview.innerHTML = '<small class="text-muted">Select a pattern to see upcoming dates</small>';
      return;
    }

    var previewUrl = preview.getAttribute('data-preview-url');
    if (!previewUrl) return;

    var params = new URLSearchParams();
    params.set('recurrence_pattern', pattern);

    var intervalEl = document.getElementById('interval_n');
    if (intervalEl && intervalEl.value) params.set('interval_n', intervalEl.value);

    var domEl = document.getElementById('day_of_month');
    if (domEl && domEl.value) params.set('day_of_month', domEl.value);

    var moyEl = document.getElementById('month_of_year');
    if (moyEl && moyEl.value) params.set('month_of_year', moyEl.value);

    var spEl = document.querySelector('[name="start_period_id"]');
    if (spEl && spEl.value) params.set('start_period_id', spEl.value);

    fetch(previewUrl + '?' + params.toString())
      .then(function(r) { return r.text(); })
      .then(function(html) { preview.innerHTML = html; })
      .catch(function() { preview.innerHTML = '<small class="text-muted">Could not load preview</small>'; });
  }

  // Initialize on page load.
  toggleFields(patternSelect.value);

  // Listen for changes.
  patternSelect.addEventListener('change', function() { toggleFields(this.value); });
  ['interval_n', 'day_of_month', 'month_of_year'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', fetchPreview);
  });
  var spEl = document.querySelector('[name="start_period_id"]');
  if (spEl) spEl.addEventListener('change', fetchPreview);
})();
