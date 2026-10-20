"""Barème de notation des joueurs (note sur 10, par poste).

Principe : chaque critère du barème donne une sous-note de 0 à 10 ; la note de base est la
moyenne pondérée des sous-notes disponibles (un critère sans assez d'actions est ignoré et
son poids est redistribué) ; puis on retire les malus (pénalités, cartons, ballons perdus) et
on ajoute les bonus (essais). Tout est ramené à 80 minutes de jeu pour les volumes.

Deux types de critères :
  - "vol" : valeur par 80 min ; la cible = la valeur qui vaut 10/10.
  - "pct" : réussite en %, de `plancher` (0/10) à `cible` (10/10) ; ignoré sous `min_n` actions.
Les poids et les cibles ci-dessous sont les valeurs de départ ; elles se règlent dans
Saison → Notes → Barème (valeurs enregistrées en base, prioritaires)."""

MIN_MINUTES = 30      # en dessous, le joueur n'est pas noté sur le match
MALUS = {"penalite": 0.3, "penalite_max": 1.5, "jaune": 0.7, "rouge": 2.0, "pdb": 0.05, "pdb_max": 0.5, "total_max": 3.0}
# Passes ratées : réussir une passe est normal (aucun bonus), la rater coûte, surtout aux 9/10.
PASSES_RATEES = {"9": (0.4, 2.5), "10": (0.4, 2.5), "12-13": (0.15, 0.75)}
PASSES_RATEES_DEFAUT = (0.1, 0.5)
BONUS = {"essai": 1.0, "essai_max": 3.0}
# Les ailiers marquent : leurs essais rapportent plus (20 oct. 2026).
BONUS_ESSAI_POSTE = {"11-14": (1.5, 4.5), "15": (1.5, 4.5)}
# Duels aériens des ailiers/arrière : jouer et gagner rapporte, ne pas en avoir à jouer ne coûte rien
# (le nombre dépend des coups de pied adverses contestables). +0,3 par duel gagné, +0,15 par duel neutre.
# Ballons récupérés (20 oct. 2026) : un bonus pour tous les postes, jamais un malus quand il n'y en a pas.
BONUS_RECUP = {"point": 0.3, "max": 1.0}
BONUS_AERIEN = {"gagne": 0.3, "neutre": 0.15, "max": 1.0, "profils": ("11-14", "15")}
# Les demis (9, 10) sont moins jugés sur le plaquage que les avants et les centres : malus de plaquage réduits.
PLAQUAGE_FACTEUR = {"9": 0.5, "10": 0.5}
PLAQUAGE_RATE = (0.15, 1.0)
PLAQUAGE_PASSIF = (0.1, 0.5)  # plaquage passif subi : malus léger
PLAQUAGE_HAUT = (0.1, 0.5)    # plaquage haut : malus léger (risque)   # malus par plaquage raté, plafond par match
CONFIANCE_MIN = 60            # minutes jouées pour que la note soit pleinement « crue »
NOTE_NEUTRE = 5.0
SHRINK_MIN = 20               # volumes : minutes « virtuelles » à demi-cible ajoutées, pour ne pas extrapoler un court passage
SHRINK_K = 4                  # un pourcentage est tiré vers la moyenne attendue tant qu'il repose sur peu d'actions



def _v(row, *keys):
    return sum((row or {}).get(k) or 0 for k in keys)


def _pct_expl_auto(ruck, g, p, ga, pa, min_n):
    """Réussite d'après les descripteurs tagués à la main (g/p) s'il y en a assez, sinon d'après
    la réussite chronologique calculée (ga/pa). Renvoie (pourcentage, actions) ou (None, 0)."""
    for gg, pp in ((g, p), (ga, pa)):
        gagnes, perdus = _v(ruck, gg) if gg else 0, _v(ruck, pp) if pp else 0
        if gagnes + perdus >= min_n:
            return 100 * gagnes / (gagnes + perdus), gagnes + perdus
    return None, 0


# Extracteurs : (lignes joueur du match) -> (valeur brute | None, nb d'actions). Pour "vol" la valeur est
# le total du match (ramené à 80 min ensuite).
def _x_contact(r):      return _v(r["attack"], "contact_total"), 0
def _x_offload(r):      return _v(r["attack"], "offload_total"), 0
def _x_defb(r):         return _v(r["attack"], "def_battu"), 0
def _x_break(r):        return _v(r["attack"], "break"), 0
def _x_soutien(r):
    # Arriver 3e ou plus loin n'est pas positif : on compte les 1ers soutiens et les cartouches
    # (plein poids) et les 2es soutiens (demi-poids).
    a = r["attack"]
    return _v(a, "soutien_1er", "cartouche") + 0.5 * _v(a, "soutien_2eme"), 0
def _x_passes(r):       return _v(r["attack"], "passes"), 0
def _x_tackle(r):       return _v(r["defense"], "plaquage_tentes"), 0
def _x_recup(r):        return _v(r["defense"], "ballons_recuperes", "combattant_total"), 0
def _x_aerien(r):       return _v(r["ruck"], "duels_aer_total"), 0
def _x_jap(r):          return _v(r["ruck"], "jap"), 0
def _x_sauteur(r):      return _v(r["ruck"], "sauteur"), 0
def _x_lifteur(r):      return _v(r["ruck"], "lifteur"), 0
def _x_lanceur(r):      return _v(r["ruck"], "lanceur"), 0
def _x_melee(r):        return _v(r["ruck"], "melee_conquete"), 0
def _x_ruck(r):
    a = r["attack"]
    return _v(a, "soutien_1er", "soutien_2eme", "soutien_3plus", "cartouche"), 0
def _x_activite(r):     return _v(r["ruck"], "actions_codees"), 0
def _x_pdb(r):          return _v(r["attack"], "pdb"), 0     # ballons perdus (moins = mieux, voir INVERSES)
def _x_essai(r):        return _v(r["ruck"], "essai"), 0


def _p_ratio(num_keys, den_keys, table, min_n):
    def f(r):
        den = _v(r[table], *den_keys)
        if den < min_n:
            return None, den
        return 100 * _v(r[table], *num_keys) / den, den
    return f


