'use strict';

/**
 * Shekel Budget App — Retirement Income Gap Chart
 *
 * Renders a Chart.js horizontal stacked bar chart showing pension income,
 * investment income, and the remaining gap relative to pre-retirement income.
 * Reads data from data-* attributes on the canvas element (CSP-compliant).
 * Uses ShekelChart.create() for consistent theming.
 *
 * @param {string} [canvasId='gapChart'] - The canvas element ID.
 */
function renderGapChart(canvasId) {
  canvasId = canvasId || 'gapChart';
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var pension = parseFloat(canvas.dataset.pension) || 0;
  var investment = parseFloat(canvas.dataset.investment) || 0;
  var preRetirement = parseFloat(canvas.dataset.preRetirement) || 0;

  if (preRetirement <= 0) return;

  var covered = pension + investment;
  var remaining = Math.max(0, preRetirement - covered);

  ShekelChart.create(canvasId, {
    type: 'bar',
    data: {
      labels: ['Monthly Income'],
      datasets: [
        {
          label: 'Pension',
          data: [pension],
          backgroundColor: ShekelChart.getColor(1),
        },
        {
          label: 'Investment Income (SWR)',
          data: [investment],
          backgroundColor: ShekelChart.getColor(0),
        },
        {
          label: 'Gap',
          data: [remaining],
          backgroundColor: remaining > 0 ? ShekelChart.getColor(6) : ShekelChart.getColor(1),
        },
      ],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return ctx.dataset.label + ': $' + ctx.parsed.x.toLocaleString(undefined, {
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
          stacked: true,
          ticks: {
            callback: function (v) {
              return '$' + v.toLocaleString();
            },
          },
        },
        y: {
          stacked: true,
          grid: { display: false },
        },
      },
    },
  });
}

// Auto-initialize on page load.
document.addEventListener('DOMContentLoaded', function () {
  renderGapChart();
});

// Re-render after HTMX swaps.
document.addEventListener('htmx:afterSwap', function () {
  if (document.getElementById('gapChart')) {
    renderGapChart();
  }
});
