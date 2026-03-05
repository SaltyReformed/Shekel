/**
 * Shekel Budget App — Payoff Chart (Chart.js)
 *
 * Renders a line chart showing loan balance over time.
 * Data is read from data-* attributes on the canvas element (CSP-compliant).
 * Supports optional accelerated (extra payment) schedule overlay.
 */

function renderPayoffChart(canvasId) {
  var canvas = document.getElementById(canvasId);
  if (!canvas) return;

  var labels = JSON.parse(canvas.getAttribute('data-labels') || '[]');
  var standard = JSON.parse(canvas.getAttribute('data-standard') || '[]');
  var accelerated = JSON.parse(canvas.getAttribute('data-accelerated') || '[]');

  if (labels.length === 0 || standard.length === 0) return;

  // Destroy existing chart instance if any.
  if (canvas._chartInstance) {
    canvas._chartInstance.destroy();
  }

  var datasets = [{
    label: 'Standard Payments',
    data: standard,
    borderColor: '#6c757d',
    backgroundColor: 'rgba(108, 117, 125, 0.1)',
    borderWidth: 2,
    fill: true,
    tension: 0.3,
    pointRadius: 0,
  }];

  if (accelerated.length > 0) {
    datasets.push({
      label: 'With Extra Payments',
      data: accelerated,
      borderColor: '#198754',
      backgroundColor: 'rgba(25, 135, 84, 0.1)',
      borderWidth: 2,
      fill: true,
      tension: 0.3,
      pointRadius: 0,
    });
  }

  canvas._chartInstance = new Chart(canvas, {
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
          ticks: {
            maxTicksLimit: 12,
            color: '#adb5bd',
          },
          grid: {
            color: 'rgba(255, 255, 255, 0.05)',
          },
        },
        y: {
          display: true,
          ticks: {
            color: '#adb5bd',
            callback: function(value) {
              return '$' + value.toLocaleString();
            },
          },
          grid: {
            color: 'rgba(255, 255, 255, 0.05)',
          },
        },
      },
      plugins: {
        legend: {
          labels: {
            color: '#dee2e6',
          },
        },
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
document.addEventListener('htmx:afterSwap', function(evt) {
  var resultChart = document.getElementById('payoff-chart-results');
  if (resultChart) {
    renderPayoffChart('payoff-chart-results');
  }
});
