/**
 * Shekel Budget App -- Account detail trend renderer.
 *
 * Renders the unified cash-account detail page's balance projection
 * chart (accounts/cash_detail.html) via the ShekelChart factory, so a
 * theme toggle re-resolves colors.  The series arrives as a JSON
 * ``data-chart`` attribute on the canvas:
 * {labels: [str], balance: [float], current_index: int}.  Floats exist
 * only at that serialization boundary (the route's chart serializer);
 * this script never computes money -- it only draws the provided points
 * and formats axis and tooltip labels.
 *
 * ``current_index`` is the current period's position in the series:
 * points before it are the anchored history (drawn solid) and the rest
 * are the forward projection (drawn dashed and lighter), with a "Today"
 * marker at the boundary -- the same grammar as the net-worth cockpit
 * trend, via the shared ShekelChart.splitSegment /
 * ShekelChart.todayMarkerPlugin helpers (chart_theme.js).
 *
 * The dropped per-period table's exact figures live here: the hover
 * tooltip shows each period's projected balance to the cent
 * (docs/design/account_detail_audit.md, decision 3 amendment).
 */

(function () {
  "use strict";

  var CANVAS_ID = "account-detail-chart-canvas";

  /**
   * Parse the canvas's ``data-chart`` JSON.
   * @param {Element} canvas - The trend canvas.
   * @returns {object|null} The series object, or null when missing /
   *   malformed / empty.
   */
  function parseData(canvas) {
    var data;
    try {
      data = JSON.parse(canvas.getAttribute("data-chart") || "{}");
    } catch (err) {
      // Malformed data-chart JSON is a server-side serialization bug, not
      // a user error: surface it to the console and bail out (a broken
      // chart must not take down the rest of the page's JS).
      console.error("Shekel: malformed account-detail data-chart JSON", err);
      return null;
    }
    if (!data.balance || !data.balance.length) return null;
    return data;
  }

  /**
   * Build the full Chart.js config.  Reads the canvas data fresh each
   * call so a theme toggle rebuilds from current data.
   * @returns {object|null} A Chart.js config, or null when no canvas/data.
   */
  function buildConfig() {
    var canvas = document.getElementById(CANVAS_ID);
    if (!canvas) return null;
    var data = parseData(canvas);
    if (!data) return null;

    var currentIndex = data.current_index || 0;
    var style = getComputedStyle(document.documentElement);
    var colors = ShekelChart.getThemeColors();
    var accent = style.getPropertyValue("--shekel-accent").trim();
    var danger = style.getPropertyValue("--shekel-danger").trim();

    // The full projection window is ~52 biweekly periods -- a visible
    // dot on every point reads as a heavy bead chain at that density,
    // so long series drop the resting dots (the index-mode tooltip
    // still hits every period; negative points stay visible as
    // warnings).  Short series keep the cockpit's dot treatment.
    var LONG_SERIES = 26;
    var isLong = data.balance.length > LONG_SERIES;

    return {
      type: "line",
      data: {
        labels: data.labels,
        datasets: [{
          label: "Balance",
          data: data.balance,
          borderColor: accent,
          borderWidth: 2,
          tension: 0,
          // A below-zero point is a real financial warning: bigger,
          // danger-colored marker (paired with the negative tooltip
          // figure, so color is never the only signal).
          pointRadius: function (ctx) {
            if (ctx.parsed && ctx.parsed.y < 0) return 4;
            return isLong ? 0 : 2.5;
          },
          pointHoverRadius: 4,
          pointBackgroundColor: function (ctx) {
            return ctx.parsed && ctx.parsed.y < 0 ? danger : accent;
          },
          segment: ShekelChart.splitSegment(
            currentIndex, accent, ShekelChart.hexToRgba(accent, 0.5)
          ),
          fill: {
            target: "origin",
            above: ShekelChart.hexToRgba(accent, 0.10),
            below: ShekelChart.hexToRgba(danger, 0.25)
          }
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
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
          x: {
            grid: { display: false },
            // Thin the ~52 period labels to a readable dozen instead of
            // rotating them all into a diagonal wall.
            ticks: { maxTicksLimit: 13, maxRotation: 0 }
          }
        }
      },
      plugins: [
        ShekelChart.todayMarkerPlugin(currentIndex, colors.textSecondary)
      ]
    };
  }

  /**
   * Create the chart when its canvas is present.  Theme re-renders are
   * handled by the ShekelChart factory; ShekelChart.create destroys any
   * prior instance registered under the same canvas id, so re-invoking
   * after a swap is safe.
   */
  function init() {
    if (typeof ShekelChart === "undefined" || typeof Chart === "undefined") {
      return;
    }
    if (!document.getElementById(CANVAS_ID)) return;
    ShekelChart.create(CANVAS_ID, buildConfig);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Re-create after HTMX replaces the band: an anchor save through the
  // hero's click-to-edit editor fires balanceChanged, and
  // #cash-band-region re-renders the whole band, canvas included (the
  // D14 port).  On afterSettle, not afterSwap, so the new canvas sits
  // in its final DOM position (the growth_chart.js precedent).
  document.addEventListener("htmx:afterSettle", function () {
    init();
  });
})();
