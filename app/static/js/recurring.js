/**
 * Shekel Budget App -- Recurring surface interactions (Loop B, P2).
 *
 * Progressive enhancement for the unified /templates page. Three jobs, none
 * of which touch money:
 *
 *   1. Unit toggle: flip the Monthly / Per-paycheck button's active state
 *      instantly on click. HTMX does the actual figure swap (it re-renders
 *      #recurring-body server-side in the chosen unit), so this only keeps
 *      the button -- which lives outside the swapped region -- in sync.
 *   2. Search / kind filter / sort: hide, reveal, and reorder the section
 *      rows client-side. Sorting parses the data-sort-* keys only to ORDER
 *      rows; the displayed amounts are the untouched server-rendered
 *      strings, never recomputed here.
 *   3. Re-apply (1's swap replaces the rows) on htmx:afterSwap so an active
 *      search / filter / sort survives a unit toggle.
 *
 * No-ops on every other page (the [data-recurring-page] root is absent).
 */

(function () {
  "use strict";

  const page = document.querySelector("[data-recurring-page]");
  if (!page) {
    return;
  }

  const searchInput = page.querySelector("[data-recurring-search]");
  const sortSelect = page.querySelector("[data-recurring-sort]");
  const filterButtons = Array.prototype.slice.call(
    page.querySelectorAll("[data-recurring-filter]"),
  );
  const unitButtons = Array.prototype.slice.call(
    page.querySelectorAll("[data-unit-value]"),
  );

  /** The kind of the currently active filter pill (default "all"). */
  function activeFilter() {
    for (let i = 0; i < filterButtons.length; i++) {
      if (filterButtons[i].classList.contains("active")) {
        return filterButtons[i].getAttribute("data-recurring-filter");
      }
    }
    return "all";
  }

  /** Case-folded, trimmed search query ("" when the field is empty). */
  function searchQuery() {
    return searchInput ? searchInput.value.trim().toLowerCase() : "";
  }

  /** The selected sort key ("monthly" default). */
  function sortKey() {
    return sortSelect ? sortSelect.value : "monthly";
  }

  /**
   * Order two rows by the chosen key. Name is ascending A-Z; next date is
   * ascending with undated rows last; monthly cost and defined amount are
   * descending with missing / non-numeric values last. Numeric keys are
   * parsed only for comparison, not for display.
   *
   * @param {Element} a
   * @param {Element} b
   * @param {string} key  "monthly" | "amount" | "name" | "next"
   * @returns {number}
   */
  function compareRows(a, b, key) {
    if (key === "name") {
      return (a.getAttribute("data-sort-name") || "").localeCompare(
        b.getAttribute("data-sort-name") || "",
      );
    }
    if (key === "next") {
      const an = a.getAttribute("data-sort-next") || "";
      const bn = b.getAttribute("data-sort-next") || "";
      if (an === bn) { return 0; }
      if (an === "") { return 1; }
      if (bn === "") { return -1; }
      return an < bn ? -1 : 1;
    }
    const attr = key === "amount" ? "data-sort-amount" : "data-sort-monthly";
    const av = parseFloat(a.getAttribute(attr));
    const bv = parseFloat(b.getAttribute(attr));
    const aok = Number.isFinite(av);
    const bok = Number.isFinite(bv);
    if (!aok && !bok) { return 0; }
    if (!aok) { return 1; }
    if (!bok) { return -1; }
    return bv - av;
  }

  /** Reorder one section's rows in place by the chosen key. */
  function sortSection(section, key) {
    const tbody = section.querySelector("tbody");
    if (!tbody) {
      return;
    }
    const rows = Array.prototype.slice.call(
      tbody.querySelectorAll("[data-recurring-row]"),
    );
    rows.sort(function (a, b) {
      return compareRows(a, b, key);
    });
    rows.forEach(function (row) {
      tbody.appendChild(row);
    });
  }

  /**
   * Apply the current search, filter, and sort to the section rows. A row
   * is hidden when its name does not contain the query; a section is hidden
   * when the filter excludes its kind or the search left it with no matches.
   * The "no matches" note shows only when definitions exist but all are
   * hidden.
   */
  function applyView() {
    const query = searchQuery();
    const filter = activeFilter();
    const key = sortKey();
    const body = page.querySelector("#recurring-body");
    if (!body) {
      return;
    }
    const sections = Array.prototype.slice.call(
      body.querySelectorAll("[data-recurring-section]"),
    );
    let anyVisible = false;

    sections.forEach(function (section) {
      const kind = section.getAttribute("data-recurring-section");
      const kindMatch = filter === "all" || filter === kind;
      const rows = Array.prototype.slice.call(
        section.querySelectorAll("[data-recurring-row]"),
      );
      let matchCount = 0;
      rows.forEach(function (row) {
        const name = row.getAttribute("data-sort-name") || "";
        const hit = query === "" || name.indexOf(query) !== -1;
        row.classList.toggle("d-none", !hit);
        if (hit) {
          matchCount++;
        }
      });
      sortSection(section, key);
      const visible = kindMatch && matchCount > 0;
      section.classList.toggle("d-none", !visible);
      if (visible) {
        anyVisible = true;
      }
    });

    const noMatch = body.querySelector("[data-recurring-nomatch]");
    if (noMatch) {
      noMatch.classList.toggle("d-none", anyVisible || sections.length === 0);
    }
  }

  /** Set the active filter pill by kind. */
  function setActiveFilter(kind) {
    filterButtons.forEach(function (btn) {
      btn.classList.toggle(
        "active", btn.getAttribute("data-recurring-filter") === kind,
      );
    });
  }

  /**
   * Disable the kind pill for a section that has no definitions, so a filter
   * can never land on an empty view. If the disabled pill was active, fall
   * back to All.
   */
  function syncFilterAvailability() {
    const body = page.querySelector("#recurring-body");
    let fellBack = false;
    filterButtons.forEach(function (btn) {
      const kind = btn.getAttribute("data-recurring-filter");
      if (kind === "all") {
        return;
      }
      const section = body
        ? body.querySelector('[data-recurring-section="' + kind + '"]')
        : null;
      const has = Boolean(section && section.querySelector("[data-recurring-row]"));
      btn.disabled = !has;
      if (!has && btn.classList.contains("active")) {
        fellBack = true;
      }
    });
    if (fellBack) {
      setActiveFilter("all");
    }
  }

  // ---- Wiring. Search / sort / filter controls live in the stable toolbar
  // (outside the swapped body), so direct listeners survive a unit swap. ----
  if (searchInput) {
    searchInput.addEventListener("input", applyView);
  }
  if (sortSelect) {
    sortSelect.addEventListener("change", applyView);
  }
  filterButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (btn.disabled) {
        return;
      }
      setActiveFilter(btn.getAttribute("data-recurring-filter"));
      applyView();
    });
  });

  // Unit toggle: reflect the click immediately; HTMX swaps the figures.
  unitButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      unitButtons.forEach(function (other) {
        const active = other === btn;
        other.classList.toggle("btn-primary", active);
        other.classList.toggle("btn-outline-primary", !active);
      });
    });
  });

  // After the live unit swap the rows are fresh nodes: re-apply the current
  // view and re-check which kind pills are available.
  document.body.addEventListener("htmx:afterSwap", function (event) {
    const detail = event && event.detail ? event.detail : {};
    const target = detail.target || event.target;
    if (target && target.id === "recurring-body") {
      syncFilterAvailability();
      applyView();
    }
  });

  // Initial pass on first render.
  syncFilterAvailability();
  applyView();
})();
