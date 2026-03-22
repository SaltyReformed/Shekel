'use strict';

/**
 * Shekel Budget App -- Reusable Chart Slider Module
 *
 * Syncs a range input with a text/number input bidirectionally and
 * triggers debounced HTMX requests when the value changes.
 *
 * Usage: Add matching data-slider-group attributes to a range input
 * and a text/number input. The module will automatically bind them.
 *
 * Attributes:
 *   data-slider-group="groupName" -- links the range and text inputs.
 *   data-slider-target="elementId" -- the HTMX target element to trigger.
 *   data-slider-debounce="250"    -- debounce delay in ms (default 250).
 */
var ChartSlider = (function () {

  var timers = {};

  /**
   * Initialize all slider groups on the page.
   * Call after DOM load or HTMX swap.
   */
  function init() {
    var ranges = document.querySelectorAll('input[type="range"][data-slider-group]');

    ranges.forEach(function (rangeInput) {
      var group = rangeInput.getAttribute('data-slider-group');
      if (!group) return;

      // Find the paired text/number input.
      var textInput = document.querySelector(
        'input:not([type="range"])[data-slider-group="' + group + '"]'
      );
      if (!textInput) return;

      // Skip if already bound (prevent duplicate listeners).
      if (rangeInput.hasAttribute('data-slider-bound')) return;
      rangeInput.setAttribute('data-slider-bound', 'true');

      var debounceMs = parseInt(
        rangeInput.getAttribute('data-slider-debounce') || '250', 10
      );
      var targetId = rangeInput.getAttribute('data-slider-target');

      // Sync range → text.
      rangeInput.addEventListener('input', function () {
        textInput.value = rangeInput.value;
        debounceTrigger(group, targetId, debounceMs);
      });

      // Sync text → range.
      textInput.addEventListener('input', function () {
        var val = parseFloat(textInput.value);
        if (!isNaN(val)) {
          var min = parseFloat(rangeInput.min);
          var max = parseFloat(rangeInput.max);
          rangeInput.value = Math.max(min, Math.min(max, val));
        }
        debounceTrigger(group, targetId, debounceMs);
      });
    });
  }

  /**
   * Debounce and trigger an HTMX event on the target element.
   * @param {string} group - The slider group name (used as timer key).
   * @param {string} targetId - The ID of the HTMX target element.
   * @param {number} delay - Debounce delay in milliseconds.
   */
  function debounceTrigger(group, targetId, delay) {
    if (timers[group]) {
      clearTimeout(timers[group]);
    }
    timers[group] = setTimeout(function () {
      if (!targetId) return;
      var target = document.getElementById(targetId);
      if (target && typeof htmx !== 'undefined') {
        htmx.trigger(target, 'slider-changed');
      }
    }, delay);
  }

  // Auto-initialize on page load.
  document.addEventListener('DOMContentLoaded', init);

  // Re-initialize after HTMX swaps.
  document.addEventListener('htmx:afterSwap', function () {
    // Small delay to let DOM settle.
    setTimeout(init, 50);
  });

  return { init: init };
})();
