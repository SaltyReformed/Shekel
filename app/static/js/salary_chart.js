/**
 * Shekel Budget App -- Salary cockpit chart renderers.
 *
 * Draws the two salary/cockpit.html charts via the ShekelChart factory
 * (chart_theme.js), so a theme toggle re-resolves every color:
 *
 * 1. The net-pay staircase (#salary-net-chart): a stepped line of
 *    REGULAR-paycheck net so raise steps read instantly, with third
 *    paychecks drawn as ringed point events on thin stems OFF the line
 *    (the locked D2 direction, docs/design/salary_audit.md).  History is
 *    solid, the projection dashed, with a Today marker at the boundary --
 *    the same grammar as the dashboard and account-detail trends.
 * 2. The salary-path sparkline (#salary-path-chart): the forward
 *    annual-salary staircase inside the "Salary path" card.
 *
 * Each canvas carries its series as a JSON ``data-chart`` attribute:
 * the staircase gets {periods: [{start, net}], thirds: [{start, net}],
 * raises: [{start, label}], today}, the sparkline {points: [{start,
 * annual}], end_label}.  Floats exist only at that serialization
 * boundary (the route's _chart_jsonable); this script never computes
 * money -- it only draws the provided points and formats labels.
 */

(function () {
  "use strict";

  var NET_CANVAS_ID = "salary-net-chart";
  var PATH_CANVAS_ID = "salary-path-chart";

  var MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
  ];

  /**
   * Format an ISO date string (YYYY-MM-DD) as a compact axis label,
   * e.g. "Jul 2 '26".  String slicing only -- no Date parsing, so no
   * timezone drift on date-only values.
   * @param {string} iso - ISO date string.
   * @returns {string} The formatted label.
   */
  function fmtLabel(iso) {
    var month = MONTHS[parseInt(iso.slice(5, 7), 10) - 1];
    var day = parseInt(iso.slice(8, 10), 10);
    return month + " " + day + " '" + iso.slice(2, 4);
  }

  /**
   * Parse a canvas's ``data-chart`` JSON.
   * @param {Element} canvas - The chart canvas.
   * @returns {object|null} The series object, or null when missing or
   *   malformed.
   */
  function parseData(canvas) {
    try {
      return JSON.parse(canvas.getAttribute("data-chart") || "null");
    } catch (err) {
      // Malformed data-chart JSON is a server-side serialization bug,
      // not a user error: log it and bail without breaking page JS.
      console.error("Shekel: malformed salary data-chart JSON", err);
      return null;
    }
  }

  /**
   * Index of the period containing "today": the last period whose start
   * is on or before it.  ISO date strings compare lexicographically in
   * chronological order, so no Date objects are needed.
   * @param {Array<{start: string}>} periods - The period series.
   * @param {string} today - ISO date string.
   * @returns {number} The current period's index (0 when today precedes
   *   every period).
   */
  function currentIndexOf(periods, today) {
    var idx = 0;
    periods.forEach(function (period, i) {
      if (period.start <= today) idx = i;
    });
    return idx;
  }

  /**
   * Inline plugin: draw the third-paycheck stems (base line up to the
   * event dot) and the raise step labels.  Both are annotations of the
   * staircase dataset (index 0) and the thirds dataset (index 1).
   * @param {object} data - The parsed chart series.
   * @param {object} colors - ShekelChart.getThemeColors() output.
   * @returns {object} A Chart.js plugin.
   */
  function annotationsPlugin(data, colors) {
    var style = getComputedStyle(document.documentElement);
    var accent = style.getPropertyValue("--shekel-accent").trim();
    return {
      id: "shekelSalaryAnnotations",
      afterDatasetsDraw: function (chart) {
        var lineMeta = chart.getDatasetMeta(0);
        var dotMeta = chart.getDatasetMeta(1);
        if (!lineMeta || !lineMeta.data || !lineMeta.data.length) return;
        var ctx = chart.ctx;
        var firstIdx = data.thirds.length
          ? indexOfStart(data.periods, data.thirds[0].start)
          : -1;
        var firstPt = null;
        var atRightEdge = false;
        ctx.save();

        // Stems: from the staircase value up to the third-paycheck dot.
        // Accent-tinted so they read as part of the series, not the grid.
        ctx.strokeStyle = ShekelChart.hexToRgba(accent, 0.45);
        ctx.lineWidth = 1;
        data.thirds.forEach(function (third) {
          var idx = indexOfStart(data.periods, third.start);
          if (idx < 0 || !dotMeta || !dotMeta.data[idx]) return;
          var basePt = lineMeta.data[idx];
          var dotPt = dotMeta.data[idx];
          ctx.beginPath();
          ctx.moveTo(basePt.x, basePt.y - 2);
          ctx.lineTo(dotPt.x, dotPt.y + 6);
          ctx.stroke();
        });

        // One selective label on the first third-paycheck dot; the rest
        // stay tooltip-only (label-every-point is noise).
        if (firstIdx >= 0 && dotMeta && dotMeta.data[firstIdx]) {
          firstPt = dotMeta.data[firstIdx];
          ctx.fillStyle = colors.textSecondary;
          ctx.font = "11px 'Inter', system-ui, sans-serif";
          ctx.textBaseline = "middle";
          atRightEdge = firstPt.x > chart.chartArea.right - 120;
          ctx.textAlign = atRightEdge ? "right" : "left";
          ctx.fillText(
            "3rd paycheck", firstPt.x + (atRightEdge ? -9 : 9), firstPt.y
          );
        }

        // Raise step labels, just above the new plateau.
        ctx.fillStyle = colors.textSecondary;
        ctx.font = "11px 'Inter', system-ui, sans-serif";
        ctx.textBaseline = "bottom";
        data.raises.forEach(function (event) {
          var rIdx = indexOfStart(data.periods, event.start);
          if (rIdx < 0 || !lineMeta.data[rIdx]) return;
          var rPt = lineMeta.data[rIdx];
          var flip = rPt.x > chart.chartArea.right - 90;
          ctx.textAlign = flip ? "right" : "left";
          ctx.fillText(event.label, rPt.x + (flip ? -4 : 4), rPt.y - 6);
        });
        ctx.restore();
      }
    };
  }

  /**
   * Find a period's index by its ISO start date.
   * @param {Array<{start: string}>} periods - The period series.
   * @param {string} start - ISO date string to find.
   * @returns {number} The index, or -1.
   */
  function indexOfStart(periods, start) {
    var found = -1;
    periods.forEach(function (period, i) {
      if (found < 0 && period.start === start) found = i;
    });
    return found;
  }

  /**
   * Build the net-pay staircase config.  Reads the canvas data fresh on
   * each call so a theme toggle rebuilds with current colors.
   * @returns {object|null} A Chart.js config, or null when no canvas/data.
   */
  function buildNetConfig() {
    var canvas = document.getElementById(NET_CANVAS_ID);
    if (!canvas) return null;
    var data = parseData(canvas);
    if (!data || !data.periods || !data.periods.length) return null;

    var style = getComputedStyle(document.documentElement);
    var colors = ShekelChart.getThemeColors();
    var accent = style.getPropertyValue("--shekel-accent").trim();
    var surface = style.getPropertyValue("--shekel-surface").trim();
    var currentIndex = currentIndexOf(data.periods, data.today);

    var labels = data.periods.map(function (p) { return fmtLabel(p.start); });
    var nets = data.periods.map(function (p) { return p.net; });
    // Thirds as a sparse overlay series: null except at event indices,
    // so the dots share the staircase's category axis.
    var thirds = data.periods.map(function () { return null; });
    data.thirds.forEach(function (t) {
      var idx = indexOfStart(data.periods, t.start);
      if (idx >= 0) thirds[idx] = t.net;
    });

    return {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Regular net",
            data: nets,
            stepped: "after",
            borderColor: accent,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointBackgroundColor: accent,
            segment: ShekelChart.splitSegment(
              currentIndex, accent, ShekelChart.hexToRgba(accent, 0.5)
            ),
            fill: {
              target: "start",
              above: ShekelChart.hexToRgba(accent, 0.08)
            }
          },
          {
            label: "3rd paycheck",
            data: thirds,
            showLine: false,
            pointRadius: 4.5,
            pointHoverRadius: 6,
            pointBackgroundColor: accent,
            // 2px surface ring keeps the dot legible over the stem.
            pointBorderColor: surface,
            pointBorderWidth: 2
          }
        ]
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
                if (ctx.parsed.y === null) return null;
                return ctx.dataset.label + ": " +
                  ShekelChart.formatMoney(ctx.parsed.y, true);
              }
            }
          }
        },
        scales: {
          y: {
            grid: { color: colors.gridColor },
            ticks: {
              maxTicksLimit: 6,
              callback: function (value) {
                return ShekelChart.formatMoney(value, false);
              }
            }
          },
          x: {
            grid: { display: false },
            ticks: { maxTicksLimit: 9, maxRotation: 0 }
          }
        }
      },
      plugins: [
        ShekelChart.todayMarkerPlugin(currentIndex, colors.textSecondary),
        annotationsPlugin(data, colors)
      ]
    };
  }

  /**
   * Build the salary-path sparkline config (axes hidden; the card's
   * end-label carries the destination figure, the tooltip the rest).
   * @returns {object|null} A Chart.js config, or null when no canvas/data.
   */
  function buildPathConfig() {
    var canvas = document.getElementById(PATH_CANVAS_ID);
    if (!canvas) return null;
    var data = parseData(canvas);
    if (!data || !data.points || !data.points.length) return null;

    var style = getComputedStyle(document.documentElement);
    var accent = style.getPropertyValue("--shekel-accent").trim();
    var surface = style.getPropertyValue("--shekel-surface").trim();
    var lastIndex = data.points.length - 1;

    return {
      type: "line",
      data: {
        labels: data.points.map(function (p) { return fmtLabel(p.start); }),
        datasets: [{
          label: "Annual salary",
          data: data.points.map(function (p) { return p.annual; }),
          stepped: "after",
          borderColor: accent,
          borderWidth: 2,
          pointRadius: function (ctx) {
            return ctx.dataIndex === lastIndex ? 3 : 0;
          },
          pointHoverRadius: 4,
          pointBackgroundColor: accent,
          pointBorderColor: surface,
          pointBorderWidth: 2
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
                return ShekelChart.formatMoney(ctx.parsed.y, false);
              }
            }
          }
        },
        // A sparkline: the trend is the message, the tooltip the values.
        scales: {
          y: { display: false },
          x: { display: false }
        }
      }
    };
  }

  /**
   * Create both charts when their canvases are present (page-load only:
   * the anatomy HTMX swaps never replace the canvases; theme re-renders
   * are handled by the ShekelChart factory).
   */
  function init() {
    if (typeof ShekelChart === "undefined" || typeof Chart === "undefined") {
      return;
    }
    if (document.getElementById(NET_CANVAS_ID)) {
      ShekelChart.create(NET_CANVAS_ID, buildNetConfig);
    }
    if (document.getElementById(PATH_CANVAS_ID)) {
      ShekelChart.create(PATH_CANVAS_ID, buildPathConfig);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
