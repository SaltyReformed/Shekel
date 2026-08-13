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
 * Each offer also CARRIES what the calendar detail rows are shown from --
 * whether the cadence anchors on a day of the month, and how many months one
 * of its units spans -- because both are properties of the resolver's anchor
 * derivation rather than of anything visible here.  This file reads them; it
 * does not restate them.
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

  // The offer chosen right now: the (unit, interval, placement) triple whose
  // server-stated facts the calendar detail rows are shown from.  Read from
  // the offer set rather than inferred, because both facts belong to the
  // WHOLE triple: "every 1 month funded from the first paycheck" anchors on a
  // paycheck and reads no day of the month, while "every 1 month" on the same
  // unit does.
  function currentOption(id, interval, placementId) {
    var matches = options.filter(function(o) {
      return String(o.unit_id) === String(id) &&
             (o.interval_n === null || o.interval_n === interval) &&
             String(o.placement_id) === String(placementId);
    });
    return matches.length ? matches[0] : null;
  }

  // The placements storable for this (unit, interval) pair -- NOT for the unit
  // alone.  The closed set stores "every 1 month funded from the first
  // paycheck" and has no quarterly or semi-annual twin, so keying on the unit
  // would offer what encode_cadence refuses.
  // DISTINCT placement ids, and the de-duplication is load-bearing rather
  // than tidy: the row hides itself when a pair offers fewer than two, and the
  // paycheck unit has TWO offers at interval 1 (every paycheck, and every N
  // paychecks with N = 1) that carry the SAME placement.  Without this the
  // most common cadence on the form rendered a "Funded from" select with one
  // usable choice beside a hidden one.
  function placementsFor(id, interval) {
    var found = [];
    options.forEach(function(o) {
      if (String(o.unit_id) !== String(id)) return;
      if (o.interval_n !== null && o.interval_n !== interval) return;
      var value = String(o.placement_id);
      if (found.indexOf(value) === -1) found.push(value);
    });
    return found;
  }

  // Show only this unit's fixed intervals, and keep the selection on one of
  // THEM -- identified by the option itself, never by its value.
  //
  // An interval value is not unique across units: "1" is offered by paychecks,
  // months AND years, so `select.value = "1"` selects whichever carries it
  // FIRST in document order, which is another unit's. Driving the real form in
  // a browser is what caught it, and the damage was not cosmetic: choosing
  // "months" left the selection on the hidden, disabled "1 paycheck" option,
  // so the control rendered BLANK and -- because a disabled option submits
  // nothing -- the form posted no interval_n at all. A validity test that
  // compared values agreed the selection was fine ("1" === "1") while
  // selectedIndex pointed at the wrong unit's entry.
  //
  // The key is the (unit, interval) PAIR, which is what the offer set says, so
  // ownership is read off data-unit and the selection is moved by INDEX.
  function syncFixedIntervals(id) {
    var firstIndex = null;
    var current = intervalFixed.options[intervalFixed.selectedIndex] || null;
    var stillValid = current !== null &&
                     current.getAttribute('data-unit') === String(id);
    Array.prototype.forEach.call(intervalFixed.options, function(opt, index) {
      var mine = opt.getAttribute('data-unit') === String(id);
      opt.hidden = !mine;
      opt.disabled = !mine;
      if (mine && firstIndex === null) firstIndex = index;
    });
    if (!stillValid && firstIndex !== null) {
      intervalFixed.selectedIndex = firstIndex;
    }
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
    var interval = currentInterval();
    syncPlacements(id, interval);

    container.classList.remove('d-none');

    // Both calendar detail rows are shown from facts the SERVER stated about
    // the chosen triple, never inferred here.  Inferring them is what an
    // earlier draft of this file did -- it read "the interval control is a
    // free number box" as "this cadence has no day of the month", which is
    // true of every cadence offered today and is not the same fact.  It also
    // read "the interval exceeds 1" as "the cycle skips months", which is
    // wrong for an ANNUAL rule: its interval is 1 and its cycle is 12 months,
    // so the Month control it needs was hidden.
    var chosen = currentOption(id, interval, placementSelect.value);
    var showsDay = chosen !== null && chosen.anchors_day_of_month;
    dom.classList.toggle('d-none', !showsDay);
    if (dueDom) dueDom.classList.toggle('d-none', !showsDay);
    // The month-of-year cell narrows a cycle that SKIPS months, so it is the
    // cycle's own span in months that decides -- the unit's span times the
    // chosen interval, which is quarterly (3), semi-annual (6) and annual (12)
    // but not monthly (1).
    var spansMonths = showsDay && chosen.months_per_unit * interval > 1;
    moy.classList.toggle('d-none', !spansMonths);

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
  // The PLACEMENT re-links too, and it is not decoration: a monthly cadence
  // funded from the month's first paycheck anchors on that paycheck and reads
  // no day of the month, so switching the funding choice adds or removes the
  // Day of Month row.  Setting a <select>'s value from script fires no change
  // event, so syncPlacements' own corrections cannot re-enter this.
  unitSelect.addEventListener('change', toggleFields);
  intervalFree.addEventListener('change', toggleFields);
  intervalFixed.addEventListener('change', toggleFields);
  placementSelect.addEventListener('change', toggleFields);
  ['day_of_month', 'due_day_of_month', 'month_of_year', 'end_date'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', fetchPreview);
  });
  var spEl = document.querySelector('[name="start_period_id"]');
  if (spEl) spEl.addEventListener('change', fetchPreview);
})();
