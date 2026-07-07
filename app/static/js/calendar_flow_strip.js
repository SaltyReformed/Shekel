/**
 * Shekel Budget App -- Analytics Calendar Flow Strip
 *
 * Renders the calendar month view's flow strip: the projected end-of-day
 * balance for every day of the month (Chart.js via the ShekelChart factory,
 * so theme toggles re-resolve colors).  The series arrives as a JSON
 * ``data-chart`` attribute on the canvas:
 * {labels: [str], values: [float], current_index: int, threshold: float,
 *  payday_indices: [int], trough_index: int|null, week_tick_indices: [int]}.
 * Floats exist only at that serialization boundary
 * (``analytics_view.serialize_flow_strip``); this script never computes money -- it only
 * splits the provided points at ``current_index`` (solid + stronger fill
 * through today, dashed + lighter fill after), styles the payday / trough
 * dots, draws the low-balance threshold line (the same second-dataset
 * treatment as the dashboard pulse chart), and formats axis / tooltip
 * labels.
 *
 * ``current_index`` is the count of measured days: points
 * ``[0, current_index)`` are on or before today (drawn solid) and the rest
 * are the forward projection (drawn dashed and lighter), with a "Today"
 * marker at the boundary -- the same semantics as the net-worth cockpit, so
 * the shared ``todayMarkerPlugin`` applies unchanged.  A wholly past month
 * is all solid (``current_index`` = day count); a wholly future month is
 * all dashed (``current_index`` = 0).
 *
 * Weekly gridlines and date ticks render only at ``week_tick_indices``
 * (the 1st plus every Sunday, matching the calendar grid's week start).
 *
 * Re-initializes after every ``htmx:afterSwap`` that contains the canvas
 * (the calendar tab is HTMX lazy-loaded and re-swapped on month
 * navigation), and on every ``shekel:theme-changed`` via the ShekelChart
 * factory re-invoking buildConfig.
 */