_p_contact = _p_ratio(("contact_plus",), ("contact_total",), "attack", 4)
_p_offload = _p_ratio(("offload_plus", "offload_neutre"), ("offload_total",), "attack", 1)
_p_passes = _p_ratio(("passe_plus", "passe_neutre"), ("passes",), "attack", 8)


def _p_tackle(r):
    made = _v(r["defense"], "plaquage_dominant", "plaquage_neutre", "plaquage_passif")
    tent = _v(r["defense"], "plaquage_tentes")
    return (100 * made / tent, tent) if tent >= 4 else (None, tent)


def _p_tackle_dom(r):
    made = _v(r["defense"], "plaquage_dominant", "plaquage_neutre", "plaquage_passif")
    return (100 * _v(r["defense"], "plaquage_dominant") / made, made) if made >= 4 else (None, made)


def _p_melee_off(r):
    ru = dict(r["ruck"] or {})
    ru["melee_off_gagnee"] = _v(ru, "melee_off_gagnee", "melee_off_obtenue")
    ru["melee_off_perdue"] = _v(ru, "melee_off_perdue", "melee_off_penalisee")  # une mêlée pénalisée est perdue
    return _pct_expl_auto(ru, "melee_off_gagnee", "melee_off_perdue", "melee_gagne_auto", "melee_perdu_auto", 3)


def _p_melee_def(r):
    ru = dict(r["ruck"] or {})
    ru["melee_def_gagnee"] = _v(ru, "melee_def_gagnee", "melee_def_obtenue")
    ru["melee_def_perdue"] = _v(ru, "melee_def_perdue", "melee_def_penalisee")
    return _pct_expl_auto(ru, "melee_def_gagnee", "melee_def_perdue", None, None, 3)


def _p_lancer(r):
    return _pct_expl_auto(r["ruck"], "lancer_gagne", "lancer_perdu", "lanceur_gagne_auto", "lanceur_perdu_auto", 4)


def _p_sauteur(r):
    return _pct_expl_auto(r["ruck"], "sauteur_plus", "sauteur_moins", "sauteur_gagne_auto", "sauteur_perdu_auto", 4)


def _p_lifteur(r):
    return _pct_expl_auto(r["ruck"], None, None, "lifteur_gagne_auto", "lifteur_perdu_auto", 4)


def _p_ruck_util(r):
    """Utilité au ruck : arrivée en cartouche ou en 1er soutien = pleinement utile, 2e = demi-utile,
    3e ou plus loin = pas utile. Pourcentage sur l'ensemble des rucks du joueur."""
    a = r["attack"]
    tot = _v(a, "soutien_1er", "soutien_2eme", "soutien_3plus", "cartouche")
    if tot < 5:
        return None, tot
    return 100 * (_v(a, "soutien_1er", "cartouche") + 0.5 * _v(a, "soutien_2eme")) / tot, tot


def _p_jap(r):
    """Qualité du jeu au pied : JAP codés + (plein) et = (demi) sur le total des JAP qualifiés."""
    tot = _v(r["ruck"], "jap_plus", "jap_neutre", "jap_moins")
    return (100 * (_v(r["ruck"], "jap_plus") + 0.5 * _v(r["ruck"], "jap_neutre")) / tot, tot) if tot >= 4 else (None, tot)


def _p_rce(r):
    tot = _v(r["ruck"], "rce_plus", "rce_neutre", "rce_minus")
    return (100 * (_v(r["ruck"], "rce_plus") + 0.5 * _v(r["ruck"], "rce_neutre")) / tot, tot) if tot >= 3 else (None, tot)


def _p_aerien(r):
    tot = _v(r["ruck"], "duels_aer_total")
    return (100 * (_v(r["ruck"], "duels_aer_plus") + 0.5 * _v(r["ruck"], "duels_aer_neutre")) / tot, tot) if tot >= 2 else (None, tot)


def _p_kicks(r):
    tent = _v(r["ruck"], "transfo_tentees", "penalite_tentees", "drop_tentes")
    ok = _v(r["ruck"], "transfo_reussies", "penalite_reussies", "drop_reussis")
    return (100 * ok / tent, tent) if tent >= 2 else (None, tent)


# id -> (libellé, type, extracteur, valeurs par défaut de la cible / du plancher)
CRITERES = {
    "contact":      ("Contacts (volume)", "vol", _x_contact),
    "contact_dom":  ("Dominance au contact (%)", "pct", _p_contact),
    "offload":      ("Offloads (volume)", "vol", _x_offload),
    "offload_pct":  ("Offloads réussis (%)", "pct", _p_offload),
    "def_battu":    ("Défenseurs battus", "vol", _x_defb),
    "break":        ("Franchissements", "vol", _x_break),
    "soutien":      ("Soutiens utiles (1er, cartouche, 2e)", "vol", _x_soutien),
    "passes":       ("Passes (volume)", "vol", _x_passes),
    "passes_pct":   ("Passes propres (%)", "pct", _p_passes),
    "plaquage":     ("Plaquages (volume)", "vol", _x_tackle),
    "plaquage_pct": ("Plaquages réussis (%)", "pct", _p_tackle),
    "plaquage_dom": ("Plaquages dominants (%)", "pct", _p_tackle_dom),
    "plaquage_vp":  ("Plaquages : réussite pondérée par le volume", "pct", _p_tackle),
    "recup":        ("Ballons récupérés / combat", "vol", _x_recup),
    "jap":          ("Jeu au pied (volume)", "vol", _x_jap),
    "aerien":       ("Duels aériens (volume)", "vol", _x_aerien),
    "sauteur":      ("Sauteur (volume)", "vol", _x_sauteur),
    "sauteur_pct":  ("Sauteur : touches gagnées (%)", "pct", _p_sauteur),
    "lifteur":      ("Lifteur (volume)", "vol", _x_lifteur),
    "lifteur_pct":  ("Lifteur : touches gagnées (%)", "pct", _p_lifteur),
    "lanceur":      ("Lancers en touche (volume)", "vol", _x_lanceur),
    "lancer_pct":   ("Lancers gagnés (%)", "pct", _p_lancer),
    "melee":        ("Mêlées (volume)", "vol", _x_melee),
    "melee_off_pct": ("Mêlée off : gagnées (%)", "pct", _p_melee_off),
    "melee_def_pct": ("Mêlée def : gagnées (%)", "pct", _p_melee_def),
    "rce_pct":      ("Réception coup d'envoi (%)", "pct", _p_rce),
    "aerien_pct":   ("Duels aériens (%)", "pct", _p_aerien),
    "kicks_pct":    ("Coups de pied au but réussis (%)", "pct", _p_kicks),
    "jap_pct":      ("Jeu au pied : qualité (%)", "pct", _p_jap),
    "ruck":         ("Rucks (volume)", "vol", _x_ruck),
    "ruck_util":    ("Utilité au ruck (%)", "pct", _p_ruck_util),
    "activite":     ("Activité (actions codées)", "vol", _x_activite),
    "pdb":          ("Ballons perdus (moins = mieux)", "vol", _x_pdb),
    "essai":        ("Essais (volume)", "vol", _x_essai),
}

