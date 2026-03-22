'use strict';

/**
 * Shekel Budget App -- Amortization Breakdown Chart
 *
 * Renders a stacked area chart showing principal vs. interest
 * composition of each loan payment over the life of the loan.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderAmortizationBreakdown(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var principal = JSON.parse(canvas.getAttribute('data-principal') || '[]');
  var interest = JSON.parse(canvas.getAttribute('data-interest') || '[]');

  if (labels.length === 0) return;

  ShekelChart.create(canvasId, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Principal',
          data: principal,
          borderColor: ShekelChart.getColor(1),
          backgroundColor: ShekelChart.getColor(1) + '40',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          pointHitRadius: 10,
          order: 2,
        },
        {
          label: 'Interest',
          data: interest,
          borderColor: ShekelChart.getColor(2),
          backgroundColor: ShekelChart.getColor(2) + '40',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          pointHitRadius: 10,
          order: 1,
        },
      ],
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
          stacked: true,
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
            footer: function(tooltipItems) {
              var total = tooltipItems.reduce(function(sum, item) {
                return sum + item.parsed.y;
              }, 0);
              return 'Total Payment: $' + total.toLocaleString(undefined, {
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
  if (document.getElementById('chart-amortization')) {
    renderAmortizationBreakdown('chart-amortization');
  }
});
