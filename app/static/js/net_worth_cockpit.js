/**
 * Shekel Budget App -- Net Worth Cockpit renderer.
 *
 * Renders the accounts screen's net-worth trend chart (Chart.js via the
 * ShekelChart factory, so a theme toggle re-resolves colors).  The series
 * arrives as a JSON ``data-chart`` attribute on the canvas:
 * {labels: [str], net: [float], assets: [float], liabilities: [float],
 *  current_index: int}.  Floats exist only at that serialization boundary
 * (the route's _serialize_net_worth_chart); this script never computes
 * money -- it only SLICES the provided points to the chosen horizon,
 * RESTYLES them (solid history vs dashed projection), and FORMATS axis and
 * tooltip labels.
 *
 * ``current_index`` is the position of the current period in the series:
 * points [0, current_index) are the honest history tail (drawn solid) and
 * the rest are the forward projection (drawn dashed and lighter), with a
 * "Today" marker at the boundary.  It is also the slice anchor -- the
 * horizon picker keeps the full history tail and varies the forward reach.
 *
 * Two pieces of view state, both pure presentation:
 *   - series: "net" (one net line, semantic fill) or "split" (separate
 *     Assets and Liabilities lines).
 *   - horizon: 6 / 13 / 26 forward periods, or "all".  Default 13.
 * The controls live in #cockpit-section; clicks are handled by delegation
 * so they survive the balanceChanged refresh that swaps the section.
 *
 * Re-initializes after every ``htmx:afterSwap`` so a balanceChanged-driven
 * refresh rebuilds the chart (resetting the controls to their rendered
 * defaults), and on every ``shekel:theme-changed`` (via the ShekelChart
 * factory re-invoking buildConfig, which reads the persisted view state).
 */

