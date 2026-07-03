/**
 * Shekel Budget App -- Retirement Flight-Path Chart (direction-D rebuild)
 *
 * Renders the readiness card's savings flight path: "your path" (the
 * after-tax projected portfolio, accent line with a ~10% area wash)
 * versus "needed to retire <date>" (the reverse-projected requirement,
 * muted gray).  Series arrive as JSON in the canvas's ``data-chart``
 * attribute (string Decimals from the readiness producer -- floats
 * exist only at this serialization boundary; this script never
 * computes money, it only formats axis/tooltip labels).
 *
 * Built through the ShekelChart factory API so dataset colors
 * re-resolve on theme toggle, and re-initialized after every
 * ``htmx:afterSettle`` so the what-if readiness re-render rebuilds the
 * chart.  afterSettle, NOT afterSwap: htmx's settle phase (~20ms after
 * the swap) restores the id-matched canvas's original attributes, and
 * the incoming canvas has no ``width``/``height`` attributes -- so a
 * chart initialized at afterSwap had the very attributes Chart.js just
 * wrote stripped mid-animation, collapsing the bitmap to a default
 * 300x150 slice of the wrapper (the acceptance-drive "partial chart /
 * only a section" defect; probed live: hasAttribute(width/height)
 * flipped to false after settle, nondeterministically per race).
 * Initializing after settle means htmx has finished touching
 * attributes before Chart.js sizes the canvas.  The wrapper div's
 * CSS height + ``maintainAspectRatio: false`` keep the canvas usable on
 * mobile (the old gap chart's fixed aspectRatio 8 collapsed to ~49px
 * at phone widths -- audit finding V1).
 */

(function () {
  "use strict";

  var CANVAS_ID = "retirement-path-canvas";

  /**
   * Build (or rebuild) the flight-path chart from the canvas's
   * ``data-chart`` JSON: {your_path: [], needed_path: [], dates: [],
   * needed_label: string}.
   * @param {Element|Document} root - Subtree containing the canvas.
   */
  function initChart(root) {
    var scope = root && root.querySelector ? root : document;
    var canvas = scope.querySelector("#" + CANVAS_ID) ||
      (scope.id === CANVAS_ID ? scope : null);
    if (!canvas || typeof ShekelChart === "undefined" ||
        typeof Chart === "undefined") {
      return;
    }

    var data;
    try {
      data = JSON.parse(canvas.getAttribute("data-chart") || "{}");
    } catch (err) {
      // Malformed data-chart JSON is a server-side serialization bug,
      // not a user error: surface it, then bail without taking down the
      // rest of the page's JS.
      console.error("Shekel: malformed retirement data-chart JSON", err);
      return;
    }
    if (!data.dates || !data.dates.length) return;

    // Float conversion happens only here, at the Chart.js boundary.
    var yourPath = (data.your_path || []).map(Number);
    var neededPath = (data.needed_path || []).map(Number);
    var dates = data.dates;
    var neededLabel = data.needed_label || "Needed";

    ShekelChart.create(CANVAS_ID, function () {
      var style = getComputedStyle(document.documentElement);
      var accent = style.getPropertyValue("--shekel-accent").trim();
      var muted = style.getPropertyValue("--shekel-text-muted").trim();
      var surface = style.getPropertyValue("--shekel-surface").trim();
      var lastIndex = dates.length - 1;

      /**
       * End-dot treatment per the mark specs: an 8px dot (r4) with a
       * 2px surface ring on the final point only; interior points stay
       * bare so the 2px line carries the series.
       * @param {string} color - The series color for the end dot.
       * @returns {object} Point options for one dataset.
       */
      function endDot(color) {
        return {
          pointRadius: function (ctx) {
            return ctx.dataIndex === lastIndex ? 4 : 0;
          },
          pointHoverRadius: 5,
          pointBackgroundColor: color,
          pointBorderColor: surface,
          pointBorderWidth: function (ctx) {
            return ctx.dataIndex === lastIndex ? 2 : 0;
          }
        };
      }

      var datasets = [
        Object.assign({
          label: "Your path",
          data: yourPath,
          borderColor: accent,
          borderWidth: 2,
          tension: 0,
          fill: {
            target: "origin",
            above: ShekelChart.hexToRgba(accent, 0.10)
          }
        }, endDot(accent)),
        Object.assign({
          label: neededLabel,
          data: neededPath,
          borderColor: muted,
          borderWidth: 2,
          tension: 0,
          fill: false
        }, endDot(muted))
      ];

      return {
        type: "line",
        data: { labels: dates, datasets: datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          plugins: {
            legend: {
              display: true,
              position: "top",
              align: "start",
              labels: { boxWidth: 14, boxHeight: 3 }
            },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  return ctx.dataset.label + ": " +
                    ShekelChart.formatMoney(ctx.parsed.y, false);
                }
              }
            }
          },
          scales: {
            y: {
              beginAtZero: true,
              ticks: {
                callback: function (value) {
                  return ShekelChart.formatMoney(value, false);
                }
              }
            },
            x: {
              grid: { display: false },
              ticks: {
                autoSkip: true,
                maxTicksLimit: 6,
                maxRotation: 0,
                callback: function (_value, index) {
                  // ISO dates -> year labels (display only).
                  return String(dates[index]).slice(0, 4);
                }
              }
            }
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

  document.body.addEventListener("htmx:afterSettle", function (event) {
    initChart(event.target || document);
  });
})();