# Critères « inversés » : plus la valeur est basse, meilleure est la note (10/10 à zéro, 0/10 à la cible).
INVERSES = {"pdb"}

# --- Cibles de référence (valeur donnant 10/10, plancher donnant 0/10) par famille de poste -----
# Calées sur ce qui est réellement codé dans les XML (par 80 min pour un volume).
_CIBLES = {
    "Pilier": {"melee_off_pct": (90, 40), "melee_def_pct": (80, 30), "plaquage_pct": (95, 70), "plaquage_dom": (50, 10), "plaquage": (9, 0), "contact": (11, 0),
               "contact_dom": (65, 30), "soutien": (10, 0), "lifteur_pct": (95, 50), "lifteur": (6, 0),
               "recup": (5, 0), "offload": (3, 0), "offload_pct": (100, 50), "passes": (6, 0),
               "ruck": (18, 0), "ruck_util": (70, 20), "activite": (50, 0)},
    "Talonneur": {"lancer_pct": (95, 50), "melee_off_pct": (90, 40), "melee_def_pct": (80, 30), "plaquage_pct": (95, 70), "plaquage_dom": (50, 10), "plaquage": (9, 0),
                  "contact": (11, 0), "contact_dom": (65, 30), "soutien": (10, 0), "lanceur": (14, 0),
                  "recup": (5, 0), "offload": (3, 0), "offload_pct": (100, 50), "passes": (6, 0)},
    "2ème ligne": {"sauteur_pct": (95, 50), "sauteur": (12, 0), "lifteur_pct": (95, 50), "lifteur": (8, 0),
                   "melee_off_pct": (90, 40), "melee_def_pct": (80, 30), "plaquage_pct": (95, 70), "plaquage_dom": (50, 10), "plaquage": (9, 0), "soutien": (10, 0),
                   "contact": (11, 0), "contact_dom": (65, 30), "recup": (6, 0), "offload": (3, 0), "offload_pct": (100, 50)},
    "3ème ligne": {"contact": (12, 0), "contact_dom": (65, 30), "break": (2, 0), "def_battu": (4, 0),
                   "soutien": (9, 0), "plaquage_pct": (95, 70), "plaquage": (10, 0), "recup": (8, 0), "plaquage_dom": (50, 10),
                   "offload": (3, 0), "offload_pct": (100, 50), "passes": (8, 0), "sauteur_pct": (95, 50),
                   "sauteur": (10, 0), "lifteur_pct": (95, 50), "lifteur": (6, 0)},
    "Charnière": {"passes": (50, 0), "jap": (12, 0), "kicks_pct": (85, 25), "plaquage_pct": (95, 70), "plaquage_dom": (50, 10), "plaquage": (5, 0), "recup": (3, 0),
                  "soutien": (4, 0), "contact": (4, 0), "contact_dom": (65, 30), "break": (2, 0),
                  "def_battu": (3, 0), "offload": (2, 0)},
    "Centre": {"contact": (11, 0), "contact_dom": (65, 30), "def_battu": (5, 0), "break": (3, 0),
               "plaquage_pct": (95, 70), "plaquage": (10, 0), "plaquage_dom": (50, 10), "recup": (6, 0), "passes": (15, 0),
               "offload": (3, 0), "offload_pct": (100, 50), "jap": (8, 0), "soutien": (8, 0)},
    "Ailier/Arrière": {"break": (4, 0), "def_battu": (5, 0), "contact_dom": (65, 30), "contact": (9, 0),
                       "plaquage_pct": (95, 70), "plaquage": (8, 0), "plaquage_dom": (50, 10), "jap": (5, 0), "rce_pct": (75, 25),
                       "aerien_pct": (80, 30), "aerien": (3, 0), "offload": (3, 0), "offload_pct": (100, 50), "passes": (6, 0), "recup": (4, 0),
                       "soutien": (7, 0)},
}

# Famille de chaque profil (sert aux cibles de référence et au calage).
PROFIL_FAMILLE = {"Pilier": "Pilier", "Talonneur": "Talonneur", "2ème ligne": "2ème ligne",
                  "3ème ligne": "3ème ligne", "9": "Charnière", "10": "Charnière", "12-13": "Centre",
                  "11-14": "Ailier/Arrière", "15": "Ailier/Arrière"}

