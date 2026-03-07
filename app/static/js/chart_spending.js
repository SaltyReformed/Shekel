'use strict';

/**
 * Shekel Budget App — Spending by Category Chart
 *
 * Renders a horizontal bar chart showing spending grouped by category.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderSpendingByCategory(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var data = JSON.parse(canvas.getAttribute('data-data') || '[]');

  if (labels.length === 0) return;

  // Generate a gradient of rose/expense colors for each bar.
  var colors = labels.map(function(_, i) {
    return ShekelChart.getColor(3);
  });

  ShekelChart.create(canvasId, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Spending',
        data: data,
        backgroundColor: colors.map(function(c) { return c + 'CC'; }),
        borderColor: colors,
        borderWidth: 1,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function(context) {
              return '$' + context.parsed.x.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              });
            },
          },
        },
      },
      scales: {
        x: {
          ticks: {
            callback: function(value) {
              return '$' + value.toLocaleString();
            },
          },
        },
        y: {
          grid: { display: false },
        },
      },
    },
  });
}

// Initialize after HTMX swap.
document.addEventListener('htmx:afterSwap', function() {
  if (document.getElementById('chart-spending-category')) {
    renderSpendingByCategory('chart-spending-category');
  }
});
