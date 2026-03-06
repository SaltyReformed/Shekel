/**
 * Shekel Budget App — Retirement Income Gap Chart
 *
 * Renders a Chart.js horizontal stacked bar chart showing pension income,
 * investment income, and the remaining gap relative to pre-retirement income.
 * Reads data from data-* attributes on the canvas element (CSP-compliant).
 */
(function () {
  "use strict";

  const canvas = document.getElementById("gapChart");
  if (!canvas) return;

  const pension = parseFloat(canvas.dataset.pension) || 0;
  const investment = parseFloat(canvas.dataset.investment) || 0;
  const gap = parseFloat(canvas.dataset.gap) || 0;
  const preRetirement = parseFloat(canvas.dataset.preRetirement) || 0;

  if (preRetirement <= 0) return;

  // Calculate remaining gap after pension + investment income.
  const covered = pension + investment;
  const remaining = Math.max(0, preRetirement - covered);

  new Chart(canvas, {
    type: "bar",
    data: {
      labels: ["Monthly Income"],
      datasets: [
        {
          label: "Pension",
          data: [pension],
          backgroundColor: "#198754",
        },
        {
          label: "Investment Income (SWR)",
          data: [investment],
          backgroundColor: "#0d6efd",
        },
        {
          label: "Gap",
          data: [remaining],
          backgroundColor: remaining > 0 ? "#dc3545" : "#198754",
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: function (ctx) {
              return ctx.dataset.label + ": $" + ctx.parsed.x.toLocaleString(undefined, {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              });
            },
          },
        },
        legend: { position: "top" },
        annotation: {
          annotations: {
            targetLine: {
              type: "line",
              xMin: preRetirement,
              xMax: preRetirement,
              borderColor: "#ffc107",
              borderWidth: 2,
              borderDash: [6, 6],
              label: {
                display: true,
                content: "Pre-retirement: $" + preRetirement.toLocaleString(),
                position: "start",
              },
            },
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          ticks: {
            callback: function (v) {
              return "$" + v.toLocaleString();
            },
          },
          grid: { color: "rgba(255,255,255,0.05)" },
        },
        y: {
          stacked: true,
          grid: { display: false },
        },
      },
    },
  });
})();
