/**
 * Recurrence form -- link the three cadence controls, then show/hide the rest.
 * Used by both recurring-transaction and recurring-transfer forms.
 *
 * Plan step R7b-2.  The form authors two axes -- how often (interval + unit)
 * and which paycheck funds an occurrence (placement) -- instead of picking a
 * name from a closed pattern set.  Until plan step R7c the cadence is still
 * STORED as one of those patterns, so not every (interval, unit, placement) can
 * be written: any N paychecks, but only 1 / 3 / 6 months, and only a ONE-month
 * interval may be funded from the first paycheck.
 *
 * The offer set arrives whole in data-cadence-options, derived server-side from
 * the encoder's own table (app.services.recurrence.picker_model).  This file
 * FILTERS it; it never states which cadences exist, so it cannot drift from
 * what the write door accepts.  Filtering is what makes the refusal
 * unreachable: a combination the closed set cannot store is never selectable.
 *
 * Both interval controls post ``interval_n``, so exactly one is ever enabled --
 * a disabled control does not submit, which keeps two spellings of one field
 * from reaching the schema together.
 */
(function() {
  var unitSelect = document.getElementById('recurrence_unit');
  if (!unitSelect) return;

  var controls = document.getElementById('cadence-controls');
  var intervalWrap = document.getElementById('field-interval');
  var intervalFree = document.getElementById('interval_n_free');
  var intervalFixed = document.getElementById('interval_n_fixed');
  var placementWrap = document.getElementById('field-placement');
  var placementSelect = document.getElementById('recurrence_placement');

  var container = document.getElementById('recurrence-fields');
  var dom = document.getElementById('field-dom');
  var dueDom = document.getElementById('field-due-dom');
  var moy = document.getElementById('field-moy');
  var startPeriod = document.getElementById('field-start-period');
  var endDate = document.getElementById('field-end-date');
  var preview = document.getElementById('recurrence-preview');

  var startPeriodLabel = document.getElementById('start-period-label');
  var startPeriodHelp = document.getElementById('start-period-help');

  var options = [];
  try {
    options = JSON.parse(controls.getAttribute('data-cadence-options') || '[]');
  } catch (err) {
    options = [];
  }

  // In-flight preview fetch.  A rapid field change aborts the previous
  // request; otherwise the LAST response to ARRIVE wins and a slow earlier
  // fetch can clobber the preview with stale dates.
  var previewAbortController = null;

  function unitId() {
    return unitSelect.value;
  }

  // A unit takes ANY positive interval when some offer for it carries a null
  // interval -- the one pattern whose interval lives in a column rather than
  // in its name.  That is also why such a unit's fixed entries are ignored:
  // the free entry subsumes them.
  function unitIsFree(id) {
    return options.some(function(o) {
      return String(o.unit_id) === String(id) && o.interval_n === null;
    });
  }

  function currentInterval() {
    var raw = unitIsFree(unitId()) ? intervalFree.value : intervalFixed.value;
    var n = parseInt(raw, 10);
    return isNaN(n) ? 1 : n;
  }

  // The placements storable for this (unit, interval) pair -- NOT for the unit
  // alone.  The closed set stores "every 1 month funded from the first
  // paycheck" and has no quarterly or semi-annual twin, so keying on the unit
  // would offer what encode_cadence refuses.
  function placementsFor(id, interval) {
    return options.filter(function(o) {
      return String(o.unit_id) === String(id) &&
             (o.interval_n === null || o.interval_n === interval);
    }).map(function(o) { return String(o.placement_id); });
  }

  // Show only this unit's fixed intervals, and keep the selection on one of
  // them: a <select> whose selected <option> is hidden still SUBMITS that
  // value, so leaving a stale month interval selected under a different unit
  // would post a cadence the user cannot see.
  function syncFixedIntervals(id) {
    var first = null;
    var stillValid = false;
    Array.prototype.forEach.call(intervalFixed.options, function(opt) {
      var mine = opt.getAttribute('data-unit') === String(id);
      opt.hidden = !mine;
      opt.disabled = !mine;
      if (!mine) return;
      if (first === null) first = opt.value;
      if (opt.value === intervalFixed.value) stillValid = true;
    });
    if (!stillValid && first !== null) intervalFixed.value = first;
  }

  // Same hazard on the placement select, plus one more: when a unit offers a
  // single placement the row is hidden, and the hidden select must still post
  // that placement rather than whatever was chosen under the previous unit.
  function syncPlacements(id, interval) {
    var allowed = placementsFor(id, interval);
    var stillValid = false;
    Array.prototype.forEach.call(placementSelect.options, function(opt) {
      var mine = allowed.indexOf(opt.value) !== -1;
      opt.hidden = !mine;
      opt.disabled = !mine;
      if (mine && opt.value === placementSelect.value) stillValid = true;
    });
    if (!stillValid && allowed.length) placementSelect.value = allowed[0];
    placementWrap.classList.toggle('d-none', allowed.length < 2);
  }

  function toggleFields() {
    var id = unitId();

    // An EMPTY value is "Does not repeat", the only non-recurring option on
    // either form since plan step R2e-3 retired the ONCE pattern.
    if (!id) {
      intervalWrap.classList.add('d-none');
      placementWrap.classList.add('d-none');
      intervalFree.disabled = true;
      intervalFixed.disabled = true;
      container.classList.add('d-none');
      if (startPeriod) {
        startPeriod.classList.remove('d-none');
        if (startPeriodLabel) startPeriodLabel.textContent = 'Pay period';
        if (startPeriodHelp) startPeriodHelp.textContent = 'Which pay period should this transfer appear in?';
      }
      fetchPreview();
      return;
    }

    var free = unitIsFree(id);
    intervalWrap.classList.remove('d-none');
    intervalFree.disabled = !free;
    intervalFixed.disabled = free;
    intervalFree.classList.toggle('d-none', !free);
    intervalFixed.classList.toggle('d-none', free);
    if (!free) syncFixedIntervals(id);
    syncPlacements(id, currentInterval());

    container.classList.remove('d-none');

    // A day-of-month is meaningful only for a cadence measured in calendar
    // units; a paycheck-space one fires on the paycheck itself.  Asked of the
    // OFFER SET rather than of a pattern id, so a unit added at plan step R8
    // needs no branch here.
    var showsDay = !free;
    dom.classList.toggle('d-none', !showsDay);
    if (dueDom) dueDom.classList.toggle('d-none', !showsDay);
    // The month-of-year cell narrows a cycle that skips months, which is a
    // cadence firing less often than every month.
    moy.classList.toggle('d-none', !(showsDay && currentInterval() > 1));

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

    var id = unitId();
    if (!id) {
      preview.innerHTML = '<small class="text-muted">Choose how often this repeats to see upcoming dates</small>';
      return;
    }

    var previewUrl = preview.getAttribute('data-preview-url');
    if (!previewUrl) return;

    var params = new URLSearchParams();
    params.set('recurrence_unit', id);
    params.set('interval_n', String(currentInterval()));
    if (placementSelect.value) params.set('recurrence_placement', placementSelect.value);

    var domEl = document.getElementById('day_of_month');
    if (domEl && domEl.value) params.set('day_of_month', domEl.value);

    var moyEl = document.getElementById('month_of_year');
    if (moyEl && moyEl.value) params.set('month_of_year', moyEl.value);

    var spEl = document.querySelector('[name="start_period_id"]');
    if (spEl && spEl.value) params.set('start_period_id', spEl.value);

    var endDateEl = document.getElementById('end_date');
    if (endDateEl && endDateEl.value) params.set('end_date', endDateEl.value);

    // Abort any preview still in flight, then reject non-2xx so a 4xx/5xx or
    // session-expiry login page is never injected as if it were the dates.
    if (previewAbortController) previewAbortController.abort();
    previewAbortController = new AbortController();
    fetch(previewUrl + '?' + params.toString(), { signal: previewAbortController.signal })
      .then(function(r) {
        if (!r.ok) throw new Error('recurrence preview fetch failed: ' + r.status);
        return r.text();
      })
      .then(function(html) { preview.innerHTML = html; })
      .catch(function(err) {
        // A newer field change aborted this preview -- intentional, not an error.
        if (err.name === 'AbortError') return;
        preview.innerHTML = '<small class="text-muted">Could not load preview</small>';
      });
  }

  // Initialize on page load.
  toggleFields();

  // Listen for changes.  The unit re-links everything below it; the interval
  // re-links the placements, because which are storable depends on the pair.
  unitSelect.addEventListener('change', toggleFields);
  intervalFree.addEventListener('change', toggleFields);
  intervalFixed.addEventListener('change', toggleFields);
  placementSelect.addEventListener('change', fetchPreview);
  ['day_of_month', 'due_day_of_month', 'month_of_year', 'end_date'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', fetchPreview);
  });
  var spEl = document.querySelector('[name="start_period_id"]');
  if (spEl) spEl.addEventListener('change', fetchPreview);
})();
