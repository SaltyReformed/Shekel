/**
 * Account types settings -- conditional field visibility and edit/cancel toggle.
 */
document.addEventListener("DOMContentLoaded", function () {
  // Category-based flag visibility.
  function updateFlagVisibility(form) {
    var select = form.querySelector(".acct-type-category");
    if (!select) return;
    var catName = select.options[select.selectedIndex]
                  ? select.options[select.selectedIndex].text.trim()
                  : "";
    form.querySelectorAll(".acct-type-flag[data-show-for-category]").forEach(function (el) {
      el.style.display = el.getAttribute("data-show-for-category") === catName ? "" : "none";
    });
    // Amortization-dependent fields.
    var amortCb = form.querySelector(".acct-type-amort-cb");
    var amortChecked = amortCb && amortCb.checked;
    form.querySelectorAll(".acct-type-flag[data-show-for-flag='has_amortization']").forEach(function (el) {
      el.style.display = amortChecked ? "" : "none";
    });
  }

  // Bind to category dropdowns and amortization checkboxes.
  document.querySelectorAll(".acct-type-category").forEach(function (select) {
    var form = select.closest("form");
    select.addEventListener("change", function () { updateFlagVisibility(form); });
    updateFlagVisibility(form);
  });
  document.querySelectorAll(".acct-type-amort-cb").forEach(function (cb) {
    var form = cb.closest("form");
    cb.addEventListener("change", function () { updateFlagVisibility(form); });
  });

  // Edit/cancel toggle for existing account type rows.
  document.querySelectorAll(".edit-type-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var li = btn.closest("li");
      li.querySelector(".account-type-display").classList.add("d-none");
      li.querySelector(".account-type-edit").classList.remove("d-none");
      btn.classList.add("d-none");
      var form = li.querySelector(".account-type-form");
      updateFlagVisibility(form);
    });
  });
  document.querySelectorAll(".cancel-type-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var li = btn.closest("li");
      li.querySelector(".account-type-display").classList.remove("d-none");
      li.querySelector(".account-type-edit").classList.add("d-none");
      li.querySelector(".edit-type-btn").classList.remove("d-none");
    });
  });
});
