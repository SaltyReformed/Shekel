/**
 * Debt Strategy page.
 *
 * 1. Custom priority toggle: shows/hides the custom priority section
 *    based on strategy radio selection.
 * 2. Order serialization: before form submission, serializes the
 *    priority dropdowns into the hidden custom_order input.
 * 3. Balance chart: renders a multi-line Chart.js chart from data-*
 *    attributes after HTMX swaps in the results partial.
 */
document.addEventListener("DOMContentLoaded", function () {
    var radios = document.querySelectorAll("[data-strategy-radio]");
    var customSection = document.getElementById("custom-priority-section");
    var form = document.getElementById("strategy-form");
    var orderInput = document.getElementById("custom-order-input");

    if (!radios.length || !customSection || !form || !orderInput) {
        return;
    }

    // Toggle custom priority section visibility.
    function updateCustomVisibility() {
        var selected = document.querySelector("[data-strategy-radio]:checked");
        if (selected && selected.value === "custom") {
            customSection.style.display = "";
        } else {
            customSection.style.display = "none";
        }
    }

    for (var i = 0; i < radios.length; i++) {
        radios[i].addEventListener("change", updateCustomVisibility);
    }
    updateCustomVisibility();

    // Before HTMX sends the form, serialize the custom order.
    document.body.addEventListener("htmx:configRequest", function (evt) {
        if (evt.detail.elt !== form && !form.contains(evt.detail.elt)) {
            return;
        }

        var selected = document.querySelector("[data-strategy-radio]:checked");
        if (!selected || selected.value !== "custom") {
            orderInput.value = "";
            return;
        }

        // Build priority -> account_id map from the select dropdowns.
        var selects = document.querySelectorAll("[data-custom-priority]");
        var entries = [];
        for (var j = 0; j < selects.length; j++) {
            entries.push({
                priority: parseInt(selects[j].value, 10),
                accountId: selects[j].dataset.accountId
            });
        }

        // Sort by priority number and extract account IDs.
        entries.sort(function (a, b) { return a.priority - b.priority; });
        var ids = [];
        for (var k = 0; k < entries.length; k++) {
            ids.push(entries[k].accountId);
        }
        orderInput.value = ids.join(",");
    });
});

/**
 * Render the multi-line balance-over-time chart.
 *
 * Reads JSON from the canvas data-chart-data attribute (set by the
 * route via json.dumps -- no Decimals, only floats).  Uses the
 * ShekelChart theme layer for consistent styling.
 *
 * @param {string} canvasId - The canvas element ID.
 */
function renderStrategyChart(canvasId) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) return;

    var raw = canvas.getAttribute("data-chart-data");
    if (!raw) return;

    var chartData;
    try {
        chartData = JSON.parse(raw);
    } catch (e) {
        return;
    }

    if (!chartData.labels || !chartData.datasets || chartData.datasets.length === 0) {
        return;
    }

    // Build Chart.js datasets from the serialized data, assigning
    // colors from the ShekelChart palette by index.
    var datasets = [];
    for (var i = 0; i < chartData.datasets.length; i++) {
        var ds = chartData.datasets[i];
        var color = ShekelChart.getColor(ds.colorIndex != null ? ds.colorIndex : i);
        datasets.push({
            label: ds.label,
            data: ds.data,
            borderColor: color,
            backgroundColor: "transparent",
            borderWidth: 2,
            fill: false,
            tension: 0.3,
            pointRadius: 0
        });
    }

    ShekelChart.create(canvasId, {
        type: "line",
        data: {
            labels: chartData.labels,
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: "index",
                intersect: false
            },
            scales: {
                x: {
                    display: true,
                    ticks: { maxTicksLimit: 12 }
                },
                y: {
                    display: true,
                    beginAtZero: true,
                    ticks: {
                        callback: function (value) {
                            return "$" + value.toLocaleString();
                        }
                    }
                }
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return context.dataset.label + ": $" +
                                context.parsed.y.toLocaleString(undefined, {
                                    minimumFractionDigits: 0,
                                    maximumFractionDigits: 0
                                });
                        }
                    }
                }
            }
        }
    });
}

// Render chart after HTMX swaps in the results partial.
document.addEventListener("htmx:afterSwap", function () {
    if (document.getElementById("strategy-chart")) {
        renderStrategyChart("strategy-chart");
    }
});
