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
BONUS = {"essai": 0.7, "essai_max": 2.0}
PLAQUAGE_RATE = (0.25, 1.5)   # malus par plaquage raté, plafond par match
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
def _x_jap(r):          return _v(r["ruck"], "jap"), 0
def _x_sauteur(r):      return _v(r["ruck"], "sauteur"), 0
def _x_lifteur(r):      return _v(r["ruck"], "lifteur"), 0
def _x_lanceur(r):      return _v(r["ruck"], "lanceur"), 0
def _x_melee(r):        return _v(r["ruck"], "melee_conquete"), 0


def _p_ratio(num_keys, den_keys, table, min_n):
    def f(r):
        den = _v(r[table], *den_keys)
        if den < min_n:
            return None, den
        return 100 * _v(r[table], *num_keys) / den, den
    return f


_p_contact = _p_ratio(("contact_plus",), ("contact_total",), "attack", 4)
_p_offload = _p_ratio(("offload_plus", "offload_neutre"), ("offload_total",), "attack", 3)
_p_passes = _p_ratio(("passe_plus", "passe_neutre"), ("passes",), "attack", 8)


def _p_tackle(r):
    made = _v(r["defense"], "plaquage_dominant", "plaquage_neutre", "plaquage_passif")
    tent = _v(r["defense"], "plaquage_tentes")
    return (100 * made / tent, tent) if tent >= 4 else (None, tent)


def _p_tackle_dom(r):
    made = _v(r["defense"], "plaquage_dominant", "plaquage_neutre", "plaquage_passif")
    return (100 * _v(r["defense"], "plaquage_dominant") / made, made) if made >= 4 else (None, made)


def _p_melee_off(r):
    return _pct_expl_auto(r["ruck"], "melee_off_gagnee", "melee_off_perdue", "melee_gagne_auto", "melee_perdu_auto", 3)


def _p_melee_def(r):
    return _pct_expl_auto(r["ruck"], "melee_def_gagnee", "melee_def_perdue", None, None, 3)


def _p_lancer(r):
    return _pct_expl_auto(r["ruck"], "lancer_gagne", "lancer_perdu", "lanceur_gagne_auto", "lanceur_perdu_auto", 4)


def _p_sauteur(r):
    return _pct_expl_auto(r["ruck"], None, None, "sauteur_gagne_auto", "sauteur_perdu_auto", 4)


def _p_lifteur(r):
    return _pct_expl_auto(r["ruck"], None, None, "lifteur_gagne_auto", "lifteur_perdu_auto", 4)


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
    "recup":        ("Ballons récupérés / combat", "vol", _x_recup),
    "jap":          ("Jeu au pied (volume)", "vol", _x_jap),
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
}

