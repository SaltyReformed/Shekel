'use strict';

/**
 * Shekel Budget App — Investment Growth Chart
 *
 * Renders a Chart.js line chart showing projected balance over time
 * with contributions overlaid. Reads data from data-* attributes
 * on the canvas element (CSP-compliant).
 * Uses ShekelChart.create() for consistent theming.
 *
 * @param {string} [canvasId='growthChart'] - The canvas element ID.
 */
function renderGrowthChart(canvasId) {
  canvasId = canvasId || 'growthChart';
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.dataset.labels || '[]');
  var balances = JSON.parse(canvas.dataset.balances || '[]').map(Number);
  var contributions = JSON.parse(canvas.dataset.contributions || '[]').map(Number);

  if (labels.length === 0) return;

  ShekelChart.create(canvasId, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
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
      ],
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

// Auto-initialize on page load.
document.addEventListener('DOMContentLoaded', function () {
  renderGrowthChart();
});

// Re-render after HTMX swaps.
document.addEventListener('htmx:afterSwap', function () {
  if (document.getElementById('growthChart')) {
    renderGrowthChart();
  }
});
