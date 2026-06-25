/**
 * Shekel Budget App -- Net Worth Cockpit renderer.
 *
 * Renders the accounts screen's forward net-worth trend chart (Chart.js
 * via the ShekelChart factory, so a theme toggle re-resolves colors).
 * The series arrives as a JSON ``data-chart`` attribute on the canvas:
 * {labels: [str], net: [float], assets: [float], liabilities: [float],
 *  actual_count: int}. Floats exist only at that serialization boundary
 * (the route's _serialize_net_worth_chart); this script never computes
 * money, it only formats axis and tooltip labels from the provided
 * numbers.
 *
 * Slice 1 (static shell + trend) renders the ``net`` series as a single
 * continuous line, matching the dashboard pulse chart's aesthetic. The
 * Net-vs-Assets/Liabilities toggle, the actual-vs-projected dash split,
 * the Today marker, and the horizon picker are later phases (they need
 * the series widened to include past periods).
 *
 * Re-initializes after every ``htmx:afterSwap`` so a future
 * balanceChanged-driven refresh rebuilds the chart for the swapped-in
 * region.
 */

(function () {
  "use strict";

  /**
   * Convert a hex color (#rgb or #rrggbb) to an rgba() string.
   * @param {string} hex - Hex color from a CSS custom property.
   * @param {number} alpha - Alpha channel 0..1.
   * @returns {string} rgba(...) color.
   */
  function hexToRgba(hex, alpha) {
    var h = hex.replace("#", "").trim();
    if (h.length === 3) {
      h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    }
    var num = parseInt(h, 16);
    if (!Number.isFinite(num)) return "rgba(0,0,0," + alpha + ")";
    var r = (num >> 16) & 255;
    var g = (num >> 8) & 255;
    var b = num & 255;
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  /**
   * Build (or rebuild) the net-worth trend chart from the canvas's
   * ``data-chart`` JSON.
   * @param {Element|Document} root - Subtree containing the canvas.
   */
  function initChart(root) {
    var scope = root && root.querySelector ? root : document;
    var canvas = scope.querySelector("#net-worth-chart-canvas") ||
      (scope.id === "net-worth-chart-canvas" ? scope : null);
    if (!canvas || typeof ShekelChart === "undefined" ||
        typeof Chart === "undefined") {
      return;
    }

    var data;
    try {
      data = JSON.parse(canvas.getAttribute("data-chart") || "{}");
    } catch (err) {
      // Malformed data-chart JSON is a server-side serialization bug, not
      // a user error: surface it to the console and bail out of rendering
      // (a broken chart must not take down the rest of the page's JS).
      console.error("Shekel: malformed net-worth data-chart JSON", err);
      return;
    }
    if (!data.net || !data.net.length) return;

    ShekelChart.create("net-worth-chart-canvas", function () {
      var style = getComputedStyle(document.documentElement);
      var accent = style.getPropertyValue("--shekel-accent").trim();
      var danger = style.getPropertyValue("--shekel-danger").trim();
      var colors = ShekelChart.getThemeColors();

      var datasets = [{
        data: data.net,
        borderColor: accent,
        borderWidth: 2,
        tension: 0,
        pointRadius: function (ctx) {
          return ctx.parsed && ctx.parsed.y < 0 ? 4 : 2.5;
        },
        pointBackgroundColor: function (ctx) {
          return ctx.parsed && ctx.parsed.y < 0 ? danger : accent;
        },
        // Semantic fill: faint accent above zero, danger pocket below.
        fill: {
          target: "origin",
          above: hexToRgba(accent, 0.10),
          below: hexToRgba(danger, 0.25)
        }
      }];

      return {
        type: "line",
        data: { labels: data.labels, datasets: datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  return ShekelChart.formatMoney(ctx.parsed.y, true);
                }
              }
            }
          },
          scales: {
            y: {
              grid: {
                // Emphasize the zero line; keep other gridlines faint.
                color: function (ctx) {
                  return ctx.tick && ctx.tick.value === 0
                    ? colors.textSecondary
                    : colors.gridColor;
                }
              },
              ticks: {
                callback: function (value) {
                  return ShekelChart.formatMoney(value, false);
                }
              }
            },
            x: { grid: { display: false } }
          }
        }
      };
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initChart(document);
    });
  } else {
    initChart(document);
  }

  document.body.addEventListener("htmx:afterSwap", function (event) {
    initChart(event.target || document);
  });
})();