# --- Grille d'attentes du club (auto-évaluation par poste) traduite en statistiques ------------
# Pour chaque profil : liste de (attente du club, {critère: poids}). Seules les attentes qui se
# mesurent en stats sont ici ; les autres (lecture, stratégie, leadership, communication, se
# déplacer / enchaîner, compréhension du système, défense de maul, dureté, état d'esprit) ne
# peuvent pas être notées automatiquement.
PROFILS = {
    "Pilier": [
        ("Mêlée fermée", {"melee_off_pct": 14, "melee_def_pct": 10}),
        ("Lift sur touche, CE et renvoi", {"lifteur_pct": 12}),
        ("Plaquer / contest", {"plaquage_dom": 4, "plaquage_pct": 4}),
        ("Soutenir / rucker", {"ruck_util": 6}),
        ("Duel off", {"contact_dom": 6}),
    ],
    # Talonneur (20 oct. 2026, demande de Téo) : pourcentages seulement (plus de volumes) ; duels off et
    # habileté technique passent dans le Facteur X (voir X_PROFILS).
    "Talonneur": [
        ("Lancer", {"lancer_pct": 17}),
        ("Mêlée fermée", {"melee_off_pct": 11, "melee_def_pct": 8}),
        ("Plaquer / contest", {"plaquage_dom": 4, "plaquage_pct": 4}),
    ],
    # Mêlée fermée retirée : non quantifiée dans les XML. Son poids est reporté sur la touche, le ruck, le plaquage et le duel.
    "2ème ligne": [
        ("Sauter / lifter / touche, CE et CR", {"sauteur_pct": 11, "lifteur_pct": 6}),
        ("Plaquer / contest / défendre", {"plaquage_dom": 5, "plaquage_pct": 5}),
        ("Soutenir / rucker", {"soutien": 9, "ruck_util": 6}),
        # Duel off / porteur de balle et habileté technique : passés dans le Facteur X (20 oct. 2026).
    ],
    "3ème ligne": [
        # Dominance (contact, plaquage) et offloads : passés dans le Facteur X (20 oct. 2026).
        ("Duel off / franchissement", {"break": 7, "def_battu": 5}),
        ("Soutenir / rucker", {"soutien": 8}),
        ("Plaquer / contest / défendre", {"plaquage_vp": 5}),
        ("Assurer la continuité", {"passes_pct": 2}),
        ("Sauter / lifter / lecture touche", {"sauteur_pct": 5, "lifteur_pct": 2}),
    ],
    # 9 (20 oct. 2026, demande de Téo) : on garde l'habileté technique, on ajoute la qualité du jeu au pied,
    # les ballons perdus (au-dessus de 1 par match, ce n'est pas bon), le % de plaquages et un peu d'utilité au ruck.
    "9": [
        ("Transmission", {"passes_pct": 8}),
        ("Jeu au pied (qualité)", {"jap_pct": 12}),
        ("Sécurité du ballon", {"pdb": 8}),
        ("Plaquer", {"plaquage_pct": 3}),
        ("Soutenir / rucker", {"ruck_util": 6}),
        ("Habileté technique", {"offload_pct": 2, "break": 4}),
    ],
    # 10 (20 oct. 2026, demande de Téo) : réussite au pied (but), qualité des passes, plaquages, qualité du jeu au pied
    # et ballons perdus. Le reste est retiré.
    "10": [
        ("Réussite au but", {"kicks_pct": 12}),
        ("Transmission", {"passes_pct": 10}),
        ("Jeu au pied (qualité)", {"jap_pct": 12}),
        ("Plaquer", {"plaquage_pct": 3}),
        ("Sécurité du ballon", {"pdb": 8}),
    ],
    "12-13": [
        ("Duel off / franchissement", {"break": 12, "def_battu": 12}),
        ("Circulation def / plaquer / contest / défendre", {"plaquage_pct": 6}),
        ("Habileté technique (main / pied)", {"offload_pct": 6, "passes_pct": 3}),
        ("Soutien / ruck offensif", {"soutien": 6}),
    ],
    "11-14": [
        ("Duel / franchissement", {"contact_dom": 8, "break": 16, "def_battu": 14}),
        ("Réception / duel aérien", {"rce_pct": 8, "aerien_pct": 12}),
        ("Plaquer / lecture défense", {"plaquage_dom": 4, "plaquage_pct": 5}),
        ("Soutien / ruck offensif", {"soutien": 4}),
        ("Habileté technique (mains / pied)", {"offload_pct": 5, "passes_pct": 2}),
    ],
    "15": [
        ("1 contre 1 défensif", {"plaquage_pct": 6, "plaquage_dom": 6}),
        ("Duel / franchissement", {"break": 14, "def_battu": 12, "contact_dom": 6}),
        ("Réception / duel aérien", {"rce_pct": 8, "aerien_pct": 12}),
        ("Habileté technique", {"offload_pct": 4, "passes_pct": 2}),
    ],
}
POSTES = list(PROFILS)
for _fam_cibles in _CIBLES.values():
    if "break" in _fam_cibles:
        _fam_cibles["break"] = (1, 0)  # 1 franchissement = 8/10, puis +1 point par franchissement
for _fam_cibles in _CIBLES.values():
    _fam_cibles.setdefault("plaquage_vp", (95, 70))
_CIBLES["Charnière"].update({"jap_pct": (75, 25), "pdb": (2, 0), "plaquage_pct": (95, 60)})  # pdb : 0/10 à 2 ballons perdus par 80 min
for _fam_cibles in _CIBLES.values():
    _fam_cibles.setdefault("passes_pct", (98, 70))
_CIBLES["Charnière"]["passes_pct"] = (99, 85)  # les 9 et 10 passent beaucoup : la barre est plus haute
NON_MESURABLES = ["Lecture du jeu", "Stratégie", "Leadership", "Communication", "Se déplacer / enchaîner les tâches",
                  "Compréhension du système", "Défense de maul", "Gestion du 3ème rideau / contre-attaque",
                  "Qualité du jeu au pied", "Dureté", "État d'esprit"]


def _construire():
    out, labels = {}, {}
    for profil, attentes in PROFILS.items():
        fam = PROFIL_FAMILLE[profil]
        d, lab = {}, {}
        for nom, crits in attentes:
            for crit, poids in crits.items():
                cible, plancher = _CIBLES[fam].get(crit) or next(
                    (v[crit] for v in _CIBLES.values() if crit in v), (10, 0))
                d[crit] = (poids, cible, plancher)
                lab[crit] = nom
        out[profil], labels[profil] = d, lab
    return out, labels


BAREME_DEFAUT, ATTENTE_DU_CRITERE = _construire()

# Calage par famille : certains postes ont des cibles plus dures dans les faits (moins d'actions
# codées). Coefficient appliqué à la note de base pour rendre les moyennes comparables.
CALAGE = {"3ème ligne": 1.15, "Ailier/Arrière": 1.2, "Centre": 1.05}

