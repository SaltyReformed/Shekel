'use strict';

/**
 * Shekel Budget App -- Budget Variance Chart
 *
 * Renders a grouped bar chart comparing estimated and actual amounts
 * per category group.  Actual bars are color-coded: green when under
 * budget, red/coral when over budget.  Uses the ShekelChart.create()
 * factory pattern from chart_theme.js.
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

  // Config factory: colors resolve inside so a theme toggle rebuilds
  // them against the active theme (ShekelChart.create re-invokes it).
  ShekelChart.create(canvasId, function() {
    // Color actual bars based on over/under budget.  This is a
    // comparison (not arithmetic) on the server-computed values; per
    // the coding standard JS monetary values are display-only.
    var actualColors = actual.map(function(val, i) {
      if (val > estimated[i]) {
        return ShekelChart.getColor(6);  // Coral/danger for overspend.
      }
      return ShekelChart.getColor(1);  // Green for under budget.
    });

    return {
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
                return context.dataset.label + ': ' +
                  ShekelChart.formatMoney(context.parsed.y, true);
              },
              afterBody: function(items) {
                var idx = items[0].dataIndex;
                var diff = variance[idx];
                // formatMoney renders the '-' itself; only the explicit
                // '+' for non-negative variance is added here.
                var prefix = diff >= 0 ? '+' : '';
                return 'Variance: ' + prefix +
                  ShekelChart.formatMoney(diff, true);
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
                return ShekelChart.formatMoney(value, false);
              },
            },
          },
        },
      },
    };
  });
}

// Initialize after HTMX swap (variance tab is lazy-loaded).
document.addEventListener('htmx:afterSwap', function() {
  if (document.getElementById('chart-variance')) {
    renderVarianceChart('chart-variance');
  }
});

// "Show only variances" toggle -- hides rows with zero variance.  This fires
// on EVERY htmx:afterSwap, so bind the change listener once per toggle
// element: the toggle is replaced wholesale on each analytics swap today (old
// listeners GC with the old node), but a future refactor that kept it across
// swaps would otherwise stack a duplicate change listener every swap (JS-15).
document.addEventListener('htmx:afterSwap', function() {
  var toggle = document.getElementById('variance-filter-toggle');
  if (!toggle || toggle.dataset.varianceFilterBound) return;
  toggle.dataset.varianceFilterBound = 'true';

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
