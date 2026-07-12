/**
 * Shekel Budget App -- Net Worth Cockpit stream renderer (P-AC1).
 *
 * Renders the accounts screen's net-worth element: ONE diverging stream
 * chart (Chart.js via the ShekelChart factory, so a theme toggle re-resolves
 * colors) that replaced both the old net-worth trend chart and the diverging
 * allocation bar.  Asset-category bands stack up from the zero line on the
 * accent ramp, the liability band hangs below in the danger token, and the
 * net-worth line rides the difference.  Both ranges arrive as one JSON
 * ``data-chart`` attribute on the canvas (the route's
 * _serialize_net_worth_chart):
 *   {labels, net, assets, liabilities, current_index, composition,
 *    horizon: {labels, net, composition, milestones, current_index} | null}
 * where ``composition`` maps each band (asset / retirement / investment /
 * other / liability) to a parallel float series.  Floats exist only at that
 * serialization boundary; this script never computes money -- it SELECTS the
 * active range, stacks the provided band series, RESTYLES the net line
 * (solid history vs dashed projection), and FORMATS axis / tooltip / label
 * text.  The one geometric transform is negating the liability series so the
 * band hangs below the zero axis (a plot position, not a money value).
 *
 * One piece of view state, pure presentation:
 *   - range: "horizon" (annual, out to the last loan payoff -- the default)
 *     or "2yr" (the engine-real biweekly series, with the current_index
 *     solid/dashed boundary and a Today marker).  The toggle lives in
 *     #cockpit-section; clicks are delegated so they survive the
 *     balanceChanged refresh that swaps the section, and the chosen range is
 *     re-asserted on the freshly-swapped controls.
 *
 * The band colors come from the --nw-band-* tokens (accounts.css) read off
 * :root, so the chart, its legend swatches, and the theme all read one
 * source; readBandColors resolves each token (including the color-mix() ramp
 * stops) to a concrete rgba for Chart.js, the way every ShekelChart consumer
 * hands it rgba.  Milestone flags (horizon only) and the end-of-line net
 * label are drawn by inline plugins, mirroring salary_chart.js /
 * dashboard_pulse.js.
 *
 * Re-initializes after every ``htmx:afterSwap`` that carries the cockpit
 * canvas (a balanceChanged-driven refresh) and on every
 * ``shekel:theme-changed`` (via the ShekelChart factory re-invoking
 * buildConfig, which reads the persisted range and re-resolves colors).
 */