# --- Cibles de référence (valeur donnant 10/10, plancher donnant 0/10) par famille de poste -----
# Calées sur ce qui est réellement codé dans les XML (par 80 min pour un volume).
_CIBLES = {
    "Pilier": {"melee_off_pct": (90, 40), "melee_def_pct": (80, 30), "plaquage": (9, 0), "contact": (11, 0),
               "contact_dom": (65, 30), "soutien": (10, 0), "lifteur_pct": (95, 50), "lifteur": (6, 0),
               "recup": (5, 0), "offload": (3, 0), "passes": (6, 0)},
    "Talonneur": {"lancer_pct": (95, 50), "melee_off_pct": (90, 40), "melee_def_pct": (80, 30), "plaquage": (9, 0),
                  "contact": (11, 0), "contact_dom": (65, 30), "soutien": (10, 0), "lanceur": (14, 0),
                  "recup": (5, 0), "offload": (3, 0), "passes": (6, 0)},
    "2ème ligne": {"sauteur_pct": (95, 50), "sauteur": (12, 0), "lifteur_pct": (95, 50), "lifteur": (8, 0),
                   "melee_off_pct": (90, 40), "melee_def_pct": (80, 30), "plaquage": (9, 0), "soutien": (10, 0),
                   "contact": (11, 0), "contact_dom": (65, 30), "recup": (6, 0), "offload": (3, 0)},
    "3ème ligne": {"contact": (12, 0), "contact_dom": (65, 30), "break": (2, 0), "def_battu": (4, 0),
                   "soutien": (9, 0), "plaquage": (10, 0), "recup": (8, 0), "plaquage_dom": (50, 10),
                   "offload": (3, 0), "offload_pct": (100, 50), "passes": (8, 0), "sauteur_pct": (95, 50),
                   "sauteur": (10, 0), "lifteur_pct": (95, 50), "lifteur": (6, 0)},
    "Charnière": {"passes": (50, 0), "jap": (12, 0), "kicks_pct": (85, 25), "plaquage": (5, 0), "recup": (3, 0),
                  "soutien": (4, 0), "contact": (4, 0), "contact_dom": (65, 30), "break": (2, 0),
                  "def_battu": (3, 0), "offload": (2, 0)},
    "Centre": {"contact": (11, 0), "contact_dom": (65, 30), "def_battu": (5, 0), "break": (3, 0),
               "plaquage": (10, 0), "plaquage_dom": (50, 10), "recup": (6, 0), "passes": (15, 0),
               "offload": (3, 0), "offload_pct": (100, 50), "jap": (8, 0), "soutien": (8, 0)},
    "Ailier/Arrière": {"break": (4, 0), "def_battu": (5, 0), "contact_dom": (65, 30), "contact": (9, 0),
                       "plaquage": (8, 0), "plaquage_dom": (50, 10), "jap": (5, 0), "rce_pct": (75, 25),
                       "aerien_pct": (80, 30), "offload": (3, 0), "passes": (6, 0), "recup": (4, 0),
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
        ("Lift sur touche, CE et renvoi", {"lifteur_pct": 12, "lifteur": 7}),
        ("Plaquer / contest", {"plaquage": 8, "recup": 7}),
        ("Soutenir / rucker", {"soutien": 5}),
        ("Duel off", {"contact_dom": 6, "contact": 4}),
        ("Habileté technique", {"offload": 5, "passes": 3}),
    ],
    "Talonneur": [
        ("Lancer", {"lancer_pct": 17, "lanceur": 7}),
        ("Mêlée fermée", {"melee_off_pct": 11, "melee_def_pct": 8}),
        ("Plaquer / contest", {"plaquage": 8, "recup": 7}),
        ("Soutenir / rucker", {"soutien": 5}),
        ("Duel off", {"contact_dom": 6, "contact": 4}),
        ("Habileté technique", {"offload": 5, "passes": 3}),
    ],
    "2ème ligne": [
        ("Sauter / lifter / touche, CE et CR", {"sauteur_pct": 9, "sauteur": 6, "lifteur_pct": 5, "lifteur": 4}),
        ("Mêlée fermée", {"melee_off_pct": 11, "melee_def_pct": 8}),
        ("Plaquer / contest / défendre", {"plaquage": 8, "recup": 7}),
        ("Soutenir / rucker", {"soutien": 5}),
        ("Duel off / porteur de balle", {"contact_dom": 5, "contact": 3}),
    ],
    "3ème ligne": [
        ("Duel off / franchissement", {"contact_dom": 7, "break": 7, "def_battu": 5, "contact": 3}),
        ("Soutenir / rucker", {"soutien": 8}),
        ("Plaquer / contest / défendre", {"plaquage": 10, "recup": 8, "plaquage_dom": 4}),
        ("Assurer la continuité", {"offload": 6, "passes": 4}),
        ("Sauter / lifter / lecture touche", {"sauteur_pct": 5, "sauteur": 3, "lifteur_pct": 2, "lifteur": 2}),
        ("Habileté technique", {"offload_pct": 6}),
    ],
    "9": [
        ("Transmission", {"passes": 24}),
        ("Colle au ballon / soutenir", {"soutien": 8}),
        ("Plaquer / circulation défensive", {"plaquage": 10, "recup": 6}),
        ("Jeu au pied / sortie de camp", {"jap": 16, "kicks_pct": 6}),
        ("Duel off", {"contact_dom": 6, "contact": 4}),
        ("Habileté technique", {"offload": 4, "break": 4}),
    ],
    "10": [
        ("Transmission", {"passes": 20, "soutien": 3}),
        ("Jeu au pied (pression, occupation, CE/CR, drop) et stratégie", {"jap": 24, "kicks_pct": 10}),
        ("Plaquer / circulation défensive", {"plaquage": 10, "recup": 6}),
        ("Duel / franchissement", {"contact_dom": 6, "break": 6, "def_battu": 6}),
        ("Habileté technique", {"offload": 4}),
    ],
    "12-13": [
        ("Duel off / franchissement", {"contact_dom": 10, "break": 12, "def_battu": 12, "contact": 4}),
        ("Circulation def / plaquer / contest / défendre", {"plaquage": 12, "recup": 8, "plaquage_dom": 6}),
        ("Habileté technique (main / pied)", {"offload": 6, "offload_pct": 4, "passes": 6, "jap": 6}),
        ("Soutien / ruck offensif", {"soutien": 6}),
    ],
    "11-14": [
        ("Duel / franchissement", {"contact_dom": 8, "break": 16, "def_battu": 14}),
        ("Réception / duel aérien", {"rce_pct": 10, "aerien_pct": 10}),
        ("Plaquer / lecture défense", {"plaquage": 10, "recup": 4}),
        ("Soutien / ruck offensif", {"soutien": 4}),
        ("Habileté technique (mains / pied)", {"offload": 6, "passes": 4, "jap": 8}),
    ],
    "15": [
        ("1 contre 1 défensif", {"plaquage": 12, "plaquage_dom": 6}),
        ("Jeu au pied", {"jap": 22}),
        ("Duel / franchissement", {"break": 14, "def_battu": 12, "contact_dom": 6}),
        ("Réception / duel aérien", {"rce_pct": 10, "aerien_pct": 10}),
        ("Habileté technique", {"offload": 4, "passes": 4}),
    ],
}
POSTES = list(PROFILS)
NON_MESURABLES = ["Lecture du jeu", "Stratégie", "Leadership", "Communication", "Se déplacer / enchaîner les tâches",
                  "Compréhension du système", "Défense de maul", "Gestion du 3ème rideau / contre-attaque",
                  "Dureté", "État d'esprit"]


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


