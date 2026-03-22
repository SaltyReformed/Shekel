'use strict';

/**
 * Shekel Budget App -- Budget vs. Actuals Chart
 *
 * Renders a grouped bar chart comparing estimated and actual amounts
 * per category. Overspend is highlighted in red.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderBudgetVsActuals(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var estimated = JSON.parse(canvas.getAttribute('data-estimated') || '[]');
  var actual = JSON.parse(canvas.getAttribute('data-actual') || '[]');

  if (labels.length === 0) return;

  // Color actual bars based on over/under budget.
  var actualColors = actual.map(function(val, i) {
    if (val > estimated[i]) {
      return ShekelChart.getColor(6);  // Coral/danger for overspend.
    }
    return ShekelChart.getColor(1);  // Green for under budget.
  });

  ShekelChart.create(canvasId, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Estimated',
          data: estimated,
          backgroundColor: ShekelChart.getColor(7) + '99',
          borderColor: ShekelChart.getColor(7),
          borderWidth: 1,
        },
        {
          label: 'Actual',
          data: actual,
          backgroundColor: actualColors.map(function(c) { return c + 'CC'; }),
          borderColor: actualColors,
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
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
    },
  });
}

// Initialize after HTMX swap.
document.addEventListener('htmx:afterSwap', function() {
  if (document.getElementById('chart-budget-vs-actuals')) {
    renderBudgetVsActuals('chart-budget-vs-actuals');
  }
});
