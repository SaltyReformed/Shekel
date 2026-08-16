/**
 * Recurrence form -- link the three cadence controls, then show/hide the rest.
 * Used by both recurring-transaction and recurring-transfer forms.
 *
 * Plan step R7b-2.  The form authors two axes -- how often (interval + unit)
 * and which paycheck funds an occurrence (placement) -- instead of picking a
 * name from a closed pattern set.
 *
 * The offer set arrives whole in data-cadence-options, derived server-side from
 * the producer that would otherwise refuse the cadence
 * (app.services.recurrence.picker_model).  This file FILTERS it; it never
 * states which cadences exist, so it cannot drift from what the write door
 * accepts.  Filtering is what makes the refusal unreachable: a combination the
 * application cannot resolve is never selectable.
 *
 * Each offer also CARRIES whether the cadence anchors on a DAY of the month,
 * because that is a property of the resolver's own routing rather than of
 * anything visible here.  This file reads it; it does not restate it.
 *
 * Plan step R7c-b deleted the Day of Month and Month controls: a rule's first
 * occurrence carries its cycle's day AND its cycle's month, so ``starts_on``
 * says both (ruling R-R16).  What is left beside that date is the one question
 * the date cannot answer -- see ``syncNominalDay``.
 *
 * **Plan step R7c-c freed the INTERVAL and kept the FUNDING row on screen.**
 * The cadence was stored as one of eight pattern names until then, so not every
 * (interval, unit, placement) could be written -- any N paychecks, but only
 * 1 / 3 / 6 months, and only a ONE-month interval funded from the first
 * paycheck -- which is why there were two controls posting interval_n with
 * exactly one enabled, and why the funding row hid itself whenever the chosen
 * pair admitted a single placement.  Every positive interval is authorable on
 * every offered (unit, placement) pair now: one number box, and a funding row
 * that is always rendered and explains itself when there is nothing to choose
 * (plan ledger row D32).
 */
