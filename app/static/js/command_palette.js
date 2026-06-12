/**
 * command_palette.js -- Ctrl+K command layer for the budget grid.
 *
 * C3 rebuild (decision 4, docs/design/grid_audit.md): a fuzzy palette
 * over the rendered grid.  The action index is derived from the grid
 * DOM itself -- every action surfaces something the server rendered
 * (a mark-paid check button, a data-can-credit stamp, an envelope
 * stamp, the anchor display) and executes through the exact same
 * controls and endpoints the on-grid buttons use.  No new mutation
 * surface, no client-side money math.
 *
 * Actions per visible window:
 *   - Mark paid  -- every projected cell (clicks its .paybtn)
 *   - Mark credit -- every credit-eligible cell (same endpoint as the
 *     card's Credit button)
 *   - Open       -- every transaction cell (opens the action card)
 *   - Add purchase -- envelope rows (opens the card, focuses the
 *     add-purchase amount input once the entries list loads)
 *   - Go to row  -- scrolls a row into view and sets the cell cursor
 *   - Update anchor balance -- opens the inline anchor editor
 *
 * Result rows are built with createElement/textContent, never
 * innerHTML, because labels carry user-named transactions.
 */
(function () {
    'use strict';

    var MAX_RESULTS = 8;

    var palette = null;
    var scrim = null;
    var input = null;
    var resultsEl = null;
    var countEl = null;

    var actions = [];
    var matches = [];
    var selected = 0;

    function gridTable() {
        return document.querySelector('.grid-table');
    }

    /** Period labels from the LAST thead row (the month band is first);
        each th wraps its date in a .fw-bold div, separate from the
        carry-forward button text. */
    function periodLabels() {
        var table = gridTable();
        if (!table) return [];
        var headerRow = table.querySelector('thead tr:last-child');
        if (!headerRow) return [];
        return Array.prototype.slice.call(headerRow.children, 1).map(function (th) {
            var label = th.querySelector('.fw-bold');
            return (label ? label.textContent : th.textContent).trim();
        });
    }

    /** Condensed visible amount text of a cell ("114 / 80", "1,850"). */
    function amountText(open) {
        return open.textContent.replace(/\s+/g, ' ').trim();
    }

    /** Scan the grid DOM and rebuild the action index. */
    function buildActions() {
        actions = [];
        var table = gridTable();
        if (!table) return;
        var headers = periodLabels();

        Array.prototype.forEach.call(table.querySelectorAll('tbody tr'), function (row) {
            var labelTh = row.querySelector('th.row-label');
            var cells = row.querySelectorAll('td.cell');
            if (!labelTh || !cells.length) return;
            var name = labelTh.textContent.trim();
            if (!name) return;

            var gotoCell = row.querySelector('td.cell.cur') || cells[0];
            // Every row action carries the bare row name as `subject` so
            // filter() can score the query against the name alone.  The
            // verb-first labels greedily eat the leading letters of names
            // starting with g/o/t/r/w ("go to row" vs Groceries, Rent,
            // Water...), so a label-only score buries "Go to row" below
            // every mutating action for exactly those queries.
            actions.push({
                kind: 'goto',
                subject: name,
                label: 'Go to row -- ' + name,
                meta: 'grid',
                run: makeGotoRunner(gotoCell),
            });

            var envelopeOpen = null;
            Array.prototype.forEach.call(cells, function (td, idx) {
                var open = td.querySelector('.txn-open[data-txn-id]');
                if (!open) return;
                var period = headers[idx] || '';
                var amount = amountText(open);

                var payBtn = td.querySelector('.paybtn');
                if (payBtn) {
                    actions.push({
                        kind: 'pay',
                        subject: name,
                        // Verb-first labels so the natural query ("pay
                        // gei") subsequence-matches from the start.
                        label: 'Pay -- ' + name + ' ' + amount,
                        meta: period,
                        run: makePayRunner(td, open.dataset.txnId),
                    });
                }

                if (open.dataset.canCredit) {
                    actions.push({
                        kind: 'credit',
                        subject: name,
                        label: 'Credit -- ' + name + ' ' + amount,
                        meta: period,
                        run: makeCreditRunner(td, open.dataset.txnId),
                    });
                }

                actions.push({
                    kind: 'open',
                    subject: name,
                    label: 'Open -- ' + name,
                    meta: period,
                    run: makeOpenRunner(td, open.dataset.txnId),
                });

                // Prefer the current period's cell for the per-row
                // add-purchase action.
                if (open.dataset.envelope
                    && (!envelopeOpen || td.classList.contains('cur'))) {
                    envelopeOpen = open;
                }
            });

            if (envelopeOpen) {
                actions.push({
                    kind: 'entry',
                    subject: name,
                    label: 'Add purchase -- ' + name,
                    meta: 'envelope',
                    run: makeAddPurchaseRunner(
                        envelopeOpen.closest('td'),
                        envelopeOpen.dataset.txnId),
                });
            }
        });

        if (document.getElementById('anchor-display')) {
            actions.push({
                kind: 'anchor',
                label: 'Update anchor balance',
                meta: 'account',
                run: function () {
                    var anchor = document.getElementById('anchor-display');
                    if (!anchor) return;
                    // The anchor display sits in the page header above
                    // the grid; reveal it before opening the inline
                    // editor so the edit does not happen off-screen.
                    anchor.scrollIntoView({ block: 'center' });
                    anchor.click();
                },
            });
        }
    }

    /** Scroll a grid cell to the center of every scrollport (the grid
        wrapper and, when the page itself scrolls, the window) BEFORE
        acting on it.  buildActions indexes every rendered tbody row --
        including rows far outside the wrapper's visible region -- so
        without this, Pay/Credit swap a cell off-screen with zero visible
        feedback and Open anchors the action card to an off-screen rect.
        Center alignment also lands the cell clear of the sticky
        header/footer/label-column chrome that overlays the scrollport
        edges. */
    function revealCell(td) {
        td.scrollIntoView({ block: 'center', inline: 'center' });
    }

    /** Re-resolve a cell control at RUN time from the cell's stable
        wrapper id.  The action index captures nodes when the palette
        opens; a swap landing before Enter (a mark-paid response, an
        entries OOB cell re-render) detaches them, and a click() on a
        detached node either no-ops silently (htmx guards on
        bodyContains) or never bubbles to the document-delegated
        handlers.  #txn-cell-<id> survives every swap shape -- both the
        innerHTML cell swaps and the OOB outerHTML re-render emit the
        same wrapper id. */
    function freshControl(txnId, selector) {
        var wrap = document.getElementById('txn-cell-' + txnId);
        return wrap ? wrap.querySelector(selector) : null;
    }

    function makeGotoRunner(td) {
        return function () {
            revealCell(td);
            // A bubbled click on the td (not on .txn-open) sets the
            // app.js cell cursor without opening the card.
            td.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        };
    }

    function makePayRunner(td, txnId) {
        return function () {
            revealCell(td);
            var btn = freshControl(txnId, '.paybtn');
            if (btn) btn.click();
        };
    }

    function makeOpenRunner(td, txnId) {
        return function () {
            revealCell(td);
            var openEl = freshControl(txnId, '.txn-open[data-txn-id]');
            if (openEl) openEl.click();
        };
    }

    function makeCreditRunner(td, txnId) {
        return function () {
            revealCell(td);
            htmx.ajax('POST', '/transactions/' + txnId + '/mark-credit', {
                target: '#txn-cell-' + txnId,
                swap: 'innerHTML',
            });
        };
    }

    /** Open the card on an envelope cell, then focus the add-purchase
        amount input once the lazy entries list has loaded (the add
        form is the last amount input in the popover). */
    function makeAddPurchaseRunner(td, txnId) {
        return function () {
            revealCell(td);
            var openEl = freshControl(txnId, '.txn-open[data-txn-id]');
            if (!openEl) return;
            openEl.click();
            var attempts = 0;
            var timer = setInterval(function () {
                attempts += 1;
                var popover = document.getElementById('txn-popover');
                var inputs = popover
                    ? popover.querySelectorAll('input[name="amount"]') : [];
                if (inputs.length) {
                    clearInterval(timer);
                    inputs[inputs.length - 1].focus();
                } else if (attempts > 30) {
                    clearInterval(timer);
                }
            }, 100);
        };
    }

    /** Subsequence fuzzy score; -1 = no match.  Consecutive runs and
        word starts score higher so "pay elec" beats scattered hits. */
    function score(query, text) {
        var q = query.toLowerCase();
        var t = text.toLowerCase();
        var ti = 0;
        var total = 0;
        var streak = 0;
        for (var qi = 0; qi < q.length; qi += 1) {
            var ch = q[qi];
            if (ch === ' ') { streak = 0; continue; }
            var found = t.indexOf(ch, ti);
            if (found === -1) return -1;
            total += 1;
            if (found === ti) { streak += 1; total += streak; }
            else { streak = 0; }
            if (found === 0 || t[found - 1] === ' ' || t[found - 1] === '-') total += 2;
            ti = found + 1;
        }
        return total;
    }

    var KIND_GLYPHS = {
        pay: '✓',
        credit: 'CC',
        open: '✎',
        goto: '⌖',
        entry: '+',
        anchor: '$',
    };

    /** Tie-break order for equal fuzzy scores.  A bare-name query ties
        every action of the matched row (they all hit via `subject`), so
        navigation must outrank the mutating actions: Enter on a typed
        row name must never silently mark a bill paid or open a card on
        it.  Verb-first queries ("pay gro") still rank Pay first because
        only its label matches, no tie. */
    var KIND_TIE_ORDER = {
        goto: 0,
        pay: 1,
        credit: 2,
        open: 3,
        entry: 4,
        anchor: 5,
    };
    // Below every known kind, so an action with an unmapped kind sorts
    // last instead of poisoning the comparator with NaN.
    var KIND_TIE_ORDER_FALLBACK = 99;

    function kindTieRank(action) {
        var rank = KIND_TIE_ORDER[action.kind];
        return rank === undefined ? KIND_TIE_ORDER_FALLBACK : rank;
    }

    function render() {
        resultsEl.textContent = '';
        if (!matches.length) {
            var empty = document.createElement('div');
            empty.className = 'cmdk-empty';
            empty.textContent = 'No matching commands in the visible window.';
            resultsEl.appendChild(empty);
            countEl.textContent = '';
            return;
        }
        matches.slice(0, MAX_RESULTS).forEach(function (action, idx) {
            var rowEl = document.createElement('div');
            rowEl.className = 'cmdk-row' + (idx === selected ? ' selected' : '');
            rowEl.setAttribute('role', 'option');
            rowEl.setAttribute('aria-selected', idx === selected ? 'true' : 'false');

            var icon = document.createElement('span');
            icon.className = 'cmdk-ic ' + action.kind;
            icon.textContent = KIND_GLYPHS[action.kind] || '·';

            var main = document.createElement('span');
            main.className = 'cmdk-main';
            main.textContent = action.label;

            var meta = document.createElement('span');
            meta.className = 'cmdk-meta';
            meta.textContent = action.meta;

            rowEl.appendChild(icon);
            rowEl.appendChild(main);
            rowEl.appendChild(meta);
            rowEl.addEventListener('click', function () { runAction(action); });
            resultsEl.appendChild(rowEl);
        });
        countEl.textContent = matches.length + ' match' + (matches.length === 1 ? '' : 'es');
    }

    function filter() {
        var query = input.value.trim();
        if (!query) {
            matches = actions.slice();
        } else {
            matches = actions
                .map(function (action) {
                    // Score the verb-first display string AND the bare
                    // row name, keeping the better hit.  Without the
                    // subject score, a name starting with g/o/t/r/w
                    // ("Rent", "Groceries"...) matches greedily inside
                    // the "Go to row" prefix, so navigation loses its
                    // streak bonuses and sinks below the row's ~12-18
                    // per-period Pay/Open entries -- out of the visible
                    // top results entirely.
                    var s = score(query, action.label + ' ' + action.meta);
                    if (action.subject) {
                        s = Math.max(s, score(query, action.subject));
                    }
                    return { action: action, s: s };
                })
                .filter(function (entry) { return entry.s >= 0; })
                .sort(function (a, b) {
                    if (b.s !== a.s) return b.s - a.s;
                    return kindTieRank(a.action) - kindTieRank(b.action);
                })
                .map(function (entry) { return entry.action; });
        }
        selected = 0;
        render();
    }

    function isOpen() {
        return palette && !palette.classList.contains('d-none');
    }

    function openPalette() {
        if (!palette || !gridTable()) return;
        buildActions();
        palette.classList.remove('d-none');
        scrim.classList.remove('d-none');
        input.value = '';
        filter();
        input.focus();
    }

    function closePalette() {
        if (!palette) return;
        palette.classList.add('d-none');
        scrim.classList.add('d-none');
        input.blur();
    }

    function runAction(action) {
        closePalette();
        action.run();
    }

    document.addEventListener('DOMContentLoaded', function () {
        palette = document.getElementById('cmdk');
        if (!palette) return;
        scrim = document.getElementById('cmdk-scrim');
        input = document.getElementById('cmdk-input');
        resultsEl = document.getElementById('cmdk-results');
        countEl = document.getElementById('cmdk-count');

        scrim.addEventListener('click', closePalette);

        var openBtn = document.getElementById('cmdk-open-btn');
        if (openBtn) openBtn.addEventListener('click', openPalette);

        input.addEventListener('input', filter);
        input.addEventListener('keydown', function (e) {
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selected = Math.min(selected + 1,
                    Math.min(matches.length, MAX_RESULTS) - 1);
                render();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selected = Math.max(selected - 1, 0);
                render();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (matches[selected]) runAction(matches[selected]);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                closePalette();
            }
        });
    });

    document.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
            if (!palette || !gridTable()) return;
            e.preventDefault();
            if (isOpen()) closePalette();
            else openPalette();
        }
    });
})();
