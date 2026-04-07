'use strict';

/**
 * Shekel Budget App -- Chart.js Theme Layer
 *
 * Provides a shared configuration layer for all Chart.js charts.
 * Reads CSS custom properties for dark/light mode support, exposes
 * a ShekelChart.create() wrapper, and re-renders charts on theme toggle.
 *
 * @namespace ShekelChart
 */
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
   * Build deep-merged options with theme defaults applied.
   * @param {object} userConfig - User-provided Chart.js config.
   * @returns {object} Merged config with theme defaults.
   */
  function mergeThemeDefaults(userConfig) {
    var colors = getThemeColors();
    var config = JSON.parse(JSON.stringify(userConfig));

    // Ensure options and nested objects exist.
    config.options = config.options || {};
    config.options.plugins = config.options.plugins || {};
    config.options.plugins.legend = config.options.plugins.legend || {};
    config.options.plugins.legend.labels = config.options.plugins.legend.labels || {};
    config.options.plugins.tooltip = config.options.plugins.tooltip || {};

    // Apply legend label color if not set.
    if (!userConfig.options || !userConfig.options.plugins ||
        !userConfig.options.plugins.legend ||
        !userConfig.options.plugins.legend.labels ||
        !userConfig.options.plugins.legend.labels.color) {
      config.options.plugins.legend.labels.color = colors.textColor;
    }

    // Apply scale colors if scales exist and weren't fully customized.
    if (config.options.scales) {
      var scaleNames = Object.keys(config.options.scales);
      for (var i = 0; i < scaleNames.length; i++) {
        var scaleName = scaleNames[i];
        var scale = config.options.scales[scaleName];

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
   * Tracks the instance for re-rendering on theme change.
   *
   * @param {string} canvasId - The ID of the canvas element.
   * @param {object} config - Chart.js configuration object.
   * @returns {Chart|null} The Chart instance, or null if canvas not found.
   */
  function create(canvasId, config) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    // Destroy any existing tracked chart on the same canvas.
    destroyById(canvasId);

    applyGlobalDefaults();
    var merged = mergeThemeDefaults(config);
    var instance = new Chart(canvas, merged);

    // Store a config factory for re-creation on theme change.
    trackedCharts.push({
      id: canvasId,
      instance: instance,
      configFn: function () { return config; }
    });

    return instance;
  }

  /**
   * Destroy a tracked chart by canvas ID.
   * @param {string} canvasId - The canvas element ID.
   */
  function destroyById(canvasId) {
    for (var i = trackedCharts.length - 1; i >= 0; i--) {
      if (trackedCharts[i].id === canvasId) {
        trackedCharts[i].instance.destroy();
        trackedCharts.splice(i, 1);
      }
    }
  }

  /**
   * Destroy all tracked chart instances.
   * Call before full page unload or HTMX full page swap.
   */
  function destroyAll() {
    for (var i = 0; i < trackedCharts.length; i++) {
      trackedCharts[i].instance.destroy();
    }
    trackedCharts = [];
  }

  /**
   * Re-render all tracked charts with updated theme colors.
   * Called automatically on theme toggle.
   */
  function rerenderAll() {
    applyGlobalDefaults();

    for (var i = 0; i < trackedCharts.length; i++) {
      var entry = trackedCharts[i];
      var canvas = document.getElementById(entry.id);
      if (!canvas) continue;

      // Get the original config and re-merge with new theme colors.
      var originalConfig = entry.configFn();
      var merged = mergeThemeDefaults(originalConfig);

      // Destroy old instance and create new one.
      entry.instance.destroy();
      entry.instance = new Chart(canvas, merged);
    }
  }

  // Listen for theme change events.
  document.addEventListener('shekel:theme-changed', function () {
    rerenderAll();
  });

  /**
   * Build a Chart.js x-axis ticks callback for year-boundary awareness.
   *
   * When labels span multiple years (detected by the "'YY" suffix that
   * the Python chart_data_service adds), the callback shows the year
   * suffix only on the first tick and at each January boundary.  This
   * avoids cluttering every tick with the year while still providing
   * orientation.  When labels do not span years, returns null so the
   * caller can skip applying a custom callback.
   *
   * @param {string[]} labels - Array of label strings from the chart data.
   * @returns {function|null} A ticks.callback function, or null if not needed.
   */
  function yearBoundaryCallback(labels) {
    if (!labels || labels.length === 0) return null;

    // Detect whether any label has the 'YY suffix (e.g. "Jan 02 '26").
    var hasYearSuffix = false;
    for (var i = 0; i < labels.length; i++) {
      if (/'\d{2}$/.test(labels[i])) {
        hasYearSuffix = true;
        break;
      }
    }
    if (!hasYearSuffix) return null;

    return function (value, index) {
      var label = labels[index] || '';
      var yearMatch = label.match(/'(\d{2})$/);
      if (!yearMatch) return label;

      var base = label.replace(/\s*'\d{2}$/, '');

      // Always show year on the first tick.
      if (index === 0) return label;

      // Show year at January boundaries.
      if (/^Jan\b/.test(label)) return label;

      // Otherwise strip the year suffix to reduce clutter.
      return base;
    };
  }

  // Public API.
  return {
    palette: palette,
    getColor: getColor,
    getThemeColors: getThemeColors,
    create: create,
    destroyById: destroyById,
    destroyAll: destroyAll,
    rerenderAll: rerenderAll,
    yearBoundaryCallback: yearBoundaryCallback
  };
})();
