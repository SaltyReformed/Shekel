/**
 * Debt Strategy page -- custom priority toggle and order serialization.
 *
 * Shows/hides the custom priority section based on strategy radio
 * selection.  Before form submission, serializes the priority
 * dropdowns into the hidden custom_order input as a comma-separated
 * list of account IDs.
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
