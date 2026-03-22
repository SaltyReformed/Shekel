'use strict';

/**
 * Shekel Budget App -- Net Worth Over Time Chart
 *
 * Renders a line chart with area fill showing total assets minus
 * total liabilities over time. Fill is green when positive,
 * red when negative.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderNetWorth(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var data = JSON.parse(canvas.getAttribute('data-data') || '[]');

  if (labels.length === 0) return;

  // Determine if net worth is mostly positive or negative for fill color.
  var minVal = Math.min.apply(null, data);
  var fillColor = minVal >= 0
    ? ShekelChart.getColor(1) + '30'
    : ShekelChart.getColor(6) + '30';

  ShekelChart.create(canvasId, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'Net Worth',
        data: data,
        borderColor: ShekelChart.getColor(0),
        backgroundColor: fillColor,
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHitRadius: 10,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          ticks: { maxTicksLimit: 12 },
        },
        y: {
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
              var val = context.parsed.y;
              var prefix = val < 0 ? '-$' : '$';
              return 'Net Worth: ' + prefix + Math.abs(val).toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              });
            },
          },
        },
      },
    },
  });
}

// Initialize after HTMX swap.
document.addEventListener('htmx:afterSwap', function() {
  if (document.getElementById('chart-net-worth')) {
    renderNetWorth('chart-net-worth');
  }
});