(function () {
  "use strict";

  var CANVAS_ID = "net-worth-chart-canvas";
  var DEFAULT_RANGE = "horizon";
  var PROJECTION_DASH = [6, 5];

  // Composition bands: the asset-side groups stack up from zero in this
  // order (bottom to top); the liability band is plotted below zero.  The
  // keys match the producer's composition map and the --nw-band-* tokens.
  var ASSET_BANDS = ["asset", "retirement", "investment", "other"];
  var LIABILITY_BAND = "liability";

  // Display labels for the tooltip + dataset names (the Jinja legend carries
  // the same strings; JS cannot read that dict, so the pairing is asserted
  // by keeping both in the band vocabulary).
  var BAND_LABELS = {
    asset: "Assets",
    retirement: "Retirement",
    investment: "Investment",
    other: "Other",
    liability: "Liabilities"
  };

  // Milestone-flag geometry (canvas px): the two staggered lanes reserved in
  // the chart's top layout padding, the flag chip height, and its text pad.
  var FLAG_TOP = 4;
  var FLAG_LANE_H = 20;
  var FLAG_H = 15;
  var FLAG_PAD_X = 6;
  var FLAG_PAD_TOP = 44;      // top layout padding when flags are shown
  var FLAG_PAD_TOP_BARE = 12; // top padding for the 2-year range (no flags)
  var END_LABEL_PAD_RIGHT = 56;

  // Persisted view state, re-asserted onto the controls on each (re)init so
  // a section swap or theme re-render keeps the user's chosen range.
  var range = DEFAULT_RANGE;

  /**
   * Strip trailing zeros (and a bare decimal point) from a fixed-decimal
   * string so compact money reads "$1.5M" / "$358k", not "$1.50M" / "$358.0k".
   * @param {string} text - A ``toFixed`` result.
   * @returns {string} The trimmed string.
   */
  function stripZeros(text) {
    return text.indexOf(".") >= 0 ? text.replace(/\.?0+$/, "") : text;
  }

  /**
   * Format a final dollar amount compactly for the axis ticks and the
   * end-of-line label ("$1.19M", "$500k", "$0").  Display formatting only --
   * the value is already computed; no money math happens here.
   * @param {number} value - Numeric dollar amount.
   * @returns {string} A compact dollar string.
   */
  function compactMoney(value) {
    var abs = Math.abs(value);
    var sign = value < 0 ? "-" : "";
    if (abs >= 1e6) return sign + "$" + stripZeros((abs / 1e6).toFixed(2)) + "M";
    if (abs >= 1e3) return sign + "$" + stripZeros((abs / 1e3).toFixed(1)) + "k";
    return sign + "$" + Math.round(abs);
  }

  /**
   * Whether a band series carries any non-zero point (so it is worth drawing
   * as a stacked area and listing in the tooltip).
   * @param {number[]} series - A band's float series.
   * @returns {boolean} True when at least one point is non-zero.
   */
  function isNonZero(series) {
    return Boolean(series) && series.some(function (value) {
      return value !== 0;
    });
  }

  /**
   * Trace a rounded-rectangle path (no dependency on ctx.roundRect, matching
   * the plain-canvas idiom of the other chart plugins).
   * @param {CanvasRenderingContext2D} ctx - The 2D context.
   * @param {number} x - Left.
   * @param {number} y - Top.
   * @param {number} w - Width.
   * @param {number} h - Height.
   * @param {number} r - Corner radius.
   */
  function roundRectPath(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  /**
   * Parse the canvas's ``data-chart`` JSON (both ranges in one payload).
   * @param {Element} canvas - The stream canvas.
   * @returns {object|null} The payload, or null when missing / malformed /
   *   empty.
   */
  function parseData(canvas) {
    var data;
    try {
      data = JSON.parse(canvas.getAttribute("data-chart") || "{}");
    } catch (err) {
      // Malformed data-chart JSON is a server-side serialization bug, not a
      // user error: surface it and bail so a broken chart cannot take down
      // the rest of the page's JS.
      console.error("Shekel: malformed net-worth data-chart JSON", err);
      return null;
    }
    if (!data.net || !data.net.length) return null;
    return data;
  }

  /**
   * Select the active range's series into one common shape.  "horizon" reads
   * the nested horizon payload (falling back to the 2-year series only in the
   * defensive case the horizon is absent); "2yr" reads the top-level series.
   * @param {object} data - The parsed payload.
   * @returns {{labels: string[], net: number[], composition: object,
   *   currentIndex: number, milestones: object[]}} The active range.
   */
  function selectRange(data) {
    var horizon;
    if (range === "horizon" && data.horizon) {
      horizon = data.horizon;
      return {
        labels: horizon.labels,
        net: horizon.net,
        composition: horizon.composition,
        currentIndex: horizon.current_index || 0,
        milestones: horizon.milestones || []
      };
    }
    return {
      labels: data.labels,
      net: data.net,
      composition: data.composition,
      currentIndex: data.current_index || 0,
      milestones: []
    };
  }

  // Per-band fill opacity: the asset-side bands sit at 30%, the liability
  // band a touch quieter at 22%, so the areas stay calm at panel scale.
  var BAND_FILL_ALPHA = {
    asset: 0.30, retirement: 0.30, investment: 0.30, other: 0.30,
    liability: 0.22
  };

  /**
   * Resolve a CSS color value (a hex token, or a color-mix() ramp stop --
   * the D12 asset ramp is defined that way) to an rgba() string at the given
   * alpha, by painting it on a 1x1 canvas and reading the pixel back.  This
   * hands Chart.js a concrete rgba the same way every other ShekelChart
   * consumer does via hexToRgba, rather than passing a color-mix() string
   * its @kurkle/color parser does not understand; the canvas does the
   * resolution the browser already applies to the CSS.
   * @param {string} cssColor - A resolved CSS color value.
   * @param {number} alpha - Alpha channel 0..1.
   * @returns {string} An rgba(...) string.
   */
  function resolveRgba(cssColor, alpha) {
    var ctx = resolveRgba._ctx;
    var canvas;
    if (!ctx) {
      canvas = document.createElement("canvas");
      canvas.width = 1;
      canvas.height = 1;
      ctx = resolveRgba._ctx = canvas.getContext(
        "2d", { willReadFrequently: true }
      );
    }
    ctx.fillStyle = cssColor;
    ctx.fillRect(0, 0, 1, 1);
    var px = ctx.getImageData(0, 0, 1, 1).data;
    return "rgba(" + px[0] + "," + px[1] + "," + px[2] + "," + alpha + ")";
  }

  /**
   * Read each band's solid stop from the --nw-band-* tokens and resolve it
   * to an opaque border color plus a translucent fill at the band's alpha
   * (theme-resolved at call time, so a theme toggle re-reads the ramp).
   * @param {CSSStyleDeclaration} style - The :root computed style.
   * @returns {object} {band: {solid, fill}} for all five bands.
   */
  function readBandColors(style) {
    var colors = {};
    ASSET_BANDS.concat([LIABILITY_BAND]).forEach(function (band) {
      var token = style.getPropertyValue("--nw-band-" + band).trim();
      colors[band] = {
        solid: resolveRgba(token, 1),
        fill: resolveRgba(token, BAND_FILL_ALPHA[band])
      };
    });
    return colors;
  }

  /**
   * Build the stacked composition-band datasets: the non-zero asset bands
   * (each filling down to the band below, the first to the zero origin) and
   * the liability band (its series negated so it hangs below zero -- a plot
   * position, not a money value).  All share one stack group so Chart.js
   * stacks the assets above zero and the liability below.
   * @param {object} sel - The selected range.
   * @param {object} bandColors - Resolved per-band colors.
   * @returns {Array<object>} The band datasets, in stack order.
   */
  function buildBandDatasets(sel, bandColors) {
    var datasets = [];
    var firstAsset = true;
    ASSET_BANDS.forEach(function (band) {
      var series = sel.composition[band];
      if (!isNonZero(series)) return;
      datasets.push({
        band: band,
        label: BAND_LABELS[band],
        data: series,
        stack: "nw",
        fill: firstAsset ? "origin" : "-1",
        backgroundColor: bandColors[band].fill,
        borderColor: bandColors[band].solid,
        borderWidth: 1.75,
        pointRadius: 0,
        pointHoverRadius: 0,
        tension: 0
      });
      firstAsset = false;
    });
    var liability = sel.composition[LIABILITY_BAND];
    if (isNonZero(liability)) {
      datasets.push({
        band: LIABILITY_BAND,
        label: BAND_LABELS[LIABILITY_BAND],
        // Negate so the owed magnitude hangs below the zero axis (display
        // geometry -- the tooltip re-negates to show the positive figure).
        // ``|| 0`` collapses the -0 of negating a zero owed balance to +0.
        data: liability.map(function (value) { return -value || 0; }),
        // The single liability band gets its OWN stack group: sharing the
        // asset stack made a zero/paid-off owed balance stack at the asset
        // cumulative top instead of resting at the zero axis.
        stack: "liab",
        fill: "origin",
        backgroundColor: bandColors[LIABILITY_BAND].fill,
        borderColor: bandColors[LIABILITY_BAND].solid,
        borderWidth: 1.75,
        pointRadius: 0,
        pointHoverRadius: 0,
        tension: 0
      });
    }
    return datasets;
  }

  /**
   * Build the net-worth line dataset: its own stack group so it renders at
   * the raw net value (not added to the composition stack), in the money ink,
   * solid through the current period then dashed for the projection.
   * @param {object} sel - The selected range.
   * @param {string} ink - The --shekel-number-ink color.
   * @returns {object} The net-line dataset.
   */
  function buildNetDataset(sel, ink) {
    return {
      label: "Net worth",
      data: sel.net,
      stack: "net",
      fill: false,
      borderColor: ink,
      borderWidth: 2.25,
      tension: 0,
      pointRadius: 0,
      pointHoverRadius: 4,
      segment: ShekelChart.splitSegment(
        sel.currentIndex, ink, ShekelChart.hexToRgba(ink, 0.85), PROJECTION_DASH
      )
    };
  }

  /**
   * Inline plugin: mark the net line's endpoint with a dot and a compact
   * dollar label in the reserved right margin (mirrors the dashboard's
   * end-station label; ShekelChart.todayMarkerPlugin is the sibling idiom).
   * @param {number} netIndex - The net dataset's index.
   * @param {string} color - The net-line color.
   * @returns {object} A Chart.js plugin.
   */
  function endLabelPlugin(netIndex, color) {
    return {
      id: "nwEndLabel",
      afterDatasetsDraw: function (chart) {
        var meta = chart.getDatasetMeta(netIndex);
        if (!meta || !meta.data || !meta.data.length) return;
        var last = meta.data.length - 1;
        var point = meta.data[last];
        if (!point) return;
        var value = chart.data.datasets[netIndex].data[last];
        var ctx = chart.ctx;
        ctx.save();
        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.arc(point.x, point.y, 3.2, 0, 2 * Math.PI);
        ctx.fill();
        ctx.font = "600 11.5px 'JetBrains Mono', ui-monospace, monospace";
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(compactMoney(value), point.x + 8, point.y);
        ctx.restore();
      }
    };
  }

  /**
   * Inline plugin: draw the milestone flags (loan payoffs, debt-free, each
   * $500k net crossing) on two staggered lanes in the top padding, each with
   * a dashed accent drop-line to the zero axis.  Each milestone carries a
   * fractional x-index (server-computed by _milestone_axis_x), positioned via
   * getPixelForValue so a flag lands between the annual samples.
   * @param {object[]} milestones - [{x, label}], ascending by x.
   * @param {object} inks - {drop, chipBg, chipBorder, chipText}.
   * @returns {object} A Chart.js plugin.
   */
  function milestoneFlagPlugin(milestones, inks) {
    return {
      id: "nwMilestones",
      afterDatasetsDraw: function (chart) {
        if (!milestones || !milestones.length) return;
        var xScale = chart.scales.x;
        var yScale = chart.scales.y;
        var zeroY = yScale.getPixelForValue(0);
        var ctx = chart.ctx;
        var i;
        var milestone;
        var x;
        var chipY;
        var chipW;
        var chipX;
        ctx.save();
        ctx.font = "9.5px 'Inter', system-ui, sans-serif";
        ctx.textBaseline = "middle";
        for (i = 0; i < milestones.length; i++) {
          milestone = milestones[i];
          x = xScale.getPixelForValue(milestone.x);
          chipY = FLAG_TOP + (i % 2) * FLAG_LANE_H;

          // Dashed accent drop-line from the chip down to the zero axis.
          ctx.beginPath();
          ctx.setLineDash([2, 3]);
          ctx.lineWidth = 1;
          ctx.strokeStyle = inks.drop;
          ctx.moveTo(x, chipY + FLAG_H);
          ctx.lineTo(x, zeroY);
          ctx.stroke();
          ctx.setLineDash([]);

          // Surface-raised chip with a subtle border, clamped within the plot.
          chipW = ctx.measureText(milestone.label).width + FLAG_PAD_X * 2;
          chipX = Math.min(
            Math.max(x - chipW / 2, xScale.left), xScale.right - chipW
          );
          roundRectPath(ctx, chipX, chipY, chipW, FLAG_H, 4);
          ctx.fillStyle = inks.chipBg;
          ctx.fill();
          ctx.lineWidth = 1;
          ctx.strokeStyle = inks.chipBorder;
          ctx.stroke();
          ctx.fillStyle = inks.chipText;
          ctx.textAlign = "center";
          ctx.fillText(milestone.label, chipX + chipW / 2, chipY + FLAG_H / 2 + 0.5);
        }
        ctx.restore();
      }
    };
  }

  /**
   * Build the full Chart.js config for the active range.  Reads the canvas
   * data, the persisted range, and the theme tokens fresh each call so a
   * balanceChanged refresh and a theme toggle both rebuild from current state.
   * @returns {object|null} A Chart.js config, or null when no canvas / data.
   */
  function buildConfig() {
    var canvas = document.getElementById(CANVAS_ID);
    if (!canvas) return null;
    var data = parseData(canvas);
    if (!data) return null;
    var sel = selectRange(data);

    var style = getComputedStyle(document.documentElement);
    var colors = ShekelChart.getThemeColors();
    var ink = style.getPropertyValue("--shekel-number-ink").trim();
    var bandColors = readBandColors(style);

    var datasets = buildBandDatasets(sel, bandColors)
      .concat([buildNetDataset(sel, ink)]);
    var netIndex = datasets.length - 1;
    var hasFlags = sel.milestones.length > 0;

    var flagInks = {
      drop: ShekelChart.hexToRgba(
        style.getPropertyValue("--shekel-accent").trim(), 0.7
      ),
      chipBg: style.getPropertyValue("--shekel-surface-raised").trim(),
      chipBorder: style.getPropertyValue("--shekel-border-subtle").trim(),
      chipText: colors.textSecondary
    };

    return {
      type: "line",
      data: { labels: sel.labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: {
          padding: {
            top: hasFlags ? FLAG_PAD_TOP : FLAG_PAD_TOP_BARE,
            right: END_LABEL_PAD_RIGHT
          }
        },
        interaction: { mode: "index", intersect: false },
        plugins: {
          // The composition + net figures are named in the HTML legend below
          // the canvas (with today's subtotals); the on-canvas legend would
          // duplicate it.
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var value = ctx.parsed.y;
                // The liability band is plotted negative; report its owed
                // magnitude.
                if (ctx.dataset.band === LIABILITY_BAND) value = -value;
                return ctx.dataset.label + ": " +
                  ShekelChart.formatMoney(value, false);
              }
            }
          }
        },
        scales: {
          y: {
            stacked: true,
            grid: {
              // Emphasize the zero line (assets above, liabilities below);
              // keep other gridlines faint.
              color: function (ctx) {
                return ctx.tick && ctx.tick.value === 0
                  ? colors.textSecondary
                  : colors.gridColor;
              }
            },
            ticks: {
              callback: function (value) { return compactMoney(value); }
            }
          },
          x: {
            grid: { display: false },
            ticks: { maxTicksLimit: 8, maxRotation: 0, autoSkip: true }
          }
        }
      },
      plugins: [
        ShekelChart.todayMarkerPlugin(sel.currentIndex, colors.textSecondary),
        endLabelPlugin(netIndex, ink),
        milestoneFlagPlugin(sel.milestones, flagInks)
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
   * Mark one range button active within its group and clear its sibling,
   * keeping aria-pressed in sync.
   * @param {Element} btn - The button to activate.
   */
  function setActiveRange(btn) {
    var group = btn.parentElement;
    var siblings = group.querySelectorAll("[data-nw-range]");
    var i;
    var on;
    for (i = 0; i < siblings.length; i++) {
      on = siblings[i] === btn;
      siblings[i].classList.toggle("active", on);
      siblings[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  /**
   * Initialize (or re-initialize after a swap) the chart for a subtree,
   * re-asserting the persisted range onto the freshly-rendered controls so a
   * balanceChanged refresh keeps the user's chosen view.
   * @param {Element|Document} root - Subtree that may contain the canvas.
   */
  function initChart(root) {
    var scope = root && root.querySelector ? root : document;
    var canvas = scope.querySelector("#" + CANVAS_ID) ||
      document.getElementById(CANVAS_ID);
    if (!canvas) return;
    var btn = document.querySelector('[data-nw-range="' + range + '"]');
    if (btn) setActiveRange(btn);
    render();
  }

  // Range clicks (delegated so they survive the section swap).
  document.body.addEventListener("click", function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var rangeBtn = target.closest("[data-nw-range]");
    if (rangeBtn) {
      range = rangeBtn.getAttribute("data-nw-range");
      setActiveRange(rangeBtn);
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
