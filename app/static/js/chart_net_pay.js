'use strict';

/**
 * Shekel Budget App — Net Pay Trajectory Chart
 *
 * Renders a step line chart showing how net biweekly pay changes
 * over time due to scheduled raises. Includes gross pay as a
 * secondary dashed line.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderNetPayTrajectory(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var data = JSON.parse(canvas.getAttribute('data-data') || '[]');
  var gross = JSON.parse(canvas.getAttribute('data-gross') || '[]');

  if (labels.length === 0) return;

  var datasets = [
    {
      label: 'Net Pay',
      data: data,
      borderColor: ShekelChart.getColor(1),
      backgroundColor: ShekelChart.getColor(1) + '20',
      borderWidth: 2,
      fill: true,
      stepped: 'before',
      pointRadius: 3,
      pointHitRadius: 10,
    },
  ];

  // Add gross pay line if available.
  if (gross.length > 0) {
    datasets.push({
      label: 'Gross Pay',
      data: gross,
      borderColor: ShekelChart.getColor(7),
      borderDash: [5, 5],
      borderWidth: 1.5,
      fill: false,
      stepped: 'before',
      pointRadius: 2,
      pointHitRadius: 10,
    });
  }

  ShekelChart.create(canvasId, {
    type: 'line',
    data: { labels: labels, datasets: datasets },
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
              return context.dataset.label + ': $' + context.parsed.y.toLocaleString(undefined, {
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
  if (document.getElementById('chart-net-pay')) {
    renderNetPayTrajectory('chart-net-pay');
  }
});
