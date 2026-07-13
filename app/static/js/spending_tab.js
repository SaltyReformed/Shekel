/**
 * Shekel Budget App -- Analytics Spending Month Chart
 *
 * Renders the Spending tab's trailing-12 emphasis month chart (the S14
 * "months lead" cockpit, D7): settled spending per calendar month as bars,
 * the viewed month in accent and every other month muted, a dashed 6-month
 * average reference line with a text label, baseline ticks for months with
 * no settled rows (never a $0 bar), a "settled history begins ..." note
 * over a leading empty run, and value labels on only the viewed month and
 * its comparison month.  Bars double as navigation: clicking one loads
 * that month's Spending tab through htmx.
 *
 * The series arrives as a JSON ``data-chart`` attribute on the canvas
 * (``analytics_view.serialize_spending_chart``):
 * {labels: [str], values: [float|null], nav: [{year, month}|null],
 *  viewed_index: int, compare_index: int, avg: float|null,
 *  history_note: str|null}.
 * Floats exist only at that serialization boundary; this script never
 * computes money -- the average line and every label are server-computed
 * figures it merely draws.
 *
 * Re-initializes after every ``htmx:afterSwap`` that contains the canvas
 * (the tab is HTMX lazy-loaded and re-swapped on month navigation), and on
 * every ``shekel:theme-changed`` via the ShekelChart factory re-invoking
 * buildConfig.
 */

