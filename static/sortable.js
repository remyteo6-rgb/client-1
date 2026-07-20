// Tri cliquable des tableaux portant la classe "sortable" : clic sur un en-tête de
// colonne = tri décroissant, second clic = croissant. Le tri lit le premier nombre de
// chaque cellule (ex : "3 / 1 (75%)" est trié sur 3) et retombe sur un tri alphabétique
// pour la première colonne (noms). Dans les tableaux à double ligne d'en-tête (groupes
// ATTAQUE/DÉFENSE...), seule la ligne tr.cols-row est cliquable.
document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('table.sortable').forEach(function (table) {
        const colsRow = table.querySelector('thead tr.cols-row') || table.querySelector('thead tr:last-child');
        if (!colsRow) return;
        Array.from(colsRow.children).forEach(function (th, colIdx) {
            th.style.cursor = 'pointer';
            th.title = 'Cliquer pour trier';
            th.addEventListener('click', function () {
                const tbody = table.querySelector('tbody');
                const rows = Array.from(tbody.querySelectorAll('tr'));
                const desc = th.dataset.sortDir !== 'desc';
                Array.from(colsRow.children).forEach(function (h) {
                    delete h.dataset.sortDir;
                    h.textContent = h.textContent.replace(/ [▲▼]$/, '');
                });
                th.dataset.sortDir = desc ? 'desc' : 'asc';
                th.textContent += desc ? ' ▼' : ' ▲';
                rows.sort(function (a, b) {
                    const ta = (a.children[colIdx] ? a.children[colIdx].textContent : '').trim();
                    const tb = (b.children[colIdx] ? b.children[colIdx].textContent : '').trim();
                    const ma = ta.match(/-?\d+(\.\d+)?/);
                    const mb = tb.match(/-?\d+(\.\d+)?/);
                    let cmp;
                    if (colIdx === 0 || (!ma && !mb)) cmp = ta.localeCompare(tb, 'fr');
                    else if (!ma) cmp = -1;
                    else if (!mb) cmp = 1;
                    else cmp = parseFloat(ma[0]) - parseFloat(mb[0]);
                    return desc ? -cmp : cmp;
                });
                rows.forEach(function (r) { tbody.appendChild(r); });
            });
        });
    });
});
