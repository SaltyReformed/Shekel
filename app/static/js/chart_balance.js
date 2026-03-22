'use strict';

/**
 * Shekel Budget App -- Balance Over Time Chart
 *
 * Renders a multi-line chart showing account balances over time.
 * Supports dual Y-axis mode for separating small balances (checking/savings)
 * from large balances (mortgage/retirement).
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderBalanceOverTime(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var datasets = JSON.parse(canvas.getAttribute('data-datasets') || '[]');

  if (labels.length === 0 || datasets.length === 0) return;

  // Check dual-axis toggle state.
  var dualAxisToggle = document.getElementById('dual-axis-toggle');
  var dualAxis = dualAxisToggle ? dualAxisToggle.checked : true;

  // Determine if we need two axes.
  var hasLeftAxis = false;
  var hasRightAxis = false;

  var chartDatasets = datasets.map(function(ds, i) {
    var axis = dualAxis ? (ds.axis || 'y') : 'y';
    if (axis === 'y') hasLeftAxis = true;
    if (axis === 'y1') hasRightAxis = true;

    return {
      label: ds.label,
      data: ds.data,
      borderColor: ShekelChart.getColor(i),
      backgroundColor: ShekelChart.getColor(i) + '1A',
      borderWidth: 2,
      fill: false,
      tension: 0.3,
      pointRadius: 0,
      pointHitRadius: 10,
      yAxisID: axis,
    };
  });

  var scales = {
    x: {
      ticks: { maxTicksLimit: 12 },
    },
    y: {
      display: hasLeftAxis || !dualAxis,
      position: 'left',
      ticks: {
        callback: function(value) {
          return '$' + value.toLocaleString();
        },
      },
    },
  };

  // Only add the right axis if dual-axis is enabled and has data.
  if (dualAxis && hasRightAxis) {
    scales.y1 = {
      display: true,
      position: 'right',
      ticks: {
        callback: function(value) {
          return '$' + value.toLocaleString();
        },
      },
      grid: { drawOnChartArea: false },
    };
  }

  ShekelChart.create(canvasId, {
    type: 'line',
    data: { labels: labels, datasets: chartDatasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: scales,
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

// Client-side dual-axis toggle -- no server round-trip needed.
document.addEventListener('change', function(e) {
  if (e.target && e.target.id === 'dual-axis-toggle') {
    renderBalanceOverTime('chart-balance-over-time');
  }
});

// Initialize after HTMX swap.
document.addEventListener('htmx:afterSwap', function() {
  if (document.getElementById('chart-balance-over-time')) {
    renderBalanceOverTime('chart-balance-over-time');
  }
});
