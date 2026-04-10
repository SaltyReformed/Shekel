'use strict';

/**
 * Shekel Budget App -- Year-End Net Worth Chart
 *
 * Renders a line chart with area fill showing net worth at 12
 * monthly endpoints for the selected calendar year.  Follows the
 * established ShekelChart.create() pattern with data-* attributes
 * for CSP compliance.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderYearEndNetWorth(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var data = JSON.parse(canvas.getAttribute('data-data') || '[]');

  if (labels.length === 0) return;

  // Abbreviate month names for compact x-axis labels.
  var shortLabels = labels.map(function(name) {
    return name.substring(0, 3);
  });

  // Determine fill color based on whether net worth is positive.
  var minVal = Math.min.apply(null, data);
  var fillColor = minVal >= 0
    ? ShekelChart.getColor(1) + '30'
    : ShekelChart.getColor(6) + '30';

  ShekelChart.create(canvasId, {
    type: 'line',
    data: {
      labels: shortLabels,
      datasets: [{
        label: 'Net Worth',
        data: data,
        borderColor: ShekelChart.getColor(0),
        backgroundColor: fillColor,
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        pointRadius: 3,
        pointHitRadius: 10,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          grid: { display: false },
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
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: function(items) {
              // Show full month name from original labels.
              return labels[items[0].dataIndex] || items[0].label;
            },
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

// Initialize after HTMX swap (year-end tab is lazy-loaded).
document.addEventListener('htmx:afterSwap', function() {
  if (document.getElementById('chart-year-end-nw')) {
    renderYearEndNetWorth('chart-year-end-nw');
  }
});
