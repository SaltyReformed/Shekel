'use strict';

/**
 * Shekel Budget App -- Investment Growth Chart
 *
 * Renders a Chart.js line chart showing projected balance over time.
 * Supports two display modes:
 *
 * Standard mode (no what-if):
 *   - "Projected Balance" (solid, filled)
 *   - "Contributions Only" (dashed)
 *
 * What-if mode (what-if amount provided):
 *   - "Current Plan" (solid, filled)
 *   - "What-If ($X/period)" (dashed, distinct color)
 *
 * Reads data from data-* attributes on the canvas element (CSP-compliant).
 * Uses ShekelChart.create() for consistent theming.
 */

/**
 * Render the growth chart, handling both standard and what-if modes.
 * @param {string} [canvasId='growthChart'] - The canvas element ID.
 */
function renderGrowthChart(canvasId) {
  canvasId = canvasId || 'growthChart';
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.dataset.labels || '[]');
  var balances = JSON.parse(canvas.dataset.balances || '[]').map(Number);
  var contributions = JSON.parse(canvas.dataset.contributions || '[]').map(Number);
  var whatIfBalances = canvas.dataset.whatifBalances
    ? JSON.parse(canvas.dataset.whatifBalances).map(Number)
    : null;
  var whatIfLabel = canvas.dataset.whatifLabel || 'What-If';

  if (labels.length === 0) return;

  var datasets;
  if (whatIfBalances && whatIfBalances.length > 0) {
    // What-if mode: committed plan vs. hypothetical scenario.
    datasets = [
      {
        label: 'Current Plan',
        data: balances,
        borderColor: ShekelChart.getColor(0),
        backgroundColor: ShekelChart.getColor(0) + '1A',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      },
      {
        label: whatIfLabel,
        data: whatIfBalances,
        borderColor: ShekelChart.getColor(2),
        borderDash: [5, 5],
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      },
    ];
  } else {
    // Standard mode: projected balance + contributions baseline.
    datasets = [
      {
        label: 'Projected Balance',
        data: balances,
        borderColor: ShekelChart.getColor(0),
        backgroundColor: ShekelChart.getColor(0) + '1A',
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
      },
      {
        label: 'Contributions Only',
        data: contributions,
        borderColor: ShekelChart.getColor(1),
        borderDash: [5, 5],
        fill: false,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 1.5,
      },
    ];
  }

  ShekelChart.create(canvasId, {
    type: 'line',
    data: {
      labels: labels,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return ctx.dataset.label + ': $' + ctx.parsed.y.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              });
            },
          },
        },
        legend: { position: 'top' },
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 12 },
        },
        y: {
          ticks: {
            callback: function (v) {
              return '$' + v.toLocaleString();
            },
          },
        },
      },
    },
  });
}

/**
 * Bind the what-if contribution input to trigger chart refresh.
 *
 * Fires the slider-changed event on the growth chart container
 * after a debounced delay, matching the horizon slider's pattern.
 * The HTMX request on the container includes both horizon_years
 * and what_if_contribution via hx-include.
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

// Auto-initialize on page load.
document.addEventListener('DOMContentLoaded', function () {
  renderGrowthChart();
  bindWhatIfInput();
});

// Re-render after HTMX swaps.
document.addEventListener('htmx:afterSwap', function () {
  if (document.getElementById('growthChart')) {
    renderGrowthChart();
  }
  // What-if input is outside the swap target so it persists.
  // data-whatif-bound prevents duplicate listeners.
  bindWhatIfInput();
});
