'use strict';

/**
 * Shekel Budget App -- Property detail equity chart.
 *
 * Renders the property detail band's equity-over-time chart
 * (accounts/property_detail.html) in the locked "stacked shares"
 * direction (docs/design/account_detail_audit.md, Loop A lock
 * 2026-07-11): the secured-debt region is washed danger from zero (the
 * bank's share), the equity band is washed accent between the debt and
 * value lines (the owner's share), and at payoff the danger wedge
 * pinches out.  Series colors follow the cockpit split-view convention
 * (market value = accent, secured debt = danger).
 *
 * The debt line carries three CONFIDENCE tiers, keyed off the per-month
 * ``debt_tier`` array the date-anchored producer emits
 * (docs/plans/implementation_plan_property_equity_chart_rebuild.md):
 * ``confirmed`` recorded payments draw solid, the ``projected`` committed
 * plan draws dashed, and the ``estimated`` contractual back-projection
 * for the months before your records begin draws in faint dots -- the
 * same "this is an assumption" texture the value line uses for its flat
 * carry, so dots always mean estimate, solid always means recorded, and
 * dashes always mean the committed forward plan.  Because recorded
 * history can end BEFORE today (the confirmed/projected split is data
 * driven, not "today"), the debt styling is driven per segment by the
 * tier array, never by a single index.
 *
 * ``today_index`` is used only for the value line's flat / compound split
 * and the shared Today marker.  A faint "Tracking start" marker sits at
 * the seam where the estimated stretch meets recorded/projected data --
 * the honest discontinuity the producer refuses to smooth (the
 * contractual estimate and your recorded opening differ there).
 *
 * The series arrives as JSON in the canvas's ``data-chart`` attribute:
 * {labels: [str], value: [float], debt: [float], equity: [float],
 * today_index: int, debt_tier: [str]}.  Floats exist only at that
 * serialization boundary; this script never computes money -- the equity
 * the tooltip footer prints is the route's precomputed per-month Decimal
 * difference, read from the ``equity`` array, never derived here.
 *
 * ``data-chart-state`` carries the content state: "no_loans" draws the
 * 10-year appreciation-only fallback (single value line, origin fill,
 * no legend, no Today marker -- the whole span is projection);
 * "zero_rate" and "standard" differ only in the caption the template
 * renders, not in chart behavior.
 */

