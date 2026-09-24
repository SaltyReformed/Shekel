/**
 * New-account form -- ask the balance in the words of the type picked.
 *
 * Plan step credit_card:CC-5-5b, ruling R-CC58.  A loan or card's box asks
 * for the amount OWED (the create route stores the held sign by the type,
 * ruling R-CC52, whatever the label says); every other type's box asks for
 * the opening balance.  The server marks each liability type's <option>
 * with ``data-asks-owed`` and puts both wordings on the field as data
 * attributes, so this script only chooses between them -- it states no
 * rule of its own.  With scripts off, the field keeps its server-rendered
 * label and the help naming both cases.
 */
(function () {
  var field = document.getElementById("opening-balance-field");
  var typeSelect = document.getElementById("account_type_id");
  if (!field || !typeSelect) return;
  var label = document.getElementById("anchor-label");
  var help = document.getElementById("anchor-help");

  function asksOwed() {
    var opt = typeSelect.options[typeSelect.selectedIndex];
    return opt ? opt.hasAttribute("data-asks-owed") : false;
  }

  function relabel() {
    var owed = asksOwed();
    label.textContent = owed ? field.dataset.owedLabel : field.dataset.heldLabel;
    help.textContent = owed ? field.dataset.owedHelp : field.dataset.heldHelp;
  }

  typeSelect.addEventListener("change", relabel);
  relabel();
})();