# Échelle de lecture des notes : (note brute, note affichée). 5 = match moyen, 7 = bon match,
# 3 = match pas bon, sous 3 = vraiment mauvais. Interpolation linéaire entre les points.
ECHELLE = [(0.0, 0.0), (2.8, 2.0), (4.4, 3.0), (5.9, 5.0), (7.2, 7.0), (8.6, 9.0), (10.0, 10.0)]


def etalonner(x):
    for (a, b), (c, d) in zip(ECHELLE, ECHELLE[1:]):
        if x <= c:
            return b + (x - a) / (c - a) * (d - b)
    return ECHELLE[-1][1]


def bareme_effectif(overrides=None):
    """Barème de départ + réglages enregistrés. overrides = {(poste, critère): (poids, cible, plancher)}."""
    out = {p: dict(c) for p, c in BAREME_DEFAUT.items()}
    for (poste, crit), val in (overrides or {}).items():
        if poste in out and (crit in CRITERES or (crit.startswith("x:") and crit[2:] in CRITERES)):
            out[poste][crit] = val  # « x:<critère> » = réglage du Facteur X de ce poste
    return out


def _clamp(x, lo=0.0, hi=10.0):
    return max(lo, min(hi, x))


def evaluer_critere(crit, cible, plancher, rows, minutes):
    """Valeur lissée et score /10 d'un critère pour un joueur. Même calcul pour la note de match
    et pour le radar du comparateur. `valeur` vaut None si le critère n'est pas évaluable
    (pas assez d'actions)."""
    label, kind, fn = CRITERES[crit]
    try:
        val, n = fn(rows)
    except Exception:
        val, n = None, 0
    out = {"label": label, "type": kind, "valeur": None, "actions": n, "score": None}
    if val is None:
        return out
    if kind == "vol":
        # Rythme par minute lissé : un court passage n'est pas multiplié tel quel jusqu'à 80 min.
        # On ajoute SHRINK_MIN minutes virtuelles jouées à demi-cible.
        prior = 0.5 * cible * SHRINK_MIN / 80.0
        val80 = (val + prior) / (minutes + SHRINK_MIN) * 80.0
        if crit == "break" and cible:
            # Franchissements (20 oct. 2026) : atteindre la cible = 8/10, chaque franchissement de plus = +1 point.
            out["score"] = _clamp(8 * val80 / cible if val80 <= cible else 8 + (val80 - cible))
        elif crit in INVERSES:
            out["score"] = _clamp(10 * (1 - val80 / cible)) if cible else 0.0
        else:
            out["score"] = _clamp(10 * val80 / cible) if cible else 0.0
        out["valeur"] = round(val80, 1)
    else:
        span = (cible - plancher) or 1
        if n:
            prior = plancher + 0.7 * span
            val = (val * n + prior * SHRINK_K) / (n + SHRINK_K)
        out["score"] = _clamp(10 * (val - plancher) / span)
        out["valeur"] = round(val)
        if crit == "plaquage_vp" and minutes:
            # Le volume compte avec la réussite : réussir 95 % sur 3 plaquages ne vaut pas 95 % sur 10.
            vol80 = n * 80.0 / minutes
            out["score"] *= 0.5 + 0.5 * min(1.0, vol80 / TACKLE_VOL_CIBLE)
    return out


# Cibles du Facteur X pour un critère de QUALITÉ hors profil (octobre 2026, calées sur les 5 premiers matchs
# de la saison). Elles remplacent « le plus bas niveau demandé à un poste » quand il est trop dur
# (plaquages dominants, dominance au contact). À revoir avec plus de matchs.
X_CIBLES = {"plaquage_dom": (30, 10), "contact_dom": (55, 30)}


def _cible_hors_poste(crit):
    """Cible et plancher d'un critère hors du profil du joueur : le plus bas niveau demandé à un
    poste qui l'a dans ses attentes (le réaliser hors de son poste est déjà un apport), sauf les
    critères recalés dans X_CIBLES."""
    if crit in X_CIBLES:
        return X_CIBLES[crit]
    cands = [v[crit] for v in _CIBLES.values() if crit in v and v[crit][0] > 0]
    return min(cands, key=lambda x: x[0]) if cands else None


TACKLE_VOL_CIBLE = 10  # plaquages tentés par 80 min pour que la réussite compte à 100 %
NB_MATCHS_ECART = 4  # à partir de 4 matchs notés, la saison écarte la meilleure et la moins bonne performance
COUVERTURE_MIN = 0.6  # part minimale du poids du barème qui doit être évaluable
X_MIN_MATCH = 20    # minutes minimales pour un Facteur X sur un match
X_MIN_SAISON = 60   # minutes minimales pour un Facteur X sur la saison
X_NB = 3      # le facteur X est la moyenne des X_NB meilleurs atouts hors poste
X_SEUIL = 3.0  # note minimale d'un critère hors poste pour compter comme atout


