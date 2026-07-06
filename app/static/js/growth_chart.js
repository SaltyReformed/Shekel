'use strict';

/**
 * Shekel Budget App -- Investment Growth Chart (Fable 5 band rebuild)
 *
 * Renders the investment / retirement detail page's growth chart via the
 * ShekelChart factory (chart_theme.js), so a theme toggle re-resolves
 * colors.  The series arrives as data-* attributes on the canvas (CSP:
 * element.dataset only), split into two segments the script merges onto
 * one axis -- display assembly, never money math:
 *
 *   data-history-labels / data-history-balances -- the solid modeled
 *     past, ending at the current period (balance_at.balance_map).
 *   data-labels / data-balances / data-contributions -- the dashed
 *     forward projection plus its contributions-only baseline, on the
 *     fragment's synthetic-period basis.
 *   data-boundary -- the Today split index (== history length).
 *   data-retirement-index / data-retirement-year -- the optional
 *     retirement-year marker slot on the combined axis.
 *   data-whatif-balances / data-whatif-label -- the optional what-if
 *     overlay (green dashed, the loan extra-payment preview grammar).
 *
 * Solid-history vs dashed-projection styling and the Today marker come
 * from the shared ShekelChart.splitSegment / todayMarkerPlugin helpers,
 * so this chart cannot drift from the cash-detail and cockpit trends.
 * Also binds the what-if input to the debounced slider-changed HTMX
 * trigger and keeps the Horizon lever's "N yr -> year" caption in sync
 * (calendar arithmetic only -- no monetary values).
 *
 * Re-initialized after htmx:afterSettle, NOT afterSwap: the settle
 * phase (~20ms after the swap) restores the id-matched canvas's
 * original attributes, so a chart initialized at afterSwap has the
 * width/height Chart.js just wrote stripped mid-animation, collapsing
 * the bitmap to a default 300x150 slice ("partial chart" -- the same
 * defect the retirement path chart hit; see retirement_path_chart.js).
 */