def bareme_effectif(overrides=None):
    """Barème de départ + réglages enregistrés. overrides = {(poste, critère): (poids, cible, plancher)}."""
    out = {p: dict(c) for p, c in BAREME_DEFAUT.items()}
    for (poste, crit), val in (overrides or {}).items():
        if poste in out and crit in CRITERES:
            out[poste][crit] = val
    return out


def _clamp(x, lo=0.0, hi=10.0):
    return max(lo, min(hi, x))


def note_match(poste, rows, minutes, jaunes=0, rouges=0, bareme=None):
    """Note /10 d'un joueur sur un match. `rows` = {"attack", "defense", "ruck"} (lignes du joueur
    pour ce match, éventuellement None). Renvoie None si pas assez de minutes ou de matière."""
    if minutes < MIN_MINUTES or poste not in POSTES:
        return None
    reglages = (bareme or BAREME_DEFAUT).get(poste) or {}
    k80 = 80.0 / minutes
    detail, tot_w, tot_s = [], 0.0, 0.0
    for crit, (poids, cible, plancher) in reglages.items():
        if poids <= 0 or crit not in CRITERES:
            continue
        label, kind, fn = CRITERES[crit]
        try:
            val, n = fn(rows)
        except Exception:
            val, n = None, 0
        if val is None:
            continue
        if kind == "vol":
            val80 = val * k80
            score = _clamp(10 * val80 / cible) if cible else 0.0
            shown = round(val80, 1)
        else:
            span = (cible - plancher) or 1
            if n:
                prior = plancher + 0.7 * span
                val = (val * n + prior * SHRINK_K) / (n + SHRINK_K)
            score = _clamp(10 * (val - plancher) / span)
            shown = round(val)
        detail.append({"critere": crit, "label": label, "type": kind, "valeur": shown, "actions": n,
                       "score": round(score, 1), "poids": poids})
        tot_w += poids
        tot_s += poids * score
    if not detail:
        return None
    base = _clamp(tot_s / tot_w * CALAGE.get(PROFIL_FAMILLE.get(poste), 1.0))
    attack = rows.get("attack")
    pen = _v(attack, "penalite_off", "penalite_def")
    pdb = _v(attack, "pdb")
    rates = _v(attack, "passe_minus")
    rates_t = _v(rows.get("defense"), "plaquage_rate")
    pr = PASSES_RATEES.get(poste, PASSES_RATEES_DEFAUT)
    essais = _v(rows.get("ruck"), "essai")
    malus = [("Pénalités concédées", min(pen * MALUS["penalite"], MALUS["penalite_max"]), pen),
             ("Plaquages ratés", min(rates_t * PLAQUAGE_RATE[0], PLAQUAGE_RATE[1]), rates_t),
             ("Cartons jaunes", jaunes * MALUS["jaune"], jaunes),
             ("Cartons rouges", rouges * MALUS["rouge"], rouges),
             ("Passes ratées", min(rates * pr[0], pr[1]), rates),
             ("Ballons perdus", min(pdb * MALUS["pdb"], MALUS["pdb_max"]), pdb)]
    malus = [{"label": l, "points": round(-p, 1), "n": n} for l, p, n in malus if p > 0]
    bonus_pts = min(essais * BONUS["essai"], BONUS["essai_max"])
    bonus = [{"label": "Essais", "points": round(bonus_pts, 1), "n": essais}] if bonus_pts > 0 else []
    total_malus = min(sum(-m["points"] for m in malus), MALUS["total_max"])
    note = _clamp(base - total_malus + bonus_pts)
    return {"note": round(note, 1), "base": round(base, 1), "malus": malus, "bonus": bonus,
            "detail": sorted(detail, key=lambda d: -d["poids"]), "minutes": minutes, "poste": poste}
