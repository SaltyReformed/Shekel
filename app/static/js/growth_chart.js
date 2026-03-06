/**
 * Shekel Budget App — Investment Growth Chart
 *
 * Renders a Chart.js line chart showing projected balance over time
 * with contributions overlaid. Reads data from data-* attributes
 * on the canvas element (CSP-compliant).
 */
(function () {
  "use strict";

  const canvas = document.getElementById("growthChart");
  if (!canvas) return;

  const labels = JSON.parse(canvas.dataset.labels || "[]");
  const balances = JSON.parse(canvas.dataset.balances || "[]").map(Number);
  const contributions = JSON.parse(canvas.dataset.contributions || "[]").map(Number);

  if (labels.length === 0) return;

  new Chart(canvas, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Projected Balance",
          data: balances,
          borderColor: "#0d6efd",
          backgroundColor: "rgba(13, 110, 253, 0.1)",
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        },
        {
          label: "Contributions Only",
          data: contributions,
          borderColor: "#198754",
          borderDash: [5, 5],
          fill: false,
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 1.5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return ctx.dataset.label + ": $" + ctx.parsed.y.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              });
            },
          },
        },
        legend: { position: "top" },
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 12 },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        y: {
          ticks: {
            callback: function (v) {
              return "$" + v.toLocaleString();
            },
          },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
      },
    },
  });
})();
