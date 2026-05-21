'use strict';

/**
 * Shekel Budget App -- Budget Variance Chart
 *
 * Renders a grouped bar chart comparing estimated and actual amounts
 * per category group.  Actual bars are color-coded: green when under
 * budget, red/coral when over budget.  Follows the pattern from
 * chart_budget.js.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderVarianceChart(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var estimated = JSON.parse(canvas.getAttribute('data-estimated') || '[]');
  var actual = JSON.parse(canvas.getAttribute('data-actual') || '[]');
  // MED-04 / E-17 / JN-03: the per-group variance (actual - estimated)
  // is computed server-side in _build_variance_chart_data so the
  // tooltip below renders the same number as the table, not a
  // re-computation that could drift from the canonical formula.
  var variance = JSON.parse(canvas.getAttribute('data-variance') || '[]');

  if (labels.length === 0) return;

  // Color actual bars based on over/under budget.  This is a
  // comparison (not arithmetic) on the server-computed values; per
  // the coding standard JS monetary values are display-only.
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
              return context.dataset.label + ': $' +
                context.parsed.y.toLocaleString(undefined, {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                });
            },
            afterBody: function(items) {
              var idx = items[0].dataIndex;
              var diff = variance[idx];
              var prefix = diff >= 0 ? '+$' : '-$';
              return 'Variance: ' + prefix +
                Math.abs(diff).toLocaleString(undefined, {
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

// Initialize after HTMX swap (variance tab is lazy-loaded).
document.addEventListener('htmx:afterSwap', function() {
  if (document.getElementById('chart-variance')) {
    renderVarianceChart('chart-variance');
  }
});

// "Show only variances" toggle -- hides rows with zero variance.
document.addEventListener('htmx:afterSwap', function() {
  var toggle = document.getElementById('variance-filter-toggle');
  if (!toggle) return;

  toggle.addEventListener('change', function() {
    var rows = document.querySelectorAll(
      '#variance-table tbody tr[data-variance]'
    );
    rows.forEach(function(row) {
      var v = parseFloat(row.getAttribute('data-variance'));
      if (toggle.checked && v === 0) {
        row.classList.add('d-none');
      } else {
        row.classList.remove('d-none');
      }
    });
  });
});