(function () {
  "use strict";

  var CANVAS_ID = "net-worth-chart-canvas";
  var DEFAULT_HORIZON = 13;
  var PROJECTION_DASH = [6, 5];

  // Persisted view state, mirrored from the active control buttons on each
  // (re)init so a theme re-render keeps the user's chosen view/horizon.
  var view = "net";
  var horizon = DEFAULT_HORIZON;

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
      console.error("Shekel: malformed net-worth data-chart JSON", err);
      return null;
    }
    if (!data.net || !data.net.length) return null;
    return data;
  }

  /**
   * Slice the series to the chosen forward horizon, always keeping the
   * full history tail.
   *
   * The visible window is points [0, current_index + horizon): the history
   * tail (everything before the current period) stays, and the forward
   * reach is the next ``horizon`` periods from the current one.  "all"
   * keeps every point.  ``current_index`` is unchanged by the slice (it
   * starts at 0), so it still marks the solid/dashed boundary in the
   * sliced arrays.
   *
   * @param {object} data - The parsed series.
   * @returns {{labels: string[], net: number[], assets: number[],
   *   liabilities: number[], currentIndex: number}} The sliced series.
   */
  function sliceToHorizon(data) {
    var currentIndex = data.current_index || 0;
    var total = data.net.length;
    var end = horizon === Infinity
      ? total
      : Math.min(total, currentIndex + horizon);
    return {
      labels: data.labels.slice(0, end),
      net: data.net.slice(0, end),
      assets: data.assets.slice(0, end),
      liabilities: data.liabilities.slice(0, end),
      currentIndex: currentIndex
    };
  }

  /**
   * Build the dataset list for the active view.  The solid-history vs
   * dashed-projection segment styling comes from the shared
   * ShekelChart.splitSegment helper (chart_theme.js), so this chart and
   * the account-detail chart cannot drift apart.
   * @param {object} sliced - The sliced series.
   * @param {object} palette - Resolved theme colors.
   * @returns {Array<object>} Chart.js datasets.
   */
  function buildDatasets(sliced, palette) {
    var currentIndex = sliced.currentIndex;

    if (view === "split") {
      // Assets and liabilities as two lines (no fill); each split into
      // solid history and dashed projection.
      return [
        {
          label: "Assets",
          data: sliced.assets,
          borderColor: palette.accent,
          borderWidth: 2,
          tension: 0,
          pointRadius: 2,
          segment: ShekelChart.splitSegment(
            currentIndex, palette.accent,
            ShekelChart.hexToRgba(palette.accent, 0.5), PROJECTION_DASH
          )
        },
        {
          label: "Liabilities",
          data: sliced.liabilities,
          borderColor: palette.danger,
          borderWidth: 2,
          tension: 0,
          pointRadius: 2,
          segment: ShekelChart.splitSegment(
            currentIndex, palette.danger,
            ShekelChart.hexToRgba(palette.danger, 0.5), PROJECTION_DASH
          )
        }
      ];
    }

    // Net view: one line with a semantic fill (faint accent above zero, a
    // danger pocket below) and the solid/dashed history-vs-projection split.
    return [{
      label: "Net worth",
      data: sliced.net,
      borderColor: palette.accent,
      borderWidth: 2,
      tension: 0,
      pointRadius: function (ctx) {
        return ctx.parsed && ctx.parsed.y < 0 ? 4 : 2.5;
      },
      pointBackgroundColor: function (ctx) {
        return ctx.parsed && ctx.parsed.y < 0 ? palette.danger : palette.accent;
      },
      segment: ShekelChart.splitSegment(
        currentIndex, palette.accent,
        ShekelChart.hexToRgba(palette.accent, 0.5), PROJECTION_DASH
      ),
      fill: {
        target: "origin",
        above: ShekelChart.hexToRgba(palette.accent, 0.10),
        below: ShekelChart.hexToRgba(palette.danger, 0.25)
      }
    }];
  }

  /**
   * Build the full Chart.js config for the active view + horizon.  Reads
   * the canvas data fresh each call so a balanceChanged refresh and a theme
   * toggle both rebuild from current data and state.
   * @returns {object|null} A Chart.js config, or null when no canvas/data.
   */
  function buildConfig() {
    var canvas = document.getElementById(CANVAS_ID);
    if (!canvas) return null;
    var data = parseData(canvas);
    if (!data) return null;

    var sliced = sliceToHorizon(data);
    var style = getComputedStyle(document.documentElement);
    var colors = ShekelChart.getThemeColors();
    var palette = {
      accent: style.getPropertyValue("--shekel-accent").trim(),
      danger: style.getPropertyValue("--shekel-danger").trim()
    };

    return {
      type: "line",
      data: {
        labels: sliced.labels,
        datasets: buildDatasets(sliced, palette)
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: view === "split" },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var name = ctx.dataset.label ? ctx.dataset.label + ": " : "";
                return name + ShekelChart.formatMoney(ctx.parsed.y, true);
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
      },
      plugins: [
        ShekelChart.todayMarkerPlugin(sliced.currentIndex, colors.textSecondary)
      ]
    };
  }

  /**
   * (Re)create the chart on its canvas from the current state.
   */
  function render() {
    if (typeof ShekelChart === "undefined" || typeof Chart === "undefined") {
      return;
    }
    if (!document.getElementById(CANVAS_ID)) return;
    ShekelChart.create(CANVAS_ID, buildConfig);
  }

  /**
   * Sync the module view state from the active control buttons, then mark
   * the matching buttons active.  Called on (re)init so the state mirrors
   * the freshly-rendered DOM (defaults after a section swap).
   * @param {Element|Document} scope - Subtree holding the controls.
   */
  function syncStateFromControls(scope) {
    var activeSeries = scope.querySelector("[data-nw-series].active");
    var activeHorizon = scope.querySelector("[data-nw-horizon].active");
    var raw;
    if (activeSeries) view = activeSeries.getAttribute("data-nw-series");
    if (activeHorizon) {
      raw = activeHorizon.getAttribute("data-nw-horizon");
      horizon = raw === "all" ? Infinity : parseInt(raw, 10);
    }
  }

  /**
   * Mark one button active within its group and clear its siblings,
   * keeping aria-pressed in sync.
   * @param {Element} btn - The clicked button.
   * @param {string} attr - The group's data attribute name.
   */
  function setActive(btn, attr) {
    var group = btn.parentElement;
    var siblings = group.querySelectorAll("[" + attr + "]");
    var i;
    var on;
    for (i = 0; i < siblings.length; i++) {
      on = siblings[i] === btn;
      siblings[i].classList.toggle("active", on);
      siblings[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  /**
   * Initialize (or re-initialize after a swap) the chart for a subtree.
   * @param {Element|Document} root - Subtree that may contain the canvas.
   */
  function initChart(root) {
    var scope = root && root.querySelector ? root : document;
    var canvas = scope.querySelector("#" + CANVAS_ID) ||
      document.getElementById(CANVAS_ID);
    if (!canvas) return;
    syncStateFromControls(document);
    render();
  }

  // Control clicks (delegated so they survive the section swap).
  document.body.addEventListener("click", function (event) {
    var target = event.target;
    var seriesBtn;
    var horizonBtn;
    var raw;
    if (!target || !target.closest) return;

    seriesBtn = target.closest("[data-nw-series]");
    if (seriesBtn) {
      view = seriesBtn.getAttribute("data-nw-series");
      setActive(seriesBtn, "data-nw-series");
      render();
      return;
    }
    horizonBtn = target.closest("[data-nw-horizon]");
    if (horizonBtn) {
      raw = horizonBtn.getAttribute("data-nw-horizon");
      horizon = raw === "all" ? Infinity : parseInt(raw, 10);
      setActive(horizonBtn, "data-nw-horizon");
      render();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initChart(document);
    });
  } else {
    initChart(document);
  }

  document.body.addEventListener("htmx:afterSwap", function (event) {
    // Only rebuild when the swapped content holds the cockpit canvas (the
    // balanceChanged refresh of #cockpit-section); an unrelated htmx swap
    // elsewhere on the page must not churn the chart.
    var target = event.target;
    if (!target || !target.querySelector) return;
    if (target.querySelector("#" + CANVAS_ID) || target.id === CANVAS_ID) {
      initChart(target);
    }
  });
})();