# Facteur X propre à un poste : liste de critères retenus (moyenne de tous les critères évaluables),
# au lieu de la règle générale « meilleurs atouts hors profil ».
X_PROFILS = {
    # Ailiers / arrière (octobre 2026, demande de Téo) : qualité du jeu au pied, passes propres,
    # sécurité du ballon (ballons perdus, ballons récupérés) et finition (essais).
    # Piliers : mobilité (défenseurs battus, franchissements, ballons récupérés, offloads), finition (essais),
    # passes (petit poids).
    # Un critère = (identifiant, poids) ; sans poids = 1.
    "Pilier": [("def_battu", 3), ("break", 3), ("essai", 3), ("offload_pct", 3),
               ("passes_pct", 1)],
    "9": ["essai", "def_battu", "break"],  # 20 oct. 2026 : uniquement le socle
    "12-13": ["essai", "def_battu", "break", "contact_dom", "plaquage_dom", "jap_pct"],  # socle + dominance off et déf
    "10": ["essai", "def_battu", "break", "passes_pct"],  # 20 oct. 2026 : socle + passes
    "11-14": ["jap_pct", "passes_pct", "pdb", "essai"],
    "15": ["jap_pct", "passes_pct", "pdb", "essai"],
    # 3ème ligne : dominance dans ce qu'ils font (contacts +/-, plaquages +/-, défenseurs battus)
    # et volume de jeu de touche (sauteur, lifteur).
    "3ème ligne": ["contact_dom", "plaquage_dom", "offload_pct", ("def_battu", 4)],
    # Talonneur : pourcentages seulement ; duels off (dominance au contact) et habileté technique
    # (offloads, passes) y sont passés en octobre 2026.
    "Talonneur": ["contact_dom", "offload_pct", "passes_pct", "ruck_util"],
    # 2ème ligne (20 oct. 2026) : duel off et habileté technique (déplacés du poste), mêlée, réception du CE.
    # Retirés : lancers gagnés, duels aériens, coups de pied au but ; l'utilité au ruck est passée au poste.
    "2ème ligne": ["contact_dom", "offload_pct", "passes_pct",
                   "melee_off_pct", "melee_def_pct", "rce_pct", "def_battu", "break", "essai"],
}


# Socle commun du Facteur X (20 oct. 2026, demande de Téo) : essais, défenseurs battus et franchissements
# comptent dans le Facteur X de CHAQUE poste.
X_SOCLE = ["essai", "def_battu", "break"]
for _p, _l in X_PROFILS.items():
    _deja = {e[0] if isinstance(e, tuple) else e for e in _l}
    _l.extend(c for c in X_SOCLE if c not in _deja)


# Cibles propres aux listes X_PROFILS quand le critère n'est pas dans les attentes du poste.
X_CIBLES_PROFIL = {"jap_pct": (75, 25), "passes_pct": (98, 70), "pdb": (3, 0), "essai": (0.5, 0)}
# Cibles propres à un poste (prioritaires sur X_CIBLES_PROFIL) : un pilier n'a pas les mêmes volumes qu'un ailier.
X_CIBLES_POSTE = {"9": {"essai": (0.3, 0), "def_battu": (2, 0), "break": (1, 0)},
                  "10": {"essai": (0.3, 0), "def_battu": (2, 0), "break": (1, 0), "passes_pct": (99, 85)},
                  "12-13": {"essai": (0.4, 0), "def_battu": (3, 0), "break": (1, 0), "contact_dom": (65, 30),
                            "plaquage_dom": (10, 0), "jap_pct": (75, 25)},
                  "3ème ligne": {"essai": (0.3, 0), "def_battu": (3, 0)},
                  "2ème ligne": {"contact_dom": (65, 30), "offload_pct": (100, 50),
                                 "passes_pct": (98, 70), "melee_off_pct": (90, 40), "melee_def_pct": (80, 30), "rce_pct": (75, 25),
                                 "def_battu": (2, 0), "break": (1, 0), "essai": (0.15, 0)},
                  "Talonneur": {"contact_dom": (65, 30), "offload_pct": (100, 50), "passes_pct": (98, 70), "ruck_util": (70, 20),
                                "def_battu": (2, 0), "break": (1, 0), "essai": (0.15, 0)},
                  "Pilier": {"def_battu": (2, 0), "break": (1, 0), "essai": (0.15, 0), "recup": (4, 0),
                             "offload_pct": (100, 50), "passes_pct": (98, 70)}}


def _facteur_x_profil(poste, rows, minutes, reglages):
    atouts = []
    poids = {}
    for entree in X_PROFILS[poste]:
        crit, w = entree if isinstance(entree, tuple) else (entree, 1)
        r = reglages.get(crit)
        ref = X_CIBLES_POSTE.get(poste, {}).get(crit) or ((r[1], r[2]) if r and r[1] else (X_CIBLES_PROFIL.get(crit) or _cible_hors_poste(crit)))
        xr = reglages.get("x:" + crit)  # réglage enregistré sur la page Barème (poids, cible, plancher)
        if xr:
            w, ref = xr[0], (xr[1], xr[2])
            if w <= 0:
                continue
        poids[crit] = w
        if not ref:
            continue
        ev = evaluer_critere(crit, ref[0], ref[1], rows, minutes)
        if ev["valeur"] is None:
            continue
        atouts.append({"critere": crit, "label": ev["label"], "type": ev["type"], "valeur": ev["valeur"],
                       "cible": ref[0], "score": round(ev["score"], 1), "actions": ev["actions"], "poids": w})
    if not atouts:
        return None
    atouts.sort(key=lambda a: -a["score"])
    tw = sum(a["poids"] for a in atouts)
    return {"note": round(sum(a["score"] * a["poids"] for a in atouts) / tw, 1), "atouts": atouts}