(function () {
  "use strict";

  var CANVAS_ID = "spending-months-canvas";
  var AVG_DASH = [5, 4];
  var MUTED_BAR_ALPHA = 0.42;

  /**
   * Parse the canvas's ``data-chart`` JSON.
   * @param {Element} canvas - The month chart canvas.
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
      console.error("Shekel: malformed spending data-chart JSON", err);
      return null;
    }
    if (!data.labels || !data.labels.length) return null;
    return data;
  }

  /**
   * True when a month draws no bar: no settled rows (value 0) or no pay
   * periods at all (value null).  Both render as a baseline tick.
   * @param {number|null} value - The month's settled total.
   * @returns {boolean} Whether the month is empty.
   */
  function isEmptyMonth(value) {
    return value === null || value === undefined || value === 0;
  }

  /**
   * Inline plugin: draw the chart's annotation layer -- empty-month
   * baseline ticks, the settled-history note, the 6-mo average label, and
   * the two selective value labels (viewed + comparison month).  Pure
   * display drawing of server-computed figures.
   *
   * @param {object} data - The parsed ``data-chart`` series.
   * @param {object} inks - Resolved theme colors: {tick, note, valViewed,
   *   valCompare}.
   * @returns {object} A Chart.js plugin.
   */
  function spendingMarkersPlugin(data, inks) {
    /**
     * Draw one bar's value label just above its top.
     * @param {object} chart - The chart instance.
     * @param {number} index - The bar index to label.
     * @param {string} color - Label ink.
     * @param {string} font - CSS font shorthand.
     */
    function drawValueLabel(chart, index, color, font) {
      var value = data.values[index];
      if (isEmptyMonth(value)) return;
      var area = chart.chartArea;
      var yTop = chart.scales.y.getPixelForValue(value);
      var ctx = chart.ctx;
      ctx.fillStyle = color;
      ctx.font = font;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      var text = ShekelChart.formatMoney(value, false);
      // Clamp so an edge bar's label (the viewed month is the LAST bar)
      // stays inside the chart area instead of clipping.
      var half = ctx.measureText(text).width / 2;
      var x = Math.min(
        Math.max(chart.scales.x.getPixelForValue(index), area.left + half),
        area.right - half
      );
      ctx.fillText(text, x, Math.max(yTop - 5, area.top + 12));
    }

    return {
      id: "spendingChartMarkers",
      afterDatasetsDraw: function (chart) {
        var xScale = chart.scales.x;
        var yScale = chart.scales.y;
        var area = chart.chartArea;
        if (!xScale || !yScale || !area) return;
        var ctx = chart.ctx;
        var baselineY = yScale.getPixelForValue(0);

        ctx.save();

        // Empty months: a short baseline tick, never a $0 bar.
        ctx.strokeStyle = inks.tick;
        ctx.lineWidth = 2;
        for (let i = 0; i < data.values.length; i++) {
          if (!isEmptyMonth(data.values[i])) continue;
          const x = xScale.getPixelForValue(i);
          ctx.beginPath();
          ctx.moveTo(x - 10, baselineY - 2);
          ctx.lineTo(x + 10, baselineY - 2);
          ctx.stroke();
        }

        // "settled history begins ..." centered over the leading empty run.
        if (data.history_note) {
          let runEnd = -1;
          for (let i = 0; i < data.values.length; i++) {
            if (!isEmptyMonth(data.values[i])) break;
            runEnd = i;
          }
          if (runEnd >= 0) {
            const mid = (xScale.getPixelForValue(0) +
              xScale.getPixelForValue(runEnd)) / 2;
            ctx.fillStyle = inks.note;
            ctx.font = "italic 11.5px 'Inter', system-ui, sans-serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "bottom";
            ctx.fillText(data.history_note, mid, baselineY - 12);
          }
        }

        // The 6-mo average line's label (the line itself is a dataset).
        if (data.avg !== null && data.avg !== undefined) {
          const yAvg = yScale.getPixelForValue(data.avg);
          if (yAvg >= area.top && yAvg <= area.bottom) {
            ctx.fillStyle = inks.note;
            ctx.font = "11px 'Inter', system-ui, sans-serif";
            ctx.textAlign = "left";
            ctx.textBaseline = "bottom";
            ctx.fillText(
              "6-mo avg " + ShekelChart.formatMoney(data.avg, false),
              area.left + 4, yAvg - 3
            );
          }
        }

        // Selective value labels: the viewed month and its comparison
        // month only (D7) -- viewed in number ink, comparison dimmed.
        drawValueLabel(
          chart, data.compare_index, inks.valCompare,
          "400 11.5px 'JetBrains Mono', monospace"
        );
        drawValueLabel(
          chart, data.viewed_index, inks.valViewed,
          "500 12.5px 'JetBrains Mono', monospace"
        );

        ctx.restore();
      }
    };
  }

  /**
   * Whether a chart element under the pointer is a navigable bar (a real
   * month other than the one already shown).
   * @param {object} data - The parsed series.
   * @param {Array} elements - Chart.js active elements.
   * @returns {boolean} True when clicking would navigate.
   */
  function isNavigable(data, elements) {
    if (!elements.length) return false;
    var el = elements[0];
    return el.datasetIndex === 0 &&
      el.index !== data.viewed_index &&
      Boolean(data.nav[el.index]);
  }

  /**
   * Build the click handler that loads the clicked month's tab via htmx.
   * The request mirrors the month-picker buttons: same target, same
   * indicator (via the canvas's hx-indicator attribute), no URL push.
   * @param {object} data - The parsed series.
   * @returns {function} A Chart.js onClick handler.
   */
  function handleBarClick(data) {
    return function (_event, elements, chart) {
      if (!isNavigable(data, elements)) return;
      var target = data.nav[elements[0].index];
      var base = chart.canvas.getAttribute("data-nav-url");
      if (!base || typeof htmx === "undefined") return;
      htmx.ajax(
        "GET",
        base + "?year=" + target.year + "&month=" + target.month,
        { source: chart.canvas, target: "#tab-content", swap: "innerHTML" }
      );
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
    var muted = style.getPropertyValue("--shekel-text-muted").trim();
    var borderStrong = style.getPropertyValue("--shekel-border-strong").trim();
    var numberInk = style.getPropertyValue("--shekel-number-ink").trim();
    var colors = ShekelChart.getThemeColors();
    var mutedFill = ShekelChart.hexToRgba(muted, MUTED_BAR_ALPHA);

    var datasets = [{
      type: "bar",
      data: data.values,
      backgroundColor: function (ctx) {
        return ctx.dataIndex === data.viewed_index ? accent : mutedFill;
      },
      borderRadius: 4,
      borderSkipped: "bottom",
      maxBarThickness: 34,
      categoryPercentage: 0.8,
      barPercentage: 0.85
    }];

    if (data.avg !== null && data.avg !== undefined) {
      // The 6-mo average reference: a flat dashed muted line (the same
      // second-dataset treatment as the flow strip's threshold line),
      // filtered out of tooltips.
      datasets.push({
        type: "line",
        data: data.labels.map(function () { return data.avg; }),
        borderColor: muted,
        borderDash: AVG_DASH,
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 0,
        fill: false
      });
    }

    return {
      type: "bar",
      data: { labels: data.labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: true },
        onClick: handleBarClick(data),
        onHover: function (event, elements) {
          var el = event.native ? event.native.target : null;
          if (!el) return;
          el.style.cursor =
            isNavigable(data, elements) ? "pointer" : "default";
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            filter: function (item) {
              return item.datasetIndex === 0 && item.parsed.y !== null;
            },
            callbacks: {
              title: function (items) {
                if (!items.length) return "";
                const target = data.nav[items[0].dataIndex];
                return items[0].label + (target ? " " + target.year : "");
              },
              label: function (ctx) {
                return ShekelChart.formatMoney(ctx.parsed.y, true);
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              autoSkip: false,
              maxRotation: 0,
              color: function (ctx) {
                return ctx.index === data.viewed_index
                  ? colors.textSecondary
                  : muted;
              },
              font: function (ctx) {
                return ctx.index === data.viewed_index
                  ? { weight: 600 }
                  : {};
              }
            }
          },
          y: {
            beginAtZero: true,
            ticks: {
              maxTicksLimit: 5,
              // Bare grouped numbers (0 / 2,000 / 4,000), per the ruled
              // mockup -- the axis is unambiguously dollars.
              callback: function (value) {
                return value.toLocaleString("en-US");
              }
            },
            grid: {
              // Emphasize the baseline; keep other gridlines faint.
              color: function (ctx) {
                return ctx.tick && ctx.tick.value === 0
                  ? borderStrong
                  : colors.gridColor;
              }
            }
          }
        }
      },
      plugins: [
        spendingMarkersPlugin(data, {
          tick: borderStrong,
          note: muted,
          valViewed: numberInk,
          valCompare: colors.textSecondary
        })
      ]
    };
  }

  /**
   * Initialize (or re-initialize after a swap) the chart for a subtree.
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
    // Only rebuild when the swapped content holds the chart canvas (a
    // spending tab load or month navigation); an unrelated htmx swap
    // elsewhere on the page must not churn the chart.
    var target = event.target;
    if (!target || !target.querySelector) return;
    if (target.querySelector("#" + CANVAS_ID) || target.id === CANVAS_ID) {
      initChart(target);
    }
  });
})();
