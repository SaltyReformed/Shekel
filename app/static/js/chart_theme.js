'use strict';

/**
 * Shekel Budget App -- Chart.js Theme Layer
 *
 * Provides a shared configuration layer for all Chart.js charts.
 * Reads CSS custom properties for dark/light mode support, exposes
 * a ShekelChart.create() wrapper, and re-renders charts on theme toggle.
 *
 * create() takes a config FACTORY (a function returning a fresh config
 * object), not a prebuilt config: theme-dependent values such as
 * ShekelChart.getColor(...) resolve against the data-bs-theme attribute
 * at call time, so the factory runs once at creation and again on every
 * theme toggle, keeping dataset colors in sync with the active theme.
 * The factory's output is used directly (no clone), which also keeps
 * function-valued options -- tooltip and tick callbacks -- intact; the
 * previous object API JSON-cloned the config and silently stripped
 * them (css_architecture_audit.md, finding 1).
 *
 * @namespace ShekelChart
 */
// biome-ignore lint/correctness/noUnusedVariables: ShekelChart is a window-global namespace consumed cross-file by chart_*.js; biome parses each file as a module and cannot see the cross-<script> usage.
var ShekelChart = (function () {

  /**
   * 8-color palette with dark and light variants.
   * Each entry has a name, dark hex, and light hex.
   * @type {Array<{name: string, dark: string, light: string}>}
   */
  var palette = [
    { name: 'Accent',  dark: '#4A9ECC', light: '#2878A8' },
    { name: 'Green',   dark: '#2ECC71', light: '#1A9B50' },
    { name: 'Amber',   dark: '#E67E22', light: '#C96B15' },
    { name: 'Rose',    dark: '#D97BA0', light: '#B05A80' },
    { name: 'Teal',    dark: '#1ABC9C', light: '#148F77' },
    { name: 'Purple',  dark: '#9B59B6', light: '#7D3C98' },
    { name: 'Coral',   dark: '#E74C3C', light: '#C0392B' },
    { name: 'Slate',   dark: '#95A5A6', light: '#707B7C' }
  ];

  /** @type {Array<{id: string, instance: Chart, configFn: function}>} */
  var trackedCharts = [];

  /**
   * Detect the current Bootstrap theme.
   * @returns {string} 'dark' or 'light'
   */
  function currentTheme() {
    var attr = document.documentElement.getAttribute('data-bs-theme');
    return attr === 'light' ? 'light' : 'dark';
  }

  /**
   * Get a palette color for the current theme by index.
   * Wraps around if index exceeds palette length.
   * @param {number} index - Palette index (0-7).
   * @returns {string} Hex color string.
   */
  function getColor(index) {
    var entry = palette[index % palette.length];
    return currentTheme() === 'light' ? entry.light : entry.dark;
  }

  /**
   * Read CSS custom properties for theme-aware chart styling.
   * @returns {{textColor: string, gridColor: string, borderColor: string, fontFamily: string}}
   */
  function getThemeColors() {
    var style = getComputedStyle(document.documentElement);
    var theme = currentTheme();

    // Read CSS vars with fallbacks.
    var textColor = style.getPropertyValue('--shekel-text-primary').trim() ||
      (theme === 'dark' ? '#E2E6EB' : '#1E2228');
    var textSecondary = style.getPropertyValue('--shekel-text-secondary').trim() ||
      (theme === 'dark' ? '#B0B9C6' : '#3E4A5C');
    var borderSubtle = style.getPropertyValue('--shekel-border-subtle').trim() ||
      (theme === 'dark' ? '#3A4250' : '#D0D8E2');

    return {
      textColor: textColor,
      textSecondary: textSecondary,
      gridColor: theme === 'dark'
        ? 'rgba(255, 255, 255, 0.06)'
        : 'rgba(0, 0, 0, 0.08)',
      borderColor: borderSubtle,
      fontFamily: "'Inter', system-ui, -apple-system, sans-serif"
    };
  }

  /**
   * Apply theme defaults to Chart.js global configuration.
   */
  function applyGlobalDefaults() {
    var colors = getThemeColors();
    Chart.defaults.color = colors.textSecondary;
    Chart.defaults.borderColor = colors.borderColor;
    Chart.defaults.font.family = colors.fontFamily;
  }

  /**
   * Apply theme defaults to a freshly built Chart.js config.
   *
   * Mutates and returns the supplied config.  Safe because every
   * config arriving here was just produced by a create() factory for
   * this exact render -- there is no shared object to protect.  The
   * config is deliberately NOT cloned: a JSON round-trip would
   * silently strip function-valued options (tooltip and tick
   * callbacks), which is the defect that motivated the factory API.
   *
   * @param {object} config - Freshly built Chart.js config (single use).
   * @returns {object} The same config with theme defaults applied.
   */
  function mergeThemeDefaults(config) {
    var colors = getThemeColors();

    // Ensure options and nested objects exist.
    config.options = config.options || {};
    config.options.plugins = config.options.plugins || {};
    config.options.plugins.legend = config.options.plugins.legend || {};
    config.options.plugins.legend.labels = config.options.plugins.legend.labels || {};
    config.options.plugins.tooltip = config.options.plugins.tooltip || {};

    // Apply legend label color if the factory did not set one (the
    // ensure-exists steps above only create empty objects, so a color
    // present here can only have come from the factory).
    if (!config.options.plugins.legend.labels.color) {
      config.options.plugins.legend.labels.color = colors.textColor;
    }

    // Apply scale colors if scales exist and weren't fully customized.
    if (config.options.scales) {
      const scaleNames = Object.keys(config.options.scales);
      for (let i = 0; i < scaleNames.length; i++) {
        const scaleName = scaleNames[i];
        const scale = config.options.scales[scaleName];

        // Apply tick color.
        scale.ticks = scale.ticks || {};
        if (!scale.ticks.color) {
          scale.ticks.color = colors.textSecondary;
        }

        // Apply grid color.
        scale.grid = scale.grid || {};
        if (!scale.grid.color) {
          scale.grid.color = colors.gridColor;
        }
      }
    }

    return config;
  }

  /**
   * Create a Chart.js chart with theme defaults merged in.
   * Tracks the chart for re-rendering on theme change.
   *
   * Takes a config FACTORY, not a config object: theme-dependent
   * values (ShekelChart.getColor(...) and friends) must resolve at
   * render time, so the factory is invoked fresh here and again on
   * every theme toggle.  A prebuilt object would bake the creating
   * theme's colors into each re-render -- the stale-dataset-color
   * defect this API closes.  The factory must return a NEW config
   * object on every call (a literal in the function body does this
   * naturally).
   *
   * @param {string} canvasId - The ID of the canvas element.
   * @param {function(): object} buildConfig - Returns a fresh Chart.js
   *   config; called once now and once per theme change.
   * @returns {Chart|null} The Chart instance, or null if canvas not found.
   */
  function create(canvasId, buildConfig) {
    if (typeof buildConfig !== 'function') {
      throw new TypeError(
        'ShekelChart.create("' + canvasId + '") expects a config ' +
        'factory function, got ' + typeof buildConfig + '. Wrap the ' +
        'config literal in a function so theme colors re-resolve on ' +
        'theme toggle.'
      );
    }

    var canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    // Destroy any existing tracked chart on the same canvas.
    destroyById(canvasId);

    applyGlobalDefaults();
    var instance = new Chart(canvas, mergeThemeDefaults(buildConfig()));

    // Track the factory itself so rerenderAll() rebuilds the config
    // with freshly resolved theme colors.
    trackedCharts.push({
      id: canvasId,
      instance: instance,
      configFn: buildConfig
    });

    return instance;
  }

  /**
   * Destroy a tracked chart by canvas ID.
   * @param {string} canvasId - The canvas element ID.
   */
  function destroyById(canvasId) {
    for (let i = trackedCharts.length - 1; i >= 0; i--) {
      if (trackedCharts[i].id === canvasId) {
        trackedCharts[i].instance.destroy();
        trackedCharts.splice(i, 1);
      }
    }
  }

  /**
   * Re-render all tracked charts with updated theme colors.
   * Called automatically on theme toggle.  Also evicts charts whose
   * canvas has left the DOM (e.g. an HTMX tab swap replaced #tab-content):
   * the orphaned instance is destroyed and dropped, so trackedCharts
   * cannot grow unbounded across repeated toggles (JS-08).
   */
  function rerenderAll() {
    applyGlobalDefaults();

    var live = [];
    for (let i = 0; i < trackedCharts.length; i++) {
      const entry = trackedCharts[i];
      const canvas = document.getElementById(entry.id);
      if (!canvas) {
        // Canvas removed from the DOM: destroy the orphaned instance
        // (frees its data arrays + detached canvas) and drop the entry.
        entry.instance.destroy();
        continue;
      }

      // Rebuild the config from the factory so theme-dependent
      // colors (getColor, getThemeColors) re-resolve against the
      // now-active theme, then re-merge the global defaults.
      const merged = mergeThemeDefaults(entry.configFn());

      // Destroy old instance and create new one.
      entry.instance.destroy();
      entry.instance = new Chart(canvas, merged);
      live.push(entry);
    }
    trackedCharts = live;
  }

  // Listen for theme change events.
  document.addEventListener('shekel:theme-changed', function () {
    rerenderAll();
  });

  /**
   * Format a number as a US-dollar display string for axis ticks and
   * tooltips.  Negatives put the sign BEFORE the dollar symbol
   * ("-$1,234", not "$-1,234"), mirroring the Jinja money macro
   * (_money_macros.html).  Display formatting only -- the value must
   * already be a final number; no monetary computation happens here.
   *
   * @param {number} value - Numeric dollar amount.
   * @param {boolean} cents - true for exactly 2 fraction digits
   *   ("-$112.40"), false for whole dollars ("-$1,234").
   * @returns {string} Formatted dollar string.
   */
  function formatMoney(value, cents) {
    var digits = cents ? 2 : 0;
    var sign = value < 0 ? '-' : '';
    return sign + '$' + Math.abs(value).toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits
    });
  }

  // Public API.
  return {
    getColor: getColor,
    getThemeColors: getThemeColors,
    create: create,
    destroyById: destroyById,
    rerenderAll: rerenderAll,
    formatMoney: formatMoney
  };
})();