(function () {
  "use strict";

  var CANVAS_ID = "calendar-flow-canvas";
  var PROJECTION_DASH = [6, 5];
  var THRESHOLD_DASH = [4, 4];

  /**
   * Parse the canvas's ``data-chart`` JSON.
   * @param {Element} canvas - The flow strip canvas.
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
      // chart must not take down the rest of the analytics tab's JS).
      console.error("Shekel: malformed flow-strip data-chart JSON", err);
      return null;
    }
    if (!data.values || !data.values.length) return null;
    return data;
  }

  /**
   * Inline plugin: draw the trough dot's amount label and the threshold
   * line's caption, so the strip's two reference figures are named on the
   * chart itself (locked anatomy: "red trough dot with amount label",
   * "threshold line + label").  Pure display formatting -- both values
   * were computed server-side.
   *
   * @param {number|null} troughIndex - 0-based day index of the month
   *   trough, or null when the month has no series.
   * @param {number|null} troughValue - The trough end-of-day balance.
   * @param {number|null} threshold - The low-balance threshold, or null.
   * @param {{danger: string, credit: string}} inks - Marker text colors.
   * @returns {object} A Chart.js plugin.
   */
  function stripMarkersPlugin(troughIndex, troughValue, threshold, inks) {
    return {
      id: "calendarStripMarkers",
      afterDatasetsDraw: function (chart) {
        var ctx = chart.ctx;
        var xScale = chart.scales.x;
        var yScale = chart.scales.y;
        var area = chart.chartArea;
        if (!xScale || !yScale || !area) return;

        ctx.save();
        ctx.font = "10px 'Inter', system-ui, sans-serif";

        if (troughIndex !== null && troughIndex !== undefined &&
            troughValue !== null && troughValue !== undefined) {
          const x = Math.min(
            Math.max(xScale.getPixelForValue(troughIndex), area.left + 24),
            area.right - 24
          );
          const yDot = yScale.getPixelForValue(troughValue);
          // Below the dot when room remains; above it near the floor.
          const below = yDot + 16 <= area.bottom;
          ctx.fillStyle = inks.danger;
          ctx.textAlign = "center";
          ctx.textBaseline = below ? "top" : "bottom";
          ctx.fillText(
            ShekelChart.formatMoney(troughValue, false),
            x, below ? yDot + 6 : yDot - 6
          );
        }

        if (threshold !== null && threshold !== undefined) {
          const yLine = yScale.getPixelForValue(threshold);
          if (yLine >= area.top && yLine <= area.bottom) {
            ctx.fillStyle = inks.credit;
            ctx.textAlign = "right";
            ctx.textBaseline = "bottom";
            ctx.fillText(
              "low balance " + ShekelChart.formatMoney(threshold, false),
              area.right - 2, yLine - 2
            );
          }
        }
        ctx.restore();
      }
    };
  }

  /**
   * Build the full Chart.js config from the canvas's current data.  Reads
   * everything fresh each call so a month-navigation swap and a theme
   * toggle both rebuild from current data with re-resolved colors.
   * @returns {object} A Chart.js config.
   */
  function buildConfig() {
    var canvas = document.getElementById(CANVAS_ID);
    var data = parseData(canvas);
    var style = getComputedStyle(document.documentElement);
    var accent = style.getPropertyValue("--shekel-accent").trim();
    var danger = style.getPropertyValue("--shekel-danger").trim();
    var done = style.getPropertyValue("--shekel-done").trim();
    var credit = style.getPropertyValue("--shekel-credit").trim();
    var surface = style.getPropertyValue("--shekel-surface").trim();
    var colors = ShekelChart.getThemeColors();

    var len = data.values.length;
    var boundary = data.current_index - 1;
    var paydaySet = new Set(data.payday_indices || []);
    var weekSet = new Set(data.week_tick_indices || []);
    var troughValue = (
      data.trough_index !== null && data.trough_index !== undefined
    ) ? data.values[data.trough_index] : null;

    // Split one series into a measured span (through today) and a
    // projected span (today onward, sharing the boundary point so the
    // line stays continuous). Pure array slicing on server-computed
    // numbers -- no monetary computation.
    var measured = data.values.map(function (v, i) {
      return i < data.current_index ? v : null;
    });
    var projected = data.values.map(function (v, i) {
      return data.current_index < len && i >= boundary ? v : null;
    });

    /**
     * Scriptable point radius: payday and trough dots only; the
     * projected dataset skips the shared boundary point (the measured
     * dataset draws it).
     * @param {boolean} isProjected - Which dataset the point is on.
     * @returns {function(object): number} Chart.js scriptable option.
     */
    function dotRadius(isProjected) {
      return function (ctx) {
        const i = ctx.dataIndex;
        if (isProjected && i === boundary) return 0;
        if (i === data.trough_index) return 4.5;
        if (paydaySet.has(i)) return 3.5;
        return 0;
      };
    }

    /**
     * Scriptable point color: danger for the trough dot, done (income
     * green) for payday dots, accent otherwise (hover crosshair dots).
     * @param {object} ctx - Chart.js scriptable context.
     * @returns {string} Point fill color.
     */
    function dotColor(ctx) {
      const i = ctx.dataIndex;
      if (i === data.trough_index) return danger;
      if (paydaySet.has(i)) return done;
      return accent;
    }

    var datasets = [
      {
        data: measured,
        borderColor: accent,
        borderWidth: 2,
        tension: 0,
        spanGaps: false,
        pointRadius: dotRadius(false),
        pointBackgroundColor: dotColor,
        pointBorderColor: surface,
        pointBorderWidth: 1.5,
        fill: {
          target: "origin",
          above: ShekelChart.hexToRgba(accent, 0.14),
          below: ShekelChart.hexToRgba(danger, 0.28)
        }
      },
      {
        data: projected,
        borderColor: ShekelChart.hexToRgba(accent, 0.55),
        borderDash: PROJECTION_DASH,
        borderWidth: 2,
        tension: 0,
        spanGaps: false,
        pointRadius: dotRadius(true),
        pointBackgroundColor: dotColor,
        pointBorderColor: surface,
        pointBorderWidth: 1.5,
        fill: {
          target: "origin",
          above: ShekelChart.hexToRgba(accent, 0.06),
          below: ShekelChart.hexToRgba(danger, 0.12)
        }
      }
    ];

    if (data.threshold !== null && data.threshold !== undefined) {
      // Same treatment as the dashboard pulse chart's threshold line:
      // a flat credit-colored dashed dataset, filtered out of tooltips.
      datasets.push({
        data: data.labels.map(function () { return data.threshold; }),
        borderColor: credit,
        borderDash: THRESHOLD_DASH,
        borderWidth: 1,
        pointRadius: 0,
        pointHoverRadius: 0,
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
          legend: { display: false },
          tooltip: {
            filter: function (item) {
              if (item.datasetIndex >= 2) return false;
              if (item.parsed.y === null) return false;
              // The boundary day exists on both spans; keep the measured
              // copy only.
              return !(item.datasetIndex === 1 && item.dataIndex === boundary);
            },
            callbacks: {
              label: function (ctx) {
                const suffix =
                  ctx.dataIndex >= data.current_index ? " projected" : "";
                return ShekelChart.formatMoney(ctx.parsed.y, true) + suffix;
              }
            }
          }
        },
        scales: {
          x: {
            grid: {
              // Weekly gridlines only (the 1st + Sundays).
              color: function (ctx) {
                return weekSet.has(ctx.index)
                  ? colors.gridColor
                  : "transparent";
              }
            },
            ticks: {
              autoSkip: false,
              maxRotation: 0,
              callback: function (value, index) {
                return weekSet.has(index)
                  ? this.getLabelForValue(value)
                  : "";
              }
            }
          },
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
          }
        }
      },
      plugins: [
        ShekelChart.todayMarkerPlugin(data.current_index, colors.textSecondary),
        stripMarkersPlugin(
          data.trough_index, troughValue, data.threshold,
          { danger: danger, credit: credit }
        )
      ]
    };
  }

  /**
   * Initialize (or re-initialize after a swap) the strip for a subtree.
   * Validates the canvas and its data before handing the factory to
   * ShekelChart.create (the factory itself assumes both -- they cannot
   * change between a create and a theme re-render).
   * @param {Element|Document} root - Subtree that may contain the canvas.
   */
  function initChart(root) {
    var scope = root && root.querySelector ? root : document;
    var canvas = scope.querySelector("#" + CANVAS_ID) ||
      document.getElementById(CANVAS_ID);
    if (!canvas || typeof ShekelChart === "undefined" ||
        typeof Chart === "undefined") {
      return;
    }
    if (!parseData(canvas)) return;
    ShekelChart.create(CANVAS_ID, buildConfig);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initChart(document);
    });
  } else {
    initChart(document);
  }

  document.body.addEventListener("htmx:afterSwap", function (event) {
    // Only rebuild when the swapped content holds the strip canvas (a
    // calendar tab load or month navigation); an unrelated htmx swap
    // elsewhere on the page must not churn the chart.
    var target = event.target;
    if (!target || !target.querySelector) return;
    if (target.querySelector("#" + CANVAS_ID) || target.id === CANVAS_ID) {
      initChart(target);
    }
  });
})();
