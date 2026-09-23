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
            input.style.display = '';
            toggle.style.display = 'none';
            input.focus();
        }
    });
})();
