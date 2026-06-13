/**
 * Shekel Budget App -- Dashboard Pulse Renderer (Terminal Road rebuild)
 *
 * Renders the pulse region's client-side presentation:
 *   1. The projected end-balance chart (Chart.js via the ShekelChart
 *      factory, so theme toggles re-resolve colors). Data arrives as a
 *      JSON ``data-chart`` attribute -- floats exist only at that
 *      serialization boundary; this script never computes money, it
 *      only formats axis/tooltip labels from the provided numbers.
 *   2. Street-band event positioning: the CSP forbids inline style
 *      attributes, so day offsets arrive as ``data-day`` and this
 *      script applies ``left: <pct>%`` via the CSSOM (the same bridge
 *      pattern as progress_bar.js).
 *   3. Track rail marker positioning from ``data-rail-pct``.
 *   4. Keyboard activation (Enter / Space) for the click-to-edit
 *      balance control (``role="button"`` without a key handler was a
 *      recorded accessibility gap).
 *
 * Re-initializes after every ``htmx:afterSwap`` so the
 * ``balanceChanged``-driven pulse refresh rebuilds the chart and
 * re-positions the swapped-in street/rails.
 */

(function () {
  "use strict";

  /**
   * Format a number as whole dollars for axis ticks.
   * @param {number} value - Numeric dollar amount.
   * @returns {string} e.g. "-$1,234" / "$2,000".
   */
  function fmtAxis(value) {
    var sign = value < 0 ? "-" : "";
    return sign + "$" + Math.abs(value).toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    });
  }

  /**
   * Format a number as dollars-and-cents for tooltips.
   * @param {number} value - Numeric dollar amount.
   * @returns {string} e.g. "-$112.40".
   */
  function fmtTooltip(value) {
    var sign = value < 0 ? "-" : "";
    return sign + "$" + Math.abs(value).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

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
    if (!isFinite(num)) return "rgba(0,0,0," + alpha + ")";
    var r = (num >> 16) & 255;
    var g = (num >> 8) & 255;
    var b = num & 255;
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  /**
   * Build (or rebuild) the projected end-balance chart from the
   * canvas's ``data-chart`` JSON: {labels: [], values: [], threshold}.
   * @param {Element|Document} root - Subtree containing the canvas.
   */
  function initChart(root) {
    var scope = root && root.querySelector ? root : document;
    var canvas = scope.querySelector("#pulse-chart-canvas") ||
      (scope.id === "pulse-chart-canvas" ? scope : null);
    if (!canvas || typeof ShekelChart === "undefined" ||
        typeof Chart === "undefined") {
      return;
    }

    var data;
    try {
      data = JSON.parse(canvas.getAttribute("data-chart") || "{}");
    } catch (err) {
      // Malformed data-chart JSON is a server-side serialization bug, not
      // a user error: surface it to the console so it is not lost, then
      // bail out of rendering (a broken chart must not take down the rest
      // of the pulse region's JS).
      console.error("Shekel: malformed data-chart JSON", err);
      return;
    }
    if (!data.labels || !data.labels.length) return;

    ShekelChart.create("pulse-chart-canvas", function () {
      var style = getComputedStyle(document.documentElement);
      var accent = style.getPropertyValue("--shekel-accent").trim();
      var danger = style.getPropertyValue("--shekel-danger").trim();
      var credit = style.getPropertyValue("--shekel-credit").trim();
      var colors = ShekelChart.getThemeColors();

      var datasets = [{
        data: data.values,
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

      if (data.threshold !== null && data.threshold !== undefined) {
        datasets.push({
          data: data.labels.map(function () { return data.threshold; }),
          borderColor: credit,
          borderDash: [4, 4],
          borderWidth: 1,
          pointRadius: 0,
          fill: false
        });
      }

      return {
        type: "line",
        data: { labels: data.labels, datasets: datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              filter: function (item) { return item.datasetIndex === 0; },
              callbacks: {
                label: function (ctx) { return fmtTooltip(ctx.parsed.y); }
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
                callback: function (value) { return fmtAxis(value); }
              }
            },
            x: { grid: { display: false } }
          }
        }
      };
    });
  }

  /**
   * Clamp a parsed percentage to [0, 100], or return null when the
   * attribute is missing/malformed.
   * @param {string|null} raw - Attribute value.
   * @returns {number|null} Clamped percentage.
   */
  function clampPct(raw) {
    var pct = parseFloat(raw);
    if (!isFinite(pct)) return null;
    if (pct < 0) pct = 0;
    if (pct > 100) pct = 100;
    return pct;
  }

  /**
   * Position street-band children: each ``[data-day]`` element inside a
   * ``[data-street-days]`` container gets ``left`` as a percentage of
   * the period's day span. Pure layout math on integers the server
   * provided -- no money involved.
   * @param {Element|Document} root - Subtree to position.
   */
  function positionStreet(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var streets = scope.querySelectorAll("[data-street-days]");
    for (var s = 0; s < streets.length; s++) {
      var days = parseFloat(streets[s].getAttribute("data-street-days"));
      if (!isFinite(days) || days <= 0) continue;
      var nodes = streets[s].querySelectorAll("[data-day]");
      for (var i = 0; i < nodes.length; i++) {
        var day = parseFloat(nodes[i].getAttribute("data-day"));
        if (!isFinite(day)) continue;
        var pct = (day / days) * 100;
        if (pct < 0) pct = 0;
        if (pct > 100) pct = 100;
        nodes[i].style.left = pct + "%";
        // Near the band's edges a centered label would clip outside the
        // street; flag the node so the CSS re-anchors its label inward.
        nodes[i].classList.remove("street__edge-left", "street__edge-right");
        if (pct < 8) {
          nodes[i].classList.add("street__edge-left");
        } else if (pct > 92) {
          nodes[i].classList.add("street__edge-right");
        }
      }
    }
  }

  /**
   * Position track rail markers/labels from ``data-rail-pct``.
   * @param {Element|Document} root - Subtree to position.
   */
  function positionRails(root) {
    var scope = root && root.querySelectorAll ? root : document;
    var nodes = scope.querySelectorAll("[data-rail-pct]");
    for (var i = 0; i < nodes.length; i++) {
      var pct = clampPct(nodes[i].getAttribute("data-rail-pct"));
      if (pct !== null) {
        nodes[i].style.left = pct + "%";
      }
    }
  }

  /**
   * Initialize everything within a subtree (initial load and after
   * each HTMX swap of the pulse region).
   * @param {Element|Document} root - Subtree to initialize.
   */
  function init(root) {
    initChart(root);
    positionStreet(root);
    positionRails(root);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init(document);
    });
  } else {
    init(document);
  }

  document.body.addEventListener("htmx:afterSwap", function (event) {
    init(event.target || document);
  });

  // Enter / Space activate the click-to-edit balance (role="button").
  document.body.addEventListener("keydown", function (event) {
    var target = event.target;
    if (!target || target.id !== "balance-display") return;
    if (event.key === "Enter" || event.key === " " ||
        event.key === "Spacebar") {
      event.preventDefault();
      target.click();
    }
  });
})();
