/**
 * Recurrence form -- show/hide fields based on pattern selection.
 * Used by both recurring-transaction and recurring-transfer forms.
 *
 * Pattern IDs are read from data attributes on the select element
 * (set server-side from the ref cache) so this file never hardcodes
 * database IDs.
 */
(function() {
  var patternSelect = document.getElementById('recurrence_pattern');
  if (!patternSelect) return;

  // Read pattern IDs from data attributes set on the select element.
  var EVERY_N  = patternSelect.getAttribute('data-every-n');
  var MONTHLY  = patternSelect.getAttribute('data-monthly');
  var QUARTERLY = patternSelect.getAttribute('data-quarterly');
  var SEMI_ANNUAL = patternSelect.getAttribute('data-semi-annual');
  var ANNUAL   = patternSelect.getAttribute('data-annual');
  var ONCE     = patternSelect.getAttribute('data-once');

  var container = document.getElementById('recurrence-fields');
  var interval = document.getElementById('field-interval');
  var dom = document.getElementById('field-dom');
  var moy = document.getElementById('field-moy');
  var startPeriod = document.getElementById('field-start-period');
  var endDate = document.getElementById('field-end-date');
  var preview = document.getElementById('recurrence-preview');

  var startPeriodLabel = document.getElementById('start-period-label');
  var startPeriodHelp = document.getElementById('start-period-help');

  function toggleFields(pattern) {
    var isOneTime = !pattern || pattern === ONCE;

    if (isOneTime) {
      // One-time transfer: hide recurrence-specific fields, show
      // period selector so the user picks where it lands.
      container.classList.add('d-none');
      if (startPeriod) {
        startPeriod.classList.remove('d-none');
        if (startPeriodLabel) startPeriodLabel.textContent = 'Pay period';
        if (startPeriodHelp) startPeriodHelp.textContent = 'Which pay period should this transfer appear in?';
      }
      return;
    }

    // Recurring transfer: show recurrence fields.
    container.classList.remove('d-none');

    interval.classList.toggle('d-none', pattern !== EVERY_N);
    dom.classList.toggle('d-none', [MONTHLY, QUARTERLY, SEMI_ANNUAL, ANNUAL].indexOf(pattern) === -1);
    moy.classList.toggle('d-none', [QUARTERLY, SEMI_ANNUAL, ANNUAL].indexOf(pattern) === -1);

    if (startPeriod) {
      startPeriod.classList.remove('d-none');
      if (startPeriodLabel) startPeriodLabel.textContent = 'First paycheck';
      if (startPeriodHelp) startPeriodHelp.textContent = 'When should this first appear on the grid?';
    }

    if (endDate) {
      endDate.classList.remove('d-none');
    }

    fetchPreview();
  }

  function fetchPreview() {
    if (!preview) return;

    var pattern = patternSelect.value;
    if (!pattern || pattern === ONCE) {
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

    var endDateEl = document.getElementById('end_date');
    if (endDateEl && endDateEl.value) params.set('end_date', endDateEl.value);

    fetch(previewUrl + '?' + params.toString())
      .then(function(r) { return r.text(); })
      .then(function(html) { preview.innerHTML = html; })
      .catch(function() { preview.innerHTML = '<small class="text-muted">Could not load preview</small>'; });
  }

  // Initialize on page load.
  toggleFields(patternSelect.value);

  // Listen for changes.
  patternSelect.addEventListener('change', function() { toggleFields(this.value); });
  ['interval_n', 'day_of_month', 'month_of_year', 'end_date'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', fetchPreview);
  });
  var spEl = document.querySelector('[name="start_period_id"]');
  if (spEl) spEl.addEventListener('change', fetchPreview);
})();