def _facteur_x_brut(poste, rows, minutes, bareme=None):
    """Facteur X : ce que le joueur apporte HORS des attentes de son poste (franchissements pour un
    pilier, jeu au pied pour un centre, etc.). Note /10 = moyenne de ses X_NB meilleurs atouts (critères hors
    profil notés au moins X_SEUIL), chacun noté comme sur la page Notes. Une valeur basse est normale : elle ne pénalise pas
    la note au poste. Renvoie None s'il n'y a rien d'évaluable."""
    reglages = (bareme or BAREME_DEFAUT).get(poste) or {}
    if poste in X_PROFILS:
        return _facteur_x_profil(poste, rows, minutes, reglages)
    dans_profil = {c for c, r in reglages.items() if r[0] > 0}
    atouts = []
    socle = []
    for crit in X_SOCLE:
        r = reglages.get(crit)
        xr = reglages.get("x:" + crit)
        w = 1
        ref = X_CIBLES_POSTE.get(poste, {}).get(crit) or ((r[1], r[2]) if r and r[1] else (X_CIBLES_PROFIL.get(crit) or _cible_hors_poste(crit)))
        if xr:
            w, ref = xr[0], (xr[1], xr[2])
        if w <= 0 or not ref:
            continue
        ev = evaluer_critere(crit, ref[0], ref[1], rows, minutes)
        if ev["valeur"] is None:
            continue
        socle.append({"critere": crit, "label": ev["label"], "type": ev["type"], "valeur": ev["valeur"],
                      "cible": ref[0], "score": round(ev["score"], 1), "actions": ev["actions"], "poids": w})
    for crit in CRITERES:
        if crit in dans_profil or crit == "jap" or crit in X_SOCLE or crit == "plaquage_vp":  # jap : volume de jeu au pied, qualité non notée
            continue
        # Le facteur X juge la QUALITÉ de ce que fait le joueur hors de son poste, pas la quantité :
        # les volumes (contacts, soutiens, plaquages, duels aériens…) relèvent du jeu au poste.
        if CRITERES[crit][1] == "vol":
            continue
        xr = reglages.get("x:" + crit)
        ref = (xr[1], xr[2]) if xr else _cible_hors_poste(crit)
        if not ref:
            continue
        ev = evaluer_critere(crit, ref[0], ref[1], rows, minutes)
        if ev["valeur"] is None:
            continue
        atouts.append({"critere": crit, "label": ev["label"], "type": ev["type"], "valeur": ev["valeur"],
                       "cible": ref[0], "score": round(ev["score"], 1), "actions": ev["actions"]})
    if not atouts and not socle:
        return None
    atouts.sort(key=lambda a: -a["score"])
    # Seuls les vrais atouts comptent (note >= X_SEUIL) : un critère à zéro ne dilue pas un atout réel.
    # Le socle (essais, défenseurs battus, franchissements) compte toujours, en plus des meilleurs atouts.
    reels = [a for a in atouts if a["score"] >= X_SEUIL][:X_NB]
    top = reels or atouts[:1]
    tw = sum(a.get("poids", 1) for a in socle) + len(top)
    note = (sum(a["score"] * a["poids"] for a in socle) + sum(a["score"] for a in top)) / tw
    return {"note": round(note, 1), "atouts": sorted(socle + atouts, key=lambda a: -a["score"])}


# Référence du Facteur X par poste : note brute d'un joueur « moyen » à ce poste. Elle est calée sur 5, pour
# qu'on puisse être très haut comme très bas à chaque poste, quelle que soit la difficulté de ses critères
# (un 3ème ligne n'est pas bas « parce que 3ème ligne », ni un pilier haut « parce que pilier »).
X_REF_DEFAUT = {"Pilier": 2.3, "Talonneur": 4.9, "2ème ligne": 3.9, "3ème ligne": 2.9, "9": 2.7, "10": 6.4,
         "12-13": 3.9, "11-14": 4.3, "15": 5.4}
X_REF = dict(X_REF_DEFAUT)  # remplacé à chaque calcul par les moyennes réelles de la saison (voir app._assurer_references)
# Note au poste : référence par poste (note brute moyenne d'un joueur de ce poste), recalculée automatiquement.
# Elle recentre chaque poste sur 5, pour que « joueur moyen = 5 » quel que soit le poste.
NOTE_REF = {}
REF_MIN_ECHANTILLON = 5  # nombre minimal de notes de match d'un poste pour calculer sa référence


def recentrer(valeur, ref):
    """0 → 0, ref → 5, 10 → 10 (linéaire par morceaux)."""
    v = _clamp(valeur)
    if not ref or ref <= 0 or ref >= 10:
        return v
    return 5 * v / ref if v <= ref else 5 + 5 * (v - ref) / (10 - ref)



def facteur_x(poste, rows, minutes, bareme=None):
    """Facteur X relatif au poste : 5 = joueur moyen du poste, vers 10 = très au-dessus, vers 0 = très en dessous.
    `brut` garde la moyenne pondérée des critères avant ce recalage."""
    res = _facteur_x_brut(poste, rows, minutes, bareme)
    if not res:
        return res
    brut = res["note"]
    ref = X_REF.get(poste)
    if ref:
        res["note"] = round(recentrer(brut, ref), 1)
    res["brut"] = brut
    return res


def x_defaut(poste):
    """Lignes du tableau « Facteur X » d'un poste, valeurs de départ : [{crit, label, type, poids, cible,
    plancher}]. Postes à liste dédiée : critères et poids de X_PROFILS (moyenne pondérée). Autres postes :
    critères de qualité (%) hors attentes du poste, dont on garde la moyenne des X_NB meilleurs (poids None)."""
    reglages = BAREME_DEFAUT.get(poste) or {}
    out = []
    if poste in X_PROFILS:
        for entree in X_PROFILS[poste]:
            crit, w = entree if isinstance(entree, tuple) else (entree, 1)
            r = reglages.get(crit)
            ref = X_CIBLES_POSTE.get(poste, {}).get(crit) or ((r[1], r[2]) if r and r[1] else (X_CIBLES_PROFIL.get(crit) or _cible_hors_poste(crit)))
            if crit in CRITERES and ref:
                out.append({"crit": crit, "label": CRITERES[crit][0], "type": CRITERES[crit][1], "poids": w,
                            "cible": ref[0], "plancher": ref[1]})
        return out
    dans_profil = {c for c, r in reglages.items() if r[0] > 0}
    for crit in X_SOCLE:
        r = reglages.get(crit)
        ref = X_CIBLES_POSTE.get(poste, {}).get(crit) or ((r[1], r[2]) if r and r[1] else (X_CIBLES_PROFIL.get(crit) or _cible_hors_poste(crit)))
        if ref:
            out.append({"crit": crit, "label": CRITERES[crit][0], "type": CRITERES[crit][1], "poids": 1,
                        "cible": ref[0], "plancher": ref[1]})
    for crit in CRITERES:
        if crit in dans_profil or crit == "jap" or crit in X_SOCLE or crit == "plaquage_vp" or CRITERES[crit][1] == "vol":
            continue
        ref = _cible_hors_poste(crit)
        if ref:
            out.append({"crit": crit, "label": CRITERES[crit][0], "type": CRITERES[crit][1], "poids": None,
                        "cible": ref[0], "plancher": ref[1]})
    return out


