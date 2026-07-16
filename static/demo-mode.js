// Mode démo : permet de flouter les noms de joueurs à l'écran pour une présentation
// (ex : montrer l'outil à un club potentiel sans révéler les vrais noms des joueurs).
// Rien n'est modifié dans les données : c'est purement visuel, activé/désactivé via
// le bouton "Mode démo" dans le menu ☰. L'état est mémorisé (localStorage), donc il
// reste actif quand on change de page.

// Colonnes à flouter en mode démo : noms de joueurs, et types de "plan de jeu" (relance)
// qui sont des appellations tactiques propres au club, pas juste des identités.
const DEMO_BLUR_HEADERS = ['Joueur', 'Plan de jeu'];

function markPlayerNameCells() {
    document.querySelectorAll('table').forEach(function (table) {
        const headerCells = table.querySelectorAll('thead th');
        let nameColIndex = -1;
        headerCells.forEach(function (th, i) {
            if (DEMO_BLUR_HEADERS.includes(th.textContent.trim())) nameColIndex = i;
        });
        if (nameColIndex === -1) return;
        table.querySelectorAll('tbody tr').forEach(function (row) {
            const cell = row.children[nameColIndex];
            if (cell) cell.classList.add('demo-blur');
        });
    });
    document.querySelectorAll('select[name="player"], select[name="a"], select[name="b"]').forEach(function (s) {
        s.classList.add('demo-blur');
    });
}

function applyDemoMode() {
    const on = localStorage.getItem('demoMode') === '1';
    document.body.classList.toggle('demo-mode', on);
    const btn = document.getElementById('demoModeToggle');
    if (btn) btn.textContent = on ? '👁️ Désactiver le mode démo' : '🙈 Mode démo (flouter les noms)';
}

function toggleDemoMode() {
    const on = localStorage.getItem('demoMode') === '1';
    localStorage.setItem('demoMode', on ? '0' : '1');
    applyDemoMode();
}

document.addEventListener('DOMContentLoaded', function () {
    markPlayerNameCells();
    applyDemoMode();
});