(function () {
  var CANVAS_ID = 'growthChart';

  /**
   * Parse a JSON-array data attribute off the canvas.
   * @param {DOMStringMap} dataset - The canvas element's dataset.
   * @param {string} key - Camel-cased dataset key.
   * @returns {Array} Parsed array, or [] when absent/malformed.
   */
  function parseArray(dataset, key) {
    try {
      return JSON.parse(dataset[key] || '[]');
    } catch (err) {
      // Malformed data-* JSON is a server-side serialization bug, not a
      // user error: surface it and degrade to an empty series.
      console.error('Shekel: malformed growth-chart data-' + key + ' JSON', err);
      return [];
    }
  }

  /**
   * Inline plugin: dashed vertical marker at the retirement-year slot.
   * The Today marker's sibling, in the credit token so the landmark
   * reads as a milestone, not a warning; paired with its text label.
   * @param {number} index - Combined-axis slot of the retirement period.
   * @param {string} yearLabel - The retirement year, e.g. "2050".
   * @param {string} color - Marker line + label color.
   * @returns {object} A Chart.js plugin.
   */
  function retirementMarkerPlugin(index, yearLabel, color) {
    return {
      id: 'shekelRetirementMarker',
      afterDatasetsDraw: function (chart) {
        var meta = chart.getDatasetMeta(0);
        if (!meta || !meta.data) return;
        if (index < 0 || index >= meta.data.length) return;

        var xScale = chart.scales.x;
        var yScale = chart.scales.y;
        var x = xScale.getPixelForValue(index);

        var ctx = chart.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.strokeStyle = color;
        ctx.moveTo(x, yScale.top);
        ctx.lineTo(x, yScale.bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.font = "10px 'Inter', system-ui, sans-serif";
        // Keep the label inside the plot when the marker hugs the right
        // edge (the default horizon ends AT the retirement year).
        ctx.textAlign = (x > xScale.right - 60) ? 'right' : 'center';
        ctx.textBaseline = 'top';
        ctx.fillText('Retire ' + yearLabel, x - ((x > xScale.right - 60) ? 4 : 0), yScale.top + 2);
        ctx.restore();
      }
    };
  }

  /**
   * Build the full Chart.js config from the canvas data attributes.
   * Reads fresh each call so theme toggles and HTMX swaps rebuild from
   * current data.
   * @returns {object|null} A Chart.js config, or null when no canvas/data.
   */
  function buildConfig() {
    var canvas = document.getElementById(CANVAS_ID);
    if (!canvas) return null;

    var histLabels = parseArray(canvas.dataset, 'historyLabels');
    var histBalances = parseArray(canvas.dataset, 'historyBalances').map(Number);
    var projLabels = parseArray(canvas.dataset, 'labels');
    var projBalances = parseArray(canvas.dataset, 'balances').map(Number);
    var contributions = parseArray(canvas.dataset, 'contributions').map(Number);
    var whatIf = canvas.dataset.whatifBalances
      ? parseArray(canvas.dataset, 'whatifBalances').map(Number)
      : null;
    var whatIfLabel = canvas.dataset.whatifLabel || 'What-if';
    var boundary = parseInt(canvas.dataset.boundary || '0', 10);
    var retireIndex = canvas.dataset.retirementIndex !== undefined
      ? parseInt(canvas.dataset.retirementIndex, 10)
      : null;
    var retireYear = canvas.dataset.retirementYear || '';

    if (histBalances.length === 0 && projBalances.length === 0) return null;

    // One combined axis: solid history, then the dashed projection.  The
    // baseline and what-if overlays exist only on the projection segment,
    // so their history slots are null (Chart.js skips them).
    var labels = histLabels.concat(projLabels);
    var balances = histBalances.concat(projBalances);
    var histPad = new Array(histBalances.length).fill(null);
    var contribSeries = histPad.concat(contributions);
    var whatIfSeries = whatIf ? histPad.concat(whatIf) : null;

    var style = getComputedStyle(document.documentElement);
    var colors = ShekelChart.getThemeColors();
    var accent = style.getPropertyValue('--shekel-accent').trim();
    var credit = style.getPropertyValue('--shekel-credit').trim();

    var datasets = [
      {
        label: 'Balance',
        data: balances,
        borderColor: accent,
        borderWidth: 2,
        tension: 0,
        pointRadius: 0,
        pointHoverRadius: 3,
        segment: ShekelChart.splitSegment(
          boundary, accent, ShekelChart.hexToRgba(accent, 0.55)
        ),
        fill: {
          target: 'origin',
          above: ShekelChart.hexToRgba(accent, 0.10),
          below: 'transparent'
        }
      },
      {
        label: 'Contributions only',
        data: contribSeries,
        borderColor: colors.textSecondary,
        borderDash: [3, 4],
        borderWidth: 1.5,
        tension: 0,
        pointRadius: 0,
        pointHoverRadius: 3,
        fill: false
      }
    ];
    if (whatIfSeries) {
      // Green dashed hypothetical -- the loan extra-payment preview grammar.
      datasets.push({
        label: whatIfLabel,
        data: whatIfSeries,
        borderColor: ShekelChart.getColor(1),
        borderDash: [6, 5],
        borderWidth: 2,
        tension: 0,
        pointRadius: 0,
        pointHoverRadius: 3,
        fill: false
      });
    }

    var plugins = [
      ShekelChart.todayMarkerPlugin(boundary, colors.textSecondary)
    ];
    if (retireIndex !== null && Number.isFinite(retireIndex)) {
      plugins.push(retirementMarkerPlugin(retireIndex, retireYear, credit));
    }

    return {
      type: 'line',
      data: {
        labels: labels,
        datasets: datasets
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'top' },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                if (ctx.parsed.y === null) return null;
                return ctx.dataset.label + ': ' + ShekelChart.formatMoney(ctx.parsed.y, true);
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxTicksLimit: 13, maxRotation: 0 }
          },
          y: {
            ticks: {
              callback: function (v) {
                return ShekelChart.formatMoney(v, false);
              }
            }
          }
        }
      },
      plugins: plugins
    };
  }

  /**
   * Render (or re-render) the growth chart when its canvas is present.
   */
  function renderGrowthChart() {
    if (typeof ShekelChart === 'undefined' || typeof Chart === 'undefined') {
      return;
    }
    if (!document.getElementById(CANVAS_ID)) return;
    ShekelChart.create(CANVAS_ID, buildConfig);
  }

  /**
   * Bind the what-if contribution input to trigger a chart refresh.
   *
   * Fires the slider-changed event on the growth-chart container after a
   * debounced delay, matching the horizon slider's pattern; the HTMX
   * request includes both horizon_years and what_if_contribution via
   * hx-include.  The input lives outside the swap target so it persists;
   * data-whatif-bound prevents duplicate listeners.
   */
  function bindWhatIfInput() {
    var input = document.getElementById('what_if_contribution');
    if (!input || input.hasAttribute('data-whatif-bound')) return;
    input.setAttribute('data-whatif-bound', 'true');

    var timer;
    input.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var container = document.getElementById('growth-chart-container');
        if (container && typeof htmx !== 'undefined') {
          htmx.trigger(container, 'slider-changed');
        }
      }, 300);
    });
  }

  /**
   * Keep the Horizon lever's headline ("24 yr -> 2050, your planned
   * retirement year") in sync with the slider / number input.  Calendar
   * arithmetic only (current year + horizon) -- no monetary values.
   */
  function syncHorizonCaption() {
    var display = document.getElementById('horizon-display');
    var caption = document.getElementById('horizon-year-caption');
    var input = document.getElementById('horizon_years');
    if (!display || !caption || !input) return;

    var years = parseInt(input.value, 10);
    if (!Number.isFinite(years)) return;
    display.textContent = String(years);

    var target = new Date().getFullYear() + years;
    var retireYear = parseInt(caption.getAttribute('data-retirement-year') || '', 10);
    caption.textContent = Number.isFinite(retireYear) && target === retireYear
      ? '→ ' + target + ', your planned retirement year'
      : '→ ' + target;
  }

  /**
   * Bind the horizon inputs to the caption sync (once per element).
   */
  function bindHorizonCaption() {
    ['horizon_slider', 'horizon_years'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || el.hasAttribute('data-horizon-caption-bound')) return;
      el.setAttribute('data-horizon-caption-bound', 'true');
      el.addEventListener('input', syncHorizonCaption);
    });
    syncHorizonCaption();
  }

  // Auto-initialize on page load.
  document.addEventListener('DOMContentLoaded', function () {
    renderGrowthChart();
    bindWhatIfInput();
    bindHorizonCaption();
  });

  // Re-render after HTMX swaps replace the fragment's canvas -- on
  // afterSettle, not afterSwap (see the module docstring).
  document.addEventListener('htmx:afterSettle', function () {
    if (document.getElementById(CANVAS_ID)) {
      renderGrowthChart();
    }
    bindWhatIfInput();
    bindHorizonCaption();
  });
})();