def x_effectif(poste, bareme=None):
    """x_defaut + réglages enregistrés (clés « x:<critère> » du barème effectif)."""
    reglages = (bareme or BAREME_DEFAUT).get(poste) or {}
    rows = x_defaut(poste)
    for r in rows:
        v = reglages.get("x:" + r["crit"])
        if v:
            if r["poids"] is not None:
                r["poids"] = v[0]
            r["cible"], r["plancher"] = v[1], v[2]
    return rows


def _facteur_x_sur(poste, rows, minutes, bareme):
    """Le facteur X est un complément : une erreur de calcul ne doit jamais bloquer la note au poste."""
    try:
        return facteur_x(poste, rows, minutes, bareme)
    except Exception:
        return None


def note_match(poste, rows, minutes, jaunes=0, rouges=0, bareme=None):
    """Note /10 d'un joueur sur un match. `rows` = {"attack", "defense", "ruck"} (lignes du joueur
    pour ce match, éventuellement None). Renvoie None si pas assez de minutes ou de matière."""
    if minutes < MIN_MINUTES or poste not in POSTES:
        return None
    reglages = (bareme or BAREME_DEFAUT).get(poste) or {}
    detail, tot_w, tot_s, manquants = [], 0.0, 0.0, []
    for crit, (poids, cible, plancher) in reglages.items():
        if poids <= 0 or crit not in CRITERES:
            continue
        ev = evaluer_critere(crit, cible, plancher, rows, minutes)
        if ev["valeur"] is None:
            manquants.append({"critere": crit, "label": ev["label"], "poids": poids, "n": ev["actions"]})
            continue
        detail.append({"critere": crit, "label": ev["label"], "type": ev["type"], "valeur": ev["valeur"],
                       "actions": ev["actions"], "score": round(ev["score"], 1), "poids": poids})
        tot_w += poids
        tot_s += poids * ev["score"]
    if not detail:
        return None
    base = _clamp(tot_s / tot_w * CALAGE.get(PROFIL_FAMILLE.get(poste), 1.0))
    # Peu de minutes = peu de preuves : sous CONFIANCE_MIN minutes, la note est tirée vers la
    # note neutre (5) au prorata des minutes jouées.
    confiance = min(1.0, minutes / CONFIANCE_MIN)
    # Couverture du barème : si moins de COUVERTURE_MIN du poids total est évaluable (critères sans assez
    # d'actions codées), la note est tirée vers 5 au prorata de ce qui manque.
    poids_total = sum(r[0] for c, r in reglages.items() if r[0] > 0 and c in CRITERES)
    couverture = tot_w / poids_total if poids_total else 1.0
    confiance *= min(1.0, couverture / COUVERTURE_MIN)
    base = etalonner(base * confiance + NOTE_NEUTRE * (1 - confiance))
    attack = rows.get("attack")
    pen = _v(attack, "penalite_off", "penalite_def", "penalite_autre")
    pdb = _v(attack, "pdb")
    rates = _v(attack, "passe_minus")
    rates_t = _v(rows.get("defense"), "plaquage_rate")
    passifs = _v(rows.get("defense"), "plaquage_passif")
    hauts = _v(rows.get("defense"), "plaquage_haut")
    pr = PASSES_RATEES.get(poste, PASSES_RATEES_DEFAUT)
    essais = _v(rows.get("ruck"), "essai")
    fp = PLAQUAGE_FACTEUR.get(poste, 1.0)
    malus = [("Pénalités concédées", min(pen * MALUS["penalite"], MALUS["penalite_max"]), pen),
             ("Plaquages ratés", fp * min(rates_t * PLAQUAGE_RATE[0], PLAQUAGE_RATE[1]), rates_t),
             ("Plaquages passifs", fp * min(passifs * PLAQUAGE_PASSIF[0], PLAQUAGE_PASSIF[1]), passifs),
             ("Plaquages hauts", fp * min(hauts * PLAQUAGE_HAUT[0], PLAQUAGE_HAUT[1]), hauts),
             ("Cartons jaunes", jaunes * MALUS["jaune"], jaunes),
             ("Cartons rouges", rouges * MALUS["rouge"], rouges),
             ("Passes ratées", min(rates * pr[0], pr[1]), rates),
             ("Ballons perdus", 0 if (reglages.get("pdb") or (0,))[0] > 0 else min(pdb * MALUS["pdb"], MALUS["pdb_max"]), pdb)]
    malus = [{"label": l, "points": round(-p, 1), "n": n} for l, p, n in malus if p > 0]
    b_pt, b_max = BONUS_ESSAI_POSTE.get(poste, (BONUS["essai"], BONUS["essai_max"]))
    bonus_pts = min(essais * b_pt, b_max)
    bonus = [{"label": "Essais", "points": round(bonus_pts, 1), "n": essais}] if bonus_pts > 0 else []
    rec = _x_recup(rows)[0] if rows.get("defense") else 0
    pts_rec = min(rec * BONUS_RECUP["point"], BONUS_RECUP["max"])
    if pts_rec > 0:
        bonus.append({"label": "Ballons récupérés", "points": round(pts_rec, 1), "n": rec})
        bonus_pts += pts_rec
    if poste in BONUS_AERIEN["profils"]:
        ruck = rows.get("ruck")
        g_, n_ = _v(ruck, "duels_aer_plus"), _v(ruck, "duels_aer_neutre")
        pts = min(g_ * BONUS_AERIEN["gagne"] + n_ * BONUS_AERIEN["neutre"], BONUS_AERIEN["max"])
        if pts > 0:
            bonus.append({"label": "Duels aériens joués", "points": round(pts, 1), "n": g_ + n_})
            bonus_pts += pts
    total_malus = min(sum(-m["points"] for m in malus), MALUS["total_max"])
    note_brute = _clamp(base - total_malus + bonus_pts)
    note = recentrer(note_brute, NOTE_REF.get(poste))
    return {"note": round(note, 1), "note_brut": round(note_brute, 2), "base": round(base, 1), "malus": malus, "bonus": bonus,
            "detail": sorted(detail, key=lambda d: -d["poids"]), "manquants": manquants, "couverture": round(100 * couverture), "minutes": minutes, "poste": poste,
            "x": _facteur_x_sur(poste, rows, minutes, bareme)}
