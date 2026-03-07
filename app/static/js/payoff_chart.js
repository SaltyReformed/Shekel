'use strict';

/**
 * Shekel Budget App — Payoff Chart (Chart.js)
 *
 * Renders a line chart showing loan balance over time.
 * Data is read from data-* attributes on the canvas element (CSP-compliant).
 * Supports optional accelerated (extra payment) schedule overlay.
 * Uses ShekelChart.create() for consistent theming.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderPayoffChart(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var standard = JSON.parse(canvas.getAttribute('data-standard') || '[]');
  var accelerated = JSON.parse(canvas.getAttribute('data-accelerated') || '[]');

  if (labels.length === 0 || standard.length === 0) return;

  var datasets = [{
    label: 'Standard Payments',
    data: standard,
    borderColor: ShekelChart.getColor(7),
    backgroundColor: ShekelChart.getColor(7) + '1A',
    borderWidth: 2,
    fill: true,
    tension: 0.3,
    pointRadius: 0,
  }];

  if (accelerated.length > 0) {
    datasets.push({
      label: 'With Extra Payments',
      data: accelerated,
      borderColor: ShekelChart.getColor(1),
      backgroundColor: ShekelChart.getColor(1) + '1A',
      borderWidth: 2,
      fill: true,
      tension: 0.3,
      pointRadius: 0,
    });
  }

  ShekelChart.create(canvasId, {
    type: 'line',
    data: {
      labels: labels,
      datasets: datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      scales: {
        x: {
          display: true,
          ticks: { maxTicksLimit: 12 },
        },
        y: {
          display: true,
          ticks: {
            callback: function(value) {
              return '$' + value.toLocaleString();
            },
          },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: function(context) {
              return context.dataset.label + ': $' + context.parsed.y.toLocaleString(undefined, {
                minimumFractionDigits: 0,
                maximumFractionDigits: 0,
              });
            },
          },
        },
      },
    },
  });
}

// Auto-initialize chart on page load.
document.addEventListener('DOMContentLoaded', function() {
  renderPayoffChart('payoff-chart');
});

// Re-render after HTMX swaps (for payoff calculator results).
document.addEventListener('htmx:afterSwap', function() {
  if (document.getElementById('payoff-chart-results')) {
    renderPayoffChart('payoff-chart-results');
  }
});
