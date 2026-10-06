// Notation rapide par clic pour les points d'étape P.P.I.D (rugby / physique) : un
// bouton par niveau au lieu d'un menu déroulant, et un commentaire replié tant qu'on
// n'en a pas besoin. Délégation d'événements sur tout le document : marche pour les
// formulaires d'ajout ET d'édition, y compris ceux ajoutés/affichés après coup (détails
// dépliés), sans avoir à ré-attacher quoi que ce soit.
(function () {
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.ppid-rate-btn');
        if (btn) {
            var group = btn.closest('.ppid-rate');
            if (!group) return;
            group.querySelectorAll('.ppid-rate-btn').forEach(function (b) {
                b.classList.remove('is-active');
            });
            btn.classList.add('is-active');
            var hidden = group.querySelector('input[type="hidden"]');
            if (hidden) hidden.value = btn.dataset.value;
            return;
        }
        var toggle = e.target.closest('.ppid-comment-toggle');
        if (toggle) {
            var group2 = toggle.closest('.ppid-rate');
            if (!group2) return;
            var input = group2.querySelector('.ppid-comment-input');
            if (!input) return;
            var wrap = group2.querySelector('.ppid-comment-wrap');
            if (wrap) wrap.style.display = '';
            input.style.display = '';
            toggle.style.display = 'none';
            input.focus();
        }
    });
})();

// Couleur d'écriture d'un texte saisi (voir color_pick dans _ppid_rate.html) : un clic sur
// une pastille enregistre la couleur dans le champ caché et colore aussitôt le texte du champ
// voisin (.txtc-target dans le même .txtc-scope), pour voir le rendu avant d'enregistrer.
(function () {
    var COLORS = ['noir', 'rouge', 'vert', 'bleu', 'orange', 'violet'];
    document.addEventListener('click', function (e) {
        var dot = e.target.closest('.txtc-dot');
        if (!dot) return;
        e.preventDefault();
        var pick = dot.closest('.txtc-pick');
        if (!pick) return;
        var color = dot.dataset.color;
        pick.querySelectorAll('.txtc-dot').forEach(function (b) { b.classList.toggle('is-active', b === dot); });
        var hidden = pick.querySelector('input[type="hidden"]');
        if (hidden) hidden.value = color === 'noir' ? '' : color;
        var scope = pick.closest('.txtc-scope');
        if (!scope) return;
        scope.querySelectorAll('.txtc-target').forEach(function (t) {
            COLORS.forEach(function (c) { t.classList.remove('txtc-' + c); });
            t.classList.toggle('txtc', color !== 'noir');
            if (color !== 'noir') t.classList.add('txtc-' + color);
        });
    });
})();
