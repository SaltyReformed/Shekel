'use strict';

/**
 * Shekel Budget App -- Loan Payoff Chart (Chart.js)
 *
 * Renders a multi-scenario line chart showing loan balance over time.
 * Supports up to four scenarios:
 *
 *   - Original: contractual baseline (dashed, lighter)
 *   - Committed: all payments applied (solid, primary)
 *   - Floor: confirmed payments only (dashed, distinct)
 *   - Accelerated: with extra payments (dashed, amber)
 *
 * Data is read from data-* attributes on the canvas element (CSP-compliant).
 * Uses ShekelChart.create() for consistent theming.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderPayoffChart(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var original = JSON.parse(canvas.getAttribute('data-original') || '[]');
  var committed = JSON.parse(canvas.getAttribute('data-committed') || '[]');
  var floor = JSON.parse(canvas.getAttribute('data-floor') || '[]');
  var accelerated = JSON.parse(canvas.getAttribute('data-accelerated') || '[]');

  // Backward compat: data-standard maps to original for older callers.
  if (original.length === 0) {
    var standard = JSON.parse(canvas.getAttribute('data-standard') || '[]');
    if (standard.length > 0) {
      original = standard;
    }
  }

  if (labels.length === 0 || original.length === 0) return;

  var datasets = [];

  // Original: always present as reference baseline.
  datasets.push({
    label: 'Original Schedule',
    data: original,
    borderColor: ShekelChart.getColor(7),
    borderDash: [5, 5],
    borderWidth: 1.5,
    fill: false,
    tension: 0.3,
    pointRadius: 0,
  });

  // Committed: present when real payments exist.
  if (committed.length > 0) {
    datasets.push({
      label: 'Current Plan',
      data: committed,
      borderColor: ShekelChart.getColor(0),
      backgroundColor: ShekelChart.getColor(0) + '1A',
      borderWidth: 2.5,
      fill: true,
      tension: 0.3,
      pointRadius: 0,
    });
  }

  // Floor: confirmed payments only (when different from committed).
  if (floor.length > 0 && committed.length > 0) {
    datasets.push({
      label: 'Confirmed Only',
      data: floor,
      borderColor: ShekelChart.getColor(4),
      borderDash: [3, 3],
      borderWidth: 1.5,
      fill: false,
      tension: 0.3,
      pointRadius: 0,
    });
  }

  // Accelerated: with extra payments (payoff calculator results).
  if (accelerated.length > 0) {
    datasets.push({
      label: 'With Extra Payments',
      data: accelerated,
      borderColor: ShekelChart.getColor(2),
      borderDash: [8, 4],
      borderWidth: 2,
      fill: false,
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
