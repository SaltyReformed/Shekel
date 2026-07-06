'use strict';

/**
 * Shekel Budget App -- Loan detail band chart + payoff-lever overlay.
 *
 * Renders the loan detail band's balance-over-time chart (loan/dashboard.html)
 * via the ShekelChart factory, so a theme toggle re-resolves colors.  The
 * series arrives as a JSON ``data-chart`` attribute on the canvas:
 * {labels: [str], balance: [float], current_index: int}.  Floats exist only at
 * that serialization boundary (the route's chart serializer); this script never
 * computes money -- it only draws the provided points and formats axis / tooltip
 * labels.
 *
 * ``current_index`` is the confirmed / projected boundary: points before it are
 * the ledger-confirmed history (solid) and the rest are the committed
 * projection (dashed, lighter), with a "Today" marker at the boundary -- the
 * same grammar as the account-detail trend, via ShekelChart.splitSegment /
 * ShekelChart.todayMarkerPlugin (chart_theme.js).
 *
 * When the "pay off sooner" lever runs, its HTMX result carries the accelerated
 * forward balances in ``#payoff-overlay-data[data-overlay]`` (leading nulls over
 * confirmed history); this script overlays them as the green dashed preview and
 * redraws.  The overlay is held in a module variable so the config factory
 * re-reads it on every rebuild (a theme toggle rebuilds via the factory and must
 * preserve the active preview).  A target-date result carries no overlay, so it
 * clears the preview.
 *
 * Alignment note: the overlay (recomputed per payoff POST) is plotted against
 * the band's labels, which are frozen from the page-load GET.  Both derive the
 * same contractual x-axis from the loan's params + confirmed history, so within
 * one session they align exactly.  In the rare case the confirmed-history length
 * shifts between the GET and a later POST (a payment settles in another tab, or
 * date.today() rolls past midnight), the preview can land one x-position off the
 * committed line until reload; the metric chips (new payoff, months / interest
 * saved) come from the POST scenario and stay correct regardless.
 */

(function () {
  "use strict";

  var CANVAS_ID = "loan-balance-chart";
  var RESULTS_ID = "payoff-results";
  var OVERLAY_DATA_ID = "payoff-overlay-data";

  // The active extra-payment preview (forward-only accelerated balances), or
  // null when no preview is shown.
  var overlaySeries = null;

  /**
   * Parse the canvas's ``data-chart`` JSON.
   * @param {Element} canvas - The band chart canvas.
   * @returns {object|null} The series object, or null when missing / malformed
   *   / empty.
   */
  function parseData(canvas) {
    var data;
    try {
      data = JSON.parse(canvas.getAttribute("data-chart") || "{}");
    } catch (err) {
      // Malformed data-chart JSON is a server-side serialization bug, not a
      // user error: surface it and bail so a broken chart cannot take down the
      // rest of the page's JS.
      console.error("Shekel: malformed loan band data-chart JSON", err);
      return null;
    }
    if (!data.balance || !data.balance.length) return null;
    return data;
  }

  /**
   * Build the full Chart.js config.  Reads the canvas data and the module
   * overlay fresh each call so a theme toggle rebuilds from current state.
   * @returns {object|null} A Chart.js config, or null when no canvas / data.
   */
  function buildConfig() {
    var canvas = document.getElementById(CANVAS_ID);
    if (!canvas) return null;
    var data = parseData(canvas);
    if (!data) return null;

    var currentIndex = data.current_index || 0;
    var colors = ShekelChart.getThemeColors();
    var style = getComputedStyle(document.documentElement);
    var accent = style.getPropertyValue("--shekel-accent").trim();
    var done = style.getPropertyValue("--shekel-done").trim();

    // A visible dot on every point reads as a heavy bead chain across a
    // multi-year loan, so long series drop the resting dots (the index-mode
    // tooltip still hits every period).
    var LONG_SERIES = 26;
    var isLong = data.balance.length > LONG_SERIES;

    var datasets = [{
      label: "Balance",
      data: data.balance,
      borderColor: accent,
      borderWidth: 2,
      tension: 0,
      pointRadius: isLong ? 0 : 2,
      pointHoverRadius: 4,
      segment: ShekelChart.splitSegment(
        currentIndex, accent, ShekelChart.hexToRgba(accent, 0.5)
      ),
      fill: {
        target: "origin",
        above: ShekelChart.hexToRgba(accent, 0.08)
      }
    }];

    if (overlaySeries) {
      datasets.push({
        label: "Pay off sooner",
        data: overlaySeries,
        borderColor: done,
        borderWidth: 2,
        borderDash: [6, 5],
        tension: 0,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: false,
        fill: false
      });
    }

    return {
      type: "line",
      data: { labels: data.labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          // The legend only earns its space when the preview overlay names a
          // second series; a lone balance line is self-evident.
          legend: { display: Boolean(overlaySeries) },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                if (ctx.parsed.y === null || ctx.parsed.y === undefined) {
                  return null;
                }
                return ctx.dataset.label + ": " +
                  ShekelChart.formatMoney(ctx.parsed.y, false);
              }
            }
          }
        },
        scales: {
          y: {
            grid: {
              // Emphasize the zero line (payoff); keep other gridlines faint.
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
   * (Re)create the band chart when its canvas is present.  ShekelChart.create
   * destroys any prior instance on the same canvas, so this is safe to call on
   * every overlay change.
   */
  function render() {
    if (typeof ShekelChart === "undefined" || typeof Chart === "undefined") {
      return;
    }
    if (!document.getElementById(CANVAS_ID)) return;
    ShekelChart.create(CANVAS_ID, buildConfig);
  }

  /**
   * When the payoff lever swaps in a result, pick up (or clear) the accelerated
   * overlay and redraw so the green preview reflects the latest run.  Ignores
   * the escrow / rate HTMX swaps (different targets).
   * @param {Event} evt - The htmx:afterSwap event.
   */
  function onAfterSwap(evt) {
    if (!evt.detail || !evt.detail.target ||
        evt.detail.target.id !== RESULTS_ID) {
      return;
    }
    var carrier = document.getElementById(OVERLAY_DATA_ID);
    if (carrier) {
      try {
        overlaySeries = JSON.parse(
          carrier.getAttribute("data-overlay") || "null"
        );
      } catch (err) {
        console.error("Shekel: malformed payoff overlay JSON", err);
        overlaySeries = null;
      }
    } else {
      // A target-date result (or an error) carries no overlay: clear any
      // previous preview.
      overlaySeries = null;
    }
    render();
  }

  function init() {
    render();
    document.body.addEventListener("htmx:afterSwap", onAfterSwap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