(function() {
  var unitSelect = document.getElementById('recurrence_unit');
  if (!unitSelect) return;

  var controls = document.getElementById('cadence-controls');
  var intervalWrap = document.getElementById('field-interval');
  var interval = document.getElementById('interval_n');
  var placementWrap = document.getElementById('field-placement');
  var placementSelect = document.getElementById('recurrence_placement');
  var placementHelp = document.getElementById('placement-help');

  var container = document.getElementById('recurrence-fields');
  var dueDom = document.getElementById('field-due-dom');
  var startPeriod = document.getElementById('field-start-period');
  var startPeriodSelect = document.getElementById('start_period_id');
  var preview = document.getElementById('recurrence-preview');

  // The rule's FIRST OCCURRENCE (plan step R7c-b).  Its ROW shows and hides
  // with the rest of #recurrence-fields -- every cadence has one -- but the
  // input itself is enabled and disabled here, because a hidden input still
  // submits.
  var startsOn = document.getElementById('starts_on');

  // Whether the SERVER rendered it locked -- a loan payment, whose first
  // occurrence the app derives from the loan's first contractual installment.
  // Read ONCE at load, for the reason endBoundLocked is: the toggling below
  // turns the same control off and on and must never hand back one the server
  // locked.
  //
  // DISABLED, and reading the wrong property here DEFEATED the lock.  This
  // asked ``readOnly`` while the template emitted ``disabled``: the two never
  // agreed, so startsOnLocked was false on every locked form, syncStartsOn
  // immediately set ``disabled = false``, and a loan payment's derived date
  // became editable and submittable the instant the page settled.  The stale
  // reading was left behind by the ruling of 2026-08-15 that moved the control
  // from readonly to disabled -- readonly SUBMITS, and the update path reads
  // an absent start as "leave the stored one alone", which is the whole
  // meaning a locked control needs to convey.  Found by reading, not by the
  // suite: rendered HTML cannot tell a control a script re-enabled from one
  // that was never locked, which is what tests/manual/verify_recurrence_form.py
  // exists for.
  var startsOnLocked = startsOn !== null && startsOn.disabled;

  // The destinations whose first occurrence the app DERIVES, for the CREATE
  // form (plan step R7c-b).  An edit form emits no such list -- it already
  // knows whether THIS template is a loan payment and locks server-side --
  // so an absent attribute leaves every behaviour below inert.
  //
  // Ids as strings, because that is what a <select>'s value is; comparing a
  // parsed number against an option value is how an off-by-type bug hides.
  var loanDestinations = (function() {
    if (!container) return [];
    var raw = container.getAttribute('data-loan-account-ids');
    if (!raw) return [];
    return raw.split(',').filter(function(id) { return id !== ''; });
  })();
  var destinationSelect = document.getElementById('to_account_id');

  // The one question ``starts_on`` cannot answer: a date that is its own
  // month's LAST day in a month shorter than 31 days could mean that day or
  // any larger one the month could not hold.  2026-04-30 is "the 30th" or
  // "the last day of the month", and those are different cadences from May on.
  var nominalDayWrap = document.getElementById('field-nominal-day');
  var nominalDay = document.getElementById('nominal_day');

  // The "Ends" control (plan step R7b-3): a mode select and the value inputs
  // its shapes need.  This file never states which shapes exist -- each
  // <option> names the control ITS shape needs in data-needs, so a shape plan
  // step R8 adds needs no edit here.
  var endBoundWrap = document.getElementById('field-end-bound');
  var endMode = document.getElementById('recurrence_end_mode');

  // Every control any shape can ask for, read off the offer set rather than
  // listed: an id here that the options never name would be a control nothing
  // can enable, and an option naming one that is missing would silently
  // enable nothing.
  var endValueWraps = endMode === null ? [] :
    Array.prototype.map.call(endMode.options, function(opt) {
      return document.getElementById(opt.getAttribute('data-needs') || '');
    }).filter(function(el) { return el !== null; });

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

  // What the interval box says, or null when it says nothing.
  //
  // **It defaulted an empty box to 1 until plan step R7c-c, and that default
  // is now a claim the save contradicts.**  Freeing the interval made the box
  // clearable for every unit, and clearing it beside a chosen cadence is
  // REFUSED at the submission (validate_authorable_cadence) rather than
  // silently stored as 1 -- so previewing twelve monthly dates for a box the
  // user has just emptied would show a schedule no save can produce, on the
  // one surface whose whole contract is "what saving would produce".
  //
  // Null rather than a guess, and fetchPreview says so with the SAME sentence
  // it uses for no cadence at all: "choose how often this repeats" is exactly
  // what is missing either way.  The server still bounds the value it is sent
  // (require_positive_interval); this is the preview's own read, not a
  // validation.
  function currentInterval() {
    var n = parseInt(interval.value, 10);
    return isNaN(n) ? null : n;
  }

  // The offer chosen right now: the (unit, placement) pair whose server-stated
  // facts the calendar detail rows are shown from.  Read from the offer set
  // rather than inferred, because both facts belong to the WHOLE pair: "months
  // funded from the first paycheck" anchors on a paycheck and reads no day of
  // the month, while "months funded from the covering paycheck" does.
  //
  // The INTERVAL left this lookup at plan step R7c-c, with the closed pattern
  // set: every positive interval is authorable on every offered pair, so it
  // selects no offer and changing it changes no fact here.
  function currentOption(id, placementId) {
    var matches = options.filter(function(o) {
      return String(o.unit_id) === String(id) &&
             String(o.placement_id) === String(placementId);
    });
    return matches.length ? matches[0] : null;
  }

  // The placements authorable for this UNIT.
  //
  // **It was keyed on the (unit, interval) PAIR until plan step R7c-c**, and
  // the pair was the closed pattern set's doing: it stored "every 1 month
  // funded from the first paycheck" and had no quarterly twin, so keying on
  // the unit alone would have offered what the write door refused.  With the
  // interval free the MONTH unit admits both placements at every interval, and
  // the dependency is gone with the fusion that created it -- which is plan
  // ledger row D32's defect ceasing to exist rather than being warned about.
  //
  // DISTINCT placement ids, and the de-duplication is still load-bearing: the
  // help text below swaps on whether a unit admits fewer than two, so a
  // duplicate would read as a choice the user does not have.
  function placementsFor(id) {
    var found = [];
    options.forEach(function(o) {
      if (String(o.unit_id) !== String(id)) return;
      var value = String(o.placement_id);
      if (found.indexOf(value) === -1) found.push(value);
    });
    return found;
  }

  // Enable the placements this unit admits, keep the selection on one of them,
  // and say which of the two states the user is in.
  //
  // **The row is no longer HIDDEN when a unit admits one placement** (plan step
  // R7c-c, developer ruling 2026-08-16 on plan ledger row D32).  Hiding it is
  // how a bill's funding rule came to change with nothing on screen saying so:
  // the select is reassigned below whenever the previous choice is not
  // admissible, which is correct and was invisible.  Two cadences still admit
  // one -- paychecks, where the placement is inert because a payday resolves to
  // its own paycheck either way, and years, whose first-paycheck anchor is plan
  // step R8's -- and for those the row renders with that one option and the
  // help text explains why there is nothing to choose.
  //
  // Both sentences come from data-* attributes the SERVER rendered; this file
  // states no copy, the same rule syncStartsOnHelp follows.
  function syncPlacements(id) {
    var allowed = placementsFor(id);
    var stillValid = false;
    Array.prototype.forEach.call(placementSelect.options, function(opt) {
      var mine = allowed.indexOf(opt.value) !== -1;
      opt.hidden = !mine;
      opt.disabled = !mine;
      if (mine && opt.value === placementSelect.value) stillValid = true;
    });
    if (!stillValid && allowed.length) placementSelect.value = allowed[0];
    if (!placementHelp) return;
    var text = placementHelp.getAttribute(
      allowed.length < 2 ? 'data-fixed-text' : 'data-choice-text'
    );
    if (text) placementHelp.textContent = text;
  }

  // Whether the server rendered the "Ends" control locked -- a loan payment,
  // whose closing bound the app DERIVES from the loan's projected payoff.
  // Read ONCE, at load, because the toggling below turns the same controls off
  // and on and must never hand back one the server disabled: a locked form
  // that posted a bound would state a stop the next loan edit silently
  // overwrites.
  var endBoundLocked = endMode !== null && endMode.disabled;

  // Show and enable only the input the chosen "Ends" shape needs, and disable
  // the whole control when the definition does not repeat.
  //
  // Disabling rather than only hiding, because a hidden control still SUBMITS
  // -- the defect class plan step R7b-2 shipped twice and the browser pass
  // caught (tests/manual/verify_recurrence_form.py).  A stale date left in a
  // box the user has moved off would otherwise reach the door beside a mode
  // that does not name it.
  function syncEndBound(repeating) {
    if (!endMode || endBoundLocked) return;
    endMode.disabled = !repeating;
    var chosen = endMode.options[endMode.selectedIndex];
    var needs = repeating && chosen
      ? chosen.getAttribute('data-needs')
      : '';
    endValueWraps.forEach(function(wrap) {
      var mine = wrap.id === needs;
      wrap.classList.toggle('d-none', !mine);
      Array.prototype.forEach.call(
        wrap.querySelectorAll('input'),
        function(input) { input.disabled = !mine; }
      );
    });
  }

  // The pay-period <select> belongs to the NON-REPEATING case alone since
  // plan step R7b-4: it says which period the single Transfer a one-time
  // transfer stands for lands in.  It used to be shown for BOTH cases with
  // its label swapped -- "First paycheck" when repeating, "Pay period"
  // otherwise -- because one control meant two things; the repeating meaning
  // is the "Starts on" date now, so the control has one label (server-rendered)
  // and one job.
  //
  // DISABLED as well as hidden, and that is the load-bearing half: a hidden
  // control still SUBMITS, so a user who picks a period and then chooses a
  // cadence would post a period the recurrence has no use for -- straight into
  // the route's ownership check and, on a crafted payload, into a column this
  // step is retiring.  It is the defect class plan step R7b-2 shipped twice
  // and only the browser pass caught (tests/manual/verify_recurrence_form.py).
  function syncStartPeriod(repeating) {
    if (!startPeriod) return;
    startPeriod.classList.toggle('d-none', repeating);
    if (startPeriodSelect) startPeriodSelect.disabled = repeating;
  }

  // The "Starts on" box hides with #recurrence-fields rather than on its own
  // -- every cadence has a first occurrence -- but hiding is not enough:
  // a hidden input SUBMITS, and the same browser pass that caught the
  // pay-period select caught this one posting an empty date on a
  // "Does not repeat" save.  The server drops the key either way now, and
  // this is the affordance half: a control the user cannot see does not
  // speak.  Never touched when the server rendered it READONLY (a loan
  // payment, whose date the app derives) -- re-enabling it here would hand
  // back a control the server locked.
  function syncStartsOn(repeating) {
    if (!startsOn || startsOnLocked) return;
    startsOn.disabled = !repeating || destinationDerivesTheStart();
  }

  // Whether the destination the user has CHOSEN is one whose first occurrence
  // the app derives (plan step R7c-b).  The create form's half of the rule an
  // edit form gets from the server: this form offers every active account, so
  // a recurring loan payment can be created here -- and asking the user for a
  // date the route is going to replace is the defect
  // LOAN_PAYMENT_BOUND_IS_DERIVED closes one path over.
  //
  // Always false when the list is absent, which is every EDIT form and every
  // form that has no destination control at all (the transaction template's).
  function destinationDerivesTheStart() {
    if (!destinationSelect || loanDestinations.length === 0) return false;
    return loanDestinations.indexOf(destinationSelect.value) !== -1;
  }

  // Word the "Starts on" help for whichever of the two the row is saying.
  //
  // The COPY is the template's, carried on the row as data attributes, for the
  // reason every other string here is: a second wording in this file is one
  // that can disagree with the server's, and the server renders the same two
  // sentences for a locked and an unlocked edit form.
  function syncStartsOnHelp() {
    var help = document.getElementById('starts-on-help');
    if (!help || startsOnLocked) return;
    var derived = destinationDerivesTheStart();
    var text = help.getAttribute(
      derived ? 'data-locked-text' : 'data-open-text'
    );
    if (text) help.textContent = text;
  }

  // The last day of the month ``value`` (an ISO date string) falls in, or null
  // when it is not a date at all.  Day 0 of the NEXT month is that day, which
  // is the only calendar fact this file computes -- the DAY a rule fires on is
  // never computed here, and neither is any amount.
  function lastDayOfMonth(value) {
    var parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    if (!parts) return null;
    return new Date(
      parseInt(parts[1], 10), parseInt(parts[2], 10), 0
    ).getDate();
  }

  // Show the "repeating on" control exactly where the chosen date leaves the
  // question open, and enable exactly the days it leaves open.
  //
  // The SAME rule the server applies (offerable_nominal_days): the cadence
  // must fire on a day of the month, the date must BE its month's last day,
  // and a day qualifies when it is larger than that.  The copy is the
  // template's and the option set is fixed, so this file states no wording and
  // no domain -- and the write door refuses any pair it gets wrong, which is
  // what keeps this an affordance rather than a rule.
  //
  // ``hasDayCoordinate`` is the offer's has_day_of_month_coordinate, which is
  // keyed on the UNIT -- never anchors_day_of_month, which is keyed on the
  // (unit, placement) pair and answers a different question.  Passing the
  // wrong one MOVED MONEY: they disagree for Monthly First, whose occurrences
  // are days of the month even though its anchor is a paycheck, so this
  // cleared and disabled a control the server had rendered enabled.  The
  // update door reads nominal_day off the same presence key as starts_on, so
  // changing only "Funded from" on a "last day of every month" rent posted the
  // date without the day, wrote nominal_day = NULL, and moved every later
  // occurrence to the 30th for good.
  //
  // DISABLED as well as hidden, for the reason every other control here is: a
  // hidden select still SUBMITS, and a nominal day left behind beside a date
  // that never clamped is exactly what ck_recurrence_rules_nominal_day
  // refuses.  The selection is cleared with it, because a day the new date
  // does not leave open is not a day the user can still mean.
  function syncNominalDay(hasDayCoordinate) {
    if (!nominalDay || !nominalDayWrap) return;
    var lastDay = lastDayOfMonth(startsOn && startsOn.value);
    var chosenDay = lastDay === null ? null : parseInt(
      startsOn.value.slice(8, 10), 10
    );
    var open = hasDayCoordinate && lastDay !== null && chosenDay === lastDay;
    var any = false;
    Array.prototype.forEach.call(nominalDay.options, function(opt) {
      var day = parseInt(opt.getAttribute('data-day') || '', 10);
      if (isNaN(day)) return;
      var mine = open && day > chosenDay;
      opt.hidden = !mine;
      opt.disabled = !mine;
      if (mine) any = true;
      if (!mine && opt.selected) nominalDay.selectedIndex = 0;
    });
    nominalDayWrap.classList.toggle('d-none', !any);
    if (!startsOnLocked) nominalDay.disabled = !any;
  }

  // The bill's separate real DUE day, shown only for a cadence that anchors on
  // a day of the month.
  //
  // DISABLED as well as hidden, and that half was missing: this row is the one
  // control in this file that was toggled by class alone, so a due day typed
  // under "every 1 month" still POSTED after switching to "funded from the
  // first paycheck" and landed in the column through _author.  It is the same
  // defect class the pay-period select, the "Ends" inputs and the nominal day
  // each carry a comment about; the browser pass could not see it, because
  // _drive_visibility was still driving the two controls this step deleted.
  //
  // The value is NOT cleared with it, unlike the nominal day's: a due day is
  // the servicer's date rather than a coordinate of the cadence, so switching
  // cadence does not make it wrong -- and the update door reads an ABSENT key
  // as "leave the stored one alone" (RecurrenceFormContext), so a hidden row
  // states nothing rather than erasing what it cannot show.
  function syncDueDom(anchorsDay) {
    if (!dueDom) return;
    dueDom.classList.toggle('d-none', !anchorsDay);
    Array.prototype.forEach.call(
      dueDom.querySelectorAll('input'),
      function(input) { input.disabled = !anchorsDay; }
    );
  }

  function toggleFields() {
    var id = unitId();

    // An EMPTY value is "Does not repeat", the only non-recurring option on
    // either form since plan step R2e-3 retired the ONCE pattern.
    if (!id) {
      intervalWrap.classList.add('d-none');
      placementWrap.classList.add('d-none');
      interval.disabled = true;
      // DISABLED with the interval, not merely hidden.  This was the one
      // control in the file that broke the file's own rule -- a hidden control
      // still SUBMITS, which is the defect class plan step R7b-2 shipped twice
      // -- and it went unnoticed because the write door pops the placement on
      // the no-cadence branch, so the stray key was absorbed one layer down by
      // a guard written for something else.  Closed at plan step R7c-c.
      placementSelect.disabled = true;
      container.classList.add('d-none');
      syncEndBound(false);
      syncStartPeriod(false);
      syncStartsOn(false);
      syncNominalDay(false);
      syncDueDom(false);
      fetchPreview();
      return;
    }

    intervalWrap.classList.remove('d-none');
    interval.disabled = false;
    placementWrap.classList.remove('d-none');
    // Re-enabled BEFORE syncPlacements, which turns individual OPTIONS off and
    // on: a disabled <select> posts nothing whatever its options say, so the
    // order here is what makes the chosen placement reach the door.
    placementSelect.disabled = false;
    syncPlacements(id);

    container.classList.remove('d-none');

    // Whether this cadence lands on a DAY of the month is a fact the SERVER
    // stated about the chosen pair, never inferred here.  Inferring it is what
    // an earlier draft of this file did -- it read "the interval control is a
    // free number box" as "this cadence has no day of the month", which was
    // true of every cadence offered then, is true of NONE now that every unit
    // uses the free box, and was never the same fact either way.
    var chosen = currentOption(id, placementSelect.value);
    // Two facts, two questions, and they differ for Monthly First -- see
    // syncNominalDay.  The DUE DAY row asks about the anchor family, which is
    // the question it has always asked; the "repeating on" control asks
    // whether occurrences land on a day of the month at all.
    var anchorsDay = chosen !== null && chosen.anchors_day_of_month;
    var hasDayCoordinate =
      chosen !== null && chosen.has_day_of_month_coordinate;
    syncDueDom(anchorsDay);

    syncStartPeriod(true);
    syncStartsOn(true);
    // The "repeating on" control rides with the date: a derived first
    // occurrence brings its own nominal day, so a control the user could still
    // touch would state a day the route is about to replace.
    syncNominalDay(hasDayCoordinate && !destinationDerivesTheStart());
    syncStartsOnHelp();

    if (endBoundWrap) {
      endBoundWrap.classList.remove('d-none');
    }
    syncEndBound(true);

    fetchPreview();
  }

  // Whether a control in the "Starts on" row states a value the preview must
  // carry.  A control the FORM turned off states nothing; one the SERVER
  // locked states the value the save will use.  See fetchPreview.
  function statesAStartValue(el) {
    if (!el) return false;
    return (startsOnLocked || !el.disabled) && el.value !== '';
  }

  function fetchPreview() {
    if (!preview) return;

    // A cadence is a unit AND a count, so a missing either is the same state
    // and gets the same sentence.  The copy matches the pre-script render in
    // _recurrence_fields.html verbatim, which is why it is written once here.
    var id = unitId();
    var everyN = currentInterval();
    if (!id || everyN === null) {
      preview.innerHTML = '<small class="text-muted">Choose how often this repeats to see upcoming dates</small>';
      return;
    }

    var previewUrl = preview.getAttribute('data-preview-url');
    if (!previewUrl) return;

    var params = new URLSearchParams();
    params.set('recurrence_unit', id);
    params.set('interval_n', String(everyN));
    if (placementSelect.value) params.set('recurrence_placement', placementSelect.value);

    // The FIRST OCCURRENCE and the day a clamped one MEANT, sent whenever the
    // "Starts on" row states them.
    //
    // ``disabled`` alone is NOT that question, and reading it as if it were
    // killed the preview outright on the one definition whose schedule matters
    // most.  A control the FORM turned off states nothing -- previewing a
    // value the save would not carry is a preview of something else -- but a
    // control the SERVER locked states the DERIVED value, which is exactly
    // what its preview should walk from.  A loan payment's row renders
    // ``disabled`` since the 2026-08-15 readonly->disabled ruling, so skipping
    // every disabled control left its preview reading "No preview for this
    // cadence" forever.  ``startsOnLocked`` is read once at load and tells the
    // two apart.
    //
    // The nominal day rides on the same test rather than its own: it is the
    // second half of one row, the server disables it with the date, and the
    // preview must show "Apr 30, May 31, Jun 30" rather than "Apr 30, May 30"
    // for a month-end loan payment as much as for a typed one.
    if (statesAStartValue(startsOn)) {
      params.set('starts_on', startsOn.value);
    }
    if (statesAStartValue(nominalDay)) {
      params.set('nominal_day', nominalDay.value);
    }

    // The closing bound, as the SAME three controls the save posts: the mode
    // that names the shape, and the one value input that shape enables.
    //
    // The MODE is what makes this a bound at all.  Sending only the values
    // leaves the endpoint composing "never" -- it dispatches on the mode, and
    // ``NeverEnds`` reads neither input -- so a rule the user has just bounded
    // previews as unbounded, on the one surface whose contract is "what
    // saving would produce".  Two adversarial reviews of plan step R7b-3
    // measured exactly that, on a form whose own comment claimed the
    // opposite.
    if (endMode && !endMode.disabled) {
      params.set('recurrence_end_mode', endMode.value);
    }

    var endDateEl = document.getElementById('end_date');
    if (endDateEl && !endDateEl.disabled && endDateEl.value) {
      params.set('end_date', endDateEl.value);
    }

    var maxOccEl = document.getElementById('max_occurrences');
    if (maxOccEl && !maxOccEl.disabled && maxOccEl.value) {
      params.set('max_occurrences', maxOccEl.value);
    }

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
  // re-links the placements, because which are authorable depends on the unit.
  // The PLACEMENT re-links too, and it is not decoration: a monthly cadence
  // funded from the month's first paycheck anchors on that paycheck and reads
  // no day of the month, so switching the funding choice adds or removes the
  // Day of Month row.  Setting a <select>'s value from script fires no change
  // event, so syncPlacements' own corrections cannot re-enter this.
  unitSelect.addEventListener('change', toggleFields);
  interval.addEventListener('change', toggleFields);
  placementSelect.addEventListener('change', toggleFields);
  // The DESTINATION re-links the "Starts on" row, and only on a create form:
  // choosing a loan hands its first occurrence to the route, so the control
  // stops being the user's to state.  ``toggleFields`` is what applies it, so
  // the enable/disable rule stays in one function rather than two that agree.
  if (destinationSelect && loanDestinations.length > 0) {
    destinationSelect.addEventListener('change', toggleFields);
  }
  ['due_day_of_month', 'nominal_day', 'end_date',
   'max_occurrences'].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.addEventListener('change', fetchPreview);
  });
  // The first occurrence re-links the "repeating on" control before it
  // previews: which days that date leaves open is a property OF the date, so
  // moving it can retire the choice the user made under the old one.
  if (startsOn) {
    startsOn.addEventListener('change', function() {
      var chosen = currentOption(unitId(), placementSelect.value);
      syncNominalDay(chosen !== null && chosen.has_day_of_month_coordinate);
      fetchPreview();
    });
  }
  // The "Ends" mode re-links which value input is enabled, then previews:
  // switching from a date to a count changes the dates the preview lists.
  if (endMode) {
    endMode.addEventListener('change', function() {
      syncEndBound(true);
      fetchPreview();
    });
  }
  // The pay-period <select> no longer drives the preview: it is shown only
  // when the definition does NOT repeat, and a non-repeating definition has
  // no occurrences to list.
})();