(function () {
  "use strict";

  var CANVAS_ID = "property-equity-chart";

  // The producer's ``debt_tier`` presentation tokens (mirrored here so
  // the string literal lives in one place per file).  These are render
  // state, not ref-table rows, so comparing them by value is correct --
  // the producer's docstring sanctions it explicitly.
  var TIER_ESTIMATED = "estimated";
  var TIER_PROJECTED = "projected";

  // Dash patterns: the family projection dash and the "assumption" dots.
  var PROJECTION_DASH = [6, 5];
  var ASSUMPTION_DOTS = [2, 3];

  // Alpha for a reduced-strength (assumption / projection) line stroke.
  var ASSUMPTION_ALPHA = 0.45;
  var PROJECTION_ALPHA = 0.5;

  // Minimum pixel gap between the Tracking-start seam and the Today marker
  // before the seam is drawn.  When records begin close to today (the common
  // single-mortgage case, where the estimated stretch runs right up to now),
  // the two markers and their labels would collide; Today owns that region and
  // the dotted texture + caption carry the seam's meaning, so the redundant
  // seam marker is suppressed.  Sized to clear the "Today" label plus the
  // left-aligned "Tracking start" label.
  var SEAM_TODAY_MIN_GAP_PX = 100;

  // Minimum on-screen region height for an in-region identity label;
  // below it the label is skipped (never clipped) and the legend +
  // tooltip carry identity alone.
  var LABEL_MIN_PX = 18;

  /**
   * Parse the canvas's ``data-chart`` JSON.
   * @param {Element} canvas - The equity chart canvas.
   * @returns {object|null} The series object, or null when missing /
   *   malformed / empty.
   */
  function parseData(canvas) {
    var data;
    try {
      data = JSON.parse(canvas.getAttribute("data-chart") || "{}");
    } catch (err) {
      // Malformed data-chart JSON is a server-side serialization bug,
      // not a user error: surface it and bail so a broken chart cannot
      // take down the rest of the page's JS.
      console.error("Shekel: malformed property equity data-chart JSON", err);
      return null;
    }
    if (!data.value || !data.value.length) return null;
    return data;
  }

  /**
   * Scriptable segment options for the market value line: assumption
   * end to end, so never solid.  Short dots at reduced alpha for the
   * flat carry through Today, the family projection dash after.
   * @param {number} todayIndex - The flat-carry / compounding boundary.
   * @param {string} accent - The resolved accent color.
   * @returns {object} A Chart.js ``segment`` option object.
   */
  function valueSegment(todayIndex, accent) {
    return {
      borderDash: function (ctx) {
        return ctx.p1DataIndex > todayIndex ? PROJECTION_DASH : ASSUMPTION_DOTS;
      },
      borderColor: function (ctx) {
        return ctx.p1DataIndex > todayIndex
          ? accent
          : ShekelChart.hexToRgba(accent, ASSUMPTION_ALPHA);
      }
    };
  }

  /**
   * Scriptable segment options for the secured-debt line: three
   * confidence tiers keyed off the per-month ``debt_tier`` array.
   *   estimated -> faint dots (a contractual estimate for the months
   *                before recorded history begins -- the value line's
   *                assumption texture, reused so dots always mean
   *                "estimated, not recorded");
   *   projected -> dashed at reduced alpha (the committed plan);
   *   confirmed -> solid, full danger (recorded payments).
   * A segment takes its END point's tier (``p1DataIndex``), so the
   * boundary segment adopts the tier it crosses INTO -- the family
   * convention (ShekelChart.splitSegment) generalized from two tiers to
   * three.  The split is data driven, never "today": recorded history
   * can end before today (M1), so a single index cannot style this line.
   * @param {Array<string>} tiers - Per-month debt_tier tokens.
   * @param {string} danger - The resolved danger color.
   * @returns {object} A Chart.js ``segment`` option object.
   */
  function debtSegment(tiers, danger) {
    return {
      borderDash: function (ctx) {
        var tier = tiers[ctx.p1DataIndex];
        if (tier === TIER_ESTIMATED) return ASSUMPTION_DOTS;
        if (tier === TIER_PROJECTED) return PROJECTION_DASH;
        return undefined;
      },
      borderColor: function (ctx) {
        var tier = tiers[ctx.p1DataIndex];
        if (tier === TIER_ESTIMATED) {
          return ShekelChart.hexToRgba(danger, ASSUMPTION_ALPHA);
        }
        if (tier === TIER_PROJECTED) {
          return ShekelChart.hexToRgba(danger, PROJECTION_ALPHA);
        }
        return danger;
      }
    };
  }

  /**
   * Inline plugin: a faint dashed "Tracking start" marker at each seam
   * where the estimated back-projection meets recorded/projected data.
   * That seam is an honest discontinuity -- the contractual estimate and
   * your recorded opening differ there, and the producer does not
   * reconcile it -- so it earns a landmark.  Drawn muted, thinner, and
   * finer-dashed than the Today marker so Today stays primary; the label
   * is offset right of its line so a seam near the left edge never clips,
   * and a seam that lands on Today's month is skipped (Today owns it).
   * @param {Array<string>} tiers - Per-month debt_tier tokens.
   * @param {number} todayIndex - The Today marker's index (collision guard).
   * @param {string} color - The muted marker line + label color.
   * @returns {object} A Chart.js plugin.
   */
  function trackingStartPlugin(tiers, todayIndex, color) {
    return {
      id: "shekelTrackingStartMarker",
      afterDatasetsDraw: function (chart) {
        var meta = chart.getDatasetMeta(0);
        if (!meta || !meta.data) return;
        var len = meta.data.length;
        var xs = chart.scales.x;
        var ys = chart.scales.y;
        var ctx = chart.ctx;
        var todayX = (xs.getPixelForValue(todayIndex - 1) +
          xs.getPixelForValue(todayIndex)) / 2;
        for (let i = 1; i < len; i++) {
          // Only an estimated -> not-estimated transition is a seam.
          if (tiers[i - 1] !== TIER_ESTIMATED || tiers[i] === TIER_ESTIMATED) {
            continue;
          }
          const x = (xs.getPixelForValue(i - 1) + xs.getPixelForValue(i)) / 2;
          // Yield to the Today marker when the seam sits too close to it.
          if (Math.abs(x - todayX) < SEAM_TODAY_MIN_GAP_PX) continue;
          ctx.save();
          ctx.beginPath();
          ctx.setLineDash([2, 4]);
          ctx.lineWidth = 1;
          ctx.strokeStyle = color;
          ctx.moveTo(x, ys.top);
          ctx.lineTo(x, ys.bottom);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.fillStyle = color;
          ctx.font = "10px 'Inter', system-ui, sans-serif";
          ctx.textAlign = "left";
          ctx.textBaseline = "top";
          ctx.fillText("Tracking start", x + 4, ys.top + 2);
          ctx.restore();
        }
      }
    };
  }

  /**
   * Inline plugin: draw the "Equity" / "Debt" in-region identity labels
   * in secondary ink (Loop A lock item 1 -- color is never the only
   * signal).  A label whose region is too shallow at its x position is
   * skipped rather than clipped; the legend and tooltip still carry
   * identity.
   * @param {Array<number>} value - Market value series.
   * @param {Array<number>} debt - Secured debt series.
   * @returns {object} A Chart.js plugin.
   */
  function regionLabelsPlugin(value, debt) {
    return {
      id: "shekelEquityRegionLabels",
      afterDatasetsDraw: function (chart) {
        var xs = chart.scales.x;
        var ys = chart.scales.y;
        var ink = ShekelChart.getThemeColors().textSecondary;
        var ctx = chart.ctx;
        ctx.save();
        ctx.fillStyle = ink;
        ctx.font = "600 12px 'Inter', system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";

        var iEquity = Math.round((value.length - 1) * 0.62);
        var topE = ys.getPixelForValue(value[iEquity]);
        var botE = ys.getPixelForValue(debt[iEquity]);
        if (botE - topE >= LABEL_MIN_PX) {
          ctx.fillText("Equity", xs.getPixelForValue(iEquity), (topE + botE) / 2);
        }

        var iDebt = Math.round((value.length - 1) * 0.28);
        var topD = ys.getPixelForValue(debt[iDebt]);
        var botD = ys.getPixelForValue(0);
        if (botD - topD >= LABEL_MIN_PX) {
          ctx.fillText("Debt", xs.getPixelForValue(iDebt), (topD + botD) / 2);
        }
        ctx.restore();
      }
    };
  }

  /**
   * Build the full Chart.js config.  Reads the canvas data fresh each
   * call so a theme toggle rebuilds from current state.
   * @returns {object|null} A Chart.js config, or null when no canvas / data.
   */
  function buildConfig() {
    var canvas = document.getElementById(CANVAS_ID);
    if (!canvas) return null;
    var data = parseData(canvas);
    if (!data) return null;

    var noLoans = canvas.getAttribute("data-chart-state") === "no_loans";
    var todayIndex = data.today_index || 0;
    var tiers = data.debt_tier || [];
    var colors = ShekelChart.getThemeColors();
    var style = getComputedStyle(document.documentElement);
    var accent = style.getPropertyValue("--shekel-accent").trim();
    var danger = style.getPropertyValue("--shekel-danger").trim();
    var muted = style.getPropertyValue("--shekel-text-muted").trim();

    var datasets = [{
      label: "Market value",
      data: data.value,
      borderColor: accent,
      borderWidth: 2,
      tension: 0,
      pointRadius: 0,
      pointHoverRadius: 4,
      pointStyle: "line",
      segment: valueSegment(todayIndex, accent),
      fill: noLoans
        ? { target: "origin", above: ShekelChart.hexToRgba(accent, 0.10) }
        : { target: 1, above: ShekelChart.hexToRgba(accent, 0.10) }
    }];

    if (!noLoans) {
      datasets.push({
        label: "Secured debt",
        data: data.debt,
        borderColor: danger,
        borderWidth: 2,
        tension: 0,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointStyle: "line",
        segment: debtSegment(tiers, danger),
        fill: { target: "origin", above: ShekelChart.hexToRgba(danger, 0.10) }
      });
    }

    var plugins = [];
    if (!noLoans) {
      // No history -> no boundary to mark; todayMarkerPlugin also
      // guards internally, and the fallback's whole span is projection.
      plugins.push(ShekelChart.todayMarkerPlugin(todayIndex, colors.textSecondary));
      plugins.push(regionLabelsPlugin(data.value, data.debt));
      // The seam marker only draws when an estimated stretch is present
      // (a mid-life-imported loan); otherwise the loop no-ops.
      if (tiers.indexOf(TIER_ESTIMATED) !== -1) {
        plugins.push(trackingStartPlugin(tiers, todayIndex, muted));
      }
    }

    return {
      type: "line",
      data: { labels: data.labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          // The single-line fallback needs no legend (the band and
          // caption name it); the two-line chart always carries one.
          legend: {
            display: !noLoans,
            labels: { usePointStyle: true, boxHeight: 6 }
          },
          tooltip: {
            usePointStyle: true,
            callbacks: {
              label: function (ctx) {
                if (ctx.parsed.y === null || ctx.parsed.y === undefined) {
                  return null;
                }
                return ctx.dataset.label + ": " +
                  ShekelChart.formatMoney(ctx.parsed.y, false);
              },
              footer: function (items) {
                if (noLoans || !items.length) return [];
                var i = items[0].dataIndex;
                return ["Equity: " + ShekelChart.formatMoney(data.equity[i], false)];
              }
            }
          }
        },
        scales: {
          y: {
            beginAtZero: true,
            grid: {
              // Emphasize the zero line (payoff / full ownership); keep
              // other gridlines faint -- the loan band's convention.
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
      plugins: plugins
    };
  }

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
})();
